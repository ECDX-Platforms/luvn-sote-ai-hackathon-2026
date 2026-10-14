# ADR-0004 — Silent absence: a withheld Kirjaus is not disclosed in any form

## Status

Accepted.

## Context

ADR-0003 made the empty-evidence state honest: a Rooli with zero visible
Kirjaus receives `arvioimaton`, and the UI disclosed the withheld tier as a
count only (*"N kirjausta piilossa roolirajauksen vuoksi"*). The 1.1 critical
recap (#1) then flagged that a count-only line still violates Principle 11:
it tells the viewer **that** a category was withheld and **how much** of it.
The council memo (Step 0.7) had explicitly
asked for the count-only line; the recap wins — a number is a disclosure,
just a coarser one.

## Decision

- Remove `excluded_count` at every layer: the `Records` field in
  `picture.py`, the construction in `pipeline.build_picture`, the `App.jsx`
  render block, and the **Poissuljettu** entry in `CONTEXT.md`. After the
  removal the seam carries only `priority` + `accessible_other`; no total and
  no withheld count is on the wire, so the count is not re-derivable from the
  payload.
- Do not substitute a "0 excluded / all shown" line — a zero-vs-nonzero
  indicator is also a disclosure.
- Replace the count in the demo with a presenter **two-screen beat**: the same
  Henkilö rendered under two Rooli side by side (one picture read per Rooli),
  labelled by role, with one narration
  line about the policy cut. No number about withheld records appears anywhere.

## Supersedes / amends

- Amends [ADR-0003](0003-empty-evidence-is-unassessable.md): its Decision
  bullet — the UI "discloses `excluded_count` as a count only … preserving
  silent absence" — is superseded: a withheld Kirjaus is no longer disclosed in
  any form.
- Corrects the "excluded count" mention in
  [ADR-0002](0002-grounding-returns-a-typed-result.md), now reworded to the
  record tiers.

## Considered options

- **Keep the count-only line (council memo Step 0.7).** Rejected: a count is a
  disclosure of what the policy withheld; the recap's Principle 11 reading
  wins.
- **Show "0 excluded" / "all shown".** Rejected: a zero-vs-nonzero indicator
  discloses the same fact with coarser granularity.
- **Just hide the block, no replacement.** Rejected: the demo loses the only
  honest way to *show* that the picture changes by role.

## Consequences

- The external contract shrinks: `Records` is `priority` +
  `accessible_other` only. With no total-record field on the seam,
  `|all| − |visible|` cannot be computed by a viewer.
- The demo carries the contrast instead: the two-screen beat lets the audience
  *see* the role-dependent picture while the narration names the policy cut
  without a number.
- A future operator who wants a count must re-open this ADR, not "fix" the
  code. Contract tests pin the absence of the field and of any total.
