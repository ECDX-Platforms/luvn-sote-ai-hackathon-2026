"""Say what the typed decision-model protocol *is*, as a value the repository can pin.

The decision model is a typed call, not an agent: `state` plus `questions`, each question a `choice`,
`score` or `noul`, answered with probabilities. That shape is the system's auditable surface — more
auditable than an agent wrapper would be, which is why it is the shape (ADR-0021) — so it gets the same
treatment as a versioned agent: a readable descriptor and a SHA-256 that both code and gate can compare.

`signature()` returns the descriptor. `SIGNATURE_SHA256` is the digest of its canonical JSON, printed by
`backend/scripts/render_decision_protocol.py` and asserted by
`backend/tests/test_decision_protocol.py`, so a question changed, added or dropped fails the gate until
the descriptor is regenerated — the drift is caught in review rather than found later in a measurement.
"""

from __future__ import annotations

import hashlib
import json

# The two roles the single decision model plays (ADR-0018: one model, both roles). Every question set is
# listed under the role that uses it, because "which questions are asked" is only half the answer — "asked
# by whom" is the other half.
ROLE_SELECTION = "selection"
ROLE_VERIFIER = "verifier"
ROLES = (ROLE_SELECTION, ROLE_VERIFIER)

# The `state` fields each role sends. Named because sending an unlisted field, or dropping a listed one,
# is exactly the kind of silent change this file exists to make visible.
STATE_FIELDS = {
    ROLE_SELECTION: ("encounter_role", "encounter_purpose", "purpose_description", "note"),
    ROLE_VERIFIER: ("sentence", "cited_sources"),
}

# One entry per question: name, wire type, and the answer vocabulary or score levels for choice/score
# questions. `noul` is a boolean-shaped answer with no vocabulary. Verifier questions whose name carries a
# record id are listed once under `supports:<record_id>`; the instance form repeats per cited record.
QUESTIONS = {
    ROLE_SELECTION: (
        {"name": "relevance", "type": "choice", "answers": "RELEVANCE_CRITERIA keys",
         "used_for": "the advisory relevance read; proposals only, never authorization"},
        {"name": "time_sensitivity", "type": "choice",
         "answers": ("current", "historical", "unclear"),
         "used_for": "anchors temporal status; `historical` can downgrade a proposal but not withhold"},
        {"name": "subject_binding", "type": "choice",
         "answers": ("person", "other_person", "unclear"),
         "used_for": "the binding margin: whose record it is, not what it is about; a proposal must bind to the encounter's Henkilö"},
        {"name": "urgency", "type": "score", "answers": "URGENCY_LEVELS (5 levels)",
         "used_for": "ordering of priority records only; it never selects or withholds"},
    ),
    ROLE_VERIFIER: (
        {"name": "support", "type": "choice", "answers": ("supported", "unsupported", "unknown"),
         "used_for": "the per-sentence verdict; only `supported` publishes prose, and only a `supported` "
                     "pre-filled form answer (app/prefill.py) is shown"},
        {"name": "contradicts", "type": "noul", "answers": None,
         "used_for": "a cited source contradicting the sentence; forces a drop"},
        {"name": "attribution", "type": "choice",
         "answers": ("attributed", "adds_claim", "unclear"),
         "used_for": "an uncited claim in the sentence; forces a drop"},
        {"name": "supports:<record_id>", "type": "noul", "answers": None,
         "used_for": "one per cited record: the citation is published only if that record supports it"},
    ),
}

# The deterministic gates applied to the answers, and the constants that set them. The oracle's point was
# that these, not a model's prose, are what makes a Decision-1 outcome auditable — so they are part of the
# signature.
GATES = (
    "ADDITION_FLOOR: a relevance probability below it cannot add a record",
    "OMISSION_FLOOR: only an explicit `irrelevant` above it can omit a note from Foundry generation",
    "DECISION_MARGIN: the separation a choice needs before it is read as that choice rather than `unclear`",
    "SHORTLIST_LIMIT / MAX_NOTES: the candidate fan-out is bounded, and a throttled call is retried",
    "fail-closed: an unconfigured provider, a throttle that outlasts its budget, or an invalid envelope "
    "degrades the stage and never invents a result",
)


def signature() -> dict:
    """The protocol as a comparable value: roles, state fields, questions and gates."""
    return {
        "model": "microsoft-decision-1",
        "route": "/providers/microsoft/v1/systemone",
        "roles": list(ROLES),
        "state_fields": {role: list(STATE_FIELDS[role]) for role in ROLES},
        "questions": {role: [dict(question) for question in QUESTIONS[role]] for role in ROLES},
        "gates": list(GATES),
    }


def canonical_json() -> str:
    """Canonical JSON of the signature: sorted keys, no incidental whitespace, so the digest is stable."""
    return json.dumps(signature(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest() -> str:
    return hashlib.sha256(canonical_json().encode("utf-8")).hexdigest()


# A literal, not `digest()`. A value computed from the descriptor cannot pin it: change a question and the
# computed value changes with it, silently. This literal has to be updated by hand alongside the
# descriptor, and `tests/test_decision_protocol.py` fails until it is — which is the point. Regenerate the
# value that goes here with `backend/scripts/render_decision_protocol.py --print-digest`.
SIGNATURE_SHA256 = "a7ff3549f45d7d79bf3ec3aacb3fc6291826a25cc5bad5c8df4965ee6301ec5f"
