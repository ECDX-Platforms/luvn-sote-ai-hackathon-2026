#!/usr/bin/env python
"""Structured-fact disagreement (Phase 4 item 12) for the Decision-1 additions and emphases.

For every addition and emphasis the production run produced, this pairs each authored free-text claim
with the structured record named in the pre-registered table and classifies the pair:

    agreement        the structured value supports the claim
    contradiction    the structured value conflicts with it
    absent           no structured record exists for that fact, person and window
    not-comparable   a record exists but measures a different construct, unit or window

Pairing is authored, never inferred from text similarity: a claim is compared only with the record ID
named in `PAIRS`. A produced addition or emphasis with no authored pair is reported as
`not-comparable` with reason `no authored pairing`, so the denominator always includes it.

Published disagreement is `contradiction + absent + not-comparable`, with its denominator and the
per-pair listing. The structured records are not independent clinical truth, and the fixtures and
answer keys share an author, so this measures agreement between authored artefacts -- not accuracy.
Note text is never copied; claim labels are authored paraphrases.

    .venv/bin/python scripts/run_structured_fact_disagreement.py
    .venv/bin/python scripts/run_structured_fact_disagreement.py --check
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app import assess, source  # noqa: E402
from app.fixtures import load_decision_eval_personas, load_personas  # noqa: E402
from app.upm import Record  # noqa: E402

SOURCE = ROOT / ".scratch" / "hackathon-readiness" / "results" / "decision-value-eval-cap80-v1.json"
TARGET = ROOT / ".scratch" / "hackathon-readiness" / "results" / "decision-structured-fact-disagreement-v1.json"

WINDOW_DAYS = 365
CLASSES = ("agreement", "contradiction", "absent", "not-comparable")

LIMITS = [
    "fixtures, notes and answer keys share an author, so this is authored-artefact agreement, NOT "
    "accuracy and NOT independent validation",
    "synthetic fixtures only; no real source integration and no clinical validation",
    "pairing is authored per claim; text or value similarity never creates a link",
    "a claim with no authored pair is reported not-comparable rather than dropped, so the "
    "denominator always includes the whole produced population",
    "disagreement is the published quantity; nothing here is an error rate for the note or the model",
]

# Authored pairings, from the pre-registered table in
# .scratch/challenge-1-1-prioritized/issues/05-phase-4-experiments.md.
# `record=None` means the pre-registered expectation is that no such record exists for that person;
# the script verifies that absence instead of assuming it.
PAIRS: dict[str, list[dict]] = {
    "bound-note-011": [
        {"claim": "regular tablet missed on several days", "kind": "adherence_vs_missed_doses",
         "record": "bound-med-005"},
        {"claim": "dizziness and fatigue", "kind": "symptom_vs_measurement", "record": None},
    ],
    "bound-note-003": [
        {"claim": "worsened sleep, mood and fatigue", "kind": "symptom_vs_instrument_items",
         "record": "bound-arviointi-003", "items": ["q2", "q3", "q4"]},
        {"claim": "no self-harm thoughts", "kind": "absence_claim_vs_instrument_item",
         "record": "bound-arviointi-003", "items": ["q9"]},
    ],
    "bound-note-010": [
        # No IADL exists for this person; the PHQ-9 concentration item is a different construct.
        {"claim": "difficulty managing affairs and starting daily tasks", "kind": "functioning_vs_iadl",
         "record": None},
        {"claim": "difficulty managing affairs and starting daily tasks", "kind": "functioning_vs_iadl",
         "record": "bound-arviointi-003"},
    ],
    "bound-note-017": [
        {"claim": "dizziness, tablet left untaken three times", "kind": "adherence_vs_missed_doses",
         "record": None},
    ],
    "bound-note-scale-041": [
        {"claim": "evening tablet left untaken on two consecutive days",
         "kind": "adherence_vs_missed_doses", "record": None},
    ],
}


class PairingError(RuntimeError):
    """The authored pairing does not match the fixtures; the classification must not be published."""


def _personas() -> list:
    return load_personas() + load_decision_eval_personas()


def _note(note_id: str) -> tuple[Record, str]:
    """The note and its persona ID; a note that is in zero or several personas is a broken pairing."""
    hits = [(persona.persona_id, record) for persona in _personas()
            for record in persona.records if record.record_id == note_id]
    if len(hits) != 1:
        raise PairingError(f"{note_id} belongs to {len(hits)} personas; the pairing is ambiguous")
    persona_id, record = hits[0]
    return record, persona_id


def _structured(persona_id: str) -> list[Record]:
    return [record for record in next(persona for persona in _personas()
                                      if persona.persona_id == persona_id).records
            if record.record_type != source.FREE_TEXT]


def _in_window(note: Record, record: Record) -> bool:
    return 0 <= (note.recorded_at.date() - record.recorded_at.date()).days <= WINDOW_DAYS


def _absence_holds(kind: str, structured: list[Record]) -> bool:
    """True when the pre-registered "no such record" expectation actually holds for this person."""
    if kind == "adherence_vs_missed_doses":
        return not any(record.record_type == source.MEDICATION for record in structured)
    if kind == "symptom_vs_measurement":
        return not any(record.record_type == source.MEASUREMENT for record in structured)
    if kind == "functioning_vs_iadl":
        return not any(source.assessment_instrument(record) == "IADL" for record in structured)
    raise PairingError(f"unknown claim kind {kind!r}")


def _classify(pair: dict, note: Record, persona_id: str) -> dict:
    kind, record_id = pair["kind"], pair.get("record")
    structured = _structured(persona_id)
    if record_id is None:
        # The pre-registered expectation is "no such record exists". Verify it, so a mis-specified
        # pair fails loudly instead of publishing a fabricated absence.
        if not _absence_holds(kind, structured):
            raise PairingError(f"{note.record_id}/{kind}: the paired record type does exist, so the "
                               f"authored pairing says 'absent' where a pair was possible")
        return {"pair": pair, "classification": "absent", "reason": "no structured record exists"}

    record = next((row for row in structured if row.record_id == record_id), None)
    if record is None:
        return {"pair": pair, "classification": "absent", "reason": "named record not on this person"}
    if not _in_window(note, record):
        return {"pair": pair, "classification": "not-comparable", "reason": "outside the pairing window"}

    payload = record.payload
    if kind == "adherence_vs_missed_doses":
        if record.record_type != source.MEDICATION:
            raise PairingError(f"{record_id} is not a medication record")
        missed = payload.get("puuttuneet_annokset_30d")
        if not isinstance(missed, int) or isinstance(missed, bool):
            return {"pair": pair, "classification": "not-comparable", "reason": "no missed-dose count"}
        return {"pair": pair, "classification": "agreement" if missed >= 1 else "contradiction",
                "evidence": {"record_id": record_id, "field": "puuttuneet_annokset_30d", "value": missed}}
    if kind in {"symptom_vs_instrument_items", "absence_claim_vs_instrument_item"}:
        answers = source.assessment_answers(record)
        items = pair["items"]
        scores = {item: answers.get(item) for item in items}
        present = {item: score for item, score in scores.items() if score is not None}
        if not present:
            return {"pair": pair, "classification": "absent", "reason": "paired items not scored"}
        if kind == "symptom_vs_instrument_items":
            raised = any(score >= 1 for score in present.values())
            return {"pair": pair,
                    "classification": "agreement" if raised else "contradiction",
                    "evidence": {"record_id": record_id, "field": "vastaukset", "items": present}}
        scored = present[items[0]]
        return {"pair": pair, "classification": "agreement" if scored == 0 else "contradiction",
                "evidence": {"record_id": record_id, "field": "vastaukset", "items": present}}
    if kind == "functioning_vs_iadl":
        instrument = source.assessment_instrument(record)
        if instrument != "IADL":
            return {"pair": pair, "classification": "not-comparable",
                    "reason": f"paired instrument is {instrument}, a different construct",
                    "evidence": {"record_id": record_id, "field": "instrumentti", "value": instrument}}
        result = assess.score(record)
        return {"pair": pair, "classification": "agreement" if not result.valmis else "contradiction",
                "evidence": {"record_id": record_id, "field": "valmis", "value": result.valmis}}
    if kind == "symptom_vs_measurement":
        return {"pair": pair, "classification": "not-comparable",
                "reason": "measurement exists but was not declared for this claim",
                "evidence": {"record_id": record_id}}
    raise PairingError(f"unknown claim kind {kind!r}")


def _population(source_doc: dict) -> list[str]:
    produced: list[str] = []
    for record in source_doc["results"].values():
        produced += list(record["decision_on"]["additional_record_ids"])
        produced += list(record["decision_on"]["emphasized_record_ids"])
    return sorted(set(produced))


def run() -> dict:
    try:
        source_doc = json.loads(SOURCE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise PairingError(f"cannot read {SOURCE.name}: {type(error).__name__}") from error

    population = _population(source_doc)
    entries = []
    for note_id in population:
        note, persona_id = _note(note_id)
        pairs = PAIRS.get(note_id)
        if not pairs:
            entries.append({"record_id": note_id, "persona_id": persona_id, "classification": "not-comparable",
                            "reason": "no authored pairing",
                            "as": ("addition" if any(note_id in record["decision_on"]["additional_record_ids"]
                                                     for record in source_doc["results"].values())
                                   else "emphasis")})
            continue
        for pair in pairs:
            classified = _classify(pair, note, persona_id)
            entries.append({"record_id": note_id, "persona_id": persona_id,
                            "claim": pair["claim"], "kind": pair["kind"],
                            "paired_record_id": pair.get("record"),
                            "classification": classified["classification"],
                            "reason": classified.get("reason"),
                            "evidence": classified.get("evidence")})

    counts = {name: sum(1 for entry in entries if entry["classification"] == name) for name in CLASSES}
    disagreements = sum(counts[name] for name in ("contradiction", "absent", "not-comparable"))
    return {
        "kind": "decision-structured-fact-disagreement",
        "date": "2026-10-06",
        "source_artifact": SOURCE.name,
        "population": {"addition_or_emphasis_ids": population,
                       "pairs": len(entries)},
        "classes": list(CLASSES),
        "counts": {**counts, "disagreement": disagreements, "pairs": len(entries),
                   "disagreement_denominator": len(entries)},
        "limits": LIMITS,
        "entries": entries,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=str(TARGET))
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    report = run()
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    target = Path(args.out)
    if args.check:
        if not target.is_file() or target.read_text(encoding="utf-8") != rendered:
            raise SystemExit(f"{target.name} does not match the replay; re-run without --check")
        print(f"{target.name} matches the replayed classification")
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(rendered, encoding="utf-8")
    for entry in report["entries"]:
        print(f"{entry['record_id']:26s} {entry['classification']:15s} "
              f"{(entry.get('kind') or '-')} -> {(entry.get('paired_record_id') or 'no record')}"
              f"{' | ' + entry['reason'] if entry.get('reason') else ''}")
    print("counts:", json.dumps(report["counts"]))
    print("wrote", target)


if __name__ == "__main__":
    main()
