# ADR-0003 — Empty evidence is `arvioimaton`, and grounding needs a citation

## Status

Accepted.

## Context

The policy cut is a first-class feature: a Rooli can legitimately see **zero**
Kirjaus (e.g. `persona-001` is entirely `treatment`-scope, so
`sosiaalityontekija` sees nothing). Two coupled defects made that case lie:

1. The decision tables fell through to their catch-all rules, so a role with no
   evidence still received `risk_level: matala` with the reason *"ei kohonneen
   riskin tunnusmerkkejä"* and `situation_type: yleinen`. (That quote is the
   wording as emitted at the time; the table's reasons are now sentence-cased.) A statement about the
   patient ("no signs of elevated risk") was emitted where the truth was "no data
   is visible to this role".
2. The grounding check computed `grounded = not dropped_citations and not
dropped_actions`. With zero records and a narrative that cited nothing, this
is **vacuously true**, so the UI rendered a green *"Grounding: OK"* badge over
no evidence. The 20-case golden matrix pinned this behaviour as correct.

`excluded_count` was already computed and crossed the seam, but `App.jsx` never
rendered it, so the third tier (silent absence) was invisible.

## Decision

- `triage.extract_features` emits `visible_record_count` — the number of records
  the policy cut actually permitted. It is the one number that separates "no
  elevated risk" from "no evidence".
- The `situation`, `risk` and `actions` tables carry a top-priority (200) rule on
  `visible_record_count <= 0` → `situation_type: ei_arvioitavissa`,
  `risk_level: arvioimaton`, and the action *"Pyydä lisätietoja ennen
  arviota"*. It outranks every clinical rule.
- The grounding check treats a non-empty narration with **no permitted citation**
  as ungrounded. Empty claims no longer pass vacuously. The requirement is
  `validate_fresh_candidate` (a sentence must cite a permitted, selected source)
  plus `accept_supported_candidate` (a sentence whose text is empty after
  `.strip()` rejects the whole candidate).
- The UI renders `arvioimaton` as its own amber state, and withheld records
  are not disclosed in any form ([ADR-0004](0004-silent-absence.md)).

## Considered options

- **Only fix the UI (hide the badge when there are no records).** Rejected: the
  decision object itself still claimed a clinical verdict; the API seam would
  still say `matala`.
- **Treat zero records as `matala` and add a "low confidence" flag.** Rejected:
  "no evidence" and "no risk" are different facts and must not share an enum.
- **Require a citation only when records exist.** Rejected: that is exactly the
  vacuous-pass path; the citation requirement is what makes the check meaningful.

## Consequences

- `matala` now means "evidence was reviewed and no elevated risk was found".
  A reviewer can tell the two apart from the decision object alone.
- The empty-evidence picture renders `grounded: false` and no narration. That is
  deliberate: there is nothing to ground. The UI shows the `arvioimaton` state,
  not a "grounding rejected" message.
- Three golden rows change from `(yleinen, matala)` to
  `(ei_arvioitavissa, arvioimaton)`; the golden matrix now pins the honest
  behaviour instead of the defect.
- Two tests pin the wording, in the empty-policy case
  (`test_decide.test_policy_cut_gates_the_decision_not_just_the_id_list`) and the
  stale-evidence case (`test_triage.test_only_stale_evidence_is_unassessable_not_low_risk`):
  each asserts the `arvioimaton` reason does not contain `risk-yleinen-matala`'s own
  reason, read from `risk.json` rather than from a literal. So the false reassurance
  cannot return by enum refactor, and it also cannot return by rewording or
  recasing the table — which is how a wording pin set as a literal would rot. For
  a time only the `matala`-level half of this consequence was actually pinned;
  the wording half was a claim in this record with no test behind it, and is now pinned too.
