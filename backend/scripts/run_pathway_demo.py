#!/usr/bin/env python
"""Exercise the retrieve-then-verify path over an authored, clearly-labelled stand-in corpus.

**What this is.** The architecture under test is the one this repository already uses for narration —
retrieve, one Decision-1 verdict per item, thresholds applied in code — pointed at a knowledge corpus instead
of a person's records. It does **not** replicate UpToDate Expert AI, whose defining property is a
continuously updated expert database; the corpus here is three entries and ten steps that we wrote.

**What the run can and cannot show.** The corpus contains a step telling the professional to continue
amlodipine unchanged, on a fixture where amlodipine was stopped for oedema and dizziness. The gate is
expected to refuse it. That step was authored *in order to be refused*, so a clean run is guaranteed by
construction: this is a **unit test of the gate**, not evidence about UpToDate and not a safety result.

**What the gate does not do.** It drops a step whose contradiction probability is at or above 0.7 and
renders one whose relevance is at or above 0.6. The runtime note gate additionally requires a 0.25
decision margin between the top two probabilities; a single Noul question has no runner-up, so **no
margin is applied here** and a contradiction of 0.69 renders. The thresholds are inherited, not
calibrated on Finnish data, and Decision-1's measured repeated-call drift on this inventory was up to 0.04 with
one verdict flip in ten. The kept steps scored 0.37 / 0.56 / 0.53 — inside that drift band.

    .venv/bin/python scripts/run_pathway_demo.py
    .venv/bin/python scripts/run_pathway_demo.py --check
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app import decision_model, decision_providers, pathways, source  # noqa: E402
from app.encounters import resolve_encounter  # noqa: E402
from app.fixtures import load_personas  # noqa: E402
from app.policy import cut_for_encounter  # noqa: E402
from app.purpose import palvelukonteksti  # noqa: E402

import run_decision_value_eval as evalmod  # noqa: E402

RESULTS = ROOT / ".scratch" / "retrieval-and-knowledge" / "results"
TARGET = RESULTS / "pathway-demo-v1.json"

# One encounter per authored entry, chosen so the first has a record that contradicts a step.
CASES = (
    ("bound-encounter-005-laakitys-sairaanhoitaja", "amlodipine stopped for oedema; s4 must be refused"),
    ("bound-encounter-003-mielenterveys-sairaanhoitaja", "mood follow-up; no contradiction expected"),
    ("bound-encounter-003-toimintakyky-sairaanhoitaja", "functioning support; no contradiction expected"),
)


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _query(encounter) -> str:
    purpose = palvelukonteksti(encounter.purpose)
    return " ".join([purpose.description, encounter.role, *purpose.baseline_terms])


def _evidence(records) -> list[dict]:
    """Only the model-safe projection: identity basics never leave, exactly as narration does it."""
    return [{"record_id": record.record_id, "record_type": record.record_type,
             "recorded_at": record.recorded_at.isoformat(),
             "temporal_status": source.temporal_status(record, evalmod.TODAY),
             "payload": source.basics_payload(record, "model")}
            for record in records]


async def _run() -> dict:
    personas = {persona.persona_id: persona for persona in load_personas()}
    results: list[dict] = []
    usage = {"input_tokens": 0, "output_tokens": 0}
    corpus = pathways.load_corpus()
    for encounter_id, expectation in CASES:
        encounter, persona = resolve_encounter(encounter_id, personas, evalmod.EVAL_ENCOUNTERS)
        scoped = cut_for_encounter(persona, encounter)
        entries = pathways.retrieve(_query(encounter), limit=1)
        if not entries:
            results.append({"encounter_id": encounter_id, "expectation": expectation,
                            "retrieved": None, "pathway": None,
                            "note": "no corpus entry cleared retrieval, so the component abstains"})
            continue
        entry = entries[0]
        steps = pathways.steps_of(entry, str(corpus["corpus_version"]), bool(corpus["illustrative"]),
                                  str(corpus["provenance"]))
        evidence = _evidence(scoped.records)
        answers: dict[str, dict] = {}
        gate_errors: list[dict] = []
        for step in steps:
            try:
                data = await decision_model.request_judgment(
                    pathways.gate_state(encounter.role, encounter.purpose or "", step, evidence),
                    pathways.gate_questions())
            except Exception as error:  # noqa: BLE001 - a failed gate must not render the step
                answers[step.step_id] = {}
                gate_errors.append({"step_id": step.step_id, "error": type(error).__name__})
                continue
            answers[step.step_id] = data["answers"]
            raw = data.get("usage")
            if isinstance(raw, dict):
                for field in ("input_tokens", "output_tokens"):
                    value = raw.get(field)
                    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                        usage[field] += value
        results.append({"encounter_id": encounter_id, "expectation": expectation,
                        "role": encounter.role, "purpose": encounter.purpose,
                        "retrieved": entry["entry_id"], "evidence_records": len(evidence),
                        "gate_errors": gate_errors, "pathway": pathways.assemble(entry, answers)})
    refused = {row["encounter_id"]: [step["step_id"] for step in (row.get("pathway") or {}).get("dropped", [])
                                     if step["reason"] == "contradicted_by_record"]
               for row in results if row.get("pathway")}
    safety_failures = [row["encounter_id"] for row in results
                       if row["encounter_id"] == CASES[0][0] and "s4" not in refused.get(row["encounter_id"], [])]
    return {"kind": "pathway-demo", "generated_by": "backend/scripts/run_pathway_demo.py",
            "generated_at": _now(), "model": decision_model.MODEL,
            "corpus": {"version": corpus["corpus_version"], "illustrative": corpus["illustrative"],
                       "entries": len(corpus["entries"]),
                       "steps": sum(len(entry["steps"]) for entry in corpus["entries"]),
                       "provenance": corpus["provenance"]},
            "floors": {"relevant": pathways.RELEVANT_FLOOR,
                       "contradiction_ceiling": pathways.CONTRADICTION_CEILING,
                       "decision_margin": 0.0},
            "method": ("Replicates the retrieve-then-verify architecture this repository already uses for "
                       "narration — retrieval, one Decision-1 call per step asking whether the step belongs and "
                       "whether the person's records contradict it, thresholds applied in code — over a "
                       "corpus we authored. It does NOT replicate UpToDate, whose property is a "
                       "continuously updated expert database. The corpus is an authored stand-in and says "
                       "so in every reference it emits."),
            "results": results, "contradiction_refusals": refused,
            "safety_failures": safety_failures, "passed": not safety_failures,
            "usage": usage, "estimated_input_cost_usd": round(usage["input_tokens"] * 0.042 / 1_000_000, 6),
            "limits": [
                "This is a unit test of the gate, not evidence about UpToDate. The corpus is 3 entries / "
                "10 steps authored by us, and the refused step was authored to be refused, so a clean run "
                "is guaranteed by construction. Do not present it as 'we replicated UpToDate'.",
                "The gate applies NO decision margin: it drops contradiction >= 0.7 and renders relevance "
                ">= 0.6, so a contradiction of 0.69 renders. The thresholds are inherited from the note "
                "gate, where a 0.25 margin also applies, and are not calibrated on Finnish data. Measured "
                "Decision-1 repeated-call drift on this inventory was up to 0.04 with one verdict flip in ten.",
                "Kept steps scored 0.37 / 0.56 / 0.53 contradiction — 0.14-0.17 from the line, inside the "
                "drift band. Treat this as a filter, not a safety control.",
                "The corpus is authored and labelled illustrative in every reference; it is not UpToDate, "
                "not Käypä hoito and not clinical guidance. Fixtures are synthetic.",
                "One retrieval path (in-process token overlap); the search service is the production "
                "retriever. Nothing here is clinical validation.",
            ]}


def _check() -> int:
    if not TARGET.exists():
        print(f"check: no artifact at {TARGET}", file=sys.stderr)
        return 1
    document = json.loads(TARGET.read_text(encoding="utf-8"))
    problems: list[str] = []
    if document.get("safety_failures"):
        problems.append(f"a contradicted step rendered: {document['safety_failures']}")
    if not document.get("contradiction_refusals", {}).get(CASES[0][0]):
        problems.append("the medication case refused nothing, so the gate was not exercised")
    if document.get("floors", {}).get("decision_margin") != 0.0:
        problems.append("the artifact must state that no decision margin is applied")
    if "does NOT replicate UpToDate" not in document.get("method", ""):
        problems.append("the artifact must say it does not replicate UpToDate")
    for row in document["results"]:
        pathway = row.get("pathway")
        if not pathway:
            continue
        if pathway["rendered"] + len(pathway["dropped"]) != pathway["candidate"]:
            problems.append(f"{row['encounter_id']}: steps do not add up")
        for step in pathway["steps"]:
            if not step["ref"].startswith("ref:pathway-corpus@"):
                problems.append(f"{row['encounter_id']}: rendered step without a corpus ref")
            if step.get("illustrative") is not True:
                problems.append(f"{row['encounter_id']}: a rendered step is not labelled illustrative")
        for step in pathway["dropped"]:
            if "relevance_probability" not in step:
                problems.append(f"{row['encounter_id']}: a dropped step lacks its relevance probability")
    for problem in problems:
        print(f"check: {problem}", file=sys.stderr)
    print(f"check: {len(document['results'])} encounters, refusals={document['contradiction_refusals']}")
    return 1 if problems else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Exercise the retrieve-then-verify path on a stand-in corpus")
    parser.add_argument("--check", action="store_true", help="verify the committed artifact")
    args = parser.parse_args(argv)
    if args.check:
        return _check()
    # The decision provider is configured by `LUNA_FOUNDRY_KEY` (app/decision_providers.py). This used to
    # read the removed vendor's `DECISION_KEY`, which nothing sets any more, so the script refused to run
    # for ever after the vendor was deleted.
    if not decision_providers.configured():
        print("run_pathway_demo: no decision provider is configured (set LUNA_FOUNDRY_KEY): refusing to "
              "run, because an empty gate would render every step", file=sys.stderr)
        return 2
    document = asyncio.run(_run())
    RESULTS.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                      encoding="utf-8")
    for row in document["results"]:
        pathway = row.get("pathway")
        if not pathway:
            print(f"  {row['encounter_id']}: {row.get('note')}")
            continue
        print(f"  {row['encounter_id']}: {pathway['rendered']}/{pathway['candidate']} steps rendered, "
              f"dropped={[step['step_id'] + ':' + step['reason'] for step in pathway['dropped']]}")
    print(f"  safety_failures={document['safety_failures']} passed={document['passed']} "
          f"cost=${document['estimated_input_cost_usd']}")
    print(f"  -> {TARGET.relative_to(ROOT)}")
    return 0 if document["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
