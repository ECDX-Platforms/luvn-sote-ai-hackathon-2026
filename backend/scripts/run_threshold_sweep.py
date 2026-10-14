#!/usr/bin/env python
"""Can the gate be tightened for free? A threshold sweep over recorded Decision-1 answers.

The recorded fan-out artifact stores, per candidate, the relevance **choice**, the probability of that
choice (which is P(relevant) whenever the choice is `relevant`) and the decision **margin**. That is exactly
what the gate reads, so the operating point can be re-derived at any floor and margin without a single new
model call — which is what makes this a calibration rather than another opinion.

The question it answers: the shipped floors (0.6/0.7, margin 0.25) were inherited and are not calibrated on
Finnish data. Decision-1 separates the classes far more sharply than the model they were inherited for, so
there may be a **higher** floor and margin that keep every expected-relevant note and refuse everything the
authored key refuses. A point that is strictly safer at no cost to recall is worth taking; one that trades
recall is not, and this reports both.

    .venv/bin/python scripts/run_threshold_sweep.py
    .venv/bin/python scripts/run_threshold_sweep.py --check
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RESULTS = ROOT / ".scratch" / "retrieval-and-knowledge" / "results"
SOURCE = RESULTS / "decision-fanout-compare-v1.json"
TARGET = RESULTS / "threshold-sweep-v1.json"
FLOORS = (0.6, 0.7, 0.8, 0.9, 0.95, 0.98, 0.99)
MARGINS = (0.0, 0.1, 0.25, 0.5)


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _candidates(document: dict, provider: str) -> list[dict]:
    rows: list[dict] = []
    for encounter_id, encounter in document["results"].items():
        key = encounter["authored_key"]
        for record_id, entry in encounter["providers"].get(provider, {}).items():
            if entry.get("status") != "ok":
                continue
            rows.append({"encounter_id": encounter_id, "record_id": record_id,
                         "authored_relevant": key.get(record_id),
                         "choice": entry.get("relevance"),
                         "probability": entry.get("relevance_probability"),
                         "margin": entry.get("margin")})
    return rows


def _score(rows: list[dict], floor: float, margin: float) -> dict:
    additions = {row["record_id"] for row in rows
                 if row["choice"] == "relevant" and (row["probability"] or 0) >= floor
                 and (row["margin"] or 0) >= margin}
    wrong = sorted(row["record_id"] for row in rows
                   if row["authored_relevant"] is False and row["record_id"] in additions)
    missed = sorted(row["record_id"] for row in rows
                    if row["authored_relevant"] is True and row["record_id"] not in additions)
    expected = sum(1 for row in rows if row["authored_relevant"] is True)
    return {"floor": floor, "margin": margin, "additions": len(additions), "expected_relevant": expected,
            "wrong_additions": len(wrong), "missed_expected_additions": len(missed),
            "recall": (round((expected - len(missed)) / expected, 4) if expected else None),
            "safe": not wrong and not missed}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sweep the gate thresholds over recorded answers")
    parser.add_argument("--check", action="store_true", help="verify the committed artifact")
    args = parser.parse_args(argv)
    if args.check:
        if not TARGET.exists():
            print(f"check: no artifact at {TARGET}", file=sys.stderr)
            return 1
        document = json.loads(TARGET.read_text(encoding="utf-8"))
        problems = []
        if not document.get("safe_points"):
            problems.append("no safe operating point was found, which contradicts the recorded run")
        if document.get("shipped_point") not in document["safe_points"]:
            problems.append("the shipped operating point is not among the safe points")
        for problem in problems:
            print(f"check: {problem}", file=sys.stderr)
        print(f"check: {len(document['grid'])} points swept, safe points: {document['safe_points']}")
        return 1 if problems else 0
    if not SOURCE.exists():
        print(f"run_threshold_sweep: no recorded answers at {SOURCE}", file=sys.stderr)
        return 2
    document = json.loads(SOURCE.read_text(encoding="utf-8"))
    provider = next(provider["name"] for provider in document["providers"]
                    if provider["name"] == "microsoft-decision-1")
    rows = _candidates(document, provider)
    if not rows:
        print("run_threshold_sweep: the artifact holds no scored candidates", file=sys.stderr)
        return 2
    grid = [_score(rows, floor, margin) for floor in FLOORS for margin in MARGINS]
    safe = [point for point in grid if point["safe"]]
    shipped = _score(rows, 0.6, 0.25)
    tightest = max((point["floor"] for point in safe), default=None)
    result = {"kind": "threshold-sweep", "generated_by": "backend/scripts/run_threshold_sweep.py",
              "generated_at": _now(), "source": SOURCE.name, "provider": provider,
              "candidates": len(rows),
              "method": ("The gate's own inputs — the relevance choice, its probability and the decision "
                         "margin — are re-read from the recorded answers and re-scored at each floor and "
                         "margin. No model is called, so every point is compared on identical answers."),
              "shipped_point": {"floor": 0.6, "margin": 0.25},
              "shipped_result": shipped, "grid": grid,
              "safe_points": [{"floor": point["floor"], "margin": point["margin"]} for point in safe],
              "tightest_safe_floor": tightest,
              "reading": (f"Every floor up to {tightest} keeps all {shipped['expected_relevant']} "
                          f"expected-relevant notes and refuses every authored non-addition, so the gate can "
                          f"be tightened to {tightest} for free. Higher floors start trading recall."
                          if tightest is not None else
                          "No tested point keeps recall and safety at once; the shipped point is not dominated."),
              "limits": ["Nine scored notes: the sweep finds the tightest point that is safe *on this "
                         "evidence*, which is a floor to calibrate from, not a calibrated threshold.",
                         "The margin is the gate's own relevance margin, not a two-sided interval."]}
    TARGET.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                      encoding="utf-8")
    print(f"  shipped (0.60/0.25): {shipped['additions']} additions, "
          f"wrong={shipped['wrong_additions']} missed={shipped['missed_expected_additions']}")
    for point in safe:
        print(f"  safe: floor={point['floor']} margin={point['margin']} additions={point['additions']} "
              f"recall={point['recall']}")
    print(f"  tightest safe floor: {tightest}")
    print(f"  -> {TARGET.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
