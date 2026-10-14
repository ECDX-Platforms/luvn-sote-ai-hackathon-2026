#!/usr/bin/env python
"""Adjudicate the verifier rule with an independent labeller.

The shipped verifier and the evaluation rule disagreed on two of twenty-nine sentences. While the classifier
was a third party that difference looked like a model property; now that one model answers both, it is a
**rule discrepancy in our own code**, and it can be settled by asking a model that is not the verifier at all
to label the sentences from their cited sources alone.

So: `gpt-6.1-sol` labels each accepted sentence as `supported`, `unsupported` or `unknown`, seeing only that
sentence and the records it cites. The runtime rule and the evaluation rule are then scored against it. This
does not make the labeller a gold standard — it is a third opinion, and the artifact says so — but it is
independent of the code under test, which is what the previous two-way comparison lacked.

    .venv/bin/python scripts/run_verifier_adjudication.py
    .venv/bin/python scripts/run_verifier_adjudication.py --check
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app import decision_evals as ev, decision_providers, llm  # noqa: E402
from app.encounters import resolve_encounter  # noqa: E402
from app.fixtures import load_decision_eval_personas, load_personas  # noqa: E402
from app.policy import cut_for_encounter  # noqa: E402

import run_decision_eval as e1  # noqa: E402
import run_decision_value_eval as evalmod  # noqa: E402

RESULTS = ROOT / ".scratch" / "retrieval-and-knowledge" / "results"
TARGET = RESULTS / "verifier-adjudication-v1.json"
LABELLER = "gpt-6.1-sol"


class LabelledSentence(BaseModel):
    sentence_id: str
    verdict: str = Field(description="One of: supported, unsupported, unknown")
    reason: str


class Labelling(BaseModel):
    labels: list[LabelledSentence]


INSTRUCTIONS = """You are labelling sentences for factual support. For each sentence you are given only the
sentence and the records it cites. Decide, for each: `supported` if every factual claim in the sentence
follows from those records; `unsupported` if a claim is contradicted by them or absent from them; `unknown`
if the records do not contain enough to decide. Judge only against the records given, not against what you
know about the world. Answer for every sentence."""


def _sources(record) -> str:
    return json.dumps({"record_id": record.record_id, "record_type": record.record_type,
                       "topic": record.topic, "recorded_at": record.recorded_at.isoformat(),
                       "payload": source.basics_payload(record, "model")}, ensure_ascii=False)


async def _run() -> dict:
    personas = {persona.persona_id: persona
                for persona in load_personas() + load_decision_eval_personas()}
    rows: list[dict] = []
    for encounter_id in e1._inventory():
        encounter, persona = resolve_encounter(encounter_id, personas, evalmod.EVAL_ENCOUNTERS)
        permitted = {record.record_id: record for record in cut_for_encounter(persona, encounter).records}
        _picture, captured, _elapsed = await e1._picture(encounter_id, personas, with_narration=True)
        sentences = e1._grounding_sentences(captured, permitted)
        if not sentences:
            continue
        # The runtime rule's own verdicts come from the picture, because that is the rule as it actually
        # ran; the evaluation rule is applied separately so the two arms can be compared to the labeller.
        runtime_rule = {verdict["sentence_id"]: verdict["verdict"]
                        for verdict in (captured.get("verifier") or {}).get("verdicts", [])}
        payload = [{"sentence_id": sentence["sentence_id"], "sentence": sentence["text"],
                    "cited_records": sentence["cited_sources"]} for sentence in sentences]
        original = os.environ.get("LUNA_FOUNDRY_MODEL")
        os.environ["LUNA_FOUNDRY_MODEL"] = LABELLER
        try:
            result = await llm._complete_bounded(  # noqa: SLF001 - the labeller is the point
                Labelling, INSTRUCTIONS, json.dumps({"sentences": payload}, ensure_ascii=False), 120.0)
        finally:
            if original is None:
                os.environ.pop("LUNA_FOUNDRY_MODEL", None)
            else:
                os.environ["LUNA_FOUNDRY_MODEL"] = original
        labels = {label.sentence_id: label for label in result.output.labels}
        provider = decision_providers.provider()
        evaluated = await ev.judge_grounding(encounter.role, encounter.purpose or "", sentences,
                                             timeout_s=30.0, call=provider.call)
        evaluation_rule = ev.grounding_row(evaluated["rows"])["verdicts"]
        rows.append({"encounter_id": encounter_id, "labeller_model": result.model,
                     "sentences": [{"sentence_id": sentence["sentence_id"], "text": sentence["text"],
                                    "cited_record_ids": [cited["record_id"]
                                                         for cited in sentence["cited_sources"]],
                                    "independent_label": labels[sentence["sentence_id"]].verdict
                                    if sentence["sentence_id"] in labels else None,
                                    "independent_reason": labels[sentence["sentence_id"]].reason
                                    if sentence["sentence_id"] in labels else None,
                                    "runtime_rule": runtime_rule.get(sentence["sentence_id"]),
                                    "evaluation_rule": evaluation_rule.get(sentence["sentence_id"])}
                                   for sentence in sentences],
                     "served_by": (provider.model if provider else None)})
    return rows


def _check() -> int:
    if not TARGET.exists():
        print(f"check: no artifact at {TARGET}", file=sys.stderr)
        return 1
    document = json.loads(TARGET.read_text(encoding="utf-8"))
    problems: list[str] = []
    sentences = [row for encounter in document["encounters"] for row in encounter["sentences"]]
    if not sentences:
        problems.append("no sentence was adjudicated, so the artifact proves nothing")
    unlabelled = [row["sentence_id"] for row in sentences if not row.get("independent_label")]
    if unlabelled:
        problems.append(f"the labeller skipped {len(unlabelled)} sentence(s)")
    for name in ("the_two_rules_differ_on", "totals"):
        if name not in document:
            problems.append(f"the artifact omits {name}")
    for problem in problems:
        print(f"check: {problem}", file=sys.stderr)
    totals = document.get("totals", {})
    print(f"check: {len(sentences)} sentences labelled by {document['labeller']}; runtime rule agrees "
          f"{totals.get('runtime_rule_agrees')}/{totals.get('labelled')}, evaluation rule agrees "
          f"{totals.get('evaluation_rule_agrees')}/{totals.get('labelled')}; the two rules differ on "
          f"{totals.get('rule_disagreements')}, and both disagree with the labeller on "
          f"{totals.get('both_rules_disagree_with_labeller')}")
    return 1 if problems else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Adjudicate the verifier rule with an independent labeller")
    parser.add_argument("--check", action="store_true", help="verify the committed artifact")
    args = parser.parse_args(argv)
    if args.check:
        return _check()
    if not llm.foundry_configured():
        print("run_verifier_adjudication: Foundry is not configured", file=sys.stderr)
        return 2
    encounters = asyncio.run(_run())
    sentences = [row for encounter in encounters for row in encounter["sentences"]]
    labelled = [row for row in sentences if row.get("independent_label")]
    def agree(field: str) -> int:
        return sum(1 for row in labelled if row.get(field) == row["independent_label"])

    def rule_disagreements() -> list[dict]:
        """Sentences where the two rules are not identical — the discrepancy being adjudicated."""
        return [{"encounter_id": encounter["encounter_id"], "sentence_id": row["sentence_id"],
                 "runtime_rule": row["runtime_rule"], "evaluation_rule": row["evaluation_rule"],
                 "independent_label": row["independent_label"], "reason": row["independent_reason"]}
                for encounter in encounters for row in encounter["sentences"]
                if row["runtime_rule"] != row["evaluation_rule"]]

    def both_rules_wrong() -> list[str]:
        return [row["sentence_id"] for row in labelled
                if row["runtime_rule"] != row["independent_label"]
                and row.get("evaluation_rule") != row["independent_label"]]
    document = {"kind": "verifier-adjudication",
                "generated_by": "backend/scripts/run_verifier_adjudication.py",
                "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat()
                .replace("+00:00", "Z"),
                "labeller": LABELLER, "provider": (decision_providers.provider().model
                                                   if decision_providers.provider() else None),
                "method": ("An independent model labels each accepted sentence from that sentence and its "
                           "cited records alone; the shipped runtime rule is then scored against it. The "
                           "labeller is a third opinion, not a gold standard, but it is not the code under "
                           "test."),
                "totals": {"sentences": len(sentences), "labelled": len(labelled),
                           "runtime_rule_agrees": agree("runtime_rule"),
                           "evaluation_rule_agrees": agree("evaluation_rule"),
                           "rule_disagreements": len(rule_disagreements()),
                           "both_rules_disagree_with_labeller": len(both_rules_wrong())},
                "the_two_rules_differ_on": rule_disagreements(), "encounters": encounters,
                "limits": ["One labeller, one pass: a third opinion, not adjudication by ground truth.",
                           "The labeller sees only cited records, so it cannot catch a sentence that is true "
                           "but uncited, which the runtime rule handles separately."]}
    RESULTS.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                      encoding="utf-8")
    return _check()


if __name__ == "__main__":
    raise SystemExit(main())
