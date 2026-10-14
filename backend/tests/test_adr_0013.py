import json

from tests.fixtures.bound_encounters import ENCOUNTERS, EVALUATION_ENCOUNTERS
from app.fixtures import load_decision_eval_personas, load_personas
from app.policy import cut


def _scoped_bytes(scoped) -> bytes:
    return json.dumps({"persona": scoped.persona.model_dump(mode="json"), "role": scoped.role,
                       "purpose": scoped.purpose, "encounter_id": scoped.encounter_id},
                      ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def test_no_case_restriction_is_byte_identical_for_every_existing_encounter():
    personas = {persona.persona_id: persona for persona in [*load_personas(), *load_decision_eval_personas()]}
    for encounter in {**ENCOUNTERS, **EVALUATION_ENCOUNTERS}.values():
        persona = personas[encounter.persona_id]
        before = cut(persona, encounter.role, encounter.purpose, encounter.encounter_id)
        after = cut(persona, encounter.role, encounter.purpose, encounter.encounter_id,
                    inherited_restricted_case_ids=frozenset())
        assert _scoped_bytes(after) == _scoped_bytes(before)


def test_case_restriction_only_removes_visible_records():
    encounter = next(iter(ENCOUNTERS.values()))
    persona = next(row for row in load_personas() if row.persona_id == encounter.persona_id)
    unrestricted = cut(persona, encounter.role, encounter.purpose, encounter.encounter_id)
    target = unrestricted.records[0]
    tagged = target.model_copy(update={"case_id": "case-demo"})
    case_persona = persona.model_copy(update={"records": [tagged if row.record_id == target.record_id else row
                                                         for row in persona.records]})
    unchanged = cut(case_persona, encounter.role, encounter.purpose, encounter.encounter_id)
    excluded = cut(case_persona, encounter.role, encounter.purpose, encounter.encounter_id,
                   inherited_restricted_case_ids=frozenset({"case-demo"}))
    assert set(excluded.ids) < set(unchanged.ids)
    assert set(excluded.ids) <= set(unchanged.ids)


def test_case_exclusion_is_byte_identical_to_source_record_absence():
    encounter = next(iter(ENCOUNTERS.values()))
    persona = next(row for row in load_personas() if row.persona_id == encounter.persona_id)
    visible = cut(persona, encounter.role, encounter.purpose, encounter.encounter_id)
    target = visible.records[0]
    tagged = target.model_copy(update={"case_id": "restricted-case"})
    case_persona = persona.model_copy(update={"records": [tagged if row.record_id == target.record_id else row
                                                         for row in persona.records]})
    absent_persona = persona.model_copy(update={"records": [row for row in persona.records
                                                            if row.record_id != target.record_id]})
    case_absent = cut(case_persona, encounter.role, encounter.purpose, encounter.encounter_id,
                      inherited_restricted_case_ids=frozenset({"restricted-case"}))
    never_existed = cut(absent_persona, encounter.role, encounter.purpose, encounter.encounter_id)
    assert _scoped_bytes(case_absent) == _scoped_bytes(never_existed)
