"""Offline tests for the Decision-1 evaluation judges — recorded answers in, explainable rows out.

No network: `decision_model.request_judgment` is replaced, so every combination rule and every batching
decision is exercised in the gate.
"""

from __future__ import annotations

import asyncio

import pytest

from app import decision_evals as ev
from app import decision_model


def noul(probability: float) -> dict:
    return {"type": "noul", "noul": probability}


def choice(label: str, probabilities: dict[str, float]) -> dict:
    return {"type": "choice", "choice": label, "probabilities": probabilities,
            "confidence": max(probabilities.values())}


# --- selection quality -----------------------------------------------------------------------------

def test_selection_row_scores_a_full_agreement_at_five():
    answers = {"note-a": noul(0.91), "note-b": noul(0.05)}
    row = ev.selection_row(answers, {"note-a": True, "note-b": False})
    assert row["metric"] == ev.SELECTION_METRIC
    assert row["score"] == 5 and row["passed"] is True
    assert row["keyed"] == ["note-a", "note-b"]
    assert row["wrong_additions"] == [] and row["missed_expected_additions"] == []
    assert [check["name"] for check in row["checks"]] == ["judged:note-a", "judged:note-b"]


def test_selection_row_scores_a_wrong_addition_below_a_missed_one():
    wrong = ev.selection_row({"note-b": noul(0.88)}, {"note-b": False})
    missed = ev.selection_row({"note-a": noul(0.11)}, {"note-a": True})
    assert wrong["score"] == 3 and wrong["passed"] is False
    assert wrong["wrong_additions"] == ["note-b"]
    assert missed["score"] == 4 and missed["passed"] is False
    assert missed["missed_expected_additions"] == ["note-a"]


def test_selection_row_reports_an_unkeyed_note_without_counting_it():
    row = ev.selection_row({"note-z": noul(0.99)}, {})
    assert row["passed"] is True and row["score"] == 5
    assert row["keyed"] == [] and row["judged"] == ["note-z"]
    assert row["checks"][0]["detail"] == "no authored key"


def test_selection_row_rejects_a_malformed_noul_answer():
    with pytest.raises(ValueError):
        ev.selection_row({"note-a": {"type": "noul", "noul": True}}, {"note-a": True})
    with pytest.raises(ValueError):
        ev.selection_row({"note-a": noul(1.4)}, {"note-a": True})


def test_judge_selection_splits_batches_and_sums_usage(monkeypatch):
    calls: list[list[str]] = []

    async def fake_request_judgment(state, questions, *, timeout_s):
        calls.append([question.split(":", 1)[1] for question in questions])
        assert [candidate["record_id"] for candidate in state["candidates"]] == calls[-1]
        answers = {question: noul(0.8) for question in questions}
        return {"model": decision_model.MODEL, "answers": answers,
                "usage": {"input_tokens": 10, "output_tokens": 0}}

    monkeypatch.setattr(decision_model, "request_judgment", fake_request_judgment)
    monkeypatch.setattr(ev, "BATCH_BUDGET_BYTES", 1)
    notes = [("note-a", "x"), ("note-b", "y"), ("note-c", "z")]
    result = asyncio.run(ev.judge_selection({"encounter_role": "sairaanhoitaja",
                                             "encounter_purpose": "medication_follow_up"}, notes))
    assert calls == [["note-a"], ["note-b"], ["note-c"]]
    # The answer map is re-keyed by record id, which is the shape `selection_row` scores.
    assert sorted(result["answers"]) == ["note-a", "note-b", "note-c"]
    assert result["usage"] == {"input_tokens": 30, "output_tokens": 0}


def test_selection_row_refuses_question_keyed_answers():
    with pytest.raises(ValueError, match="keyed by record id"):
        ev.selection_row({"relevant:note-a": noul(0.9)}, {"note-a": True})


def test_judge_selection_keeps_requests_under_the_budget_by_default(monkeypatch):
    sizes: list[int] = []

    async def fake_request_judgment(state, questions, *, timeout_s):
        sizes.append(ev._request_size(state, questions))
        return {"model": decision_model.MODEL,
                "answers": {question: noul(0.5) for question in questions}, "usage": {}}

    monkeypatch.setattr(decision_model, "request_judgment", fake_request_judgment)
    notes = [(f"note-{index}", "t" * 2_000) for index in range(40)]
    asyncio.run(ev.judge_selection({"encounter_role": "sairaanhoitaja", "encounter_purpose": "x"}, notes))
    assert sizes and max(sizes) <= ev.BATCH_BUDGET_BYTES
    assert len(sizes) > 1


# --- grounding support -----------------------------------------------------------------------------

def _grounding_answers(cited: list[str], *, support: str = "supported", supported_probability: float = 0.92,
                       contradiction: float = 0.01, per_source: float = 0.9) -> dict:
    probabilities = {"supported": 0.0, "unsupported": 0.0, "unknown": 0.0}
    probabilities[support] = supported_probability
    remaining = max(0.0, round(1 - supported_probability, 2))
    other = [label for label in ("supported", "unsupported", "unknown") if label != support]
    probabilities[other[0]] = remaining
    return {"support": choice(support, probabilities), "contradicts": noul(contradiction),
            **{f"supports:{record_id}": noul(per_source) for record_id in cited}}


def test_grounding_verdict_requires_every_cited_source_to_support():
    verdict, evidence = ev.grounding_verdict(_grounding_answers(["a", "b"]), ["a", "b"])
    assert verdict == "supported" and evidence["supporting_record_ids"] == ["a", "b"]

    partial = _grounding_answers(["a", "b"])
    partial["supports:b"] = noul(0.1)
    verdict, evidence = ev.grounding_verdict(partial, ["a", "b"])
    assert verdict == "unknown" and evidence["supporting_record_ids"] == ["a"]


def test_grounding_verdict_treats_a_contradiction_as_unsupported():
    answers = _grounding_answers(["a"], contradiction=0.88)
    verdict, evidence = ev.grounding_verdict(answers, ["a"])
    assert verdict == "unsupported" and evidence["contradiction_probability"] == 0.88


def test_grounding_row_caps_the_score_at_two_on_a_contradiction():
    rows = [
        {"sentence_id": "s1", "cited_record_ids": ["a"], "answers": _grounding_answers(["a"])},
        {"sentence_id": "s2", "cited_record_ids": ["b"], "answers": _grounding_answers(["b"], contradiction=0.9)},
    ]
    row = ev.grounding_row(rows)
    assert row["metric"] == ev.GROUNDING_METRIC
    assert row["score"] == 2 and row["passed"] is False
    assert row["contradictions"] == ["s2"] and row["supported"] == ["s1"]


def test_grounding_row_scores_all_supported_at_five():
    rows = [{"sentence_id": "s1", "cited_record_ids": ["a"], "answers": _grounding_answers(["a"])}]
    row = ev.grounding_row(rows)
    assert row["score"] == 5 and row["passed"] is True
    assert row["checks"] == [{"name": "supported:s1", "passed": True, "detail": "verdict=supported contradiction=0.01"}]


def test_grounding_row_is_not_passed_on_an_empty_sentence_set():
    row = ev.grounding_row([])
    assert row["passed"] is False and row["sentences"] == 0


def test_decisional_judgment_without_a_key_is_a_failure_not_a_silent_empty(monkeypatch):
    monkeypatch.delenv("LUNA_FOUNDRY_KEY", raising=False)
    with pytest.raises(RuntimeError):
        asyncio.run(decision_model.request_judgment({"a": 1}, {"q": {"type": "noul", "instructions": "x"}}))
