"""End-to-end: persona + role -> decision -> LLM narration -> grounded picture.

This is the sole author of the external `Picture` (ADR-0002). The policy cut
runs once here and travels as a `Scoped` value into decide and the narration
owner (`narration.narrate`), so no downstream module re-derives the permitted
set.

The render split shows accepted citations and Decision-1-relevant selected notes first;
all other selected records remain accessible in the second tier.

Withheld (Poissuljettu) records are **not disclosed in any form**, not even as a
count (ADR-0004, silent absence). The demo shows the role contrast instead.
"""

from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import date
from typing import Literal
from . import assess, source, prefill
from . import actions as actions_module
# pi-lens-ignore: reportMissingImports
from .telemetry import record as telemetry_record, request_trace  # type: ignore[reportMissingImports]
from .decide import decide
from .decision_model import classify_notes as classify_decision_notes, coverage as decision_coverage
from .encounters import Encounter, resolve_encounter
from .intended_use import INTENDED_USE, INTENDED_USE_VERSION
from . import llm
from .narration import Preparation, narrate, narration_fields
from .picture import (DuplicateFact, FormCatalogueEntry, DecisionNoteOutcome, GroundingOutcome, Picture,
                      PipelineStageTrace, RecordView, Records, SelectionProvenance)
from .policy import Scoped, cut_for_encounter
from .proposals import proposals_for_encounter
from .selection import RULE_VERSION, apply_decision, baseline
from .upm import Persona, Record

# 160 s: a 30 s Decision-1 stage, the 80 s narration window and the 45 s pre-fill budget in sequence, with
# room to spare; the browser aborts at 180 s and the ingress at 240 s (owner decision 2026-10-14: no constraints).
PREPARATION_WINDOW_S = 160.0


def _milliseconds(started: float, finished: float | None = None) -> int:
    return max(0, round(((finished or time.monotonic()) - started) * 1000))


def _record_views(scoped: Scoped, record_ids: list[str], reasons: dict[str, tuple[str, ...]], today: date) -> list[RecordView]:
    records = {record.record_id: record for record in scoped.records}
    return [
        RecordView.from_record(records[record_id]).model_copy(
            update={
                "temporal_status": source.temporal_status(records[record_id], today),
                "selection_reasons": list(reasons.get(record_id, ())),
            }
        )
        for record_id in record_ids
        if record_id in records
    ]


_FORM_CATALOGUE: dict[tuple[str, str], tuple[tuple[str, str, bool, str | None], ...]] = {
    ("medication_follow_up", "adult"): (),
    ("mental_health_assessment", "adult"): (
        ("PHQ-9", "mielenterveys", True, None),
    ),
    ("functioning_service_needs", "adult"): (
        ("IADL", "toimintakyky", True, None),
        ("RAI", "toimintakyky", False, None),
    ),
    # Illustrative neuvola rows: the life-stage key, not the purpose alone, decides that these apply.
    ("child_health_checkup", "child"): (
        ("neuvolan terveystarkastuksen esitietolomake", "neuvola", True,
         "Tarkista esitietolomake yhdessä huoltajan kanssa."),
        ("neuvolan kasvukäyrä", "neuvola", True,
         "Tarkista kasvukäyrä ja keskustele löydöksestä huoltajan kanssa."),
        ("neuvolan kuulotutkimus", "neuvola", False, None),
    ),
}


def _narration_eligible(records: Sequence[Record]) -> list[Record]:
    """Only an explicit draft is unfinished evidence; absent state preserves legacy behaviour."""
    return [record for record in records if record.state != "draft"]


def _duplicate_facts(records: list[Record]) -> list[DuplicateFact]:
    """Group explicitly keyed facts from at least two selected form types.

    Group members are ordered by recency only (newest first, record ID as tie-break): the group must
    never order -- or pick -- by form authority, because no source is the winner.
    """
    groups: dict[str, list[Record]] = {}
    form_types: dict[str, set[str]] = {}
    for record in records:
        form = source.form_metadata(record)
        if record.concept_key and form is not None:
            groups.setdefault(record.concept_key, []).append(record)
            form_types.setdefault(record.concept_key, set()).add(form.form_type)
    return [DuplicateFact(concept_key=key, record_ids=[
        record.record_id for record in sorted(rows, key=lambda row: (-row.recorded_at.timestamp(),
                                                                    row.record_id))])
            for key, rows in sorted(groups.items()) if len(form_types[key]) > 1]


def _form_catalogue(purpose: str, life_stage: str, records: list[Record],
                    today: date) -> list[FormCatalogueEntry]:
    entries: list[FormCatalogueEntry] = []
    for form_type, service, required, next_step in _FORM_CATALOGUE.get((purpose, life_stage), ()):
        matches = [(record, source.form_metadata(record)) for record in records]
        matches = [(record, form) for record, form in matches
                   if form is not None and form.form_type == form_type and form.service == service]
        due_dates = [form.due_at for _record, form in matches if form.due_at is not None]
        overdue = [value for value in due_dates if value.date() < today]
        due_at = min(overdue) if overdue else None
        incomplete = (form_type in assess.INSTRUMENTS
                      and any(source.is_assessment(record)
                              and source.assessment_instrument(record) == form_type
                              and not assess.score(record).valmis
                              for record, _form in matches))
        status: Literal["present", "incomplete", "due", "not_shown_in_briefing"] = (
            "not_shown_in_briefing" if not matches else
            "due" if due_at is not None else
            "incomplete" if incomplete else "present"
        )
        entries.append(FormCatalogueEntry(
            form_type=form_type,
            service=service,
            required=required,
            status=status,
            record_ids=sorted(record.record_id for record, _form in matches),
            due_at=due_at,
            suggested_next_step=(next_step if status in {"due", "incomplete", "not_shown_in_briefing"}
                                 and required else None),
        ))
    return entries


async def _build_resolved_picture(
    encounter, persona: Persona, today: date, started_at: float | None = None,
    preparation_deadline: float | None = None, decision_enabled: bool = True, foundry_enabled: bool = True,
) -> Picture:
    started_at = time.monotonic() if started_at is None else started_at
    preparation_deadline = started_at + PREPARATION_WINDOW_S if preparation_deadline is None else preparation_deadline
    policy_started = time.monotonic()
    permitted = cut_for_encounter(persona, encounter)
    telemetry_record("policy.cut", policy_started, status="complete")
    baseline_records, reasons = baseline(permitted, today)
    # Selection changes the briefing input only; deterministic outputs continue
    # to use the full purpose-permitted set and cannot depend on Decision-1.
    decision = decide(permitted)

    # Decision-1 reads current permitted notes after the cut; deterministic outputs do
    # not depend on its response.
    decision_started = time.monotonic()
    note_results = (await classify_decision_notes(permitted, {r.record_id for r in baseline_records}, today)
                    if decision_enabled else [])
    decision_elapsed_ms = _milliseconds(decision_started)
    selection = apply_decision(permitted, baseline_records, reasons, note_results, today)
    if not decision_enabled:
        selection = replace(selection, decision_status="disabled")
    decision_model = next((row.get("model") for row in note_results if row.get("model")), None)
    telemetry_record("decision.selection", decision_started, model=decision_model, status=selection.decision_status)
    # Sources reach the generator most relevant to this encounter first (validated Decision-1 relevance, then
    # urgency, then the deterministic order). The order only sorts records already eligible; it never adds, removes
    # or demotes one. ponytail: narration_instructions.md still says the first source is the most time-critical; that
    # sentence is no longer true under this order. Prompt left unchanged per L3D; owner to decide the wording.
    eligible_narration_records = _narration_eligible(selection.narration_records)
    narration_by_id = {record.record_id: record for record in eligible_narration_records}
    narration_order = selection.relevance_order([record.record_id for record in eligible_narration_records])
    narration_records = [narration_by_id[record_id] for record_id in narration_order]
    narration_scope = replace(permitted, persona=persona.model_copy(update={"records": narration_records}))
    narration_ids = [record.record_id for record in narration_records]
    narration = await narrate(
        narration_scope,
        narration_ids,
        today=today,
        preparation=Preparation(started_at=started_at, deadline=preparation_deadline),
        stages=llm,
        foundry_enabled=foundry_enabled,
    )
    # The pre-filled esitiedot (the flagship): the county's own structure deterministically, then the official
    # forms through the generator and the verifier, over the permitted set. Bounded by its own budget inside the
    # preparation window; a form that fails is an empty form, never a failed briefing.
    prefill_started = time.monotonic()
    esitiedot, prefilled = await prefill.build(permitted, encounter.purpose, today,
                                               deadline=preparation_deadline, foundry_enabled=foundry_enabled)
    telemetry_record("prefill.total", prefill_started, status="complete")
    narration_columns = narration_fields(narration)
    grounding = narration.grounding
    cited_ids = [record.record_id for record in grounding.citations]
    selected_ids = [record.record_id for record in selection.records]
    selected_views = _record_views(permitted, selected_ids, selection.reasons, today)
    selected_by_id = {row.record_id: row for row in selected_views}
    citation_views = [selected_by_id[row.record_id] for row in grounding.citations if row.record_id in selected_by_id]
    priority_views = [selected_by_id[record_id] for record_id in selection.priority_ids(cited_ids)
                      if record_id in selected_by_id]
    priority_ids = {row.record_id for row in priority_views}
    selected_records = list(selection.records)
    duplicate_fact_rows = _duplicate_facts(selected_records)
    form_catalogue_rows = _form_catalogue(encounter.purpose, encounter.life_stage, selected_records, today)
    assessments = assess.select_for_picture(baseline_records, today)
    proposals = proposals_for_encounter(
        encounter, permitted, baseline_records, decision["conflicts"], today
    )
    reported_note_ids = set(selection.baseline_record_ids) | set(selection.additional_record_ids)
    note_outcomes = [DecisionNoteOutcome(
        record_id=item["record_id"], status=item["status"], model=item.get("model"),
        elapsed_ms=item.get("elapsed_ms"), input_tokens=item.get("input_tokens"),
        output_tokens=item.get("output_tokens"),
        relevance_probabilities=(item.get("relevance") or {}).get("probabilities", {}),
        gate_probabilities={key: value.get("probabilities", {}) for key, value in item.items()
                            if key in {"time_sensitivity", "subject_binding", "urgency"} and isinstance(value, dict)},
        relevance_confidence=(item.get("relevance") or {}).get("confidence"),
        time_sensitivity=(item.get("time_sensitivity") or {}).get("choice"),
        # The source-wide negation question was dropped; the wire field is retained but unpopulated.
        negation_probability=None,
        subject_binding=(item.get("subject_binding") or {}).get("choice"),
        gate_refused=bool(item.get("gate_refused")),
    ) for item in selection.decision_notes if item.get("record_id") in reported_note_ids]
    coverage = decision_coverage(note_results)
    # The optional actions module runs after the core has finished and reads only the core's
    # two outputs. `LUVN_ACTIONS_MODULE=0` switches it off and the picture carries no
    # suggestions; nothing else about the briefing changes, which is the point of the module
    # boundary. Its trace entry is appended only when it actually ran.
    actions: list[str] = []
    decision_trace = list(decision["trace"])
    if actions_module.enabled():
        risk_rule_id = next(entry["rule_id"] for entry in decision_trace if entry["table"] == "risk")
        actions, actions_trace = actions_module.suggest_actions(
            decision["situation"]["situation_type"], decision["risk"]["risk_level"], risk_rule_id)
        decision_trace.append(actions_trace)
    return Picture(
        identity_header=source.identity_header(permitted.records),
        role=encounter.role,
        purpose=encounter.purpose,
        intended_use=INTENDED_USE,
        intended_use_version=INTENDED_USE_VERSION,
        encounter_id=encounter.encounter_id,
        situation=decision["situation"],
        risk=decision["risk"],
        actions=actions,
        risk_reasons_by_record=decision.get("risk_reasons_by_record", {}),
        conflicts=decision["conflicts"],
        narrative=grounding.narrative,
        citations=citation_views,
        records=Records(
            priority=priority_views,
            accessible_other=[row for row in selected_views if row.record_id not in priority_ids],
        ),
        duplicate_facts=duplicate_fact_rows,
        form_catalogue=form_catalogue_rows,
        esitiedot=esitiedot,
        prefilled_forms=prefilled,
        trace=decision_trace,
        grounded=grounding.grounded,
        **narration_columns,
        grounding=GroundingOutcome(
            dropped_citations=grounding.dropped_citations,
            dropped_actions=grounding.dropped_actions,
            rejected_sentences=grounding.rejected_sentences,
        ),
        proposals=proposals,
        selection=SelectionProvenance(
            baseline_record_ids=list(selection.baseline_record_ids),
            additional_record_ids=list(selection.additional_record_ids),
            emphasized_record_ids=list(selection.emphasized_record_ids),
            rule_version=RULE_VERSION,
            decision_status=selection.decision_status,
            decision_notes=note_outcomes,
            decision_elapsed_ms=decision_elapsed_ms,
            **coverage,
        ),
        assessments=assessments,
    )


async def build_picture_for_encounter(
    encounter_id: str,
    personas: list[Persona] | Mapping[str, Persona] | None = None,
    today: date | None = None,
    preparation_deadline: float | None = None,
    started_at: float | None = None,
    decision_enabled: bool = True,
    foundry_enabled: bool = True,
    encounters: Mapping[str, Encounter] | None = None,
) -> Picture:
    """Resolve the encounter and return a bounded, accepted-only Picture."""
    started_at = time.monotonic() if started_at is None else started_at
    preparation_deadline = (started_at + PREPARATION_WINDOW_S
                            if preparation_deadline is None else preparation_deadline)
    with request_trace() as trace:
        try:
            if personas is None:
                from .fixtures import load_personas

                personas = load_personas()
            if encounters is None:
                raise ValueError("build_picture_for_encounter needs an encounter catalogue (encounters=)")
            resolve_started = time.monotonic()
            encounter, persona = resolve_encounter(encounter_id, personas, encounters)
            trace.record("encounter.resolve", round((time.monotonic() - resolve_started) * 1000), status="complete")
            picture = await _build_resolved_picture(
                encounter, persona, today or date.today(), started_at, preparation_deadline,
                decision_enabled, foundry_enabled,
            )
            trace.record("pipeline.total", round((time.monotonic() - started_at) * 1000), status="complete")
            pipeline_trace = [PipelineStageTrace.model_validate(event) for event in trace.events]
            return picture.model_copy(update={"pipeline_trace_id": trace.trace_id,
                                              "pipeline_trace": pipeline_trace})
        finally:
            await trace.flush()
