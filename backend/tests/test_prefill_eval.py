"""The offline pre-fill evaluation: cases come from the corpus, and `--check` refuses a row citing outside it."""
import copy
import json

import pytest

from scripts import run_prefill_eval as evaluation

ROOT = evaluation.ROOT


@pytest.fixture(scope="module")
def personas():
    return evaluation._personas()


def test_cases_are_every_bound_encounter_with_a_note(personas):
    cases = evaluation._cases(personas)
    assert cases, "no bound fixture encounter carries a note"
    for case in cases:
        permitted = evaluation._permitted_records(case["case_id"], personas)
        assert case["record_ids"] == list(permitted), "the case scope is the permitted set after the cut"
        assert evaluation._has_note(permitted.values())


def test_committed_artifact_passes_the_offline_check(personas):
    document = json.loads(evaluation.ARTIFACT.read_text(encoding="utf-8"))
    assert evaluation.problems(document, personas) == []


def test_check_refuses_a_row_citing_outside_its_own_picture(personas):
    document = json.loads(evaluation.ARTIFACT.read_text(encoding="utf-8"))
    tampered = copy.deepcopy(document)
    row = tampered["results"][0]
    row["answered"] = 1
    row["answered_items"] = [{"question_id": "q1", "answer_text": "kyllä",
                              "source_record_ids": ["bound-note-not-permitted"]}]
    # Totals are recomputed so the only remaining problem is the permission rule itself.
    tampered["totals"] = evaluation._totals(tampered["encounters"], tampered["results"])
    found = evaluation.problems(tampered, personas)
    assert any("outside the encounter's permitted set" in problem for problem in found), found


def test_check_refuses_a_figure_the_cited_record_lacks(personas):
    document = json.loads(evaluation.ARTIFACT.read_text(encoding="utf-8"))
    tampered = copy.deepcopy(document)
    row = tampered["results"][0]
    permitted = evaluation._permitted_records(row["case_id"], personas)
    row["answered"] = 1
    row["answered_items"] = [{"question_id": "q1", "answer_text": "arvo 987654321",
                              "source_record_ids": [next(iter(permitted))]}]
    tampered["totals"] = evaluation._totals(tampered["encounters"], tampered["results"])
    found = evaluation.problems(tampered, personas)
    assert any("a figure its cited records do not carry" in problem for problem in found), found


def test_check_refuses_a_stale_scope_digest(personas):
    document = json.loads(evaluation.ARTIFACT.read_text(encoding="utf-8"))
    tampered = dict(document, case_scope_digest="sha256:0")
    assert any("case scope digest" in problem for problem in evaluation.problems(tampered, personas))


def test_offline_artifact_cannot_claim_a_generated_form(personas):
    document = json.loads(evaluation.ARTIFACT.read_text(encoding="utf-8"))
    assert document["model_enabled"] is False
    tampered = copy.deepcopy(document)
    tampered["results"][0]["generation_status"] = "generated"
    tampered["totals"] = evaluation._totals(tampered["encounters"], tampered["results"])
    assert any("without a model" in problem for problem in evaluation.problems(tampered, personas))
