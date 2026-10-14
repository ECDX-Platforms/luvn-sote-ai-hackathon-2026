"""The decision-model protocol is a pinned value, and the pin has to be able to fail.

ADR-0021 decides that Decision-1 stays a typed call and that its protocol — roles, `state` fields,
questions, answer vocabularies, gates — is the auditable surface. A descriptor nobody checks is
documentation; these tests make it a control:

* the pinned digest matches the descriptor, so changing a question without touching the pin fails here;
* the descriptor actually describes the requests the code sends, so the descriptor cannot drift into
  fiction while the digest still agrees with itself;
* the rollback is asserted (removing a question changes the digest), so the pin is not vacuous.

The last one matters most. A digest test that only compares a value to itself passes for every possible
implementation, including one where `digest()` returns a constant.
"""

from __future__ import annotations

import json

import pytest

from app import decision_model, decision_protocol


def test_pinned_digest_matches_the_descriptor():
    assert decision_protocol.SIGNATURE_SHA256 == decision_protocol.digest(), (
        "the protocol descriptor changed without updating SIGNATURE_SHA256. If the change is intended, "
        "update the literal in backend/app/decision_protocol.py and regenerate docs/decision-protocol.md "
        "with backend/scripts/render_decision_protocol.py."
    )


def test_digest_changes_when_a_question_changes():
    """Non-vacuity: the digest is a function of the descriptor, not a constant."""
    original = decision_protocol.QUESTIONS[decision_protocol.ROLE_SELECTION]
    mutated = tuple(original) + ({"name": "a_question_nobody_asks", "type": "noul", "answers": None,
                                  "used_for": "mutation probe"},)
    decision_protocol.QUESTIONS[decision_protocol.ROLE_SELECTION] = mutated
    try:
        assert decision_protocol.digest() != decision_protocol.SIGNATURE_SHA256, (
            "adding a question did not change the digest, so the pin cannot detect a protocol change"
        )
    finally:
        decision_protocol.QUESTIONS[decision_protocol.ROLE_SELECTION] = original


def test_digest_is_canonical_json_of_the_signature():
    canonical = json.dumps(decision_protocol.signature(), ensure_ascii=False, sort_keys=True,
                           separators=(",", ":"))
    assert decision_protocol.canonical_json() == canonical


def test_every_question_the_selection_request_sends_is_described():
    """The descriptor is read against the request builder, not against itself."""
    request = decision_model._request("sairaanhoitaja", "mental_health_assessment", "esimerkkiteksti")
    assert set(request) == {"model", "state", "questions"}
    assert set(request["state"]) == set(decision_protocol.STATE_FIELDS[decision_protocol.ROLE_SELECTION])
    described = {question["name"]: question["type"]
                 for question in decision_protocol.QUESTIONS[decision_protocol.ROLE_SELECTION]}
    assert set(request["questions"]) == set(described), (
        "a question was added to or removed from _request without the descriptor following"
    )
    for name, question in request["questions"].items():
        assert question["type"] == described[name], f"{name} is described with the wrong wire type"


def test_selection_state_carries_no_record_identity():
    """The protocol's own boundary: the note text is sent, the record's id and person are not.

    ADR-0011's rule is that every AI path reads only the post-`cut` set. This asserts the narrower,
    checkable half: the `state` the typed call builds contains no record id, no person id and no
    encounter id, so a question cannot be answered from an identifier that was never sent.
    """
    request = decision_model._request("sairaanhoitaja", "mental_health_assessment", "esimerkkiteksti")
    state_keys = set(request["state"])
    for forbidden in ("record_id", "persona_id", "encounter_id", "henkilo", "lähdehenkilötunniste"):
        assert forbidden not in state_keys, f"the selection state now sends {forbidden!r}"


def test_verifier_descriptor_covers_the_per_citation_question_shape():
    """`supports:<record_id>` repeats per cited record, so the descriptor names the shape once."""
    names = [question["name"] for question in decision_protocol.QUESTIONS[decision_protocol.ROLE_VERIFIER]]
    assert "supports:<record_id>" in names, (
        "the per-citation question is what publishes a citation only when that record supports it; "
        "the descriptor must name it"
    )
    assert set(decision_protocol.STATE_FIELDS[decision_protocol.ROLE_VERIFIER]) == {"sentence", "cited_sources"}


def test_rendered_document_matches_the_module():
    """`docs/decision-protocol.md` is generated; a hand-edit is caught here as well as by the renderer."""
    import pathlib
    import sys

    root = pathlib.Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(root / "backend"))
    from scripts.render_decision_protocol import render  # noqa: PLC0415

    target = root / "docs" / "decision-protocol.md"
    assert target.is_file(), "docs/decision-protocol.md is missing; run the renderer"
    assert target.read_text(encoding="utf-8") == render(), (
        "docs/decision-protocol.md no longer matches app/decision_protocol.py; regenerate it with "
        "backend/scripts/render_decision_protocol.py"
    )


@pytest.mark.parametrize("role", decision_protocol.ROLES)
def test_every_described_question_says_what_it_is_for(role):
    for question in decision_protocol.QUESTIONS[role]:
        assert question["used_for"].strip(), f"{role}/{question['name']} has no stated purpose"
        assert question["type"] in {"choice", "score", "noul"}, question["type"]
        if question["type"] == "choice":
            assert question["answers"], f"{role}/{question['name']} is a choice with no vocabulary"


def test_urgency_legend_in_the_document_matches_the_code():
    """The five urgency legends are a control, not decoration.

    The UI shows the classifier's own legend text for a record's leading urgency level, and that string is
    the only urgency wording the system displays — it renders the classifier's answer rather than composing a
    sentence of its own. `docs/decision-protocol.md` is rendered *from* `decision_model.URGENCY_LEVELS`, so a
    disagreement means one of the two was hand-edited and the UI could show a level the documented question
    never offered. Checked in both directions, and on the count.
    """
    import pathlib

    document = (pathlib.Path(__file__).resolve().parents[2] / "docs" / "decision-protocol.md").read_text(
        encoding="utf-8")
    rows = [line for line in document.splitlines()
            if line.startswith("| ") and line.rstrip().endswith(" |")
            and line.split("|")[1].strip().isdigit()]
    assert len(rows) == len(decision_model.URGENCY_LEVELS) == 5, rows
    for index, (row, legend) in enumerate(zip(rows, decision_model.URGENCY_LEVELS)):
        cells = [cell.strip() for cell in row.split("|")]
        assert cells[1] == str(index), row
        assert cells[2] == legend, f"document says {cells[2]!r}, code says {legend!r}"


def test_the_urgency_question_scopes_itself_to_scheduling():
    """The limit travels with the question, not only with a document about it.

    A criteria list a reader can mistake for treatment urgency is how an ordering mechanism becomes a
    clinical claim. The instruction the model is actually sent scopes itself to the visit's scheduling and
    refuses the treatment-decision reading; this pins both halves by reading the module's own source, so the
    limit cannot be dropped while the pin still passes.
    """
    import inspect

    source = inspect.getsource(decision_model)
    assert '"urgency": {"type": "score"' in source
    assert "ajoitukseen" in source, "the urgency question must scope itself to scheduling"
    assert "hoitopäätöstä" in source, "the urgency question must refuse the treatment-decision reading"
    urgency = next(question for question in decision_protocol.QUESTIONS[decision_protocol.ROLE_SELECTION]
                   if question["name"] == "urgency")
    assert urgency["type"] == "score"
    assert "ordering of priority records only" in urgency["used_for"]
