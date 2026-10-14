#!/usr/bin/env python
"""Does the verifier's question language change its verdicts?

The model's own documentation names **wording sensitivity** as a limitation: scores change with how a question
and its options are phrased, and poorly framed questions still return scores. It documents no language
coverage at all. And the shipped verifier asks its questions in **English** while the sentences and the
evidence it judges are **Finnish**.

That is a testable defect rather than an opinion. This sends the same sentence and the same cited sources to
the same model twice: once with the shipped English questions, once with the same questions in Finnish. The
metric is the verdict flip rate — how often the decision changes when only the question language does. A rule
whose output moves with the language of its own instructions is not measuring the sentence.

    .venv/bin/python scripts/run_verifier_language_ablation.py
    .venv/bin/python scripts/run_verifier_language_ablation.py --check
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app import decision_evals as ev, decision_providers  # noqa: E402
from app.encounters import resolve_encounter  # noqa: E402
from app.fixtures import load_decision_eval_personas, load_personas  # noqa: E402
from app.policy import cut_for_encounter  # noqa: E402

import run_decision_eval as e1  # noqa: E402
import run_decision_value_eval as evalmod  # noqa: E402

RESULTS = ROOT / ".scratch" / "retrieval-and-knowledge" / "results"
TARGET = RESULTS / "verifier-language-ablation-v1.json"
TIMEOUT_S = 30.0

# The same questions, in the language of the sentences they judge. Only the wording moves.
FINNISH = {
    "support": ("Seuraavatko kaikki lauseen asiaväittämät yksinomaan sen viitatuista lähteistä?", {
        "supported": "Jokainen asiaväittämä on nimenomaisesti tuettu.",
        "unsupported": "Jokin väittämä on ristiriidassa lähteiden kanssa tai puuttuu niistä.",
        "unknown": "Näyttö on riittämätöntä tai tulkinnanvaraista."}),
    "contradicts": ("Onko jokin viitattu lähde ristiriidassa lauseen asiaväittämän kanssa?",
                    {"true": "Viitattu lähde on ristiriidassa lauseen kanssa.",
                     "false": "Mikään viitatuista lähteistä ei ole ristiriidassa lauseen kanssa."}),
    "supports": ("Tukeeko tämä viitattu lähde lauseen asiaväittämiä?",
                 {"true": "Tämä lähde tukee lauseen väittämiä.",
                  "false": "Tämä lähde ei tue niitä."}),
}


def _finnish_questions(cited_ids) -> dict:
    questions = {
        "support": {"type": "choice", "instructions": FINNISH["support"][0],
                    "criteria": FINNISH["support"][1]},
        "contradicts": {"type": "noul", "instructions": FINNISH["contradicts"][0],
                        "criteria": FINNISH["contradicts"][1]},
    }
    for record_id in cited_ids:
        questions[f"supports:{record_id}"] = {
            "type": "noul", "instructions": FINNISH["supports"][0], "criteria": FINNISH["supports"][1]}
    return questions


async def _run() -> dict:
    provider = decision_providers.provider()
    if provider is None:
        raise SystemExit("run_verifier_language_ablation: no decision provider is configured")
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
        for sentence in sentences:
            cited_ids = [cited["record_id"] for cited in sentence["cited_sources"]]
            state = ev.grounding_state(encounter.role, encounter.purpose or "", sentence["text"],
                                       sentence["cited_sources"])
            english_questions = ev.grounding_questions(cited_ids)
            finnish_questions = _finnish_questions(cited_ids)
            # Judging one sentence at a time keeps the comparison exact and the calls independent.
            english_answers = await provider.call(state, english_questions, timeout_s=TIMEOUT_S)
            finnish_answers = await provider.call(state, finnish_questions, timeout_s=TIMEOUT_S)
            # `grounding_verdict` returns (verdict, detail); compare the verdict, not the explanation, or
            # every sentence "flips" because the detail dictionaries differ in wording.
            english_verdict = ev.grounding_verdict(english_answers["answers"], cited_ids)[0]
            finnish_verdict = ev.grounding_verdict(finnish_answers["answers"], cited_ids)[0]
            rows.append({"encounter_id": encounter_id, "sentence_id": sentence["sentence_id"],
                         "english_verdict": english_verdict, "finnish_verdict": finnish_verdict,
                         "flipped": english_verdict != finnish_verdict})
    flips = [row for row in rows if row["flipped"]]
    return {"kind": "verifier-language-ablation",
            "generated_by": "backend/scripts/run_verifier_language_ablation.py",
            "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat()
            .replace("+00:00", "Z"),
            "provider": provider.model,
            "method": ("Each accepted sentence and its cited sources are judged twice by the same model: once "
                       "with the shipped English questions, once with the same questions in Finnish. Only the "
                       "question language changes."),
            "totals": {"sentences": len(rows), "flips": len(flips),
                       "flip_rate": round(len(flips) / len(rows), 4) if rows else None,
                       "english_supported": sum(1 for row in rows
                                                if row["english_verdict"] == "supported"),
                       "finnish_supported": sum(1 for row in rows
                                                if row["finnish_verdict"] == "supported")},
            "flipped_sentences": flips, "sentences": rows,
            "limits": ["One model, one pass, one day: a flip rate is a signal about wording sensitivity, not a "
                       "measurement of which language is better.",
                       "The Finnish wording is our translation, so a flip could be a translation artefact "
                       "rather than a language effect; the translation is recorded verbatim for that reason."]}


def _check() -> int:
    if not TARGET.exists():
        print(f"check: no artifact at {TARGET}", file=sys.stderr)
        return 1
    document = json.loads(TARGET.read_text(encoding="utf-8"))
    problems: list[str] = []
    totals = document["totals"]
    if not totals["sentences"]:
        problems.append("no sentence was judged, so the artifact proves nothing")
    counted = sum(1 for row in document["sentences"] if row["flipped"])
    if counted != totals["flips"]:
        problems.append("the flip count disagrees with its own rows")
    for problem in problems:
        print(f"check: {problem}", file=sys.stderr)
    print(f"check: {totals['sentences']} sentences judged in both languages; "
          f"{totals['flips']} flipped ({totals['flip_rate']}); supported "
          f"{totals['english_supported']} in English vs {totals['finnish_supported']} in Finnish")
    return 1 if problems else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compare verifier verdicts across question languages")
    parser.add_argument("--check", action="store_true", help="verify the committed artifact")
    args = parser.parse_args(argv)
    if args.check:
        return _check()
    document = asyncio.run(_run())
    RESULTS.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                      encoding="utf-8")
    return _check()


if __name__ == "__main__":
    raise SystemExit(main())
