# ADR-0016 — The Swiss 1 foundations library is the UI authority

Status: **superseded by [ADR-0026](0026-the-luvn-component-library-is-the-ui-authority.md) (2026-10-13) for the vendor choice only** — the contract below is retained. Originally accepted and implemented (2026-10-08). All nine phases landed and the app ships the vendored library as its UI authority; the migration is deployed as revision `--0000032`. The pinned copy is `frontend/vendor/comp-lib/`, with the SHA-256 of every pinned file in its `PROVENANCE.md` and re-checked by the gate. It was re-vendored from **`comp-lib-dev-test-button-pipeline`**, which adds `Button` on top of the batch-1 foundations (`comp-lib-dev-batch-1-foundations`); the token files are byte-identical between the two, so no design decision moved when it was re-pointed. The ownable design source is the **Swiss 1 baseline styleguide v2.1**, kept by the design owner and not in this repository; the library mirrors the Figma **Design-Library** (`fileKey P8E4RKubznUkk8vi9765oW`). Work, including the phase resolutions and an adversarial review of the finished result, is tracked in [`.scratch/design-system-migration/`](../../.scratch/design-system-migration/spec.md).

## Context

Two authorities coexisted. The UI remake ([`.scratch/ui-remake/`](../../.scratch/ui-remake/spec.md)) took its
token values and visual rules from `style-options.html`, an owner-authored HTML mockup, and enforced them
with a test that parsed the mockup's `PRESETS` and compared all seven palettes × both modes against
`frontend/src/styles.css`. That mockup is a design *input* — it is not maintained, and it cannot state a
rule the app has to follow.

Three facts force the replacement:

1. **The library's default palette is value-for-value the app's existing `vivid-violet-signal` preset**, in
   both light and dark. The colour does not change; this is a formalisation, not a recolour.
2. **The library states rules the mockup never did**: the `.t-*` type roles on a 12·14·17·20·24·29·35 scale,
   three weights (400 read, 500 click, 600 find), the 4px spacing grid, radius as
   0/4/6/full, a 2px selection bar, a 2px focus ring with a 2px offset, "never lower contrast with
   opacity", "surface-brand is for thin marks only, never behind text", "status as icon + word", and
   "primitives are never used directly in components".
3. **The library deliberately defines no `--color-sidebar` and no `--color-header`** — the styleguide marks
   them "not used in this style". The app's dark purple sidebar and its header-band tokens have no
   counterpart and are not re-tokenised.

Batch 1 contained no components; the pinned copy now exports the library's `Button` (`src/index.ts`), so every product surface is built in the app
against the library's rules until a later batch supplies components.

**Re-vendored to the whole library, 2026-10-09.** The pinned copy is now **`ECDX-Platforms/comp-lib-dev` @
`main` `164683d`** — the library's 35 components, not just `Button` — and `PROVENANCE.md` records that
repository and revision in place of the former local-directory source. Three facts made this a pointer change
rather than a redesign, and they are the reason no design decision moved:

- **The token layer is byte-identical** to the previous copy (`tokens/figma-export.json`,
  `tokens/styleguide-only.json`, `tokens/dtcg/*`, `src/styles/tokens.css`), so the 87 declared tokens, the
  thirteen `.t-*` roles and every phase of the migration are untouched.
- **`Button.tsx` is byte-identical**, so the one adopted component did not shift under the app.
- **The shell components do not need the tokens point 3 rules out.** Checked in their own CSS modules:
  `Sidebar` and `IdentityStrip` use a local `--sidebar-w` (a *width*, not a colour) and `Header`, `NavItem`,
  `Alert`, `Tag`, `Table`, `Tabs` and `Metric` reference no sidebar/header colour tokens. Point 3 stands: the
  app still owns its sidebar and header colour, and the library's `--color-sidebar`/`--color-header` are not
  adopted. Demo-page colour props are not component requirements.

The gate's vendored half was tightened at the same time: it no longer pins a hand-maintained list of seven
paths (under which a component could be vendored and adopted without ever entering the ledger, i.e. trusted
rather than enforced). It now sweeps the vendored tree and requires **every** file under `tokens/` and `src/`
to appear in `PROVENANCE.md`'s checksum table — 119 files — and fails if the ledger and the tree disagree.

## Decision

1. **The Swiss 1 baseline styleguide, as implemented by `@ecdx/comp-lib`, is the rule for every UI build.**
   `style-options.html` becomes historical design input: kept, not maintained, not retro-edited.
2. **The app carries no hand-rolled token layer.** Colour, spacing, radius, control sizing and motion come
   from the library's tokens; primitives (`--color-brand-*`, `--color-neutral-*`) are never referenced by
   component CSS; all type comes from the `.t-*` roles. No raw hex, no off-scale size, no raw type value,
   no `opacity` used to lower contrast.
3. **One palette plus dark.** `data-style` and `data-preset` and the seven-preset chooser are removed.
   Theme is `data-theme` on `<html>`, which is the only element the library's `:root[data-theme="dark"]`
   can match. No density control ships: the library's comfortable density collapses `--control-sm`,
   `--control-h` and `--control-lg` to one 44px value while leaving `--sidebar-w` and `--logo-row` fixed.
4. **The sidebar and header are de-coloured, not re-invented.** No `--color-sidebar` or `--color-header`
   token is created. Structure comes from `--color-line` and the canvas/surface pair.
5. **The library is vendored, pinned and hash-checked**, not installed from an external path, and never
   hand-edited: `tokens.css` and `tokens/dtcg/` are generated. Regeneration is a documented manual step.
6. **`Picture` is untouched.** This ADR governs presentation only. It changes no wire field, no policy
   input and no selection behaviour, so ADR-0001 is unaffected.
7. **Colour remains meaning.** The binding table survives the migration: only risk level, coverage gap,
   `due`, Decision-1 addition/emphasis and out-of-generation omission may carry status colour; the absence state
   renders identically for every reason; the impact estimate is never a stats card and contains no minutes;
   the two Decision-1 states stay neutral; nothing derived from a withheld record appears in any form.

## Consequences

- The mockup-enforcement test is replaced by a design-system gate in two halves: the vendored copy is
  pinned by SHA-256 and its token names asserted, and the app stylesheets are checked for raw hex,
  `opacity`, primitive references, raw type declarations, off-scale lengths, unscoped selectors and
  dead selectors.
- The `.scratch/ui-remake/` tracker is superseded for token and style authority. Its record of what was
  implemented stays true below the token layer and is not rewritten.
- Screenshots cannot be taken by the agent session, so visual acceptance is owned by a Pi
  chrome-devtools session against a capture matrix; the repository gate
  (`scripts/check.sh --full`) remains the gate and is Azure-free.
- Components are hand-built in the app until the library's next batch lands, and are kept in one block per
  component so the eventual swap is a replacement rather than archaeology.
- The flat-card choice (`--radius-none`) accepts that a panel is separated from the canvas by a 1px
  `--color-line` at 1.29:1, which the styleguide itself calls decorative. That is a knowing trade-off and
  is recorded here so it is not rediscovered as a bug.
