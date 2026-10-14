"""Same-origin encounter API, feedback binding and SPA routing contracts."""

import json
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.fixtures import load_personas
from app.store import Store, _decision_claim_key, get_store
from app.upm import Kypsyysarvio
from tests.fixtures.bound_encounters import ENCOUNTERS, EXAMPLE_LABELS
from test_store import MemoryContainer, _FailingRecordBlob

# Every write route records the platform principal; these are the headers Entra Easy Auth injects.
PRINCIPAL = {"x-ms-client-principal-id": "oid-1234", "x-ms-client-principal-name": "nurse@example.fi"}


@pytest.fixture
def runtime_client():
    store = Store(personas=load_personas(), path=None, encounters=ENCOUNTERS, example_labels=EXAMPLE_LABELS)
    main.app.dependency_overrides[get_store] = lambda: store
    with TestClient(main.app) as client:
        yield client, store
    main.app.dependency_overrides.clear()


def _final_turn(client, encounter_id: str, turn_id: str, *, headers: dict,
                question: str = "mitkä ovat seuraavat toimenpiteet") -> dict:
    """Ask the assistant's stream route one question and return its final `turn` event's payload."""
    from test_assistant import _events

    response = client.post(f"/api/encounters/{encounter_id}/chat/stream",
                           json={"turn_id": turn_id, "question": question}, headers=headers)
    assert response.status_code == 200, response.text
    name, payload = _events(response.text)[-1]
    assert name == "turn", name
    return payload


def _bind_test_context(store, encounter_id, record_updates=None, **encounter_updates):
    encounter = store.encounter_index()[encounter_id]
    encounter = replace(encounter, **encounter_updates)
    store._encounters = {**store.encounter_index(), encounter_id: encounter}
    persona = store.persona(encounter.persona_id)
    updates = record_updates or {}
    store._personas[persona.persona_id] = persona.model_copy(update={
        "records": [record.model_copy(update={"encounter_id": encounter_id, **updates.get(record.record_id, {})})
                    for record in persona.records]
    })
    return encounter


def test_verifier_valueerror_fails_closed_without_escaping_picture_route(monkeypatch, runtime_client):
    """A live verifier ValueError yields the deterministic Picture, never a 500 or prose."""
    from app import decision_model, llm

    client, _ = runtime_client

    async def generated(scoped, _today, _timeout_s, **_kwargs):
        return llm.FoundryResult(
            output=llm.FreshNarration(sentences=[llm.NarrationSentence(
                sentence_id="s1", text="Unverified candidate text.", source_ids=[scoped.ids[0]],
            )]),
            model="synthetic-test-model",
        )

    async def verifier_valueerror(*_args, **_kwargs):
        raise ValueError("malformed verifier response")

    monkeypatch.setattr(llm, "foundry_configured", lambda: True)
    monkeypatch.setattr(llm, "generate_fresh_narration", generated)
    monkeypatch.setattr(decision_model, "verify_narration", verifier_valueerror)
    response = client.get("/api/encounters/bound-encounter-002-laakitys-sairaanhoitaja/picture", headers=PRINCIPAL)
    assert response.status_code == 200
    body = response.json()
    assert body["narrative"] is None
    assert body["narration_acceptance_status"] == "rejected"
    assert body["narration_support_status"] == "invalid"
    assert body["risk"]["risk_level"] == "ristiriita"
    assert body["records"]["priority"] or body["records"]["accessible_other"]
    assert "Unverified candidate text." not in response.text
    assert any(stage["stage"] == "grounding.verification" and stage["status"] == "failed"
               for stage in body["pipeline_trace"])


def test_all_walkthrough_encounters_resolve_to_server_owned_tuples(runtime_client):
    client, _ = runtime_client
    response = client.get("/api/encounters")
    assert response.status_code == 200
    rows = response.json()["encounters"]
    assert {row["encounter_id"] for row in rows} == {
        "bound-encounter-002-laakitys-sairaanhoitaja",
        "bound-encounter-003-mielenterveys-sairaanhoitaja",
        "bound-encounter-003-toimintakyky-sairaanhoitaja",
        "bound-encounter-003-mielenterveys-laakari",
        "bound-encounter-003-toimintakyky-fysioterapeutti",
        "bound-encounter-004-sosiaalipalvelut-sosiaalityontekija",
        "bound-encounter-005-laakitys-sairaanhoitaja",
        "bound-encounter-012-neuvola-terveydenhoitaja",
        "bound-encounter-013-vammaispalvelut-sosiaalityontekija",
    }
    assert len(rows) == 9
    assert all({"person_name", "fixture_label", "identity_available", "demo_name"}.isdisjoint(row)
               for row in rows), "the encounter index carries no per-lens identity oracle"
    # A display name, never the internal identifier: what a professional reads must not be a fixture key.
    assert all(row["example_label"] and not row["example_label"].startswith("bound-") for row in rows)
    assert {row["encounter_id"]: row["example_label"] for row in rows} == EXAMPLE_LABELS
    # The picker label is generated from the purpose and the role; the catalogue keeps no map of them.
    assert all(row["picker_label"] == f"{row['purpose_label']} · {row['role_label']}" for row in rows)
    nurse_contexts = [row for row in rows if row["role"] == "sairaanhoitaja"
                      and row["purpose"] in {"mental_health_assessment", "functioning_service_needs"}]
    assert len(nurse_contexts) == 2
    assert ENCOUNTERS["bound-encounter-003-mielenterveys-sairaanhoitaja"].persona_id == ENCOUNTERS[
        "bound-encounter-003-toimintakyky-sairaanhoitaja"
    ].persona_id
    for row in rows:
        picture = client.get(
            f"/api/encounters/{row['encounter_id']}/picture",
            params={"role": "laakari", "purpose": "mental_health_assessment", "persona_id": "attacker"},
            headers=PRINCIPAL,
        )
        assert picture.status_code == 200
        body = picture.json()
        assert body["encounter_id"] == row["encounter_id"]
        assert body["role"] == row["role"]
        assert body["purpose"] == row["purpose"]
        # H3: a caller-supplied `persona_id` is ignored, and the picture no longer returns the internal
        # fixture key at all — the client navigates by `encounter_id`.
        assert "persona_id" not in body and "attacker" not in picture.text
        assert "bound-persona" not in picture.text
        assert body["identity_header"] == "Henkilöllisyystieto ei saatavilla"
        assert body["pipeline_trace_id"]
        assert "narration_generation_path" in body
        stage_names = {stage["stage"] for stage in body["pipeline_trace"]}
        assert {"encounter.resolve", "policy.cut", "decision.selection", "pipeline.total"} <= stage_names
        assert "record_id" not in json.dumps(body["pipeline_trace"])
        assert "note" not in json.dumps(body["pipeline_trace"])


def test_the_ai_layers_cannot_change_the_deterministic_decision(runtime_client):
    """Gate 8's invariant, as a test rather than as copy.

    The presenter surface tells a reader that Decision-1 and Foundry cannot change the permitted records, the
    rule-based decision or the assessments. If that ever stops being true, the comparison stops being a
    justification and becomes a demonstration that the model moved the answer. `mode` is the only axis
    that varies here, so the three responses are the same encounter under three layer settings.
    """
    client, _ = runtime_client
    encounter_id = "bound-encounter-003-mielenterveys-sairaanhoitaja"
    normal = client.get(f"/api/encounters/{encounter_id}/picture", headers=PRINCIPAL).json()
    decision_off = client.get(f"/api/encounters/{encounter_id}/picture", params={"mode": "decision-off"}, headers=PRINCIPAL).json()
    foundry_off = client.get(f"/api/encounters/{encounter_id}/picture", params={"mode": "foundry-off"}, headers=PRINCIPAL).json()

    for name, other in (("decision-off", decision_off), ("foundry-off", foundry_off)):
        assert other["situation"] == normal["situation"], f"{name} changed the situation"
        assert other["risk"] == normal["risk"], f"{name} changed the risk decision"
        assert other["actions"] == normal["actions"], f"{name} changed the actions"
        assert other["assessments"] == normal["assessments"], f"{name} changed the assessments"
        # The permitted record set is the policy cut's output and predates both layers.
        normal_ids = {row["record_id"] for row in [*normal["records"]["priority"],
                                                   *normal["records"]["accessible_other"]]}
        other_ids = {row["record_id"] for row in [*other["records"]["priority"],
                                                  *other["records"]["accessible_other"]]}
        assert other_ids <= normal_ids, f"{name} disclosed a record the cut had not permitted"
    # What a layer CAN change is the prose. `foundry-off` withholds it by construction, which is the one
    # direction this environment can assert without a live model: a withheld narration is never accepted.
    assert foundry_off["narration_acceptance_status"] != "accepted"


def test_the_role_label_is_the_policy_role_label_with_no_per_encounter_override(runtime_client):
    """The visible role is the permission key's own label: no encounter specialises it, so a label can
    never suggest a permission split the policy does not make."""
    client, _ = runtime_client
    rows = {row["encounter_id"]: row for row in client.get("/api/encounters").json()["encounters"]}
    mental = rows["bound-encounter-003-mielenterveys-sairaanhoitaja"]
    functioning = rows["bound-encounter-003-toimintakyky-sairaanhoitaja"]
    assert mental["role_label"] == functioning["role_label"] == "Sairaanhoitaja"
    assert mental["role"] == functioning["role"] == "sairaanhoitaja"
    assert {"person_name", "identity_available"}.isdisjoint(mental)
    assert {"person_name", "identity_available"}.isdisjoint(functioning)
    assert not hasattr(main, "ROLE_LABEL_OVERRIDES")


@pytest.mark.parametrize(("age", "assessment_value", "expected_visible"), [
    (11, None, True), (12, None, False), (None, None, False), (11, "paatoskykyinen", False),
])
def test_picture_api_applies_guardian_age_default_only_below_twelve(
    runtime_client, age, assessment_value, expected_visible,
):
    client, store = runtime_client
    encounter_id = "bound-encounter-012-neuvola-terveydenhoitaja"
    record_id = "bound-note-020"
    assessment = Kypsyysarvio(
        value=assessment_value, assessed_by_role="terveydenhoitaja",
        assessed_at=datetime(2026, 10, 1, tzinfo=timezone.utc),
    ) if assessment_value else None
    _bind_test_context(
        store, encounter_id, {record_id: {"consent_holder": "self"}},
        age_at_encounter=age, kypsyysarvio=assessment,
    )

    response = client.get(f"/api/encounters/{encounter_id}/picture?mode=foundry-off", headers=PRINCIPAL)
    assert response.status_code == 200
    picture = response.json()
    visible_ids = {row["record_id"] for row in [
        *picture["records"]["priority"], *picture["records"]["accessible_other"]
    ]}
    assert (record_id in visible_ids) is expected_visible
    assert "consent_holder" not in response.text


def test_picture_api_binds_inherited_case_restriction_and_keeps_silent_absence(runtime_client):
    client, store = runtime_client
    encounter_id = "bound-encounter-002-laakitys-sairaanhoitaja"
    record_id = "bound-note-002"
    before = client.get(f"/api/encounters/{encounter_id}/picture?mode=foundry-off", headers=PRINCIPAL)
    assert before.status_code == 200
    before_ids = {row["record_id"] for row in [*before.json()["records"]["priority"],
                                                 *before.json()["records"]["accessible_other"]]}
    assert record_id in before_ids

    _bind_test_context(
        store, encounter_id, {record_id: {"case_id": "case-demo"}},
        case_id="case-demo", inherited_restricted_case_ids=frozenset({"case-demo"}),
    )
    response = client.get(f"/api/encounters/{encounter_id}/picture?mode=foundry-off", headers=PRINCIPAL)
    assert response.status_code == 200
    body = response.json()
    after_ids = {row["record_id"] for row in [*body["records"]["priority"], *body["records"]["accessible_other"]]}
    assert record_id not in after_ids
    assert {"bound-note-002-alignment", "bound-note-002-draft"} <= after_ids
    assert "Kotikäynti, lääkehoidon tarkistus" not in response.text
    assert "excluded_count" not in body["records"]


def test_case_provenance_is_post_cut_and_unchanged_by_added_withheld_records(monkeypatch, runtime_client):
    from app import pipeline

    client, store = runtime_client
    encounter_id = "bound-encounter-002-laakitys-sairaanhoitaja"
    encounter = store.encounter_index()[encounter_id]
    persona = store.persona(encounter.persona_id)
    allowed_id = "bound-note-002"
    template = next(row for row in persona.records if row.record_id == allowed_id)
    base_records = [row.model_copy(update={
        "encounter_id": encounter_id,
        **({"case_id": "case-visible"} if row.record_id == allowed_id else {}),
    }) for row in persona.records]
    scoped_encounter = replace(encounter, case_id="case-visible",
                               inherited_restricted_case_ids=frozenset({"case-hidden"}))
    store._encounters = {**store.encounter_index(), encounter_id: scoped_encounter}

    async def no_decision(*_args):
        return []

    monkeypatch.setattr(pipeline, "classify_decision_notes", no_decision)
    groupings = []
    for count in (0, 1, 3):
        hidden = [template.model_copy(update={
            "record_id": f"withheld-case-{index}", "encounter_id": encounter_id,
            "case_id": "case-hidden", "payload": {"otsikko": f"secret title {index}",
                                                      "teksti": f"secret body {index}"},
        }) for index in range(count)]
        store._personas[persona.persona_id] = persona.model_copy(update={"records": [*base_records, *hidden]})
        response = client.get(f"/api/encounters/{encounter_id}/picture?mode=foundry-off", headers=PRINCIPAL)
        assert response.status_code == 200
        body = response.json()
        rows = [*body["records"]["priority"], *body["records"]["accessible_other"]]
        visible_ids = {row["record_id"] for row in rows}
        assert allowed_id in visible_ids
        assert all(f"withheld-case-{index}" not in visible_ids for index in range(count))
        assert all(f"secret body {index}" not in response.text for index in range(count))
        visible_allowed = next(row for row in rows if row["record_id"] == allowed_id)
        assert visible_allowed["encounter_id"] == encounter_id
        assert visible_allowed["case_id"] == "case-visible"
        groupings.append(sorted((row["record_id"], row.get("encounter_id"), row.get("case_id"))
                                for row in rows))
    assert groupings[0] == groupings[1] == groupings[2]


def test_picture_api_scopes_explicit_assessment_to_one_prescription(runtime_client):
    client, store = runtime_client
    encounter_id = "bound-encounter-002-laakitys-sairaanhoitaja"
    target_id, sibling_id, note_id = "bound-med-002", "bound-med-003", "bound-note-002"
    assessment = Kypsyysarvio(
        value="ei_paatoskykyinen_tiedot_huoltajalle", assessed_by_role="laakari",
        assessed_at=datetime(2026, 10, 1, tzinfo=timezone.utc), scope="resepti",
        prescription_record_id=target_id,
    )
    _bind_test_context(
        store, encounter_id,
        {
            target_id: {"consent_holder": "self"},
            sibling_id: {"consent_holder": "self", "topic": "verenpaine"},
            note_id: {"consent_holder": "self"},
        },
        consent_holders=("guardian",), age_at_encounter=15, kypsyysarvio=assessment,
    )

    response = client.get(f"/api/encounters/{encounter_id}/picture?mode=foundry-off", headers=PRINCIPAL)
    assert response.status_code == 200
    picture = response.json()
    visible_ids = {row["record_id"] for row in [
        *picture["records"]["priority"], *picture["records"]["accessible_other"]
    ]}
    assert target_id in visible_ids
    assert sibling_id not in visible_ids
    assert note_id not in visible_ids
    assert "legal_basis" not in response.text


def test_picture_and_feedback_use_byte_identical_encounter_scopes(monkeypatch, runtime_client):
    from app import pipeline
    from app.policy import cut_for_encounter as resolve_cut

    client, _ = runtime_client
    encounter_id = "bound-encounter-012-neuvola-terveydenhoitaja"
    observed = []

    def capture(persona, encounter):
        scoped = resolve_cut(persona, encounter)
        encoded = json.dumps({
            "records": scoped.persona.model_dump(mode="json"), "role": scoped.role,
            "purpose": scoped.purpose, "encounter_id": scoped.encounter_id,
        }, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        observed.append((encounter.encounter_id, encoded))
        return scoped

    monkeypatch.setattr(main, "cut_for_encounter", capture)
    monkeypatch.setattr(pipeline, "cut_for_encounter", capture)
    listing = client.get("/api/encounters")
    assert listing.status_code == 200
    assert {"identity_available", "person_name", "fixture_label"}.isdisjoint(listing.json()["encounters"][0])
    picture = client.get(f"/api/encounters/{encounter_id}/picture?mode=foundry-off", headers=PRINCIPAL)
    assert picture.status_code == 200
    feedback = client.post(f"/api/encounters/{encounter_id}/feedback", json={
        "event_id": "same-scope-all-routes", "proposal_id": "stale-proposal",
        "rule_version": "stale-rule", "action": "accept",
    }, headers=PRINCIPAL)
    assert feedback.status_code == 409
    matching = [encoded for seen_id, encoded in observed if seen_id == encounter_id]
    assert len(matching) == 2  # picture and proposal feedback; the index carries no per-lens cut data
    assert matching[0] == matching[1]


def test_guardian_encounter_list_does_not_disclose_withheld_identity(runtime_client):
    client, store = runtime_client
    encounter_id = "bound-encounter-012-neuvola-terveydenhoitaja"
    persona = store.persona("bound-persona-012-neuvola-lapsi")
    hidden_name = "SYNTHETIC-WITHHELD-IDENTITY"
    store._personas[persona.persona_id] = persona.model_copy(update={
        "records": [record.model_copy(update={
            "consent_holder": "self", "payload": {**record.payload, "nimi": hidden_name}
        }) if record.record_id == "bound-demo-012" else record for record in persona.records]
    })
    response = client.get("/api/encounters")
    assert response.status_code == 200
    entry = next(row for row in response.json()["encounters"] if row["encounter_id"] == encounter_id)
    assert {"person_name", "fixture_label", "identity_available"}.isdisjoint(entry)
    assert hidden_name not in response.text


def test_http_snapshot_ignores_withheld_rows_but_checks_pipeline_timing_separately(monkeypatch, runtime_client):
    import asyncio
    from app import narration, pipeline

    client, store = runtime_client
    encounter_id = "bound-encounter-002-laakitys-sairaanhoitaja"
    encounter = store.encounter_index()[encounter_id]
    persona = store.persona(encounter.persona_id)
    encounter = replace(encounter, case_id="case-visible",
                        inherited_restricted_case_ids=frozenset({"case-hidden"}))
    store._encounters = {**store.encounter_index(), encounter_id: encounter}
    template = next(row for row in persona.records if row.record_type == "vapaateksti")
    monkeypatch.setattr(pipeline, "classify_decision_notes", lambda *_: asyncio.sleep(0, result=[]))
    monkeypatch.setattr(pipeline, "_milliseconds", lambda *_: 0)
    monkeypatch.setattr(narration, "_milliseconds", lambda *_: 0)

    snapshots = []
    for count in (0, 1, 3):
        hidden = [template.model_copy(update={
            "record_id": f"http-withheld-{index}", "encounter_id": encounter_id,
            "case_id": "case-hidden", "payload": {"otsikko": f"secret {index}", "teksti": "hidden"},
        }) for index in range(count)]
        restricted = [row.model_copy(update={"encounter_id": encounter_id, "case_id": "case-hidden"})
                      for row in persona.records]
        store._personas[persona.persona_id] = persona.model_copy(update={"records": [*restricted, *hidden]})
        response = client.get(f"/api/encounters/{encounter_id}/picture?mode=foundry-off", headers=PRINCIPAL)
        assert response.status_code == 200, response.text
        body = response.json()
        trace = body.pop("pipeline_trace")
        body.pop("pipeline_trace_id")
        total = next(stage for stage in trace if stage["stage"] == "pipeline.total")
        assert total["status"] == "complete" and isinstance(total["elapsed_ms"], int) and total["elapsed_ms"] >= 0
        snapshots.append(json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
        assert all(f"http-withheld-{index}" not in response.text for index in range(count))
    # pipeline.total is intentionally excluded: execution time is not byte-identical across record counts.
    assert snapshots[0] == snapshots[1] == snapshots[2]


def test_picture_route_passes_handler_entry_preparation_deadline(monkeypatch, runtime_client):
    client, store = runtime_client
    observed = {}

    async def picture_for_encounter(encounter_id, **kwargs):
        observed.update(kwargs)
        return await original(encounter_id, **kwargs)

    original = main.build_picture_for_encounter
    monkeypatch.setattr(main, "build_picture_for_encounter", picture_for_encounter)
    response = client.get("/api/encounters/bound-encounter-002-laakitys-sairaanhoitaja/picture", headers=PRINCIPAL)
    assert response.status_code == 200
    assert observed["personas"] is store.persona_index()
    assert observed["preparation_deadline"] - observed["started_at"] == pytest.approx(main.PREPARATION_WINDOW_S, abs=0.01)


def test_picture_failure_modes_re_request_server_pipeline_without_global_switches(monkeypatch, runtime_client):
    from app import pipeline

    client, _ = runtime_client

    async def no_decision(*_args):
        return []

    monkeypatch.setattr(pipeline, "classify_decision_notes", no_decision)
    monkeypatch.setattr(pipeline.llm, "foundry_configured", lambda: False)
    base = client.get("/api/encounters/bound-encounter-002-laakitys-sairaanhoitaja/picture", headers=PRINCIPAL).json()
    decision_off = client.get("/api/encounters/bound-encounter-002-laakitys-sairaanhoitaja/picture?mode=decision-off", headers=PRINCIPAL).json()
    foundry_off = client.get("/api/encounters/bound-encounter-002-laakitys-sairaanhoitaja/picture?mode=foundry-off", headers=PRINCIPAL).json()
    assert base["selection"]["decision_status"] == "no_input"
    assert decision_off["selection"]["decision_status"] == "disabled"
    assert base["selection"]["baseline_record_ids"] == decision_off["selection"]["baseline_record_ids"]
    assert base["situation"] == decision_off["situation"] == foundry_off["situation"]
    assert foundry_off["narration_rejection_reason"] == "foundry_disabled"
    assert client.get("/api/encounters/bound-encounter-002-laakitys-sairaanhoitaja/picture?mode=unknown", headers=PRINCIPAL).status_code == 422


def test_feedback_post_reads_created_event_by_id_without_enumerating_jsonl(runtime_client, monkeypatch):
    client, store = runtime_client
    proposal = client.get("/api/encounters/bound-encounter-002-laakitys-sairaanhoitaja/picture", headers=PRINCIPAL).json()["proposals"][0]
    storage = store._feedback_storage
    monkeypatch.setattr(storage, "read_all", lambda: (_ for _ in ()).throw(AssertionError("full read")))
    response = client.post("/api/encounters/bound-encounter-002-laakitys-sairaanhoitaja/feedback", json={
        "event_id": "endpoint-indexed-read", "proposal_id": proposal["proposal_id"],
        "rule_version": proposal["rule_version"], "action": "accept",
    }, headers=PRINCIPAL)
    assert response.status_code == 200
    assert response.json()["event_id"] == "endpoint-indexed-read"


def test_proposal_feedback_requires_an_actor_and_stores_nothing_anonymously(runtime_client):
    """B1: the primary human decision point records the platform principal or refuses."""
    client, store = runtime_client
    picture = client.get("/api/encounters/bound-encounter-002-laakitys-sairaanhoitaja/picture", headers=PRINCIPAL).json()
    proposal = picture["proposals"][0]
    response = client.post("/api/encounters/bound-encounter-002-laakitys-sairaanhoitaja/feedback", json={
        "event_id": "anonymous-decision", "proposal_id": proposal["proposal_id"],
        "rule_version": proposal["rule_version"], "action": "accept",
    })
    assert response.status_code == 401
    assert store.feedback("bound-persona-002-krooninen-ikääntynyt") == []
    assert store.feedback_event("anonymous-decision") is None


def test_proposal_feedback_is_bound_durable_idempotent_and_uses_edited_text(runtime_client):
    client, store = runtime_client
    picture = client.get("/api/encounters/bound-encounter-002-laakitys-sairaanhoitaja/picture", headers=PRINCIPAL).json()
    assert picture["proposals"]
    proposal = picture["proposals"][0]
    body = {
        "event_id": "event-retry-1",
        "proposal_id": proposal["proposal_id"],
        "rule_version": proposal["rule_version"],
        "action": "edit",
        "note": "Tarkistan lääkityksen lähteet yhdessä asiakkaan kanssa.",
    }
    first = client.post("/api/encounters/bound-encounter-002-laakitys-sairaanhoitaja/feedback", json=body,
                        headers=PRINCIPAL)
    retry = client.post("/api/encounters/bound-encounter-002-laakitys-sairaanhoitaja/feedback", json=body,
                        headers=PRINCIPAL)
    assert first.status_code == retry.status_code == 200
    assert first.json()["event_id"] == retry.json()["event_id"] == body["event_id"]
    assert first.json()["actor_id"] == "oid-1234"
    rows = store.feedback("bound-persona-002-krooninen-ikääntynyt")
    assert len(rows) == 1
    assert rows[0]["note"] == body["note"]
    assert rows[0]["encounter_id"] == "bound-encounter-002-laakitys-sairaanhoitaja"
    assert rows[0]["proposal_id"] == proposal["proposal_id"]
    assert rows[0]["rule_version"] == proposal["rule_version"]
    assert rows[0]["actor_id"] == "oid-1234" and rows[0]["actor_name"] == "nurse@example.fi"
    # B16: the stored decision can be reconstructed without re-deriving the proposal.
    assert rows[0]["proposal_text"] == proposal["text"]
    assert rows[0]["proposal_status"] == proposal["status"]
    assert rows[0]["proposal_evidence_record_ids"] == proposal["evidence_record_ids"]


def test_proposal_feedback_binds_each_decision_to_its_actor(runtime_client):
    """H2/B6: the decision slot is per actor, not per proposal.

    The old expectation was that a second, different decision on a proposal is refused *for anyone*. That
    was wrong for this product: several professional roles legitimately see the same proposal through their
    own lens, and the audit records who decided, so the first professional's decision must not consume the
    slot everyone else needs. A second decision by the *same* actor is still refused; a decision by a
    *different* actor on the same proposal succeeds and is stored under that actor.
    """
    client, store = runtime_client
    proposal = client.get("/api/encounters/bound-encounter-002-laakitys-sairaanhoitaja/picture", headers=PRINCIPAL).json()["proposals"][0]
    body = {"event_id": "one-decision", "proposal_id": proposal["proposal_id"],
            "rule_version": proposal["rule_version"], "action": "accept"}
    assert client.post("/api/encounters/bound-encounter-002-laakitys-sairaanhoitaja/feedback", json=body,
                       headers=PRINCIPAL).status_code == 200
    assert client.post("/api/encounters/bound-encounter-002-laakitys-sairaanhoitaja/feedback", json=body,
                       headers=PRINCIPAL).status_code == 200
    # The same actor's second, different decision is refused.
    second = {**body, "event_id": "second-decision", "action": "dismiss"}
    refused = client.post("/api/encounters/bound-encounter-002-laakitys-sairaanhoitaja/feedback", json=second,
                          headers=PRINCIPAL)
    assert refused.status_code == 409
    assert "decision" in refused.json()["detail"]
    # A different professional deciding the same proposal succeeds; the slot was never shared.
    other = {"x-ms-client-principal-id": "oid-other", "x-ms-client-principal-name": "other@example.fi"}
    allowed = client.post("/api/encounters/bound-encounter-002-laakitys-sairaanhoitaja/feedback",
                          json={**body, "event_id": "other-decision", "action": "dismiss"}, headers=other)
    assert allowed.status_code == 200, allowed.text
    rows = store.feedback("bound-persona-002-krooninen-ikääntynyt")
    assert sorted(row["event_id"] for row in rows) == ["one-decision", "other-decision"]
    assert {row["actor_id"] for row in rows} == {"oid-1234", "oid-other"}


def test_proposal_feedback_does_not_reveal_another_actors_decision_or_proposal_state(runtime_client):
    """H2: the three probes below must not distinguish the three states of a proposal id.

    State the probes used to expose, before the fix: `409 proposal is no longer available` for an unknown id,
    `400 edited proposal text is required` for a known-undecided id (the note rule fired before the claim and
    before existence), and `409 this proposal already has a decision` naming the state for a known-decided id.
    Now the request-shape `400` is stateless and fires for an unknown id too, and another actor's decision is
    not a conflict at all, so no probe reports it.
    """
    client, store = runtime_client
    encounter = "bound-encounter-002-laakitys-sairaanhoitaja"
    proposal = client.get(f"/api/encounters/{encounter}/picture", headers=PRINCIPAL).json()["proposals"][0]
    other = {"x-ms-client-principal-id": "oid-other", "x-ms-client-principal-name": "other@example.fi"}

    # Probe 1: an unknown proposal id. One body; the request-shape check runs first, so a bad-note probe
    # does not distinguish "unknown" from "known" either.
    unknown = client.post(f"/api/encounters/{encounter}/feedback", json={
        "event_id": "probe-unknown", "proposal_id": "no-such-proposal",
        "rule_version": proposal["rule_version"], "action": "accept"}, headers=PRINCIPAL)
    assert unknown.status_code == 409
    assert unknown.json()["detail"] == "proposal is no longer available"
    # This 409 is a *failure*, not the caller's own duplicate: it must not carry the discriminator the
    # client renders as "you already decided this".
    assert "reason" not in unknown.json()
    unknown_bad_note = client.post(f"/api/encounters/{encounter}/feedback", json={
        "event_id": "probe-unknown-note", "proposal_id": "no-such-proposal",
        "rule_version": proposal["rule_version"], "action": "edit"}, headers=PRINCIPAL)
    assert unknown_bad_note.status_code == 400
    known_bad_note = client.post(f"/api/encounters/{encounter}/feedback", json={
        "event_id": "probe-known-note", "proposal_id": proposal["proposal_id"],
        "rule_version": proposal["rule_version"], "action": "edit"}, headers=PRINCIPAL)
    assert known_bad_note.status_code == 400
    assert unknown_bad_note.json() == known_bad_note.json(), \
        "the note-rule refusal distinguished an unknown proposal id from a known one"

    # Probe 2: another professional decides the proposal first. This actor's decision on the same proposal
    # still succeeds, and nothing in the response says a decision already exists.
    decided = client.post(f"/api/encounters/{encounter}/feedback", json={
        "event_id": "other-actor-decision", "proposal_id": proposal["proposal_id"],
        "rule_version": proposal["rule_version"], "action": "accept"}, headers=other)
    assert decided.status_code == 200, decided.text
    mine = client.post(f"/api/encounters/{encounter}/feedback", json={
        "event_id": "my-decision", "proposal_id": proposal["proposal_id"],
        "rule_version": proposal["rule_version"], "action": "dismiss"}, headers=PRINCIPAL)
    assert mine.status_code == 200, mine.text
    assert "already has a decision" not in mine.text
    assert "oid-other" not in mine.text

    # Probe 3: this actor's own earlier decision is the only decision state that may be reported.
    again = client.post(f"/api/encounters/{encounter}/feedback", json={
        "event_id": "my-second-decision", "proposal_id": proposal["proposal_id"],
        "rule_version": proposal["rule_version"], "action": "accept"}, headers=PRINCIPAL)
    assert again.status_code == 409
    assert "already has a decision" in again.json()["detail"]
    # The body carries a machine-readable discriminator so the rail can render this as the caller's own
    # earlier decision rather than as a generic rejection, without matching on the prose of `detail`.
    assert again.json()["reason"] == "already_decided"
    # Both actors' decisions are recorded, each under its own actor.
    rows = store.feedback("bound-persona-002-krooninen-ikääntynyt")
    assert sorted(row["event_id"] for row in rows) == ["my-decision", "other-actor-decision"]


def test_blob_feedback_acknowledges_the_callers_own_record_not_another_actors_claim():
    """Finding 1 end to end on the Blob backend, the backend production uses.

    The caller chooses `event_id`, and another actor's decision-claim key is deterministic, so the caller
    can spell it. The old acknowledgement read the ownerless name first and returned that claim, so a
    successful 200 carried the other actor's `recorded_at` and the caller's exact retry then answered 409.
    """
    blobs = MemoryContainer()
    store = Store(personas=load_personas(), blob_container=blobs, encounters=ENCOUNTERS, example_labels=EXAMPLE_LABELS)
    main.app.dependency_overrides[get_store] = lambda: store
    try:
        with TestClient(main.app) as client:
            encounter = "bound-encounter-004-sosiaalipalvelut-sosiaalityontekija"
            persona_id = "bound-persona-004-sosiaalipalvelut"
            proposal = client.get(f"/api/encounters/{encounter}/picture", headers=PRINCIPAL).json()["proposals"][0]
            other = {"x-ms-client-principal-id": "oid-other", "x-ms-client-principal-name": "other@example.fi"}
            decided = client.post(f"/api/encounters/{encounter}/feedback", json={
                "event_id": "a-decision", "proposal_id": proposal["proposal_id"],
                "rule_version": proposal["rule_version"], "action": "accept"}, headers=other)
            assert decided.status_code == 200, decided.text

            claim_key = _decision_claim_key(
                f"proposal\0{encounter}\0{proposal['proposal_id']}\0oid-other")
            body = {"event_id": claim_key, "proposal_id": proposal["proposal_id"],
                    "rule_version": proposal["rule_version"], "action": "dismiss"}
            guessed = client.post(f"/api/encounters/{encounter}/feedback", json=body, headers=PRINCIPAL)
            assert guessed.status_code == 200, guessed.text
            mine = next(row for row in store.feedback(persona_id) if row["event_id"] == claim_key)
            assert mine["actor_id"] == "oid-1234"
            assert guessed.json()["recorded_at"] == mine["recorded_at"].isoformat(), \
                "the acknowledgement was the other actor's claim time, not this caller's own record"
            # The exact retry is idempotent (a 200 with the same acknowledgement), not a 409.
            retry = client.post(f"/api/encounters/{encounter}/feedback", json=body, headers=PRINCIPAL)
            assert retry.status_code == 200, retry.text
            assert retry.json()["recorded_at"] == guessed.json()["recorded_at"]
            assert all(row["kind"] == "proposal_feedback" for row in store.feedback(persona_id))
    finally:
        main.app.dependency_overrides.clear()


def test_blob_feedback_conflict_does_not_burn_the_callers_decision_slot():
    """Finding 2 end to end on the Blob backend: a conflicting `event_id` records nothing and costs nothing.

    The old write path reserved the caller's decision claim before it checked the event identity, so reusing
    a written id was refused *after* the slot was consumed: the caller's fresh retry then answered
    "this proposal already has a decision" although they had recorded nothing.
    """
    blobs = MemoryContainer()
    store = Store(personas=load_personas(), blob_container=blobs, encounters=ENCOUNTERS, example_labels=EXAMPLE_LABELS)
    main.app.dependency_overrides[get_store] = lambda: store
    try:
        with TestClient(main.app) as client:
            encounter = "bound-encounter-004-sosiaalipalvelut-sosiaalityontekija"
            persona_id = "bound-persona-004-sosiaalipalvelut"
            proposal = client.get(f"/api/encounters/{encounter}/picture", headers=PRINCIPAL).json()["proposals"][0]
            other = {"x-ms-client-principal-id": "oid-other", "x-ms-client-principal-name": "other@example.fi"}
            base = {"proposal_id": proposal["proposal_id"], "rule_version": proposal["rule_version"]}
            assert client.post(f"/api/encounters/{encounter}/feedback",
                               json={**base, "event_id": "a-decision", "action": "accept"},
                               headers=other).status_code == 200

            conflict = client.post(f"/api/encounters/{encounter}/feedback",
                                   json={**base, "event_id": "a-decision", "action": "dismiss"},
                                   headers=PRINCIPAL)
            assert conflict.status_code == 409
            assert conflict.json()["detail"] == "feedback event conflicts with an earlier request"
            # A conflict is a failure, never the caller's own duplicate, so it carries no discriminator.
            assert "reason" not in conflict.json()

            fresh = client.post(f"/api/encounters/{encounter}/feedback",
                                json={**base, "event_id": "b-fresh", "action": "dismiss"},
                                headers=PRINCIPAL)
            assert fresh.status_code == 200, fresh.text
            assert {row["event_id"]: row["actor_id"] for row in store.feedback(persona_id)} == {
                "a-decision": "oid-other", "b-fresh": "oid-1234"}
    finally:
        main.app.dependency_overrides.clear()


def test_encounter_decisions_returns_the_callers_own_proposal_decisions_and_nothing_else(runtime_client):
    """The read side of the per-actor contract: only the caller's own decisions, with no trace of others.

    The negative half is the point. After another principal decides a proposal, the caller's read must not
    name that proposal, count it, or hint that a decision exists — not in a field, not in the prose body.
    The per-actor claim means the other decision is not a conflict for this caller, so there is genuinely
    nothing this caller may be told about it.
    """
    client, store = runtime_client
    encounter = "bound-encounter-002-laakitys-sairaanhoitaja"
    proposal = client.get(f"/api/encounters/{encounter}/picture", headers=PRINCIPAL).json()["proposals"][0]
    other = {"x-ms-client-principal-id": "oid-other", "x-ms-client-principal-name": "other@example.fi"}

    # No principal is refused exactly as the write route and the prompts route are: this read has no
    # anonymous subset to answer with.
    anonymous = client.get(f"/api/encounters/{encounter}/decisions")
    assert anonymous.status_code == 401
    assert anonymous.json()["detail"] == "an authenticated professional is required"

    # An encounter this instance does not have is a 404, like every other encounter route.
    assert client.get("/api/encounters/not-an-encounter/decisions", headers=PRINCIPAL).status_code == 404

    # Before anyone decides, the caller's page is an empty envelope — present, so the client has a shape.
    empty = client.get(f"/api/encounters/{encounter}/decisions", headers=PRINCIPAL)
    assert empty.status_code == 200
    assert empty.json() == {"encounter_id": encounter, "proposals": [], "chat_suggestions": []}

    # Another professional decides it first, choosing the opposite action.
    decided = client.post(f"/api/encounters/{encounter}/feedback", json={
        "event_id": "other-decides", "proposal_id": proposal["proposal_id"],
        "rule_version": proposal["rule_version"], "action": "accept"}, headers=other)
    assert decided.status_code == 200, decided.text

    # The caller learns nothing: not the id, not the action, not a count, not a boolean.
    mine = client.get(f"/api/encounters/{encounter}/decisions", headers=PRINCIPAL)
    assert mine.status_code == 200
    assert mine.json() == {"encounter_id": encounter, "proposals": [], "chat_suggestions": []}
    assert "oid-other" not in mine.text
    assert "accept" not in mine.text
    assert proposal["proposal_id"] not in mine.text

    # The caller's own decision is then the only one the read reports, with its action and time.
    written = client.post(f"/api/encounters/{encounter}/feedback", json={
        "event_id": "mine-decides", "proposal_id": proposal["proposal_id"],
        "rule_version": proposal["rule_version"], "action": "dismiss"}, headers=PRINCIPAL)
    assert written.status_code == 200, written.text
    after = client.get(f"/api/encounters/{encounter}/decisions", headers=PRINCIPAL).json()
    stored = next(row for row in store.feedback("bound-persona-002-krooninen-ikääntynyt")
                  if row["event_id"] == "mine-decides")
    assert after["proposals"] == [{
        "proposal_id": proposal["proposal_id"], "action": "dismiss",
        "decided_at": stored["recorded_at"].isoformat(),
    }]
    assert set(after["proposals"][0]) == {"proposal_id", "action", "decided_at"}
    # The other actor's `accept` and identity are still absent from the caller's body.
    assert "accept" not in json.dumps(after)
    assert "oid-other" not in json.dumps(after)
    assert after["chat_suggestions"] == []

    # And the other principal's read reports their own decision, never the caller's.
    theirs = client.get(f"/api/encounters/{encounter}/decisions", headers=other).json()
    assert theirs["proposals"] == [{
        "proposal_id": proposal["proposal_id"], "action": "accept",
        "decided_at": next(row for row in store.feedback("bound-persona-002-krooninen-ikääntynyt")
                           if row["event_id"] == "other-decides")["recorded_at"].isoformat(),
    }]
    assert "dismiss" not in json.dumps(theirs)


def test_encounter_decisions_are_scoped_to_the_encounter_even_for_one_persona(runtime_client):
    """A persona appears in several encounters; a decision belongs to the encounter it was made on.

    The two mental-health nurse lenses share one persona, so an owner-scoped store read returns both
    encounters' records. The route must scope them back to the addressed encounter, and it must also keep
    another principal's chat decision off the caller's page even though it is stored under the same persona.
    """
    client, _store = runtime_client
    encounter = "bound-encounter-003-mielenterveys-sairaanhoitaja"
    sibling = "bound-encounter-003-toimintakyky-sairaanhoitaja"   # same persona, different encounter
    other = {"x-ms-client-principal-id": "oid-other", "x-ms-client-principal-name": "other@example.fi"}

    asked = _final_turn(client, encounter, "my-turn", headers=PRINCIPAL)
    suggestions = asked["turn"]["suggestions"]
    if not suggestions:
        pytest.skip("this encounter produced no suggestion to review")
    reviewed = client.post("/api/chat/reviews", json={
        "review_id": "my-review", "turn_id": "my-turn",
        "suggestion_id": suggestions[0]["suggestion_id"], "action": "accept"}, headers=PRINCIPAL)
    assert reviewed.status_code == 200, reviewed.text

    # Another principal asks and reviews their own turn on the same encounter and persona.
    theirs = _final_turn(client, encounter, "their-turn", headers=other)
    their_suggestions = theirs["turn"]["suggestions"]
    if their_suggestions:
        assert client.post("/api/chat/reviews", json={
            "review_id": "their-review", "turn_id": "their-turn",
            "suggestion_id": their_suggestions[0]["suggestion_id"], "action": "dismiss"},
            headers=other).status_code == 200

    mine = client.get(f"/api/encounters/{encounter}/decisions", headers=PRINCIPAL)
    assert mine.status_code == 200
    stored = _store.chat_review("my-review")
    assert mine.json()["chat_suggestions"] == [{
        "turn_id": "my-turn", "suggestion_id": suggestions[0]["suggestion_id"], "action": "accept",
        "decided_at": stored["recorded_at"].isoformat(),
    }]
    assert set(mine.json()["chat_suggestions"][0]) == {"turn_id", "suggestion_id", "action", "decided_at"}
    assert "their-turn" not in mine.text
    assert "dismiss" not in mine.text

    # The sibling encounter is the same person, but the decision was not made there: it says nothing.
    elsewhere = client.get(f"/api/encounters/{sibling}/decisions", headers=PRINCIPAL)
    assert elsewhere.status_code == 200
    assert elsewhere.json()["chat_suggestions"] == []
    assert "my-turn" not in elsewhere.text


def test_proposal_feedback_does_not_re_check_the_hashed_rule_version(runtime_client):
    """B15: `proposal_id` already hashes the rule version, so a repeated check could only mislead."""
    client, store = runtime_client
    proposal = client.get("/api/encounters/bound-encounter-002-laakitys-sairaanhoitaja/picture", headers=PRINCIPAL).json()["proposals"][0]
    response = client.post("/api/encounters/bound-encounter-002-laakitys-sairaanhoitaja/feedback", json={
        "event_id": "rule-version-label", "proposal_id": proposal["proposal_id"],
        "rule_version": "a-client-supplied-label", "action": "accept",
    }, headers=PRINCIPAL)
    assert response.status_code == 200
    [row] = store.feedback("bound-persona-002-krooninen-ikääntynyt")
    assert row["rule_version"] == proposal["rule_version"]


@pytest.mark.parametrize("action", ["accept", "dismiss"])
def test_feedback_accept_and_dismiss_are_bound_and_acknowledged(runtime_client, action):
    client, store = runtime_client
    picture = client.get("/api/encounters/bound-encounter-002-laakitys-sairaanhoitaja/picture", headers=PRINCIPAL).json()
    proposal = picture["proposals"][0]
    event_id = f"{action}-event"
    response = client.post("/api/encounters/bound-encounter-002-laakitys-sairaanhoitaja/feedback", json={
        "event_id": event_id,
        "proposal_id": proposal["proposal_id"],
        "rule_version": proposal["rule_version"],
        "action": action,
    }, headers=PRINCIPAL)
    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert response.json()["event_id"] == event_id
    [entry] = store.feedback("bound-persona-002-krooninen-ikääntynyt")
    assert entry["encounter_id"] == "bound-encounter-002-laakitys-sairaanhoitaja"
    assert entry["proposal_id"] == proposal["proposal_id"]
    assert entry["rule_version"] == proposal["rule_version"]
    assert entry["action"] == action
    assert entry["note"] is None


def test_feedback_rejects_stale_or_client_invented_proposal(runtime_client):
    client, _ = runtime_client
    response = client.post("/api/encounters/bound-encounter-002-laakitys-sairaanhoitaja/feedback", json={
        "event_id": "bad-proposal",
        "proposal_id": "client-made-up",
        "rule_version": "challenge-1.1-assessment-review-v1",
        "action": "accept",
    }, headers=PRINCIPAL)
    assert response.status_code == 409
    response = client.post("/api/encounters/bound-encounter-002-laakitys-sairaanhoitaja/feedback", json={
        "event_id": "extra-role",
        "proposal_id": "client-made-up",
        "rule_version": "rule-v1",
        "action": "accept",
        "role": "laakari",
        "evidence_record_ids": ["hidden-source"],
    }, headers=PRINCIPAL)
    assert response.status_code == 422


def test_feedback_storage_failure_is_not_acknowledged(runtime_client):
    client, store = runtime_client
    proposal = client.get("/api/encounters/bound-encounter-002-laakitys-sairaanhoitaja/picture", headers=PRINCIPAL).json()["proposals"][0]

    def fail_write(entry, event_id=None):
        raise OSError("storage unavailable")

    store.add_feedback = fail_write
    response = client.post("/api/encounters/bound-encounter-002-laakitys-sairaanhoitaja/feedback", json={
        "event_id": "durability-failure",
        "proposal_id": proposal["proposal_id"],
        "rule_version": proposal["rule_version"],
        "action": "accept",
    }, headers=PRINCIPAL)
    assert response.status_code == 503
    assert response.json()["detail"] == "feedback was not saved"


class _TransientRecordWriteContainer:
    """The real Blob stand-in with ONE failing record write; claim writes keep working.

    The route-level reproduction of Finding 2: the claim is taken, then the decision record's upload fails
    transiently, so the route answers 503 while the claim has already been written. A wrapper that failed
    the claim write instead would never build the state this test is about.
    """

    def __init__(self, inner, fail_times: int = 1):
        self._inner = inner
        self.fail_times = fail_times

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def get_blob_client(self, name):
        client = self._inner.get_blob_client(name)
        # Claims and read-audit rows are not the feedback record this injector is about: the audit row for a
        # briefing read is written first in this test, and failing it would fail the read, not the decision.
        return client if name.startswith(("claims/", "audit/")) else _FailingRecordBlob(self, client)


def test_a_transient_record_write_does_not_strand_the_actor_behind_a_false_already_decided():
    """Finding 2 end to end on the Blob backend the route uses, next to the failure test above.

    Before this fix: 1st attempt 503 "feedback was not saved"; 2nd attempt, NEW `event_id`, 409
    `reason=already_decided` with `decisions` still `[]`. The actor was told a decision existed while the
    read the page is built from said none did. After: the 503 is the same, the new `event_id` is accepted
    (no decision was ever stored, and the failed attempt's claim was released), and the ONLY 409 says
    `already_decided` when `proposals` really does name that decision.
    """
    blobs = _TransientRecordWriteContainer(MemoryContainer())
    store = Store(personas=load_personas(), blob_container=blobs, encounters=ENCOUNTERS, example_labels=EXAMPLE_LABELS)
    main.app.dependency_overrides[get_store] = lambda: store
    try:
        with TestClient(main.app) as client:
            encounter = "bound-encounter-004-sosiaalipalvelut-sosiaalityontekija"
            proposal = client.get(f"/api/encounters/{encounter}/picture", headers=PRINCIPAL).json()["proposals"][0]

            def post(event_id: str, action: str):
                return client.post(f"/api/encounters/{encounter}/feedback", json={
                    "event_id": event_id, "proposal_id": proposal["proposal_id"],
                    "rule_version": proposal["rule_version"], "action": action}, headers=PRINCIPAL)

            def decisions() -> list[dict]:
                return client.get(f"/api/encounters/{encounter}/decisions",
                                  headers=PRINCIPAL).json()["proposals"]

            failed = post("event-first-fails", "accept")
            assert failed.status_code == 503, failed.text
            assert failed.json()["detail"] == "feedback was not saved"
            assert decisions() == [], "nothing was stored, so the read must say so"

            # The recovery half: the SAME actor, a NEW event id, and no stored decision in the way.
            recovered = post("event-second-succeeds", "dismiss")
            assert recovered.status_code == 200, recovered.text
            after = decisions()
            assert [row["proposal_id"] for row in after] == [proposal["proposal_id"]]
            assert [row["action"] for row in after] == ["dismiss"]

            # The refusal half: now a decision really exists, so the fresh id is refused honestly.
            refused = post("event-third-refused", "accept")
            assert refused.status_code == 409
            assert refused.json()["reason"] == "already_decided"
            assert decisions() == after, "the refusal changed nothing, and the read still names the decision"
    finally:
        main.app.dependency_overrides.clear()


def test_unknown_encounters_are_not_exposed_by_runtime_routes(runtime_client):
    client, _ = runtime_client
    assert client.get("/api/encounters/encounter-removed/picture", headers=PRINCIPAL).status_code == 404
    assert client.post("/api/encounters/encounter-removed/feedback", json={
        "event_id": "unknown-encounter-feedback",
        "proposal_id": "anything",
        "rule_version": "anything",
        "action": "accept",
    }, headers=PRINCIPAL).status_code == 404


def test_bound_identity_resolution_failure_is_404_for_picture_and_feedback(runtime_client):
    client, store = runtime_client
    persona = store.persona("bound-persona-002-krooninen-ikääntynyt")
    record = persona.records[0].model_copy(update={"source_person_id": "foreign-source"})
    store._personas[persona.persona_id] = persona.model_copy(update={"records": [record, *persona.records[1:]]})
    assert client.get("/api/encounters/bound-encounter-002-laakitys-sairaanhoitaja/picture", headers=PRINCIPAL).status_code == 404
    assert client.post("/api/encounters/bound-encounter-002-laakitys-sairaanhoitaja/feedback", json={
        "event_id": "invalid-identity-feedback",
        "proposal_id": "anything",
        "rule_version": "anything",
        "action": "accept",
    }, headers=PRINCIPAL).status_code == 404


def test_hosted_mode_exposes_only_bound_encounters_and_no_role_only_routes(monkeypatch, runtime_client):
    client, _ = runtime_client
    monkeypatch.setenv("LUVN_HOSTED", "1")
    headers = {"X-MS-CLIENT-PRINCIPAL": "spoofed", "X-Role": "laakari"}
    hosted_rows = client.get("/api/encounters", headers=headers).json()["encounters"]
    assert hosted_rows and all(row["encounter_id"].startswith("bound-") for row in hosted_rows)
    # Regenerated before this build: no caller-supplied identity header is trusted,
    # and the old role-only persona routes stay absent.
    assert client.get("/api/personas", headers=headers).status_code == 404
    assert client.get("/api/personas/anything/picture?role=laakari", headers=headers).status_code == 404
    assert client.post("/api/personas/anything/feedback", json={"role": "laakari", "action": "accept"}, headers=headers).status_code == 404


def test_browser_cors_is_installed_for_development_only(monkeypatch):
    """H4: the permissive loopback origin is a development affordance, never a hosted one.

    With `allow_credentials`, a loopback regex in the hosted app lets any page served from a local port read
    the API with the victim's Entra session cookie and write as the victim. The middleware is therefore
    installed only outside hosting, `LUVN_DEV_ORIGINS` can pin exact origins, and the hosted app installs no
    CORS at all.
    """
    from fastapi import FastAPI
    from fastapi.middleware.cors import CORSMiddleware

    def installed(application):
        return any(middleware.cls is CORSMiddleware for middleware in application.user_middleware)

    monkeypatch.delenv("LUVN_HOSTED", raising=False)
    monkeypatch.delenv("LUVN_DEV_ORIGINS", raising=False)
    assert main._cors_configuration() == ([], main.LOOPBACK_ORIGIN_REGEX)
    development = FastAPI()
    main.install_cors(development)
    assert installed(development), "the Vite dev origin needs CORS"

    monkeypatch.setenv("LUVN_DEV_ORIGINS", "http://localhost:5173, https://presenter.example")
    assert main._cors_configuration() == (["http://localhost:5173", "https://presenter.example"], None)

    monkeypatch.setenv("LUVN_HOSTED", "1")
    assert main._cors_configuration() == ([], None)
    hosted = FastAPI()
    main.install_cors(hosted)
    assert not installed(hosted), "the hosted app must allow no browser origin"


def test_a_hostile_loopback_origin_gets_no_allow_header_from_the_hosted_app(monkeypatch):
    """H4, behaviourally: development echoes the origin; hosting does not."""
    from fastapi import FastAPI

    def probe_app():
        application = FastAPI()
        main.install_cors(application)

        @application.get("/probe")
        def probe():
            return {"ok": True}

        return application

    monkeypatch.delenv("LUVN_DEV_ORIGINS", raising=False)
    monkeypatch.delenv("LUVN_HOSTED", raising=False)
    with TestClient(probe_app()) as client:
        development = client.get("/probe", headers={"Origin": "http://localhost:5173"})
    assert development.headers.get("access-control-allow-origin") == "http://localhost:5173"

    monkeypatch.setenv("LUVN_HOSTED", "1")
    with TestClient(probe_app()) as client:
        hosted = client.get("/probe", headers={"Origin": "http://localhost:5173"})
    assert "access-control-allow-origin" not in hosted.headers


def test_health_diagnostics_and_same_origin_spa_fallback(monkeypatch, tmp_path: Path, runtime_client):
    client, _ = runtime_client
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<main>SPA shell</main>", encoding="utf-8")
    (tmp_path / "assets" / "app.js").write_text("export {}", encoding="utf-8")
    monkeypatch.setenv("LUVN_STATIC_DIR", str(tmp_path))
    monkeypatch.setenv("LUVN_BUILD_REVISION", "revision-test")
    monkeypatch.delenv("LUNA_FOUNDRY_PROJECT_ENDPOINT", raising=False)
    monkeypatch.delenv("LUNA_FOUNDRY_AGENT_NAME", raising=False)
    assert client.get("/health/live").json() == {"status": "live", "revision": "revision-test"}
    assert client.get("/health/ready").json() == {"status": "ready", "revision": "revision-test"}
    assert client.get("/api/diagnostics").json()["revision"] == "revision-test"
    assert client.get("/deep/link/encounter").text == "<main>SPA shell</main>"
    assert client.get("/assets/app.js").text == "export {}"
    assert client.get("/assets/missing.js").status_code == 404
    assert client.get("/missing.css").status_code == 404
    unknown = client.get("/api/unknown")
    assert unknown.status_code == 404
    assert unknown.headers["content-type"].startswith("application/json")


def test_diagnostics_probes_configured_agent_without_gating_readiness(monkeypatch, runtime_client):
    client, _ = runtime_client
    monkeypatch.setenv("LUNA_FOUNDRY_PROJECT_ENDPOINT", "https://project.example/api/projects/demo")
    monkeypatch.setenv("LUNA_FOUNDRY_AGENT_NAME", "configured-agent")
    monkeypatch.setenv("LUNA_FOUNDRY_AGENT_VERSION", "8")
    calls = []

    async def failed_token_probe():
        calls.append("diagnostics")
        return "failed"

    monkeypatch.setattr(main.llm, "probe_prompt_agent_token", failed_token_probe)
    diagnostics = client.get("/api/diagnostics").json()
    assert diagnostics["foundry_agent"] == {"configured": True, "token_status": "failed"}
    assert client.get("/health/ready").json()["status"] == "ready"
    assert calls == ["diagnostics"]


def test_readiness_does_not_depend_on_optional_model_services(monkeypatch, runtime_client):
    client, _ = runtime_client
    monkeypatch.delenv("LUVN_STATIC_DIR", raising=False)
    monkeypatch.delenv("LUNA_FOUNDRY_KEY", raising=False)
    monkeypatch.delenv("LUNA_FOUNDRY_BASE", raising=False)
    monkeypatch.delenv("LUNA_FOUNDRY_KEY", raising=False)
    assert client.get("/health/ready").json()["status"] == "ready"


def test_the_shell_revalidates_and_hashed_assets_are_immutable(monkeypatch, tmp_path: Path, runtime_client):
    """A shell cached without revalidation pins a browser to a previous build's bundle.

    The failure is silent and total: the revision, `/health/ready` and every gate report the new build
    while the browser keeps rendering the old one from its own cache. That is not hypothetical - it cost
    two deploys on 2026-10-08, the second misdiagnosed as a wrong image when the image was already right.
    Vite hashes asset names, so those may be cached forever; the shell must not be.
    """
    client, _ = runtime_client
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<main>SPA shell</main>", encoding="utf-8")
    (tmp_path / "assets" / "index-abc123.js").write_text("export {}", encoding="utf-8")
    (tmp_path / "favicon.svg").write_text("<svg/>", encoding="utf-8")
    monkeypatch.setenv("LUVN_STATIC_DIR", str(tmp_path))

    shell = client.get("/deep/link/encounter")
    assert shell.text == "<main>SPA shell</main>"
    assert shell.headers["cache-control"] == "no-cache"

    hashed = client.get("/assets/index-abc123.js")
    assert hashed.text == "export {}"
    assert hashed.headers["cache-control"] == "public, max-age=31536000, immutable"

    # Unhashed public files are not content-addressed, so they must revalidate like the shell.
    assert client.get("/favicon.svg").headers["cache-control"] == "no-cache"


def test_an_unknown_vocabulary_value_is_a_4xx_that_names_the_value_and_the_known_set(runtime_client):
    """A hand-edited corpus with one undeclared value used to be an opaque 500 with a traceback.

    `policy.cut` refuses an undeclared topic or record_type on purpose -- serving an empty permitted set is
    the failure it exists to prevent -- so the refusal must reach the caller as a readable 4xx. The body
    names the axis, the offending value and the known set (the mapping instruction) and leaks no path.
    """
    client, store = runtime_client
    encounter_id = "bound-encounter-002-laakitys-sairaanhoitaja"
    encounter = store.encounter_index()[encounter_id]
    persona = store.persona(encounter.persona_id)
    record_id = persona.records[0].record_id

    _bind_test_context(store, encounter_id,
                       record_updates={record_id: {"record_type": "vapaateksti",
                                                   "topic": "tuntematon_aihe"}})
    response = client.get(f"/api/encounters/{encounter_id}/picture", headers=PRINCIPAL)
    assert response.status_code == 422, response.text
    body = response.json()
    assert body["axis"] == "topic" and body["unknown"] == ["tuntematon_aihe"]
    assert "hoito" in body["known"], "the known set is the mapping instruction"
    assert body["detail"] and "Traceback" not in response.text
    assert "/Users/" not in response.text and "site-packages" not in response.text

    # The other axis, from a vendor spelling that was never mapped into a canonical one.
    _bind_test_context(store, encounter_id,
                       record_updates={record_id: {"record_type": "vendor-observation-v9"}})
    other = client.get(f"/api/encounters/{encounter_id}/picture", headers=PRINCIPAL)
    assert other.status_code == 422, other.text
    assert other.json()["axis"] == "record_type"
    assert other.json()["unknown"] == ["vendor-observation-v9"]
    assert "vapaateksti" in other.json()["known"]


# --- readiness sees the data layer; liveness deliberately does not ------------------------------------------

def test_readiness_fails_when_the_data_layer_cannot_answer(monkeypatch, runtime_client):
    """A replica that cannot reach its corpus must not receive traffic.

    Before this, readiness checked only that the SPA's `index.html` existed, so it reported `ready` with the
    database unreachable -- and every briefing such a replica accepted would then fail. The refusal carries the
    same terse body as the other not-ready path, because a probe that explained itself would be an oracle for
    what is wrong with the data layer.
    """
    client, _store = runtime_client
    monkeypatch.delenv("LUVN_STATIC_DIR", raising=False)
    monkeypatch.setenv("LUVN_BUILD_REVISION", "revision-test")
    monkeypatch.setattr(Store, "data_layer_ready", lambda self: False)

    response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json() == {"status": "not_ready", "revision": "revision-test"}


def test_liveness_stays_healthy_when_the_data_layer_is_down(monkeypatch, runtime_client):
    """The asymmetry is the whole point. Readiness gates traffic, liveness gates restarts.

    If liveness failed on a database outage, the platform would restart every replica -- turning a recoverable
    data outage into a crash loop that still cannot serve anyone the moment the database returns.
    """
    client, _store = runtime_client
    monkeypatch.setattr(Store, "data_layer_ready", lambda self: False)

    assert client.get("/health/live").status_code == 200
    assert client.get("/health/ready").status_code == 503


def test_a_fixture_backed_store_has_no_data_layer_verdict(runtime_client):
    """`None` is "not applicable", never "failed": a deployment reading fixtures must not be made unhealthy."""
    _client, store = runtime_client
    assert store.data_layer_ready() is None


def test_the_sql_probe_is_one_trivial_statement_and_raises_on_failure():
    """The probe must not read the corpus on a five-second timer, and it must report failure by raising --
    the store turns that into False and the route into a 503 without exposing the reason."""
    from app import sqldelivery

    index = sqldelivery.SqlDeliveryIndex.__new__(sqldelivery.SqlDeliveryIndex)
    seen: list[str] = []
    index._rows = lambda sql, *params: (seen.append(sql), [])[1]

    index.probe()

    assert seen == ["SELECT 1"], "the readiness probe must be a trivial statement, not a corpus read"

    def refuse(_sql, *_params):
        raise RuntimeError("the database did not answer")

    index._rows = refuse
    with pytest.raises(RuntimeError):
        index.probe()
