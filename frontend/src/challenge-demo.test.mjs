import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { createRoot } from 'react-dom/client';
import { JSDOM } from 'jsdom';
import { createServer } from 'vite';
import { fileURLToPath } from 'node:url';
import {
  answerCompositionText,
  CLASS_LABELS,
  chatDecisionKey,
  completionCandidate,
  decisionAlreadyRecorded,
  feedbackContextIsCurrent,
  feedbackScopeKey,
  formatDecisionTime,
  leadingUrgencyLabel,
  personNameForEncounter,
  picturePresentation,
  prefillReviewKey,
  requestedView,
  seedPrefillReviews,
  viewAddress,
  decisionOutcomeText,
  selectionReasonText,
  URGENCY_UI_LABELS,
  reviewWriteReducer,
  seedChatDecisions,
  seedProposalReviews,
  briefingOrder,
  BRIEFING_SECTION_IDS,
} from './challenge-demo.mjs';

/*
 * The previous 27 regex assertions pinned JSX/App source spelling rather than
 * behavior. Reducer transitions, identity selection, and rendered label /
 * disclosure are exercised behaviorally below. Remaining cosmetic source-shape
 * checks were dead weight; legacy-absence checks are gone.
 */

function testReviewWriteReducerReusesIdsAndBlocksDifferentBodies() {
  const scope = feedbackScopeKey('encounter-a', 'proposal-a');
  const bodyA = { proposal_id: 'proposal-a', action: 'edit', note: 'A' };
  const bodyB = { proposal_id: 'proposal-a', action: 'edit', note: 'B' };
  const keyA = JSON.stringify(bodyA);
  const initial = new Map();

  const first = reviewWriteReducer(initial, {
    type: 'submit', scope, body: bodyA, candidateEventId: 'event-1',
  });
  assert.equal(first.effect.type, 'post');
  assert.equal(first.effect.eventId, 'event-1');
  assert.equal(initial.size, 0, 'the reducer leaves its input state unchanged');

  const retryWhileInFlight = reviewWriteReducer(first.pending, {
    type: 'retry', scope, previous: { status: 'ambiguous', body: bodyA },
  });
  assert.equal(retryWhileInFlight.effect.type, 'none', 'retry cannot overlap the initial request');

  const repeated = reviewWriteReducer(first.pending, {
    type: 'submit', scope, body: bodyA, candidateEventId: 'discarded-id',
  });
  assert.equal(repeated.effect.type, 'none', 'duplicate same-body submit does not create a concurrent POST');
  assert.equal(repeated.pending, first.pending);

  const blocked = reviewWriteReducer(repeated.pending, {
    type: 'submit', scope, body: bodyB, candidateEventId: 'event-2',
  });
  assert.equal(blocked.effect.type, 'blocked', 'a different body cannot write while one is unresolved');
  assert.equal(blocked.effect.status, 'ambiguous');
  assert.deepEqual(blocked.effect.body, bodyA);
  assert.equal(blocked.effect.eventId, 'event-1');

  const settledAmbiguously = reviewWriteReducer(blocked.pending, {
    type: 'failure', scope, bodyKey: keyA, status: 503,
  });
  assert.equal(settledAmbiguously.effect.status, 'ambiguous');
  assert.equal(settledAmbiguously.pending.get(scope).inFlightBody, null);
  assert.equal(settledAmbiguously.pending.get(scope).eventIds.get(keyA), 'event-1');
  const wrongBodyRetry = reviewWriteReducer(settledAmbiguously.pending, {
    type: 'retry', scope, previous: { status: 'ambiguous', body: bodyB },
  });
  assert.equal(wrongBodyRetry.effect.type, 'none', 'only the unresolved body can retry');

  const resubmit = reviewWriteReducer(settledAmbiguously.pending, {
    type: 'submit', scope, body: bodyA, candidateEventId: 'discarded-id',
  });
  assert.equal(resubmit.effect.type, 'blocked', 'ambiguous writes require the explicit retry event');

  const retry = reviewWriteReducer(settledAmbiguously.pending, {
    type: 'retry', scope, previous: { status: 'ambiguous', body: bodyA },
  });
  assert.equal(retry.effect.type, 'post');
  assert.equal(retry.effect.eventId, 'event-1', 'retry reuses the original event ID');
  assert.equal(retry.pending.get(scope).inFlightBody, keyA);

  const duplicateRetry = reviewWriteReducer(retry.pending, {
    type: 'retry', scope, previous: { status: 'ambiguous', body: bodyA },
  });
  assert.equal(duplicateRetry.effect.type, 'none', 'retry cannot overlap an in-flight retry');

  const saved = reviewWriteReducer(retry.pending, { type: 'success', scope, bodyKey: keyA });
  assert.equal(saved.pending.has(scope), false, 'successful write clears resolved pending state');
}

function testReviewWriteReducerRetainsOnlyAmbiguousWrites() {
  const scope = feedbackScopeKey('encounter-a', 'proposal-a');
  const body = { proposal_id: 'proposal-a', action: 'accept' };
  const key = JSON.stringify(body);
  const started = reviewWriteReducer(new Map(), {
    type: 'submit', scope, body, candidateEventId: 'event-1',
  });

  const ambiguous = reviewWriteReducer(started.pending, {
    type: 'failure', scope, bodyKey: key, status: 503,
  });
  assert.equal(ambiguous.effect.status, 'ambiguous');
  assert.equal(ambiguous.pending.get(scope).ambiguousBody, key);
  assert.equal(ambiguous.pending.get(scope).eventIds.get(key), 'event-1');

  const retry = reviewWriteReducer(ambiguous.pending, {
    type: 'retry', scope, previous: { status: 'ambiguous', body },
  });
  assert.equal(retry.effect.eventId, 'event-1');

  const definite = reviewWriteReducer(retry.pending, {
    type: 'failure', scope, bodyKey: key, status: 409,
  });
  assert.equal(definite.effect.status, 'failed');
  assert.equal(definite.pending.has(scope), false, 'a definite client rejection discards the retry ID');

  const unknownTransport = reviewWriteReducer(started.pending, {
    type: 'failure', scope, bodyKey: key,
  });
  assert.equal(unknownTransport.effect.status, 'ambiguous', 'a missing response status leaves write outcome unknown');
}

testReviewWriteReducerReusesIdsAndBlocksDifferentBodies();
testReviewWriteReducerRetainsOnlyAmbiguousWrites();

// Arrow-down completes the composer only from the offered questions, and only as a prefix of what was typed.
{
  const prompts = [
    { text: 'Mihin riskiarvio perustuu?', why: '', evidence_record_ids: [] },
    { text: 'Mitä viimeisimmässä kirjauksessa sanotaan?', why: '', evidence_record_ids: [] },
    { text: 'Mihin riski liittyy?', why: '', evidence_record_ids: [] },
  ];
  assert.equal(completionCandidate(prompts, 'Mihin ri'), 'Mihin riskiarvio perustuu?',
    'a typed prefix completes to the offered question it begins');
  assert.equal(completionCandidate(prompts, 'MITÄ'), 'Mitä viimeisimmässä kirjauksessa sanotaan?',
    'the match is case-insensitive under the Finnish locale, so the capital Ä folds to ä');
  assert.equal(completionCandidate(prompts, '   '), null, 'an empty field has nothing to complete');
  assert.equal(completionCandidate(prompts, 'riskiarvio'), null,
    'a middle-of-phrase fragment is not a prefix, so there is no completion');
  assert.equal(completionCandidate(prompts, 'Mihin riskiarvio perustuu?'), null,
    'a field that already equals the offered question has nothing left to complete');
  assert.equal(completionCandidate(prompts, 'Mihin riski'), 'Mihin riskiarvio perustuu?',
    'when two offered questions share a prefix, the first in server order wins');
  assert.equal(completionCandidate({ text: 'Mihin' }, 'Mi'), null, 'a non-array prompts value returns null');
  assert.equal(completionCandidate(null, 'Mi'), null, 'a missing prompts value returns null');
  assert.equal(completionCandidate([null, { why: 'no text' }, { text: 42 }, { text: 'Mitä nyt?' }], 'Mi'),
    'Mitä nyt?', 'malformed entries are skipped without throwing, and the next valid prompt still matches');
}
assert.equal(feedbackContextIsCurrent(1, 3), false, 'an A→B→A response from the first A context stays stale');
assert.equal(feedbackContextIsCurrent(3, 3), true);

// ---- the loaded-decisions read: only the caller's own duplicate is a "decided" outcome ----
{
  // A bare 409 is NOT "you already decided this": it is also what an unavailable proposal and a stale
  // digest return. The discriminator comes from the body the write routes emit.
  assert.equal(decisionAlreadyRecorded(409, { reason: 'already_decided' }), true);
  assert.equal(decisionAlreadyRecorded(409, { detail: 'this proposal already has a decision' }), true,
    'the wording from an older server is still recognised');
  assert.equal(decisionAlreadyRecorded(409, { detail: 'this suggestion already has a decision' }), true);
  assert.equal(decisionAlreadyRecorded(409, {}), false,
    'a 409 with no recognised reason is a failure, not the caller\'s own earlier decision');
  assert.equal(decisionAlreadyRecorded(409, { detail: 'proposal is no longer available' }), false);
  assert.equal(decisionAlreadyRecorded(409, { detail: 'the encounter changed since this conversation started' }), false);
  assert.equal(decisionAlreadyRecorded(400, { reason: 'already_decided' }), false, 'only a 409 can mean this');

  const scope = feedbackScopeKey('encounter-a', 'proposal-a');
  const body = { proposal_id: 'proposal-a', action: 'accept' };
  const key = JSON.stringify(body);
  const started = reviewWriteReducer(new Map(), { type: 'submit', scope, body, candidateEventId: 'event-1' });
  const decided = reviewWriteReducer(started.pending, {
    type: 'failure', scope, bodyKey: key, status: 409, alreadyDecided: true,
  });
  assert.equal(decided.effect.status, 'decided',
    'a 409 that names the caller\'s own decision has its own outcome, not the generic failure');
  assert.equal(decided.pending.has(scope), false,
    'the caller already decided, so the idempotency key is discarded like any definite outcome');
  // A 5xx is unresolved whatever its body says: the write may or may not have landed, so it stays retryable.
  const unresolved = reviewWriteReducer(started.pending, {
    type: 'failure', scope, bodyKey: key, status: 503, alreadyDecided: true,
  });
  assert.equal(unresolved.effect.status, 'ambiguous');
}

// ---- seeding the two rails from the caller's own decisions ----
{
  const payload = {
    encounter_id: 'encounter-a',
    proposals: [{ proposal_id: 'proposal-a', action: 'accept', decided_at: '2026-10-11T09:00:00Z' }],
    chat_suggestions: [{ turn_id: 'turn-1', suggestion_id: 's0', action: 'dismiss', decided_at: '2026-10-11T09:05:00Z' }],
  };
  const seeded = seedProposalReviews({}, payload);
  assert.deepEqual(seeded['proposal-a'], {
    status: 'saved', action: 'accept', recordedAt: '2026-10-11T09:00:00Z',
  });
  // A live attempt is newer than the load: a write in flight or left ambiguous must not be overwritten.
  const inFlight = { status: 'saving', body: { proposal_id: 'proposal-a' }, eventId: 'event-1' };
  assert.equal(seedProposalReviews({ 'proposal-a': inFlight }, payload)['proposal-a'], inFlight);

  const chatSeeded = seedChatDecisions(payload);
  assert.deepEqual(Object.keys(chatSeeded), [chatDecisionKey('turn-1', 's0')]);
  assert.equal(chatSeeded['turn-1:s0'].status, 'saved');
  assert.equal(chatSeeded['turn-1:s0'].action, 'dismiss');
  // A seeded decision is keyed by its own turn, so it cannot mark another turn's `s0` as decided.
  assert.equal(chatSeeded[chatDecisionKey('turn-2', 's0')], undefined);

  // A malformed or partial payload seeds nothing rather than throwing: the read is an enhancement.
  assert.deepEqual(seedProposalReviews({}, undefined), {});
  assert.deepEqual(seedProposalReviews({}, { proposals: [{ action: 'accept' }] }), {});
  assert.deepEqual(seedChatDecisions({ chat_suggestions: [{ suggestion_id: 's0' }] }), {});

  // The rendered time is the server's own instant, in the app's Finnish time role.
  assert.equal(formatDecisionTime(''), '');
  assert.match(formatDecisionTime('2026-10-11T09:00:00Z'), /2026/, 'the decided time carries its year');
}

// ---------- the chat rail's own harness ----------
//
// The chat rail is a fetched, stateful surface: its prompts, turns and review writes exist only after
// effects and clicks, which is why a `renderToStaticMarkup` of it audited an empty form. This harness
// installs a fetch double and the browser globals React needs, so the reset, the turn-scoped decisions and
// the idempotent review write can be exercised for real.
function chatJson(body, status = 200) {
  return { ok: status >= 200 && status < 300, status, json: async () => body };
}

function chatTurn(turnId, text, extra = {}) {
  return {
    turn_id: turnId, digest_id: 'digest-1', seq: 1, asked_at: '2026-10-11T00:00:00Z', outcome: 'answer',
    suggestions: [{ suggestion_id: 's0', turn_id: turnId, ordinal: 0, statement_class: 'source_fact',
      text, label: 'Lähde', source_record_ids: ['source-a'] }],
    ...extra,
  };
}

function chatAnswer(turn, extra = {}) {
  return chatJson({ turn, digest: { digest_id: turn.digest_id }, ...extra });
}

// The rail is asked through an offered prompt (see `ask` below), so every harness needs one.
const CHAT_PROMPTS = { prompts: [{ text: 'Mikä on tilanne?', why: 'kirjaus on mukana', evidence_record_ids: [] }],
  guideline_links: [] };

function chatApi({ promptsFor = () => ({ prompts: [], guideline_links: [] }), answers = [],
  reviewFor = () => chatJson({ label: 'Tallennettu palveluun' }),
  decisionsFor = () => ({ encounter_id: 'enc-a', proposals: [], chat_suggestions: [] }) } = {}) {
  const calls = { ask: [], reviews: [], decisions: [] };
  const fetchMock = async (input, init = {}) => {
    const url = String(input);
    const payload = init.body ? JSON.parse(init.body) : null;
    const promptMatch = url.match(/\/api\/encounters\/([^/]+)\/chat\/prompts$/);
    if (promptMatch) return chatJson(promptsFor(promptMatch[1]));
    // The caller's own loaded decisions, which the rail seeds from so a reload does not offer a decision
    // that already exists.
    const decisionMatch = url.match(/\/api\/encounters\/([^/]+)\/decisions$/);
    if (decisionMatch) {
      calls.decisions.push(decisionMatch[1]);
      return chatJson(decisionsFor(decisionMatch[1]));
    }
    if (url.endsWith('/api/chat/reviews')) {
      calls.reviews.push(payload);
      return reviewFor(payload, calls.reviews.length - 1);
    }
    // The rail asks the streaming assistant route; a scripted JSON answer (no event-stream content type) takes the
    // rail's JSON path, so these tests exercise the same turn shape the stream's final event carries.
    if (url.endsWith('/chat') || url.endsWith('/chat/stream')) {
      calls.ask.push(payload);
      return answers.shift() ?? chatJson({ detail: 'no scripted answer' }, 500);
    }
    throw new Error(`Unexpected request: ${url}`);
  };
  return { fetchMock, calls };
}

// The suggestion card is the button: it reads mark + short title + the question (the reference panel's card
// anatomy), so its textContent is no longer the bare question. The card is identified by the question it
// asks, which is what the professional sees and clicks, and that is what this helper matches on.
const chatButton = (document, label) =>
  [...document.querySelectorAll('.brief-data-section button')]
    .find(button => button.textContent.includes(label));

async function withChatPanel(fetchMock, body) {
  const dom = new JSDOM('<!doctype html><div id="root"></div>', { url: 'http://localhost' });
  const keys = ['window', 'document', 'HTMLElement', 'navigator', 'IS_REACT_ACT_ENVIRONMENT', 'fetch', 'ResizeObserver'];
  const previous = new Map(keys.map(key => [key, Object.getOwnPropertyDescriptor(globalThis, key)]));
  for (const [key, value] of Object.entries({
    window: dom.window, document: dom.window.document, HTMLElement: dom.window.HTMLElement,
    navigator: dom.window.navigator, IS_REACT_ACT_ENVIRONMENT: true,
    // jsdom has no ResizeObserver; the library AppShell measures its bottom bar with one in a layout effect.
    ResizeObserver: class { observe() {} disconnect() {} unobserve() {} },
  })) Object.defineProperty(globalThis, key, { configurable: true, writable: true, value });
  Object.defineProperty(globalThis, 'fetch', { configurable: true, writable: true, value: fetchMock });
  dom.window.fetch = fetchMock;
  // React's input-event polyfill (IE only) calls `attachEvent` on a focused text field; jsdom has no such
  // method, so focusing an editor would throw. The polyfill is not what these tests exercise, so it is a no-op.
  dom.window.HTMLElement.prototype.attachEvent = () => {};
  dom.window.HTMLElement.prototype.detachEvent = () => {};
  const root = createRoot(dom.window.document.getElementById('root'));
  const tick = () => new Promise(resolve => setTimeout(resolve, 0));
  const render = async props => {
    await React.act(async () => { root.render(React.createElement(ChatPanel, props)); await tick(); });
  };
  const click = async node => {
    assert(node, 'the control the test clicks exists');
    await React.act(async () => {
      node.dispatchEvent(new dom.window.MouseEvent('click', { bubbles: true }));
      await tick();
    });
  };
  // Asking happens through the rail's own controls. A question is set by clicking an offered card while the
  // panel is empty — React 19's text-input onChange is not reached by a synthesised `input` event in this
  // jsdom setup, so a click on the card exercises the same `setQuestion` path a professional uses. Once a
  // turn exists the cards are off the screen (the answered panel is a conversation: reply, then composer),
  // so a later `ask()` submits the question still in the composer's field.
  const submit = async () => {
    // jsdom does not run a submit button's implicit form submission through React's delegated listener, so
    // the form's own `submit` event is dispatched; it reaches the same handler the button would.
    await React.act(async () => {
      dom.window.document.querySelector('.brief-data-section form')
        .dispatchEvent(new dom.window.Event('submit', { bubbles: true, cancelable: true }));
      await tick();
    });
  };
  // Typing is reported to React through the field's own change handler (the one React attaches as a prop).
  // A synthesised `input` event does not reach React's controlled-field state in this jsdom setup, and the
  // composer's value then stays empty when the form is submitted.
  const typeQuestion = async text => {
    const input = dom.window.document.querySelector('.brief-chat-ask input');
    assert(input, 'the composer field exists');
    const propsKey = Object.keys(input).find(key => key.startsWith('__reactProps$'));
    assert(propsKey, 'the composer field is a React-controlled input');
    await React.act(async () => {
      input[propsKey].onChange({ target: { value: text }, currentTarget: { value: text } });
      await tick();
    });
  };
  // A suggestion click asks by itself (one click, one request), so a card is clicked and nothing else is sent.
  // Only when no card is on screen (a turn exists) is the composer typed into and submitted.
  const ask = async ({ text = '' } = {}) => {
    const card = text
      ? chatButton(dom.window.document, text)
      : dom.window.document.querySelector('.brief-chat-suggestion');
    if (card) { await click(card); return; }
    if (text) await typeQuestion(text);
    await submit();
  };
  try {
    await render({ encounterId: 'enc-a', api: '' });
    await body({ dom, root, render, click, ask, typeQuestion, document: dom.window.document });
  } finally {
    await React.act(async () => root.unmount());
    dom.window.close();
    for (const key of keys) {
      const descriptor = previous.get(key);
      if (descriptor) Object.defineProperty(globalThis, key, descriptor);
      else delete globalThis[key];
    }
  }
}

// The same browser-globals harness, but mounted on the App shell so the briefing's own decisions load can
// be exercised: the rail must render a proposal the caller already decided as decided, from the separate
// decisions route, without a click.
// `phone` installs a `matchMedia` that answers true for the shell's phone query (jsdom has none, so the
// default render is the desktop header); `url` is the address the shell boots from.
async function withBriefingApp(fetchMock, body, { phone = false, url = 'http://localhost/?encounter=enc-decided' } = {}) {
  // `pretendToBeVisual` gives the window `requestAnimationFrame`, which the library Tooltip schedules its
  // placement on while it is shown — and it is shown whenever an IconButton takes focus (the assistant pane
  // hands focus back to its toggle).
  const dom = new JSDOM('<!doctype html><div id="root"></div>', { url, pretendToBeVisual: true });
  if (phone) {
    dom.window.matchMedia = query => ({ matches: query === '(max-width: 639px)', media: query,
      addEventListener() {}, removeEventListener() {} });
  }
  const keys = ['window', 'document', 'HTMLElement', 'navigator', 'IS_REACT_ACT_ENVIRONMENT', 'fetch', 'ResizeObserver',
    'getComputedStyle', 'requestAnimationFrame', 'cancelAnimationFrame'];
  const previous = new Map(keys.map(key => [key, Object.getOwnPropertyDescriptor(globalThis, key)]));
  for (const [key, value] of Object.entries({
    window: dom.window, document: dom.window.document, HTMLElement: dom.window.HTMLElement,
    navigator: dom.window.navigator, IS_REACT_ACT_ENVIRONMENT: true,
    // jsdom has no ResizeObserver; the library AppShell measures its bottom bar with one in a layout effect.
    ResizeObserver: class { observe() {} disconnect() {} unobserve() {} },
    // The library Tooltip places itself with the bare globals, not `window.`-qualified ones.
    getComputedStyle: dom.window.getComputedStyle.bind(dom.window),
    requestAnimationFrame: dom.window.requestAnimationFrame.bind(dom.window),
    cancelAnimationFrame: dom.window.cancelAnimationFrame.bind(dom.window),
  })) Object.defineProperty(globalThis, key, { configurable: true, writable: true, value });
  Object.defineProperty(globalThis, 'fetch', { configurable: true, writable: true, value: fetchMock });
  dom.window.fetch = fetchMock;
  const root = createRoot(dom.window.document.getElementById('root'));
  const tick = () => new Promise(resolve => setTimeout(resolve, 0));
  try {
    await React.act(async () => {
      root.render(React.createElement(App));
      // The shell makes several chained fetches (catalogue, picture, decisions, chat prompts); a handful of
      // macrotask turns lets every one of them settle before the assertions run.
      for (let turn = 0; turn < 6; turn += 1) await tick();
    });
    await body({ dom, document: dom.window.document });
  } finally {
    await React.act(async () => root.unmount());
    dom.window.close();
    for (const key of keys) {
      const descriptor = previous.get(key);
      if (descriptor) Object.defineProperty(globalThis, key, descriptor);
      else delete globalThis[key];
    }
  }
}


const vite = await createServer({
  root: fileURLToPath(new URL('..', import.meta.url)),
  server: { middlewareMode: true, hmr: false },
  appType: 'custom',
});
let PictureBriefing;
let TechnicalView;
let LENS_SCOPE_NOTE;
let ILLUSTRATIVE_NOTE;
let ILLUSTRATIVE_THRESHOLD;
let ChatPanel;
let ChatRail;
let App;
let DayView;
try {
  ({ default: App } = await vite.ssrLoadModule('/src/App.jsx'));
  ({ DayView } = await vite.ssrLoadModule('/src/DayView.jsx'));
  ({ PictureBriefing, LENS_SCOPE_NOTE, ILLUSTRATIVE_NOTE, ILLUSTRATIVE_THRESHOLD } = await vite.ssrLoadModule('/src/ChallengeDemo.jsx'));
  ({ TechnicalView } = await vite.ssrLoadModule('/src/TechnicalView.jsx'));
  ({ ChatPanel, ChatRail } = await vite.ssrLoadModule('/src/ChatPanel.jsx'));
  const markup = renderToStaticMarkup(React.createElement(App));
  // The shell is the library's own: `Sidebar` owns the rail and its navigation landmark, `Header` owns the
  // page title and the toolbar. Asserted structurally rather than by the app's former hand-built classes.
  assert.match(markup, /aria-label="Päänavigaatio"/, 'the navigation landmark comes from the library Sidebar');
  assert.match(markup, /<header class="_header_[^"]*"><div class="_titleBlock_[^"]*">/,
    'the app shell renders the library Header with its title block');
  assert.match(markup, /<h1 class="_title_[a-z0-9]+_\d+ t-page-title">Tilannekuva<\/h1>/,
    'the Header owns the page h1 on the briefing route');
  assert.match(markup, /<div class="brief-sidebar"[^>]*><div class="_sidebar_[a-z0-9]+_\d+">/, 'the rail is the library Sidebar');
  assert.match(markup, /_navItem_[a-z0-9]+_\d+ _sidebar_[a-z0-9]+_\d+/, 'the destinations are library NavItems');
  assert.match(markup, /LUVN/);
  assert.match(markup, /aria-label="Hae palvelutilannetta"/,
    'the search field keeps its Finnish accessible name even though the visible label is gone');
  assert.match(markup, /<span role="search"><label class="_search_[a-z0-9]+_\d+"/,
    'the search field sits in a search landmark of its own');
  assert(!markup.includes('data-preset') && !markup.includes('data-style'),
    'the seven-palette skin attributes are gone: one palette, light and dark only');
  assert(!markup.includes('Väripaletti'), 'the palette chooser is gone with the palettes');
  assert(!markup.includes('data-view=') && !markup.includes('brief-disclosure'), 'only the professional record shell is rendered');

  const dom = new JSDOM('<!doctype html><div id="root"></div>', { url: 'http://localhost' });
  const keys = ['window', 'document', 'HTMLElement', 'navigator', 'IS_REACT_ACT_ENVIRONMENT', 'fetch', 'ResizeObserver'];
  const previous = new Map(keys.map(key => [key, Object.getOwnPropertyDescriptor(globalThis, key)]));
  for (const [key, value] of Object.entries({
    window: dom.window, document: dom.window.document, HTMLElement: dom.window.HTMLElement,
    navigator: dom.window.navigator, IS_REACT_ACT_ENVIRONMENT: true,
    // jsdom has no ResizeObserver; the library AppShell measures its bottom bar with one in a layout effect.
    ResizeObserver: class { observe() {} disconnect() {} unobserve() {} },
  })) Object.defineProperty(globalThis, key, { configurable: true, writable: true, value });
  const requests = [];
  const response = body => ({ ok: true, status: 200, json: async () => body });
  const encounters = [
    { encounter_id: 'nurse', example_label: 'Aino Virtanen', demo_name: 'Aino', picker_label: 'Mielialan arviointi · Sairaanhoitaja', role_label: 'Sairaanhoitaja', purpose_label: 'Mielialan arviointi', role: 'sairaanhoitaja' },
    { encounter_id: 'doctor', example_label: 'Aino Virtanen', demo_name: 'Aino', picker_label: 'Mielialan arviointi · Lääkäri', role_label: 'Lääkäri', purpose_label: 'Mielialan arviointi', role: 'laakari' },
  ];
  const pictureFor = (encounterId, recordId, title) => ({
    encounter_id: encounterId, role: encounterId === 'nurse' ? 'sairaanhoitaja' : 'laakari',
    purpose: 'mental_health_assessment', identity_header: 'Henkilöllisyystieto ei saatavilla', situation: null, risk: null,
    actions: [], trace: [], grounded: false, citations: [], assessments: [], conflicts: [], proposals: [],
    grounding: { dropped_citations: [], dropped_actions: [], rejected_sentences: [] },
    selection: { baseline_record_ids: [recordId], additional_record_ids: [], emphasized_record_ids: [],
      decision_notes: [], decision_candidate_count: 0, decision_assessed_count: 0, decision_not_shortlisted_count: 0,
      decision_budget_exhausted_count: 0, decision_unavailable_count: 0, decision_gate_refused_count: 0, rule_version: 'test' },
    records: { priority: [], accessible_other: [{ record_id: recordId, record_type: 'vapaateksti',
      topic: 'mielenterveys', source: 'synthetic', recorded_at: '2026-10-01T00:00:00Z',
      selection_reasons: [], payload: { otsikko: title, teksti: 'Oman näkökulman kirjaus.' } }] },
    narration_generation_status: 'unavailable', narration_support_status: 'not_run',
    narration_acceptance_status: 'rejected',
  });
  const pictures = {
    nurse: pictureFor('nurse', 'nurse-record', 'Sairaanhoitajan oma lähde'),
    doctor: pictureFor('doctor', 'doctor-record', 'Lääkärin oma lähde'),
    far: pictureFor('far', 'far-record', 'Sivun ulkopuolinen oma lähde'),
  };
  // One encounter the catalogue page does not carry but the single-encounter route answers for: the shape a
  // deep link, a day row or a search hit beyond the first page resolves.
  const farRow = { encounter_id: 'far', example_label: 'SYN-ASI-9', picker_label: 'Lääkityksen seuranta · Sairaanhoitaja',
    role_label: 'Sairaanhoitaja', purpose_label: 'Lääkityksen seuranta', role: 'sairaanhoitaja' };
  // The catalogue endpoint answers a page plus whole-catalogue counts, and the app makes two kinds of
  // request against it: the shell's bootstrap (no `role`) and the worklist's own server-scoped page
  // (`role`, `q`, `limit`, `offset`). The mock honours the query the way the endpoint does, so a test
  // that asserts "the doctor encounter is not in the nurse worklist" is exercising the real contract
  // rather than a client-side filter.
  const summary = rows => ({
    encounters: rows.length,
    people: new Set(rows.map(row => row.example_label)).size,
    purposes: new Set(rows.map(row => row.purpose_label)).size,
  });
  const catalogueResponse = params => {
    const role = params.get('role');
    const q = (params.get('q') ?? '').toLocaleLowerCase('fi');
    const limit = Number(params.get('limit') ?? 50);
    const offset = Number(params.get('offset') ?? 0);
    const matching = encounters.filter(row => (!role || row.role === role)
      && (!q || [row.example_label, row.purpose_label, row.picker_label]
        .some(value => (value ?? '').toLocaleLowerCase('fi').includes(q))));
    const page = matching.slice(offset, offset + limit);
    return {
      encounters: page, total: matching.length, count: page.length, limit, offset,
      query: params.get('q') ?? '', role,
      aggregate: {
        catalogue: { ...summary(encounters), roles: new Set(encounters.map(row => row.role)).size },
        roster: [...new Map(encounters.map(row => [row.role, { role: row.role, label: row.role_label }])).values()],
        matching: summary(matching),
      },
    };
  };
  const fetchMock = async input => {
    const url = String(input);
    requests.push(url);
    const [path, search] = url.split('?');
    if (path.endsWith('/api/encounters')) return response(catalogueResponse(new URLSearchParams(search ?? '')));
    if (path.endsWith('/api/encounters/far')) return response(farRow);
    // The presenter's mode switch sets the picture route's own `mode`, so the mock has to accept it;
    // otherwise the control would look broken in this harness for a reason that is the harness's fault.
    const match = url.match(/\/api\/encounters\/([^/]+)\/picture(?:\?mode=(decision-off|foundry-off))?$/);
    if (match && pictures[match[1]]) return response(pictures[match[1]]);
    throw new Error(`Unexpected request: ${url}`);
  };
  Object.defineProperty(globalThis, 'fetch', { configurable: true, writable: true, value: fetchMock });
  dom.window.fetch = fetchMock;
  const root = createRoot(dom.window.document.getElementById('root'));
  try {
    await React.act(async () => {
      root.render(React.createElement(App));
      await new Promise(resolve => setTimeout(resolve, 0));
    });
    // A bare address lands on Työpäivä with the default case loaded behind it; the briefing is one click away.
    assert.equal(dom.window.document.querySelector('header h1').textContent, 'Työpäivä', 'a bare address lands on the day');
    await React.act(async () => {
      [...dom.window.document.querySelectorAll('.brief-sidebar nav[aria-label="Päänavigaatio"] a')]
        .find(link => link.textContent.includes('Tilannekuva'))
        .dispatchEvent(new dom.window.MouseEvent('click', { bubbles: true }));
      await new Promise(resolve => setTimeout(resolve, 0));
    });
    let visible = dom.window.document.body.textContent;
    assert(visible.includes(encounters[1].picker_label), 'sibling label comes from loaded encounter metadata');
    assert(visible.includes('Sairaanhoitajan oma lähde'), 'the selected lens renders its own source');
    assert(visible.includes('Henkilöllisyystieto ei saatavilla'), 'unavailable patient identity uses the neutral state');
    assert(!visible.includes('Aino'), 'an invented demo name is never presented as patient identity');
    assert(!visible.includes('Lääkärin oma lähde'), 'sibling source data is not present in the nurse view');
    assert(!visible.includes('doctor-record'), 'the sibling record ID is not present in the nurse view');
    assert(!requests.some(url => url.includes('/doctor/picture')), 'comparison metadata causes no sibling Picture request');

    // ---- Address truthfulness on the bare load (review findings 1 and 2) ----
    // A bare '/' opens the default case without naming it, and no lens has been chosen, so the picker's
    // `roleChoice` is still empty while the role on screen is the one inferred from the case. An appearance or
    // presenter toggle must write the lens that is on screen, or a reload drops it.
    const addressParams = () => new URLSearchParams(dom.window.location.search);
    await React.act(async () => {
      dom.window.document.querySelector('.brief-sidebar button[aria-label="Vaihda tummaan teemaan"]')
        .dispatchEvent(new dom.window.MouseEvent('click', { bubbles: true }));
    });
    assert.equal(addressParams().get('theme'), 'dark', 'the theme toggle is written to the address');
    assert.equal(addressParams().get('role'), 'sairaanhoitaja',
      'after a theme toggle the address names the effective role on screen, not the empty picker choice');
    assert.equal(addressParams().get('encounter'), encounters[0].encounter_id,
      'after a theme toggle the address still names the open encounter');
    await React.act(async () => {
      dom.window.document.querySelector('.brief-sidebar button[aria-label="Vaihda vaaleaan teemaan"]')
        .dispatchEvent(new dom.window.MouseEvent('click', { bubbles: true }));
    });
    assert.equal(addressParams().get('theme'), null, 'toggling back to light removes the theme parameter');
    assert.equal(addressParams().get('role'), 'sairaanhoitaja', 'toggling back keeps the effective role');
    assert.equal(addressParams().get('encounter'), encounters[0].encounter_id, 'toggling back keeps the encounter');
    // The mode switch is on the technical page (B2), a destination of its own: open it from the briefing's
    // footer link, switch, and the address carries the page as well as the mode, so a reload lands back on
    // the technical page with the same recomputation.
    assert.equal(dom.window.document.querySelector('#presenter-tools'), null,
      'the presenter tools are not on the briefing');
    await React.act(async () => {
      const footerLink = [...dom.window.document.querySelectorAll('.brief-footer a')]
        .find(link => link.textContent === 'Tekniset tiedot');
      assert(footerLink, 'the briefing ends with a link to the technical page');
      footerLink.dispatchEvent(new dom.window.MouseEvent('click', { bubbles: true, cancelable: true }));
      await new Promise(resolve => setTimeout(resolve, 0));
    });
    assert.equal(addressParams().get('page'), 'technical', 'the footer link pushes the technical page\'s address');
    assert.equal(dom.window.document.querySelector('header h1').textContent, 'Tekniset tiedot',
      'the shell Header titles the technical page');
    await React.act(async () => {
      dom.window.document.querySelector('#presenter-tools input[value="decision-off"]').click();
      await new Promise(resolve => setTimeout(resolve, 0));
    });
    assert.equal(addressParams().get('mode'), 'decision-off', 'the mode switch is written to the address');
    assert.equal(addressParams().get('page'), 'technical', 'the mode switch keeps the address on the technical page');
    assert.equal(addressParams().get('role'), 'sairaanhoitaja',
      'after a mode switch the address names the effective role on screen, not the empty picker choice');
    assert.equal(addressParams().get('encounter'), encounters[0].encounter_id,
      'after a mode switch the address still names the open encounter');
    await React.act(async () => {
      dom.window.document.querySelector('#presenter-tools input[value="normal"]').click();
      await new Promise(resolve => setTimeout(resolve, 0));
    });
    assert.equal(addressParams().get('mode'), null, 'returning to normal removes the mode parameter');
    // Back to the briefing through the rail: the address drops `page`, so the two agree again.
    await React.act(async () => {
      [...dom.window.document.querySelectorAll('.brief-sidebar nav[aria-label="Päänavigaatio"] a')]
        .find(link => link.textContent.includes('Tilannekuva'))
        .dispatchEvent(new dom.window.MouseEvent('click', { bubbles: true }));
      await new Promise(resolve => setTimeout(resolve, 0));
    });
    assert.equal(addressParams().get('page'), null, 'leaving the technical page removes the page parameter');
    assert.equal(addressParams().get('encounter'), encounters[0].encounter_id,
      'and the address still names the open encounter');

    // The destinations are the library's NavItems, which are links, not buttons (issue 02).
    const rail = dom.window.document.querySelector('.brief-sidebar nav[aria-label="Päänavigaatio"]');
    // Work order E: the professional's path is two destinations. The rail's primary landmark and the
    // bottom bar both carry exactly those two, in order; the utility group sits in its own landmark with a
    // different name, and no bottom-bar item is a utility destination.
    const labelsOf = root => [...root.querySelectorAll('a')].map(link => link.textContent.trim());
    const utilityRail = dom.window.document.querySelector('.brief-sidebar nav[aria-label="Työkalut"]');
    assert.deepEqual(labelsOf(rail), ['Työpäivä', 'Tilannekuva'],
      'the rail\'s primary landmark holds exactly the two professional destinations, in order');
    assert(utilityRail, 'the utility destinations have their own navigation landmark in the rail');
    assert.deepEqual(labelsOf(utilityRail), ['Tekniset tiedot'],
      'the utility landmark holds exactly the utility destinations');
    const bottomBar = dom.window.document.querySelector('nav[aria-label="Pikavalikko"]');
    assert(bottomBar, 'the phone bottom bar is the library BottomNav, named for its surface');
    assert.deepEqual(labelsOf(bottomBar), ['Työpäivä', 'Tilannekuva'],
      'the bottom bar contains exactly the two primary destinations, in order');
    const currentIn = root => [...root.querySelectorAll('a')]
      .filter(link => link.getAttribute('aria-current') === 'page').map(link => link.textContent.trim());
    assert.deepEqual(currentIn(bottomBar), ['Tilannekuva'],
      'the bottom bar marks the current destination with aria-current="page"');
    assert.deepEqual(currentIn(rail), ['Tilannekuva'],
      'the rail marks the same current destination');
    assert(!bottomBar.textContent.includes('Työpöytä') && !bottomBar.textContent.includes('Tekniset tiedot'),
      'neither the dashboard nor the technical destination is in the bottom bar');
    const caseNavigation = [...rail.querySelectorAll('a')]
      .find(link => link.textContent.includes('Työpäivä'));
    assert(caseNavigation, 'case selection has a separate navigation destination');
    assert.equal(caseNavigation.getAttribute('aria-current'), null,
      'the briefing is current while the worklist link is not');
    await React.act(async () => {
      caseNavigation.dispatchEvent(new dom.window.MouseEvent('click', { bubbles: true }));
    });
    assert.equal(caseNavigation.getAttribute('aria-current'), 'page',
      'the current destination is marked on the NavItem that owns it');
    // Rooli is a first-class axis. The worklist is scoped to the role the professional is looking
    // through, so the nurse's page carries the nurse encounter and never the doctor's — and the role
    // picker lists the roles the returned catalogue actually carries.
    // The worklist panel's heading is an h2: the page's only h1 is the library Header's, so a worklist
    // route does not carry two of them (measured in the browser, not visible to the SSR a11y render).
    // The panel heading says what the panel lists, not where you are: the shell title is already
    // "Oma työlista" and the breadcrumb ends with it, so the panel naming the same words at the same size
    // made one destination read as three headings.
    assert(dom.window.document.querySelector('#brief-case-picker-heading').textContent.includes('Palvelutilanteet'),
      'the worklist panel is headed, and its heading says something the page title does not');
    assert.equal(dom.window.document.querySelector('#brief-case-picker-heading').tagName, 'H2',
      'the worklist heading is a section heading under the Header h1');
    assert.equal(dom.window.document.querySelectorAll('h1').length, 1,
      'a worklist route renders exactly one h1');
    assert(dom.window.document.querySelector('header h1').textContent === 'Työpäivä',
      'the Header owns that h1 and titles the destination');
    assert(dom.window.document.body.textContent.includes('Sairaanhoitaja'),
      'the active role is stated on the worklist');
    assert(!dom.window.document.querySelector('.brief-case-picker').textContent.includes(encounters[1].picker_label),
      'another role\'s encounter is not offered on the nurse worklist');
    const roleOptions = [...dom.window.document.querySelectorAll('#brief-role-picker option')].map(option => option.value);
    assert.deepEqual(roleOptions, ['sairaanhoitaja', 'laakari'],
      'the picker offers exactly the roles the catalogue returned');
    const worklistRows = () => [...dom.window.document.querySelectorAll('.brief-case-picker button')]
      .map(button => button.textContent);
    const nurseRows = worklistRows();
    assert.equal(nurseRows.length, 1, 'the nurse worklist carries the one nurse encounter');
    // The scoping is the SERVER's: the worklist asks the catalogue endpoint for one role's page rather
    // than filtering the whole catalogue in the browser, which is what makes the worklist correct at a
    // catalogue size the browser never holds in full.
    assert(requests.some(url => url.includes('/api/encounters?')
      && new URLSearchParams(url.split('?')[1]).get('role') === 'sairaanhoitaja'),
    'the nurse worklist is a role-scoped request to the catalogue endpoint');
    assert(requests.some(url => {
      const params = new URLSearchParams(url.split('?')[1] ?? '');
      return url.includes('/api/encounters?') && params.has('limit') && params.has('offset');
    }), 'the worklist page is requested with an explicit limit and offset');
    // Review finding 1: choosing the case that is already open must still name it in the address. A bare '/'
    // shows the default case without naming it; the explicit choice has to be recorded, or a reload re-derives
    // a default instead of the case the professional picked. The address is set back to '/' here to put the
    // page in exactly that state, then the row for the open case is clicked.
    dom.window.history.replaceState(null, '', '/');
    assert.equal(dom.window.location.search, '', 'the address is bare before the row is chosen');
    await React.act(async () => {
      const openRow = [...dom.window.document.querySelectorAll('.brief-case-picker button')]
        .find(button => button.textContent.includes(encounters[0].picker_label));
      assert(openRow, 'the open case is offered on its own worklist');
      openRow.dispatchEvent(new dom.window.MouseEvent('click', { bubbles: true }));
      await new Promise(resolve => setTimeout(resolve, 0));
    });
    assert.equal(new URLSearchParams(dom.window.location.search).get('encounter'), encounters[0].encounter_id,
      'choosing the case that is already open names it in the address, so a reload opens that case');
    assert.equal(new URLSearchParams(dom.window.location.search).get('role'), 'sairaanhoitaja',
      'choosing a case writes the role of that case');
    await React.act(async () => {
      [...rail.querySelectorAll('a')].find(link => link.textContent.includes('Työpäivä'))
        .dispatchEvent(new dom.window.MouseEvent('click', { bubbles: true }));
    });
    await React.act(async () => {
      const rolePicker = dom.window.document.querySelector('#brief-role-picker');
      rolePicker.value = 'laakari';
      rolePicker.dispatchEvent(new dom.window.Event('change', { bubbles: true }));
    });
    const doctorRows = worklistRows();
    assert.equal(doctorRows.length, 1, 'the doctor worklist carries the one doctor encounter');
    assert(requests.some(url => url.includes('/api/encounters?')
      && new URLSearchParams(url.split('?')[1]).get('role') === 'laakari'),
    'changing the lens re-requests the catalogue scoped to the new role rather than re-filtering a list');
    assert(doctorRows[0].includes(encounters[1].picker_label), 'the doctor worklist offers the doctor lens');
    assert(!doctorRows[0].includes(encounters[0].picker_label), 'the nurse encounter left the doctor worklist');
    assert.equal(new URLSearchParams(dom.window.location.search).get('role'), 'laakari',
      'the chosen role is addressable, so a worklist state can be captured');
    assert(!new URLSearchParams(dom.window.location.search).has('encounter'),
      'choosing a role clears the addressed encounter rather than showing another role\'s case');
    await React.act(async () => {
      const rolePicker = dom.window.document.querySelector('#brief-role-picker');
      rolePicker.value = 'sairaanhoitaja';
      rolePicker.dispatchEvent(new dom.window.Event('change', { bubbles: true }));
    });
    const nurseOnlyRow = worklistRows();
    assert.equal(nurseOnlyRow.length, 1, 'the nurse worklist is restored when the role changes back');
    assert(nurseOnlyRow[0].includes(encounters[0].picker_label), 'the nurse lens is the row on offer');
    assert(dom.window.document.querySelector('.brief-case-group h2').textContent === 'Aino Virtanen',
      'picker labels are unmistakably marked as examples');
    assert(!dom.window.document.body.textContent.includes('demo-key-002'),
      'picker never displays the internal demo key');
    // Selecting a row is still "open this encounter", and the other role's encounter is not reachable by
    // clicking a worklist that does not carry it.
    const doctorPicker = [...dom.window.document.querySelectorAll('.brief-case-picker button')]
      .find(button => button.textContent.includes(encounters[1].picker_label));
    assert.equal(doctorPicker, undefined, 'the doctor encounter is not clickable from the nurse worklist');
    await React.act(async () => {
      const target = [...dom.window.document.querySelectorAll('.brief-case-picker button')]
        .find(button => button.textContent.includes(encounters[0].picker_label));
      target.dispatchEvent(new dom.window.MouseEvent('click', { bubbles: true }));
      await new Promise(resolve => setTimeout(resolve, 0));
    });
    assert.equal(new URLSearchParams(dom.window.location.search).get('encounter'), encounters[0].encounter_id,
      'after clicking a worklist row the address carries that encounter\'s id, so a reload reopens it');
    assert(visible.includes('Sairaanhoitajan oma lähde'), 'the nurse lens is still the one that opens');
    assert(!requests.some(url => url.includes('/doctor/picture')),
      'no role\'s worklist can request another role\'s Picture');
    assert(!dom.window.document.body.textContent.includes('Lääkärin oma lähde'),
      'the other role\'s source data never enters the nurse view through the worklist');
    const pictureRequestsBeforePreset = requests.filter(url => url.includes('/doctor/picture')).length;
    // "Tekniset tiedot" is a destination (B2): the click sets it, pushes `page=technical`, renders the
    // technical page in place of the briefing and marks the link current. Nothing scrolls and nothing opens.
    const technicalNav = [...utilityRail.querySelectorAll('a')]
      .find(link => link.textContent.includes('Tekniset tiedot'));
    assert(technicalNav, 'the technical destination is in the utility landmark of the rail');
    assert.equal(technicalNav.getAttribute('href'), '#brief-content',
      'the technical link promises the content landmark, which is where the page renders');
    assert.equal(dom.window.document.getElementById('run-specifications'), null,
      'there is no run-specifications disclosure to open');
    await React.act(async () => {
      technicalNav.dispatchEvent(new dom.window.MouseEvent('click', { bubbles: true }));
      await new Promise(resolve => setTimeout(resolve, 0));
    });
    assert.equal(new URLSearchParams(dom.window.location.search).get('page'), 'technical',
      'the technical destination is addressed as page=technical');
    assert(dom.window.document.getElementById('section-coverage') && dom.window.document.getElementById('section-impact'),
      'the technical page renders the coverage and impact cards');
    assert.equal(dom.window.document.querySelectorAll('.brief-section').length, 0,
      'the briefing\'s reading sections are not on the technical page');
    assert.equal(technicalNav.getAttribute('aria-current'), 'page', 'the technical link is marked current');
    assert.equal(dom.window.document.querySelectorAll('h1').length, 1, 'the shell Header is still the only h1');
    assert.equal(dom.window.document.querySelector('header h1').textContent, 'Tekniset tiedot');
    // A role change leaves the technical page for that role's day, exactly as it leaves a briefing.
    await React.act(async () => {
      dom.window.document.getElementById('brief-role-picker')
        .dispatchEvent(new dom.window.Event('change', { bubbles: true }));
    });
    assert.equal(dom.window.document.getElementById('section-coverage'), null,
      'choosing a role leaves the technical page');

    // The theme control is the library's own IconButton toggle in the sidebar column, following the
    // reference: one appearance state, one labelled button, no select to choose between two. (The phone
    // tools row above the content repeats it for phones, where the sidebar is hidden.)
    const themeChooser = dom.window.document.querySelector('.brief-sidebar button[aria-label="Vaihda tummaan teemaan"]');
    assert(themeChooser, 'the theme control is the sidebar toggle');
    await React.act(async () => themeChooser.dispatchEvent(new dom.window.MouseEvent('click', { bubbles: true })));
    // The library selects dark with `:root[data-theme="dark"]`, so the attribute has to be on <html>.
    assert.equal(dom.window.document.documentElement.dataset.theme, 'dark');
    assert.equal(dom.window.document.querySelector('.brief-app').dataset.theme, undefined,
      'the theme attribute left the app div for <html>');
    assert.equal(new URLSearchParams(dom.window.location.search).get('theme'), 'dark');
    assert.equal(dom.window.document.querySelectorAll('.brief-sidebar [aria-label^="Vaihda "]').length, 1,
      'the theme control is the only appearance control left');
    // The Rooli axis renders twice — once in the rail and once in the phone tools row, because the rail is
    // hidden on phones and the role scopes the whole server-side worklist. Both copies are in the DOM
    // (CSS decides which is on screen), and the ids must differ or the two labels would bind to one element.
    const roleSelects = [...dom.window.document.querySelectorAll('.brief-app select')];
    assert.deepEqual(roleSelects.map(select => select.id).sort(),
      ['brief-role-picker', 'brief-role-picker-mobile'],
      'the Rooli selector is the only select in the shell, and it renders in the rail and the menu');
    assert.equal(requests.filter(url => url.includes('/doctor/picture')).length, pictureRequestsBeforePreset,
      'changing appearance is presentation-only and does not request another Picture');

    // ---- Gate 3: the presenter surface and its mode switch ----
    //
    // Back to a briefing first. The role-picker change above re-scoped the worklist and cleared the Picture,
    // so the shell is on the worklist destination with nothing loaded — and the presenter surface belongs to a
    // briefing, not to a picker. Open a case the same way a professional does.
    await React.act(async () => {
      [...rail.querySelectorAll('a')].find(link => link.textContent.includes('Työpäivä'))
        .dispatchEvent(new dom.window.MouseEvent('click', { bubbles: true }));
    });
    const caseRows = [...dom.window.document.querySelectorAll('.brief-case-picker button')];
    assert(caseRows.length > 0, 'the nurse worklist still offers a case to open');
    await React.act(async () => {
      caseRows[0].dispatchEvent(new dom.window.MouseEvent('click', { bubbles: true }));
      await new Promise(resolve => setTimeout(resolve, 0));
    });
    //
    // Structural first: the demo's comparison instruments have one home, it is labelled, and it sits
    // outside every section the professional reads. A render assertion is the right shape for "outside the
    // reading path"; the click below is the part that cannot be done by reading markup.
    // B2: the presenter surface is on the technical page, a destination of its own. The briefing carries
    // neither the surface nor a shortcut to it (the two `#presenter-tools` jump links are gone with the
    // disclosure they pointed into); its footer line links to the page, and the rail's utility link does too.
    assert.equal(dom.window.document.getElementById('presenter-tools'), null,
      'the presenter surface is not on the briefing');
    assert.equal(dom.window.document.querySelectorAll('a[href="#presenter-tools"]').length, 0,
      'no jump link to the presenter surface remains in the rail or the phone tools row');
    const panelSections = [...dom.window.document.querySelectorAll('.brief-section')];
    // The pre-filled esitiedot added one wrapper to the definition grid and the record columns, so three
    // reading-column sections render. Asserted exactly, so the count cannot drift silently.
    assert.equal(panelSections.length, 3, 'the briefing renders its three stacked reading-column sections');
    assert.equal(dom.window.document.getElementById('section-coverage'), null,
      'the coverage card is not on the briefing');
    assert(dom.window.document.querySelector('.brief-footer').textContent.includes('Tekoäly järjesti ja muotoili; se ei päättänyt.'),
      'the briefing ends with the boundary sentence');
    await React.act(async () => {
      [...utilityRail.querySelectorAll('a')].find(link => link.textContent.includes('Tekniset tiedot'))
        .dispatchEvent(new dom.window.MouseEvent('click', { bubbles: true }));
      await new Promise(resolve => setTimeout(resolve, 0));
    });
    const presenter = dom.window.document.getElementById('presenter-tools');
    assert(presenter, 'the presenter surface renders on the technical page and is addressable by its own id');
    assert(presenter.closest('.brief-technical-page'), 'the presenter surface sits inside the technical page');
    assert.equal(dom.window.document.querySelectorAll('.brief-section').length, 0,
      'the technical page renders none of the briefing\'s reading sections');
    assert(presenter.querySelector('.t-overline').textContent.includes('Esittäjän työkalut'),
      'the presenter surface leads with the words that say whose surface it is');
    assert(presenter.textContent.includes('eivät ole osa ammattilaisen tilannekuvaa'),
      'the presenter surface says it is not part of the situation picture');
    assert(presenter.querySelector('.brief-comparison-grid'),
      'the three-way comparison lives on the presenter surface');
    assert(presenter.textContent.includes('Näkökulmavertailu'),
      'the sibling-lens comparison lives on the presenter surface');

    const modeRadios = [...presenter.querySelectorAll('input[type="radio"]')];
    assert.deepEqual(modeRadios.map(radio => radio.value), ['normal', 'decision-off', 'foundry-off'],
      'the mode switch offers exactly the picture modes the route accepts, in order');
    assert(modeRadios.filter(radio => radio.checked).map(radio => radio.value).join() === 'normal',
      'the checked mode is the mode this Picture was computed with');
    const beforeMode = requests.length;
    await React.act(async () => {
      presenter.querySelector('input[value="decision-off"]').click();
      await new Promise(resolve => setTimeout(resolve, 0));
    });
    assert(requests.slice(beforeMode).some(url => url.includes('mode=decision-off')),
      'switching the mode recomputes THIS encounter through the picture route');
    assert.equal(new URLSearchParams(dom.window.location.search).get('mode'), 'decision-off',
      'the mode is addressable: the address carries the state the control shows');
    await React.act(async () => {
      dom.window.document.querySelector('#presenter-tools input[value="normal"]').click();
      await new Promise(resolve => setTimeout(resolve, 0));
    });
    assert.equal(new URLSearchParams(dom.window.location.search).get('mode'), null,
      'returning to normal removes the parameter: a URL that says nothing is not a written-down choice');
    // An address naming an encounter this catalogue does not carry: the briefing says so, and no Picture is
    // requested for it. The ghost mounts beside the live shell in the same window, so the requests counted
    // from here are its own.
    dom.window.history.replaceState(null, '', '/?encounter=not-in-catalogue');
    const ghostHost = dom.window.document.createElement('div');
    dom.window.document.body.appendChild(ghostHost);
    const ghostRoot = createRoot(ghostHost);
    const requestsBeforeGhost = requests.length;
    try {
      await React.act(async () => {
        ghostRoot.render(React.createElement(App));
        await new Promise(resolve => setTimeout(resolve, 0));
      });
      assert(ghostHost.textContent.includes('Valittua palvelutilannetta ei ole tässä aineistossa'),
        'an address for an encounter the catalogue does not carry renders the neutral state');
      assert(!ghostHost.textContent.includes('Sairaanhoitajan oma lähde') && !ghostHost.textContent.includes('Lääkärin oma lähde'),
        'the neutral state shows no person\'s case, not even the first row');
      assert(!requests.slice(requestsBeforeGhost).some(url => url.includes('/picture')),
        'an unknown encounter address requests no Picture');
    } finally {
      await React.act(async () => ghostRoot.unmount());
      ghostHost.remove();
      dom.window.history.replaceState(null, '', '/');
    }
    // A deep link to an encounter beyond the bootstrapped page: the row is resolved by id, joins the catalogue
    // and the briefing renders from it. Found in the browser: the resolution used to open the address without
    // adding the row, so `selected` stayed undefined and the main column stayed blank with every request 200.
    dom.window.history.replaceState(null, '', '/?role=sairaanhoitaja&encounter=far');
    const farHost = dom.window.document.createElement('div');
    dom.window.document.body.appendChild(farHost);
    const farRoot = createRoot(farHost);
    try {
      await React.act(async () => {
        farRoot.render(React.createElement(App));
        await new Promise(resolve => setTimeout(resolve, 0));
        await new Promise(resolve => setTimeout(resolve, 0));
      });
      assert(farHost.textContent.includes('Sivun ulkopuolinen oma lähde'),
        'a deep link beyond the catalogue page renders the briefing of the resolved encounter');
      assert(!farHost.textContent.includes('Valittua palvelutilannetta ei ole tässä aineistossa'),
        'a resolved deep link is not reported as absent');
      assert(requests.some(url => url.includes('/far/picture')), 'the resolved deep link requests its own Picture');
    } finally {
      await React.act(async () => farRoot.unmount());
      farHost.remove();
      dom.window.history.replaceState(null, '', '/');
    }
  } finally {
    await React.act(async () => root.unmount());
    dom.window.close();
    for (const key of keys) {
      const descriptor = previous.get(key);
      if (descriptor) Object.defineProperty(globalThis, key, descriptor);
      else delete globalThis[key];
    }
  }

// ---- F1: the chat rail resets its whole conversation when the encounter changes ----
{
  const { fetchMock, calls } = chatApi({
    promptsFor: encounterId => ({ prompts: [{ text: `Kysymys ${encounterId}`, why: 'syy', evidence_record_ids: [] }],
      guideline_links: [] }),
    answers: [chatAnswer(chatTurn('turn-a', 'Vastaus A')), chatAnswer(chatTurn('turn-b', 'Vastaus B'))],
  });
  await withChatPanel(fetchMock, async ({ document, render, click, ask }) => {
    await render({ encounterId: 'enc-a', api: '' });
    assert(document.body.textContent.includes('Kysymys enc-a'), 'the first encounter offers its own question');
    await ask({ text: 'Kysymys enc-a' });
    assert(document.body.textContent.includes('Vastaus A'), 'the answer for encounter A renders');
    await click(chatButton(document, 'Hyväksy tarkistettavaksi'));
    assert(document.body.textContent.includes('Tallennettu palveluun'), 'the review is recorded on encounter A');
    assert.equal(calls.ask.length, 1);
    assert.equal(calls.reviews[0].digest_id, 'digest-1',
      'H1: the review carries the digest the turn was sealed with, so a stale thread is refused client-visibly');

    await render({ encounterId: 'enc-b', api: '' });
    assert(document.body.textContent.includes('Kysymys enc-b'), 'the new encounter loads its own offered question');
    assert(!document.body.textContent.includes('Vastaus A'),
      'the previous encounter\'s answer does not survive the switch');
    assert(!document.body.textContent.includes('Tallennettu palveluun'),
      'the previous "Tallennettu palveluun" marker does not survive the switch');
    assert.equal(document.querySelectorAll('.brief-decision-outcomes li').length, 0,
      'no suggestion row from the previous encounter is left on screen');

    // The next ask must not carry the previous encounter's sealed digest, which would 409.
    await ask({ text: 'Kysymys enc-b' });
    assert.equal(calls.ask.length, 2);
    assert.equal(calls.ask[1].digest_id, null, 'the new encounter asks without the previous encounter\'s digest');
    assert(document.body.textContent.includes('Vastaus B'), 'the new encounter\'s answer renders');
  });
}

// ---- F2: a decision recorded for turn A is never read back for turn B's `s0` ----
{
  let releaseReview;
  const reviewGate = new Promise(resolve => { releaseReview = resolve; });
  const { fetchMock, calls } = chatApi({
    promptsFor: () => CHAT_PROMPTS,
    answers: [chatAnswer(chatTurn('turn-a', 'A:n s0')), chatAnswer(chatTurn('turn-b', 'B:n s0'))],
    reviewFor: () => reviewGate.then(() => chatJson({ label: 'Tallennettu palveluun' })),
  });
  await withChatPanel(fetchMock, async ({ document, click, ask }) => {
    await ask({ text: 'Mikä on tilanne?' });
    await click(chatButton(document, 'Hyväksy tarkistettavaksi'));
    assert.equal(calls.reviews.length, 1, 'the turn A review is in flight');
    assert(calls.reviews[0].turn_id === 'turn-a');

    // Turn B starts while turn A's review response has not landed.
    await ask({ text: 'Mikä on tilanne?' });
    const rowText = () => document.querySelector('article[data-turn-id="turn-b"] .brief-decision-outcomes').textContent;
    assert(rowText().includes('B:n s0'), 'turn B renders its own s0');

    releaseReview();
    await React.act(async () => { await new Promise(resolve => setTimeout(resolve, 0)); });
    assert(!rowText().includes('Tallennettu palveluun'),
      'turn A\'s late review does not mark turn B\'s s0 as saved');
    assert(chatButton(document, 'Hyväksy tarkistettavaksi'),
      'turn B\'s s0 is still reviewable rather than locked out by the stale decision');
  });
}

// ---- F3: the AI notice is the API's own string, and only a generated turn carries it ----
{
  const { fetchMock } = chatApi({
    promptsFor: () => CHAT_PROMPTS,
    answers: [chatAnswer(chatTurn('turn-1', 'Vastaus', { generator: 'gpt-6-luna' }),
      { answer_notice: 'API:n oma ilmoitus.' })],
  });
  await withChatPanel(fetchMock, async ({ document, ask }) => {
    await ask();
    assert(document.body.textContent.includes('API:n oma ilmoitus.'),
      'the notice rendered is the answer_notice the API returned');
    assert(!document.body.textContent.includes('Olet yhteydessä tekoälyyn'),
      'the hardcoded fallback is not used when the API supplies the notice');
  });
}

// ---- Part B: a withheld model arm is visible on a real response, and state (i) is unchanged ----
{
  const withheldCaption = 'Tekoälyä käytettiin, mutta sen tuottamaa tekstiä ei julkaistu tässä vastauksessa.';
  const { fetchMock } = chatApi({
    promptsFor: () => CHAT_PROMPTS,
    answers: [chatAnswer(chatTurn('turn-1', 'Vastaus', { generator: null, generator_state: 'withheld' }),
      { answer_notice: 'API:n oma ilmoitus.' })],
  });
  await withChatPanel(fetchMock, async ({ document, ask }) => {
    await ask();
    assert(document.body.textContent.includes(withheldCaption),
      'a response whose model published nothing says so instead of claiming a model-free answer');
    assert(!document.body.textContent.includes('Vastaus on koottu suoraan lähteistä ilman mallia.'),
      'the deterministic caption is not used for a turn a model was involved in');
    assert(document.body.textContent.includes('API:n oma ilmoitus.'),
      'the API notice is rendered for the withheld state too: the model was actually involved');
    assert(document.body.textContent.includes('Vastaus'),
      'the deterministic arm\'s own statements still render');
  });

  const { fetchMock: noModelMock } = chatApi({
    promptsFor: () => CHAT_PROMPTS,
    answers: [chatAnswer(chatTurn('turn-1', 'Vastaus', { generator: null, generator_state: 'not_configured' }),
      { answer_notice: 'API:n oma ilmoitus.' })],
  });
  await withChatPanel(noModelMock, async ({ document, ask }) => {
    await ask();
    assert(document.body.textContent.includes('Vastaus on koottu suoraan lähteistä ilman mallia.'),
      'a turn answered without a model keeps the existing deterministic caption');
    assert(!document.body.textContent.includes(withheldCaption));
    assert(!document.body.textContent.includes('Olet yhteydessä tekoälyyn'),
      'no model was involved, so no AI-interaction claim is made');
  });
}

// ---- F4: a lost response offers the same save again; a definite rejection does not ----
{
  const reviewIds = [];
  const { fetchMock, calls } = chatApi({
    promptsFor: () => CHAT_PROMPTS,
    answers: [chatAnswer(chatTurn('turn-1', 'Vastaus'))],
    reviewFor: payload => {
      reviewIds.push(payload.review_id);
      return reviewIds.length === 1
        ? chatJson({ detail: 'the review was not saved' }, 503)
        : chatJson({ label: 'Tallennettu palveluun' });
    },
  });
  await withChatPanel(fetchMock, async ({ document, click, ask }) => {
    await ask();
    await click(chatButton(document, 'Hyväksy tarkistettavaksi'));
    assert(document.querySelector('.brief-decision-outcomes').textContent.includes('Tallennuksen tila on epäselvä'),
      'a 5xx leaves the write ambiguous rather than failed');
    assert(chatButton(document, 'Yritä samaa tallennusta uudelleen'), 'an ambiguous write offers the same save again');
    assert(!chatButton(document, 'Hyväksy tarkistettavaksi'),
      'no second decision is offered while the first write is unresolved');

    await click(chatButton(document, 'Yritä samaa tallennusta uudelleen'));
    assert.equal(calls.reviews.length, 2);
    assert.equal(reviewIds[1], reviewIds[0], 'the retry reuses the same review_id');
    assert(document.querySelector('.brief-decision-outcomes').textContent.includes('Tallennettu palveluun'),
      'the retried write is the one that records the decision');
  });
}

{
  const reviewIds = [];
  const { fetchMock, calls } = chatApi({
    promptsFor: () => CHAT_PROMPTS,
    answers: [chatAnswer(chatTurn('turn-1', 'Vastaus'))],
    reviewFor: payload => {
      reviewIds.push(payload.review_id);
      return chatJson({ detail: 'turn_id conflicts with an earlier request' }, 409);
    },
  });
  await withChatPanel(fetchMock, async ({ document, click, ask }) => {
    await ask();
    await click(chatButton(document, 'Hyväksy tarkistettavaksi'));
    const row = document.querySelector('.brief-decision-outcomes');
    const alert = row.querySelector('[role="alert"]');
    assert(alert && alert.textContent.includes('Palvelu hylkäsi tallennuksen'),
      'a definite rejection is announced');
    assert(!alert.classList.contains('visually-hidden'),
      'the definite rejection is visible text, not only a live announcement');
    assert(chatButton(document, 'Yritä samaa tallennusta uudelleen') === undefined,
      'a definite rejection is not retried with the same key');
    const accept = chatButton(document, 'Hyväksy tarkistettavaksi');
    assert(accept, 'the decision buttons return after a definite rejection');
    assert.equal(accept.getAttribute('aria-describedby'), alert.id,
      'the visible error describes the action it belongs to');
    await click(accept);
    assert.equal(calls.reviews.length, 2);
    assert.notEqual(reviewIds[1], reviewIds[0],
      'a definite rejection discards the key, so the next decision is a new write');
  });
}

// ---- Change 2: one suggestion click asks, once, with that suggestion's own text ----
{
  const { fetchMock, calls } = chatApi({
    promptsFor: () => CHAT_PROMPTS,
    answers: [chatAnswer(chatTurn('turn-1', 'Vastaus kortista'))],
  });
  await withChatPanel(fetchMock, async ({ document, click }) => {
    await click(chatButton(document, 'Mikä on tilanne?'));
    await React.act(async () => { await new Promise(resolve => setTimeout(resolve, 0)); });
    assert.equal(calls.ask.length, 1, 'one suggestion click starts exactly one chat request');
    assert.equal(calls.ask[0].question, 'Mikä on tilanne?', 'the request carries that suggestion\'s own text');
    assert(document.body.textContent.includes('Vastaus kortista'), 'and the answer renders without a second click');
    assert.equal(document.querySelector('.brief-chat-ask input').value, '',
      'the composer is not filled as a side effect of the click');
  });
}

// ---- Change 4: with three findings, one set of review controls at a time; selecting reveals that set ----
{
  const finding = (id, text) => ({ suggestion_id: id, turn_id: 'turn-multi', ordinal: Number(id.slice(1)),
    statement_class: 'source_fact', text, label: 'Lähde', source_record_ids: [] });
  const { fetchMock } = chatApi({
    promptsFor: () => CHAT_PROMPTS,
    answers: [chatAnswer({ ...chatTurn('turn-multi', 'x'), suggestions: [
      finding('s0', 'Ensimmäinen havainto'), finding('s1', 'Toinen havainto'), finding('s2', 'Kolmas havainto')] })],
  });
  await withChatPanel(fetchMock, async ({ document, click, ask }) => {
    await ask();
    const buttonsNamed = label => [...document.querySelectorAll('.brief-data-section button')]
      .filter(button => button.textContent === label);
    assert.equal(buttonsNamed('Hyväksy tarkistettavaksi').length, 1,
      'with three findings exactly one set of review controls is in the DOM');
    assert.equal(buttonsNamed('Tarkista tämä').length, 3, 'each finding offers one control that selects it');
    const selectFirst = buttonsNamed('Tarkista tämä')[0];
    assert.equal(selectFirst.getAttribute('aria-expanded'), 'true', 'the first open finding is the one selected by default');
    assert.equal(buttonsNamed('Tarkista tämä')[1].getAttribute('aria-expanded'), 'false');
    assert(document.getElementById(buttonsNamed('Tarkista tämä')[1].getAttribute('aria-controls')),
      'an unselected finding\'s select control points at a group that exists in the DOM');
    await click(buttonsNamed('Tarkista tämä')[1]);
    assert.equal(buttonsNamed('Hyväksy tarkistettavaksi').length, 1, 'selecting a finding keeps one set in the DOM');
    const shown = buttonsNamed('Hyväksy tarkistettavaksi')[0];
    assert(shown.closest('li').textContent.includes('Toinen havainto'),
      'selecting the second finding reveals the second finding\'s controls');
    assert.equal(buttonsNamed('Tarkista tämä')[1].getAttribute('aria-expanded'), 'true');
    assert(document.getElementById(buttonsNamed('Tarkista tämä')[1].getAttribute('aria-controls')).contains(shown),
      'the selected finding\'s select control points at the group that holds its controls');
  });
}

// ---- Change 4: with one finding, the three review controls are there at once, with no selection step ----
{
  const { fetchMock } = chatApi({
    promptsFor: () => CHAT_PROMPTS,
    answers: [chatAnswer(chatTurn('turn-1', 'Vastaus'))],
  });
  await withChatPanel(fetchMock, async ({ document, ask }) => {
    await ask();
    assert(chatButton(document, 'Hyväksy tarkistettavaksi'), 'the accept control is present at once');
    assert(chatButton(document, 'Muokkaa'), 'the edit control is present at once');
    assert(chatButton(document, 'Ei tarvetta nyt'), 'the dismiss control is present at once');
    assert.equal(chatButton(document, 'Tarkista tämä'), undefined, 'a single finding has no selection step');
  });
}

// ---- Change 4: an editor takes focus on open, and cancelling returns focus to the control that opened it ----
{
  const { fetchMock } = chatApi({
    promptsFor: () => CHAT_PROMPTS,
    answers: [chatAnswer(chatTurn('turn-1', 'Vastaus'))],
  });
  await withChatPanel(fetchMock, async ({ document, click, ask }) => {
    await ask();
    await click(chatButton(document, 'Muokkaa'));
    assert.equal(document.activeElement.tagName, 'TEXTAREA', 'opening the editor moves focus into its text area');
    await click(chatButton(document, 'Peruuta'));
    assert.equal(document.activeElement, chatButton(document, 'Muokkaa'),
      'cancelling returns focus to the Muokkaa control that opened the editor');
  });
}

// ---- Change 3: the reply comes into view once per turn, and only when it is not already on screen ----
{
  const { fetchMock } = chatApi({
    promptsFor: () => CHAT_PROMPTS,
    answers: [chatAnswer(chatTurn('turn-1', 'Vastaus'))],
  });
  await withChatPanel(fetchMock, async ({ dom, ask }) => {
    const scrolls = [];
    dom.window.HTMLElement.prototype.scrollIntoView = function scrollIntoView(options) {
      if (this.classList.contains('brief-chat-reply')) scrolls.push(options);
    };
    dom.window.HTMLElement.prototype.getBoundingClientRect = function getBoundingClientRect() {
      return { top: 1200, bottom: 1400 };
    };
    await ask();
    assert.deepEqual(scrolls, [{ block: 'start', behavior: 'smooth' }],
      'a reply below the fold is brought to its start, once, with the smooth scroll the app uses elsewhere');
  });
  await withChatPanel(fetchMock, async ({ dom, ask }) => {
    const scrolls = [];
    dom.window.HTMLElement.prototype.scrollIntoView = function scrollIntoView() { scrolls.push(this); };
    dom.window.HTMLElement.prototype.getBoundingClientRect = function getBoundingClientRect() {
      return { top: 100, bottom: 300 };
    };
    await ask();
    assert.equal(scrolls.length, 0, 'a reply that is already fully on screen is not scrolled');
  });
}

// ---- Arrow-down completes the composer from the offered questions, with no request ----
{
  const { fetchMock, calls } = chatApi({ promptsFor: () => CHAT_PROMPTS });
  let fetchCount = 0;
  const countingFetch = async (input, init) => { fetchCount += 1; return fetchMock(input, init); };
  await withChatPanel(countingFetch, async ({ dom, document, typeQuestion }) => {
    const input = () => document.querySelector('.brief-chat-ask input');
    const hint = () => document.querySelector('.brief-chat-ask').textContent;
    const arrowDown = async () => {
      await React.act(async () => {
        input().dispatchEvent(new dom.window.KeyboardEvent('keydown',
          { key: 'ArrowDown', bubbles: true, cancelable: true }));
        await new Promise(resolve => setTimeout(resolve, 0));
      });
    };
    const fetchesBefore = fetchCount;

    await typeQuestion('Mikä on');
    assert(hint().includes('Täydennä: Mikä on tilanne? (paina alas-nuolta)'),
      'the hint names the offered question the typed prefix completes to');
    await arrowDown();
    assert.equal(input().value, 'Mikä on tilanne?', 'arrow-down completes the field to the offered question');
    assert.equal(calls.ask.length, 0, 'completing the field does not ask: no /chat request was issued');
    assert.equal(fetchCount, fetchesBefore, 'completing the field issues no fetch at all');
    assert(!hint().includes('Täydennä'), 'once the field equals the question, the completion hint is gone');

    await typeQuestion('xyz');
    assert(!hint().includes('Täydennä'), 'the hint is absent when no offered question completes the field');
    await arrowDown();
    assert.equal(input().value, 'xyz', 'arrow-down with no candidate leaves the field unchanged');
    assert.equal(fetchCount, fetchesBefore, 'arrow-down with no candidate issues no fetch');
  });
}

// ---- Change 3: a cited record is a link where the briefing renders it, and plain text where it does not ----
{
  const cited = { turn_id: 'turn-cited', digest_id: 'd', seq: 1, asked_at: '2026-10-11T00:00:00Z', outcome: 'answer',
    suggestions: [{ suggestion_id: 's0', turn_id: 'turn-cited', ordinal: 0, statement_class: 'source_fact',
      text: 'Lähde kertoo lääkityksen olevan aktiivinen.', label: 'Lähde',
      source_record_ids: ['source-a', 'source-b'] }] };
  const markup = renderToStaticMarkup(React.createElement(ChatRail,
    { turn: cited, recordTargets: new Set(['source-a']) }));
  const citedDocument = new JSDOM(markup).window.document;
  assert(citedDocument.querySelector('a[href="#source-source-a"]'),
    'a cited record whose target is rendered is a link to it');
  assert(!citedDocument.querySelector('a[href="#source-source-b"]'),
    'a cited record whose target is not rendered is not a link');
  assert(citedDocument.body.textContent.includes('source-b'), 'and it is still shown, as plain text');
}

// ---- the chat rail's reload: the caller's own earlier reviews load back as decided ----
{
  const turn = chatTurn('turn-seed', 'Lähde kertoo lääkityksen olevan aktiivinen.');
  // C2: the caller's own decisions are fetched ONCE per encounter open, by the shell, and handed to the rail
  // as the `decisions` prop; the rail used to request the same route itself, so one open was two requests.
  const loadedDecisions = {
    encounter_id: 'enc-a', proposals: [],
    chat_suggestions: [{ turn_id: 'turn-seed', suggestion_id: 's0', action: 'accept',
      decided_at: '2026-10-11T09:00:00Z' }],
  };
  const { fetchMock, calls } = chatApi({
    promptsFor: () => CHAT_PROMPTS,
    answers: [chatAnswer(turn)],
    decisionsFor: () => { throw new Error('the rail must not request the decisions route itself'); },
  });
  await withChatPanel(fetchMock, async ({ document, ask, render }) => {
    assert.deepEqual(calls.decisions, [],
      'the rail makes no request of its own for the decisions: the shell fetches them once per open');
    await render({ encounterId: 'enc-a', api: '', decisions: loadedDecisions });
    assert.deepEqual(calls.decisions, [], 'handing the payload down requests nothing either');
    // No turn is on screen yet, so nothing is rendered for the loaded decision: it is keyed by its turn.
    assert.equal(document.querySelector('.brief-decision-outcomes'), null);
    await ask();
    const row = document.querySelector('.brief-decision-outcomes');
    assert(row.textContent.includes('Tallennettu palveluun'),
      'a review recorded before the reload renders as decided after asking the same turn');
    assert(row.textContent.includes('Hyväksy tarkistettavaksi'), 'and shows the action that was taken');
    assert(document.querySelector('time[datetime="2026-10-11T09:00:00Z"]'),
      'and when it was recorded, as a machine-readable time');
    assert.equal(chatButton(document, 'Hyväksy tarkistettavaksi'), undefined,
      'the three decision buttons are not offered for a suggestion the caller already reviewed');
    assert.equal(calls.reviews.length, 0, 'loading a decision is not a write');
  });
}

// ---- a 409 that means "you already decided this" is rendered as that, not as a generic failure ----
{
  const { fetchMock, calls } = chatApi({
    promptsFor: () => CHAT_PROMPTS,
    answers: [chatAnswer(chatTurn('turn-1', 'Vastaus'))],
    // What the server now returns for the caller's own duplicate: the machine-readable reason, so the rail
    // does not have to parse the prose.
    reviewFor: () => chatJson({ detail: 'this suggestion already has a decision', reason: 'already_decided' }, 409),
  });
  await withChatPanel(fetchMock, async ({ document, click, ask }) => {
    await ask();
    await click(chatButton(document, 'Hyväksy tarkistettavaksi'));
    const row = document.querySelector('.brief-decision-outcomes');
    assert(row.textContent.includes('Olet jo päättänyt tämän ehdotuksen'),
      'the caller\'s own duplicate is rendered as their earlier decision');
    assert(!row.textContent.includes('Palvelu hylkäsi tallennuksen'),
      'and not as a generic rejection, which would read as a failed write');
    assert.equal(chatButton(document, 'Hyväksy tarkistettavaksi'), undefined,
      'no second decision is offered for a suggestion that already has one');
    assert.equal(calls.reviews.length, 1);
  });
}

// ---- a 409 that is something else stays a definite failure ----
{
  const { fetchMock } = chatApi({
    promptsFor: () => CHAT_PROMPTS,
    answers: [chatAnswer(chatTurn('turn-1', 'Vastaus'))],
    reviewFor: () => chatJson({ detail: 'the encounter changed since this conversation started' }, 409),
  });
  await withChatPanel(fetchMock, async ({ document, click, ask }) => {
    await ask();
    await click(chatButton(document, 'Hyväksy tarkistettavaksi'));
    const row = document.querySelector('.brief-decision-outcomes');
    assert(row.textContent.includes('Palvelu hylkäsi tallennuksen'),
      'a stale-digest 409 is a real failure and says so');
    assert(!row.textContent.includes('Olet jo päättänyt tämän ehdotuksen'),
      'it is not misreported as a decision the caller made');
  });
}

// ---- the briefing rail's reload: the App loads the caller's decisions and renders them as decided ----
{
  const requests = [];
  const encounter = {
    encounter_id: 'enc-decided', example_label: 'Aino Virtanen',
    picker_label: 'Mielialan arviointi · Sairaanhoitaja', role_label: 'Sairaanhoitaja',
    purpose_label: 'Mielialan arviointi', role: 'sairaanhoitaja',
  };
  const proposal = { proposal_id: 'review-decided', rule_version: 'v1',
    text: 'Tarkista lääkityksen ja kirjauksen ristiriita.', status: 'source_conflict',
    evidence_record_ids: [] };
  const picture = {
    encounter_id: 'enc-decided', role: 'sairaanhoitaja', purpose: 'mental_health_assessment',
    identity_header: 'Henkilöllisyystieto ei saatavilla', situation: null, risk: null, actions: [], trace: [],
    grounded: false, citations: [], assessments: [], conflicts: [], proposals: [proposal],
    grounding: { dropped_citations: [], dropped_actions: [], rejected_sentences: [] },
    selection: { baseline_record_ids: [], additional_record_ids: [], emphasized_record_ids: [],
      decision_notes: [], decision_candidate_count: 0, decision_assessed_count: 0,
      decision_not_shortlisted_count: 0, decision_budget_exhausted_count: 0, decision_unavailable_count: 0,
      decision_gate_refused_count: 0, rule_version: 'test' },
    records: { priority: [], accessible_other: [] },
    narration_generation_status: 'unavailable', narration_support_status: 'not_run',
    narration_acceptance_status: 'rejected',
  };
  const fetchMock = async input => {
    const url = String(input);
    requests.push(url);
    const [path] = url.split('?');
    if (path.endsWith('/api/encounters')) {
      return { ok: true, status: 200, json: async () => ({
        encounters: [encounter], total: 1, count: 1, limit: 50, offset: 0, query: '', role: null,
        aggregate: { catalogue: { encounters: 1, people: 1, purposes: 1, roles: 1 },
          roster: [{ role: 'sairaanhoitaja', label: 'Sairaanhoitaja' }],
          matching: { encounters: 1, people: 1, purposes: 1 } },
      }) };
    }
    if (/\/api\/encounters\/[^/]+\/picture/.test(url)) {
      return { ok: true, status: 200, json: async () => picture };
    }
    if (/\/api\/encounters\/[^/]+\/decisions/.test(url)) {
      return { ok: true, status: 200, json: async () => ({
        encounter_id: 'enc-decided',
        proposals: [{ proposal_id: 'review-decided', action: 'accept', decided_at: '2026-10-11T09:00:00Z' }],
        chat_suggestions: [],
      }) };
    }
    if (/\/api\/encounters\/[^/]+\/chat\/prompts/.test(url)) {
      return { ok: true, status: 200, json: async () => ({ prompts: [], guideline_links: [] }) };
    }
    throw new Error(`Unexpected request: ${url}`);
  };
  await withBriefingApp(fetchMock, async ({ document }) => {
    // C2: exactly one request for the route per encounter open. The shell's effect makes it and both rails
    // seed from the payload; a second request from the assistant rail is what this count refuses.
    assert.equal(requests.filter(url => /\/api\/encounters\/enc-decided\/decisions$/.test(url)).length, 1,
      `the decisions route is requested exactly once per encounter open: ${requests.join(' ')}`);
    assert.equal(requests.filter(url => /\/api\/encounters\/enc-decided\/chat\/prompts$/.test(url)).length, 1,
      'and the offered questions once');
    const rail = document.querySelector('.brief-proposal');
    assert(rail, 'the proposal rail renders for a picture that carries a proposal');
    assert(rail.textContent.includes('Tallennettu palveluun'),
      'a proposal the caller decided before the reload renders as decided without a click');
    assert(rail.textContent.includes('Hyväksytty'), 'and shows the action taken');
    assert(rail.querySelector('time[datetime="2026-10-11T09:00:00Z"]'), 'and when it was recorded');
    // Work order G 1a: a recorded decision is an outcome, so the three controls are ABSENT (they used to render
    // disabled). The recorded line above is what the professional sees instead.
    const decisions = [...rail.querySelectorAll('button')]
      .filter(button => ['Muokkaa ehdotusta', 'Hylkää ehdotus', 'Hyväksy ehdotus'].includes(button.textContent));
    assert.equal(decisions.length, 0,
      'a decision recorded before the reload offers no decision controls, so it cannot be offered again');
    assert(!requests.some(url => url.includes('/feedback')), 'loading a decision is not a write');
  });
}

// ---- work order G 2a: while a briefing is prepared, its identity stays on screen and nothing else is requested ----
{
  const requests = [];
  const catalogue = {
    encounters: [{ encounter_id: 'enc-decided', example_label: 'Aino Virtanen',
      picker_label: 'Mielialan arviointi · Sairaanhoitaja', role_label: 'Sairaanhoitaja',
      purpose_label: 'Mielialan arviointi', role: 'sairaanhoitaja' }],
    total: 1, count: 1, limit: 50, offset: 0, query: '', role: null,
    aggregate: { catalogue: { encounters: 1, people: 1, purposes: 1, roles: 1 },
      roster: [{ role: 'sairaanhoitaja', label: 'Sairaanhoitaja' }], matching: { encounters: 1, people: 1, purposes: 1 } },
  };
  const fetchMock = async input => {
    const url = String(input);
    requests.push(url);
    if (url.split('?')[0].endsWith('/api/encounters')) return { ok: true, status: 200, json: async () => catalogue };
    if (/\/decisions$/.test(url)) return { ok: true, status: 200, json: async () => ({ encounter_id: 'enc-decided', proposals: [], chat_suggestions: [] }) };
    if (/\/prefill\/reviews$/.test(url)) return { ok: true, status: 200, json: async () => ({ encounter_id: 'enc-decided', reviews: [] }) };
    // The briefing is still being prepared for the whole of this check.
    if (/\/picture/.test(url)) return new Promise(() => {});
    throw new Error(`Unexpected request: ${url}`);
  };
  await withBriefingApp(fetchMock, async ({ document }) => {
    const loadingBlock = document.querySelector('[aria-busy="true"]');
    assert(loadingBlock, 'the loading block is marked aria-busy while the briefing is prepared');
    const strip = loadingBlock.querySelector('.brief-context-strip');
    assert(strip, 'the context strip is on screen while the briefing loads');
    assert(strip.textContent.includes('Sairaanhoitaja') && strip.textContent.includes('Mielialan arviointi'),
      'the strip names the selected encounter\'s role and purpose');
    assert(!strip.textContent.includes('Aino Virtanen') && !strip.textContent.includes('Henkilö')
      && !strip.querySelector('dt')?.textContent.includes('Henkilö'),
    'while loading the strip makes no person claim: the demo label is not printed and there is no Henkilö field');
    assert(document.querySelector('.brief-app-status[role="status"]')?.textContent === 'Esitiedot valmistellaan…',
      'the honest status line stays as it was');
    assert(!loadingBlock.querySelector('[aria-hidden="true"]'), 'no placeholder bars within the first 300 ms');
    await React.act(async () => { await new Promise(resolve => setTimeout(resolve, 350)); });
    assert(loadingBlock.querySelector('[aria-hidden="true"]'), 'the placeholder bars appear once loading has taken longer than 300 ms');
    assert(requests.every(url => /\/api\/(worklist\?|encounters(\?|$|\/enc-decided\/(picture|decisions|prefill\/reviews)))/.test(url)),
      `loading requests nothing but the catalogue, this encounter's Picture, its decisions and its pre-fill verdicts: ${requests.join(' ')}`);
  });
}

// ---- C3: on a phone the header drops the breadcrumb and the role; a deep link to the technical page renders it ----
//
// The library Header's full anatomy (breadcrumb, title, user with role) measured 189 px at 390 px and 205 px
// with a role: more than two rows. The shell leaves the breadcrumb and the role out under 640 px through a
// `matchMedia` hook, never through CSS over the vendored component. The same render also opens
// `?page=technical` directly, so a reload or a shared link lands on the technical page.
{
  const catalogue = {
    encounters: [{ encounter_id: 'enc-decided', example_label: 'Aino Virtanen',
      picker_label: 'Mielialan arviointi · Sairaanhoitaja', role_label: 'Sairaanhoitaja',
      purpose_label: 'Mielialan arviointi', role: 'sairaanhoitaja' }],
    total: 1, count: 1, limit: 50, offset: 0, query: '', role: null,
    aggregate: { catalogue: { encounters: 1, people: 1, purposes: 1, roles: 1 },
      roster: [{ role: 'sairaanhoitaja', label: 'Sairaanhoitaja' }], matching: { encounters: 1, people: 1, purposes: 1 } },
  };
  const picture = {
    encounter_id: 'enc-decided', role: 'sairaanhoitaja', purpose: 'mental_health_assessment',
    identity_header: 'Henkilöllisyystieto ei saatavilla', situation: null, risk: null, actions: [], trace: [],
    grounded: false, citations: [], assessments: [], conflicts: [], proposals: [],
    grounding: { dropped_citations: [], dropped_actions: [], rejected_sentences: [] },
    selection: { baseline_record_ids: [], additional_record_ids: [], emphasized_record_ids: [],
      decision_notes: [], decision_candidate_count: 0, decision_assessed_count: 0,
      decision_not_shortlisted_count: 0, decision_budget_exhausted_count: 0, decision_unavailable_count: 0,
      decision_gate_refused_count: 0, rule_version: 'test' },
    records: { priority: [], accessible_other: [] },
    narration_generation_status: 'unavailable', narration_support_status: 'not_run',
    narration_acceptance_status: 'rejected',
  };
  const fetchMock = async input => {
    const url = String(input);
    if (url.split('?')[0].endsWith('/api/encounters')) return { ok: true, status: 200, json: async () => catalogue };
    if (/\/picture/.test(url)) return { ok: true, status: 200, json: async () => picture };
    if (/\/decisions$/.test(url)) return { ok: true, status: 200, json: async () => ({ encounter_id: 'enc-decided', proposals: [], chat_suggestions: [] }) };
    if (/\/chat\/prompts$/.test(url)) return { ok: true, status: 200, json: async () => ({ prompts: [], guideline_links: [] }) };
    if (/\/api\/worklist/.test(url)) return { ok: true, status: 200, json: async () => ({ rows: [], days: [], ready: 0 }) };
    throw new Error(`Unexpected request: ${url}`);
  };
  await withBriefingApp(fetchMock, async ({ document }) => {
    const header = document.querySelector('header');
    assert.equal(header.querySelector('nav'), null, 'the phone header carries no breadcrumb landmark');
    assert.equal(header.querySelector('h1').textContent, 'Tekniset tiedot', 'the deep link lands on the technical page');
    assert(!header.textContent.includes('Sairaanhoitaja'), 'the phone header does not print the role under the user');
    assert(document.querySelector('#brief-role-picker-mobile'), 'the role is still on the phone tools row');
    assert(document.getElementById('section-coverage') && document.getElementById('presenter-tools'),
      'the technical page renders from the deep link');
    assert.equal(document.querySelectorAll('.brief-section').length, 0, 'and not the briefing');
  }, { phone: true, url: 'http://localhost/?encounter=enc-decided&page=technical' });
  // The desktop header, same address: the breadcrumb and the role are there, so the phone case above is a
  // difference the hook makes rather than one the fixture happens to have.
  await withBriefingApp(fetchMock, async ({ document }) => {
    const header = document.querySelector('header');
    assert(header.querySelector('nav'), 'the desktop header carries the breadcrumb landmark');
    assert(header.textContent.includes('Sairaanhoitaja'), 'and prints the role under the user');
  }, { url: 'http://localhost/?encounter=enc-decided&page=technical' });
}

// ---- Night plan N1: the assistant is a side pane of the shell, toggled from the header, addressed as `assistant=1` ----
// Closed by default and rendered with the `hidden` attribute (not merely off-screen), so axe and the tab order are
// honest. The header toggle opens it, writes `assistant=1` in place, and moves focus into the pane; Escape inside
// the pane closes it and hands focus back to the toggle that opened it. An address that already says
// `assistant=1` renders it open, with the scope chip (role · identity header) once the Picture has landed, and
// a day view with no addressed case shows the one-line note instead of the panel.
{
  const catalogue = {
    encounters: [{ encounter_id: 'enc-decided', example_label: 'Aino Virtanen',
      picker_label: 'Mielialan arviointi · Sairaanhoitaja', role_label: 'Sairaanhoitaja',
      purpose_label: 'Mielialan arviointi', role: 'sairaanhoitaja' }],
    total: 1, count: 1, limit: 50, offset: 0, query: '', role: null,
    aggregate: { catalogue: { encounters: 1, people: 1, purposes: 1, roles: 1 },
      roster: [{ role: 'sairaanhoitaja', label: 'Sairaanhoitaja' }], matching: { encounters: 1, people: 1, purposes: 1 } },
  };
  const picture = {
    encounter_id: 'enc-decided', role: 'sairaanhoitaja', purpose: 'mental_health_assessment',
    identity_header: 'SYN-ASI-1000012236', situation: null, risk: null, actions: [], trace: [],
    grounded: false, citations: [], assessments: [], conflicts: [], proposals: [],
    grounding: { dropped_citations: [], dropped_actions: [], rejected_sentences: [] },
    selection: { baseline_record_ids: [], additional_record_ids: [], emphasized_record_ids: [],
      decision_notes: [], decision_candidate_count: 0, decision_assessed_count: 0,
      decision_not_shortlisted_count: 0, decision_budget_exhausted_count: 0, decision_unavailable_count: 0,
      decision_gate_refused_count: 0, rule_version: 'test' },
    records: { priority: [], accessible_other: [] },
    narration_generation_status: 'unavailable', narration_support_status: 'not_run',
    narration_acceptance_status: 'rejected',
  };
  const fetchMock = async input => {
    const url = String(input);
    if (url.split('?')[0].endsWith('/api/encounters')) return { ok: true, status: 200, json: async () => catalogue };
    if (/\/picture/.test(url)) return { ok: true, status: 200, json: async () => picture };
    if (/\/decisions$/.test(url)) return { ok: true, status: 200, json: async () => ({ encounter_id: 'enc-decided', proposals: [], chat_suggestions: [] }) };
    if (/\/prefill\/reviews$/.test(url)) return { ok: true, status: 200, json: async () => ({ encounter_id: 'enc-decided', reviews: [] }) };
    if (/\/chat\/prompts$/.test(url)) return { ok: true, status: 200, json: async () => ({ prompts: [], guideline_links: [] }) };
    if (/\/api\/worklist/.test(url)) return { ok: true, status: 200, json: async () => ({ rows: [], days: [], ready: 0 }) };
    throw new Error(`Unexpected request: ${url}`);
  };
  const pane = document => document.querySelector('aside.brief-assistant-pane');
  const headerToggle = document => document.querySelector('header button[aria-controls="brief-assistant"]');
  const keydown = (dom, target, key) => target.dispatchEvent(new dom.window.KeyboardEvent('keydown', { key, bubbles: true, cancelable: true }));

  await withBriefingApp(fetchMock, async ({ dom, document }) => {
    const aside = pane(document);
    assert(aside, 'the shell renders the assistant pane on the briefing');
    assert.equal(aside.getAttribute('role'), 'complementary', 'the pane is a complementary landmark');
    assert.equal(aside.getAttribute('aria-label'), 'LUVN-avustaja');
    assert.equal(aside.id, 'brief-assistant', 'the pane carries the id the toggles point at');
    assert(aside.hasAttribute('hidden'), 'the pane is closed by default, with the hidden attribute');
    assert(!document.querySelector('main aside.brief-assistant-pane'), 'the pane is not inside the main landmark');
    assert(!document.querySelector('.brief-body .brief-chat'), 'the briefing no longer carries the assistant card');
    assert(!new URLSearchParams(dom.window.location.search).has('assistant'), 'the address does not say assistant');

    const toggle = headerToggle(document);
    assert(toggle, 'the header carries the assistant toggle beside the search');
    assert.equal(toggle.getAttribute('aria-label'), 'Avaa avustaja');
    assert.equal(toggle.getAttribute('aria-expanded'), 'false');
    assert(toggle.closest('header .brief-header-tools')?.querySelector('[role="search"] input'),
      'the toggle shares the header search slot with the search landmark');
    const phoneToggle = document.querySelector('.brief-phone-tools button[aria-controls="brief-assistant"]');
    assert(phoneToggle, 'the phone tools row repeats the toggle');

    await React.act(async () => { toggle.dispatchEvent(new dom.window.MouseEvent('click', { bubbles: true })); });
    assert(!aside.hasAttribute('hidden'), 'the toggle opens the pane');
    assert.equal(toggle.getAttribute('aria-expanded'), 'true');
    assert.equal(toggle.getAttribute('aria-label'), 'Sulje avustaja');
    assert.equal(new URLSearchParams(dom.window.location.search).get('assistant'), '1', 'the open state is in the address');
    assert.equal(new URLSearchParams(dom.window.location.search).get('encounter'), 'enc-decided',
      'the address still names the encounter');
    assert(aside.contains(document.activeElement), 'opening moves focus into the pane');
    assert.equal(document.activeElement, aside.querySelector('h2'), 'onto the pane heading (tabIndex -1)');
    assert.equal(document.activeElement.getAttribute('tabindex'), '-1', 'which is not in the tab order itself');
    assert(document.documentElement.classList.contains('brief-assistant-open')
      || document.querySelector('.brief-app').classList.contains('brief-assistant-open'),
      'the app root records the open state for the docking grid');
    assert(aside.querySelector('[role="tablist"]') && aside.querySelector('[role="log"]')
      && aside.querySelector('.brief-chat-ask input'), 'the pane body is the chat panel: tabs, the log and the composer');
    assert.equal(aside.querySelectorAll('.brief-chat-identity').length, 0, 'the panel\'s own identity row is off: the pane names the surface');
    const chip = aside.querySelector('.brief-assistant-chip');
    assert.equal(chip?.textContent, 'Sairaanhoitaja · SYN-ASI-1000012236', 'the scope chip is the role and the identity header');
    assert.equal(aside.querySelector('h2')?.textContent, 'LUVN-avustaja');

    await React.act(async () => { keydown(dom, document.activeElement, 'Escape'); });
    assert(aside.hasAttribute('hidden'), 'Escape inside the pane closes it');
    assert.equal(toggle.getAttribute('aria-expanded'), 'false');
    assert(!new URLSearchParams(dom.window.location.search).has('assistant'), 'and the address no longer says assistant');
    assert.equal(document.activeElement, toggle, 'focus returns to the toggle that opened the pane');

    // The close control, and the phone toggle as the opener: focus returns to the control that opened it.
    await React.act(async () => { phoneToggle.dispatchEvent(new dom.window.MouseEvent('click', { bubbles: true })); });
    assert(!aside.hasAttribute('hidden'), 'the phone toggle opens the pane too');
    assert.equal(phoneToggle.getAttribute('aria-expanded'), 'true');
    const close = aside.querySelector('button[aria-label="Sulje avustaja"]');
    await React.act(async () => { close.dispatchEvent(new dom.window.MouseEvent('click', { bubbles: true })); });
    assert(aside.hasAttribute('hidden'), 'the close control closes the pane');
    assert.equal(document.activeElement, phoneToggle, 'focus returns to the phone toggle that opened it');

    // A theme toggle rewrites the address and must not drop the pane's flag.
    await React.act(async () => { toggle.dispatchEvent(new dom.window.MouseEvent('click', { bubbles: true })); });
    await React.act(async () => {
      document.querySelector('.brief-sidebar button[aria-label="Vaihda tummaan teemaan"]')
        .dispatchEvent(new dom.window.MouseEvent('click', { bubbles: true }));
    });
    assert.equal(new URLSearchParams(dom.window.location.search).get('theme'), 'dark');
    assert.equal(new URLSearchParams(dom.window.location.search).get('assistant'), '1', 'the theme rewrite keeps assistant=1');
    assert(!aside.hasAttribute('hidden'), 'and the pane stays open');
  }, { url: 'http://localhost/?encounter=enc-decided' });

  // The address opens it: a reload or a shared link lands with the pane open, on the technical page too.
  await withBriefingApp(fetchMock, async ({ document }) => {
    const aside = pane(document);
    assert(!aside.hasAttribute('hidden'), 'an address with assistant=1 renders the pane open');
    assert.equal(headerToggle(document).getAttribute('aria-expanded'), 'true');
    assert.equal(document.querySelector('header h1').textContent, 'Tekniset tiedot', 'on the technical destination');
    assert(aside.querySelector('.brief-chat-ask input'), 'with the chat panel for the open encounter');
  }, { url: 'http://localhost/?encounter=enc-decided&page=technical&assistant=1' });

  // No encounter open (a role's day): the pane says so instead of offering a composer with nothing to answer on.
  await withBriefingApp(fetchMock, async ({ document }) => {
    const aside = pane(document);
    assert(!aside.hasAttribute('hidden'), 'the pane opens from the address on the day view');
    assert.equal(document.querySelector('header h1').textContent, 'Työpäivä');
    assert.equal(aside.querySelector('.brief-assistant-empty')?.textContent, 'Avaa palvelutilanne, niin avustaja voi vastata.');
    assert(!aside.querySelector('.brief-chat'), 'and carries no chat panel');
    assert(!aside.querySelector('.brief-assistant-chip'), 'and no scope chip');
  }, { url: 'http://localhost/?role=sairaanhoitaja&assistant=1' });
}

// ---- Issue 10: keyboard focus on the search field must not unmount the briefing; typing and a pointer press still open the worklist ----
{
  const SEARCH = 'input[aria-label="Hae palvelutilannetta"]';
  const BRIEFING_TITLE = 'Tilannekuva';
  const WORKLIST_TITLE = 'Työpäivä';
  const catalogue = {
    encounters: [{ encounter_id: 'enc-decided', example_label: 'Aino Virtanen',
      picker_label: 'Mielialan arviointi · Sairaanhoitaja', role_label: 'Sairaanhoitaja',
      purpose_label: 'Mielialan arviointi', role: 'sairaanhoitaja' }],
    total: 1, count: 1, limit: 50, offset: 0, query: '', role: null,
    aggregate: { catalogue: { encounters: 1, people: 1, purposes: 1, roles: 1 },
      roster: [{ role: 'sairaanhoitaja', label: 'Sairaanhoitaja' }], matching: { encounters: 1, people: 1, purposes: 1 } },
  };
  const picture = {
    encounter_id: 'enc-decided', role: 'sairaanhoitaja', purpose: 'mental_health_assessment',
    identity_header: 'Henkilöllisyystieto ei saatavilla', situation: null, risk: null, actions: [], trace: [],
    grounded: false, citations: [], assessments: [], conflicts: [], proposals: [],
    grounding: { dropped_citations: [], dropped_actions: [], rejected_sentences: [] },
    selection: { baseline_record_ids: [], additional_record_ids: [], emphasized_record_ids: [],
      decision_notes: [], decision_candidate_count: 0, decision_assessed_count: 0,
      decision_not_shortlisted_count: 0, decision_budget_exhausted_count: 0, decision_unavailable_count: 0,
      decision_gate_refused_count: 0, rule_version: 'test' },
    records: { priority: [], accessible_other: [] },
    narration_generation_status: 'unavailable', narration_support_status: 'not_run',
    narration_acceptance_status: 'rejected',
  };
  const fetchMock = async input => {
    const url = String(input);
    if (url.split('?')[0].endsWith('/api/encounters')) return { ok: true, status: 200, json: async () => catalogue };
    if (/\/picture/.test(url)) return { ok: true, status: 200, json: async () => picture };
    if (/\/decisions$/.test(url)) return { ok: true, status: 200, json: async () => ({ encounter_id: 'enc-decided', proposals: [], chat_suggestions: [] }) };
    if (/\/chat\/prompts/.test(url)) return { ok: true, status: 200, json: async () => ({ prompts: [], guideline_links: [] }) };
    throw new Error(`Unexpected request: ${url}`);
  };
  const pointerEvent = dom => new dom.window.PointerEvent('pointerdown', { bubbles: true, cancelable: true });
  // A keystroke as the browser delivers it: the value changes, then `input` and `keyup` fire. In this harness
  // react-dom loads before the DOM exists, so React runs its IE input-event polyfill, which reads a value
  // change on `keyup` (and only once the field has focus) rather than on `input`. Both events are dispatched.
  const typeIntoSearch = (dom, input, value) => {
    const setValue = Object.getOwnPropertyDescriptor(dom.window.HTMLInputElement.prototype, 'value').set;
    setValue.call(input, value);
    input.dispatchEvent(new dom.window.Event('input', { bubbles: true }));
    input.dispatchEvent(new dom.window.KeyboardEvent('keyup', { bubbles: true, key: value.at(-1) }));
  };

  // (a) keyboard focus: the destination marker and the briefing are unchanged; (b) typing switches it, which proves the marker can move.
  await withBriefingApp(fetchMock, async ({ dom, document }) => {
    // React's IE input polyfill calls `attachEvent` on a focused text field; jsdom has no such method, so focusing
    // the field would throw inside React's focus dispatch. The polyfill is not what this check exercises: a no-op.
    dom.window.HTMLElement.prototype.attachEvent = () => {};
    dom.window.HTMLElement.prototype.detachEvent = () => {};
    const input = document.querySelector(SEARCH);
    assert(input, 'the topbar search input is found by its accessible name (non-vacuity)');
    const titleBefore = document.querySelector('h1.t-page-title')?.textContent;
    assert.equal(titleBefore, BRIEFING_TITLE, 'the shell opens on the briefing destination');
    assert(document.querySelector('#brief-content .brief-body'), 'the briefing is in the DOM before focus (non-vacuity)');

    await React.act(async () => { input.focus(); });
    assert.equal(document.activeElement, input, 'the search input really received keyboard focus (non-vacuity)');
    assert.equal(document.querySelector('h1.t-page-title')?.textContent, BRIEFING_TITLE,
      'issue 10: keyboard focus on the search field leaves the destination on the briefing');
    assert(document.querySelector('#brief-content .brief-body'),
      'issue 10: the briefing stays in the DOM after the search field takes focus, so Tab can still reach it');

    await React.act(async () => { typeIntoSearch(dom, input, 'Mieli'); });
    assert.equal(document.querySelector('h1.t-page-title')?.textContent, WORKLIST_TITLE,
      'typing in the search field still switches the destination to the worklist (non-vacuity control)');
  });

  // (c) a pointer press on a fresh render still switches to the worklist.
  await withBriefingApp(fetchMock, async ({ dom, document }) => {
    const input = document.querySelector(SEARCH);
    assert(input, 'the search input exists on the fresh render');
    assert.equal(document.querySelector('h1.t-page-title')?.textContent, BRIEFING_TITLE,
      'the fresh render opens on the briefing destination');
    await React.act(async () => { input.dispatchEvent(pointerEvent(dom)); });
    assert.equal(document.querySelector('h1.t-page-title')?.textContent, WORKLIST_TITLE,
      'a pointer press on the search field still switches the destination to the worklist');
  });
}

// ---- Finding 1: the loaded briefing, unlike the loading panel, states the identity the Picture supplies ----
{
  const catalogue = {
    encounters: [{ encounter_id: 'enc-decided', example_label: 'Aino Virtanen',
      picker_label: 'Mielialan arviointi · Sairaanhoitaja', role_label: 'Sairaanhoitaja',
      purpose_label: 'Mielialan arviointi', role: 'sairaanhoitaja' }],
    total: 1, count: 1, limit: 50, offset: 0, query: '', role: null,
    aggregate: { catalogue: { encounters: 1, people: 1, purposes: 1, roles: 1 },
      roster: [{ role: 'sairaanhoitaja', label: 'Sairaanhoitaja' }], matching: { encounters: 1, people: 1, purposes: 1 } },
  };
  const loaded = {
    encounter_id: 'enc-decided', role: 'sairaanhoitaja', purpose: 'mental_health_assessment',
    identity_header: 'Aino Virtanen', situation: null, risk: null, actions: [], trace: [],
    grounded: false, citations: [], assessments: [], conflicts: [], proposals: [],
    grounding: { dropped_citations: [], dropped_actions: [], rejected_sentences: [] },
    selection: { baseline_record_ids: [], additional_record_ids: [], emphasized_record_ids: [],
      decision_notes: [], decision_candidate_count: 0, decision_assessed_count: 0,
      decision_not_shortlisted_count: 0, decision_budget_exhausted_count: 0, decision_unavailable_count: 0,
      decision_gate_refused_count: 0, rule_version: 'test' },
    records: { priority: [], accessible_other: [] },
    narration_generation_status: 'unavailable', narration_support_status: 'not_run',
    narration_acceptance_status: 'rejected',
  };
  const fetchMock = async input => {
    const url = String(input);
    if (url.split('?')[0].endsWith('/api/encounters')) return { ok: true, status: 200, json: async () => catalogue };
    if (/\/picture/.test(url)) return { ok: true, status: 200, json: async () => loaded };
    if (/\/decisions$/.test(url)) return { ok: true, status: 200, json: async () => ({ encounter_id: 'enc-decided', proposals: [], chat_suggestions: [] }) };
    if (/\/chat\/prompts/.test(url)) return { ok: true, status: 200, json: async () => ({ prompts: [], guideline_links: [] }) };
    throw new Error(`Unexpected request: ${url}`);
  };
  await withBriefingApp(fetchMock, async ({ document }) => {
    const strip = document.querySelector('.brief-context-strip');
    assert(strip?.textContent.includes('Aino Virtanen'), 'the loaded briefing states the identity the Picture supplies');
    assert(strip.querySelector('dt')?.textContent === 'Henkilö' && strip.querySelector('dd')?.textContent === 'Aino Virtanen',
      'the loaded briefing shows the Henkilö field with that identity');
  });
}

// ---- work order G 2b: a loaded briefing survives a visit to the worklist; returning issues no second /picture ----
{
  const requests = [];
  const catalogue = {
    encounters: [{ encounter_id: 'enc-decided', example_label: 'Aino Virtanen',
      picker_label: 'Mielialan arviointi · Sairaanhoitaja', role_label: 'Sairaanhoitaja',
      purpose_label: 'Mielialan arviointi', role: 'sairaanhoitaja' }],
    total: 1, count: 1, limit: 50, offset: 0, query: '', role: null,
    aggregate: { catalogue: { encounters: 1, people: 1, purposes: 1, roles: 1 },
      roster: [{ role: 'sairaanhoitaja', label: 'Sairaanhoitaja' }], matching: { encounters: 1, people: 1, purposes: 1 } },
  };
  const picture = {
    encounter_id: 'enc-decided', role: 'sairaanhoitaja', purpose: 'mental_health_assessment',
    identity_header: 'Henkilöllisyystieto ei saatavilla', situation: null, risk: null, actions: [], trace: [],
    grounded: false, citations: [], assessments: [], conflicts: [], proposals: [],
    grounding: { dropped_citations: [], dropped_actions: [], rejected_sentences: [] },
    selection: { baseline_record_ids: [], additional_record_ids: [], emphasized_record_ids: [],
      decision_notes: [], decision_candidate_count: 0, decision_assessed_count: 0,
      decision_not_shortlisted_count: 0, decision_budget_exhausted_count: 0, decision_unavailable_count: 0,
      decision_gate_refused_count: 0, rule_version: 'test' },
    records: { priority: [], accessible_other: [] },
    narration_generation_status: 'unavailable', narration_support_status: 'not_run',
    narration_acceptance_status: 'rejected',
  };
  const fetchMock = async input => {
    const url = String(input);
    requests.push(url);
    if (url.split('?')[0].endsWith('/api/encounters')) return { ok: true, status: 200, json: async () => catalogue };
    if (/\/picture/.test(url)) return { ok: true, status: 200, json: async () => picture };
    if (/\/decisions$/.test(url)) return { ok: true, status: 200, json: async () => ({ encounter_id: 'enc-decided', proposals: [], chat_suggestions: [] }) };
    if (/\/chat\/prompts/.test(url)) return { ok: true, status: 200, json: async () => ({ prompts: [], guideline_links: [] }) };
    throw new Error(`Unexpected request: ${url}`);
  };
  await withBriefingApp(fetchMock, async ({ dom, document }) => {
    const pictureRequests = () => requests.filter(url => /\/picture/.test(url)).length;
    const clickIn = async (selector, label) => {
      await React.act(async () => {
        const node = [...document.querySelectorAll(selector)].find(item => item.textContent.includes(label));
        assert(node, `"${label}" is on screen`);
        node.dispatchEvent(new dom.window.MouseEvent('click', { bubbles: true }));
        await new Promise(resolve => setTimeout(resolve, 0));
      });
    };
    assert.equal(pictureRequests(), 1, 'the briefing loads its Picture once');
    assert(document.querySelector('.brief-context-strip'), 'the briefing is on screen');
    await clickIn('a', 'Työpäivä');
    assert(document.querySelector('.brief-case-picker'), 'the worklist is on screen');
    // Opening the case that is already open is not a new load: the same encounter, so no state reset.
    await clickIn('.brief-case-picker button', 'Mielialan arviointi');
    assert.equal(pictureRequests(), 1, 'choosing the open case again does not request its Picture again');
    await clickIn('a', 'Tilannekuva');
    assert.equal(pictureRequests(), 1, 'returning to the briefing issues no second /picture request');
    assert(document.querySelector('.brief-context-strip') && !document.querySelector('[aria-busy="true"]'),
      'the already-loaded briefing is re-rendered, not a loading panel');
    assert(document.querySelector('.brief-context-strip').textContent.includes('Mielialan arviointi'),
      'and it still states the same service purpose');
  });
}

} finally {
  await vite.close();
}

// ---- F3 (rail half), F6 and F8: the populated rail states, rendered without a server ----
const railTurn = {
  turn_id: 'turn-static', digest_id: 'd', seq: 1, asked_at: '2026-10-11T00:00:00Z', outcome: 'answer',
  suggestions: [{ suggestion_id: 's0', turn_id: 'turn-static', ordinal: 0, statement_class: 'source_fact',
    text: 'Lähde kertoo lääkityksen olevan aktiivinen.', label: 'Lähde', source_record_ids: ['source-a'] }],
};
{
  // F3: the AI-interaction notice is rendered from the API, and only a generated turn carries it.
  const generated = renderToStaticMarkup(React.createElement(ChatRail, {
    turn: { ...railTurn, generator: 'gpt-6-luna' }, notice: 'API:n oma ilmoitus.',
  }));
  assert(generated.includes('API:n oma ilmoitus.'), 'the rail renders the notice the API supplied');
  assert(generated.includes('Vastaus on koottu lähteistä ja tarkistettu lauseittain.'),
    'a generated turn states that it was verified sentence by sentence');
  assert(!generated.includes('Vastaus on koottu suoraan lähteistä ilman mallia.'),
    'a generated turn does not claim a deterministic answer');

  const generatedWithoutNotice = renderToStaticMarkup(React.createElement(ChatRail, {
    turn: { ...railTurn, generator: 'gpt-6-luna' }, notice: null,
  }));
  assert(generatedWithoutNotice.includes('Olet yhteydessä tekoälyyn. Vastaus on tarkistettava.'),
    'the last-resort notice is used only when a model was involved and the API omitted the field');

  const deterministic = renderToStaticMarkup(React.createElement(ChatRail, {
    turn: { ...railTurn, generator: null }, notice: 'API:n oma ilmoitus.',
  }));
  assert(deterministic.includes('Vastaus on koottu suoraan lähteistä ilman mallia.'),
    'a deterministic turn states that no model was involved');
  assert(!deterministic.includes('Olet yhteydessä tekoälyyn'),
    'a deterministic turn makes no AI-interaction claim');
  assert(!deterministic.includes('API:n oma ilmoitus.'),
    'the API notice is not rendered on a turn that involved no model');
}
{
  // Part B: `generator_state` separates the three states the rail previously could not tell apart. The
  // fallback above (no `generator_state`, only `generator`) is also exercised by the F3 renders.
  const WITHHELD_CAPTION = 'Tekoälyä käytettiin, mutta sen tuottamaa tekstiä ei julkaistu tässä vastauksessa.';
  const withheld = renderToStaticMarkup(React.createElement(ChatRail, {
    turn: { ...railTurn, generator: null, generator_state: 'withheld' }, notice: 'API:n oma ilmoitus.',
  }));
  assert(withheld.includes(WITHHELD_CAPTION),
    'a called model whose text was not published is disclosed, not reported as a model-free answer');
  assert(!withheld.includes('Vastaus on koottu suoraan lähteistä ilman mallia.'),
    'a withheld model arm does not claim the answer was assembled without a model');
  assert(!withheld.includes('Vastaus on koottu lähteistä ja tarkistettu lauseittain.'),
    'a withheld model arm does not claim verified generated prose');
  assert(withheld.includes('API:n oma ilmoitus.'),
    'the AI-interaction notice still renders: the model was actually involved');
  // The caption names nothing that was withheld and asserts no cause: no count, no record id, no claim
  // that something is hidden, and no implication that the model refused.
  assert(!/\d/.test(WITHHELD_CAPTION), 'the withheld caption carries no count');
  assert(!/piilot|salass|hidden|kieltäyty|ei suostu/i.test(WITHHELD_CAPTION),
    'the withheld caption neither claims hidden content nor implies a refusal');
  assert(!withheld.includes('withheld-record'), 'no withheld record id reaches the rail');

  const published = renderToStaticMarkup(React.createElement(ChatRail, {
    turn: { ...railTurn, generator: 'gpt-6-luna', generator_state: 'published' },
    notice: 'API:n oma ilmoitus.',
  }));
  assert(published.includes('Vastaus on koottu lähteistä ja tarkistettu lauseittain.'),
    'the published state keeps the verified-prose caption');
  assert(!published.includes(WITHHELD_CAPTION));

  const noModel = renderToStaticMarkup(React.createElement(ChatRail, {
    turn: { ...railTurn, generator: null, generator_state: 'not_configured' },
    notice: 'API:n oma ilmoitus.',
  }));
  assert(noModel.includes('Vastaus on koottu suoraan lähteistä ilman mallia.'),
    'the not-configured state keeps the existing deterministic caption');
  assert(!noModel.includes(WITHHELD_CAPTION) && !noModel.includes('Olet yhteydessä tekoälyyn'));
}
{
  // F6: the justification is visible, the button is described by it, and evidence is linked only where
  // the page actually renders the record's anchor.
  const prompt = { text: 'Mitkä lääkitykset ovat ristiriidassa?', why: 'ristiriita kirjattu',
    evidence_record_ids: ['source-a', 'source-b'] };
  const rail = renderToStaticMarkup(React.createElement(ChatRail, {
    prompts: [prompt], recordTargets: new Set(['source-a']),
  }));
  const railDocument = new JSDOM(rail).window.document;
  // The suggestion CARD is the button (the reference panel's anatomy: mark, short title, the question), and
  // it is selected by its own class. `querySelector('button')` would now find the first tab of the tab row
  // above the cards, which describes itself with `aria-controls`, not with a justification.
  const button = railDocument.querySelector('.brief-chat-suggestion');
  assert(button, 'the suggestion card renders as a button');
  const describedBy = button.getAttribute('aria-describedby');
  assert(describedBy, 'the offered question is associated with its justification');
  const justification = railDocument.getElementById(describedBy);
  assert(justification && justification.textContent.includes('ristiriita kirjattu'),
    'the justification is visible text, not only a `title` tooltip');
  assert.equal(button.getAttribute('title'), null, 'the title-only justification is gone');
  // WCAG 2.5.3 (label in name): the accessible name is the aria-label when one is set, else the visible text,
  // and it must contain the full visible question. A derived title alone would fail this.
  const accessibleName = (button.getAttribute('aria-label') ?? button.textContent).replace(/\s+/g, ' ').trim();
  assert(accessibleName.includes(prompt.text),
    `the suggestion's accessible name contains its visible question (got "${accessibleName}")`);
  assert(railDocument.querySelector('a[href="#source-source-a"]'),
    'evidence whose anchor this page renders is a link');
  assert(!railDocument.querySelector('a[href="#source-source-b"]'),
    'evidence whose anchor this page does not render is not a dead link');
  assert(!railDocument.body.textContent.includes('source-b'),
    'an evidence id this page does not render is not printed: the why line shows only links it can make');

  const noTargets = renderToStaticMarkup(React.createElement(ChatRail, { prompts: [prompt] }));
  assert(!noTargets.includes('href="#source-'),
    'with no known anchor set the evidence ids render as plain text rather than as links');
  assert(!noTargets.includes('source-a'), 'and with no rendered record they are not printed at all');
}
{
  // F8: the `Ohjeet` panel is gated on the safety-filtered list, not the raw one. The panel is the vendored
  // `Tabs`' tabpanel and only the open tab renders, so a static render opens it through `initialTab` rather
  // than clicking (a click is the a11y gate's job, and `renderToStaticMarkup` has no click at all).
  const unsafe = [{ code: 'x', title: 'Roskalinkki', url: 'javascript:alert(1)', published: '2024' }];
  const allUnsafe = renderToStaticMarkup(React.createElement(ChatRail,
    { links: unsafe, initialTab: 'ohjeet' }));
  assert(!allUnsafe.includes('Masennus') && !allUnsafe.includes('javascript:'),
    'an all-unsafe list renders no link and the unsafe URL never reaches the markup');
  assert(allUnsafe.includes('Tähän palvelutilanteeseen ei ole liitetty'),
    'and the tab says in one plain sentence that there are none');

  const safe = { code: 'kh-1', title: 'Masennus', url: 'https://www.kaypahoito.fi/kh-1', published: '2024' };
  const mixed = renderToStaticMarkup(React.createElement(ChatRail,
    { links: [unsafe[0], safe], initialTab: 'ohjeet' }));
  assert(mixed.includes('Käypä hoito') && mixed.includes('Masennus'),
    'a safe link still renders, under the panel heading');
  assert(mixed.includes('julkaistu 2024'), 'the publication date is visible beside the link');
  assert(!mixed.includes('Roskalinkki'), 'the unsafe entry is the only one dropped');
}
{
  // The panel's presentation state: the empty state has no greeting card (the assistant header and the
  // suggestions open it), and the context chip states what the assistant is scoped to (or is omitted when the
  // labels are not available).
  const named = renderToStaticMarkup(React.createElement(ChatRail,
    { contextRole: 'Sairaanhoitaja', contextPerson: 'Aino Rautakallio' }));
  assert(!named.includes('Hei ') && !named.includes('brief-chat-greeting'),
    'the empty state carries no greeting card and no salutation');
  assert(named.includes('Sairaanhoitaja · Aino Rautakallio'),
    'the context chip states the role and the person the assistant can see');
  assert(named.includes('Kysy kysymys') && named.includes('Ohjeet'),
    'the two tabs are on one row, as in the reference');
  assert(named.includes('role="tablist"') && named.includes('role="tab"') && named.includes('role="tabpanel"'),
    'the tab row has real tab semantics');

  const anonymous = renderToStaticMarkup(React.createElement(ChatRail, {}));
  assert(!anonymous.includes('brief-chat-greeting'), 'with no greeting card there is no name-less salutation either');
  assert(!anonymous.includes('None') && !anonymous.includes('undefined'),
    'and it never renders "None" or "undefined" into the page');
  assert(!anonymous.includes('brief-chat-context'),
    'the context chip is omitted rather than invented when the labels are not available');
}
{
  // The panel-level wiring for that chip: the person the chip names is the ENCOUNTER's label, which the
  // page hands to ChatPanel, not the caller's principal name, and a scope chip printing the caller's own name
  // would claim the assistant is scoped to the request
  // rather than to the encounter. This is the seam the static rail test above cannot see: it renders the
  // rail with the chip's own props, while the panel decides which of the two names it has.
  const panel = renderToStaticMarkup(React.createElement(ChatPanel, {
    encounterId: 'enc-chip', roleLabel: 'Sairaanhoitaja', personName: 'Aino Rautakallio',
  }));
  assert(panel.includes('Sairaanhoitaja · Aino Rautakallio'),
    'the panel\'s context chip names the role and the encounter person the page resolved');
  assert(panel.includes('brief-chat-context-value'),
    'the chip renders rather than being omitted, because both labels are available');
}
{
  // Defect 2: an `external_evidence` statement renders the guideline as a link, the SAME way the rail's
  // guideline panel renders one — visible title, publisher and publication date, an absolute https URL, and
  // no dead `#` anchor. It must not invent a second link style.
  const guidelineText = 'Käypä hoito -suositus tähän tilanteeseen: Kohonnut verenpaine (julkaistu 21.09.2026).';
  const guidelineTurn = {
    turn_id: 'turn-guideline', digest_id: 'd', seq: 1, asked_at: '2026-10-11T00:00:00Z', outcome: 'answer',
    suggestions: [
      { suggestion_id: 's0', turn_id: 'turn-guideline', ordinal: 0, statement_class: 'answer_lead',
        text: 'Kysymykseesi "Mitä lääkityksiä potilaalla on?" tämän palvelutilanteen sallitut kirjaukset vastaavat näin:',
        label: 'Kysymys', source_record_ids: [] },
      { suggestion_id: 's1', turn_id: 'turn-guideline', ordinal: 1, statement_class: 'external_evidence',
        text: guidelineText, label: 'Käypä hoito -suositus · www.kaypahoito.fi',
        source_record_ids: ['demo-p1-mittaus-1'], url: 'https://www.kaypahoito.fi/hoi04010' },
    ],
  };
  const markup = renderToStaticMarkup(React.createElement(ChatRail, { turn: guidelineTurn }));
  const document = new JSDOM(markup).window.document;
  const anchor = document.querySelector('a[href="https://www.kaypahoito.fi/hoi04010"]');
  assert(anchor, 'the guideline statement renders its recorded deep link');
  assert(anchor.textContent.includes('Kohonnut verenpaine') && anchor.textContent.includes('www.kaypahoito.fi'),
    'the link carries the guideline title and the publisher as visible text');
  assert(document.body.textContent.includes('julkaistu 21.09.2026'),
    'the publication date is visible beside the link');
  assert.equal(anchor.getAttribute('target'), '_blank', 'the deep link opens in a new tab');
  assert(anchor.getAttribute('rel').includes('noopener'), 'and carries the no-opener relation');
  assert(document.body.textContent.includes(guidelineText),
    'the statement sentence itself is still rendered, not replaced by the link');
  assert(!markup.includes('href="#'), 'no statement link is a dead anchor');
  assert(markup.includes('Ulkoinen lähde'), 'the statement carries its class label');

  // The URL is API data, so an unsafe one is not rendered as a link even on this class: the sentence stays
  // as plain text and no `javascript:` URL reaches the page.
  const unsafeGuideline = renderToStaticMarkup(React.createElement(ChatRail, {
    turn: { ...guidelineTurn, suggestions: [{ ...guidelineTurn.suggestions[1], url: 'javascript:alert(1)' }] },
  }));
  assert(!unsafeGuideline.includes('javascript:'), 'an unsafe guideline URL never reaches the markup');
  assert(unsafeGuideline.includes(guidelineText), 'the statement is still shown, as plain text');
}
{
  // The answered panel is a conversation, not a form with output under it: the reply takes the empty state's
  // place and the composer moves BELOW it, and the greeting and the suggestion cards are gone. This is the
  // reference's shape (message, then composer) and it is what puts the answer where the eye already is; the
  // owner rejected the first version, which kept four cards and the composer above the reply.
  const question = 'Mitkä lääkitykset ovat ristiriidassa?';
  const leadTurn = {
    ...railTurn,
    suggestions: [
      { suggestion_id: 's0', turn_id: 'turn-static', ordinal: 0, statement_class: 'answer_lead',
        text: `Kysymykseesi "${question}" tämän palvelutilanteen sallitut kirjaukset vastaavat näin:`,
        label: 'Kysymys', source_record_ids: [] },
      railTurn.suggestions[0],
    ],
  };
  const answered = renderToStaticMarkup(React.createElement(ChatRail, {
    turn: leadTurn, question,
    prompts: [{ text: question, why: 'ristiriita kirjattu', evidence_record_ids: [] }],
    greetingName: 'Juhannes', initialTab: 'kysy',
  }));
  const answeredDocument = new JSDOM(answered).window.document;
  const order = [...answeredDocument.querySelectorAll(
    '.brief-chat-reply, .brief-chat-greeting, .brief-chat-suggestions, .brief-chat-ask')]
    .map(node => node.className.split(' ').find(name => name.startsWith('brief-chat-')));

  assert.deepEqual(order, ['brief-chat-reply', 'brief-chat-ask'],
    'the answered state renders the reply and then the composer, and nothing else in between');
  assert(!answered.includes('brief-chat-suggestion') && !answered.includes('brief-chat-greeting'),
    'the suggestion cards and the greeting belong to the empty state only');
  assert(!answered.includes('brief-chat-reply-question'),
    'the reply has no separate question header: the lead already names the question it answers');
  // The question is framed ONCE, by the lead (the deterministic framing statement). The owner rejected the
  // first version, which printed "Vastaus kysymykseen: <question>" above the lead and so framed the same
  // question twice in two wordings. The composer still holds the question in its field — that is the text the
  // next ask would send, not a second framing, and it is outside the reply.
  const reply = answeredDocument.querySelector('.brief-chat-reply').textContent;
  assert.equal(reply.split(question).length - 1, 1,
    'the question is framed once inside the reply, in the lead');
  assert(!answered.includes('Vastaus kysymykseen'), 'the duplicate ticket line is gone');
  assert(answered.includes('Kysymykseesi'), 'the lead is what opens the reply');

  // And the empty state keeps its own order: greeting, cards, then the composer.
  const empty = renderToStaticMarkup(React.createElement(ChatRail, {
    prompts: [{ text: 'Mitkä lääkitykset ovat ristiriidassa?', why: 'ristiriita kirjattu',
      evidence_record_ids: [] }], initialTab: 'kysy',
  }));
  const emptyOrder = [...new JSDOM(empty).window.document.querySelectorAll(
    '.brief-chat-reply, .brief-chat-greeting, .brief-chat-suggestions, .brief-chat-ask')]
    .map(node => node.className.split(' ').find(name => name.startsWith('brief-chat-')));
  assert.deepEqual(emptyOrder, ['brief-chat-suggestions', 'brief-chat-ask'],
    'the empty state keeps the cards above the composer, with no greeting card before them');
}
{
  // A statement whose label only repeats its class tag does not get a source line. A rule output cites no
  // record and its label IS "Sääntöpohjainen arvio", so printing it under the tag printed the same words
  // twice; the tag is the claim, the line is the citation, and a line with no citation is noise.
  const ruleOnly = renderToStaticMarkup(React.createElement(ChatRail, {
    turn: { ...railTurn, suggestions: [{ suggestion_id: 's0', turn_id: 'turn-static', ordinal: 0,
      statement_class: 'rule_output', text: 'Lähteiden ristiriita lääkityksessä — tarkista, kumpi lähde on ajantasainen',
      label: 'Sääntöpohjainen arvio', source_record_ids: [] }] },
  }));
  assert(ruleOnly.includes('Sääntöpohjainen arvio'), 'the tag is rendered');
  // Counted inside the statement's own row: the class legend below the reply names the same class word on
  // purpose, so a count over the whole rail would count the legend and not the row this assertion is about.
  const ruleRow = new JSDOM(ruleOnly).window.document.querySelector('li.brief-proposal').textContent;
  assert.equal(ruleRow.split('Sääntöpohjainen arvio').length - 1, 1,
    'the class word appears once in the row: no second, word-for-word source line under the tag');
  assert(ruleOnly.includes('Lähteiden ristiriita'), 'the conflict statement is rendered');
  // The statement that DOES carry a citation keeps its line: a label that differs from the tag, and ids.
  const withRecord = renderToStaticMarkup(React.createElement(ChatRail, { turn: railTurn }));
  assert(new JSDOM(withRecord).window.document.body.textContent.includes('Lähde · source-a'),
    'a citing statement keeps its source line — label and record id together');
}
{
  // The engine's proposal and its finding carry different server labels under the same class. The tag must show
  // which one the row is, and the caption must not print that same label again underneath it.
  const proposalRow = (overrides) => ({ suggestion_id: 's0', turn_id: 'turn-static', ordinal: 0,
    statement_class: 'rule_output', text: 'Jatka tilanteen seurantaa', label: 'Sääntöpohjainen arvio (ehdotus)',
    source_record_ids: [], ...overrides });
  // O2: a proposal is a step, identified by its label. Without its basis note it is withheld from every list, so its
  // statement is never shown as a finding with review controls. With the note it is a step in the steps block.
  const withheld = renderToStaticMarkup(React.createElement(ChatRail, {
    turn: { ...railTurn, suggestions: [proposalRow({})] },
  }));
  assert(!withheld.includes('Jatka tilanteen seurantaa') && !withheld.includes('data-steps'),
    'a proposal stored without its basis note is withheld: no statement, no steps block');
  const stepped = new JSDOM(renderToStaticMarkup(React.createElement(ChatRail, {
    turn: { ...railTurn, suggestions: [proposalRow({ basis_note: 'Tilanne: krooninen. Havainnollistava sääntö.' })] },
  }))).window.document;
  assert(stepped.querySelector('[data-steps="engine"]') && stepped.body.textContent.includes('Jatka tilanteen seurantaa'),
    'a proposal with its basis note is rendered as a step');
  assert(!stepped.querySelector('li.brief-proposal'), 'a step is not a finding row, so it carries no review controls');
  // A finding keeps the plain class word and no duplicate line, exactly as before.
  const finding = renderToStaticMarkup(React.createElement(ChatRail, {
    turn: { ...railTurn, suggestions: [proposalRow({ label: 'Sääntöpohjainen arvio', text: 'Vaikea masennusoireilu (PHQ-9)' })] },
  }));
  const findingItem = new JSDOM(finding).window.document.querySelector('li.brief-proposal');
  assert.equal(findingItem.querySelector('.brief-role-chip').textContent, 'Sääntöpohjainen arvio',
    'a rule finding keeps the plain class tag');
  assert.equal(findingItem.textContent.split('Sääntöpohjainen arvio').length - 1, 1,
    'a rule finding’s class word appears once in its row');
  // A source fact keeps its source label as its caption, and its record link follows the rendered targets.
  const sourceFact = { ...railTurn, suggestions: [{ suggestion_id: 's0', turn_id: 'turn-static', ordinal: 0,
    statement_class: 'source_fact', text: 'Lähde kertoo lääkityksen olevan aktiivinen.',
    label: 'Lähde: lääkitys · 2026-08-18', source_record_ids: ['source-a'] }] };
  const linked = new JSDOM(renderToStaticMarkup(React.createElement(ChatRail,
    { turn: sourceFact, recordTargets: new Set(['source-a']) }))).window.document;
  assert(linked.querySelector('li.brief-proposal .t-caption')?.textContent.startsWith('Lähde: lääkitys · 2026-08-18'),
    'a source fact keeps its source label as its caption');
  assert(linked.querySelector('a[href="#source-source-a"]'),
    'a source fact’s cited record is a link when its target is rendered');
  const unlinked = new JSDOM(renderToStaticMarkup(React.createElement(ChatRail, { turn: sourceFact })))
    .window.document;
  assert(!unlinked.querySelector('a[href="#source-source-a"]'),
    'a source fact’s cited record is plain text when its target is not rendered');
  assert(unlinked.querySelector('li.brief-proposal .t-caption')?.textContent.includes('source-a'),
    'and the record id is still shown');
}
{
  // A threshold rule's output carries the caveat the briefing carries, in the rail, under the statement. The
  // note text is the exported constant, not a retyped literal, so a wording change cannot leave the rail behind.
  const thresholdText = 'Riski kohonnut rajalle, kynnysarvo ylittyy';
  const thresholdRow = (overrides) => ({ suggestion_id: 's0', turn_id: 'turn-static', ordinal: 0,
    statement_class: 'rule_output', text: thresholdText, label: 'Sääntöpohjainen arvio', source_record_ids: [],
    ...overrides });
  const qualified = renderToStaticMarkup(React.createElement(ChatRail, {
    turn: { ...railTurn, suggestions: [thresholdRow({ basis: ILLUSTRATIVE_THRESHOLD })] },
  }));
  const qualifiedDocument = new JSDOM(qualified).window.document;
  const qualifiedRow = qualifiedDocument.querySelector('li.brief-proposal');
  const captions = [...qualifiedRow.querySelectorAll('.t-caption')].map(node => node.textContent);
  assert(captions.includes(ILLUSTRATIVE_NOTE),
    'an illustrative-threshold rule output renders the exported qualification inside its own row');
  assert.equal(captions.indexOf(ILLUSTRATIVE_NOTE), captions.length - 1,
    'the qualification follows the statement and its citation line, so it is read with the claim');
  // The caveat is earned only by the threshold rule. Without a basis, or on a source fact that happens to carry
  // the same wording, the rail must not add one: a caveat on a claim that did not earn it spreads past its claim.
  const unqualified = renderToStaticMarkup(React.createElement(ChatRail, {
    turn: { ...railTurn, suggestions: [thresholdRow({})] },
  }));
  // Scoped to the statement row: the class legend on an answered turn explains the rule-output class in general and
  // names the qualification as a conditional, so the whole rail carries the wording whatever the row does.
  const rowOf = markup => new JSDOM(markup).window.document.querySelector('li.brief-proposal').textContent;
  assert(!rowOf(unqualified).includes(ILLUSTRATIVE_NOTE),
    'a rule output without a threshold basis renders no qualification in its row');
  const sourceFact = renderToStaticMarkup(React.createElement(ChatRail, {
    turn: { ...railTurn, suggestions: [thresholdRow({ statement_class: 'source_fact', basis: ILLUSTRATIVE_THRESHOLD })] },
  }));
  assert(!rowOf(sourceFact).includes(ILLUSTRATIVE_NOTE),
    'a source fact carrying the same basis renders no qualification in its row: the caveat is for rule outputs only');
}

{
  // A label taken from the statement's own words renders as one line inside its row, between the class tag and
  // the statement text. A row without one renders no label element at all and still renders its statement.
  const headed = renderToStaticMarkup(React.createElement(ChatRail, {
    turn: { ...railTurn, suggestions: [{ suggestion_id: 's0', turn_id: 'turn-static', ordinal: 0,
      statement_class: 'source_fact', text: 'Lääkitys on aktiivinen.', label: 'Lähde: lääkitys · 2026-09-30',
      heading: 'Soitto omahoitajalle', source_record_ids: ['r-heading'] }] },
  }));
  const headedRow = new JSDOM(headed).window.document.querySelector('li.brief-proposal');
  const labels = headedRow.querySelectorAll('[data-heading]');
  assert.equal(labels.length, 1, 'a suggestion with a heading renders exactly one heading element');
  assert.equal(labels[0].textContent, 'Soitto omahoitajalle', 'the heading element carries the server heading as text');
  assert(labels[0].classList.contains('t-body-strong'), 'the heading uses the body-strong type role');
  const statementNode = [...headedRow.querySelectorAll('p')].find(node => node.textContent === 'Lääkitys on aktiivinen.');
  assert(statementNode, 'the statement text is rendered as its own paragraph');
  assert(labels[0].compareDocumentPosition(statementNode) & 4,
    'the heading comes before the statement text in the row');
  const tagNode = headedRow.querySelector('.brief-role-chip');
  assert(tagNode && (tagNode.compareDocumentPosition(labels[0]) & 4),
    'the heading comes after the class tag in the row');
  const plain = renderToStaticMarkup(React.createElement(ChatRail, {
    turn: { ...railTurn, suggestions: [{ suggestion_id: 's0', turn_id: 'turn-static', ordinal: 0,
      statement_class: 'source_fact', text: 'Lääkitys on aktiivinen.', label: 'Lähde: lääkitys · 2026-09-30',
      source_record_ids: ['r-heading'] }] },
  }));
  const plainDocument = new JSDOM(plain).window.document;
  assert.equal(plainDocument.querySelectorAll('[data-heading]').length, 0,
    'a suggestion without a heading renders no heading element');
  assert(plainDocument.querySelector('li.brief-proposal').textContent.includes('Lääkitys on aktiivinen.'),
    'a suggestion without a heading still renders its statement');
}

{
  // The label is the statement's opening clause, so the row shows it once: the statement is rendered without that
  // clause, and what remains is always a suffix of the API's own sentence. Nothing is added to it, and the only
  // separator dropped is the one the engine wrote between label and sentence.
  const rowFor = suggestion => new JSDOM(renderToStaticMarkup(React.createElement(ChatRail, {
    turn: { ...railTurn, suggestions: [{ suggestion_id: 's0', turn_id: 'turn-static', ordinal: 0, source_record_ids: [],
      label: 'Lähde: lääkitys · 2026-09-30', ...suggestion }] },
  }))).window.document.querySelector('li.brief-proposal');
  const sentence = 'Levorotyroksiini 25 mikrog kerran vuorokaudessa on merkitty aktiiviseksi lääkitykseksi.';
  const medication = rowFor({ statement_class: 'source_fact', text: sentence, heading: 'Levorotyroksiini' });
  const medicationHeadings = medication.querySelectorAll('[data-heading]');
  assert.equal(medicationHeadings.length, 1, 'a prefix heading renders exactly one heading element');
  assert.equal(medicationHeadings[0].textContent, 'Levorotyroksiini', 'the label is the heading text');
  const medicationStatement = [...medication.querySelectorAll('p')].find(node => !node.hasAttribute('data-heading')
    && !node.querySelector('.brief-role-chip') && node.textContent.trim());
  assert(medicationStatement, 'the statement renders as its own paragraph');
  assert(!medicationStatement.textContent.includes('Levorotyroksiini'),
    'the statement paragraph does not repeat the heading text');
  assert(sentence.endsWith(medicationStatement.textContent),
    'the rendered statement is a suffix of the API sentence, with nothing added');
  // A remainder keeps its characters in order and drops only the separator and whitespace the engine wrote.
  const dashed = 'Lähteiden ristiriita lääkityksessä — tarkista, kumpi lähde on ajantasainen';
  const dashRow = rowFor({ statement_class: 'rule_output', text: dashed, heading: 'Lähteiden ristiriita lääkityksessä' });
  const dashHeading = dashRow.querySelector('[data-heading]').textContent;
  const dashStatement = [...dashRow.querySelectorAll('p')].find(node => node.textContent.startsWith('tarkista'));
  assert(dashStatement, 'the remainder after the em dash renders without the separator');
  assert(dashed.endsWith(dashStatement.textContent), 'the remainder is a suffix of the API sentence');
  const strip = text => text.replace(/[\s—–\-:,]/g, '');
  assert.equal(strip(dashHeading + dashStatement.textContent), strip(dashed),
    'the heading plus the rendered statement reproduces the original characters in order');
  // A heading that the statement does not open with is not a label for it: the statement stays whole.
  const unrelated = rowFor({ statement_class: 'source_fact', text: 'Lääkitys on aktiivinen.', heading: 'Soitto omahoitajalle' });
  assert(unrelated.textContent.includes('Lääkitys on aktiivinen.'), 'a heading that is not a prefix leaves the statement whole');
  assert.equal(unrelated.querySelector('[data-heading]').textContent, 'Soitto omahoitajalle',
    'a heading that is not a prefix is still rendered as the label');
  // A heading that covers the whole statement leaves nothing to render, so the statement is kept whole.
  const whole = rowFor({ statement_class: 'source_fact', text: 'Soitto omahoitajalle.', heading: 'Soitto omahoitajalle.' });
  assert(whole.textContent.includes('Soitto omahoitajalle.'), 'a heading equal to the whole statement keeps the statement');
  assert.equal([...whole.querySelectorAll('p')].filter(node => node.textContent.trim() === '').length, 0,
    'no empty statement paragraph is rendered');
  // A prefix match inside a word is not a label either, so the word is never split.
  const inWord = rowFor({ statement_class: 'source_fact', text: 'Levorotyroksiini on aktiivinen.', heading: 'Levo' });
  assert(inWord.textContent.includes('Levorotyroksiini on aktiivinen.'), 'a heading that ends inside a word leaves the statement whole');
  // A dash glued to the next word is part of that word: `PHQ-9-yhteispisteet` is one compound, so the heading is
  // shown over the full sentence and nothing is cut from it.
  const glued = 'PHQ-9-yhteispisteet 16, ja kohta 9 2.';
  const gluedRow = rowFor({ statement_class: 'source_fact', text: glued, heading: 'PHQ-9' });
  assert.equal(gluedRow.querySelector('[data-heading]').textContent, 'PHQ-9', 'the glued-compound heading is rendered');
  assert([...gluedRow.querySelectorAll('p')].some(node => node.textContent.includes(glued)),
    'a glued hyphen keeps the full sentence, so the compound word is not split');
  // External evidence keeps its full sentence and its link, with the heading above it.
  const guideline = 'Käypä hoito -suositus tähän tilanteeseen: Kilpirauhasen vajaatoiminta (julkaistu 2024-01-02).';
  const guidelineRow = rowFor({ statement_class: 'external_evidence', text: guideline,
    heading: 'Käypä hoito -suositus', url: 'https://www.kaypahoito.fi/hoi00001' });
  assert(guidelineRow.textContent.includes(guideline), 'an external-evidence statement keeps its full sentence');
  assert(guidelineRow.querySelector('a[href="https://www.kaypahoito.fi/hoi00001"]'),
    'an external-evidence statement keeps its guideline link');
  assert.equal(guidelineRow.querySelector('[data-heading]').textContent, 'Käypä hoito -suositus',
    'an external-evidence statement shows its heading above the unstripped sentence');
}

{
  // The composition line is counted from the statements the reply actually renders, so the reader is told what
  // the answer is made of. The lead is the frame, not a finding, and is never counted.
  // Each fixture gets its own id from its class and position, so no two rendered rows share a React key.
  let fixtureIndex = 0;
  const statement = (statementClass, text = 'Kirjaus.') => ({ suggestion_id: `s-${statementClass}-${fixtureIndex++}`,
    turn_id: 'turn-comp', ordinal: 0, statement_class: statementClass, text, label: 'Lähde', source_record_ids: [] });
  const lead = { suggestion_id: 'lead', turn_id: 'turn-comp', ordinal: 0, statement_class: 'answer_lead',
    text: 'Kysymykseesi tämän palvelutilanteen sallitut kirjaukset vastaavat näin:', label: 'Kysymys', source_record_ids: [] };
  const tail = 'Lauseet on valittu tämän kysymyksen perusteella, eikä niiden määrä kerro koko kirjausjoukon laajuudesta.';
  const answerOf = suggestions => ({ turn_id: 'turn-comp', outcome: 'answer', suggestions });
  assert.equal(answerCompositionText(answerOf([lead, statement('source_fact')])),
    `Vastauksessa on 1 lähdekirjaus. ${tail}`, 'one source fact is named in the singular');
  assert.equal(answerCompositionText(answerOf([lead, statement('rule_output')])),
    `Vastauksessa on 1 sääntöpohjainen arvio. ${tail}`, 'one rule output is named in the singular');
  assert.equal(answerCompositionText(answerOf([lead, statement('external_evidence')])),
    `Vastauksessa on 1 ulkoinen lähde. ${tail}`, 'one external source is named in the singular');
  assert.equal(answerCompositionText(answerOf([lead, statement('model_summary')])),
    `Vastauksessa on 1 tekoälyn muotoilema lause. ${tail}`, 'one model sentence is named in the singular');
  assert.equal(answerCompositionText(answerOf([lead, statement('source_fact'), statement('source_fact'),
    statement('rule_output'), statement('external_evidence')])),
    `Vastauksessa on 2 lähdekirjausta, 1 sääntöpohjainen arvio ja 1 ulkoinen lähde. ${tail}`,
    'a mixed answer joins in the fixed table order with a comma and a final ja');
  assert.equal(answerCompositionText(answerOf([lead, statement('model_summary'), statement('model_summary'),
    statement('source_fact')])),
    `Vastauksessa on 1 lähdekirjaus ja 2 tekoälyn muotoilemaa lausetta. ${tail}`,
    'the join follows the table order, not the order the statements arrived in');
  assert.equal(answerCompositionText(answerOf([lead])), null,
    'an answer with only its lead has no findings to count, so no composition line');
  assert.equal(answerCompositionText({ ...answerOf([lead, statement('source_fact')]), outcome: 'procedural_refusal' }), null,
    'a refusal renders its one sentence and never a composition line');
  assert.equal(answerCompositionText(answerOf([lead, statement('source_fact'), statement('made_up_class')])), null,
    'an unknown class fails closed: a partial count would misstate the answer');
  assert.equal(answerCompositionText(answerOf([lead, statement('source_fact'), { ...lead, suggestion_id: 'lead-2' }])), null,
    'a second answer_lead is a finding the rail renders, so it is counted, and as an unknown class it fails closed');
  assert.equal(answerCompositionText(answerOf([lead, statement('source_fact'), statement('rule_output', '')])), null,
    'a finding with no statement text (the idempotent retry path) renders a blank row, so no composition line');

  const composed = renderToStaticMarkup(React.createElement(ChatRail, {
    turn: { ...railTurn, suggestions: [statement('source_fact'), statement('rule_output')] },
  }));
  const composedLines = new JSDOM(composed).window.document.querySelectorAll('[data-composition="answer"]');
  assert.equal(composedLines.length, 1, 'an answered turn renders exactly one composition line');
  assert(composedLines[0].textContent.includes(tail), 'the composition line carries the constant second sentence');
  assert.equal(composedLines[0].textContent, answerCompositionText({ ...railTurn, suggestions: [statement('source_fact'), statement('rule_output')] }),
    'the rendered composition line is the helper output, word for word');
  const refused = renderToStaticMarkup(React.createElement(ChatRail, {
    turn: { ...railTurn, outcome: 'procedural_refusal', abstention: 'Ei vastausta tähän kysymykseen.' },
  }));
  assert(!refused.includes('data-composition'), 'a refusal turn renders no composition line');
}

{
  // The legend explains the four class tags on the surface where they are read. Its terms are the rendered tag
  // strings themselves: a renamed class changes the label the tag shows and the term the legend defines together.
  const legendRow = (statementClass, text) => ({ suggestion_id: `s-${statementClass}`, turn_id: 'turn-legend', ordinal: 0,
    statement_class: statementClass, text, label: CLASS_LABELS[statementClass], source_record_ids: [] });
  const legendTurn = { ...railTurn, turn_id: 'turn-legend', suggestions: [
    legendRow('source_fact', 'Kirjaus.'), legendRow('rule_output', 'Sääntö.'),
    legendRow('external_evidence', 'Suositus.'), legendRow('model_summary', 'Lause.'),
    { ...legendRow('knowledge_advice', 'Ehdotus.'), refs: ['form:f#q'], basis_note: 'Lähde: Lääkitysarvio · v2' },
  ] };
  const legendDocument = new JSDOM(renderToStaticMarkup(React.createElement(ChatRail, { turn: legendTurn })))
    .window.document;
  const legends = [...legendDocument.querySelectorAll('details')]
    .filter(node => node.querySelector(':scope > summary')?.textContent === 'Mitä merkinnät tarkoittavat?');
  assert.equal(legends.length, 1, 'an answered turn renders the class legend exactly once');
  assert.equal(legends[0].closest('details').parentElement.tagName, 'DIV',
    'the legend is a collapsible disclosure, not an open block');
  const legendTerms = [...legends[0].querySelectorAll('dl > dt')].map(node => node.textContent);
  assert.deepEqual(legendTerms, Object.values(CLASS_LABELS),
    'the legend defines every class label, in the order the classes are declared');
  const renderedTags = [...legendDocument.querySelectorAll('li.brief-proposal > p.t-body')]
    .map(node => node.firstElementChild.textContent);
  assert.deepEqual([...new Set(legendTerms)].sort(), [...new Set(renderedTags)].sort(),
    'the legend terms are exactly the tag strings the turn rendered, so the two cannot drift apart');
  assert.equal(legendDocument.querySelectorAll('dl > dd').length, legendTerms.length,
    'each legend term has exactly one gloss');
  const emptyRail = renderToStaticMarkup(React.createElement(ChatRail, {}));
  assert(!emptyRail.includes('Mitä merkinnät tarkoittavat?'),
    'the empty state has no class tags to explain, so the legend is absent');
}

const picture = {
  persona_id: 'persona-001', encounter_id: 'encounter-test', role: 'sairaanhoitaja', purpose: 'mental_health_assessment',
  situation: { situation_type: 'valmistelu', confidence: 'high' },
  risk: { risk_level: 'kohtalainen', reason: 'Sääntöpohjainen peruste.' },
  actions: ['Varmista tiedot asiakkaalta.'],
  trace: [{ table: 'situation', rule_id: 'rule-1', output: { matched: true }, matched: [] }],
  pipeline_trace_id: 'opaque-trace-0123456789',
  pipeline_trace: [{ stage: 'foundry.generation', elapsed_ms: 100, model: 'gpt-6-luna',
    input_tokens: 12, output_tokens: 10, status: 'generated' }],
  grounded: true,
  grounding: { dropped_citations: ['source-c'], dropped_actions: [], rejected_sentences: [{ sentence_id: 's2', reason: 'unsupported' }] },
  selection: {
    baseline_record_ids: ['source-a', 'source-a'], additional_record_ids: ['source-b'], emphasized_record_ids: ['source-a'],
    rule_version: 'selection-v1', decision_status: 'complete',
    decision_notes: [
      { record_id: 'source-b', status: 'relevant', model: 'decision-1.13.0', elapsed_ms: 21, input_tokens: 32,
        relevance_probabilities: { relevant: 0.82, irrelevant: 0.1, unclear: 0.08 }, relevance_confidence: 0.9,
        gate_probabilities: { urgency: { '0': 0, '1': 0, '2': 0.1, '3': 0.8, '4': 0.1 } },
        time_sensitivity: 'current', negation_probability: 0.03, subject_binding: 'person' },
      { record_id: 'bound-note-018', status: 'unclear', gate_refused: true, model: 'decision-1.13.0' },
    ],
    decision_candidate_count: 4, decision_assessed_count: 2, decision_not_shortlisted_count: 1,
    decision_budget_exhausted_count: 1, decision_gate_refused_count: 1,
  },
  records: { priority: [{ record_id: 'source-a', record_type: 'arviointi', topic: 'PHQ-9', source: 'Lomake', recorded_at: '2026-09-28T00:00:00Z', payload: { instrumentti: 'PHQ-9', vastaukset: { q1: 2 }, unlisted_secret: 'must not appear' }, selection_reasons: ['assessment:latest-relevant'] }], accessible_other: [{ record_id: 'source-b', record_type: 'vapaateksti', topic: 'mielenterveys', source: 'Note', recorded_at: '2026-09-29T00:00:00Z', payload: { otsikko: 'Vastaanotto', teksti: 'Asiakas kuvaa mielialan vaihtelua.', unlisted_secret: 'must not appear' }, selection_reasons: ['keyword:theme'] }] },
  citations: [{ record_id: 'source-a' }, { record_id: 'source-a' }],
  assessments: [{ instrumentti: 'PHQ-9', instrument_version: 'demo-v2', valmis: false, total: null, max_total: 27, tulkinta: null, osiot: [{ osio: 'q1', kysymys: 'Kohta 1', pisteet: null }], lahteet: ['source-a'], temporal_status: 'history' }],
  conflicts: [{ drug: 'Lääke', medication_record_id: 'source-a', note_record_id: 'source-b' }],
  proposals: [], narrative: 'Hyväksytty lause.',
  intended_use: 'Näyttää ammattilaiselle tämän palvelutilanteen kannalta olennaiset ja sallitut esitiedot, '
    + 'jotka on koottu lähteistä ja tarkistettu lähdeviitteisiin. Ei ole kliininen päätös eikä lääkinnällinen laite.',
  narration_generation_status: 'generated', narration_generation_deployment: 'deployment', narration_generation_path: 'agent_reference', narration_generation_model: 'model', narration_generation_elapsed_ms: 100,
  narration_support_status: 'supported', narration_support_deployment: 'deployment', narration_support_model: 'decision-1.13.0', narration_support_elapsed_ms: 50,
  narration_acceptance_status: 'accepted', narration_elapsed_ms: 150,
};
const mapped = picturePresentation(picture, { personName: 'Aino', roleLabel: 'Sairaanhoitaja', purposeLabel: 'Mielialan arviointi' });
assert.equal(mapped.personName, 'Aino');
// The risk verdict's per-record provenance is mapped straight through from the backend, never
// re-derived or re-worded in the browser: the reason strings are the rule's own sentences. The
// conflict rule's per-record reason names the disagreement, not a side of it (defect 1 fix): it
// must pass through exactly as the backend wrote it, same as every other rule's reason.
const withRiskReasons = picturePresentation({ ...picture, risk_reasons_by_record: { 'source-a': ['Lähteiden ristiriita lääkityksessä — tarkista, kumpi lähde on ajantasainen'] } });
assert.deepEqual(withRiskReasons.sources.find(row => row.id === 'source-a').riskReasonTexts,
  ['Lähteiden ristiriita lääkityksessä — tarkista, kumpi lähde on ajantasainen']);
// A record with no risk provenance carries an empty list, not undefined -- the view checks length.
assert.deepEqual(withRiskReasons.sources.find(row => row.id === 'source-b').riskReasonTexts, []);
assert.deepEqual(mapped.sources.find(row => row.id === 'source-a').riskReasonTexts, [],
  'no map on the picture means no risk reasons, and no invented ones');
// There is no `demoName` on the presentation: the adapter used to carry a presenter alias that no
// component ever read, so the alias is gone rather than passed around. What the header renders is the
// source-backed name when identity is available, and the neutral line when it is not.
const demoAlias = picturePresentation(picture, { exampleLabel: 'Elias Korhonen', identityAvailable: false });
assert.equal(demoAlias.personName, 'Henkilö');
const neutralIdentity = picturePresentation({ ...picture, identity_header: 'Henkilöllisyystieto ei saatavilla' });
assert.equal(neutralIdentity.personName, 'Henkilöllisyystieto ei saatavilla');
// The declared purpose of use is served, not invented in the browser (ADR-0001).
assert.equal(mapped.intendedUse, picture.intended_use);
assert.equal(picturePresentation({ ...picture, intended_use: null }).intendedUse, null);
const selectedSourceName = personNameForEncounter({
  fixture_label: 'Aino Esimerkki · DEMO-007',
  person_name: 'Alternate name',
  demo_name: 'Presenter alias',
});
assert.equal(selectedSourceName, 'Aino Esimerkki · DEMO-007');
const sourcedIdentity = picturePresentation(
  { ...picture, identity_header: 'Fallback identity' },
  { personName: selectedSourceName, identityAvailable: true },
);
assert.equal(sourcedIdentity.personName, 'Aino Esimerkki · DEMO-007');
const aliasOnlyName = personNameForEncounter({ demo_name: 'Presenter alias' });
assert.equal(aliasOnlyName, undefined);
const aliasOnlyView = picturePresentation(picture, { personName: aliasOnlyName, identityAvailable: false });
assert.equal(aliasOnlyView.personName, 'Henkilö');
assert.deepEqual(mapped.selectedSourceIds, ['source-a', 'source-b']);
assert.deepEqual(mapped.citedSourceIds, ['source-a']);
assert.deepEqual(mapped.narration.inputRecordIds, ['source-a', 'source-b']);
const filteredNarration = picturePresentation({ ...picture, narration_input_record_ids: ['source-a'] });
assert.deepEqual(filteredNarration.narration.inputRecordIds, ['source-a']);
assert.deepEqual(filteredNarration.selectedSourceIds, ['source-a', 'source-b']);
assert.deepEqual(mapped.narration.citedRecordIds, ['source-a']);
assert.equal(mapped.sources.length, 2);
assert.equal(mapped.sources[0].excerpt, 'Toimintakykymittari: PHQ-9');
assert.equal(mapped.sources[1].excerpt, 'Otsikko: Vastaanotto · Kirjaus: Asiakas kuvaa mielialan vaihtelua.');
assert.equal(mapped.sources[1].decisionOutcome.status, 'relevant');
assert.equal(mapped.sources[1].selectionReasonTexts[0], 'Kirjauksen aihe sopii palvelutilanteeseen.');
assert(!JSON.stringify(mapped.sources).includes('unlisted_secret'));
assert(!JSON.stringify(mapped.sources).includes('vastaukset'));
assert.equal(mapped.summary, 'Hyväksytty lause.');
assert.equal(mapped.narration.generationDeployment, 'deployment');
assert.equal(mapped.narration.generationPath, 'agent_reference');
assert.equal(picturePresentation({ ...picture, narration_generation_path: 'direct_fallback' }).narration.generationPath, 'direct_fallback');
assert.equal(mapped.narration.supportModel, 'decision-1.13.0');
assert.equal(mapped.narration.elapsedMs, 150);
assert.equal(mapped.assessments[0].osiot[0].pisteet, null);
assert.equal(mapped.assessments[0].instrument_version, 'demo-v2');
assert.equal(mapped.assessments[0].temporal_status, 'history');
assert.equal(mapped.selection.decision_notes[0].model, 'decision-1.13.0');
assert.deepEqual(mapped.selection.decision_notes[0].gate_probabilities.urgency, { '0': 0, '1': 0, '2': 0.1, '3': 0.8, '4': 0.1 });
assert.equal(mapped.selection.decision_notes.length, 1, 'unselected Decision-1 outcomes do not disclose withheld identities');
assert.equal(mapped.situation.situation_type, 'valmistelu');
assert.equal(mapped.risk.reason, 'Sääntöpohjainen peruste.');
assert.deepEqual(mapped.actions, ['Varmista tiedot asiakkaalta.']);
assert.equal(mapped.trace[0].rule_id, 'rule-1');
assert.equal(mapped.pipelineTrace[0].stage, 'foundry.generation');
assert.equal(mapped.pipelineTraceId, 'opaque-trace-0123456789');
assert.equal(mapped.grounding.dropped_citations.length, 1);
assert.equal(mapped.narration.generationInputTokens, null);
assert.equal(mapped.conflicts.length, 1);
assert.deepEqual(mapped.proposals, []);
// B2: two renders of one Picture. The briefing is what the professional reads; the technical page
// (`TechnicalView`) is the production material -- the presenter tools, the comparison, the coverage and impact
// cards, the Decision-1 trace, the intended use and the build stamp -- which used to sit in a disclosure at the
// foot of the briefing and is a destination of its own now. Every assertion below names the surface it is about.
const briefProps = { picture, personName: 'Aino', roleLabel: 'Sairaanhoitaja', purposeLabel: 'Mielialan arviointi' };
const visibleBrief = renderToStaticMarkup(React.createElement(PictureBriefing, briefProps));

// ---- D3: Decision-1's time-sensitivity beside the record's "why" chip ----
//
// The fixture's `source-b` note is `relevant` and `current`, so its row carries both chips and the ordering
// caveat the day view's lead line uses; `historical` reads as the model's own word, and `unclear` or an
// unanswered question adds nothing.
assert(visibleBrief.includes('Olennainen tälle käynnille') && visibleBrief.includes('>Ajankohtainen<'),
  'a record Decision-1 called current carries the Ajankohtainen chip beside its why chip');
assert(!visibleBrief.includes('(järjestysperuste, ei hoidon kiireellisyys)'),
  'the ordering caveat is no longer printed beside every chip (owner, 2026-10-14); the technical page keeps it');
{
  const withTiming = sensitivity => renderToStaticMarkup(React.createElement(PictureBriefing, { ...briefProps,
    picture: { ...picture, selection: { ...picture.selection, decision_notes: picture.selection.decision_notes.map(note =>
      note.record_id === 'source-b' ? { ...note, time_sensitivity: sensitivity } : note) } } }));
  assert(withTiming('historical').includes('Aiempi (mallin mukaan)'), 'historical renders as the model\'s own word');
  const unclear = withTiming('unclear');
  assert(!unclear.includes('Ajankohtainen') && !unclear.includes('Aiempi (mallin mukaan)'), 'unclear adds no chip');
}

// ---- D2: accept / edit on the pre-filled form ----
//
// One answered question offers two small buttons; a recorded verdict renders its tag and no controls; the copy
// text carries the professional's wording once an edit is recorded, and the proposal's until then.
{
  const form = { form_id: 'laakityksen-seuranta', name: 'Lääkityksen seuranta', form_role: 'official',
    generation_status: 'generated', verified: 1, total_questions: 2, items: [
      { question_id: 'laakehoito', question_text: 'Nykyinen lääkehoito', status: 'answered', answer_text: 'Amlodipiini 5 mg',
        source_record_ids: ['source-a'], category_name: 'Lääkitys' },
      { question_id: 'haitat', question_text: 'Haittavaikutukset', status: 'unknown', category_name: 'Lääkitys' },
    ] };
  const withForm = (reviews = {}) => renderToStaticMarkup(React.createElement(PictureBriefing, { ...briefProps,
    picture: { ...picture, prefilled_forms: [form] }, prefillReviews: reviews, onPrefillReview: () => Promise.resolve(true) }));
  const offered = withForm();
  assert(offered.includes('>Vahvista<') && offered.includes('>Muokkaa<'), 'an answered question offers Vahvista and Muokkaa');
  assert(!offered.includes('Vahvistettu') && !offered.includes('>Muokattu<'), 'no verdict is shown before one is recorded');
  const key = prefillReviewKey('laakityksen-seuranta', 'laakehoito');
  const confirmed = withForm({ [key]: { status: 'saved', verdict: 'vahvistettu', editedText: null, recordedAt: '2026-10-11T09:00:00Z' } });
  assert(confirmed.includes('Vahvistettu') && !confirmed.includes('>Vahvista<') && !confirmed.includes('>Muokkaa<'),
    'a recorded verdict renders its tag and no controls');
  assert(confirmed.includes('Amlodipiini 5 mg'), 'a confirmed answer keeps the proposal\'s text');
  const edited = withForm({ [key]: { status: 'saved', verdict: 'muokattu', editedText: 'Amlodipiini 5 mg, otettu epäsäännöllisesti' } });
  assert(edited.includes('>Muokattu<') && edited.includes('Amlodipiini 5 mg, otettu epäsäännöllisesti'),
    'an edited answer renders the professional\'s wording under the Muokattu tag');
  const decided = withForm({ [key]: { status: 'decided', verdict: 'vahvistettu', editedText: null } });
  assert(decided.includes('Vahvistettu') && !decided.includes('>Vahvista<'), 'a 409 (already recorded) renders as the recorded verdict');
  // A loaded verdict seeds the same shape a live save reaches; the last verdict on an answer stands, and an
  // entry already held (a write in flight) is not overwritten by the load.
  const seeded = seedPrefillReviews({}, { reviews: [
    { review_id: 'r1', form_id: 'f', question_id: 'q', verdict: 'vahvistettu', edited_text: null, decided_at: '2026-10-11T09:00:00Z' },
    { review_id: 'r2', form_id: 'f', question_id: 'q', verdict: 'muokattu', edited_text: 'uusi', decided_at: '2026-10-11T10:00:00Z' },
  ] });
  assert.deepEqual(seeded, { [prefillReviewKey('f', 'q')]: { status: 'saved', verdict: 'muokattu', editedText: 'uusi', reviewId: 'r2',
    recordedAt: '2026-10-11T10:00:00Z' } }, 'the last loaded verdict on an answer stands');
  const held = { [prefillReviewKey('f', 'q')]: { status: 'saving', verdict: 'vahvistettu' } };
  assert.equal(seedPrefillReviews(held, { reviews: [{ review_id: 'r1', form_id: 'f', question_id: 'q', verdict: 'hylätty' }] })[prefillReviewKey('f', 'q')].status,
    'saving', 'a write in flight is newer than the load');
}

// ---- D4: the day view says when the briefings were last prepared ----
{
  const dayProps = { day: '2026-10-14', days: [{ day: '2026-10-14', appointments: 1 }], rows: [], ready: 0 };
  const prepared = renderToStaticMarkup(React.createElement(DayView, { ...dayProps, warmup: { last_built_at: '2026-10-14T05:30:00Z' } }));
  assert(/Valmisteltu viimeksi [^<]*2026/.test(prepared), 'a prepared cache renders its date and time');
  const unprepared = renderToStaticMarkup(React.createElement(DayView, { ...dayProps, warmup: { last_built_at: null } }));
  assert(unprepared.includes('Esitietoja ei ole vielä valmisteltu'), 'a null warm-up is said in words');
  const unknown = renderToStaticMarkup(React.createElement(DayView, dayProps));
  assert(!unknown.includes('Valmisteltu viimeksi') && !unknown.includes('Esitietoja ei ole vielä valmisteltu'),
    'before the day has answered, no readiness claim is made');
}
const visibleTechnical = renderToStaticMarkup(React.createElement(TechnicalView, {
  ...briefProps,
  failureComparison: { decisionOff: { ...picture, selection: { ...picture.selection, additional_record_ids: [] } },
    foundryOff: { ...picture, narrative: null, narration_acceptance_status: 'rejected', narration_support_status: 'not_run' } },
}));
// The briefing ends at the material about the encounter: the production material is on the technical page and
// the briefing's last line says the boundary and links to the page. No disclosure remains.
assert(!visibleBrief.includes('run-specifications') && !visibleBrief.includes('brief-technical')
  && !visibleBrief.includes('presenter-tools') && !visibleBrief.includes('section-coverage'),
'the briefing carries no run-specifications disclosure, presenter surface or coverage card');
assert(/<p class="t-caption brief-footer">Tekoäly järjesti ja muotoili; se ei päättänyt\. <a href="#brief-content"[^>]*>Tekniset tiedot<\/a><\/p>$/.test(visibleBrief.trimEnd()),
  'the briefing ends with the boundary sentence and the link to the technical page');
assert(visibleTechnical.includes('id="section-coverage"') && visibleTechnical.includes('id="section-impact"')
  && visibleTechnical.includes('id="presenter-tools"'),
'the technical page keeps the coverage, impact and presenter ids the deep links name');
assert(/<h2[^>]*>Tekniset tiedot<\/h2>/.test(visibleTechnical) && !visibleTechnical.includes('<h1'),
  'the technical page is titled by an h2: the shell Header owns the only h1');
assert(visibleTechnical.includes('<strong>Kerronnan reitti:</strong> Valinta: '),
  'the status line sits under the Kerronnan reitti prefix');
// D6: the scope caption is on every briefing render, as one sentence with no digit after it.
const scopeCaptionFollowedByDigit = new RegExp(`${LENS_SCOPE_NOTE.replace(/[.]/g, '\\.')}\\s*(<[^>]*>\\s*)*\\d`);
assert(!/\d/.test(LENS_SCOPE_NOTE), 'the scope caption itself carries no number');
assert(scopeCaptionFollowedByDigit.test(`${LENS_SCOPE_NOTE} <span>3</span>`),
  'the digit probe is non-vacuous: a count after the caption is caught');
assert(visibleBrief.includes(`<p class="t-caption brief-lens-scope">${LENS_SCOPE_NOTE}</p>`)
  && !scopeCaptionFollowedByDigit.test(visibleBrief),
'the briefing carries the scope caption as its own line, and no number follows it');
assert(!visibleTechnical.includes(LENS_SCOPE_NOTE), 'the caption is the briefing\'s, not the technical page\'s');
const fallbackTechnical = renderToStaticMarkup(React.createElement(TechnicalView, {
  ...briefProps, picture: { ...picture, narration_generation_path: 'direct_fallback' },
}));
assert(fallbackTechnical.includes('Suora mallikutsu (varapolku)') && !fallbackTechnical.includes('Direct-call'),
  'the technical page labels the configured-agent fallback plainly, in Finnish');

// ---- Work order I: the build stamp on the technical page ----
// Vite captures VITE_* from the environment when a server is created, so each case gets a server of its own
// and the variable is restored afterwards. The stamp must appear once with its value, or not at all.
async function renderedBuildStamp(value) {
  const saved = process.env.VITE_BUILD_REVISION;
  if (value === undefined) delete process.env.VITE_BUILD_REVISION; else process.env.VITE_BUILD_REVISION = value;
  const stampServer = await createServer({
    root: fileURLToPath(new URL('..', import.meta.url)),
    server: { middlewareMode: true, hmr: false },
    appType: 'custom',
  });
  try {
    const { TechnicalView: Technical } = await stampServer.ssrLoadModule('/src/TechnicalView.jsx');
    return renderToStaticMarkup(React.createElement(Technical, briefProps));
  } finally {
    await stampServer.close();
    if (saved === undefined) delete process.env.VITE_BUILD_REVISION; else process.env.VITE_BUILD_REVISION = saved;
  }
}
{
  const stamped = await renderedBuildStamp('rev-0123abcd');
  const stamps = [...stamped.matchAll(/<p[^>]*data-build="([^"]*)"[^>]*>([^<]*)<\/p>/g)];
  assert.equal(stamps.length, 1, 'a real build revision renders exactly one [data-build] element');
  assert.equal(stamps[0][1], 'rev-0123abcd', 'the stamp carries the build revision it was compiled with');
  assert(stamps[0][2].includes('Käytössä oleva koonti') && stamps[0][2].includes('rev-0123abcd'),
    'the stamp is labelled as the running build, not as a deployed claim');
}
{
  const padded = await renderedBuildStamp(' unknown ');
  assert(!padded.includes('data-build'), 'a whitespace-padded unknown renders no [data-build] element');
  assert(!padded.includes('Käytössä oleva koonti'), 'a whitespace-padded unknown claims no build');
  const shouted = await renderedBuildStamp('UNKNOWN');
  assert(!shouted.includes('data-build'), 'UNKNOWN in any capitalisation renders no [data-build] element');
  const blank = await renderedBuildStamp('');
  assert(!blank.includes('data-build'), 'an empty build revision renders no [data-build] element');
  const named = await renderedBuildStamp('rev-0123abcd');
  assert(named.includes('Käytössä oleva koonti: rev-0123abcd (tämän sivun koonti, ei palvelimen versio)'),
    'a real revision is labelled as this page\'s bundle build, not the server version');
}
for (const absent of [undefined, 'unknown']) {
  const plain = await renderedBuildStamp(absent);
  assert(!plain.includes('data-build'), `no build revision (${absent ?? 'unset'}) renders no [data-build] element`);
  assert(!plain.includes('Käytössä oleva koonti'), 'and claims no build when none is known');
}
for (const visible of ['source-b', 'Sääntöpohjainen arvio', 'Sääntöpohjainen peruste.']) {
  assert(visibleBrief.includes(visible), `brief renders ${visible}`);
}
for (const visible of ['Mitä tekoäly teki', 'Decision-1 lisäsi', 'source-b', '0.82',
  'rule-1', 'Versionoitu Prompt Agent', 'deployment', 'Decision-1 pois päältä', 'Foundry pois päältä',
  'ei tuettu', 'Vaihekohtainen jälki', 'foundry.generation', '100 ms', 'opaque-trace',
  'kiireellisyys (järjestysperuste, ei hoidon kiireellisyys)']) {
  assert(visibleTechnical.includes(visible), `the technical page renders ${visible}`);
}
// The limit is the point of that line, so it is asserted as wording and not only as a substring: the urgency
// row has to say it is an ordering basis and NOT how urgent the patient's care is, and it has to carry the
// classifier's own leading level in words rather than a number the browser picked.
assert(visibleTechnical.includes('nopea huomio'),
  'the urgency row shows the classifier\'s leading level in words');
assert(!/kiireellisyys \(järjestys\):/.test(visibleTechnical),
  'the unqualified ordering label is gone: the row must name its own limit');
// The old expectation here was `'Hylätty lause', 'Lause s2 pidätettiin'` in the ALERT panel, and it was
// wrong, not merely stale: it pinned one action-list row per withheld generated sentence, led by an
// internal sentence id and a description of the generation pipeline ("Lause ei näy koosteen tekstissä"
// is a statement, not an act a professional can take). That moved a pipeline event into the reader's
// list of things needing attention and inflated the alert count; with more withheld sentences it buried
// the real conflicts. Withholding is now stated once, beside the summary, in the professional's terms
// and without identifiers or counts, and the per-sentence machinery lives in the technical disclosure.
const withheldCaption = 'Osa tämän koosteen koneellisesti tuotetusta tekstistä jätettiin näyttämättä, koska sitä ei voitu vahvistaa lähteisiin.';
assert(visibleBrief.includes(withheldCaption),
  'a withheld sentence is disclosed once, beside the summary, in the professional\'s terms');
assert(!/\d/.test(withheldCaption), 'the withheld caption carries no sentence count');
assert(!/Lause\s+s\d|withheld-/.test(withheldCaption), 'the withheld caption names no sentence identifier');
assert(!/[Pp]iilot/.test(withheldCaption), 'the withheld caption does not say text was hidden');
assert(!/[Kk]ieltäyty/.test(withheldCaption), 'the withheld caption does not imply the model refused');
assert(visibleTechnical.includes('Näyttämättä jätetyt lauseet')
  && visibleTechnical.includes('Lause s2 · hylätty · syy: ei tuettu (unsupported)'),
  'the per-sentence id, verdict and both reason forms stay auditable on the technical page');
assert(!visibleBrief.includes('Näyttämättä jätetyt lauseet') && !visibleBrief.includes('Lause s2'),
  'and never on the briefing');
// The method caption is not a second occurrence notice, and the occurrence caption is not a second method
// claim: one still renders when nothing was withheld, the other only when something was.
const noWithheldBrief = renderToStaticMarkup(React.createElement(PictureBriefing, {
  picture: { ...picture, grounding: { ...picture.grounding, rejected_sentences: [] } },
  personName: 'Aino', roleLabel: 'Sairaanhoitaja', purposeLabel: 'Mielialan arviointi',
}));
assert(noWithheldBrief.includes('koneellisesti tuotettu'),
  'the method caption stays when nothing was withheld');
assert(!noWithheldBrief.includes(withheldCaption),
  'the occurrence caption is absent when nothing was withheld');
// The grounding row must not call the withholding a rejection. `narration_rejection_reason` is
// `partial_support` on the shipped medication-conflict encounter, whose narration was ACCEPTED
// (`narration_acceptance_status: accepted`, `narration_support_status: supported`); the old line rendered
// it as "Grounding: verifioitu · hylkäyksen syy: partial_support", asserting a rejection that never
// happened and contradicting the acceptance the same payload carries.
const acceptedWithheldBrief = renderToStaticMarkup(React.createElement(TechnicalView, {
  picture: { ...picture, narration_rejection_reason: 'partial_support',
    grounding: { ...picture.grounding, rejected_sentences: [{ sentence_id: 's3', reason: 'unknown' }] } },
  personName: 'Aino', roleLabel: 'Sairaanhoitaja', purposeLabel: 'Mielialan arviointi',
}));
assert(acceptedWithheldBrief.includes('Lähdetarkistus: verifiointi ajettu · kooste hyväksytty, mutta osa lauseista'
  + ' jätettiin näyttämättä, koska niiden tuki lähteissä jäi riittämättömäksi · syykoodi: partial_support.'),
'an accepted briefing with withheld sentences says so and keeps `partial_support` as the machine reason');
assert(!acceptedWithheldBrief.includes('hylkäyksen syy') && !acceptedWithheldBrief.includes('ei hyväksytty'),
  'withholding is not presented as a rejection when the briefing was accepted');
assert(acceptedWithheldBrief.includes('syykoodi: partial_support'),
  'the machine-readable reason stays visible on the technical page');
assert(!acceptedWithheldBrief.includes('Grounding:'), 'the row is labelled in Finnish, not with the English term');
// A briefing that really was rejected still says so, with its own reason code, and never claims acceptance.
const rejectedBrief = renderToStaticMarkup(React.createElement(TechnicalView, {
  picture: { ...picture, narrative: null, narration_acceptance_status: 'rejected', narration_support_status: 'unsupported',
    narration_rejection_reason: 'unsupported', grounding: { ...picture.grounding, rejected_sentences: [{ sentence_id: 's1', reason: 'unsupported' }] } },
  personName: 'Aino', roleLabel: 'Sairaanhoitaja', purposeLabel: 'Mielialan arviointi',
}));
assert(rejectedBrief.includes('Lähdetarkistus: verifiointi ajettu · koostetta ei hyväksytty · syykoodi: unsupported.'),
  'a rejected briefing says it was not accepted, with the machine reason');
assert(!rejectedBrief.includes('kooste hyväksytty'), 'a rejected briefing never claims it was accepted');
// Verification that never ran is not described as having run. `not_run` is the Foundry-unavailable path.
const notRunBrief = renderToStaticMarkup(React.createElement(TechnicalView, {
  picture: { ...picture, narrative: null, narration_acceptance_status: 'rejected', narration_support_status: 'not_run',
    narration_rejection_reason: 'foundry_unavailable', grounding: { ...picture.grounding, rejected_sentences: [] } },
  personName: 'Aino', roleLabel: 'Sairaanhoitaja', purposeLabel: 'Mielialan arviointi',
}));
assert(notRunBrief.includes('Lähdetarkistus: verifiointia ei ajettu · koostetta ei hyväksytty · syykoodi: foundry_unavailable.'),
  'a briefing whose verification never ran does not claim it did');

assert(!visibleBrief.includes('bound-note-018') && !visibleTechnical.includes('bound-note-018'),
  'an unselected abstention identity stays hidden on both pages');
// The prose carries an on-screen statement that a model drafted it, and the declared purpose of use
// renders on the technical page. Neither may appear when it has nothing to describe.
assert(visibleBrief.includes('koneellisesti tuotettu'), 'the machine-drafted statement renders beside the prose');
const fallbackBrief = renderToStaticMarkup(React.createElement(PictureBriefing, {
  ...briefProps, picture: { ...picture, narration_generation_path: 'direct_fallback' },
}));
assert(fallbackBrief.includes('koneellisesti tuotettu'),
  'the statement follows the accepted prose, not the generation path');
const noProse = renderToStaticMarkup(React.createElement(PictureBriefing, {
  picture: { ...picture, narrative: null, narration_acceptance_status: 'rejected', narration_support_status: 'not_run' },
  personName: 'Aino', roleLabel: 'Sairaanhoitaja', purposeLabel: 'Mielialan arviointi',
}));
assert(!noProse.includes('koneellisesti tuotettu'),
  'a withheld prose view makes no machine-drafted claim (fail-closed stays fail-closed)');
assert(visibleTechnical.includes('Ei ole kliininen päätös eikä lääkinnällinen laite.'),
  'the served purpose of use renders on the technical page');
// The purpose row is served, not hardcoded in the browser: with the field absent it renders nothing, so an
// older payload and the current one differ only by that one line. It is independent of the prose, so a
// fail-closed view still states what the system is for.
const noDeclaration = renderToStaticMarkup(React.createElement(TechnicalView, {
  ...briefProps, picture: { ...picture, intended_use: null },
}));
assert(!noDeclaration.includes('Ei ole kliininen päätös eikä lääkinnällinen laite.'),
  'a picture without the served purpose renders no purpose row');
assert(renderToStaticMarkup(React.createElement(PictureBriefing, { ...briefProps, picture: { ...picture, intended_use: null } }))
  .includes('koneellisesti tuotettu'),
'the machine-drafted statement does not depend on the declared purpose being served');
assert(visibleTechnical.includes('Decision-1 arvioi kirjauksen olennaiseksi.'), 'Decision-1 outcome is readable beside its probabilities');
assert(!visibleBrief.includes('Keksitty väite.') && !visibleTechnical.includes('Keksitty väite.'),
  'unsupported candidate text never crosses the Picture boundary');
assert(!visibleBrief.includes('must not appear') && !visibleTechnical.includes('must not appear'));
// The panel is one card of stacked severity rows. Each row is a `<div>` with the same shape; the count
// sits in the card header beside the title rather than inside the heading text.
const coverageAlert = visibleBrief.match(/<div class="brief-severity brief-warning brief-alert-row"[\s\S]*?<\/div><\/div>/)?.[0] ?? '';
// The alerts panel is two labelled groups. The first is the attention group ("Tarkistettavat"): its caption
// counts the rows in that group, and only that group. The second is the status group ("Tilanne"), which
// carries no count. Each row is a `<div class="brief-alert-row">`, so the rows are counted from the markup the
// professional sees, and the caption is checked against that count rather than against a literal.
const alertGroups = html => {
  const at = html.indexOf('<section id="section-alerts"');
  const panel = at < 0 ? '' : html.slice(at, html.indexOf('</section>', at));
  return panel.split('<div class="brief-alert-group">').slice(1);
};
const attentionGroup = html => alertGroups(html)[0] ?? '';
const attentionRowCount = html => (attentionGroup(html).match(/brief-alert-row/g) ?? []).length;
const attentionCaption = html => attentionGroup(html).match(/<p class="t-caption">(\d+) kohdetta<\/p>/)?.[1];
const renderedAttention = attentionRowCount(visibleBrief);
assert(renderedAttention > 0, 'the attention group renders at least one row');
assert(/<h2[^>]*>Tilanne ja hälytykset<\/h2>/.test(visibleBrief)
  && /<h3[^>]*>Tarkistettavat<\/h3>/.test(visibleBrief),
  'the panel is headed by its card title and the attention group heading');
assert.equal(attentionCaption(visibleBrief), String(renderedAttention),
  `the attention caption equals the number of rows in the attention group (${renderedAttention})`);
// This fixture withholds one sentence. The withheld sentence contributes no row and no number, so the attention
// group holds exactly the three actionable rows (the coverage gap, the conflict and the warning-level risk) plus
// Decision-1's own finding rows (`decisionHighlights`), which are counted separately here so the withheld
// sentence is still shown to add nothing.
const withheldSentences = picture.grounding.rejected_sentences.length;
assert(withheldSentences === 1, 'the fixture withholds exactly one sentence, against which the count is checked');
const decisionRows = (attentionGroup(visibleBrief).match(/<strong>Decision-1\.<\/strong>/g) ?? []).length;
assert(renderedAttention === 3 + decisionRows,
  `withheld sentences add no attention row (${renderedAttention} rows, ${decisionRows} Decision-1 rows, ${withheldSentences} withheld)`);
assert(attentionCaption(visibleBrief) !== String(renderedAttention + withheldSentences),
  'the attention caption never counts a withheld sentence');
const alertsPanelHtml = (() => {
  const at = visibleBrief.indexOf('<section id="section-alerts"');
  return at < 0 ? '' : visibleBrief.slice(at, visibleBrief.indexOf('</section>', at));
})();
assert(!/Lause\s+s\d/.test(alertsPanelHtml) && !alertsPanelHtml.includes('Hylätty lause'),
  'the professional\'s alert panel carries no sentence identifier and no pipeline verdict');
assert(coverageAlert.includes('class="icon"'), 'the severity row carries the library icon class');
assert(coverageAlert.indexOf('class="icon"') < coverageAlert.indexOf('<strong>Varoitus.</strong>')
  && coverageAlert.indexOf('<strong>Varoitus.</strong>') < coverageAlert.indexOf('Valinnan kattavuus')
  && coverageAlert.indexOf('Valinnan kattavuus') < coverageAlert.indexOf('Siirry kattavuusriveihin'),
'alert order is icon, severity, fact, then act');
const exaggeratedPicture = { ...picture, selection: { ...picture.selection, decision_candidate_count: 987,
  decision_assessed_count: 987, decision_not_shortlisted_count: 987, decision_budget_exhausted_count: 987,
  decision_unavailable_count: 987, decision_gate_refused_count: 987 } };
const exaggeratedCoverage = renderToStaticMarkup(React.createElement(PictureBriefing, { picture: exaggeratedPicture }));
const exaggeratedTechnical = renderToStaticMarkup(React.createElement(TechnicalView, { picture: exaggeratedPicture }));
assert(exaggeratedTechnical.includes('1 Decision-1-tulosriviä / 1 näkyvää vapaatekstikirjausta.')
  && !exaggeratedTechnical.includes('987') && !exaggeratedCoverage.includes('987'),
'coverage numbers use only the visible source and outcome rows, on both pages');
assert.equal(attentionCaption(exaggeratedCoverage), String(renderedAttention),
  `the count is unchanged by larger non-rendered coverage numbers (${renderedAttention} rendered rows)`);
assert(visibleBrief.includes('class="brief-risk brief-alert-row brief-warning"'), 'kohtalainen deterministic risk uses warning');
assert(visibleBrief.includes('class="brief-alert-neutral brief-alert-row"') && visibleBrief.includes('Lähteiden ristiriita'),
  'a conflict is not status-coloured outside the binding table');
const criticalBrief = renderToStaticMarkup(React.createElement(PictureBriefing, {
  picture: { ...picture, risk: { risk_level: 'korkea', reason: 'Korkean riskin peruste.' } },
}));
const criticalStart = criticalBrief.indexOf('<div class="brief-risk brief-alert-row brief-critical"');
const criticalRisk = criticalBrief.slice(criticalStart, criticalBrief.indexOf('</div>', criticalStart) + 6);
assert(criticalRisk.includes('class="icon"'), 'the risk row carries the library icon class');
assert(criticalRisk.indexOf('class="icon"') < criticalRisk.indexOf('<strong>Kriittinen.</strong>')
  && criticalRisk.indexOf('<strong>Kriittinen.</strong>') < criticalRisk.indexOf('Korkean riskin peruste.')
  && criticalRisk.indexOf('Korkean riskin peruste.') < criticalRisk.indexOf('Varmista tiedot asiakkaalta.'),
'critical risk follows icon, severity, fact, action');
const neutralRiskBrief = renderToStaticMarkup(React.createElement(PictureBriefing, {
  picture: { ...picture, risk: { risk_level: 'matala', reason: 'Ei kohonneen riskin tunnusmerkkejä' }, conflicts: [], actions: [] },
}));
const neutralRiskStart = neutralRiskBrief.indexOf('<div class="brief-risk"');
const neutralRisk = neutralRiskBrief.slice(neutralRiskStart, neutralRiskBrief.indexOf('</div>', neutralRiskStart));
assert(!/brief-(?:critical|warning|info|success)/.test(neutralRisk), 'matala risk is not given a severity color');
// Every `risk_level` the data carries must render as a presentation word, not as a bare data value. Two of
// the four previously led the sentence as lowercase fragments ("matala Ei kohonneen..."). A rule that cannot
// fail is not a rule, so this asserts the shape for all four and the absence wording for `arvioimaton`.
for (const [level, expected] of [['korkea', 'Kriittinen.'], ['kohtalainen', 'Varoitus.'],
  ['matala', 'Matala riski.'], ['arvioimaton', 'Riskiä ei ole arvioitu.'], ['ristiriita', 'Lähteiden ristiriita.']]) {
  const rendered = renderToStaticMarkup(React.createElement(PictureBriefing, {
    picture: { ...picture, risk: { risk_level: level, reason: 'Peruste.' }, conflicts: [], actions: [] },
  }));
  const start = rendered.indexOf('<div class="brief-risk');
  const row = rendered.slice(start, rendered.indexOf('</div>', start));
  assert(row.includes(`<strong>${expected}</strong>`),
    `risk_level ${level} renders as the presentation word ${expected}`);
  // An unassessed risk is not a low risk, and a source conflict is not a severity finding:
  // none of these three carries severity colour.
  if (level === 'matala' || level === 'arvioimaton' || level === 'ristiriita') {
    assert(!/brief-(?:critical|warning|info|success)/.test(row),
      `${level} is a neutral row with no severity colour`);
  }
}
// ---- Work order B: attention versus neutral status ----
// A neutral status row is a state, not something to check. With a matala risk and nothing actionable, the
// panel shows no attention count and still shows the risk row, in the status group, which has no count.
// Decision-1's own finding rows are attention rows by design, so this quiet fixture also leaves Decision-1
// with nothing to say (no additions, no urgency, no relevant-and-current record).
const quietBase = { ...picture, conflicts: [], actions: [], formCatalogue: [],
  selection: { ...picture.selection, decision_not_shortlisted_count: 0, decision_budget_exhausted_count: 0,
    decision_too_long_count: 0, decision_unavailable_count: 0, additional_record_ids: [],
    decision_notes: (picture.selection.decision_notes ?? []).map(note => ({ ...note, status: 'unclear', gate_probabilities: {} })) } };
const quietBrief = renderToStaticMarkup(React.createElement(PictureBriefing, {
  picture: { ...quietBase, risk: { risk_level: 'matala', reason: 'Ei kohonneen riskin tunnusmerkkejä' } },
  personName: 'Aino', roleLabel: 'Sairaanhoitaja', purposeLabel: 'Mielialan arviointi',
}));
assert.equal(attentionRowCount(quietBrief), 0, 'a matala risk is not an attention row');
assert.equal(attentionCaption(quietBrief), undefined, 'the attention group shows no count when it has no rows');
assert(alertGroups(quietBrief)[1]?.includes('Matala riski.'), 'the matala risk row is in the status group');
assert(!/kohdetta/.test(alertGroups(quietBrief)[1] ?? '') && /<h3[^>]*>Tilanne<\/h3>/.test(quietBrief),
  'the status group is headed Tilanne and carries no count');
// A ristiriita risk that has a conflict renders that discrepancy exactly once, and the attention count is 1.
// The number of occurrences is asserted, not mere presence: the old panel showed the same finding twice.
const conflictOnce = renderToStaticMarkup(React.createElement(PictureBriefing, {
  picture: { ...quietBase, conflicts: picture.conflicts, risk: { risk_level: 'ristiriita', reason: 'Lähteiden ristiriita lääkityksessä — tarkista, kumpi lähde on ajantasainen' } },
  personName: 'Aino', roleLabel: 'Sairaanhoitaja', purposeLabel: 'Mielialan arviointi',
}));
const conflictFact = 'kirjaus kertoo sen lopettamisesta';
assert.equal(conflictOnce.split(conflictFact).length - 1, 1, 'the source conflict renders exactly once');
assert.equal(conflictOnce.split('Lähteiden ristiriita lääkityksessä').length - 1, 0,
  'the patient-level ristiriita row is not rendered beside the conflict row that carries it');
assert.equal(attentionRowCount(conflictOnce), 1, 'the conflict is the one attention row');
assert.equal(attentionCaption(conflictOnce), '1', 'the attention count is 1 for one conflict');
assert(conflictOnce.includes('class="brief-alert-neutral brief-alert-row"'),
  'the conflict is a neutral row: a disagreement carries no severity tone');
assert.equal(alertGroups(conflictOnce)[1], undefined, 'no status row remains once the conflict carries the finding');

// The rule's confidence is no longer part of the decision sentence; it is a labelled line in the disclosure.
assert(!visibleBrief.includes('triage-signaali (ei palvelutilanne): valmistelu · high'),
  'the confidence is not appended to the clinical triage sentence');
assert(visibleBrief.includes('Kirjauksista johdettu triage-signaali (ei palvelutilanne): valmistelu'),
  'the triage sentence keeps its framing word for word');
assert(visibleTechnical.includes('<strong>Triage-signaalin luottamus:</strong> high.'),
  'the confidence is rendered once as a labelled line on the technical page');
assert(!visibleBrief.includes('Triage-signaalin luottamus'), 'and not on the briefing');

// The role is stated by the context strip and is not a second Tag in the patient pills.
assert(visibleBrief.includes('<dt class="t-caption">Ammattirooli</dt><dd class="t-body">Sairaanhoitaja</dd>'),
  'the role is stated by the encounter context strip');
const pillsHtml = visibleBrief.match(/<section class="brief-patient-pills"[\s\S]*?<\/section>/)?.[0] ?? '';
assert(!pillsHtml.includes('Sairaanhoitaja'), 'the role is no longer a Tag in PatientPills');

// ---- Gate 1: an uncalibrated cut-off has to read as illustrative where it is read ----
//
// The rule set is read from the backend table rather than restated here, so a rule added later cannot
// render a bare clinical finding and still pass. The two directions the check needs are both present: the
// qualification renders on **every** cut-off-derived reason, and on nothing else.

const RISK_TABLE = JSON.parse(readFileSync(new URL('../../backend/app/tables/risk.json', import.meta.url), 'utf8'));
const CUTOFF_OPS = ['>=', '<=', '>', '<'];
const positiveCutoff = rule => (rule.when ?? []).find(pred => CUTOFF_OPS.includes(pred.op)
  && typeof pred.value === 'number' && pred.value > 0);
const cutoffRules = RISK_TABLE.rules.filter(positiveCutoff);
assert(cutoffRules.length >= 6,
  `the cut-off sweep found ${cutoffRules.length} rules — it would pass vacuously`);
assert(cutoffRules.every(rule => rule.output.basis === 'illustrative_threshold'),
  'every rule applying a numeric cut-off marks its own output as an illustrative threshold');
// The families are the measured fields the thresholds compare. A rule may compare two of them (a blood
// pressure has a systolic and a diastolic half), so every cut-off predicate contributes its field rather than
// only the first -- keying on `positiveCutoff` alone recorded one half of that rule and hid the other.
const cutoffFields = rule => (rule.when ?? [])
  .filter(pred => CUTOFF_OPS.includes(pred.op) && typeof pred.value === 'number' && pred.value > 0)
  .map(pred => pred.field);
const cutoffFamilies = new Map();
for (const rule of cutoffRules) {
  for (const field of cutoffFields(rule)) {
    cutoffFamilies.set(field, [...(cutoffFamilies.get(field) ?? []), rule]);
  }
}
assert.deepEqual([...cutoffFamilies.keys()].sort(),
  ['diastolic_max', 'iadl_total', 'max_temp_c', 'min_spo2', 'missed_doses_total', 'phq9_total',
    'systolic_max'],
  'the cut-off families are the ones the briefing can render a threshold reason for');
for (const [field, rules] of cutoffFamilies) {
  // The representative for the family is the highest-priority rule, i.e. the one that fires first.
  const rule = rules.reduce((best, candidate) => candidate.priority > best.priority ? candidate : best);
  const html = renderToStaticMarkup(React.createElement(PictureBriefing, {
    picture: { ...picture, risk: { ...rule.output }, conflicts: [], actions: [] },
  }));
  assert(html.includes(ILLUSTRATIVE_NOTE),
    `the ${field} threshold reason (${rule.rule_id}) renders the qualification next to it`);
  assert(html.indexOf(rule.output.reason) < html.indexOf(ILLUSTRATIVE_NOTE),
    `the qualification follows the claim it is about (${rule.rule_id}), so it is read with it`);
}
// The other direction, and the "pure source fact" case: a reason that restates a source fact, and a
// source-fact row that already carries its own label, must not pick the caveat up. Applying it there would
// spread it past the claim it is about until it means nothing.
// Without a conflict in the picture the risk row is the only carrier of the finding, so it must still render,
// in the status group (it has no severity tone).
const sourceFactBrief = renderToStaticMarkup(React.createElement(PictureBriefing, {
  picture: { ...picture, conflicts: [], risk: { risk_level: 'ristiriita', reason: 'Lähteiden ristiriita lääkityksessä — tarkista, kumpi lähde on ajantasainen' } },
}));
assert(alertGroups(sourceFactBrief)[1]?.includes('Lähteiden ristiriita lääkityksessä'),
  'a ristiriita risk with no conflict is rendered in the status group, the only carrier of the finding');
assert(!attentionGroup(sourceFactBrief).includes('Lähteiden ristiriita lääkityksessä'),
  'a status-only ristiriita is not counted as an attention row');
assert(sourceFactBrief.includes('Lähteiden ristiriita lääkityksessä'),
  'the conflict reason still renders');
assert(!sourceFactBrief.includes(ILLUSTRATIVE_NOTE),
  'a reason that restates a source fact carries no threshold qualification');
assert(sourceFactBrief.includes('<strong>Valinnan peruste:</strong>'),
  'the source-fact row keeps its own existing label and clickable evidence');
// Defect 1: the conflict rule's own risk row (risk_level 'ristiriita') must carry no severity
// word ("Kriittinen"/"Varoitus"/etc.) and no critical-severity styling. It states a disagreement
// between sources, not a judgement about the patient.
const conflictRiskStart = sourceFactBrief.indexOf('<div class="brief-risk');
const conflictRiskRow = sourceFactBrief.slice(conflictRiskStart, sourceFactBrief.indexOf('</div>', conflictRiskStart));
assert(!/Kriittinen|Korkea|Varoitus/.test(conflictRiskRow),
  'the conflict risk row renders no severity word');
assert(!/brief-(?:critical|warning|info|success)/.test(conflictRiskRow),
  'the conflict risk row carries no severity colour class');
assert(conflictRiskRow.includes('Lähteiden ristiriita'),
  'the conflict risk row carries its own neutral conflict label, not a severity word');
// The absence rule is not a cut-off either — an unassessed risk is not a threshold finding.
const absenceBrief = renderToStaticMarkup(React.createElement(PictureBriefing, {
  picture: { ...picture, risk: { risk_level: 'arvioimaton', reason: 'Roolille ei kerry arvioitavaa aineistoa; arviointia ei voi tehdä' } },
}));
assert(absenceBrief.includes('Riskiä ei ole arvioitu.') && !absenceBrief.includes(ILLUSTRATIVE_NOTE),
  'an absence is worded as absence and is not qualified as a threshold claim');
// Non-vacuity in both directions: the qualification string is real copy, and the probes above are not
// passing because the string is empty or because the render never produced the disclaimer at all.
assert(ILLUSTRATIVE_NOTE.length > 20 && /kalibroitu/.test(ILLUSTRATIVE_NOTE),
  'the qualification says, in the professional\'s own reading, that the rule is not calibrated');
assert(visibleBrief.includes(ILLUSTRATIVE_NOTE) === false,
  'the shared fixture carries no threshold basis, so its row is unaffected');
assert(visibleBrief.includes('Sääntöpohjainen peruste.'),
  'a reason with no marking renders exactly as before');
assert(visibleTechnical.includes('brief-selection-info') && visibleTechnical.includes('brief-selection-success'),
  'Decision-1 additions and emphasis use their info/success tokens');
assert(!visibleTechnical.includes('class="brief-stats"') && !visibleTechnical.includes('brief-impact-estimate'),
  'impact estimate is not a stats card');
assert(!/(?:\d+(?:[,.]\d+)?\s*min\b|\bminuutt\w*)/i.test(visibleBrief)
  && !/(?:\d+(?:[,.]\d+)?\s*min\b|\bminuutt\w*)/i.test(visibleTechnical),
'neither page contains minutes');

const markedPicture = {
  ...picture,
  selection: { ...picture.selection, baseline_record_ids: ['source-a', 'source-b'], additional_record_ids: [],
    emphasized_record_ids: [], decision_notes: [
      { record_id: 'source-a', status: 'irrelevant' },
      { record_id: 'source-b', status: 'budget_exhausted' },
    ] },
  records: { priority: [], accessible_other: [
    { ...picture.records.accessible_other[0], record_id: 'source-a', selection_reasons: ['keyword:medication'] },
    { ...picture.records.accessible_other[0], record_id: 'source-b', selection_reasons: ['context:active-medication'] },
  ] },
  narration_generation_status: 'generated', narration_input_record_ids: ['source-b'],
};
const markedBrief = renderToStaticMarkup(React.createElement(PictureBriefing, {
  picture: markedPicture, personName: 'Aino', roleLabel: 'Sairaanhoitaja', purposeLabel: 'Lääkityksen seuranta',
}));
assert(markedBrief.includes('ei mallisyötteessä'), 'an omitted baseline note is marked on its row');
assert(markedBrief.includes('Ei arvioitu — Valinnan budjetti'), 'an unassessed selected note is marked on its row');
assert(markedBrief.includes('Kirjauksen aihe sopii palvelutilanteeseen.'), 'keyword reason is translated without opening provenance');
assert(markedBrief.includes('Ajantasainen lääkelista kuuluu tähän tilannekuvaan.'), 'context reason is translated without opening provenance');
assert(!markedBrief.includes('keyword:medication') && !markedBrief.includes('context:active-medication'),
  'machine selection reasons do not appear in the briefing');
assert(!markedBrief.includes('budget_exhausted'), 'machine Decision-1 status does not appear in the briefing');
assert(markedBrief.includes('brief-warning') && markedBrief.includes('brief-out-generation') && !markedBrief.includes('brief-critical'),
  'out-of-generation omission is warning, never error-styled');
assert(markedBrief.includes('ei mallisyötteessä') && markedBrief.includes('säilyi mallisyötteessä'),
  'out-of-input and unassessed-in-input remain distinct');
const abstentionPicture = {
  ...markedPicture,
  selection: { ...markedPicture.selection, decision_notes: [{ record_id: 'source-b', status: 'unclear', gate_refused: true }] },
  narration_input_record_ids: ['source-a', 'source-b'],
};
const abstentionBrief = renderToStaticMarkup(React.createElement(PictureBriefing, { picture: abstentionPicture }));
// MEANING CHANGE (phase 07, owner sign-off recorded in the issue): the binding table puts BOTH Decision-1 states
// under "must not carry status colour" — `ei mallisyötteessä` (out of the model's input) versus
// `säilyi mallisyötteessä` (unassessed, still in it). Abstention is restraint, not an error, so it renders
// neutral; the two states stay distinct by wording instead of by colour.
assert(!/brief-decision-outcome brief-(?:critical|warning|info|success)/.test(abstentionBrief),
  'Decision-1 abstention carries no status colour: restraint is correct behaviour, not a finding');
assert(abstentionBrief.includes('säilyi mallisyötteessä'),
  'abstention is distinguished by its wording — säilyi mallisyötteessä — not by a colour');
assert(!abstentionBrief.includes('ei mallisyötteessä'),
  'the two Decision-1 states are not confused: an abstention stayed in the model input');
for (const [reason, expected] of [
  ['context:person-basics', 'Henkilön perustiedot kuuluvat tähän tilannekuvaan.'],
  ['context:active-medication', 'Ajantasainen lääkelista kuuluu tähän tilannekuvaan.'],
  ['context:active-diagnosis', 'Aktiivinen diagnoosi kuuluu tähän tilannekuvaan.'],
  ['assessment:latest-relevant', 'Uusin tähän palvelutilanteeseen sopiva arviointi valittiin.'],
  ['keyword:one,two', 'Kirjauksen aihe sopii palvelutilanteeseen.'],
  ['decision:medication_follow_up:relevant', 'Decision-1 arvioi kirjauksen olennaiseksi tälle palvelutilanteelle.'],
]) assert.equal(selectionReasonText(reason), expected);
assert.equal(decisionOutcomeText('not_shortlisted'), 'Ei arvioitu — ei mahtunut Valinnan esivalintalistalle; kirjaus säilyi mallisyötteessä.');

const sharedSourceLabel = picture.records.priority[0].source;
const formPicture = {
  ...picture,
  selection: { ...picture.selection, baseline_record_ids: ['source-a', 'source-b', 'source-c'], additional_record_ids: [],
    emphasized_record_ids: [], decision_notes: [] },
  records: { priority: [{ ...picture.records.priority[0], selection_reasons: [], form: {
    form_type: 'PHQ-9', version: 'demo-v1', service: 'mental_health_assessment',
    filled_by_role: 'sairaanhoitaja', due_at: '2026-10-01T00:00:00Z',
  } }], accessible_other: [
    { ...picture.records.accessible_other[0], source: sharedSourceLabel, selection_reasons: [], form: {
      form_type: 'ennakkokysely', version: 'v2', service: 'mental_health_assessment',
      filled_by_role: 'sairaanhoitaja', due_at: null,
    } },
    { ...picture.records.priority[0], record_id: 'source-c', source: sharedSourceLabel, selection_reasons: [], form: {
      form_type: 'PHQ-9', version: 'demo-v1', service: 'mental_health_assessment',
      filled_by_role: 'sairaanhoitaja', due_at: '2026-10-01T00:00:00Z',
    } },
  ] },
  duplicate_facts: [{ concept_key: 'bp.systolic.v1.observation-1',
    record_ids: ['source-a', 'source-b', 'source-c', 'withheld-record'] }],
  form_catalogue: [
    { form_type: 'PHQ-9', service: 'mental_health_assessment', required: true,
      status: 'due', record_ids: ['source-a', 'source-c'], due_at: '2026-10-01T00:00:00Z',
      // Item C: the backend no longer emits a 'kuuluuko ... tähän palvelutilanteeseen' prompt for PHQ-9.
      suggested_next_step: null },
    { form_type: 'RAI', service: 'functioning_service_needs', required: false,
      status: 'not_shown_in_briefing', record_ids: ['withheld-record'], due_at: null, suggested_next_step: null },
  ],
};
const formBrief = renderToStaticMarkup(React.createElement(PictureBriefing, {
  picture: formPicture, personName: 'Aino', roleLabel: 'Sairaanhoitaja', purposeLabel: 'Mielialan arviointi',
}));
assert(!formBrief.includes('2 h 48 min') && !formBrief.includes('42 min'), 'population statistics are not mixed into the record view');
assert(formBrief.includes('Sama mitattu tieto usealla lomakkeella'), 'same-fact group is shown');
assert(formBrief.includes('PHQ-9') && formBrief.includes('ennakkokysely'), 'both form sources stay visible');
assert(formBrief.includes('Tila · due'), 'due catalogue entries are surfaced with their wire status');
assert(formBrief.includes('class="brief-severity brief-warning brief-alert-row"'), 'due uses the warning token');
assert(!formBrief.includes('withheld-record') && !formBrief.includes('measurement.systolic'),
  'unselected record IDs and raw concept keys do not cross the Picture presentation');
// The catalogue's absence rows are not rendered any more: beside a pre-filled PHQ-9 a "PHQ-9 · Ei näytetä tässä
// koosteessa" row read as a contradiction. Whatever the reason, nothing about an absent form reaches the page.
const absenceReasons = ['irrelevant', 'budget_exhausted', 'not_shortlisted', 'too_long', 'unavailable', 'policy.cut'];
for (const reason of absenceReasons) {
  const html = renderToStaticMarkup(React.createElement(PictureBriefing, { picture: {
    ...formPicture, duplicate_facts: [], form_catalogue: [{ form_type: 'RAI', service: 'functioning_service_needs',
      required: false, status: 'not_shown_in_briefing', record_ids: [], reason }],
  } }));
  assert(!html.includes('brief-catalogue-absence') && !html.includes('Ei näytetä tässä koosteessa')
    && !html.includes('Palvelukatalogi'), `an absent form (${reason}) renders nothing`);
}

const provenanceIds = ['entry-a', 'entry-b', 'lab-a', 'diagnosis-a'];
const provenancePicture = {
  ...picture,
  narrative: null,
  selection: { ...picture.selection, baseline_record_ids: provenanceIds, additional_record_ids: [],
    emphasized_record_ids: [], decision_notes: [] },
  records: { priority: [], accessible_other: [
    { record_id: 'entry-a', record_type: 'vapaateksti', topic: 'hoito', source: 'Synteettinen palvelu',
      recorded_at: '2026-09-28T00:00:00Z', käyntiaika: '2026-09-27T11:00:00Z',
      encounter_id: 'encounter-003', case_id: 'case-003',
      heading: { local: 'Hoidon tarpeen arviointi', national: 'Suunnitelma' },
      state: 'draft', authored_by_role: 'terveydenhoitaja', responsible_approver: 'sairaanhoitaja',
      vastaanottajat: ['Vastaanottaja A'], erikoisala: 'Neuvola', suorituspaikka: 'Synteettinen yksikkö',
      payload: { otsikko: 'Synteettinen luonnos', teksti: 'Tarkistettava merkintä.', kirjaaja: 'terveydenhoitaja' }, selection_reasons: ['keyword:test'] },
    { record_id: 'entry-b', record_type: 'vapaateksti', topic: 'hoito', source: 'Synteettinen palvelu',
      recorded_at: '2026-09-29T11:00:00Z', authored_by_role: 'huoltaja', responsible_approver: 'opaque-hidden-id',
      vastaanottajat: ['Vastaanottaja B'],
      payload: { otsikko: 'Toinen synteettinen teksti', teksti: 'Erillinen merkintä.' }, selection_reasons: [] },
    { record_id: 'lab-a', record_type: 'tutkimustulos', topic: 'tutkimus', source: 'Synteettinen laboratorio',
      recorded_at: '2026-09-30T08:00:00Z', payload: { tutkimus: 'LDL', arvo: 3.4, yksikkö: 'mmol/l', tulkinta: 'poikkeava' },
      selection_reasons: [] },
    { record_id: 'diagnosis-a', record_type: 'diagnoosi', topic: 'hoito', source: 'Synteettinen palvelu',
      recorded_at: '2026-09-26T00:00:00Z', payload: { nimi: 'Synteettinen diagnoosi', tila: 'aktiivinen' }, selection_reasons: [] },
  ] },
  citations: [], duplicate_facts: [], form_catalogue: [], assessments: [], conflicts: [], proposals: [],
  narration_acceptance_status: 'rejected', narration_support_status: 'not_run', grounding: { dropped_citations: [], dropped_actions: [] },
};
const provenanceProps = { picture: provenancePicture, personName: 'Ulpu Kanerva', roleLabel: 'Terveydenhoitaja',
  purposeLabel: 'Neuvolan terveystarkastus' };
const provenanceHtml = renderToStaticMarkup(React.createElement(PictureBriefing, provenanceProps));
const provenanceTechnical = renderToStaticMarkup(React.createElement(TechnicalView, provenanceProps));
const cardFor = id => {
  const markerIndex = provenanceHtml.indexOf(`id="source-${id}"`);
  const start = provenanceHtml.lastIndexOf('<article', markerIndex);
  const end = provenanceHtml.indexOf('</article>', markerIndex);
  return start >= 0 && end >= 0 ? provenanceHtml.slice(start, end + '</article>'.length) : '';
};
const entryACard = cardFor('entry-a');
const entryBCard = cardFor('entry-b');
assert(entryACard.includes('Vastaanottaja A') && !entryACard.includes('Vastaanottaja B'));
assert(entryBCard.includes('Vastaanottaja B') && !entryBCard.includes('Vastaanottaja A'));
assert(entryACard.includes('Kohtaaminen') && entryACard.includes('encounter-003')
  && entryACard.includes('Asia') && entryACard.includes('case-003')
  && !entryBCard.includes('Kohtaaminen') && !entryBCard.includes('Asia'));
assert(entryACard.includes('Käyntiaika') && entryACard.includes('14.00') && entryACard.includes('Erikoisala')
  && entryACard.includes('Kirjaava yksikkö') && !entryBCard.includes('Käyntiaika')
  && !entryBCard.includes('Erikoisala') && !entryBCard.includes('Kirjaava yksikkö'));
assert(!provenanceHtml.includes('opaque-hidden-id'), 'an unrecognized approver identifier is not rendered');
assert(provenanceHtml.includes('Henkilöllisyystieto ei saatavilla')
  && !provenanceHtml.includes('Ulpu Kanerva'), 'unavailable identity is neutral, never the supplied demo name');
assert(entryACard.includes('Hoidon tarpeen arviointi'));
assert(provenanceHtml.includes('class="t-title-sm brief-record-group-heading">Suunnitelma</h3>'),
  'national heading groups the matching local entry');
// The four sections are stacked and always visible. They were the library's `Tabs` (issue 03), which kept
// all four in the DOM but switched which one was `display: none` by class; the owner dropped that, because
// a tab strip is a picker of content and the brief's promise is "valmiina ilman turhaa klikkailua". The
// 0-click property this file used to pin as "all four panels are in the DOM" is now the stronger "all four
// are on screen", and it is asserted from the markup rather than from a class.
assert(!provenanceHtml.includes('brief-tab-panel') && !provenanceHtml.includes('brief-section-tabs'),
  'no visibility-switching class and no tab strip remain');
assert.equal([...provenanceHtml.matchAll(/class="brief-section"/g)].length, 3,
  'the three reading-column sections render, stacked and visible');
{
  // The audit cards are on the technical page by DOM containment, not by string position: inside the page's
  // Block, and in no reading-column section of the briefing.
  const doc = new JSDOM(`<body>${provenanceTechnical}</body>`).window.document;
  const page = doc.querySelector('.brief-technical-page');
  assert(page.querySelector('#section-coverage') && page.querySelector('#section-impact'),
    'the coverage and impact cards sit inside the technical page');
  const briefingDoc = new JSDOM(`<body>${provenanceHtml}</body>`).window.document;
  assert(!briefingDoc.querySelector('#section-coverage') && !briefingDoc.querySelector('#section-impact'),
    'and are not on the briefing at all');
}
assert.equal([...provenanceHtml.matchAll(/role="tab"/g)].length, 0,
  'no tab role remains: the sections are not a picker of content');
{
  const sectionsStart = provenanceHtml.indexOf('class="brief-sections"');
  assert(sectionsStart >= 0 && !provenanceHtml.includes('brief-presenter'),
    'the sections render on the briefing and the presenter surface does not');
  // The bare `hidden` attribute, not `aria-hidden`: a `\bhidden\b` test also matches every
  // `aria-hidden="true"` icon in the alerts panel, which the old slice happened to exclude.
  assert(!/\shidden(?=[\s/>])/.test(provenanceHtml.slice(sectionsStart)),
    'nothing is hidden with the `hidden` attribute, which would remove it from the accessibility tree');
  // Every `#section-*` id appears exactly once on the page that owns it. The ids live on the cards inside
  // these wrappers, not on the wrappers, which is why the deep links survived the section model changing
  // twice; a duplicate would give one deep link two targets and the accessibility tree two landmarks
  // sharing a name.
  assert.equal([...provenanceHtml.matchAll(/id="section-records"/g)].length, 1, '#section-records appears exactly once');
  for (const sectionId of ['section-coverage', 'section-impact', 'presenter-tools']) {
    assert.equal([...provenanceTechnical.matchAll(new RegExp(`id="${sectionId}"`, 'g'))].length, 1,
      `#${sectionId} appears exactly once on the technical page`);
  }
  // The identity grid renders only when the lens carries the identity record (it is withheld on the social-care
  // lens, and this fixture carries none), so it appears at most once and never twice.
  assert([...provenanceHtml.matchAll(/id="section-basics"/g)].length <= 1, '#section-basics appears at most once');
}
// `#section-basics` is not in this list: the identity grid renders only when the lens carries the identity
// record, and this fixture carries none; `a11y.test.mjs` audits it on a fixture that does.
const sectionHeadings = {
  '#section-records': ['Valitut kirjaukset', provenanceHtml],
  '#section-coverage': ['Kattavuus', provenanceTechnical], '#section-impact': ['Tekoälyn vaikutus', provenanceTechnical],
};
for (const [href, [heading, html]] of Object.entries(sectionHeadings)) {
  const id = href.slice(1);
  const start = html.indexOf(`id="${id}"`);
  assert(start >= 0, `${href} target is present without any interaction`);
  const following = html.indexOf('id="section-', start + 1);
  const section = html.slice(start, following === -1 ? undefined : following);
  assert(new RegExp(`<h2[^>]*>${heading}</h2>`).test(section),
    `${href} renders its heading with no interaction`);
  assert(section.replace(/<[^>]+>/g, ' ').trim().split(/\s+/).length > heading.split(' ').length,
    `${href} renders its content, not an empty panel`);
}
// The contract (restored placement, work order L): the side column (`brief-record-side`) carries the attention
// panel, the medications card and the rule-based assessment; the reading column (`brief-record-main`) carries the
// purpose's remaining record cards. The side column follows the reading column in the markup, as before.
const CARD_ANCHORS = {
  assessments: 'section-assessments', measurements: 'section-measurements', labs: 'section-labs',
  status: 'section-diagnoses', medications: 'section-medications', records: 'section-records',
};
const railStart = provenanceHtml.indexOf('class="brief-record-side"');
const mainStart = provenanceHtml.indexOf('class="brief-record-main"');
assert(mainStart >= 0 && railStart > mainStart, 'content column precedes the right rail');
const readingColumn = provenanceHtml.slice(mainStart, railStart);
assert(readingColumn.includes('id="section-records"'),
  'the main column carries the record content');
const rail = provenanceHtml.slice(railStart, provenanceHtml.indexOf('</aside>', railStart));
// Restored placement: the attention items, the medications and the rule-based assessment sit in the side column,
// and the reading column carries the rest of the record cards. The medications card is rendered once, in the side.
assert(/<h2[^>]*>Tilanne ja hälytykset/.test(rail), 'the side column carries the attention items');
// The medications card renders only when the lens carries a medication; this fixture carries none, so the
// placement is asserted where the card exists (the briefing render with medications below) and its absence here.
const hasMedications = provenancePicture.records.accessible_other.some(record => record.record_type === 'lääkitys')
  || provenancePicture.records.priority.some(record => record.record_type === 'lääkitys');
if (hasMedications) {
  assert(/<h2[^>]*>Lääkitykset<\/h2>/.test(rail), 'the side column carries the medications table');
  assert(rail.includes('id="section-medications"'), 'source rows stay reachable at 0 clicks');
}
assert(!/<h2[^>]*>Tilanne ja hälytykset/.test(readingColumn) && !readingColumn.includes('id="section-alerts"'),
  'the reading column no longer carries the attention items');
assert(!readingColumn.includes('id="section-medications"') && !/<h2[^>]*>Lääkitykset<\/h2>/.test(readingColumn),
  'the medications card is not inside the reading column, so it is rendered once');
assert(rail.includes('id="section-decision"'), 'the side column carries the rule-based assessment');
// Placement order inside the side column: attention, then medications, then the rule-based assessment.
const sideAt = {
  alerts: rail.indexOf('id="section-alerts"'), medications: rail.indexOf('id="section-medications"'),
  decision: rail.indexOf('id="section-decision"'),
};
assert(sideAt.alerts >= 0 && sideAt.alerts < sideAt.decision, 'the side column reads attention, then the rule-based assessment');
if (hasMedications) {
  assert(sideAt.alerts < sideAt.medications && sideAt.medications < sideAt.decision,
    'the side column reads attention, then medications, then the rule-based assessment');
}
// `#section-alerts` is the first element inside the side column: the in-page anchors point at it. `rail` starts at the
// aside's class attribute, so the body begins after the aside's closing `>`.
const sideBody = rail.slice(rail.indexOf('>') + 1).trimStart();
assert(sideBody.startsWith('<section') && sideBody.slice(0, sideBody.indexOf('>')).includes('id="section-alerts"'),
  'the attention panel is the first element inside the side column');
// Reading order, pinned on purpose. The reading column is first in the markup, so its first record card precedes the
// side column's attention panel: the attention items now FOLLOW the reading column, a deliberate trade (see
// `ProfessionalRecord`), not an accident. Within the side column the attention panel precedes the medications card and
// the rule-based assessment (asserted above). Both directions are pinned, so either reading of the order fails here.
{
  const readingAnchors = Object.entries(CARD_ANCHORS).filter(([id]) => id !== 'medications').map(([, anchor]) => anchor);
  const readingCardsAt = readingAnchors.map(anchor => readingColumn.indexOf(`id="${anchor}"`)).filter(at => at >= 0);
  assert(readingCardsAt.length > 0, 'the reading column carries record cards to order against the side column');
  assert(mainStart + Math.min(...readingCardsAt) < railStart + sideAt.alerts,
    'the reading column first card precedes the side column attention panel');
}
// The rail after night plan N1: the proposal awaiting a decision is the whole of `.brief-rail-section`, which leads
// the body; the assistant is the shell's side pane (`AssistantPane`) and the briefing renders no assistant of its
// own — a `chat` element handed to it (the pre-N1 contract) is ignored rather than placed somewhere.
{
  const StubAssistant = () => React.createElement('div', { className: 'stub-assistant' }, 'Avustaja');
  const railHtml = renderToStaticMarkup(React.createElement(PictureBriefing, {
    picture: { ...provenancePicture, proposals: [{ proposal_id: 'proposal-rail', text: 'Ehdotus oikeassa palkissa',
      evidence_record_ids: [], rule_version: 'rule-rail' }] },
    chat: React.createElement(StubAssistant), personName: 'Ulpu Kanerva', roleLabel: 'Terveydenhoitaja',
    purposeLabel: 'Neuvolan terveystarkastus',
  }));
  const toolsAt = railHtml.indexOf('class="brief-rail-section"');
  const proposalAt = railHtml.indexOf('class="brief-proposal', toolsAt);
  const sectionsAt = railHtml.indexOf('class="brief-sections"');
  assert(toolsAt >= 0 && proposalAt > toolsAt, 'the rail carries the proposal');
  assert(sectionsAt > proposalAt, 'the proposal leads the body: the rail precedes the reading sections');
  assert(!railHtml.includes('stub-assistant') && !railHtml.includes('LUVN-avustaja'),
    'the briefing renders no assistant of its own; the assistant is the shell\'s side pane');
  assert(!railHtml.slice(railHtml.indexOf('class="brief-record-main"'), railHtml.indexOf('class="brief-record-side"'))
    .includes('brief-proposal'), 'the proposal is not in the reading column');
}
// The purpose's order, behaviourally: the rendered cards follow `briefingOrder` for every purpose.
for (const purpose of ['medication_follow_up', 'functioning_service_needs', 'mental_health_assessment', 'child_health_checkup', null]) {
  const html = renderToStaticMarkup(React.createElement(PictureBriefing, {
    picture: { ...provenancePicture, purpose }, personName: 'Ulpu Kanerva', roleLabel: 'Terveydenhoitaja',
    purposeLabel: 'Neuvolan terveystarkastus',
  }));
  // Medications is the side column's card (work order L), so the reading column's cards are checked in order
  // against the same `briefingOrder` with that one id left out.
  const reading = html.slice(html.indexOf('class="brief-record-main"'), html.indexOf('class="brief-record-side"'));
  const side = html.slice(html.indexOf('class="brief-record-side"'));
  const readingIds = briefingOrder(purpose).filter(id => CARD_ANCHORS[id] && id !== 'medications');
  // A card with no rows renders nothing (the empty "Ei kirjauksia." cards are gone), so the cards that DO render
  // are checked: the records card always has rows here, and whichever others have rows follow `briefingOrder`.
  const at = readingIds.map(id => reading.indexOf(`id="${CARD_ANCHORS[id]}"`)).filter(index => index >= 0);
  assert(reading.includes(`id="${CARD_ANCHORS.records}"`), `the records card renders on purpose ${purpose}`);
  assert(at.every((index, i) => i === 0 || index > at[i - 1]), `the cards render in briefingOrder for purpose ${purpose}`);
  // The component omits a card whose id the purpose's order does not carry, so the side-column check holds only when
  // the order has it. No real purpose omits it today; the omit case below covers the absent one.
  if (briefingOrder(purpose).includes('medications') && hasMedications) {
    assert(side.includes(`id="${CARD_ANCHORS.medications}"`), `the medications card renders in the side column on purpose ${purpose}`);
  }
}
// An order that does not carry `medications` renders without the card, in either column, and nothing throws. The real
// orders all carry it, so this server rewrites the default order in memory (the module on disk is untouched).
{
  const DEFAULT_ORDER_LINE = "const DEFAULT_BRIEFING_ORDER = ['measurements', 'labs', 'assessments', 'status', 'medications', 'records', 'forms'];";
  let rewritten = false;
  const omitServer = await createServer({
    root: fileURLToPath(new URL('..', import.meta.url)),
    server: { middlewareMode: true, hmr: false },
    appType: 'custom',
    plugins: [{
      name: 'omit-medications-from-default-order',
      enforce: 'pre',
      transform(code, id) {
        if (!id.endsWith('/src/challenge-demo.mjs')) return null;
        assert(code.includes(DEFAULT_ORDER_LINE), 'the default order is where the omit fixture expects it');
        rewritten = true;
        return code.replace(DEFAULT_ORDER_LINE, DEFAULT_ORDER_LINE.replace(" 'medications',", ''));
      },
    }],
  });
  try {
    const { PictureBriefing: Omitting } = await omitServer.ssrLoadModule('/src/ChallengeDemo.jsx');
    const omitted = renderToStaticMarkup(React.createElement(Omitting, {
      picture: { ...provenancePicture, purpose: 'unknown_purpose' }, personName: 'Ulpu Kanerva', roleLabel: 'Terveydenhoitaja',
      purposeLabel: 'Neuvolan terveystarkastus',
    }));
    assert(rewritten, 'the omit fixture reached the order module');
    assert(!omitted.includes(`id="${CARD_ANCHORS.medications}"`) && !/<h2[^>]*>Lääkitykset<\/h2>/.test(omitted),
      'an order without medications renders no medications card in either column');
    assert(omitted.includes('id="section-alerts"') && omitted.includes('id="section-decision"'),
      'the rest of the briefing still renders when the order omits medications');
  } finally {
    await omitServer.close();
  }
}
{
  const functioning = renderToStaticMarkup(React.createElement(PictureBriefing, {
    picture: { ...provenancePicture, purpose: 'functioning_service_needs' }, personName: 'Ulpu Kanerva',
    roleLabel: 'Terveydenhoitaja', purposeLabel: 'Toimintakyvyn arviointi',
  }));
  // A card with no rows no longer renders, so the order is asserted between the cards that do render: when both the
  // assessments and the measurements cards are on the page, the assessments lead. The records card always renders.
  const assessmentsAt = functioning.indexOf('id="section-assessments"');
  const measurementsAt = functioning.indexOf('id="section-measurements"');
  assert(functioning.includes('id="section-records"'), 'a functioning/service-needs picture renders its records');
  if (assessmentsAt >= 0 && measurementsAt >= 0) {
    assert(assessmentsAt < measurementsAt,
      'a functioning/service-needs picture leads with the assessments, ahead of the measurements');
  }
}
// The split is proportional (2:1) with a measured floor under the rail: 360px is the first track width
// whose 326px content box holds all three medication columns — 146px of drug name, 91px of dose, 87px of
// status — with the fixture's longest name (`hydroklooritiatsidi`) unbroken. A fixed slab starved the main
// column instead, so the ratio and the floor are both pinned here.
assert(readFileSync(fileURLToPath(new URL('./styles.css', import.meta.url)), 'utf8')
  .includes('.brief-record-columns { display: grid; grid-template-columns: minmax(0, 2fr) minmax(360px, 1fr); gap: var(--space-lg); align-items: start; }'),
  'the two-column layout is a real grid rule whose rail is wide enough for its table');
assert(provenanceHtml.includes('Synteettinen diagnoosi') && provenanceHtml.includes('brief-diagnosis-tag'));
assert(provenanceHtml.includes('Kirjaaja</span> terveydenhoitaja') && provenanceHtml.includes('Terveydenhoitaja'));
assert(entryACard.includes('Luonnos') && entryACard.includes('Kirjaaja')
  && entryACard.includes('Vastuullinen hyväksyjä'));
assert(entryACard.includes('brief-record-draft'), 'draft is visibly marked for its separate style');
assert(!provenanceHtml.includes('Yhteydenotot') && !provenanceHtml.includes('Vastaanottaja A · Vastaanottaja B'),
  'entry recipients never become contact history or a cross-entry list');
assert(provenanceHtml.includes('Tulkinta') && provenanceHtml.includes('poikkeava'));
assert(provenanceHtml.includes('Viitearvo') && !provenanceHtml.includes('viitearvo'), 'the lab header is Finnish and the payload key never prints');
const labRecord = (record_id, payload) => ({ record_id, record_type: 'tutkimustulos', topic: 'tutkimus',
  source: 'Synteettinen laboratorio', recorded_at: '2026-09-30T08:00:00Z', payload, selection_reasons: [] });
const rangePicture = {
  ...provenancePicture,
  selection: { ...provenancePicture.selection, baseline_record_ids: ['lab-with-range', 'lab-without-range'],
    additional_record_ids: [], emphasized_record_ids: [], decision_notes: [] },
  records: { priority: [], accessible_other: [
    labRecord('lab-with-range', { tutkimus: 'LDL', arvo: 3.4, yksikkö: 'mmol/l', viitearvo: '< 3.0', tulkinta: 'poikkeava' }),
    labRecord('lab-without-range', { tutkimus: 'HDL', arvo: 1.2, yksikkö: 'mmol/l', tulkinta: 'viitearvo' }),
  ] },
};
const rangeHtml = renderToStaticMarkup(React.createElement(PictureBriefing, { picture: rangePicture }));
// The library header cell wraps its label in two spans (label + optional sort chevron slot); the cell itself is
// still a `th scope="col"`.
assert(/<th scope="col"[^>]*>(?:<span[^>]*>){2}Viitearvo(?:<\/span>){2}<\/th>/.test(rangeHtml) && /<td[^>]*>&lt; 3\.0<\/td>/.test(rangeHtml),
  'a source-supplied reference range renders verbatim');
const missingRangeRow = rangeHtml.match(/<tr id="source-lab-without-range"[^>]*>([\s\S]*?)<\/tr>/)?.[1] ?? '';
assert(/<td[^>]*><\/td>/.test(missingRangeRow) && !missingRangeRow.includes('range')
  && !missingRangeRow.includes('Reference unavailable'), 'an absent range has no value or placeholder');
// Work order D put the selection rationale behind a disclosure and B2 made the audit cards the technical
// page's. What stays on the briefing is the disclosure's own summary; the audit cards are on the other page.
assert(provenanceHtml.includes('Valinnan perusteet'), 'the selection rationale is offered as a visible disclosure summary');
assert(!provenanceHtml.includes('Kattavuus') && !provenanceHtml.includes('Tekoälyn vaikutus')
  && !provenanceHtml.includes('brief-presenter') && !provenanceHtml.includes('Näkökulmavertailu'),
'the audit cards, the presenter surface and the lens comparison are not in the reading path');
assert(provenanceTechnical.includes('Kattavuus') && provenanceTechnical.includes('Tekoälyn vaikutus')
  && provenanceTechnical.includes('brief-presenter'),
'coverage, source-only impact and the presenter surface are on the technical page');
assert(!/\b\d+ ?(?:minuutti|minutes?)\b/i.test(provenanceTechnical), 'impact includes no unsupported time-saving estimate');
const unsupportedEndUser = renderToStaticMarkup(React.createElement(PictureBriefing, {
  picture: provenancePicture, view: 'eu', personName: 'Ulpu Kanerva',
}));
assert(!unsupportedEndUser.includes('Omat tulokset') && !unsupportedEndUser.includes('Yhteydenotot'),
  'there is no citizen/end-user presentation branch');

// Patient-level responsible clinician is display-only: it renders on a record that supplies it and
// renders nothing when absent, distinct from the record-level responsible approver.
const clinicianRecord = {
  record_id: 'clinician-a', record_type: 'vapaateksti', topic: 'hoito', source: 'Synteettinen palvelu',
  recorded_at: '2026-09-28T00:00:00Z', authored_by_role: 'sairaanhoitaja',
  responsible_clinician: 'Vastuuhenkilö A', responsible_approver: 'laakari',
  payload: { otsikko: 'Vastuuhenkilökirjaus', teksti: 'Synteettinen.' }, selection_reasons: [],
};
const noClinicianRecord = { ...clinicianRecord, record_id: 'clinician-b', responsible_clinician: null,
  payload: { otsikko: 'Ilman vastuuhenkilöä', teksti: 'Synteettinen.' } };
const clinicianPicture = {
  ...provenancePicture,
  records: { priority: [], accessible_other: [clinicianRecord, noClinicianRecord] },
  selection: { ...provenancePicture.selection, baseline_record_ids: ['clinician-a', 'clinician-b'],
    additional_record_ids: [], emphasized_record_ids: [], decision_notes: [] },
};
const clinicianHtml = renderToStaticMarkup(React.createElement(PictureBriefing, { picture: clinicianPicture }));
const withClinician = clinicianHtml.slice(clinicianHtml.indexOf('id="source-clinician-a"'),
  clinicianHtml.indexOf('id="source-clinician-b"'));
const withoutClinician = clinicianHtml.slice(clinicianHtml.indexOf('id="source-clinician-b"'));
assert(withClinician.includes('Vastuuhenkilö') && withClinician.includes('Vastuuhenkilö A'),
  'a supplied patient-level responsible clinician renders');
assert(withClinician.includes('Vastuullinen hyväksyjä'),
  'the patient-level clinician is distinct from the record-level approver');
assert(!withoutClinician.includes('Vastuuhenkilö'), 'an absent responsible clinician renders nothing');

const emptyPicture = {
  ...provenancePicture,
  selection: { ...provenancePicture.selection, baseline_record_ids: [], additional_record_ids: [], emphasized_record_ids: [], decision_notes: [] },
  records: { priority: [], accessible_other: [] }, citations: [], assessments: [], conflicts: [], proposals: [],
  duplicate_facts: [], form_catalogue: [], narrative: null, narration_acceptance_status: 'rejected',
};
const emptyProfessionalHtml = renderToStaticMarkup(React.createElement(PictureBriefing, { picture: emptyPicture }));
const emptyPanelMessages = [...emptyProfessionalHtml.matchAll(/class="[^"]*brief-empty-panel">([^<]*)<\/p>/g)].map(match => match[1]);
// The record cards with no rows render nothing now; the one panel that always renders (the selected records)
// still explains its empty state. The identity grid and the medications card are absent when the lens carries
// neither record, and their absence is cause-neutral.
assert.equal(emptyPanelMessages.length, 1, 'the always-rendered records panel explains its empty state');
assert.deepEqual(new Set(emptyPanelMessages), new Set(['Ei kirjauksia.']),
  'empty panels use one neutral phrase regardless of the absent record type');
assert(!emptyProfessionalHtml.includes('suostumus') && !emptyProfessionalHtml.includes('käyttöoikeus'),
  'empty panels do not imply a withheld record');

// Lens comparison uses only the already-loaded picker metadata. It is visible without a click and
// names sibling lenses without presenting their records or putting the current lens count under them.
const siblingEncounter = {
  encounter_id: 'bound-encounter-003-mielenterveys-laakari',
  picker_label: 'Mielialan arvio · sama henkilö ja tilanne, eri ammattilainen (003, lääkäri)',
};
// The lens comparison (`LensDemo`) is production material on the technical page; the briefing's own lens
// control is `LensSwitch`, which renders only under a shell that can open the sibling (`onOpenLens`).
const lensProps = { ...briefProps, lensSiblings: [siblingEncounter] };
const lensBrief = renderToStaticMarkup(React.createElement(PictureBriefing, { ...lensProps, onOpenLens: () => {} }));
const lensTechnical = renderToStaticMarkup(React.createElement(TechnicalView, lensProps));
assert(lensTechnical.includes('Näkökulmavertailu'), 'the comparison panel renders for a person with another lens');
assert(lensTechnical.includes(siblingEncounter.picker_label), 'the sibling is identified by its picker label');
assert(!lensBrief.includes('Näkökulmavertailu') && !lensBrief.includes('brief-lens-demo'),
  'the lens comparison is out of the reading path: it is not on the briefing');
assert(lensBrief.includes('brief-lens-switch') && lensBrief.includes(siblingEncounter.picker_label),
  'the briefing offers the sibling lens as a switch, under its picker label');
assert(!lensBrief.includes('<details') || !lensBrief.includes('run-specifications'),
  'no run-specifications disclosure remains on the briefing');
assert(lensBrief.trimEnd().endsWith('Tekniset tiedot</a></p>'),
  'the technical-page link is last in the briefing body');
assert(!lensBrief.includes('Tässä näkökulmassa:') && !lensTechnical.includes('Tässä näkökulmassa:'),
  'no current-lens count is placed under the sibling label');
// D6, on every briefing render: the scope caption, and no digit after it (the lens switch follows it and
// must not add a count).
for (const [label, html] of [['visible', visibleBrief], ['lens', lensBrief]]) {
  assert(html.includes(LENS_SCOPE_NOTE) && !scopeCaptionFollowedByDigit.test(html),
    `the ${label} briefing carries the scope caption with no number after it`);
}
// Pinned strings: the visible copy that must not drift without a deliberate decision. Add to this list when
// a string is pinned; a rename then fails here rather than in review.
const PINNED_STRINGS = [
  // ('Ei näytetä tässä koosteessa' is pinned by the per-reason absence fixtures below, which render it.)
  'Valinnan peruste',                   // the selection rationale, inside its closed disclosure (work order D)
  'Näkökulmavertailu',                  // the lens comparison heading
  'Tekoälyn vaikutus',                  // the impact section heading
  'Tekoälyn vaikutuksen vertailu',      // the three-way comparison heading on the presenter surface
  'Mitä tekoäly teki',                  // the Decision-1 panel heading
  'Ei mukana Foundry-syötteessä.',      // the out-of-generation omission
  'Varoitus.',                          // severity as a leading word
  'säilyi mallisyötteessä',             // the abstention wording that replaced the info tint
  'koneellisesti tuotettu',             // the prose is labelled machine-drafted beside the accepted text
  'Esittäjän työkalut',                 // the presenter surface announces itself as presenter material
];
// Every briefing-level render this file produces, so a string pinned in one of them is checked where it
// actually appears rather than in whichever variable happened to be in scope.
const ALL_BRIEFS = [visibleBrief, lensBrief, markedBrief, formBrief, abstentionBrief, visibleTechnical, lensTechnical].join('\n');
for (const pinned of PINNED_STRINGS) {
  assert(ALL_BRIEFS.includes(pinned), `the pinned string is still rendered verbatim: ${pinned}`);
}
// Pinned clinical strings that no fixture in this file renders (the medication table needs rows the fixtures
// do not carry) are pinned at source level, so a rename still fails rather than passing silently.
const COMPONENT_SOURCE = readFileSync(fileURLToPath(new URL('./ChallengeDemo.jsx', import.meta.url)), 'utf8');
for (const sourcePinned of ['Puuttuneet annokset / 30 vrk']) {
  assert(COMPONENT_SOURCE.includes(sourcePinned), `the pinned clinical string is unchanged in the source: ${sourcePinned}`);
}
assert(PINNED_STRINGS.length >= 8, 'the pinned-string inventory has not been emptied to make a rename pass');

assert(lensTechnical.includes('Tekoälyn vaikutuksen vertailu'),
  'the technical page keeps the AI-impact comparison, under its own heading (not the renamed section)');
const singleLens = renderToStaticMarkup(React.createElement(TechnicalView, { ...briefProps, lensSiblings: [] }));
assert(!singleLens.includes('Näkökulmavertailu'), 'a person with one lens gets no comparison panel');
assert(!renderToStaticMarkup(React.createElement(PictureBriefing, { ...briefProps, lensSiblings: [], onOpenLens: () => {} }))
  .includes('brief-lens-switch'), 'and no lens switch on the briefing');

// The comparison instruments are the technical page's (B2): the presenter surface, the mode switch and the
// three-way comparison render there and nowhere on the briefing. Both directions are asserted, because "it
// renders somewhere" is not the property. The lookups are class-token based: pinning the exact attribute
// string made them fail when an element legitimately gained the library's `prose` class.
const presenterStart = lensTechnical.indexOf('brief-presenter');
assert(presenterStart >= 0, 'the technical page carries the presenter surface');
assert.equal(lensBrief.indexOf('brief-presenter'), -1, 'the briefing does not');
// The presenter surface runs from its own class to the AI provenance that follows it on the page.
const presenterMarkup = lensTechnical.slice(presenterStart, lensTechnical.indexOf('brief-ai-evidence'));
assert(presenterMarkup.includes('brief-mode-switch') && presenterMarkup.includes('brief-comparison-grid'),
  'the mode switch and the three-way comparison are both inside the presenter surface');
assert(lensTechnical.indexOf('id="section-impact"') < presenterStart,
  'the impact card precedes the presenter surface on the technical page');
// Nothing that was visible without a click became a click away — the sections are all visible now, which is
// strictly more than the tab strip offered.
assert.equal([...lensBrief.matchAll(/class="brief-section"/g)].length, 3, 'the three reading-column sections still render');
assert.equal([...lensTechnical.matchAll(/class="brief-section"/g)].length, 0, 'and none of them on the technical page');
assert(lensTechnical.includes('Tekoälyn vaikutus') && !lensBrief.includes('Tekoälyn vaikutus'),
  'the impact card is the technical page\'s');
assert(!presenterMarkup.includes('Tekoälyn vaikutus'), 'the presenter surface does not absorb the impact card');

// ---- Gate 4c: RAI is a PRESENTED record, not an implemented instrument ----
//
// A supplied RAI result carries answers and no instrument specification, so there is no total, no band and
// no item rows -- and before this the row said only "Ei kokonaispisteitä", which reads the same as a record
// that carried nothing. What the row may show is what the source itself supplied, counted from the record
// cited beside it in that same row; what it may NOT show is any sum, band or interpretation of them.
const assessmentPicture = {
  ...picture,
  // One selected record per instrument, so each row's own `source-*` anchor is distinct.
  selection: { ...picture.selection, baseline_record_ids: ['source-a', 'source-c'] },
  records: { ...picture.records, accessible_other: [...picture.records.accessible_other, {
    record_id: 'source-c', record_type: 'arviointi', topic: 'toimintakyky', source: 'Synteettinen palvelu',
    recorded_at: '2026-09-18T00:00:00Z', state: 'final', payload: { instrumentti: 'RAI', versio: 'demo-v1' },
    selection_reasons: [],
  }] },
  assessments: [
    { instrumentti: 'PHQ-9', instrument_version: 'demo-v2', valmis: true, total: 16, max_total: 27,
      tulkinta: 'keskivaikea–vaikea', lahteet: ['source-a'], temporal_status: 'current',
      source_answers: 9,
      osiot: [{ osio: 'q1', kysymys: 'INSTRUMENTIN OMA KYSYMYS 1', pisteet: 2 },
        { osio: 'q8', kysymys: 'INSTRUMENTIN OMA KYSYMYS 8', pisteet: 2 },
        { osio: 'q9', kysymys: 'INSTRUMENTIN OMA KYSYMYS 9', pisteet: 3 }] },
    { instrumentti: 'IADL', instrument_version: 'demo-v1', valmis: false, total: null, max_total: 8,
      tulkinta: null, source_answers: 6, lahteet: ['source-b'], temporal_status: 'incomplete',
      osiot: [{ osio: 'i1', kysymys: 'INSTRUMENTIN OMA KYSYMYS I1', pisteet: 1 },
        { osio: 'i7', kysymys: 'INSTRUMENTIN OMA KYSYMYS I7', pisteet: null }] },
    { instrumentti: 'RAI', instrument_version: 'demo-v1', valmis: false, total: null, max_total: null,
      tulkinta: null, osiot: [], source_answers: 2, lahteet: ['source-c'], temporal_status: 'incomplete' },
    // An instrument with neither a specification nor supplied answers must not gain a zero.
    { instrumentti: 'GDS-15', instrument_version: null, valmis: false, total: null, max_total: null,
      tulkinta: null, osiot: [], source_answers: 0, lahteet: ['source-b'], temporal_status: 'incomplete' },
  ],
};
const assessmentHtml = renderToStaticMarkup(React.createElement(PictureBriefing, {
  picture: assessmentPicture, personName: 'Aino', roleLabel: 'Sairaanhoitaja', purposeLabel: 'Mielialan arviointi',
}));
// One assessment is a fragment of up to three sibling `<tr>`s: the values row, an optional answers row and
// an optional source-metadata row. The slice therefore runs to the NEXT row start, not to the first
// `</tr>` — the old boundary cut the fragment at the values row, so an assertion about the row's own
// anchor could only pass while the anchor lived on that row.
const fragmentFrom = (instrument) => {
  const at = assessmentHtml.indexOf(`>${instrument}</th>`);
  if (at < 0) return '';
  const start = assessmentHtml.lastIndexOf('<tr', at);
  // The fragment runs to the next assessment's values row — identified by its `<th scope="row">` — not to
  // the next `<tr`, because the answers row and the source-metadata row are siblings of the values row and
  // belong to this assessment.
  const nextOffset = assessmentHtml.slice(at).search(/<tr[^>]*><th scope="row"/);
  const next = nextOffset < 0 ? -1 : at + nextOffset;
  return assessmentHtml.slice(start, next < 0 ? undefined : next);
};
const assessmentRow = instrument => fragmentFrom(instrument);
assert(assessmentRow('RAI').length > 0, 'the RAI assessment renders as a row in the Arvioinnit table');
assert(assessmentRow('RAI').includes('Ei kokonaispisteitä · lähteessä 2 vastausta'),
  'a presented record says how much the source supplied instead of rendering as an empty row');
assert(assessmentRow('RAI').includes('Ei tulkintaa') && assessmentRow('RAI').includes('demo-v1'),
  'the RAI row keeps its version and its absent interpretation');
assert(!/\b2\s*\/\s*2\b/.test(assessmentRow('RAI')) && !assessmentRow('RAI').includes('max_total'),
  'the count is never rendered as a fraction or a maximum: it is a count of source facts, not a score');
assert(assessmentRow('IADL').includes('lähteessä 6 vastausta'),
  'an incomplete instrument reports the answers it does have, not only the ones it lacks');
assert(assessmentRow('PHQ-9').includes('16 / 27') && !assessmentRow('PHQ-9').includes('lähteessä'),
  'a completed instrument still renders its supported total, never a bare count');
assert(assessmentRow('GDS-15').includes('Ei kokonaispisteitä')
  && !assessmentRow('GDS-15').includes('lähteessä'),
  'nothing to count means nothing is said, rather than a zero');
// The row is where this record is anchored, so the count and the record it was read from are in the same
// view -- which is what `CONTEXT.md:49` requires of any displayed number. A record is anchored exactly
// once: `anchorOwner` in the component decides which table emits the id, and the anchor-integrity check
// below fails if two elements ever carry it.
assert(assessmentRow('RAI').includes('id="source-source-c"'),
  'the RAI row is the anchor for the very source record its answer count was read from');
assert(assessmentRow('RAI').includes('href="#source-source-c"'),
  'and it links to it, so the row is citable as well as anchored');

// ---- Step 1 of the PHQ-9 item 9 decision: the supplied answers, read-only ----
//
// `osiot` has been on the wire since the instrument layer existed and was rendered nowhere, so item 9 --
// the item whose score most changes what a professional does next -- could not be seen at any value through
// any view. These pin what the row may and may not say about it.
const itemsRow = instrument => {
  const at = assessmentHtml.indexOf(`>${instrument}</th>`);
  if (at < 0) return '';
  const from = assessmentHtml.indexOf('brief-assessment-items', at);
  if (from < 0) return '';
  return assessmentHtml.slice(assessmentHtml.lastIndexOf('<tr', from), assessmentHtml.indexOf('</tr>', from));
};
assert(itemsRow('PHQ-9').includes('Lähteen vastaukset (3)'), 'a supplied answer list renders under its assessment, summarised by its count');
assert(/\bq9\s+3\b/.test(itemsRow('PHQ-9')), "item 9's supplied score is visible, which is the whole point");
assert(itemsRow('PHQ-9').includes('q1 2') && itemsRow('PHQ-9').includes('q8 2'),
  'every supplied answer is listed, not only the interesting one');
assert(itemsRow('IADL').includes('i7 ei vastausta'),
  'an answer the source did not supply renders as absent, never as 0');
assert(!/\bi7\s+0\b/.test(itemsRow('IADL')), 'an absent answer is never given a zero');
assert(!itemsRow('RAI'), 'an instrument with no supplied answers renders no answer list at all');
assert(!itemsRow('GDS-15'), 'nothing to show means nothing is shown, not an empty list');
// The boundary this deliberately does not cross: the instrument's own item wording.
assert(!assessmentHtml.includes('INSTRUMENTIN OMA KYSYMYS'),
  "the instrument's item wording is not displayed, only the source's own item codes");
// Neutral: a source fact, not a finding. No status token, no icon, no colour of its own.
assert(!/brief-(?:critical|warning|info|success)/.test(itemsRow('PHQ-9')),
  'the answer list carries no status colour — there is no computed meaning here');
assert(!/<svg/.test(itemsRow('PHQ-9')), 'the answer list carries no icon');

// ---- Work order D: the assessment row states its date, its completeness and what is missing ----
//
// The always-visible row is the values row only: the first `</tr>` of the fragment. The answer list and the
// source metadata are siblings, and are asserted separately so the boundary is not guessed.
const valuesRow = instrument => {
  const fragment = assessmentRow(instrument);
  return fragment.slice(0, fragment.indexOf('</tr>') + '</tr>'.length);
};
assert(assessmentRow('PHQ-9').includes('28.9.2026') && valuesRow('PHQ-9').includes('28.9.2026'),
  'the row states the date of its own rendered source record, in its own cell');
assert(valuesRow('RAI').includes('18.9.2026'), 'a source record that carries a date gives its row that date');
const undatedPicture = JSON.parse(JSON.stringify(assessmentPicture).replace(/"recorded_at":"[^"]*"/g, '"recorded_at":""'));
const undatedHtml = renderToStaticMarkup(React.createElement(PictureBriefing, {
  picture: undatedPicture, personName: 'Aino', roleLabel: 'Sairaanhoitaja', purposeLabel: 'Mielialan arviointi',
}));
const undatedPhq = (() => {
  const at = undatedHtml.indexOf('>PHQ-9</th>');
  return at < 0 ? '' : undatedHtml.slice(at, undatedHtml.indexOf('</tr>', at));
})();
assert(undatedPhq.length > 0 && !undatedPhq.includes('Päiväys puuttuu') && !/\d{1,2}\.\d{1,2}\.\d{4}/.test(undatedPhq),
  'a row whose sources carry no date renders no date, not a placeholder');
assert(valuesRow('IADL').includes('1/2 osiota vastattu'),
  'completeness counts the supplied items that carry an answer, over the items supplied');
assert(valuesRow('PHQ-9').includes('3/3 osiota vastattu'), 'a fully answered instrument reads as complete, by count');
assert(!assessmentRow('RAI').includes('osiota vastattu') && !assessmentRow('RAI').includes('Ei vastauksia lähteessä'),
  'no completeness is stated for a row that supplied no items');
assert(valuesRow('IADL').includes('1 osiota ilman vastausta'),
  'the missing answer is named in the missingness statement, as a count');
assert(!valuesRow('IADL').includes('i7') && !valuesRow('IADL').includes('ei vastausta'),
  'the item code of the missing answer stays out of the always-visible row');
assert(!/>0<\/td>/.test(valuesRow('IADL')) && !/\bi7\s+0\b/.test(assessmentHtml),
  'a missing answer is never rendered as a zero in the row or in the answer list');
const noAnswers = renderToStaticMarkup(React.createElement(PictureBriefing, {
  picture: { ...assessmentPicture, assessments: [{ ...assessmentPicture.assessments[1],
    osiot: [{ osio: 'i1', kysymys: 'x', pisteet: null }, { osio: 'i2', kysymys: 'x', pisteet: null }] }] },
  personName: 'Aino', roleLabel: 'Sairaanhoitaja', purposeLabel: 'Mielialan arviointi',
}));
const noAnswersRow = (() => {
  const at = noAnswers.indexOf('>IADL</th>');
  return at < 0 ? '' : noAnswers.slice(at, noAnswers.indexOf('</tr>', at));
})();
assert(noAnswersRow.includes('Ei vastauksia lähteessä') && noAnswersRow.includes('2 osiota ilman vastausta'),
  'when zero items are answered the row says so, and names how many are missing');
// The answer list is behind a disclosure, and its count is what the summary says.
{
  const items = itemsRow('PHQ-9');
  assert(items.includes('<details') && items.indexOf('<details') < items.indexOf('q9 3'),
    'the item codes sit inside the answer-list disclosure');
  assert(items.includes('<summary') && items.includes('Lähteen vastaukset (3)'),
    'the disclosure summary states what it contains, as a count, without asserting anything');
  assert(!valuesRow('PHQ-9').includes('q9 3') && !valuesRow('IADL').includes('i1 1'),
    'the item dump is not in the always-visible table cells');
}
// ---- Finding 2: an assessment whose source carries no optional metadata still renders its Lähde link ----
{
  const minimal = { ...picture,
    selection: { ...picture.selection, baseline_record_ids: ['min'] },
    assessments: [{ instrumentti: 'MIN', instrument_version: 'demo-v1', valmis: false, total: null, max_total: null,
      tulkinta: null, osiot: [], source_answers: 0, lahteet: ['min'], temporal_status: 'incomplete' }],
    records: { ...picture.records, accessible_other: [...picture.records.accessible_other, {
      record_id: 'min', record_type: 'arviointi', topic: 'toimintakyky', source: 'Synteettinen palvelu',
      recorded_at: '2026-09-18T00:00:00Z', payload: { instrumentti: 'MIN' }, selection_reasons: [] }] } };
  const html = renderToStaticMarkup(React.createElement(PictureBriefing, {
    picture: minimal, personName: 'Aino', roleLabel: 'Sairaanhoitaja', purposeLabel: 'Mielialan arviointi',
  }));
  const doc = new JSDOM(`<body>${html}</body>`).window.document;
  const row = doc.querySelector('tr.brief-source-metadata');
  assert(row, 'an assessment backed by a minimal source still renders its source-metadata row');
  const link = [...row.querySelectorAll('a')].find(a => a.textContent === 'Lähde');
  assert(link && link.getAttribute('href') === '#source-min',
    'the minimal source keeps its Lähde link to that source');
}

// Selection machinery sits behind a disclosure; the out-of-generation warning is the one thing that does not.
{
  const doc = new JSDOM(`<body>${ALL_BRIEFS}</body>`).window.document;
  const machinery = [...doc.querySelectorAll('strong')]
    .filter(el => /^(Valinnan peruste|Riskihavainnon peruste|Decision-1):$/.test(el.textContent));
  assert(machinery.length > 0, 'the fixtures render selection machinery to check');
  assert(machinery.every(el => el.closest('details')),
    'every selection, risk and Decision-1 paragraph sits inside a disclosure');
  const warnings = [...doc.querySelectorAll('.brief-out-generation')];
  assert(warnings.length > 0 && warnings.every(el => !el.closest('details')),
    'the out-of-generation warning is not inside a disclosure: it is about what the professional sees');
}

// ---- Gate 8: the AI justification and the innovation, said where they can be seen ----
//
// Two claims, both checkable against what this view renders:
//   * the medication-conflict beat names WHICH source claims what and links both, instead of reporting
//     that two sources differ. The direction is fixed by `triage._find_conflicts`: an active medication
//     paired with a note that says it was stopped;
//   * the comparison says what none of the three layers may change, and the three numbers it names are
//     the risk row, the action list and the assessment rows this same view is already rendering.
const conflictRow = (() => {
  const at = visibleBrief.indexOf('Lähteiden ristiriita');
  return at < 0 ? '' : visibleBrief.slice(visibleBrief.lastIndexOf('<div class="brief-alert-neutral', at),
                                           visibleBrief.indexOf('</div>', at));
})();
assert(conflictRow.length > 0, 'the conflict row renders');
assert(conflictRow.includes('lääkelista näyttää lääkityksen aktiivisena, mutta'),
  'the conflict says which source claims what, not that two sources differ');
assert(conflictRow.includes('kirjaus kertoo sen lopettamisesta'),
  'the conflict names the note as the source that says the drug was stopped');
assert(conflictRow.includes('href="#source-source-a"') && conflictRow.includes('href="#source-source-b"'),
  'both sides of the disagreement are linked, so the reader checks it at its sources');
assert(conflictRow.includes('Tarkista, kumpi lähde on ajantasainen.'),
  'the row asks the professional to judge freshness rather than asserting which source is right');
// The neutral branch used to render only the heading and the fact, so an `act` on a neutral alert was
// silently dropped — a row that looked complete and was missing its next step.
assert(conflictRow.includes('brief-alert-act'),
  'a neutral alert carries its action line too; the branch difference is colour, not anatomy');
assert(!/\bpoikkeavat toisistaan\b/.test(visibleBrief),
  'the old "they differ" wording is gone: it named no source and no direction');

const invariant = (() => {
  const at = visibleTechnical.indexOf('brief-comparison-invariant');
  return at < 0 ? '' : visibleTechnical.slice(at, visibleTechnical.indexOf('</div>', at));
})();
assert(invariant.length > 0, 'the comparison states what does not change, before any comparison is run');
assert(invariant.includes('Mikä ei muutu'), 'the invariant block is headed as what does not change');
assert(invariant.includes('Sääntöpohjainen päätös (kohtalainen)'),
  'the invariant names the decision layer explicitly, with the risk level this view renders');
assert(invariant.includes('1 toimenpidettä') && invariant.includes('1 arviointia'),
  'it counts the actions and the assessment rows this view renders, not a corpus total');
assert(invariant.includes('Decision-1 ja Foundry eivät voi muuttaa niitä'),
  'it says which layers cannot change the decision — the point of the whole comparison');
// Ticket 14: the qualification must travel to the tier wherever the tier is rendered, and must stay off
// when the row is not a threshold claim. The shared fixture's risk carries no `basis`, so this direction
// asserts the "must not acquire a caveat it is not about" half; the block below asserts the other half.
assert(!invariant.includes(ILLUSTRATIVE_NOTE),
  'a risk row with no threshold basis does not pick the qualification up in the invariant block');
const thresholdInvariant = (() => {
  const html = renderToStaticMarkup(React.createElement(TechnicalView, {
    ...briefProps, picture: { ...picture, risk: { ...picture.risk, basis: 'illustrative_threshold' } },
  }));
  const at = html.indexOf('brief-comparison-invariant');
  return at < 0 ? '' : html.slice(at, html.indexOf('</div>', at));
})();
assert(thresholdInvariant.includes('Sääntöpohjainen päätös (kohtalainen)'),
  'the qualified invariant still names the decision layer and the tier');
assert(thresholdInvariant.includes(ILLUSTRATIVE_NOTE),
  'when the risk output carries a threshold basis, the invariant block carries the qualification too');

// The presenter surface carries the innovation line. It states THIS system's boundary and makes no claim
// about any other system, because this repository has measured none.
const presenterCopy = lensTechnical.slice(lensTechnical.indexOf('brief-presenter'));
assert(presenterCopy.includes('Tekoäly tekee tässä yhden asian'),
  'the presenter surface states what the model is allowed to do, in one line');
assert(presenterCopy.includes('Se ei päätä.'), 'and states the boundary as a sentence, not a hint');
assert(presenterCopy.includes('Malli ei voi muuttaa niitä'),
  'the innovation line is tied to the invariant, not left as a slogan');

// ---- Blood pressure and pulse have a surface now ----
//
// `mittaus`'s render keys were `{paino_kg, pituus_cm}` while `USEFUL_PAYLOAD_KEYS[mittaus]` named
// `{verenpaine, syke}`. Two consequences, and this pins the second: the sets were disjoint so no
// measurement row could ever reconcile clean, AND blood pressure was renderable nowhere in the product
// while our own delivery sample carried it. `RENDER_PAYLOAD_KEYS` and `SOURCE_FIELDS` are tied together by
// the backend's drift test, but NOTHING tied either to `DISPLAY_PAYLOAD_FIELDS` — which is the map that
// decides whether a payload value reaches the view at all. This is that assertion.
const measurementPicture = {
  ...picture,
  selection: { ...picture.selection, baseline_record_ids: ['source-a', 'source-m'], additional_record_ids: [] },
  records: { priority: [], accessible_other: [{ record_id: 'source-m', record_type: 'mittaus', topic: 'verenpaine',
    source: 'Synteettinen mittaus', recorded_at: '2026-09-22T08:15:00Z',
    payload: { paino_kg: 72.5, pituus_cm: 178, verenpaine: '158/92', syke: 78 }, selection_reasons: [] }] },
  assessments: [], conflicts: [], form_catalogue: [], duplicate_facts: [],
};
const measurementHtml = renderToStaticMarkup(React.createElement(PictureBriefing, {
  picture: measurementPicture, personName: 'Aino', roleLabel: 'Sairaanhoitaja', purposeLabel: 'Lääkityksen seuranta',
}));
const tileFor = label => {
  const at = measurementHtml.indexOf(`>${label}</h3>`);
  if (at < 0) return '';
  const start = measurementHtml.lastIndexOf('<div class="brief-measurement-value"', at);
  return measurementHtml.slice(start, measurementHtml.indexOf('</div>', at));
};
// The label is written in sentence case and uppercased by the `t-overline` role, so the markup carries
// `Paino`, not `PAINO` — the browser shows the transformed text.
for (const [label, value] of [['Paino', '72.5'], ['Pituus', '178'], ['Verenpaine', '158/92'], ['Syke', '78']]) {
  assert(tileFor(label).includes(value),
    `a ${label} measurement reaches the view with its source-supplied value (${value})`);
}
// The unit is rendered only where the payload key carries one. `paino_kg`/`pituus_cm` do; `verenpaine` and
// `syke` do not, and the tile must not invent one — a unit is a source fact like any other.
assert(tileFor('Paino').includes('<span>kg</span>') && tileFor('Pituus').includes('<span>cm</span>'),
  'the tile renders the unit the payload key carries');
assert(!/<span>\s*<\/span>/.test(tileFor('Verenpaine')) && !tileFor('Verenpaine').includes('<span>'),
  'a blood-pressure tile renders no unit: the source supplies none and none is invented');
assert(!tileFor('Syke').includes('<span>'),
  'a pulse tile renders no unit, for the same reason');
assert.equal([...measurementHtml.matchAll(/class="brief-measurement-value"/g)].length, 4,
  'one tile per measurement the source carries');

// ---- Nothing is fetched that is not shown ----
//
// `asiointitapahtuma` was declared in the wire map, carried a payload, was selected, reached the model and was
// cited by the narration — and rendered NOWHERE, because it sat on the selected-records blocklist while no
// section claimed it. The audience-facing symptom is a record type that silently does not exist. So this
// fixture carries one record of every declared type, each with a value only it has, and every one of those
// values must appear in the markup. A type with no surface fails here rather than in the demo.
const coverageRecords = [
  ['perustieto', { sukupuoli: 'COVER-suomi', syntymävuosi: 1970, äidinkieli: 'COVER-suomi', kotikunta: 'COVER-Tampere', asumismuoto: 'COVER-vuokra' }],
  ['diagnoosi', { nimi: 'COVER-diagnoosi', icd10: 'COVER-X00', tila: 'aktiivinen' }],
  ['lääkitys', { lääke: 'COVER-lääke', annos: 'COVER-annos', tila: 'aktiivinen', puuttuneet_annokset_30d: 1 }],
  ['mittaus', { paino_kg: 1.5, pituus_cm: 151, verenpaine: 'COVER-158/92', syke: 71 }],
  ['arviointi', { instrumentti: 'COVER-PHQ-9', versio: 'COVER-v1' }],
  ['tutkimustulos', { tutkimus: 'COVER-tutkimus', arvo: 'COVER-arvo', yksikkö: 'COVER-yksikkö', tulkinta: 'COVER-tulkinta', viitearvo: 'COVER-viite' }],
  ['asiointitapahtuma', { kanava: 'COVER-puhelu', suunta: 'COVER-lähtevä', aihe: 'COVER-aihe', yhteenveto: 'COVER-yhteenveto' }],
  ['vapaateksti', { otsikko: 'COVER-otsikko', teksti: 'COVER-teksti', kirjaaja: 'COVER-kirjaaja', käyntityyppi: 'COVER-käyntityyppi' }],
  ['päätös', { päätös: 'COVER-päätös', voimassa: 'COVER-voimassa' }],
].map(([type, payload], index) => ({
  record_id: `cover-${index}`, record_type: type, recorded_at: '2026-09-01T00:00:00Z',
  // `authored_by_role` and a selection reason are what make a row render its provenance line — the row that
  // carries the `colSpan` this file exists to keep honest. Without them the metadata rows are absent and the
  // colspan assertion has nothing to check, which is exactly how it passed while the bug was present.
  authored_by_role: 'sairaanhoitaja', selection_reasons: ['keyword:hoito'],
  // The basics grid finds its record by topic: `sources.find(s => s.type === 'perustieto' && s.topic ===
  // 'demografia')`. A perustieto record under any other topic renders nothing, which is a convention this
  // fixture has to respect rather than a gap — and which cost a debugging cycle when it did not.
  topic: type === 'perustieto' ? 'demografia' : 'COVER-topic',
  payload, source: 'Synteettinen',
}));
const coveragePicture = {
  ...picture,
  selection: { ...picture.selection, baseline_record_ids: coverageRecords.map(r => r.record_id), additional_record_ids: [] },
  records: { priority: [], accessible_other: coverageRecords },
  // An `arviointi` record is not rendered from the record view: the backend scores it into `assessments`, and
  // that derived field is what the assessment table draws. So the fixture supplies both, as the pipeline does.
  // `osiot` is not empty, and that is load-bearing for the table-cell rule below: the assessment items row is
  // the OTHER hand-written `colSpan` in the briefing (`ChallengeDemo.jsx`, colSpan=4 in a four-column table),
  // and with no items it does not render — which is exactly how changing that span to 5 passed the rule while
  // the row was malformed. A fixture that omits the row makes the rule blind to it.
  assessments: [{ instrumentti: 'COVER-PHQ-9', instrument_version: 'COVER-v1', valmis: true, total: 5,
    max_total: 27, tulkinta: 'COVER-tulkinta', source_answers: 2, lahteet: ['cover-4'],
    temporal_status: 'current',
    osiot: [{ osio: 'q1', pisteet: 2 }, { osio: 'q9', pisteet: 3 }] }],
};
const coverageHtml = renderToStaticMarkup(React.createElement(PictureBriefing, {
  picture: coveragePicture, personName: 'Aino', roleLabel: 'Sairaanhoitaja', purposeLabel: 'Lääkityksen seuranta',
}));
const coverageText = coverageHtml.replace(/<[^>]+>/g, ' ');
for (const [type, payload] of coverageRecords.map(r => [r.record_type, r.payload])) {
  const values = Object.values(payload).map(String);
  const visible = values.some(value => coverageText.includes(value));
  assert(visible, `a ${type} record renders none of its own values (${values.join(', ')}) — it is fetched and invisible`);
}

// ---- Every table row must claim exactly as many columns as its header ----
//
// The medications table's provenance row had a bare `<td>` where its three siblings tables all set a
// `colSpan`, so that one cell claimed a fourth column in a three-column table. In a real table that is
// malformed; here the rows are CSS grids, so the visible damage was different and worse — the surplus width
// went to the metadata instead of the row header, and at the rail's ~345px the drug name was squeezed to
// 24px and set one character per line ("l e v o t y r o k s i i n i"). jsdom cannot see widths, so this
// asserts the structural half, which is the half a test can hold.
// Rendered from the coverage fixture above, which carries one record of every type and therefore every
// table the briefing can draw — including the medications one whose provenance row was malformed.
const spanTables = [...coverageHtml.matchAll(/<table[^>]*>([\s\S]*?)<\/table>/g)].map(m => m[1]);
assert(spanTables.length >= 3, `expected the record tables in the markup, found ${spanTables.length}`);
for (const table of spanTables) {
  const header = (table.match(/<thead>[\s\S]*?<\/thead>/) || [''])[0];
  const columns = [...header.matchAll(/<th\b[^>]*>/g)]
    .reduce((n, th) => n + Number((th[0].match(/colspan="(\d+)"/i) || [])[1] || 1), 0);
  assert(columns > 0, 'a table rendered without a header row');
  for (const row of table.matchAll(/<tr\b[^>]*>([\s\S]*?)<\/tr>/g)) {
    const cells = [...row[1].matchAll(/<(?:th|td)\b[^>]*>/g)]
      .reduce((n, cell) => n + Number((cell[0].match(/colspan="(\d+)"/i) || [])[1] || 1), 0);
    if (cells === 0) continue;
    assert.equal(cells, columns,
      `a row claims ${cells} columns where its header has ${columns}: ${row[1].replace(/<[^>]+>/g, ' ').trim().slice(0, 60)}`);
  }
}

// ---- briefingOrder: a presentation rule over the seven cards, never a filter ----
{
  const SEVEN = ['assessments', 'measurements', 'labs', 'status', 'medications', 'records', 'forms'];
  assert.deepEqual([...BRIEFING_SECTION_IDS].sort(), [...SEVEN].sort(), 'the id list is exactly the seven cards');
  for (const purpose of ['medication_follow_up', 'functioning_service_needs', 'mental_health_assessment', 'child_health_checkup', null, 'unknown_purpose']) {
    assert.deepEqual([...briefingOrder(purpose)].sort(), [...SEVEN].sort(),
      `the order for ${purpose} is a permutation of the seven ids`);
  }
  assert.deepEqual(briefingOrder('medication_follow_up'),
    ['medications', 'measurements', 'labs', 'assessments', 'status', 'records', 'forms']);
  assert.deepEqual(briefingOrder('functioning_service_needs'),
    ['assessments', 'records', 'measurements', 'medications', 'labs', 'status', 'forms']);
  assert.deepEqual(briefingOrder('mental_health_assessment'),
    ['assessments', 'measurements', 'records', 'medications', 'labs', 'status', 'forms']);
  assert.deepEqual(briefingOrder('child_health_checkup'),
    ['measurements', 'assessments', 'records', 'labs', 'medications', 'status', 'forms']);
  const defaultOrder = ['measurements', 'labs', 'assessments', 'status', 'medications', 'records', 'forms'];
  assert.deepEqual(briefingOrder(null), defaultOrder, 'an absent purpose gets the default order');
  assert.deepEqual(briefingOrder(undefined), defaultOrder, 'an undefined purpose gets the default order');
  assert.deepEqual(briefingOrder('unknown_purpose'), defaultOrder, 'an unknown purpose gets the default order');
  assert.deepEqual(briefingOrder('constructor'), defaultOrder, 'an inherited property name is not a purpose');
  assert.notEqual(briefingOrder(null), briefingOrder(null).constructor, 'the order is a fresh array');
  assert.equal(picturePresentation({ purpose: 'child_health_checkup' }).purpose, 'child_health_checkup',
    'the purpose code passes through the presentation adapter');
  assert.equal(picturePresentation({}).purpose, null, 'an absent purpose stays null');
}

const rejected = picturePresentation({ ...picture, narrative: 'Rejected raw prose.', narration_support_status: 'unknown', narration_acceptance_status: 'rejected' });
assert.equal(rejected.summary, null);
assert.deepEqual(rejected.citedSourceIds, []);
assert.deepEqual(rejected.narration.inputRecordIds, ['source-a', 'source-b']);
assert.deepEqual(rejected.narration.citedRecordIds, []);

// Heavy-size render: the evaluator-only shortlist fixture renders 20 sources, one of them a
// pathologically long synthetic note. The briefing must hold at that size and the long excerpt
// must be collapsed behind a keyboard-accessible disclosure without being removed from the DOM.
const heavySources = Array.from({ length: 20 }, (_, index) => ({
  record_id: `heavy-${String(index).padStart(3, '0')}`,
  record_type: 'vapaateksti', topic: 'lääkitys', source: 'kotihoidon kirjaus',
  recorded_at: '2026-09-20T00:00:00Z', selection_reasons: [],
  payload: { otsikko: 'Kirjaus', teksti: index === 0 ? 'pitkä kirjaus '.repeat(800).trim() : 'Lyhyt kirjaus.' },
}));
const heavyBrief = renderToStaticMarkup(React.createElement(PictureBriefing, {
  picture: { ...picture, records: { priority: [], accessible_other: heavySources },
    selection: { ...picture.selection,
      baseline_record_ids: heavySources.map(source => source.record_id),
      additional_record_ids: [], emphasized_record_ids: [] } },
  personName: 'Aino', roleLabel: 'Sairaanhoitaja', purposeLabel: 'Mielialan arviointi',
}));
assert.equal([...heavyBrief.matchAll(/<article[^>]*class="brief-selected-record"/g)].length, 20,
  'heavy fixture renders 20 selected record cards');
assert.equal([...heavyBrief.matchAll(/class="brief-source-more"/g)].length, 1,
  'only the pathological excerpt is collapsed behind a disclosure');
assert(heavyBrief.includes('Näytä koko kirjaus'), 'the disclosure is labelled for keyboard users');
assert(heavyBrief.includes('pitkä kirjaus '.repeat(50)),
  'the full long excerpt is present in the DOM inside the disclosure');

// The URL selects state but cannot widen it: an encounter id the API did not return is ignored, and an
// unknown mode falls back to normal rather than being forwarded to the picture route.
const listedEncounters = [{ encounter_id: 'enc-a' }, { encounter_id: 'enc-b' }];
const addressed = requestedView('?encounter=enc-b&mode=decision-off', listedEncounters);
assert.equal(addressed.encounterId, 'enc-b', 'a listed encounter id is addressed from the URL');
assert.equal(addressed.mode, 'decision-off');
// An addressed encounter the API did not return is a neutral selection, not a silent substitution: the first
// row belongs to someone else, and showing it under the requested address is the defect this pins.
const unknownAddress = requestedView('?encounter=not-returned', listedEncounters);
assert.equal(unknownAddress.encounterId, '',
  'an encounter the API did not return is a neutral selection, not a silent substitution of the first row');
assert.equal(unknownAddress.encounterUnknown, true, 'the unknown address is reported as unknown so the briefing can say so');
assert.equal(unknownAddress.encounterAddressed, true);
assert.equal(requestedView('?mode=made-up', listedEncounters).mode, 'normal',
  'an unknown mode falls back to normal instead of reaching the picture route');
assert.equal(requestedView('', listedEncounters).encounterId, 'enc-a', 'no parameter selects the first lens');
assert.equal(requestedView('', listedEncounters).encounterUnknown, false, 'an absent parameter is a default, not an unknown address');
assert.equal(requestedView('', listedEncounters).worklistOnly, false, 'an absent parameter is a default, not a worklist');
// A role with no addressed encounter is that role's worklist, never a case chosen for it.
const roleRows = [
  { encounter_id: 'enc-n', role: 'sairaanhoitaja' },
  { encounter_id: 'enc-d', role: 'laakari' },
];
const roleOnly = requestedView('?role=laakari', roleRows);
assert.equal(roleOnly.worklistOnly, true, 'a role-only address is that role\'s worklist');
assert.equal(roleOnly.encounterId, '', 'a role-only address selects no encounter');
assert.equal(roleOnly.role, 'laakari');
assert.equal(requestedView('?role=potilas', roleRows).role, '', 'the potilas lens is not admissible');
assert.equal(requestedView('', []).encounterId, '', 'an empty encounter list selects nothing');
assert.equal(requestedView('?encounter=enc-a', []).encounterUnknown, true,
  'an address against an empty catalogue is unknown, not defaulted');

// A deep link beyond the bootstrapped page. The page is a first slice of the caller's scope, so an addressed
// id outside it used to be reported absent while `GET /api/encounters/{id}` answered 200 for it. The caller
// resolves the id and passes the row back in; the address then opens. Without a resolution it stays unknown,
// which is what a refused or genuinely absent id must keep -- the same answer for both, by design.
const farRow = { encounter_id: 'enc-far', role: 'sairaanhoitaja' };
const deepLinked = requestedView('?encounter=enc-far', listedEncounters, farRow);
assert.equal(deepLinked.encounterUnknown, false,
  'a deep link the server resolved must not be reported as absent');
assert.equal(deepLinked.encounterId, 'enc-far', 'the resolved deep link opens the addressed encounter');
assert.equal(deepLinked.encounterWanted, 'enc-far',
  'the address reports the id the caller has to resolve before settling for unknown');
assert.equal(requestedView('?encounter=enc-far', listedEncounters).encounterUnknown, true,
  'without a resolution the address stays unknown, so a refused id keeps the same answer');
assert.equal(requestedView('?encounter=not-returned', listedEncounters).encounterWanted, 'not-returned',
  'an unresolved address still names the id, so the caller can try to resolve it');
assert.equal(requestedView('', listedEncounters).encounterWanted, '',
  'an unaddressed view names no id to resolve');
assert.equal(requestedView('?encounter=enc-far', listedEncounters, {}).encounterUnknown, true,
  'a resolution that carried no row resolves nothing');

// The one address builder: the encounter, the role, the presenter\'s mode and the theme come from the same place.
assert.equal(viewAddress({ encounterId: 'enc-a', role: 'laakari', mode: 'normal', theme: 'dark' }),
  '?role=laakari&encounter=enc-a&theme=dark', 'viewAddress writes the encounter and the role, and theme=dark');
assert.equal(viewAddress({ encounterId: 'enc-a', mode: 'normal', theme: 'light' }), '?encounter=enc-a',
  'mode=normal is the absence of the parameter, and light writes no theme');
assert.equal(viewAddress({ encounterId: 'enc-a', mode: 'decision-off' }), '?encounter=enc-a&mode=decision-off');
assert.equal(viewAddress({ theme: 'dark' }), '?theme=dark', 'dark is the only theme value written');
assert.equal(viewAddress({ capture: true }), '?capture=1');
assert.equal(viewAddress({ mode: 'made-up' }), '', 'a mode outside PICTURE_MODES is never written');
assert.equal(viewAddress({}), '', 'no parameter set writes no query string');
assert.equal(viewAddress(), '', 'the builder takes no argument');
// Round trip: the address a view writes reads back as the same view.
const written = viewAddress({ encounterId: 'enc-b', role: 'laakari', mode: 'foundry-off', theme: 'dark', capture: true });
const readBack = requestedView(written, listedEncounters);
assert.equal(readBack.encounterId, 'enc-b');
assert.equal(readBack.mode, 'foundry-off');
assert.equal(readBack.theme, 'dark');
assert.equal(readBack.capture, true);
assert.equal(readBack.page, '', 'an address without `page` is the briefing');
// The technical page (B2) is `page=technical`, and only that: any other value is the briefing, and the
// parameter is never written for the briefing. (`view=eu` below stays refused: `page` is not a presentation.)
const technicalAddress = viewAddress({ encounterId: 'enc-b', role: 'laakari', page: 'technical' });
assert.equal(technicalAddress, '?role=laakari&encounter=enc-b&page=technical', 'the technical page is written as page=technical');
assert.equal(requestedView(technicalAddress, listedEncounters).page, 'technical', 'and reads back as the technical page');
assert.equal(requestedView(technicalAddress, listedEncounters).encounterId, 'enc-b', 'with the encounter it describes');
assert.equal(viewAddress({ encounterId: 'enc-b', page: 'briefing' }), '?encounter=enc-b', 'no other page value is written');
assert.equal(requestedView('?encounter=enc-b&page=settings', listedEncounters).page, '', 'no other page value is read');
const forwarded = requestedView('?capture=1&theme=dark&view=eu', listedEncounters);
assert(!Object.hasOwn(forwarded, 'view'), 'URL parameters cannot enable an end-user presentation');
assert.equal(forwarded.capture, true);
// `style` and `preset` are gone: ADR-0016 makes the design system the single authority, so a stale link
// carrying them must be ignored rather than select something.
assert(!Object.hasOwn(forwarded, 'style') && !Object.hasOwn(forwarded, 'preset'),
  'there are no style or preset selectors in the presentation options');
assert.equal(requestedView('?style=R&preset=pale-teal-signal', listedEncounters).theme, 'light',
  'a stale style/preset link falls back to the default appearance');
assert.equal(requestedView('?encounter=enc-b', listedEncounters).capture, false, 'capture is opt-in');
assert.equal(requestedView('?theme=dark', listedEncounters).theme, 'dark');
assert.equal(requestedView('?mode=dark', listedEncounters).theme, 'dark',
  "the mockup's mode=dark shorthand still selects the dark theme");
assert.equal(requestedView('?mode=dark', listedEncounters).mode, 'normal',
  'a light/dark value in mode never reaches the picture route');
assert.equal(requestedView('?mode=decision-off', listedEncounters).theme, 'light');
assert.equal(requestedView('', listedEncounters).theme, 'light', 'light is the default appearance');
assert.equal(requestedView('?theme=dark&mode=foundry-off', listedEncounters).mode, 'foundry-off',
  'theme and picture mode are independent');

// There is one style layer now (ADR-0016). It used to be gated on data-style so that a plain visit rendered
// the pre-layer baseline; that attribute and the seven palettes are gone, so the check below simply asserts
// that none of them can come back.
// Phase 04 gave every heading a `.t-*` role class, so these assertions match the heading text inside an
// <h2> rather than the bare tag. No heading text changed.
const styleCss = readFileSync(new URL('./styles.css', import.meta.url), 'utf8');
// The app sheet defines no custom properties at all now: every colour, radius, size and duration comes
// from the vendored design system, and the hash-pinned gate in design-system.test.mjs owns their values.
assert.deepEqual([...styleCss.matchAll(/^\s*(--[a-z-]+):/gm)].map(m => m[1]), [],
  'the app stylesheet defines no design tokens of its own');
assert(!styleCss.includes('#215d4a') && !styleCss.includes('#93662d'),
  'the app stylesheet carries no teal/green leftovers');
// Phase 03 consolidated three sheets into one and re-scoped every skin rule to the app root. The
// scoping invariant itself is enforced by design-system.test.mjs; here we assert the skin attribute is
// gone for good, so nothing can quietly reintroduce a second palette layer.
const styleRules = [...styleCss.replace(/\/\*[\s\S]*?\*\//g, '').matchAll(/(^|\})([^{}]+)\{/g)]
  .flatMap(match => match[2].split(',').map(selector => selector.trim()).filter(selector => selector && !selector.startsWith('@')));
assert(styleRules.length > 200, `the consolidated sheet still carries its rules (found ${styleRules.length})`);
for (const gone of ['data-style', 'data-preset', '--r-', '--brief-']) {
  assert(!styleCss.includes(gone), `${gone} must not survive the migration`);
}

// The merge deleted every class family nothing renders, so that the design-system gate can be turned on
// over a sheet with no dead code in it. A resurrected family would be a gate failure waiting to happen.
const DEAD_FAMILIES = ['brief-about', 'brief-assessment', 'brief-assessment-item', 'brief-citations',
  'brief-deterministic', 'brief-disclosure', 'brief-encounters', 'brief-fact', 'brief-fact-heading',
  'brief-facts', 'brief-generated', 'brief-grid', 'brief-items', 'brief-layout', 'brief-mobile-context',
  'brief-person-group', 'brief-picker-search', 'brief-preset-picker', 'brief-primary', 'brief-processing',
  'brief-role-nav', 'brief-score', 'brief-section-head', 'brief-smoke-scorecard', 'brief-source-excerpt',
  'brief-summary', 'brief-support'];
assert.equal(DEAD_FAMILIES.length, 27, 'the dead-family list is complete');
const resurrected = DEAD_FAMILIES.filter(name => new RegExp(`\\.${name}(?![\\w-])`).test(styleCss));
assert.deepEqual(resurrected, [], 'no dead class family survives the merge');
const draftRule = styleCss.match(/\.brief-app \.brief-selected-record\.brief-record-draft\s*\{([^}]*)\}/)?.[1] ?? '';
assert(draftRule.includes('color: var(--color-text-muted)') && draftRule.includes('font-style: italic'),
  'draft styling is explicitly muted and italic');

// The frozen mockup is no longer read. Its PRESETS comparison is replaced by design-system.test.mjs,
// which pins the vendored library by checksum and bans raw values in the app stylesheets. The mockup's
// generated images and its style-options.html were deleted on 2026-10-11 as redundant binaries; what is
// still committed under .scratch/ui-remake/ is the written record (README, spec, the eight issues) of
// what the remake was copying.

// ---- Anchor integrity: every `#source-*` link points at exactly one element ----
//
// `sourceHref` is called in six places and the targets only exist for the visible-source set. A link to a
// record the filter dropped is a control that looks like evidence and goes nowhere, and two anchors with
// one id resolve to whichever renders first. This renders a briefing whose conflict and assessment name
// records that are *outside* the selection, then requires every internal anchor to resolve exactly once.
const anchorPicture = {
  ...picture,
  selection: { ...picture.selection, baseline_record_ids: ['source-a'] },
  assessments: [{ instrumentti: 'PHQ-9', instrument_version: 'demo-v2', valmis: false, total: null,
    max_total: 27, tulkinta: null, osiot: [], source_answers: 3, lahteet: ['source-a', 'source-b'],
    temporal_status: 'incomplete' }],
  conflicts: [{ drug: 'Lääke', medication_record_id: 'source-b', note_record_id: 'source-c' }],
};
const anchorHtml = renderToStaticMarkup(React.createElement(PictureBriefing, {
  picture: anchorPicture, personName: 'Aino', roleLabel: 'Sairaanhoitaja', purposeLabel: 'Mielialan arviointi',
}));
// The briefing renders inside the shell's content landmark (`#brief-content` in App.jsx), which is what its
// technical-page links point at; the wrapper here stands in for that landmark, so those links are checked too.
const anchorDom = new JSDOM(`<!doctype html><html><body><div id="brief-content">${anchorHtml}</div></body></html>`).window.document;
assert(anchorHtml.includes('href="#brief-content"'), 'the briefing links to the technical page through the content landmark');
const internalAnchors = [...anchorDom.querySelectorAll('a[href^="#"]')]
  .map(link => link.getAttribute('href')).filter(href => href && href.length > 1);
for (const href of new Set(internalAnchors)) {
  const matches = anchorDom.querySelectorAll(`[id="${href.slice(1)}"]`);
  assert.equal(matches.length, 1,
    `${href} must resolve to exactly one element, found ${matches.length} — an id rendered twice, or a link`
    + ' to a record the visible-source filter dropped');
}
// Non-vacuity: the fixture must actually emit the anchors this is about, and at least one must be a plain
// span rather than a link, because the record it names is not rendered.
assert(internalAnchors.filter(href => href.startsWith('#source-')).length > 0,
  'the anchor fixture emits no source anchors, so the assertion above is looking at nothing');
assert(anchorHtml.includes('Lääkelista') && anchorHtml.includes('Kirjaus'),
  'the conflict row renders both of its labels even though one record is outside the selection');

// ---- F5: a definite proposal-review failure is visible and associated with the action ----
{
  const proposalPicture = { ...picture, proposals: [{ proposal_id: 'review-x', rule_version: 'v1',
    text: 'Tarkista lääkityksen ja kirjauksen ristiriita.', status: 'source_conflict', evidence_record_ids: ['source-a'] }] };
  const failedHtml = renderToStaticMarkup(React.createElement(PictureBriefing, {
    picture: proposalPicture,
    proposalReviews: { 'review-x': { status: 'failed', error: 'Ehdotusta ei enää ole.' } },
  }));
  const failedDocument = new JSDOM(`<!doctype html><html><body>${failedHtml}</body></html>`).window.document;
  const failure = failedDocument.getElementById('proposal-error-review-x');
  assert(failure && failure.textContent.includes('Palvelu hylkäsi tallennuksen'),
    'a definite failure renders its explanation');
  assert(!failure.classList.contains('visually-hidden'),
    'the definite failure is visible, not only announced to assistive technology');
  assert.equal(failure.getAttribute('role'), 'alert', 'and it is still a live announcement');
  const described = [...failedDocument.querySelectorAll('button')]
    .filter(button => button.getAttribute('aria-describedby') === 'proposal-error-review-x');
  assert(described.length > 0, 'the failure is associated with the action it belongs to');
  // The buttons are usable again (only `saving`/`saved`/`decided`/`ambiguous` disable them), so the click
  // that failed must not look like a page that did nothing.
  const accept = [...failedDocument.querySelectorAll('button')].find(button => button.textContent === 'Hyväksy ehdotus');
  assert(accept && accept.getAttribute('aria-disabled') === null,
    'the decisions are available again after a definite rejection');
  // Work order G 1a: after a definite failure the three controls are PRESENT, together with the failure text.
  const offeredAfterFailure = [...failedDocument.querySelectorAll('button')]
    .filter(button => ['Muokkaa ehdotusta', 'Hylkää ehdotus', 'Hyväksy ehdotus'].includes(button.textContent));
  assert.equal(offeredAfterFailure.length, 3, 'a failed save still offers its three controls next to the failure text');
}

// ---- a decided proposal renders as decided, and no second decision is offered ----
{
  const decidedProposal = { proposal_id: 'review-decided', rule_version: 'v1',
    text: 'Tarkista lääkityksen ja kirjauksen ristiriita.', status: 'source_conflict', evidence_record_ids: ['source-a'] };
  const proposalPicture = { ...picture, proposals: [decidedProposal] };
  const buttonsIn = document => [...document.querySelectorAll('button')]
    .filter(button => ['Muokkaa ehdotusta', 'Hylkää ehdotus', 'Hyväksy ehdotus'].includes(button.textContent));

  // (a) a proposal the caller decided: the action taken and when, with the buttons no longer actionable.
  const savedHtml = renderToStaticMarkup(React.createElement(PictureBriefing, {
    picture: proposalPicture,
    proposalReviews: { 'review-decided': { status: 'saved', action: 'accept', recordedAt: '2026-10-11T09:00:00Z' } },
  }));
  const savedDocument = new JSDOM(`<!doctype html><html><body>${savedHtml}</body></html>`).window.document;
  const savedStatus = savedDocument.querySelector('.brief-inline-status[role="status"]');
  assert(savedStatus && savedStatus.textContent.includes('Tallennettu palveluun'),
    'a decided proposal says its decision was recorded');
  assert(savedStatus.textContent.includes('Hyväksytty'), 'and shows the action that was taken');
  assert(savedDocument.querySelector('time[datetime="2026-10-11T09:00:00Z"]'),
    'and when it was recorded, as a machine-readable time');
  // Work order G 1a: the controls are absent, not disabled, once a decision is recorded.
  assert.equal(buttonsIn(savedDocument).length, 0,
    'a proposal the caller decided offers no decision controls: they are absent, not disabled');

  // The same proposal's 409 outcome: the server says the caller already decided, so the rail says that and
  // stops offering the buttons even though the load had not reported the decision.
  const decidedHtml = renderToStaticMarkup(React.createElement(PictureBriefing, {
    picture: proposalPicture,
    proposalReviews: { 'review-decided': { status: 'decided' } },
  }));
  const decidedDocument = new JSDOM(`<!doctype html><html><body>${decidedHtml}</body></html>`).window.document;
  assert(decidedDocument.body.textContent.includes('Olet jo päättänyt tämän ehdotuksen'),
    'the caller\'s own duplicate renders as their earlier decision, not as a generic failure');
  assert(!decidedDocument.body.textContent.includes('Palvelu hylkäsi tallennuksen'),
    'and not as a rejection that would read as a failed write');
  assert.equal(buttonsIn(decidedDocument).length, 0, 'a decision the server reports as already made is not offered again');

  // An ambiguous write is unresolved: its retry stays reachable with the same idempotency key (unchanged).
  const ambiguousHtml = renderToStaticMarkup(React.createElement(PictureBriefing, {
    picture: proposalPicture,
    proposalReviews: { 'review-decided': { status: 'ambiguous', error: 'Yhteys katkesi.' } },
  }));
  const ambiguousDocument = new JSDOM(`<!doctype html><html><body>${ambiguousHtml}</body></html>`).window.document;
  assert(ambiguousDocument.body.textContent.includes('Tallennuksen tila on epäselvä'),
    'an ambiguous write says its outcome is unresolved');
  assert([...ambiguousDocument.querySelectorAll('button')].some(button => button.textContent === 'Yritä samaa tallennusta uudelleen'),
    'an ambiguous write keeps its retry control, so the same save stays reachable');

  // (b) a proposal nobody decided: the three decisions are still offered, enabled.
  const openHtml = renderToStaticMarkup(React.createElement(PictureBriefing, {
    picture: proposalPicture, proposalReviews: {},
  }));
  const openDocument = new JSDOM(`<!doctype html><html><body>${openHtml}</body></html>`).window.document;
  const openButtons = buttonsIn(openDocument);
  assert.equal(openButtons.length, 3, 'an undecided proposal offers all three decisions');
  for (const button of openButtons) {
    assert.equal(button.getAttribute('aria-disabled'), null, `"${button.textContent}" is available`);
  }
  assert(!openDocument.body.textContent.includes('Olet jo päättänyt tämän ehdotuksen'));
}

// ---- work order G 1b, moved by work order L: the proposal sits in the rail, before the assistant. It was moved to the
// reading column beside its attention item in G 1b; work order L restores the rail placement, so these checks follow
// the card to `.brief-rail-section` and keep their intent: one proposal, rendered once, not in the reading column.
{
  const placedPicture = { ...picture, proposals: [{ proposal_id: 'review-placed', rule_version: 'v1',
    text: 'Tarkista lääkityksen ja kirjauksen ristiriita.', status: 'source_conflict', evidence_record_ids: [] }] };
  const placedHtml = renderToStaticMarkup(React.createElement(PictureBriefing, { picture: placedPicture, proposalReviews: {} }));
  const placedDocument = new JSDOM(`<!doctype html><html><body>${placedHtml}</body></html>`).window.document;
  const proposalCard = placedDocument.querySelector('.brief-proposal.brief-card');
  assert(proposalCard, 'the proposal card renders');
  const rail = placedDocument.querySelector('.brief-rail-section');
  assert(rail && rail.contains(proposalCard), 'the proposal card is in the rail (.brief-rail-section)');
  assert(!placedDocument.querySelector('.brief-record-main')?.contains(proposalCard), 'and not in the reading column');
  assert.equal(proposalCard.parentElement, rail, 'as a direct child of the rail');
  assert.equal(rail.firstElementChild, proposalCard, 'it is the first thing in the rail, before the assistant');
  assert.equal([...placedDocument.querySelectorAll('.brief-proposal.brief-card')].length, 1,
    'the proposal is rendered once');
}

console.log('Reducer, Picture presentation, and consolidated-stylesheet checks passed.');


/*
 * The urgency labels are pinned to the protocol document, in both directions and on the count.
 *
 * Decision-1 is asked how time-critical a Kirjaus is for this visit, and ADR-0009 makes that answer order
 * already-eligible records only. The interface may therefore show the classifier's own level in words, but it
 * must never invent severity wording of its own, and the distribution it annotates has to be the one the
 * classifier actually returned. The document's table is rendered from `app.decision_model.URGENCY_LEVELS` by
 * `backend/scripts/render_decision_protocol.py`, so this reads the rendered artefact rather than a copy.
 */
const PROTOCOL_DOC = readFileSync(new URL('../../docs/decision-protocol.md', import.meta.url), 'utf8');
const LEGEND_ROWS = PROTOCOL_DOC.split('\n')
  .filter(line => /^\|\s*\d\s*\|/.test(line))
  .map(line => line.split('|').map(cell => cell.trim()));
assert.equal(LEGEND_ROWS.length, 5, 'the protocol document must render five urgency levels');

assert.deepEqual(URGENCY_UI_LABELS, LEGEND_ROWS.map(row => row[3]),
  'the UI labels must equal the protocol document\'s label column, in level order');
for (const [index, row] of LEGEND_ROWS.entries()) {
  assert.equal(Number(row[1]), index, 'the document levels must be 0-4 in order');
  assert(row[2] && row[2].length > 0, `level ${index} must carry the classifier's legend text`);
}

// The label is the argmax of the distribution that is already on the wire -- never a new number, and never a
// default when the distribution is unusable.
assert.equal(leadingUrgencyLabel({ '0': 0, '1': 0, '2': 0.1, '3': 0.8, '4': 0.1 }), 'nopea huomio');
assert.equal(leadingUrgencyLabel({ '4': 0.9, '3': 0.05 }), 'aikakriittinen');
assert.equal(leadingUrgencyLabel({}), null, 'no distribution means no label, not a default level');
assert.equal(leadingUrgencyLabel(undefined), null);

// A label is an ordering basis, so no label may read as a clinical severity or a treatment instruction.
for (const label of URGENCY_UI_LABELS) {
  assert(!/hengenvaara|välitön hoito|hätä|kriittinen potilas/i.test(label),
    `urgency label ${JSON.stringify(label)} reads as a clinical severity claim`);
}

// ---- work order N: the engine's steps lead the answer with their basis; the source count; the follow-ups ----
{
  const STEP_NOTE = 'Havainnollistava sääntö, ei kliinisesti kalibroitu eikä hyväksytty käyttöön.';
  const stepRow = (id, text) => ({ suggestion_id: id, turn_id: 'turn-steps', ordinal: 0, statement_class: 'rule_output',
    text, label: 'Sääntöpohjainen arvio (ehdotus)', basis_note: `Tilanne: krooninen. ${STEP_NOTE}`, source_record_ids: [] });
  const stepLead = (question) => ({ suggestion_id: 'lead', turn_id: 'turn-steps', ordinal: 0, statement_class: 'answer_lead',
    text: `Kysymykseesi "${question}" tämän palvelutilanteen sallitut kirjaukset vastaavat näin:`, label: 'Kysymys',
    source_record_ids: [] });
  const finding = { ...railTurn.suggestions[0], suggestion_id: 'f0', turn_id: 'turn-steps' };
  const stepTurn = { ...railTurn, turn_id: 'turn-steps', suggestions: [stepLead('Mitä teen?'),
    stepRow('st0', 'Jatka omaseurantaa'), stepRow('st1', 'Suunnittele kontrollikäynti'), finding],
  source_count: { available: 4, cited: 1 } };
  const html = renderToStaticMarkup(React.createElement(ChatRail, { turn: stepTurn }));
  const document = new JSDOM(html).window.document;
  const block = document.querySelector('[data-steps="engine"]');
  assert(block, 'an answer with engine steps renders the steps block');
  // O4: the status is in the heading, which comes before the first step, so the first thing read is qualified.
  assert.equal(block.querySelector('p.t-body-strong').textContent,
    `Sääntöpohjainen ehdotus · Tilanne: krooninen. ${STEP_NOTE}`,
    'the steps are headed by the engine rule, the situation and the table status, before any step');
  assert(block.querySelector('p.t-body-strong').compareDocumentPosition(block.querySelector('li')) & 4,
    'the status heading precedes the first step in document order');
  assert.deepEqual([...block.querySelectorAll('li > p.t-body')].map(node => node.textContent),
    ['Jatka omaseurantaa', 'Suunnittele kontrollikäynti'], 'the steps are listed in the table order');
  assert.deepEqual([...block.querySelectorAll('li > p.t-caption')].map(node => node.textContent),
    [`Tilanne: krooninen. ${STEP_NOTE}`, `Tilanne: krooninen. ${STEP_NOTE}`],
    'every step carries the engine basis and the table status, visibly');
  assert.equal(block.querySelectorAll('button').length, 0, 'the steps carry no accept, edit or dismiss controls');
  assert(block.compareDocumentPosition(document.querySelector('.brief-chat-findings')) & 4,
    'the steps come before the first finding');
  assert.equal(document.querySelectorAll('.brief-chat-findings li.brief-proposal').length, 1,
    'a finding that is not a step keeps its review row');
  assert(!document.querySelector('.brief-chat-findings').textContent.includes(STEP_NOTE),
    'the steps are not repeated among the findings');

  // The source count is stated beside the composition line, and the lead never carries it.
  const counted = document.querySelector('[data-source-count="answer"]');
  // O6: the count is the selected set the answer was composed from, and the caption says so.
  assert.equal(counted.textContent, 'Vastauksessa viitataan 1 kirjaukseen; valitussa joukossa on 4 sallittua kirjausta.',
    'the caption states what the answer cites beside the selected set it counted');
  assert(!document.querySelector('.brief-chat-lead').textContent.includes('viitataan'),
    'the source count is not placed in the lead sentence');
  const single = new JSDOM(renderToStaticMarkup(React.createElement(ChatRail, {
    turn: { ...stepTurn, source_count: { available: 1, cited: 1 } } }))).window.document;
  assert.equal(single.querySelector('[data-source-count="answer"]').textContent,
    'Vastauksessa viitataan 1 kirjaukseen; valitussa joukossa on 1 sallittu kirjaus.', 'the count reads in the singular');
  const uncited = new JSDOM(renderToStaticMarkup(React.createElement(ChatRail, {
    turn: { ...stepTurn, source_count: { available: 4, cited: 0 } } }))).window.document;
  assert.equal(uncited.querySelector('[data-source-count="answer"]'), null,
    'no source count is rendered when the answer cites no record');

  // Follow-ups: the offered questions other than the one this answer answers, and never on a refusal.
  const offered = [{ text: 'Mitä teen?', why: 'syy', evidence_record_ids: [] },
    { text: 'Mitä muuta?', why: 'syy', evidence_record_ids: [] }];
  const answered = new JSDOM(renderToStaticMarkup(React.createElement(ChatRail, {
    turn: stepTurn, prompts: offered, question: 'Mitä teen?' }))).window.document;
  assert.deepEqual([...answered.querySelectorAll('[data-followups="answer"] button')].map(node => node.textContent),
    ['Mitä muuta?'], 'the answered turn offers the other questions, not the one it answers');
  const refusal = renderToStaticMarkup(React.createElement(ChatRail, {
    turn: { ...railTurn, outcome: 'procedural_refusal', suggestions: [], abstention: 'Ei vastausta tähän kysymykseen.' },
    prompts: offered }));
  assert(!refusal.includes('data-followups'), 'a refusal offers no follow-up questions');
  assert(!refusal.includes('data-steps') && !refusal.includes('data-source-count'),
    'a refusal shows neither steps nor a source count');
  // O7: a refusal that carries a populated, qualified step AND prompts renders neither the steps nor the follow-ups.
  const dangerous = renderToStaticMarkup(React.createElement(ChatRail, {
    turn: { ...stepTurn, outcome: 'procedural_refusal', abstention: 'Ei vastausta tähän kysymykseen.' },
    prompts: offered, question: 'Mitä teen?' }));
  assert(stepTurn.suggestions.some(item => item.basis_note && item.text === 'Jatka omaseurantaa'),
    'the fixture carries a qualified step, so the test is not vacuous');
  assert(!dangerous.includes('data-steps') && !dangerous.includes('Jatka omaseurantaa'),
    'a refusal with a qualified step renders no steps block');
  assert(!dangerous.includes('data-followups') && !dangerous.includes('Mitä muuta?'),
    'a refusal with populated prompts renders no follow-up questions');

  // O2: a legacy step with no basis note is withheld entirely; a step with the note renders.
  const legacyStep = { suggestion_id: 'legacy', turn_id: 'turn-steps', ordinal: 0, statement_class: 'rule_output',
    text: 'Lopeta lääkitys ilman lääkärin arviota', label: 'Sääntöpohjainen arvio (ehdotus)', source_record_ids: [] };
  const legacy = renderToStaticMarkup(React.createElement(ChatRail, {
    turn: { ...stepTurn, suggestions: [stepLead('Mitä teen?'), legacyStep, finding] } }));
  assert(!legacy.includes('data-steps') && !legacy.includes('Lopeta lääkitys'),
    'a step stored without its basis note renders no step and no unqualified imperative');
  assert(!legacy.includes('brief-proposal') || !legacy.includes('Lopeta lääkitys'),
    'a withheld step is not shown as a finding either');
  assert(legacy.includes('brief-chat-findings'), 'the other findings still render');
  const noted = renderToStaticMarkup(React.createElement(ChatRail, { turn: stepTurn }));
  assert(noted.includes('data-steps="engine"'), 'a step with its note renders the steps block');

  // The clicks themselves: a follow-up asks its own text, through the same path as an offered card.
  const { fetchMock, calls } = chatApi({
    promptsFor: () => ({ prompts: offered, guideline_links: [] }),
    answers: [chatAnswer(stepTurn), chatAnswer({ ...stepTurn, turn_id: 'turn-next',
      suggestions: [stepLead('Mitä muuta?'), stepRow('st2', 'Tarkista lääkitys')] })],
  });
  await withChatPanel(fetchMock, async ({ document, render, click, ask }) => {
    await render({ encounterId: 'enc-a', api: '' });
    await ask({ text: 'Mitä teen?' });
    assert.equal(calls.ask[0].question, 'Mitä teen?');
    const followUp = [...document.querySelectorAll('[data-followups="answer"] button')]
      .find(node => node.textContent === 'Mitä muuta?');
    assert(followUp, 'the answered turn renders the follow-up as a button');
    await click(followUp);
    assert.equal(calls.ask.length, 2, 'clicking a follow-up sends one question');
    assert.equal(calls.ask[1].question, 'Mitä muuta?', 'the follow-up asks its own text');
  });
}

// Work order S6: an advice row is counted, cited by its basis note, shows its refs as text, and is withheld without refs.
{ // a knowledge-advice row is counted in the composition line, and withheld when it has no ref
  const lead = { suggestion_id: 'lead', turn_id: 'turn-adv', ordinal: 0, statement_class: 'answer_lead',
    text: 'Lead.', label: 'Kysymys', source_record_ids: [] };
  const fact = { suggestion_id: 'fact', turn_id: 'turn-adv', ordinal: 1, statement_class: 'source_fact',
    text: 'Kirjaus.', label: 'Lähde', source_record_ids: [] };
  const advice = { suggestion_id: 'adv', turn_id: 'turn-adv', ordinal: 2, statement_class: 'knowledge_advice',
    text: 'Mittaa tarvittaessa ohjeen mukaan.', label: CLASS_LABELS.knowledge_advice, source_record_ids: [],
    refs: ['form:f#q'], basis_note: 'Lähde: Lääkitysarvio · v2' };
  const answerOf = suggestions => ({ turn_id: 'turn-adv', outcome: 'answer', suggestions });
  assert.match(answerCompositionText(answerOf([lead, fact, advice])),
    /^Vastauksessa on 1 lähdekirjaus ja 1 tietopohjainen ehdotus\./);
  assert.equal(answerCompositionText(answerOf([lead, fact, { ...advice, refs: [] }])), null,
    'an advice row with no ref makes the count unsayable, rather than counting a row that is not shown');
}

{ // a knowledge-advice row renders its basis note and refs as text, and a row with no refs does not render
  const advice = { suggestion_id: 'adv', turn_id: 'turn-adv', ordinal: 0, statement_class: 'knowledge_advice',
    text: 'Mittaa tarvittaessa ohjeen mukaan.', label: CLASS_LABELS.knowledge_advice, source_record_ids: [],
    refs: ['form:f#q'], basis_note: 'Lähde: Lääkitysarvio · v2' };
  const turnOf = suggestions => ({ turn_id: 'turn-adv', outcome: 'answer', suggestions });
  const shown = new JSDOM(renderToStaticMarkup(React.createElement(ChatRail, { turn: turnOf([advice]) })))
    .window.document;
  const row = shown.querySelector('li.brief-proposal');
  assert.ok(row, 'the advice row renders');
  assert.ok(row.textContent.includes('Lähde: Lääkitysarvio · v2'), 'the citation line is the basis note');
  assert.ok(row.textContent.includes('Viitteet: form:f#q'), 'the refs are printed as text');
  assert.equal(row.querySelectorAll('a[href*="form:f"]').length, 0, 'the refs are not links');
  const withheld = new JSDOM(renderToStaticMarkup(React.createElement(ChatRail,
    { turn: turnOf([{ ...advice, refs: [] }]) }))).window.document;
  assert.equal(withheld.querySelectorAll('li.brief-proposal').length, 0,
    'an advice row with no refs is not rendered');
}

// ---- The assistant's event stream: only tool names and verified sentences travel; the final turn is the reply ----
{
  const { readTurnStream } = await import('./challenge-demo.mjs');
  const encoder = new TextEncoder();
  const frames = [
    'event: progress\ndata: {"state":"reading","generator_configured":true}\n\n',
    'event: tool\ndata: {"name":"list_records"}\n\nevent: tool\ndata: {"name":"get_rec',
    'ord"}\n\nevent: sentence\ndata: {"text":"Käytössä on levotyroksiini.","source_ids":["r1"]}\n\n',
    'event: turn\ndata: {"turn":{"turn_id":"t1","outcome":"answer","suggestions":[],"followups":["Onko annos muuttunut?"]},"digest":{"digest_id":"d1"},"prompts":[],"guideline_links":[],"answer_notice":"n"}\n\n',
  ];
  const body = new ReadableStream({
    start(controller) { for (const frame of frames) controller.enqueue(encoder.encode(frame)); controller.close(); },
  });
  const events = [];
  // C4: after a verified sentence the reader yields to the event loop before parsing the next block, so a
  // short answer's sentence is painted before the final turn replaces the live list. The probe is a macrotask
  // queued from the sentence event: it can only have run by the time the reader returns if the reader waited
  // for the macrotask queue, which a reader that only awaited `reader.read()` (microtasks) never does.
  const painted = [];
  const final = await readTurnStream(body, event => {
    events.push(event);
    if (event.name === 'sentence') setTimeout(() => painted.push(event.data.text), 0);
  });
  assert.deepEqual(painted, ['Käytössä on levotyroksiini.'],
    'the reader yields a macrotask after the sentence, so React can paint it before the turn arrives');
  assert.deepEqual(events.map(event => event.name), ['progress', 'tool', 'tool', 'sentence'],
    'every event before the turn reaches the caller, in order, across chunk boundaries');
  assert.deepEqual(events[2].data, { name: 'get_record' }, 'an event split across two chunks is reassembled');
  assert.equal(final.turn.turn_id, 't1');
  assert.deepEqual(final.turn.followups, ['Onko annos muuttunut?']);
  assert.equal(final.digest.digest_id, 'd1');
  const failing = new ReadableStream({
    start(controller) { controller.enqueue(encoder.encode('event: error\ndata: {"status":503,"detail":"not saved"}\n\n')); controller.close(); },
  });
  assert.deepEqual(await readTurnStream(failing, () => {}), { error: { status: 503, detail: 'not saved' } },
    'an error event is returned as a failure the rail reports, not as a turn');
}

// ---------- N3: table folds and lab labels ----------
//
// The diagnoses table draws one row per (ICD-10 code, status) from the newest record; the medications table
// puts the current medications first and folds the ended ones behind a disclosure; a lab row names the
// abbreviation in plain Finnish without hiding it. Every fold is over rows the table already renders — the
// selected, permitted sources — so every count is of rendered and folded rows, and every folded record keeps
// its `source-<id>` anchor in the DOM so a citation still has a target.
const { describe, it } = await import('node:test');
const {
  LAB_LABELS, labLabel, foldStatusRows, statusCountText, splitMedicationRows, medicationCountText, medicationIsHistorical,
} = await import('./challenge-demo.mjs');

const foldRecord = (record_id, record_type, recorded_at, payload) => ({
  record_id, record_type, topic: 'hoito', source: 'Synteettinen', recorded_at, payload,
  authored_by_role: 'laakari', selection_reasons: ['keyword:hoito'],
});
const foldRecords = [
  foldRecord('dx-e11-old', 'diagnoosi', '2024-03-01T00:00:00Z', { nimi: 'Tyypin 2 diabetes', icd10: 'E11.9', tila: 'aktiivinen' }),
  foldRecord('dx-e11-mid', 'diagnoosi', '2025-01-15T00:00:00Z', { nimi: 'Tyypin 2 diabetes', icd10: 'E11.9', tila: 'aktiivinen' }),
  foldRecord('dx-e11-new', 'diagnoosi', '2026-02-20T00:00:00Z', { nimi: 'Tyypin 2 diabetes', icd10: 'E11.9', tila: 'aktiivinen' }),
  foldRecord('dx-i10', 'diagnoosi', '2025-06-01T00:00:00Z', { nimi: 'Verenpainetauti', icd10: 'I10', tila: 'aktiivinen' }),
  foldRecord('dx-j06-ended', 'diagnoosi', '2025-03-15T00:00:00Z', { nimi: 'Ylähengitystieinfektio', icd10: 'J06.9', tila: 'ei_aktiivinen' }),
  foldRecord('decision-a', 'päätös', '2025-09-01T00:00:00Z', { päätös: 'Kotihoidon palvelupäätös', voimassa: 'toistaiseksi' }),
  foldRecord('med-metformin', 'lääkitys', '2026-01-10T00:00:00Z', { lääke: 'metformiini', annos: '1 g x 2', tila: 'aktiivinen' }),
  foldRecord('med-parasetamol', 'lääkitys', '2025-06-14T00:00:00Z', { lääke: 'parasetamoli', annos: 'tarvittaessa', tila: 'päättynyt' }),
  foldRecord('med-ramipril', 'lääkitys', '2026-02-01T00:00:00Z', { lääke: 'ramipriili', annos: '5 mg x 1', tila: 'aktiivinen' }),
  foldRecord('med-diklofenak', 'lääkitys', '2025-11-14T00:00:00Z', { lääke: 'diklofenaakki', annos: 'tarvittaessa', tila: 'päättynyt' }),
  foldRecord('lab-hba1c', 'tutkimustulos', '2026-02-20T00:00:00Z', { tutkimus: 'B-HbA1c', arvo: 52, yksikkö: 'mmol/mol', tulkinta: 'koholla' }),
  foldRecord('lab-krea', 'tutkimustulos', '2026-02-20T00:00:00Z', { tutkimus: 'P-Krea', arvo: 78, yksikkö: 'µmol/l', tulkinta: 'viitearvo' }),
  foldRecord('lab-unknown', 'tutkimustulos', '2026-02-20T00:00:00Z', { tutkimus: 'X-Tuntematon', arvo: 1, yksikkö: '', tulkinta: 'viitearvo' }),
];
const foldPicture = {
  ...provenancePicture,
  selection: { ...provenancePicture.selection, baseline_record_ids: foldRecords.map(record => record.record_id),
    additional_record_ids: [], emphasized_record_ids: [],
    // Decision-1 called the newest E11.9 record current and the oldest historical: the folded row must carry
    // the newest record's chip, not the oldest's.
    decision_notes: [
      { record_id: 'dx-e11-new', status: 'relevant', model: 'decision-1.13.0', time_sensitivity: 'current' },
      { record_id: 'dx-e11-old', status: 'irrelevant', model: 'decision-1.13.0', time_sensitivity: 'historical' },
    ] },
  records: { priority: [], accessible_other: foldRecords },
};
const foldProps = { picture: foldPicture, personName: 'Aino', roleLabel: 'Sairaanhoitaja', purposeLabel: 'Lääkityksen seuranta' };
const foldHtml = renderToStaticMarkup(React.createElement(PictureBriefing, foldProps));
const foldDocument = new JSDOM(foldHtml).window.document;
const sectionOf = id => foldDocument.getElementById(id);
const rowsOf = section => [...section.querySelectorAll('tbody tr')].filter(row => !row.classList.contains('brief-source-metadata'));

describe('N3a: the diagnoses table folds one row per code and status', () => {
  const section = sectionOf('section-diagnoses');
  it('draws one row per (ICD-10 code, status), from the newest record', () => {
    const rows = rowsOf(section);
    assert.equal(rows.length, 4, 'E11.9 aktiivinen, I10 aktiivinen, J06.9 ei_aktiivinen and the decision: four rows for six records');
    const e11 = rows.find(row => row.textContent.includes('Tyypin 2 diabetes'));
    assert.equal(e11.id, 'source-dx-e11-new', 'the drawn row is the newest record of the group and carries its anchor');
    assert(e11.textContent.includes('20.2.2026'), 'the row shows the newest date');
    assert(e11.textContent.includes('×3 kirjausta'), 'a folded row says how many records it stands for');
    const i10 = rows.find(row => row.textContent.includes('Verenpainetauti'));
    assert(!i10.textContent.includes('kirjausta'), 'a row that stands for one record carries no fold tag');
    assert.equal(section.querySelectorAll('tr#source-dx-e11-new').length, 1);
  });
  it('keeps every folded record anchored in the DOM, inside the drawn row', () => {
    for (const id of ['dx-e11-old', 'dx-e11-mid']) {
      const anchor = section.querySelector(`#source-${id}`);
      assert(anchor, `the folded record ${id} still has its #source- anchor`);
      assert.equal(anchor.closest('ul').className, 'visually-hidden', 'the folded anchors sit in a visually-hidden list');
      assert.equal(anchor.closest('tr').id, 'source-dx-e11-new', 'inside the row that stands for them');
    }
    assert.equal([...foldHtml.matchAll(/id="source-dx-e11-/g)].length, 3, 'each record of the group is anchored exactly once');
  });
  it('shows the newest record\'s Decision-1 chip, not the oldest\'s', () => {
    // The drawn row and its provenance row (the `brief-source-metadata` sibling) are the group's whole surface.
    const row = section.querySelector('tr#source-dx-e11-new');
    const provenance = row.nextElementSibling;
    assert(provenance.classList.contains('brief-source-metadata'), 'the drawn row carries a provenance row');
    const e11Block = row.textContent + provenance.textContent;
    assert(e11Block.includes('Olennainen tälle käynnille') && e11Block.includes('Ajankohtainen'), 'the newest record\'s chips');
    assert(!e11Block.includes('Taustatieto tälle käynnille') && !e11Block.includes('Aiempi (mallin mukaan)'), 'and not the oldest\'s');
    assert(!section.textContent.includes('Taustatieto tälle käynnille'), 'the folded record\'s chip is nowhere in the table');
  });
  it('counts rendered rows and the records they fold', () => {
    assert(section.textContent.includes('3 diagnoosia · 1 päätöstä · 6 kirjausta'), section.querySelector('.t-caption')?.textContent);
    assert.equal(statusCountText(foldStatusRows([])), '0 kirjausta');
  });
  it('folds by code and status in the pure helper', () => {
    const groups = foldStatusRows([
      { id: 'a', type: 'diagnoosi', date: '2024-01-01', payload: { icd10: 'E11.9', tila: 'aktiivinen' } },
      { id: 'b', type: 'diagnoosi', date: '2026-01-01', payload: { icd10: 'E11.9', tila: 'aktiivinen' } },
      { id: 'c', type: 'diagnoosi', date: '2025-01-01', payload: { icd10: 'E11.9', tila: 'ei_aktiivinen' } },
      { id: 'd', type: 'diagnoosi', date: '', payload: { icd10: 'E11.9', tila: 'aktiivinen' } },
    ]);
    assert.deepEqual(groups.map(group => [group.newest.id, group.folded.map(row => row.id), group.count]),
      [['b', ['a', 'd'], 3], ['c', [], 1]], 'same code, different status is a different row; a dateless record never leads');
    assert.deepEqual(foldStatusRows([{ id: 'x', type: 'diagnoosi', payload: {} }, { id: 'y', type: 'diagnoosi', payload: {} }])
      .map(group => group.count), [1, 1], 'a record with neither code nor name is its own row');
  });
});

describe('N3b: the medications table puts current medications first and folds the ended ones', () => {
  const section = sectionOf('section-medications');
  it('renders the active rows, in order, and no ended row while collapsed', () => {
    const names = rowsOf(section).map(row => row.querySelector('th')?.textContent).filter(Boolean);
    assert.deepEqual(names, ['metformiini', 'ramipriili']);
    assert(!section.textContent.includes('parasetamoli') && !section.textContent.includes('diklofenaakki'));
  });
  it('offers the ended rows behind a labelled disclosure', () => {
    const toggle = section.querySelector('button[aria-expanded]');
    assert.equal(toggle.textContent, 'Näytä aiemmat (2)');
    assert.equal(toggle.getAttribute('aria-expanded'), 'false');
    assert(foldDocument.getElementById(toggle.getAttribute('aria-controls')), 'aria-controls points at an element in the DOM');
  });
  it('keeps the collapsed rows anchored in the DOM', () => {
    for (const id of ['med-parasetamol', 'med-diklofenak']) {
      const anchor = section.querySelector(`#source-${id}`);
      assert(anchor, `the collapsed record ${id} still has its #source- anchor`);
      assert.equal(anchor.closest('ul').className, 'visually-hidden');
    }
  });
  it('counts current and ended rows', () => {
    assert(section.textContent.includes('2 käytössä · 2 aiempaa'), section.querySelector('.t-caption')?.textContent);
    assert.equal(medicationCountText(splitMedicationRows([{ payload: { tila: 'aktiivinen' } }])), '1 käytössä');
    assert.equal(medicationCountText(splitMedicationRows([{ payload: { tila: 'päättynyt' } }])), '1 aiempaa');
  });
  it('splits on the corpus status words and keeps an unknown status in view', () => {
    assert(medicationIsHistorical({ payload: { tila: 'päättynyt' } }));
    assert(medicationIsHistorical({ payload: { tila: 'Ei_aktiivinen' } }));
    assert(!medicationIsHistorical({ payload: { tila: 'aktiivinen' } }));
    assert(!medicationIsHistorical({ payload: {} }), 'no status is not a reason to hide');
    const { active, historical } = splitMedicationRows([{ id: 1, payload: { tila: 'päättynyt' } }, { id: 2, payload: { tila: 'aktiivinen' } }, { id: 3, payload: {} }]);
    assert.deepEqual([active.map(row => row.id), historical.map(row => row.id)], [[2, 3], [1]]);
  });
  it('expands to the ended rows on click, as rows of their own', async () => {
    const dom = new JSDOM('<!doctype html><div id="root"></div>', { url: 'http://localhost' });
    const keys = ['window', 'document', 'HTMLElement', 'navigator', 'IS_REACT_ACT_ENVIRONMENT', 'ResizeObserver'];
    const previous = new Map(keys.map(key => [key, Object.getOwnPropertyDescriptor(globalThis, key)]));
    for (const [key, value] of Object.entries({
      window: dom.window, document: dom.window.document, HTMLElement: dom.window.HTMLElement,
      navigator: dom.window.navigator, IS_REACT_ACT_ENVIRONMENT: true,
      ResizeObserver: class { observe() {} disconnect() {} unobserve() {} },
    })) Object.defineProperty(globalThis, key, { configurable: true, writable: true, value });
    const root = createRoot(dom.window.document.getElementById('root'));
    const tick = () => new Promise(resolve => setTimeout(resolve, 0));
    try {
      await React.act(async () => { root.render(React.createElement(PictureBriefing, foldProps)); await tick(); });
      const live = dom.window.document.getElementById('section-medications');
      const toggle = live.querySelector('button[aria-expanded]');
      await React.act(async () => { toggle.dispatchEvent(new dom.window.MouseEvent('click', { bubbles: true })); await tick(); });
      assert.equal(toggle.textContent, 'Piilota aiemmat');
      assert.equal(toggle.getAttribute('aria-expanded'), 'true');
      const names = rowsOf(live).map(row => row.querySelector('th')?.textContent).filter(Boolean);
      assert.deepEqual(names, ['metformiini', 'ramipriili', 'parasetamoli', 'diklofenaakki'], 'current first, then the ended rows');
      assert.equal(live.querySelector('tr#source-med-parasetamol')?.tagName, 'TR', 'an expanded row carries the anchor itself');
      assert.equal(live.querySelectorAll('#source-med-parasetamol').length, 1, 'and the hidden anchor is gone: anchored exactly once');
      assert(live.textContent.includes('2 käytössä · 2 aiempaa'), 'the count line does not change with the fold');
      await React.act(async () => { toggle.dispatchEvent(new dom.window.MouseEvent('click', { bubbles: true })); await tick(); });
      assert.equal(toggle.textContent, 'Näytä aiemmat (2)');
      assert(live.querySelector('ul.visually-hidden #source-med-parasetamol'), 'collapsed again, the anchor is back in the hidden list');
    } finally {
      await React.act(async () => { root.unmount(); });
      for (const [key, descriptor] of previous) {
        if (descriptor) Object.defineProperty(globalThis, key, descriptor); else delete globalThis[key];
      }
    }
  });
});

describe('N3c: a lab row names the abbreviation in plain Finnish without hiding it', () => {
  it('maps the corpus abbreviations and leaves an unknown one unchanged', () => {
    assert.equal(labLabel('B-HbA1c'), 'Pitkäaikaissokeri (B-HbA1c)');
    assert.equal(labLabel('P-Krea'), 'Kreatiniini (P-Krea)');
    assert.equal(labLabel('fP-Gluk'), 'Paastoverensokeri (fP-Gluk)');
    assert.equal(labLabel('P-TSH'), 'Tyreotropiini (TSH) (P-TSH)');
    assert.equal(labLabel('X-Tuntematon'), 'X-Tuntematon');
    assert.equal(labLabel(undefined), undefined);
    assert.equal(labLabel(''), '');
    for (const [abbreviation, label] of Object.entries(LAB_LABELS)) {
      assert(labLabel(abbreviation).includes(abbreviation) && labLabel(abbreviation).includes(label), `${abbreviation} keeps both the label and the source value`);
    }
    for (const corpus of ['P-Krea', 'fP-Gluk', 'B-HbA1c', 'P-TSH']) assert(LAB_LABELS[corpus], `${corpus} is what the Veil import writes`);
  });
  it('renders the label with the abbreviation in the row header', () => {
    const section = sectionOf('section-labs');
    const headers = [...section.querySelectorAll('tbody th')].map(cell => cell.textContent);
    assert.deepEqual(headers, ['Pitkäaikaissokeri (B-HbA1c)', 'Kreatiniini (P-Krea)', 'X-Tuntematon']);
    assert.equal(section.textContent.match(/3 tutkimusta/)?.[0], '3 tutkimusta', 'the lab count is unchanged');
  });
});

// ---- Decision-1's findings as blue information rows in the attention panel (owner, 2026-10-14) ----
{
  const { describe, it } = await import('node:test');
  const { decisionHighlights, DECISION_HIGHLIGHT_LIMIT, picturePresentation } = await import('./challenge-demo.mjs');
  describe('decisionHighlights', () => {
    const note = (record_id, status, time_sensitivity, confidence, urgency) => ({
      record_id, status, time_sensitivity, relevance_confidence: confidence,
      gate_probabilities: urgency ? { urgency } : {},
    });
    const record = (record_id, record_type, payload, topic = 'hoito') => ({
      record_id, record_type, topic, payload, recorded_at: '2026-06-01T00:00:00Z', temporal_status: 'current', selection_reasons: [],
    });
    const base = {
      records: { priority: [record('dx-1', 'diagnoosi', { nimi: 'Kohonnut verenpaine', icd10: 'I10' }),
                            record('med-1', 'lääkitys', { lääke: 'amlodipiini' }),
                            record('lab-1', 'tutkimustulos', { tutkimus: 'B-HbA1c' }),
                            record('note-1', 'vapaateksti', { otsikko: 'Käynti' })], accessible_other: [] },
      selection: {
        decision_status: 'complete',
        baseline_record_ids: ['dx-1', 'med-1', 'lab-1'], additional_record_ids: ['note-1'], emphasized_record_ids: ['dx-1', 'med-1'],
        decision_notes: [note('dx-1', 'relevant', 'current', 0.9, { 0: 0.1, 1: 0.2, 2: 0.7 }),
                         note('med-1', 'relevant', 'current', 0.6, { 0: 0.8, 1: 0.2 }),
                         note('lab-1', 'unclear', 'current', 0.4, {}),
                         note('note-1', 'relevant', 'historical', 0.8, { 0: 0.5, 1: 0.5 })],
      },
    };
    it('names what Decision-1 added, what it put first and what it called relevant and current, titled by the record', () => {
      const items = decisionHighlights(picturePresentation(base));
      assert.deepEqual(items.map(i => [i.kind, i.id, i.title]), [
        ['added', 'note-1', 'Käynti'], ['lead', 'dx-1', 'Kohonnut verenpaine'], ['relevant', 'med-1', 'amlodipiini']]);
      assert(items[1].text.includes('selvitä tällä käynnillä'), 'the lead carries the urgency legend word');
      assert(items.length <= DECISION_HIGHLIGHT_LIMIT);
    });
    it('is empty when Decision-1 did not answer, and never names a record the picture does not render', () => {
      assert.deepEqual(decisionHighlights(picturePresentation({ ...base, selection: { ...base.selection, decision_status: 'not_run' } })), []);
      const withheld = { ...base, selection: { ...base.selection, additional_record_ids: ['note-1', 'withheld-9'] } };
      assert(!decisionHighlights(picturePresentation(withheld)).some(i => i.id === 'withheld-9'));
    });
    it('renders as blue information rows in the attention panel with a source link', () => {
      const html = renderToStaticMarkup(React.createElement(PictureBriefing, { picture: base }));
      assert(html.includes('brief-info') && html.includes('Decision-1.'), 'the rows use the info tone and the Decision-1 keyword');
      assert(html.includes('nosti esiin kirjauksen') && html.includes('#source-note-1'), 'the added record is linked');
      assert(!html.includes('(järjestysperuste, ei hoidon kiireellisyys)'), 'no ordering caveat on the briefing');
    });
  });
}
