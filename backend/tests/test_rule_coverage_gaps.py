"""The three rule-coverage gaps the challenge's own text names, closed and pinned.

The challenge lists the professional groups, the example forms and the structured assessments Kaisu
must be able to *present*. Three of those were unmodelled: the therapist capacity, the fourth named
example form together with its service domain, and a supplied RAI result's own content. Each has a
test here that would fail if the coverage were removed rather than merely reworded.

Nothing in this file adds an instrument, a threshold or a clinical interpretation.
"""

from __future__ import annotations

import asyncio
import json
from datetime import date
from pathlib import Path

from app import assess, purpose
from app.encounters import resolve_encounter
from tests.fixtures.bound_encounters import ENCOUNTERS
from app.fixtures import load_personas
from app.main import PURPOSE_LABELS
from app.pipeline import build_picture_for_encounter
from app.policy import cut_for_encounter, roles

ROOT = Path(__file__).resolve().parents[2]
TODAY = date(2026, 10, 2)

THERAPIST = "bound-encounter-003-toimintakyky-fysioterapeutti"
VAMMAISPALVELUT = "bound-encounter-013-vammaispalvelut-sosiaalityontekija"
SOCIAL_WORKER_004 = "bound-encounter-004-sosiaalipalvelut-sosiaalityontekija"
NURSE_FUNCTIONING = "bound-encounter-003-toimintakyky-sairaanhoitaja"

# `Vammaispalvelujen palvelutarpeen arvioinnin esitiedot` is the fourth example form the brief names.
VAMMAISPALVELU_FORM = "Vammaispalvelujen palvelutarpeen arvioinnin esitiedot"
VAMMAISPALVELU_SERVICE = "vammaispalvelut"


def _picture(encounter_id: str):
    return asyncio.run(build_picture_for_encounter(
        encounter_id, personas=load_personas(), today=TODAY, decision_enabled=False, foundry_enabled=False, encounters=ENCOUNTERS
    ))


# ---------------------------------------------------------------------------------------------------
# 4a — the therapist capacity
# ---------------------------------------------------------------------------------------------------

def test_therapist_role_exists_and_takes_an_existing_purpose():
    """One role string, one purpose that already existed. No new Palvelukonteksti is invented."""
    policy = roles()
    assert "fysioterapeutti" in policy
    assert policy["fysioterapeutti"]["label"] == "Fysioterapeutti"
    encounter = ENCOUNTERS[THERAPIST]
    assert encounter.role == "fysioterapeutti"
    assert encounter.purpose == "functioning_service_needs"
    assert encounter.purpose in purpose.palvelukontekstit()
    assert "fysioterapeutti" in purpose.palvelukonteksti(encounter.purpose).roles


def test_therapist_lens_is_a_role_contrast_on_the_same_person_and_purpose():
    """The only axis that varies against the nurse's functioning lens is the professional."""
    _, person = resolve_encounter(THERAPIST, load_personas(), ENCOUNTERS)
    nurse = ENCOUNTERS[NURSE_FUNCTIONING]
    assert ENCOUNTERS[THERAPIST].persona_id == nurse.persona_id
    assert ENCOUNTERS[THERAPIST].purpose == nurse.purpose
    assert ENCOUNTERS[THERAPIST].role != nurse.role

    therapist_scope = cut_for_encounter(person, ENCOUNTERS[THERAPIST])
    nurse_scope = cut_for_encounter(person, nurse)
    # These two roles are permitted the SAME records here, and the test says so rather than implying a
    # contrast that does not exist. `policy.cut` varies by consent scope, sensitivity ceiling and excluded
    # topics, not by role name; both roles are treatment-scoped with a `sensitive` ceiling and neither
    # excludes a topic this purpose admits. What decides this set is the purpose's own topic list, which
    # admits `toimintakyky` and does not admit `mielenterveys` -- so neither lens can see the PHQ-9 or the
    # mood note. A role is a lens, not a filter that must differ from its neighbour, and the honest
    # statement is that the therapist capacity can see exactly this and no more.
    assert set(therapist_scope.ids) == set(nurse_scope.ids) == {"bound-demo-003", "bound-note-010"}
    assert "bound-arviointi-003" not in set(therapist_scope.ids)


def test_therapist_lens_renders_a_ready_briefing_without_a_special_case():
    picture = _picture(THERAPIST)
    assert picture.role == "fysioterapeutti"
    assert picture.purpose == "functioning_service_needs"
    assert picture.encounter_id == THERAPIST
    # No instrument is visible to this lens, so nothing may be scored and nothing invented from absence.
    assert picture.assessments == []
    assert picture.risk.risk_level in {"matala", "arvioimaton"}
    # It is a real lens in the fixture catalogue, with the therapist's own role: the worklist row's labels
    # are generated from the purpose and the role, so this is what the lens comparison reads.
    assert ENCOUNTERS[THERAPIST].role == "fysioterapeutti"
    assert roles()["fysioterapeutti"]["label"]


# ---------------------------------------------------------------------------------------------------
# 4b — the vammaispalvelut form and its service domain
# ---------------------------------------------------------------------------------------------------

def test_vammaispalvelut_form_and_service_domain_are_modelled():
    """The form is provenance on the Kirjaus that is its instance, and `vammaispalvelut` is its domain."""
    source_path = ROOT / "data" / "experimental" / "bound-personas" / "bound-persona-013-vammaispalvelut.json"
    persona = json.loads(source_path.read_text(encoding="utf-8"))
    carriers = [row for row in persona["records"]
                if (row.get("form") or {}).get("form_type") == VAMMAISPALVELU_FORM]
    assert len(carriers) == 1, "exactly one record is the filled form"
    form = carriers[0]["form"]
    assert form["service"] == VAMMAISPALVELU_SERVICE
    assert form["version"]
    # The service domain is a modelled source, not only a form attribute: the records name it.
    assert VAMMAISPALVELU_SERVICE in {row["source"] for row in persona["records"]}
    assert VAMMAISPALVELU_SERVICE in {row["topic"] for row in persona["records"]}
    assert VAMMAISPALVELU_SERVICE in purpose.palvelukonteksti("functioning_service_needs").topics


def test_vammaispalvelut_lens_carries_the_form_to_the_briefing():
    picture = _picture(VAMMAISPALVELUT)
    views = [*picture.records.priority, *picture.records.accessible_other]
    forms = {(view.form.form_type, view.form.service, view.form.version)
             for view in views if view.form is not None}
    assert (VAMMAISPALVELU_FORM, VAMMAISPALVELU_SERVICE, "demo-v1") in forms
    # The form is carried by a selected, permitted record with its own source person, so the briefing can
    # cite it rather than describing it.
    carrier = next(view for view in views
                   if view.form is not None and view.form.form_type == VAMMAISPALVELU_FORM)
    assert carrier.source == VAMMAISPALVELU_SERVICE
    assert carrier.record_id in set(picture.selection.baseline_record_ids)
    # The service domain reaches the form catalogue's own vocabulary as a service, and the lens is in the
    # fixture catalogue under the purpose the display label names.
    assert VAMMAISPALVELUT in ENCOUNTERS
    assert PURPOSE_LABELS[picture.purpose] == "Palvelutarpeen arviointi"


def test_vammaispalvelut_persona_carries_no_invented_identity():
    """A new lens must not become an identity oracle: the bound fixtures carry no approved name."""
    assert _picture(VAMMAISPALVELUT).identity_header == "Henkilöllisyystieto ei saatavilla"


# ---------------------------------------------------------------------------------------------------
# 4c — RAI is PRESENTED, never implemented
# ---------------------------------------------------------------------------------------------------

def test_supplied_rai_is_presented_with_its_source_version_completeness_and_answers():
    """ADR-0008 and issues/05: shown read-only, never scored, never acted on.

    The record reaches the briefing through the same `Assessment` path as every other instrument --
    instrument, version, completeness, and the record it came from. What is added here is the one fact
    about the record the row used to swallow: it carries two supplied answers.
    """
    picture = _picture(SOCIAL_WORKER_004)
    [rai] = [row for row in picture.assessments if row.instrumentti == "RAI"]
    assert rai.instrument_version == "demo-v1"
    assert rai.valmis is False
    assert rai.total is None and rai.max_total is None and rai.tulkinta is None
    assert rai.osiot == [], "no instrument specification means no item rows, invented or otherwise"
    assert rai.source_answers == 2
    assert rai.lahteet == ["bound-arviointi-007"]
    # Presenting it produces no review proposal and changes no situation/risk/action. The equivalence
    # against a persona copy without the record is asserted in
    # `test_forms.test_supplied_rai_result_is_displayed_and_never_scored`; this pins the brief's own output.
    assert not [proposal for proposal in picture.proposals
                if "bound-arviointi-007" in proposal.evidence_record_ids]


def test_the_source_answer_count_is_a_count_and_never_a_score():
    """Read from the record before any instrument lookup, and never let into `total`."""
    picture = _picture(SOCIAL_WORKER_004)
    by_instrument = {row.instrumentti: row for row in picture.assessments}
    iadl = by_instrument["IADL"]
    # The IADL record supplies six of its eight declared items. The count reports the source; the four
    # present points are a number `assess` deliberately does NOT publish as a total, and the two differ,
    # so the count cannot be mistaken for one.
    supplied_points = sum(item.pisteet for item in iadl.osiot if item.pisteet is not None)
    assert iadl.source_answers == 6
    assert supplied_points == 4
    assert iadl.source_answers != supplied_points
    assert iadl.valmis is False and iadl.total is None and iadl.tulkinta is None
    # RAI is the case the count exists for: no specification, so `osiot` is empty and the count is the
    # only thing that distinguishes a record carrying answers from one carrying nothing.
    rai = by_instrument["RAI"]
    assert rai.osiot == [] and rai.source_answers == 2


def test_a_completed_instrument_reports_its_score_and_not_a_bare_count():
    """The count never replaces a supported total: a completed PHQ-9 still renders `total / max_total`."""
    picture = _picture("bound-encounter-003-mielenterveys-sairaanhoitaja")
    [phq9] = [row for row in picture.assessments if row.instrumentti == "PHQ-9"]
    assert phq9.valmis is True
    assert phq9.total is not None and phq9.max_total == 27
    assert phq9.source_answers == 9
    # The same record through the scorer alone, so the two paths cannot disagree about the total.
    persona = next(p for p in load_personas() if p.persona_id == "bound-persona-003-mielenterveys-sosiaali")
    source_record = next(record for record in persona.records if record.record_id == "bound-arviointi-003")
    direct = assess.score(source_record)
    assert direct.total == phq9.total and direct.source_answers == phq9.source_answers
