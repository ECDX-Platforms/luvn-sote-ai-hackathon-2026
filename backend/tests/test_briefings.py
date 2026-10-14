"""The briefings cache (`app/briefings.py`), the warm-up, and the day worklist route that reads them."""

from __future__ import annotations

import csv
import shutil
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import briefings, delivery, main
from app.fixtures import load_personas
from app.pipeline import build_picture_for_encounter
from app.store import Store, get_store
from tests.fixtures.bound_encounters import ENCOUNTERS, EXAMPLE_LABELS

ROOT = Path(__file__).resolve().parents[2]
PRINCIPAL = {"X-MS-CLIENT-PRINCIPAL-NAME": "presenter@example.test",
             "X-MS-CLIENT-PRINCIPAL-ID": "00000000-0000-0000-0000-000000000001"}
ENCOUNTER = "bound-encounter-002-laakitys-sairaanhoitaja"


@pytest.fixture
def cached_client(monkeypatch):
    monkeypatch.setenv("LUVN_BRIEFING_CACHE", "memory")
    store = Store(personas=load_personas(), path=None, encounters=ENCOUNTERS, example_labels=EXAMPLE_LABELS)
    assert isinstance(store.briefing_cache, briefings.MemoryBriefingCache)
    main.app.dependency_overrides[get_store] = lambda: store
    with TestClient(main.app) as client:
        yield client, store
    main.app.dependency_overrides.clear()


def test_a_fixture_store_has_no_cache_unless_opted_in():
    store = Store(personas=load_personas(), path=None, encounters=ENCOUNTERS, example_labels=EXAMPLE_LABELS)
    assert isinstance(store.briefing_cache, briefings.NullBriefingCache)
    key = briefings.BriefingKey.for_encounter(ENCOUNTER, None)
    assert key.corpus_generation == "unknown" and key.rule_version and "|" in key.agent_version
    assert store.briefing_cache.read(key) is None
    assert store.appointments_for_day(date(2026, 10, 13)) == [] and store.appointment_days(date(2026, 10, 1), date(2026, 10, 31)) == {}


def test_the_second_read_is_served_from_the_cache_and_the_compare_arms_are_not(cached_client):
    client, store = cached_client
    first = client.get(f"/api/encounters/{ENCOUNTER}/picture", headers=PRINCIPAL)
    assert first.status_code == 200 and first.headers["X-LUVN-Briefing-Source"] == "built"
    assert len(store.briefing_cache) == 1
    second = client.get(f"/api/encounters/{ENCOUNTER}/picture", headers=PRINCIPAL)
    assert second.status_code == 200 and second.headers["X-LUVN-Briefing-Source"] == "cache"
    assert second.json() == first.json(), "the stored picture is the picture that was built"
    compare = client.get(f"/api/encounters/{ENCOUNTER}/picture?mode=foundry-off", headers=PRINCIPAL)
    assert compare.status_code == 200 and compare.headers["X-LUVN-Briefing-Source"] == "built"
    assert len(store.briefing_cache) == 1, "a compare arm is never stored"
    # Every read is audited, cached or not.
    assert sum(1 for row in store.read_audit() if row.get("encounter_id") == ENCOUNTER) == 3


def test_a_stale_row_is_rebuilt_and_a_rebuild_replaces_it(cached_client, monkeypatch):
    client, store = cached_client
    client.get(f"/api/encounters/{ENCOUNTER}/picture", headers=PRINCIPAL)
    key = briefings.BriefingKey.for_encounter(ENCOUNTER, None)
    stored = store.briefing_cache.read(key)
    assert briefings.is_fresh(stored, date.today()) and not briefings.is_fresh(stored, date(2000, 1, 1))
    # A different agent version is a different key: the old row is not served for it.
    monkeypatch.setenv("LUNA_FOUNDRY_AGENT_VERSION", "999")
    response = client.get(f"/api/encounters/{ENCOUNTER}/picture", headers=PRINCIPAL)
    assert response.headers["X-LUVN-Briefing-Source"] == "built" and len(store.briefing_cache) == 2


def test_chat_reads_the_cached_picture_instead_of_rebuilding(cached_client, monkeypatch):
    import asyncio

    client, store = cached_client
    client.get(f"/api/encounters/{ENCOUNTER}/picture", headers=PRINCIPAL)

    async def refuse(*_args, **_kwargs):
        raise AssertionError("the chat picture must come from the cache")

    monkeypatch.setattr(main, "build_picture_for_encounter", refuse)
    picture = asyncio.run(main._chat_picture(store, ENCOUNTER))
    assert picture.encounter_id == ENCOUNTER
    prompts = client.get(f"/api/encounters/{ENCOUNTER}/chat/prompts", headers=PRINCIPAL)
    assert prompts.status_code == 200


def test_the_attention_summary_counts_only_rendered_rows():
    import asyncio

    store = Store(personas=load_personas(), path=None, encounters=ENCOUNTERS, example_labels=EXAMPLE_LABELS)
    picture = asyncio.run(build_picture_for_encounter(ENCOUNTER, personas=store.persona_index(),
                                                      encounters=store.encounter_index(), foundry_enabled=False))
    summary = briefings.attention_summary(picture)
    rendered = len(picture.records.priority) + len(picture.records.accessible_other)
    assert summary["record_count"] == rendered
    assert summary["active_medications"] <= rendered and summary["conflicts"] == len(picture.conflicts)
    assert summary["risk_level"] == picture.risk.risk_level and summary["narration_accepted"] is False
    assert summary["lead"] is None, "no Decision-1 ran here, so there is no urgency lead to show"


def _delivery_with_appointments(tmp_path: Path, day: date) -> Path:
    corpus = tmp_path / "corpus"
    shutil.copytree(ROOT / "data" / "experimental" / "delivery-demo", corpus)
    encounters = list(csv.DictReader((corpus / "encounters.csv").open(encoding="utf-8")))
    first, second = encounters[0], encounters[1]
    with (corpus / "appointments.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=delivery.APPOINTMENT_COLUMNS, lineterminator="\n")
        writer.writeheader()
        writer.writerow({"appointment_id": "appt-1", "source_person_id": first["source_person_id"],
                         "domain": "treatment", "scheduled_on": day.isoformat(), "status_text": "Toteutunut",
                         "urgency_text": "Hoito akuutti- tai kiirevastaanotolla", "specialty": "98",
                         "contact_record_id": "", "encounter_id": first["encounter_id"]})
        writer.writerow({"appointment_id": "appt-2", "source_person_id": second["source_person_id"],
                         "domain": "treatment", "scheduled_on": day.isoformat(), "status_text": "Toteutunut",
                         "urgency_text": "Kiireetön hoito", "specialty": "98", "contact_record_id": "",
                         "encounter_id": second["encounter_id"]})
        writer.writerow({"appointment_id": "appt-3", "source_person_id": second["source_person_id"],
                         "domain": "treatment", "scheduled_on": day.isoformat(), "status_text": "Toteutunut",
                         "urgency_text": "", "specialty": "98", "contact_record_id": "", "encounter_id": ""})
    delivery.import_delivery(corpus, tmp_path / "import")
    return tmp_path / "import"


def test_the_worklist_reads_appointments_and_the_cache_and_the_warm_up_fills_it(tmp_path, monkeypatch):
    import asyncio

    day = date(2026, 10, 20)
    imported = _delivery_with_appointments(tmp_path, day)
    monkeypatch.setenv("LUVN_DELIVERY_DIR", str(imported))
    monkeypatch.setenv("LUVN_BRIEFING_CACHE", "memory")
    monkeypatch.delenv("LUVN_SQL_SERVER", raising=False)
    store = Store()
    main.app.dependency_overrides[get_store] = lambda: store
    try:
        with TestClient(main.app) as client:
            empty = client.get("/api/worklist?day=2026-10-21", headers=PRINCIPAL).json()
            assert empty["rows"] == [] and empty["count"] == 0 and len(empty["days"]) == 15
            assert next(d for d in empty["days"] if d["day"] == day.isoformat())["appointments"] == 3
            before = client.get(f"/api/worklist?day={day.isoformat()}", headers=PRINCIPAL).json()
            assert before["count"] == 2, "a row with no lens is scheduled but has nothing to open"
            assert [row["appointment_id"] for row in before["rows"]] == ["appt-1", "appt-2"], "urgent first"
            assert all(row["readiness"] == "pending" and row["attention"] is None for row in before["rows"])
            assert before["rows"][0]["role_label"] and before["rows"][0]["purpose_label"]
            assert client.get("/api/worklist?day=20-10-2026", headers=PRINCIPAL).status_code == 422
            assert client.get("/api/worklist?role=nobody", headers=PRINCIPAL).status_code == 422
            # The warm-up builds every lens scheduled that day, once; a second pass finds them fresh.
            from app import warmup

            ids = warmup.encounter_ids_for_days(store, day, 1)
            assert len(ids) == 2
            # A briefing is built for the date it is built on, whichever day it is planned for; the row's readiness
            # is whether such a briefing exists today, so a briefing built "for" the planned day would never count.
            counts = asyncio.run(briefings.warm(store, ids, build=build_picture_for_encounter, today=date.today(),
                                                log=lambda _line: None))
            assert counts == {"requested": 2, "fresh": 0, "built": 2, "failed": 0}
            again = asyncio.run(briefings.warm(store, ids, build=build_picture_for_encounter, today=date.today(),
                                               log=lambda _line: None))
            assert again["fresh"] == 2 and again["built"] == 0
            after = client.get(f"/api/worklist?day={day.isoformat()}", headers=PRINCIPAL).json()
            assert after["ready"] == 2
            assert all(row["readiness"] == "ready" and row["attention"]["record_count"] >= 1 for row in after["rows"])
            role = before["rows"][0]["role"]
            scoped = client.get(f"/api/worklist?day={day.isoformat()}&role={role}", headers=PRINCIPAL).json()
            assert scoped["count"] == sum(1 for row in after["rows"] if row["role"] == role)
    finally:
        main.app.dependency_overrides.clear()


def test_the_attention_summary_counts_the_models_current_notes_over_rendered_rows_only():
    """D3: `current_by_model` is the number of rendered rows Decision-1 called `current`; None when it did not run.
    A note outside the rendered rows is not counted, so the number never needs a withheld denominator."""
    import asyncio

    from app.picture import DecisionNoteOutcome

    store = Store(personas=load_personas(), path=None, encounters=ENCOUNTERS, example_labels=EXAMPLE_LABELS)
    picture = asyncio.run(build_picture_for_encounter(ENCOUNTER, personas=store.persona_index(),
                                                      encounters=store.encounter_index(), foundry_enabled=False))
    assert briefings.attention_summary(picture)["current_by_model"] is None, "no Decision-1 ran here"
    rendered = [view.record_id for view in [*picture.records.priority, *picture.records.accessible_other]]
    assert len(rendered) >= 2
    notes = [DecisionNoteOutcome(record_id=rendered[0], status="relevant", time_sensitivity="current"),
             DecisionNoteOutcome(record_id=rendered[1], status="unclear", time_sensitivity="historical"),
             DecisionNoteOutcome(record_id=rendered[0], status="relevant", time_sensitivity="current"),
             DecisionNoteOutcome(record_id="not-a-rendered-row", status="relevant", time_sensitivity="current")]
    decided = picture.model_copy(update={"selection": picture.selection.model_copy(
        update={"decision_status": "complete", "decision_notes": notes})})
    summary = briefings.attention_summary(decided)
    assert summary["current_by_model"] == 1, "one rendered row, counted once; the unrendered id is not counted"
    assert summary["record_count"] == len(rendered)
    disabled = picture.model_copy(update={"selection": picture.selection.model_copy(
        update={"decision_status": "disabled", "decision_notes": notes})})
    assert briefings.attention_summary(disabled)["current_by_model"] is None
