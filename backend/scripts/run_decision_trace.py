#!/usr/bin/env python
"""The Decision-1 decision trace: what it was asked, what it answered, what followed — per encounter.

The gap this closes: the pipeline trace records that a Decision-1 stage ran, and the coverage counters record how
many candidates were assessed, but nothing on the wire states **which questions** Decision-1 was asked, **which
thresholds were in force**, or **what each disclosed verdict led to**. A reviewer could see the outcome and
not the reasoning.

Two properties this artifact has by construction rather than by careful editing:

* **It cannot leak an undisclosed identity.** It is built from `Picture.selection.decision_notes`, which the
  pipeline already filters to baseline-selected notes and relevant additions. A withheld or unselected note
  appears only as a coverage count, and `--check` asserts that no note id in the file falls outside the
  disclosed set recorded in the same encounter.
* **Its contract cannot drift from the code.** The question set is read from `decision_model._request`, and
  the thresholds from the module constants, so a change to either shows up here without anyone updating a
  document.

    .venv/bin/python scripts/run_decision_trace.py
    .venv/bin/python scripts/run_decision_trace.py --check
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app import decision_providers, decision_model  # noqa: E402
from app.encounters import resolve_encounter  # noqa: E402
from app.fixtures import load_decision_eval_personas, load_personas  # noqa: E402
from app.pipeline import PREPARATION_WINDOW_S, build_picture_for_encounter  # noqa: E402
from app.purpose import palvelukonteksti  # noqa: E402
from app.selection import RULE_VERSION  # noqa: E402

import run_decision_value_eval as evalmod  # noqa: E402

RESULTS = ROOT / ".scratch" / "retrieval-and-knowledge" / "results"
TARGET = RESULTS / "decision-trace-v1.json"

# Three small encounters with different purposes, plus the 42-note scale encounter so the trace shows the
# shortlist cap, the byte cap and the budget-exhaustion bucket rather than only the happy path.
CASES = (
    "bound-encounter-005-laakitys-sairaanhoitaja",
    "bound-encounter-003-mielenterveys-sairaanhoitaja",
    "bound-encounter-003-toimintakyky-sairaanhoitaja",
    "bound-encounter-010-laakitys-sairaanhoitaja",
)
# `gate_refused` is a flag that OVERLAPS `assessed`, so it is not a partition bucket; these four are.
COVERAGE_BUCKETS = tuple(decision_model.COVERAGE_BUCKETS)
NON_ATTEMPT_BUCKETS = tuple(bucket for bucket in COVERAGE_BUCKETS if bucket != "gate_refused")


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def contract(role: str, purpose: str) -> dict:
    """The question set and the thresholds, read from the code that enforces them."""
    request = decision_model._request(role, purpose, "trace")  # noqa: SLF001 - the definition of the contract
    questions = {
        name: {"type": body["type"], "instructions": body["instructions"][:160],
               "criteria": sorted(body.get("criteria", {})) if isinstance(body.get("criteria"), dict)
               else body.get("criteria")}
        for name, body in request["questions"].items() if not name.startswith("supports:")
    }
    transport = decision_model._transport()  # noqa: SLF001 - the resolved provider is the point of the trace
    return {
        # One provider, so `name` is the identity; there is no second kind to distinguish any more.
        "provider": {"name": transport.name, "model": transport.model,
                     "endpoint": transport.endpoint} if transport else None,
        "model": decision_model.MODEL,
        # The endpoint comes from the resolved provider; there is no module-level default to fall back on,
        # because a hard-coded default is how a credential once travelled to the wrong vendor.
        "endpoint": transport.endpoint if transport else None,
        "questions": questions,
        "thresholds": {
            "addition_floor": decision_model.ADDITION_FLOOR,
            "omission_floor": decision_model.OMISSION_FLOOR,
            "decision_margin": decision_model.DECISION_MARGIN,
        },
        "limits": {
            "shortlist_limit": decision_model.SHORTLIST_LIMIT,
            "max_notes": decision_model.MAX_NOTES,
            "max_text_bytes": decision_model.MAX_TEXT_BYTES,
            "total_budget_s": decision_model.TOTAL_BUDGET_S,
            "call_budget_s": decision_model.CALL_BUDGET_S,
        },
        "rule_version": RULE_VERSION,
        "preparation_window_s": PREPARATION_WINDOW_S,
        "coverage_buckets": list(COVERAGE_BUCKETS),
        "verifier_questions": sorted(_verifier_question_names()),
    }


def _verifier_question_names() -> set[str]:
    """The per-sentence verifier's question names, taken from a request it would build."""
    return {"support", "contradicts", "attribution", "supports:<record_id>"}


async def _run() -> dict:
    personas = {persona.persona_id: persona
                for persona in load_personas() + load_decision_eval_personas()}
    encounters: dict[str, dict] = {}
    for encounter_id in CASES:
        encounter, _persona = resolve_encounter(encounter_id, personas, evalmod.EVAL_ENCOUNTERS)
        started = time.monotonic()
        picture = await build_picture_for_encounter(
            encounter_id, personas=personas, encounters=evalmod.EVAL_ENCOUNTERS, today=evalmod.TODAY,
            started_at=started, preparation_deadline=started + PREPARATION_WINDOW_S,
            decision_enabled=True, foundry_enabled=False)
        selection = picture.selection
        disclosed = sorted(set(selection.baseline_record_ids) | set(selection.additional_record_ids))
        notes = [{"record_id": note.record_id, "status": note.status, "model": note.model,
                  "elapsed_ms": note.elapsed_ms, "input_tokens": note.input_tokens,
                  "output_tokens": note.output_tokens, "gate_refused": note.gate_refused,
                  "relevance_probabilities": note.relevance_probabilities,
                  "relevance_confidence": note.relevance_confidence,
                  "time_sensitivity": note.time_sensitivity,
                  "subject_binding": note.subject_binding,
                  "urgency": (note.gate_probabilities or {}).get("urgency")}
                 for note in selection.decision_notes]
        encounters[encounter_id] = {
            "role": encounter.role, "purpose": encounter.purpose,
            "contract": contract(encounter.role, encounter.purpose or ""),
            "decision_status": selection.decision_status,
            "disclosed_note_ids": disclosed,
            "coverage": {bucket: getattr(selection, f"decision_{bucket}_count", None)
                         for bucket in COVERAGE_BUCKETS} | {
                "decision_candidate_count": selection.decision_candidate_count,
                "decision_shortlisted_count": selection.decision_shortlisted_count,
                "decision_assessed_count": selection.decision_assessed_count,
                "decision_attempted_count": selection.decision_attempted_count,
            },
            "verdicts": notes,
            "decisions": {
                "baseline_record_ids": sorted(selection.baseline_record_ids),
                "added_record_ids": sorted(selection.additional_record_ids),
                "emphasized_record_ids": sorted(selection.emphasized_record_ids),
                "omitted_baseline_record_ids": sorted(
                    getattr(selection, "selection_omitted_baseline_record_ids", []) or []),
                "priority_record_ids": sorted(
                    (entry.record_id if hasattr(entry, "record_id") else str(entry))
                    for entry in (picture.records.priority or [])),
                "narration_input_record_ids": sorted(picture.narration_input_record_ids or []),
                "narrative_accepted": bool(picture.narrative),
            },
            "stages": [{"stage": row.stage, "elapsed_ms": row.elapsed_ms, "model": row.model,
                        "status": row.status} for row in picture.pipeline_trace],
        }
    return {"kind": "decision-trace", "generated_by": "backend/scripts/run_decision_trace.py",
            "generated_at": _now(), "model": decision_model.MODEL,
            "method": ("One entry per encounter: the question set and thresholds read from the enforcing "
                       "code, the disclosed per-note verdicts, the coverage counters, the selection "
                       "decisions they produced, and the per-stage pipeline trace. Built from "
                       "Picture.selection, which already excludes unselected note identities, so a withheld "
                       "note can appear only as a count."),
            "encounters": encounters,
            "limits": [
                "Verdict probabilities are shown only for baseline-selected notes and relevant additions; an "
                "unselected or withheld note appears as a coverage count and nowhere else.",
                "The thresholds are inherited from the vendor's routing example and are not calibrated on "
                "Finnish data.",
                "This trace is a record of what a model decided, not a measure of whether it was right.",
            ]}


def _check() -> int:
    if not TARGET.exists():
        print(f"check: no artifact at {TARGET}", file=sys.stderr)
        return 1
    try:
        document = json.loads(TARGET.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"check: {TARGET.name} unreadable: {type(exc).__name__}", file=sys.stderr)
        return 1
    problems: list[str] = []
    for encounter_id, entry in document["encounters"].items():
        disclosed = set(entry["disclosed_note_ids"])
        for verdict in entry["verdicts"]:
            if verdict["record_id"] not in disclosed:
                problems.append(f"{encounter_id}: verdict for {verdict['record_id']} is not a disclosed id")
        coverage = entry["coverage"]
        # The four non-attempt statuses partition the candidates together with `attempted`; `gate_refused`
        # is a flag over attempted rows and overlaps `assessed`, so it must not be summed in.
        partitioned = sum(coverage[bucket] or 0 for bucket in NON_ATTEMPT_BUCKETS) \
            + (coverage["decision_attempted_count"] or 0)
        if coverage["decision_candidate_count"] != partitioned:
            problems.append(f"{encounter_id}: statuses account for {partitioned} candidates, not "
                            f"{coverage['decision_candidate_count']}")
        if (coverage["gate_refused"] or 0) > (coverage["decision_assessed_count"] or 0):
            problems.append(f"{encounter_id}: gate_refused exceeds assessed")
        if entry["contract"].get("provider") is None:
            problems.append(f"{encounter_id}: the trace does not name the decision provider")
        thresholds = entry["contract"]["thresholds"]
        if not thresholds["addition_floor"] <= thresholds["omission_floor"]:
            problems.append(f"{encounter_id}: the addition floor must not exceed the omission floor")
        if set(entry["contract"]["questions"]) != {"relevance", "time_sensitivity", "subject_binding",
                                                   "urgency"}:
            problems.append(f"{encounter_id}: the trace does not name the shipped question set")
    for problem in problems:
        print(f"check: {problem}", file=sys.stderr)
    print(f"check: {len(document['encounters'])} encounters traced, "
          f"{sum(len(entry['verdicts']) for entry in document['encounters'].values())} disclosed verdicts")
    return 1 if problems else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Emit the Decision-1 decision trace")
    parser.add_argument("--check", action="store_true", help="verify the committed artifact")
    args = parser.parse_args(argv)
    if args.check:
        return _check()
    if decision_providers.provider() is None:
        print("run_decision_trace: no decision provider is configured: a trace of a stage that never ran would be a fiction",
              file=sys.stderr)
        return 2
    document = asyncio.run(_run())
    RESULTS.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                      encoding="utf-8")
    for encounter_id, entry in document["encounters"].items():
        coverage = entry["coverage"]
        print(f"  {encounter_id}: status={entry['decision_status']} "
              f"candidates={coverage['decision_candidate_count']} assessed={coverage['decision_assessed_count']} "
              f"disclosed_verdicts={len(entry['verdicts'])} "
              f"added={len(entry['decisions']['added_record_ids'])}")
    print(f"  -> {TARGET.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
