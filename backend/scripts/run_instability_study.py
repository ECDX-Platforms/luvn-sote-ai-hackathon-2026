#!/usr/bin/env python
"""N repeats of the selection read, to measure how stable the gate is from run to run.

The earlier version of this study was an ad-hoc artifact with no producer, so nothing could re-derive it and
nothing checked it. This is that producer: it runs the value evaluation `RUNS` times against the live
service, then aggregates the runs into one summary.

Why the study exists: the gate adds a note only when a probability clears a floor **and** beats its runner-up
by a margin. A margin that sits just above the threshold on some runs and just below it on others is a
single-point failure — the same input would produce a different briefing. The watched record is the keyed
missed-cohort note, whose margin was the narrowest one recorded.

The margin is computed here rather than stored by the evaluator: it is the top probability minus the
runner-up in the subject-binding distribution, which is exactly what the gate compares against
`DECISION_MARGIN`. Recomputing it keeps this script honest about what it measures.

    .venv/bin/python scripts/run_instability_study.py              # five live runs, then aggregate
    .venv/bin/python scripts/run_instability_study.py --check      # re-aggregate and verify, no calls
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app import decision_model  # noqa: E402

DIR = ROOT / ".scratch" / "hackathon-readiness" / "results" / "decision-value-eval-instability"
SUMMARY = DIR / "summary.json"
EVAL = ROOT / "backend" / "scripts" / "run_decision_value_eval.py"
RUNS = 5
WATCHED = "bound-note-017"
WATCHED_KEY = WATCHED.replace("-", "_")
COVERAGE_FIELDS = ("decision_candidate_count", "decision_shortlisted_count", "decision_assessed_count",
                   "decision_not_shortlisted_count", "decision_budget_exhausted_count",
                   "decision_too_long_count", "decision_unavailable_count", "decision_gate_refused_count")


def _load_runs() -> list[dict]:
    runs = []
    for index in range(1, RUNS + 1):
        path = DIR / f"run{index}.json"
        if not path.is_file():
            raise SystemExit(f"run_instability_study: missing {path}")
        try:
            runs.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError) as exc:
            raise SystemExit(f"run_instability_study: unreadable {path.name}: {type(exc).__name__}")
    return runs


def _summarise_run(index: int, document: dict, provider: str) -> dict:
    totals = document["totals"]
    watched: dict | None = None
    coverage = dict.fromkeys(COVERAGE_FIELDS, 0)
    keyed_additions: list[str] = []
    for encounter in document["results"].values():
        decision_on = encounter.get("decision_on") or {}
        for name in COVERAGE_FIELDS:
            coverage[name] += int((decision_on.get("coverage") or {}).get(name) or 0)
        keyed_additions.extend((encounter.get("metrics") or {}).get("correct_additions") or [])
        for observation in encounter.get("candidate_observations") or []:
            if observation.get("record_id") != WATCHED:
                continue
            probabilities = observation.get("subject_binding_probabilities") or {}
            ranked = sorted((value for value in probabilities.values() if isinstance(value, (int, float))),
                            reverse=True)
            watched = {
                "added": WATCHED in (decision_on.get("additional_record_ids") or []),
                "status": observation.get("status"),
                "gate_refused": observation.get("gate_refused"),
                "subject_binding_choice": observation.get("subject_binding_choice"),
                "subject_binding_probabilities": probabilities,
                "subject_binding_margin": round(ranked[0] - ranked[1], 4) if len(ranked) > 1 else None,
            }
    return {"run": index, "additions": sorted(set(keyed_additions)),
            "addition_precision": totals.get("addition_precision"),
            "wrong_additions": totals.get("wrong_additions"),
            "missed_expected_additions": totals.get("missed_expected_additions"),
            WATCHED_KEY: watched, "decision_on_coverage": coverage,
            "total_additions": totals.get("additions")}


def _aggregate(per_run: list[dict], provider: str) -> dict:
    margins = [run[WATCHED_KEY]["subject_binding_margin"] for run in per_run
               if run[WATCHED_KEY] and run[WATCHED_KEY]["subject_binding_margin"] is not None]
    return {"kind": "decision-value-eval-instability", "date": date.today().isoformat(), "runs": len(per_run),
            "live_service": provider,
            "aggregate": {
                f"added_{WATCHED_KEY}": sum(1 for run in per_run
                                                          if run[WATCHED_KEY] and run[WATCHED_KEY]["added"]),
                "runs": len(per_run),
                "additions_each": [len(run["additions"]) for run in per_run],
                "precision_each": [run["addition_precision"] for run in per_run],
                "subject_binding_margin_min": min(margins) if margins else None,
                "subject_binding_margin_max": max(margins) if margins else None,
                "threshold": decision_model.DECISION_MARGIN,
                "margin_clears_threshold_on_every_run": bool(margins)
                and min(margins) > decision_model.DECISION_MARGIN,
            },
            "note": (f"N={len(per_run)} repeat of backend/scripts/run_decision_value_eval.py against the live "
                     f"decision model under the shipped contract. Margins are the subject-binding top "
                     f"probability minus its runner-up, recomputed from the recorded distributions, and are "
                     f"compared against DECISION_MARGIN. A margin that crosses the threshold between runs "
                     f"would mean the same input renders differently."),
            "per_run": per_run,
            "limits": ["Five runs: this measures stability, not correctness, and a stable wrong answer is still "
                       "wrong.", "The watched record is one keyed note; stability elsewhere is not measured."]}


def _check() -> int:
    if not SUMMARY.is_file():
        print(f"check: no artifact at {SUMMARY}", file=sys.stderr)
        return 1
    try:
        document = json.loads(SUMMARY.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"check: {SUMMARY.name} unreadable: {type(exc).__name__}", file=sys.stderr)
        return 1
    problems = []
    if document["runs"] != len(document["per_run"]):
        problems.append("the summary disagrees with its own per-run count")
    if document["aggregate"]["runs"] != document["runs"]:
        problems.append("the aggregate disagrees with the run count")
    observed = [run[WATCHED_KEY]["subject_binding_margin"] for run in document["per_run"]
                if run[WATCHED_KEY] and run[WATCHED_KEY]["subject_binding_margin"] is not None]
    if observed and (round(min(observed), 4) != document["aggregate"]["subject_binding_margin_min"]
                     or round(max(observed), 4) != document["aggregate"]["subject_binding_margin_max"]):
        problems.append("the aggregate margins disagree with the per-run rows")
    for problem in problems:
        print(f"check: {problem}", file=sys.stderr)
    aggregate = document["aggregate"]
    print(f"check: {document['runs']} runs, additions {aggregate['additions_each']}, "
          f"precision {aggregate['precision_each']}, subject-binding margin "
          f"{aggregate['subject_binding_margin_min']}–{aggregate['subject_binding_margin_max']} "
          f"against threshold {aggregate['threshold']}")
    return 1 if problems else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Repeat the selection read and aggregate the runs")
    parser.add_argument("--check", action="store_true", help="verify the summary against its runs, no calls")
    parser.add_argument("--aggregate-only", action="store_true", help="rebuild the summary from existing runs")
    args = parser.parse_args(argv)
    if args.check:
        return _check()
    provider = decision_model._transport()  # noqa: SLF001 - the study records what it talked to
    if provider is None:
        print("run_instability_study: no decision provider is configured", file=sys.stderr)
        return 2
    DIR.mkdir(parents=True, exist_ok=True)
    if not args.aggregate_only:
        for index in range(1, RUNS + 1):
            target = DIR / f"run{index}.json"
            print(f"  run {index}/{RUNS} -> {target.name}")
            result = subprocess.run([sys.executable, str(EVAL), "--out", str(target)],
                                    capture_output=True, text=True)
            if result.returncode != 0:
                print(result.stdout[-800:], file=sys.stderr)
                print(result.stderr[-800:], file=sys.stderr)
                return result.returncode
    per_run = [_summarise_run(index, document, f"{provider.endpoint} (live)")
               for index, document in enumerate(_load_runs(), 1)]
    SUMMARY.write_text(json.dumps(_aggregate(per_run, f"{provider.endpoint} (live)"), ensure_ascii=False,
                                  indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return _check()


if __name__ == "__main__":
    raise SystemExit(main())
