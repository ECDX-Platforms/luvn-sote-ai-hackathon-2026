# ADR-0005 — Residual disclosure: cause-neutral empty-evidence wording

## Status

Accepted.

## Context

ADR-0004 removed `excluded_count` at every layer, closing the count channel.
The oracle checkpoint after ticket 02 (2026-09-29) found that the count was
gone but other restricted-indicator channels remained:

- `actions.json` rule `act-ei-arvioitavissa` emitted *"Tarkista suostumus tai
  käyttöoikeus ennen arviota"* — telling the viewer to check consent/access
  implies records exist but are withheld from this role.
- `risk.json` rule `risk-arvioimaton` emitted *"roolille ei näy kirjauksia"* —
  "ei näy" (not visible) frames the absence as a viewing restriction rather
  than a neutral fact.
- `App.jsx` (~L167–170) repeated the consent/access phrasing in the narrative
  panel.

Under Principle 11, an empty-evidence state must not imply that a category of
records exists but is withheld from this viewer — that is itself a
restricted-indicator disclosure, just as a withheld count was.

## Decision

- Role-facing wording is cause-neutral: *"Ei arvioitavissa: tälle roolille ei
  kerry arvioitavaa aineistoa."* It states that no assessable material
  accumulates for the role, without asserting a cause (withheld vs. genuinely
  absent).
- `risk.json` `risk-arvioimaton` reason reworded to *"Roolille ei kerry
  arvioitavaa aineistoa; arviointia ei voi tehdä"* — drops "ei näy kirjauksia".
  (The first letter is capitalised because the reason is now rendered as a sentence
  after the risk label; the decision above is about cause-neutrality, not case.)
- `actions.json` `act-ei-arvioitavissa` no longer emits the consent/access
  hint. It emits a still-actionable, cause-neutral step instead: *"Pyydä
  lisätietoja ennen arviota"*. `actions` must stay non-empty (golden tests
  assert this for every case).
- The consent/access hint leaves the role view (`Picture`/UI) entirely. It may
  live in presenter notes only — never rendered to a role.
- Trace `actual` values (e.g. `visible_record_count`) are left in place: the
  visible count is already derivable from the returned record tiers, so it is
  not a withheld-count leak (pinned at the Picture level by
  `tests/test_bound_basics.py::test_empty_evidence_picture_is_cause_neutral_and_hides_withheld_count`,
  which shows the role-facing Picture is identical with zero, one, and two
  withheld Kirjaus; the absent `excluded_count` field is pinned by
  `tests/test_contract.py::test_contract_exposes_the_nested_keys_the_frontend_reads`).
  No persona total crosses the seam.
- The two-screen comparison beat (ticket 02) remains presenter-only and must
  not name a withheld item or explain a person's absence as an access
  restriction.

## Consequences

- No role-facing payload (`Picture`, actions, risk reason, narrative) contains
  "suostumus", "käyttöoikeus", or "piilossa" wording. Pinned at the Picture level
  by
  `tests/test_bound_basics.py::test_empty_evidence_picture_is_cause_neutral_and_hides_withheld_count`.
- The empty-evidence affordance is weaker as a safety nudge (it no longer
  tells a clinician to check consent/access specifically) but stronger as a
  disclosure boundary — it cannot be read as "records exist, ask for access".
  A future operator who wants the stronger safety hint must reopen this ADR
  and decide where it lives (presenter-only, or a different non-role channel).

## References

Amends the residual channels left open by [ADR-0004](0004-silent-absence.md).
