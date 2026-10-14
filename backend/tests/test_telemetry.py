"""Optional App Insights stage telemetry never persists content or IDs."""
import asyncio
import importlib
import json
from datetime import date, datetime, timezone

import httpx

from app import decision_model, llm, pipeline
from app.fixtures import load_personas
from app.policy import cut
from app.upm import Persona, Record
from tests.fixtures.bound_encounters import ENCOUNTERS

# The optional local telemetry module is outside the app's declared import set.
telemetry = importlib.import_module("app.telemetry")


def test_trace_buffer_is_correlated_and_contains_only_stage_metadata():
    buffer = telemetry.TraceBuffer()
    buffer.record("decision.relevance", 12, model="decision-1.13.0", input_tokens=32,
                  output_tokens=0, status="relevant")
    [event] = buffer.events
    assert event == {"stage": "decision.relevance", "elapsed_ms": 12, "model": "decision-1.13.0",
                     "input_tokens": 32, "output_tokens": 0, "status": "relevant"}
    assert buffer.trace_id and "record_id" not in json.dumps(event)
    assert "note" not in json.dumps(event) and "text" not in json.dumps(event)


def test_missing_connection_string_makes_flush_a_noop(monkeypatch):
    monkeypatch.delenv("APPLICATIONINSIGHTS_CONNECTION_STRING", raising=False)
    buffer = telemetry.TraceBuffer()
    buffer.record("pipeline.total", 10)

    def unexpected_client(*_args, **_kwargs):
        raise AssertionError("telemetry must not initialize a client without configuration")

    monkeypatch.setattr(telemetry.httpx, "AsyncClient", unexpected_client)
    asyncio.run(buffer.flush())


def test_each_actual_decision_call_records_scrubbed_model_usage(monkeypatch):
    record = Record(record_id="synthetic-note-id", source="synthetic", record_type="vapaateksti",
                    authored_by_role="sairaanhoitaja", recorded_at=datetime(2026, 9, 20, tzinfo=timezone.utc),
                    topic="mielenterveys", sensitivity_tag="none", consent_scope="treatment",
                    payload={"teksti": "synthetic note text"})
    scoped = cut(Persona(persona_id="synthetic-person", label="Synthetic", records=[record]),
                 "sairaanhoitaja", "mental_health_assessment")

    def choice(value, labels):
        return {"type": "choice", "choice": value, "confidence": 0.9,
                "probabilities": {label: (0.8 if label == value else 0.2 / (len(labels) - 1))
                                  for label in labels}}

    def handler(_request):
        return httpx.Response(200, json={"model": decision_model.MODEL, "usage": {
            "input_tokens": 13, "output_tokens": 2}, "answers": {
            "relevance": choice("relevant", ("relevant", "irrelevant", "unclear")),
            "time_sensitivity": choice("current", ("current", "historical", "unclear")),
            "subject_binding": choice("person", ("person", "other_person", "unclear")),
            "urgency": {"type": "score", "score": 2.0, "confidence": 0.8,
                        "probabilities": {"0": 0.0, "1": 0.0, "2": 1.0, "3": 0.0, "4": 0.0},
                        "legend": {str(i): level for i, level in enumerate(decision_model.URGENCY_LEVELS)}},
        }})

    real = httpx.AsyncClient
    monkeypatch.setenv("LUNA_FOUNDRY_KEY", "fake")
    monkeypatch.setattr(decision_model.httpx, "AsyncClient", lambda **kwargs: real(
        transport=httpx.MockTransport(handler), **kwargs))
    with telemetry.request_trace() as trace:
        result = asyncio.run(decision_model.classify_notes(scoped, set(), date(2026, 9, 30)))
    assert result[0]["status"] == "relevant"
    [event] = trace.events
    assert event["stage"] == "decision.relevance"
    assert event["model"] == decision_model.MODEL
    assert event["input_tokens"] == 13 and event["output_tokens"] == 2
    serialized = json.dumps(event)
    assert "synthetic-note-id" not in serialized and "synthetic note text" not in serialized


def test_picture_trace_covers_the_stages_and_never_contains_source_ids(monkeypatch):
    async def no_relevance(_scoped, _baseline_ids, _today):
        return []

    async def generate(scoped, _today, _timeout):
        candidate = llm.FreshNarration(sentences=[llm.NarrationSentence(
            sentence_id="s1", text="Synteettinen lähdelause.", source_ids=[scoped.ids[0]])])
        return llm.FoundryResult(output=candidate, model="gpt-6.1-sol-2026-09-29",
                                 input_tokens=44, output_tokens=12)

    monkeypatch.setattr(pipeline, "classify_decision_notes", no_relevance)
    monkeypatch.setattr(llm, "foundry_configured", lambda: True)
    monkeypatch.setattr(llm, "generate_fresh_narration", generate)
    monkeypatch.delenv("LUNA_FOUNDRY_KEY", raising=False)
    picture = asyncio.run(pipeline.build_picture_for_encounter(
        "bound-encounter-002-laakitys-sairaanhoitaja", personas=load_personas(), today=date(2026, 9, 30),
        encounters=ENCOUNTERS))
    stages = {event.stage for event in picture.pipeline_trace}
    assert {"encounter.resolve", "policy.cut", "decision.selection", "foundry.generation",
            "decision.grounding.verify", "grounding.verification", "pipeline.total"} <= stages
    generation = next(event for event in picture.pipeline_trace if event.stage == "foundry.generation")
    assert generation.model == "gpt-6.1-sol-2026-09-29"
    assert generation.input_tokens == 44 and generation.output_tokens == 12
    verification = next(event for event in picture.pipeline_trace if event.stage == "grounding.verification")
    assert verification.status == "unavailable" and verification.model is None
    selection = next(event for event in picture.pipeline_trace if event.stage == "decision.selection")
    assert selection.model is None
    verify_event = next(event for event in picture.pipeline_trace if event.stage == "decision.grounding.verify")
    assert verify_event.status == "unavailable" and verify_event.model is None
    assert picture.pipeline_trace_id
    assert "record_id" not in json.dumps([event.model_dump() for event in picture.pipeline_trace])


def test_flush_scrubs_to_stage_metadata_and_is_best_effort(monkeypatch):
    monkeypatch.setenv("APPLICATIONINSIGHTS_CONNECTION_STRING",
                       "InstrumentationKey=fake;IngestionEndpoint=https://example.test/")
    seen = {}

    class Response:
        def raise_for_status(self):
            pass

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def post(self, url, *, content, headers):
            seen["url"] = url
            seen["headers"] = headers
            seen["body"] = json.loads(content)
            return Response()

    monkeypatch.setattr(telemetry.httpx, "AsyncClient", lambda **_kwargs: Client())
    buffer = telemetry.TraceBuffer()
    buffer.record("foundry.generation", 100, model="gpt-6.1-sol", input_tokens=50,
                  output_tokens=12, status="generated")
    asyncio.run(buffer.flush())
    [event] = seen["body"]
    properties = event["data"]["baseData"]["properties"]
    assert seen["url"] == "https://example.test/v2/track"
    assert properties["stage"] == "foundry.generation"
    assert properties["trace_id"] == buffer.trace_id
    assert "encounter_ref" not in properties
    serialized = json.dumps(seen["body"])
    for forbidden in ("record_id", "source_person_id", "Kirjaus text", "withheld_count", "other_person"):
        assert forbidden not in serialized
