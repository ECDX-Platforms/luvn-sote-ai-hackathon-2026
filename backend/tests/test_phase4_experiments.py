"""Phase 4 experiments: the floor/margin ablation and the structured-fact pairing.

Both are offline and deterministic. These tests pin the two published results so a classifier or
fixture change cannot silently rewrite the sensitivity finding, and they check the properties the
reports claim: the replay is faithful, zero floors never demote a status, and each pairing class is
reachable.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

# The offline experiments live in `backend/scripts`; pytest imports them as a package (pythonpath
# "."), which the type checker's import root does not cover. Same suppression pattern as app.telemetry.
# pi-lens-ignore: reportAttributeAccessIssue
from scripts import replay_floor_ablation as ablation  # type: ignore[reportAttributeAccessIssue]
# pi-lens-ignore: reportAttributeAccessIssue
from scripts import run_structured_fact_disagreement as disagreement  # type: ignore[reportAttributeAccessIssue]
from app.decision_model import classify_status  # noqa: E402


def _answer(choice: str, probabilities: dict) -> dict:
    return {"choice": choice, "probabilities": probabilities}


def test_zero_floors_never_demote_a_status_and_still_require_the_stated_choice():
    """Monotone: the ablation can only promote, and a non-committal answer stays unclear."""
    cases = [
        (_answer("relevant", {"relevant": 0.55, "unclear": 0.45}),
         _answer("current", {"current": 0.6, "historical": 0.4}),
         _answer("person", {"person": 0.6, "object": 0.4})),
        (_answer("irrelevant", {"irrelevant": 0.66, "unclear": 0.34}),
         _answer("current", {"current": 0.9, "historical": 0.1}),
         _answer("person", {"person": 0.9, "object": 0.1})),
        (_answer("relevant", {"relevant": 0.4, "unclear": 0.6}),
         _answer("current", {"current": 0.9, "historical": 0.1}),
         _answer("person", {"person": 0.9, "object": 0.1})),
        (_answer("unclear", {"unclear": 0.7, "relevant": 0.3}),
         _answer("current", {"current": 0.9, "historical": 0.1}),
         _answer("person", {"person": 0.9, "object": 0.1})),
    ]
    ranks = {"relevant": 2, "irrelevant": 2, "unclear": 1}
    for relevance, temporal, binding in cases:
        production, _, _ = classify_status(relevance, temporal, binding)
        zero, gate_refused, _ = classify_status(relevance, temporal, binding, addition_floor=0.0,
                                                omission_floor=0.0, decision_margin=0.0)
        assert ranks[zero] >= ranks[production], "zero floors demoted a status"
        if relevance["choice"] == "unclear":
            assert zero == "unclear" and not gate_refused, "a non-committal answer became a decision"
    assert classify_status(*cases[0], addition_floor=0.0, omission_floor=0.0, decision_margin=0.0)[0] == "relevant"
    assert classify_status(*cases[3], addition_floor=0.0, omission_floor=0.0, decision_margin=0.0)[0] == "unclear"


def test_person_binding_and_currentness_still_gate_at_zero_floors():
    """The ablation removes probability gates, not the model's explicit choice requirements."""
    relevance = _answer("relevant", {"relevant": 1.0, "unclear": 0.0})
    historical = _answer("historical", {"historical": 1.0, "current": 0.0})
    object_binding = _answer("object", {"object": 1.0, "person": 0.0})
    person = _answer("person", {"person": 1.0, "object": 0.0})
    current = _answer("current", {"current": 1.0, "historical": 0.0})
    zero = {"addition_floor": 0.0, "omission_floor": 0.0, "decision_margin": 0.0}
    assert classify_status(relevance, historical, person, **zero)[0] == "unclear"
    assert classify_status(relevance, current, object_binding, **zero)[0] == "unclear"
    assert classify_status(relevance, current, person, **zero)[0] == "relevant"


def test_floor_ablation_replay_is_faithful_and_pins_the_sensitivity_result():
    """Replay the recorded run under a zero floor, and pin what the floors actually change.

    These are the Decision-1 measurements, re-recorded when the classifier was substituted. They differ from
    the previous model\'s numbers in one instructive way: the shipped floors now refuse one addition that a
    zero floor makes (`bound-note-009`, in an encounter whose key authors nothing), so the ablation shows the
    thresholds doing work rather than only reproducing the same set. The values are pinned so the artifact
    cannot drift unnoticed; they are a recorded measurement, not a threshold of correctness.
    """
    report = ablation.run()
    result = report["result"]

    # The replay reproduced the recorded run: nothing is published from an unfaithful replay.
    assert "all 11 encounters" in report["replay_validation"]
    totals = result["totals"]
    assert totals["additions"]["production"] == ["bound-note-011", "bound-note-017",
                                                 "bound-note-scale-041"]
    # A zero floor adds one more record — the unauthored one the shipped floors refuse.
    assert totals["additions"]["zero_floor"] == ["bound-note-009", "bound-note-011", "bound-note-017",
                                                 "bound-note-scale-041"]
    production_omitted = totals["selection_omitted_baseline_record_ids"]["production"]
    assert "bound-note-013" in production_omitted
    assert len(production_omitted) == 7
    # Lowering the floor does not change which baseline records are omitted.
    assert totals["selection_omitted_baseline_record_ids"]["zero_floor"] == production_omitted
    # No omission or addition the answer key speaks to is wrong in either arm.
    assert totals["wrong_omissions"] == {"production": 0, "zero_floor": 0}
    assert totals["wrong_additions"] == {"production": 0, "zero_floor": 0}
    assert totals["gate_refused_count"] == {"production": 2, "zero_floor": 0}
    assert totals["assessed_count"] == 63
    assert totals["status_changes"] == 2
    assert result["pre_registered_expectations"] == {
        "additions_unchanged": False, "omissions_unchanged": True, "emphasis_not_reduced": True,
        "gate_refused_not_increased": True, "assessed_set_unchanged": True}

    # The changes are promotions inside the recorded assessment set, each one published with its note.
    for rows in result["status_changes_by_encounter"].values():
        for row in rows:
            assert row["recorded_status"] == "unclear"
            assert row["replayed_status"] in {"relevant", "irrelevant"}

    # The committed artifact is exactly what this replay produces, so it cannot drift unnoticed.
    target = ablation.TARGET.read_text(encoding="utf-8")
    import json

    assert json.loads(target) == report


def test_structured_fact_pairing_classifies_every_authored_pair():
    report = disagreement.run()
    # Decision-1 promoted more sources to addition-or-emphasis than the previous model did, so the pairing
    # population is wider and the counts below are the re-recorded ones.
    assert report["population"]["addition_or_emphasis_ids"] == [
        "bound-esitieto-013", "bound-note-002", "bound-note-003", "bound-note-005", "bound-note-010",
        "bound-note-011", "bound-note-017", "bound-note-020", "bound-note-022", "bound-note-scale-041"]
    assert report["counts"] == {"agreement": 2, "contradiction": 1, "absent": 4,
                                "not-comparable": 6, "disagreement": 11, "pairs": 13,
                                "disagreement_denominator": 13}
    by_key = {(entry["record_id"], entry.get("kind"), entry.get("paired_record_id")): entry
              for entry in report["entries"]}
    # The count of missed doses supports the note's adherence claim.
    assert by_key[("bound-note-011", "adherence_vs_missed_doses", "bound-med-005")]["classification"] == "agreement"
    # The note denies self-harm thoughts while the PHQ-9 scores that item above zero.
    contradiction = by_key[("bound-note-003", "absence_claim_vs_instrument_item", "bound-arviointi-003")]
    assert contradiction["classification"] == "contradiction"
    assert contradiction["evidence"]["items"] == {"q9": 1}
    # Two personas carry no structured record at all; one claim has no instrument of its construct.
    assert by_key[("bound-note-010", "functioning_vs_iadl", None)]["classification"] == "absent"
    assert by_key[("bound-note-010", "functioning_vs_iadl", "bound-arviointi-003")]["classification"] == "not-comparable"
    assert by_key[("bound-note-017", "adherence_vs_missed_doses", None)]["classification"] == "absent"
    import json

    assert json.loads(disagreement.TARGET.read_text(encoding="utf-8")) == report


def test_a_mispaired_claim_fails_loudly_instead_of_publishing_an_absence():
    """The 'absent' entries are verified, not assumed: a pair claiming no medication record while the
    person has one must fail instead of reporting a fabricated absence."""
    note, persona_id = disagreement._note("bound-note-011")
    with pytest.raises(disagreement.PairingError):
        disagreement._classify({"claim": "regular tablet missed on several days",
                                "kind": "adherence_vs_missed_doses", "record": None}, note, persona_id)
