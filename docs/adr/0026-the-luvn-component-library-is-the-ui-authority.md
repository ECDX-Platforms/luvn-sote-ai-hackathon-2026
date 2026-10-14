# ADR-0026 — The LUVN Component Library is the UI authority

Status: **accepted.** Supersedes [ADR-0016](0016-swiss-1-foundations-is-the-ui-authority.md)'s
**vendor choice** (which library is pinned and vendored) and **retains its contract** unchanged — the same
pattern as [ADR-0018](0018-microsoft-decision-1-replaces-the-vendor-classifier.md) over the earlier
vendor-classifier decision. The decision was reached on an oracle review of the two libraries;
stages 0 and 1 are implemented here. Stages 2 and 3 (components, shell) are not yet done and are recorded
below as pending, not as complete.

## Context

ADR-0016 vendored `@ecdx/comp-lib`, pinned to a revision of `ECDX-Platforms/comp-lib-dev`. That repository
is a **test copy**: its own runbook names `comp-lib-dev` as a test repository and
`LUVN-Component-Library` as the real library. The vendored copy carried that
runbook, and the ADR-0016 ledger quoted it as the library's self-description.

The real library is now `@luvn/component-library`, in `ECDX-Platforms/LUVN-Component-Library`, pinned to a
revision (never a branch). Both repositories mirror the same design source:

- The **Swiss 1 baseline styleguide v2.1**, held by the design owner and not in this repository. That styleguide
  is the authority. Neither library is.
- The Figma **Design-Library**, `fileKey P8E4RKubznUkk8vi9765oW`. Both libraries pin this key, and this ADR keeps it.

Measured against the test copy, the new token layer differs in exactly two declarations and in resets on
the type roles. `--font-sans` changes from `"Inter"` to `"Host Grotesk"`, `--font-features` drops `"cv11" 1`, and
the `.t-*` roles gain `letter-spacing: 0` (body and small roles) and `text-transform: none` (all roles). The
base layer and the index are byte-identical. No colour, spacing, radius, control, motion or density value moved.
The change therefore moves the typeface and nothing else.

## Decision

1. **The UI authority is the Swiss 1 baseline styleguide v2.1, as implemented by `@luvn/component-library` at
   a pinned revision.** The pin is to that revision. It is never a branch: not `main`, and not any floating
   revision. The repository is `ECDX-Platforms/LUVN-Component-Library` (private, pull-only for this product).
   The Figma fileKey is `P8E4RKubznUkk8vi9765oW`.
2. **The ADR-0016 contract is retained, unchanged.** That is: no hand-rolled token layer; one palette plus dark;
   theme on `data-theme`; no density control; all type through the `.t-*` roles; no raw hex, no `opacity` used to
   lower contrast, no off-scale length, no primitive `--color-brand-*`/`--color-neutral-*` references; no
   `--color-sidebar` or `--color-header`; the library vendored, hash-pinned and never hand-edited. `Picture` is
   untouched: this ADR governs presentation only, changes no wire field, policy input or selection behaviour, and
   leaves ADR-0001 unaffected. ADR-0016's Decision 1–7 stand as written.
3. **Exactly one library is the authority.** The two libraries are never co-authorities. The test copy's token
   set and the new one cannot both be live. Two token sets (Inter and Host Grotesk) would be two authorities, and
   the rosters differ too. The test copy is retired by stage 4. Until then it survives only as the explicitly
   labelled interim split below, and nothing in it may be extended or re-pinned.
4. **Typeface.** `--font-sans` is `"Host Grotesk", system-ui, sans-serif` and `--font-features` is `"tnum" 1`. The
   app self-hosts Host Grotesk 400, 500 and 600 in `frontend/src/fonts.css` from `frontend/public/fonts/`, as
   static WOFF2 files under the SIL Open Font License 1.1. The library's `@fontsource/host-grotesk` dependency is
   not adopted: the product keeps its no-network, no-package container story. A token change without the font
   load would fall back silently to `system-ui`, and no static check would see it, so the gate now checks both the
   token value and the load (see Enforcement).

## Re-vendor procedure

From a clean clone of `ECDX-Platforms/LUVN-Component-Library` at the pinned revision (never `main`):

1. Copy the files the product builds from: `tokens/**`, `src/styles/{tokens,base,index}.css`, and the components and
   barrel for the stage being re-vendored. Do not copy anything in the exclusion boundary below.
2. Regenerate the generated token files in the **library**, never in the product: `npm install` (style-dictionary,
   needs network, once), then `npm run tokens`. That runs figma-to-dtcg, build-tokens and the 198-check
   `verify-tokens` against the Figma export. Copy the result; do not hand-patch a copied file.
3. Regenerate `frontend/vendor/comp-lib/PROVENANCE.md`'s checksum table with `sha256sum` over the vendored tree
   (`src/` and `tokens/`). Update the revision and the per-file source table. A typed hash is a defect.
4. Run `scripts/check.sh --full` from the repository root. The gate re-hashes the tree, checks the authority
   named in PROVENANCE, and checks the typeface.

## Exclusion boundary

The product vendors what it builds from and nothing else. The earlier ledger drew this line and the new ledger
keeps it, extended to the new repository's additions:

- **Not vendored:** `*.stories.tsx`, `*.test.tsx` and `*.figma.ts` (they import Storybook, Vitest, Playwright or
  `@figma/code-connect`, none of which the product installs); `src/foundations/` (Storybook pages only);
  `src/styles/fonts.css` (the product self-hosts its font); `.storybook/`; `test/`; `scripts/`; `.github/`;
  `tsconfig.json`; `vite.config.ts`; `vitest.config.ts`; `figma.config.json`; `package.json`; `package-lock.json`;
  and devDependencies. Of `docs/`, only the library's runbook is vendored.
- **Vendored:** `tokens/**`, `src/styles/{tokens,base,index}.css`, `src/index.ts`, `src/components/**`, and the
  runbook.
- **Residue, not yet removed:** the test copy's `README.md`, `package.json` and `scripts/` are still in the
  tree. They are not pinned and nothing in the product builds from them, but the repository's doc-claims check
  enumerates `README.md`, so removing them belongs to stage 4 with that check's update, not to the token re-pin.

The gate sweeps `src/`, `tokens/` and `src/styles/` against the ledger, and fails on a file the ledger does not name
or a named file that is absent. The exclusion boundary is therefore enforced by the ledger, not by convention.

## Interim split: what is not yet from the pinned revision

The re-pin moved the token layer, base, index styles and runbook to the pinned revision. It did **not** move the components
or the barrel, because the component and shell migrations are separate, ordered stages. Until they land, the
product's `src/index.ts` and `src/components/**` (35 directories) remain from the test copy. They
consume only token names the new tokens still declare; the gate checks that every component `var(--…)` resolves
(`identity-left` has a fallback and was never declared). PROVENANCE records this split in a table, so it is
visible rather than trusted.

- **Stage 2 — components.** Re-vendor the twelve components the new library also has, from the pinned revision: `Button`,
  `IconButton`, `Metric`, `MetricRow`, `NavItem`, `Search`, `Skeleton`, `Tag`, `KeyValueList`, `Link`, `Tabs`, and
  the rest of the shared set. Two names change structure, not spelling. `TextField` (a `Field` wrapping an input)
  becomes `Field` + `Input`, and `TextArea` becomes `Field` + `Textarea`, because the new library's `Input` and
  `Textarea` are bare controls meant for `Field`. That is a re-composition in `ChatPanel.jsx`, not a rename.
- **Stage 3 — the shell.** The new library lacks `Header` (with the `HeaderUser` export), and `Sidebar`. These are
  about 95% design-system logic, not app logic: token-only assemblies with generic props. The chosen path is to
  upstream them into `LUVN-Component-Library` by pull request (that library's runbook already places the App shell
  after Overlays). Until that PR merges, the three stay as a **pinned, dated, labelled temporary shim** from the
  test copy. The shim is deleted in the same change that re-vendors the upstreamed shell. The shim is the only
  permitted bridge, and it has one eventual source.
- **Stage 4 — retire the test copy.** Delete the remaining test-copy files. The vendored tree is then the pinned
  revision only.

## Declined until needed

The test copy had six components the new library does not have, and the app imports none of them. They are
**not** carried into the new tree:

`Dialog`, `Table`, `Tooltip`, `Menu`, `IdentityStrip`, `TypedConfirmation`.

Each is declined until a surface needs it. When one is needed, build it in `LUVN-Component-Library` first (its
runbook orders Overlays and Data display as later pages) and vendor it from there. **Never resurrect one from the
test copy.** This is the same discipline as the gate's `DECLINED_TOKENS` list.

## Enforcement

The gate (`frontend/src/design-system.test.mjs`) enforces the following, and this list is the contract:

- **Every vendored file is pinned by SHA-256** in PROVENANCE, and the table and the tree cannot disagree (a sweep
  of `src/`, `tokens/` and `src/styles/`).
- **The authority is named.** PROVENANCE must name `ECDX-Platforms/LUVN-Component-Library`, the revision
  the pinned revision (a commit, never `main`), the package `@luvn/component-library`, and the Figma fileKey.
- **The typeface is checked by value and by load.** `--font-sans` must name `"Host Grotesk"`, and
  `--font-features` must be exactly `"tnum" 1`. `fonts.css` must declare that family at weights 400, 500 and 600,
  each file it names must exist as a WOFF2 whose SHA-256 matches the hash recorded in `fonts.css`'s header, and no
  Inter file may remain in `public/fonts`. A token that names an unloaded font, or a missing face, fails.
- **The token names, the thirteen `.t-*` roles, the forbidden `--color-sidebar`/`--color-header`, and the base-layer
  rules** still hold, as in ADR-0016.

## Consequences

- ADR-0016 is superseded for the vendor choice only. Its contract is in force and is restated above, so a reader
  need not open both to know the rule.
- Stages 2 to 4 are open work. Until stage 2 lands, the product renders with the new tokens and typeface over the
  test copy's components. This is coherent because the token names are shared, and the gate checks that.
- A component was declined, not lost. A future surface that needs `Dialog`, `Table`, `Tooltip`, `Menu`,
  `IdentityStrip` or `TypedConfirmation` builds it in the library first.
- The verdict's reasoning relies on the test copy's runbook and the two trees. It does not rely on that
  repository's private history, which was not inspected.
