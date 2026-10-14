"""Mock-only checks; never spend a real Foundry request."""
import asyncio
import importlib
import json
import time
from datetime import date, datetime, timedelta, timezone

import httpx
import pytest

from app import decision_model, selection, pipeline, llm, source
from app.encounters import Encounter
from tests.fixtures.bound_encounters import ENCOUNTERS, EVALUATION_ENCOUNTERS
from app.fixtures import load_decision_eval_personas, load_personas
from app.upm import ConsentScope
from app.decision_model import _with_throttle_retry


from app.policy import cut
from app.upm import Persona, Record

TODAY = date(2026, 9, 30)


def note(record_id, *, text="Potilas tarvitsee apua arjen askareissa.", topic="hoito", when=None, scope: ConsentScope = "treatment"):
    return Record(record_id=record_id, source="synthetic", record_type="vapaateksti",
                  authored_by_role="sairaanhoitaja", recorded_at=when or datetime(2026, 9, 20, tzinfo=timezone.utc),
                  topic=topic, sensitivity_tag="none", consent_scope=scope, payload={"teksti": text})


def response(status="relevant", *, model=decision_model.MODEL, probabilities=None,
             time_status="current", time_probs=None, subject="person", subject_probs=None,
             urgency_score=2.0, urgency_probabilities=None, usage=None):
    probabilities = probabilities or {
        "relevant": {"relevant": 0.8, "irrelevant": 0.1, "unclear": 0.1}[status],
        "irrelevant": {"relevant": 0.1, "irrelevant": 0.8, "unclear": 0.2}[status],
        "unclear": {"relevant": 0.1, "irrelevant": 0.1, "unclear": 0.6}[status],
    }
    def choice(value, labels, probs=None):
        probs = probs or {label: (0.8 if label == value else 0.2 / (len(labels) - 1)) for label in labels}
        return {"type": "choice", "choice": value, "confidence": 0.8, "probabilities": probs}
    urgency_probabilities = urgency_probabilities or {
        str(level): (1.0 if level == round(urgency_score) else 0.0)
        for level in range(len(decision_model.URGENCY_LEVELS))
    }
    body = {"model": model, "answers": {
        "relevance": choice(status, ("relevant", "irrelevant", "unclear"), probabilities),
        "time_sensitivity": choice(time_status, ("current", "historical", "unclear"), time_probs),
        "subject_binding": choice(subject, ("person", "other_person", "unclear"), subject_probs),
        "urgency": {"type": "score", "score": urgency_score, "confidence": 0.8,
                    "probabilities": urgency_probabilities,
                    "legend": {str(index): level for index, level in enumerate(decision_model.URGENCY_LEVELS)}},
    }}
    if usage is not None:
        body["usage"] = usage
    return json.dumps(body).encode()


def test_fanout_answers_are_parsed_and_malformed_or_missing_answer_fails_closed():
    parsed = decision_model._parse(response())
    assert parsed["status"] == "relevant"
    assert parsed["relevance"]["probabilities"]["relevant"] == 0.8
    assert parsed["time_sensitivity"]["choice"] == "current"
    assert parsed["subject_binding"]["choice"] == "person"
    assert parsed["urgency"]["score"] == 2.0
    assert parsed["urgency"]["probabilities"] == {"0": 0.0, "1": 0.0, "2": 1.0, "3": 0.0, "4": 0.0}
    request = decision_model._request("sairaanhoitaja", "medication_follow_up", "tabletti jäi ottamatta")
    assert set(request["questions"]) == {"relevance", "time_sensitivity", "subject_binding", "urgency"}
    assert request["questions"]["urgency"]["type"] == "score"
    assert request["questions"]["urgency"]["criteria"] == list(decision_model.URGENCY_LEVELS)
    # The provider is no longer assumed to be Decision-1: a response naming an unaccepted model is refused, and
    # the message is provider-neutral because Decision-1 echoes its own name back.
    with pytest.raises(ValueError, match="unexpected decision model"):
        decision_model._parse(response(model="decision-latest"))
    # The service reports its own identity, and that is exactly what the contract accepts.
    assert decision_model._parse(response(model="microsoft-decision-1"))["model"] == "microsoft-decision-1"
    with pytest.raises(ValueError, match="probabilities"):
        decision_model._parse(response(probabilities={"relevant": 0.5, "irrelevant": 0.1, "unclear": 0.1}))
    malformed = json.loads(response())
    del malformed["answers"]["time_sensitivity"]
    with pytest.raises(ValueError, match="answer IDs"):
        decision_model._parse(json.dumps(malformed).encode())
    bad_score = json.loads(response())
    bad_score["answers"]["urgency"]["score"] = 4.5
    with pytest.raises(ValueError, match="urgency score value"):
        decision_model._parse(json.dumps(bad_score).encode())
    bad_legend = json.loads(response())
    bad_legend["answers"]["urgency"]["legend"]["4"] = "mismatched level"
    with pytest.raises(ValueError, match="urgency legend"):
        decision_model._parse(json.dumps(bad_legend).encode())
    with pytest.raises(ValueError, match="JSON"):
        decision_model._parse(b"not-json")


def test_risk_scaled_thresholds_refuse_near_ties_and_use_stricter_omission_floor():
    near_margin = decision_model._parse(response(probabilities={
        "relevant": 0.62, "irrelevant": 0.38, "unclear": 0.0}))
    assert near_margin["status"] == "unclear"
    assert near_margin["gate_refused"] is True

    above_floor = decision_model._parse(response(probabilities={
        "relevant": 0.7, "irrelevant": 0.2, "unclear": 0.1}))
    assert above_floor["status"] == "relevant"
    assert above_floor["gate_refused"] is False

    below_floor = decision_model._parse(response(probabilities={
        "relevant": 0.55, "irrelevant": 0.25, "unclear": 0.2}))
    assert below_floor["status"] == "unclear"
    assert below_floor["gate_refused"] is True
    assert below_floor["relevance"]["probabilities"]["relevant"] == 0.55  # raw answer stays auditable

    borderline_omission = decision_model._parse(response(status="irrelevant", probabilities={
        "relevant": 0.15, "irrelevant": 0.65, "unclear": 0.2}))
    assert borderline_omission["status"] == "unclear"
    assert borderline_omission["gate_refused"] is True

    above_omission = decision_model._parse(response(status="irrelevant", probabilities={
        "relevant": 0.1, "irrelevant": 0.75, "unclear": 0.15}))
    assert above_omission["status"] == "irrelevant"


def test_decision_gate_requires_current_and_person_signal():
    assert decision_model._parse(response(time_status="historical"))["status"] == "unclear"
    assert decision_model._parse(response(subject="other_person"))["status"] == "unclear"


def test_negation_question_is_dropped_from_the_fan_out():
    """A source-wide negation label cannot safely drive a source-wide prose directive, so the
    question is dropped rather than paid for: the request has no negation key and a stray
    negation answer is rejected (ADR-0009)."""
    request = decision_model._request("sairaanhoitaja", "medication_follow_up", "tabletti jäi ottamatta")
    assert "negation" not in request["questions"]
    stray = json.loads(response())
    stray["answers"]["negation"] = {"type": "noul", "noul": 0.9}
    with pytest.raises(ValueError, match="answer IDs"):
        decision_model._parse(json.dumps(stray).encode())


def test_score_is_strictly_validated_and_does_not_change_selection_status():
    low = decision_model._parse(response(urgency_score=0.0, urgency_probabilities={
        "0": 1.0, "1": 0.0, "2": 0.0, "3": 0.0, "4": 0.0}))
    high = decision_model._parse(response(urgency_score=4.0, urgency_probabilities={
        "0": 0.0, "1": 0.0, "2": 0.0, "3": 0.0, "4": 1.0}))
    assert low["status"] == high["status"] == "relevant"
    assert low["urgency"]["score"] == 0.0 and high["urgency"]["score"] == 4.0
    rounded = decision_model._parse(response(urgency_score=1.95, urgency_probabilities={
        "0": 0.2, "1": 0.2, "2": 0.2, "3": 0.2, "4": 0.2}))
    assert rounded["urgency"]["score"] == 1.95
    inconsistent = json.loads(response(urgency_probabilities={
        "0": 0.2, "1": 0.2, "2": 0.2, "3": 0.2, "4": 0.2}))
    inconsistent["answers"]["urgency"]["score"] = 1.7
    with pytest.raises(ValueError, match="score disagrees"):
        decision_model._parse(json.dumps(inconsistent).encode())
    malformed = json.loads(response())
    malformed["answers"]["urgency"]["probabilities"].pop("4")
    with pytest.raises(ValueError, match="urgency probabilities"):
        decision_model._parse(json.dumps(malformed).encode())


def test_high_relevance_cannot_override_an_uncertain_auxiliary_answer():
    """A confident relevance answer must not authorize an addition when the time
    or subject question is a near-uniform tie: low confidence means do not act."""
    uncertain_time = decision_model._parse(response(time_probs={
        "current": 0.35, "historical": 0.34, "unclear": 0.31}))
    assert uncertain_time["status"] == "unclear" and uncertain_time["gate_refused"] is True

    uncertain_subject = decision_model._parse(response(subject_probs={
        "person": 0.36, "other_person": 0.34, "unclear": 0.30}))
    assert uncertain_subject["status"] == "unclear" and uncertain_subject["gate_refused"] is True


def test_only_post_cut_permitted_unselected_notes_are_sent(monkeypatch):
    records = [note("baseline", text="Mieliala on laskenut.", topic="mielenterveys"),
               note("candidate", text="Arjessa jaksaminen on vähentynyt."),
               note("denied", scope="social_care"),
               note("old", when=datetime(2024, 1, 1, tzinfo=timezone.utc))]
    scoped = cut(Persona(persona_id="synthetic", label="Synthetic", records=records), "sairaanhoitaja", "mental_health_assessment")
    base, reasons = selection.baseline(scoped, TODAY)
    seen = []

    def handler(request):
        state = json.loads(request.content)["state"]
        seen.append(state)
        assert request.headers["api-key"] == "fake"
        return httpx.Response(200, content=response())

    real = httpx.AsyncClient
    monkeypatch.setenv("LUNA_FOUNDRY_KEY", "fake")
    monkeypatch.setattr(decision_model.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    outcomes = asyncio.run(decision_model.classify_notes(scoped, {r.record_id for r in base}, TODAY))
    # L3C: the historical note ("old", 2024) is now sent too, with its status as data. The withheld one is not.
    assert len(seen) == 3
    assert seen[0]["encounter_role"] == "sairaanhoitaja"
    assert seen[0]["encounter_purpose"] == "mental_health_assessment"
    assert "Mieliala" in seen[0]["note"] and "jaksaminen" in seen[1]["note"]
    assert seen[1]["note"].startswith("ajallisuus: current") and seen[2]["note"].startswith("ajallisuus: history")
    assert "denied" not in json.dumps(seen)
    result = selection.apply_decision(scoped, base, reasons, outcomes, TODAY)
    assert result.baseline_record_ids == ("baseline",)
    assert result.additional_record_ids == ("candidate", "old")
    assert {r.record_id for r in result.records} == {"baseline", "candidate", "old"}
    assert result.reasons["candidate"] == ("decision:mental_health_assessment:relevant:vapaateksti:current",)
    assert result.reasons["old"] == ("decision:mental_health_assessment:relevant:vapaateksti:history",)


@pytest.mark.parametrize("status", ["irrelevant", "unclear", "timeout", "invalid_response", "unavailable"])
def test_only_relevant_adds_and_baseline_is_immutable(status):
    baseline = note("protected", text="Mieliala on laskenut.", topic="mielenterveys")
    candidate = note("candidate")
    scoped = cut(Persona(persona_id="synthetic", label="Synthetic", records=[baseline, candidate]), "sairaanhoitaja", "mental_health_assessment")
    base, reasons = selection.baseline(scoped, TODAY)
    result = selection.apply_decision(scoped, base, reasons, [{"record_id": "candidate", "status": status}], TODAY)
    assert result.baseline_record_ids == ("protected",)
    assert result.additional_record_ids == ()
    assert [r.record_id for r in result.records] == ["protected"]


@pytest.mark.parametrize("status", ["irrelevant", "unclear", "invalid_response", "timeout", "unavailable"])
def test_nonrelevant_or_failed_baseline_assessment_never_removes_source(status):
    keyword_note = note("keyword-baseline", text="Mieliala on laskenut.")
    scoped = cut(Persona(persona_id="synthetic", label="Synthetic", records=[keyword_note]),
                 "sairaanhoitaja", "mental_health_assessment")
    baseline, reasons = selection.baseline(scoped, TODAY)
    result = selection.apply_decision(scoped, baseline, reasons,
                                 [{"record_id": "keyword-baseline", "status": status}], TODAY)
    assert result.baseline_record_ids == ("keyword-baseline",)
    assert result.additional_record_ids == ()
    assert result.emphasized_record_ids == ()
    assert [record.record_id for record in result.records] == ["keyword-baseline"]


def test_missing_key_or_rounded_invalid_http_keeps_baseline(monkeypatch):
    scoped = cut(Persona(persona_id="synthetic", label="Synthetic", records=[note("candidate")]),
                 "sairaanhoitaja", "functioning_service_needs")
    monkeypatch.delenv("LUNA_FOUNDRY_KEY", raising=False)
    [unavailable] = asyncio.run(decision_model.classify_notes(scoped, set(), TODAY))
    assert unavailable == {"record_id": "candidate", "status": "unavailable",
                          "candidate_group": "missed", "attempted": False}
    monkeypatch.setenv("LUNA_FOUNDRY_KEY", "fake")
    real = httpx.AsyncClient
    monkeypatch.setattr(decision_model.httpx, "AsyncClient", lambda **kw: real(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=b"not-json")), **kw))
    [result] = asyncio.run(decision_model.classify_notes(scoped, set(), TODAY))
    assert result["record_id"] == "candidate" and result["status"] == "invalid_response"
    assert result["candidate_group"] == "missed" and result["attempted"] is True
    assert result["elapsed_ms"] >= 0


def test_rate_limited_note_is_unavailable_and_accounted(monkeypatch):
    scoped = cut(Persona(persona_id="synthetic", label="Synthetic", records=[note("candidate")]),
                 "sairaanhoitaja", "functioning_service_needs")
    real = httpx.AsyncClient
    monkeypatch.setenv("LUNA_FOUNDRY_KEY", "fake")
    monkeypatch.setattr(decision_model.httpx, "AsyncClient", lambda **kwargs: real(
        transport=httpx.MockTransport(lambda request: httpx.Response(429, request=request)), **kwargs))
    [row] = asyncio.run(decision_model.classify_notes(scoped, set(), TODAY))
    assert row["status"] == "unavailable" and row["attempted"] is True
    assert decision_model.coverage([row])["decision_unavailable_count"] == 1


def test_kayntiaika_temporal_status_is_data_on_the_candidate_not_a_filter():
    """L3C: a late-entered old visit is a historical candidate carrying its status, not a silent exclusion.
    (L3B asserted this record was filtered out; that was the currentness gate the work order removes.)"""
    late_entry = note("late-entry", when=datetime(2026, 9, 28, tzinfo=timezone.utc)).model_copy(update={
        "käyntiaika": datetime(2025, 9, 28, tzinfo=timezone.utc),
    })
    scoped = cut(Persona(persona_id="synthetic", label="Synthetic", records=[late_entry]),
                 "sairaanhoitaja", "mental_health_assessment")
    assert [(c.record.record_id, c.temporal_status) for c in
            decision_model._candidates(scoped, {late_entry.record_id}, TODAY)] == [("late-entry", "history")]

    current_visit = late_entry.model_copy(update={"käyntiaika": datetime(2026, 9, 28, tzinfo=timezone.utc)})
    scoped = cut(Persona(persona_id="synthetic", label="Synthetic", records=[current_visit]),
                 "sairaanhoitaja", "mental_health_assessment")
    assert [(c.record.record_id, c.temporal_status) for c in
            decision_model._candidates(scoped, {current_visit.record_id}, TODAY)] == [(current_visit.record_id, "current")]


def test_concurrent_reads_return_in_deterministic_candidate_order(monkeypatch):
    records = [note("slow", text="slow Mieliala huono.", when=datetime(2026, 9, 1, tzinfo=timezone.utc)),
               note("fast", text="fast Toinen huomio.", when=datetime(2026, 9, 2, tzinfo=timezone.utc))]
    scoped = cut(Persona(persona_id="synthetic", label="Synthetic", records=records),
                 "sairaanhoitaja", "mental_health_assessment")

    class DelayedTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            text = json.loads(request.content)["state"]["note"]
            await asyncio.sleep(0.04 if "slow" in text else 0.001)
            return httpx.Response(200, content=response(), request=request)

    real = httpx.AsyncClient
    monkeypatch.setenv("LUNA_FOUNDRY_KEY", "fake")
    monkeypatch.setattr(decision_model.httpx, "AsyncClient", lambda **kwargs: real(
        transport=DelayedTransport(), **kwargs))
    rows = asyncio.run(decision_model.classify_notes(scoped, set(), TODAY))
    # Newest first, whatever order the replies arrive in.
    assert [row["record_id"] for row in rows] == ["fast", "slow"]
    assert [row["status"] for row in rows] == ["relevant", "relevant"]


def test_grounding_verifier_abstains_and_keeps_sentence_evidence_isolated(monkeypatch):
    records = [note(f"source-{index}", text="source text") for index in range(4)]
    scoped = cut(Persona(persona_id="synthetic", label="Synthetic", records=records),
                 "sairaanhoitaja", "mental_health_assessment")
    labels = ("supported", "unsupported", "unknown")
    attribution_labels = ("attributed", "adds_claim", "unclear")
    seen = []

    def choice(value, options, probabilities=None):
        probabilities = probabilities or {key: (0.9 if key == value else 0.05) for key in options}
        return {"type": "choice", "choice": value, "confidence": 0.9, "probabilities": probabilities}

    def handler(request):
        payload = json.loads(request.content)
        seen.append(payload["state"])
        sentence = payload["state"]["sentence"]
        support = "unsupported" if sentence == "contradicted" else "unknown" if sentence == "uncertain" else "supported"
        support_probs = ({"supported": 0.65, "unsupported": 0.2, "unknown": 0.15}
                         if sentence == "low-confidence" else None)
        return httpx.Response(200, json={"model": decision_model.MODEL, "answers": {
            "support": choice(support, labels, support_probs),
            "contradicts": {"type": "noul", "noul": 0.9 if sentence == "contradicted" else 0.05},
            "attribution": choice("attributed", attribution_labels),
            **{f"supports:{source['record_id']}": {"type": "noul", "noul": 0.9}
               for source in payload["state"]["cited_sources"]},
        }})

    candidate = llm.FreshNarration(sentences=[
        llm.NarrationSentence(sentence_id=f"s{index + 1}", text=text,
                                            source_ids=[f"source-{index}"])
        for index, text in enumerate(("supported", "contradicted", "low-confidence", "uncertain"))
    ])
    real = httpx.AsyncClient
    monkeypatch.setenv("LUNA_FOUNDRY_KEY", "fake")
    monkeypatch.setattr(decision_model.httpx, "AsyncClient", lambda **kwargs: real(
        transport=httpx.MockTransport(handler), **kwargs))
    result = asyncio.run(decision_model.verify_narration(scoped, candidate, TODAY, 2.0))
    assert [row["verdict"] for row in result["verdicts"]] == ["supported", "unsupported", "unknown", "unknown"]
    assert all(len(state["cited_sources"]) == 1 for state in seen)
    assert [state["cited_sources"][0]["record_id"] for state in seen] == [f"source-{i}" for i in range(4)]


def test_decision_outage_is_counted_as_unavailable_not_budget_exhaustion(monkeypatch):
    records = [note(f"record-{index:03d}", text=f"note {index}") for index in range(80)]
    scoped = cut(Persona(persona_id="synthetic", label="Synthetic", records=records),
                 "sairaanhoitaja", "mental_health_assessment")
    monkeypatch.delenv("LUNA_FOUNDRY_KEY", raising=False)
    rows = asyncio.run(decision_model.classify_notes(scoped, set(), TODAY))
    counts = decision_model.coverage(rows)
    assert counts["decision_candidate_count"] == 80
    assert counts["decision_unavailable_count"] == 80
    assert counts["decision_budget_exhausted_count"] == 0
    assert counts["decision_not_shortlisted_count"] == 0


def test_per_note_failure_is_attempted_and_unavailable_never_assessed(monkeypatch):
    """A call that was made and failed (an HTTP 500 here) is attempted and unavailable, not assessed,
    and the disjoint buckets still reconcile to the candidate count."""
    scoped = cut(Persona(persona_id="synthetic", label="Synthetic", records=[note("candidate")]),
                 "sairaanhoitaja", "mental_health_assessment")
    real = httpx.AsyncClient
    monkeypatch.setenv("LUNA_FOUNDRY_KEY", "fake")
    monkeypatch.setattr(decision_model.httpx, "AsyncClient", lambda **kwargs: real(
        transport=httpx.MockTransport(lambda request: httpx.Response(500)), **kwargs))
    [row] = asyncio.run(decision_model.classify_notes(scoped, set(), TODAY))
    assert row["attempted"] is True and row["status"] == "unavailable"
    counts = decision_model.coverage([row])
    assert counts["decision_attempted_count"] == 1
    assert counts["decision_unavailable_count"] == 1
    assert counts["decision_assessed_count"] == 0
    assert (counts["decision_assessed_count"] + counts["decision_not_shortlisted_count"]
            + counts["decision_budget_exhausted_count"] + counts["decision_too_long_count"]
            + counts["decision_unavailable_count"]) == counts["decision_candidate_count"] == 1


def test_malformed_response_is_attempted_and_unavailable_never_assessed(monkeypatch):
    """A malformed 200 is an attempt with no usable answer: `invalid_response` folds into the `unavailable`
    bucket instead of falling out of the disjoint sum, and is never counted as assessed."""
    scoped = cut(Persona(persona_id="synthetic", label="Synthetic", records=[note("candidate")]),
                 "sairaanhoitaja", "mental_health_assessment")
    real = httpx.AsyncClient
    monkeypatch.setenv("LUNA_FOUNDRY_KEY", "fake")
    monkeypatch.setattr(decision_model.httpx, "AsyncClient", lambda **kwargs: real(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=b"not json")), **kwargs))
    [row] = asyncio.run(decision_model.classify_notes(scoped, set(), TODAY))
    assert row["attempted"] is True and row["status"] == "invalid_response"
    counts = decision_model.coverage([row])
    assert counts["decision_attempted_count"] == 1
    assert counts["decision_unavailable_count"] == 1
    assert counts["decision_assessed_count"] == 0
    assert (counts["decision_assessed_count"] + counts["decision_not_shortlisted_count"]
            + counts["decision_budget_exhausted_count"] + counts["decision_too_long_count"]
            + counts["decision_unavailable_count"]) == counts["decision_candidate_count"] == 1


def test_overlong_note_is_skipped_without_a_model_call_and_counted(monkeypatch):
    """A note over the byte cap never reaches the model, is reported as `too_long`
    (not `budget_exhausted`), and is accounted for in coverage."""
    long_text = "x" * 200
    records = [note("record-long", text=long_text), note("record-short", text="lyhyt kirjaus")]
    scoped = cut(Persona(persona_id="synthetic", label="Synthetic", records=records),
                 "sairaanhoitaja", "mental_health_assessment")

    bodies: list[str] = []
    real = httpx.AsyncClient
    monkeypatch.setenv("LUNA_FOUNDRY_KEY", "fake")
    monkeypatch.setattr(decision_model, "MAX_TEXT_BYTES", 50)

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(request.content.decode())
        return httpx.Response(200, content=response())

    monkeypatch.setattr(decision_model.httpx, "AsyncClient",
                        lambda **kwargs: real(transport=httpx.MockTransport(handler), **kwargs))
    rows = asyncio.run(decision_model.classify_notes(scoped, set(), TODAY))
    by_id = {row["record_id"]: row for row in rows}
    assert by_id["record-long"]["status"] == "too_long"
    assert by_id["record-long"]["attempted"] is False
    assert by_id["record-short"]["attempted"] is True
    # Only the short note left the process; the overlong text was never sent.
    assert len(bodies) == 1
    assert "lyhyt kirjaus" in bodies[0] and long_text not in bodies[0]
    counts = decision_model.coverage(rows)
    assert counts["decision_too_long_count"] == 1
    assert counts["decision_assessed_count"] == 1


def test_verifier_returns_only_the_cited_records_it_judged_supporting(monkeypatch):
    """A sentence citing A and B where only A supports it must publish only A: the
    per-source question decides, not the sentence's whole citation list."""
    records = [note("source-a", text="a"), note("source-b", text="b")]
    scoped = cut(Persona(persona_id="synthetic", label="Synthetic", records=records),
                 "sairaanhoitaja", "mental_health_assessment")
    candidate = llm.FreshNarration(sentences=[
        llm.NarrationSentence(sentence_id="s1", text="Osittain tuettu lause.",
                              source_ids=["source-a", "source-b"]),
    ])

    def handler(request):
        payload = json.loads(request.content)
        def choice(value, options):
            return {"type": "choice", "choice": value, "confidence": 0.9,
                    "probabilities": {key: (0.9 if key == value else 0.05) for key in options}}
        return httpx.Response(200, json={"model": decision_model.MODEL, "answers": {
            "support": choice("supported", ("supported", "unsupported", "unknown")),
            "contradicts": {"type": "noul", "noul": 0.05},
            "attribution": choice("attributed", ("attributed", "adds_claim", "unclear")),
            "supports:source-a": {"type": "noul", "noul": 0.9},
            "supports:source-b": {"type": "noul", "noul": 0.05},
        }})

    real = httpx.AsyncClient
    monkeypatch.setenv("LUNA_FOUNDRY_KEY", "fake")
    monkeypatch.setattr(decision_model.httpx, "AsyncClient", lambda **kwargs: real(
        transport=httpx.MockTransport(handler), **kwargs))
    result = asyncio.run(decision_model.verify_narration(scoped, candidate, TODAY, 2.0))
    [verdict] = result["verdicts"]
    assert verdict["verdict"] == "supported"
    assert verdict["supporting_record_ids"] == ["source-a"]

    from app import grounding

    checked = grounding.validate_fresh_candidate(scoped, candidate, ["source-a", "source-b"])
    grounding_result, status, _ = grounding.accept_supported_candidate(
        checked, {"verdicts": [{"sentence_id": "s1", "verdict": verdict["verdict"],
                                 "supporting_record_ids": verdict["supporting_record_ids"]}]})
    assert status == "supported"
    assert [row.record_id for row in grounding_result.citations] == ["source-a"]
    assert grounding_result.dropped_citations == ["source-b"]


def test_slow_response_hits_absolute_budget_without_adding(monkeypatch):
    class Slow(httpx.AsyncByteStream):
        async def __aiter__(self):
            while True:
                await asyncio.sleep(0.02)
                yield b"{"

        async def aclose(self):
            pass

    scoped = cut(Persona(persona_id="synthetic", label="Synthetic", records=[note("candidate")]),
                 "sairaanhoitaja", "functioning_service_needs")
    real = httpx.AsyncClient
    monkeypatch.setenv("LUNA_FOUNDRY_KEY", "fake")
    monkeypatch.setattr(decision_model, "CALL_BUDGET_S", 0.06)
    monkeypatch.setattr(decision_model, "TOTAL_BUDGET_S", 0.07)
    monkeypatch.setattr(decision_model.httpx, "AsyncClient", lambda **kw: real(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, stream=Slow())), **kw))
    [result] = asyncio.run(decision_model.classify_notes(scoped, set(), TODAY))
    assert result["status"] == "unavailable"
    assert result["elapsed_ms"] < 250
    assert decision_model.coverage([result])["decision_unavailable_count"] == 1


def test_encounter_picture_decision_adds_source_without_decision_or_proposal_change(monkeypatch):
    encounter, persona = pipeline.resolve_encounter("bound-encounter-004-sosiaalipalvelut-sosiaalityontekija", load_personas(), ENCOUNTERS)
    monkeypatch.setattr(pipeline.llm, "foundry_configured", lambda: False)
    async def unavailable(scoped, baseline_ids, today):
        return []
    monkeypatch.setattr(pipeline, "classify_decision_notes", unavailable)
    baseline_picture = asyncio.run(pipeline._build_resolved_picture(encounter, persona, TODAY))

    async def relevant(scoped, baseline_ids, today):
        assert "bound-note-009" in scoped.ids and "bound-note-009" not in baseline_ids
        assert all(r.record_id in scoped.ids for r in scoped.records)
        return [{"record_id": "bound-note-009", "status": "relevant", "model": decision_model.MODEL},
                {"record_id": "not-permitted", "status": "relevant", "model": decision_model.MODEL},
                {"record_id": "bound-note-008", "status": "irrelevant", "model": decision_model.MODEL}]
    monkeypatch.setattr(pipeline, "classify_decision_notes", relevant)
    picture = asyncio.run(pipeline._build_resolved_picture(encounter, persona, TODAY))
    assert picture.selection is not None
    assert picture.selection.additional_record_ids == ["bound-note-009"]
    assert [x.record_id for x in picture.selection.decision_notes] == ["bound-note-009"]
    assert "not-permitted" not in picture.model_dump_json()
    assert (picture.situation, picture.risk, picture.actions, picture.assessments, picture.proposals) == (
        baseline_picture.situation, baseline_picture.risk, baseline_picture.actions, baseline_picture.assessments, baseline_picture.proposals)
    assert "bound-note-009" in {r.record_id for r in picture.records.priority}


def test_picture_promotes_only_decision_relevant_selected_notes_and_keeps_baseline(monkeypatch):
    baseline_relevant = note("baseline-relevant", text="Mieliala on laskenut.")
    baseline_unclear = note("baseline-unclear", text="Masennus ei helpota.")
    missed_relevant = note("missed-relevant", text="Teksti ilman avainsanaa.")
    missed_irrelevant = note("missed-irrelevant", text="Hallinnollinen teksti.")
    persona = Persona(persona_id="synthetic", label="Synthetic",
                      records=[baseline_relevant, baseline_unclear, missed_relevant, missed_irrelevant])
    encounter = Encounter("synthetic-encounter", persona.persona_id, "sairaanhoitaja",
                          "mental_health_assessment")
    monkeypatch.setattr(pipeline.llm, "foundry_configured", lambda: False)

    async def decision_on(scoped, baseline_ids, today):
        assert baseline_ids == {"baseline-relevant", "baseline-unclear"}
        return [
            {"record_id": "baseline-relevant", "status": "relevant", "candidate_group": "baseline", "attempted": True},
            {"record_id": "missed-relevant", "status": "relevant", "candidate_group": "missed", "attempted": True},
            {"record_id": "baseline-unclear", "status": "unclear", "candidate_group": "baseline", "attempted": True},
            {"record_id": "missed-irrelevant", "status": "irrelevant", "candidate_group": "missed", "attempted": True},
        ]

    monkeypatch.setattr(pipeline, "classify_decision_notes", decision_on)
    on = asyncio.run(pipeline._build_resolved_picture(encounter, persona, TODAY))
    assert [row.record_id for row in on.records.priority] == ["baseline-relevant", "missed-relevant"]
    assert [row.record_id for row in on.records.accessible_other] == ["baseline-unclear"]
    assert on.selection is not None
    assert on.selection.baseline_record_ids == ["baseline-relevant", "baseline-unclear"]
    assert on.selection.additional_record_ids == ["missed-relevant"]
    coverage = on.model_dump(mode="json")["selection"]
    assert coverage["decision_baseline_candidate_count"] == 2
    assert coverage["decision_baseline_attempted_count"] == 2
    assert coverage["decision_missed_candidate_count"] == 2
    assert coverage["decision_missed_attempted_count"] == 2
    assert "missed-irrelevant" not in on.model_dump_json()

    async def decision_off(scoped, baseline_ids, today):
        return []

    monkeypatch.setattr(pipeline, "classify_decision_notes", decision_off)
    off = asyncio.run(pipeline._build_resolved_picture(encounter, persona, TODAY))
    assert off.selection is not None and off.selection.baseline_record_ids == on.selection.baseline_record_ids
    assert (off.situation, off.risk, off.actions, off.assessments, off.proposals) == (
        on.situation, on.risk, on.actions, on.assessments, on.proposals)
    assert {row.record_id for row in off.records.priority + off.records.accessible_other} == set(on.selection.baseline_record_ids)


def test_client_constructor_failure_keeps_baseline(monkeypatch):
    scoped = cut(Persona(persona_id="synthetic", label="Synthetic", records=[note("candidate")]),
                 "sairaanhoitaja", "functioning_service_needs")
    monkeypatch.setenv("LUNA_FOUNDRY_KEY", "fake")
    def broken_client(**kwargs):
        raise ValueError("bad proxy configuration")
    monkeypatch.setattr(decision_model.httpx, "AsyncClient", broken_client)
    [unavailable] = asyncio.run(decision_model.classify_notes(scoped, set(), TODAY))
    assert unavailable == {"record_id": "candidate", "status": "unavailable",
                          "candidate_group": "missed", "attempted": False}


def test_decision_coverage_counts_attempts_without_claiming_missing_usage():
    rows = [
        {"record_id": "baseline", "candidate_group": "baseline", "status": "irrelevant", "attempted": True},
        {"record_id": "missed", "candidate_group": "missed", "status": "budget_exhausted", "attempted": False},
    ]
    counts = decision_model.coverage(rows)
    assert counts["decision_baseline_attempted_count"] == 1
    assert counts["decision_missed_candidate_count"] == 1
    assert counts["decision_attempted_count"] == 1
    assert counts["decision_usage_complete"] is False
    # Only the three valid verdicts are assessed; not_shortlisted, budget_exhausted and too_long never are.
    assert decision_model.coverage([{"status": s, "attempted": True} for s in ("relevant", "irrelevant", "unclear")]
                                   + [{"status": s, "attempted": False} for s in ("not_shortlisted", "budget_exhausted", "too_long")])["decision_assessed_count"] == 3


def test_gate_refused_baseline_note_stays_citable_and_records_its_probabilities(monkeypatch):
    """A baseline note the addition gate refused is not acted on, but stays in the
    briefing and its probabilities stay auditable under the disclosed-source rule."""
    baseline_note = note("baseline-note", text="Mieliala on laskenut.")
    persona = Persona(persona_id="synthetic", label="Synthetic", records=[baseline_note])
    encounter = Encounter("synthetic-gate-refused", persona.persona_id, "sairaanhoitaja",
                          "mental_health_assessment")
    monkeypatch.setattr(pipeline.llm, "foundry_configured", lambda: False)

    async def refused(_scoped, _baseline_ids, _today):
        return [{"record_id": "baseline-note", "status": "unclear", "candidate_group": "baseline",
                 "attempted": True, "model": decision_model.MODEL, "elapsed_ms": 12,
                 "input_tokens": 40, "output_tokens": 0, "gate_refused": True,
                 "relevance": {"choice": "relevant", "confidence": 0.5,
                               "probabilities": {"relevant": 0.55, "irrelevant": 0.25, "unclear": 0.2}},
                 "time_sensitivity": {"choice": "current"}, "subject_binding": {"choice": "person"}}]

    monkeypatch.setattr(pipeline, "classify_decision_notes", refused)
    picture = asyncio.run(pipeline._build_resolved_picture(encounter, persona, TODAY))
    assert picture.selection is not None
    body = picture.model_dump(mode="json")["selection"]
    assert body["decision_gate_refused_count"] == 1
    assert body["additional_record_ids"] == []
    assert body["baseline_record_ids"] == ["baseline-note"]
    [outcome] = body["decision_notes"]
    assert outcome["gate_refused"] is True
    assert outcome["relevance_probabilities"] == {"relevant": 0.55, "irrelevant": 0.25, "unclear": 0.2}
    assert outcome["relevance_confidence"] == 0.5


def test_unavailable_baseline_note_is_disclosed_per_row_and_kept(monkeypatch):
    baseline_note = note("baseline-note", text="Mieliala on laskenut.")
    persona = Persona(persona_id="synthetic", label="Synthetic", records=[baseline_note])
    encounter = Encounter("synthetic-unavailable", persona.persona_id, "sairaanhoitaja",
                          "mental_health_assessment")
    monkeypatch.setattr(pipeline.llm, "foundry_configured", lambda: False)

    async def unavailable(_scoped, _baseline_ids, _today):
        return [{"record_id": "baseline-note", "status": "unavailable", "candidate_group": "baseline",
                 "attempted": True}]

    monkeypatch.setattr(pipeline, "classify_decision_notes", unavailable)
    picture = asyncio.run(pipeline._build_resolved_picture(encounter, persona, TODAY))
    body = picture.model_dump(mode="json")["selection"]
    assert body["decision_status"] == "unavailable"
    assert body["baseline_record_ids"] == ["baseline-note"]
    assert body["decision_notes"] == [{"record_id": "baseline-note", "status": "unavailable", "model": None,
                                  "elapsed_ms": None, "input_tokens": None, "output_tokens": None,
                                  "relevance_probabilities": {}, "gate_probabilities": {},
                                  "relevance_confidence": None, "time_sensitivity": None,
                                  "negation_probability": None, "subject_binding": None,
                                  "gate_refused": False}]
    assert any(row.record_id == "baseline-note" for row in picture.records.accessible_other)


def test_urgency_orders_existing_priority_without_changing_membership():
    low = note("low", text="Mieliala vaihtelee.", topic="mielenterveys")
    high = note("high", text="Ahdistus haittaa arkea.", topic="mielenterveys")
    scoped = cut(Persona(persona_id="synthetic", label="Synthetic", records=[low, high]),
                 "sairaanhoitaja", "mental_health_assessment")
    baseline, reasons = selection.baseline(scoped, TODAY)
    probabilities = lambda score: {str(i): float(i == score) for i in range(5)}
    selected = selection.apply_decision(scoped, baseline, reasons, [
        {"record_id": "low", "status": "relevant", "urgency": {"score": 0.0, "probabilities": probabilities(0)}},
        {"record_id": "high", "status": "relevant", "urgency": {"score": 4.0, "probabilities": probabilities(4)}},
    ], TODAY)
    assert {record.record_id for record in selected.records} == {"low", "high"}
    assert {record.record_id for record in selected.narration_records} == {"low", "high"}
    assert selected.priority_ids(["low", "high"]) == ["high", "low"]


def test_picture_priority_uses_urgency_without_changing_selected_records(monkeypatch):
    low = note("routine", text="Mieliala vaihtelee.", topic="mielenterveys")
    high = note("urgent", text="Ahdistus haittaa arkea.", topic="mielenterveys")
    persona = Persona(persona_id="synthetic", label="Synthetic", records=[low, high])
    encounter = Encounter("synthetic-urgency-order", persona.persona_id, "sairaanhoitaja",
                          "mental_health_assessment")
    distributions = {
        "routine": {"0": 1.0, "1": 0.0, "2": 0.0, "3": 0.0, "4": 0.0},
        "urgent": {"0": 0.0, "1": 0.0, "2": 0.0, "3": 0.0, "4": 1.0},
    }

    async def classify(_scoped, _baseline_ids, _today):
        return [{"record_id": rid, "status": "relevant", "attempted": True,
                 "relevance": {"probabilities": {"relevant": 0.9}},
                 "urgency": {"score": score, "probabilities": distributions[rid]}}
                for rid, score in (("routine", 0.0), ("urgent", 4.0))]

    monkeypatch.setattr(pipeline, "classify_decision_notes", classify)
    monkeypatch.setattr(pipeline.llm, "foundry_configured", lambda: False)
    picture = asyncio.run(pipeline._build_resolved_picture(encounter, persona, TODAY))
    assert [row.record_id for row in picture.records.priority] == ["urgent", "routine"]
    selected_ids = {row.record_id for row in picture.records.priority + picture.records.accessible_other}
    assert selected_ids == {"routine", "urgent"}
    selection = picture.selection
    assert selection is not None
    [urgent_outcome] = [row for row in selection.decision_notes if row.record_id == "urgent"]
    assert urgent_outcome.gate_probabilities["urgency"] == distributions["urgent"]


def test_retention_half_of_the_asymmetry_keeps_a_below_floor_irrelevant_in_generation():
    """The omission path is gated harder than the addition path. An `irrelevant` that clears the
    addition floor but not the omission floor must leave the baseline note in the generation
    input, while a clear `irrelevant` omits it from generation and still keeps the source."""
    record = note("baseline-note", text="Asiakas kertoo masennusoireista.", topic="mielenterveys")
    scoped = cut(Persona(persona_id="synthetic", label="Synthetic", records=[record]),
                 "sairaanhoitaja", "mental_health_assessment")
    baseline_records, reasons = selection.baseline(scoped, TODAY)
    assert [row.record_id for row in baseline_records] == ["baseline-note"]

    below = decision_model._parse(response(status="irrelevant", probabilities={
        "relevant": 0.25, "irrelevant": 0.65, "unclear": 0.10}))
    assert below["status"] == "unclear" and below["gate_refused"] is True
    kept = selection.apply_decision(scoped, baseline_records, reasons, [
        {"record_id": "baseline-note", "status": below["status"],
         "candidate_group": "baseline", "attempted": True}], TODAY)
    assert [row.record_id for row in kept.narration_records] == ["baseline-note"]

    above = decision_model._parse(response(status="irrelevant", probabilities={
        "relevant": 0.10, "irrelevant": 0.85, "unclear": 0.05}))
    assert above["status"] == "irrelevant"
    omitted = selection.apply_decision(scoped, baseline_records, reasons, [
        {"record_id": "baseline-note", "status": "irrelevant",
         "candidate_group": "baseline", "attempted": True}], TODAY)
    assert omitted.narration_records == ()
    assert [row.record_id for row in omitted.records] == ["baseline-note"]


def test_ambiguous_missed_note_gate_refuses_and_baseline_briefing_still_renders(monkeypatch):
    eval_persona = next(p for p in load_decision_eval_personas()
                        if p.persona_id == "bound-persona-011-ambiguous-control")
    ambiguous_note = next(r for r in eval_persona.records if r.record_id == "bound-note-018")
    baseline_note = note("baseline-note", text="Lääkitys on kirjattu aktiiviseksi.", topic="lääkitys")
    baseline_note = baseline_note.model_copy(update={"source_person_id": eval_persona.source_person_id})
    persona = eval_persona.model_copy(update={"records": [baseline_note, *eval_persona.records]})
    encounter = EVALUATION_ENCOUNTERS["bound-encounter-011-laakitys-sairaanhoitaja"]
    note_id = ambiguous_note.record_id
    raw = response(status="relevant", probabilities={
        "relevant": 0.8, "irrelevant": 0.1, "unclear": 0.1},
        time_probs={"current": 0.4, "historical": 0.35, "unclear": 0.25},
        subject_probs={"person": 0.35, "other_person": 0.34, "unclear": 0.31})
    assessed = {"record_id": note_id, "candidate_group": "missed", "attempted": True,
                **decision_model._parse(raw)}
    generated_inputs = []

    async def classify(_scoped, _baseline_ids, _today):
        return [assessed]

    async def generate(scoped, _today, _timeout_s):
        generated_inputs.append(set(scoped.ids))
        return llm.FoundryResult(output=llm.FreshNarration(sentences=[
            llm.NarrationSentence(sentence_id="s1", text="Lääkitys on kirjattu.", source_ids=["baseline-note"])
        ]), model="stub-model")

    async def verify(_scoped, candidate, _today, _timeout_s):
        return {"verdicts": [{"sentence_id": "s1", "verdict": "supported",
                              "supporting_record_ids": ["baseline-note"]}], "model": decision_model.MODEL}

    monkeypatch.setattr(pipeline, "classify_decision_notes", classify)
    monkeypatch.setattr(pipeline.llm, "foundry_configured", lambda: True)
    # This test counts the narration generator's calls; the pre-fill's own generator calls are not its subject.
    monkeypatch.setattr(pipeline.prefill, "forms_for_purpose", lambda *_args: [])
    monkeypatch.setattr(pipeline.llm, "generate_fresh_narration", generate)
    monkeypatch.setattr(decision_model, "verify_narration", verify)
    picture = asyncio.run(pipeline._build_resolved_picture(encounter, persona, TODAY))
    selection = picture.selection
    assert selection is not None
    assert assessed["status"] == "unclear" and assessed["gate_refused"] is True
    assert selection.decision_gate_refused_count == 1
    assert selection.additional_record_ids == []
    assert generated_inputs == [{"baseline-note"}]
    assert picture.narrative is not None and picture.narration_acceptance_status == "accepted"
    assert all(row.record_id != note_id for row in selection.decision_notes)


def test_authored_irrelevant_baseline_note_is_omitted_only_from_generation(monkeypatch):
    personas = load_personas()
    real = httpx.AsyncClient

    def handler(request):
        note_text = json.loads(request.content)["state"]["note"]
        if "arkistointikuittaus" in note_text:
            return httpx.Response(200, content=response(status="irrelevant", probabilities={
                "relevant": 0.05, "irrelevant": 0.9, "unclear": 0.05}), request=request)
        return httpx.Response(200, content=response(status="unclear", probabilities={
            "relevant": 0.2, "irrelevant": 0.2, "unclear": 0.6}), request=request)

    monkeypatch.setenv("LUNA_FOUNDRY_KEY", "fake")
    monkeypatch.setattr(decision_model.httpx, "AsyncClient", lambda **kwargs: real(
        transport=httpx.MockTransport(handler), **kwargs))
    seen: dict[str, set[str]] = {}

    async def generate(scoped, today, timeout_s):
        seen["generation_input"] = set(scoped.ids)
        sentence = llm.NarrationSentence(sentence_id="s1", text="Hoito jatkuu.", source_ids=["bound-dx-005"])
        return llm.FoundryResult(output=llm.FreshNarration(sentences=[sentence]), model="stub-model")

    async def verify(scoped, candidate, today, timeout_s):
        return {"verdicts": [{"sentence_id": "s1", "verdict": "supported",
                              "supporting_record_ids": ["bound-dx-005"]}], "model": decision_model.MODEL}

    monkeypatch.setattr(pipeline.llm, "foundry_configured", lambda: True)
    monkeypatch.setattr(pipeline.llm, "generate_fresh_narration", generate)
    monkeypatch.setattr(decision_model, "verify_narration", verify)
    picture = asyncio.run(pipeline.build_picture_for_encounter(
        "bound-encounter-005-laakitys-sairaanhoitaja", personas=personas,
        today=TODAY, foundry_enabled=True, encounters=ENCOUNTERS))
    selection = picture.selection
    assert selection is not None
    note_id = "bound-note-013"
    assert note_id in selection.baseline_record_ids
    # The observed generation input is the authoritative proof: the irrelevant baseline note was
    # withheld from what the generator received, while a retained baseline source was present.
    assert note_id not in seen["generation_input"]
    assert "bound-dx-005" in seen["generation_input"]
    assert note_id not in picture.narration_input_record_ids
    visible_ids = {row.record_id for row in picture.records.priority + picture.records.accessible_other}
    assert note_id in visible_ids
    outcome = next(row for row in selection.decision_notes if row.record_id == note_id)
    assert outcome.status == "irrelevant" and not outcome.gate_refused


def test_narration_input_leads_with_the_most_relevant_source_then_urgency(monkeypatch):
    """The generator receives sources in the deterministic order the prompt describes -- relevance first, and
    among equally relevant sources the most time-critical first -- with no probabilistic label or per-source
    directive. The assertion is on the generated input, not on model prose.

    This fixture carries urgency and NO relevance, which is the tie case: every source is equally (un)scored
    for relevance, so the order falls through to urgency and `urgent` leads. That is also what makes this a
    check of the PROMPT, which is baked into a pinned Foundry agent version and so is a contract rather than a
    comment: the prompt must describe the order the pipeline actually imposes (see
    test_narration_ordering_prompt.py)."""
    routine = note("routine", text="Mieliala vaihtelee.", topic="mielenterveys")
    urgent = note("urgent", text="Ahdistus haittaa arkea.", topic="mielenterveys")
    persona = Persona(persona_id="synthetic", label="Synthetic", records=[routine, urgent])
    encounter = Encounter("synthetic-narration-order", persona.persona_id, "sairaanhoitaja",
                          "mental_health_assessment")

    def probabilities(score):
        return {str(index): float(index == score) for index in range(5)}

    async def classify(_scoped, _baseline_ids, _today):
        return [
            {"record_id": "routine", "status": "relevant", "attempted": True,
             "urgency": {"score": 0.0, "probabilities": probabilities(0)}},
            {"record_id": "urgent", "status": "relevant", "attempted": True,
             "urgency": {"score": 4.0, "probabilities": probabilities(4)}},
        ]

    captured: dict[str, str] = {}

    async def complete(_schema, instructions, user, _timeout_s):
        captured["instructions"], captured["user"] = instructions, user
        return llm.FoundryResult(output=llm.FreshNarration(sentences=[
            llm.NarrationSentence(sentence_id="s1", text="Lähde.", source_ids=["urgent"])]),
            model="stub-model")

    async def verify(_scoped, _candidate, _today, _timeout_s):
        return {"verdicts": [{"sentence_id": "s1", "verdict": "supported",
                              "supporting_record_ids": ["urgent"]}], "model": decision_model.MODEL}

    monkeypatch.setattr(pipeline, "classify_decision_notes", classify)
    monkeypatch.setattr(pipeline.llm, "foundry_configured", lambda: True)
    # This test counts the narration generator's calls; the pre-fill's own generator calls are not its subject.
    monkeypatch.setattr(pipeline.prefill, "forms_for_purpose", lambda *_args: [])
    monkeypatch.setattr(decision_model, "verify_narration", verify)
    monkeypatch.setattr(llm, "_complete_bounded", complete)
    monkeypatch.delenv("LUNA_FOUNDRY_PROJECT_ENDPOINT", raising=False)
    monkeypatch.delenv("LUNA_FOUNDRY_AGENT_NAME", raising=False)
    asyncio.run(pipeline._build_resolved_picture(encounter, persona, TODAY))

    evidence = json.loads(captured["user"])["selected_evidence"]
    assert [row["record_id"] for row in evidence] == ["urgent", "routine"]
    assert all("source_directive" not in row for row in evidence)
    assert "olennaisimmat" in captured["instructions"], (
        "the prompt must describe the relevance-first order the pipeline imposes")
    assert "ensimmäinen lähde on aikakriittisin" not in captured["instructions"], (
        "the prompt must not claim unconditionally that the first source is the most time-critical; that is "
        "only true among equally relevant sources, and the deployed agent's text must match the code")


# --- L3E: the subject-binding question asks whose record this is, not what it is about -------------------------

# Captured once at import: a loop that re-patches httpx.AsyncClient would wrap its own wrapper.
_REAL_ASYNC_CLIENT = httpx.AsyncClient


def _record(record_id, record_type, payload):
    return Record(record_id=record_id, source="synthetic", record_type=record_type, authored_by_role="laakari",
                  recorded_at=datetime(2026, 9, 20, tzinfo=timezone.utc), topic="hoito",
                  sensitivity_tag="none", consent_scope="treatment", payload=payload)


def _medication():
    return _record("med-1", source.MEDICATION, {"lääke": "Metformiini", "tila": source.MEDICATION_ACTIVE,
                                                "annos": "500 mg"})


def _decision():
    return _record("besl-1", source.DECISION, {"paatos": "Jatketaan kotihoitoa"})


def _classify_with_subject(monkeypatch, records, subject):
    """Run the real request, transport, parse and gate for each record; the model's answer is the only stub.

    Returns (assessed rows by record id, every request body as sent).
    """
    persona = Persona(persona_id="synthetic-l3e", label="Synthetic", records=list(records))
    scoped = cut(persona, "sairaanhoitaja", "medication_follow_up")
    bodies = []

    def handler(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200, content=response(subject=subject))

    monkeypatch.setenv("LUNA_FOUNDRY_KEY", "fake")
    monkeypatch.setattr(decision_model.httpx, "AsyncClient", lambda **kwargs: _REAL_ASYNC_CLIENT(
        transport=httpx.MockTransport(handler), **kwargs))
    rows = asyncio.run(decision_model.classify_notes(scoped, set(), TODAY))
    return {row["record_id"]: row for row in rows}, bodies


def test_a_medication_and_a_paatos_bound_to_the_encounter_person_clear_the_gate(monkeypatch):
    """The two measured regressions: a model that reads these as person-bound must no longer be refused."""
    rows, _ = _classify_with_subject(monkeypatch, [_medication(), _decision()], "person")
    for record_id in ("med-1", "besl-1"):
        assert rows[record_id]["subject_binding"]["choice"] == "person"
        assert rows[record_id]["gate_refused"] is False, record_id
        assert rows[record_id]["status"] == "relevant", record_id


def test_another_persons_record_and_an_unclear_subject_keep_refusing(monkeypatch):
    """other_person and unclear are the discriminator that exists, and neither may be promoted."""
    for subject in ("other_person", "unclear"):
        rows, _ = _classify_with_subject(monkeypatch, [_medication(), _decision()], subject)
        for record_id in ("med-1", "besl-1"):
            assert rows[record_id]["gate_refused"] is True, (subject, record_id)
            assert rows[record_id]["status"] == "unclear", (subject, record_id)
    # The retired content labels are not answers any more: a reply naming them fails closed, it is not accepted.
    for retired in ("object", "administrative"):
        with pytest.raises(ValueError, match="subject binding"):
            decision_model._parse(response(subject=retired))


def test_the_question_as_sent_does_not_invite_a_content_type_answer(monkeypatch):
    """Asserted on the request body that went over the wire, not on the module constant."""
    _, bodies = _classify_with_subject(monkeypatch, [_medication()], "person")
    [body] = bodies
    question = body["questions"]["subject_binding"]
    assert set(question["criteria"]) == {"person", "other_person", "unclear"}
    sent = json.dumps(question, ensure_ascii=False)
    assert "ensisijaisesti" not in sent            # the old "what is it primarily about" wording
    assert '"object"' not in sent and '"administrative"' not in sent
    assert "Kenen Henkilön tietoihin" in question["instructions"]
    assert "Älä valitse kirjauksen tyyppiä" in question["instructions"]


def test_the_gate_thresholds_and_the_zero_floor_contract_are_unchanged():
    """The question is the defect, not the threshold: the constants stay exactly as ADR-0018 pins them."""
    assert (decision_model.ADDITION_FLOOR, decision_model.OMISSION_FLOOR, decision_model.DECISION_MARGIN) == (
        0.6, 0.7, 0.25)
    assert decision_model.SUBJECT_ANSWERS == ("person", "other_person", "unclear")
    assert decision_model.classify_status(
        decision_model._parse(response())["relevance"], decision_model._parse(response())["time_sensitivity"],
        decision_model._parse(response(subject="other_person"))["subject_binding"])[1] is True
