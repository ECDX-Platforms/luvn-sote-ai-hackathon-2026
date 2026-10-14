"""Migration equivalence to c41edcb over a fixed corpus, with Phase 4 growth declared explicitly.

A whole-set equality proof cannot coexist with authorized corpus growth. Its assertion form therefore
preserves the intent: no baseline record disappears or changes holder, and every new visible ID is
exactly allowlisted below. This changes the assertion's FORM, not the no-silent-addition rule.
"""

import ast
import hashlib
import json
from pathlib import Path

from tests.fixtures.bound_encounters import ENCOUNTERS, EVALUATION_ENCOUNTERS
from app.fixtures import load_decision_eval_personas, load_personas
from app.policy import cut, cut_for_encounter
from app.upm import Kypsyysarvio

BASELINE_PATH = Path(__file__).parent / "fixtures" / "adr0012_visibility_baseline.json"
PRE_CHANGE_SHA256 = "13469f54d71d89dd55e3eefe7818f7ed66a3cd390c8a42c777dcf56dc54bc81b"
AUTHORIZED_FIXTURE_ADDITIONS = {
    "bound-encounter-002-laakitys-sairaanhoitaja": {"bound-note-002-alignment", "bound-note-002-draft"},
    "bound-encounter-003-mielenterveys-sairaanhoitaja": {
        "bound-note-003-alignment", "bound-note-003-draft",
    },
    "bound-encounter-003-mielenterveys-laakari": {"bound-note-003-alignment", "bound-note-003-draft"},
    "bound-encounter-004-sosiaalipalvelut-sosiaalityontekija": {
        "bound-note-004-alignment", "bound-note-004-draft",
    },
    "bound-encounter-005-laakitys-sairaanhoitaja": {"bound-lab-005-alignment"},
}
# The frozen baseline predates these lenses, so their permitted sets are declared here rather than in it:
# the baseline file's SHA-256 is pinned above, and rewriting it would retire the proof it exists to give.
# Each set is what `cut_for_encounter` returns today, stated so an undeclared widening of either lens
# fails here instead of passing as "not in the baseline".
UNBASELINED_AUTHORED_RECORDS = {
    "bound-encounter-003-toimintakyky-fysioterapeutti": {"bound-demo-003", "bound-note-010"},
    "bound-encounter-013-vammaispalvelut-sosiaalityontekija": {
        "bound-demo-013", "bound-esitieto-013", "bound-note-022", "bound-note-023",
    },
}


def test_policy_cut_preserves_frozen_records_and_declares_every_addition():
    assert hashlib.sha256(BASELINE_PATH.read_bytes()).hexdigest() == PRE_CHANGE_SHA256
    baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    encounters = {**ENCOUNTERS, **EVALUATION_ENCOUNTERS}
    personas = {persona.persona_id: persona for persona in [*load_personas(), *load_decision_eval_personas()]}
    assert set(encounters) == set(baseline) | set(UNBASELINED_AUTHORED_RECORDS)

    for encounter_id, encounter in encounters.items():
        scoped = cut_for_encounter(personas[encounter.persona_id], encounter)
        current_ids = {record.record_id for record in scoped.records}
        is_evaluator = encounter_id in EVALUATION_ENCOUNTERS
        projected_holders = {
            record.record_id: record.projected_consent_holder(None, encounter.encounter_id)
            for record in scoped.records
        }

        if encounter_id not in baseline:
            assert current_ids == UNBASELINED_AUTHORED_RECORDS[encounter_id]
            continue

        baseline_ids = set(baseline[encounter_id]["permitted_record_ids"])
        assert baseline_ids <= current_ids, f"pre-existing records disappeared in {encounter_id}"
        prior_holders = {record_id: projected_holders[record_id] for record_id in baseline_ids}
        assert prior_holders == baseline[encounter_id]["consent_holder_by_record"], (
            f"pre-existing consent_holder changed in {encounter_id}"
        )
        if is_evaluator:
            assert sorted(current_ids) == baseline[encounter_id]["permitted_record_ids"], (
                f"evaluator permitted IDs changed in {encounter_id}"
            )
            assert prior_holders == baseline[encounter_id]["consent_holder_by_record"]
        else:
            declared = AUTHORIZED_FIXTURE_ADDITIONS.get(encounter_id, set())
            assert current_ids - baseline_ids == declared, (
                f"undeclared additions in {encounter_id}: {sorted(current_ids - baseline_ids)}"
            )


def test_no_app_or_script_module_constructs_a_cut_outside_the_encounter_seam():
    """Scripts are production evidence producers too; only policy owns raw cut construction."""
    backend = Path(__file__).resolve().parents[1]
    roots = (backend / "app", backend / "scripts")
    violations = []
    for root in roots:
        for path in root.rglob("*.py"):
            if path == backend / "app" / "policy.py":
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            policy_imports = [node for node in ast.walk(tree)
                              if isinstance(node, ast.ImportFrom) and node.module and node.module.endswith("policy")]
            scoped_names = {alias.asname or alias.name for node in policy_imports for alias in node.names
                            if alias.name == "Scoped"}
            for node in policy_imports:
                if any(alias.name == "cut" for alias in node.names):
                    violations.append(f"{path.relative_to(backend)}:{node.lineno} imports cut")
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    name = node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", None)
                    if name in {"cut", "Scoped"} | scoped_names:
                        violations.append(f"{path.relative_to(backend)}:{node.lineno} constructs/calls {name}")
    assert not violations, "direct policy cut/Scoped bypasses encounter seam: " + ", ".join(violations)


def test_encounter_assessment_is_optional_scoped_and_uses_no_unapproved_legal_basis():
    encounter = ENCOUNTERS["bound-encounter-012-neuvola-terveydenhoitaja"]
    persona = next(row for row in load_personas() if row.persona_id == encounter.persona_id)
    legacy = cut(persona, encounter.role, encounter.purpose, encounter.encounter_id,
                 encounter.consent_holders)
    self_row = next(row for row in persona.records if row.consent_holder == "self")
    guardian_row = next(row for row in persona.records if row.consent_holder == "guardian")
    scoped_rows = [row.model_copy(update={"encounter_id": encounter.encounter_id})
                   if row.record_id in {self_row.record_id, guardian_row.record_id} else row
                   for row in persona.records]
    scoped_persona = persona.model_copy(update={"records": scoped_rows})
    assessed = Kypsyysarvio(value="ei_paatoskykyinen_tiedot_huoltajalle", assessed_by_role="terveydenhoitaja",
                            assessed_at=self_row.recorded_at)
    projected = cut(scoped_persona, encounter.role, encounter.purpose, encounter.encounter_id,
                    encounter.consent_holders, kypsyysarvio=assessed)
    assert assessed.legal_basis is None
    assert self_row.record_id in projected.ids
    assert guardian_row.record_id in projected.ids
    assert self_row.record_id not in legacy.ids
