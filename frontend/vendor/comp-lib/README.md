# LUVN-Component-Library — Swiss 1 component library

React component library for the LUVN product.
It mirrors the Figma **Design-Library** file and follows the Swiss 1 baseline styleguide (v2.1).

- **Figma:** https://www.figma.com/design/P8E4RKubznUkk8vi9765oW/Design-Library
- **Styleguide:** Swiss 1 baseline v2.1, the rule for every UI build. It is kept by Theo (design owner); ask him for the current copy until it is added to this repo.
- **Status:** built at the event (13–14 Oct 2026), component group by component group, in Figma page order. Steps: `docs/EVENT-RUNBOOK.md`.

## Run it

```bash
npm install
npm run storybook
```

Open http://localhost:6006. Use **Theme** in the toolbar to switch light and dark.

| Command | What it does |
|---|---|
| `npm run storybook` | Component showcase with accessibility checks (WCAG 2.2 AA) on every story. |
| `npm test` | Runs every story in a real browser (Chromium) with axe (WCAG 2.2 AA), once in light and once in dark; any violation fails. Stories tagged `no-overflow` must also fit 320px and 375px screens. Plus browser tests outside Storybook: CSS (`test/`) and component behaviour (`src/**/*.test.tsx`). First time: `npx playwright install chromium`. |
| `npm run tokens` | Rebuilds `src/styles/tokens.css` from the Figma export and checks every value against Figma. |
| `npm run typecheck` | TypeScript check. |
| `npm run build-storybook` | Static Storybook in `storybook-static/`. |

## How tokens flow

```
Figma variables ──export──▶ tokens/figma-export.json
                            │  scripts/figma-to-dtcg.mjs
                            ▼
                 tokens/dtcg/*.json   (Design Tokens Format 2025.10)
                 + tokens/styleguide-only.json (motion: not in Figma)
                            │  scripts/build-tokens.mjs (Style Dictionary 5)
                            ▼
                 src/styles/tokens.css   light on :root, dark on :root[data-theme="dark"],
                                         density on [data-density="comfortable"] and under 640px, type roles as .t-* classes
                            │  scripts/verify-tokens.mjs
                            ▼
                 198 checks: every Figma value (light and dark, color-mix resolved),
                 every styleguide system variable, the density block and .muted
```

- Never edit `tokens.css` or `tokens/dtcg/` by hand. Change the Figma variables, export again, run `npm run tokens`.
- CSS variable names are the ones in the styleguide (`--color-surface`, `--space-md`, `--control-h` …).
- Stroke widths (1px hairline, 2px selection and focus) have no CSS variable in the styleguide and are written as values in component CSS.

**Compared with the styleguide's reference CSS**
- Same: every preset and system variable, comfortable density (by `data-density="comfortable"` and automatically under 640px), the `.t-*` roles, `.muted`. Derived colours use `color-mix()` exactly as in the styleguide.
- Added (they exist in Figma): `--color-on-primary-muted`, `--color-on-inverse-muted`, `--color-on-inverse-link` (the emphasis-block mixes as variables) and `.t-initials` (avatar initials, 12/16 600).
- Left out: `--color-sidebar`, `--color-header` and their `--color-on-…` pairs. The styleguide marks them "not used in this style" and they are not in Figma.
- Comfortable density is in the CSS (from the styleguide) but not yet modelled in Figma.

## Use it in the app

Import the styles once:

```ts
import '@luvn/component-library/fonts.css'; // optional: Host Grotesk 400, 500, 600
import '@luvn/component-library/styles.css';
```

**Fonts.** `styles.css` sets `font-family: "Host Grotesk"` but does not load the font: without it, text falls back to `system-ui`. The app must load Host Grotesk in weights **400, 500 and 600** (the only weights the styleguide uses). The easiest way is `fonts.css` above: it self-hosts the three weights from `@fontsource/host-grotesk` (Latin and Latin Extended, no request to Google Fonts). If the app already loads the font another way, leave `fonts.css` out. `test/consumer.test.ts` checks this setup outside Storybook.

Components will be imported from `@luvn/component-library` as they are added.

Dark mode: set `data-theme="dark"` on `<html>`. Light is the default.
How the product consumes the library (installed from this repo, or copied into `frontend/src`) is decided with the devs once components exist. Today the package points at source files (`.ts`, `.css`) with no build step: that works when installed into a Vite app or when copied. Another bundler would need a build step, which we add if needed.

Notes for devs: TypeScript 7, React 19, Vite 8, Storybook 10.6. Storybook loads Host Grotesk through the same `fonts.css` an app would use.

## Structure

```
tokens/              figma-export.json (Figma source), styleguide-only.json (motion), dtcg/ (generated)
scripts/             token export, build and check
src/styles/          tokens.css (generated), base.css (from the styleguide), index.css, fonts.css (optional)
src/foundations/     Storybook pages: Colour, Typography, Spacing and shape
src/components/      one folder per component
.storybook/          Storybook config: theme switch, fonts, accessibility and phone-width checks
test/                browser tests outside Storybook: nested type roles, fonts in a consuming app
```

## Rules for components

Every component follows the styleguide: tokens only (no raw hex, no off-scale sizes), the `.t-*` type roles, three weights, status as icon + word, selection as the 2px bar, focus ring 2px with 2px offset. Components the styleguide does not define say **NEW** in their docs, as in Figma.

## How changes get in

Work on a branch, open a pull request. Juhis or Aslam review and merge. After the initial commit (README and .gitignore only), nothing goes straight to `main`.
