# ADR-0028 — The library pin moves to `014b02a`, and the rehearsal copy is retired

Status: **accepted, 2026-10-14.** Amends [ADR-0026](0026-the-luvn-component-library-is-the-ui-authority.md)'s
pin (`a00bb4d` → `014b02a`) and records its stages 2, 3 and 4 as complete. ADR-0026's contract, exclusion
boundary and enforcement list stand unchanged; this record changes the revision and closes the interim split.

## Context

ADR-0026 pinned `@luvn/component-library` at `a00bb4d` and vendored only its token layer, leaving the components
and the barrel as the rehearsal copy (`comp-lib-dev` @ `164683d`) under an explicitly labelled interim split.
Stage 3 planned to upstream the app shell from the rehearsal shim by pull request.

The library moved instead. `ECDX-Platforms/LUVN-Component-Library` `main` at
`014b02ae927f2b40ada8e66a68cf5174fd670675` — *"Redesign v2.3 part 3, on the current main"*, 2026-10-14 — carries
the shell itself (`AppShell`, `Header`, `Sidebar`, `BottomNav`, `NavItem`, `TabBar`, `IdentityStrip`), the six
components ADR-0026 declined until needed (`Dialog`, `Table`, `Tooltip`, `Menu`, `IdentityStrip`,
`TypedConfirmation`), the split controls (`Field` + `Input`, `Field` + `Textarea`), `TabList`/`Tab`/`TabPanel`
in place of `Tabs`, `StatusTag` beside a status-less `Tag`, and the record-display patterns. Its token layer moved
too: the neutrals are grey (`--color-neutral-*`, `--color-bg`, `--color-line`, `--color-text`), three semantic
tokens became literals (`--color-text-muted`, `--color-primary-tint`, `--color-inverse`) and four are new
(`--color-interaction-tint`, `--color-data`, `--color-avatar`, `--color-on-avatar`). The token check is
"206 of 206". Its own suite at this revision passes (185 files, 1148 tests, 1 expected fail).

Seven components render **English** words that are not props: `AiSummaryBlock` (Suggestion, Accept, Reject,
Based on…), `SameFactTwoSources` (Sources differ), `GroupedRecordRow` (Long-term…), `CautionItem` (Allergy, Side
effect, Not recorded, None known), `ProcessSteps` (Done, Upcoming, No date…), `ResultSeries` (Latest, All
results…), `TimePeriod` (month abbreviations, Ongoing, Starts later…). The product is Finnish. `SourceReference`
is a button with `onOpen`, built for `SourceDialog`, not a same-document link.

## Decision

1. **The pin is `014b02a`.** Every file under `frontend/vendor/comp-lib/{src,tokens}` is copied from that commit
   and recorded in `PROVENANCE.md` with its SHA-256 and that source revision; the gate asserts a single source
   revision for every row. Nothing is carried from the rehearsal. ADR-0026's stages 2, 3 and 4 are complete.
2. **The shell was built upstream, not upstreamed from the shim.** The product's hand-built grid, header context
   caption, header menu and bottom bar are deleted; `App.jsx` renders the library's `AppShell` with `Sidebar`,
   `Header` (the active role as `user.role`), `BottomNav` and `NavItem`. The sidebar shows from 640 px, as the
   library decides; the role picker and theme toggle are repeated once under 640 px in the app's own
   `brief-phone-tools` block, because the library's sidebar is hidden there.
3. **The six "declined until needed" components now exist upstream** and may be adopted when a surface needs
   them, from the pin, never from the rehearsal. The same applies to the components new at this revision:
   `CareOverview`, `TodayBriefing`, `ClientTimeline`, `TimelineRow`, `TimelineMark`, `VisitRhythm`,
   `SourceDialog`, `RecordBlock`, `NotOnRecordLine`, `ShowAllButton`, `UserSwitcher`, `UserMenuItem`,
   `TimelineTodayLine`. None is adopted by this record.
4. **The seven English-string components are not adopted** until an upstream change adds label props with the
   current English as defaults (plan unit A5, a pull request the owner reviews). A hard-coded English word in
   the Finnish product is a defect, and a vendored file is never hand-edited.
5. **`SourceLink` stays the product's citation.** The library's `SourceReference` is a button that opens a
   dialog; the product's citation is a same-document jump to the rendered record (`#source-<id>`), which a link
   expresses and a button does not. Adopting `SourceReference` means adopting `SourceDialog`, a product change
   outside this record.
6. **The pinned Vite hash in `a11y.test.mjs`** (the `Button.module.css` module hash the audit uses to attribute
   rendered classes to vendored sheets) moves with the pin: `vrhsz` → `17e58`, verified against `dist/assets`
   after a build. Each re-pin re-verifies it the same way.

## Consequences

- ADR-0026's interim split, the per-file source table and the "stage 3 by pull request" path are closed. The
  re-vendor procedure in ADR-0026 remains the procedure, with "copy the token layer" now literal: the layer
  moved at this pin and will move again.
- The app stylesheet lost the shell rules and the hand-built table and card rules the library now covers; the
  gate's rule that every class the sheet defines is referenced by the source stays the guard against residue.
- A5 is blocked on the owner's merge. Until then the record-display patterns stay unadopted and the briefing
  renders its sections with `Block`, `Table`, `Timeline` and `StatusTag` from the pin.
