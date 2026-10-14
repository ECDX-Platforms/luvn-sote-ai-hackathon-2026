"""The Picture contract: it is typed, and its keys match what the UI reads.

If someone renames a field, the frontend silently renders `undefined`. This test
pins the top-level keys the UI depends on, so the rename fails here instead.
"""

import asyncio
from pathlib import Path

import app.pipeline as pipeline
from app import decision_model, source
from app.actions import suggest_actions
from app.decide import decide
from app.fixtures import load_personas
from app.picture import (
    Assessment, AssessmentItem, Conflict, DuplicateFact, FormCatalogueEntry, FormProvenance,
    DecisionNoteOutcome, Picture, RecordView, PipelineStageTrace, Records, ReviewProposal,
    Risk, SelectionProvenance, Situation, TraceEntry,
)
from app.upm import HEADING_PAIRS, FormMetadata, Record
from tests.fixtures.bound_encounters import ENCOUNTERS
from app.pipeline import build_picture_for_encounter
from app.policy import cut

def _matched_risk_rule_id(decision: dict) -> str:
    """The risk rule the decision matched, as the actions module now receives it.

    Some step sets are keyed on the core's own conclusion rather than on the two scalars alone, so a test that
    compares the picture's actions against the module has to pass the same rule id the pipeline does. It is read
    from the decision's trace, which is where the core records what it matched.
    """
    return next(entry["rule_id"] for entry in decision["trace"] if entry.get("table") == "risk")


# Top-level Picture keys read by picturePresentation / ChallengeDemo.
NARRATION_KEYS = {
    "narration_generation_status", "narration_input_record_ids", "narration_support_status",
    "narration_acceptance_status", "narration_generation_deployment", "narration_generation_path",
    "narration_generation_model",
    "narration_support_deployment", "narration_support_model", "narration_generation_elapsed_ms",
    "narration_generation_input_tokens", "narration_generation_output_tokens",
    "narration_support_input_tokens", "narration_support_output_tokens",
    "narration_support_elapsed_ms", "narration_elapsed_ms", "narration_rejection_reason",
}
FRONTEND_KEYS = {
    "identity_header", "encounter_id", "role", "selection", "proposals",
    "conflicts", "assessments", "narrative", "citations", "records",
    "duplicate_facts", "form_catalogue", "pipeline_trace_id", "pipeline_trace",
    "intended_use", "intended_use_version", "esitiedot", "prefilled_forms",
    *NARRATION_KEYS,
}


def test_contract_exposes_the_keys_the_frontend_reads():
    assert FRONTEND_KEYS <= set(Picture.model_json_schema()["properties"])
    assert NARRATION_KEYS == {name for name in Picture.model_fields if name.startswith("narration_")}


def test_internal_decider_fields_do_not_leak_into_the_contract():
    props = set(Picture.model_json_schema()["properties"])
    assert {"features", "visible_record_ids"}.isdisjoint(props)


def test_optional_source_vocabulary_preserves_existing_fixture_render_and_projects_heading():
    national_headings = (
        "Nykytila", "Esitiedot", "Diagnoosi", "Lääkehoito", "Fysiologiset mittaukset",
        "Toimenpiteet", "Testaus- ja arviointitulokset", "Hoitotoimet", "Konsultaatio/Lähete",
        "Ennaltaehkäisy", "Suunnitelma", "Tulosyy",
    )
    assert all(HEADING_PAIRS[name] == (name, name) for name in national_headings)
    assert HEADING_PAIRS["Hoidon tarpeen arviointi"] == ("Hoidon tarpeen arviointi", "Suunnitelma")
    personas = load_personas()
    legacy_keys = {"record_id", "source", "record_type", "topic", "recorded_at", "payload",
                   "temporal_status", "selection_reasons"}
    optional_fields = {
        "heading": lambda record: record.heading is not None,
        "code_system": lambda record: record.code_system is not None,
        "state": lambda record: record.state is not None,
        "authored_by_role": lambda record: record.state is not None or record.responsible_approver is not None,
        "responsible_approver": lambda record: record.responsible_approver is not None,
        "responsible_clinician": lambda record: record.responsible_clinician is not None,
        "encounter_id": lambda record: record.encounter_id is not None,
        "case_id": lambda record: record.case_id is not None,
        "käyntiaika": lambda record: record.käyntiaika is not None,
        "vastaanottajat": lambda record: bool(record.vastaanottajat),
        "erikoisala": lambda record: bool(record.erikoisala),
        "suorituspaikka": lambda record: bool(record.suorituspaikka),
        "form": lambda record: source.form_metadata(record) is not None,
        "concept_key": lambda record: record.concept_key is not None,
    }
    for persona in personas:
        for record in persona.records:
            rendered = RecordView.from_record(record).model_dump(mode="json")
            expected_optional = {key for key, present in optional_fields.items() if present(record)}
            assert set(rendered) == legacy_keys | expected_optional
            assert rendered["topic"] == record.topic
    original = next(record for persona in personas for record in persona.records)
    assert set(RecordView.from_record(original).model_dump(mode="json")) == legacy_keys
    headed_record = Record.model_validate({
        **original.model_dump(mode="python"), "heading": "Yhteydenotto", "code_system": "ICD-10"
    })
    rendered = RecordView.from_record(headed_record).model_dump(mode="json")
    assert rendered["heading"] == {"local": "Yhteydenotto", "national": "Etäkontakti"}
    assert rendered["code_system"] == "ICD-10"
    assert rendered["topic"] == original.topic
    attributed = RecordView.from_record(original.model_copy(update={
        "encounter_id": "encounter-attributed", "case_id": "case-attributed",
    })).model_dump(mode="json")
    assert attributed["encounter_id"] == "encounter-attributed"
    assert attributed["case_id"] == "case-attributed"
    assert "encounter_id" not in RecordView.from_record(original).model_dump(mode="json")
    assert "case_id" not in RecordView.from_record(original).model_dump(mode="json")


def test_absent_form_and_concept_key_are_omitted_from_record_view():
    record = next(record for persona in load_personas() for record in persona.records
                  if record.form is None and record.concept_key is None
                  and source.form_metadata(record) is None)
    rendered = RecordView.from_record(record).model_dump(mode="json")
    assert "form" not in rendered
    assert "concept_key" not in rendered


def test_record_provenance_renders_only_present_entry_fields_and_unit_is_display_only():
    original = load_personas()[0].records[0]
    assert {"role", "purpose"} <= set(Picture.model_fields)
    provenance = {
        "käyntiaika": original.recorded_at,
        "encounter_id": "encounter-provenance",
        "case_id": "case-provenance",
        "vastaanottajat": ["this-entry-recipient"],
        "erikoisala": "yleislääketiede",
        "suorituspaikka": "kirjaava-yksikkö",
    }
    record = Record.model_validate({**original.model_dump(mode="python"), **provenance})
    view = RecordView.from_record(record).model_dump(mode="json")
    assert {key: view[key] for key in provenance} == {
        **provenance, "käyntiaika": provenance["käyntiaika"].isoformat()
    }
    assert "suorituspaikka" not in RecordView.from_record(original).model_dump(mode="json")
    assert "toiminto" not in Record.model_fields and "toiminto" not in RecordView.model_fields


def test_patient_level_responsible_clinician_is_display_only_and_absent_by_default():
    """Distinct from the record-level approver and rendered only when the record supplies it."""
    original = load_personas()[0].records[0]
    assert original.responsible_clinician is None
    assert "responsible_clinician" not in RecordView.from_record(original).model_dump(mode="json")
    record = Record.model_validate({
        **original.model_dump(mode="python"),
        "responsible_approver": "sairaanhoitaja",
        "responsible_clinician": "Vastuuhenkilö A",
    })
    view = RecordView.from_record(record).model_dump(mode="json")
    assert view["responsible_clinician"] == "Vastuuhenkilö A"
    assert view["responsible_approver"] == "sairaanhoitaja"
    # No permission axis and no registry: the same cut with and without the display value.
    encounter = ENCOUNTERS["bound-encounter-002-laakitys-sairaanhoitaja"]
    persona = next(item for item in load_personas() if item.persona_id == encounter.persona_id)
    with_clinician = persona.model_copy(update={
        "records": [item.model_copy(update={"responsible_clinician": "Vastuuhenkilö A"})
                    for item in persona.records]
    })
    assert cut(persona, encounter.role, encounter.purpose, encounter.encounter_id,
               encounter.consent_holders).ids == cut(
        with_clinician, encounter.role, encounter.purpose, encounter.encounter_id,
        encounter.consent_holders
    ).ids

    encounter = ENCOUNTERS["bound-encounter-002-laakitys-sairaanhoitaja"]
    persona = next(item for item in load_personas() if item.persona_id == encounter.persona_id)
    with_unit = persona.model_copy(update={
        "records": [item.model_copy(update={"suorituspaikka": "kirjaava-yksikkö"}) for item in persona.records]
    })
    assert cut(persona, encounter.role, encounter.purpose, encounter.encounter_id,
               encounter.consent_holders).ids == cut(
        with_unit, encounter.role, encounter.purpose, encounter.encounter_id, encounter.consent_holders
    ).ids


def test_recipients_remain_scoped_to_the_entry_and_never_become_history():
    original = load_personas()[0].records[0]
    entries = [Record.model_validate({
        **original.model_dump(mode="python"), "record_id": f"entry-{index}",
        "vastaanottajat": [recipient],
    }) for index, recipient in enumerate(("recipient-a", "recipient-b"))]
    views = [RecordView.from_record(record).model_dump(mode="json") for record in entries]
    assert [view["vastaanottajat"] for view in views] == [["recipient-a"], ["recipient-b"]]
    assert all("yhteydenotot" not in view and "recipient_history" not in view for view in views)
    assert "recipient-a" not in views[1]["payload"].values()
    assert "recipient-b" not in views[0]["payload"].values()


def test_contract_exposes_the_nested_keys_the_frontend_reads():
    """Top-level keys alone are not enough: nested renames still render undefined."""
    assert {"priority", "accessible_other"} <= set(Records.model_fields)
    # silent absence (ADR-0004): the withheld count is gone from the contract
    assert "excluded_count" not in Records.model_fields
    assert {"record_id", "record_type", "topic", "source", "payload", "recorded_at", "temporal_status", "selection_reasons", "form", "concept_key", "heading", "code_system", "state", "authored_by_role", "responsible_approver", "responsible_clinician", "encounter_id", "case_id", "käyntiaika", "vastaanottajat", "erikoisala", "suorituspaikka"} <= set(RecordView.model_fields)
    assert {"form_type", "service", "version", "filled_by_role", "due_at"} <= set(FormProvenance.model_fields)
    assert {"concept_key", "record_ids"} <= set(DuplicateFact.model_fields)
    assert {"form_type", "service", "required", "status", "record_ids", "due_at", "suggested_next_step"} <= set(FormCatalogueEntry.model_fields)
    assert {"form", "concept_key"} <= set(Record.model_fields)
    assert {"form_type", "service", "version", "due_at"} <= set(FormMetadata.model_fields)
    assert {"drug", "medication_record_id", "note_record_id"} <= set(Conflict.model_fields)
    assert {"baseline_record_ids", "additional_record_ids", "emphasized_record_ids", "rule_version", "decision_status", "decision_notes"} <= set(SelectionProvenance.model_fields)
    assert {"record_id", "status", "model"} <= set(DecisionNoteOutcome.model_fields)
    assert {"instrumentti", "instrument_version", "recorded_at", "temporal_status", "valmis", "total", "max_total", "tulkinta", "osiot", "lahteet"} <= set(Assessment.model_fields)
    assert {"proposal_id", "rule_version", "text", "evidence_record_ids"} <= set(ReviewProposal.model_fields)
    assert {"osio", "kysymys", "pisteet"} <= set(AssessmentItem.model_fields)


def test_unread_structured_decision_fields_remain_on_the_picture_contract():
    props = set(Picture.model_json_schema()["properties"])
    assert {"situation", "risk", "actions", "grounded", "trace", "purpose"} <= props
    assert {"situation_type", "confidence"} <= set(Situation.model_fields)
    assert {"risk_level", "reason"} <= set(Risk.model_fields)
    assert {"table", "rule_id", "matched"} <= set(TraceEntry.model_fields)
    assert {"stage", "elapsed_ms", "model", "input_tokens", "output_tokens", "status"} <= set(PipelineStageTrace.model_fields)
    assert Picture.model_fields["narration_generation_path"].default is None


def test_pipeline_returns_a_typed_picture():
    pic = asyncio.run(build_picture_for_encounter("bound-encounter-002-laakitys-sairaanhoitaja", encounters=ENCOUNTERS))
    assert isinstance(pic, Picture)
    assert pic.risk.risk_level == "ristiriita"
    # Without a live Foundry response there is no narration to accept or cite.
    assert pic.narration_generation_status == "unavailable"
    assert pic.narrative is None and pic.citations == []


def test_picture_rejects_invalid_fresh_candidate_without_leaking_prose(monkeypatch):
    """A membership-invalid candidate cannot replace structured decisions."""
    personas = load_personas()
    encounter, persona = pipeline.resolve_encounter("bound-encounter-002-laakitys-sairaanhoitaja", personas, ENCOUNTERS)
    decision = decide(cut(persona, encounter.role))

    generation_feedback = []
    permitted_ids = set()

    async def bad_generation(scoped, today, timeout_s, **kwargs):
        permitted_ids.update(scoped.ids)
        generation_feedback.append(kwargs.get("rejection_feedback"))
        return pipeline.llm.FoundryResult(
            output=pipeline.llm.FreshNarration(sentences=[pipeline.llm.NarrationSentence(
                sentence_id="s1", text="keksitty kuvaus", source_ids=["note-999"]
            )]),
            model="gpt-5.4-mini-test",
        )

    monkeypatch.setenv("LUNA_FOUNDRY_KEY", "fake")
    monkeypatch.setattr(pipeline.llm, "foundry_configured", lambda: True)
    monkeypatch.setattr(pipeline.llm, "generate_fresh_narration", bad_generation)
    monkeypatch.setattr(
        decision_model,
        "verify_narration",
        lambda *_args: (_ for _ in ()).throw(AssertionError("invalid candidate must not reach Decision-1")),
    )
    pic = asyncio.run(build_picture_for_encounter(encounter.encounter_id, personas, encounters=ENCOUNTERS))

    assert len(generation_feedback) == 2, "invalid membership gets only one retry"
    assert generation_feedback[1] == [{"sentence_id": "s1", "reason": "invalid_membership",
                                       "permitted_citation_ids": sorted(permitted_ids)}]
    assert "keksitty kuvaus" not in str(generation_feedback)
    assert pic.grounded is False
    assert pic.narrative is None
    assert pic.records.priority == [] and pic.citations == [], "rejected narration claims no citations"
    assert pic.narration_generation_status == "generated"
    assert pic.narration_support_status == "invalid"
    assert pic.narration_acceptance_status == "rejected"
    assert pic.narration_rejection_reason == "invalid_membership"
    # Actions come from the optional module, never from the core's decision record.
    assert pic.actions == suggest_actions(decision["situation"]["situation_type"],
                                          decision["risk"]["risk_level"],
                                          _matched_risk_rule_id(decision))[0]
    assert pic.situation.situation_type == decision["situation"]["situation_type"]
    assert pic.risk.risk_level == decision["risk"]["risk_level"]


def test_heading_pairs_maps_toimintakyky_to_the_vendor_local_heading():
    """L: HEADING_PAIRS maps the national heading Toimintakyky to a real source heading.

    The local side is "Testaus- ja arviointitulokset" (the vendor's own heading for assessment
    content, per ADR-0015's provenance table), not an identity pair — Toimintakyky must alias an
    actual source heading, never alias itself.
    """
    assert HEADING_PAIRS["Toimintakyky"] == ("Testaus- ja arviointitulokset", "Toimintakyky")
    assert HEADING_PAIRS["Toimintakyky"][0] != HEADING_PAIRS["Toimintakyky"][1], \
        "Toimintakyky must not be an identity pair"


def test_heading_pairs_preserves_existing_source_headings():
    """L: adding Toimintakyky must not disturb the existing Yhteydenotto/Hoidon tarpeen arviointi pairs."""
    assert HEADING_PAIRS["Yhteydenotto"] == ("Yhteydenotto", "Etäkontakti")
    assert HEADING_PAIRS["Etäkontakti"] == ("Yhteydenotto", "Etäkontakti")
    assert HEADING_PAIRS["Hoidon tarpeen arviointi"] == ("Hoidon tarpeen arviointi", "Suunnitelma")
    # The vendor's own local heading keeps its identity entry too: Toimintakyky is an ADDITIONAL
    # alias, not a replacement of the existing "Testaus- ja arviointitulokset" entry.
    assert HEADING_PAIRS["Testaus- ja arviointitulokset"] == (
        "Testaus- ja arviointitulokset", "Testaus- ja arviointitulokset")


def test_frontend_arviointi_label_names_the_national_term():
    """L: SOURCE_FIELDS['arviointi'] in the frontend adapter uses the national term 'Toimintakykymittari',
    not the former generic 'Mittari' label."""
    root = Path(__file__).resolve().parents[2]
    text = (root / "frontend/src/challenge-demo.mjs").read_text(encoding="utf-8")
    block = text.split("const SOURCE_FIELDS = {", 1)[1].split("\n};", 1)[0]
    arviointi_line = next(line for line in block.splitlines() if line.strip().startswith("arviointi:"))
    assert "Toimintakykymittari" in arviointi_line
    assert "'Mittari'" not in arviointi_line


def test_knowledge_registry_attribution_is_read_by_national_term_attribution():
    """L: the registry's national_terms_sourced/national_headings_sourced lists are read by a real
    code path (`knowledge.national_term_attribution`), and that path resolves the exact term and
    heading the shipped code now uses."""
    from app import knowledge

    assert knowledge.national_term_attribution("toimintakykymittari") == "THL (Terveyden ja hyvinvoinnin laitos)"
    assert knowledge.national_term_attribution("Toimintakyky") == "THL (Terveyden ja hyvinvoinnin laitos)"
    assert knowledge.source("thl-sote-sanastot") is not None
    # A term nobody declared must resolve to nothing, so the function cannot manufacture a citation.
    assert knowledge.national_term_attribution("not-a-sourced-term") is None
