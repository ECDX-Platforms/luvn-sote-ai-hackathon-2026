# ADR-0029 — The library pin moves to `f505b9b`, and the record-display patterns are adopted in Finnish

Status: **accepted.** Amends [ADR-0026](0026-the-luvn-component-library-is-the-ui-authority.md)'s pin
(`014b02a` → `f505b9b`) and lifts the earlier reserve on the seven English-string data-display components,
which were held back until label props existed upstream. ADR-0026's contract, exclusion boundary and
enforcement list stand unchanged.

## Context

The earlier pin decision declined the seven data-display components that rendered fixed English words
(`AiSummaryBlock`, `SameFactTwoSources`, `GroupedRecordRow`, `CautionItem`, `ProcessSteps`, `ResultSeries`,
`TimePeriod`) until an upstream change made every word a prop with the current English as its default. That
change is upstream as `f505b9b` — *"Labels props: every fixed word of seven data-display components and
BlockState can be replaced"*. Each of the seven, and `BlockState`'s "Warning." prefix, now takes an optional
`labels` object; defaults are the English copy, so existing uses render unchanged.

A UI review of the hosted product (six clients, fifteen role views) found that the client view used about
twenty library basics and none of the v2.2 and v2.3 patterns; that the main grid, block rhythm, text measure
and zones of the styleguide were not implemented; that what Decision-1 lifted was shown in three places that
could disagree (and did, on one record); and that the AI summary carried no per-sentence source, no suggestion
tag and no equal-weight reject. The review is what this record closes.

## Decision

1. **The pin is `f505b9b`.** Every file under `frontend/vendor/comp-lib/{src,tokens}` is copied from that
   commit and recorded in `PROVENANCE.md` with its SHA-256 and that source revision. Only the seventeen
   component files of the label commit changed bytes; the token layer and `Button.module.css` (the a11y
   audit's hash anchor) did not, so the pinned Vite hash `17e58` stands.
2. **Adopted, with every word Finnish through `labels`:** `AiSummaryBlock` (the source-backed summary),
   `GroupedRecordRow` (one row per medicine substance, per diagnosis or decision group, per lab test, and per
   group of identical selected records), `BlockState` (pre-fill failures), and `MainSide`, `IdentityStrip`,
   `NowStrip`/`NowCell`, `MetricRow`/`Metric`, `TaskList`/`TaskItem`, `SourceReference` and `Dialog`, which
   carry no fixed words. A rendered English default anywhere in the briefing is a test failure.
3. **Still not adopted, each for a stated reason:** `ShowAllButton` and `RecordBlock` (fixed "Show all N"),
   and `NotOnRecordLine` (fixed "Not on record") — the product renders the same anatomy with a library
   `Button` and app markup until the upstream adds labels; `SourceDialog` (fixed subtitle "Source record ·")
   — the product's source dialog is app markup on the library `Dialog`; `ResultSeries` — it needs a fixed
   clinical scale per test that no professional has checked for this corpus, so lab results are grouped rows
   with the source's exact values; `CautionItem` — a model pick must never look like a safety fact, so
   Decision-1's lifted items are a sibling layout under their own name.
4. **One place for what Decision-1 lifted.** The lead record is chosen only among records Decision-1 judged
   relevant, so the lifted block, the row marker and the closed reasoning can no longer disagree. The blue
   information rows leave the attention panel; per-row chips are reduced to one neutral "Ehdotus" marker on
   lifted records; the ordering-basis caveat (ADR-0024) is said once under the block title.
5. **Per-sentence sources are on the wire.** The Picture carries `narrative_sentences` (the accepted
   sentences with the record ids that verified each), so the summary's sentences cite their own records; the
   flat `narrative` stays for every reader that has it.
6. **The cut to `today` applies to every record**, and a decision whose text opens with "PÄÄTÖSLUONNOS" is a
   draft (`state: draft`) when the envelope gave no state. Neither invents data; both stop a future date or a
   draft from reading as a settled fact.

## Consequences

- `SourceLink` (a same-document jump) and `SourceReference` (a button that opens the record in the dialog)
  coexist; the record anchors `#source-<id>` stay in the DOM for every record, open or folded, because
  citations and the dialog's "Siirry kirjaukseen" both land there.
- Feedback on the summary ("Pidä tällä käynnillä" / "Ei hyödyllinen") and the "Hylkää ehdotukset" reject on
  the lifted block are client-side states; no route stores them yet, and the status lines say so rather than
  claiming a save.
- The tablet range (640–1023 px) still shows the sidebar: the library's `AppShell` has no tablet rule. That
  is a library gap and is not worked around in the product.
