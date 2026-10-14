"""The review adapter may expose only exact, selected source identities."""

import asyncio
from datetime import date, datetime, timezone

from app import assess, main, pipeline
from app.encounters import resolve_encounter
from app.fixtures import load_personas
from app.proposals import ASSESSMENT_REVIEW_RULE, review_proposals
from app.store import Store
from app.upm import Persona, Record
from tests.fixtures.bound_encounters import ENCOUNTERS

TODAY = date(2026, 9, 30)


def _record(record_id: str, payload: dict) -> Record:
    return Record(
        record_id=record_id,
        source="synteettinen lomake",
        record_type="arviointi",
        authored_by_role="sairaanhoitaja",
        recorded_at=datetime(2026, 9, 20, tzinfo=timezone.utc),
        topic="mielenterveys",
        sensitivity_tag="sensitive",
        consent_scope="treatment",
        payload=payload,
    )


def test_complete_irrelevant_and_unselected_assessments_create_no_proposal():
    complete_record = _record(
        "complete",
        {"instrumentti": "PHQ-9", "vastaukset": {f"q{i}": 1 for i in range(1, 10)}},
    )
    incomplete_record = _record("unselected", {"instrumentti": "PHQ-9", "vastaukset": {"q1": 1}})
    proposals = review_proposals(
        "encounter-test",
        [assess.score(complete_record), assess.score(incomplete_record)],
        [],
        {"complete"},
        [complete_record, incomplete_record],
    )
    assert proposals == []


def test_incomplete_proposal_binds_only_selected_assessment_with_version_and_date():
    record = _record(
        "iadl-incomplete",
        {"instrumentti": "IADL", "versio": "demo-v2", "vastaukset": {"i1": 1}},
    )
    proposal = review_proposals(
        "encounter-social",
        assess.select_for_picture([record], TODAY),
        [],
        {"iadl-incomplete", "irrelevant-note", "denied-record"},
        [record],
    )[0]
    assert proposal.rule_version == ASSESSMENT_REVIEW_RULE
    assert proposal.text == "Tarkista arvioinnin puuttuvat tai ristiriitaiset vastaukset (IADL)."
    assert proposal.evidence_record_ids == ["iadl-incomplete"]
    assert proposal.evidence[0].instrument == "IADL"
    assert proposal.evidence[0].instrument_version == "demo-v2"
    assert proposal.evidence[0].recorded_at == record.recorded_at


def test_ambiguous_assessment_proposal_groups_exact_tied_sources():
    when = datetime(2026, 9, 20, tzinfo=timezone.utc)
    low = _record("phq-low", {"instrumentti": "PHQ-9", "versio": "v1", "vastaukset": {f"q{i}": 0 for i in range(1, 10)}})
    high = _record("phq-high", {"instrumentti": "PHQ-9", "versio": "v1", "vastaukset": {f"q{i}": 2 for i in range(1, 10)}})
    low = low.model_copy(update={"recorded_at": when})
    high = high.model_copy(update={"recorded_at": when})
    assessments = assess.select_for_picture([low, high], TODAY)
    proposal = review_proposals("encounter-mental", assessments, [], {"phq-low", "phq-high"}, [low, high])[0]
    assert proposal.status == "ambiguous"
    assert proposal.text == "Tarkista ristiriitaiset vastaukset (PHQ-9)."
    assert proposal.evidence_record_ids == ["phq-high", "phq-low"]
    assert len(proposal.evidence) == 2
    repeated = review_proposals("encounter-mental", assessments, [], {"phq-low", "phq-high"}, [low, high])[0]
    assert repeated.proposal_id == proposal.proposal_id


def test_functioning_walkthrough_keeps_incomplete_iadl_and_review_evidence(monkeypatch):
    personas = load_personas()
    encounter, persona = resolve_encounter("bound-encounter-004-sosiaalipalvelut-sosiaalityontekija", personas, ENCOUNTERS)


    monkeypatch.setattr(pipeline.llm, "foundry_configured", lambda: False)
    picture = asyncio.run(pipeline._build_resolved_picture(encounter, persona, TODAY))
    [iadl] = [item for item in picture.assessments if item.instrumentti == "IADL"]
    assert iadl.valmis is False and iadl.total is None
    assert "bound-arviointi-006" in iadl.lahteet
    [proposal] = [item for item in picture.proposals if "bound-arviointi-006" in item.evidence_record_ids]
    assert proposal.status == "incomplete"
    assert proposal.rule_version == ASSESSMENT_REVIEW_RULE


def test_review_proposals_intersect_evidence_with_permitted_records_not_only_selected():
    """B8: a `selected` set wider than `records` can neither emit nor KeyError on an out-of-permitted id."""
    record = _record("note-1", {"teksti": "kirjaus"})
    other = _record("med-1", {"nimi": "lääke"})
    # The note id is selected but not a permitted record, so the conflict branch must drop the pair rather
    # than raise a KeyError on the `recorded_at` lookup.
    assert review_proposals(
        "encounter-x", [], [{"medication_record_id": "med-1", "note_record_id": "never-permitted"}],
        {"med-1", "never-permitted"}, [other],
    ) == []
    # A pair whose ids are both permitted still forms a proposal, and the id outside `records` never appears.
    [proposal] = review_proposals(
        "encounter-x", [], [{"medication_record_id": "med-1", "note_record_id": "note-1"}],
        {"med-1", "note-1", "never-permitted"}, [other, record],
    )
    assert proposal.evidence_record_ids == ["med-1", "note-1"]


def test_picture_and_feedback_use_the_same_proposal_derivation(monkeypatch):
    personas = load_personas()
    encounter_id = "bound-encounter-004-sosiaalipalvelut-sosiaalityontekija"
    encounter, persona = resolve_encounter(encounter_id, personas, ENCOUNTERS)

    async def no_decision(*_args):
        return []

    monkeypatch.setattr(pipeline, "classify_decision_notes", no_decision)
    monkeypatch.setattr(pipeline.llm, "foundry_configured", lambda: False)
    # Both derivations are pinned to the same fixed date. They used to each call the real clock
    # (`date.today()`), which is symmetric only until midnight crosses between the two calls; a
    # fixed date is what the rest of the suite already uses (TODAY in test_bound_basics/test_forms).
    picture = asyncio.run(pipeline._build_resolved_picture(encounter, persona, TODAY))
    store = Store(personas=personas, path=None, encounters=ENCOUNTERS)
    _, _, feedback_proposals = main._encounter_proposals(encounter_id, store, TODAY)

    assert feedback_proposals
    assert [item.model_dump(mode="json") for item in feedback_proposals] == [
        item.model_dump(mode="json") for item in picture.proposals
    ]


def test_decision_additions_do_not_change_review_proposal_inputs(monkeypatch):
    personas = load_personas()
    encounter, persona = resolve_encounter("bound-encounter-004-sosiaalipalvelut-sosiaalityontekija", personas, ENCOUNTERS)
    candidate = Record(
        record_id="note-decision-addition",
        source="syntheettinen esitietolomake",
        record_type="vapaateksti",
        authored_by_role="sosiaalityontekija",
        recorded_at=datetime(2026, 9, 20, tzinfo=timezone.utc),
        topic="sosiaalipalvelut",
        sensitivity_tag="sensitive",
        consent_scope="social_care",
        payload={"otsikko": "Esitieto", "teksti": "Asiakas kertoo arjen tilanteesta."},
    )
    persona = persona.model_copy(update={"records": [*persona.records, candidate]})
    outcomes = [[], [{"record_id": candidate.record_id, "status": "relevant", "model": "decision-1.13.0"}]]

    async def classify_notes(scoped, baseline_ids, today):
        assert candidate in scoped.records
        return outcomes.pop(0)

    monkeypatch.setattr(pipeline, "classify_decision_notes", classify_notes)
    monkeypatch.setattr(pipeline.llm, "foundry_configured", lambda: False)
    first = asyncio.run(pipeline._build_resolved_picture(encounter, persona, TODAY))
    second = asyncio.run(pipeline._build_resolved_picture(encounter, persona, TODAY))
    assert first.selection is not None and second.selection is not None
    assert first.selection.additional_record_ids == []
    assert second.selection.additional_record_ids == [candidate.record_id]
    assert [item.model_dump() for item in first.proposals] == [item.model_dump() for item in second.proposals]
    assert all(set(item.evidence_record_ids) <= set(first.selection.baseline_record_ids) for item in first.proposals)
