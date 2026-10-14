"""The external situation-picture contract.

One definition of what the API returns and the frontend consumes. Previously
this shape was an untyped dict spread across decide/grounding/pipeline/main and
App.jsx, so renaming a key failed silently (the UI rendered `undefined`).
Pydantic is already a dependency, so typing it costs nothing and gives FastAPI
the OpenAPI schema for free.

`visible_record_ids` and the raw triage `features` are deliberately NOT here:
they are internal to the decision layer and never cross this seam.

This module also hosts `GroundingResult`, the internal typed result of the
grounding check (ADR-0002). It is not part of the external contract; it lives
here only because it reuses `RecordView`.

Decision-critical values (situation, risk, actions) are typed so the grounding
check's guarantee -- they come from the decision object, never model text -- is
visible at the seam.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, model_serializer

from .upm import Heading, Record


class Situation(BaseModel):
    situation_type: str
    confidence: str


class Risk(BaseModel):
    """The deterministic risk row, carrying the provenance of its own reason.

    `reason` is the sentence a professional reads in the briefing. Some of those
    sentences are *not* observations: they are the output of a numeric cut-off
    applied to a measured value (`phq9_total >= 15` -> "vaikea masennusoireilu"),
    and the cut-offs are inherited, not calibrated on real Finnish data.

    The obvious place to say so was `risk.json`'s `_meta` string, and that is
    exactly where it was *not read*: the professional reads the rendered sentence,
    not the table. So the qualification travels on the output itself as `basis`,
    set by the rule that made the claim, and the briefing renders it next to that
    reason and nowhere else.

    Why here rather than a frontend-recomputed list of "known cut-off rules": a
    list would be a second source of truth that drifts the first time a rule is
    added, and drifting in the silent direction. The rule that applies the
    threshold is the only thing that knows it applied one.

    Why `basis` is not set on every rule: a rule that restates a source fact
    (`risk-laakitysristiriita`, `risk-akuutti`) or reports that nothing could be
    assessed (`risk-arvioimaton`, `risk-yleinen-matala`) is not making a
    threshold claim, and qualifying it would dilute the qualification into
    boilerplate on every row. The caveat has to sit on the claim it is about.

    `basis` is presentation data. It is never read by `decide.py`, by
    `evaluate_table`, or by any rule predicate, and it never changes a cut-off.
    """

    risk_level: str
    reason: str
    # "illustrative_threshold" on a cut-off-derived reason; None on every other
    # reason, including a source-fact restatement and an absence.
    basis: str | None = None


class Conflict(BaseModel):
    drug: str | None = None
    medication_record_id: str
    note_record_id: str


class TraceEntry(BaseModel):
    table: str
    rule_id: str
    output: dict[str, Any]
    matched: list[dict[str, Any]]


class PipelineStageTrace(BaseModel):
    stage: str
    elapsed_ms: int
    model: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    status: str | None = None


class FormProvenance(BaseModel):
    form_type: str
    version: str | None = None
    service: str
    filled_by_role: str
    due_at: datetime | None = None


class DuplicateFact(BaseModel):
    concept_key: str
    record_ids: list[str]


class FormCatalogueEntry(BaseModel):
    form_type: str
    service: str
    required: bool
    status: Literal["present", "incomplete", "due", "not_shown_in_briefing"]
    record_ids: list[str] = Field(default_factory=list)
    due_at: datetime | None = None
    suggested_next_step: str | None = None


class RecordView(BaseModel):
    record_id: str
    source: str
    record_type: str
    topic: str
    recorded_at: datetime
    payload: dict[str, Any]
    heading: Heading | None = None
    käyntiaika: datetime | None = None
    vastaanottajat: list[str] | None = None
    erikoisala: str | None = None
    # Display-only source field; the UI label is "kirjaava yksikkö".
    suorituspaikka: str | None = None
    code_system: str | None = None
    state: str | None = None
    authored_by_role: str | None = None
    responsible_approver: str | None = None
    # Patient-level display text, sourced from the record; never an ownership or permission axis.
    responsible_clinician: str | None = None
    # Per-record provenance only; no history aggregation or index is derived from these identifiers.
    encounter_id: str | None = None
    case_id: str | None = None
    temporal_status: str = "current"
    selection_reasons: list[str] = Field(default_factory=list)
    form: FormProvenance | None = None
    concept_key: str | None = None

    @model_serializer(mode="wrap")
    def _serialize_optional_vocabulary(self, handler):
        data = handler(self)
        if self.heading is None:
            data.pop("heading", None)
        for name in ("käyntiaika", "vastaanottajat", "erikoisala", "suorituspaikka"):
            if not getattr(self, name):
                data.pop(name, None)
        if self.code_system is None:
            data.pop("code_system", None)
        if self.state is None:
            data.pop("state", None)
        if self.state is None and self.responsible_approver is None:
            data.pop("authored_by_role", None)
        elif self.authored_by_role is None:
            data.pop("authored_by_role", None)
        if self.responsible_approver is None:
            data.pop("responsible_approver", None)
        if self.responsible_clinician is None:
            data.pop("responsible_clinician", None)
        for name in ("encounter_id", "case_id"):
            if not getattr(self, name):
                data.pop(name, None)
        for name in ("form", "concept_key"):
            if getattr(self, name) is None:
                data.pop(name, None)
        return data

    @classmethod
    def from_record(cls, record: Record) -> "RecordView":
        """The one constructor for a source view -- no parallel dict builder."""
        from . import source

        metadata = source.form_metadata(record)
        return cls(
            record_id=record.record_id,
            source=record.source,
            record_type=record.record_type,
            topic=record.topic,
            recorded_at=record.recorded_at,
            heading=record.heading,
            käyntiaika=record.käyntiaika,
            vastaanottajat=record.vastaanottajat,
            erikoisala=record.erikoisala,
            suorituspaikka=record.suorituspaikka,
            code_system=record.code_system,
            state=record.state,
            authored_by_role=record.authored_by_role,
            responsible_approver=record.responsible_approver,
            responsible_clinician=record.responsible_clinician,
            encounter_id=record.encounter_id,
            case_id=record.case_id,
            payload=source.basics_payload(record, "display"),
            form=FormProvenance(
                form_type=metadata.form_type, version=metadata.version, service=metadata.service,
                filled_by_role=record.authored_by_role, due_at=metadata.due_at,
            ) if metadata else None,
            concept_key=record.concept_key,
        )


class GroundingResult(BaseModel):
    """What the grounding checks (`validate_fresh_candidate` +
    `accept_supported_candidate`) found. Internal to Python, not the external
    seam. (Their predecessor, `grounding.enforce`, was superseded and removed.)"""

    narrative: str | None
    citations: list[RecordView]
    grounded: bool
    dropped_citations: list[str]
    dropped_actions: list[str]
    rejected_sentences: list["RejectedSentence"] = Field(default_factory=list)
    # One row per ACCEPTED sentence, in order, with the record ids the verifier actually affirmed for it.
    narrative_sentences: list["NarrativeSentence"] = Field(default_factory=list)


class NarrativeSentence(BaseModel):
    """An accepted narration sentence with its own verified sources.

    `narrative` stays the flat joined string; this is the per-sentence view of the same accepted text, so a
    reader can show which record supports which sentence. `record_ids` are the verifier's
    `supporting_record_ids` for that sentence, never a record outside the permitted and selected set.
    """

    sentence_id: str
    text: str
    record_ids: list[str] = Field(default_factory=list)


class RejectedSentence(BaseModel):
    """A sentence was withheld; disclose only its bounded ID and verifier reason.

    Unverified candidate text must not cross the Picture boundary or render.
    """

    sentence_id: str
    reason: str


class Records(BaseModel):
    priority: list[RecordView]
    accessible_other: list[RecordView]


class GroundingOutcome(BaseModel):
    dropped_citations: list[str]
    dropped_actions: list[str]
    rejected_sentences: list[RejectedSentence] = Field(default_factory=list)


class AssessmentItem(BaseModel):
    osio: str          # item code
    kysymys: str       # Finnish item text
    pisteet: int | None


class Assessment(BaseModel):
    instrumentti: str
    instrument_version: str | None = None
    valmis: bool
    total: int | None
    max_total: int | None
    tulkinta: str | None      # band label, None when not completed
    osiot: list[AssessmentItem]
    # How many answers the SOURCE record supplied, counted before any instrument is looked up. It exists
    # because "not completed" and "nothing there" rendered identically: a supplied RAI result carries two
    # answers and no instrument specification, so `osiot` is empty and the row said only that there was no
    # total -- true, and uninformative about the record it is presenting. This is a count of source facts,
    # recomputable from the record rendered beside it in the same view (`CONTEXT.md:49`), and it is NOT a
    # score: nothing sums it, bands it, or compares it with a cut-off.
    source_answers: int = 0
    lahteet: list[str]        # record_ids
    recorded_at: datetime | None = None
    temporal_status: str = "current"


class DecisionNoteOutcome(BaseModel):
    record_id: str
    status: str
    model: str | None = None
    elapsed_ms: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    relevance_probabilities: dict[str, float] = Field(default_factory=dict)
    gate_probabilities: dict[str, dict[str, float]] = Field(default_factory=dict)
    relevance_confidence: float | None = None
    time_sensitivity: str | None = None
    # Retained on the frozen Picture wire (ADR-0001) but no longer populated: the source-wide
    # negation question was dropped because it cannot safely drive prose. See ADR-0009.
    negation_probability: float | None = None
    subject_binding: str | None = None
    gate_refused: bool = False


class SelectionProvenance(BaseModel):
    baseline_record_ids: list[str] = Field(default_factory=list)
    additional_record_ids: list[str] = Field(default_factory=list)
    emphasized_record_ids: list[str] = Field(default_factory=list)
    rule_version: str
    decision_status: str = "not_run"
    decision_notes: list[DecisionNoteOutcome] = Field(default_factory=list)
    decision_elapsed_ms: int | None = None
    decision_reported_input_tokens: int | None = None
    decision_reported_output_tokens: int | None = None
    decision_usage_complete: bool = False
    decision_candidate_count: int = 0
    decision_shortlisted_count: int = 0
    decision_assessed_count: int = 0
    decision_not_shortlisted_count: int = 0
    decision_budget_exhausted_count: int = 0
    decision_too_long_count: int = 0
    decision_unavailable_count: int = 0
    decision_attempted_count: int = 0
    decision_gate_refused_count: int = 0
    decision_baseline_candidate_count: int = 0
    decision_baseline_attempted_count: int = 0
    decision_missed_candidate_count: int = 0
    decision_missed_attempted_count: int = 0
    # Permitted records held back by the date cut (effective date after the day the picture is built for):
    # how many are not shown and why, never which. Counted over the permitted set only (ADR-0004).
    future_dated_count: int = 0


class ProposalEvidence(BaseModel):
    record_id: str
    recorded_at: datetime | None = None
    instrument: str | None = None
    instrument_version: str | None = None


class ReviewProposal(BaseModel):
    proposal_id: str
    rule_version: str
    text: str
    status: str
    evidence_record_ids: list[str]
    evidence: list[ProposalEvidence]


class EsitietoItem(BaseModel):
    """One fact under an esitieto heading, in the source's own words, citing the record it was read from."""

    text: str
    source_record_ids: list[str] = Field(default_factory=list)
    recorded_at: datetime | None = None
    temporal_status: str = "current"
    kind: Literal["section", "medication", "diagnosis", "measurement"] = "section"


class EsitietoSection(BaseModel):
    """One heading of the county's own esitieto structure (the notes' `## ` headings), filled or marked open."""

    key: str
    heading: str
    status: Literal["answered", "unknown"]
    items: list[EsitietoItem] = Field(default_factory=list)


class PrefillAnswerView(BaseModel):
    question_id: str
    question_text: str
    category_name: str | None = None
    question_type: str
    status: Literal["answered", "unknown"]
    verified: bool = False
    answer_text: str | None = None
    answer_id: str | None = None
    source_record_ids: list[str] = Field(default_factory=list)
    historical: bool = False  # every cited record is history: shown with `Tarkista`


class FormScore(BaseModel):
    total: int
    min: int
    max: int
    title: str
    summary: str | None = None
    function: str
    basis: str


class PrefilledForm(BaseModel):
    """An official form from the release, pre-filled where the permitted records answer it, every answer verified."""

    form_id: str
    revision_id: str
    name: str
    form_role: Literal["official", "related"]
    relation_note: str | None = None
    source: str
    source_file: str | None = None
    source_sha256: str | None = None
    generation_status: Literal["generated", "unavailable", "failed", "timed_out"] = "unavailable"
    generation_model: str | None = None
    verification_status: Literal["not_run", "complete", "partial", "unavailable"] = "not_run"
    # `proposed`: answers the generator offered that cited permitted records; `answered` == `verified`: the ones
    # the verifier confirmed and the form shows. Kept apart so a reader can see how much the verifier declined.
    proposed: int = 0
    answered: int = 0
    verified: int = 0
    total_questions: int = 0
    items: list[PrefillAnswerView] = Field(default_factory=list)
    score: FormScore | None = None


class Picture(BaseModel):
    # No `persona_id`: it is the internal fixture key that joins every audit record, and no reader of the
    # picture needs it — the client navigates by `encounter_id`. Putting it on the wire would hand out the
    # identity key for a person whose `identity_header` is deliberately withheld.
    identity_header: str = "Henkilöllisyystieto ei saatavilla"
    role: str
    purpose: str | None = None
    # The demo's declared purpose of use, from `app.intended_use`: display-only, no legal
    # classification, no withheld-record disclosure, no permission meaning (ADR-0001). The version
    # travels with the sentence so a consumer can tell which declaration a payload was built under,
    # the same reason `narration_generation_*` carries its own provenance.
    intended_use: str | None = None
    intended_use_version: str | None = None
    encounter_id: str | None = None
    selection: SelectionProvenance | None = None
    # Bounded review proposals only; existing clinical action strings are separate.
    proposals: list[ReviewProposal] = Field(default_factory=list)
    situation: Situation
    risk: Risk
    # Suggested next steps, produced by the **optional** `app.actions` module and empty when
    # that module is switched off. The core never fills this: `decide` has no actions key,
    # because the core proposing something is the boundary this system exists to keep. An
    # empty list is a complete briefing, not a degraded one.
    actions: list[str] = Field(default_factory=list)
    # Why each record is in the risk picture, keyed by record id. A restatement of the risk
    # rule that fired, attached to the record it came from, so the reason can be read on the
    # record instead of as a bare patient-level label. Only rules with single-record
    # provenance appear; an absence or an assembled score contributes no entry, because
    # inventing one would fill an absence.
    risk_reasons_by_record: dict[str, list[str]] = Field(default_factory=dict)
    conflicts: list[Conflict]
    narrative: str | None
    citations: list[RecordView]
    # The accepted narration sentence by sentence, each with its verified sources. Empty when there is no
    # accepted narrative, and absent from cache rows written before the field existed: a reader then falls
    # back to `narrative` as one sentence with `citations` as its sources.
    narrative_sentences: list[NarrativeSentence] = Field(default_factory=list)
    records: Records
    duplicate_facts: list[DuplicateFact] = Field(default_factory=list)
    form_catalogue: list[FormCatalogueEntry] = Field(default_factory=list)
    # The pre-filled esitiedot (`app/prefill.py`): the county's own heading structure filled from the permitted
    # records, and the official release forms for this purpose, pre-filled where the records answer them.
    esitiedot: list[EsitietoSection] = Field(default_factory=list)
    prefilled_forms: list[PrefilledForm] = Field(default_factory=list)
    trace: list[TraceEntry]
    pipeline_trace_id: str | None = None
    pipeline_trace: list[PipelineStageTrace] = Field(default_factory=list)
    grounded: bool
    narration_generation_status: Literal["generated", "unavailable", "failed", "timed_out"] = "unavailable"
    narration_input_record_ids: list[str] = Field(default_factory=list)  # sources offered to successful generation
    narration_support_status: Literal["not_run", "supported", "unsupported", "unknown", "invalid", "timed_out"] = "not_run"
    narration_acceptance_status: Literal["accepted", "rejected"] = "rejected"
    narration_generation_deployment: str | None = None
    narration_generation_path: Literal["agent_reference", "direct_fallback", "direct_chat_completions"] | None = None
    narration_generation_model: str | None = None
    narration_support_deployment: str | None = None
    narration_support_model: str | None = None
    narration_generation_elapsed_ms: int | None = None
    narration_generation_input_tokens: int | None = None
    narration_generation_output_tokens: int | None = None
    narration_support_elapsed_ms: int | None = None
    narration_support_input_tokens: int | None = None
    narration_support_output_tokens: int | None = None
    narration_elapsed_ms: int | None = None
    narration_rejection_reason: str | None = None
    grounding: GroundingOutcome
    # deterministic structured assessments (PHQ-9, IADL); scored from
    # role-visible records only, never from the model
    assessments: list[Assessment] = Field(default_factory=list)
