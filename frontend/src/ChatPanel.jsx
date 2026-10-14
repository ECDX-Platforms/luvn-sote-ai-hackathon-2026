import { Fragment, useEffect, useRef, useState } from "react";
import { Button, Field, Input, Link, Tab, TabList, TabPanel, Tag, Textarea } from "../vendor/comp-lib/src/index.ts";
import { ILLUSTRATIVE_NOTE, ILLUSTRATIVE_THRESHOLD, SourceLink } from "./ChallengeDemo.jsx";
import {
  answerCompositionText, chatDecisionKey, CLASS_LABELS, completionCandidate, decisionAlreadyRecorded, feedbackScopeKey,
  formatDecisionTime, readTurnStream, reviewWriteReducer, seedChatDecisions,
} from "./challenge-demo.mjs";

/**
 * The assistant panel, visually subordinate to the briefing but no longer a bare form.
 *
 * It explains rather than authors: every statement arrives with its class and the records it came from, a
 * refusal is one cause-neutral sentence, and each statement ends in a review decision the professional makes.
 * The notice that this is an AI system comes from the API rather than being written here, so a client cannot
 * omit it: `ask()` reads `answer_notice` off the chat response and the rail renders that string on any turn
 * whose `generator_state` says a model was actually involved. The literal below is a last-resort wording for
 * a response that omitted the field, and it is only ever reached on such a turn — a deterministic turn
 * (`generator_state: "not_configured"`) states that instead.
 *
 * The structure is the owner's reference (the Copilot side panel in Dynamics 365), with two adaptations the
 * brief names: the second tab is `Ohjeet` — the encounter's Käypä hoito references as real data — rather than
 * a second generative surface whose grounding guarantees would be weaker, and the reference's changeable
 * context picker is a read-only chip, because the role and the encounter are chosen once in the page's own
 * sidebar and a second picker here would be a bug. The five parts, in order: the assistant header, the two
 * tabs, the context chip, the suggestion cards (empty state only), and the reply.
 */

const REDUCED_MOTION = "(prefers-reduced-motion: reduce)";

// The source count the rail states: what the answer cites, beside what the view offers. Nothing is stated when no
// record is cited, and the view's own count is never worded as support for the answer.
function sourceCountCaption(count) {
  const cited = count?.cited ?? 0;
  if (!cited) return "";
  const available = count.available ?? 0;
  const citedText = cited === 1 ? "1 kirjaukseen" : `${cited} kirjaukseen`;
  // The count is the selected set the answer was composed from (the digest's members), not the wider set the
  // caller is permitted to see, so the caption names that selected set and claims nothing wider.
  const availableText = available === 1 ? "valitussa joukossa on 1 sallittu kirjaus"
    : `valitussa joukossa on ${available} sallittua kirjausta`;
  return `Vastauksessa viitataan ${citedText}; ${availableText}.`;
}

// A question as the lead quotes it: quotes, angle brackets and runs of spaces are removed the same way.
const spokenForm = text => text.replace(/["'<>]/g, " ").replace(/\s+/g, " ").trim();

// Statuses after which a finding is an outcome rather than an offer: they keep their status text without a
// selection step (see `suggestionRow`). `failed` is listed too, but its review controls stay offered directly.
// The proposal label the actions table's steps carry (`chat.STEP_LABEL`). Shared with the backend by value: the
// label is also what identifies a step in a stored turn, so it cannot drift without the audit rows drifting too.
const STEP_LABEL = "Sääntöpohjainen arvio (ehdotus)";

const SETTLED_STATUSES = new Set(["saved", "decided", "failed", "ambiguous"]);

const REVIEW_LABELS = {
  accept: "Hyväksy tarkistettavaksi",
  edit: "Muokkaa",
  dismiss: "Ei tarvetta nyt",
};

// The guidance links arrive as API data, so the scheme and the publisher are checked here as well as on the
// server (backend/app/guidance.py validates the same host before it records a link). The vendored `Link`
// hands `href` straight to `<a>`, so without this a `javascript:` URL from a compromised or replaced API
// would become a script link on the professional's page. The host check is exact-or-subdomain, never a
// prefix match: `https://www.kaypahoito.fi.evil.example/` must not pass.
const GUIDELINE_HOST = "www.kaypahoito.fi";

function linkIsSafe(link) {
  if (!link || typeof link.url !== "string") return false;
  let parsed;
  try {
    parsed = new URL(link.url);
  } catch {
    return false;
  }
  return parsed.protocol === "https:"
    && (parsed.hostname === GUIDELINE_HOST || parsed.hostname.endsWith(`.${GUIDELINE_HOST}`));
}

/**
 * A Käypä hoito deep link, rendered the one way the rail renders one: the title, the publisher and the
 * publication date as visible text, never a bare or dead anchor. The rail's guideline panel and an
 * `external_evidence` statement both come through here, so the two cannot drift into two link styles — and
 * both go through `linkIsSafe`, because the URL arrives as API data.
 */
function GuidelineLink({ link }) {
  return (
    <>
      <Link href={link.url} target="_blank" rel="noreferrer noopener">
        {link.title} — {GUIDELINE_HOST}
      </Link>
      <span className="t-caption"> (julkaistu {link.published})</span>
    </>
  );
}

/**
 * The statement text plus, for a guideline statement, the link the statement cites.
 *
 * The title and the publication date are read back out of the sentence rather than carried in separate
 * fields: the sentence is the citation (the server composes it from the guideline's own recorded metadata),
 * so the link cannot say a different title or date than the text beside it. The URL is checked again here
 * even though the server attached it — it crosses the wire as data.
 */
function StatementText({ suggestion }) {
  if (suggestion.statement_class !== "external_evidence") return suggestion.text;
  const link = { title: guidelineTitle(suggestion.text), url: suggestion.url,
    published: guidelinePublished(suggestion.text) };
  if (!linkIsSafe(link)) return suggestion.text;
  return (
    <>
      {suggestion.text}{" "}
      <GuidelineLink link={link} />
    </>
  );
}

/** `Käypä hoito -suositus tähän tilanteeseen: Kohonnut verenpaine (julkaistu 21.09.2026).` -> the title. */
function guidelineTitle(text) {
  const match = /^Käypä hoito -suositus tähän tilanteeseen: (.+?) \(julkaistu [^)]+\)\.?$/.exec(text || "");
  return match ? match[1] : "";
}

function guidelinePublished(text) {
  const match = /\(julkaistu ([^)]+)\)/.exec(text || "");
  return match ? match[1] : "";
}

/**
 * The statement with its heading's clause removed, for the row that renders the heading as its label. The label
 * exists so the reader does not read the same clause twice, so the statement keeps only what follows the label.
 *
 * Provably a suffix of the API's own sentence: the heading must be its opening (case-insensitively) and end at a
 * word boundary, and only the separator and whitespace after it are removed. Nothing is added or reworded. A
 * remainder that would be empty returns `null`, so the caller keeps the full statement rather than an empty row.
 */
function statementRemainder(text, heading) {
  if (typeof text !== "string" || !heading) return null;
  if (text.slice(0, heading.length).toLowerCase() !== heading.toLowerCase()) return null;
  const rest = text.slice(heading.length);
  // A dash is a separator only when whitespace surrounds it; a colon or comma only when whitespace follows it.
  // A separator glued to the next word (`PHQ-9-yhteispisteet`) makes the whole thing one word, so nothing is cut.
  const separator = /^(?:\s+[-–—]|\s*[:,])\s+/.exec(rest);
  if (!separator && rest !== "" && !/^\s/.test(rest)) return null;
  const remainder = separator ? rest.slice(separator[0].length) : rest.replace(/^\s+/, "");
  return remainder.trim() ? remainder : null;
}

/**
 * The panel's marks, drawn here rather than shipped as assets — the app's existing idiom, the one
 * `STATUS_ICON_SHAPES` establishes in `ChallengeDemo.jsx`: a 24×24 viewBox, `currentColor`, `stroke-width`
 * 1.75, round caps and joins, and `aria-hidden` because every mark is decorative beside a real text label.
 *
 * The reference panel's cards each lead with a mark, and the mark is what makes three stacked cards read as
 * a choice rather than as a wall of questions: horizontal lines = a summary/list, a question-mark in a
 * speech bubble = ask, a lightbulb = something worth noticing, a document = a note or a source. They carry
 * no meaning a screen reader needs — the card's title and question are the label — so each is `aria-hidden`.
 */
const ICON_SHAPES = {
  list: <><path d="M4 6h16M4 12h16M4 18h10" /></>,
  ask: <><path d="M4 5h16v11H9l-5 4z" /><path d="M9.5 9.6a2.5 2.5 0 1 1 3.2 2.4v1.6" /><path d="M12.6 15.4v.2" /></>,
  idea: <><path d="M9.5 17h5" /><path d="M10 20h4" /><path d="M12 3a6 6 0 0 0-3.4 10.9V15h6.8v-1.1A6 6 0 0 0 12 3z" /></>,
  document: <><path d="M6 3h8l4 4v14H6z" /><path d="M14 3v4h4" /><path d="M9 12h6M9 16h6" /></>,
  scope: <><circle cx="12" cy="12" r="8" /><path d="M12 8.5v4l2.5 2" /></>,
};

function PanelIcon({ shape }) {
  return <svg className="icon brief-chat-icon" viewBox="0 0 24 24" width="24" height="24" fill="none"
    stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    {ICON_SHAPES[shape] ?? ICON_SHAPES.list}
  </svg>;
}

/**
 * The reference's product mark: a rounded square carrying three ascending bars, in the brand ink. It is a
 * mark, not a glyph — nothing in it is read out — so it is `aria-hidden` and the wordmark beside it is the
 * panel's identity.
 */
function AssistantMark() {
  return <svg className="brief-chat-mark" viewBox="0 0 24 24" width="24" height="24" aria-hidden="true">
    <rect x="2.5" y="2.5" width="19" height="19" rx="5" fill="var(--color-primary-tint)"
      stroke="var(--color-primary)" strokeWidth="1.5" />
    <path d="M7.5 16v-3.5M12 16V9M16.5 16v-6" fill="none" stroke="var(--color-primary)" strokeWidth="2"
      strokeLinecap="round" />
  </svg>;
}

/**
 * Which mark and which short title an offered question gets.
 *
 * The title is a deterministic short noun phrase derived from the question, never a new clinical claim: it is
 * the subject the question is about, read off the same vocabulary `chat.prompt_suggestions` builds its
 * questions from. The table is keyed by that vocabulary rather than by the whole sentence, so a rewording
 * that keeps its subject keeps its title ("Mihin riskiarvio perustuu?" and "Millaiseen riskiarvioon tämä
 * perustuu?" are the same card), and a question the table does not recognise falls back to its own leading
 * words rather than to an invented subject.
 *
 * An unmatched question is rendered under its first three words, which is a truncation of the reader's own
 * question and therefore cannot assert anything the question does not already say.
 */
const PROMPT_KINDS = [
  [/ristiriid|yhteisvaikutus|lääkitys|laakitys|lääke/i, { shape: "ask", title: "Lääkitykset" }],
  [/annos|annosta|ottamatta/i, { shape: "list", title: "Annostelu" }],
  [/riskiarvio|riski/i, { shape: "idea", title: "Riskiarvio" }],
  [/toimenpiteet|toimenpide/i, { shape: "list", title: "Toimenpiteet" }],
  [/arvioinnit|arviointi/i, { shape: "list", title: "Arvioinnit" }],
  [/kirjaus|kirjauks|vapaateksti|viimeisimm/i, { shape: "document", title: "Kirjaukset" }],
  [/mittaus|tulos|verenpaine/i, { shape: "document", title: "Mittaukset" }],
  [/yhteenveto|kooste/i, { shape: "list", title: "Yhteenveto" }],
  [/henkilö|perustiedot/i, { shape: "document", title: "Perustiedot" }],
];

function promptKind(prompt) {
  const question = prompt.text ?? "";
  const matched = PROMPT_KINDS.find(([pattern]) => pattern.test(question));
  if (matched) return matched[1];
  const leading = question.trim().split(/\s+/).slice(0, 3).join(" ").replace(/[?.,:;!]+$/, "");
  return { shape: "ask", title: leading || "Kysy" };
}

/**
 * The statement's citation, `{ label, ids }`, or `null` when there is nothing to print. The ids are rendered
 * through `SourceLink`, which links only the records the briefing renders.
 *
 * A `rule_output` label is the tag itself (`suggestionTag`), so it is never printed again. Any other label is
 * printed as before; a label that only repeats the class word, with no ids, is dropped. A `source_fact` label (`Lähde: lääkitys · 2026-08-18`) and an
 * `external_evidence` label that names the publisher differ from the class word, so they render.
 */
function citationOf(suggestion, tagText) {
  const classWord = CLASS_LABELS[suggestion.statement_class] || suggestion.statement_class;
  const label = suggestion.label || "";
  const ids = suggestion.source_record_ids ?? [];
  // An advice row's citation is the knowledge source it was drafted from (`basis_note`); its refs are printed beside it.
  if (suggestion.statement_class === "knowledge_advice") return { label: suggestion.basis_note || "", ids };
  const printed = suggestion.statement_class === "rule_output" && label === tagText ? "" : label;
  if (!ids.length && (!printed || printed === classWord)) return null;
  return { label: printed, ids };
}

/**
 * The tag's text. A `rule_output` carries the server's own label when it differs from the class word, so the
 * engine's finding (`Sääntöpohjainen arvio`) and its proposal (`Sääntöpohjainen arvio (ehdotus)`) read apart.
 * Every other class shows its `CLASS_LABELS` value.
 */
function suggestionTag(suggestion) {
  const classWord = CLASS_LABELS[suggestion.statement_class] || suggestion.statement_class;
  const label = suggestion.label || "";
  return suggestion.statement_class === "rule_output" && label && label !== classWord ? label : classWord;
}

// The tools the assistant reads, in the professional's words. Shown while the answer is being prepared.
const TOOL_WORDS = {
  list_records: "kirjausten luettelo", get_record: "kirjaus", get_esitiedot: "esitiedot",
  get_prefilled_forms: "lomakkeet", get_engine_assessment: "sääntöpohjainen arvio", search_knowledge: "tietopohja",
};

function newRequestId() {
  return `w${Date.now().toString(36)}${Math.random().toString(36).slice(2, 10)}`;
}

// F2: the decision map is keyed by the turn as well as the suggestion, because the API restarts suggestion
// ids at `s0` on every turn (backend/app/chat.py). A decision recorded against turn A therefore cannot be
// read back on turn B's `s0`, and a response from turn A that lands after turn B started writes under A's
// own key instead of turning B's `s0` into "Tallennettu palveluun" and locking it out of review. The key
// lives in `challenge-demo.mjs` with the loader that seeds from the server, so both agree by construction.
const decisionKey = chatDecisionKey;
// F3: shipped wording. `AI_NOTICE_FALLBACK` is the last-resort string for a response that carried a
// generator but omitted `answer_notice`; it is never used to claim AI involvement on a deterministic turn.
const AI_NOTICE_FALLBACK = "Olet yhteydessä tekoälyyn. Vastaus on tarkistettava.";
const MODEL_CAPTION = "Vastaus on koottu lähteistä ja tarkistettu lauseittain.";
const NO_MODEL_CAPTION = "Vastaus on koottu suoraan lähteistä ilman mallia.";
// State "withheld": a model was called and no sentence of its output is in this answer. Deliberately
// cause-neutral, because the one state covers a sentence the verifier did not support, an empty answer the
// model returned under rule 2, a call that failed, and a verified sentence the deterministic arm's four
// slots displaced — so the caption must not assert grounding as the cause. It says that a model was used
// and that its text is not published; it does not say anything is hidden, names no record, no count and
// none of the withheld text, and does not imply the model refused. This is the state the rail previously
// could not tell from "no model was configured", which made a withheld model arm read as a model-free
// answer.
const MODEL_WITHHELD_CAPTION = "Tekoälyä käytettiin, mutta sen tuottamaa tekstiä ei julkaistu tässä vastauksessa.";
// The caption is chosen from the turn's generator state, the field that separates the three cases. The
// fallback covers a response from an older server: a surviving `generator` means published, its absence
// means no model was involved, and a withheld arm cannot be recovered from a record that never held it.
const CAPTIONS = {
  published: MODEL_CAPTION,
  withheld: MODEL_WITHHELD_CAPTION,
  not_configured: NO_MODEL_CAPTION,
};

function generatorState(turn) {
  if (turn.generator_state) return turn.generator_state;
  return turn.generator ? "published" : "not_configured";
}

const MODEL_CLASSES = new Set(["model_summary", "knowledge_advice"]);
const MODEL_GROUP_LABEL = "Mallin muotoilemat lauseet ja ehdotukset";
const NO_STATEMENTS_TEXT = "Vastauksessa ei ole julkaistavia lauseita.";
const NO_ABSTENTION_TEXT = "Vastausta ei saatu: palvelu ei antanut perustelua.";
// The server's own 409 for a digest that no longer matches the encounter (`main.py`, the chat route).
const SEAL_DETAIL = "the encounter changed";
const SEAL_TEXT = "Keskustelu suljettiin, koska tiedot muuttuivat. Aloita uusi keskustelu, jotta kysymykset kohdistuvat nykyiseen tilanteeseen.";
const RESTART_LABEL = "Aloita uusi keskustelu";
const questionId = turnId => `chat-question-${turnId}`;

// The class-tag gloss, looked up at call time: `ILLUSTRATIVE_NOTE` comes from ChallengeDemo, which imports this file.
function glossFor(tagText, statementClass) {
  if (tagText === STEP_LABEL) return "ehdotus seuraavasta askeleesta, jonka järjestelmän sääntö tuottaa; ehdotuksen päättää aina ammattilainen";
  switch (statementClass) {
    case "source_fact": return "yhdestä tämän näkymän kirjauksesta muodostettu lause, ei kirjauksen koko sisältö; lähdetunniste ja päiväys näkyvät rivin omassa katselmuksessa";
    // The qualification is a conditional, because two of the three rule outputs carry no basis and applied no cut-off.
    case "rule_output": return `järjestelmän oman säännön tuottama lause, ei lähdekirjaus eikä mallin tekstiä. Jos sääntö on soveltanut havainnollistavaa numeerista rajaa, rivillä on siitä erillinen merkintä: ${ILLUSTRATIVE_NOTE}`;
    case "external_evidence": return "Käypä hoito -suosituksen oma otsikko, julkaisupäivä ja linkki; suosituksen sisältöä ei toisteta";
    case "model_summary": return "mallin muotoilema lause, joka läpäisi lausekohtaisen tarkistuksen; ei itsenäinen lähde";
    case "knowledge_advice": return "ehdotus, joka on laadittu tässä kysymyksessä näytetyistä tietopohjan kohdista; lähde ja viitteet näkyvät rivillä, ja ammattilainen päättää";
    default: return null;
  }
}

// Everything the reply of one turn shows, split into the stages it is read in: the deterministic lead and steps,
// the deterministic findings, then the model-authored sentences. The split is the order the reader meets them in;
// the server's own ordinal is kept within each stage.
function analyse(turn, prompts) {
  const suggestions = turn?.suggestions ?? [];
  const lead = suggestions.find(item => item.statement_class === "answer_lead") ?? null;
  const allFindings = suggestions.filter(item => item !== lead);
  const isStep = item => item.statement_class === "rule_output" && item.label === STEP_LABEL;
  // An advice row with no ref is withheld: it names no knowledge it was drafted from, so it has nothing to show.
  const isUncitedAdvice = item => item.statement_class === "knowledge_advice" && !item.refs?.length;
  // A step without its basis note is still a step, and it is withheld rather than shown as a finding.
  const steps = allFindings.filter(item => isStep(item) && item.basis_note);
  const findings = allFindings.filter(item => !isStep(item) && !isUncitedAdvice(item));
  const deterministic = findings.filter(item => !MODEL_CLASSES.has(item.statement_class));
  const modelled = findings.filter(item => MODEL_CLASSES.has(item.statement_class));
  const refusal = turn?.outcome === "procedural_refusal";
  // The heading names the situation the engine keyed on, read from the steps' own basis note ("Tilanne: <type>. …").
  const situation = steps.map(item => item.basis_note.match(/^Tilanne: (.+?)\. /)?.[1]).find(Boolean);
  const firstNote = steps[0]?.basis_note ?? "";
  const separator = firstNote.indexOf(". ");
  const status = separator < 0 ? firstNote : firstNote.slice(separator + 2);
  const stepsBase = situation ? `Sääntöpohjainen ehdotus · Tilanne: ${situation}` : "Sääntöpohjainen ehdotus";
  // The follow-ups are the offered questions other than the one this answer already answers.
  const askedQuestion = spokenForm(lead?.text.match(/^Kysymykseesi "(.*)" tämän palvelutilanteen/)?.[1] ?? "");
  return {
    lead, steps, stepsHeading: status ? `${stepsBase}. ${status}` : stepsBase,
    deterministic, modelled, findings: [...deterministic, ...modelled],
    // Every tagged row the reader sees, for the legend. A refusal shows no rows, so it has none to explain.
    tagged: refusal ? [] : [...steps, ...deterministic, ...modelled],
    refusal, composition: answerCompositionText(turn), sourceCount: sourceCountCaption(turn?.source_count),
    state: turn ? generatorState(turn) : "not_configured",
    followUps: turn?.outcome === "answer"
      ? [...(turn.followups ?? []).map(text => ({ text })),
        ...prompts.filter(prompt => spokenForm(prompt.text) !== askedQuestion
          && !(turn.followups ?? []).includes(prompt.text))]
      : [],
  };
}

/**
 * The rail's whole surface, without state or requests, so the populated states (prompts, guideline links,
 * a conversation, a recorded decision, a failure) can be rendered and audited directly.
 *
 * `thread` is the conversation, oldest first: `{ turnId, question, pending }` while a question waits, then
 * `{ turnId, question, turn, notice }` once its reply is in. `turn` and `notice` alone render one reply, for the
 * states that have no conversation around them.
 */
export function ChatRail({
  question = "", onQuestionChange, onSubmit, loading = false, error = "", waited = 0,
  prompts = [], links = [], recordTargets,
  turn = null, notice = null, thread = null, sealed = false, onRestart,
  decisions = {}, editing = "", editText = "",
  onEditTextChange, onDecide, onRetryDecide, onStartEdit, onCancelEdit, onSubmitEdit,
  // Asking a suggested question: one click sends its text, and the composer is left as it was.
  onAskQuestion,
  // The panel's own context: what the assistant is scoped to.
  contextRole = "", contextPerson = "",
  // Which tab opens. It is view state that belongs to this surface rather than to the page, and switching
  // tabs must not clear a typed question. The prop exists so a render-only fixture (and the audit) can open
  // the `Ohjeet` panel without a click; the interface is still the surface's own default, `Kysy kysymys`.
  initialTab = "kysy",
  // The identity row (mark and wordmark). The shell's assistant pane carries the title in its own header row,
  // so it switches the row off; a standalone render keeps it.
  identity = true,
}) {
  const [tab, setTab] = useState(initialTab);
  // Which finding the review controls are open on, per turn: each answer starts from its own default (the first
  // open finding) and keeps the choice the reader made on it.
  const [selected, setSelected] = useState({});
  const railRef = useRef(null);
  const replyRef = useRef(null);
  const editAreaRef = useRef(null);
  // The finding whose editor was cancelled: focus returns to its own Muokkaa control once it is rendered.
  const focusOnEditClose = useRef("");
  // The decision whose control was just used: focus moves to its status line once that line is rendered.
  const focusOnSaved = useRef("");
  const restartFocus = useRef(false);

  const entries = thread ?? (turn ? [{ turnId: turn.turn_id, question: "", turn, notice }] : []);
  const analysed = entries.map(entry => (entry.turn ? analyse(entry.turn, prompts) : null));
  // The newest reply is the one brought into view; an earlier reply is already where the reader left it.
  const newestReplyId = [...entries].reverse().find(entry => entry.turn)?.turn.turn_id ?? null;

  // Editing moves focus into the text area; cancelling returns it to the control that opened the editor.
  useEffect(() => {
    if (editing) {
      editAreaRef.current?.focus();
      return;
    }
    const opener = focusOnEditClose.current;
    if (!opener) return;
    focusOnEditClose.current = "";
    [...(railRef.current?.querySelectorAll("[data-edit-opener]") ?? [])]
      .find(node => node.dataset.editOpener === opener)?.focus();
  }, [editing]);

  // A saved decision removes the buttons that were used; focus goes to the status that replaces them. A decision
  // that failed or is still ambiguous has no such status, so the pending focus is dropped rather than kept.
  useEffect(() => {
    const key = focusOnSaved.current;
    if (!key) return;
    const status = [...(railRef.current?.querySelectorAll("[data-decision-status]") ?? [])]
      .find(node => node.dataset.decisionStatus === key);
    if (status) {
      focusOnSaved.current = "";
      status.focus();
    } else if (decisions[key]?.status !== "saving") {
      focusOnSaved.current = "";
    }
  });

  // After a restart the composer returns, and focus goes into it so the keyboard user is not left on nothing.
  useEffect(() => {
    if (sealed || !restartFocus.current) return;
    restartFocus.current = false;
    railRef.current?.querySelector(".brief-chat-ask input")?.focus();
  }, [sealed]);

  // The newest reply comes into view once, unless the whole reply is already on screen. Reduced motion gets an
  // instant jump, as `revealRunSpecifications` in App.jsx does.
  useEffect(() => {
    const node = replyRef.current;
    if (!node?.scrollIntoView) return;
    const rect = node.getBoundingClientRect();
    if (rect.top >= 0 && rect.bottom <= (globalThis.innerHeight ?? 0)) return;
    const reduced = globalThis.matchMedia?.(REDUCED_MOTION).matches ?? false;
    node.scrollIntoView({ block: "start", behavior: reduced ? "auto" : "smooth" });
  }, [newestReplyId]);  // F8: the wrapper is gated on the SAME list it maps over. Gating on the raw `links` printed the heading
  // over an empty list whenever every link failed the safety check above.
  const safeLinks = links.filter(linkIsSafe);

  function selectFinding(turnId, suggestionId) {
    setSelected(current => ({ ...current, [turnId]: suggestionId }));
  }

  function cancelEdit(turnNow, suggestion) {
    // The finding whose editor is closed is selected, so its own controls (and Muokkaa) are on screen to
    // receive focus. Selecting it also means a finding the reader had moved away from comes back.
    const key = decisionKey(turnNow.turn_id, suggestion.suggestion_id);
    focusOnEditClose.current = key;
    selectFinding(turnNow.turn_id, suggestion.suggestion_id);
    onCancelEdit?.();
  }

  function suggestionRow(view, suggestion) {
    const turnNow = view.turn;
    const id = suggestion.suggestion_id;
    const key = decisionKey(turnNow.turn_id, id);
    const decision = decisions[key] || {};
    const saved = decision.status === "saved";
    const saving = decision.status === "saving";
    // F4: an ambiguous outcome keeps the decision buttons off the screen — the only offered action is a retry
    // of the SAME save, with its original idempotency key. A definite (4xx) rejection is not retryable with
    // the same key, so the buttons return and a fresh decision starts a fresh write.
    const tagText = suggestionTag(suggestion);
    const citation = citationOf(suggestion, tagText);
    const heading = typeof suggestion.heading === "string" && suggestion.heading.trim() ? suggestion.heading : "";
    // An external-evidence statement keeps its whole sentence: its link is rendered through StatementText beside it.
    const remainder = heading && suggestion.statement_class !== "external_evidence"
      ? statementRemainder(suggestion.text, heading) : null;
    const classTag = (
      <Tag className={suggestion.statement_class === "model_summary"
        ? "brief-record-state brief-state-draft" : "brief-role-chip"}>
        {tagText}
      </Tag>
    );
    const errorId = `chat-decision-error-${turnNow.turn_id}-${id}`;
    const describedBy = decision.status === "failed" ? errorId : undefined;
    // With one finding its three controls are shown at once, as before. With more, each finding offers a
    // single "Tarkista tämä" that opens its own group, and only the selected finding renders the controls. A
    // failed finding keeps its controls on screen: its retry is an offer, and hiding it behind a selection
    // would make the failure look final.
    const offersDirectly = !view.multi || decision.status === "failed";
    const isSelected = view.selectedId === id;
    const groupId = `chat-review-${turnNow.turn_id}-${id}`;
    const editing_ = editing === key;
    return (
      <li key={id} className="brief-proposal">
        {/* The heading is the server's label taken from the statement's own words (`chat._heading_for_record`,
            `chat._rule_heading`), rendered as text. Where the statement opens with it, the statement is shown without
            that clause, so the label and the sentence do not read the same words twice. With none, the row is the
            single paragraph it always was. */}
        {heading ? (
          <>
            <p className="t-body">{classTag}</p>
            <p className="t-body-strong" data-heading={heading}>{heading}</p>
            <p className="t-body">{remainder ?? <StatementText suggestion={suggestion} />}</p>
          </>
        ) : (
          <p className="t-body">
            {classTag}{" "}
            <StatementText suggestion={suggestion} />
          </p>
        )}
        {/* The label line is the citation, not a restatement: it is rendered only for what the tag does not say, a
            record id or a source label. A rule output's label is the tag itself, so it is never printed twice. */}
        {citation ? (
          <p className="t-caption">
            {citation.label}
            {citation.label && citation.ids.length ? " · " : ""}
            {citation.ids.map((recordId, position) => (
              <Fragment key={recordId}>{position ? ", " : ""}
                <SourceLink id={recordId} targets={recordTargets}>{recordId}</SourceLink>
              </Fragment>
            ))}
          </p>
        ) : null}
        {/* The refs are the knowledge items' identifiers, printed as text. They are not links: no URL is invented here. */}
        {suggestion.statement_class === "knowledge_advice" && suggestion.refs?.length ? (
          <p className="t-caption">Viitteet: {suggestion.refs.join(", ")}</p>
        ) : null}
        {/* The marker comes from the server's `basis`, not from a list here: a frontend list of "threshold
            rules" would go stale in the silent direction, dropping the caveat when the backend changes. */}
        {suggestion.statement_class === "rule_output" && suggestion.basis === ILLUSTRATIVE_THRESHOLD ? (
          <p className="t-caption">{ILLUSTRATIVE_NOTE}</p>
        ) : null}
        {editing_ ? (
          <form onSubmit={event => {
            event.preventDefault();
            focusOnSaved.current = key;
            onSubmitEdit?.(turnNow, suggestion);
          }}>
            <Field label="Muokattu teksti" hint="Tallennetaan sellaisenaan tarkistettavaksi.">
              <Textarea ref={editAreaRef} rows={3} value={editText} onChange={event => onEditTextChange?.(event.target.value)} />
            </Field>
            <div className="brief-button-row">
              <Button variant="primary" type="submit">Tallenna muokkaus</Button>
              <Button type="button" onClick={() => cancelEdit(turnNow, suggestion)}>Peruuta</Button>
            </div>
          </form>
        ) : saved ? (
          <p className="t-caption" role="status" tabIndex={-1} data-decision-status={key}>
            {decision.label || "Tallennettu palveluun"} · {REVIEW_LABELS[decision.action] || decision.action}
            {decision.recordedAt ? <> · <time dateTime={decision.recordedAt}>{formatDecisionTime(decision.recordedAt)}</time></> : null}
          </p>
        ) : decision.status === "decided" ? (
          // The caller's own duplicate, reported by the server's `reason: already_decided`. The three
          // buttons are gone: this suggestion already has the caller's decision, and offering it again is
          // the bug this state exists to prevent.
          <p className="t-caption" role="status">
            Olet jo päättänyt tämän ehdotuksen. Tallennettua päätöstä ei voi tehdä uudelleen.
          </p>
        ) : decision.status === "ambiguous" ? (
          <div className="t-caption" role="alert">
            <p>Tallennuksen tila on epäselvä: {decision.error} Tee sama tallennus uudelleen samalla tunnisteella ennen muutosta.</p>
            <Button variant="primary" onClick={() => onRetryDecide?.(turnNow, suggestion)}>Yritä samaa tallennusta uudelleen</Button>
          </div>
        ) : (
          <>
            {view.multi && !offersDirectly ? (
              <Button aria-expanded={isSelected} aria-controls={groupId}
                onClick={() => selectFinding(turnNow.turn_id, id)}>Tarkista tämä</Button>
            ) : null}
            <div id={groupId} className={offersDirectly || isSelected ? "brief-button-row" : undefined}>
              {offersDirectly || isSelected ? (<>
                <Button variant="primary" disabled={saving} aria-describedby={describedBy}
                  onClick={() => { focusOnSaved.current = key; onDecide?.(turnNow, suggestion, "accept"); }}>{REVIEW_LABELS.accept}</Button>
                <Button disabled={saving} aria-describedby={describedBy}
                  onClick={() => {
                    selectFinding(turnNow.turn_id, id);
                    onStartEdit?.(turnNow, suggestion);
                  }} data-edit-opener={key}>{REVIEW_LABELS.edit}</Button>
                <Button disabled={saving} aria-describedby={describedBy}
                  onClick={() => { focusOnSaved.current = key; onDecide?.(turnNow, suggestion, "dismiss"); }}>{REVIEW_LABELS.dismiss}</Button>
              </>) : null}
            </div>
          </>
        )}
        {decision.status === "failed" ? (
          // F4: a definite rejection is a visible, associated error, not only a live announcement: the
          // professional's click did nothing and the page must say so next to the action it belongs to.
          <p className="t-caption brief-inline-status" role="alert" id={errorId}>
            Palvelu hylkäsi tallennuksen: {decision.error}
          </p>
        ) : null}
      </li>
    );
  }

  // One answered question: the question as the reader asked it, then the reply in its stages. The lead and the
  // steps are deterministic and come first; the model-authored sentences come after them, under their own label.
  function replyEntry(entry, index) {
    const turnNow = entry.turn;
    const view = analysed[index];
    const settled = item => SETTLED_STATUSES.has(decisions[decisionKey(turnNow.turn_id, item.suggestion_id)]?.status);
    const firstOpen = view.findings.find(item => !settled(item)) ?? null;
    const rowView = {
      turn: turnNow, multi: view.findings.length > 1,
      selectedId: selected[turnNow.turn_id] ?? firstOpen?.suggestion_id,
    };
    const nothingToShow = !view.lead && !view.steps.length && !view.findings.length;
    return (
      <article key={entry.turnId} ref={turnNow.turn_id === newestReplyId ? replyRef : undefined}
        className="brief-chat-reply" data-turn-id={turnNow.turn_id}
        aria-labelledby={entry.question ? questionId(turnNow.turn_id) : undefined}>
        {entry.question ? <p id={questionId(turnNow.turn_id)} className="t-body-strong">{entry.question}</p> : null}
        <div className="brief-chat-reply-body">
          {view.state !== "not_configured" ? <p className="t-caption brief-chat-notice">{entry.notice || AI_NOTICE_FALLBACK}</p> : null}
          {view.refusal ? (<>
            {/* A refusal is the procedural answer, not a missing one. A model that was called and published nothing
                is still disclosed, so the two cases read differently. */}
            <p className="t-body" data-outcome="refusal">{turnNow.abstention ?? NO_ABSTENTION_TEXT}</p>
            {view.state === "withheld" ? <p className="t-caption" data-state="withheld">{CAPTIONS.withheld}</p> : null}
          </>) : (<>
            {view.lead ? <p className="t-body brief-chat-lead">{view.lead.text}</p> : null}
            {view.steps.length ? (
              <div data-steps="engine">
                <p className="t-body-strong">{view.stepsHeading}</p>
                <ol>
                  {view.steps.map(item => (
                    <li key={item.suggestion_id}>
                      <Tag>{suggestionTag(item)}</Tag>
                      <p className="t-body">{item.text}</p>
                      <p className="t-caption">{item.basis_note}</p>
                    </li>
                  ))}
                </ol>
              </div>
            ) : null}
            {view.deterministic.length ? (
              <ul className="brief-decision-outcomes brief-chat-findings" data-stage="deterministic">
                {view.deterministic.map(item => suggestionRow(rowView, item))}
              </ul>
            ) : null}
            {view.modelled.length ? (
              <div data-stage="model">
                <p className="t-caption">{MODEL_GROUP_LABEL}</p>
                <ul className="brief-decision-outcomes brief-chat-findings">
                  {view.modelled.map(item => suggestionRow(rowView, item))}
                </ul>
              </div>
            ) : null}
            {nothingToShow ? <p className="t-caption" data-absent="answer">{NO_STATEMENTS_TEXT}</p> : null}
            {view.composition ? <p className="t-caption" data-composition="answer">{view.composition}</p> : null}
            {view.sourceCount ? <p className="t-caption" data-source-count="answer">{view.sourceCount}</p> : null}
            <p className="t-caption" data-state={view.state}>{CAPTIONS[view.state] || NO_MODEL_CAPTION}</p>
            {view.followUps.length ? (
              <div data-followups="answer">
                <p className="t-caption">Voit kysyä myös</p>
                {view.followUps.map(prompt => (
                  <Button key={prompt.text} disabled={loading} onClick={() => onAskQuestion?.(prompt.text)}>
                    {prompt.text}
                  </Button>
                ))}
              </div>
            ) : null}
          </>)}
        </div>
      </article>
    );
  }

  // A question still waiting for its reply. It is not a reply, so it says only what is known: it is waiting, and
  // how long. The elapsed seconds are the wait itself, not a value about the answer.
  function pendingEntry(entry) {
    const tools = [...new Set((entry.tools ?? []).map(name => TOOL_WORDS[name] ?? name))];
    const stage = entry.stage === "replaying" ? "Aiempi vastaus haetaan"
      : tools.length ? `Avustaja lukee: ${tools.join(", ")}` : "Vastausta odotetaan";
    return (
      <article key={entry.turnId} className="brief-chat-reply" data-pending={entry.turnId}>
        <p className="t-body-strong">{entry.question}</p>
        <p className="t-caption">{stage} <span aria-hidden="true">· {waited} s</span></p>
        {entry.sentences?.length ? (
          <ul className="brief-chat-live" data-live="sentences" aria-label="Tarkistetut lauseet">
            {entry.sentences.map((sentence, index) => <li key={index} className="t-body">{sentence.text}</li>)}
          </ul>
        ) : null}
      </article>
    );
  }

  // The legend explains the tags the thread shows, and only those: each term is a tag that was rendered, and a tag
  // with no gloss is left out rather than named.
  const legend = [];
  for (const view of analysed) {
    for (const item of view?.tagged ?? []) {
      const term = suggestionTag(item);
      if (legend.some(([known]) => known === term)) continue;
      const gloss = glossFor(term, item.statement_class);
      if (gloss) legend.push([term, gloss]);
    }
  }

  // The follow-ups and the prompt cards: the cards are the empty state only. Once a question exists the panel is a
  // conversation, and the composer below carries the next one.
  // F6: the justification stays visible text on screen, described BY the card, so a keyboard or touch user
  // gets the same "why is this offered" a pointer user got from the title tooltip. The evidence ids use the
  // briefing's own target-aware record link: an id whose anchor this page does not render stays plain text
  // rather than becoming a dead link.
  const suggestionCards = prompts.length ? (
    <ul className="brief-chat-suggestions">
      {prompts.map((prompt, index) => {
        const whyId = `chat-prompt-why-${index}`;
        const shownIds = (prompt.evidence_record_ids ?? []).filter(recordId => recordTargets?.has(recordId));
        const kind = promptKind(prompt);
        return (
          <li key={prompt.text}>
            {/* One click asks: the card sends its own text through `onAskQuestion`. No `aria-label`: the accessible
                name is the visible question, so it contains the label (WCAG 2.5.3). `kind` only picks the icon. */}
            <button type="button" className="brief-chat-suggestion" disabled={loading}
              aria-describedby={whyId} onClick={() => onAskQuestion?.(prompt.text)}>
              <PanelIcon shape={kind.shape} />
              <span className="brief-chat-suggestion-body">
                <span className="t-body brief-chat-suggestion-question">{prompt.text}</span>
              </span>
            </button>
            {/* The record ids are shown only where the briefing renders that record, as a link to it. An id
                this page does not render is not printed at all: a bare id is not something the reader can use. */}
            <p className="t-caption brief-chat-suggestion-why" id={whyId}>{prompt.why}
              {shownIds.length ? <>{" · "}{shownIds.map((recordId, position) => (
              <Fragment key={recordId}>{position ? ", " : ""}
                <SourceLink id={recordId} targets={recordTargets}>{recordId}</SourceLink>
              </Fragment>
            ))}</> : null}
            </p>
          </li>
        );
      })}
    </ul>
  ) : null;

  const candidate = loading ? null : completionCandidate(prompts, question);
  // The tabpanel wrapper is the app's own element around the open tab's content, so the two panels can be
  // laid out (and audited) separately without the app touching the vendored component's own panel class.
  const tabPanel = (name, content) =>
    <TabPanel tabId={`chat-tab-${name}`} selected
      className={name === "kysy" ? "brief-chat-tabpanel brief-chat-tabpanel-kysy" : "brief-chat-tabpanel brief-chat-tabpanel-ohjeet"}>{content}</TabPanel>;

  return (
    <section ref={railRef} className="brief-card brief-data-section brief-chat" aria-label="LUVN-avustaja">
      {/* 1. The assistant header: a mark and the wordmark. Left out when the host pane names the surface. */}
      {identity ? <div className="brief-chat-identity">
        <AssistantMark />
        <p className="t-title-md brief-chat-wordmark">LUVN-avustaja</p>
      </div> : null}
      {/* 2. Two tabs on one row. `Tabs` is the vendored component and owns the tab semantics. The second tab is
          `Ohjeet`, not the reference's email writer: this product's prose is grounded per sentence, so a second
          generative surface with weaker guarantees would be a clinical problem rather than a feature. */}
      <div className="brief-chat-tabs">
      <TabList label="Avustajan näkymät">
        <Tab id="chat-tab-kysy" selected={tab === "kysy"} onClick={() => setTab("kysy")}>Kysy kysymys</Tab>
        <Tab id="chat-tab-ohjeet" selected={tab === "ohjeet"} onClick={() => setTab("ohjeet")}>Ohjeet</Tab>
      </TabList>
        {tab === "kysy" ? tabPanel("kysy", <>
          {/* 3. The context chip, read-only: the role and the encounter are chosen once in the page's own sidebar. */}
          {contextRole || contextPerson ? (
            <p className="t-caption brief-chat-context"
              title="Avustaja näkee vain tämän roolin ja tämän palvelutilanteen tiedot.">
              <PanelIcon shape="scope" />
              <span className="brief-chat-context-value">
                {[contextRole, contextPerson].filter(Boolean).join(" · ")}
              </span>
            </p>
          ) : null}
          {/* 4. The suggestion cards, in the empty state only. */}
          {entries.length ? null : suggestionCards}
          {/* 5. The conversation. It is one log: each question is appended when it is asked and its reply replaces
              the waiting entry in place, so the reader keeps every earlier question and answer on screen. The log is
              a polite live region; the waiting text is kept out of it below. */}
          <div role="log" aria-label="Keskustelu" aria-live="polite" aria-busy={loading}>
            {entries.map((entry, index) => (entry.pending ? pendingEntry(entry) : replyEntry(entry, index)))}
          </div>
          {legend.length ? (
            <div className="t-caption">
              <details>
                <summary className="t-control">Mitä merkinnät tarkoittavat?</summary>
                <dl>
                  {legend.map(([term, gloss]) => (
                    <Fragment key={term}>
                      <dt className="t-body-strong">{term}</dt>
                      <dd>{gloss}</dd>
                    </Fragment>
                  ))}
                </dl>
              </details>
            </div>
          ) : null}
          {/* The composer. The library's TextField carries the error itself, so a failed ask turns on
              `aria-invalid` and the message lands in the control's `aria-describedby`. Arrow-down completes the
              field from the offered questions, and only those: no phrase is authored here. */}
          {sealed ? (
            // A 409 from the server: the digest the conversation was sealed with no longer matches the encounter, so
            // no further question can be answered on it. The earlier turns stay on screen; a restart begins again.
            <div>
              <p className="t-body" role="alert">{SEAL_TEXT}</p>
              <div className="brief-button-row">
                <Button variant="primary" onClick={() => { restartFocus.current = true; onRestart?.(); }}>{RESTART_LABEL}</Button>
              </div>
            </div>
          ) : (
            <form className="brief-chat-ask" onSubmit={onSubmit}>
              <Field label="Kysymys" error={error || undefined}
                hint={candidate ? `Täydennä: ${candidate} (paina alas-nuolta)` : undefined}>
                <Input value={question} maxLength={2000}
                  onKeyDown={event => {
                    if (event.key !== "ArrowDown" || !candidate) return;
                    event.preventDefault();
                    onQuestionChange?.(candidate);
                  }}
                  onChange={event => onQuestionChange?.(event.target.value)} />
              </Field>
              <div className="brief-button-row">
                <Button variant="primary" type="submit" loading={loading}>Kysy</Button>
              </div>
            </form>
          )}
          {loading ? <p role="status" className="visually-hidden brief-transient-status">Haetaan vastausta…</p> : null}
          {error ? <p role="alert" className="t-caption brief-transient-status visually-hidden">{error}</p> : null}
        </>) : tabPanel("ohjeet", <>
          {/* The `Ohjeet` tab is real data, not a second generative surface: the encounter's Käypä hoito
              references, the same `guideline_links` the rail has always received. Titles, publisher and publication
              date are the publisher's own citation metadata; no guideline content is reproduced. */}
          <h3 className="t-body-strong brief-chat-guidelines-heading">Tämän tilanteen Käypä hoito -suositukset</h3>
          {safeLinks.length ? (
            <ul className="brief-chat-guidelines">
              {safeLinks.map(link => (
                <li key={link.code}>
                  <GuidelineLink link={link} />
                </li>
              ))}
            </ul>
          ) : (
            <p className="t-caption brief-chat-guidelines-empty">
              Tähän palvelutilanteeseen ei ole liitetty Käypä hoito -suosituksia.
            </p>
          )}
        </>)}
      </div>
    </section>
  );
}

// F1: the conversation belongs to ONE encounter, and the reset is a **mount key's** job — React replaces the
// component instead of reusing it, so every piece of state *and* every ref (`epoch`, which guards in-flight
// answers, and `pendingReviews`) starts fresh. The key is applied here, inside the exported component, so no
// caller can forget it: `App.jsx` passes the same key, and a caller that re-renders one root with a new
// encounter — as the test harness does — gets the reset anyway.
//
// This replaces a hand-written reset that cleared a dozen state values and two refs. The compiler flagged that
// code twice over, and both objections have the same answer: setting state synchronously in an effect starts a
// second render, and the same reset moved out of the effect reached the two refs during render. A key is what
// discards component state, it covers the refs as well as the state, and it cannot be half-applied.
export function ChatPanel(props) {
  return <ChatPanelFor key={props.encounterId} {...props} />;
}

// `decisions` is the `GET …/decisions` payload the shell already fetched for this encounter (or null before it
// lands). The rail used to request the route itself, so one encounter open was two identical requests (C2);
// it now seeds from the prop and asks the route for nothing.
function ChatPanelFor({ encounterId, api = "", recordTargets, roleLabel = "", personName = "", decisions: loaded = null,
  identity = true }) {
  const [question, setQuestion] = useState("");
  // The conversation, oldest first. A question still waiting is `pending` and has no turn yet. The server keeps the
  // same turns; this list is what this visit asked, so a second question is appended and never replaces the first.
  const [thread, setThread] = useState([]);
  const [digestId, setDigestId] = useState(null);
  const [sealed, setSealed] = useState(false);
  const [loading, setLoading] = useState(false);
  const [waited, setWaited] = useState(0);
  const [error, setError] = useState("");
  const [editing, setEditing] = useState("");
  const [editText, setEditText] = useState("");
  const [decisions, setDecisions] = useState({});
  // The decisions the server already holds for this encounter and this caller, keyed exactly like the live
  // map. Kept apart because the live map is per-visit state: the loaded answers survive a reset of it, and
  // they are merged over it at render time. Derived from the prop, not fetched: the shell owns the request.
  const loadedDecisions = loaded ? seedChatDecisions(loaded) : {};
  const [prompts, setPrompts] = useState([]);
  const [links, setLinks] = useState([]);
  // The review writes' own generation state, exactly as the proposal rail keeps it: stable ids per body,
  // and an unresolved (ambiguous) body that only an explicit retry may re-post.
  const pendingReviews = useRef(new Map());
  // Bumped on every encounter change, so an answer that lands after the switch is dropped rather than shown in the
  // new encounter's conversation.
  const epoch = useRef(0);

  // Offered questions arrive before the first is asked, so the surface shows what this encounter invites
  // rather than an empty box. A failure here is silent by design: the box still works without them.
  //
  // F1: the conversation belongs to ONE encounter — the previous answers, their "Tallennettu palveluun" markers
  // and the stale digest must not survive a switch. That reset is the mount key's job and this file applies it
  // itself, in the exported wrapper below, so no caller has to remember it. See that wrapper for why the
  // hand-written reset is gone.
  //
  // What remains here is the part that genuinely is an effect: fetching what the encounter invites. (The
  // caller's own earlier decisions are the `decisions` prop; the shell fetched them once for both rails.)
  useEffect(() => {
    let live = true;
    if (!encounterId) return undefined;
    fetch(`${api}/api/encounters/${encodeURIComponent(encounterId)}/chat/prompts`)
      .then(response => (response.ok ? response.json() : { prompts: [] }))
      .then(payload => {
        if (!live) return;
        setPrompts(payload.prompts || []);
        setLinks(payload.guideline_links || []);
      })
      .catch(() => { if (live) { setPrompts([]); setLinks([]); } });
    return () => { live = false; };
  }, [encounterId, api]);

  // The elapsed wait while a question is out. It is the client's own clock for the request, shown as wait time.
  // The clock is reset where the request starts rather than here: setting state synchronously in an effect is
  // the second render the compiler flags, and `askQuestion` is the only place `loading` becomes true.
  useEffect(() => {
    if (!loading) return undefined;
    const started = Date.now();
    const timer = setInterval(() => setWaited(Math.floor((Date.now() - started) / 1000)), 1000);
    return () => clearInterval(timer);
  }, [loading]);

  // One request per call. It takes the text as an argument so a suggestion click can ask without waiting for
  // the composer's state to land, and it leaves the composer alone. The question is shown at once, as pending,
  // and its reply replaces that entry in place when it arrives.
  async function askQuestion(text) {
    if (!text || loading || sealed) return;
    const scope = epoch.current;
    const turnId = newRequestId();
    setLoading(true);
    setWaited(0);
    setError("");
    setThread(current => [...current, { turnId, question: text, pending: true, stage: "reading", tools: [], sentences: [] }]);
    // What this conversation has shown so far: the questions and the verified sentences, as model context only.
    // The server keeps no statement text, so this is the client's transcript and never evidence.
    const history = thread.filter(entry => entry.turn).map(entry => ({
      question: entry.question,
      answer: (entry.turn.suggestions ?? []).filter(item => item.statement_class === "model_summary").map(item => item.text),
    }));
    // Progress on the waiting entry: the stage, the tools read so far, the sentences verified so far.
    const progress = patch => setThread(current => current.map(entry => (entry.turnId === turnId && entry.pending
      ? { ...entry, ...patch(entry) } : entry)));
    try {
      const response = await fetch(`${api}/api/encounters/${encodeURIComponent(encounterId)}/chat/stream`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ turn_id: turnId, question: text, digest_id: digestId, history }),
      });
      let payload;
      const contentType = response.headers?.get?.("content-type") ?? "";
      if (response.ok && contentType.includes("text/event-stream") && response.body?.getReader) {
        payload = await readTurnStream(response.body, event => {
          if (scope !== epoch.current) return;
          if (event.name === "progress") progress(() => ({ stage: event.data.state }));
          else if (event.name === "tool") progress(entry => ({ tools: [...entry.tools, event.data.name] }));
          else if (event.name === "sentence") progress(entry => ({ sentences: [...entry.sentences, event.data] }));
        });
      } else {
        payload = await response.json().catch(() => ({}));
      }
      if (!response.ok || payload?.error) {
        const detail = payload?.error?.detail ?? payload?.detail;
        const status = payload?.error?.status ?? response.status;
        const failure = new Error(detail || `${status}`);
        failure.status = status;
        failure.detail = detail;
        throw failure;
      }
      if (scope !== epoch.current) return;
      // F3: the notice is the API's, not this file's. A response that omits it is rendered with the last-resort
      // wording only when the turn itself says a model was involved.
      setThread(current => current.map(entry => (entry.turnId === turnId
        ? { turnId, question: text, turn: payload.turn, notice: payload.answer_notice ?? null } : entry)));
      setDigestId(payload.digest.digest_id);
      if (payload.guideline_links) setLinks(payload.guideline_links);
    } catch (failure) {
      if (scope !== epoch.current) return;
      setThread(current => current.filter(entry => entry.turnId !== turnId));
      if (failure.status === 409 && String(failure.detail ?? "").includes(SEAL_DETAIL)) {
        setSealed(true);
        setError("");
      } else {
        setError(`Kysymystä ei voitu käsitellä: ${failure.message}`);
      }
    } finally {
      if (scope === epoch.current) setLoading(false);
    }
  }

  function ask(event) {
    event.preventDefault();
    return askQuestion(question.trim());
  }

  // A new conversation after a seal: the sealed turns are no longer offered, so their decisions go with them.
  function restart() {
    setThread([]);
    setDigestId(null);
    setSealed(false);
    setError("");
    setDecisions({});
    setEditing("");
    setEditText("");
  }

  function transition(event) {
    const result = reviewWriteReducer(pendingReviews.current, event);
    pendingReviews.current = result.pending;
    return result.effect;
  }

  async function postReview(effect) {
    const { scope, body, bodyKey, eventId } = effect;
    const key = decisionKey(body.turn_id, body.suggestion_id);
    setDecisions(current => ({ ...current, [key]: { status: effect.status, action: body.action, body, eventId } }));
    try {
      const response = await fetch(`${api}/api/chat/reviews`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        // The review_id IS the idempotency key: the reducer minted it once for this body and a retry reuses
        // it, so a lost response followed by "retry the same save" records one decision, not two.
        body: JSON.stringify({ review_id: eventId, ...body }),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        const failure = new Error(payload.detail || `Taustapalvelu vastasi virheellä ${response.status}.`);
        failure.status = response.status;
        // Carried so the 409 that means "the caller already decided" is told from the other 409s (a stale
        // digest, a conflicting id), which are real failures.
        failure.payload = payload;
        throw failure;
      }
      transition({ type: "success", scope, bodyKey });
      setDecisions(current => ({
        ...current,
        [key]: { status: "saved", action: body.action, label: payload.label, body, eventId,
          recordedAt: payload.recorded_at },
      }));
      return true;
    } catch (failure) {
      // A missing status or a 5xx leaves the write unresolved (`ambiguous`): the same key may be retried. A
      // definite 4xx is `failed`, and a 409 that says the caller already decided is `decided`: the reducer
      // discards the key in both cases, and the rail stops offering the decision that already exists.
      const outcome = transition({
        type: "failure", scope, bodyKey, status: failure.status,
        alreadyDecided: decisionAlreadyRecorded(failure.status, failure.payload),
      });
      setDecisions(current => ({
        ...current,
        [key]: {
          status: outcome.status, action: body.action, body, eventId,
          error: failure.message || "Tallennusta ei voitu tallentaa.",
        },
      }));
      return false;
    }
  }

  function decide(turnNow, suggestion, action, editedText) {
    const body = {
      turn_id: turnNow.turn_id,
      // H1: send the digest the turn was sealed with. The server checks freshness unconditionally against
      // the stored digest, but sending it back lets the API refuse a stale thread visibly (the ask path
      // already carries it) instead of a decision silently landing on a subject that has since changed.
      digest_id: turnNow.digest_id,
      suggestion_id: suggestion.suggestion_id,
      action,
      edited_text: action === "edit" ? (editedText ?? null) : null,
    };
    const key = decisionKey(turnNow.turn_id, suggestion.suggestion_id);
    const scope = feedbackScopeKey(encounterId, key);
    const effect = transition({ type: "submit", scope, body, candidateEventId: newRequestId() });
    if (effect.type === "blocked") {
      setDecisions(current => ({
        ...current,
        [key]: {
          status: effect.status, action, body: effect.body, eventId: effect.eventId,
          error: "Aiemman tallennuksen tila on epäselvä. Yritä sama tallennus uudelleen samalla tunnisteella ennen muutosta.",
        },
      }));
      return Promise.resolve(false);
    }
    return effect.type === "post" ? postReview(effect) : Promise.resolve(false);
  }

  function retryDecide(turnNow, suggestion) {
    const key = decisionKey(turnNow.turn_id, suggestion.suggestion_id);
    const scope = feedbackScopeKey(encounterId, key);
    const effect = transition({ type: "retry", scope, previous: decisions[key] });
    return effect.type === "post" ? postReview(effect) : Promise.resolve(false);
  }

  async function submitEdit(turnNow, suggestion) {
    if (!editText.trim()) return;
    if (await decide(turnNow, suggestion, "edit", editText)) {
      setEditing("");
      setEditText("");
    }
  }

  function startEdit(turnNow, suggestion) {
    setEditing(decisionKey(turnNow.turn_id, suggestion.suggestion_id));
    setEditText(suggestion.text);
  }

  // The live map wins over the loaded one: a `decided`/`failed`/`ambiguous` outcome from this visit is
  // newer than the load. The keys carry the turn id, so a loaded decision can only ever match the turn it
  // was recorded against.
  const visibleDecisions = { ...loadedDecisions, ...decisions };

  return <ChatRail
    question={question} onQuestionChange={setQuestion} onSubmit={ask} loading={loading} error={error}
    waited={waited} thread={thread} sealed={sealed} onRestart={restart}
    prompts={prompts} links={links} recordTargets={recordTargets} onAskQuestion={askQuestion}
    decisions={visibleDecisions} editing={editing} editText={editText}
    // The chip states what the assistant is scoped to: the role and the ENCOUNTER, not the caller — the
    // caller is the signed-in user, and a scope chip that said the caller's own name would claim the
    // assistant can see a person the platform only asserted about this request. `personName` is the
    // encounter's own label, handed down by the page (ChallengeDemo's PictureBriefing passes it into this
    // element), empty when the identity is not available.
    contextRole={roleLabel} contextPerson={personName}
    identity={identity}
    onEditTextChange={setEditText}
    onDecide={decide}
    onRetryDecide={retryDecide}
    onStartEdit={startEdit}
    onCancelEdit={() => { setEditing(""); setEditText(""); }}
    onSubmitEdit={submitEdit}
  />;
}
