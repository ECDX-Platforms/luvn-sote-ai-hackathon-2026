"""Offline fail-closed checks over a separately frozen synthetic fault fixture."""
import asyncio
import hashlib
import json
from datetime import date
from pathlib import Path

import httpx
import pytest

from app import decision_model, llm, pipeline, selection
from app.encounters import Encounter
from app.policy import cut
from app.upm import Persona, Record

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "data" / "experimental" / "decision-faults-v1.json"
FIXTURE_SHA256 = "9c3ce0bff29e4343a2a45453edb317af9e606414d5cfbf27831d24e9b5a32bc3"
TODAY = date(2026, 9, 30)


def decision_response(status):
    labels = ("relevant", "irrelevant", "unclear")
    probabilities = {key: (0.9 if key == status else 0.05) for key in labels}
    def choice(value, options):
        return {"type": "choice", "choice": value, "confidence": 0.9,
                "probabilities": {key: (0.9 if key == value else 0.1 / (len(options) - 1)) for key in options}}
    return {"model": decision_model.MODEL, "answers": {
        "relevance": choice(status, labels),
        "time_sensitivity": choice("current", ("current", "historical", "unclear")),
        "subject_binding": choice("person", ("person", "other_person", "unclear")),
        "urgency": {"type": "score", "score": 2.0, "confidence": 0.8,
                    "probabilities": {"0": 0.0, "1": 0.0, "2": 1.0, "3": 0.0, "4": 0.0},
                    "legend": {str(i): level for i, level in enumerate(decision_model.URGENCY_LEVELS)}},
    }}


def test_frozen_fault_inventory_keeps_baseline_and_adds_only_mock_relevant_candidate(monkeypatch):
    raw = FIXTURE.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == FIXTURE_SHA256
    fixture = json.loads(raw)
    records = [Record.model_validate(row) for row in fixture["records"]]
    persona = Persona(persona_id="synthetic-fault-person-001", label="synthetic", source_person_id=fixture["source_person_id"], records=records)
    scoped = cut(persona, fixture["role"], fixture["purpose"])
    baseline, reasons = selection.baseline(scoped, TODAY)
    baseline_ids = {row.record_id for row in baseline}
    assert baseline_ids == {"fault-001-baseline-protected", "fault-003-keyword-decoy"}

    seen = []

    def handler(request):
        note_text = json.loads(request.content)["state"]["note"]
        status = "relevant" if "jaksamisestaan" in note_text else "unclear"
        seen.append(note_text)
        return httpx.Response(200, json=decision_response(status))

    real_client = httpx.AsyncClient
    monkeypatch.setenv("LUNA_FOUNDRY_KEY", "offline-test-only")
    monkeypatch.setattr(decision_model.httpx, "AsyncClient", lambda **kwargs: real_client(
        transport=httpx.MockTransport(handler), **kwargs
    ))
    outcomes = asyncio.run(decision_model.classify_notes(scoped, baseline_ids, TODAY))
    selected = selection.apply_decision(scoped, baseline, reasons, outcomes, TODAY)

    assert len(seen) == 4
    assert all("SYNTHETIC-DENIED-MARKER" not in text for text in seen)
    assert selected.baseline_record_ids == ("fault-001-baseline-protected", "fault-003-keyword-decoy")
    assert selected.additional_record_ids == ("fault-002-relevant-keyword-miss",)
    assert {row.record_id for row in selected.records} == baseline_ids | {"fault-002-relevant-keyword-miss"}
    assert "fault-004-other-person-mention" not in {row.record_id for row in selected.records}
    assert "fault-005-denied-record" not in scoped.ids


def test_faults_stay_out_of_picture_decision_and_foundry_while_malformed_candidate_fails_closed(monkeypatch, caplog):
    raw = FIXTURE.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == FIXTURE_SHA256
    fixture = json.loads(raw)
    records = [Record.model_validate(row) for row in fixture["records"]]
    persona = Persona(persona_id="synthetic-fault-person-001", label="synthetic",
                      source_person_id=fixture["source_person_id"], records=records)
    encounter = Encounter("synthetic-fault-encounter", persona.persona_id, fixture["role"],
                          fixture["purpose"], fixture["source_person_id"])

    decision_payloads = []
    def handler(request):
        payload = json.loads(request.content)
        decision_payloads.append(payload)
        if "note" not in payload["state"]:
            labels = ("supported", "unsupported", "unknown")
            attribution = ("attributed", "adds_claim", "unclear")
            def choice(value, options):
                return {"type": "choice", "choice": value, "confidence": 0.9,
                        "probabilities": {key: (0.9 if key == value else 0.05) for key in options}}
            return httpx.Response(200, json={"model": decision_model.MODEL, "answers": {
                "support": choice("supported", labels), "contradicts": {"type": "noul", "noul": 0.05},
                "attribution": choice("attributed", attribution),
                **{f"supports:{source['record_id']}": {"type": "noul", "noul": 0.9}
                   for source in payload["state"]["cited_sources"]}}})
        note_text = payload["state"]["note"]
        if "sisaruksen" in note_text:
            return httpx.Response(200, content=b"not-json")
        status = ("relevant" if "jaksamisestaan" in note_text else
                  "irrelevant" if "laitehuolto" in note_text else "unclear")
        return httpx.Response(200, json=decision_response(status))

    real_client = httpx.AsyncClient
    monkeypatch.setenv("LUNA_FOUNDRY_KEY", "offline-test-only")
    monkeypatch.setattr(decision_model.httpx, "AsyncClient", lambda **kwargs: real_client(
        transport=httpx.MockTransport(handler), **kwargs
    ))

    foundry_payloads = []
    async def complete_bounded(schema, instructions, user, timeout_s):
        payload = json.loads(user)
        foundry_payloads.append(payload)
        if schema is llm.FreshNarration:
            output = schema.model_validate({"sentences": [{
                "sentence_id": "s1",
                "text": "Synteettinen suojattu lähdetieto.",
                "source_ids": ["fault-001-baseline-protected"],
            }]})
        else:
            [sentence] = payload["candidate_sentences"]
            output = schema.model_validate({"verdicts": [{
                "sentence_id": sentence["sentence_id"],
                "verdict": "supported",
                "supporting_record_ids": sentence["source_ids"],
            }]})
        return llm.FoundryResult(output=output, model="offline-mock")

    monkeypatch.setattr(llm, "foundry_configured", lambda: True)
    monkeypatch.setattr(llm, "_complete_bounded", complete_bounded)
    # This test counts the narration generator's requests; the pre-fill's own generator calls are not its subject.
    from app import prefill

    monkeypatch.setattr(prefill, "forms_for_purpose", lambda *_args: [])
    picture = asyncio.run(pipeline._build_resolved_picture(encounter, persona, TODAY))

    # Baseline-selected and missed notes share the four-assessment cap; malformed
    # relative-mention input fails closed while the useful missed note is recovered.
    assert picture.selection is not None
    assert len(decision_payloads) == 5
    assert "Decision-1 relevance read failed: ValueError" in caplog.text
    assert ["fault-001-baseline-protected", "fault-003-keyword-decoy"] == picture.selection.baseline_record_ids
    assert picture.selection.additional_record_ids == ["fault-002-relevant-keyword-miss"]
    assert len(picture.selection.additional_record_ids) == 1  # mock-labeled useful additions
    assert not ({"fault-003-keyword-decoy", "fault-004-other-person-mention", "fault-005-denied-record"}
                & set(picture.selection.additional_record_ids))  # mock-labeled wrong additions: zero
    recovered = next(note for note in picture.selection.decision_notes
                     if note.record_id == "fault-002-relevant-keyword-miss")
    assert recovered.status == "relevant"
    assert picture.selection.decision_baseline_candidate_count == 2
    assert picture.selection.decision_baseline_attempted_count == 2
    assert picture.selection.decision_missed_candidate_count == 2
    assert picture.selection.decision_missed_attempted_count == 2
    assert [citation.record_id for citation in picture.citations] == ["fault-001-baseline-protected"]
    assert "fault-001-baseline-protected" in {row.record_id for row in picture.records.accessible_other}
    assert "fault-001-baseline-protected" not in {row.record_id for row in picture.records.priority}
    assert "fault-002-relevant-keyword-miss" in {row.record_id for row in picture.records.priority}
    assert len(foundry_payloads) == 1
    assert {row["record_id"] for row in foundry_payloads[0]["selected_evidence"]} == {
        "fault-001-baseline-protected", "fault-002-relevant-keyword-miss"
    }
    assert "fault-003-keyword-decoy" in {row.record_id for row in picture.records.accessible_other}
    assert set(picture.narration_input_record_ids) == {"fault-001-baseline-protected", "fault-002-relevant-keyword-miss"}
    verifier_state = decision_payloads[-1]["state"]
    assert {row["record_id"] for row in verifier_state["cited_sources"]} == {"fault-001-baseline-protected"}
    assert "fault-003-keyword-decoy" not in json.dumps(verifier_state)

    denied_markers = ("fault-005-denied-record", "SYNTHETIC-DENIED-MARKER")
    serialized = json.dumps({"decision": decision_payloads, "foundry": foundry_payloads,
                             "picture": picture.model_dump(mode="json")}, ensure_ascii=False)
    assert all(marker not in serialized for marker in denied_markers)
    assert all(marker not in json.dumps(picture.model_dump(mode="json")["trace"], ensure_ascii=False)
               for marker in denied_markers)
    assert all(marker not in caplog.text for marker in denied_markers)
    assert "fault-004-other-person-mention" not in json.dumps(foundry_payloads, ensure_ascii=False)
    assert "fault-004-other-person-mention" not in picture.model_dump_json()


def test_swapped_owner_fault_is_rejected_before_decision_or_foundry(monkeypatch):
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    records = [Record.model_validate(row) for row in fixture["records"]]
    persona = Persona(persona_id="synthetic-fault-person-001", label="synthetic",
                      source_person_id=fixture["source_person_id"], records=records)
    persona.records[1].source_person_id = "synthetic-other-person"
    encounter = Encounter("synthetic-fault-ownership-encounter", persona.persona_id, fixture["role"],
                          fixture["purpose"], fixture["source_person_id"])
    downstream_calls = []

    async def classify(*args):
        downstream_calls.append("decision")
        return []

    monkeypatch.setattr(pipeline, "classify_decision_notes", classify)
    monkeypatch.setattr(llm, "foundry_configured", lambda: True)
    monkeypatch.setattr(llm, "_complete_bounded", lambda *args: downstream_calls.append("foundry"))

    with pytest.raises(ValueError, match="source person"):
        asyncio.run(pipeline.build_picture_for_encounter(
            encounter.encounter_id, [persona], today=TODAY, encounters={encounter.encounter_id: encounter}
        ))
    assert downstream_calls == []
