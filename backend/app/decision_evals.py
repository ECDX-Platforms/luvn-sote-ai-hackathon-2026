"""LUVN's Decision-1 judgments, shaped as evaluation rows.

One Decision-1 call per metric per encounter. Each metric is split into small typed questions, the answers are
combined in code, and every check is published, so a run is explainable without prose. That is the
pattern Microsoft documents for retrieved passages ("decide in code which ones reach the answering
model") and the shape the community `decision-foundry-judge` project uses for Foundry evaluators.

**Deliberately independent of the runtime rules.** `grounding_verdict` is *simpler* than the verifier in
`decision_model.verify_narration`, because an evaluator that reuses the runtime's exact rule cannot
disagree with it. Where the two differ on the same recorded sentence, the difference is the finding.

No Azure import lives here and no HTTP client is constructed here: the transport is
`decision_model.request_judgment`, so the offline gate exercises every combination rule by injecting
recorded answers.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from . import decision_model

SELECTION_METRIC = "selection_quality"
GROUNDING_METRIC = "grounding_support"

# Read from the runtime gate so one judgment and one selection use the same floors. Both are parameters
# so a recorded answer set can be replayed under different thresholds without touching the defaults.
SELECTION_FLOOR = decision_model.ADDITION_FLOOR
GROUNDING_FLOOR = decision_model.OMISSION_FLOOR

# One request has to stay under the transport's byte cap, so candidate questions are split into batches.
BATCH_BUDGET_BYTES = 48_000
_GROUNDING_LEVELS = ("supported", "unsupported", "unknown")


@dataclass(frozen=True)
class Check:
    """One explainable step behind a metric score."""

    name: str
    passed: bool
    detail: str

    def as_dict(self) -> dict:
        return {"name": self.name, "passed": self.passed, "detail": self.detail}


def _request_size(state: Mapping[str, object], questions: Mapping[str, object]) -> int:
    return len(json.dumps({"model": decision_model.MODEL, "state": state, "questions": questions},
                          ensure_ascii=False, sort_keys=True).encode())


def _batched(state: Mapping[str, object], notes: Sequence[tuple[str, str]],
             budget: int | None = None) -> list[list[tuple[str, str]]]:
    """Split candidates so no single request exceeds the transport's byte cap.

    The budget is resolved at call time, so a test can override it without changing the module.
    """
    if budget is None:
        budget = BATCH_BUDGET_BYTES
    batches: list[list[tuple[str, str]]] = []
    current: list[tuple[str, str]] = []
    for note in notes:
        candidate = [*current, note]
        if current and _request_size(_selection_state(state, candidate),
                                     _selection_questions(candidate)) > budget:
            batches.append(current)
            current = [note]
        else:
            current = candidate
    if current:
        batches.append(current)
    return batches


# --- selection quality ------------------------------------------------------------------------------

def _selection_state(prefix: Mapping[str, object], notes: Sequence[tuple[str, str]]) -> dict:
    return {**prefix, "candidates": [{"record_id": record_id, "text": text} for record_id, text in notes]}


def _selection_questions(notes: Sequence[tuple[str, str]]) -> dict:
    return {
        f"relevant:{record_id}": {
            "type": "noul",
            "instructions": "Onko tämä Kirjaus olennainen juuri tämän ammattilaisroolin ja "
                            "palvelukontekstin käynnin valmistelussa? Pelkkä avainsana ei riitä.",
            "criteria": {"true": "Kirjaus auttaa valmistautumaan tähän käyntiin.",
                         "false": "Kirjaus ei auta tässä käynnissä."},
        }
        for record_id, _text in notes
    }


def selection_state(role: str, purpose: str, description: str) -> dict:
    """The encounter half of the state; candidate texts are added per batch."""
    return {"encounter_role": role, "encounter_purpose": purpose, "purpose_description": description}


async def judge_selection(prefix: Mapping[str, object], notes: Sequence[tuple[str, str]], *,
                          timeout_s: float = decision_model.CALL_BUDGET_S, call=None) -> dict:
    """Ask one relevance question per candidate; returns answers **keyed by record id**.

    The request keys each question as `relevant:<record_id>` because the model needs a stable question name;
    the answer map is re-keyed here so every caller reads the same shape `selection_row` scores.

    `call` is the transport: `(state, questions, *, timeout_s) -> envelope`. It defaults to the Foundry
    different decision provider implementing the same protocol can be compared without touching any
    question, threshold or validator.
    """
    if call is None:
        call = decision_model.request_judgment
    answers: dict[str, dict] = {}
    usage = {"input_tokens": 0, "output_tokens": 0}
    for batch in _batched(prefix, notes):
        data = await call(_selection_state(prefix, batch), _selection_questions(batch), timeout_s=timeout_s)
        answers.update({record_id: data["answers"][f"relevant:{record_id}"] for record_id, _text in batch})
        raw_usage = data.get("usage")
        if isinstance(raw_usage, dict):
            for field in ("input_tokens", "output_tokens"):
                value = raw_usage.get(field)
                if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                    usage[field] += value
    return {"answers": answers, "usage": usage}


def selection_row(answers: Mapping[str, object], expected: Mapping[str, bool], *,
                  floor: float = SELECTION_FLOOR) -> dict:
    """Combine per-candidate answers into one explainable metric row.

    `answers` is keyed by record id (the shape `judge_selection` returns). `expected` is the authored
    key: `True` means "authored as relevant for this purpose". Notes without a key are reported but
    never counted as passes, so the denominator stays the authored keys.
    """
    checks: list[Check] = []
    verdicts: dict[str, bool] = {}
    probabilities: dict[str, float] = {}
    for record_id, answer in answers.items():
        if record_id.startswith("relevant:"):
            raise ValueError(f"selection answers must be keyed by record id, not question name: {record_id}")
        probability = decision_model.noul_answer(answer, f"relevance:{record_id}")
        verdict = probability >= floor
        verdicts[record_id] = verdict
        probabilities[record_id] = probability
        keyed = record_id in expected
        agreed = keyed and verdict == expected[record_id]
        checks.append(Check(
            name=f"judged:{record_id}", passed=agreed or not keyed,
            detail=f"author={'relevant' if expected[record_id] else 'not_relevant'}"
                   if keyed else "no authored key",
        ))
    wrong_additions = sorted(record_id for record_id, verdict in verdicts.items()
                             if verdict and record_id in expected and not expected[record_id])
    missed_expected = sorted(record_id for record_id, verdict in verdicts.items()
                             if not verdict and record_id in expected and expected[record_id])
    keyed = sorted(record_id for record_id in verdicts if record_id in expected)
    score = max(1, min(5, 5 - 2 * len(wrong_additions) - len(missed_expected)))
    return {
        "metric": SELECTION_METRIC,
        "score": score,
        "passed": not wrong_additions and not missed_expected,
        "judged": sorted(verdicts),
        "keyed": keyed,
        "wrong_additions": wrong_additions,
        "missed_expected_additions": missed_expected,
        "probabilities": probabilities,
        "checks": [check.as_dict() for check in checks],
        "reason": ("no authored key disagreed with the judge" if not wrong_additions and not missed_expected
                   else f"wrong_additions={wrong_additions} missed_expected={missed_expected}"),
    }


# --- grounding support ------------------------------------------------------------------------------

def grounding_state(role: str, purpose: str, sentence: str, cited: Sequence[Mapping[str, object]]) -> dict:
    """One sentence and only the records it cites, projected through the model-safe payload."""
    return {"encounter_role": role, "encounter_purpose": purpose,
            "sentence": sentence, "cited_sources": list(cited)}


def grounding_questions(cited_ids: Sequence[str]) -> dict:
    return {
        "support": {"type": "choice",
                    "instructions": "Do all factual claims in this sentence follow from only its cited sources?",
                    "criteria": {"supported": "Every factual claim is explicitly supported.",
                                 "unsupported": "A claim is contradicted or absent from the sources.",
                                 "unknown": "The evidence is insufficient or ambiguous."}},
        "contradicts": {"type": "noul",
                        "instructions": "Does any cited source contradict a factual claim in this sentence?",
                        "criteria": {"true": "A cited source contradicts the sentence.",
                                     "false": "No cited source contradicts the sentence."}},
        **{f"supports:{record_id}": {
            "type": "noul",
            "instructions": "Does this cited source support the factual claims in the sentence?",
            "criteria": {"true": "This source supports the sentence's claims.",
                         "false": "This source does not support them."}}
           for record_id in cited_ids},
    }


async def judge_grounding(role: str, purpose: str, sentences: Sequence[dict], *,
                          timeout_s: float = decision_model.CALL_BUDGET_S, call=None) -> dict:
    """Ask the grounding questions for every sentence; returns one answer set per sentence id."""
    if call is None:
        call = decision_model.request_judgment
    rows: list[dict] = []
    usage = {"input_tokens": 0, "output_tokens": 0}
    for sentence in sentences:
        cited = sentence["cited_sources"]
        cited_ids = [str(source["record_id"]) for source in cited]
        data = await call(grounding_state(role, purpose, sentence["text"], cited),
                          grounding_questions(cited_ids), timeout_s=timeout_s)
        rows.append({"sentence_id": sentence["sentence_id"], "cited_record_ids": cited_ids,
                     "answers": data["answers"]})
        raw_usage = data.get("usage")
        if isinstance(raw_usage, dict):
            for field in ("input_tokens", "output_tokens"):
                value = raw_usage.get(field)
                if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                    usage[field] += value
    return {"rows": rows, "usage": usage}


def grounding_verdict(answers: Mapping[str, object], cited_ids: Sequence[str], *,
                      floor: float = GROUNDING_FLOOR) -> tuple[str, dict]:
    """A simpler rule than the runtime verifier, so the two can disagree and the disagreement means something.

    **Do not make this identical to the runtime rule.** A review asked for exactly that, and it would make the
    two agree by construction -- including on the runtime's errors -- turning the cross-check into a
    tautology and hiding the two sentences the runtime refuses that an independent label supports. The
    disagreement is the instrument; issues/13 records the decision and the evidence.

    Supported requires the sentence's own support choice to lead and clear the floor, no contradiction
    above the floor, and *every* cited source to support the sentence.
    """
    support = decision_model.choice_answer(answers["support"], _GROUNDING_LEVELS, "support")
    contradiction = decision_model.noul_answer(answers["contradicts"], "contradiction")
    supporting = {record_id for record_id in cited_ids
                  if decision_model.noul_answer(answers[f"supports:{record_id}"],
                                               f"support:{record_id}") >= floor}
    probabilities = support["probabilities"]
    if contradiction >= floor or (support["choice"] == "unsupported"
                                  and probabilities["unsupported"] >= floor):
        verdict = "unsupported"
    elif (support["choice"] == "supported" and probabilities["supported"] >= floor
          and probabilities["supported"] >= max(probabilities["unsupported"], probabilities["unknown"])
          and set(cited_ids) <= supporting):
        verdict = "supported"
    else:
        verdict = "unknown"
    evidence = {"support": support, "contradiction_probability": contradiction,
                "supporting_record_ids": sorted(supporting)}
    return verdict, evidence


def grounding_row(rows: Sequence[Mapping[str, object]], *, floor: float = GROUNDING_FLOOR) -> dict:
    """Combine per-sentence verdicts into one explainable metric row."""
    checks: list[Check] = []
    verdicts: dict[str, str] = {}
    contradictions: list[str] = []
    for row in rows:
        sentence_id = str(row["sentence_id"])
        cited_ids = [str(record_id) for record_id in row["cited_record_ids"]]
        verdict, evidence = grounding_verdict(row["answers"], cited_ids, floor=floor)
        verdicts[sentence_id] = verdict
        checks.append(Check(name=f"supported:{sentence_id}", passed=verdict == "supported",
                            detail=f"verdict={verdict} contradiction="
                                   f"{evidence['contradiction_probability']:.2f}"))
        if evidence["contradiction_probability"] >= floor:
            contradictions.append(sentence_id)
    supported = sorted(sentence_id for sentence_id, verdict in verdicts.items() if verdict == "supported")
    if contradictions:
        score = 2
    elif verdicts and len(supported) == len(verdicts):
        score = 5
    elif verdicts and len(supported) >= len(verdicts) / 2:
        score = 4
    else:
        score = 3
    return {
        "metric": GROUNDING_METRIC,
        "score": score,
        "passed": bool(verdicts) and not contradictions and len(supported) == len(verdicts),
        "sentences": len(verdicts),
        "supported": supported,
        "contradictions": contradictions,
        "verdicts": verdicts,
        "checks": [check.as_dict() for check in checks],
        "reason": (f"supported={len(supported)}/{len(verdicts)} contradictions={contradictions}"),
    }
