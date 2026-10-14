import asyncio
import json
import re
from pathlib import Path
from datetime import date, datetime, timezone

from app import assess, source
from app.decide import decide
from app.encounters import Encounter
from app.fixtures import load_personas
from app.pipeline import _build_resolved_picture, _duplicate_facts, _form_catalogue, build_picture_for_encounter
from app.picture import FormCatalogueEntry, RecordView
from app.policy import cut
from app.triage import extract_features
from app.upm import ConsentScope, FormMetadata, Persona, Record
from tests.fixtures.bound_encounters import ENCOUNTERS

SOCIAL_WORKER_ENCOUNTER = "bound-encounter-004-sosiaalipalvelut-sosiaalityontekija"
SOCIAL_WORKER_PERSONA = "bound-persona-004-sosiaalipalvelut"


def _social_worker_picture(personas=None):
    return asyncio.run(build_picture_for_encounter(
        SOCIAL_WORKER_ENCOUNTER, personas=personas or load_personas(), today=TODAY,
        decision_enabled=False, foundry_enabled=False, encounters=ENCOUNTERS
    ))


def _social_worker_persona(personas=None):
    return next(persona for persona in (personas or load_personas())
                if persona.persona_id == SOCIAL_WORKER_PERSONA)

TODAY = date(2026, 10, 2)


def _record(record_id: str, form_type: str, concept_key: str | None, *, due_at=None,
            record_type: str = "mittaus", topic: str | None = None,
            consent_scope: ConsentScope = "treatment", service: str = "synthetic-service",
            payload: dict | None = None) -> Record:
    return Record(
        record_id=record_id,
        source="synthetic-system",
        record_type=record_type,
        authored_by_role="sairaanhoitaja",
        recorded_at=datetime(2026, 10, 1, tzinfo=timezone.utc),
        topic=topic or ("mielenterveys" if record_type == "arviointi" else "verenpaine"),
        sensitivity_tag="none",
        consent_scope=consent_scope,
        payload=payload or {"systolic_mmhg": 132, "unit": "mmHg"},
        form=FormMetadata(form_type=form_type, version="demo-v1", service=service, due_at=due_at),
        concept_key=concept_key,
    )


def _phq9(*, due_at=None, complete: bool = True) -> Record:
    answers = {f"q{index}": 0 for index in range(1, 10)} if complete else {"q1": 0}
    return _record(
        "phq9-source", "PHQ-9", None, due_at=due_at, record_type="arviointi", service="mielenterveys",
        payload={"instrumentti": "PHQ-9", "versio": "demo-v1", "vastaukset": answers},
    )


def test_duplicate_group_uses_explicit_selected_concept_and_distinct_forms():
    intake = _record("intake", "intake-questionnaire", "bp.systolic.v1.observation-1")
    measurement = _record("measurement", "home-measurement-form", "bp.systolic.v1.observation-1")
    same_form = _record("repeat", "intake-questionnaire", "bp.systolic.v1.observation-1")
    different_fact = _record("other-fact", "other-form", "bp.diastolic.v1.observation-1")
    unrelated = _record("unrelated", "other-form", "bp.systolic.v1.observation-2")

    assert intake.payload == measurement.payload == {"systolic_mmhg": 132, "unit": "mmHg"}

    [group] = _duplicate_facts([intake, measurement, same_form, different_fact])
    assert group.concept_key == "bp.systolic.v1.observation-1"
    assert group.record_ids == ["intake", "measurement", "repeat"]
    assert _duplicate_facts([intake, same_form]) == [], "one form type alone is not a two-form duplicate"
    assert _duplicate_facts([intake]) == [], "unselected sources cannot create a group"
    assert _duplicate_facts([intake, unrelated]) == [], "different versioned observation keys never group"


def test_record_view_projects_form_provenance_from_existing_record_fields():
    intake = _record("intake", "intake-questionnaire", "bp.systolic.v1.observation-1")
    view = RecordView.from_record(intake)
    assert view.form is not None and view.form.filled_by_role == "sairaanhoitaja"
    assert (view.form.form_type, view.form.version, view.form.service, view.form.due_at) == (
        "intake-questionnaire", "demo-v1", "synthetic-service", None,
    )
    assert view.recorded_at == intake.recorded_at  # filled-at reuses source time


def test_same_encounter_impact_count_uses_selected_source_labels_and_form_types():
    picture = asyncio.run(build_picture_for_encounter(
        "bound-encounter-003-mielenterveys-sairaanhoitaja",
        personas=load_personas(), today=TODAY, decision_enabled=False, foundry_enabled=False, encounters=ENCOUNTERS
    ))
    selected = [*picture.records.priority, *picture.records.accessible_other]
    assert {record.record_id for record in selected} == {
        "bound-demo-003", "bound-note-003", "bound-dx-004", "bound-med-004", "bound-arviointi-003",
        "bound-note-003-alignment", "bound-note-003-draft",
    }, "only the two declared Phase 4 additions extend the selected mental-health set"
    source_labels = {record.source for record in selected if record.source}
    form_types = {(record.form.form_type, record.form.version, record.form.service)
                  for record in selected if record.form is not None}
    assert len(source_labels) == 3
    assert form_types == {("PHQ-9", None, "mielenterveys")}
    [phq9] = [entry for entry in picture.form_catalogue if entry.form_type == "PHQ-9"]
    assert (phq9.status, phq9.record_ids) == ("present", ["bound-arviointi-003"])


def _build_synthetic_picture(records: list[Record]):
    persona = Persona(persona_id="synthetic-person", label="Synteettinen", records=records)
    encounter = Encounter("synthetic-encounter", "synthetic-person", "sairaanhoitaja", "mental_health_assessment")
    return asyncio.run(_build_resolved_picture(
        encounter, persona, TODAY, decision_enabled=False, foundry_enabled=False,
    ))


def test_picture_preserves_both_duplicate_sources_without_winner():
    records = [
        _record("form-a", "intake-questionnaire", "bp.systolic.v1.observation-1", topic="mielenterveys"),
        _record("form-b", "home-measurement-form", "bp.systolic.v1.observation-1", topic="mielenterveys"),
    ]
    picture = _build_synthetic_picture(records)
    group, = picture.duplicate_facts
    views = {record.record_id: record for record in [*picture.records.priority, *picture.records.accessible_other]}
    assert group.record_ids == ["form-a", "form-b"]
    assert views["form-a"].payload == views["form-b"].payload == {"systolic_mmhg": 132, "unit": "mmHg"}
    assert not hasattr(group, "winner")


def test_catalogue_and_duplicate_ids_are_limited_to_selected_view():
    concept_key = "bp.systolic.v1.observation-1"
    records = [
        _record("form-a", "intake-questionnaire", concept_key, topic="mielenterveys"),
        _record("form-b", "home-measurement-form", concept_key, topic="mielenterveys"),
        # A free-text note, not a measurement: a current measurement is baseline context since 2026-10-14
        # (`context:current-result`), so the permitted-but-unselected case must be a type that is not.
        _record("permitted-irrelevant", "PHQ-9", concept_key, topic="hoito", service="mielenterveys",
                record_type="vapaateksti"),
        _record("withheld-form", "home-measurement-form", concept_key,
                topic="mielenterveys", consent_scope="social_care"),
    ]
    picture = _build_synthetic_picture(records)
    selected_ids = {record.record_id for record in [*picture.records.priority, *picture.records.accessible_other]}
    assert {"form-a", "form-b"} <= selected_ids
    assert {"permitted-irrelevant", "withheld-form"}.isdisjoint(selected_ids)
    assert [group.record_ids for group in picture.duplicate_facts] == [["form-a", "form-b"]]
    assert all(set(group.record_ids) <= selected_ids for group in picture.duplicate_facts)
    [phq9] = [entry for entry in picture.form_catalogue if entry.form_type == "PHQ-9"]
    assert (phq9.status, phq9.record_ids) == ("not_shown_in_briefing", [])
    assert all(set(entry.record_ids) <= selected_ids for entry in picture.form_catalogue)


def test_synthetic_catalogue_reports_present_incomplete_due_and_not_shown():
    due = _phq9(due_at=datetime(2026, 10, 1, tzinfo=timezone.utc))
    incomplete = _phq9(complete=False)
    [due_entry] = [entry for entry in _form_catalogue("mental_health_assessment", "adult", [due], TODAY)
                   if entry.form_type == "PHQ-9"]
    [incomplete_entry] = [entry for entry in _form_catalogue("mental_health_assessment", "adult",
                                                             [incomplete], TODAY)
                          if entry.form_type == "PHQ-9"]
    [present_entry] = [entry for entry in _form_catalogue("mental_health_assessment", "adult",
                                                          [_phq9()], TODAY)
                       if entry.form_type == "PHQ-9"]
    [same_day_entry] = [entry for entry in _form_catalogue(
        "mental_health_assessment", "adult",
        [_phq9(due_at=datetime(2026, 10, 2, tzinfo=timezone.utc))], TODAY,
    ) if entry.form_type == "PHQ-9"]
    form_only = _record("form-only", "PHQ-9", None, topic="mielenterveys", service="mielenterveys")
    [form_only_entry] = [entry for entry in _form_catalogue("mental_health_assessment", "adult",
                                                           [form_only], TODAY)
                         if entry.form_type == "PHQ-9"]
    assert (due_entry.status, due_entry.record_ids) == ("due", ["phq9-source"])
    assert (incomplete_entry.status, incomplete_entry.record_ids) == ("incomplete", ["phq9-source"])
    assert (present_entry.status, present_entry.record_ids) == ("present", ["phq9-source"])
    assert same_day_entry.status == "present", "a due date has not passed on the fixed date"
    assert form_only_entry.status == "present", "form metadata alone does not trigger assessment scoring"
    assert due_entry.service == "mielenterveys"
    assert due_entry.due_at is not None and due_entry.due_at.date() == TODAY.replace(day=1)
    assert present_entry.due_at is None, "no source due date means no inferred due state"

    missing = _form_catalogue("functioning_service_needs", "adult", [], TODAY)
    assert all(entry.status == "not_shown_in_briefing" and entry.record_ids == [] for entry in missing)
    assert next(entry for entry in missing if entry.form_type == "RAI").required is False
    rai = _record("rai-source", "RAI", None, record_type="arviointi", topic="toimintakyky",
                  service="toimintakyky", payload={"instrumentti": "RAI", "raha": "toimitettu"})
    [rai_entry] = [entry for entry in _form_catalogue("functioning_service_needs", "adult", [rai], TODAY)
                   if entry.form_type == "RAI"]
    assert (rai_entry.status, rai_entry.record_ids) == ("present", ["rai-source"])
    assert _form_catalogue("unsupported-purpose", "adult", [], TODAY) == []


def test_source_decision_review_date_renders_due_from_that_source_record():
    """The only due state comes from an explicit date on a selected source record."""
    persona = _social_worker_persona()
    decision = next(record for record in persona.records if record.record_id == "bound-paatos-007")
    assert decision.form is not None and decision.form.due_at is not None
    picture = _social_worker_picture()
    selected = {record.record_id: record for record in [*picture.records.priority, *picture.records.accessible_other]}
    assert decision.record_id in selected, "a due row must come from a selected source row"
    view = selected[decision.record_id]
    assert view.form is not None and view.form.due_at is not None
    assert view.form.due_at == decision.form.due_at
    [iadl] = [entry for entry in picture.form_catalogue if entry.form_type == "IADL"]
    # The row lists every selected source carrying that form; the date still comes from the decision.
    matching = sorted(record.record_id for record in persona.records
                      if (form := source.form_metadata(record)) is not None
                      and (form.form_type, form.service) == ("IADL", "toimintakyky"))
    assert (iadl.status, iadl.record_ids) == ("due", matching)
    assert decision.record_id in matching
    assert iadl.due_at is not None and iadl.due_at == decision.form.due_at, \
        "the date is read from the source, never from today"
    assert iadl.due_at.date() < TODAY
    # Item C (challenge 1.1 scope): the "kuuluuko ... tähän palvelutilanteeseen" prompt was removed
    # deliberately; the adult rows carry no suggested step, the due status and date carry the signal.
    assert iadl.suggested_next_step is None


def test_no_catalogue_entry_asks_whether_the_form_belongs_to_the_service_situation():
    from app.pipeline import _FORM_CATALOGUE
    pattern = re.compile(r"kuuluuko .* tähän palvelutilanteeseen")
    steps = [row[3] for rows in _FORM_CATALOGUE.values() for row in rows if row[3]]
    assert steps, "the guardian prompts remain"
    assert not [step for step in steps if pattern.search(step)]


def test_form_contract_carries_one_explicit_date_and_no_recurrence_rule():
    assert set(FormMetadata.model_fields) == {"form_type", "service", "version", "due_at"}
    assert set(FormCatalogueEntry.model_fields) == {
        "form_type", "service", "required", "status", "record_ids", "due_at", "suggested_next_step"}
    assert not [name for name in (*FormMetadata.model_fields, *FormCatalogueEntry.model_fields)
                if "recur" in name or "interval" in name or "toistu" in name]


def test_supplied_rai_result_is_displayed_and_never_scored():
    """ADR-0008: a supplied RAI result is shown read-only, never scored, never acted on."""
    persona = _social_worker_persona()
    rai = next(record for record in persona.records if record.record_id == "bound-arviointi-007")
    scored = assess.score(rai)
    assert scored.instrumentti == "RAI" and scored.valmis is False
    assert scored.total is None and scored.tulkinta is None and scored.osiot == []

    without = persona.model_copy(update={"records": [record for record in persona.records
                                                     if record.record_id != rai.record_id]})
    with_rai = _social_worker_picture()
    without_rai = _social_worker_picture([without if p.persona_id == persona.persona_id else p
                                          for p in load_personas()])
    [rai_row] = [item for item in with_rai.assessments if item.instrumentti == "RAI"]
    assert rai_row.valmis is False and rai_row.total is None and rai_row.osiot == []
    assert not [item for item in with_rai.assessments if item.instrumentti == "RAI" and item.total is not None]
    [rai_entry] = [entry for entry in with_rai.form_catalogue if entry.form_type == "RAI"]
    assert (rai_entry.status, rai_entry.record_ids) == ("present", [rai.record_id])
    # Displaying it changes nothing the professional acts on and produces no review proposal.
    assert (with_rai.situation, with_rai.risk, with_rai.actions, with_rai.proposals) == (
        without_rai.situation, without_rai.risk, without_rai.actions, without_rai.proposals)
    assert not [proposal for proposal in with_rai.proposals
                if rai.record_id in proposal.evidence_record_ids]
    scoped = extract_features(cut(persona).persona)
    scoped_without = extract_features(cut(without).persona)
    assert scoped["iadl_total"] == scoped_without["iadl_total"]
    assert scoped["phq9_total"] == scoped_without["phq9_total"]
    assert scoped["flags"] == scoped_without["flags"]
    assert decide(cut(persona))["risk"] == decide(cut(without))["risk"]


# ---------- the concept-key convention is ours, and it is pinned ----------
#
# `concept_key` groups the same fact recorded on two forms. Its shape drifted: the product fixture used
# `neuvola.kasvu.v1.2026-09-tarkastus` while the backend test used `bp.systolic.v1.observation-1` and a
# frontend fixture used `measurement.systolic.v1.observation-1` — three vocabularies, none authoritative,
# and nothing noticed. This pins one convention (CONTEXT.md, Käsitetunniste) and forbids the borrowed
# identifier shape that an openEHR-alignment proposal would have introduced: we have no archetypes,
# templates, OPTs or terminology bindings, so a half-conformant identifier would advertise artefacts that
# do not exist.

CONCEPT_KEY = re.compile(r"^[a-z][a-z0-9]*(\.[a-z0-9][a-z0-9-]*)+\.v\d+(\.[a-z0-9][a-z0-9-]*)?$")

ROOT = Path(__file__).resolve().parents[2]
BOUND_CORPUS = ROOT / "data" / "experimental" / "bound-personas"


def _concept_keys_from_corpus() -> list[tuple[str, str]]:
    """Every (source path, concept_key) the product actually ships."""
    found: list[tuple[str, str]] = []
    for path in sorted(BOUND_CORPUS.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        for record in payload.get("records", []):
            key = record.get("concept_key")
            if key is not None:
                found.append((path.name, key))
    return found


def test_concept_key_convention_is_pinned() -> None:
    keys = _concept_keys_from_corpus()
    assert keys, "the corpus scan found no concept_key — the check would be vacuous"

    for source_name, key in keys:
        assert CONCEPT_KEY.match(key), f"{source_name}: concept_key is off-convention: {key}"
        assert "openehr" not in key.lower() and "ehr-" not in key.lower(), (
            f"{source_name}: concept_key must not borrow a standard's identifier shape: {key}"
        )

    # The convention accepts the shape we ship and rejects the shape we declined.
    assert CONCEPT_KEY.match("neuvola.kasvu.v1.2026-09-tarkastus")
    assert CONCEPT_KEY.match("bp.systolic.v1.observation-1")
    assert not CONCEPT_KEY.match("openEHR-EHR-OBSERVATION.paino.v1")
    assert not CONCEPT_KEY.match("openEHR-EHR-OBSERVATION.paino.v1/data[at0001]")
