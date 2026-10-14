import json
from datetime import date
from pathlib import Path

import pytest

from app.encounters import Encounter, resolve_encounter
from tests.fixtures.bound_encounters import ENCOUNTERS, EVALUATION_ENCOUNTERS
from app.fixtures import load_personas
from app.picture import RecordView
from app.policy import cut, cut_for_encounter, roles
from app.selection import baseline
from app.upm import Persona

ROOT = Path(__file__).resolve().parents[2]
MAP_PATH = ROOT / "data" / "experimental" / "bound-encounter-map.json"
TODAY = date(2026, 9, 30)
BOUND_IDS = [
    "bound-encounter-002-laakitys-sairaanhoitaja",
    "bound-encounter-003-mielenterveys-sairaanhoitaja",
    "bound-encounter-003-toimintakyky-sairaanhoitaja",
    "bound-encounter-003-mielenterveys-laakari",
    "bound-encounter-003-toimintakyky-fysioterapeutti",
    "bound-encounter-004-sosiaalipalvelut-sosiaalityontekija",
    "bound-encounter-005-laakitys-sairaanhoitaja",
    "bound-encounter-012-neuvola-terveydenhoitaja",
    "bound-encounter-013-vammaispalvelut-sosiaalityontekija",
]
EVALUATOR_IDS = {
    f"bound-encounter-{person}-laakitys-sairaanhoitaja"
    for person in ("006", "007", "008", "009", "010", "011")
}


def test_bound_inventory_is_source_keyed_complete_and_globally_unique():
    personas = load_personas()
    assert {p.persona_id for p in personas} == {
        "bound-persona-002-krooninen-ikääntynyt",
        "bound-persona-003-mielenterveys-sosiaali",
        "bound-persona-004-sosiaalipalvelut",
        "bound-persona-005-omahoidon-tuki",
        "bound-persona-012-neuvola-lapsi",
        "bound-persona-013-vammaispalvelut",
    }
    records = [r for p in personas for r in p.records]
    assert len({r.record_id for r in records}) == len(records)
    for p in personas:
        assert p.source_person_id
        assert p.records
        assert all(r.source_person_id == p.source_person_id for r in p.records)
    mapping = json.loads(MAP_PATH.read_text(encoding="utf-8"))
    assert "manually ASSIGNED SYNTHETIC keys for derived demo copies ONLY" in mapping["provenance"]
    assert "never source-derived provenance or proof about real data" in mapping["provenance"]
    for entry in mapping["persona_mappings"].values():
        persona = next(p for p in personas if p.persona_id == entry["persona_id"])
        assert persona.source_person_id == entry["source_person_id"]
        assert set(entry["records"].values()) == {r.record_id for r in persona.records}
    assert {entry["encounter_id"] for entry in mapping["encounter_mappings"]} == set(BOUND_IDS)
    assert all(ENCOUNTERS[e].source_person_id for e in BOUND_IDS)


def test_curated_personas_carry_the_provenance_envelope_the_adrs_describe():
    """G5: the curated-fixture path, not only the delivery path, exercises the ADR-0012/0013/0015 envelope.

    The rule the pinned fixtures encode (keep it in mind when extending them): the five historical/phase-3
    personas must carry NO `case_id`, NO `käyntiaika` and NO `vastaanottajat`, and any addition to them must
    be an `erikoisala`/`suorituspaikka` provenance-only row (`test_phase4_fixtures.py` asserts both). The
    only bound persona without that pin is `bound-persona-013-vammaispalvelut`, which is where the full set
    lives — and `legal_basis` is absent from every record, because ADR-0015 ships no value.
    """
    personas = {persona.persona_id: persona for persona in load_personas()}
    records = [record for persona in personas.values() for record in persona.records]
    by_id = {record.record_id: record for record in records}

    headings = [record for record in records if record.heading]
    assert headings, "no curated record carries a heading (ADR-0015)"
    assert any(record.heading.local != record.heading.national for record in headings), (
        "no heading is a local/national pair")
    assert any(record.käyntiaika and record.käyntiaika != record.recorded_at for record in records), (
        "no curated record carries a käyntiaika distinct from recorded_at")
    assert any(record.vastaanottajat for record in records), "no curated record carries vastaanottajat"
    assert any(record.erikoisala and record.suorituspaikka for record in records), (
        "no curated record carries erikoisala + suorituspaikka")
    assert any(record.case_id for record in records), "no curated record carries case_id (ADR-0013)"
    assert any(record.encounter_id for record in records), (
        "no curated record carries encounter_id, so the encounter-scoped path has no data")
    assert all(record.legal_basis is None for record in records), (
        "legal_basis must ship ABSENT until the owner approves one specific basis")

    encounter, persona = resolve_encounter(
        "bound-encounter-013-vammaispalvelut-sosiaalityontekija", personas, ENCOUNTERS)
    scoped = cut_for_encounter(persona, encounter)
    assert "bound-note-023" in set(scoped.ids), (
        "the provenance row is not visible to the lens it was authored for")
    view = RecordView.from_record(by_id["bound-note-023"]).model_dump(mode="json")
    assert view["heading"] == {"local": "Hoidon tarpeen arviointi", "national": "Suunnitelma"}
    assert view["käyntiaika"] and view["käyntiaika"] != view["recorded_at"]
    assert view["vastaanottajat"] == ["Juho Nieminen", "Omaohjaaja"]
    assert view["erikoisala"] == "Vammaispalvelut"
    assert view["suorituspaikka"] == "Synteettinen vammaispalveluyksikkö 013"
    assert view["case_id"] == "bound-case-013-2026"
    assert view["encounter_id"] == "bound-encounter-013-vammaispalvelut-sosiaalityontekija"
    assert view["state"] == "approved" and view["responsible_approver"] == "sosiaalityontekija"
    assert "legal_basis" not in view


def test_bound_resolution_matches_keys_and_same_person_has_purpose_contrast():
    personas = load_personas()
    mental, same_person = resolve_encounter("bound-encounter-003-mielenterveys-sairaanhoitaja", personas, ENCOUNTERS)
    functioning, same_person_2 = resolve_encounter("bound-encounter-003-toimintakyky-sairaanhoitaja", personas, ENCOUNTERS)
    assert same_person.persona_id == same_person_2.persona_id == "bound-persona-003-mielenterveys-sosiaali"
    assert mental.source_person_id == functioning.source_person_id == same_person.source_person_id
    assert mental.purpose != functioning.purpose
    for encounter_id in BOUND_IDS:
        encounter, persona = resolve_encounter(encounter_id, personas, ENCOUNTERS)
        assert encounter.source_person_id == persona.source_person_id
        assert all(r.source_person_id == encounter.source_person_id for r in persona.records)


def test_product_and_evaluator_inventory_and_pair_lenses_are_locked():
    assert set(ENCOUNTERS) == set(BOUND_IDS)
    assert set(EVALUATION_ENCOUNTERS) == EVALUATOR_IDS

    mental_nurse = ENCOUNTERS["bound-encounter-003-mielenterveys-sairaanhoitaja"]
    functioning_nurse = ENCOUNTERS["bound-encounter-003-toimintakyky-sairaanhoitaja"]
    mental_doctor = ENCOUNTERS["bound-encounter-003-mielenterveys-laakari"]
    functioning_social_worker = ENCOUNTERS["bound-encounter-004-sosiaalipalvelut-sosiaalityontekija"]

    # Situation contrast: same person and professional; purpose alone varies.
    assert mental_nurse.persona_id == functioning_nurse.persona_id
    assert mental_nurse.role == functioning_nurse.role == "sairaanhoitaja"
    assert mental_nurse.purpose == "mental_health_assessment"
    assert functioning_nurse.purpose == "functioning_service_needs"

    # Role contrast: same person and purpose; professional alone varies.
    assert mental_nurse.persona_id == mental_doctor.persona_id
    assert mental_nurse.purpose == mental_doctor.purpose == "mental_health_assessment"
    assert mental_nurse.role == "sairaanhoitaja" and mental_doctor.role == "laakari"

    # The old pair is still a person+role confound, not a controlled role contrast.
    assert functioning_nurse.purpose == functioning_social_worker.purpose
    assert functioning_nurse.persona_id != functioning_social_worker.persona_id
    assert functioning_nurse.role != functioning_social_worker.role


def test_doctor_and_nurse_baselines_differ_only_by_doctor_policy_access():
    personas = load_personas()
    nurse_case, nurse_person = resolve_encounter("bound-encounter-003-mielenterveys-sairaanhoitaja", personas, ENCOUNTERS)
    doctor_case, doctor_person = resolve_encounter("bound-encounter-003-mielenterveys-laakari", personas, ENCOUNTERS)
    assert nurse_case.persona_id == doctor_case.persona_id == nurse_person.persona_id == doctor_person.persona_id
    assert nurse_case.purpose == doctor_case.purpose == "mental_health_assessment"
    assert nurse_case.role == "sairaanhoitaja" and doctor_case.role == "laakari"

    nurse_scope = cut(nurse_person, nurse_case.role, nurse_case.purpose)
    doctor_scope = cut(doctor_person, doctor_case.role, doctor_case.purpose)
    phase4_003_ids = {"bound-note-003-alignment", "bound-note-003-draft"}
    original_mental_ids = {
        "bound-demo-003", "bound-note-003", "bound-dx-004", "bound-med-004", "bound-arviointi-003",
    }
    assert set(nurse_scope.ids) == original_mental_ids | phase4_003_ids
    assert set(doctor_scope.ids) - set(nurse_scope.ids) == {"bound-note-019"}
    assert set(nurse_scope.ids) - set(doctor_scope.ids) == set()

    [doctor_only] = [record for record in doctor_scope.records if record.record_id == "bound-note-019"]
    assert (doctor_only.topic, doctor_only.consent_scope, doctor_only.sensitivity_tag) == (
        "mielenterveys", "social_care", "highly_sensitive"
    )
    policy = roles()
    assert "social_care" not in policy["sairaanhoitaja"]["consent_scopes"]
    assert policy["sairaanhoitaja"]["max_sensitivity"] == "sensitive"
    assert "social_care" in policy["laakari"]["consent_scopes"]
    assert policy["laakari"]["max_sensitivity"] == "highly_sensitive"

    nurse_baseline, _ = baseline(nurse_scope, TODAY)
    doctor_baseline, _ = baseline(doctor_scope, TODAY)
    assert {record.record_id for record in nurse_baseline} == original_mental_ids | phase4_003_ids
    assert {record.record_id for record in doctor_baseline} == original_mental_ids | phase4_003_ids | {"bound-note-019"}


def _bound_003():
    return next(p for p in load_personas() if p.persona_id == "bound-persona-003-mielenterveys-sosiaali")


def _assert_rejected(persona: Persona):
    with pytest.raises(ValueError):
        resolve_encounter("bound-encounter-003-mielenterveys-sairaanhoitaja", [persona], ENCOUNTERS)


def test_actual_fault_records_reject_swapped_and_missing_owner_keys():
    persona = _bound_003()
    no_person_key = persona.model_copy(update={"source_person_id": None})
    _assert_rejected(no_person_key)
    swapped = persona.model_copy(deep=True)
    swapped.records[1].source_person_id = "bound-source-004"
    _assert_rejected(swapped)
    missing = persona.model_copy(deep=True)
    missing.records[1].source_person_id = None
    _assert_rejected(missing)


def test_actual_denied_other_person_record_is_rejected_before_policy_cut():
    persona = _bound_003().model_copy(deep=True)
    foreign_denied = persona.records[1].model_copy(update={
        "record_id": "fault-denied-foreign-record",
        "source_person_id": "bound-source-004",
        "consent_scope": "social_care",
    })
    persona.records.append(foreign_denied)
    _assert_rejected(persona)


def test_actual_duplicate_record_id_is_rejected_within_and_across_people():
    persona = _bound_003().model_copy(deep=True)
    duplicate = persona.records[1].model_copy(update={"record_id": persona.records[0].record_id})
    persona.records.append(duplicate)
    _assert_rejected(persona)

    other_record = persona.records[0].model_copy(update={"source_person_id": "source-other"})
    other = Persona(persona_id="fault-other-person", label="synthetic other",
                    source_person_id="source-other", records=[other_record])
    with pytest.raises(ValueError, match="duplicate record ID"):
        resolve_encounter("bound-encounter-003-mielenterveys-sairaanhoitaja", [persona, other], ENCOUNTERS)


def test_source_person_key_cannot_map_to_distinct_people_and_basics_must_be_unique():
    target = _bound_003()
    same_owner_record = target.records[1].model_copy(update={"record_id": "different-record"})
    same_owner = Persona(persona_id="fault-second-copy", label="synthetic duplicate owner",
                         source_person_id=target.source_person_id, records=[same_owner_record])
    with pytest.raises(ValueError, match="multiple people"):
        resolve_encounter("bound-encounter-003-mielenterveys-sairaanhoitaja", [target, same_owner], ENCOUNTERS)

    basics = next(record for record in target.records if record.record_type == "perustieto")
    ambiguous = target.model_copy(update={"records": [*target.records,
        basics.model_copy(update={"record_id": "bound-duplicate-basics"})]})
    with pytest.raises(ValueError, match="ambiguous basics"):
        resolve_encounter("bound-encounter-003-mielenterveys-sairaanhoitaja", [ambiguous], ENCOUNTERS)


def test_resolver_accepts_an_operator_supplied_encounter_mapping():
    persona = _bound_003()
    encounter = Encounter("operator-encounter", persona.persona_id, "sairaanhoitaja",
                          "mental_health_assessment", persona.source_person_id)
    resolved, bound_persona = resolve_encounter(
        "operator-encounter", {persona.persona_id: persona}, {"operator-encounter": encounter})
    assert resolved == encounter
    assert bound_persona.persona_id == persona.persona_id


def test_near_duplicate_name_does_not_join_other_source_person():
    personas = load_personas()
    target = next(p for p in personas if p.persona_id == "bound-persona-003-mielenterveys-sosiaali")
    same_name_other_owner = target.records[0].model_copy(update={
        "record_id": "fault-near-duplicate-name-other-person",
        "source_person_id": "different-source-person",
    })
    other = Persona(
        persona_id="fault-near-duplicate-person",
        label="synthetic same-name different owner",
        source_person_id="different-source-person",
        records=[same_name_other_owner],
    )
    encounter, resolved = resolve_encounter("bound-encounter-003-mielenterveys-sairaanhoitaja", [target, other], ENCOUNTERS)
    assert encounter.source_person_id != other.source_person_id
    assert resolved.persona_id == target.persona_id
    # the near-duplicate carries the same basics payload but never binds to the encounter person
    assert same_name_other_owner.payload == target.records[0].payload
