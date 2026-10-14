"""The optional actions module, and the per-record provenance of the risk verdict.

Two boundaries are pinned here, and both are the kind that rot silently if nothing checks them:

1. **The core proposes nothing.** `decide` has no `actions` key and traces only its own two
   tables. Suggested next steps come from `app.actions`, which reads the core's finished
   output — two scalars — and feeds nothing back. `LUVN_ACTIONS_MODULE=0` runs the core alone
   and produces a complete briefing with no suggestions.
2. **A risk reason travels with the record it came from.** `risk_reasons_by_record` restates
   the rule that fired, keyed by the permitted record behind it. It must never invent a record:
   a rule with no single-record provenance (an absence, a count, an assembled score) adds
   nothing, because filling that absence is the failure the whole surface exists to avoid.
"""

from __future__ import annotations

import asyncio
import inspect

import pytest

from app import actions
from app.decide import decide
from app.pipeline import build_picture_for_encounter
from app.policy import cut
from app.fixtures import load_personas
from tests.fixtures.bound_encounters import ENCOUNTERS

ENCOUNTER = "bound-encounter-002-laakitys-sairaanhoitaja"
PERSONAS = {p.persona_id: p for p in load_personas()}


def test_core_decision_has_no_actions_key():
    decision = decide(cut(PERSONAS["bound-persona-002-krooninen-ikääntynyt"]))
    assert "actions" not in decision
    assert [row["table"] for row in decision["trace"]] == ["situation", "risk"]


def test_actions_module_reads_only_the_cores_two_outputs():
    """The module takes scalars, so it cannot leak a record it was never given."""
    acts, trace = actions.suggest_actions("mielenterveys", "korkea")
    assert trace["rule_id"] == "act-mielenterveys-korkea"
    assert acts and all(isinstance(a, str) for a in acts)
    # The signature is the boundary: three core scalars, and no record, persona, features or Scoped argument.
    assert list(inspect.signature(actions.suggest_actions).parameters) == \
        ["situation_type", "risk_level", "risk_rule_id"]


def test_a_risk_rule_id_keys_the_four_blood_pressure_steps_in_the_tables_order():
    acts, trace = actions.suggest_actions("krooninen", "korkea", "risk-verenpaine-korkea")
    assert trace["rule_id"] == "act-verenpaine-korkea", "the risk-keyed row outranks the situation-keyed one"
    assert acts == [
        "Toista verenpainemittaus ja kirjaa tulos molemmista käsivarsista",
        "Käy läpi nykyinen verenpainelääkitys ja sen toteutuminen",
        "Sovi kontrollimittaus 1–3 kuukauden päähän",
        "Käy läpi elintapamuutokset: suola, liikunta, painonhallinta",
    ]


def test_the_fallback_still_fires_when_no_rule_matches_and_the_version_is_v2():
    acts, trace = actions.suggest_actions("akuutti", "matala", "risk-yleinen-matala")
    assert trace["rule_id"] == "act-yleinen-fallback"
    assert acts == ["Jatka tilanteen seurantaa"]
    assert actions.MODULE_VERSION == "luvn-actions-module-v2"


def test_actions_module_is_off_when_the_flag_says_so(monkeypatch):
    monkeypatch.setenv("LUVN_ACTIONS_MODULE", "0")
    assert actions.enabled() is False


def test_module_off_produces_a_complete_briefing_with_no_suggestions(monkeypatch):
    """Switching the module off must change nothing else about the picture."""
    with_module = asyncio.run(build_picture_for_encounter(ENCOUNTER, encounters=ENCOUNTERS))
    monkeypatch.setenv("LUVN_ACTIONS_MODULE", "0")
    without_module = asyncio.run(build_picture_for_encounter(ENCOUNTER, encounters=ENCOUNTERS))

    assert with_module.actions, "the module is on by default and supplies the suggestions"
    assert without_module.actions == [], "module off means no suggestions, not an error"
    assert [row.table for row in without_module.trace] == ["situation", "risk"]
    assert [row.table for row in with_module.trace][:2] == ["situation", "risk"]
    assert "actions" in [row.table for row in with_module.trace]

    # The core's own outputs are untouched by the module's presence.
    assert without_module.situation == with_module.situation
    assert without_module.risk == with_module.risk
    assert without_module.risk_reasons_by_record == with_module.risk_reasons_by_record
    assert {r.record_id for r in without_module.records.priority} == \
           {r.record_id for r in with_module.records.priority}


def test_risk_reasons_name_only_records_the_viewer_may_see():
    picture = asyncio.run(build_picture_for_encounter(ENCOUNTER, encounters=ENCOUNTERS))
    visible = {row.record_id for row in picture.records.priority} | \
              {row.record_id for row in picture.records.accessible_other}
    permitted = {row.record_id for row in
                 cut(PERSONAS["bound-persona-002-krooninen-ikääntynyt"], role="laakari").records}
    for record_id in picture.risk_reasons_by_record:
        assert record_id in permitted, f"{record_id} is not in the permitted set"
    # Reasons may name a record outside the *selected* tier (the conflict note can be
    # de-prioritised), but never outside the permitted set -- checked above. The values are
    # always non-empty restatements of the rule that fired.
    assert all(reasons for reasons in picture.risk_reasons_by_record.values())


def test_absence_risk_rule_fills_no_record():
    """`risk-arvioimaton` is an absence. It must not be attributed to any record."""
    from app.upm import Persona

    empty = Persona(persona_id="synthetic-absent", label="Synthetic", records=[])
    decision = decide(cut(empty, role="laakari"))
    assert decision["risk"]["risk_level"] == "arvioimaton"
    assert decision["risk_reasons_by_record"] == {}, \
        "an absence has no record behind it, so it must be given none"


def test_conflict_rule_attributes_both_ends_of_the_conflict():
    decision = decide(cut(PERSONAS["bound-persona-002-krooninen-ikääntynyt"]))
    assert decision["risk"]["risk_level"] == "ristiriita"
    reasons = decision["risk_reasons_by_record"]
    assert reasons, "the conflict rule has single-record provenance and must name it"
    conflict = decision["conflicts"][0]
    assert conflict["medication_record_id"] in reasons
    assert conflict["note_record_id"] in reasons
