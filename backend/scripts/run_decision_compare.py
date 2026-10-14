#!/usr/bin/env python
"""Microsoft-Decision-1 vs Decision-1 on LUVN's own frozen inventory — same questions, thresholds and scoring.

The comparison is fair by construction rather than by care:

* **Same candidates.** The judged set is the *authored* candidate set — the record ids the evaluation keys
  name, intersected with the permitted free-text notes for that encounter. Neither provider influences which
  notes are judged, and the pipeline's own Decision-1 stage is not run at all.
* **Same questions, thresholds and validators.** Both providers receive `decision_evals`' question sets and
  are scored by `selection_row`. A difference in the numbers is a difference in the models.
* **Same scoring rule for both.** Agreement, wrong additions and missed expected additions are counted
  against the authored keys, and every failure mode is named (`no_answer`, invalid response, HTTP error).

Honest limits, stated up front: the authored key covers **nine scored notes** across twelve encounters, the
keys are author-authored and unadjudicated, and the thresholds are inherited rather than calibrated. This is
a model comparison on a small fixture set, not a validation. Decision-1 is English-first and these notes are
Finnish, which is a stated vendor limitation and cuts against Decision-1 here.

    .venv/bin/python scripts/run_decision_compare.py
    .venv/bin/python scripts/run_decision_compare.py --check
"""
from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app import decision_evals as ev, decision_model, decision_providers, source  # noqa: E402
from app.encounters import resolve_encounter  # noqa: E402
from app.fixtures import load_decision_eval_personas, load_personas  # noqa: E402
from app.policy import cut_for_encounter  # noqa: E402
from app.purpose import palvelukonteksti  # noqa: E402

import run_decision_eval as e1  # noqa: E402
import run_decision_value_eval as evalmod  # noqa: E402

RESULTS = ROOT / ".scratch" / "retrieval-and-knowledge" / "results"
TARGETS = {"single": RESULTS / "decision-model-compare-v1.json",
           "fanout": RESULTS / "decision-fanout-compare-v1.json"}
# One judge call can carry several questions, but the transport caps the body; these are the same limits the
# evaluation rig uses.
TIMEOUT_S = 30.0


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _candidates(permitted: dict, expected: dict) -> list[tuple[str, str]]:
    """The authored candidate set: the ids the key names, restricted to permitted free-text notes."""
    ids: set[str] = set()
    for field in e1.AUTHORED_FIELDS:
        ids.update(expected.get(field, []))
    rows: list[tuple[str, str]] = []
    for record_id in sorted(ids):
        record = permitted.get(record_id)
        if record is None:
            continue
        text = source.note_text(record).strip()
        if text:
            rows.append((record_id, text))
    return rows


async def _judge(provider, prefix, candidates) -> dict:
    """One batched call, timed, with the failure mode named rather than raised."""
    started = time.monotonic()
    try:
        data = await ev.judge_selection(prefix, candidates, timeout_s=TIMEOUT_S, call=provider.call)
        elapsed = max(0, round((time.monotonic() - started) * 1000))
        return {"status": "ok", "answers": data["answers"], "usage": data["usage"], "elapsed_ms": elapsed}
    except Exception as error:  # noqa: BLE001 - a provider failure is a result to record, not a crash
        elapsed = max(0, round((time.monotonic() - started) * 1000))
        return {"status": type(error).__name__, "answers": {}, "usage": {}, "elapsed_ms": elapsed,
                "detail": str(error)[:200]}


async def _run() -> dict:
    available = decision_providers.providers()
    if not available:
        raise SystemExit("no decision provider reachable: set LUNA_FOUNDRY_KEY")
    personas = {persona.persona_id: persona
                for persona in load_personas() + load_decision_eval_personas()}
    inventory = e1._inventory()
    results: dict[str, dict] = {}
    per_provider: dict[str, dict] = {
        provider.name: {"encounters": 0, "calls": 0, "keyed": 0, "agreed": 0, "wrong_additions": 0,
                        "missed_expected_additions": 0, "verdict_flips": 0, "max_probability_delta": 0.0,
                        "invalid_responses": 0, "latencies_ms": [], "input_tokens": 0, "output_tokens": 0,
                        "scores": []}
        for provider in available}

    for encounter_id in inventory:
        encounter, persona = resolve_encounter(encounter_id, personas, evalmod.EVAL_ENCOUNTERS)
        permitted = {record.record_id: record for record in cut_for_encounter(persona, encounter).records}
        expected = evalmod.EXPECTED.get(encounter_id, {})
        candidates = _candidates(permitted, expected)
        if not candidates:
            results[encounter_id] = {"candidates": [], "providers": {}}
            continue
        judged_ids = {record_id for record_id, _ in candidates}
        keyed = {record_id: value for record_id, value in e1._authored_key(expected).items()
                 if record_id in judged_ids}
        description = palvelukonteksti(encounter.purpose).description if encounter.purpose else ""
        prefix = ev.selection_state(encounter.role, encounter.purpose or "", description)
        rows: dict[str, dict] = {}
        for provider in available:
            first = await _judge(provider, prefix, candidates)
            second = await _judge(provider, prefix, candidates)
            row = ev.selection_row(first["answers"], keyed)
            flips: list[str] = []
            max_delta = 0.0
            for record_id in first["answers"]:
                try:
                    left = first["answers"][record_id]["noul"]
                    right = second["answers"].get(record_id, {}).get("noul")
                except (KeyError, TypeError):
                    continue
                if isinstance(left, (int, float)) and isinstance(right, (int, float)):
                    max_delta = max(max_delta, abs(left - right))
                    if (left >= ev.SELECTION_FLOOR) != (right >= ev.SELECTION_FLOOR):
                        flips.append(record_id)
            invalid = sum(1 for status in (first["status"], second["status"]) if status != "ok")
            rows[provider.name] = {"status": first["status"], "score": row["score"], "passed": row["passed"],
                                   "judged": row["judged"], "keyed": row["keyed"],
                                   "wrong_additions": row["wrong_additions"],
                                   "missed_expected_additions": row["missed_expected_additions"],
                                   "probabilities": row["probabilities"],
                                   "verdict_flips": sorted(flips),
                                   "max_probability_delta": round(max_delta, 4),
                                   "elapsed_ms": first["elapsed_ms"],
                                   "usage": first["usage"],
                                   "detail": first.get("detail")}
            summary = per_provider[provider.name]
            summary["encounters"] += 1
            summary["calls"] += 1
            summary["keyed"] += len(row["keyed"])
            summary["agreed"] += (len(row["keyed"]) - len(row["wrong_additions"])
                                  - len(row["missed_expected_additions"]))
            summary["wrong_additions"] += len(row["wrong_additions"])
            summary["missed_expected_additions"] += len(row["missed_expected_additions"])
            summary["verdict_flips"] += len(flips)
            summary["max_probability_delta"] = max(summary["max_probability_delta"], max_delta)
            summary["invalid_responses"] += invalid
            summary["latencies_ms"].append(first["elapsed_ms"])
            summary["input_tokens"] += int(first["usage"].get("input_tokens") or 0)
            summary["output_tokens"] += int(first["usage"].get("output_tokens") or 0)
            summary["scores"].append(row["score"])
        results[encounter_id] = {"role": encounter.role, "purpose": encounter.purpose,
                                 "candidates": [record_id for record_id, _ in candidates],
                                 "keyed": keyed, "providers": rows}

    for provider in available:
        summary = per_provider[provider.name]
        latencies = summary.pop("latencies_ms")
        scores = summary.pop("scores")
        summary["agreement"] = (summary["agreed"] / summary["keyed"]) if summary["keyed"] else None
        summary["mean_score"] = (statistics.fmean(scores) if scores else None)
        summary["latency_p50_ms"] = int(statistics.median(latencies)) if latencies else None
        summary["latency_p95_ms"] = (int(sorted(latencies)[max(0, int(len(latencies) * 0.95) - 1)])
                                     if latencies else None)
        summary["estimated_input_cost_usd"] = (round(summary["input_tokens"] * provider.input_usd_per_million
                                                     / 1_000_000, 6)
                                               if provider.input_usd_per_million is not None else None)

    return {"kind": "decision-model-compare", "generated_by": "backend/scripts/run_decision_compare.py",
            "generated_at": _now(),
            "providers": [{"name": provider.name, "endpoint": provider.endpoint, "model": provider.model,
                           "input_usd_per_million": provider.input_usd_per_million}
                          for provider in available],
            "inventory": {"encounters": len(inventory),
                          "scored_notes": per_provider[available[0].name]["keyed"]},
            "method": ("Both providers answer the same authored question set over the same authored "
                       "candidate set, and both are scored by the same rule against the same keys. The "
                       "pipeline's own selector is not run, so neither provider influences which notes are "
                       "judged. Determinism is one repeated call per encounter, compared at the threshold."),
            "floors": {"selection": ev.SELECTION_FLOOR, "grounding": ev.GROUNDING_FLOOR},
            "results": results, "totals": per_provider,
            "limits": [
                "The authored key covers nine scored notes across twelve encounters; it is author-authored "
                "and unadjudicated, and the thresholds are inherited rather than calibrated. This compares "
                "two models on a small fixture set; it does not validate either.",
                "Decision-1 is English-first and these notes are Finnish — a stated vendor limitation that cuts "
                "against Decision-1 in this comparison.",
                "Determinism is one repeated call per encounter, not a distribution.",
                "Decision-1 pricing was not published on the documentation page read, so its cost is left "
                "unknown rather than estimated.",
            ]}


async def _run_fanout() -> dict:
    """The production contract: the four-question fan-out per candidate, through each provider.

    The single-question mode above cannot distinguish the models properly, because both make the same two
    errors on it. This mode runs what the shipped pipeline actually asks — relevance, time-sensitivity,
    subject-binding and an urgency Score in one call per note — and applies the shipped gate
    (`classify_status`) to both providers, so the comparison is against the same contract the product uses.
    """
    available = decision_providers.providers()
    personas = {persona.persona_id: persona
                for persona in load_personas() + load_decision_eval_personas()}
    results: dict[str, dict] = {}
    per_provider: dict[str, dict] = {
        provider.name: {"candidates_judged": 0, "relevant": 0, "wrong_additions": 0,
                        "missed_expected_additions": 0, "gate_refused": 0, "unavailable": 0,
                        "latencies_ms": [], "input_tokens": 0, "output_tokens": 0}
        for provider in available}
    for encounter_id in e1._inventory():
        encounter, persona = resolve_encounter(encounter_id, personas, evalmod.EVAL_ENCOUNTERS)
        permitted = {record.record_id: record for record in cut_for_encounter(persona, encounter).records}
        expected = evalmod.EXPECTED.get(encounter_id, {})
        candidates = _candidates(permitted, expected)
        if not candidates:
            continue
        keyed = e1._authored_key(expected)
        rows: dict[str, dict] = {}
        for provider in available:
            per_note: dict[str, dict] = {}
            for record_id, text in candidates:
                request = decision_model._request(encounter.role, encounter.purpose or "", text)
                answer = await _judge_single(provider, request["state"], request["questions"])
                entry: dict = {"status": answer["status"], "elapsed_ms": answer["elapsed_ms"]}
                if answer["status"] == "ok":
                    try:
                        answers = answer["answers"]
                        relevance = decision_model.choice_answer(answers["relevance"],
                                                                decision_model.RELEVANCE_ANSWERS, "relevance")
                        temporal = decision_model.choice_answer(answers["time_sensitivity"],
                                                              ("current", "historical", "unclear"),
                                                              "time sensitivity")
                        binding = decision_model.choice_answer(answers["subject_binding"],
                                                             decision_model.SUBJECT_ANSWERS, "subject binding")
                        urgency = decision_model.score_answer(answers["urgency"], decision_model.URGENCY_LEVELS,
                                                            "urgency")
                    except (KeyError, ValueError) as error:
                        entry.update({"status": "invalid_response", "detail": str(error)[:120]})
                    else:
                        status, refused, margin = decision_model.classify_status(relevance, temporal, binding)
                        entry.update({"status": "ok", "gate_status": status, "gate_refused": refused,
                                      "margin": round(margin, 4), "relevance": relevance["choice"],
                                      "relevance_probability": round(relevance["probabilities"].get(
                                          relevance["choice"], 0.0), 4),
                                      "time_sensitivity": temporal["choice"],
                                      "subject_binding": binding["choice"],
                                      "urgency_score": round(urgency["score"], 3)})
                per_note[record_id] = entry
                summary = per_provider[provider.name]
                summary["candidates_judged"] += 1
                summary["latencies_ms"].append(answer["elapsed_ms"])
                summary["input_tokens"] += int(answer["usage"].get("input_tokens") or 0)
                summary["output_tokens"] += int(answer["usage"].get("output_tokens") or 0)
                if answer["status"] != "ok":
                    summary["unavailable"] += 1
                elif entry.get("gate_status") == "relevant":
                    summary["relevant"] += 1
                    if keyed.get(record_id) == False:  # noqa: E712 - keys are True/False/None
                        summary["wrong_additions"] += 1
                elif entry.get("gate_status") == "irrelevant":
                    summary["gate_refused"] += 1
                if keyed.get(record_id) == True and entry.get("gate_status") != "relevant":  # noqa: E712
                    summary["missed_expected_additions"] += 1
            rows[provider.name] = per_note
        results[encounter_id] = {"role": encounter.role, "purpose": encounter.purpose,
                                 "candidates": [record_id for record_id, _ in candidates],
                                 "authored_key": keyed, "providers": rows}
    for provider in available:
        summary = per_provider[provider.name]
        latencies = summary.pop("latencies_ms")
        expected_relevant = sum(1 for row in results.values()
                                for record_id, value in row["authored_key"].items() if value == True  # noqa: E712
                                and record_id in row["candidates"])
        summary["expected_relevant"] = expected_relevant
        summary["latency_p50_ms"] = int(statistics.median(latencies)) if latencies else None
        summary["latency_p95_ms"] = (int(sorted(latencies)[max(0, int(len(latencies) * 0.95) - 1)])
                                     if latencies else None)
        summary["hit_rate"] = (round((expected_relevant - summary["missed_expected_additions"])
                                     / expected_relevant, 4) if expected_relevant else None)
    return {"kind": "decision-fanout-compare", "generated_by": "backend/scripts/run_decision_compare.py",
            "generated_at": _now(), "mode": "production fan-out, one call per candidate",
            "providers": [{"name": provider.name, "endpoint": provider.endpoint, "model": provider.model}
                          for provider in available],
            "method": ("Each provider answers the shipped four-question fan-out (relevance, time-sensitivity, "
                       "subject-binding, urgency Score) per candidate, and the shipped gate "
                       "`decision_model.classify_status` is applied to both. Additions, wrong additions and "
                       "missed expected additions are counted against the authored keys."),
            "results": results, "totals": per_provider,
            "limits": [
                "Nine scored notes across twelve encounters, author-authored and unadjudicated keys, inherited "
                "thresholds: a small comparison, not a validation.",
                "A candidate whose reply fails the schema is recorded as unavailable or invalid, never scored.",
            ]}


async def _judge_single(provider, state: dict, questions: dict) -> dict:
    """One provider call for an already-built state and question set, timed, with the failure named."""
    started = time.monotonic()
    try:
        data = await provider.call(state, questions, timeout_s=TIMEOUT_S)
    except Exception as error:  # noqa: BLE001 - a provider failure is a result, not a crash
        return {"status": type(error).__name__, "answers": {}, "usage": {},
                "elapsed_ms": max(0, round((time.monotonic() - started) * 1000)), "detail": str(error)[:160]}
    return {"status": "ok", "answers": data["answers"], "usage": data.get("usage") or {},
            "elapsed_ms": max(0, round((time.monotonic() - started) * 1000))}


def _check() -> int:
    problems: list[str] = []
    seen = 0
    for _mode, path in sorted(TARGETS.items()):
        if not path.exists():
            continue
        seen += 1
        problems.extend(_check_one(path))
    if not seen:
        print(f"check: no comparison artifact among {[str(p) for p in TARGETS.values()]}", file=sys.stderr)
        return 1
    if problems:
        for problem in problems:
            print(f"check: {problem}", file=sys.stderr)
        return 1
    return 0


def _check_one(path: Path) -> list[str]:
    problems: list[str] = []
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return [f"{path.name}: unreadable ({type(exc).__name__})"]
    names = {provider["name"] for provider in document["providers"]}
    if len(names) < 2:
        problems.append(f"{path.name}: a comparison needs at least two providers, found {sorted(names)}")
    for name, summary in document["totals"].items():
        if "keyed" in summary:
            if summary["agreed"] != (summary["keyed"] - summary["wrong_additions"]
                                     - summary["missed_expected_additions"]):
                problems.append(f"{path.name}/{name}: agreement does not reconcile with its denominator")
            if summary["keyed"] == 0:
                problems.append(f"{path.name}/{name}: nothing was scored, so the row proves nothing")
        elif summary.get("candidates_judged", 0) == 0:
            problems.append(f"{path.name}/{name}: nothing was judged, so the row proves nothing")
    for encounter_id, row in document["results"].items():
        if row.get("providers") is None:
            continue
        judged_key = "judged"
        for name, provider_row in row["providers"].items():
            if "keyed" in provider_row:
                identifiers = provider_row.get(judged_key, [])
            else:
                identifiers = list(provider_row)
            if not isinstance(identifiers, list):
                continue
            for record_id in identifiers:
                if record_id not in row["candidates"]:
                    problems.append(f"{path.name}/{encounter_id}/{name}: judged {record_id}, not a candidate")
    for problem in problems:
        print(f"check: {problem}", file=sys.stderr)
    for name, summary in sorted(document["totals"].items()):
        if "keyed" in summary:
            print(f"check: {path.name} {name}: agreement={summary['agreement']} "
                  f"wrong={summary['wrong_additions']} missed={summary['missed_expected_additions']} "
                  f"flips={summary['verdict_flips']} p50={summary['latency_p50_ms']}ms")
        else:
            print(f"check: {path.name} {name}: relevant={summary['relevant']} "
                  f"wrong={summary['wrong_additions']} missed={summary['missed_expected_additions']} "
                  f"hit={summary['hit_rate']} p50={summary['latency_p50_ms']}ms")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compare decision providers on the frozen inventory")
    parser.add_argument("--mode", choices=("single", "fanout"), default="single",
                        help="single: one relevance question per candidate; fanout: the shipped"
                             " four-question contract per candidate")
    parser.add_argument("--check", action="store_true", help="verify the committed artifacts")
    args = parser.parse_args(argv)
    if args.check:
        return _check()
    if decision_providers.provider() is None:
        print("run_decision_compare: no provider credentials in the environment", file=sys.stderr)
        return 2
    target = TARGETS[args.mode]
    document = asyncio.run(_run_fanout() if args.mode == "fanout" else _run())
    RESULTS.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                      encoding="utf-8")
    for name, summary in sorted(document["totals"].items()):
        if args.mode == "fanout":
            print(f"  {name:24s} relevant={summary['relevant']} wrong={summary['wrong_additions']} "
                  f"missed={summary['missed_expected_additions']}/{summary['expected_relevant']} "
                  f"refused={summary['gate_refused']} hit={summary['hit_rate']} "
                  f"p50={summary['latency_p50_ms']}ms tokens={summary['input_tokens']}")
            continue
        print(f"  {name:24s} agreement={summary['agreement']} wrong={summary['wrong_additions']} "
              f"missed={summary['missed_expected_additions']} flips={summary['verdict_flips']} "
              f"delta={summary['max_probability_delta']} p50={summary['latency_p50_ms']}ms "
              f"tokens={summary['input_tokens']}")
    print(f"  -> {target.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
