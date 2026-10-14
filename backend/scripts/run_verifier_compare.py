#!/usr/bin/env python
"""Can Microsoft-Decision-1 also be the grounding verifier? The second half of dropping Decision-1.

Decision-1 has two jobs in this product: it selects (the relevance fan-out) and it verifies (per sentence,
against only the records that sentence cites). Dropping it needs *both* to work, so this runs the shipped
verifier question set — `support`, `contradicts`, `attribution`, and one `supports:<record_id>` question per
cited record — through each provider over the same live narration, and reports:

* each provider's verdict per sentence, from the evaluator's own simpler rule;
* agreement between the two providers, and each provider against the **runtime verifier**'s verdict, which
  is the shipped contract's own answer;
* latency and tokens.

The narration itself is generated once, by the shipped path, so both providers judge identical text.

    .venv/bin/python scripts/run_verifier_compare.py
    .venv/bin/python scripts/run_verifier_compare.py --check
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app import decision_providers, decision_evals as ev  # noqa: E402
from app.encounters import resolve_encounter  # noqa: E402
from app.fixtures import load_decision_eval_personas, load_personas  # noqa: E402
from app.policy import cut_for_encounter  # noqa: E402

import run_decision_eval as e1  # noqa: E402
import run_decision_value_eval as evalmod  # noqa: E402

RESULTS = ROOT / ".scratch" / "retrieval-and-knowledge" / "results"
TARGET = RESULTS / "decision-verifier-compare-v1.json"
TIMEOUT_S = 30.0


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


async def _run() -> dict:
    available = decision_providers.providers()
    personas = {persona.persona_id: persona
                for persona in load_personas() + load_decision_eval_personas()}
    per_provider: dict[str, dict] = {
        provider.name: {"sentences": 0, "supported": 0, "unsupported": 0, "unknown": 0,
                        "latencies_ms": [], "input_tokens": 0, "output_tokens": 0, "unavailable": 0}
        for provider in available}
    verifier_agreement = {provider.name: {"compared": 0, "agreed": 0, "disagreements": []}
                          for provider in available}
    rows: list[dict] = []
    for encounter_id in e1._inventory():
        encounter, persona = resolve_encounter(encounter_id, personas, evalmod.EVAL_ENCOUNTERS)
        permitted = {record.record_id: record for record in cut_for_encounter(persona, encounter).records}
        picture, captured, _elapsed = await e1._picture(encounter_id, personas, with_narration=True)
        sentences = e1._grounding_sentences(captured, permitted)
        if not sentences:
            continue
        runtime = {verdict["sentence_id"]: verdict["verdict"]
                   for verdict in (captured.get("verifier") or {}).get("verdicts", [])}
        entry: dict = {"encounter_id": encounter_id, "sentences": len(sentences), "providers": {},
                       "runtime_verdicts": runtime}
        for provider in available:
            started = time.monotonic()
            judged = await ev.judge_grounding(encounter.role, encounter.purpose or "", sentences,
                                              timeout_s=TIMEOUT_S, call=provider.call)
            per_sentence_ms = max(0, round((time.monotonic() - started) * 1000 / max(1, len(sentences))))
            row = ev.grounding_row(judged["rows"])
            entry["providers"][provider.name] = {"verdicts": row["verdicts"], "supported": row["supported"],
                                                 "contradictions": row["contradictions"],
                                                 "score": row["score"], "passed": row["passed"]}
            summary = per_provider[provider.name]
            # Count the verdicts actually formed, not the rows submitted: a duplicate sentence id in one
            # encounter would otherwise make the totals disagree with themselves.
            summary["sentences"] += len(row["verdicts"])
            summary["supported"] += len(row["supported"])
            summary["unsupported"] += len(row["contradictions"])
            summary["unknown"] += sum(1 for verdict in row["verdicts"].values() if verdict == "unknown")
            summary["latencies_ms"].extend([per_sentence_ms] * len(judged["rows"]))
            summary["input_tokens"] += int(judged["usage"].get("input_tokens") or 0)
            summary["output_tokens"] += int(judged["usage"].get("output_tokens") or 0)
            for sentence_id, evaluator_verdict in row["verdicts"].items():
                if sentence_id not in runtime:
                    continue
                agreement = verifier_agreement[provider.name]
                agreement["compared"] += 1
                if (evaluator_verdict == "supported") == (runtime[sentence_id] == "supported"):
                    agreement["agreed"] += 1
                else:
                    agreement["disagreements"].append(
                        {"encounter_id": encounter_id, "sentence_id": sentence_id,
                         "evaluator": evaluator_verdict, "runtime": runtime[sentence_id]})
        rows.append(entry)
    cross: dict = {"compared": 0, "agreed": 0, "disagreements": []}
    if len(available) >= 2:
        left, right = (provider.name for provider in available[:2])
        for entry in rows:
            a = entry["providers"][left]["verdicts"]
            b = entry["providers"][right]["verdicts"]
            for sentence_id, verdict in a.items():
                if sentence_id not in b:
                    continue
                cross["compared"] += 1
                if (verdict == "supported") == (b[sentence_id] == "supported"):
                    cross["agreed"] += 1
                else:
                    cross["disagreements"].append({"encounter_id": entry["encounter_id"],
                                                   "sentence_id": sentence_id,
                                                   left: verdict, right: b[sentence_id]})
    for provider in available:
        summary = per_provider[provider.name]
        summary["latency_p50_ms"] = int(statistics.median(summary["latencies_ms"])) if summary["latencies_ms"] else None
        summary.pop("latencies_ms", None)
        summary["status"] = "live"
    return {"kind": "decision-verifier-compare", "generated_by": "backend/scripts/run_verifier_compare.py",
            "generated_at": _now(),
            "providers": [{"name": provider.name, "endpoint": provider.endpoint, "model": provider.model}
                          for provider in available],
            "method": ("The shipped narration runs once per encounter; both providers then judge the same "
                       "accepted sentences with the shipped verifier question set, and each is compared to "
                       "the runtime verifier's own verdict. Grounding here is the evaluator's simpler rule, "
                       "not the runtime's exact rule, so agreement is informative rather than tautological."),
            "results": rows, "totals": per_provider, "runtime_agreement": verifier_agreement,
            "cross_provider_agreement": cross if cross["compared"] else None,
            "limits": [
                "Narration is generated by the shipped path, so this measures the verifier, not the writer.",
                "Agreement with the runtime verifier is a consistency check between two rules, not accuracy: "
                "neither is a gold standard for whether a sentence is true.",
                "Small set: the encounters that produced accepted prose.",
            ]}


def _check() -> int:
    if not TARGET.exists():
        print(f"check: no artifact at {TARGET}", file=sys.stderr)
        return 1
    document = json.loads(TARGET.read_text(encoding="utf-8"))
    problems: list[str] = []
    if len(document["providers"]) < 2:
        problems.append("a comparison needs at least two providers")
    total = sum(summary["sentences"] for summary in document["totals"].values())
    if total == 0:
        problems.append("no sentence was judged, so the artifact proves nothing")
    for name, summary in document["totals"].items():
        counted = summary["supported"] + summary["unsupported"] + summary["unknown"]
        if counted != summary["sentences"]:
            problems.append(f"{name}: verdicts ({counted}) do not add up to sentences ({summary['sentences']})")
    for problem in problems:
        print(f"check: {problem}", file=sys.stderr)
    for name, summary in sorted(document["totals"].items()):
        agreement = document["runtime_agreement"][name]
        print(f"check: {name:24s} sentences={summary['sentences']} supported={summary['supported']} "
              f"unsupported={summary['unsupported']} unknown={summary['unknown']} "
              f"runtime_agreement={agreement['agreed']}/{agreement['compared']} p50={summary['latency_p50_ms']}ms")
    cross = document.get("cross_provider_agreement")
    if cross:
        print(f"check: providers agree {cross['agreed']}/{cross['compared']}")
    return 1 if problems else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compare verifier providers on the same narration")
    parser.add_argument("--check", action="store_true", help="verify the committed artifact")
    args = parser.parse_args(argv)
    if args.check:
        return _check()
    if not os.environ.get("LUNA_FOUNDRY_KEY", "").strip():
        print("run_verifier_compare: LUNA_FOUNDRY_KEY is required for the narration run", file=sys.stderr)
        return 2
    document = asyncio.run(_run())
    RESULTS.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                      encoding="utf-8")
    return _check()


if __name__ == "__main__":
    raise SystemExit(main())
