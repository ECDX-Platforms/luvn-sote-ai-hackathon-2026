# Event-day runbook — building the component library

Rehearsed in `ECDX-Platforms/comp-lib-dev` (test repo) on 8 Oct 2026.
At the event the real library is **`LUVN-Component-Library`**. Do not push to it before the event.

Source of truth: the Swiss 1 baseline styleguide (v2.1) and the Figma **Design-Library** file
(`P8E4RKubznUkk8vi9765oW`, Tietoevry org). Figma and code use the same names.

## 0. Before the event (done in the test repo)

| Step | Status (8 Oct) |
|---|---|
| GitHub sign-in on the Mac (`gh auth login`, HTTPS), git name + noreply email | Done |
| Token pipeline: Figma export → DTCG → Style Dictionary → `tokens.css` → 198 checks | Proven |
| Round trip: one Figma colour change → exactly one changed line in `tokens.css` | Proven, reverted |
| One component end to end (Button): Figma → React → Storybook → axe | Proven |
| CI (GitHub Actions): tokens, generated files committed, typecheck, Storybook build | Proven on GitHub (PR #2, green) |
| More component types: TextField (form), Tag, Alert (status, dismiss), Dialog (modal, focus trap) | Proven 9 Oct. Test only: not copied to the real repo, rebuilt at the event |
| Every Figma component outside Patterns (45 Figma components → 40 code components: form controls, tags, navigation, data display, overlays, app shell pieces) | Built 9 Oct in 5 batches, each measured against Figma, axe light + dark, reviewed. Test only, rebuilt at the event. Not built: the Patterns page and a responsive app-shell layout (menu button below 1024px, bottom bar on phones) |
| Code Connect | **Skipped (decision 8 Oct): optional, after the event.** Template for Button kept in the test repo (`comp-lib-dev`) for later. |

## 1. Set up the real repo (once, at the start)

1. Copy the pipeline from the test repo: `package.json`, `tsconfig.json`, `vite.config.ts`, `.storybook/`, `.github/`, `scripts/`, `tokens/`, `src/styles/`, `figma.config.json`, `docs/`.
   `src/components/Button/` is general-purpose and may be copied too (declared as brought); the team decides. Everything else in `src/components/` is built at the event.
2. `npm install`, then `npm run tokens` → expect "198 of 198".
3. `npm run storybook` → Foundations pages render in light and dark.
4. Open the first pull request ("Foundations"). Merge after review.

## 2. Tokens changed in Figma

1. Run `scripts/figma-export.plugin.js` with the Figma MCP `use_figma` tool (read-only) and save the returned JSON as `tokens/figma-export.json`.
2. `npm run tokens`. The check must pass.
3. `git diff` must show only the intended values. Commit, pull request.

## 3. Each component (repeat per component, in Figma page order)

1. **Read the design.** `get_design_context` on the component set (with screenshot) and `get_context_for_code_connect` for its properties.
2. **Build** `src/components/<Name>/`:
   - `<Name>.tsx` — props follow the Figma properties; hover, focus and disabled are browser states, not props.
   - `<Name>.module.css` — tokens only (`var(--…)`), `.t-*` type roles, no raw hex, no off-scale sizes.
   - `index.ts`, and export from `src/index.ts`.
3. **Stories** `<Name>.stories.tsx`: one story per main use, plus an "All variants" grid that mirrors the Figma set.
4. **Check in the browser** (Storybook): measure height, padding, colours, type and radius against Figma; hover, Tab for focus; light and dark.
5. **Accessibility:** axe (WCAG 2.2 AA) on every story, light and dark → 0 violations. `npm test` runs it for every story in both themes; tag a story `no-overflow` to also check it at 320 and 375px.
6. **Gate:** `npm run tokens`, `npm run typecheck`, `npm run build-storybook`, `npm test`. Then the reviewer, then a pull request per component group.

Order (Figma pages): Buttons → Form controls → Tags and flags → Alerts → Navigation → Data display → Overlays → App shell → Patterns.
Challenge-specific patterns are designed and built during the event only.

## 4. Code Connect (optional, after the event)

Not part of the event build: components are built from Figma with `get_design_context`, and devs use them from code and Storybook. If the product continues, add Code Connect then:

1. In Figma: Assets panel → **Publish library** (Design-Library). Needed once, and again after component changes.
2. Publish the templates with the CLI: `npx figma connect publish` (Figma personal access token with Code Connect write scope, entered by its owner in their own terminal, never in chat or in the repo).
   Lesson from the rehearsal (8 Oct): the Figma MCP `add_code_connect_map` tool only stored a plain link and ignored the template, so Dev Mode showed Figma's property names (`variant="Danger"`, `label=…`) instead of real code. A plain link must be disconnected in Figma before the CLI can publish the template.
3. Check: `get_design_context` on an instance (or Dev Mode) must show the real code, e.g. `<Button variant="danger">Delete record</Button>`.

## 5. Known traps (from the rehearsal)

- Figma: editing a text style on one variant spreads to all variants that share a text property. Set styles before combining variants.
- Figma: `resize()` resets "hug" sizing. Set sizing after resizing.
- Figma: shared text and icon defaults across variants. Use neutral defaults ("Label") so a status never shows a wrong word or icon.
- macOS `sed` differs from Linux; use the project scripts, not ad-hoc edits, for generated files.
- Never edit `tokens.css` or `tokens/dtcg/` by hand. CI fails if they differ from a fresh build.
- Make `package-lock.json` with the same npm version as CI (`npx npm@<CI version> install`). An older local npm left out Linux-only packages and `npm ci` failed on GitHub.

Second rehearsal (9 Oct: TextField, Tag, Alert, Dialog):
- Figma MCP `get_metadata` without a node id can list only some pages (Overlays, Data display, App shell and Patterns were missing). List pages with a read-only `use_figma` script instead.
- Figma sample text is not always neutral (names, care wording in Field, Tag and Dialog defaults). Never copy sample text from `get_design_context` into code or stories; write neutral story text.
- The styleguide is the only spec. Where Figma and the styleguide differ (Figma focus ring sits 3px out, the styleguide says 2px; Alert keyword and sentence are two columns in Figma, inline text in the starter CSS), follow the styleguide and note it.
- The starter CSS is not complete (no Success status tag). Build the missing variant from the same token pattern, and say so in a CSS comment.
- Every interactive part needs the shared states (hover, focus, pressed, disabled) or a note why a state does not apply. Icon-only buttons need `aria-label` and a tooltip.
- axe: inject `axe-core` into the story's own iframe and run it on `#storybook-root`. Running it from the outer page fails, and the iframe also holds Storybook's own hidden buttons.
- Modal stories (Dialog): set `docs.story.inline: false`, or the docs page opens several modals at once.
- Accessibility checks need a real keyboard for focus: click the page, then Tab. Script `focus()` does not always show `:focus-visible`.

Full-library rehearsal (9 Oct, all components):
- The Storybook dev server does not pick up a new component folder until it is restarted (no error, the story is just missing). Restart after adding folders; CI's static build is not affected.
- Measure at desktop width (e.g. a 1200px frame). A narrow preview pane is phone width (under 640px), where body text is 17/24 and controls are 44 high.
- Storybook's preview page holds its own hidden table and buttons. Scope every check to `#storybook-root`.
- Test scroll-based behaviour (identity strip) on a real page: off-screen test frames pause IntersectionObserver.
- Native `<dialog>`: Chrome may close it on Esc even when the cancel event is cancelled (no click on the page yet, or a second Esc). To keep it open (typed confirmation), stop the Escape key itself.
- Tooltips and menus are placed with `position: fixed` from their trigger, so scroll areas (wide tables) do not clip them. An element hidden while it is being placed cannot take focus: move focus after placing.
- Table rows: collapsed borders make rows 36.5–37 high; draw the hairline inside the row (inset shadow) to get exactly 36 as in Figma. Inline-flex content (Flag) needs `vertical-align: top` or the row grows to 40.
- Icons: Figma's 16px icons draw a 1px stroke, the same as lucide at stroke width 1.5. The styleguide's "1.5px" is on lucide's 24px grid.
- Exception to the aria-disabled rule: a disabled Radio uses native `disabled`, because arrow keys would still select an aria-disabled radio. Explain why in the group hint.
- Only checked in Chromium. Check in Firefox and Safari at the event: radio dot, toggle thumb and checkbox tick (drawn on native inputs), the whole-row table link and its focus outline (`:has`), and the fixed-position tooltip and menu.

## 6. Declare it

Log in the vault `What we brought.md` everything made before the event:
- Figma Design-Library: tokens, text styles, icons, general-purpose components (challenge-shaped examples removed on 8 Oct).
- Code (test repo): token pipeline and checks, base styles, Storybook setup, CI workflow, Figma export script.
- Button React component and its Code Connect template (the rehearsal component; test repo only, rebuilt at the event in this repo).
- This runbook.

Anything shaped to Challenge 1.1 is built at the event.
