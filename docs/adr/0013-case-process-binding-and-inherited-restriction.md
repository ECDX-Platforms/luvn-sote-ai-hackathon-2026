# ADR-0013 — Case/process context and inherited restriction

Status: **accepted with a scope split; case/process model and the end-to-end encounter cut seam are implemented (2026-10-08); product restriction data remains DATA-GATED.** Derived from the vendor's Lifecare training corpus (scan 2026-10-07). Illustrative synthetic modelling, not a statutory rule.

## Owner decision, 2026-10-07

- **Split decision.** The optional case/process **binding** (`case_id`, `process_type`,
  `origin_organisation`, `origin_service_unit`) is **in scope** for Challenge 1.1. It is optional, so the
  health-care path is untouched, and it is what makes inherited restriction expressible — that is a
  **permission** property, not a history feature.
- **Process and contact history is Challenge 1.2.** The brief excludes "yhteydenottojen ja
  asiointihistorian koontia". Build the binding; **never** the history. The `asiointitapahtuma` records
  stay invisible to every purpose until 1.2 is explicitly opened.
- **Boundary call worth confirming with the organisers.** The brief's exclusion is worded for
  *history*, not for *case structure*, so "case structure is in, history is out" is our reading rather
  than the brief's explicit text. This is recorded as a call to check, not as settled fact.
  See [`.scratch/challenge-1-1-alignment/issues/09-challenge-scope-guardrails.md`](../../.scratch/challenge-1-1-alignment/issues/09-challenge-scope-guardrails.md), which turns the history exclusion into a test.
- **Deferred** with the envelope work (issues 01, 03, 05): it changes `policy.cut`, so it lands after the
  judged event alongside ADR-0012.

## Implementation disposition, 2026-10-07

**IMPLEMENTED:** optional encounter/record case and process fields flow through `cut_for_encounter` to the one policy cut, including its materialised inherited-restriction set. The encounter list, picture and proposal-feedback paths share the same scope; API tests verify byte-identical scopes and silent absence. The original safety-critical policy-cut deferral was honored.

**DATA-GATED:** no product encounter carries a case restriction. API tests use transient synthetic restriction state only; no case IDs are rendered. See `.scratch/challenge-1-1-alignment/work-order.md` Phase 0 for the seam and acceptance evidence; no new ADR or change to the inheritance decision is introduced. The boundary-check call remains flagged for owner confirmation below.

## Correction, 2026-10-07 (second-pass source read) — later tightening is not automatically propagated

A later read of the same corpus resolved an escaped text block the first pass had missed, and it
qualifies this ADR's wording. From `OmaKanta-rajoitukset` (course 99, module 2630):

> "On huomioitava, että asialle asetetut OmaKanta-rajoitukset periytyvät **aina myös** asian
> asiakasasiakirjoille. Mikäli asialle asetetaan **myöhemmin tiukemmat** OmaKanta-rajoitukset, täytyy
> muutokset tehdä sen jälkeen **käsin myös kaikille asiaan liittyville asiakasasiakirjoille**."

So inheritance applies **at creation**, and a later *tightening* must be applied manually to each
related document. **The decision is unchanged:** the effective exclusion remains the union of case-level
and record-level restrictions, and the effective exclusion set is evaluated in one place. The case input
represents the already materialised effective state; this implementation does not recompute member
records after a later case change.

The same module ties restrictions to age and to the guardian question: *"OmaKanta-rajoitusten sisällöt
määräytyvät asiakkaan iän mukaan. Alaikäisen asiakkaan kohdalla ammattilaisen täytyy aina ottaa myös
kantaa siihen, saako asiakirjoja luovuttaa huoltajille."* That is independent support for ADR-0012's
per-encounter judgement, in the social-care path.

**Flagged for owner confirmation**, because this ADR is already accepted and a correction to an accepted
record is the owner's call. This note records the finding; it does not amend the decision.

## Context

Our `Kohtaaminen` binds a person, a `Rooli` and a `Palvelukonteksti`, and `policy.cut` evaluates each
`Record` **independently**. Social care in the source system is different in two ways.

**Documents cannot exist without a process.**

> "Sosiaalihuollon Lifecare on **prosessipohjainen** asiakastietojärjestelmä, jossa asiakkaan
> asiakasasiakirjat kirjataan aina **palveluprosessiin**. Ilman palveluprosessia ei
> asiakasasiakirjojen kirjaaminen ole järjestelmässä mahdollista."

**Restrictions are two-level and inherit.**

> "OmaKanta-näkyvyydet ja niiden rajoitukset ovat **kaksitasoisia** — voit asettaa rajoituksia joko
> koko sosiaalihuollon **asialle**, jolloin rajoitukset **periytyvät** kaikille asiaan liittyville
> asiakasasiakirjoille, tai yksittäiselle asiakasasiakirjalle."

Observed case/process vocabulary: `asia`, `palveluprosessi`, `vireilletulo`,
`vastaava työntekijä` with `organisaatio` + `palveluyksikkö`, and process types such as
`Lapsiperheiden palvelutarpeen arviointi`, `lastensuojeluilmoituksen vireilletulo`,
`kuntouttava työtoiminta`, `päihdehuollon laitospalvelu`.

A flat per-`Kirjaus` cut therefore under-models the source: it cannot express "this whole case is
restricted", and it cannot bind a social-care record to the process that gives it meaning.

## Decision

1. Add an **optional case/process binding** to the encounter envelope: `case_id`, `process_type`,
   `origin_organisation`, `origin_service_unit`. It is optional so the health-care path is unaffected.
2. `policy.cut` gains an **inherited restriction** input: a restriction recorded at case level applies
   to **every** member `Kirjaus`, *and* a per-record restriction may still apply. The effective
   exclusion is the **union**, still evaluated in one place.
3. The case is **context, never a second identity**: it does not grant access to anything, and it is
   not by itself a relevance signal.

## Boundaries

- **Silent absence is preserved** (ADR-0004). A case-level exclusion discloses no count, no existence
  and no identity — it looks exactly like a record that was never there.
- **Not a 1.2 feature.** We model case *structure*; we do not aggregate contact or interaction
  history. That is the explicit exclusion in the Challenge 1.1 `Rajaukset`.
- A case-level restriction must **not** become a de facto shared-read group: two professionals bound
  to the same `case_id` do not thereby share visibility.
- The case identifier is a modelling handle, not a source-system identifier we can claim to have.

## Consequences

- Fixtures need `case_id`/`process_type` wherever social-care records appear.
- A new test must prove that a case-level restriction removes records the role and purpose would
  otherwise admit — i.e. that exclusion is genuinely inheritable, not additive-only.
- The briefing must not render case structure as a permission *hint* ("3 other cases exist" is a leak,
  even without names).
- `delivery.py` needs the case columns in its assumed shape, or the binding stays fixture-only until
  the real delivery arrives.

## Relationship to existing architecture

The single-decision-point invariant holds: `policy.cut` still runs once per request and remains the
sole authority (ADR-0011). This ADR widens its *input* to include inherited exclusions; it does not add
a second cut and does not move the cut after retrieval.
