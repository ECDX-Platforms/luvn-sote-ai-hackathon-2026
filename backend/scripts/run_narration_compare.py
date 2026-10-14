#!/usr/bin/env python
"""Does the narrator need to be the most capable model? gpt-6.1-sol vs model-router vs gpt-6-luna.

The comparison holds everything except the writer constant:

* **Same evidence.** Each model receives the same post-`cut` permitted scope for the same encounter, built
  the same way, with the selector's own Decision-1 stage not run — so the input is identical across models.
* **Same verification.** The per-sentence verifier is the shipped one (Decision-1) in every run, so "sentences the
  trust path accepted" measures the writer rather than two things moving at once.
* **Same code path.** The versioned prompt agent is pinned to one model, so the agent path is disabled for
  every run and each model goes through the same direct strict-schema call. Otherwise one model would answer
  through the agent and the others through the fallback, and the comparison would be meaningless.

Reported per model: whether generation succeeded, whether the narration was accepted, sentences rendered,
citations, latency and tokens. A model that produces prose the verifier refuses scores worse on the only
axis this product cares about, however fluent it is.

    .venv/bin/python scripts/run_narration_compare.py
    .venv/bin/python scripts/run_narration_compare.py --check
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from pydantic import BaseModel  # noqa: E402

from app import llm  # noqa: E402
from app.fixtures import load_decision_eval_personas, load_personas  # noqa: E402
from app.pipeline import PREPARATION_WINDOW_S, build_picture_for_encounter  # noqa: E402

import run_decision_value_eval as evalmod  # noqa: E402

RESULTS = ROOT / ".scratch" / "retrieval-and-knowledge" / "results"
TARGET = RESULTS / "narration-model-compare-v1.json"
MODELS = ("gpt-6.1-sol", "model-router", "gpt-6-luna")
CASES = (
    "bound-encounter-005-laakitys-sairaanhoitaja",
    "bound-encounter-003-mielenterveys-sairaanhoitaja",
    "bound-encounter-009-laakitys-sairaanhoitaja",
    "bound-encounter-011-laakitys-sairaanhoitaja",
)
_SENTENCE = re.compile(r"[^.!?]+[.!?]")


class _Probe(BaseModel):
    """A trivial strict-schema payload, so a model's *usability* is separable from its prose quality."""

    ok: str


async def _preflight(model: str, timeout_s: float = 45.0) -> dict:
    """Can this deployment serve the app's own request shape at all? Records the reason verbatim.

    This is how the negative result was found: `gpt-6-luna` is refused by the application's own route guard
    (`_validate_route_model`), and `model-router` is throttled at the capacity it was deployed with. Neither
    is a statement about model capability, and neither would have been visible from the narration outcome
    alone, which simply reported a failed generation.
    """
    original = os.environ.get("LUNA_FOUNDRY_MODEL")
    os.environ["LUNA_FOUNDRY_MODEL"] = model
    try:
        result = await llm._complete_bounded(_Probe, "Reply with JSON.", '{"probe": true}', timeout_s)  # noqa: SLF001
        return {"usable": True, "served_model": result.model}
    except Exception as error:  # noqa: BLE001 - the reason is the finding
        return {"usable": False, "reason": f"{type(error).__name__}: {str(error)[:240]}"}
    finally:
        if original is None:
            os.environ.pop("LUNA_FOUNDRY_MODEL", None)
        else:
            os.environ["LUNA_FOUNDRY_MODEL"] = original


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


async def _run() -> dict:
    personas = {persona.persona_id: persona
                for persona in load_personas() + load_decision_eval_personas()}
    per_model: dict[str, dict] = {
        model: {"runs": 0, "generated": 0, "accepted": 0, "sentences_rendered": 0, "citations": 0,
                "latencies_ms": [], "input_tokens": 0, "output_tokens": 0, "statuses": []}
        for model in MODELS}
    results: dict[str, dict] = {}
    preflight = {model: await _preflight(model) for model in MODELS}
    for model, outcome in preflight.items():
        print(f"  preflight {model:16s} usable={outcome['usable']} {outcome.get('reason','')[:90]}")
    for encounter_id in CASES:
        rows: dict[str, dict] = {}
        for model in MODELS:
            # `llm._configured_model()` reads this environment variable on every call, so patching the
            # module constant would silently run the same model for every "model". That mistake was made
            # once and caught by the generation_model column, which is why `--check` now demands variation.
            original_env = os.environ.get("LUNA_FOUNDRY_MODEL")
            original_agent = llm._complete_prompt_agent  # noqa: SLF001 - forcing one code path for all

            async def _no_agent(*_args, **_kwargs):
                raise RuntimeError("agent path disabled for the model comparison")

            os.environ["LUNA_FOUNDRY_MODEL"] = model
            llm._complete_prompt_agent = _no_agent  # noqa: SLF001
            try:
                picture = await build_picture_for_encounter(
                    encounter_id, personas=personas, encounters=evalmod.EVAL_ENCOUNTERS,
                    today=evalmod.TODAY, decision_enabled=True, foundry_enabled=True)
            except Exception as error:  # noqa: BLE001 - a model failure is a result
                rows[model] = {"error": f"{type(error).__name__}: {str(error)[:160]}"}
                continue
            finally:
                if original_env is None:
                    os.environ.pop("LUNA_FOUNDRY_MODEL", None)
                else:
                    os.environ["LUNA_FOUNDRY_MODEL"] = original_env
                llm._complete_prompt_agent = original_agent  # noqa: SLF001
            narrative = picture.narrative or ""
            sentences = len(_SENTENCE.findall(narrative)) if narrative else 0
            row = {"generation_status": picture.narration_generation_status,
                   "acceptance_status": picture.narration_acceptance_status,
                   "generation_path": picture.narration_generation_path,
                   "generation_model": picture.narration_generation_model,
                   "sentences": sentences,
                   "characters": len(narrative),
                   "citations": len(picture.citations),
                   "elapsed_ms": picture.narration_generation_elapsed_ms,
                   "input_tokens": picture.narration_generation_input_tokens,
                   "output_tokens": picture.narration_generation_output_tokens,
                   "rejection_reason": picture.narration_rejection_reason}
            rows[model] = row
            summary = per_model[model]
            summary["runs"] += 1
            summary["generated"] += int(row["generation_status"] == "generated")
            summary["accepted"] += int(row["acceptance_status"] == "accepted")
            summary["sentences_rendered"] += sentences
            summary["citations"] += row["citations"]
            if row["elapsed_ms"]:
                summary["latencies_ms"].append(row["elapsed_ms"])
            summary["input_tokens"] += row["input_tokens"] or 0
            summary["output_tokens"] += row["output_tokens"] or 0
            summary["statuses"].append(row["generation_status"])
        results[encounter_id] = rows
    for model, summary in per_model.items():
        latencies = summary.pop("latencies_ms")
        summary["latency_p50_ms"] = int(statistics.median(latencies)) if latencies else None
        summary["acceptance_rate"] = (round(summary["accepted"] / summary["runs"], 4)
                                      if summary["runs"] else None)
    served = sorted({row["generation_model"] for rows in results.values() for row in rows.values()
                     if isinstance(row, dict) and row.get("generation_model")})
    return {"kind": "narration-model-compare", "generated_by": "backend/scripts/run_narration_compare.py",
            "generated_at": _now(), "models": list(MODELS), "preflight": preflight,
            "models_actually_served": served,
            "comparison_valid": len(served) >= 2,
            "method": ("Each model writes the narration for each encounter through the same direct "
                       "strict-schema path, with the versioned prompt agent disabled so no model gets a "
                       "different code path, and with the shipped per-sentence verifier held constant. "
                       "Acceptance is therefore a property of the writer."),
            "results": results, "totals": per_model,
            "limits": [
                "Four encounters, one run each: this is a signal, not a benchmark, and run-to-run variance "
                "is not measured here.",
                "Per-token prices for these deployments were not published in the retail price feed, so "
                "cost is reported as tokens rather than guessed.",
                "The verifier is one model in every run, so a writer could in principle be tuned to it; "
                "acceptance is not a measure of truth.",
            ]}


def _check() -> int:
    if not TARGET.exists():
        print(f"check: no artifact at {TARGET}", file=sys.stderr)
        return 1
    document = json.loads(TARGET.read_text(encoding="utf-8"))
    problems: list[str] = []
    if len(document["models"]) < 2:
        problems.append("a comparison needs at least two models")
    served = set(document.get("models_actually_served") or [])
    if len(served) < 2:
        # A void comparison is a legitimate artifact only if it explains itself: every model that did not
        # serve must carry a recorded reason, so "void" cannot be used to hide a harness mistake.
        if document.get("comparison_valid") is not False:
            problems.append("fewer than two models served, but the artifact does not declare itself void")
        for model, outcome in (document.get("preflight") or {}).items():
            if not outcome.get("usable") and not outcome.get("reason"):
                problems.append(f"{model}: unusable with no recorded reason")
        for model, summary in document["totals"].items():
            if summary["runs"] and summary["generated"] == 0:
                reasons = [row.get("generation_status") for rows in document["results"].values()
                           for row in [rows.get(model, {})]]
                if not any(status and status != "generated" for status in reasons):
                    problems.append(f"{model}: generated nothing and says nothing why")
    for model, summary in document["totals"].items():
        if summary["runs"] == 0:
            problems.append(f"{model}: no run completed, so its row proves nothing")
        if summary["accepted"] > summary["generated"]:
            problems.append(f"{model}: accepted more runs than it generated")
    for encounter_id, rows in document["results"].items():
        for model, row in rows.items():
            if "error" in row:
                continue
            if row["acceptance_status"] == "accepted" and row["sentences"] == 0:
                problems.append(f"{encounter_id}/{model}: accepted with no sentences")
    for problem in problems:
        print(f"check: {problem}", file=sys.stderr)
    print(f"check: models actually served: {sorted(served)}")
    for model, summary in document["totals"].items():
        print(f"check: {model:16s} generated={summary['generated']}/{summary['runs']} "
              f"accepted={summary['accepted']} sentences={summary['sentences_rendered']} "
              f"citations={summary['citations']} p50={summary['latency_p50_ms']}ms "
              f"tokens={summary['input_tokens']}/{summary['output_tokens']}")
    return 1 if problems else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compare narration writers")
    parser.add_argument("--check", action="store_true", help="verify the committed artifact")
    args = parser.parse_args(argv)
    if args.check:
        return _check()
    if not llm.foundry_configured():
        print("run_narration_compare: Foundry is not configured", file=sys.stderr)
        return 2
    document = asyncio.run(_run())
    RESULTS.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                      encoding="utf-8")
    return _check()


if __name__ == "__main__":
    raise SystemExit(main())
