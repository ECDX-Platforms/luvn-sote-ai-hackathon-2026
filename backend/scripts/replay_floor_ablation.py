#!/usr/bin/env python
"""Paired floor/margin ablation (Phase 4 item 11) over one recorded Decision-1 candidate set.

The ablation changes only the three probability gates: `ADDITION_FLOOR`, `OMISSION_FLOOR` and
`DECISION_MARGIN`, from their shipped values to zero. Everything else -- schema and probability
validation, the model's explicit `choice`, the currentness and person-binding requirements, the
permission cut, the shortlist/byte/budget limits and fail-closed handling -- stays in force, because
both variants call the same `app.decision_model.classify_status`.

Both variants are computed from `results/decision-value-eval-v2.json`, which records every candidate's
parsed answer. Computing both from one answer set is what makes the comparison paired: a second live
call set would vary independently of the thresholds. Note text is never read here.

This script is offline and deterministic: it calls no model and no network.

    .venv/bin/python scripts/replay_floor_ablation.py
    .venv/bin/python scripts/replay_floor_ablation.py --check   # verify the committed artifact
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app import decision_model  # noqa: E402
from app.policy import cut_for_encounter  # noqa: E402
from app import selection as selection_mod  # noqa: E402
from app.encounters import resolve_encounter  # noqa: E402
from app.fixtures import load_decision_eval_personas, load_personas  # noqa: E402

import run_decision_value_eval as evalmod  # noqa: E402

SOURCE = ROOT / ".scratch" / "hackathon-readiness" / "results" / "decision-value-eval-cap80-v1.json"
TARGET = ROOT / ".scratch" / "hackathon-readiness" / "results" / "decision-floor-ablation-v1.json"

PRODUCTION = {"addition_floor": decision_model.ADDITION_FLOOR,
              "omission_floor": decision_model.OMISSION_FLOOR,
              "decision_margin": decision_model.DECISION_MARGIN}
ZERO = {"addition_floor": 0.0, "omission_floor": 0.0, "decision_margin": 0.0}

LIMITS = [
    "one recorded candidate set replayed under two threshold sets; it cannot say how a fresh selector "
    "call behaves under zero floors",
    "gate-refused is a subset of assessed, never a disjoint bucket",
    "the fixtures and answer keys are synthetic, author-authored and unadjudicated: this is a "
    "sensitivity result, not accuracy",
    "no production threshold, fixture or answer key is changed; the ablation passes its own floors",
]


class AblationError(RuntimeError):
    """The replay disagrees with the recorded run; the sensitivity must not be published."""


def _replay(encounter_id: str, record: dict, floors: dict, personas: list) -> dict:
    encounter, persona = resolve_encounter(encounter_id, personas, evalmod.EVAL_ENCOUNTERS)
    scoped = cut_for_encounter(persona, encounter)
    baseline_records, reasons = selection_mod.baseline(scoped, evalmod.TODAY)
    baseline_ids = {record_.record_id for record_ in baseline_records}

    note_results = []
    changed = []
    for candidate in record["ablation_candidates"]:
        answer = candidate.get("relevance")
        assessable = (bool(candidate.get("attempted")) and isinstance(answer, dict)
                      and isinstance(candidate.get("time_sensitivity"), dict)
                      and isinstance(candidate.get("subject_binding"), dict))
        if assessable:
            status, gate_refused, _margin = decision_model.classify_status(
                answer, candidate["time_sensitivity"], candidate["subject_binding"], **floors)
        else:
            status, gate_refused = candidate["status"], False
        if status != candidate["status"]:
            changed.append({"record_id": candidate["record_id"],
                            "candidate_group": candidate["candidate_group"],
                            "recorded_status": candidate["status"], "replayed_status": status,
                            "choice": (answer or {}).get("choice")})
        note_results.append({"record_id": candidate["record_id"], "status": status,
                             "attempted": bool(candidate.get("attempted")),
                             "gate_refused": gate_refused,
                             "candidate_group": candidate["candidate_group"]})

    selected = selection_mod.apply_decision(scoped, baseline_records, dict(reasons), note_results,
                                       evalmod.TODAY)
    facts = {
        "baseline_record_ids": sorted(baseline_ids),
        "additional_record_ids": sorted(selected.additional_record_ids),
        "emphasized_record_ids": sorted(selected.emphasized_record_ids),
        "selection_omitted_baseline_record_ids": sorted(
            row["record_id"] for row in note_results
            if row["status"] == "irrelevant" and row["record_id"] in baseline_ids),
    }
    observations = [{"record_id": row["record_id"], "status": row["status"],
                     "gate_refused": row["gate_refused"]} for row in note_results]
    return {
        "facts": facts,
        "metrics": evalmod._metrics(facts, record["expected"], observations),
        "gate_refused_count": sum(1 for row in note_results if row["gate_refused"]),
        "assessed_count": sum(1 for row in note_results if row["attempted"]),
        "unclear_with_choice_count": sum(
            1 for row in note_results if row["status"] == "unclear"
            and isinstance(next((candidate.get("relevance") for candidate in record["ablation_candidates"]
                                 if candidate["record_id"] == row["record_id"]), None), dict)
            and (next(candidate["relevance"] for candidate in record["ablation_candidates"]
                      if candidate["record_id"] == row["record_id"]) or {}).get("choice") in {"relevant", "irrelevant"}),
        "status_changes": changed,
    }


def _validate(encounter_id: str, production: dict, record: dict) -> None:
    recorded = record["decision_on"]
    for field in ("additional_record_ids", "emphasized_record_ids",
                  "selection_omitted_baseline_record_ids"):
        if production["facts"][field] != sorted(recorded[field]):
            raise AblationError(f"{encounter_id}: replay {field}={production['facts'][field]} "
                                f"but the recorded run has {sorted(recorded[field])}")
    if production["metrics"] != record["metrics"]:
        raise AblationError(f"{encounter_id}: replayed metrics differ from the recorded metrics")


def run() -> dict:
    try:
        source = json.loads(SOURCE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise AblationError(f"cannot read {SOURCE.name}: {type(error).__name__}") from error
    if not source["results"] or "ablation_candidates" not in next(iter(source["results"].values())):
        raise AblationError(f"{SOURCE.name} predates the full candidate recording")
    personas = load_personas() + load_decision_eval_personas()

    encounters: dict[str, dict] = {}
    for encounter_id, record in source["results"].items():
        production = _replay(encounter_id, record, PRODUCTION, personas)
        _validate(encounter_id, production, record)
        zero = _replay(encounter_id, record, ZERO, personas)
        encounters[encounter_id] = {
            "expected": record["expected"],
            "assessed_count": production["assessed_count"],
            "unclear_with_choice_count": production["unclear_with_choice_count"],
            "production": {"additional_record_ids": production["facts"]["additional_record_ids"],
                           "selection_omitted_baseline_record_ids":
                               production["facts"]["selection_omitted_baseline_record_ids"],
                           "emphasized_record_ids": production["facts"]["emphasized_record_ids"],
                           "gate_refused_count": production["gate_refused_count"]},
            "zero_floor": {"additional_record_ids": zero["facts"]["additional_record_ids"],
                           "selection_omitted_baseline_record_ids":
                               zero["facts"]["selection_omitted_baseline_record_ids"],
                           "emphasized_record_ids": zero["facts"]["emphasized_record_ids"],
                           "gate_refused_count": zero["gate_refused_count"]},
            "production_metrics": production["metrics"],
            "zero_floor_metrics": zero["metrics"],
            "delta": {
                "additions_added": sorted(set(zero["facts"]["additional_record_ids"])
                                          - set(production["facts"]["additional_record_ids"])),
                "additions_removed": sorted(set(production["facts"]["additional_record_ids"])
                                            - set(zero["facts"]["additional_record_ids"])),
                "omissions_added": sorted(set(zero["facts"]["selection_omitted_baseline_record_ids"])
                                          - set(production["facts"]["selection_omitted_baseline_record_ids"])),
                "omissions_removed": sorted(set(production["facts"]["selection_omitted_baseline_record_ids"])
                                            - set(zero["facts"]["selection_omitted_baseline_record_ids"])),
                "emphasis_added": sorted(set(zero["facts"]["emphasized_record_ids"])
                                         - set(production["facts"]["emphasized_record_ids"])),
                "emphasis_removed": sorted(set(production["facts"]["emphasized_record_ids"])
                                           - set(zero["facts"]["emphasized_record_ids"])),
                "gate_refused_dropped": production["gate_refused_count"] - zero["gate_refused_count"],
                "status_changes": zero["status_changes"],
            },
        }

    def totals(variant: str, field: str) -> int:
        return sum(len(row[variant][field]) for row in encounters.values())

    def union(variant: str, field: str) -> list[str]:
        return sorted({record_id for row in encounters.values() for record_id in row[variant][field]})

    production_additions = union("production", "additional_record_ids")
    zero_additions = union("zero_floor", "additional_record_ids")
    production_omissions = union("production", "selection_omitted_baseline_record_ids")
    zero_omissions = union("zero_floor", "selection_omitted_baseline_record_ids")
    production_emphasis = union("production", "emphasized_record_ids")
    zero_emphasis = union("zero_floor", "emphasized_record_ids")
    assessed_recorded = sum(record["decision_on"]["coverage"]["decision_assessed_count"]
                           for record in source["results"].values())
    if sum(row["assessed_count"] for row in encounters.values()) != assessed_recorded:
        raise AblationError("the replayed assessed set differs from the recorded coverage count")
    changed = {encounter_id: row["delta"]["status_changes"] for encounter_id, row in encounters.items()
               if row["delta"]["status_changes"]}
    result = {
        "totals": {
            "additions": {"production": production_additions, "zero_floor": zero_additions},
            "selection_omitted_baseline_record_ids": {"production": production_omissions,
                                                      "zero_floor": zero_omissions},
            "emphasized_record_ids": {"production": production_emphasis, "zero_floor": zero_emphasis},
            "wrong_omissions": {
                "production": sum(len(row["production_metrics"]["wrong_omissions"])
                                  for row in encounters.values()),
                "zero_floor": sum(len(row["zero_floor_metrics"]["wrong_omissions"])
                                  for row in encounters.values())},
            "wrong_additions": {
                "production": sum(len(row["production_metrics"]["wrong_additions"])
                                  for row in encounters.values()),
                "zero_floor": sum(len(row["zero_floor_metrics"]["wrong_additions"])
                                  for row in encounters.values())},
            "gate_refused_count": {
                "production": sum(row["production"]["gate_refused_count"] for row in encounters.values()),
                "zero_floor": sum(row["zero_floor"]["gate_refused_count"] for row in encounters.values())},
            "assessed_count": sum(row["assessed_count"] for row in encounters.values()),
            "unclear_with_choice_count": sum(row["unclear_with_choice_count"]
                                             for row in encounters.values()),
            "status_changes": sum(len(rows) for rows in changed.values()),
        },
        "pre_registered_expectations": {
            "additions_unchanged": production_additions == zero_additions,
            "omissions_unchanged": production_omissions == zero_omissions,
            "emphasis_not_reduced": set(production_emphasis) <= set(zero_emphasis),
            "gate_refused_not_increased": all(
                row["zero_floor"]["gate_refused_count"] <= row["production"]["gate_refused_count"]
                for row in encounters.values()),
            "assessed_set_unchanged": True,
        },
        "status_changes_by_encounter": changed,
    }
    return {
        "kind": "decision-floor-ablation",
        "date": "2026-10-06",
        "source_artifact": SOURCE.name,
        "source_run_today": source["today"],
        "variants": {"production": PRODUCTION, "zero_floor": ZERO},
        "design": ("both variants replay the same recorded candidate answers through "
                   "app.decision_model.classify_status; only the three thresholds differ, so the "
                   "comparison is paired"),
        "replay_validation": ("production floors reproduced the recorded additional/emphasized/omitted "
                              "IDs and metrics for all 11 encounters, and the replayed assessed set "
                              "matches the recorded coverage count (the floors are applied after a "
                              "response, so they cannot gate assessment)"),
        "limits": LIMITS,
        "result": result,
        "encounters": encounters,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=str(TARGET))
    parser.add_argument("--check", action="store_true",
                        help="re-run and compare with the committed artifact instead of writing it")
    args = parser.parse_args()
    report = run()
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    target = Path(args.out)
    if args.check:
        if not target.is_file() or target.read_text(encoding="utf-8") != rendered:
            raise SystemExit(f"{target.name} does not match the replay; re-run without --check")
        print(f"{target.name} matches the replayed ablation")
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(rendered, encoding="utf-8")
    totals = report["result"]["totals"]
    print("additions:", totals["additions"])
    print("omissions:", totals["selection_omitted_baseline_record_ids"])
    print("emphasis:", totals["emphasized_record_ids"])
    print("gate_refused:", totals["gate_refused_count"], "assessed:", totals["assessed_count"],
          "unclear-with-choice:", totals["unclear_with_choice_count"])
    print("wrong_omissions:", totals["wrong_omissions"], "wrong_additions:", totals["wrong_additions"])
    print("status changes:", totals["status_changes"])
    for encounter_id, rows in report["result"]["status_changes_by_encounter"].items():
        for row in rows:
            print(f"  {encounter_id}: {row['record_id']} {row['recorded_status']} -> "
                  f"{row['replayed_status']} (choice {row['choice']}, {row['candidate_group']})")
    print("expectations:", json.dumps(report["result"]["pre_registered_expectations"]))
    print("wrote", target)


if __name__ == "__main__":
    main()
