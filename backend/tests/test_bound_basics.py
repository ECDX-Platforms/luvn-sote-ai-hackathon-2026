import asyncio
import json
from datetime import date, datetime, timezone

import httpx
import pytest

from app import decision_model, llm, pipeline, source
from app.encounters import Encounter
from app.policy import cut
from app.picture import RecordView
from app.upm import ConsentScope, Persona, Record, SensitivityTag

TODAY = date(2026, 9, 30)


def record(record_id="basics", record_type="perustieto", topic="demografia", payload=None, *, scope: ConsentScope = "treatment", sensitivity: SensitivityTag = "none"):
    return Record(
        record_id=record_id, source="synthetic", record_type=record_type,
        authored_by_role="rekisterisihteeri", recorded_at=datetime(2026, 9, 15, tzinfo=timezone.utc),
        topic=topic, sensitivity_tag=sensitivity, consent_scope=scope,
        payload=payload or {"nimi": "Aino Esimerkki", "näyttötunniste": "DEMO-007", "syntymävuosi": 1990, "unexpected": "secret"},
    )


def persona(records):
    return Persona(persona_id="synthetic-basics", label="Synthetic", records=records)


def _mock_verifier(monkeypatch, seen):
    def handler(request):
        payload = json.loads(request.content)
        seen.append(payload)
        def choice(value, labels):
            return {"type": "choice", "choice": value, "confidence": 0.9,
                    "probabilities": {label: (0.9 if label == value else 0.05) for label in labels}}
        return httpx.Response(200, json={"model": decision_model.MODEL, "answers": {
            "support": choice("unknown", ("supported", "unsupported", "unknown")),
            "contradicts": {"type": "noul", "noul": 0.05},
            "attribution": choice("attributed", ("attributed", "adds_claim", "unclear")),
            **{f"supports:{source['record_id']}": {"type": "noul", "noul": 0.9}
               for source in payload["state"]["cited_sources"]},
        }})
    real = httpx.AsyncClient
    monkeypatch.setenv("LUNA_FOUNDRY_KEY", "fake")
    monkeypatch.setattr(decision_model.httpx, "AsyncClient", lambda **kwargs: real(
        transport=httpx.MockTransport(handler), **kwargs))


def _model_projection(records):
    """Model-egress projection of records, local to the test (no product serialiser)."""
    return json.dumps(
        {"kirjaukset": [{"payload": source.basics_payload(row, "model")} for row in records]},
        ensure_ascii=False,
    )


def test_basics_policy_is_exact_and_after_role_consent_sensitivity_checks():
    basics = record()
    assert cut(persona([basics]), "sairaanhoitaja", "mental_health_assessment").ids == ("basics",)
    assert not cut(persona([record(record_type="arviointi")]), "sairaanhoitaja", "mental_health_assessment").records
    assert not cut(persona([record(scope="social_care")]), "sairaanhoitaja", "mental_health_assessment").records
    assert not cut(persona([record(sensitivity="highly_sensitive")]), "sairaanhoitaja", "mental_health_assessment").records
    with pytest.raises(ValueError):
        cut(persona([basics]), "sosiaalityontekija", "mental_health_assessment")


def test_basics_projection_keeps_only_the_explicit_display_allowlist():
    row = record(payload={"nimi": "Aino Esimerkki", "näyttötunniste": "DEMO-007", "sukupuoli": "nainen",
                         "syntymävuosi": 1990, "äidinkieli": "suomi", "kotikunta": "Vantaa",
                         "asumismuoto": "koti", "unexpected": "secret", "yhteystieto": "secret"})
    assert RecordView.from_record(row).payload == {
        "nimi": "Aino Esimerkki", "näyttötunniste": "DEMO-007", "sukupuoli": "nainen",
        "syntymävuosi": 1990, "äidinkieli": "suomi", "kotikunta": "Vantaa", "asumismuoto": "koti",
    }
    egress_request = json.loads(_model_projection(cut(persona([row]), "sairaanhoitaja").records))
    assert egress_request["kirjaukset"][0]["payload"] == {}
    assert "Aino Esimerkki" not in json.dumps(egress_request)


def test_foundry_generation_and_decision_verifier_receive_no_basics_payload(monkeypatch):
    row = record()
    scoped = cut(persona([row]), "sairaanhoitaja", "mental_health_assessment")
    generated = []

    async def capture(schema, instructions, user, timeout):
        generated.append(json.loads(user))
        return llm.FoundryResult(output=schema.model_validate({"sentences": [{"sentence_id": "s1", "text": "supported", "source_ids": ["basics"]}]}), model="test")

    monkeypatch.setattr(llm, "_complete_bounded", capture)
    asyncio.run(llm.generate_fresh_narration(scoped, TODAY, 1))
    candidate = llm.FreshNarration(sentences=[llm.NarrationSentence(sentence_id="s1", text="x", source_ids=["basics"])])
    verified = []
    _mock_verifier(monkeypatch, verified)
    asyncio.run(decision_model.verify_narration(scoped, candidate, TODAY, 1))
    assert "Aino Esimerkki" not in json.dumps(generated + verified)
    assert "DEMO-007" not in json.dumps(generated + verified)
    assert "unexpected" not in json.dumps(generated + verified)
    assert "syntymävuosi" not in json.dumps(generated + verified)
    assert generated[0]["selected_evidence"][0]["payload"] == {}
    assert verified[0]["state"]["cited_sources"][0]["payload"] == {}


def test_picture_identity_header_and_recordviews_cover_selected_path(monkeypatch):
    basics = record()
    p = persona([basics])
    monkeypatch.setattr(pipeline.llm, "foundry_configured", lambda: False)
    monkeypatch.setattr(pipeline, "classify_decision_notes", lambda *args: asyncio.sleep(0, result=[]))
    encounter = Encounter("synthetic", p.persona_id, "sairaanhoitaja", "mental_health_assessment")
    picture = asyncio.run(pipeline._build_resolved_picture(encounter, p, TODAY))
    assert picture.identity_header == "Aino Esimerkki · DEMO-007"
    rows = [*picture.citations, *picture.records.priority, *picture.records.accessible_other]
    assert rows and all(row.payload == {"nimi": "Aino Esimerkki", "näyttötunniste": "DEMO-007", "syntymävuosi": 1990} for row in rows)


def test_missing_basics_uses_neutral_header_without_count_or_denied_hint(monkeypatch):
    denied = record(scope="social_care")
    p = persona([denied])
    monkeypatch.setattr(pipeline.llm, "foundry_configured", lambda: False)
    monkeypatch.setattr(pipeline, "classify_decision_notes", lambda *args: asyncio.sleep(0, result=[]))
    encounter = Encounter("synthetic", p.persona_id, "sairaanhoitaja", "mental_health_assessment")
    picture = asyncio.run(pipeline._build_resolved_picture(encounter, p, TODAY))
    assert picture.identity_header == "Henkilöllisyystieto ei saatavilla"
    assert "1" not in picture.identity_header
    assert not picture.records.priority and not picture.records.accessible_other


def _assert_no_withheld_denominator_number(pictures):
    """A complete role-facing snapshot must not change when only withheld rows vary."""
    rendered = [json.dumps(picture.model_dump(), ensure_ascii=False, sort_keys=True) for picture in pictures]
    assert rendered and all(body == rendered[0] for body in rendered[1:]), \
        "NO DERIVED NUMBER MAY REQUIRE A WITHHELD DENOMINATOR"


def test_empty_evidence_picture_is_cause_neutral_and_hides_withheld_count(monkeypatch):
    """ADR-0005: the role-facing Picture for an empty-evidence encounter is
    identical with zero, one, and two withheld Kirjaus, so the withheld count
    is not derivable. It also never states an access-restriction cause."""
    denied = [record(record_id=f"withheld-basics-{i}", scope="social_care") for i in range(2)]
    monkeypatch.setattr(pipeline.llm, "foundry_configured", lambda: False)
    monkeypatch.setattr(pipeline, "classify_decision_notes", lambda *args: asyncio.sleep(0, result=[]))
    monkeypatch.setattr(pipeline, "_milliseconds", lambda *_: 0)

    pictures = []
    for count in (0, 1, 2):
        p = persona(denied[:count])
        encounter = Encounter("synthetic", p.persona_id, "sairaanhoitaja", "mental_health_assessment")
        picture = asyncio.run(pipeline._build_resolved_picture(encounter, p, TODAY))
        assert picture.situation.situation_type == "ei_arvioitavissa"
        assert picture.risk.risk_level == "arvioimaton"
        assert picture.identity_header == "Henkilöllisyystieto ei saatavilla"
        assert not picture.records.priority and not picture.records.accessible_other
        pictures.append(picture)

    _assert_no_withheld_denominator_number(pictures)
    body = json.dumps(pictures[0].model_dump(), ensure_ascii=False, sort_keys=True)
    for token in ("suostumus", "käyttöoikeus", "piilossa", "ei näy", "withheld-basics", "excluded_count"):
        assert token not in body


def test_baseline_version_and_basics_id_delta():
    selected, reasons = pipeline.baseline(cut(persona([record()]), "sairaanhoitaja", "functioning_service_needs"), TODAY)
    assert [row.record_id for row in selected] == ["basics"]
    assert reasons["basics"] == ["context:person-basics"]
    assert pipeline.RULE_VERSION == "challenge-1.1-decision-selection-v8"


def test_display_and_unlisted_basics_values_do_not_change_deterministic_decision():
    from app.decide import decide

    one = record()
    two = one.model_copy(update={"payload": {"nimi": "Other", "näyttötunniste": "OTHER-1", "syntymävuosi": 1990, "unexpected": "diabetes"}})
    assert decide(cut(persona([one]), "sairaanhoitaja", "mental_health_assessment")) == decide(
        cut(persona([two]), "sairaanhoitaja", "mental_health_assessment")
    )


def test_non_demographic_basics_are_projected_at_every_egress(monkeypatch):
    # A declared topic that is NOT `demografia`, so this stays the non-demographic basics path; `Record`
    # now refuses an undeclared topic (D6), so the old free-form placeholder cannot be constructed.
    row = record(topic="hoito", payload={
        "nimi": "Aino", "näyttötunniste": "DEMO-007", "syntymävuosi": 1990,
        "sukupuoli": "nainen", "secret": "never-egress",
    })
    p = persona([row])
    scoped = cut(p, "sairaanhoitaja")
    assert not cut(p, "sairaanhoitaja", "mental_health_assessment").records
    assert RecordView.from_record(row).payload == {"nimi": "Aino", "näyttötunniste": "DEMO-007"}
    assert "never-egress" not in _model_projection(scoped.records)
    assert not source.string_values(row)

    generated = []

    async def capture(schema, instructions, user, timeout):
        generated.append(json.loads(user))
        output = schema.model_validate({"sentences": [{"sentence_id": "s1", "text": "x", "source_ids": [row.record_id]}]})
        return llm.FoundryResult(output=output, model="test")

    monkeypatch.setattr(llm, "_complete_bounded", capture)
    asyncio.run(llm.generate_fresh_narration(scoped, TODAY, 1))
    candidate = llm.FreshNarration(sentences=[llm.NarrationSentence(sentence_id="s1", text="x", source_ids=[row.record_id])])
    verified = []
    _mock_verifier(monkeypatch, verified)
    asyncio.run(decision_model.verify_narration(scoped, candidate, TODAY, 1))
    assert len(generated) == len(verified) == 1
    assert "never-egress" not in json.dumps(generated + verified)
    assert generated[0]["selected_evidence"][0]["payload"] == {}
    assert verified[0]["state"]["cited_sources"][0]["payload"] == {}


def test_eligible_basics_without_both_display_fields_uses_neutral_header(monkeypatch):
    p = persona([record(payload={"syntymävuosi": 1990, "unexpected": "private"})])
    monkeypatch.setattr(pipeline.llm, "foundry_configured", lambda: False)
    monkeypatch.setattr(pipeline, "classify_decision_notes", lambda *args: asyncio.sleep(0, result=[]))
    encounter = Encounter("synthetic", p.persona_id, "sairaanhoitaja", "mental_health_assessment")
    picture = asyncio.run(pipeline._build_resolved_picture(encounter, p, TODAY))
    assert picture.identity_header == "Henkilöllisyystieto ei saatavilla"
    assert picture.records.accessible_other[0].payload == {"syntymävuosi": 1990}
