"""Accept / edit / reject on the pre-filled form (`/prefill/reviews`), and the day view's warm-up line.

A verdict is a professional's statement about one prefilled answer: append-only, per actor, never written
back to the form or the record it was filled from. The read side is the caller's own verdicts and nothing
about anyone else's. The worklist's `warmup.last_built_at` is when the briefings cache was last written —
`None` where nothing has been prepared — and it is a timestamp, not a count.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

import app.main as main
from app import access
from app.fixtures import load_personas
from app.store import Store, get_store
from tests.fixtures.bound_encounters import ENCOUNTERS, EXAMPLE_LABELS

NURSE = {"x-ms-client-principal-id": "oid-1234", "x-ms-client-principal-name": "nurse@example.fi"}
OTHER = {"x-ms-client-principal-id": "oid-other", "x-ms-client-principal-name": "other@example.fi"}
ENCOUNTER = "bound-encounter-002-laakitys-sairaanhoitaja"
ROUTE = f"/api/encounters/{ENCOUNTER}/prefill/reviews"


def _store() -> Store:
    return Store(personas=load_personas(), path=None, encounters=ENCOUNTERS, example_labels=EXAMPLE_LABELS)


@pytest.fixture
def client():
    store = _store()
    main.app.dependency_overrides[get_store] = lambda: store
    with TestClient(main.app) as test_client:
        yield test_client, store
    main.app.dependency_overrides.clear()


def _verdict(review_id="r1", verdict="vahvistettu", **extra) -> dict:
    return {"review_id": review_id, "form_id": "laakityksen-seuranta", "question_id": "laakehoito",
            "verdict": verdict, **extra}


# ---------- create ----------

def test_a_verdict_is_recorded_for_the_actor_and_read_back_on_the_same_encounter(client):
    api, store = client
    response = api.post(ROUTE, json=_verdict(), headers=NURSE)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True and body["review_id"] == "r1" and body["verdict"] == "vahvistettu"
    assert body["edited_text"] is None and body["recorded_at"] and body["label"] == "Tallennettu palveluun"

    listed = api.get(ROUTE, headers=NURSE).json()
    assert listed["encounter_id"] == ENCOUNTER
    assert [(row["review_id"], row["verdict"], row["edited_text"]) for row in listed["reviews"]] == [
        ("r1", "vahvistettu", None)]
    # The stored row is the audit row: who, which encounter, which answer, which verdict — and no record.
    stored = store.prefill_review("r1")
    assert stored["actor_id"] == "oid-1234" and stored["encounter_id"] == ENCOUNTER
    assert stored["form_id"] == "laakityksen-seuranta" and stored["question_id"] == "laakehoito"
    assert stored["kind"] == "prefill_review" and stored["event_id"] == "prefill-review-r1"
    assert "record_id" not in stored and "evidence" not in stored


def test_an_edit_carries_the_professionals_wording_and_the_other_verdicts_carry_none(client):
    api, _ = client
    edited = api.post(ROUTE, json=_verdict("r-edit", "muokattu", edited_text="  Amlodipiini 5 mg, otettu epäsäännöllisesti.  "),
                      headers=NURSE)
    assert edited.status_code == 200, edited.text
    assert edited.json()["edited_text"] == "Amlodipiini 5 mg, otettu epäsäännöllisesti."
    # `muokattu` without wording, and wording on a verdict that is not an edit, are 400s at the shape
    # boundary — before any lookup, so neither answers whether the encounter exists.
    assert api.post(ROUTE, json=_verdict("r-empty", "muokattu", edited_text="   "), headers=NURSE).status_code == 400
    assert api.post(ROUTE, json=_verdict("r-extra", "hylätty", edited_text="x"), headers=NURSE).status_code == 400
    assert api.post("/api/encounters/no-such-encounter/prefill/reviews",
                    json=_verdict("r-none", "muokattu"), headers=NURSE).status_code == 400
    assert api.post(ROUTE, json=_verdict("r-long", "muokattu", edited_text="x" * 2001), headers=NURSE).status_code == 422
    assert api.post(ROUTE, json=_verdict("r-bad", "accept"), headers=NURSE).status_code == 422


def test_the_verdicts_write_nothing_back_to_the_form_or_the_records(client):
    api, store = client
    before = json.dumps([record.model_dump(mode="json") for record in store.persona(
        ENCOUNTERS[ENCOUNTER].persona_id).records], sort_keys=True)
    picture_before = api.get(f"/api/encounters/{ENCOUNTER}/picture?mode=foundry-off", headers=NURSE).json()
    for index, (verdict, extra) in enumerate((("vahvistettu", {}), ("muokattu", {"edited_text": "Toisin."}),
                                              ("hylätty", {}))):
        assert api.post(ROUTE, json=_verdict(f"r-{index}", verdict, **extra), headers=NURSE).status_code == 200
    after = json.dumps([record.model_dump(mode="json") for record in store.persona(
        ENCOUNTERS[ENCOUNTER].persona_id).records], sort_keys=True)
    picture_after = api.get(f"/api/encounters/{ENCOUNTER}/picture?mode=foundry-off", headers=NURSE).json()
    assert before == after
    assert picture_before["records"] == picture_after["records"]
    assert picture_before.get("prefilled_forms") == picture_after.get("prefilled_forms")
    assert store.feedback(ENCOUNTERS[ENCOUNTER].persona_id) == []


# ---------- retry ----------

def test_a_retry_under_the_same_id_is_idempotent_and_a_different_body_under_it_is_refused(client):
    api, _ = client
    first = api.post(ROUTE, json=_verdict("same"), headers=NURSE)
    again = api.post(ROUTE, json=_verdict("same"), headers=NURSE)
    assert first.status_code == again.status_code == 200
    assert first.json()["recorded_at"] == again.json()["recorded_at"], "the stored row is returned, not a second one"
    assert len(api.get(ROUTE, headers=NURSE).json()["reviews"]) == 1

    conflicting = api.post(ROUTE, json=_verdict("same", "hylätty"), headers=NURSE)
    assert conflicting.status_code == 409
    assert "review_id" in conflicting.json()["detail"]
    assert [row["verdict"] for row in api.get(ROUTE, headers=NURSE).json()["reviews"]] == ["vahvistettu"]


def test_a_second_verdict_on_the_same_answer_is_kept_in_order_not_refused(client):
    """Confirm, then edit: both stay on the record, newest last. There is no one-decision claim here."""
    api, _ = client
    assert api.post(ROUTE, json=_verdict("v1", "vahvistettu"), headers=NURSE).status_code == 200
    assert api.post(ROUTE, json=_verdict("v2", "muokattu", edited_text="Tarkennus."), headers=NURSE).status_code == 200
    rows = api.get(ROUTE, headers=NURSE).json()["reviews"]
    assert [(row["review_id"], row["verdict"]) for row in rows] == [("v1", "vahvistettu"), ("v2", "muokattu")]
    assert rows[0]["decided_at"] <= rows[1]["decided_at"]


# ---------- who may write and read ----------

def test_an_anonymous_caller_is_refused_and_another_actor_never_sees_the_verdict(client):
    api, _ = client
    assert api.post(ROUTE, json=_verdict("anon")).status_code == 401
    assert api.get(ROUTE).status_code == 401
    assert api.post(ROUTE, json=_verdict("mine"), headers=NURSE).status_code == 200
    # The colleague's read is empty: no count, no hint, no id — the nurse's verdict is not in this answer.
    other = api.get(ROUTE, headers=OTHER).json()
    assert other == {"encounter_id": ENCOUNTER, "reviews": []}
    assert "mine" not in json.dumps(other)


def test_a_scoped_out_actor_is_forbidden_before_anything_is_read_or_written(client, monkeypatch):
    api, store = client
    policy = json.dumps({"version": 1, "rules": [{"objects": ["oid-1234"], "upns": ["nurse@example.fi"],
                                                  "encounters": [ENCOUNTER]}]})
    monkeypatch.setenv(access.POLICY_ENV, policy)
    # The nurse's own encounter: granted.
    assert api.post(ROUTE, json=_verdict("scoped"), headers=NURSE).status_code == 200
    # The colleague matches no rule: 403 on write and on read, and nothing was stored under their name.
    assert api.post(ROUTE, json=_verdict("stranger"), headers=OTHER).status_code == 403
    assert api.get(ROUTE, headers=OTHER).status_code == 403
    assert store.prefill_review("stranger") is None
    # The nurse, on an encounter the policy does not grant: the same refusal.
    out_of_scope = "/api/encounters/bound-encounter-013-vammaispalvelut-sosiaalityontekija/prefill/reviews"
    assert api.post(out_of_scope, json=_verdict("elsewhere"), headers=NURSE).status_code == 403
    assert store.prefill_review("elsewhere") is None


def test_the_verdict_id_space_is_disjoint_from_chat_reviews_and_feedback(client):
    """`prefill-review-<id>` cannot collide with `review-<id>` or a feedback event id, on either backend."""
    api, store = client
    assert api.post(ROUTE, json=_verdict("shared-id"), headers=NURSE).status_code == 200
    assert store.chat_review("shared-id") is None
    assert store.prefill_review("shared-id") is not None
    assert store.chat_reviews(ENCOUNTERS[ENCOUNTER].persona_id) == []


# ---------- D4: the day view's warm-up line ----------

def test_the_worklist_names_no_warm_up_time_when_nothing_has_been_prepared(client):
    api, store = client
    assert store.latest_briefing_built_at() is None
    body = api.get("/api/worklist?day=2026-10-20", headers=NURSE).json()
    assert body["warmup"] == {"last_built_at": None}


def test_the_worklist_names_the_newest_cache_write_once_a_briefing_is_prepared(monkeypatch):
    monkeypatch.setenv("LUVN_BRIEFING_CACHE", "memory")
    store = _store()
    main.app.dependency_overrides[get_store] = lambda: store
    try:
        with TestClient(main.app) as api:
            assert api.get("/api/worklist?day=2026-10-20", headers=NURSE).json()["warmup"]["last_built_at"] is None
            started = datetime.now(timezone.utc)
            assert api.get(f"/api/encounters/{ENCOUNTER}/picture", headers=NURSE).status_code == 200
            built = store.latest_briefing_built_at()
            assert built is not None and built >= started
            body = api.get("/api/worklist?day=2026-10-20", headers=NURSE).json()
            assert body["warmup"]["last_built_at"] == built.isoformat()
            # A second briefing moves the line forward: it is the newest write over the whole cache.
            assert api.get("/api/encounters/bound-encounter-005-laakitys-sairaanhoitaja/picture",
                           headers=NURSE).status_code == 200
            assert store.latest_briefing_built_at() >= built
    finally:
        main.app.dependency_overrides.clear()


def test_the_sql_cache_answers_the_latest_build_through_the_index_connection():
    """The SQL cache has no rows of its own in memory: the store asks the index for `MAX(built_at)`."""
    when = datetime(2026, 10, 14, 6, 30, tzinfo=timezone.utc)

    class _SqlShapedCache:  # the shape of `briefings.SqlBriefingCache`: persists, no `_rows`
        persists = True

    class _Index:
        def __init__(self, rows):
            self.rows, self.queries = rows, []

        def _rows(self, sql, *params):
            self.queries.append(sql)
            return self.rows

    store = _store()
    store.briefing_cache = _SqlShapedCache()
    store._delivery_index = _Index([(when,)])
    assert store.latest_briefing_built_at() == when
    assert store._delivery_index.queries == ["SELECT MAX(built_at) FROM briefings"]
    store._delivery_index = _Index([(None,)])
    assert store.latest_briefing_built_at() is None
    store._delivery_index = _Index([("2026-10-14T06:30:00+00:00",)])
    assert store.latest_briefing_built_at() == when


def test_a_cache_with_its_own_latest_built_at_is_asked_first():
    when = datetime(2026, 10, 14, 7, 0, tzinfo=timezone.utc)

    class _Cache:
        persists = True

        def latest_built_at(self):
            return when

    store = _store()
    store.briefing_cache = _Cache()
    assert store.latest_briefing_built_at() == when
