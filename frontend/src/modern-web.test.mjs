// The modern-web-guidance gate: the four platform patterns this app adopted, pinned so they cannot rot.
//
// The properties these guides recommend are invisible to a DOM audit — jsdom does not lay out, so it cannot
// tell `text-wrap: pretty` from `text-wrap: wrap`, nor watch a view transition cross-fade — so they are
// asserted at the level at which they are actually decided: the stylesheet's declarations, the resolved
// cascade for the one property whose winner can be shadowed (the sheet has a history of that; see
// frontend/scripts/find-shadowed-css.mjs), and the shell's call sites.
//
// Two of the four are read off a rendered document with `getComputedStyle`: `contain` on the real chat-rail
// row and the real worklist row, and `field-sizing` on the app's textarea rule (that one through a synthetic
// host, because the rendered one only exists after an edit click — the claim under test is the cascade, not
// the markup). The view-transition half extracts the real `startViewTransition` and `selectTheme` out of
// `App.jsx` and drives them against a stub document, rather than restating them here: a probe that
// re-implements the implementation can pass while the implementation is broken.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { JSDOM } from 'jsdom';
import postcss from '../node_modules/postcss/lib/postcss.mjs';
import { createServer } from 'vite';
import { fileURLToPath } from 'node:url';
import { viewAddress } from './challenge-demo.mjs';

const stylesPath = new URL('./styles.css', import.meta.url);
const appPath = new URL('./App.jsx', import.meta.url);
const rawCss = fs.readFileSync(stylesPath, 'utf8');
const appSource = fs.readFileSync(appPath, 'utf8');
const tokensCss = fs.readFileSync(
  fileURLToPath(new URL('../vendor/comp-lib/src/styles/tokens.css', import.meta.url)), 'utf8');
const baseCss = fs.readFileSync(
  fileURLToPath(new URL('../vendor/comp-lib/src/styles/base.css', import.meta.url)), 'utf8');

const declaredTokens = new Set([...tokensCss.matchAll(/--([\w-]+)\s*:/g)].map(([, name]) => name));

/** Every rule for an exact selector part, its body as written, in source order — the same collector shape
 *  as the design-system gate's `rulesFor`, so a second rule for the same selector is found, not shadowed. */
function rulesFor(selector) {
  const found = [];
  for (const match of rawCss.replace(/\/\*[\s\S]*?\*\//g, '').matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
    const [, selectorText, body] = match;
    if (selectorText.split(',').map(part => part.trim().replace(/\s+/g, ' ')).includes(selector)) found.push(body);
  }
  return found;
}

/** Every declaration of `property` in the sheet, with the selector list it was written under. */
function declarationsOf(property) {
  const decls = [];
  postcss.parse(rawCss).walkDecls(property, decl => decls.push({ selector: decl.parent.selector, value: decl.value.trim() }));
  return decls;
}

/** A jsdom document carrying the shipped sheets, then `extraCss`, and `markup` in the body. The sheets are
 *  the same three the a11y gate installs (tokens, base, app), so a cascade read here is the shipped one. */
function documentFor(markup, extraCss = '') {
  const css = [tokensCss, baseCss, rawCss, extraCss].join('\n');
  return new JSDOM('<!doctype html><html lang="fi" data-theme="light"><head><style>'
    + `${css}</style></head><body><div class="brief-app"><main>${markup}</main></div></body></html>`).window.document;
}

// ---------- 1. text legibility (`improve-text-layout-and-legibility`, `typography`) ----------
//
// The guide: "For short text blocks like headings, use `text-wrap: balance`… Use `text-wrap: pretty` for
// long-form body text (paragraphs, blockquotes, etc.)" and "DO NOT apply it on `*`". The declarations are
// asserted with their whole selector lists, because `pretty` landing on `th` and `balance` landing on `li`
// is not the advice even when both strings appear in the sheet.
{
  const balance = declarationsOf('text-wrap').filter(decl => decl.value === 'balance');
  const pretty = declarationsOf('text-wrap').filter(decl => decl.value === 'pretty');
  assert.equal(balance.length, 1, `exactly one \`text-wrap: balance\` rule, found ${balance.length}`);
  assert.equal(pretty.length, 1, `exactly one \`text-wrap: pretty\` rule, found ${pretty.length}`);

  const balanced = balance[0].selector.split(',').map(part => part.trim());
  assert.deepEqual(balanced, ['.brief-app h1', '.brief-app h2', '.brief-app h3', '.brief-app th'],
    'balance reaches exactly the headings and the table headers the guide names, and nothing else');
  const prettied = pretty[0].selector.split(',').map(part => part.trim());
  assert.deepEqual(prettied, ['.brief-app p', '.brief-app li', '.brief-app dd'],
    'pretty reaches exactly the running-text blocks');

  // The guide's warning: `balance` on an element with a visible box leaves a visibly empty area, because it
  // changes wrapping and not the container's width. Every balanced element here is a bare heading-like tag —
  // the chips (`brief-record-state`) are spans, not headings, which is what keeps
  // this true as the sheet grows.
  const HEADING_LIKE = new Set(['h1', 'h2', 'h3', 'th']);
  for (const part of balanced) {
    const element = part.split(/\s+/).pop();
    assert(HEADING_LIKE.has(element), `${part} is not the kind of short block \`balance\` is for`);
    const boxed = rulesFor(part).join(' ');
    assert(!/background|border/.test(boxed),
      `${part} must not carry a visible box, which the guide warns against for balance`);
  }
  assert.deepEqual(declarationsOf('text-wrap').filter(decl => /^\*|^html|^body/.test(decl.selector.trim())), [],
    'text-wrap is never applied to `*` or the page root, which the guide forbids');

  // Non-vacuity: the collector reads real declarations (a rule that exists is found, an invented selector
  // is not), and the two value groups are genuinely different sets.
  assert(rulesFor('.brief-app h2').length > 0, 'the collector finds the rule it just judged');
  assert.equal(rulesFor('.brief-app blockquote').length, 0, 'the collector cannot invent a rule that is not there');
  assert.notDeepEqual(balanced, prettied, 'the short blocks and the long blocks are different element sets');
}

// ---------- 2. reflow containment (`interactions-in-complex-layouts`) ----------
//
// The guide: "Apply `content-visibility: auto` to self-contained layout regions… If a mutation occurs inside
// a container with containment applied, the browser knows that the changes cannot affect the geometry or
// styles of elements outside that container. The page reflow is isolated." Its fallback section offers
// `contain: layout style paint`, and the app takes the layout half. Asserted on the rendered rows, because
// `contain` is not inherited and the boundary has to be on the element that mutates.
{
  const containRules = declarationsOf('contain');
  assert.equal(containRules.length, 1, `exactly one rule sets \`contain\`, found ${containRules.length}`);
  assert.equal(containRules[0].value, 'layout',
    'containment is `layout` only: `paint` would clip the focus ring and the inset selection mark');
  assert.deepEqual(containRules[0].selector.split(',').map(part => part.trim()),
    ['.brief-app .brief-decision-outcomes', '.brief-app .brief-proposal', '.brief-app .brief-day-row',
      '.brief-app .brief-case-group button'],
    'containment reaches the chat-rail rows, the day rows and the worklist rows, the app\'s mutation sites');

  const vite = await createServer({
    root: fileURLToPath(new URL('..', import.meta.url)),
    server: { middlewareMode: true, hmr: false },
    appType: 'custom',
  });
  let ChatRail;
  let CasePicker;
  try {
    ({ ChatRail } = await vite.ssrLoadModule('/src/ChatPanel.jsx'));
    ({ CasePicker } = await vite.ssrLoadModule('/src/App.jsx'));
  } finally {
    await vite.close();
  }

  // The chat rail's own row, rendered by the real component. Its decision buttons are replaced by a status
  // line under the pointer, which is the mutation the guide is about.
  const chatMarkup = renderToStaticMarkup(React.createElement(ChatRail, {
    turn: {
      turn_id: 'turn-1', digest_id: 'digest-1', seq: 1, outcome: 'answer', generator: 'gpt-6-luna',
      suggestions: [{ suggestion_id: 's0', turn_id: 'turn-1', ordinal: 0, statement_class: 'source_fact',
        text: 'Lähde kertoo lääkityksen olevan aktiivinen.', label: 'Lähde', source_record_ids: ['source-a'] }],
    },
  }));
  const chatDocument = documentFor(chatMarkup);
  const railRow = chatDocument.querySelector('li.brief-proposal');
  assert(railRow, 'the chat rail renders the suggestion row containment is for');
  assert.equal(chatDocument.defaultView.getComputedStyle(railRow).contain, 'layout',
    'a suggestion row is a containment boundary, so its decision buttons reflow only inside it');
  assert.equal(chatDocument.defaultView.getComputedStyle(railRow).display, 'list-item',
    'containment did not change what the row is — it is still the list the rail renders');

  // The worklist's selection row, rendered by the real component.
  const worklistMarkup = renderToStaticMarkup(React.createElement(CasePicker, {
    items: [{ encounterId: 'e-1', label: 'Lääkityksen seuranta', person: 'Aino Virtanen',
      roleLabel: 'Sairaanhoitaja', purposeLabel: 'Lääkityksen seuranta' }],
    summary: { encounters: 1, people: 1, purposes: 1 }, total: 1, offset: 0,
    selectedId: null, onSelect: () => {}, onPage: () => {},
  }));
  const worklistDocument = documentFor(worklistMarkup);
  const row = worklistDocument.querySelector('.brief-case-group button');
  assert(row, 'the worklist renders the selection row containment is for');
  assert.equal(worklistDocument.defaultView.getComputedStyle(row).contain, 'layout',
    'a worklist row is a containment boundary too');
  const wrapper = worklistDocument.querySelector('.brief-case-group > div');
  assert.notEqual(worklistDocument.defaultView.getComputedStyle(wrapper).contain, 'layout',
    'the boundary is on the rows, not on the group wrapper — contain is not inherited');

  // Non-vacuity of the computed read, two ways. First, the same markup with no app sheet carries no
  // containment at all, so "layout" is the sheet's declaration and not a default. Second, the winning rule
  // is read back off the document's own CSSOM — which is where jsdom is reliable, and it is the property
  // that matters anyway: `getComputedStyle` is not used for the cascade half because jsdom does not
  // re-resolve `contain` for a later same-specificity rule (verified: an injected `.brief-app
  // .brief-proposal { contain: none }` is still reported as `layout`, while an injected `z-index` in the
  // same rule is reported correctly).
  const plainDocument = new JSDOM('<!doctype html><html><head></head><body>'
    + `${chatMarkup}</body></html>`).window.document;
  assert.equal(plainDocument.defaultView.getComputedStyle(plainDocument.querySelector('li.brief-proposal')).contain,
    'none', 'without the app sheet the row carries no containment, so the declaration is what set it');
  const chatStyleSheet = chatDocument.querySelector('style').sheet;
  const applied = [...chatStyleSheet.cssRules].filter(rule =>
    rule.selectorText && rule.style?.contain
    && [...rule.selectorText.split(',')].some(part => {
      try { return railRow.matches(part.trim()); } catch { return false; }
    }));
  assert.equal(applied.length, 1, `exactly one CSSOM rule applies contain to the rendered row (${applied.length})`);
  assert.equal(applied[0].style.contain, 'layout',
    'the rule that reaches the rendered row is the one that declares containment');
}

// ---------- 3. the auto-sizing proposal textarea (`form-fields-automatically-fit-contents`) ----------
//
// The guide: "Setting `field-sizing: content` on… textareas allows them to resize dynamically as the user
// types", "you must account for… minimum/maximum constraints", and "For textareas… apply an explicit width
// (like `width: 100%`)… This forces the textarea to maintain a stable horizontal width while still
// auto-sizing vertically". The rule's selector is `.brief-proposal textarea`, the note field the briefing's
// review card renders after its edit action; the cascade is read through that same host, because a
// declaration a later same-selector rule overwrote is the failure mode this assertion exists for.
{
  const fieldSizing = declarationsOf('field-sizing');
  assert.equal(fieldSizing.length, 1, `exactly one rule sets \`field-sizing\`, found ${fieldSizing.length}`);
  assert.equal(fieldSizing[0].value, 'content', 'the one use is the guide\'s value');
  assert.equal(fieldSizing[0].selector, '.brief-app .brief-proposal textarea',
    'it reaches the note field and nothing else — the chat form\'s TextArea is a vendored component');

  const document = documentFor('<section class="brief-proposal"><textarea></textarea></section>');
  const textarea = document.querySelector('.brief-proposal textarea');
  assert(textarea, 'the host markup the rule targets is present');
  const computed = document.defaultView.getComputedStyle(textarea);
  assert.equal(computed.fieldSizing, 'content',
    'the note field sizes itself to its content, as the guide prescribes for textareas');
  assert.equal(computed.width, '100%',
    'the inline size stays explicit, which is what stops the horizontal jump the guide warns about');
  assert.equal(computed.maxInlineSize, '100%', 'the guide\'s maximum is applied on the inline axis');
  // `lh` resolves against the element's own line-height, which the vendored base layer sets from the
  // library's `--lh-body` token — so the floor and ceiling are line counts, not a length the app invented.
  // jsdom resolves the `lh` unit to px but leaves the inherited `line-height` as its `var()` text, so the
  // claim is checked against the ratio and against the token: two lines against eight. (The token is
  // asserted to be what the base layer uses, below — that is the half jsdom cannot resolve for us.)
  const minBlock = parseFloat(computed.minBlockSize);
  const maxBlock = parseFloat(computed.maxBlockSize);
  assert(minBlock > 0 && maxBlock > 0,
    `the note field has both bounds resolved (${computed.minBlockSize} / ${computed.maxBlockSize})`);
  assert.equal(maxBlock / minBlock, 4,
    'the ceiling is four times the floor, which is what makes them eight lines and two lines');
  const resolvedLine = minBlock / 2;
  assert(resolvedLine >= 16 && resolvedLine <= 32,
    `two lines resolve to a plausible line box (${resolvedLine}px per line, i.e. ${computed.lineHeight})`);
  assert.equal(computed.minHeight, '40px', 'the older fixed floor is the fallback geometry for the above');

  // The fallback is the current behaviour, stated as a claim about the sheet: `field-sizing` is the ONLY
  // thing the added rule contributes. Remove it and the rule that remains is the pre-existing textarea rule
  // — which is exactly what an engine without `field-sizing` renders.
  const autoSizingRule = rulesFor('.brief-app .brief-proposal textarea').find(body => /field-sizing/.test(body));
  assert(autoSizingRule, 'the sheet has a rule declaring field-sizing on the note field');
  assert.deepEqual(autoSizingRule.split(';').map(part => part.trim()).filter(Boolean).sort(),
    ['field-sizing: content', 'max-block-size: 8lh', 'max-inline-size: 100%', 'min-block-size: 2lh'],
    'the rule adds field-sizing plus only its three bounds');
  const withoutSizing = autoSizingRule.replace(/field-sizing:\s*content;?/, '')
    .split(';').map(part => part.trim()).filter(Boolean);
  assert.deepEqual(withoutSizing.sort(), ['max-block-size: 8lh', 'max-inline-size: 100%', 'min-block-size: 2lh'],
    'with field-sizing removed, the rule is inert bounds and the existing textarea rule is what renders');
  assert(rulesFor('.brief-app .brief-proposal textarea').some(body => /min-height:\s*40px/.test(body)),
    'the original fixed-size rule is still there for an engine that ignores field-sizing');

  // Every value in the added rule is on the library's scale. `lh` resolves from the row's own line-height
  // token, so no new length is introduced; the design-system gate's value grammar runs these too.
  const measured = [...autoSizingRule.matchAll(/(?:max-block-size|min-block-size|max-inline-size|field-sizing):\s*([^;]+)/g)]
    .map(([, value]) => value.trim());
  assert.deepEqual(measured, ['content', '100%', '2lh', '8lh'], 'the rule declares the four values under test');
  assert(!measured.some(value => /(?:rem|em|ch|pt|px)\b/.test(value)),
    'the line-height units are `lh`, not a length the token scale would have to own');
  assert(declaredTokens.has('lh-body'), 'the line height the `lh` unit resolves against is a library token');
  assert(/line-height:\s*var\(--lh-body\)/.test(baseCss),
    'the vendored base layer is what sets that line-height on the textarea\'s ancestors');
}

// ---------- 4. the smooth theme swap (`same-document-transitions`, `motion`, `dark-mode`) ----------
//
// The guide: "Wrap the logic that updates the DOM in `document.startViewTransition`… The browser captures a
// snapshot of the current state, runs the update, and then captures the new state." `motion` requires that
// motion be opt-out-able, and `dark-mode` requires that the native UI follow the scheme. jsdom has no View
// Transitions, so what is asserted is what the guides actually decide: the real wrapper takes the fallback
// branch when the API is absent or the reader asked for reduced motion, and the shell routes the theme swap
// through it while leaving the colour-scheme half to the pinned tokens.
{
  const wrapperSource = /function startViewTransition\(update\) \{[\s\S]*?\n\}/.exec(appSource)?.[0];
  assert(wrapperSource, 'App.jsx still declares `startViewTransition(update)`');
  assert.equal([...appSource.matchAll(/function startViewTransition\(/g)].length, 1,
    'there is exactly one wrapper, so the extracted one is the one the shell calls');
  const selectThemeSource = /function selectTheme\(theme\) \{[\s\S]*?\n  \}/.exec(appSource)?.[0];
  assert(selectThemeSource, 'App.jsx still declares `selectTheme(theme)`');
  // selectTheme writes the address through the one shared writer, so the harness runs that writer too.
  const writeAddressSource = /function writeAddress\([\s\S]*?\n  \}/.exec(appSource)?.[0];
  assert(writeAddressSource, 'App.jsx still declares the one address writer, `writeAddress`');
  const guardSource = /const REDUCED_MOTION = "\(prefers-reduced-motion: reduce\)";/.exec(appSource)?.[0];
  assert(guardSource, 'the reduced-motion query is a named constant holding the query itself');
  // The view transition is the one scripted-motion feature left, and it consults the query through that one
  // constant. The smooth scroll that used to be the second call site went with the run-specifications
  // disclosure (B2: the technical page is a destination, not a section to scroll to), so there is exactly
  // one call site — asserted exactly, so a new scripted motion that skips the guard is counted rather than
  // waved through. The phone-header hook (`useMediaQuery`) also calls `matchMedia`, for a width query, and is
  // deliberately not matched here: it is not motion.
  assert.equal([...appSource.matchAll(/matchMedia\?\.\(REDUCED_MOTION\)\.matches/g)].length, 1,
    'the transition wrapper is the one reader of the reduced-motion query');
  assert(!/scrollIntoView/.test(appSource),
    'no scripted scroll remains in the shell: the technical page is a destination, not a scroll target');
  assert(/function startViewTransition[\s\S]*?matchMedia\?\.\(REDUCED_MOTION\)\.matches/.test(appSource),
    'the view-transition wrapper consults it');

  let historyWrite = null;
  // `hasApi` is what makes the two fallback branches reachable at all. The reduced-motion branch stubs the
  // API in and asserts it is never called; the no-API branch stubs it out and asserts the update still runs.
  // A wrapper that always called `document.startViewTransition` would fail the second, and one that never
  // did would fail the third.
  function drive({ hasApi = true, reduced = false } = {}) {
    const calls = { transitions: 0, updates: 0, renders: 0 };
    const documentStub = { documentElement: { dataset: {} } };
    if (hasApi) {
      documentStub.startViewTransition = callback => {
        calls.transitions += 1;
        callback();
        return { finished: Promise.resolve() };
      };
    }
    const setPageOptions = () => { calls.renders += 1; };
    const windowStub = { location: { search: '', pathname: '/', hash: '' },
      history: { replaceState(_a, _b, url) { historyWrite = url; } } };
    const api = new Function('globalThis', 'document', 'setPageOptions', 'window', 'URLSearchParams', 'viewAddress',
      'encounterId', 'roleChoice', 'activeRole', 'mode', 'pageOptions', 'page',
      `${guardSource}\n${wrapperSource}\n${writeAddressSource}\n${selectThemeSource}\nreturn { startViewTransition, selectTheme };`)(
      { matchMedia: () => ({ matches: reduced }) }, documentStub, setPageOptions, windowStub, URLSearchParams,
      // `page` is the destination's address parameter (`technical`, or undefined on the briefing and the day);
      // the harness stands on the briefing, so the writer must leave it out of the address below.
      viewAddress, '', '', 'sairaanhoitaja', 'normal', { theme: 'light', capture: false }, undefined);
    return { api, calls, documentStub };
  }

  // Fallback branch 1: the reader asked for reduced motion, with the API present. The update still runs and
  // the API is never called.
  {
    const { api, calls } = drive({ hasApi: true, reduced: true });
    api.startViewTransition(() => { calls.updates += 1; });
    assert.equal(calls.updates, 1, 'reduced motion still applies the update');
    assert.equal(calls.transitions, 0, 'reduced motion never starts a view transition, even where one exists');
  }
  // Fallback branch 2: no API at all — an engine without it, and this process.
  {
    const { api, calls } = drive({ hasApi: false });
    assert.equal(typeof globalThis.document?.startViewTransition, 'undefined',
      'the process has no View Transitions API, which is also the real fallback this asserts');
    api.startViewTransition(() => { calls.updates += 1; });
    assert.equal(calls.updates, 1, 'an engine without the API still applies the update');
    assert.equal(calls.transitions, 0, 'and starts no transition, because there is none to start');
  }
  // The enhancement branch, and the real call site: the attribute and the state update are inside the one
  // transition, so the snapshots are the document before and after the swap.
  {
    const { api, calls, documentStub } = drive({ hasApi: true, reduced: false });
    historyWrite = null;
    api.selectTheme('dark');
    assert.equal(calls.transitions, 1, 'the theme toggle goes through startViewTransition');
    assert.equal(calls.renders, 1, 'the state that re-renders the app is updated inside the transition');
    assert.equal(documentStub.documentElement.dataset.theme, 'dark',
      'the attribute the pre-paint script also writes is set inside the transition');
    // Review finding 2: the address names the effective role on screen, which is `activeRole` here. The
    // picker choice (`roleChoice`) is empty in this harness, as it is on a bare load, so the old `?theme=dark`
    // dropped the lens that is on screen.
    assert.equal(historyWrite, '/?role=sairaanhoitaja&theme=dark',
      'the swap stays addressable, and the address names the effective role, not the empty picker choice');
  }
  assert(appSource.includes('document.documentElement.dataset.theme'),
    'the pre-paint attribute is still what the app writes, not a second theme mechanism');
  assert(/startViewTransition\(\(\) => \{[\s\S]*?setPageOptions/.test(selectThemeSource),
    'the state update is inside the same transition as the attribute');

  // The library owns the colour-scheme half: `:root[data-theme="dark"] { color-scheme: dark }` in the
  // pinned tokens is what themes the native scrollbars, which is why the app declares none of its own and
  // why the `dark-mode-toggle` guide's `<meta>`/`:has()` indirection has nothing to do here.
  assert(/:root\[data-theme="dark"\]\s*\{\s*color-scheme:\s*dark/.test(tokensCss),
    'the vendored tokens still key `color-scheme` off the attribute the app writes');
  assert(!/color-scheme/.test(rawCss),
    'the app sheet declares no `color-scheme` of its own — that would be a second source of truth');
  assert(/prefers-reduced-motion:\s*reduce/.test(baseCss)
    && /\*\s*,\s*\*::before\s*,\s*\*::after\s*\{[^}]*animation-duration:\s*0s\s*!important/.test(baseCss),
    'the vendored base layer is where reduced motion is enforced for the app\'s animations and transitions');
}

console.log(JSON.stringify({
  tool: 'modern-web-guidance gate',
  guides: ['improve-text-layout-and-legibility', 'typography', 'interactions-in-complex-layouts',
    'form-fields-automatically-fit-contents', 'same-document-transitions', 'motion', 'dark-mode'],
  checks: {
    textWrapRules: declarationsOf('text-wrap').length,
    containRules: declarationsOf('contain').length,
    fieldSizingRules: declarationsOf('field-sizing').length,
    viewTransitionCallSites: [...appSource.matchAll(/startViewTransition\(/g)].length,
  },
}));
