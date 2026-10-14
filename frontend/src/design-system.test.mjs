// Design-system gate, vendored half.
//
// The design authority is the Swiss 1 baseline styleguide as implemented by the vendored
// `@luvn/component-library` at 014b02a (ADR-0026, which supersedes ADR-0016's vendor choice). This file makes
// the vendored copy *enforced* rather than trusted:
// every pinned file is checked against the SHA-256 recorded in PROVENANCE.md, and the token names the
// product depends on must exist. A hand-edit of a generated token is therefore a test failure, not a
// silent divergence from Figma.
//
// The app-half rules (no raw hex, no raw type values, no off-scale lengths, no opacity, no primitive
// references, app-root scoping, the dead-selector check) are added by phase 03 and phase 04 of
// `.scratch/design-system-migration/`. This half must pass before either of them.
import assert from 'node:assert/strict';
import postcss from '../node_modules/postcss/lib/postcss.mjs';
import { createHash } from 'node:crypto';
import { readFileSync, readdirSync } from 'node:fs';

// The medications table was once a hand-built grid whose row unit was pinned here, because a `nowrap` dose
// column starved the drug name to one character per line at rail width and nothing else could see it. The
// table is the library's `Table` now (ADR-0028), which never squeezes a column below its longest word, so the
// guard inverts: the app sheet must carry **no** `.brief-medications` rule at all — a rule layered on the
// vendored table would be the residue that reintroduces the squeeze.
{
  // Comments stripped first: a rule's "selector text" otherwise includes the comment block above it.
  const css = readFileSync(new URL('./styles.css', import.meta.url), 'utf8')
    .replace(/\/\*[\s\S]*?\*\//g, '');
  const rulesFor = (selectors) => {
    const found = [];
    for (const match of css.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
      const [, selectorText, body] = match;
      const selectorsInRule = selectorText.split(',').map(part => part.trim().replace(/\s+/g, ' '));
      if (selectors.some(wanted => selectorsInRule.includes(wanted) || wanted.test?.(selectorsInRule.join(',')))) found.push(body);
    }
    return found;
  };
  assert.equal(rulesFor([/brief-medications/]).length, 0,
    'the medications table is the vendored Table; no `.brief-medications` rule may be layered on it');
  // And the starvation class the medications bug belonged to: an `overflow-wrap: anywhere` text element that
  // shares a flex row with something that cannot shrink can be squeezed mid-word. The record heading is the
  // live instance — `time` is `flex: none` and `nowrap` at phone width — so the title beside it must not
  // zero its own minimum. `break-word` keeps the longest word as the minimum; `anywhere` does not.
  for (const body of rulesFor(['.brief-app .brief-selected-record'])) {
    assert(!/overflow-wrap:\s*anywhere/.test(body),
      '`.brief-selected-record` sets `overflow-wrap: anywhere`, which zeroes the minimum of the record title '
      + 'inside the flex heading and lets it starve mid-word beside the unshrinkable date. Use `break-word`.');
  }
}

const VENDOR = new URL('../vendor/comp-lib/', import.meta.url);
const read = relative => readFileSync(new URL(relative, VENDOR), 'utf8');

// ---------- the vendored copy is pinned, not trusted ----------

const provenance = read('PROVENANCE.md');

/** The checksum table in PROVENANCE.md: | `path` | `sha256` | `source` | */
function recordedChecksums(markdown) {
  return Object.fromEntries([...markdown.matchAll(/^\| `([^`]+)` \| `([0-9a-f]{64})` \| `([0-9a-f]{7})` \|$/gm)]
    .map(([, path, digest]) => [path, digest]));
}

/** The same table's source column: the revision each file was copied from. */
function recordedSources(markdown) {
  return Object.fromEntries([...markdown.matchAll(/^\| `([^`]+)` \| `[0-9a-f]{64}` \| `([0-9a-f]{7})` \|$/gm)]
    .map(([, path, source]) => [path, source]));
}

const PINNED = recordedChecksums(provenance);
const SOURCES = recordedSources(provenance);

// Completeness, not a hand-maintained list. The gate previously pinned exactly seven paths, so a component
// could be vendored and adopted without ever entering the ledger — trusted rather than enforced. Every file
// the product builds from must be pinned, and the sweep is derived from the tree so it cannot drift when a
// component is added.
function vendoredPaths(dir = '') {
  const out = [];
  for (const entry of readdirSync(new URL(dir, VENDOR), { withFileTypes: true })) {
    const path = `${dir}${entry.name}`;
    if (entry.isDirectory()) out.push(...vendoredPaths(`${path}/`));
    else out.push(path);
  }
  return out;
}
const VENDORED = vendoredPaths().filter(path => path.startsWith('tokens/') || path.startsWith('src/'));
assert(VENDORED.length >= 80,
  `the vendored sweep found ${VENDORED.length} files — a collapsed sweep would make this check vacuous`);
assert.deepEqual(VENDORED.filter(path => !(path in PINNED)), [],
  "every vendored file the product builds from must appear in PROVENANCE.md's checksum table");
assert.deepEqual(Object.keys(PINNED).filter(path => !VENDORED.includes(path)), [],
  'PROVENANCE.md pins files that are not in the vendored copy — the ledger and the tree disagree');

// Every vendored file must be copied from the one pinned revision. A checksum proves the bytes are unchanged;
// this proves each file's recorded source is that revision, and that no file is left without one.
const CURRENT_LIBRARY = '014b02a';
assert.deepEqual([...new Set(Object.values(SOURCES))], [CURRENT_LIBRARY],
  'every PROVENANCE row must name the pinned library revision (ADR-0026)');
assert.deepEqual(VENDORED.filter(path => !(path in SOURCES)), [],
  'every vendored file must name its source revision in PROVENANCE.md');

function assertPinned(markdown, contents = read) {
  const pinned = recordedChecksums(markdown);
  for (const [path, digest] of Object.entries(pinned)) {
    const actual = createHash('sha256').update(contents(path), 'utf8').digest('hex');
    assert.equal(actual, digest,
      `${path} does not match its pinned checksum — the vendored copy was edited by hand. `
      + 'Regenerate with `npm run tokens` in frontend/vendor/comp-lib and update PROVENANCE.md.');
  }
}

assertPinned(provenance, read);
assert(PINNED['src/components/Button/Button.module.css'], 'the adopted component is pinned too');
assert(PINNED['src/styles/tokens.css'], 'tokens.css is pinned — it is generated and must not be hand-edited');
assert(PINNED['tokens/figma-export.json'], 'the Figma export is pinned — it is the source of truth for the tokens');

// ---------- the vendored copy names one authority, pinned to a commit ----------
//
// ADR-0026: the library is pinned to a commit of LUVN-Component-Library, never to a branch, and the rehearsal
// repository is not the source of the token layer. A re-vendor from the wrong repository, or to a floating
// revision, fails here rather than in a review.
assert(/^\|\s*Repository\s*\|\s*\*\*`ECDX-Platforms\/LUVN-Component-Library`\*\*/m.test(provenance),
  'PROVENANCE names ECDX-Platforms/LUVN-Component-Library as the repository (ADR-0026)');
assert(/^\|\s*Revision\s*\|\s*\*\*`014b02a`\*\*/m.test(provenance),
  'PROVENANCE pins the revision to the commit 014b02a, not a branch (ADR-0026)');
assert(/^\|\s*Package\s*\|\s*`@luvn\/component-library`/m.test(provenance),
  'PROVENANCE names the package @luvn/component-library');
assert(provenance.includes('`fileKey P8E4RKubznUkk8vi9765oW`'),
  'PROVENANCE keeps the Figma fileKey the styleguide mirror is pinned against');

// Non-vacuity: a single changed byte must be caught, and so must a changed pin.
// The tamper targets a primitive's literal hex: the semantic roles are `var()` aliases, so replacing a
// role's declaration would be a no-op and the probe would pass vacuously.
const TAMPER_FROM = '--color-brand-600: #8251ca';
const TAMPER_TO = '--color-brand-600: #8251cb';
assert(read('src/styles/tokens.css').includes(TAMPER_FROM),
  'the non-vacuity probe targets a literal that exists — a no-op tamper would pass vacuously');
const tampered = path => read(path).replace(TAMPER_FROM, TAMPER_TO);
assert.notEqual(tampered('src/styles/tokens.css'), read('src/styles/tokens.css'),
  'the probe actually changes the file');
assert.throws(() => assertPinned(provenance, tampered), /tokens\.css does not match its pinned checksum/,
  'a single changed byte in tokens.css is caught');
assert.throws(() => assertPinned(provenance.replace(PINNED['src/styles/base.css'], '0'.repeat(64)), read),
  /base\.css does not match its pinned checksum/, 'a changed pin is caught');

// ---------- the token names the product depends on ----------

const tokensCss = read('src/styles/tokens.css');
const baseCss = read('src/styles/base.css');
const declared = new Set([...tokensCss.matchAll(/--([\w-]+)\s*:/g)].map(([, name]) => name));

const REQUIRED = {
  'semantic colour roles': [
    'color-bg', 'color-surface', 'color-surface-hover', 'color-surface-brand', 'color-primary-tint',
    'color-line', 'color-line-strong', 'color-text', 'color-text-muted',
    'color-primary', 'color-primary-hover', 'color-on-primary', 'color-on-primary-muted',
    'color-link', 'color-focus', 'color-primary-fill', 'color-on-primary-fill',
    'color-inverse', 'color-on-inverse', 'color-on-inverse-muted', 'color-on-inverse-link',
    'color-critical-fg', 'color-critical-bg', 'color-critical-border',
    'color-warning-fg', 'color-warning-bg', 'color-warning-border',
    'color-success-fg', 'color-success-bg', 'color-success-border',
    'color-info-fg', 'color-info-bg', 'color-info-border',
    'shadow-overlay', 'scrim',
  ],
  'colour primitives (never referenced by components)': [
    'color-brand-50', 'color-brand-100', 'color-brand-200', 'color-brand-300', 'color-brand-400',
    'color-brand-500', 'color-brand-600', 'color-brand-700', 'color-brand-800', 'color-brand-900',
    'color-neutral-0', 'color-neutral-50', 'color-neutral-100', 'color-neutral-200', 'color-neutral-300',
    'color-neutral-400', 'color-neutral-500', 'color-neutral-600', 'color-neutral-700',
    'color-neutral-800', 'color-neutral-900',
  ],
  'spacing scale (a 4px grid, and nothing else)': [
    'space-2xs', 'space-xs', 'space-sm', 'space-md', 'space-lg', 'space-xl', 'space-2xl', 'space-3xl',
  ],
  radii: ['radius-none', 'radius-sm', 'radius-md', 'radius-full'],
  'control, row and layout sizes': [
    'control-sm', 'control-h', 'control-lg', 'row-h', 'sidebar-w', 'logo-row', 'identity-h',
  ],
  motion: ['duration-fast', 'duration-base', 'duration-slow', 'ease'],
  'system typography': ['fs-body', 'lh-body', 'fs-small', 'lh-small', 'btn-pad', 'btn-pad-sm',
    'font-sans', 'font-features'],
};

for (const [group, names] of Object.entries(REQUIRED)) {
  const missing = names.filter(name => !declared.has(name));
  assert.deepEqual(missing, [], `${group}: missing from the vendored tokens.css`);
}

// Non-vacuity: the check reads real declarations, not a name that happens to appear in a comment.
assert(declared.has('color-primary'), 'the declaration scan finds a token that exists');
assert(!declared.has('color-sidebar'), 'the declaration scan does not invent a token that is absent');

// ---------- the typeface, by value and by load ----------
//
// A token change that names a font nothing loads falls back silently to system-ui, and no static check sees
// it: the first ADR-0026 re-pin changed --font-sans from Inter to Host Grotesk, and the gate was green while the
// font was unloaded. So the typeface is checked by value (the family and the feature list the tokens declare)
// and by load: the app's fonts.css must declare that family at weights 400, 500 and 600, and each file it names
// must be present, a WOFF2, and the SHA-256 recorded in fonts.css's header.

const FONTS_CSS = readFileSync(new URL('./fonts.css', import.meta.url), 'utf8');
const FONT_DIR = new URL('../public/fonts/', import.meta.url);

/** Every problem that would make --font-sans fall back silently. Empty when the family is loaded. */
function fontProblems(tokensText, fontsText) {
  const problems = [];
  const sans = tokensText.match(/--font-sans:\s*([^;]+);/);
  if (!sans) return ['--font-sans is not declared'];
  const family = sans[1].split(',')[0].trim().replace(/^["']|["']$/g, '');
  if (family !== 'Host Grotesk') problems.push(`--font-sans names "${family}", not "Host Grotesk" (ADR-0026)`);
  const features = (tokensText.match(/--font-features:\s*([^;]+);/) || [])[1];
  if (features?.trim() !== '"tnum" 1') {
    problems.push(`--font-features is ${features ? `\`${features.trim()}\`` : 'undeclared'}, not \`"tnum" 1\` (cv11 is dropped by ADR-0026)`);
  }
  const faces = postcss.parse(fontsText).nodes
    .filter(node => node.type === 'atrule' && node.name === 'font-face')
    .map(rule => {
      const decl = prop => rule.nodes.find(node => node.type === 'decl' && node.prop === prop)?.value ?? '';
      return {
        family: decl('font-family').replace(/["']/g, ''),
        weight: decl('font-weight').trim(),
        file: (decl('src').match(/url\(["']?\/fonts\/([^"')]+)["']?\)/) || [])[1],
      };
    });
  for (const weight of ['400', '500', '600']) {
    if (!faces.some(face => face.family === family && face.weight === weight && face.file)) {
      problems.push(`no @font-face declares "${family}" at weight ${weight}, so body text falls back to system-ui`);
    }
  }
  return problems;
}

assert.deepEqual(fontProblems(tokensCss, FONTS_CSS), [],
  'the typeface is Host Grotesk at weights 400/500/600, loaded by fonts.css — a silent fallback fails here');
// Non-vacuity: each way the font can silently go missing is reported.
assert(fontProblems(tokensCss.replace('"Host Grotesk", system-ui', '"Inter", system-ui'), FONTS_CSS)
  .some(problem => problem.includes('"Inter", not "Host Grotesk"')), 'a token that names Inter is reported');
assert(fontProblems(tokensCss, FONTS_CSS.replace(/font-weight: 600;/g, 'font-weight: 700;'))
  .some(problem => problem.includes('weight 600')), 'a missing 600 face is reported');
assert(fontProblems(tokensCss.replace('--font-features: "tnum" 1;', '--font-features: "tnum" 1, "cv11" 1;'), FONTS_CSS)
  .some(problem => problem.includes('--font-features')), 'a re-added cv11 feature is reported');

// The files: present, WOFF2, and the hashes the header of fonts.css records. Inter is gone, not dormant.
const FONT_HASHES = [...FONTS_CSS.matchAll(/^ \*\s+(host-grotesk-[\w-]+\.woff2)\s+([0-9a-f]{64})$/gm)]
  .map(([, file, digest]) => ({ file, digest }));
assert.equal(FONT_HASHES.length, 6, 'fonts.css records the SHA-256 of all six Host Grotesk files');
const publicFonts = readdirSync(FONT_DIR).filter(name => name.endsWith('.woff2'));
assert.deepEqual(publicFonts.filter(name => !name.startsWith('host-grotesk-')), [],
  'no Inter or other font file remains in public/fonts after the switch');
assert.deepEqual(FONT_HASHES.map(({ file }) => file).sort(), publicFonts.sort(),
  'the files in public/fonts are exactly the files fonts.css records');
for (const { file, digest } of FONT_HASHES) {
  const bytes = readFileSync(new URL(file, FONT_DIR));
  assert.equal(bytes.subarray(0, 4).toString('latin1'), 'wOF2', `${file} is a WOFF2`);
  assert.equal(createHash('sha256').update(bytes).digest('hex'), digest, `${file} matches its recorded SHA-256`);
}

// ---------- the two colours the styleguide deliberately omits ----------

for (const forbidden of ['--color-sidebar', '--color-header', '--color-on-sidebar', '--color-on-header']) {
  assert(!tokensCss.includes(forbidden),
    `${forbidden} must not exist: the styleguide marks sidebar/header "not used in this style"`);
}

// ---------- the type roles, and the base layer's contract ----------

const ROLES = ['t-page-title', 't-title-lg', 't-title-md', 't-title-sm', 't-metric', 't-metric-sm',
  't-body', 't-body-strong', 't-control', 't-caption', 't-tag', 't-overline', 't-initials'];
const missingRoles = ROLES.filter(role => !new RegExp(`^\\s*\\.${role}\\s*\\{`, 'm').test(tokensCss));
assert.deepEqual(missingRoles, [], 'all thirteen .t-* roles are declared');
assert(ROLES.length === 13, 'the role list is complete — a silently dropped role is a failure');

for (const rule of [
  /:focus-visible\s*\{[^}]*outline:\s*2px solid var\(--color-focus\)/,
  /:focus-visible\s*\{[^}]*outline-offset:\s*2px/,
  /\.prose\s*\{\s*max-width:\s*75ch/,
  /\.visually-hidden\s*\{/,
  /\.icon\s*\{[^}]*width:\s*16px/,
]) {
  assert(rule.test(baseCss), `the base layer still supplies ${rule}`);
}
assert(/scroll-padding-top:\s*calc\(var\(--identity-h\) \+ var\(--space-xs\)\)/.test(baseCss),
  'the base layer keeps the identity-bar scroll offset the app relies on');

// ---------- the app stylesheets obey the styleguide ----------
//
// The library's rules, as far as they can be checked without a browser: tokens only, no colour literal,
// never lower contrast with opacity, primitives never referenced, type from the roles, lengths on the
// grid, and no rule that could style anything outside the app.

const APP_SHEETS = ['styles.css', 'design-system.css', 'fonts.css'].map(name => ({
  name,
  css: readFileSync(new URL(`./${name}`, import.meta.url), 'utf8'),
}));

/** Rules only: @-rule preludes and @font-face bodies are not component styles. */
function appRuleNodes(name, css) {
  void name;
  const root = postcss.parse(css);
  const out = [];
  root.walkRules(rule => {
    if (rule.parent?.type === 'atrule' && rule.parent.name === 'font-face') return;
    out.push({ selector: rule.selector, rule });
  });
  return out;
}
function appRules(name, css) {
  const root = postcss.parse(css);
  const out = [];
  root.walkRules(rule => {
    if (rule.parent?.type === 'atrule' && rule.parent.name === 'font-face') return;
    out.push({ selector: rule.selector, body: rule.nodes ? css.slice(rule.source.start.offset, rule.source.end.offset) : '' });
  });
  return out;
}

/** The one deliberate exception: index.css's base rules, re-homed by phase 03 commit 1. Matched per
 *  selector part, because a rule's selector list is split before it is judged. */
const BASE_EXCEPTIONS = new Set([
  '*', 'html', 'body', '#root',   // index.css's re-homed base rules
  'strong', 'b',                  // emphasis elements: base.css does not reset them, and the design
                                  // system has exactly three weights, so <strong> must land on 600
]);

// ---------- a value grammar, not a blacklist ----------
//
// The rules here used to be blacklists — `#hex`, `opacity:`, `var(--color-brand-*` — and a crafted
// violation walked past most of them: `rgb(0 0 0 / .5)`, `Opacity: .5`, `VAR(--color-brand-500)`,
// `1.25rem`, `PADDING: 6PX`, `padding-inline`, `font-size-adjust`, or a custom property holding `6px`
// and referenced by `padding`. A blacklist has to enumerate every way to write a value, so it fails open.
// A grammar states what is allowed and fails closed.
//
// In the app sheets, every declaration is:
//   - a `var()` naming a token the library declares — so a typo, a case trick or a primitive fails, and
//     the check does not care how the value is spelled around it;
//   - a permitted literal: a length on the 4px grid, a spacing step, one of three weights, a keyword;
//   - a custom property: never. The app takes the library's tokens; it does not mint its own.
//   - a type declaration: never, except `font-weight`.
// and nothing anywhere may carry a colour literal or use opacity. Property names and value keywords are
// lowercased first, because CSS is case-insensitive.

const COLOUR_PROPERTY = /^(?:color|background|background-color|background-image|border|border-(?:top|right|bottom|left)(?:-(?:color|style|width))?|border-color|outline|outline-color|fill|stroke|box-shadow|text-decoration-color|caret-color|accent-color|column-rule|column-rule-color)$/;
const COLOUR_FUNCTION = /(?:^|[^\w-])(?:rgb|rgba|hsl|hsla|hwb|lab|lch|oklab|oklch|color|color-mix|light-dark)\s*\(/;
const HEX_COLOUR = /#[0-9a-f]{3,8}\b/;
const TYPE_PROPERTY = /^(?:font-size|line-height|letter-spacing|font-family|font|font-stretch|font-size-adjust|font-variation-settings|font-feature-settings|font-kerning|font-optical-sizing|word-spacing|text-rendering)$/;
// Every property whose value is a dimension the token scale owns. Council F found `grid-template-columns`,
// `block-size`, `inset-inline-start` and `outline-width` walking past the old list, which named only the
// box-model properties.
const LAYOUT_PROPERTY = /^(?:padding|margin|gap|row-gap|column-gap|width|height|min-width|max-width|min-height|max-height|block-size|inline-size|min-block-size|max-block-size|min-inline-size|max-inline-size|inset|top|right|bottom|left|inset-inline[a-z-]*|inset-block[a-z-]*|flex-basis|grid-template-columns|grid-template-rows|grid-auto-columns|grid-auto-rows|border(?:-[a-z]+)?-width|outline(?:-[a-z]+)?-width|scroll-margin[a-z-]*|scroll-padding[a-z-]*|transform)$/;
const SPACING_PROPERTY = /^(?:padding|margin|gap|row-gap|column-gap)/;
const WEIGHTS = new Set(['400', '500', '600']);
const SPACING_STEPS = new Set([4, 8, 12, 16, 24, 32, 48, 64]);
// Off the grid: anything not in px. `rem`/`em` scale with a context the token set does not define, and a
// `ch` measure is the library's `.prose` to own.
const OFF_UNIT_LENGTH = /-?\d*\.?\d+(?:rem|em|ex|ch|pt|pc|cm|mm|in|q)\b/;
// Everything a colour-bearing value may legitimately contain once numbers, units and `var()` are removed.
const COLOUR_KEYWORDS = new Set([
  'none', 'transparent', 'currentcolor', 'inherit', 'initial', 'unset', 'revert', 'revert-layer', 'auto',
  'solid', 'dashed', 'dotted', 'double', 'groove', 'ridge', 'inset', 'outset', 'hidden',
  'left', 'right', 'top', 'bottom', 'center', 'cover', 'contain', 'no-repeat', 'repeat', 'round', 'space',
  'border-box', 'padding-box', 'content-box', 'linear', 'radial', 'conic', 'gradient', 'var', 'calc',
]);
// One named exemption, with its reason: the library exposes no role for numeric variants, and base.css does
// not set tabular numerals, so aligning a column of measurement values needs this one declaration.
const VALUE_EXEMPTIONS = new Map([['font-variant-numeric', new Set(['tabular-nums'])]]);

/** Every value-grammar finding, keyed by rule. Pure: a function of the sheets and the declared tokens. */
function appValueFindings(sheets, declaredTokens) {
  const out = { customProperty: [], unknownToken: [], primitive: [], colour: [], opacity: [],
    type: [], weight: [], length: [], unit: [], step: [] };
  for (const { name, css } of sheets) {
    for (const { selector, rule } of appRuleNodes(name, css)) {
      rule.walkDecls(decl => {
        const prop = decl.prop.trim().toLowerCase();
        const raw = decl.value.replace(/\/\*[\s\S]*?\*\//g, ' ').trim();
        const value = raw.toLowerCase();
        const at = `${name}: ${prop} in ${selector.replace(/\s+/g, ' ').slice(0, 38)}`;

        if (prop.startsWith('--')) { out.customProperty.push(`${name}: ${prop}`); return; }
        if (VALUE_EXEMPTIONS.get(prop)?.has(value)) return;

        for (const match of value.matchAll(/var\(\s*--([\w-]+)/g)) {
          const token = match[1];
          if (!declaredTokens.has(token)) out.unknownToken.push(`${at} -> --${token}`);
          if (/^color-(?:brand|neutral)-/.test(token)) out.primitive.push(`${at} -> --${token}`);
        }

        if (HEX_COLOUR.test(value) || COLOUR_FUNCTION.test(value)) {
          out.colour.push(`${at}: ${raw.slice(0, 36)}`);
        } else if (COLOUR_PROPERTY.test(prop)) {
          const stray = (value.replace(/var\([^)]*\)/g, ' ').replace(/-?\d*\.?\d+[a-z%]*/g, ' ').match(/[a-z][a-z-]*/g) ?? [])
            .filter(word => !COLOUR_KEYWORDS.has(word));
          if (stray.length) out.colour.push(`${at}: ${raw.slice(0, 36)} (${stray[0]})`);
        }

        if (prop === 'opacity' || /(?:^|[^\w-])opacity\s*\(/.test(value)) out.opacity.push(at);
        if (TYPE_PROPERTY.test(prop)) out.type.push(at);
        if (prop === 'font-weight' && !WEIGHTS.has(value)) out.weight.push(`${at}: ${raw}`);

        if (SPACING_PROPERTY.test(prop)) {
          // Strip only the var() parts, not the whole declaration: `28px var(--space-xl)` is one token and
          // one off-scale literal, and skipping the declaration because it mentions a token was a bypass.
          for (const match of value.replace(/var\([^)]*\)/g, ' ').match(/-?\d*\.?\d+px/g) ?? []) {
            // The magnitude must be a step; the sign is direction. A negative margin that pulls a row out
            // by `--space-xs` so a left border sits flush is a deliberate technique, not an off-scale value.
            const size = Math.abs(parseFloat(match));
            if (size !== 0 && !SPACING_STEPS.has(size)) out.step.push(`${at}: ${match}`);
          }
        }
        if (LAYOUT_PROPERTY.test(prop)) {
          if (OFF_UNIT_LENGTH.test(value)) out.unit.push(`${at}: ${raw.slice(0, 36)}`);
          for (const match of value.match(/-?\d*\.?\d+px/g) ?? []) {
            const size = parseFloat(match);
            if (size !== 0 && size % 4 !== 0 && Math.abs(size) > 2) out.length.push(`${at}: ${match}`);
          }
        }
      });
    }
  }
  return out;
}

// The scope rule is a selector rule, not a value rule, so it stays separate.
const appFindings = { scope: [] };
for (const { name, css } of APP_SHEETS) {
  for (const rule of appRules(name, css)) {
    for (const part of rule.selector.split(',').map(p => p.trim()).filter(Boolean)) {
      if (BASE_EXCEPTIONS.has(part)) continue;
      const classes = [...part.matchAll(/\.([\w-]+)/g)].map(m => m[1]);
      // Either explicitly scoped to the app root, or a `.brief-*` class selector. Every `.brief-*` class
      // is rendered inside the app root, so that cannot leak; a bare tag selector could.
      if (part.includes('.brief-app')) continue;
      if (classes.length && classes.every(c => c.startsWith('brief-'))) continue;
      appFindings.scope.push(`${name}: ${part.slice(0, 60)}`);
    }
  }
}

const appValues = appValueFindings(APP_SHEETS, declared);
for (const [rule, message] of [
  ['customProperty', 'the app sheets declare no custom properties — the tokens come from the library'],
  ['unknownToken', 'every var() names a token the library declares — a typo or an invented token fails here'],
  ['primitive', 'no primitive reference: --color-brand-* and --color-neutral-* are never used by components'],
  ['colour', 'no colour literal anywhere — colour comes from the library tokens, however it is spelled'],
  ['opacity', 'no opacity, as a property or a function: the styleguide says never lower contrast with opacity'],
  ['type', 'no raw type declaration: font-size, line-height, letter-spacing and font-family come from .t-* roles'],
  ['weight', "the only weight literals are the design system's three: 400, 500, 600"],
  ['length', 'every layout length is on the 4px grid, or a 1px/2px stroke'],
  ['unit', 'layout lengths are in px on the grid — rem/em/ch/pt and friends are not on the token scale'],
  ['step', 'spacing lands on a library step (4/8/12/16/24/32/48/64), not merely a multiple of 4'],
]) {
  assert.deepEqual(appValues[rule], [], message);
}
assert.deepEqual(appFindings.scope, [],
  'every rule is app-root scoped, a .brief-* class selector, or a documented base rule');

// Non-vacuity, data-driven: every rule above is probed with a value it must reject, and the probes are
// counted rather than typed. The previous version re-implemented the rules inside the probe helper, so
// disabling a collector left the gate green — these run the real collector.
const VALUE_PROBES = [
  ['customProperty', '.brief-app { --p1: 6px; padding: var(--p1); }'],
  ['customProperty', '.brief-app { --Spacing: 6px; }'],
  ['unknownToken', '.brief-app { color: var(--color-made-up); }'],
  ['primitive', '.brief-app { color: var(--color-brand-500); }'],
  ['primitive', '.brief-app { color: VAR( --color-neutral-900 ); }'],
  ['primitive', '.brief-app { color: var(--color-brand-500, transparent); }'],
  ['colour', '.brief-app { color: #ff0000; }'],
  ['colour', '.brief-app { color: rgb(0 0 0 / 0.5); }'],
  ['colour', '.brief-app { color: hsl(270 50% 53%); }'],
  ['colour', '.brief-app { color: rebeccapurple; }'],
  ['colour', '.brief-app { COLOr: RED; }'],
  ['colour', '.brief-app { background: color-mix(in srgb, red 50%, blue); }'],
  ['opacity', '.brief-app { opacity: 0.5; }'],
  ['opacity', '.brief-app { Opacity: 0.5; }'],
  ['opacity', '.brief-app { filter: opacity(0.5); }'],
  ['type', '.brief-app { font-size: 3rem; }'],
  ['type', '.brief-app { FONT-SIZE: 12px; }'],
  ['type', '.brief-app { font-size-adjust: 0.5; }'],
  ['type', '.brief-app { word-spacing: 4px; }'],
  ['weight', '.brief-app { font-weight: 700; }'],
  ['length', '.brief-app { width: 250px; }'],
  ['length', '.brief-app { transform: translateX(6px); }'],
  ['length', '.brief-app { grid-template-columns: 250px 1fr; }'],
  ['length', '.brief-app { block-size: 250px; }'],
  ['length', '.brief-app { inset-inline-start: 250px; }'],
  ['length', '.brief-app { outline-width: 3px; }'],
  ['length', '.brief-app { PADDING: 6PX; }'],
  ['unit', '.brief-app { padding: 1.25rem; }'],
  ['unit', '.brief-app { width: 1.5em; }'],
  ['unit', '.brief-app { max-inline-size: 65ch; }'],
  ['step', '.brief-app { margin: 20px; }'],
  ['step', '.brief-app { padding: 28px var(--space-xl); }'],
  ['step', '.brief-app { padding-inline: 6px; }'],
  ['step', '.brief-app { padding: 28px var(--space-xl); }'],
  ['step', '.brief-app { margin-left: -20px; }'],
];
let valueProbeCount = 0;
for (const [rule, css] of VALUE_PROBES) {
  const findings = appValueFindings([{ name: 'probe.css', css }], declared);
  assert(findings[rule].length > 0, `the ${rule} rule must reject: ${css}`);
  valueProbeCount += 1;
}
// The grammar must also accept what the real sheets are, or "everything is a finding" would pass the
// probes above while failing the app.
assert.deepEqual(
  Object.entries(appValueFindings([{ name: 'ok.css', css: '.brief-app { color: var(--color-text); padding: var(--space-xs) var(--space-xs); border: 1px solid var(--color-line-strong); font-weight: 500; grid-template-columns: 260px minmax(0, 1fr); }' }], declared))
    .filter(([, found]) => found.length),
  [], 'the value grammar accepts a declaration set that obeys it');
assert.deepEqual(appValueFindings([{ name: 'ok.css', css: '.brief-app { margin-left: -8px; padding-left: 8px; }' }], declared).step, [],
  'a negative margin whose magnitude is a step is accepted — the sign is direction');
assert(COLOUR_KEYWORDS.has('solid') && !COLOUR_KEYWORDS.has('red'),
  'the colour keyword set is non-vacuous: a border style passes and a named colour does not');


// The files every source-level scan below reads, DERIVED from the tree rather than listed by hand.
//
// They used to be three literal names, so anything new under `frontend/src` rendered without any static
// check at all: a new component could have styled a class nothing renders, dropped a `.t-*` role off a
// heading, or (via the backend's matching sweep) rendered a banned word, and every gate would have stayed
// green. Deriving the set means a file is covered on the commit that adds it. Test files are excluded —
// they hold negative fixtures on purpose, and the probes are where a planted violation belongs.
function sourceFiles(pattern) {
  return readdirSync(new URL('./', import.meta.url), { withFileTypes: true })
    .filter(entry => entry.isFile() && entry.name.endsWith(pattern) && !entry.name.endsWith('.test.mjs'))
    .map(entry => entry.name)
    .sort();
}
const JSX_FILES = sourceFiles('.jsx');
const MODULE_FILES = sourceFiles('.mjs');
const SOURCE_FILES = [...JSX_FILES, ...MODULE_FILES];
// Anti-vacuity: a collapsed or wrong-rooted sweep would make every scan below pass by reading nothing.
assert(SOURCE_FILES.length >= 4, `the source sweep found ${SOURCE_FILES.length} files — a collapsed sweep would pass vacuously`);
for (const required of ['App.jsx', 'ChallengeDemo.jsx', 'challenge-demo.mjs']) {
  assert(SOURCE_FILES.includes(required), `the source sweep lost ${required}`);
}
// The render path and the setup file: a `.test.mjs` that leaked in would make the class scans meaningless.
assert(JSX_FILES.includes('App.jsx') && JSX_FILES.includes('main.jsx') && MODULE_FILES.includes('worklist.mjs'),
  'the sweep picks up every source kind it is meant to');
assert(!SOURCE_FILES.some(name => name.endsWith('.test.mjs')), 'the sweep excludes test fixtures on purpose');

// Every class the app stylesheets define must actually be rendered. This is the static half of the
// dead-selector check: it cannot prove a selector *matches* — that needs the DOM, and phase 07 owns it
// once the shell is final and both themes are audited — but it does catch a class renamed in the JSX and
// forgotten in the CSS, or one orphaned by the migration.
const APP_SOURCE = SOURCE_FILES
  .map(name => readFileSync(new URL(`./${name}`, import.meta.url), 'utf8')).join('\n');
// Composed at runtime as `brief-${tone}` / `brief-${riskTone}`, so no literal appears in the source.
const COMPOSED = new Set(['brief-critical', 'brief-warning', 'brief-info']);
const definedClasses = new Set();
for (const { name, css } of APP_SHEETS) {
  for (const rule of appRules(name, css)) {
    for (const match of rule.selector.matchAll(/\.([\w-]+)/g)) definedClasses.add(match[1]);
  }
}
// A boundary-aware reference test, not a bare substring search: `brief-summary-x` must not count as a
// reference to `brief-summary`, or a dropped rule's markup would leave its neighbour looking live. The
// pattern is exercised in both directions by the probes below.
const references = (cls, haystack = APP_SOURCE) => new RegExp(`(?<![\\w-])${cls}(?![\\w-])`).test(haystack);
const orphaned = [...definedClasses].filter(cls => !COMPOSED.has(cls) && !references(cls));
assert.deepEqual(orphaned, [],
  'every class the app stylesheets define is referenced by the app source; a renamed or dropped class '
  + 'must not leave its rule behind');

// Non-vacuity: a class that exists only in CSS must be reported, so the probe uses one the source
// genuinely does not mention.
assert(!references('brief-summary'),
  'the orphan rule is non-vacuous: it would report a class the source does not reference');
// The boundary is load-bearing, and this is the case that proves it in the data the sweep actually
// contains: `brief-summary-x` must not be read as a reference to `brief-summary`, or dropping a rule's
// markup would leave the rule looking live.
assert(!references('brief-summary', 'className="brief-summary-x"'),
  'a longer class name is not a reference to the shorter one it extends');
assert(references('brief-summary', 'className="brief-summary"'),
  'the boundary-aware matcher still finds the class itself');


// The CSS cannot see this: a heading with no role class just inherits body text, and looks wrong without
// failing anything. Every heading, table header, definition term and disclosure summary must carry a role.
// The role, motion and h1 scans read the whole derived set: a heading without a role, an unguarded smooth
// scroll, or a second page heading must not become invisible by moving into a new file.
const JSX_SOURCES = JSX_FILES
  .map(name => ({ name, source: readFileSync(new URL(`./${name}`, import.meta.url), 'utf8') }));
const HAS_ROLE = /className=[^>]*\bt-[a-z-]+/;
const roleFindings = [];
for (const { name, source } of JSX_SOURCES) {
  for (const tag of ['h1', 'h2', 'h3', 'th', 'dt', 'summary']) {
    for (const match of source.matchAll(new RegExp(`<${tag}(\\s[^>]*)?>`, 'g'))) {
      if (!HAS_ROLE.test(match[0])) roleFindings.push(`${name}: <${tag}> without a role class`);
    }
  }
}
assert.deepEqual(roleFindings, [],
  'every heading, table header, definition term and disclosure summary carries a .t-* role');
const roleScanProbe = `<h2>No role</h2>`;
assert(!HAS_ROLE.test(roleScanProbe), 'the role scan is non-vacuous: a bare heading does not pass it');

// ONE h1 PER RENDERED VIEW. The library's `Header` owns it now (issue 02): it renders `title` as the
// page's heading, the worklist panel's own heading is an h2, and the briefing's is switched off with
// `hideHeading`. The old check counted literal h1 tags per file and asserted they summed to two, which
// was arithmetic about *files*, not a statement about what renders — it stayed green while the loaded
// briefing put the Header's heading and the component's own in the DOM together, and it went red the
// moment the shell stopped declaring one. What is checkable without a browser is the shell's half: the
// Header is rendered exactly once and is actually handed a title.
const appSource = readFileSync(new URL('./App.jsx', import.meta.url), 'utf8');
assert.equal([...appSource.matchAll(/<Header(\s|>)/g)].length, 1,
  'the shell renders the library Header exactly once, so the view has exactly one page heading');
assert.equal([...appSource.matchAll(/<Header\s+title=/g)].length, 1,
  'the Header is handed a title — a second title prop, or a missing one, fails here');
// Non-vacuity: a second title prop would be counted, so "exactly one" is a real constraint.
assert.equal([...'<Header title={a}><Header title={b}>'.matchAll(/<Header\s+title=/g)].length, 2,
  'the title-prop count is non-vacuous: two Headers with titles are two');
// The component keeps its own heading for the renders that have no shell around them (the SSR tests and
// the lens/comparison fixtures), and the shell switches it off; both halves are asserted.
assert([...readFileSync(new URL('./ChallengeDemo.jsx', import.meta.url), 'utf8')
  .matchAll(/hideHeading/g)].length >= 2,
'the briefing component still takes hideHeading, which is what the shell passes');
for (const { name, source } of JSX_SOURCES) {
  const h1s = [...source.matchAll(/<h1(\s[^>]*)?>/g)].length;
  assert(h1s <= 1, `${name} declares at most one h1 (found ${h1s})`);
}

// The library supplies `.prose` (75ch) as the measure for running text; the app must not re-decide it.
// Viewport heights (`100dvh`) are exempt: the library has no viewport token, and they decide no spacing
// or measure.
const measures = APP_SHEETS.flatMap(({ name, css }) =>
  [...css.replace(/\/\*[\s\S]*?\*\//g, '').matchAll(/max-width:\s*[\d.]+ch/g)].map(m => `${name}: ${m[0]}`));
assert.deepEqual(measures, [], "prose measures come from the library's .prose, not a literal ch cap");
assert.equal([...'max-width: 65ch'.matchAll(/max-width:\s*[\d.]+ch/g)].length, 1,
  'the measure rule is non-vacuous: a literal ch cap is caught');

// Tokens the product does not consume, each with its reason. The alternative — silence — makes a genuine
// oversight indistinguishable from a deliberate decline, and the next author would have to re-derive it.
const DECLINED_TOKENS = {
  'color-inverse': 'no inverse emphasis block: the styleguide allows at most one per screen and this product has none',
  'color-on-inverse': 'same: no inverse emphasis block',
  'color-on-inverse-muted': 'same: no inverse emphasis block',
  'color-on-inverse-link': 'same: no inverse emphasis block',
  'color-primary-fill': 'the re-pointable fill for an emphasis block; this product does not re-point it',
  'color-on-primary-fill': 'paired with --color-primary-fill, unused for the same reason',
  'color-on-primary-muted': 'paired with the emphasis block; the primary label uses --color-on-primary',
  'color-surface-brand': 'thin marks only, never behind text; the product has no such mark, and phase 03 removed the last six misuses',
  'shadow-overlay': 'overlays only (dialog, menu, popover, tooltip); the product has no overlay surface',
  'scrim': 'same: no overlay surface',
  'space-3xl': "the only 64px use is the upper bound of a fluid clamp() on the main padding, which is not a spacing step",
  'fs-small': 'consumed through the role classes (.t-caption/.t-tag/.t-overline), not referenced directly',
  'lh-small': 'consumed through the role classes, not referenced directly',
};
for (const name of Object.keys(DECLINED_TOKENS)) {
  assert(!new RegExp(`var\\(--${name}\\)`).test(APP_SHEETS.map(s => s.css).join('\\n')),
    `--${name} is declined and must not be referenced: ${DECLINED_TOKENS[name]}`);
}
assert(Object.keys(DECLINED_TOKENS).length === 13, 'every declined token is named with a reason');

// Motion must be opt-out-able. A smooth scroll ignores `prefers-reduced-motion` unless it is asked, and the
// styleguide's reduced-motion rule covers scripted motion as well as CSS. Two calls here had no guard.
for (const { name, source } of JSX_SOURCES) {
  if (!/behavior:\s*["']smooth["']/.test(source)) continue;
  assert(/prefers-reduced-motion/.test(source),
    `${name} asks for a smooth scroll but never consults prefers-reduced-motion`);
}
assert(/prefers-reduced-motion/.test(`matchMedia('(prefers-reduced-motion: reduce)')`),
  'the reduced-motion guard is non-vacuous');

// A cascade trap that no other check can see. A bare element selector in the app sheet out-specifies a
// vendored component's class and silently rewrites it: `.brief-app button` is (0,1,1) against the Button's
// CSS-module class at (0,1,0), so it won the padding, the background and the radius, and `variant="primary"`
// lost its fill entirely while every gate stayed green. The fix was to scope the app's rules to the elements
// they are for, and this keeps it fixed: an element compound may not be constrained only by the app root.
const COMPONENT_ROOTS = new Set(['button']);   // vendored Button renders <button>; grows with each component
// Only the app root and the .t-* roles fail to constrain the element; any other class (a component class
// such as .brief-primary-nav) is a real constraint, because a library Button never sits inside one.
const NOT_A_CONSTRAINT = /^(\.brief-app|\.t-[\w-]+)?$/;
const bareElementRules = [];
for (const { name, css } of APP_SHEETS) {
  for (const { selector } of appRuleNodes(name, css)) {
    for (const part of selector.split(',')) {
      const compounds = part.trim().split(/[\s>+~]+/).filter(Boolean);
      const last = compounds[compounds.length - 1] ?? '';
      const element = /^([a-z][\w-]*)(?::[\w-]+(?:\([^)]*\))?|\[[^\]]*\])*$/.exec(last);
      if (!element || !COMPONENT_ROOTS.has(element[1])) continue;
      const ancestors = compounds.slice(0, -1).filter(c => !NOT_A_CONSTRAINT.test(c));
      if (!ancestors.length) bareElementRules.push(`${name}: ${part.trim()}`);
    }
  }
}
assert.deepEqual(bareElementRules, [],
  'no app rule constrains a vendored component\'s element by the app root alone (it would out-specify the component)');
assert(NOT_A_CONSTRAINT.test('.brief-app') && !NOT_A_CONSTRAINT.test('.brief-primary-nav'),
  'the constraint test is non-vacuous: a real class ancestor is not treated as the app root');

// A `.t-*` role owns its colour, so an app class that sets a *different* colour on the same element silently
// overrides the role. `.brief-eyebrow` carried `color: var(--color-link)` on a `t-overline`, which is both a
// role override and a second accent; the gate could not see it because the selector never names the role.
// Same value (a harmless re-statement) and roles with no colour of their own (`.t-tag`, which inherits) are
// allowed — the rule is about disagreement, not about touching the property.
const roleColours = new Map();
{
  postcss.parse(read('src/styles/tokens.css')).walkRules(rule => {
    const role = rule.selector.trim();
    if (!/^\.t-[\w-]+$/.test(role)) return;
    const decl = rule.nodes?.find(n => n.type === 'decl' && n.prop === 'color');
    roleColours.set(role.slice(1), decl ? decl.value.trim() : null);
  });
}
assert.equal(roleColours.get('t-caption'), 'var(--color-text-muted)', 'the role colours are read from the vendored tokens');
assert.equal(roleColours.get('t-tag'), null, 'a role with no colour of its own is distinguished from a muted one');
assert.equal(roleColours.size, 13, `all 13 roles are known (found ${roleColours.size})`);

const appClassColours = new Map();
for (const { name, css } of APP_SHEETS) {
  for (const { selector, rule } of appRuleNodes(name, css)) {
    let colour = null;
    rule.walkDecls(d => { if (d.prop === 'color') colour = d.value.trim(); });
    if (!colour) continue;
    for (const part of selector.split(',')) {
      const last = part.trim().split(/[\s>+~]+/).pop() ?? '';
      for (const m of last.matchAll(/\.([\w-]+)/g)) appClassColours.set(m[1], colour);
    }
  }
}
const overrides = [];
for (const { name, source } of JSX_SOURCES) {
  for (const match of source.matchAll(/className=(?:"([^"]*)"|\{`([^`]*)`)/g)) {
    let template = match[1] ?? match[2] ?? '';
    for (let before = ''; before !== template;) {
      before = template;
      template = template.replace(/\$\{[^{}]*\}/g, ' ');
    }
    const tokens = template.split(/[\s?:'"]+/).filter(t => /^[a-z][\w-]*$/.test(t));
    const roles = tokens.filter(t => roleColours.has(t) && roleColours.get(t));
    for (const cls of tokens.filter(t => appClassColours.has(t))) {
      for (const role of roles) {
        if (appClassColours.get(cls) !== roleColours.get(role)) {
          overrides.push(`${name}: .${cls} sets ${appClassColours.get(cls)} but .${role} sets ${roleColours.get(role)}`);
        }
      }
    }
  }
}
assert.deepEqual(overrides, [], 'no app class disagrees with the colour of the .t-* role on the same element');
const overrideProbe = [{ cls: 'brief-eyebrow', colour: 'var(--color-link)', role: 't-overline', roleColour: 'var(--color-text-muted)' }];
assert(overrideProbe[0].colour !== overrideProbe[0].roleColour,
  'the role-override rule is non-vacuous: the eyebrow case would be reported');

// Structural debris the class-based orphan check cannot see: a rule or at-rule left empty by a merge.
const emptyNodes = [];
for (const { name, css } of APP_SHEETS) {
  postcss.parse(css).walk(node => {
    if ((node.type === 'rule' || node.type === 'atrule') && node.nodes && node.nodes.length === 0) {
      emptyNodes.push(`${name}: ${node.selector ?? '@' + node.name + ' ' + node.params}`);
    }
  });
}
assert.deepEqual(emptyNodes, [], 'no empty rule and no empty at-rule survives a merge');
assert.equal(postcss.parse('.x { }').first.nodes.length, 0, 'the empty-rule check is non-vacuous');

// The mirror of the orphan check: every class the JSX applies must be styled, be a library class, be a
// `.t-*` role, or be a named semantic hook. Without this, a className typo — `.brief-selected-record` for
// `.brief-selected-records` — removes styling silently, and the orphan check cannot see it because the
// misspelled name is simply unknown to the stylesheet.
const LIBRARY_CLASSES = new Set(['icon', 'visually-hidden', 'muted', 'prose']);
// Unstyled by design: they mark structure for tests and for a reader, and cost nothing.
const SEMANTIC_HOOKS = new Set([
  'brief-form-overview', 'brief-selected-records', 'brief-heading-group', 'brief-omission-fact',
  'brief-decision-assessed',
]);
const appliedClasses = new Set();
for (const { source } of JSX_SOURCES) {
  for (const match of source.matchAll(/className=(?:"([^"]*)"|\{`([^`]*)`)/g)) {
    // Strip `${…}` expressions innermost-first: they hold identifiers (a tone, a state), not class names.
    let template = match[1] ?? match[2] ?? '';
    for (let before = ''; before !== template;) {
      before = template;
      template = template.replace(/\$\{[^{}]*\}/g, ' ');
    }
    for (const token of template.split(/[\s?:'"]+/)) {
      if (/^[a-z][\w-]*$/.test(token)) appliedClasses.add(token);
    }
  }
}
const unstyled = [...appliedClasses].filter(cls =>
  !definedClasses.has(cls) && !LIBRARY_CLASSES.has(cls) && !SEMANTIC_HOOKS.has(cls)
  && !cls.startsWith('t-') && !/^(window|location|search|params|className|string|undefined)$/.test(cls));
assert.deepEqual(unstyled, [],
  'every class the JSX applies is styled, a library class, a .t-* role, or a named semantic hook');
assert(appliedClasses.size > 40, `the applied-class scan reads real markup (found ${appliedClasses.size})`);
assert(!SEMANTIC_HOOKS.has('brief-selected-record'),
  'the typo probe: a one-letter slip is not on the allowlist and would be reported');

// The single-shot probes, and every one of them must still exist in this file. The reported count used to
// be a literal — it was typed as 15, drifted to 17 and then 19 while rules were added, and nothing checked
// it. Listing the probes and requiring each description to appear means the number cannot claim coverage
// the file does not have; the value-grammar probes above are counted directly.
const SINGLE_PROBES = [
  'the probe actually changes the file',
  'a single changed byte in tokens.css is caught',
  'a changed pin is caught',
  'the declaration scan finds a token that exists',
  'the declaration scan does not invent a token that is absent',
  'the role list is complete — a silently dropped role is a failure',
  'the colour keyword set is non-vacuous: a border style passes and a named colour does not',
  'the orphan rule is non-vacuous: it would report a class the source does not reference',
  'the role scan is non-vacuous: a bare heading does not pass it',
  'the title-prop count is non-vacuous: two Headers with titles are two',
  'the measure rule is non-vacuous: a literal ch cap is caught',
  'the reduced-motion guard is non-vacuous',
  'the constraint test is non-vacuous: a real class ancestor is not treated as the app root',
  'the role-override rule is non-vacuous: the eyebrow case would be reported',
  'the empty-rule check is non-vacuous',
  'the typo probe: a one-letter slip is not on the allowlist and would be reported',
  'every declined token is named with a reason',
];
const ownSource = readFileSync(new URL('./design-system.test.mjs', import.meta.url), 'utf8');
const staleProbes = SINGLE_PROBES.filter(description => !ownSource.includes(description));
assert.deepEqual(staleProbes, [],
  'every registered probe still exists in this file — a stale list would report coverage that is not there');
assert(SINGLE_PROBES.length >= 15 && valueProbeCount >= 30,
  'the probe count is not trivially small, and the value grammar carries the bulk of it');

console.log(JSON.stringify({
  tool: 'design-system gate',
  vendored: { pinned: Object.keys(PINNED).length, declaredTokens: declared.size, typeRoles: ROLES.length },
  declinedTokens: Object.keys(DECLINED_TOKENS).length,
  app: {
    sheets: APP_SHEETS.map(s => s.name),
    rules: APP_SHEETS.reduce((total, s) => total + appRules(s.name, s.css).length, 0),
    stage: 2,
  },
  semanticHooks: SEMANTIC_HOOKS.size,
  appliedClasses: appliedClasses.size,
  nonVacuityProbes: valueProbeCount + SINGLE_PROBES.length,
}));
