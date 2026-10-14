"""Decision-layer checks: the tables fire on the right personas, roles differ."""

from datetime import datetime, timezone

import pytest

from app.actions import suggest_actions
from app.decide import decide, evaluate_table
from app.fixtures import load_personas
from app.policy import cut, table, visible_records
from app.upm import Persona, Record

PERSONAS = {p.persona_id: p for p in load_personas()}


def _note(record_id: str, text: str, topic: str = "hoito") -> Record:
    return Record(
        record_id=record_id,
        source="synthetic",
        record_type="vapaateksti",
        authored_by_role="lääkäri",
        recorded_at=datetime(2026, 9, 20, 8, tzinfo=timezone.utc),
        topic=topic,
        sensitivity_tag="none",
        consent_scope="treatment",
        payload={"otsikko": "Vastaanotto", "teksti": text},
    )


def test_acute_note_is_medium_not_high():
    acute = Persona(persona_id="synthetic-acute", label="synthetic", records=[
        _note("acute-note", "Kolmen päivän kuume ja kuiva yskä. Potilas neuvottu hakeutumaan "
                            "päivystykseen, jos hengitysvaikeus pahenee.", topic="hoito"),
    ])
    d = decide(cut(acute))
    assert d["situation"]["situation_type"] == "akuutti"
    assert d["risk"]["risk_level"] == "kohtalainen"
    assert "actions" not in d, "the core proposes nothing; actions live in the optional module"
    assert suggest_actions("akuutti", "kohtalainen")[0]


def test_conflict_persona_is_high_and_action_drives_review():
    d = decide(cut(PERSONAS["bound-persona-002-krooninen-ikääntynyt"]))
    assert d["situation"]["situation_type"] == "krooninen"
    # A source conflict is its own risk_level, not "korkea": the rule restates a
    # disagreement between sources, not a severity judgement, so it must render with
    # neither the critical-severity word nor the critical-severity styling (see
    # ChallengeDemo.jsx RISK_WORD / riskTone, which `ristiriita` is deliberately absent from).
    assert d["risk"]["risk_level"] == "ristiriita"
    assert d["conflicts"], "the hardcoded conflicts link must fire"
    assert any("lääk" in a.lower() for a in suggest_actions("krooninen", "ristiriita")[0])


def test_mental_health_persona():
    d = decide(cut(PERSONAS["bound-persona-003-mielenterveys-sosiaali"]))
    assert d["situation"]["situation_type"] == "mielenterveys"
    # completed PHQ-9 = 16 drives risk to korkea (illustrative demo policy)
    assert d["risk"]["risk_level"] == "korkea"


def test_every_decision_traces_the_two_core_tables():
    """The core traces exactly situation and risk. `actions` is deliberately absent: it is an
    optional downstream module and must not appear in the core's own decision record."""
    for persona in PERSONAS.values():
        d = decide(cut(persona))
        assert [t["table"] for t in d["trace"]] == ["situation", "risk"]
        assert all(t["rule_id"] for t in d["trace"])


def test_elder_care_role_view_is_ikaantyminen():
    d = decide(cut(PERSONAS["bound-persona-002-krooninen-ikääntynyt"], "sosiaalityontekija"))
    assert d["situation"]["situation_type"] == "ikääntyminen"
    assert d["risk"]["risk_level"] == "matala"


def test_role_switch_changes_the_governed_picture():
    p = PERSONAS["bound-persona-003-mielenterveys-sosiaali"]
    laakari = {r.record_id for r in visible_records(p, "laakari")}
    sosiaali = {r.record_id for r in visible_records(p, "sosiaalityontekija")}
    assert "bound-note-003" in laakari and "bound-note-004" in laakari
    assert "bound-note-004" in sosiaali
    assert "bound-note-003" not in sosiaali, "treatment-scope note must not reach social care"
    assert laakari != sosiaali


def test_policy_cut_gates_the_decision_not_just_the_id_list():
    """Policy before rank: a role that cannot see the records gets no clinical decision."""
    p = PERSONAS["bound-persona-004-sosiaalipalvelut"]  # every record is social_care
    sosiaali = decide(cut(p, "sosiaalityontekija"))
    sairaanhoitaja = decide(cut(p, "sairaanhoitaja"))
    assert sosiaali["situation"]["situation_type"] == "sosiaalipalvelut"
    assert sosiaali["risk"]["risk_level"] == "matala"
    assert sairaanhoitaja["visible_record_ids"] == []
    assert sairaanhoitaja["features"]["visible_record_count"] == 0
    assert sairaanhoitaja["situation"]["situation_type"] == "ei_arvioitavissa"
    assert sairaanhoitaja["risk"]["risk_level"] == "arvioimaton"
    # ADR-0003's wording pin, in its other case. The `matala` level half was pinned; the wording half was
    # not, so this is the pin that record claims. It compares against the catch-all rule's own reason rather
    # than a literal, so rewording or recasing the table cannot silently retire the guard.
    low_risk_reason = next(rule["output"]["reason"] for rule in table("risk")["rules"]
                           if rule["rule_id"] == "risk-yleinen-matala")
    assert low_risk_reason not in sairaanhoitaja["risk"]["reason"]
    assert sairaanhoitaja["features"]["max_temp_c"] is None  # no hidden vital leak


def test_policy_cut_enforces_sensitivity_and_topic_not_just_consent():
    """Sensitivity and excluded topics are load-bearing policy dimensions; pin them
    so deleting a predicate cannot stay green."""
    p003 = PERSONAS["bound-persona-003-mielenterveys-sosiaali"]
    potilas = {r.record_id for r in visible_records(p003, "potilas")}
    assert "bound-note-003" in potilas
    assert "bound-note-004" not in potilas, "highly_sensitive must not reach potilas (max sensitive)"

    p004 = PERSONAS["bound-persona-004-sosiaalipalvelut"]
    assert "bound-note-005" not in {r.record_id for r in visible_records(p004, "potilas")}

    # sairaanhoitaja excludes the toimeentulo topic by policy
    assert all(r.topic != "toimeentulo" for r in visible_records(p003, "sairaanhoitaja"))


def test_policy_cut_excluded_records_stay_out_of_the_permitted_set():
    p = PERSONAS["bound-persona-002-krooninen-ikääntynyt"]
    scoped = cut(p, "sosiaalityontekija")
    assert set(scoped.ids) == {"bound-asiointi-002"}
    assert {"bound-med-002", "bound-note-002"}.isdisjoint(scoped.ids)
    assert {"bound-note-002-alignment", "bound-note-002-draft"}.isdisjoint(scoped.ids), (
        "the Phase 4 treatment-only synthetic additions do not enter a social-care cut"
    )


def test_unknown_role_is_a_named_error_not_a_keyerror():
    with pytest.raises(ValueError, match="unknown role"):
        cut(PERSONAS["bound-persona-002-krooninen-ikääntynyt"], "nobody")


def test_stop_statement_is_bound_to_its_own_clause():
    from app.triage import _clause, _has_stop_statement

    # The stop word sits in the second clause with amlodipiini; simvastatiini's
    # clause must not inherit it (that was the false "korkea" risk).
    text = "simvastatiini aloitettiin, amlodipiini lopettanut."
    assert not _has_stop_statement(_clause(text, text.find("simvasta")))
    assert _has_stop_statement(_clause(text, text.find("amlodipi")))


def test_negation_tolerates_words_between_ei_and_the_verb():
    from app.triage import _has_stop_statement

    assert not _has_stop_statement("asiakas ei ole vielä lopettanut lääkitystä")
    assert _has_stop_statement("asiakas kertoo lopettaneensa lääkkeen")


def test_negated_discontinuation_does_not_create_conflict():
    from app.triage import _has_stop_statement

    assert _has_stop_statement("asiakas kertoo lopettaneensa lääkkeen")
    assert not _has_stop_statement("asiakas ei ole lopettanut lääkitystä")


def test_evaluator_first_match_wins_and_default_fires():
    table = {
        "table": "t",
        "rules": [
            {"rule_id": "hi", "priority": 10, "when": [{"field": "x", "op": ">=", "value": 3}], "output": {"v": "hi"}},
            {"rule_id": "lo", "priority": 0, "when": [], "output": {"v": "lo"}},
        ],
    }
    assert evaluate_table(table, {"x": 5})[0]["rule_id"] == "hi"
    assert evaluate_table(table, {"x": None})[0]["rule_id"] == "lo"


def test_unknown_op_fails_loud():
    table = {"table": "t", "rules": [{"rule_id": "bad", "priority": 1, "when": [{"field": "x", "op": "~=", "value": 1}], "output": {}}]}
    with pytest.raises(ValueError, match="unknown op"):
        evaluate_table(table, {"x": 1})


# ---------------------------------------------------------------------------------------------------
# Gate 1 — an uncalibrated cut-off has to be marked where it is read.
#
# `risk.json` ships numeric cut-offs that render as clinical findings, and
# `docs/architecture-as-built.md` already states they are not calibrated on real Finnish data. That
# admission lived in the table's `_meta` string and in the audit document, which is not where the
# professional reads it: the rendered sentence is. So each rule that applies a cut-off marks its own
# output, and the briefing renders the qualification next to that sentence and nowhere else.
#
# The sweep is derived from the table so a cut-off rule added later cannot render as a bare clinical
# finding and still pass. The boundary is mechanical and honest: a comparison against a value **greater
# than zero** is a threshold on a measured reading, while a comparison against zero is an emptiness test
# (`visible_record_count <= 0` reports that nothing could be assessed — that is absence, not a cut-off),
# and `non_empty` / `is_true` restate a source fact rather than applying a number.
# ---------------------------------------------------------------------------------------------------

CUTOFF_OPS = {">=", "<=", ">", "<"}
THRESHOLD_BASIS = "illustrative_threshold"


def _positive_cutoff_predicates(rule: dict) -> list[dict]:
    """Predicates that compare a measured value against a number greater than zero."""
    return [pred for pred in rule.get("when", [])
            if pred["op"] in CUTOFF_OPS
            and isinstance(pred.get("value"), (int, float))
            and pred["value"] > 0]


def _cutoff_rules() -> list[dict]:
    return [rule for rule in table("risk")["rules"] if _positive_cutoff_predicates(rule)]


def test_every_risk_cutoff_rule_marks_its_own_output_illustrative():
    rules = _cutoff_rules()
    # Anti-vacuity: a sweep that found nothing would pass every assertion below by reading nothing.
    assert len(rules) >= 6, f"the cut-off sweep found {len(rules)} rules — it would pass vacuously"
    assert {rule["rule_id"] for rule in rules} == {
        "risk-phq9-vaikea", "risk-phq9-keskivaikea",
        "risk-iadl-merkittava", "risk-iadl-kohtalainen",
        "risk-akuutti-vitaalit", "risk-happikyllasteisyys",
        "risk-hoitamattomuus", "risk-puuttuneet-annokset", "risk-verenpaine-korkea",
    }, "a cut-off rule was added, removed or renamed without its marking being reconsidered"
    for rule in rules:
        assert rule["output"].get("basis") == THRESHOLD_BASIS, (
            f"{rule['rule_id']} applies a numeric cut-off but does not mark its output: the rendered "
            "sentence would read as a calibrated clinical finding")


def test_one_marked_rule_per_cutoff_family_and_every_family_is_covered():
    """Every family of cut-off the briefing can render has at least one marked rule."""
    families: dict[str, list[str]] = {}
    for rule in _cutoff_rules():
        for pred in _positive_cutoff_predicates(rule):
            families.setdefault(pred["field"], []).append(rule["rule_id"])
    assert set(families) == {"phq9_total", "iadl_total", "max_temp_c", "min_spo2", "missed_doses_total",
                             "systolic_max", "diastolic_max"}, (
        f"the cut-off families changed: {sorted(families)} — each one needs a marked rule and a "
        "rendered qualification")
    for field, rule_ids in families.items():
        assert rule_ids, f"family {field} has no marked rule"
        assert all(next(r for r in table("risk")["rules"] if r["rule_id"] == rule_id)["output"].get("basis")
                   == THRESHOLD_BASIS for rule_id in rule_ids), f"family {field} has an unmarked rule"


def test_absence_and_source_fact_rules_carry_no_threshold_caveat():
    """The qualification sits on the claim it is about. Absence and a restated source fact are not
    threshold claims, and marking them would dilute the caveat into boilerplate on every row."""
    by_id = {rule["rule_id"]: rule for rule in table("risk")["rules"]}
    for rule_id in ("risk-arvioimaton", "risk-ei-ajantasaista", "risk-laakitysristiriita",
                    "risk-akuutti", "risk-yleinen-matala"):
        assert "basis" not in by_id[rule_id]["output"], f"{rule_id} is not a threshold claim"
    # The boundary probe: these two compare against zero, so they are emptiness tests, not cut-offs.
    assert not _positive_cutoff_predicates(by_id["risk-arvioimaton"])
    assert not _positive_cutoff_predicates(by_id["risk-ei-ajantasaista"])


def test_a_threshold_reason_reaches_the_decision_with_its_marking():
    """Anti-vacuity for the whole chain: marking the table is not the same as the marking arriving."""
    persona = PERSONAS["bound-persona-003-mielenterveys-sosiaali"]
    risk = decide(cut(persona, "laakari"))["risk"]
    assert risk["reason"] == "PHQ-9-kokonaispistemäärä 15 tai yli (havainnollistava raja)"
    assert risk["basis"] == THRESHOLD_BASIS


def test_a_source_fact_reason_reaches_the_decision_without_a_marking():
    """The medication conflict alert states the disagreement, not a side of it."""
    persona = PERSONAS["bound-persona-002-krooninen-ikääntynyt"]
    risk = decide(cut(persona, "laakari"))["risk"]
    # The alert text must state the conflict (source disagreement), not assert medication is active/stopped
    assert risk["reason"] == "Lähteiden ristiriita lääkityksessä — tarkista, kumpi lähde on ajantasainen"
    assert risk.get("basis") is None


def test_medication_conflict_alert_names_disagreement_not_a_side():
    """The conflict alert must not assert 'active' or 'stopped', only that sources disagree."""
    persona = PERSONAS["bound-persona-002-krooninen-ikääntynyt"]
    decision = decide(cut(persona, "laakari"))
    risk_reason = decision["risk"]["reason"]

    # The reason must name the disagreement (Lähteiden ristiriita)
    assert "Lähteiden ristiriita" in risk_reason, "reason must name the source conflict"

    # The reason must NOT assert medication state on the risk row itself
    # (state is asserted in the conflict detail row via the frontend's rendering)
    assert "Aktiivinen" not in risk_reason, "reason must not assert medication is active"
    assert "lopettanut" not in risk_reason, "reason must not assert medication is stopped"

    # The conflicts array still contains both sources (for frontend rendering)
    assert len(decision["conflicts"]) > 0, "conflicts array must be present"
    conflict = decision["conflicts"][0]
    assert "medication_record_id" in conflict
    assert "note_record_id" in conflict
    assert "drug" in conflict


def _measurement(record_id: str, payload: dict) -> Record:
    return Record(
        record_id=record_id,
        source="synthetic",
        record_type="mittaus",
        authored_by_role="sairaanhoitaja",
        recorded_at=datetime(2026, 9, 20, 8, tzinfo=timezone.utc),
        topic="verenpaine",
        sensitivity_tag="none",
        consent_scope="treatment",
        payload=payload,
    )


def _risk_entry(decision: dict) -> dict:
    return next(row for row in decision["trace"] if row["table"] == "risk")


def test_a_high_blood_pressure_is_korkea_with_the_tables_reason_and_basis():
    p = Persona(persona_id="synthetic-bp", label="synthetic", records=[_measurement("bp", {"verenpaine": "158/92"})])
    d = decide(cut(p, "laakari"))
    assert d["risk"]["risk_level"] == "korkea"
    assert _risk_entry(d)["rule_id"] == "risk-verenpaine-korkea"
    reason = next(rule["output"]["reason"] for rule in table("risk")["rules"]
                  if rule["rule_id"] == "risk-verenpaine-korkea")
    assert d["risk"]["reason"] == reason
    assert d["risk"]["basis"] == "illustrative_threshold"


def test_the_blood_pressure_rule_needs_both_cut_offs():
    """Two predicates are ANDed by `_match_rule`: a high systolic with a normal diastolic is not the rule."""
    p = Persona(persona_id="synthetic-bp", label="synthetic", records=[_measurement("bp", {"verenpaine": "150/85"})])
    assert _risk_entry(decide(cut(p, "laakari")))["rule_id"] != "risk-verenpaine-korkea"


def test_a_fever_with_a_high_blood_pressure_still_reports_the_fever_rule():
    p = Persona(persona_id="synthetic-fever", label="synthetic", records=[
        _measurement("fever", {"lämpö_c": 39.5}),
        _measurement("bp", {"verenpaine": "158/92"}),
    ])
    d = decide(cut(p, "laakari"))
    assert _risk_entry(d)["rule_id"] == "risk-akuutti-vitaalit"
    assert d["risk"]["reason"] == "Korkea kuume"


def test_a_persona_with_no_measurement_is_unmoved_by_the_blood_pressure_rule():
    p = Persona(persona_id="synthetic-quiet", label="synthetic", records=[_note("n", "Vointi hyvä.", topic="hoito")])
    assert _risk_entry(decide(cut(p, "laakari")))["rule_id"] == "risk-yleinen-matala"
