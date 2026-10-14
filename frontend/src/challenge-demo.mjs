// Synthetic runtime helpers for the same-origin encounter API.

const unique = values => [...new Set(values)];

/*
 * The urgency levels' short UI labels, in level order (0–4).
 *
 * These are an *ordering basis*, never a patient-level verdict: Decision-1 is asked how time-critical a
 * Kirjaus is for this visit, and ADR-0009 makes that answer order already-eligible records only — it never
 * selects, withholds or times an action. The labels say so themselves, and the distribution they describe is
 * already on the wire. The full legend text lives in the protocol document, rendered from
 * `app.decision_model.URGENCY_LEVELS`; `challenge-demo.test.mjs` fails if this array and that document's
 * label column disagree, so the two cannot drift apart silently.
 *
 * Nothing here is a clinical severity word. If a label ever reads like a triage verdict, the label is wrong.
 */
export const URGENCY_UI_LABELS = [
  'ei vaikuta ajoitukseen',
  'tavanomainen käynti',
  'selvitä tällä käynnillä',
  'nopea huomio',
  'aikakriittinen',
];

/** The label for the level a distribution puts first, or null when it has no usable level. */
export function leadingUrgencyLabel(probabilities) {
  const entries = Object.entries(probabilities ?? {})
    .map(([level, value]) => [Number.parseInt(level, 10), Number(value)])
    .filter(([level, value]) => Number.isInteger(level) && Number.isFinite(value));
  if (!entries.length) return null;
  const [level] = entries.reduce((best, row) => (row[1] > best[1] ? row : best));
  return URGENCY_UI_LABELS[level] ?? null;
}

// The first offered question the professional's typed prefix is the start of, or null. Prefix only, in server
// order: a completion the professional did not begin would put words in their mouth, and the cards exist so
// that an offered question is asked by a click, never by the field.
export function completionCandidate(prompts, typed) {
  if (!Array.isArray(prompts)) return null;
  const needle = String(typed ?? '').trim().toLocaleLowerCase('fi');
  if (!needle) return null;
  for (const prompt of prompts) {
    const text = prompt?.text;
    if (typeof text !== 'string') continue;
    const haystack = text.toLocaleLowerCase('fi');
    if (haystack.length > needle.length && haystack.startsWith(needle)) return text;
  }
  return null;
}

export function selectionReasonText(reason) {
  const fixed = {
    'context:person-basics': 'Henkilön perustiedot kuuluvat tähän tilannekuvaan.',
    'context:active-medication': 'Ajantasainen lääkelista kuuluu tähän tilannekuvaan.',
    'context:active-diagnosis': 'Aktiivinen diagnoosi kuuluu tähän tilannekuvaan.',
    'assessment:latest-relevant': 'Uusin tähän palvelutilanteeseen sopiva arviointi valittiin.',
  };
  if (fixed[reason]) return fixed[reason];
  if (typeof reason === 'string' && reason.startsWith('fold:same-day-home-care')) {
    return 'Saman päivän kotihoidon käynnit on koottu tähän kirjaukseen.';
  }
  if (typeof reason === 'string' && reason.startsWith('keyword:')) {
    return 'Kirjauksen aihe sopii palvelutilanteeseen.';
  }
  if (typeof reason === 'string' && reason.startsWith('decision:') && reason.endsWith(':relevant')) {
    return 'Decision-1 arvioi kirjauksen olennaiseksi tälle palvelutilanteelle.';
  }
  return 'Kirjaus valittiin sääntöpohjaisesti.';
}

// ---- What Decision-1 lifted for this visit ----
//
// Decision-1 answers per record with a verdict, a time-sensitivity read and an urgency distribution; it writes no
// prose. What is worth lifting into the professional's "Nostettu tälle käynnille" block is (1) a record it ADDED
// beyond the rule-based baseline, (2) the record it puts first by expected urgency (the day view's lead, same
// formula), and (3) records it called relevant AND current, by its own confidence. The lead is chosen ONLY among
// notes whose verdict is `relevant`: a record the model called irrelevant can carry the highest expected urgency,
// and lifting it would make the block, the record's own tag and its closed reasoning disagree about the same
// record. Everything is computed over the rendered sources only, so a withheld record can never be named; the
// limit keeps the block a list, not a dump. Each item carries the note's verdict so the UI writes it in words
// and never derives a second reading of its own.
export const DECISION_HIGHLIGHT_LIMIT = 4;

function expectedUrgency(note) {
  const distribution = note?.gate_probabilities?.urgency ?? {};
  let expected = 0;
  let weight = 0;
  for (const [level, probability] of Object.entries(distribution)) {
    const l = Number.parseInt(level, 10);
    const p = Number(probability);
    if (!Number.isInteger(l) || !Number.isFinite(p)) continue;
    expected += l * p;
    weight += p;
  }
  return weight > 0 ? expected : null;
}

/** The urgency label for a note's expected level (the day view's lead formula), or null without a usable distribution. */
export function expectedUrgencyLabel(note) {
  const expected = expectedUrgency(note);
  if (expected === null) return null;
  const level = Math.max(0, Math.min(URGENCY_UI_LABELS.length - 1, Math.round(expected)));
  return URGENCY_UI_LABELS[level];
}

/** True when Decision-1 answered for this view (`complete` or `partial`); every other status is "no assessment". */
export function decisionAnswered(selection) {
  return Boolean(selection) && ['complete', 'partial'].includes(selection.decision_status);
}

export function decisionHighlights(presentation) {
  const selection = presentation?.selection;
  const sources = presentation?.sources ?? [];
  if (!decisionAnswered(selection)) return [];
  const byId = new Map(sources.map(source => [source.id, source]));
  const notes = (selection.decision_notes ?? []).filter(note => byId.has(note.record_id));
  const noteById = new Map(notes.map(note => [note.record_id, note]));
  const out = [];
  const taken = new Set();
  const push = (id, kind, text) => {
    if (!byId.has(id) || taken.has(id) || out.length >= DECISION_HIGHLIGHT_LIMIT) return;
    taken.add(id);
    const note = noteById.get(id) ?? null;
    out.push({
      id, kind, title: byId.get(id).title, date: byId.get(id).date || null, text,
      status: note?.status ?? null,
      timeSensitivity: note?.time_sensitivity ?? null,
      urgencyLabel: note ? (leadingUrgencyLabel(note.gate_probabilities?.urgency) ?? null) : null,
    });
  };
  for (const id of selection.additional_record_ids ?? []) {
    push(id, 'added', 'Decision-1 nosti esiin kirjauksen, jota sääntöpohjainen valinta ei valinnut');
  }
  const relevantNotes = notes.filter(note => note.status === 'relevant');
  let lead = null;
  for (const note of relevantNotes) {
    const expected = expectedUrgency(note);
    if (expected === null) continue;
    if (!lead || expected > lead.expected) lead = { note, expected };
  }
  if (lead) push(lead.note.record_id, 'lead', `Decision-1 asetti ensimmäiseksi: ${expectedUrgencyLabel(lead.note)}`);
  const current = relevantNotes
    .filter(note => note.time_sensitivity === 'current')
    .sort((a, b) => Number(b.relevance_confidence ?? 0) - Number(a.relevance_confidence ?? 0));
  for (const note of current) push(note.record_id, 'relevant', 'Decision-1: olennainen tälle käynnille ja ajankohtainen');
  return out;
}

/** The ids `decisionHighlights` lifts, so a record row and the block read the same list and cannot disagree. */
export function liftedRecordIds(presentation) {
  return new Set(decisionHighlights(presentation).map(item => item.id));
}

// The triage signal's Finnish word per `situation_type` in `backend/app/tables/situation.json`. The raw table value
// never reaches the screen: an unknown value is rendered as itself, capitalised, rather than invented a meaning.
const SITUATION_WORD = {
  ei_arvioitavissa: 'Ei arvioitavissa',
  akuutti: 'Akuutti',
  mielenterveys: 'Mielenterveys',
  sosiaalipalvelut: 'Sosiaalipalvelut',
  krooninen: 'Pitkäaikaissairaus',
  ikääntyminen: 'Ikääntyminen',
  yleinen: 'Yleinen',
};
export function situationWord(value) {
  if (typeof value !== 'string' || !value.trim()) return SITUATION_WORD.ei_arvioitavissa;
  if (SITUATION_WORD[value]) return SITUATION_WORD[value];
  const text = value.trim();
  return text.charAt(0).toLocaleUpperCase('fi') + text.slice(1);
}

export function decisionOutcomeText(status) {
  return ({
    relevant: 'Decision-1 arvioi kirjauksen olennaiseksi.',
    irrelevant: 'Decision-1 arvioi kirjauksen epäolennaiseksi — ei mallisyötteessä.',
    unclear: 'Decision-1 jäi epävarmaksi; kirjaus säilyi mallisyötteessä.',
    budget_exhausted: 'Ei arvioitu — Valinnan budjetti; kirjaus säilyi mallisyötteessä.',
    not_shortlisted: 'Ei arvioitu — ei mahtunut Valinnan esivalintalistalle; kirjaus säilyi mallisyötteessä.',
    too_long: 'Ei arvioitu — pituusraja; kirjaus säilyi mallisyötteessä.',
    unavailable: 'Ei arvioitu — Decision-1 ei ollut käytettävissä; kirjaus säilyi mallisyötteessä.',
    timeout: 'Valinnan arviointi keskeytyi aikarajan vuoksi; kirjaus säilyi mallisyötteessä.',
    invalid_response: 'Valinnan vastausta ei voitu käyttää; kirjaus säilyi mallisyötteessä.',
  })[status] ?? 'Valinnan arviointitila ei ole tunnistettu.';
}

export function feedbackScopeKey(encounterId, proposalId) {
  return JSON.stringify([encounterId, proposalId]);
}

// F2: a chat decision is keyed by the turn as well as the suggestion, because the API restarts suggestion
// ids at `s0` on every turn (backend/app/chat.py). A decision recorded against turn A therefore cannot be
// read back on turn B's `s0`, and a response from turn A that lands after turn B started writes under A's
// own key instead of turning B's `s0` into "Tallennettu palveluun" and locking it out of review. The same
// key is what the loaded decisions use, so a reload cannot re-key them either.
export function chatDecisionKey(turnId, suggestionId) {
  return `${turnId}:${suggestionId}`;
}

// When a decision was recorded, rendered in the same Finnish date/time role the record tables use.
export function formatDecisionTime(value) {
  if (!value) return '';
  const parsed = new Date(value);
  return Number.isNaN(parsed.valueOf()) ? value : new Intl.DateTimeFormat('fi-FI', {
    dateStyle: 'medium', timeStyle: 'short', timeZone: 'Europe/Helsinki',
  }).format(parsed);
}

// The server's own discriminator for the one 409 that means "you already decided this". The other 409s on
// the two write routes — `proposal is no longer available`, a stale digest, a conflicting request id — are
// real failures and must not be reported as an earlier decision, so the outcome is read from the body's
// machine-readable `reason`, with the detail wording kept as a fallback for an older server.
export function decisionAlreadyRecorded(status, payload) {
  if (status !== 409) return false;
  if (payload?.reason === 'already_decided') return true;
  return typeof payload?.detail === 'string' && payload.detail.includes('already has a decision');
}

// A loaded decision is rendered through the same `saved` state a live save reaches, so a reload looks
// exactly like the click that recorded it. An entry already in `current` wins: a write in flight, or one
// left ambiguous on this visit, is newer than the load and must not be overwritten by it.
export function seedProposalReviews(current, payload) {
  const seeded = { ...(current ?? {}) };
  for (const decision of payload?.proposals ?? []) {
    if (!decision?.proposal_id || seeded[decision.proposal_id]) continue;
    seeded[decision.proposal_id] = {
      status: 'saved', action: decision.action, recordedAt: decision.decided_at,
    };
  }
  return seeded;
}

// Chat decisions seeded from the same read, keyed exactly as a live decision is. Nothing is invented for a
// turn this caller did not ask for: the route already scoped the rows to the caller and the encounter, and
// the key keeps a seeded entry from leaking onto another turn's `s0`.
export function seedChatDecisions(payload) {
  const seeded = {};
  for (const decision of payload?.chat_suggestions ?? []) {
    if (!decision?.turn_id || !decision?.suggestion_id) continue;
    seeded[chatDecisionKey(decision.turn_id, decision.suggestion_id)] = {
      status: 'saved', action: decision.action, recordedAt: decision.decided_at,
    };
  }
  return seeded;
}

// A pre-filled answer is keyed by the form and the question, which is how the write route names it; the key
// is the same for a live save and a loaded verdict, so a reload renders what the click did.
export function prefillReviewKey(formId, questionId) {
  return `${formId}:${questionId}`;
}

export function prefillReviewScopeKey(encounterId, formId, questionId) {
  return JSON.stringify([encounterId, formId, questionId]);
}

// The caller's own verdicts from `GET …/prefill/reviews`, seeded exactly as `seedProposalReviews` seeds the
// proposal rail: the route returns them in decision order, so the last verdict on an answer is the one that
// stands. An entry already in `current` (a write in flight, or one left ambiguous) wins over the load.
export function seedPrefillReviews(current, payload) {
  const seeded = { ...(current ?? {}) };
  const latest = {};
  for (const review of payload?.reviews ?? []) {
    if (!review?.form_id || !review?.question_id || !review?.verdict) continue;
    latest[prefillReviewKey(review.form_id, review.question_id)] = review;
  }
  for (const [key, review] of Object.entries(latest)) {
    if (seeded[key]) continue;
    seeded[key] = { status: 'saved', verdict: review.verdict, editedText: review.edited_text ?? null,
      reviewId: review.review_id, recordedAt: review.decided_at ?? null };
  }
  return seeded;
}

// The reply states what it is made of, so a reader cannot take a selection for the whole record set. The
// table's order is the order the phrases are joined in. A class the table does not cover returns null rather
// than a partial count, because a count that leaves a statement out is a false statement about the answer.
const COMPOSITION_PHRASES = [
  ['source_fact', '1 lähdekirjaus', n => `${n} lähdekirjausta`],
  ['rule_output', '1 sääntöpohjainen arvio', n => `${n} sääntöpohjaista arviota`],
  ['external_evidence', '1 ulkoinen lähde', n => `${n} ulkoista lähdettä`],
  ['model_summary', '1 tekoälyn muotoilema lause', n => `${n} tekoälyn muotoilemaa lausetta`],
  ['knowledge_advice', '1 tietopohjainen ehdotus', n => `${n} tietopohjaista ehdotusta`],
];

// Exported so the legend and the tests read the same four strings the tags render. It lives in this data module,
// not in the component file, so the component file exports only components.
export const CLASS_LABELS = {
  source_fact: "Lähde",
  rule_output: "Sääntöpohjainen arvio",
  external_evidence: "Ulkoinen lähde",
  model_summary: "Tekoälyn muotoilema",
  knowledge_advice: "Tietopohjainen ehdotus",
};

export function answerCompositionText(turn) {
  if (turn?.outcome !== 'answer') return null;
  const suggestions = turn.suggestions ?? [];
  // The same lead the rail takes: the first `answer_lead` only. A second lead is rendered as a finding, so it is
  // counted here too, and as an unknown class it fails closed below.
  const lead = suggestions.find(item => item.statement_class === 'answer_lead') ?? null;
  const findings = suggestions.filter(item => item !== lead);
  if (!findings.length) return null;
  const counts = new Map();
  for (const item of findings) {
    if (!COMPOSITION_PHRASES.some(([statementClass]) => statementClass === item.statement_class)) return null;
    // The idempotent-retry path returns the stored turn without statement text, so the rail renders blank rows.
    // A count over rows the reader cannot see would describe content that is not on screen.
    if (!item.text) return null;
    // Advice without a resolving ref is not rendered (`ChatRail`), so counting it would describe a row not on screen.
    if (item.statement_class === 'knowledge_advice' && !item.refs?.length) return null;
    counts.set(item.statement_class, (counts.get(item.statement_class) ?? 0) + 1);
  }
  const parts = COMPOSITION_PHRASES.filter(([statementClass]) => counts.has(statementClass))
    .map(([statementClass, one, many]) => (counts.get(statementClass) === 1 ? one : many(counts.get(statementClass))));
  const joined = parts.length === 1 ? parts[0] : `${parts.slice(0, -1).join(', ')} ja ${parts.at(-1)}`;
  // The second sentence is the one the turn can prove: the statements are question-matched, and a count of displayed
  // statements is not a count of the record set. A coverage claim (this is all, or a share, of the record set) is
  // not provable from the turn, so it is not stated. No total either: the count excludes the lead.
  return `Vastauksessa on ${joined}. Lauseet on valittu tämän kysymyksen perusteella, eikä niiden määrä kerro koko kirjausjoukon laajuudesta.`;
}

export function personNameForEncounter(encounter) {
  return encounter?.fixture_label || encounter?.person_name;
}

// Modes the picture route accepts; anything else is treated as normal rather than forwarded.
export const PICTURE_MODES = ['normal', 'decision-off', 'foundry-off'];

// The build the bundle was compiled from (Dockerfile frontend-build). Baked in at build time on purpose: a tab
// loaded before a deploy runs older JavaScript, so it must not ask the server which revision it is. Read with
// optional chaining because this module is also imported by plain Node (the stream test), where there is no
// `import.meta.env` at all.
const rawBuild = typeof import.meta.env?.VITE_BUILD_REVISION === 'string' ? import.meta.env.VITE_BUILD_REVISION.trim() : '';
export const BUILD_REVISION = rawBuild !== '' && rawBuild.toLowerCase() !== 'unknown' ? rawBuild : null;

// The status words the technical page prints for the selection and the narration, one per literal the API emits.
export function decisionStatus(status) {
  return ({ no_input: 'Ei syötettä', complete: 'Valmis', partial: 'Osittainen', disabled: 'Ei käytössä', budget_exhausted: 'Budjetti loppui', unavailable: 'Ei käytettävissä' })[status] ?? status ?? 'Ei tietoa';
}
export function generationStatus(status) {
  return ({ generated: 'tuotettu', unavailable: 'ei käytettävissä', failed: 'virhe', timed_out: 'aikaraja' })[status] ?? status ?? 'ei tietoa';
}

// Appearance modes. The mockup spelled the light/dark switch `mode` too, so `mode=dark` is read as the
// theme as well as `theme=dark`; the picture mode keeps its own meaning.
export const THEMES = ['light', 'dark'];

// A non-browser render (the frontend tests load App through Vite SSR) has no location, so it gets the
// defaults instead of a crash.
export function currentSearch() {
  return typeof window === 'undefined' ? '' : window.location.search;
}

// One builder for the address, so the encounter, the role lens, the presenter's mode and the theme cannot
// drift into four different URLs. `normal` is the absence of `mode`, not a value of it.
const ISO_DAY = /^\d{4}-\d{2}-\d{2}$/;

export function viewAddress({ encounterId, role, mode, theme, capture, day, page } = {}) {
  const params = new URLSearchParams();
  if (capture) params.set('capture', '1');
  if (role) params.set('role', role);
  if (typeof day === 'string' && ISO_DAY.test(day)) params.set('day', day);
  if (encounterId) params.set('encounter', encounterId);
  // The technical page is a destination of its own (`?page=technical`), reached from the rail or the briefing's
  // footer. It is the only page value; the briefing is the absence of the parameter, as `normal` is for `mode`.
  if (page === 'technical') params.set('page', 'technical');
  if (PICTURE_MODES.includes(mode) && mode !== 'normal') params.set('mode', mode);
  if (theme === 'dark') params.set('theme', 'dark');
  const query = params.toString();
  return query ? `?${query}` : '';
}

// The URL is a presentational selector over rows the API already returned, on the same footing as the
// picker. An encounter id the API did not return is NOT replaced by another row: it is a neutral selection
// (`encounterUnknown`), because substituting the first row showed another person's case under the address
// that was asked for. Reading the address once also makes a briefing state addressable for baseline capture.
//
// `role` is the same kind of selector over the catalogue's own `role` axis: it names the professional
// lens (Rooli) the worklist is scoped to. A role the returned rows do not carry resolves to '', so a
// crafted query cannot ask for a worklist the API did not offer. It selects a *view*; it grants nothing,
// because every opened encounter still runs `policy.cut` server-side. A role with no addressed encounter
// is that role's worklist (`worklistOnly`), not a case chosen on its behalf.
//
// `style` and `preset` are gone: ADR-0016 makes the Swiss 1 foundations library the single design
// authority, so there is one palette and only light/dark to choose between.
// `resolved` is a single row the caller fetched by ID because the bootstrapped page did not carry it. The
// catalogue page is a first slice of the caller's scope, not the whole of it, so an addressed encounter that
// fell outside that slice was being reported as absent while `GET /api/encounters/{id}` answered 200 for it.
// Passing the resolved row here keeps the decision in this pure function -- and keeps the refusal semantics
// intact: a row the server refused (out of scope, or no such encounter) is simply not passed, so the address
// stays unknown, exactly as an unknown id does.
export function requestedView(search, encounters = [], resolved = null) {
  const rows = resolved && resolved.encounter_id ? [resolved, ...encounters] : encounters;
  const params = new URLSearchParams(search ?? '');
  const wanted = params.get('encounter');
  const mode = params.get('mode');
  // `theme` wins; the mockup's `mode=dark` shorthand is accepted when no theme is given.
  const theme = [params.get('theme'), mode].find(value => THEMES.includes(value)) ?? 'light';
  // `potilas` is a policy comparison key with no product surface (concept boundary register:
  // "Citizen/end-user view | REJECTED for this product"), so it is not an admissible lens here either.
  const wantedRole = params.get('role');
  const role = wantedRole !== 'potilas' && rows.some(row => row.role === wantedRole) ? wantedRole : '';
  const addressed = typeof wanted === 'string' && wanted !== '';
  const known = addressed && rows.some(row => row.encounter_id === wanted);
  return {
    // An addressed encounter the catalogue does not carry is a NEUTRAL selection, never another person's
    // case. Only an address that names neither an encounter nor a role takes the first row as the default;
    // a named role is a worklist, so it selects no case on the professional's behalf.
    encounterId: known ? wanted : (addressed || role !== '' ? '' : (rows[0]?.encounter_id ?? '')),
    encounterAddressed: addressed,
    // The id the address named, so the caller can resolve it by ID before settling for "unknown".
    encounterWanted: addressed ? wanted : '',
    encounterUnknown: addressed && !known,
    worklistOnly: !addressed && role !== '',
    role,
    // The day the Työpäivä view shows; an address with no valid day shows today.
    day: ISO_DAY.test(params.get('day') ?? '') ? params.get('day') : '',
    mode: PICTURE_MODES.includes(mode) ? mode : 'normal',
    theme,
    capture: params.has('capture'),
    // `page=technical` is the one page a link can name; anything else is the briefing. It is a presentation
    // selector like `mode`: the technical page renders the same Picture the briefing holds and requests nothing.
    page: params.get('page') === 'technical' ? 'technical' : '',
  };
}

const feedbackBodyKey = body => JSON.stringify(body);

function copyPending(pending) {
  return pending
    ? { eventIds: new Map(pending.eventIds), ambiguousBody: pending.ambiguousBody, inFlightBody: pending.inFlightBody }
    : { eventIds: new Map(), ambiguousBody: null, inFlightBody: null };
}

function withPending(pendingByScope, scope, pending) {
  const next = new Map(pendingByScope);
  if (pending.eventIds.size) next.set(scope, pending);
  else next.delete(scope);
  return next;
}

function noEffect(pendingByScope) {
  return { pending: pendingByScope, effect: { type: "none" } };
}

export function reviewWriteReducer(pendingByScope, event) {
  const state = pendingByScope ?? new Map();
  switch (event.type) {
    case "submit": {
      const key = feedbackBodyKey(event.body);
      const current = state.get(event.scope);
      if (current?.inFlightBody === key) return noEffect(state);
      const unresolved = current?.ambiguousBody ?? current?.inFlightBody;
      if (unresolved) {
        let body;
        try {
          body = JSON.parse(unresolved);
        } catch {
          return noEffect(state);
        }
        const pending = copyPending(current);
        pending.ambiguousBody = unresolved;
        return {
          pending: withPending(state, event.scope, pending),
          effect: { type: "blocked", status: "ambiguous", body, eventId: current.eventIds.get(unresolved) },
        };
      }
      const pending = copyPending(current);
      const eventId = pending.eventIds.get(key) ?? event.candidateEventId;
      if (!eventId) return noEffect(state);
      pending.eventIds.set(key, eventId);
      pending.inFlightBody = key;
      return {
        pending: withPending(state, event.scope, pending),
        effect: { type: "post", status: "saving", scope: event.scope, body: event.body, bodyKey: key, eventId },
      };
    }
    case "retry": {
      const previous = event.previous;
      if (!previous || !["failed", "ambiguous"].includes(previous.status) || !previous.body) {
        return noEffect(state);
      }
      const key = feedbackBodyKey(previous.body);
      const current = state.get(event.scope);
      const eventId = current?.eventIds.get(key);
      if (!current || !eventId || current.inFlightBody || current.ambiguousBody !== key) return noEffect(state);
      const pending = copyPending(current);
      pending.inFlightBody = key;
      return {
        pending: withPending(state, event.scope, pending),
        effect: { type: "post", status: "saving", scope: event.scope, body: previous.body, bodyKey: key, eventId },
      };
    }
    case "success": {
      const current = state.get(event.scope);
      if (!current) return { pending: state, effect: { type: "success" } };
      const pending = copyPending(current);
      if (pending.inFlightBody === event.bodyKey) pending.inFlightBody = null;
      pending.eventIds.delete(event.bodyKey);
      if (pending.ambiguousBody === event.bodyKey) pending.ambiguousBody = null;
      return { pending: withPending(state, event.scope, pending), effect: { type: "success" } };
    }
    case "failure": {
      const ambiguous = event.status == null || event.status >= 500;
      // The third definite outcome. A 409 carrying `alreadyDecided` (from `decisionAlreadyRecorded`) is the
      // server telling the caller they already decided this: the rail says exactly that and the buttons stop
      // offering a second decision. Every other definite rejection — including the 409s that are not the
      // caller's own duplicate — stays `failed`, because "you already decided this" would be false for them.
      const status = ambiguous ? "ambiguous" : event.alreadyDecided ? "decided" : "failed";
      const current = state.get(event.scope);
      if (!current) return { pending: state, effect: { type: "failure", status } };
      const pending = copyPending(current);
      if (pending.inFlightBody === event.bodyKey) pending.inFlightBody = null;
      if (ambiguous) pending.ambiguousBody = event.bodyKey;
      else {
        pending.eventIds.delete(event.bodyKey);
        if (pending.ambiguousBody === event.bodyKey) pending.ambiguousBody = null;
      }
      return { pending: withPending(state, event.scope, pending), effect: { type: "failure", status } };
    }
    default:
      throw new TypeError(`unknown review-write event: ${event.type}`);
  }
}

export function feedbackContextIsCurrent(requestGeneration, currentGeneration) {
  return requestGeneration === currentGeneration;
}

const ROLE_LABELS = { laakari: 'Lääkäri', sairaanhoitaja: 'Sairaanhoitaja', sosiaalityontekija: 'Sosiaalityöntekijä' };
const recordId = record => record?.record_id ?? record?.recordId;
const DISPLAY_PAYLOAD_FIELDS = {
  perustieto: ['nimi', 'näyttötunniste', 'sukupuoli', 'syntymävuosi', 'äidinkieli', 'kotikunta', 'asumismuoto'],
  diagnoosi: ['nimi', 'icd10', 'tila'],
  lääkitys: ['lääke', 'annos', 'tila', 'puuttuneet_annokset_30d'],
  mittaus: ['paino_kg', 'pituus_cm', 'verenpaine', 'syke'],
  arviointi: ['instrumentti', 'versio'],
  tutkimustulos: ['tutkimus', 'arvo', 'yksikkö', 'tulkinta', 'viitearvo'],
  asiointitapahtuma: ['kanava', 'suunta', 'aihe', 'yhteenveto'],
  vapaateksti: ['otsikko', 'teksti', 'kirjaaja', 'käyntityyppi'],
  'päätös': ['päätös', 'voimassa'],
};
const SOURCE_FIELDS = {
  perustieto: [['sukupuoli', 'Sukupuoli'], ['syntymävuosi', 'Syntymävuosi'], ['äidinkieli', 'Äidinkieli'], ['kotikunta', 'Kotikunta'], ['asumismuoto', 'Asumismuoto']],
  diagnoosi: [['nimi', 'Diagnoosi'], ['icd10', 'ICD-10'], ['tila', 'Tila']],
  lääkitys: [['lääke', 'Lääke'], ['annos', 'Annos'], ['tila', 'Tila'], ['puuttuneet_annokset_30d', 'Puuttuneet annokset / 30 vrk']],
  vapaateksti: [['otsikko', 'Otsikko'], ['teksti', 'Kirjaus'], ['käyntityyppi', 'Käyntityyppi']],
  // 'Toimintakykymittari' is the THL Sote-sanastot national term for this instrument class; the
  // payload KEYS ('instrumentti', 'versio') are the ones mirrored in `delivery.RENDER_PAYLOAD_KEYS`
  // and checked for drift there — the label text itself has no backend mirror to keep in sync.
  arviointi: [['instrumentti', 'Toimintakykymittari'], ['versio', 'Versio']],
  // `verenpaine` and `syke` name the measurement rather than the value, and they carry no unit key: the
  // tile renders the source's own value with no unit rather than inventing one. Keep this entry on ONE
  // line: `test_delivery._frontend_source_fields` parses `SOURCE_FIELDS` line by line, so a wrapped entry
  // drops out of the drift check silently — which is how the two maps would diverge unnoticed.
  mittaus: [['paino_kg', 'Paino (kg)'], ['pituus_cm', 'Pituus (cm)'], ['verenpaine', 'Verenpaine'], ['syke', 'Syke'], ['lämpö_c', 'Lämpö (°C)'], ['spo2', 'SpO2 (%)']],
  tutkimustulos: [['tutkimus', 'Tutkimus'], ['arvo', 'Tulos'], ['yksikkö', 'Yksikkö'], ['viitearvo', 'Viitearvo'], ['tulkinta', 'Tulkinta']],
  asiointitapahtuma: [['kanava', 'Kanava'], ['suunta', 'Suunta'], ['aihe', 'Aihe'], ['yhteenveto', 'Yhteenveto']],
  'päätös': [['päätös', 'Päätös'], ['voimassa', 'Tila']],
};

function displayPayload(record) {
  return Object.fromEntries((DISPLAY_PAYLOAD_FIELDS[record?.record_type] ?? []).flatMap(key => {
    const value = record?.payload?.[key];
    return (typeof value === 'string' || (typeof value === 'number' && Number.isFinite(value))) ? [[key, value]] : [];
  }));
}

// The excerpt as structured rows, the same field labels `sourceExcerpt` joins: the source dialog renders them
// as "heading: content" sections instead of one dotted line.
export function sourceExcerptFields(record) {
  const fields = SOURCE_FIELDS[record?.record_type] ?? [];
  return fields.flatMap(([key, label]) => {
    const raw = record?.payload?.[key];
    // A decision's validity is the one payload value that reaches the reader as a raw token
    // (`toistaiseksi`, an ISO date); it is rendered in words here, nowhere else.
    const value = record?.record_type === 'päätös' && key === 'voimassa' ? validityText(raw) : raw;
    return (typeof value === 'string' || typeof value === 'number') && String(value).trim()
      ? [{ label, value: String(value) }]
      : [];
  });
}

function sourceExcerpt(record) {
  return sourceExcerptFields(record).map(({ label, value }) => `${label}: ${value}`).join(' · ');
}

// The vendor label printed under a record. `delivery` is the delivery stack's own source key — it names the
// pipeline that served the record, not a vendor, a system or a form — so a reader cannot act on it and it
// is omitted. Any other label is the record's own and renders as it came.
export function sourceLabel(record) {
  const source = typeof record?.source === 'string' ? record.source.trim() : '';
  return source && source !== 'delivery' ? source : '';
}

// A decision's `voimassa` value, in words: an ISO date as d.m.yyyy, the two fixed tokens capitalised, and
// anything else exactly as recorded (it is the data, and nothing here guesses what an unknown token means).
const VALIDITY_WORDS = { toistaiseksi: 'Toistaiseksi', määräaikainen: 'Määräaikainen' };
export function validityText(value) {
  if (typeof value !== 'string') return value;
  const trimmed = value.trim();
  if (/^\d{4}-\d{2}-\d{2}(?:[T ].*)?$/.test(trimmed)) {
    const parsed = new Date(trimmed.length === 10 ? `${trimmed}T00:00:00Z` : trimmed);
    if (!Number.isNaN(parsed.valueOf())) {
      return new Intl.DateTimeFormat('fi-FI', { dateStyle: 'medium', timeZone: 'UTC' }).format(parsed);
    }
  }
  return VALIDITY_WORDS[trimmed.toLowerCase()] ?? value;
}

// The lines of an excerpt, for rendering. A note's text is markdown in the source system (`## KÄYNNIN
// TIEDOT`, `# PÄÄTÖSLUONNOS`), and the marks are typesetting, not content: a heading line becomes a
// `heading` block carrying its words without the marks, every other non-empty line a `paragraph`. The
// field label the excerpt puts before a value (`Kirjaus: ## ...`) stays as its own paragraph before the
// heading. The words are never changed — `PÄÄTÖSLUONNOS` stays on screen, as a heading.
const EXCERPT_HEADING_LINE = /^(?:(.*?:)\s+)?#{1,6}\s+(.+?)\s*#*\s*$/;
export function excerptBlocks(text) {
  if (typeof text !== 'string') return [];
  const blocks = [];
  for (const raw of text.split(/\r?\n/)) {
    const line = raw.trim();
    if (!line) continue;
    const heading = EXCERPT_HEADING_LINE.exec(line);
    if (heading) {
      if (heading[1]) blocks.push({ kind: 'paragraph', text: heading[1] });
      blocks.push({ kind: 'heading', text: heading[2] });
    } else {
      blocks.push({ kind: 'paragraph', text: line });
    }
  }
  return blocks;
}

// The "Valitut kirjaukset" list, folded: records with the same title, the same headings and the same
// excerpt text (eight same-day home-care rows) are one entry whose records are listed newest first;
// every other record is an entry of its own. Entries keep their national-heading grouping and their
// order of first appearance. The fold is a rendering choice over rows the list already has — nothing is
// dropped, and every record keeps its anchor.
export function foldSelectedRecords(rows) {
  const groups = new Map();
  for (const row of rows ?? []) {
    const heading = row.heading?.national || null;
    const entries = groups.get(heading) ?? [];
    const foldKey = [row.title ?? '', row.heading?.local ?? '', row.excerpt ?? ''].join('\u0000');
    const entry = entries.find(candidate => candidate.foldKey === foldKey);
    if (entry) entry.records.push(row); else entries.push({ foldKey, key: row.id, records: [row] });
    groups.set(heading, entries);
  }
  const byDateDesc = (a, b) => String(b.date ?? '').localeCompare(String(a.date ?? ''));
  return [...groups].map(([heading, entries]) => ({
    heading,
    entries: entries.map(({ key, records }) => ({ key, records: [...records].sort(byDateDesc) })),
  }));
}

// The record type in the professional's words, for a source reference's label and a source dialog's title.
// `vapaateksti` is "Kirjaus" — the same word the record card uses for a free-text entry.
export const RECORD_TYPE_LABELS = {
  diagnoosi: 'Diagnoosi', lääkitys: 'Lääkitys', vapaateksti: 'Kirjaus', arviointi: 'Arviointi', mittaus: 'Mittaus',
  tutkimustulos: 'Tutkimustulos', asiointitapahtuma: 'Yhteydenotto', 'päätös': 'Päätös', perustieto: 'Perustieto',
};
export function recordTypeLabel(type) {
  return RECORD_TYPE_LABELS[type] ?? 'Kirjaus';
}

// ISO dates inside generated prose (2024-07-20) read as the page writes them (20.7.2024). Only a full
// yyyy-mm-dd with a real month and day, bounded by non-digits, is rewritten; every other digit run (a dose,
// a code, a blood-pressure reading, a year on its own) is left exactly as the verifier accepted it.
export function finnishDatesInText(text) {
  if (typeof text !== 'string') return text;
  return text.replace(/(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)/g, (match, year, month, day) => {
    const m = Number(month);
    const d = Number(day);
    return m >= 1 && m <= 12 && d >= 1 && d <= 31 ? `${d}.${m}.${year}` : match;
  });
}

// Read-only adapter for the existing Picture wire object; it never decides visibility.
// The seven record cards the briefing can draw, by id. Every purpose's order must contain all of them.
export const BRIEFING_SECTION_IDS = ['assessments', 'measurements', 'labs', 'status', 'medications', 'records', 'forms'];

const BRIEFING_ORDERS = {
  medication_follow_up: ['medications', 'measurements', 'labs', 'assessments', 'status', 'records', 'forms'],
  functioning_service_needs: ['assessments', 'records', 'measurements', 'medications', 'labs', 'status', 'forms'],
  mental_health_assessment: ['assessments', 'measurements', 'records', 'medications', 'labs', 'status', 'forms'],
  child_health_checkup: ['measurements', 'assessments', 'records', 'labs', 'medications', 'status', 'forms'],
};
const DEFAULT_BRIEFING_ORDER = ['measurements', 'labs', 'assessments', 'status', 'medications', 'records', 'forms'];

// The order the existing record cards are READ in, keyed by the encounter's service purpose.
//
// This is a PRESENTATION rule over data whose content the server already decided: `policy.cut` has already
// fixed which records are permitted, and selection has already fixed which permitted records are relevant.
// Ordering here can therefore neither add, remove nor re-permit a record — every card still renders on every
// purpose. It exists because relevance is purpose-bound: a medication follow-up must lead with the
// medication list, and a functioning/service-needs assessment must lead with the structured assessments.
export function briefingOrder(purpose) {
  return Object.hasOwn(BRIEFING_ORDERS, purpose) ? [...BRIEFING_ORDERS[purpose]] : [...DEFAULT_BRIEFING_ORDER];
}

export function picturePresentation(picture, { personName, roleLabel, purposeLabel, exampleLabel, identityAvailable } = {}) {
  const selection = picture?.selection ?? null;
  // Per-record provenance of the risk verdict. The map is keyed by record id and its values are
  // ready-to-read Finnish sentences from the rule that fired, so nothing is recomputed here —
  // the same rule the app already obeys for `selectionReasons` and for risk tone.
  const riskReasonsByRecord = picture?.risk_reasons_by_record ?? {};
  const selectedSourceIds = selection
    ? unique([...(selection.baseline_record_ids ?? []), ...(selection.additional_record_ids ?? []), ...(selection.emphasized_record_ids ?? [])])
    : [];
  const safeSelection = selection ? {
    ...selection,
    decision_notes: (selection.decision_notes ?? []).filter(note => selectedSourceIds.includes(recordId(note))),
  } : null;
  const decisionNotesById = new Map((safeSelection?.decision_notes ?? []).map(note => [recordId(note), note]));
  const recordViews = [
    ...(picture?.records?.priority ?? []),
    ...(picture?.records?.accessible_other ?? []),
    ...(picture?.citations ?? []),
  ];
  const seen = new Set();
  const sources = recordViews.filter(record => {
    const id = recordId(record);
    if (!id || seen.has(id) || (selection && !selectedSourceIds.includes(id))) return false;
    seen.add(id);
    return true;
  }).map(record => {
    const id = recordId(record);
    const reasons = record.selection_reasons ?? [];
    let title = record.topic || record.record_type || id;
    if (record.record_type === 'vapaateksti') title = record.payload?.otsikko || record.topic || 'Kirjaus';
    else if (record.record_type === 'lääkitys') title = record.payload?.lääke || record.topic || 'Lääkitys';
    else if (record.record_type === 'arviointi') title = record.payload?.instrumentti || record.topic || 'Arviointi';
    // A visit or contact is titled by what it was about, not by its topic word: "hoito" names nothing.
    else if (record.record_type === 'asiointitapahtuma') title = record.payload?.aihe || record.topic || 'Yhteydenotto';
    // A diagnosis is titled by its own name or code and a result by its own test, never by the topic word.
    else if (record.record_type === 'diagnoosi') title = record.payload?.nimi || record.payload?.icd10 || record.topic || 'Diagnoosi';
    else if (record.record_type === 'tutkimustulos') title = record.payload?.tutkimus || record.topic || 'Tutkimustulos';
    else if (record.record_type === 'mittaus') title = record.payload?.mittaus || record.payload?.suure || record.topic || 'Mittaus';
    return {
      id,
      type: record.record_type,
      payload: displayPayload(record),
      title,
      excerpt: sourceExcerpt({ ...record, payload: displayPayload(record) }),
      excerptFields: sourceExcerptFields({ ...record, payload: displayPayload(record) }),
      typeLabel: recordTypeLabel(record.record_type),
      source: record.source || '',
      date: record.recorded_at || '',
      topic: record.topic || '',
      temporalStatus: record.temporal_status || 'unknown',
      selectionReasonTexts: reasons.map(selectionReasonText),
      riskReasonTexts: riskReasonsByRecord[id] ?? [],
      decisionOutcome: decisionNotesById.get(id) ?? null,
      heading: record.heading ?? null,
      käyntiaika: record.käyntiaika ?? null,
      vastaanottajat: record.vastaanottajat ?? null,
      erikoisala: record.erikoisala ?? null,
      suorituspaikka: record.suorituspaikka ?? null,
      state: record.state ?? null,
      authoredByRole: record.authored_by_role ?? null,
      responsibleClinician: record.responsible_clinician ?? null,
      responsibleApproverLabel: ROLE_LABELS[record.responsible_approver] ?? null,
      encounterId: record.encounter_id ?? null,
      caseId: record.case_id ?? null,
      form: record.form ?? null,
      conceptKey: record.concept_key ?? null,
    };
  }).sort((a, b) => Number(Boolean(b.excerpt)) - Number(Boolean(a.excerpt)));
  // Which rows Decision-1 lifted, from the same helper the "Nostettu tälle käynnille" block renders, so a row's
  // "Ehdotus" tag and the block's list are one computation and cannot disagree.
  const liftedIds = liftedRecordIds({ selection: safeSelection, sources });
  for (const source of sources) source.lifted = liftedIds.has(source.id);
  const visibleIds = new Set(sources.map(source => source.id));
  const duplicateFacts = (picture?.duplicate_facts ?? []).map(group => ({
    recordIds: (group.record_ids ?? []).filter(id => visibleIds.has(id)),
  })).filter(group => group.recordIds.length > 1);
  const formCatalogue = (picture?.form_catalogue ?? []).map(entry => ({
    ...entry,
    record_ids: (entry.record_ids ?? []).filter(id => visibleIds.has(id)),
  }));
  const narrativeAccepted = picture?.narration_acceptance_status === 'accepted'
    && picture?.narration_support_status === 'supported'
    && typeof picture?.narrative === 'string'
    && picture.narrative.trim().length > 0;
  const citedSourceIds = narrativeAccepted ? unique((picture.citations ?? []).map(recordId).filter(Boolean)) : [];
  // The accepted summary sentence by sentence, each with the record ids the verifier affirmed for it, cut to the
  // ids that render. A cache row written before `narrative_sentences` existed carries only the flat narrative:
  // it renders as one sentence whose sources are the record-level citations, never as nothing.
  const wireSentences = Array.isArray(picture?.narrative_sentences) ? picture.narrative_sentences : [];
  const summarySentences = !narrativeAccepted ? []
    : wireSentences.length > 0
      ? wireSentences
        .filter(row => typeof row?.text === 'string' && row.text.trim())
        .map((row, index) => ({
          id: row.sentence_id || `s${index + 1}`,
          text: finnishDatesInText(row.text.trim()),
          recordIds: unique((row.record_ids ?? []).filter(id => visibleIds.has(id))),
        }))
      : [{ id: 'narrative', text: finnishDatesInText(picture.narrative.trim()),
        recordIds: citedSourceIds.filter(id => visibleIds.has(id)) }];
  return {
    kind: 'runtime',
    exampleLabel,
    identityAvailable,
    encounterId: picture?.encounter_id ?? null,
    personName: personName || picture?.identity_header || 'Henkilö',
    roleLabel: roleLabel || ROLE_LABELS[picture?.role] || picture?.role || '',
    situation: picture?.situation ?? null,
    risk: picture?.risk ?? null,
    actions: picture?.actions ?? [],
    trace: picture?.trace ?? [],
    pipelineTraceId: picture?.pipeline_trace_id ?? null,
    pipelineTrace: picture?.pipeline_trace ?? [],
    grounded: picture?.grounded ?? false,
    grounding: picture?.grounding ?? null,
    purposeLabel: purposeLabel || '',
    // The wire's purpose code, passed through unchanged. It keys `briefingOrder`; the label above is the display.
    purpose: picture?.purpose ?? null,
    // The demo's declared purpose of use, served by the backend (ADR-0001). It is not a legal
    // classification: it renders as one sentence in the run-specification disclosure, and absence
    // renders nothing.
    intendedUse: picture?.intended_use ?? null,
    summary: narrativeAccepted ? picture.narrative : null,
    summarySentences,
    selectedSourceIds: selection ? selectedSourceIds : sources.map(source => source.id),
    // Every record id an anchor can exist for, so a `#source-<id>` link is suppressed where its target
    // does not render. `conflicts`, `assessments` and `decision_notes` all name source ids that the
    // visible-source filter may have dropped (a withheld or unselected record renders nothing), so a link
    // built from the id alone can point at an element that does not exist.
    targetRecordIds: [...visibleIds],
    citedSourceIds,
    sources,
    duplicateFacts,
    formCatalogue,
    selection: safeSelection,
    assessments: picture?.assessments ?? [],
    conflicts: picture?.conflicts ?? [],
    proposals: picture?.proposals ?? [],
    // The pre-filled esitiedot (`app/prefill.py`): the county's own heading structure and the official forms.
    esitiedot: picture?.esitiedot ?? [],
    prefilledForms: picture?.prefilled_forms ?? [],
    narration: {
      generationStatus: picture?.narration_generation_status ?? 'unavailable',
      inputRecordIds: picture?.narration_generation_status === 'generated' ? (picture.narration_input_record_ids ?? selectedSourceIds) : [],
      citedRecordIds: citedSourceIds,
      generationDeployment: picture?.narration_generation_deployment ?? null,
      generationPath: picture?.narration_generation_path ?? null,
      generationModel: picture?.narration_generation_model ?? null,
      generationElapsedMs: picture?.narration_generation_elapsed_ms ?? null,
      generationInputTokens: picture?.narration_generation_input_tokens ?? null,
      generationOutputTokens: picture?.narration_generation_output_tokens ?? null,
      supportStatus: picture?.narration_support_status ?? 'not_run',
      supportDeployment: picture?.narration_support_deployment ?? null,
      supportModel: picture?.narration_support_model ?? null,
      supportElapsedMs: picture?.narration_support_elapsed_ms ?? null,
      supportInputTokens: picture?.narration_support_input_tokens ?? null,
      supportOutputTokens: picture?.narration_support_output_tokens ?? null,
      acceptanceStatus: picture?.narration_acceptance_status ?? 'rejected',
      elapsedMs: picture?.narration_elapsed_ms ?? null,
      rejectionReason: picture?.narration_rejection_reason ?? null,
    },
  };
}

/**
 * Read a server-sent event stream to its final `turn` event and return that event's payload, calling `onEvent`
 * for every event on the way (progress, tool, sentence). An `error` event is returned as `{ error }` so the
 * caller reports it the way it reports a failed response. Raw model text never travels on this stream: the
 * server emits only tool names and sentences Decision-1 already verified.
 */
export async function readTurnStream(body, onEvent) {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let final = null;
  const handle = block => {
    const lines = block.split("\n");
    const name = lines.find(line => line.startsWith("event: "))?.slice(7).trim();
    const raw = lines.filter(line => line.startsWith("data: ")).map(line => line.slice(6)).join("\n");
    if (!name || !raw) return undefined;
    let data;
    try { data = JSON.parse(raw); } catch { return undefined; }
    if (name === "turn") final = data;
    else if (name === "error") final = { error: data };
    else onEvent?.({ name, data });
    return name;
  };
  // A short answer arrives in one chunk: its sentences and the final `turn` are parsed in the same tick, so
  // React never painted the live sentence list before the turn replaced it. After each verified sentence the
  // reader yields to the event loop, so the sentence is on screen before the next block is parsed. The
  // contract is unchanged: the same events, in the same order, and nothing is dispatched that was not sent.
  const paint = () => new Promise(resolve => setTimeout(resolve, 0));
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let separator = buffer.indexOf("\n\n");
    while (separator >= 0) {
      const handled = handle(buffer.slice(0, separator));
      buffer = buffer.slice(separator + 2);
      separator = buffer.indexOf("\n\n");
      if (handled === "sentence") await paint();
    }
  }
  buffer += decoder.decode();
  if (buffer.trim()) handle(buffer);
  return final ?? {};
}

// ---- Table folds (night plan N3) ----
//
// Every helper here works on the rows a table already renders: the selected, permitted sources the Picture
// handed over. Folding changes how many rows are drawn, never which records are in the set, so every count
// a table prints from these is a count of rendered or folded rows and never of anything withheld.

// A record's recorded_at as a sortable number; a missing or unparseable date sorts after every real one.
function recordTime(row) {
  const parsed = Date.parse(row?.date ?? '');
  return Number.isNaN(parsed) ? Number.NEGATIVE_INFINITY : parsed;
}

// The fold key of a diagnosis or decision row: one row per (ICD-10 code, status). A diagnosis without a
// code folds on its name; a decision on its text and validity. A row with none of those is its own group.
export function statusRowKey(row) {
  const payload = row?.payload ?? {};
  const subject = row?.type === 'päätös' ? payload.päätös : (payload.icd10 || payload.nimi);
  const status = row?.type === 'päätös' ? payload.voimassa : payload.tila;
  return subject ? `${row.type}|${subject}|${status ?? ''}` : `id|${row?.id}`;
}

// Folds the diagnosis and decision rows into one group per key. The group keeps its first-appearance
// position (the backend's order); `newest` is the latest record of the group, `folded` the rest (newest
// first), and `count` the number of records the rendered row stands for.
export function foldStatusRows(rows) {
  const groups = new Map();
  for (const row of rows ?? []) {
    const key = statusRowKey(row);
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(row);
  }
  return [...groups.entries()].map(([key, records]) => {
    const sorted = [...records].sort((a, b) => recordTime(b) - recordTime(a));
    return { key, newest: sorted[0], folded: sorted.slice(1), count: sorted.length };
  });
}

// The count line of the folded status table: rendered rows by kind, then the records they stand for.
export function statusCountText(groups) {
  const diagnoses = groups.filter(group => group.newest.type === 'diagnoosi').length;
  const decisions = groups.filter(group => group.newest.type === 'päätös').length;
  const records = groups.reduce((sum, group) => sum + group.count, 0);
  return [diagnoses > 0 && `${diagnoses} diagnoosia`, decisions > 0 && `${decisions} päätöstä`, `${records} kirjausta`]
    .filter(Boolean).join(' · ');
}

// The corpus writes a medication in use as `aktiivinen` and an ended one as `päättynyt` (a diagnosis uses
// `ei_aktiivinen`). Only a status that says the medication has ended is folded away; a missing or unknown
// status keeps the row in view, because hiding is the stronger claim.
export const MEDICATION_ACTIVE_STATUS = 'aktiivinen';
export const MEDICATION_HISTORICAL_STATUSES = new Set(['päättynyt', 'ei_aktiivinen', 'lopetettu', 'keskeytetty']);

export function medicationIsHistorical(row) {
  const status = row?.payload?.tila;
  return typeof status === 'string' && MEDICATION_HISTORICAL_STATUSES.has(status.trim().toLowerCase());
}

// Current medications first, in their original order; ended ones after, also in their original order.
export function splitMedicationRows(rows) {
  const active = [];
  const historical = [];
  for (const row of rows ?? []) (medicationIsHistorical(row) ? historical : active).push(row);
  return { active, historical };
}

// The count line of the medications card. `active` and `historical` are the substances the card draws as
// rows (one per substance, see `groupMedicationRows`), so the numbers are of substances, said as such; the
// records those rows stand for are counted separately when the caller passes them.
export function medicationCountText({ active, historical, records }) {
  const substances = active.length + historical.length;
  return [active.length > 0 && `${active.length} ${substances === 1 ? 'lääke' : 'lääkettä'} käytössä`,
    historical.length > 0 && `${historical.length} ${active.length > 0 ? 'aiempaa' : (substances === 1 ? 'aiempi lääke' : 'aiempaa lääkettä')}`,
    typeof records === 'number' && `${records} ${records === 1 ? 'kirjaus' : 'kirjausta'}`]
    .filter(Boolean).join(' · ');
}

// Plain-Finnish names for the laboratory abbreviations the corpus writes (the Veil import uses P-Krea,
// fP-Gluk, B-HbA1c and P-TSH; the rest are the common primary-care panel). The abbreviation is the source
// value and stays visible beside the label; an abbreviation this map does not know renders unchanged.
export const LAB_LABELS = {
  'B-HbA1c': 'Pitkäaikaissokeri',
  'fP-Gluk': 'Paastoverensokeri',
  'P-K': 'Kalium',
  'P-Na': 'Natrium',
  'P-Krea': 'Kreatiniini',
  'fP-Kol': 'Kokonaiskolesteroli',
  'fP-LDL': 'LDL-kolesteroli',
  'fP-HDL': 'HDL-kolesteroli',
  'fP-Trigly': 'Triglyseridit',
  'B-Hb': 'Hemoglobiini',
  'P-TSH': 'Tyreotropiini (TSH)',
  'P-CRP': 'CRP-tulehdusarvo',
  'B-Leuk': 'Leukosyytit',
  'B-Trom': 'Trombosyytit',
  'P-ALAT': 'ALAT-maksa-arvo',
  'U-Alb': 'Virtsan albumiini',
  'Pt-GFRe': 'Munuaisten suodatusnopeus (eGFR)',
};

export function labLabel(abbreviation) {
  if (typeof abbreviation !== 'string') return abbreviation;
  const trimmed = abbreviation.trim();
  const label = LAB_LABELS[trimmed];
  return label ? `${label} (${trimmed})` : abbreviation;
}

// ---- Grouped records (W5): one row per substance, per test, per (code, status) group ----

// A corpus status word as the professional reads it. The corpus writes `aktiivinen`, `päättynyt`,
// `lopetettu`, `keskeytetty`, `ei_aktiivinen` and `sairastettu`; a word this map does not know renders with
// its underscores as spaces and a capital first letter, never raw. An absent status is said in words.
const RECORD_STATUS_WORDS = {
  aktiivinen: 'Aktiivinen', päättynyt: 'Päättynyt', lopetettu: 'Lopetettu', keskeytetty: 'Keskeytetty',
  ei_aktiivinen: 'Ei aktiivinen', sairastettu: 'Sairastettu',
};
export function recordStatusText(value) {
  if (typeof value !== 'string' || !value.trim()) return 'Tila puuttuu';
  const trimmed = value.trim();
  const known = RECORD_STATUS_WORDS[trimmed.toLowerCase()];
  if (known) return known;
  const spaced = trimmed.replace(/_/g, ' ');
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}
export const medicationStatusText = recordStatusText;

// Medications, one group per substance (`lääke`), each group's records newest first. A substance with any
// record in use is current, and its `since` is the earliest date of those records; a substance whose records
// have all ended is historical, and its `ended` is the latest date. Groups keep their first-appearance
// order; current substances come first.
export function groupMedicationRows(rows) {
  const groups = new Map();
  for (const row of rows ?? []) {
    const name = typeof row?.payload?.lääke === 'string' && row.payload.lääke.trim() ? row.payload.lääke.trim() : null;
    const key = name ? `lääke|${name.toLowerCase()}` : `id|${row?.id}`;
    if (!groups.has(key)) groups.set(key, { key, name: name ?? 'Nimetön lääkitys', records: [] });
    groups.get(key).records.push(row);
  }
  const all = [...groups.values()].map(group => {
    const records = [...group.records].sort((a, b) => recordTime(b) - recordTime(a));
    const current = records.filter(row => !medicationIsHistorical(row));
    const active = current.length > 0;
    const dated = list => list.filter(row => Number.isFinite(recordTime(row)));
    const since = active ? dated(current).at(-1)?.date ?? null : null;
    const ended = active ? null : dated(records)[0]?.date ?? null;
    return { ...group, records, newest: records[0], first: dated(records).at(-1) ?? records.at(-1), count: records.length, active, since, ended };
  });
  return { active: all.filter(group => group.active), historical: all.filter(group => !group.active) };
}

// Laboratory results, one group per test abbreviation, each group's results newest first, in first-appearance order.
export function groupLabRows(rows) {
  const groups = new Map();
  for (const row of rows ?? []) {
    const abbreviation = typeof row?.payload?.tutkimus === 'string' && row.payload.tutkimus.trim() ? row.payload.tutkimus.trim() : null;
    const key = abbreviation ? `tutkimus|${abbreviation}` : `id|${row?.id}`;
    if (!groups.has(key)) groups.set(key, { key, abbreviation, records: [] });
    groups.get(key).records.push(row);
  }
  return [...groups.values()].map(group => {
    const records = [...group.records].sort((a, b) => recordTime(b) - recordTime(a));
    const dated = records.filter(row => Number.isFinite(recordTime(row)));
    return { ...group, records, newest: records[0], first: dated.at(-1) ?? records.at(-1), count: records.length };
  });
}

// The plain-Finnish name of a test on its own, for a row whose code slot already shows the abbreviation.
// An abbreviation the map does not know is its own name, and the row then carries no separate code.
export function labName(abbreviation) {
  if (typeof abbreviation !== 'string') return 'Nimetön tutkimus';
  const trimmed = abbreviation.trim();
  return LAB_LABELS[trimmed] ?? (trimmed || 'Nimetön tutkimus');
}
