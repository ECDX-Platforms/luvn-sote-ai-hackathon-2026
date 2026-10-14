#!/usr/bin/env python
"""E1 — replay LUVN's Decision-1 judgments as evaluation rows.

Reads the frozen 12-encounter inventory that `run_decision_value_eval.py` keys against, builds each
encounter's picture with Foundry narration disabled, and judges the **selection** with one Noul question
per authored candidate. `--with-narration` additionally runs the live narration and judges every
generated sentence against only its cited records, then reports how far that simpler rule agrees with the
runtime verifier.

Writes `.scratch/retrieval-and-knowledge/results/decision-eval-v1.json` and a row-shaped dataset
beside it. `--foundry` additionally logs the run through `azure.ai.evaluation.evaluate(...,
azure_ai_project=...)` and prints the `studio_url`; that library is imported lazily, so neither this
module nor the local artifact depends on it, and a missing library is recorded as an outcome rather than
raised.

    uv run --env-file .env python scripts/run_decision_eval.py
    uv run --env-file .env python scripts/run_decision_eval.py --with-narration
    .venv/bin/python scripts/run_decision_eval.py --check
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app import decision_providers, decision_evals as ev  # noqa: E402
from app import decision_model, narration, source  # noqa: E402
from app.encounters import resolve_encounter  # noqa: E402
from app.fixtures import load_decision_eval_personas, load_personas  # noqa: E402
from app.pipeline import build_picture_for_encounter  # noqa: E402
from app.policy import cut_for_encounter  # noqa: E402
from app.purpose import palvelukonteksti, palvelukontekstit  # noqa: E402

import run_decision_value_eval as evalmod  # noqa: E402

RESULTS = ROOT / ".scratch" / "retrieval-and-knowledge" / "results"
TARGET = RESULTS / "decision-eval-v1.json"
DATASET = RESULTS / "decision-eval-v1-dataset.jsonl"
# The frozen inventory is the encounter set the recorded cap-80 evaluation scored, not every encounter
# `EVAL_ENCOUNTERS` resolves: the doctor control and the two lenses added after that run have no key.
FROZEN = ROOT / ".scratch" / "hackathon-readiness" / "results" / "decision-value-eval-cap80-v1.json"
TODAY = evalmod.TODAY

AUTHORED_FIELDS = ("expect_additions", "expect_no_addition", "expect_omissions",
                   "expect_abstentions", "observe_candidates")


def _inventory() -> list[str]:
    """The recorded 12-encounter inventory, in `EVAL_ENCOUNTERS` order."""
    if not FROZEN.exists():
        return list(evalmod.EVAL_ENCOUNTERS)
    recorded = set(json.loads(FROZEN.read_text(encoding="utf-8"))["results"])
    return [encounter_id for encounter_id in evalmod.EVAL_ENCOUNTERS if encounter_id in recorded]


def _authored_key(expected: dict) -> dict[str, bool]:
    """`True` = authored as relevant.

    An authored abstention is deliberately not keyed: a Noul question cannot express "no confident
    verdict", so counting it either way would misstate the key.
    """
    keyed: dict[str, bool] = {record_id: True for record_id in expected.get("expect_additions", [])}
    for field in ("expect_no_addition", "expect_omissions"):
        keyed.update({record_id: False for record_id in expected.get(field, [])})
    return keyed


def _candidates(picture, expected: dict, permitted: dict) -> list[tuple[str, str]]:
    ids = set(picture.selection.additional_record_ids if picture.selection else ())
    for field in AUTHORED_FIELDS:
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


async def _judge_twice(prefix, candidates, timeout_s):
    """Judge the same candidates twice: the determinism check the issue pre-registers.

    The comparison is **verdict stability at the floor**, not float equality. The measured drift is a
    few hundredths on the probability (recorded as `max_probability_delta`), which does not move a
    verdict unless the probability sits within that band of the floor — and a verdict flip is the only
    difference that can change a briefing.
    """
    first = await ev.judge_selection(prefix, candidates, timeout_s=timeout_s)
    second = await ev.judge_selection(prefix, candidates, timeout_s=timeout_s)
    flips: list[str] = []
    max_delta = 0.0
    for record_id, answer in first["answers"].items():
        left = decision_model.noul_answer(answer, f"relevance:{record_id}")
        right = decision_model.noul_answer(second["answers"].get(record_id), f"relevance:{record_id}")
        max_delta = max(max_delta, abs(left - right))
        if (left >= ev.SELECTION_FLOOR) != (right >= ev.SELECTION_FLOOR):
            flips.append(record_id)
    usage = {field: first["usage"][field] + second["usage"][field]
             for field in ("input_tokens", "output_tokens")}
    return first, {"identical": not flips, "verdict_flips": sorted(flips),
                   "judgments_compared": len(first["answers"]),
                   "max_probability_delta": round(max_delta, 4)}, usage


def _grounding_sentences(captured: dict, permitted: dict) -> list[dict]:
    candidate = captured.get("candidate")
    if candidate is None:
        return []
    sentences = []
    for sentence in candidate.sentences:
        cited = [{"record_id": record_id,
                  "record_type": permitted[record_id].record_type,
                  "recorded_at": permitted[record_id].recorded_at.isoformat(),
                  "payload": source.basics_payload(permitted[record_id], "model")}
                 for record_id in sentence.source_ids if record_id in permitted]
        if len(cited) != len(sentence.source_ids):
            continue
        sentences.append({"sentence_id": sentence.sentence_id, "text": sentence.text, "cited_sources": cited})
    return sentences


async def _picture(encounter_id: str, personas: dict, with_narration: bool) -> tuple[object, dict, int]:
    captured: dict = {}
    original_validate = original_verify = None
    started = time.monotonic()
    if with_narration:
        original_validate = narration.validate_fresh_candidate
        original_verify = narration.decision_model.verify_narration

        def capture_validate(*args, _original=original_validate, **kwargs):
            result = _original(*args, **kwargs)
            captured["candidate"] = getattr(result, "candidate", None)
            return result

        async def capture_verify(*args, _original=original_verify, **kwargs):
            result = await _original(*args, **kwargs)
            captured["verifier"] = result
            return result

        narration.validate_fresh_candidate = capture_validate
        narration.decision_model.verify_narration = capture_verify
    try:
        picture = await build_picture_for_encounter(
            encounter_id, personas=personas, encounters=evalmod.EVAL_ENCOUNTERS, today=TODAY,
            started_at=time.monotonic(), preparation_deadline=time.monotonic() + 300.0,
            decision_enabled=True, foundry_enabled=with_narration)
    finally:
        if with_narration:
            narration.validate_fresh_candidate = original_validate
            narration.decision_model.verify_narration = original_verify
    return picture, captured, max(0, round((time.monotonic() - started) * 1000))


async def _run(with_narration: bool) -> tuple[dict, list[dict]]:
    personas_list = load_personas() + load_decision_eval_personas()
    personas = {persona.persona_id: persona for persona in personas_list}
    results: dict[str, dict] = {}
    dataset: list[dict] = []
    totals = {"encounters": 0, "judged": 0, "keyed": 0, "agreed": 0, "wrong_additions": 0,
              "missed_expected_additions": 0, "determinism_mismatches": 0, "judgments_compared": 0,
              "max_probability_delta": 0.0}
    usage_total = {"input_tokens": 0, "output_tokens": 0}
    scores: list[int] = []
    grounding_rows: list[dict] = []
    verifier_agreement = {"compared": 0, "agreed": 0, "disagreements": []}

    for encounter_id in _inventory():
        encounter, persona = resolve_encounter(encounter_id, personas, evalmod.EVAL_ENCOUNTERS)
        permitted = {record.record_id: record for record in cut_for_encounter(persona, encounter).records}
        expected = evalmod.EXPECTED.get(encounter_id, {})
        picture, captured, elapsed_ms = await _picture(encounter_id, personas, with_narration)

        candidates = _candidates(picture, expected, permitted)
        judged_ids = {record_id for record_id, _ in candidates}
        keyed = {record_id: value for record_id, value in _authored_key(expected).items()
                 if record_id in judged_ids}
        description = palvelukonteksti(encounter.purpose).description if encounter.purpose else ""
        prefix = ev.selection_state(encounter.role, encounter.purpose or "", description)
        if candidates:
            answers, determinism, usage = await _judge_twice(prefix, candidates, decision_model.CALL_BUDGET_S)
            for field in usage:
                usage_total[field] += usage[field]
        else:
            answers = {"answers": {}}
            determinism = {"identical": True, "verdict_flips": [], "judgments_compared": 0,
                           "max_probability_delta": 0.0}
        row = ev.selection_row(answers["answers"], keyed)
        row.update({"candidate_ids": [record_id for record_id, _ in candidates],
                    "determinism": determinism, "elapsed_ms": elapsed_ms})

        grounding = None
        if with_narration:
            sentences = _grounding_sentences(captured, permitted)
            if sentences:
                judged = await ev.judge_grounding(encounter.role, encounter.purpose or "", sentences,
                                                  timeout_s=decision_model.CALL_BUDGET_S)
                grounding = ev.grounding_row(judged["rows"])
                for field in ("input_tokens", "output_tokens"):
                    usage_total[field] += judged["usage"][field]
                grounding_rows.append({"encounter_id": encounter_id, **grounding})
                runtime = {verdict["sentence_id"]: verdict["verdict"]
                           for verdict in (captured.get("verifier") or {}).get("verdicts", [])}
                for sentence_id, evaluator_verdict in grounding["verdicts"].items():
                    if sentence_id not in runtime:
                        continue
                    verifier_agreement["compared"] += 1
                    if (evaluator_verdict == "supported") == (runtime[sentence_id] == "supported"):
                        verifier_agreement["agreed"] += 1
                    else:
                        verifier_agreement["disagreements"].append(
                            {"encounter_id": encounter_id, "sentence_id": sentence_id,
                             "evaluator": evaluator_verdict, "runtime": runtime[sentence_id]})
            else:
                grounding = {"metric": ev.GROUNDING_METRIC, "score": None, "passed": False,
                             "sentences": 0, "reason": "no accepted sentences to judge"}

        results[encounter_id] = {"role": encounter.role, "purpose": encounter.purpose,
                                 "selection": row, "grounding": grounding,
                                 "narrative_accepted": bool(picture.narrative),
                                 "narration_generation_path": picture.narration_generation_path,
                                 "narration_generation_status": picture.narration_generation_status}
        totals["encounters"] += 1
        totals["judged"] += len(row["judged"])
        totals["keyed"] += len(row["keyed"])
        totals["agreed"] += (len(row["keyed"]) - len(row["wrong_additions"])
                             - len(row["missed_expected_additions"]))
        totals["wrong_additions"] += len(row["wrong_additions"])
        totals["missed_expected_additions"] += len(row["missed_expected_additions"])
        totals["judgments_compared"] += determinism["judgments_compared"]
        totals["determinism_mismatches"] += len(determinism["verdict_flips"])
        totals["max_probability_delta"] = max(totals["max_probability_delta"],
                                              determinism["max_probability_delta"])
        scores.append(row["score"])
        dataset.append({
            "encounter_id": encounter_id,
            "query": f"{encounter.role} / {encounter.purpose}",
            "response": json.dumps({"judged": row["judged"]}, ensure_ascii=False),
            "context": json.dumps([{"record_id": record_id, "text": text}
                                   for record_id, text in candidates], ensure_ascii=False),
            "authored_key": json.dumps(keyed, ensure_ascii=False),
        })

    return {
        "kind": "decision-foundry-eval",
        "generated_by": "backend/scripts/run_decision_eval.py",
        "today": TODAY.isoformat(),
        "model": decision_model.MODEL,
        "floors": {"selection": ev.SELECTION_FLOOR, "grounding": ev.GROUNDING_FLOOR},
        "method": (
            "Selection is judged by one Noul question per authored candidate; grounding (optional) by one "
            "call per accepted sentence against only its cited records. Both are combined in code and every "
            "check is published. The evaluator's grounding rule is deliberately simpler than the runtime "
            "verifier's, so agreement between them is informative rather than tautological. The authored "
            "keys are author-authored and unadjudicated, exactly as the recorded eval states. A Noul "
            "question cannot express an authored abstention, so `expect_abstentions` is judged but not keyed."
        ),
        "results": results,
        "totals": {**totals,
                   "agreement": (totals["agreed"] / totals["keyed"]) if totals["keyed"] else None,
                   "mean_selection_score": (sum(scores) / len(scores)) if scores else None},
        "usage": usage_total,
        "estimated_input_cost_usd": round(usage_total["input_tokens"] * 0.042 / 1_000_000, 6),
        "grounding": grounding_rows or None,
        "verifier_agreement": verifier_agreement if with_narration else None,
        "dataset": {"path": str(DATASET.relative_to(ROOT)), "rows": len(dataset)},
        "foundry": None,
        "limits": [
            "Author-authored, unadjudicated keys; this is not accuracy and not clinical validity.",
            "Decision-1 is English-first; these fixtures are Finnish, so lower precision on Finnish is expected.",
            "One run over 12 encounters; `determinism` compares the judge against itself, not against a gold standard.",
            "The grounding half is populated only with --with-narration, which needs live Foundry.",
        ],
    }, dataset


class _SelectionEvaluator:
    """Adapt one metric row to the `azure.ai.evaluation` evaluator contract.

    Foundry calls an evaluator per dataset row, so this recomputes the judgment for that row. It is the
    only place the spike calls Decision-1 inside an evaluation run.
    """

    def __init__(self, timeout_s: float = decision_model.CALL_BUDGET_S):
        self.timeout_s = timeout_s

    def __call__(self, *, query=None, context=None, authored_key=None, **_ignored) -> dict:
        candidates = [(row["record_id"], row["text"]) for row in json.loads(context or "[]")]
        keyed = {record_id: bool(value) for record_id, value in json.loads(authored_key or "{}").items()}
        role, _separator, purpose = (query or "").partition(" / ")
        description = palvelukonteksti(purpose).description if purpose in palvelukontekstit() else ""
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            raise RuntimeError("the Decision-1 judge must not be called from inside a running event loop")
        judged = asyncio.run(ev.judge_selection(ev.selection_state(role, purpose, description),
                                                candidates, timeout_s=self.timeout_s))
        row = ev.selection_row(judged["answers"], keyed)
        return {"decision_selection": row["score"], "decision_selection_score": row["score"],
                "decision_selection_passed": row["passed"], "decision_selection_reason": row["reason"],
                "decision_selection_properties": {"checks": row["checks"], "judged": row["judged"],
                                             "keyed": row["keyed"]}}


def _log_to_foundry() -> dict:
    """Log the committed dataset to the Foundry project. Returns the outcome; never raises."""
    try:
        from azure.ai.evaluation import evaluate  # noqa: PLC0415
    except ImportError as exc:
        return {"logged": False, "reason": f"azure-ai-evaluation is not installed ({exc})"}
    endpoint = os.environ.get("LUNA_FOUNDRY_PROJECT_ENDPOINT", "").strip()
    if not endpoint:
        return {"logged": False, "reason": "LUNA_FOUNDRY_PROJECT_ENDPOINT is not set"}
    try:
        result = evaluate(data=str(DATASET), evaluators={"decision_selection": _SelectionEvaluator()},
                          azure_ai_project=endpoint)
    except Exception as exc:  # noqa: BLE001 - the spike records the failure instead of hiding it
        return {"logged": False, "reason": f"{type(exc).__name__}: {exc}"}
    return {"logged": True, "studio_url": result.get("studio_url"), "metrics": result.get("metrics")}


def _check() -> int:
    if not TARGET.exists():
        print(f"check: no artifact at {TARGET}", file=sys.stderr)
        return 1
    document = json.loads(TARGET.read_text(encoding="utf-8"))
    problems: list[str] = []
    totals = document["totals"]
    recomputed = {"judged": 0, "keyed": 0, "wrong_additions": 0, "missed_expected_additions": 0}
    for encounter_id, row in document["results"].items():
        selection = row["selection"]
        if not isinstance(selection.get("checks"), list):
            problems.append(f"{encounter_id}: selection row carries no checks")
        recomputed["judged"] += len(selection["judged"])
        recomputed["keyed"] += len(selection["keyed"])
        recomputed["wrong_additions"] += len(selection["wrong_additions"])
        recomputed["missed_expected_additions"] += len(selection["missed_expected_additions"])
    for field, value in recomputed.items():
        if totals[field] != value:
            problems.append(f"totals.{field}={totals[field]} but rows sum to {value}")
    # A verdict flip is a *finding* about the judge, not a defect in the artifact: it is recorded in the
    # artifact and written up in the issue. The check verifies integrity, and reports the flip.
    flips = totals["determinism_mismatches"]
    for problem in problems:
        print(f"check: {problem}", file=sys.stderr)
    print(f"check: {totals['encounters']} encounters, {totals['keyed']} keyed notes, "
          f"{flips} verdict flip(s) recorded, max probability delta {totals['max_probability_delta']}")
    return 1 if problems else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Replay LUVN's Decision-1 judgments as evaluation rows")
    parser.add_argument("--with-narration", action="store_true",
                        help="also run live narration and judge each sentence against its citations")
    parser.add_argument("--foundry", action="store_true", help="also log the run to the Foundry project")
    parser.add_argument("--check", action="store_true", help="verify the committed artifact and change nothing")
    args = parser.parse_args(argv)
    if args.check:
        return _check()
    if decision_providers.provider() is None:
        print("run_decision_eval: no decision provider is configured: refusing to run, because an empty judgment "
              "would be reported as a passed metric", file=sys.stderr)
        return 2
    document, dataset = asyncio.run(_run(with_narration=args.with_narration))
    RESULTS.mkdir(parents=True, exist_ok=True)
    DATASET.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
                               for row in dataset), encoding="utf-8")
    if args.foundry:
        document["foundry"] = _log_to_foundry()
    TARGET.write_text(json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                      encoding="utf-8")
    totals = document["totals"]
    print(f"run_decision_eval: {totals['encounters']} encounters, {totals['judged']} judged, "
          f"{totals['keyed']} keyed, agreement={totals['agreement']}, "
          f"wrong_additions={totals['wrong_additions']}, "
          f"missed_expected={totals['missed_expected_additions']}, "
          f"determinism_mismatches={totals['determinism_mismatches']}")
    if document["foundry"] is not None:
        print(f"  foundry: {document['foundry']}")
    print(f"  -> {TARGET.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
