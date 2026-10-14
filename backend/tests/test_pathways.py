"""Offline tests for the pathway gate — the part that must fail closed.

No network and no Decision-1: recorded answers are injected, so every drop reason is exercised in the gate.
"""

from __future__ import annotations

import json

from app import pathways


def noul(probability: float) -> dict:
    return {"type": "noul", "noul": probability}


def _entry(entry_id: str) -> dict:
    return next(entry for entry in pathways.load_corpus()["entries"] if entry["entry_id"] == entry_id)


def test_the_corpus_labels_itself_as_a_stand_in():
    corpus = pathways.load_corpus()
    assert corpus["illustrative"] is True
    assert "NOT UpToDate" in corpus["provenance"]
    for entry in corpus["entries"]:
        assert entry["steps"], entry["entry_id"]
        assert all(step["step_id"] and step["text"] for step in entry["steps"])


def test_retrieval_finds_the_medication_entry_for_a_medication_query():
    found = pathways.retrieve("Lääkityksen seuranta: toteutuminen, annos ja haitat sairaanhoitaja")
    assert found and found[0]["entry_id"] == "medication_follow_up"


def test_retrieval_returns_nothing_for_an_unrelated_query():
    assert pathways.retrieve("avaruusaluksen moottorinvaihto") == []


def test_a_step_that_contradicts_the_record_is_dropped_with_its_reason():
    answers = {"s1": {"relevant": noul(0.9), "contradicts_records": noul(0.05)},
               "s2": {"relevant": noul(0.9), "contradicts_records": noul(0.05)},
               "s3": {"relevant": noul(0.9), "contradicts_records": noul(0.05)},
               "s4": {"relevant": noul(0.95), "contradicts_records": noul(0.93)}}
    pathway = pathways.assemble(_entry("medication_follow_up"), answers)
    rendered = [step["step_id"] for step in pathway["steps"]]
    dropped = {step["step_id"]: step["reason"] for step in pathway["dropped"]}
    assert rendered == ["s1", "s2", "s3"]
    assert dropped["s4"] == "contradicted_by_record"
    assert pathway["candidate"] == 4 and pathway["rendered"] == 3


def test_a_contradiction_outranks_relevance():
    """A step can be highly relevant and still unsafe; the contradiction must win."""
    answers = {"s4": {"relevant": noul(0.99), "contradicts_records": noul(0.9)}}
    pathway = pathways.assemble(_entry("medication_follow_up"), answers)
    assert pathway["rendered"] == 0
    assert pathway["dropped"][-1]["reason"] == "contradicted_by_record"


def test_an_irrelevant_step_is_dropped_and_says_so():
    answers = {"s1": {"relevant": noul(0.2), "contradicts_records": noul(0.01)}}
    pathway = pathways.assemble(_entry("medication_follow_up"), answers)
    assert pathway["steps"] == []
    assert pathway["dropped"][0]["reason"] == "not_relevant"


def test_a_missing_or_malformed_answer_fails_closed():
    pathway = pathways.assemble(_entry("mental_health_assessment"),
                                {"s1": {"relevant": noul(0.9)},
                                 "s2": {"relevant": {"type": "noul", "noul": True}, "contradicts_records": noul(0.1)}})
    reasons = {step["step_id"]: step["reason"] for step in pathway["dropped"]}
    assert reasons["s1"] == "invalid_answer"      # contradicts_records missing
    assert reasons["s2"] == "invalid_answer"      # a boolean is not a probability
    assert reasons["s3"] == "no_answer"
    assert pathway["abstained"] is True


def test_every_accepted_step_carries_a_corpus_reference():
    answers = {"s1": {"relevant": noul(0.9), "contradicts_records": noul(0.02)}}
    pathway = pathways.assemble(_entry("functioning_service_needs"), answers)
    step = pathway["steps"][0]
    assert step["ref"] == "ref:pathway-corpus@pathways-v1#functioning_service_needs.s1"
    assert step["illustrative"] is True
    assert "NOT UpToDate" in step["provenance"]


def test_the_gate_state_carries_only_the_model_safe_evidence():
    step = pathways.PathwayStep(entry_id="e", step_id="s", text="t", version="v1", illustrative=True,
                                provenance="p")
    state = pathways.gate_state("sairaanhoitaja", "medication_follow_up", step,
                                [{"record_id": "r1", "payload": {}}])
    assert state["pathway_step"] == "t"
    assert state["person_records"] == [{"record_id": "r1", "payload": {}}]
    assert json.dumps(state)  # serialisable, so it can be sent as-is


def test_a_contradiction_just_below_the_ceiling_still_renders():
    """The gate has no decision margin, so 0.69 renders. Pinned so the limit is visible rather than implied."""
    answers = {"s1": {"relevant": noul(0.9), "contradicts_records": noul(0.69)}}
    pathway = pathways.assemble(_entry("functioning_service_needs"), answers)
    assert [step["step_id"] for step in pathway["steps"]] == ["s1"]


def test_the_ceiling_is_the_only_thing_that_stops_a_contradiction():
    """0.70 drops it; 0.69 does not. The boundary is the whole safety argument, so it is pinned on both sides."""
    below = pathways.assemble(_entry("functioning_service_needs"),
                              {"s1": {"relevant": noul(0.9), "contradicts_records": noul(0.70)}})
    assert below["steps"] == [] and below["dropped"][0]["reason"] == "contradicted_by_record"
