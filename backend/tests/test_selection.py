"""Locked synthetic IDs for the additive purpose-aware selection contract."""

import asyncio
from dataclasses import FrozenInstanceError
from datetime import date, datetime, timezone


import pytest

import app.pipeline as pipeline
from app import decision_model
from dataclasses import replace
from app.fixtures import load_personas
from tests.fixtures.bound_encounters import ENCOUNTERS
from app.policy import cut
from app.upm import ConsentScope, Persona, Record

TODAY = date(2026, 9, 30)


def _record(
    record_id: str,
    record_type: str = "vapaateksti",
    *,
    text: str = "Käynti sujui rauhallisesti.",
    topic: str = "hoito",
    scope: ConsentScope = "treatment",
    recorded_at: datetime | None = None,
) -> Record:
    return Record(
        record_id=record_id,
        source="synthetic",
        record_type=record_type,
        authored_by_role="sairaanhoitaja",
        recorded_at=recorded_at or datetime(2026, 9, 15, 8, tzinfo=timezone.utc),
        topic=topic,
        sensitivity_tag="sensitive",
        consent_scope=scope,
        payload={"otsikko": "Keskustelu", "teksti": text},
    )


def _persona(records: list[Record]) -> Persona:
    return Persona(persona_id="synthetic-selection", label="Synthetic", records=records)




# Frozen expected IDs are the answer key. These cases deliberately use stubbed
# labels: they test the mapping/selection contract, not model usefulness.




def _run_case(purpose, role, records, note_results):
    scoped = cut(_persona(records), role, purpose)
    baseline_records, reasons = pipeline.baseline(scoped, TODAY)
    return scoped, pipeline.apply_decision(scoped, baseline_records, reasons, note_results, TODAY)


def test_same_nurse_same_person_functioning_encounter_has_backend_selection_delta(monkeypatch):
    async def no_classification(scoped, baseline_ids, today):
        return []

    monkeypatch.setattr(pipeline, "classify_decision_notes", no_classification)
    monkeypatch.setattr(pipeline.llm, "foundry_configured", lambda: False)
    personas = load_personas()
    mental = asyncio.run(pipeline.build_picture_for_encounter("bound-encounter-003-mielenterveys-sairaanhoitaja", personas, TODAY, encounters=ENCOUNTERS))
    functioning = asyncio.run(pipeline.build_picture_for_encounter("bound-encounter-003-toimintakyky-sairaanhoitaja", personas, TODAY, encounters=ENCOUNTERS))

    # The person is resolved from the encounter mapping, not from the Picture: H3 removed the internal
    # `persona_id` join key from the wire response, so the same-person assertion is made against the
    # encounter itself rather than a field the client no longer receives.
    mental_encounter = ENCOUNTERS["bound-encounter-003-mielenterveys-sairaanhoitaja"]
    functioning_encounter = ENCOUNTERS["bound-encounter-003-toimintakyky-sairaanhoitaja"]
    assert (mental_encounter.persona_id, mental.role, mental.purpose) == (
        "bound-persona-003-mielenterveys-sosiaali", "sairaanhoitaja", "mental_health_assessment"
    )
    assert (functioning_encounter.persona_id, functioning.role, functioning.purpose) == (
        mental_encounter.persona_id, mental.role, "functioning_service_needs"
    )
    assert mental.selection is not None and functioning.selection is not None
    assert mental.selection.rule_version == functioning.selection.rule_version == "challenge-1.1-decision-selection-v8"
    assert mental.selection.baseline_record_ids == ["bound-demo-003", "bound-note-003", "bound-dx-004", "bound-med-004",
                                                   "bound-arviointi-003", "bound-note-003-alignment", "bound-note-003-draft"]
    assert functioning.selection.baseline_record_ids == ["bound-demo-003", "bound-note-010"]
    assert "bound-note-003" not in functioning.selection.baseline_record_ids
    assert "bound-note-010" not in mental.selection.baseline_record_ids






def test_latest_incomplete_assessment_supersedes_stale_completed_assessment():
    from app.assess import select_for_picture

    complete_answers = {f"q{index}": 1 for index in range(1, 10)}
    old = _record("old-complete", "arviointi", topic="mielenterveys", recorded_at=datetime(2025, 1, 1, tzinfo=timezone.utc)).model_copy(
        update={"payload": {"instrumentti": "PHQ-9", "vastaukset": complete_answers}}
    )
    incomplete_answers = {"q1": 2}
    latest = _record("latest-incomplete", "arviointi", topic="mielenterveys", recorded_at=datetime(2026, 9, 20, tzinfo=timezone.utc)).model_copy(
        update={"payload": {"instrumentti": "PHQ-9", "vastaukset": incomplete_answers}}
    )
    selected = select_for_picture([old, latest], TODAY)
    assert [(item.lahteet, item.valmis, item.temporal_status) for item in selected] == [
        (["latest-incomplete"], False, "incomplete")
    ]
    _, selection = _run_case("mental_health_assessment", "sairaanhoitaja", [old, latest], [])
    assert list(selection.baseline_record_ids) == ["latest-incomplete"]


def test_assessment_provenance_preserves_instrument_version_and_history_status():
    historical = _record("old-version", "arviointi", topic="mielenterveys", recorded_at=datetime(2024, 1, 1, tzinfo=timezone.utc)).model_copy(
        update={"payload": {"instrumentti": "PHQ-9", "versio": "demo-v2", "vastaukset": {f"q{index}": 1 for index in range(1, 10)}}}
    )
    from app.assess import select_for_picture

    [result] = select_for_picture([historical], TODAY)
    assert result.instrument_version == "demo-v2"
    assert result.temporal_status == "history"
    assert result.lahteet == ["old-version"]


def test_conflicting_same_time_assessment_ties_are_ambiguous():
    from app.assess import select_for_picture

    when = datetime(2026, 9, 20, tzinfo=timezone.utc)
    low = _record("tie-low", "arviointi", topic="mielenterveys", recorded_at=when).model_copy(
        update={"payload": {"instrumentti": "PHQ-9", "vastaukset": {f"q{index}": 0 for index in range(1, 10)}}}
    )
    high = _record("tie-high", "arviointi", topic="mielenterveys", recorded_at=when).model_copy(
        update={"payload": {"instrumentti": "PHQ-9", "vastaukset": {f"q{index}": 2 for index in range(1, 10)}}}
    )
    selected = select_for_picture([low, high], TODAY)
    assert {item.temporal_status for item in selected} == {"ambiguous"}
    assert all(not item.valmis and item.total is None for item in selected)
















def test_picture_narration_and_records_use_selected_set_without_changing_decision(monkeypatch):
    encounter, _ = pipeline.resolve_encounter("bound-encounter-003-mielenterveys-sairaanhoitaja", load_personas(), ENCOUNTERS)
    encounter = replace(encounter, persona_id="synthetic-selection")
    baseline_note = _record("baseline-note", text="Mieliala vaihtelee.", topic="mielenterveys")
    candidate_note = _record("candidate-note")
    persona = _persona([baseline_note, candidate_note])
    narration_inputs = []
    candidate_enabled = False

    async def classify(scoped, baseline_ids, today):
        assert all(record.record_id in {"baseline-note", "candidate-note"} for record in scoped.records)
        return ([{"record_id": "candidate-note", "status": "relevant", "model": "decision-1.13.0"}]
                if candidate_enabled else [])

    async def generate(scoped, today, timeout_s):
        narration_inputs.append(set(scoped.ids))
        sentence = pipeline.llm.NarrationSentence(
            sentence_id="s1", text="Mieliala vaihtelee.", source_ids=[scoped.ids[0]]
        )
        return pipeline.llm.FoundryResult(
            output=pipeline.llm.FreshNarration(sentences=[sentence]), model="gpt-5.4-mini-test"
        )

    async def verify(scoped, candidate, today, timeout_s):
        sentence = candidate.sentences[0]
        return {"verdicts": [{"sentence_id": sentence.sentence_id, "verdict": "supported",
                              "supporting_record_ids": sentence.source_ids}], "model": decision_model.MODEL}

    monkeypatch.setenv("LUNA_FOUNDRY_KEY", "fake")
    monkeypatch.setattr(pipeline, "classify_decision_notes", classify)
    monkeypatch.setattr(pipeline.llm, "foundry_configured", lambda: True)
    monkeypatch.setattr(pipeline.llm, "generate_fresh_narration", generate)
    monkeypatch.setattr(decision_model, "verify_narration", verify)
    fallback = asyncio.run(pipeline._build_resolved_picture(encounter, persona, TODAY))
    candidate_enabled = True
    enriched = asyncio.run(pipeline._build_resolved_picture(encounter, persona, TODAY))

    assert narration_inputs == [{"baseline-note"}, {"baseline-note", "candidate-note"}]
    assert (fallback.situation, fallback.risk, fallback.actions) == (enriched.situation, enriched.risk, enriched.actions)
    assert {row.record_id for row in enriched.records.priority + enriched.records.accessible_other} == narration_inputs[-1]
    assert enriched.selection is not None and enriched.selection.additional_record_ids == ["candidate-note"]
    assert enriched.narration_generation_status == "generated"
    assert enriched.narration_support_status == "supported"
    assert enriched.narration_acceptance_status == "accepted"


def test_missing_foundry_rejects_prose_but_keeps_only_selected_records(monkeypatch):
    encounter, _ = pipeline.resolve_encounter("bound-encounter-003-mielenterveys-sairaanhoitaja", load_personas(), ENCOUNTERS)
    encounter = replace(encounter, persona_id="synthetic-selection")
    persona = _persona([
        _record("selected-note", text="Mieliala vaihtelee.", topic="mielenterveys"),
        _record("permitted-but-irrelevant"),
    ])

    async def no_decision(scoped, baseline_ids, today):
        return []

    monkeypatch.setattr(pipeline, "classify_decision_notes", no_decision)
    monkeypatch.setattr(pipeline.llm, "foundry_configured", lambda: False)
    picture = asyncio.run(pipeline._build_resolved_picture(encounter, persona, TODAY))
    assert picture.narrative is None and picture.citations == []
    assert picture.narration_generation_status == "unavailable"
    assert picture.narration_support_status == "not_run"
    assert picture.narration_acceptance_status == "rejected"
    assert [row.record_id for row in picture.records.priority + picture.records.accessible_other] == ["selected-note"]


def test_encounter_resolution_is_immutable_and_unknown_ids_reject_closed():
    personas = load_personas()
    encounter, persona = pipeline.resolve_encounter("bound-encounter-003-mielenterveys-sairaanhoitaja", personas, ENCOUNTERS)
    assert persona.persona_id == encounter.persona_id
    with pytest.raises(FrozenInstanceError):
        setattr(encounter, "role", "sosiaalityontekija")
    with pytest.raises(ValueError):
        pipeline.resolve_encounter("not-an-encounter", personas, ENCOUNTERS)
    with pytest.raises(ValueError):
        cut(persona, "sosiaalityontekija", "mental_health_assessment")
    with pytest.raises(ValueError):
        cut(persona, encounter.role, "unknown-purpose")


def test_selection_owns_generation_and_prominence_without_dropping_baseline():
    baseline = _record("baseline", text="Mieliala vaihtelee.")
    missed = _record("missed")
    scoped = cut(_persona([baseline, missed]), "sairaanhoitaja", "mental_health_assessment")
    base, reasons = pipeline.baseline(scoped, TODAY)
    selection = pipeline.apply_decision(scoped, base, reasons, [
        {"record_id": "baseline", "status": "irrelevant", "attempted": True},
        {"record_id": "missed", "status": "relevant", "attempted": True},
    ], TODAY)
    assert {r.record_id for r in selection.records} == {"baseline", "missed"}
    assert [r.record_id for r in selection.narration_records] == ["missed"]
    assert selection.priority_ids(["baseline", "missed"]) == ["missed"]
    failed = pipeline.apply_decision(scoped, base, reasons, [
        {"record_id": "baseline", "status": "irrelevant", "attempted": False}
    ], TODAY)
    assert [r.record_id for r in failed.narration_records] == ["baseline"]
    # Only an attempted irrelevant read may suppress, and a non-string record_id
    # from the model side can neither crash selection nor suppress a baseline note.
    malformed = pipeline.apply_decision(scoped, base, reasons, [
        {"record_id": 7, "status": "irrelevant", "attempted": True},
        {"record_id": "baseline", "status": "irrelevant"},
    ], TODAY)
    assert [r.record_id for r in malformed.narration_records] == ["baseline"]


def _home_care_visit(record_id: str, when: datetime) -> Record:
    # The shape `build_veil_corpus._kotihoito` writes: an appointment under the social-care scope whose `aihe` carries
    # the `kotihoito` baseline term, exactly as the delivery store presents a home-care contact.
    return Record(record_id=record_id, source="delivery", record_type="asiointitapahtuma",
                  authored_by_role="sairaanhoitaja", recorded_at=when, topic="kotihoidon palvelu",
                  sensitivity_tag="none", consent_scope="social_care",
                  payload={"kanava": "SIM-Kotikäynti", "aihe": "Kotihoito"})


def test_social_baseline_folds_same_day_home_care_visits_to_the_newest_and_names_the_rest():
    visits = [
        _home_care_visit("home-morning", datetime(2026, 9, 29, 8, tzinfo=timezone.utc)),
        _home_care_visit("home-evening", datetime(2026, 9, 29, 18, tzinfo=timezone.utc)),
        _home_care_visit("home-noon", datetime(2026, 9, 29, 12, tzinfo=timezone.utc)),
    ]
    scoped = cut(_persona(visits), "sosiaalityontekija", "functioning_service_needs")
    chosen, reasons = pipeline.baseline(scoped, TODAY)
    assert [r.record_id for r in chosen] == ["home-evening"]
    assert reasons == {"home-evening": ["keyword:kotihoito", "fold:same-day-home-care:home-noon,home-morning"]}
    # The fold is a baseline rule only: Decision-1 may still add a folded visit it judged relevant.
    selection = pipeline.apply_decision(scoped, chosen, reasons, [
        {"record_id": "home-morning", "status": "relevant", "attempted": True},
    ], TODAY)
    assert {r.record_id for r in selection.records} == {"home-evening", "home-morning"}
    assert selection.additional_record_ids == ("home-morning",)


def test_social_baseline_keeps_home_care_visits_on_different_days():
    visits = [
        _home_care_visit("home-1", datetime(2026, 9, 27, 9, tzinfo=timezone.utc)),
        _home_care_visit("home-2", datetime(2026, 9, 28, 9, tzinfo=timezone.utc)),
        _home_care_visit("home-3", datetime(2026, 9, 29, 9, tzinfo=timezone.utc)),
    ]
    scoped = cut(_persona(visits), "sosiaalityontekija", "functioning_service_needs")
    chosen, reasons = pipeline.baseline(scoped, TODAY)
    assert [r.record_id for r in chosen] == ["home-1", "home-2", "home-3"]
    assert all(reasons[r.record_id] == ["keyword:kotihoito"] for r in chosen)


def test_same_day_fold_uses_the_visit_time_not_the_entry_time():
    entered_together = datetime(2026, 9, 30, 6, tzinfo=timezone.utc)
    visits = [
        _home_care_visit("home-a", entered_together).model_copy(update={"käyntiaika": datetime(2026, 9, 28, 9, tzinfo=timezone.utc)}),
        _home_care_visit("home-b", entered_together).model_copy(update={"käyntiaika": datetime(2026, 9, 28, 17, tzinfo=timezone.utc)}),
        _home_care_visit("home-c", entered_together).model_copy(update={"käyntiaika": datetime(2026, 9, 29, 9, tzinfo=timezone.utc)}),
    ]
    scoped = cut(_persona(visits), "sosiaalityontekija", "functioning_service_needs")
    chosen, reasons = pipeline.baseline(scoped, TODAY)
    assert [r.record_id for r in chosen] == ["home-b", "home-c"]
    assert reasons["home-b"] == ["keyword:kotihoito", "fold:same-day-home-care:home-a"]


def test_same_day_home_care_fold_is_limited_to_the_social_lens():
    # Under a clinical purpose the same records are not folded; whatever the baseline admits, it admits per visit.
    visits = [
        _home_care_visit("home-morning", datetime(2026, 9, 29, 8, tzinfo=timezone.utc)),
        _home_care_visit("home-evening", datetime(2026, 9, 29, 18, tzinfo=timezone.utc)),
    ]
    persona = _persona(visits + [_record("note", text="Kotihoito käy päivittäin.", topic="hoito")])
    for purpose, role in (("medication_follow_up", "sairaanhoitaja"), ("mental_health_assessment", "sairaanhoitaja")):
        try:
            scoped = cut(persona, role, purpose)
        except ValueError:
            continue
        _, reasons = pipeline.baseline(scoped, TODAY)
        assert not any(reason.startswith("fold:") for why in reasons.values() for reason in why), (purpose, reasons)
