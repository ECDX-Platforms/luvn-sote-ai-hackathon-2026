"""F2 value evaluators for the Decision-1 relevance layer.

Two questions the product claim rests on, and which no earlier artifact answered:

  1. When Decision-1 ADDS a source the deterministic baseline missed, is that addition in fact relevant?
  2. Is a baseline `Kirjaus` ever WRONGLY omitted from generation?

Both are selection questions, so this runs with **Foundry disabled**: narration is not needed to answer
either, which keeps the run cheap and free of the narration coupling. It requires `LUNA_FOUNDRY_KEY` and refuses
to run without it -- a missing key must never be reported as "no additions", which is how an earlier
scorecard ended up giving a false reason for a missing evaluator.

Answer keys live in `EXPECTED` and are **author-authored and unadjudicated**: this measures pipeline
behaviour against the author's expectation, not clinical accuracy. Run from `backend/` with LUNA_FOUNDRY_KEY set.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app import decision_providers, decision_model, pipeline  # noqa: E402
from tests.fixtures.bound_encounters import ENCOUNTERS, EVALUATION_ENCOUNTERS  # noqa: E402
from app.fixtures import load_decision_eval_personas, load_personas  # noqa: E402
from app.pipeline import PREPARATION_WINDOW_S, build_picture_for_encounter  # noqa: E402

# The doctor role-control encounter is covered by a deterministic policy test, not an F2 Decision-1 answer key.
EVAL_ENCOUNTERS = {key: encounter for key, encounter in {**ENCOUNTERS, **EVALUATION_ENCOUNTERS}.items()
                   if key != "bound-encounter-003-mielenterveys-laakari"}

RESULT = ROOT / ".scratch" / "hackathon-readiness" / "results" / "decision-value-eval-cap80-v1.json"

# Fixed so a rerun is comparable. The fixtures' current notes are dated within the year before this.
TODAY = date(2026, 10, 2)

EMPHASIS_PROVENANCE = (
    "Emphasis expectations were authored 2026-10-05 after the first live observation of the "
    "emphasised IDs. They measure author agreement with Decision-1's priority ranking, not pre-registered "
    "or adjudicated validation, and must not be quoted as accuracy."
)

EXPECTED: dict[str, dict] = {
    "bound-encounter-002-laakitys-sairaanhoitaja": {
        "expect_additions": [],
        "expect_no_addition": [],
        "note": "no baseline-missed candidate is authored as relevant",
    },
    # Emphasis expectations were authored on 2026-10-05, AFTER the first live observation of which
    # IDs Decision-1 emphasised, so they are author agreement and NOT pre-registered validation — unlike the
    # addition keys, which were authored before the run. They remain falsifiable: each names the note
    # whose topic matches the encounter purpose, and because the two nurse encounters share one
    # person, a purpose-blind classifier would emphasise the SAME note in both. See
    # EMPHASIS_PROVENANCE, which is published in the report.
    "bound-encounter-003-mielenterveys-sairaanhoitaja": {
        "expect_additions": [], "expect_no_addition": [],
        "expect_emphasis": ["bound-note-003"],
        "note": "mood and sleep note; topic mielenterveys matches the mental-health purpose",
    },
    "bound-encounter-003-toimintakyky-sairaanhoitaja": {
        "expect_additions": [], "expect_no_addition": [],
        "expect_emphasis": ["bound-note-010"],
        "note": "daily-tasks note; topic toimintakyky matches the functioning purpose, and this is the "
                "same person as the mental-health encounter, which must emphasise a different note",
    },
    "bound-encounter-004-sosiaalipalvelut-sosiaalityontekija": {"expect_additions": [], "expect_no_addition": []},
    "bound-encounter-005-laakitys-sairaanhoitaja": {
        "expect_additions": ["bound-note-011"],
        "expect_no_addition": ["bound-note-012"],
        "expect_omissions": ["bound-note-013"],
        "observe_candidates": ["bound-note-013"],
        "expect_emphasis": ["bound-note-011"],
        "note": "note-011 is a missed relevant medication-follow-up fact and the clinical priority; note-012 is unrelated; note-013 is administrative and should be omitted from generation but remain visible",
    },
    "bound-encounter-006-laakitys-sairaanhoitaja": {
        "expect_additions": [], "expect_no_addition": ["bound-note-014"],
        "observe_candidates": ["bound-note-014"],
        "note": "current note describes the spouse's medication, not the encounter person",
    },
    "bound-encounter-007-laakitys-sairaanhoitaja": {
        "expect_additions": [], "expect_no_addition": ["bound-note-015"],
        "observe_candidates": ["bound-note-015"],
        "note": "current office-room booking is not a patient situation fact",
    },
    "bound-encounter-008-laakitys-sairaanhoitaja": {
        "expect_additions": [], "expect_no_addition": ["bound-note-016"],
        "observe_candidates": ["bound-note-016"],
        "note": "the medication note is older than the current-note window and must not be added",
    },
    "bound-encounter-009-laakitys-sairaanhoitaja": {
        "expect_additions": ["bound-note-017"], "expect_no_addition": [],
        "observe_candidates": ["bound-note-017"],
        "note": "the current adherence/side-effect note is relevant but shares no medication baseline term",
    },
    "bound-encounter-010-laakitys-sairaanhoitaja": {
        "expect_additions": ["bound-note-scale-041"], "expect_no_addition": [],
        "observe_candidates": ["bound-note-scale-000", "bound-note-scale-041"],
        "expected_coverage": {
            "decision_candidate_count": 42, "decision_shortlisted_count": 42,
            "decision_not_shortlisted_count": 0, "decision_budget_exhausted_count": 0,
            "decision_too_long_count": 1,
        },
        "note": "42 current free-text notes exercise the 80-note shortlist/call cap, byte cap and coverage accounting. bound-note-scale-041 remains the unchanged authored relevant keyless missed note. The one over-byte-cap note is not sent; the live run measures all other responses, including any throttling or timeout, rather than assuming all 41 concurrent requests succeed.",
    },
    "bound-encounter-011-laakitys-sairaanhoitaja": {
        "expect_additions": [], "expect_no_addition": ["bound-note-018"],
        "expect_abstentions": ["bound-note-018"],
        "observe_candidates": ["bound-note-018"],
        "note": "owner-authored ambiguous note: actor, date and tablet are unspecified; expected Decision-1 abstention",
    },
}


def _facts(picture) -> dict:
    sel = picture.selection
    baseline = set(sel.baseline_record_ids)
    # Foundry is disabled for this value run. Count selection-layer omissions from the explicit
    # completed `irrelevant` baseline reads; a separate stubbed pipeline test proves generator input.
    selection_omitted = sorted(n.record_id for n in sel.decision_notes
                               if n.status == "irrelevant" and n.record_id in baseline)
    return {
        "decision_status": sel.decision_status,
        "rule_version": sel.rule_version,
        "baseline_record_ids": sorted(baseline),
        "additional_record_ids": sorted(sel.additional_record_ids),
        "emphasized_record_ids": sorted(sel.emphasized_record_ids),
        "priority_record_ids": [row.record_id for row in picture.records.priority],
        "urgency_order_scores": {
            n.record_id: sum(
                index * probability
                for index, (_level, probability) in enumerate(
                    sorted(n.gate_probabilities["urgency"].items())
                )
            )
            for n in sel.decision_notes if "urgency" in n.gate_probabilities
        },
        "selection_omitted_baseline_record_ids": selection_omitted,
        "coverage": {
            "decision_candidate_count": sel.decision_candidate_count,
            "decision_shortlisted_count": sel.decision_shortlisted_count,
            "decision_assessed_count": sel.decision_assessed_count,
            "decision_not_shortlisted_count": sel.decision_not_shortlisted_count,
            "decision_budget_exhausted_count": sel.decision_budget_exhausted_count,
            "decision_too_long_count": sel.decision_too_long_count,
            "decision_unavailable_count": sel.decision_unavailable_count,
            "decision_gate_refused_count": sel.decision_gate_refused_count,
            "decision_baseline_candidate_count": sel.decision_baseline_candidate_count,
            "decision_baseline_attempted_count": sel.decision_baseline_attempted_count,
            "decision_missed_candidate_count": sel.decision_missed_candidate_count,
            "decision_missed_attempted_count": sel.decision_missed_attempted_count,
        },
        "notes": [
            {
                "record_id": n.record_id,
                "status": n.status,
                "gate_refused": getattr(n, "gate_refused", None),
                "relevance_probabilities": n.relevance_probabilities,
                "gate_probabilities": n.gate_probabilities,
                "relevance_confidence": n.relevance_confidence,
                "time_sensitivity": n.time_sensitivity,
                "subject_binding": n.subject_binding,
                "negation_probability": n.negation_probability,
                "model": n.model,
                "elapsed_ms": n.elapsed_ms,
            }
            for n in sel.decision_notes
        ],
        "decision_elapsed_ms": sel.decision_elapsed_ms,
        "decision_reported_input_tokens": sel.decision_reported_input_tokens,
        "decision_reported_output_tokens": sel.decision_reported_output_tokens,
    }


def _metrics(facts: dict, expected: dict, observations: list[dict]) -> dict:
    additions = set(facts["additional_record_ids"])
    wanted = set(expected.get("expect_additions", []))
    forbidden = set(expected.get("expect_no_addition", []))
    omissions = set(facts["selection_omitted_baseline_record_ids"])
    wanted_omissions = set(expected.get("expect_omissions", []))
    wanted_abstentions = set(expected.get("expect_abstentions", []))
    emphasized = set(facts["emphasized_record_ids"])
    wanted_emphasis = set(expected.get("expect_emphasis", []))
    # Only an encounter with an authored `expect_emphasis` can be wrong about emphasis. Counting an
    # emphasis in an unauthored encounter as "wrong" would report the absence of an answer key as a
    # classifier error, which is the misleading-number pattern this evaluator exists to prevent.
    emphasis_authored = "expect_emphasis" in expected
    # An encounter with no authored key cannot be wrong about what it added. Counting every addition in a
    # silent encounter as an error reports the absence of an answer key as a classifier mistake — the same
    # misleading-number pattern the emphasis rule below refuses. `bound-note-009` is added in four of five
    # repeat runs of an encounter whose key authors nothing, which is a gap in the fixture and is reported as
    # an unauthored addition rather than as a wrong one.
    unauthored = additions - wanted - forbidden
    authored_additions = additions & (wanted | forbidden)
    # The same rule for omissions: an omission the key does not speak to is not an error. Without this, a
    # record the fixture left no expectation for is counted as wrongly omitted, which reports the key's gap
    # as a selection failure.
    unauthored_omissions = omissions - wanted_omissions
    candidate_rows = {row["record_id"]: row for row in observations}
    actual_abstentions = {record_id for record_id in wanted_abstentions
                          if (row := candidate_rows.get(record_id)) and row.get("status") == "unclear"}
    gate_refused_abstentions = {record_id for record_id in actual_abstentions
                                if candidate_rows[record_id].get("gate_refused")}
    return {
        "expected_additions": sorted(wanted),
        "actual_additions": sorted(additions),
        "correct_additions": sorted(additions & wanted),
        "wrong_additions": sorted(additions & forbidden),
        "unauthored_additions": sorted(unauthored),
        "missed_expected_additions": sorted(wanted - additions),
        # Precision over the additions the key actually speaks to; an unauthored addition is counted
        # separately so it cannot silently flatter or deflate the ratio.
        "addition_precision_numerator": len(additions & wanted),
        "addition_precision_denominator": len(authored_additions),
        "irrelevant_candidate_wrongly_added": sorted(additions & forbidden),
        "expected_omissions": sorted(wanted_omissions),
        "actual_omissions": sorted(omissions),
        "correct_omissions": sorted(omissions & wanted_omissions),
        "wrong_omissions": sorted(omissions - wanted_omissions - unauthored_omissions),
        "unauthored_omissions": sorted(unauthored_omissions),
        "missed_expected_omissions": sorted(wanted_omissions - omissions),
        "expected_abstentions": sorted(wanted_abstentions),
        "actual_abstentions": sorted(actual_abstentions),
        "gate_refused_abstentions": sorted(gate_refused_abstentions),
        "missed_expected_abstentions": sorted(wanted_abstentions - actual_abstentions),
        "actual_emphasized": sorted(emphasized),
        "expected_emphasis": sorted(wanted_emphasis),
        "emphasis_expectation_authored": emphasis_authored,
        "correct_emphasis": sorted(emphasized & wanted_emphasis),
        "wrong_emphasis": sorted(emphasized - wanted_emphasis) if emphasis_authored else [],
        "unauthored_emphasis": sorted(emphasized) if not emphasis_authored else [],
        "missed_expected_emphasis": sorted(wanted_emphasis - emphasized),
    }


async def _run(personas) -> dict:
    results: dict[str, dict] = {}
    raw_candidates: dict[str, list[dict]] = {}
    current_encounter: str | None = None
    classify = pipeline.classify_decision_notes

    async def capture_candidates(scoped, baseline_ids, today):
        rows = await classify(scoped, baseline_ids, today)
        if current_encounter is not None:
            raw_candidates[current_encounter] = rows
        return rows

    pipeline.classify_decision_notes = capture_candidates
    try:
        for encounter_id in EVAL_ENCOUNTERS:
            current_encounter = encounter_id
            started_at = time.monotonic()
            on = await build_picture_for_encounter(
                encounter_id, personas=personas, encounters=EVAL_ENCOUNTERS, today=TODAY,
                started_at=started_at,
                preparation_deadline=started_at + PREPARATION_WINDOW_S,
                decision_enabled=True, foundry_enabled=False)
            started_at = time.monotonic()
            off = await build_picture_for_encounter(
                encounter_id, personas=personas, encounters=EVAL_ENCOUNTERS, today=TODAY,
                started_at=started_at,
                preparation_deadline=started_at + PREPARATION_WINDOW_S,
                decision_enabled=False, foundry_enabled=False)
            on_facts, off_facts = _facts(on), _facts(off)
            expected = EXPECTED.get(encounter_id, {"expect_additions": [], "expect_no_addition": []})
            observed_ids = (set(expected.get("expect_additions", []))
                            | set(expected.get("expect_no_addition", []))
                            | set(expected.get("expect_omissions", []))
                            | set(expected.get("expect_abstentions", []))
                            | set(expected.get("observe_candidates", [])))
            candidate_observations = []
            for row in raw_candidates.get(encounter_id, []):
                if row.get("record_id") not in observed_ids:
                    continue
                relevance = row.get("relevance") or {}
                temporal = row.get("time_sensitivity") or {}
                binding = row.get("subject_binding") or {}
                urgency = row.get("urgency") or {}
                candidate_observations.append({
                    "record_id": row["record_id"],
                    "candidate_group": row.get("candidate_group"),
                    "status": row.get("status"),
                    "gate_refused": bool(row.get("gate_refused")),
                    "relevance_choice": relevance.get("choice"),
                    "relevance_probabilities": relevance.get("probabilities"),
                    "relevance_confidence": relevance.get("confidence"),
                    "time_sensitivity_choice": temporal.get("choice"),
                    "time_sensitivity_probabilities": temporal.get("probabilities"),
                    "subject_binding_choice": binding.get("choice"),
                    "subject_binding_probabilities": binding.get("probabilities"),
                    "negation_probability": row.get("negation"),
                    "urgency_score": urgency.get("score"),
                    "urgency_probabilities": urgency.get("probabilities"),
                    "model": row.get("model"),
                    "elapsed_ms": row.get("elapsed_ms"),
                })
            results[encounter_id] = {
                "expected": expected,
                "decision_on": on_facts,
                "decision_off_baseline": off_facts,
                "candidate_observations": candidate_observations,
                # Every candidate's parsed answer, for the paired floor/margin replay: the replay
                # rebuilds the selection from these rows under two threshold sets, so the model
                # answers are byte-identical between variants. Note text is never copied; record IDs
                # are needed because a promoted candidate becomes a selected or omitted source.
                "ablation_candidates": [
                    {
                        "record_id": row["record_id"],
                        "candidate_group": row.get("candidate_group"),
                        "attempted": bool(row.get("attempted")),
                        "status": row.get("status"),
                        "gate_refused": bool(row.get("gate_refused")),
                        "relevance": row.get("relevance"),
                        "time_sensitivity": row.get("time_sensitivity"),
                        "subject_binding": row.get("subject_binding"),
                        "urgency": (row.get("urgency") or {}).get("probabilities"),
                        "model": row.get("model"),
                    }
                    for row in raw_candidates.get(encounter_id, [])
                ],
                # The deterministic baseline must not depend on Decision-1.
                "baseline_identical_without_decision": (
                    on_facts["baseline_record_ids"] == off_facts["baseline_record_ids"]),
                "coverage_matches_expected": {
                    key: on_facts["coverage"][key] == value
                    for key, value in expected.get("expected_coverage", {}).items()
                },
                "metrics": _metrics(on_facts, expected, candidate_observations),
            }
        return results
    finally:
        pipeline.classify_decision_notes = classify


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=str(RESULT),
                        help="artifact path; defaults to the scorecard the evidence summary renders")
    args = parser.parse_args()
    out = Path(args.out)
    if decision_providers.provider() is None:
        raise SystemExit(
            "no decision provider is configured: refusing to run. A missing key would be reported as 'no additions', "
            "which is exactly the false conclusion this evaluator exists to prevent.")

    personas = load_personas() + load_decision_eval_personas()
    results = asyncio.run(_run(personas))

    counts = {"additions": 0, "expected_additions": 0, "correct_additions": 0,
              "wrong_additions": 0, "unauthored_additions": 0, "missed_expected_additions": 0,
              "correct_omissions": 0, "wrong_omissions": 0,
              "missed_expected_omissions": 0, "correct_abstentions": 0,
              "expected_omissions": 0, "expected_abstentions": 0,
              "baseline_candidate_denominator": 0, "baseline_attempted_denominator": 0,
              "missed_expected_abstentions": 0, "encounters": len(results),
              "emphasized_sources": 0, "encounters_with_emphasis": 0,
              "correct_emphasis": 0, "expected_emphasis": 0,
              "wrong_emphasis": 0, "missed_expected_emphasis": 0,
              "unauthored_emphasis": 0, "audited_emphasized_sources": 0}
    for facts in results.values():
        m = facts["metrics"]
        counts["additions"] += len(m["actual_additions"])
        counts["expected_additions"] += len(m["expected_additions"])
        counts["correct_additions"] += len(m["correct_additions"])
        counts["wrong_additions"] += len(m["wrong_additions"])
        counts["unauthored_additions"] += len(m["unauthored_additions"])
        counts["missed_expected_additions"] += len(m["missed_expected_additions"])
        counts["correct_omissions"] += len(m["correct_omissions"])
        counts["wrong_omissions"] += len(m["wrong_omissions"])
        counts["missed_expected_omissions"] += len(m["missed_expected_omissions"])
        counts["correct_abstentions"] += len(m["actual_abstentions"])
        counts["expected_omissions"] += len(m["expected_omissions"])
        counts["expected_abstentions"] += len(m["expected_abstentions"])
        counts["baseline_candidate_denominator"] += facts["decision_on"]["coverage"]["decision_baseline_candidate_count"]
        counts["baseline_attempted_denominator"] += facts["decision_on"]["coverage"]["decision_baseline_attempted_count"]
        counts["missed_expected_abstentions"] += len(m["missed_expected_abstentions"])
        counts["emphasized_sources"] += len(m["actual_emphasized"])
        counts["encounters_with_emphasis"] += 1 if m["actual_emphasized"] else 0
        counts["correct_emphasis"] += len(m["correct_emphasis"])
        counts["expected_emphasis"] += len(m["expected_emphasis"])
        counts["wrong_emphasis"] += len(m["wrong_emphasis"])
        counts["missed_expected_emphasis"] += len(m["missed_expected_emphasis"])
        counts["unauthored_emphasis"] += len(m["unauthored_emphasis"])
        if m["emphasis_expectation_authored"]:
            counts["audited_emphasized_sources"] += len(m["actual_emphasized"])
    # Precision is over the additions the answer key speaks to. An addition in an encounter whose key authors
    # nothing is reported as `unauthored_additions`, never as a miss and never in this denominator, because
    # otherwise the absence of a key would move the headline number.
    authored_additions = counts["correct_additions"] + counts["wrong_additions"]
    totals = {**counts,
              "addition_precision": (round(counts["correct_additions"] / authored_additions, 3)
                                     if authored_additions else None),
              "addition_precision_numerator": counts["correct_additions"],
              "addition_precision_denominator": authored_additions,
              "f2_denominators": {
                  "addition_precision": [counts["correct_additions"], authored_additions],
                  "missed_expected_additions": [counts["missed_expected_additions"], counts["expected_additions"]],
                  "wrong_additions": [counts["wrong_additions"], authored_additions],
                  "unauthored_additions": [counts["unauthored_additions"], counts["additions"]],
                  "correct_omissions": [counts["correct_omissions"], counts["expected_omissions"]],
                  "wrong_omissions": [counts["wrong_omissions"], counts["baseline_candidate_denominator"]],
                  "missed_expected_omissions": [counts["missed_expected_omissions"], counts["expected_omissions"]],
                  "abstentions": [counts["correct_abstentions"], counts["expected_abstentions"]],
                  "emphasis_matches": [counts["correct_emphasis"], counts["expected_emphasis"]],
                  "wrong_emphasis": [counts["wrong_emphasis"], counts["audited_emphasized_sources"]],
              }}

    report = {
        "kind": "decision-value-eval",
        "today": TODAY.isoformat(),
        "preparation_window_s": PREPARATION_WINDOW_S,
        "mode": "selection-only; Foundry disabled (narration is not needed to measure selection)",
        "selection_caps": {
            "shortlist_limit": decision_model.SHORTLIST_LIMIT,
            "max_notes": decision_model.MAX_NOTES,
            "total_budget_s": decision_model.TOTAL_BUDGET_S,
            "max_text_bytes": decision_model.MAX_TEXT_BYTES,
        },
        "emphasis_provenance": EMPHASIS_PROVENANCE,
        "limits": [
            "synthetic fixtures; the answer keys are author-authored and unadjudicated, not clinical truth",
            "selection-only run against the live Decision-1 service; no Foundry call and no narration is measured",
            "a green precision number on this synthetic inventory is not evidence of clinical or Finnish accuracy",
            "answer keys are expectations, not model outputs; omission and abstention results are counted against their explicit authored IDs",
            "candidate_observations records probabilities only for authored control IDs; note text is never copied",
            "ablation_candidates records every candidate's parsed answer (choices and probabilities, no note text) so the floor/margin replay can rebuild the selection from one recorded answer set",
            "wrong_omissions counts baseline records excluded from the generation-eligible selection; it is a selection-layer omission, not an observed model-call omission",
            "the six evaluator-only encounters live in tests.fixtures.bound_encounters.EVALUATION_ENCOUNTERS and are not part of the hosted product inventory",
            "the 42-note scale fixture's per-run candidate outcomes and Decision-1 elapsed time are recorded in the scale result; over-byte-cap notes are not sent",
            EMPHASIS_PROVENANCE,
        ],
        "totals": totals,
        "results": results,
    }
    RESULT.parent.mkdir(parents=True, exist_ok=True)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    for encounter_id, facts in results.items():
        m = facts["metrics"]
        print(f"{encounter_id}: added={m['actual_additions']} expected={m['expected_additions']} "
              f"wrong={m['wrong_additions']} wrong_omissions={m['wrong_omissions']} "
              f"baseline_stable={facts['baseline_identical_without_decision']}")
    print(f"\ntotals: {json.dumps(totals, ensure_ascii=False)}")
    for encounter_id, facts in results.items():
        if facts["expected"].get("expected_coverage"):
            print(f"coverage {encounter_id}: {json.dumps(facts['coverage_matches_expected'], ensure_ascii=False)}")
    print(f"result: {out}")


if __name__ == "__main__":
    main()
