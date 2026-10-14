// Automated accessibility gate for the rendered briefing and the three-card comparison view.
//
// axe-core runs against the actual rendered DOM in jsdom. jsdom cannot lay out or paint, so axe's
// `color-contrast` rule is disabled. The only contrast check is the hardcoded WCAG ratio table below;
// it is not exhaustive and no real-browser Lighthouse audit exists in this repository.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { JSDOM } from 'jsdom';
import { createServer } from 'vite';
import { fileURLToPath } from 'node:url';
import postcss from '../node_modules/postcss/lib/postcss.mjs';
import { picturePresentation } from './challenge-demo.mjs';

const AXE_SOURCE = fs.readFileSync(
  fileURLToPath(new URL('../node_modules/axe-core/axe.min.js', import.meta.url)),
  'utf8',
);
const vendor = name => fileURLToPath(new URL(`../vendor/comp-lib/src/styles/${name}`, import.meta.url));
// The audited document must be the shipped document: the library's tokens and base layer, then the app
// sheet. The list is explicit rather than a relative `@import`, because jsdom does not fetch — reading
// design-system.css here would silently drop the whole library.
const SHEETS = [vendor('tokens.css'), vendor('base.css'), fileURLToPath(new URL('./styles.css', import.meta.url))];

/** Every adopted component's own stylesheet, with Vite's CSS-module hashes applied.
 *
 *  Scope, stated honestly: axe finds ARIA, structure and labelling problems, and `color-contrast` is
 *  disabled here because jsdom cannot paint. It does NOT catch an app selector out-specifying a component
 *  class — nothing in a DOM audit can, which is why that failure (`.brief-app button` erasing the Button's
 *  primary fill) was found by adversarial review and is now guarded structurally by `COMPONENT_ROOTS` in
 *  the design-system gate. What this does is close the blind spot that let a component render with **no
 *  stylesheet in the document at all**: a component whose CSS is absent cannot be judged for anything.
 *
 *  An app selector can silently erase a component's semantics — `.brief-app button` (0,1,1) beat the
 *  Button's class (0,1,0) and rendered every `variant="primary"` as a neutral surface button while every
 *  check stayed green. The audit could not see it because the component's CSS was never in the document.
 *
 *  This used to recover exactly one component (Button) by matching a hard-coded list of its class names.
 *  Every component adopted after it rendered **unaudited** while the gate stayed green, which is the
 *  failure the file's own header describes. The set is derived now:
 *
 *    1. every vendored `*.module.css` is hashed the way Vite hashes it (`_<block>_<base36(djb2(css))>_<line>`)
 *       to build a hash -> module map;
 *    2. the rendered markup is searched for that shape, which yields exactly the modules the render used;
 *    3. each of those modules is rewritten from `.<block>` to `._<block>_<hash>_<line>`.
 *
 *  So a newly adopted component's CSS reaches the audit on the commit that adopts it, and the per-module
 *  probes below fail if a module the render used could not be rewritten.
 */
function viteModuleHash(css) {
  let hash = 5381;
  for (let i = css.length - 1; i >= 0; i -= 1) hash = ((hash * 33) ^ css.charCodeAt(i)) >>> 0;
  return hash.toString(36).slice(0, 5);
}
const COMPONENT_MODULES = new Map();
for (const component of fs.readdirSync(vendor('../components'))) {
  const dir = vendor(`../components/${component}`);
  if (!fs.statSync(dir).isDirectory()) continue;
  for (const name of fs.readdirSync(dir)) {
    if (!name.endsWith('.module.css')) continue;
    const path = `${dir}/${name}`;
    const css = fs.readFileSync(path, 'utf8');
    COMPONENT_MODULES.set(`${component}/${name}`, { path, css, hash: viteModuleHash(css) });
  }
}
// The map has to be non-vacuous in both directions before anything below trusts it: it must reproduce a
// hash this repository has already observed in build output, and it must be keyed by the real modules.
assert(COMPONENT_MODULES.get('Button/Button.module.css').hash === '17e58',
  'the derived hash reproduces the Button hash Vite produced in dist/assets (17e58)');
assert(COMPONENT_MODULES.size >= 30, `the module map found ${COMPONENT_MODULES.size} sheets`);
assert(COMPONENT_MODULES.get('Tag/Tag.module.css') && COMPONENT_MODULES.get('KeyValueList/KeyValueList.module.css'),
  'the module map is keyed by component, not by a single hand-picked file');

/** `_<block>_<hash>_<line>` occurrences in rendered markup -> the component sheets they came from. */
const MODULE_BY_HASH = new Map();
for (const [name, module] of COMPONENT_MODULES) {
  if (!MODULE_BY_HASH.has(module.hash)) MODULE_BY_HASH.set(module.hash, []);
  MODULE_BY_HASH.get(module.hash).push(name);
}
function usedComponentModules(rendered) {
  const used = new Set();
  for (const match of rendered.matchAll(/_([A-Za-z][A-Za-z0-9]*)_([A-Za-z0-9]{4,8})_\d+/g)) {
    for (const name of MODULE_BY_HASH.get(match[2]) ?? []) used.add(name);
  }
  return used;
}

function componentModuleCss(rendered) {
  const chunks = [];
  for (const name of usedComponentModules(rendered)) {
    const module = COMPONENT_MODULES.get(name);
    // Rewrite EVERY `_<block>_<hash>_<line>` occurrence, not just a leading `.class`, so a descendent or
    // selector-list rule is rewritten too — and substitute the module's own hash, which is a property of
    // the file, so a mis-keyed module produces no match instead of plausible-looking CSS.
    // The callback args are (match, block, hash, line, offset, string) — the module's own hash is
    // substituted, so only the block name is carried across.
    const rewritten = module.css.replace(/_([A-Za-z][A-Za-z0-9]*)_([A-Za-z0-9]{4,8})_(\d+)/g,
      (whole, block, _hash, line) => `_${block}_${module.hash}_${line}`);
    // The component's own class names are not hashed in the source, so hash them the way Vite does.
    const hashed = rewritten.replace(/^(\s*)\.([a-zA-Z][\w-]*)/gm, (_, indent, cls) => `${indent}._${cls}_${module.hash}`);
    assert(hashed.includes(`_${module.hash}`), `the rewrite produced hashed CSS for ${name}`);
    chunks.push(`/* ${name} */\n${hashed}`);
  }
  return chunks.join('\n');
}
const CSS = SHEETS.map(path => fs.readFileSync(path, 'utf8')).join('\n');
const SKIN_CSS = CSS;
const TOKENS_CSS = fs.readFileSync(vendor('tokens.css'), 'utf8');

// ---------- deterministic WCAG contrast check (jsdom cannot paint) ----------

function luminance(hex) {
  const value = hex.replace('#', '');
  const channel = index => {
    const component = parseInt(value.slice(index, index + 2), 16) / 255;
    return component <= 0.03928 ? component / 12.92 : ((component + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * channel(0) + 0.7152 * channel(2) + 0.0722 * channel(4);
}
function contrast(foreground, background) {
  const [bright, dark] = [luminance(foreground), luminance(background)].sort((a, b) => b - a);
  return (bright + 0.05) / (dark + 0.05);
}

// The pairs that actually render text, named by token instead of hardcoded. A raw read of the vendored
// sheet would compare the wrong strings: most semantic roles are `var()` aliases and four are
// `color-mix()`, so the values have to be resolved the way the library's own verifier resolves them.
// Value is [foreground token, background token, minimum ratio].
const TOKEN_PAIRS = {
  'body ink on the canvas': ['color-text', 'color-bg', 4.5],
  'body ink on a surface': ['color-text', 'color-surface', 4.5],
  'muted text on the canvas': ['color-text-muted', 'color-bg', 4.5],
  'muted text on a surface': ['color-text-muted', 'color-surface', 4.5],
  'muted text on an emphasis tint': ['color-text-muted', 'color-primary-tint', 4.5],
  'muted text on a hover fill': ['color-text-muted', 'color-surface-hover', 4.5],
  'links on the canvas': ['color-link', 'color-bg', 4.5],
  'links on a surface': ['color-link', 'color-surface', 4.5],
  // The active navigation item used to be `--color-link` on `--color-primary-tint`; it is now the primary
  // fill with its paired on-primary label, so it shares the "primary button label on its fill" pair below
  // rather than naming a tint nothing renders behind link text any more. The tint pair above still renders:
  // the pressed case row puts muted text on it.
  'primary button label on its fill': ['color-on-primary', 'color-primary', 4.5],
  'critical alert text': ['color-critical-fg', 'color-critical-bg', 4.5],
  'warning alert text': ['color-warning-fg', 'color-warning-bg', 4.5],
  'success alert text': ['color-success-fg', 'color-success-bg', 4.5],
  'info alert text': ['color-info-fg', 'color-info-bg', 4.5],
  'control borders against a surface': ['color-line-strong', 'color-surface', 3],
  'the focus ring against a surface': ['color-focus', 'color-surface', 3],
  'the primary label, muted, on its fill': ['color-on-primary-muted', 'color-primary', 4.5],
  'inverse block text': ['color-on-inverse', 'color-inverse', 4.5],
  'inverse block muted text': ['color-on-inverse-muted', 'color-inverse', 4.5],
  'inverse block links': ['color-on-inverse-link', 'color-inverse', 4.5],
};

// NOT asserted, and deliberately: the four status borders are 1.79–2.17:1 against a surface, below the 3:1
// a *control* boundary needs. That is the styleguide's own split — `--color-line` is decorative and
// `--color-line-strong` is the one for controls — and it is exactly why status must be icon + word: the
// border is never the sole carrier of the meaning, and the tinted background and the icon are what a
// low-vision reader gets. Raising these would be a styleguide change, not a bug fix.

/** Every declaration in each block matching `\n<selector> {` — the two `:root` blocks both count. */
function declarations(css, selector) {
  const out = {};
  let from = 0;
  for (;;) {
    const start = css.indexOf(`\n${selector} {`, from);
    if (start < 0) return out;
    const open = css.indexOf('{', start);
    const close = css.indexOf('}', open);
    for (const [, name, value] of css.slice(open + 1, close).matchAll(/--([\w-]+):\s*([^;]+);/g)) {
      out[name] = value.trim();
    }
    from = close;
  }
}
const LIGHT = declarations(TOKENS_CSS, ':root');
const DARK = { ...LIGHT, ...declarations(TOKENS_CSS, ':root[data-theme="dark"]') };

function mixHex(a, percent, b) {
  const channel = (hex, i) => parseInt(hex.replace('#', '').slice(i, i + 2), 16);
  return `#${[0, 2, 4]
    .map(i => Math.round(channel(a, i) * percent / 100 + channel(b, i) * (100 - percent) / 100))
    .map(v => v.toString(16).padStart(2, '0'))
    .join('')}`;
}
function resolve(tokens, name, depth = 0) {
  if (depth > 10) throw new Error(`token cycle at --${name}`);
  const value = tokens[name];
  if (value === undefined) throw new Error(`--${name} is not declared in the vendored tokens`);
  const alias = /^var\(--([\w-]+)\)$/.exec(value);
  if (alias) return resolve(tokens, alias[1], depth + 1);
  const mix = /^color-mix\(in srgb, var\(--([\w-]+)\) (\d+)%, var\(--([\w-]+)\)\)$/.exec(value);
  if (mix) return mixHex(resolve(tokens, mix[1], depth + 1), Number(mix[2]), resolve(tokens, mix[3], depth + 1));
  return value;
}

let contrastPairsChecked = 0;
for (const [theme, tokens] of Object.entries({ light: LIGHT, dark: DARK })) {
  for (const [label, [foreground, background, minimum]] of Object.entries(TOKEN_PAIRS)) {
    const ratio = contrast(resolve(tokens, foreground), resolve(tokens, background));
    assert(ratio >= minimum, `${theme}: ${label} contrast ${ratio.toFixed(2)} must be >= ${minimum}`);
    contrastPairsChecked += 1;
  }
}
assert.equal(contrastPairsChecked, Object.keys(TOKEN_PAIRS).length * 2, 'every pair is checked in both themes');

// ---------- axe-core over the rendered DOM ----------

function audit(html) {
  const dom = new JSDOM(html, { runScripts: 'outside-only', pretendToBeVisual: true });
  dom.window.eval(AXE_SOURCE);
  return dom.window.axe
    .run(dom.window.document, {
      // Painting-dependent; disabled because jsdom cannot compute painted contrast. TOKEN_PAIRS, checked
      // above from the resolved vendored tokens, is a partial check only and no real-browser Lighthouse
      // audit is claimed.
      rules: { 'color-contrast': { enabled: false } },
    })
    // Re-materialise in this realm: axe's arrays/objects come from the jsdom realm and
    // assert.deepEqual compares prototypes.
    .then(results => JSON.parse(JSON.stringify({
      violations: results.violations.map(violation => ({
        id: violation.id,
        impact: violation.impact,
        nodes: violation.nodes.map(node => node.target.join(' ')),
      })),
      incomplete: results.incomplete.map(item => item.id),
    })));
}

// The theme belongs on <html>, which is the only element the library's `:root[data-theme="dark"]` can
// match — a shell that omits it audits light while claiming to audit the app.
function shell(markup, theme = 'light', extraCss = '') {
  return `<!doctype html><html lang="fi" data-theme="${theme}"><head><meta charset="utf-8">` +
    `<title>LUVN</title><style>${CSS}\n${extraCss}</style></head><body><div class="brief-app">` +
    `<a class="brief-skip" href="#brief-content">Siirry sisältöön</a>` +
    `<main id="brief-content">${markup}</main></div></body></html>`;
}

const vite = await createServer({
  root: fileURLToPath(new URL('..', import.meta.url)),
  server: { middlewareMode: true, hmr: false },
  appType: 'custom',
});
let ChatPanel;
let ChatRail;
let PictureBriefing;
let AssistantPane;
let SourceRecordDialog;
let LENS_SCOPE_NOTE;
let TechnicalView;
let App;
let RolePicker;
let CasePicker;
try {
  ({ PictureBriefing, AssistantPane, LENS_SCOPE_NOTE, SourceRecordDialog } = await vite.ssrLoadModule('/src/ChallengeDemo.jsx'));
  ({ TechnicalView } = await vite.ssrLoadModule('/src/TechnicalView.jsx'));
  ({ ChatPanel, ChatRail } = await vite.ssrLoadModule('/src/ChatPanel.jsx'));
  ({ default: App, RolePicker, CasePicker } = await vite.ssrLoadModule('/src/App.jsx'));
} finally {
  await vite.close();
}

const presentationPicture = {
  persona_id: 'persona-001', encounter_id: 'encounter-test', role: 'sairaanhoitaja', purpose: 'mental_health_assessment',
  identity_header: 'Aino Esimerkki · DEMO-007',
  situation: { situation_type: 'valmistelu', confidence: 'high' },
  risk: { risk_level: 'keskitaso', reason: 'Sääntöpohjainen peruste.' },
  actions: ['Varmista tiedot asiakkaalta.'],
  trace: [{ table: 'situation', rule_id: 'rule-1', output: { matched: true }, matched: [] }],
  pipeline_trace_id: 'opaque-trace-0123456789',
  pipeline_trace: [{ stage: 'foundry.generation', elapsed_ms: 100, model: 'gpt-6-luna',
    input_tokens: 12, output_tokens: 10, status: 'generated' }],
  grounded: true,
  grounding: { dropped_citations: ['source-c'], dropped_actions: [], rejected_sentences: [{ sentence_id: 's2', reason: 'unsupported' }] },
  selection: {
    baseline_record_ids: ['source-a', 'source-c', 'source-d'], additional_record_ids: ['source-b'], emphasized_record_ids: ['source-a'],
    rule_version: 'selection-v1', decision_status: 'complete',
    decision_notes: [{ record_id: 'source-b', status: 'relevant', model: 'decision-1.13.0', elapsed_ms: 21, input_tokens: 32,
      relevance_probabilities: { relevant: 0.82, irrelevant: 0.1, unclear: 0.08 }, relevance_confidence: 0.9,
      time_sensitivity: 'current', negation_probability: 0.03, subject_binding: 'person' }],
    decision_candidate_count: 2, decision_assessed_count: 2,
  },
  records: { priority: [{ record_id: 'source-a', record_type: 'arviointi', topic: 'PHQ-9', source: 'Lomake', recorded_at: '2026-09-28T00:00:00Z', state: 'approved', erikoisala: 'Psykiatria', suorituspaikka: 'Synteettinen yksikkö', payload: { instrumentti: 'PHQ-9' }, selection_reasons: ['assessment:latest-relevant'] }],
    accessible_other: [
      { record_id: 'source-b', record_type: 'vapaateksti', topic: 'mielenterveys', source: 'Note', recorded_at: '2026-09-29T00:00:00Z', state: 'draft', authored_by_role: 'sairaanhoitaja', payload: { otsikko: 'Vastaanotto', teksti: 'Asiakas kuvaa mielialan vaihtelua.' }, selection_reasons: ['keyword:theme'] },
      { record_id: 'source-c', record_type: 'lääkitys', topic: 'lääkitys', source: 'Lääkelista', recorded_at: '2026-09-27T00:00:00Z', payload: { lääke: 'Synteettinen lääke', annos: '1 tabletti', tila: 'aktiivinen', puuttuneet_annokset_30d: 0 }, selection_reasons: [] },
      // The definition grid is the vendored `KeyValueList` now, and an adopted component whose stylesheet
      // never reaches the audit is exactly how its grid went dead unseen. Rendering a `perustieto` here is
      // what puts `KeyValueList.module.css` into the audited sheet set; the required-module assertion below
      // fails without it, so this can neither rot nor be quietly dropped.
      { record_id: 'source-d', record_type: 'perustieto', topic: 'demografia', source: 'Väestörekisteri', recorded_at: '2026-09-01T00:00:00Z', payload: { sukupuoli: 'Nainen', syntymävuosi: 1979, äidinkieli: 'suomi', kotikunta: 'Helsinki', asumismuoto: 'Yksin asuva' }, selection_reasons: ['context:person-basics'] },
    ] },
  citations: [{ record_id: 'source-a' }],
  assessments: [{ instrumentti: 'PHQ-9', instrument_version: 'demo-v2', valmis: false, total: null, max_total: 27,
    osiot: [{ osio: 'q1', kysymys: 'Kohta 1', pisteet: null }], lahteet: ['source-a'], temporal_status: 'history' }],
  conflicts: [{ drug: 'Lääke', medication_record_id: 'source-a', note_record_id: 'source-b' }],
  proposals: [{ proposal_id: 'review-abc', rule_version: 'challenge-1.1-source-conflict-review-v1',
    text: 'Tarkista lääkitys- ja kirjauslähteiden välinen ristiriita.', status: 'source_conflict',
    evidence_record_ids: ['source-a', 'source-b'] }],
  narrative: 'Hyväksytty lause.',
  narration_generation_status: 'generated', narration_generation_deployment: 'deployment', narration_generation_model: 'model',
  narration_support_status: 'supported', narration_support_model: 'decision-1.13.0',
  narration_acceptance_status: 'accepted', narration_elapsed_ms: 150,
};

const comparison = {
  decisionOff: { ...presentationPicture, selection: { ...presentationPicture.selection, additional_record_ids: [] },
    narration_acceptance_status: 'rejected', narration_support_status: 'unknown', narrative: null },
  foundryOff: { ...presentationPicture, selectedSourceIds: undefined, narrative: null,
    narration_acceptance_status: 'rejected', narration_support_status: 'not_run' },
};

const baseProps = {
  picture: presentationPicture, personName: 'Aino Esimerkki', roleLabel: 'Sairaanhoitaja',
  purposeLabel: 'Mielialan arviointi', identityAvailable: true,
};

// The lens comparison reuses the comparison-card class, so audit it separately from the failure
// comparison. It receives only picker metadata; a sibling Picture is never loaded for this panel.
const lensProps = {
  ...baseProps,
  lensSiblings: [{ encounter_id: 'sibling-lens', picker_label: 'Mielialan arvio · eri ammattilainen (lääkäri)' }],
};

// The Decision-1 panel's assessed-notes list lives behind a disclosure so it reads as "what Decision-1 considered"
// rather than "what Decision-1 did". axe skips hidden content, so open it for the audit rather than losing
// coverage of the probabilities and outcome labels inside it.
const openDecisionAssessed = html =>
  html.replace(/<details class="brief-decision-assessed">/, '<details class="brief-decision-assessed" open>');

const briefingHtml = openDecisionAssessed(renderToStaticMarkup(React.createElement(PictureBriefing, {
  ...baseProps,
  proposalReviews: { 'review-abc': { status: 'saved', action: 'accept', recordedAt: '2026-10-11T09:00:00Z' } },
})));
assert(briefingHtml.includes('brief-record-state') && briefingHtml.includes('Hyväksytty'),
  'the audited briefing includes a lifecycle badge');
assert(briefingHtml.includes('brief-record-draft') && briefingHtml.includes('Luonnos'),
  'the audited briefing includes a draft record and its lifecycle badge');
assert(briefingHtml.includes('aria-label="Ammattilaisen tilanne ja tarkistettavat asiat"') && briefingHtml.includes('Tilanne ja hälytykset')
  && briefingHtml.includes('Lääkitykset') && briefingHtml.includes('Synteettinen lääke'),
  'the audited briefing includes the populated right rail');
// W2: the summary is the library AiSummaryBlock. This fixture withholds one generated sentence, so the audited
// render carries the block's withheld count in Finnish; every fixed word is Finnish and no library default
// (English) renders. The one accepted sentence carries one SourceReference (the fixture has no per-sentence
// rows, so the flat narrative is one sentence whose source is the citation).
assert(briefingHtml.includes('jätettiin näyttämättä: 1 lause (sitä ei voitu vahvistaa lähteisiin)'),
  'the audited render includes the withheld count in the block footer');
assert(briefingHtml.includes('Ehdotus') && briefingHtml.includes('Perustuu') && briefingHtml.includes('1 kirjaukseen')
  && briefingHtml.includes('Pidä tällä käynnillä') && briefingHtml.includes('Ei hyödyllinen')
  && briefingHtml.includes('Millainen mielialan tilanne on?'),
'the audited summary block renders its Finnish labels and the purpose question');
for (const english of ['Suggestion', 'Keep for this visit', 'Based on', 'Not useful', 'withheld:', 'Draft', 'Source record']) {
  assert(!briefingHtml.includes(english), `no library default renders in English: ${english}`);
}
assert((briefingHtml.match(/aria-haspopup="dialog"/g) ?? []).length === 1,
  'one SourceReference per accepted sentence');
// The source dialog, rendered open for the record the fixture marks as a draft: Finnish title, subtitle, the
// draft tag, the excerpt fields, the jump link and the close action. The static render cannot call showModal,
// so the `open` attribute is set here for the audit; the markup is otherwise the shipped markup.
const sourceDialogHtml = renderToStaticMarkup(React.createElement(SourceRecordDialog, {
  presentation: picturePresentation(presentationPicture, { personName: 'Aino Esimerkki' }),
  dialog: { openId: 'source-b', open: () => {}, close: () => {} },
})).replace('<dialog ', '<dialog open ');
assert(sourceDialogHtml.includes('Kirjaus · 29.9.2026') && sourceDialogHtml.includes('Lähdekirjaus · Aino Esimerkki')
  && sourceDialogHtml.includes('Luonnos') && sourceDialogHtml.includes('Otsikko') && sourceDialogHtml.includes('Vastaanotto')
  && sourceDialogHtml.includes('href="#source-source-b"') && sourceDialogHtml.includes('Siirry kirjaukseen')
  && sourceDialogHtml.includes('Sulje'),
'the audited source dialog carries its Finnish title, subtitle, draft tag, fields, jump link and close action');
assert(!sourceDialogHtml.includes('Draft') && !sourceDialogHtml.includes('Source record'),
  'the source dialog never renders the library\'s English words');
assert(!briefingHtml.includes('Hylätty lause: Lause s2 pidätettiin'),
  'the audited render no longer presents the withheld sentence as a clinical alert row');
assert(SKIN_CSS.includes('.brief-selected-record.brief-record-draft')
  && SKIN_CSS.includes('color: var(--color-text-muted); font-style: italic'),
  'the audited draft styling is grey and italic');
// D6: the scope caption is on every briefing, the same sentence, with no count after it. Asserted on the
// audited render so axe reads it, and asserted as a sentence that no digit follows.
assert(briefingHtml.includes(LENS_SCOPE_NOTE), 'the audited briefing carries the lens-scope caption');
assert(!new RegExp(`${LENS_SCOPE_NOTE.replace(/[.]/g, '\\.')}\\s*(<[^>]*>\\s*)*\\d`).test(briefingHtml),
  'no number follows the scope caption: it never counts lenses or records');
// B2: the production material is the technical page, a destination of its own (`TechnicalView`), so the
// comparison and the lens panel are audited there. Nothing is opened for the audit: it is a page, not a
// disclosure, and axe reads all of it.
const technicalHtml = openDecisionAssessed(renderToStaticMarkup(React.createElement(TechnicalView, baseProps)));
assert(technicalHtml.includes('id="section-coverage"') && technicalHtml.includes('id="section-impact"')
  && technicalHtml.includes('id="presenter-tools"'),
'the technical page carries the coverage and impact cards and the presenter surface under their ids');
assert(!technicalHtml.includes('<h1'), 'the technical page never renders an h1: the shell Header owns it');
assert(!briefingHtml.includes('run-specifications') && !briefingHtml.includes('brief-technical'),
  'the briefing no longer carries the run-specifications disclosure');
const comparisonHtml = openDecisionAssessed(renderToStaticMarkup(React.createElement(TechnicalView, {
  ...baseProps,
  failureComparison: comparison,
})));
assert(comparisonHtml.includes('brief-comparison-grid'), 'comparison view renders three cards');
assert.equal([...comparisonHtml.matchAll(/brief-comparison-card/g)].length, 3, 'exactly three comparison cards');

const lensHtml = openDecisionAssessed(renderToStaticMarkup(React.createElement(TechnicalView, lensProps)));
assert(lensHtml.includes('Näkökulmavertailu'), 'the lens comparison panel renders for the audit');
assert.equal([...lensHtml.matchAll(/brief-comparison-card/g)].length, 1,
  'the lens panel adds exactly one card per sibling lens');

// axe reports `landmark-one-main` / `page-has-heading-one` as incomplete under jsdom (no layout);
// pin the structure those rules look for explicitly so the gate does not silently lose it.
let statusIconBlocksChecked = 0;
let deadSelectorsChecked = 0;
let deadSelectorsOutOfScope = 0;
const briefingDocument = new JSDOM(shell(briefingHtml)).window.document;
// The shell too. The component render cannot satisfy `.brief-sidebar`, `.brief-case-picker` or `.brief-main`,
// so every one of those selectors used to be skipped as out of scope and never checked against any DOM. The
// shell is the library's AppShell, Header, Sidebar, BottomNav, NavItem, Search, IconButton and Avatar now;
// the app classes that remain are the sidebar wrapper, the phone tools row and the content wrapper.
const appShellHtml = renderToStaticMarkup(React.createElement(App));
const appShell = `<!doctype html><html lang="fi" data-theme="light"><head><meta charset="utf-8">` +
  `<title>LUVN</title></head>` +
  `<body><div id="root">${appShellHtml}</div></body></html>`;
const appDocument = new JSDOM(appShell).window.document;
assert(appDocument.querySelector('.brief-app'), 'the shell render produced the app root');
assert.equal(appDocument.querySelectorAll('main').length, 1, 'the library AppShell renders exactly one main');
assert.equal(appDocument.querySelectorAll('h1').length, 1, 'the shell carries exactly one h1');
assert(appDocument.querySelector('nav[aria-label="Pikavalikko"]'),
  'the phone bottom bar is a navigation landmark with its own name');
assert(appDocument.querySelector('header .t-page-title'), 'the shell page title is the library Header h1');
// The rail column is a named region and the library Sidebar's own `<nav>` sits inside it: the logo row and
// the Rooli selector are outside any landmark otherwise, and the shell audit reports that as a `region`
// violation rather than passing quietly.
assert(appDocument.querySelector('.brief-sidebar[role="region"][aria-label="Sivupalkki"]'),
  'the rail is a named region that contains the whole column');
assert(appDocument.querySelector('.brief-sidebar nav[aria-label="Päänavigaatio"]'),
  'the rail carries the library Sidebar\'s own navigation landmark');
// Four navigation landmarks in the app: the rail's primary and utility navs, the phone bottom bar, and the
// library Breadcrumb in the header. The utility nav in the rail is the one added by work order E.
assert.equal(appDocument.querySelectorAll('.brief-app nav').length, 4,
  'the rail (primary and utility), the phone bottom bar and the breadcrumb are all rendered, each named for its surface');
assert.equal(new Set([...appDocument.querySelectorAll('.brief-app nav')].map(nav => nav.getAttribute('aria-label'))).size,
  appDocument.querySelectorAll('.brief-app nav').length,
  'the navigation landmarks are uniquely named, so `landmark-unique` holds');

// The assistant side pane (night plan N1). The static shell renders it closed, carrying the `hidden` attribute,
// so it is out of the accessibility tree rather than off-screen; it sits beside the shell, not inside `main`,
// so it is a top-level complementary landmark. The open state cannot be reached from a static render of App
// (it is the address's `assistant=1`, read from `window`), so the open pane is rendered on its own — with the
// chat panel for an encounter inside it — and spliced into the shell markup in the closed pane's place. That
// document is audited below in both themes. It is NOT added to the dead-selector set: that check unions the
// tags of every document, and the composer's `form` and `label` would make `.brief-proposal form label` look
// satisfiable with no document that matches it. The pane's own `.brief-assistant-*` rules are checked against
// the shell render, where the closed pane carries every one of those classes.
const closedPane = appDocument.querySelector('aside.brief-assistant-pane');
assert(closedPane?.hasAttribute('hidden'), 'the static shell renders the assistant pane closed, with `hidden`');
assert.equal(closedPane.getAttribute('role'), 'complementary');
assert(!appDocument.querySelector('main aside.brief-assistant-pane'), 'the pane is not inside the main landmark');
assert.equal(appDocument.querySelectorAll('header button[aria-controls="brief-assistant"]').length, 1,
  'the header carries the assistant toggle');
const openPaneHtml = renderToStaticMarkup(React.createElement(AssistantPane, {
  open: true, onClose: () => {}, roleLabel: 'Sairaanhoitaja', personLabel: 'SYN-ASI-1000012236',
  chat: React.createElement(ChatPanel, { encounterId: 'bound-encounter-002-laakitys-sairaanhoitaja', identity: false }),
}));
// (The IconButton's tooltip span is `hidden` of its own accord; the check is the aside's own attribute.)
assert(!/<aside [^>]*hidden=""/.test(openPaneHtml) && openPaneHtml.includes('Sairaanhoitaja · SYN-ASI-1000012236')
  && openPaneHtml.includes('role="log"'), 'the open pane renders the scope chip and the chat panel (non-vacuity)');
const closedPaneMarkup = /<aside [^>]*hidden=""[^>]*>[\s\S]*?<\/aside>/;
assert(closedPaneMarkup.test(appShellHtml), 'the closed pane is found in the shell markup to be replaced');
const appShellOpenHtml = appShellHtml.replace(closedPaneMarkup, openPaneHtml)
  .replace('class="brief-app"', 'class="brief-app brief-assistant-open"');
assert(appShellOpenHtml.includes('brief-assistant-open') && appShellOpenHtml.includes('SYN-ASI-1000012236'),
  'the shell markup now carries the open pane');
const appShellOpen = theme => `<!doctype html><html lang="fi" data-theme="${theme}"><head><meta charset="utf-8">` +
  `<title>LUVN</title><style>${CSS}</style></head><body><div id="root">${appShellOpenHtml}</div></body></html>`;
const assistantDocument = new JSDOM(appShellOpen('light')).window.document;
assert(!assistantDocument.querySelector('aside.brief-assistant-pane').hasAttribute('hidden'), 'the audited pane is open');
assert.equal(assistantDocument.querySelectorAll('main').length, 1, 'one main beside the open pane');
assert.equal(assistantDocument.querySelectorAll('[role="complementary"]').length, 1, 'one complementary landmark');

// The worklist owns two states the plain shell render cannot reach, because the shell renders before any
// fetch resolves: the populated Rooli selector, and a role-scoped worklist grouped by person. Rendering
// them here is what makes `.brief-role-picker` auditable — the alternative, an allowlist entry, would report the control as covered while nothing
// ever rendered it. The items are already-scoped worklist rows (not catalogue rows), so this render
// cannot accidentally claim the component is handed a wider list.
const ROSTER = [{ role: 'sairaanhoitaja', label: 'Sairaanhoitaja' }, { role: 'laakari', label: 'Lääkäri' }];
const WORKLIST_ITEMS = [
  { encounterId: 'nurse-1', label: 'Lääkityksen seuranta · erillinen tapaus', person: 'Aino Virtanen',
    roleLabel: 'Sairaanhoitaja', purposeLabel: 'Lääkityksen seuranta' },
  { encounterId: 'nurse-2', label: 'Toimintakyvyn arvio · sama henkilö, eri tilanne', person: 'Elias Korhonen',
    roleLabel: 'Sairaanhoitaja', purposeLabel: 'Palvelutarpeen arviointi' },
];
const worklistHtml = renderToStaticMarkup(React.createElement('div', null,
  React.createElement(RolePicker, { roster: ROSTER, role: 'sairaanhoitaja', onSelect: () => {} }),
  React.createElement(CasePicker, { items: WORKLIST_ITEMS, selectedId: 'nurse-1', onSelect: () => {} }),
));
// The authored slot table (Aikataulu) was deleted: no worklist row may carry a clock mark again.
assert(!worklistHtml.includes('aikamerkintä'), 'the worklist renders no authored slot mark');
assert.equal([...worklistHtml.matchAll(/brief-case-group/g)].length, 2,
  'the audited worklist groups its own rows by person');
const worklistDocument = new JSDOM(shell(worklistHtml)).window.document;
assert(worklistDocument.querySelector('.brief-role-picker select'),
  'the audited worklist render includes the populated Rooli selector');

const technicalDocument = new JSDOM(shell(technicalHtml)).window.document;
// The briefing's static render carries its own h1 (`hideHeading` is the shell's call); the technical page never
// does, because the shell Header is the only h1 on that destination.
assert.equal(technicalDocument.querySelectorAll('h1').length, 0, 'the technical page renders no h1 of its own');
const DOMS = [briefingDocument, technicalDocument, appDocument, worklistDocument];
// The worklist's metric row is a DERIVED-NUMBER surface, which `CONTEXT.md:49` constrains: every number
// must be recomputable from rows rendered in the same panel, with no denominator and no percentage. Two
// of these assertions are about that invariant, one is about a prohibition no gate can see — `Metric.trend`
// renders an "above range"/"below range" arrow, a computed range claim — and one is about the other half of
// the invariant: nothing to count means the row is absent, not a row of zeroes.
{
  const metricRow = worklistDocument.querySelector('.brief-worklist-metrics');
  assert(metricRow, 'the worklist renders its metric row');
  const cells = [...metricRow.children];
  assert.equal(cells.length, 3, 'one cell per count with something to count');
  assert.equal(cells[0].querySelector('.t-metric').textContent, String(WORKLIST_ITEMS.length),
    'the encounter count equals the rows this panel renders');
  assert.equal(cells[1].querySelector('.t-metric').textContent,
    String(new Set(WORKLIST_ITEMS.map(item => item.person)).size),
    'the person count equals the groups this panel renders');
  const rowText = metricRow.textContent;
  assert(!/above range|below range/.test(rowText), 'no metric carries a trend text alternative');
  assert.equal(metricRow.querySelectorAll('svg').length, 0, 'the metric row renders no icon, trend or otherwise');
  assert(!/%/.test(rowText), 'the metric row carries no percentage');
  const numbers = [...metricRow.querySelectorAll('.t-metric')].map(node => Number(node.textContent));
  assert(numbers.every(value => value <= WORKLIST_ITEMS.length),
    `no metric exceeds the rendered rows (${numbers.join(', ')} vs ${WORKLIST_ITEMS.length})`);
  const probe = new JSDOM('<div class="brief-worklist-metrics"><div class="t-metric">4</div></div>')
    .window.document.querySelector('.brief-worklist-metrics');
  assert.equal(probe.querySelectorAll('.t-metric').length, 1,
    'the metric-reading assertions are non-vacuous: a cell is found when one is rendered');
}
const emptyWorklistHtml = renderToStaticMarkup(React.createElement(CasePicker, {
  items: [], selectedId: '', onSelect: () => {},
}));
assert(!emptyWorklistHtml.includes('brief-worklist-metrics'),
  'a worklist with no rows renders no metric row rather than zero-valued tiles');
assert(emptyWorklistHtml.includes('brief-empty-panel'), 'the empty worklist still explains itself');

// The worklist is a SERVER PAGE now: its counts come from `aggregate.matching` and its paging controls
// render only when the catalogue holds more rows than the page. This render is the only one that
// exercises that state — and the paging buttons are the only place `Button` appears on this surface, so
// auditing it is what keeps the adopted component's stylesheet in the document rather than assumed.
const PAGED_ITEMS = [
  { encounterId: 'paged-1', label: 'Lääkityksen seuranta · erillinen tapaus', person: 'Aino Virtanen',
    roleLabel: 'Sairaanhoitaja', purposeLabel: 'Lääkityksen seuranta', time: '', synthetic: false },
];
const pagedWorklistHtml = renderToStaticMarkup(React.createElement(CasePicker, {
  items: PAGED_ITEMS, summary: { encounters: 41, people: 30, purposes: 4 }, total: 41, offset: 25,
  selectedId: '', onSelect: () => {}, onPage: () => {},
}));
const pagedWorklistDocument = new JSDOM(shell(pagedWorklistHtml)).window.document;
DOMS.push(pagedWorklistDocument);
{
  const pagedMetrics = pagedWorklistDocument.querySelector('.brief-worklist-metrics');
  assert(pagedMetrics, 'the paged worklist renders its server-provided metric row');
  assert.equal(pagedMetrics.children[0].querySelector('.t-metric').textContent, '41',
    'the encounter count is the server total for the whole role, not the one row this page renders');
  assert.equal(pagedMetrics.children[1].querySelector('.t-metric').textContent, '30',
    'the person count is the server count for the whole role');
  assert(pagedWorklistDocument.querySelector('.brief-button-row'),
    'a catalogue larger than the page renders the paging controls, so the selector is not dead');
  const paging = pagedWorklistDocument.querySelector('.brief-button-row');
  assert.equal(paging.querySelectorAll('button').length, 2, 'paging is two steps, not a page-number list');
  assert(paging.textContent.includes('26–26 / 41'),
    'the range names the page within the whole role, which is what the server total is for');
}


assert.equal(briefingDocument.documentElement.lang, 'fi', 'Finnish content declares lang="fi"');
assert.equal(briefingDocument.querySelectorAll('main').length, 1, 'exactly one main landmark');
assert.equal(briefingDocument.querySelectorAll('h1').length, 1, 'exactly one h1');

// ---------- colour as meaning (axe cannot judge this) ----------

// The styleguide requires status to be icon + word, always. A tinted box with a word and no icon is the
// failure mode that looks fine in every automated check, so assert it structurally.
// Both audited pages: the attention rows are the briefing's, the Decision-1 rows are the technical page's.
const statusBlocks = [briefingDocument, technicalDocument].flatMap(doc => [...doc.querySelectorAll('[class*="brief-critical"],[class*="brief-warning"],[class*="brief-success"],[class*="brief-info"]')]);
assert(statusBlocks.length > 0, 'the audited pages contain status blocks to check');
for (const block of statusBlocks) {
  assert(block.querySelector('.icon'), `a status block carries an icon: ${block.className}`);
  assert(block.textContent.trim().length > 0, 'a status block carries a word');
  // `.t-caption` and `.t-overline` are the only roles that bake a colour (text-muted). Inside a status block
  // that colour out-specifies the status foreground, so the block keeps its icon and its word while the text
  // silently loses its status colour — invisible to axe, because the pair still passes contrast.
  assert(!block.querySelector('.t-caption, .t-overline'),
    `no colour-baking role inside a status block (${block.className}): it would override the status foreground`);
}
{
  const probe = new JSDOM(`<div class="brief-warning">x<span class="t-caption">m</span></div>`).window.document;
  assert(probe.querySelector('.brief-warning').querySelector('.t-caption'),
    'the status-subtree rule is non-vacuous: a caption inside a status block is found');
}
statusIconBlocksChecked = statusBlocks.length;

// The absence state's neutrality is owned by challenge-demo.test.mjs, which renders one fixture per reason
// and asserts a single identical section. The loop that used to sit here tested the same `briefingHtml` six
// times against a fixture with no `form_catalogue` at all, so it could not fail for any reason — a check
// that reads as coverage while providing none. Removed rather than left as reassurance, including the
// `|| true` line that followed it — that one could not fail either, so it was the same non-check twice.

// ---------- the dead-selector check ----------
//
// Every selector in the app sheet that this render can satisfy must actually match an element. A dropped or
// mis-scoped rule otherwise leaves markup unstyled while the CSS-regex gate and axe both report green —
// jsdom cannot paint, and the scope assertion is derived from the CSS itself. Selectors whose classes are
// only rendered by the shell (navigation, the case picker) are counted and reported rather than asserted,
// because this render is the briefing component, not the whole app.
const appSheet = fs.readFileSync(fileURLToPath(new URL('./styles.css', import.meta.url)), 'utf8');
const presentClasses = new Set(DOMS.flatMap(doc => [...doc.querySelectorAll('*')]
  .flatMap(node => [...node.classList])));
const selectors = [];
postcss.parse(appSheet).walkRules(rule => {
  for (const part of rule.selector.split(',').map(p => p.trim())) {
    if (!part || part.startsWith('@')) continue;
    selectors.push(part);
  }
});
// A selector is checked only if this render can actually satisfy it: every class and every element it names
// must appear somewhere here, and a state-dependent selector (:hover, [aria-pressed], …) is out of scope
// because jsdom has no interaction. Anything else is a claim that the rule matches nothing.
const presentTags = new Set(DOMS.flatMap(doc => [...doc.querySelectorAll('*')].map(node => node.tagName.toLowerCase())));
// Selectors this fixture cannot exercise, each with its reason. The audited Picture carries no `perustieto`
// record, so the definition grid renders its empty panel rather than a <dl>; and it has no out-of-generation
// omission, so the Decision-1 row carries no icon. Both shapes are exercised by dedicated fixtures in
// challenge-demo.test.mjs, so this is a gap in this render, not in the coverage.
// Selectors these fixtures cannot satisfy — and each entry must *prove* it. The earlier allowlist was a
// bare Set with a comment claiming the shapes were "exercised by dedicated fixtures in challenge-demo.test.mjs";
// five of its six entries named fixtures that do not exist, which made it a rubber stamp rather than a gap.
// A premise that is asserted cannot rot: if a fixture ever renders one of these, the premise fails and the
// entry has to go — which is the point.
const UNSATISFIABLE = [
  // The four `.brief-app .brief-definition dl`/`dt`/`dd` entries that used to sit here are gone with the
  // rules themselves: the definition grid is the vendored `KeyValueList` now, its `<dl>` is rendered by this
  // fixture, and the app declares no selector for it. An entry whose selector no longer exists is a stale
  // excuse rather than a documented gap, so the loop below requires every entry to name a declared selector.
  ['.brief-app .brief-decision-outcome .icon', () => {
    // Decision-1 rows render, but none carries an out-of-generation omission, which is the only thing that adds
    // the warning icon. Both halves are asserted, so "the row is missing" cannot pass as the reason.
    return technicalDocument.querySelectorAll('.brief-decision-outcome').length > 0
      && technicalDocument.querySelector('.brief-decision-outcome .icon') === null;
  }],
  ['.brief-app .brief-source-metadata .brief-entry-metadata', () => {
    // This was an excuse and it has been retired. The rule could not match because the assessment rows were
    // filtered to the *selected* set, so a row whose source had provenance never rendered — the assertion
    // recorded that as "this fixture has no per-entry provenance", which was the wrong reason. The filter is
    // the visible set now, the rows render, and the rule matches. Kept as a POSITIVE assertion so the
    // selector cannot silently become unmatchable again.
    return briefingDocument.querySelector('.brief-source-metadata') !== null
      && briefingDocument.querySelector('.brief-source-metadata .brief-entry-metadata') !== null;
  }],
];
for (const [selector, premise] of UNSATISFIABLE) {
  assert(premise(), `the unsatisfiable reason for ${selector} still holds — a stale excuse must fail`);
  // The other direction: an excuse for a rule that no longer exists is not a gap, it is a rubber stamp.
  // Without this, deleting a rule left its allowlist entry behind and the entry kept "covering" nothing.
  assert(selectors.includes(selector),
    `${selector} is excused as unsatisfiable but the app stylesheet no longer declares it`);
}
assert(UNSATISFIABLE.length >= 2, 'the unsatisfiable list is still exercised');
const UNSATISFIABLE_SELECTORS = new Set(UNSATISFIABLE.map(([selector]) => selector));
const DOCUMENT_LEVEL = new Set();
let matched = 0, outOfScope = 0; const unmatched = [];
for (const selector of selectors) {
  if (/[:[]/.test(selector)) { outOfScope += 1; continue; }
  const classes = [...selector.matchAll(/\.([\w-]+)/g)].map(m => m[1]);
  const tags = [...selector.matchAll(/(?:^|[\s>+~])([a-z][a-z0-9]*)/g)].map(m => m[1]);
  if (!classes.every(cls => presentClasses.has(cls)) || !tags.every(tag => presentTags.has(tag))) {
    outOfScope += 1; continue;
  }
  if (UNSATISFIABLE_SELECTORS.has(selector) || DOCUMENT_LEVEL.has(selector)) { outOfScope += 1; continue; }
  if (DOMS.some(doc => doc.querySelector(selector))) matched += 1;
  else unmatched.push(selector);
}
assert(matched > 100, `the dead-selector check reads real selectors (matched ${matched})`);
assert.deepEqual(unmatched, [], 'every selector this render can satisfy matches an element');
deadSelectorsChecked = matched;
deadSelectorsOutOfScope = outOfScope;

// ---------- the chat rail in its populated states ----------
//
// The rail is stateful: its prompts, guideline links, turn and review decisions only exist after effects
// and clicks, so `renderToStaticMarkup` of the container rendered a heading, a caption and an empty form —
// the only chat markup axe ever saw. `ChatRail` is the same surface without the fetching, so these renders
// cover what a professional actually gets.
const chatPrompt = { text: 'Mitkä lääkitykset ovat ristiriidassa keskenään?', why: 'ristiriita kirjattu',
  evidence_record_ids: ['source-a', 'source-b'] };
const chatTurnFixture = {
  turn_id: 'turn-1', digest_id: 'digest-1', seq: 1, asked_at: '2026-10-11T00:00:00Z', outcome: 'answer',
  generator: 'gpt-6-luna',
  suggestions: [
    { suggestion_id: 's0', turn_id: 'turn-1', ordinal: 0, statement_class: 'source_fact',
      text: 'Lähde kertoo lääkityksen olevan aktiivinen.', label: 'Lähde', source_record_ids: ['source-a'] },
    { suggestion_id: 's1', turn_id: 'turn-1', ordinal: 1, statement_class: 'model_summary',
      text: 'Malli muotoili yhteenvedon lähteistä.', label: 'Tekoälyn muotoilema', source_record_ids: ['source-b'] },
  ],
};
const chatLinksFixture = [
  { code: 'kh-1', title: 'Masennus', url: 'https://www.kaypahoito.fi/kh-1', published: '2024' },
  { code: 'kh-2', title: 'Ahdistuneisuushäiriöt', url: 'https://www.kaypahoito.fi/kh-2', published: '2023' },
];
// A called model whose text was not published: the deterministic statements render, no model_summary does,
// and `generator_state` is what makes the rail disclose the model involvement.
const chatWithheldTurn = {
  ...chatTurnFixture, generator: null, generator_state: 'withheld',
  suggestions: chatTurnFixture.suggestions.filter(item => item.statement_class !== 'model_summary'),
};
// A headed finding: a rule output (the plain class tag) and a headed source fact. A proposal is a step, rendered
// in the steps block with no heading row, so it cannot stand in for a headed finding here. Each
// heading is a prefix of its sentence, so the three-part row (tag, heading, remainder) is what axe reads here.
const chatHeadedRule = { suggestion_id: 's2', turn_id: 'turn-1', ordinal: 2, statement_class: 'rule_output',
  label: 'Sääntöpohjainen arvio', heading: 'Vaikea masennusoireilu',
  text: 'Vaikea masennusoireilu (PHQ-9): tarkista arvio ammattilaisena.', source_record_ids: ['source-a'] };
const chatHeadedFact = { suggestion_id: 's3', turn_id: 'turn-1', ordinal: 3, statement_class: 'source_fact',
  label: 'Lähde: lääkitys', heading: 'Levorotyroksiini',
  text: 'Levorotyroksiini 25 mikrog kerran vuorokaudessa on merkitty aktiiviseksi lääkitykseksi.', source_record_ids: ['source-a'] };
// The steps block is audited on its own state, because it is the one surface where an imperative could read as
// authority if the table's status line were ever dropped, and because a step renders in a block rather than as
// a headed row, so the headed-finding state above cannot stand in for it. The status text is the backend's own
// `ACTIONS_BASIS_NOTE`; the assertion below fails if it stops reaching the block.
const chatStepRow = { suggestion_id: 's4', turn_id: 'turn-1', ordinal: 4, statement_class: 'rule_output',
  label: 'Sääntöpohjainen arvio (ehdotus)', text: 'Järjestä psykiatrin arvio', source_record_ids: [],
  basis_note: 'Tilanne: mielenterveys. Havainnollistava sääntö, ei kliinisesti kalibroitu eikä hyväksytty käyttöön.' };
// An advice row with its citation (`basis_note`) and refs. The refs are inert text, so the audit sees no link for them.
const chatAdviceRow = { suggestion_id: 's5', turn_id: 'turn-1', ordinal: 5, statement_class: 'knowledge_advice',
  label: 'Tietopohjainen ehdotus', text: 'Mittaa tarvittaessa ohjeen mukaan.', source_record_ids: [],
  refs: ['form:f#q'], basis_note: 'Lähde: Lääkitysarvio · v2' };
const chatStates = {
  'knowledge advice': { turn: { ...chatTurnFixture, suggestions: [chatAdviceRow] } },
  'headed finding': { turn: { ...chatTurnFixture, suggestions: [chatHeadedRule, chatHeadedFact] } },
  'steps block': { turn: { ...chatTurnFixture, suggestions: [chatStepRow] } },
  prompts: { prompts: [chatPrompt], recordTargets: new Set(['source-a']) },
  // The `Ohjeet` panel only renders while its tab is open, so the links state opens it. Everything else
  // audits the question tab, which is what the panel shows on load.
  'guideline links': { links: chatLinksFixture, initialTab: 'ohjeet' },
  'suggestion list': { turn: chatTurnFixture },
  'recorded decision': { turn: chatTurnFixture, notice: 'Olet yhteydessä tekoälyyn. Vastaus on tarkistettava.',
    decisions: { 'turn-1:s0': { status: 'saved', label: 'Tallennettu palveluun', action: 'accept',
      recordedAt: '2026-10-11T09:00:00Z' } } },
  'already decided': { turn: chatTurnFixture,
    decisions: { 'turn-1:s0': { status: 'decided', action: 'accept' } } },
  'ambiguous write': { turn: chatTurnFixture,
    decisions: { 'turn-1:s0': { status: 'ambiguous', action: 'accept', error: 'yhteys katkesi' } } },
  'definite failure': { turn: chatTurnFixture, error: 'Kysymystä ei voitu käsitellä: 503',
    decisions: { 'turn-1:s0': { status: 'failed', action: 'accept', error: 'palvelu hylkäsi tallennuksen' } } },
  'withheld model arm': { turn: chatWithheldTurn,
    notice: 'Olet yhteydessä tekoälyyn. Vastaus on tarkistettava.' },
};
const chatStateHtml = Object.fromEntries(Object.entries(chatStates)
  .map(([label, props]) => [label, renderToStaticMarkup(React.createElement(ChatRail, props))]));
// Non-vacuity: each audited state must actually carry the thing it is named for, or axe would be auditing
// the empty rail once per state and reporting 'chat rail is clean' about a surface nobody rendered.
assert(chatStateHtml.prompts.includes(chatPrompt.why)
  && chatStateHtml.prompts.includes('href="#source-source-a"'),
  'the audited prompts state carries a visible justification and the evidence link whose anchor exists');
// The headed state must actually render its heading elements and the API-supplied proposal tag, or it would be
// an unaudited state under a name that claims otherwise.
assert(chatStateHtml['headed finding'].includes('data-heading="Vaikea masennusoireilu"')
  && chatStateHtml['headed finding'].includes('data-heading="Levorotyroksiini"'),
  'the audited headed state renders its [data-heading] elements');
assert(chatStateHtml['headed finding'].includes('Sääntöpohjainen arvio'),
  'the audited headed state renders the rule finding under its class tag');
// The steps state must actually render the block and the table's status, or the audit above would pass over a
// surface that had silently lost the qualification the two oracles made a condition of shipping at all.
assert(chatStateHtml['steps block'].includes('data-steps')
  && chatStateHtml['steps block'].includes('Havainnollistava sääntö, ei kliinisesti kalibroitu eikä hyväksytty käyttöön.'),
  'the audited steps state renders the step block together with the engine table’s own status line');
assert(chatStateHtml['guideline links'].includes('www.kaypahoito.fi/kh-1'),
  'the audited links state carries a safe guideline link');
assert(chatStateHtml['suggestion list'].includes('Lähde kertoo lääkityksen olevan aktiivinen.'),
  'the audited suggestion state carries the statements');
assert(chatStateHtml['recorded decision'].includes('Tallennettu palveluun'),
  'the audited decision state carries the recorded marker');
assert(chatStateHtml['already decided'].includes('Olet jo päättänyt tämän ehdotuksen'),
  'the audited duplicate-decision state carries the "you already decided this" wording');
assert(chatStateHtml['ambiguous write'].includes('Yritä samaa tallennusta uudelleen'),
  'the audited ambiguous state carries the same-save retry');
assert(chatStateHtml['definite failure'].includes('Palvelu hylkäsi tallennuksen'),
  'the audited failure state carries the visible rejection');
// The advice state must render its citation and refs before the audit can mean anything about them.
assert(chatStateHtml['knowledge advice'].includes('Lähde: Lääkitysarvio · v2')
  && chatStateHtml['knowledge advice'].includes('Viitteet: form:f#q'),
  'the audited advice state carries its citation line and its refs');
assert(chatStateHtml['withheld model arm'].includes(
  'Tekoälyä käytettiin, mutta sen tuottamaa tekstiä ei julkaistu tässä vastauksessa.'),
  'the audited withheld state carries the disclosure instead of the verified-prose caption');
assert(!chatStateHtml['withheld model arm'].includes('Vastaus on koottu lähteistä ja tarkistettu lauseittain.'),
  'and it does not claim generated prose was published');

// Every component sheet the shipped markup actually used, concatenated into ONE argument that every audit
// below receives. Previously only the briefing got the Button's sheet, so the comparison, lens and
// worklist renders audited components with no component CSS at all.
const RENDERED_FOR_AUDIT = briefingHtml + comparisonHtml + lensHtml + worklistHtml + appShellHtml + sourceDialogHtml
  + Object.values(chatStateHtml).join('');
const COMPONENT_CSS = componentModuleCss(RENDERED_FOR_AUDIT);
assert(COMPONENT_CSS, 'the component sheets are recovered from the rendered markup, so their styles are audited');
assert(COMPONENT_CSS.includes('min-height: var(--control-h)'),
  'the rewritten component sheets are real CSS, not an empty substitution');
// The render must have exercised more than one component, or "every adopted component is audited" would
// be satisfied by auditing one of them.
const usedModules = usedComponentModules(briefingHtml + comparisonHtml + lensHtml + worklistHtml + appShellHtml
  + sourceDialogHtml + Object.values(chatStateHtml).join(''));
assert(usedModules.size >= 8, `the renders used ${usedModules.size} component sheets (expected the adopted set)`);
for (const required of ['Button/Button.module.css', 'Tag/Tag.module.css',
  'Metric/Metric.module.css', 'NavItem/NavItem.module.css', 'Sidebar/Sidebar.module.css',
  'Header/Header.module.css', 'Search/Search.module.css', 'KeyValueList/KeyValueList.module.css',
  // W1: the layout components, so their sheets (the column rule, the sticky strip, the cell rules) are audited.
  'MainSide/MainSide.module.css', 'IdentityStrip/IdentityStrip.module.css', 'NowStrip/NowStrip.module.css', 'NowCell/NowCell.module.css',
  // The chat rail is the only surface that adopts the Link; without the populated renders its sheet
  // would be audited nowhere, which is the failure this file's header describes.
  'Link/Link.module.css',
  // W2: the summary block, its inline source references and the app-side source dialog on the library Dialog.
  'AiSummaryBlock/AiSummaryBlock.module.css', 'SourceReference/SourceReference.module.css', 'Dialog/Dialog.module.css']) {
  assert(usedModules.has(required), `the rendered markup used ${required}, so its CSS is in the audit`);
}
// Per-module non-vacuity, one probe per used module: each must contribute CSS carrying its OWN hash, in
// the `_<block>_<hash>` form Vite emits. A module keyed to the wrong hash would produce none.
for (const name of usedModules) {
  const module = COMPONENT_MODULES.get(name);
  assert(new RegExp(`_[A-Za-z][A-Za-z0-9]*_${module.hash}(?![A-Za-z0-9])`).test(COMPONENT_CSS),
    `${name} contributed hashed CSS to the audit (hash ${module.hash})`);
}
// Non-vacuity of that probe: a hash the render did not use must NOT appear as a rewritten block.
{
  const unused = [...COMPONENT_MODULES].find(([name]) => !usedModules.has(name));
  assert(unused, 'some vendored module is unused, so the negative probe has a subject');
  assert(!new RegExp(`_[A-Za-z][A-Za-z0-9]*_${unused[1].hash}(?![A-Za-z0-9])`).test(COMPONENT_CSS),
    `${unused[0]} is NOT in the audit — its hash (${unused[1].hash}) appears nowhere`);
}

// Non-vacuity of the whole exercise, in the only terms that are true here. `color-contrast` is disabled
// (jsdom cannot paint), so a colour poison proves nothing, and `region` fires only for content OUTSIDE a
// landmark — which `shell()` cannot produce, because it wraps the markup in `<main>`. Two facts are
// checkable, and together they are the claim:
//   1. the component CSS is really installed in the audited document, not merely concatenated;
//   2. that document can produce a violation for a rule a stylesheet is responsible for.
{
  const probeShell = shell(briefingHtml, 'light', COMPONENT_CSS);
  assert(probeShell.includes(`_${COMPONENT_MODULES.get('Tag/Tag.module.css').hash}`),
    'the component class hashes are present in the audited document, not just in a variable');
  // (2) A stylesheet-owned failure: content outside every landmark is the `region` rule, and the audit
  // reports it when the same document carries it.
  const loose = probeShell.replace('<main id="brief-content">', '<p class="brief-probe">loose</p><main id="brief-content">');
  const looseResults = await audit(loose);
  assert(looseResults.violations.some(v => v.id === 'region'),
    'the audit reports a stylesheet-relevant violation on this document, so a green audit means something');
  const cleanResults = await audit(probeShell);
  assert(!cleanResults.violations.some(v => v.id === 'region'),
    'and the unmodified document is clean, so the probe is about the injected content');
}

// The build stamp is read once, at module load, from VITE_BUILD_REVISION, so the stamped render loads its own copy
// of the technical page with the variable set (as the challenge-demo test does). The stamp is on the technical
// page, which is a page and not a disclosure, so nothing has to be opened for axe to read it.
async function renderStampedTechnical(revision) {
  const saved = process.env.VITE_BUILD_REVISION;
  process.env.VITE_BUILD_REVISION = revision;
  const stampServer = await createServer({
    root: fileURLToPath(new URL('..', import.meta.url)),
    server: { middlewareMode: true, hmr: false },
    appType: 'custom',
  });
  try {
    const { TechnicalView: Stamped } = await stampServer.ssrLoadModule('/src/TechnicalView.jsx');
    return renderToStaticMarkup(React.createElement(Stamped, baseProps));
  } finally {
    await stampServer.close();
    if (saved === undefined) delete process.env.VITE_BUILD_REVISION; else process.env.VITE_BUILD_REVISION = saved;
  }
}
const stampedHtml = await renderStampedTechnical('rev-a11y0001');
assert(stampedHtml.includes('<p class="t-caption" data-build="rev-a11y0001"'),
  'the audited stamped render carries the [data-build] element');
const stampedResults = await audit(shell(stampedHtml, 'light', COMPONENT_CSS));
const briefingResults = await audit(shell(briefingHtml, 'light', COMPONENT_CSS));
const sourceDialogResults = await audit(shell(sourceDialogHtml, 'light', COMPONENT_CSS));
const technicalResults = await audit(shell(technicalHtml, 'light', COMPONENT_CSS));
const comparisonResults = await audit(shell(comparisonHtml, 'light', COMPONENT_CSS));
const lensResults = await audit(shell(lensHtml, 'light', COMPONENT_CSS));
const worklistResults = await audit(shell(worklistHtml, 'light', COMPONENT_CSS));
const chatHtml = renderToStaticMarkup(React.createElement(ChatPanel, {
  encounterId: 'bound-encounter-002-laakitys-sairaanhoitaja' }));
const chatResults = await audit(shell(chatHtml, 'light', COMPONENT_CSS));
// Every populated state, audited on its own: the container render above is the pre-load form only.
const chatStateResults = {};
for (const [label, html] of Object.entries(chatStateHtml)) {
  chatStateResults[label] = await audit(shell(html, 'light', COMPONENT_CSS));
}

for (const [view, results] of [['briefing', briefingResults], ['technical page', technicalResults],
  ['technical page with build stamp', stampedResults],
  ['comparison', comparisonResults],
  ['lens comparison', lensResults], ['worklist', worklistResults],
  ['source dialog', sourceDialogResults],
  ['chat panel', chatResults],
  ...Object.entries(chatStateResults).map(([label, results]) => [`chat rail (${label})`, results])]) {
  assert.deepEqual(
    results.violations, [],
    `${view} axe violations:\n${JSON.stringify(results.violations, null, 2)}`,
  );
}
// The chat rail's status and justification text is muted ink, so it is covered by the token pairs checked
// in both themes above rather than by a new colour that no ratio was ever measured for.
{
  const statusRule = /\.brief-app \.brief-inline-status\s*\{([^}]*)\}/.exec(SKIN_CSS)?.[1] ?? '';
  assert(statusRule.includes('color: var(--color-text-muted)'),
    'the audited chat status text takes its colour from --color-text-muted');
  assert.equal(TOKEN_PAIRS['muted text on a surface'][0], 'color-text-muted',
    'and that token is one the contrast table checks against a surface');
  assert.equal(TOKEN_PAIRS['muted text on the canvas'][0], 'color-text-muted',
    'and against the canvas');
}

// Dark is a shipped theme, not a variant: audit the same renders under `data-theme="dark"` on <html>.
const darkResults = {
  briefing: await audit(shell(briefingHtml, 'dark', COMPONENT_CSS)),
  technical: await audit(shell(technicalHtml, 'dark', COMPONENT_CSS)),
  comparison: await audit(shell(comparisonHtml, 'dark', COMPONENT_CSS)),
  lensComparison: await audit(shell(lensHtml, 'dark', COMPONENT_CSS)),
  worklist: await audit(shell(worklistHtml, 'dark', COMPONENT_CSS)),
  'chat rail (suggestion list)': await audit(shell(chatStateHtml['suggestion list'], 'dark', COMPONENT_CSS)),
  'chat rail (guideline links)': await audit(shell(chatStateHtml['guideline links'], 'dark', COMPONENT_CSS)),
};
for (const [view, results] of Object.entries(darkResults)) {
  assert.deepEqual(
    results.violations, [],
    `${view} axe violations in dark:\n${JSON.stringify(results.violations, null, 2)}`,
  );
}

// Phase A ships the shell, and no automated check could see it before: `audit()` ran only over the
// briefing, the comparison, the lens and the worklist renders. The shell is a rendered document like any
// other, so it is audited in both themes for its landmarks, its labelling and its contrast pairs.
const shellResults = await audit(appShell.replace('</style>', `${COMPONENT_CSS}</style>`));
const shellDarkResults = await audit(appShell.replace('data-theme="light"', 'data-theme="dark"')
  .replace('</style>', `${COMPONENT_CSS}</style>`));
for (const [view, results] of [['shell (light)', shellResults], ['shell (dark)', shellDarkResults]]) {
  assert.deepEqual(
    results.violations, [],
    `${view} axe violations:\n${JSON.stringify(results.violations, null, 2)}`,
  );
}
// The shell with the assistant pane open, in both themes: the pane's landmark, its heading, the toggle's
// `aria-expanded`/`aria-controls` pair and the chat panel inside it are all in the audited tree here. jsdom
// does no layout, so the 390px sheet form of the pane is a CSS claim this audit cannot see.
const assistantOpenResults = {
  'shell with assistant open (light)': await audit(appShellOpen('light').replace('</style>', `${COMPONENT_CSS}</style>`)),
  'shell with assistant open (dark)': await audit(appShellOpen('dark').replace('</style>', `${COMPONENT_CSS}</style>`)),
};
for (const [view, results] of Object.entries(assistantOpenResults)) {
  assert.deepEqual(
    results.violations, [],
    `${view} axe violations:\n${JSON.stringify(results.violations, null, 2)}`,
  );
}

// The four sections are stacked and always visible now — the owner dropped the tab strip, because a tab
// strip is a picker of content and the brief's promise is "valmiina ilman turhaa klikkailua". That makes
// this block simpler AND stronger than the version it replaces: there, `display: none` took three of the
// four sections out of the accessibility tree, so each one had to be opened by its own deep link and
// audited in a separate render (`panelAudits`, four hashes x two themes), and "the briefing is audited"
// meant four renders. One render now contains all four, so the audit above covers them by construction.
// What the old loop proved — "each deep link selects a different panel" — is replaced by what actually
// matters now: all four sections are in the document axe just looked at, and nothing hides them.
//
// B2 split the four between two documents: the briefing keeps the sections about the person, and the
// coverage and impact cards are the technical page's, audited above in light and dark. Each document is
// checked for exactly the sections it owns, and for not carrying the other's.
const SECTION_IDS = ['section-basics', 'section-records'];
const SECTION_HEADINGS = ['Perustiedot', 'Valitut kirjaukset'];
const TECHNICAL_SECTION_IDS = ['section-coverage', 'section-impact'];
const TECHNICAL_SECTION_HEADINGS = ['Kattavuus', 'Tekoälyn vaikutus'];
const auditedSections = (doc, ids, headings, name) => {
  for (const [index, sectionId] of ids.entries()) {
    const nodes = doc.querySelectorAll(`#${sectionId}`);
    assert.equal(nodes.length, 1, `the audited ${name} carries exactly one #${sectionId}`);
    assert(nodes[0].textContent.includes(headings[index]),
      `#${sectionId} carries its own heading in the audited ${name} (${headings[index]})`);
    assert(nodes[0].textContent.trim().split(/\s+/).length > headings[index].split(' ').length,
      `#${sectionId} carries content, not just a heading`);
  }
};
auditedSections(briefingDocument, SECTION_IDS, SECTION_HEADINGS, 'briefing');
auditedSections(technicalDocument, TECHNICAL_SECTION_IDS, TECHNICAL_SECTION_HEADINGS, 'technical page');
for (const sectionId of TECHNICAL_SECTION_IDS) {
  assert.equal(briefingDocument.querySelectorAll(`#${sectionId}`).length, 0,
    `the briefing no longer carries #${sectionId}: it is the technical page's`);
}
assert.equal(new Set([...SECTION_HEADINGS, ...TECHNICAL_SECTION_HEADINGS]).size, 4,
  'the four sections are four distinct sections');
assert.equal(briefingDocument.querySelectorAll('[role="tablist"], [role="tab"], [role="tabpanel"]').length, 0,
  'no tab strip remains: the four sections are stacked, not picked');
// No section, Block or landmark is hidden with the `hidden` attribute, which would remove it from the accessibility
// tree. The library IdentityStrip IS hidden in a static render — until its observer reports that the identity block
// has scrolled away (W1) — and a library row may fold its own records behind a disclosure; neither hides a section.
{
  const hiddenNodes = [...briefingDocument.querySelectorAll('[hidden]')];
  assert.deepEqual(hiddenNodes.filter(node => node.matches('section, aside, main, nav, [class*="_block_"], .brief-section')), [],
    'no section, Block or landmark is hidden with the `hidden` attribute');
  assert(hiddenNodes.some(node => node.matches('[role="group"][aria-label="Henkilö ja palvelutilanne"]')),
    'the identity strip is hidden in a static render, so it never covers the title on load');
}
// W1: the identity block, the Now strip and the MainSide columns are in the audited render.
assert(briefingDocument.querySelector('section.brief-identity[aria-label="Henkilö ja palvelutilanne"] h1'),
  'the identity block is a named section carrying the static render\'s h1');
assert.equal(briefingDocument.querySelectorAll('.brief-now-strip > *').length, 4, 'the Now strip has four cells');
assert.equal(briefingDocument.querySelectorAll('.brief-zone > .t-overline').length, 2, 'two zone lines');
assert.equal(briefingDocument.querySelectorAll('.brief-record-columns > aside').length, 1, 'the side column is one aside landmark');
assert(!briefingHtml.includes('brief-tab-panel') && !briefingHtml.includes('brief-section-tabs'),
  'the classes that hid three of the four sections are gone from the markup, so nothing can re-hide them');

// The record tables are the library `Table`, which owns the scroll box: it measures the table in a layout
// effect and, only when the table is wider than its container (measured at 375px, the lab table is 586px wide
// inside a 309px box), makes the box a named region and a Tab stop, so a keyboard user can reach the columns
// that are off-screen. jsdom has no layout, so the static audit cannot see that mode; what it can hold is the
// name the box takes — the table's own `aria-label`, which `Table` sets from its `label` prop — so every table
// is named, and named distinctly, so a reader can tell which table they are in.
const tables = [...briefingDocument.querySelectorAll('table')];
assert(tables.length > 0, 'the audited briefing carries the record tables at all');
for (const table of tables) {
  assert(table.getAttribute('aria-label')?.trim(), 'every record table has a name');
}
assert.equal(new Set(tables.map(t => t.getAttribute('aria-label'))).size, tables.length,
  'the record tables are named distinctly, so a reader can tell which table they are in');

// ---------- dynamic state announcements (axe cannot observe these) ----------

const states = {
  'comparison loading': { comparisonLoading: true },
  'proposal saving': { proposalReviews: { 'review-abc': { status: 'saving' } } },
  'proposal failed': { proposalReviews: { 'review-abc': { status: 'failed', error: 'hylätty' } } },
  'proposal ambiguous': { proposalReviews: { 'review-abc': { status: 'ambiguous', error: 'epäselvä' } } },
  'proposal decided': { proposalReviews: { 'review-abc': { status: 'decided' } } },
};
const announcements = {};
for (const [label, extra] of Object.entries(states)) {
  // The comparison runs on the technical page; the proposal states are the briefing's rail.
  const Surface = label.startsWith('comparison') ? TechnicalView : PictureBriefing;
  announcements[label] = renderToStaticMarkup(React.createElement(Surface, { ...baseProps, ...extra }));
}
assert.match(announcements['comparison loading'], /role="status"[^>]*>[^<]*Lasketaan vertailua/,
  'comparison loading state is announced in a polite live region');
assert.match(announcements['proposal saving'], /role="status"[^>]*>Tallennetaan/);
assert.match(announcements['proposal failed'], /role="alert"[^>]*>Palvelu hylkäsi tallennuksen/);
assert.match(announcements['proposal ambiguous'], /role="alert"/);
assert.match(announcements['proposal decided'], /role="status"[^>]*>[^<]*Olet jo päättänyt tämän ehdotuksen/,
  'the caller\'s own duplicate is announced politely as their earlier decision');

console.log(JSON.stringify({
  tool: 'axe-core (jsdom) + WCAG ratio assertion',
  briefing: { violations: briefingResults.violations.length, incomplete: briefingResults.incomplete },
  chatPanel: { violations: chatResults.violations.length, incomplete: chatResults.incomplete },
  chatRailStates: Object.fromEntries(Object.entries(chatStateResults)
    .map(([label, results]) => [label, { violations: results.violations.length }])),
  comparison: { violations: comparisonResults.violations.length, incomplete: comparisonResults.incomplete },
  lensComparison: { violations: lensResults.violations.length, incomplete: lensResults.incomplete },
  contrastMethod: 'resolved from the vendored tokens, light and dark; axe color-contrast disabled',
  contrastPairs: Object.keys(TOKEN_PAIRS).length * 2,
  darkAudits: Object.keys(darkResults).length,
  shell: { violations: shellResults.violations.length, darkViolations: shellDarkResults.violations.length,
    landmarks: appDocument.querySelectorAll('.brief-app nav').length },
  assistantPane: Object.fromEntries(Object.entries(assistantOpenResults)
    .map(([label, results]) => [label, { violations: results.violations.length, incomplete: results.incomplete }])),
  sections: { briefing: SECTION_IDS.length, technical: TECHNICAL_SECTION_IDS.length },
  componentSheets: { mapped: COMPONENT_MODULES.size, usedByRenders: usedModules.size,
    auditedBytes: COMPONENT_CSS.length },
  statusBlocksWithIcon: statusIconBlocksChecked,
  deadSelectorsMatched: deadSelectorsChecked,
  deadSelectorsOutOfScope: deadSelectorsOutOfScope,
}));
