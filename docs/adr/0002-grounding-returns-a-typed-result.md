# Grounding returns a typed result; decision internals stay untyped dicts

## Status

Accepted. One scoped exception to [ADR-0001](0001-picture-contract-at-the-seam.md);
[ADR-0004](0004-silent-absence.md) records a correction to its "excluded count" mention.

## Context

Amends [ADR-0001](0001-picture-contract-at-the-seam.md) with one scoped
exception. ADR-0001 keeps the decision layer as plain dicts and types only the
external seam. The grounding check is the exception that proves the rule: it
decides whether a model candidate's prose may render, and it is typed rather
than assembled through a mutable dict across two modules.

`grounding.validate_fresh_candidate` returns a `CandidateValidation`
(candidate · valid · reason · records) for shape and selected/permitted source
and proposal membership. `grounding.accept_supported_candidate` takes a
`CandidateValidation` plus the Decision-1 grounding-verifier result and returns a
`(GroundingResult, status, reason)` tuple for verdict completeness. The typed
result describes what the check found, not the external contract, so it is not a
second `Picture`. Unsupported sentences are retained on the result only as
`rejected_sentences` (bounded sentence ID · reason); the candidate text is not
serialized or rendered. The briefing can show that a sentence was withheld and why
without crossing the spec's hard boundary that unsupported prose is dropped, not shown. `pipeline` is the sole
author of `Picture`, and
`RecordView.from_record` is the one constructor for a source view.

## Considered options

- **Have the check build a near-`Picture` dict.** Rejected: it made the
  contract's shape a shared responsibility of `grounding` and `pipeline` through
  a mutable dict.
- **Type every internal hop** (`Decision`, `Features`, `TraceEntry`). Rejected:
  ADR-0001's reasoning still holds -- no second consumer outside Python.
- **Return a `Picture` directly from the check.** Rejected: the grounding check
  would then own the record tiers, which are pipeline concerns.

## Consequences

- The picture is assembled in one place; `grounding` is testable without the
  contract shape.
- `Decision`, `Features` and the raw triage dict stay untyped, as ADR-0001
  decided. Only the *result* of an independent check is typed.
- If another internal hop starts returning a near-`Picture` dict, that is the
  signal to type it too -- not to broaden this one.
