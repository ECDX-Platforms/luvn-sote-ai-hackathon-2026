import { Fragment, useState } from 'react';
import { X } from 'lucide-react';
import { PICTURE_MODES, briefingOrder, decisionHighlights, decisionOutcomeText, foldStatusRows, formatDecisionTime, labLabel, leadingUrgencyLabel, medicationCountText, picturePresentation, prefillReviewKey, splitMedicationRows, statusCountText } from './challenge-demo.mjs';
import { Block, Button, Field, IconButton, KeyValueList, StatusTag, Table, TableBody, TableCell, TableHeader, TableHeaderCell, TableRow, Tag, Textarea } from '../vendor/comp-lib/src/index.ts';
import './styles.css';

// Gate 1: three classes of statement have to be distinguishable **where they render**, not only in the
// table that produced them.
//
//   1. A source fact (`Lähde:`, `Valinnan peruste:`) already carries its own label and is clickable. It is
//      left alone — nothing here touches `SelectionEvidence` or `EntryMetadata`.
//   2. An *illustrative rule output* — `phq9_total >= 15` -> "vaikea masennusoireilu (PHQ-9)" — used to
//      render as a bare assertion. That is the gap this closes.
//   3. External evidence is not implemented anywhere, and this adds no source, vendor or name.
//
// The qualification is NOT recomputed here. The backend marks the rule output itself (`risk.basis`), set by
// the rule that applied a numeric cut-off, so the sentence and its caveat cannot drift apart: a frontend
// list of "known threshold rules" would be a second source of truth that goes stale the first time a rule
// is added, and it would go stale in the silent direction — a new cut-off rendering as a plain finding.
// A rule that restates a source fact (a conflict, an acute flag) or reports absence carries no `basis`, so
// it renders without the caveat: the qualification sits on the claim it is about and nowhere else.
//
// Wording is deliberately about the *system's* rule, never about the professional or their access.
export const ILLUSTRATIVE_THRESHOLD = 'illustrative_threshold';
export const ILLUSTRATIVE_NOTE = 'Havainnollistava sääntöraja, ei kliinisesti kalibroitu.';

function isIllustrativeThreshold(risk) {
  return risk?.basis === ILLUSTRATIVE_THRESHOLD;
}

const sourceHref = id => `#source-${id}`;

// A link to a record only when that record actually renders. `#source-<id>` is built in six places and the
// targets exist only for the visible-source set, so a link to a withheld, unselected or non-rendered
// record would be a dead anchor — a control that looks like evidence and goes nowhere. When the target is
// absent the label renders as plain text, which keeps the fact and drops the false affordance.
export function SourceLink({ id, targets, children }) {
  if (!id) return null;
  return targets?.has(id)
    ? <a href={sourceHref(id)}>{children ?? id}</a>
    : <span>{children ?? id}</span>;
}

// The set of rendered anchor targets, from the presentation the adapter built.
function anchorTargets(presentation) {
  return new Set(presentation.targetRecordIds ?? []);
}

// True when the grounding verifier withheld at least one generated sentence. It drives the single plain
// occurrence caption beside the summary; the per-sentence ids, reasons and verdicts stay in the technical
// disclosure, which is the only place machinery is read.
function hasWithheldSentences(presentation) {
  return (presentation.grounding?.rejected_sentences?.length ?? 0) > 0;
}

// Above this length an excerpt is collapsed behind a keyboard-accessible disclosure instead of
// rendering an unbounded wall of text.
const EXCERPT_DISCLOSURE_CHARS = 400;
const EMPTY_RECORDS = 'Ei kirjauksia.';

// One professional presentation of the existing Picture response. This component never requests
// sibling Pictures or derives permission; every displayed value is already on the returned source.
//
// The production material (the presenter's tools, the comparison, the coverage and impact cards, the Decision-1
// trace and the build stamp) is not here: it is `TechnicalView`, a destination of its own, reached from the rail
// or from the one footer line below (`onOpenTechnical`). The briefing ends at the material about the encounter.
export function PictureBriefing({ picture, mode = 'normal', personName, roleLabel, purposeLabel, exampleLabel,
  identityAvailable, lensSiblings = [], onOpenLens, proposalReviews = {}, onProposalReview, onRetryProposalReview,
  prefillReviews = {}, onPrefillReview, onRetryPrefillReview, hideHeading = false, onOpenTechnical }) {
  const presentation = picturePresentation(picture, { personName, roleLabel, purposeLabel, exampleLabel, identityAvailable });
  return <ProfessionalRecord presentation={presentation} mode={mode}
    lensSiblings={lensSiblings} onOpenLens={onOpenLens} proposalReviews={proposalReviews} onProposalReview={onProposalReview}
    onRetryProposalReview={onRetryProposalReview} prefillReviews={prefillReviews} onPrefillReview={onPrefillReview}
    onRetryPrefillReview={onRetryPrefillReview} hideHeading={hideHeading} onOpenTechnical={onOpenTechnical} />;
}

// A link to the technical page. The href is the content landmark, which is where the page renders, so the link
// is honest in a static render too; under the shell the click navigates (`onOpenTechnical`) instead.
function TechnicalLink({ onOpenTechnical, children }) {
  return <a href="#brief-content" onClick={event => {
    if (!onOpenTechnical) return;
    event.preventDefault();
    onOpenTechnical();
  }}>{children}</a>;
}

// D6: the one sentence that says the view is scoped, said the same way on every briefing. It carries no count
// and no condition on how many lenses the catalogue holds, because a withheld record is never named, counted or
// hinted at (ADR-0004): the sentence is as true for a person with one lens as for one with three.
export const LENS_SCOPE_NOTE = 'Näkymä on rajattu rooliin ja käyntisyyhyn.';

// The same person through the other lenses the catalogue carries: the permission story, shown by switching rather
// than by a count. The siblings are catalogue rows the shell already holds; nothing here fetches a sibling's picture,
// and nothing here says what the other lens would show.
function LensSwitch({ siblings, onOpenLens }) {
  if (!siblings?.length || !onOpenLens) return null;
  return <nav className="brief-lens-switch" aria-label="Näytä sama henkilö toisessa roolissa">
    <p className="t-caption">Näytä kuten:</p>
    {siblings.map(encounter => <Button key={encounter.encounter_id} size="sm" onClick={() => onOpenLens(encounter.encounter_id)}>
      {encounter.picker_label || [encounter.role_label, encounter.purpose_label].filter(Boolean).join(' · ')}
    </Button>)}
  </nav>;
}

const PREFILL_FORM_ROLE = { official: 'Virallinen lomake', related: 'Liittyvä lomake' };
const PREFILL_STATUS_LINE = 'Esitäyttö on ehdotus. Vastaukset on tarkistettu lähdekirjauksia vasten; tarkistamattomia ei näytetä.';
const ASK_AT_VISIT = 'Kysy vastaanotolla';

// The plain-text copy of the pre-filled form: the "nothing re-typed" moment. Every line is already on screen.
// An answer the professional edited is copied as edited: the copy is what they would re-type, not the proposal.
function prefilledFormText(form, reviews = {}) {
  const lines = [`${form.name} · ${PREFILL_FORM_ROLE[form.form_role] ?? form.form_role}`];
  for (const item of form.items) {
    const answer = prefilledAnswerText(form, item, reviews);
    lines.push(`${item.question_text.replace(/\s+/g, ' ').trim()}: ${item.status === 'answered' ? answer : ASK_AT_VISIT}`);
  }
  if (form.score) lines.push(`${form.score.title}: ${form.score.total} / ${form.score.max} (${form.score.basis})`);
  lines.push(PREFILL_STATUS_LINE);
  return lines.join('\n');
}

// Esitiedot: the flagship. Level 1 is the county's own heading structure, filled from the permitted records
// deterministically; level 2 the official release forms, pre-filled where the records answer them and every
// answer verified. A heading or a question nothing permitted answers reads `Kysy vastaanotolla`, never a guess.
function prefilledAnswerText(form, item, reviews = {}) {
  const review = reviews[prefillReviewKey(form.form_id, item.question_id)];
  const edited = (review?.status === 'saved' || review?.status === 'decided') && review.verdict === 'muokattu' ? review.editedText : null;
  return edited || item.answer_text;
}

export function Esitiedot({ presentation, reviews = {}, onReview, onRetry }) {
  const sections = presentation.esitiedot ?? [];
  const forms = presentation.prefilledForms ?? [];
  const targets = anchorTargets(presentation);
  if (!sections.length && !forms.length) return null;
  const answered = sections.filter(section => section.status === 'answered').length;
  return <Block title="Esitiedot" headingLevel={2} id="section-esitiedot" className="brief-data-section brief-esitiedot"
    actions={sections.length > 0 ? <span className="t-caption">{answered} / {sections.length} otsikkoa täytetty sallituista kirjauksista</span> : undefined}>
    {sections.length > 0 && <dl className="brief-esitieto-sections">
      {sections.map(section => <div key={section.key} className="brief-esitieto-section" data-status={section.status}>
        <dt className="t-body-strong">{section.heading}</dt>
        <dd>
          {section.status === 'answered'
            ? <ul>{section.items.map((item, index) => <li key={`${section.key}-${index}`}>
                <span>{item.text}</span>
                {item.temporal_status === 'history' && <Tag className="brief-record-state"> aiempi</Tag>}
                <EsitietoSources ids={item.source_record_ids} targets={targets} recordedAt={item.recorded_at} />
              </li>)}</ul>
            : <p className="t-caption brief-esitieto-open">{ASK_AT_VISIT}</p>}
        </dd>
      </div>)}
    </dl>}
    {forms.map(form => <PrefilledForm key={form.form_id} form={form} targets={targets} reviews={reviews} onReview={onReview} onRetry={onRetry} />)}
    {forms.length > 0 && <p className="t-caption brief-small">{PREFILL_STATUS_LINE}</p>}
  </Block>;
}

// The sources of one esitieto line: a link for every record the page renders, numbered when there are several;
// a record the page does not render (permitted but not selected into the briefing) is not a dead link and not a
// bare word -- the line keeps its date instead, which is the fact the reader can still use.
function EsitietoSources({ ids = [], targets, recordedAt = null }) {
  const linked = ids.filter(id => targets?.has(id));
  if (!linked.length) {
    return recordedAt ? <span className="t-caption brief-esitieto-sources">{' · '}kirjattu {formatDate(recordedAt)}</span> : null;
  }
  return <span className="t-caption brief-esitieto-sources">{' · '}
    {linked.map((id, position) => <Fragment key={id}>{position ? ', ' : ''}
      <SourceLink id={id} targets={targets}>{linked.length > 1 ? `Lähde ${position + 1}` : 'Lähde'}</SourceLink></Fragment>)}
  </span>;
}

function PrefilledForm({ form, targets, reviews = {}, onReview, onRetry }) {
  const [copied, setCopied] = useState(false);
  const answered = form.items.filter(item => item.status === 'answered');
  const open = form.items.filter(item => item.status !== 'answered');
  const categories = [...new Set(form.items.map(item => item.category_name ?? ''))];
  const generationNote = {
    unavailable: 'Esitäyttöä ei ajettu: mallia ei ole käytössä.',
    failed: 'Esitäyttö ei onnistunut tällä kertaa.',
    timed_out: 'Esitäyttö ei ehtinyt valmistua.',
  }[form.generation_status];
  async function copy() {
    try {
      await navigator.clipboard?.writeText(prefilledFormText(form, reviews));
      setCopied(true);
    } catch { /* the text is on screen */ }
  }
  return <Block headingLevel={3} className="brief-prefilled-form"
    title={<>{form.name} <Tag>{PREFILL_FORM_ROLE[form.form_role] ?? form.form_role}</Tag></>}
    actions={<span className="t-caption">{form.verified} / {form.total_questions} esitäytetty</span>}>
    {form.relation_note && <p className="t-caption brief-small">{form.relation_note}</p>}
    {generationNote && <p className="t-caption brief-small">{generationNote}</p>}
    {answered.length > 0 && categories.map(category => {
      const rows = answered.filter(item => (item.category_name ?? '') === category);
      if (!rows.length) return null;
      return <div key={category || 'none'} className="brief-prefilled-group">
        {category && <h4 className="t-overline">{category}</h4>}
        <dl>{rows.map(item => <div key={item.question_id} className="brief-prefilled-item">
          <dt className="t-caption">{item.question_text}</dt>
          <dd><span className="t-body-strong">{prefilledAnswerText(form, item, reviews)}</span>
            {item.historical && <Tag className="brief-record-state brief-state-draft"> Tarkista: aiempi kirjaus</Tag>}
            <EsitietoSources ids={item.source_record_ids} targets={targets} />
            {onReview && <PrefillAnswerReview form={form} item={item}
              review={reviews[prefillReviewKey(form.form_id, item.question_id)]} onReview={onReview} onRetry={onRetry} />}
          </dd>
        </div>)}</dl>
      </div>;
    })}
    {form.score && <p className="brief-prefilled-score"><strong>{form.score.title}:</strong> {form.score.total} / {form.score.max}
      {form.score.summary ? ` · ${form.score.summary}` : ''}<span className="t-caption"> · {form.score.basis}</span></p>}
    {open.length > 0 && <details className="t-caption brief-prefilled-open">
      <summary className="t-control">{ASK_AT_VISIT}: {open.length} kysymystä</summary>
      <ul>{open.map(item => <li key={item.question_id}>{item.question_text}</li>)}</ul>
    </details>}
    <div className="brief-button-row">
      <Button size="sm" onClick={copy}>Kopioi esitäytetty lomake</Button>
      {copied && <p className="t-caption brief-inline-status" role="status">Kopioitu leikepöydälle.</p>}
      {form.source_file && <p className="t-caption brief-small">Lähde: {form.source} · {form.source_file}{form.source_sha256 ? ` · sha256 ${form.source_sha256.slice(0, 12)}` : ''}</p>}
    </div>
  </Block>;
}

// The verdicts in the past tense the recorded state needs, one per literal the write route accepts. `hylätty`
// is not offered here — the form is a proposal and an answer the professional does not confirm stays a
// proposal — but a loaded verdict is rendered as what it is.
const PREFILL_VERDICT_TAGS = {
  vahvistettu: { status: 'success', label: 'Vahvistettu' },
  muokattu: { status: 'info', label: 'Muokattu' },
  hylätty: { status: 'warning', label: 'Hylätty' },
};

// Accept / edit on one pre-filled answer. A recorded verdict (saved on this visit, loaded from the caller's own
// verdicts, or reported by the server as already recorded) is an outcome, not an offer: it renders its tag and
// no controls. Nothing here writes to the form or the record the answer was filled from; the verdict is stored
// beside the proposal, under the actor who made it.
function PrefillAnswerReview({ form, item, review, onReview, onRetry }) {
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(item.answer_text ?? '');
  const recorded = review?.status === 'saved' || review?.status === 'decided';
  const disabled = ['saving', 'ambiguous'].includes(review?.status);
  const disabledReason = review?.status === 'saving' ? 'Tallennetaan…'
    : review?.status === 'ambiguous' ? 'Tallennuksen tila on epäselvä. Yritä samaa tallennusta uudelleen.'
      : undefined;
  const failureId = `prefill-error-${form.form_id}-${item.question_id}`;
  const describedBy = review?.status === 'failed' ? failureId : undefined;
  const tag = recorded ? PREFILL_VERDICT_TAGS[review.verdict] : null;

  async function submit(event) {
    event.preventDefault();
    if (!text.trim()) return;
    const saved = await onReview(form, item, 'muokattu', text);
    if (saved) setEditing(false);
  }

  return <div className="brief-prefilled-review">
    {tag && <p className="t-caption brief-inline-status" role="status">
      <StatusTag status={tag.status}>{tag.label}</StatusTag>
      {review.recordedAt && <> <time dateTime={review.recordedAt}>{formatDecisionTime(review.recordedAt)}</time></>}
    </p>}
    {!recorded && <div className="brief-button-row">
      <Button size="sm" disabled={disabled} title={disabled ? disabledReason : undefined} aria-describedby={describedBy}
        onClick={() => setEditing(value => !value)}>Muokkaa</Button>
      <Button size="sm" variant="primary" disabled={disabled} title={disabled ? disabledReason : undefined}
        aria-describedby={describedBy} loading={review?.status === 'saving'}
        onClick={() => onReview(form, item, 'vahvistettu')}>Vahvista</Button>
    </div>}
    {!recorded && editing && <form onSubmit={submit}>
      <Field label="Muokkaa vastausta"
        error={review?.status === 'failed' ? `Palvelu hylkäsi tallennuksen: ${review.error}` : undefined}>
        <Textarea id={`prefill-edit-${form.form_id}-${item.question_id}`}
          required maxLength={2000} value={text} onChange={event => setText(event.target.value)} />
      </Field>
      <Button type="submit" size="sm" variant="primary" disabled={disabled || !text.trim()}
        title={!text.trim() ? 'Kirjoita ensin muokattu teksti.' : disabled ? disabledReason : undefined}
        loading={review?.status === 'saving'}>Tallenna muokkaus</Button>
    </form>}
    {review?.status === 'saving' && <p className="t-caption brief-inline-status brief-transient-status" role="status">Tallennetaan…</p>}
    {review?.status === 'ambiguous' && <div className="t-caption brief-inline-status brief-transient-status" role="alert">
      <p>Tallennuksen tila on epäselvä: {review.error} Tee sama tallennus uudelleen samalla tapahtumatunnisteella ennen muutosta.</p>
      {onRetry && <Button size="sm" variant="primary" onClick={() => onRetry(form, item)}>Yritä samaa tallennusta uudelleen</Button>}
    </div>}
    {review?.status === 'failed' && <p className="t-caption brief-inline-status brief-transient-status" role="alert" id={failureId}>Palvelu hylkäsi tallennuksen: {review.error}</p>}
  </div>;
}

// The source-backed summary: the one prose block on the page, and the first thing a professional reads after the
// context. It renders nothing when the narration was not accepted, so the page never shows unsupported text.
function PreparationOverview({ presentation }) {
  return <>
  {presentation.summary && <Block title="Lähteisiin perustuva kooste" headingLevel={2} className="brief-narrative">
    <p>{presentation.summary}</p>
    <p className="t-caption">Koosteen teksti on koneellisesti tuotettu (tekoäly) vain sallituista lähteistä, ja jokainen lause on tarkistettu lähdeviitteisiinsä — tukematon teksti jää näyttämättä. Ammattilainen tekee päätökset.</p>
    {/* The one place the reader is told that something was withheld, and why. The wording is chosen so it
        cannot be misread: "jätettiin näyttämättä" (left unshown), never "piilotettiin" (hidden) — nothing
        is concealed, the verifier declined to publish text it could not confirm. It is agentless
        ("ei voitu vahvistaa"), so it does not imply the model refused or failed. It carries no sentence
        id and no count, because those are machinery the professional cannot act on; the per-sentence
        detail (id, reason, verdict) stays on the technical page where it is auditable.
        This states the occurrence ("this summary had text withheld, because the sources did not confirm
        it"); the caption above states the method and the general rule. They are separate claims, so the
        reader is not told the same thing twice — and the method caption still renders when nothing was
        withheld. */}
    {hasWithheldSentences(presentation) && <p className="t-caption">Osa tämän koosteen koneellisesti tuotetusta tekstistä jätettiin näyttämättä, koska sitä ei voitu vahvistaa lähteisiin.</p>}
  </Block>}
  </>;
}

const DEFINITION_FIELDS = [
  ['sukupuoli', 'Sukupuoli'], ['syntymävuosi', 'Syntymävuosi'], ['äidinkieli', 'Äidinkieli'],
  ['kotikunta', 'Kotikunta'], ['asumismuoto', 'Asumismuoto'],
];

function ProfessionalRecord({ presentation, mode,
  lensSiblings, onOpenLens, proposalReviews, onProposalReview, onRetryProposalReview,
  prefillReviews, onPrefillReview, onRetryPrefillReview, hideHeading, onOpenTechnical }) {
  const sources = presentation.sources ?? [];
  const anchorOwner = anchorOwners(sources);
  const { narration } = presentation;
  const basics = sources.find(source => source.type === 'perustieto' && source.topic === 'demografia');
  // The side column takes the medications card; the reading column takes the rest, in `briefingOrder`'s order.
  // Both lists are filtered from `briefingOrder`, so no card renders twice and none is invented for a purpose whose
  // order leaves it out.
  const order = briefingOrder(presentation.purpose);
  const sideIds = order.filter(id => id === SIDE_RECORD_ID);
  const readingIds = order.filter(id => id !== SIDE_RECORD_ID);
  // The reading column is stacked and always visible: the definition grid, then the record cards. The audit
  // cards (coverage and impact) moved to the technical page (`TechnicalView`; work order D, 2026-10-12, then B2), because
  // they describe how the summary was produced rather than the person, so the reading path ends at the
  // encounter material. This narrows the "all sections visible" decision; it does not reverse it. They were the library's `Tabs` (issue 03) with a
  // class switching which one was `display: none`; the owner reversed that here, because a tab strip is a
  // picker of content and the brief's promise is "valmiina ilman turhaa klikkailua". What the Tabs version
  // claimed — "every section is present without any interaction" — was true of the DOM and false of the
  // screen: the alerts panel, the medications, the proposals and the rule-based assessment each needed one
  // click, and the professional's most decision-relevant card was behind it.
  //
  // Two consequences worth stating, because the previous round's reasoning depended on them:
  //  * The `#section-*` ids are on the section CARDS (`DefinitionGrid`, `SelectedRecords`,
  //    `SelectionCoverage`, `ImpactEstimate`, on the technical page), not on these wrappers, so every deep link and the in-page
  //    "Siirry kattavuusriveihin" anchor work exactly as before — the browser scrolls instead of the app
  //    selecting a tab, and `.brief-app :target { scroll-margin-top }` already handles the offset.
  //  * There is no `activeSection` state, no hash listener and no `hidden` attribute anywhere: nothing is
  //    removed from the tree or from the screen, so there is no state that can disagree with the address.
  return <>
    <EncounterContext presentation={presentation} />
    {!hideHeading && <header className="brief-heading">
      <p className="t-overline brief-eyebrow">Synteettinen palvelutilanne</p>
      <h1 className="t-page-title">{presentation.identityAvailable ? presentation.personName : 'Henkilöllisyystieto ei saatavilla'}</h1>
      <p>{[presentation.roleLabel, presentation.purposeLabel].filter(Boolean).join(' · ')}</p>
    </header>}
    <PatientPills sources={sources} presentation={presentation} />
    <p className="t-caption brief-lens-scope">{LENS_SCOPE_NOTE}</p>
    <LensSwitch siblings={lensSiblings} onOpenLens={onOpenLens} />
    <PreparationOverview presentation={presentation} />
    {/* Two columns inside the record section. The previous round put attention first in the reading column; this
        restores the earlier column assignment on the owner's instruction, after the placement was measured (a 360px
        side column holding one 155px card beside a 2164px row, 93% empty). The trade is deliberate: attention now
        follows the reading column in document order. The side column carries the attention panel, the medications
        card and the rule-based assessment; the reading column carries the purpose's remaining record cards in
        `briefingOrder`. The columns collapse to one at 1280px, reading column first. The placement is fixed here
        rather than derived from `briefingOrder`, which orders cards and does not place them.
        The assistant is no longer a card in this grid: it is the shell's side pane (`AssistantPane`, night plan
        N1), present on every destination. What used to be the rail is the proposal alone, and it leads the body at
        full width rather than leaving a 360px column standing beside the sections. */}
    <div className="brief-body">
    <ProfessionalTools presentation={presentation}
      proposalReviews={proposalReviews} onProposalReview={onProposalReview} onRetryProposalReview={onRetryProposalReview} />
    <div className="brief-sections">
      <section className="brief-section">
        <DefinitionGrid source={basics} />
      </section>
      <section className="brief-section">
        <Esitiedot presentation={presentation} reviews={prefillReviews} onReview={onPrefillReview} onRetry={onRetryPrefillReview} />
      </section>
      <section className="brief-section">
        <div className="brief-record-columns">
          <section className="brief-record-main">
            <SourceEvidence ids={readingIds} presentation={presentation} sources={sources} narration={narration} anchorOwner={anchorOwner} />
          </section>
          <aside className="brief-record-side" aria-label="Ammattilaisen tilanne ja tarkistettavat asiat">
            <AttentionItems presentation={presentation} mode={mode} id="section-alerts" onOpenTechnical={onOpenTechnical} />
            <SourceEvidence ids={sideIds} presentation={presentation} sources={sources} narration={narration} anchorOwner={anchorOwner} />
            {/* The general row (`yleinen`) matched nothing about this person: its one step is "keep following",
                which beside a briefing reads as a verdict with no basis, so the block is shown only for a
                situation the tables actually keyed (owner, 2026-10-14). */}
            {presentation.situation?.situation_type !== 'yleinen' &&
            <Block title="Sääntöpohjainen arvio" headingLevel={2} id="section-decision" className="brief-data-section">
              <p>Kirjauksista johdettu triage-signaali (ei palvelutilanne): {presentation.situation?.situation_type ?? 'Ei arvioitavissa'}</p>
              {presentation.actions.length > 0 && <ul>{presentation.actions.map((action, index) => <li key={`${action}-${index}`}>{action}</li>)}</ul>}
            </Block>}
          </aside>
        </div>
      </section>
    </div>
    </div>
    {/* The page ends at the material about the encounter. Everything about how the summary was produced (the
        presenter's tools, the comparison, the coverage and impact cards, the Decision-1 trace, the build stamp)
        is `TechnicalView`, a destination of its own; this one line says the boundary and links to it. The
        href is the content landmark, which is where the technical page renders, so the link is honest even
        where no shell intercepts it (a static render). */}
    <p className="t-caption brief-footer">Tekoäly järjesti ja muotoili; se ei päättänyt.{' '}
      <TechnicalLink onOpenTechnical={onOpenTechnical}>Tekniset tiedot</TechnicalLink>
    </p>
  </>;
}

// The record cards the briefing draws, by id. Each takes the same props and reads only what it needs, so the
// order is decided by `briefingOrder` and this map only names what each id renders.
const RECORD_CARDS = {
  assessments: RelevantAssessments,
  measurements: MeasurementTiles,
  labs: LabResultsTable,
  status: RecordStatusTable,
  medications: MedicationTable,
  records: SelectedRecords,
  forms: FormOverview,
};
// The one card the side column takes (see `ProfessionalRecord`). Named once so the split cannot drift from the id
// `briefingOrder` returns for it.
const SIDE_RECORD_ID = 'medications';

// Renders the given record cards in the order given, which is always a slice of `briefingOrder`. The cards and
// their `#section-*` anchors are unchanged; only which column each sits in is decided by the caller.
function SourceEvidence({ ids, presentation, sources, narration, anchorOwner }) {
  return <>{ids.map(id => {
    const Card = RECORD_CARDS[id];
    return <Card key={id} presentation={presentation} sources={sources} narration={narration} anchorOwner={anchorOwner} />;
  })}</>;
}

// The professional's own tools: the proposal awaiting a decision. It used to share this section with the assistant
// panel, which is the shell's side pane now (`AssistantPane`); the section keeps its name and its place at the head
// of the body, not the reading column.
function ProfessionalTools({ presentation, proposalReviews, onProposalReview, onRetryProposalReview }) {
  return <section className="brief-rail-section" aria-label="Ammattilaisen työkalut">
    <Proposal presentation={presentation} proposalReviews={proposalReviews}
      onProposalReview={onProposalReview} onRetryProposalReview={onRetryProposalReview} />
  </section>;
}

// The element id the shell's toggles point at (`aria-controls`), and the pane itself.
export const ASSISTANT_PANE_ID = 'brief-assistant';

// The assistant as a Copilot-style side pane (night plan N1): docked to the right edge of the shell at 1024px and
// up, a full-height sheet over the content below it (CSS in styles.css), on every destination. It is a
// complementary landmark of its own, so it sits beside `main` rather than inside it. The header row carries the
// title, the scope chip and the close control; the body is `ChatPanel` unchanged (its identity row switched off,
// because this header names the surface), or a one-line note when no encounter is open.
//
// Closed, the pane is rendered with the `hidden` attribute, not moved off-screen: axe and the tab order see what
// the professional sees. It stays mounted so the conversation survives a close and re-open. The shell owns the
// open state (the address's `assistant=1`), the focus moves and the Escape key; this component only renders.
// The heading takes the focus on open (`headingRef`, tabIndex -1), not the close control: the library's
// IconButton shows its tooltip on focus and that tooltip takes the first Escape for itself, so focusing the
// control would make the pane need two presses to close.
//
// The chip is the role label and the encounter's identity header — the same two strings the briefing prints —
// never a demo label, which is not an identity (ADR-0004).
export function AssistantPane({ open, onClose, onKeyDown, roleLabel = '', personLabel = '', chat = null, headingRef }) {
  const scope = [roleLabel, personLabel].filter(Boolean).join(' · ');
  return <aside id={ASSISTANT_PANE_ID} className="brief-assistant-pane" role="complementary" aria-label="LUVN-avustaja"
    hidden={!open} onKeyDown={onKeyDown}>
    <div className="brief-assistant-head">
      <h2 className="t-title-sm brief-assistant-title" tabIndex={-1} ref={headingRef}>LUVN-avustaja</h2>
      {scope ? <p className="t-caption brief-assistant-chip"
        title="Avustaja näkee vain tämän roolin ja tämän palvelutilanteen tiedot.">{scope}</p> : null}
      <span className="brief-assistant-close">
        <IconButton label="Sulje avustaja" icon={<X />} onClick={onClose} />
      </span>
    </div>
    {chat ?? <p className="t-body brief-assistant-empty">Avaa palvelutilanne, niin avustaja voi vastata.</p>}
  </aside>;
}

// The definition grid is the library's `KeyValueList`.
//
// This reverses the "not adopted" half of issue 04, and the reversal is *conditional*: with
// `.brief-app .brief-definition dl` (0,2,1) still in the app stylesheet, the component's `.list` (0,1,0)
// lost the grid and the library's responsive 6/3/2 shape (`KeyValueList.module.css:12-13`) was dead while
// every gate stayed green — the app's own 6/3/2 at different breakpoints looked close enough to pass a
// screenshot. Those rules are deleted in the same commit. If they come back, this component silently stops
// being the design authority again, which is why the reason is written here and not only in the tracker.
//
// `EntryMetadata` stays hand-built. That is the *other* half of issue 04's reason and it still holds: it is a
// different anatomy — label and value on one line, wrapping — that the component has no slot for.
//
// The vendored revision has no `columns` prop; the revision the reference bundle was built from did
// (`.scratch/ui-ux-upgrade/issues/01-vendor-the-reference.md`). That decision was to proceed on the vendored
// revision and **not** hand-patch the vendored copy (ADR-0016), so the grid is the component's own and the
// app no longer re-scopes it at 850px or 540px.
function DefinitionGrid({ source }) {
  const payload = source?.payload ?? {};
  const fields = DEFINITION_FIELDS.filter(([key]) => payload[key] !== undefined);
  // No identity record in this lens (the social-care lens, where it is withheld by policy): no empty card, and
  // nothing that would say why -- the absence is cause-neutral (ADR-0004).
  if (!source) return null;
  return <Block title="Perustiedot" headingLevel={2} id="section-basics">
    {fields.length > 0
      ? <KeyValueList items={fields.map(([key, label]) => ({ label, value: payload[key] }))} />
      : <EmptyPanel />}
    <EntryMetadata source={source} />
  </Block>;
}

// Binding status-colour table (issue 05): only deterministic high/moderate risk, Decision-1 coverage gaps,
// due forms, Decision-1 addition/emphasis, abstention, and out-of-generation omission get c/w/i/s tokens.
// Conflicts, fail-closed, absence, impacts, and lens switching remain neutral; excluded records never feed it.
// NEW: the alerts panel is not in the styleguide. Colour is meaning here — only deterministic high or
// moderate risk, coverage gaps, due forms, Decision-1 additions and out-of-generation omissions carry a status
// token; conflicts, fail-closed, absence and lens switching stay neutral.
//
// One card, stacked severity rows: every row is the same width and carries the same anatomy — icon,
// severity word, fact, then the action — so the panel reads as a severity list rather than a grid of
// unrelated boxes. A row with no binding status token (a conflict, fail-closed) is neutral: it keeps the
// row shape and the equal width, and carries no tint, because colour is only ever meaning here.
const SEVERITY_WORD = { critical: 'Kriittinen', warning: 'Varoitus', info: 'Tieto', success: 'Valmis' };

// Every `risk_level` the data actually carries, given a presentation word. Only `korkea` and `kohtalainen`
// have a severity tone and therefore an icon; the other two are neutral rows that must still read as
// sentences - `matala` and `arvioimaton` were rendering as bare lowercase values leading a line.
//
// `arvioimaton` is deliberately worded as a statement of absence rather than a level. The assessment is
// missing, not low, and the register's rule for an absent value is to say so plainly instead of implying
// one. It is also why the row carries no severity colour: an unassessed risk is not a low risk.
const RISK_WORD = {
  arvioimaton: 'Riskiä ei ole arvioitu',
  matala: 'Matala riski',
  kohtalainen: SEVERITY_WORD.warning,
  korkea: SEVERITY_WORD.critical,
  // A source conflict is not a severity finding: the rule restates a disagreement between
  // sources, not a judgement about the patient, so it carries no severity word and (via the
  // `riskTone` map below, which this level is deliberately absent from) no critical styling.
  ristiriita: 'Lähteiden ristiriita',
};

// Attention items: the findings the professional must check, with their source links. The markup and
// behaviour are the former `AlertPanel`'s; only the name and the place in the side column changed.
function AttentionItems({ presentation, mode, id, onOpenTechnical }) {
  const attention = [];
  const selection = presentation.selection;
  const conflicts = presentation.conflicts ?? [];
  const riskLevel = presentation.risk?.risk_level;
  const riskTone = ({ korkea: 'critical', kohtalainen: 'warning' })[riskLevel];
  const riskWord = RISK_WORD[riskLevel] ?? SEVERITY_WORD[riskTone] ?? 'Riskitaso';
  // A `ristiriita` risk with conflicts is the same finding as the conflict rows below, which hold both source
  // links and the review prompt. The patient-level row is therefore not rendered as well. This is decided from
  // the structured level and the conflicts array, never by comparing rendered text.
  const riskIsConflictRow = riskLevel === 'ristiriita' && conflicts.length > 0;
  const hasCoverageGap = ['decision_not_shortlisted_count', 'decision_budget_exhausted_count', 'decision_too_long_count', 'decision_unavailable_count']
    .some(key => (selection?.[key] ?? 0) > 0);
  if (hasCoverageGap) attention.push({
    tone: 'warning', severity: SEVERITY_WORD.warning, key: 'coverage-gap',
    fact: 'Valinnan kattavuus jäi vajaaksi; näytetyt tulosrivit näkyvät jäljitettävyydessä.',
    // The coverage rows are on the technical page (B2), so this is a page link, not a hash to a target the
    // briefing does not carry.
    act: <TechnicalLink onOpenTechnical={onOpenTechnical}>Siirry kattavuusriveihin (Tekniset tiedot).</TechnicalLink>,
  });
  // Gate 8: the beat the challenge's *"tietoa jää huomaamatta"* asks for, said as which source claims
  // what rather than as "two sources differ". The direction is fixed by construction:
  // `triage._find_conflicts` pairs an ACTIVE medication with a free-text note that says it was stopped, so
  // this sentence is true of every conflict this code can produce. Both records are linked, so the reader
  // checks the disagreement at its sources instead of taking the row's word for it.
  // A conflict is a disagreement, not a severity, so it has no tone and renders as a neutral row.
  for (const conflict of conflicts) attention.push({
    key: `conflict-${conflict.medication_record_id}-${conflict.note_record_id}`,
    heading: 'Lähteiden ristiriita',
    fact: <>{conflict.drug ? `${conflict.drug}: ` : ''}lääkelista näyttää lääkityksen aktiivisena, mutta
      kirjaus kertoo sen lopettamisesta.{' '}
      <SourceLink id={conflict.medication_record_id} targets={anchorTargets(presentation)}>Lääkelista</SourceLink> ·{' '}
      <SourceLink id={conflict.note_record_id} targets={anchorTargets(presentation)}>Kirjaus</SourceLink></>,
    act: 'Tarkista, kumpi lähde on ajantasainen.',
  });
  // Decision-1's own findings, as blue information rows: what it added beyond the baseline, what it put first,
  // what it called relevant and current. Each links to the record it is about; nothing here is a care claim.
  for (const item of decisionHighlights(presentation)) attention.push({
    tone: 'info', severity: 'Decision-1', key: `decision-${item.kind}-${item.id}`,
    fact: <>{item.text}: <SourceLink id={item.id} targets={anchorTargets(presentation)}>{item.title}</SourceLink>.</>,
  });
  for (const entry of presentation.formCatalogue ?? []) if (entry.status === 'due') attention.push({
    tone: 'warning', severity: SEVERITY_WORD.warning, key: `due-${entry.form_type}`,
    fact: <>{entry.form_type} · Tila · due{entry.due_at ? ` · ${formatDate(entry.due_at)}` : ''}
      {(entry.record_ids ?? []).map((id, index) => <Fragment key={id}> · <SourceLink id={id} targets={anchorTargets(presentation)}>Lähde {index + 1}</SourceLink></Fragment>)}</>,
    act: entry.suggested_next_step ?? 'Tarkista kirjauksen ajantasaisuus.',
  });
  // Withheld generated sentences deliberately do NOT become rows here. The panel is the professional's
  // list of things needing attention, and "Lause s3 pidätettiin" is neither about the person nor
  // actionable: it names an internal sentence id and describes the generation pipeline. One row per
  // withheld sentence also inflated the attention count and buried the real conflicts. The fact is stated
  // once, in the professional's terms, beside the summary; the per-sentence machinery (id, reason,
  // verdict) is on the technical page.
  if (mode === 'foundry-off' || (!presentation.summary && presentation.narration.acceptanceStatus !== 'accepted')) attention.push({
    key: 'fail-closed', heading: 'Suljettu vikatilassa', fact: 'Koostetta ei näytetä; valitut lähdetiedot ja sääntöpohjainen arvio säilyvät.',
  });
  // The patient-level risk row. It is in the attention group only when its level has a severity tone, and
  // in the status group otherwise (`matala`, `arvioimaton`, or `ristiriita` without conflicts).
  const riskRow = presentation.risk && !riskIsConflictRow
    ? <div className={`brief-risk brief-alert-row${riskTone ? ` brief-${riskTone}` : ''}`} data-severity={riskTone} key="risk">
      {riskTone && <SeverityIcon tone={riskTone} />}
      <div className="brief-alert-copy"><p><strong>{riskWord}.</strong> {presentation.risk.reason}
        {/* The qualification is IN the sentence, not a caption under it. Two gates decided that, and both
            were right: a `.t-caption` here bakes `color: var(--color-text-muted)`, which out-specifies the
            status foreground and would strip the tint off the very row it is qualifying (the a11y gate's
            status-subtree rule), and a separate paragraph reads as a second, independent claim rather than
            as a remark on this one. Inline, it inherits the row's own colour and is read with the finding. */}
        {isIllustrativeThreshold(presentation.risk) && ` — ${ILLUSTRATIVE_NOTE}`}</p>
        {riskTone && presentation.actions[0] && <p className="brief-alert-act">{presentation.actions[0]}</p>}
      </div>
    </div>
    : null;
  const alertRow = alert => <div className={`${alert.tone ? `brief-severity brief-${alert.tone}` : 'brief-alert-neutral'} brief-alert-row`} key={alert.key}>
    {alert.tone && <SeverityIcon tone={alert.tone} />}
    <div className="brief-alert-copy">{alert.tone
      ? <><p><strong>{alert.severity}.</strong> {alert.fact}</p>{alert.act && <p className="brief-alert-act">{alert.act}</p>}</>
      : <><p><strong>{alert.heading}</strong> {alert.fact}</p>{alert.act && <p className="brief-alert-act">{alert.act}</p>}</>}
    </div>
  </div>;
  // The attention count is the number of rows in the attention group and nothing else. A neutral status row
  // is not a thing to check, so it is not counted, and a source conflict is counted as one row, not as an
  // alert.
  const attentionRows = [...attention.map(alertRow), riskTone && riskRow].filter(Boolean);
  const statusRow = riskTone ? null : riskRow;
  return <Block title="Tilanne ja hälytykset" headingLevel={2} id={id}>
    <div className="brief-alert-group">
      <h3 className="t-title-sm">Tarkistettavat</h3>
      {attentionRows.length > 0 && <p className="t-caption">{attentionRows.length} kohdetta</p>}
      <div className="brief-alert-stack">{attentionRows}</div>
    </div>
    {statusRow && <div className="brief-alert-group">
      <h3 className="t-title-sm">Tilanne</h3>
      <div className="brief-alert-stack">{statusRow}</div>
    </div>}
  </Block>;
}

const STATUS_ICON_SHAPES = {
  critical: <><circle cx="12" cy="12" r="9" /><path d="M12 7v6m0 3v.5" /></>,
  warning: <><path d="M12 3 22 21H2z" /><path d="M12 10v5m0 3v.5" /></>,
  info: <><circle cx="12" cy="12" r="9" /><path d="M12 11v6m0-9v.5" /></>,
  success: <><circle cx="12" cy="12" r="9" /><path d="m8 12.5 3 3 5-6" /></>,
};

function SeverityIcon({ tone }) {
  return <svg className="icon" aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round">
    {STATUS_ICON_SHAPES[tone] ?? STATUS_ICON_SHAPES.info}
  </svg>;
}

function hasSourceDetails(source, narration) {
  const recipients = Array.isArray(source.vastaanottajat) && source.vastaanottajat.some(value => typeof value === 'string' && value.trim());
  const author = source.payload?.kirjaaja || source.authoredByRole;
  const outOfGeneration = narration?.generationStatus === 'generated' && !narration.inputRecordIds.includes(source.id);
  return Boolean(source.state || source.käyntiaika || recipients || source.erikoisala || source.suorituspaikka
    || author || source.responsibleClinician || source.responsibleApproverLabel || source.encounterId || source.caseId
    || source.selectionReasonTexts?.length || source.decisionOutcome || outOfGeneration);
}

// The medications table sits in the side column, beside the attention panel and the rule-based assessment. It is three columns rather than
// four because the card keeps the dose and status readable at the narrower widths the column takes below
// 1280px, and every per-row provenance line — `Valinnan peruste`, the missed-dose count, the record state —
// sits on its own full-width line inside the row. Those lines wrap; they are never truncated and never
// squeezed into a column.
function MedicationTable({ sources, narration, anchorOwner }) {
  const rows = sources.filter(source => source.type === 'lääkitys');
  // N3b: current medications first; ended ones fold behind a disclosure. The fold is a rendering choice over
  // the rows the table already has — every row is a selected, permitted source, and the counts are of those.
  const { active, historical } = splitMedicationRows(rows);
  const [showHistorical, setShowHistorical] = useState(false);
  const historyId = 'section-medications-history';
  if (!rows.length) return null;
  const medicationRow = row => <Fragment key={row.id}>
    <TableRow showArrow={false} id={anchorOwner?.get(row.id) === 'medications' ? `source-${row.id}` : undefined}>
      <th scope="row" className="t-body-strong">{row.payload.lääke}</th>
      <TableCell>{row.payload.annos}</TableCell>
      <TableCell>{row.payload.tila}</TableCell>
    </TableRow>
    {/* `colSpan` is not optional. Without it this cell claims a FOURTH column, so the browser lays the
        table out with one more column than the header has and gives the surplus width to the metadata
        rather than to the row header — which squeezed the drug name to 24px and set it one character per
        line ("l e v o t y r o k s i i n i") on the live page. The other three metadata rows all span; this
        one was missed, and the shape of the bug is why it survived: the table still rendered, and only the
        widest cell (a long dosage) drove the squeeze, so a short sample looked fine. */}
    {hasSourceDetails(row, narration) && <TableRow showArrow={false} className="brief-source-metadata"><TableCell colSpan={3}>
      <RecordStateBadge state={row.state} /><EntryMetadata source={row} />
      <SelectionEvidence source={row} narration={narration} />
      {row.payload.puuttuneet_annokset_30d !== undefined
        && <p className="brief-medication-doses"><strong>Puuttuneet annokset / 30 vrk:</strong> {row.payload.puuttuneet_annokset_30d}</p>}
    </TableCell></TableRow>}
  </Fragment>;
  return <Block title="Lääkitykset" headingLevel={2} id="section-medications" className="brief-data-section"
    actions={rows.length > 0 ? <span className="t-caption">{medicationCountText({ active, historical })}</span> : undefined}>
    {!rows.length ? <EmptyPanel /> : <Table label="Lääkitysten taulukko">
      <TableHeader>
        <TableHeaderCell>Lääke</TableHeaderCell>
        <TableHeaderCell>Annos</TableHeaderCell>
        <TableHeaderCell>Tila</TableHeaderCell>
      </TableHeader>
      <TableBody>{active.map(medicationRow)}</TableBody>
      {/* The ended medications keep their own body, which is what the toggle controls. Collapsed, the body
          holds the toggle and a visually-hidden list of the folded records' `source-<id>` anchors, so a citation
          in the summary that points at an ended medication still has a target in the DOM; expanded, the rows. */}
      {historical.length > 0 && <TableBody id={historyId}>
        <TableRow showArrow={false}><TableCell colSpan={3}>
          {/* The library's `ShowAllButton` hard-codes its English label ("Show all N" / "Show fewer"), so the
              disclosure is the library `Button` carrying the same `aria-expanded` / `aria-controls` contract
              and the Finnish label the page needs. */}
          <Button size="sm" aria-expanded={showHistorical} aria-controls={historyId}
            onClick={() => setShowHistorical(open => !open)}>
            {showHistorical ? 'Piilota aiemmat' : `Näytä aiemmat (${historical.length})`}
          </Button>
          {!showHistorical && <FoldedAnchors rows={historical} owner="medications" anchorOwner={anchorOwner} />}
        </TableCell></TableRow>
        {showHistorical && historical.map(medicationRow)}
      </TableBody>}
    </Table>}
  </Block>;
}

// The `source-<id>` anchors of records a fold does not draw as rows of their own. They stay in the DOM so a
// citation link still lands on the table that owns the record; each item names the record's date, which is
// what a screen reader announces when a link lands here. Ownership is the same rule as for a drawn row: the
// table that `anchorOwners` assigned the record to emits its anchor, and no other.
function FoldedAnchors({ rows, owner, anchorOwner }) {
  const owned = rows.filter(row => anchorOwner?.get(row.id) === owner);
  if (!owned.length) return null;
  return <ul className="visually-hidden">{owned.map(row => <li key={row.id} id={`source-${row.id}`}>{formatDate(row.date)}</li>)}</ul>;
}

// A metric tile leads with its reading and puts the label beneath it, the way the reference workspace
// does: the number is what the clinician scans for, the label only names it. The label is emitted once —
// under the first reading and above that reading's provenance lines — so a tile that also carries older
// readings stays labelled without repeating the label after every source line.
function measurementMetrics(sources) {
  const records = sources.filter(source => source.type === 'mittaus');
  return [['verenpaine', 'Verenpaine', ''], ['syke', 'Syke', ''], ['lämpö_c', 'Lämpö', '°C'], ['spo2', 'SpO2', '%'],
    ['paino_kg', 'Paino', 'kg'], ['pituus_cm', 'Pituus', 'cm']].flatMap(([key, label, unit]) => {
    const values = new Map();
    for (const source of records) if (source.payload[key] !== undefined) {
      const value = source.payload[key];
      const group = source.conceptKey ? `${source.conceptKey}:${value}` : source.id;
      values.set(group, [...(values.get(group) ?? []), source]);
    }
    return values.size ? [{ key, label, unit, values: [...values.values()] }] : [];
  });
}

function MeasurementTiles({ sources, narration, anchorOwner }) {
  // `paino_kg`/`pituus_cm` encode their unit in the key; `verenpaine`/`syke` do not, so their tile renders
  // the source's own value with no unit rather than a unit we supplied. The owner reversed issue 04's
  // "measurement values render in the record tables, not as tiles" here: blood pressure was renderable
  // nowhere at all, which is what forced the reversal rather than a change of taste.
  const metrics = measurementMetrics(sources);
  const linkedSources = new Set();
  if (!metrics.length) return null;
  return <Block title="Mittaukset" headingLevel={2} id="section-measurements" className="brief-data-section"
    actions={metrics.length > 0 ? <span className="t-caption">{metrics.length} mittausta</span> : undefined}>
    {!metrics.length ? <EmptyPanel /> : <div className="brief-measurements">{metrics.map(metric => <article key={metric.key}>
      {metric.values.map((group, index) => <div className="brief-measurement-value" key={`${metric.key}-${index}`}>
        <p><strong className="t-metric-sm">{group[0].payload[metric.key]}</strong>{metric.unit && <> <span>{metric.unit}</span></>}</p>
        {index === 0 && <h3 className="t-overline">{metric.label}</h3>}
        {group.map(source => {
          const target = !linkedSources.has(source.id);
          linkedSources.add(source.id);
          return <div className="t-caption brief-measurement-source" key={source.id}>
            <a id={target && anchorOwner?.get(source.id) === 'measurements' ? `source-${source.id}` : undefined} href={sourceHref(source.id)}>{formatDate(source.date)}{source.form?.form_type ? ` · ${source.form.form_type}` : ''}</a>
            {target && <><EntryMetadata source={source} /><SelectionEvidence source={source} narration={narration} /></>}
          </div>;
        })}
      </div>)}
    </article>)}</div>}
  </Block>;
}

function RecordStatusTable({ sources, narration, anchorOwner }) {
  const rows = sources.filter(source => source.type === 'diagnoosi' || source.type === 'päätös');
  if (!rows.length) return null;
  // N3a: one row per (ICD-10 code, status), drawn from its newest record. The row's Decision-1 chip and
  // provenance are the newest record's; the folded records keep their anchors in a visually-hidden list
  // inside the row. Both numbers on the count line are of these rendered and folded rows.
  const groups = foldStatusRows(rows);
  return <Block title="Diagnoosit ja päätökset" headingLevel={2} id="section-diagnoses" className="brief-data-section"
    actions={rows.length > 0 ? <span className="t-caption">{statusCountText(groups)}</span> : undefined}>
    {!rows.length ? <EmptyPanel /> : <Table label="Kirjausten tilan taulukko">
      <TableHeader><TableHeaderCell>Kirjaus</TableHeaderCell><TableHeaderCell>Tila</TableHeaderCell><TableHeaderCell>Kirjattu</TableHeaderCell></TableHeader>
      <TableBody>{groups.map(({ key, newest: row, folded, count }) => <Fragment key={key}>
        <TableRow showArrow={false} id={anchorOwner?.get(row.id) === 'status' ? `source-${row.id}` : undefined}><th scope="row" className="t-body-strong">{row.payload.nimi ?? row.payload.päätös}
            {count > 1 && <> <Tag className="brief-record-state">×{count} kirjausta</Tag></>}
            <FoldedAnchors rows={folded} owner="status" anchorOwner={anchorOwner} /></th>
          <TableCell>{row.type === 'päätös' ? row.payload.voimassa : row.payload.tila}</TableCell>
          <TableCell>{formatDate(row.date)}</TableCell></TableRow>
        {hasSourceDetails(row, narration) && <TableRow showArrow={false} className="brief-source-metadata"><TableCell colSpan={3}><RecordStateBadge state={row.state} />
          <EntryMetadata source={row} /><SelectionEvidence source={row} narration={narration} /></TableCell></TableRow>}
      </Fragment>)}</TableBody>
    </Table>}
  </Block>;
}

// Which record types each table displays, and therefore which table emits the canonical
// `id="source-<id>"` anchor for a record. A record is anchored exactly once, by the table that shows it:
// the medications table and the status table for medication/diagnosis/decision records, the research table
// for lab results, the measurement tiles for the first record of a measurement group, and the assessment
// rows for assessment records. A record that no table displays is anchored by nothing and its links render
// as plain text (see `SourceLink`), which is the honest state: there is no source in this view to point at.
function anchorOwners(sources) {
  const owners = new Map();
  const claim = (id, owner) => { if (id && !owners.has(id)) owners.set(id, owner); };
  const of = (...types) => sources.filter(source => types.includes(source.type));
  for (const source of of('lääkitys')) claim(source.id, 'medications');
  for (const source of of('diagnoosi', 'päätös')) claim(source.id, 'status');
  for (const source of of('tutkimustulos')) claim(source.id, 'research');
  for (const metric of measurementMetrics(sources)) {
    for (const values of metric.values) claim(values[0]?.id, 'measurements');
  }
  for (const source of of('arviointi')) claim(source.id, 'assessments');
  return owners;
}

// The rule-based assessment records (instrument, version, count, reading) that the selection kept visible.
function RelevantAssessments({ presentation, narration, anchorOwner }) {
  // The rendered set, not the selected set. A record can be selected and still have no rendered row here
  // (it may be outside this Picture's own record arrays), and the row's whole point is that its own `Lähde`
  // metadata is the thing the count beside it was read from — so a row whose source does not render would
  // carry a number with no source in view, and its link would point at nothing.
  const visible = new Set((presentation.sources ?? []).map(source => source.id));
  const sourceById = new Map((presentation.sources ?? []).map(source => [source.id, source]));
  const rows = (presentation.assessments ?? [])
    .filter(row => row.lahteet?.length && row.lahteet.every(id => visible.has(id)));
  if (!rows.length) return null;
  return <Block title="Arvioinnit" headingLevel={2} id="section-assessments" className="brief-data-section"
    actions={rows.length > 0 ? <span className="t-caption">{rows.length} arviointia</span> : undefined}>
    {!rows.length ? <EmptyPanel /> : <Table label="Arviointien taulukko">
      <TableHeader><TableHeaderCell>Instrumentti</TableHeaderCell><TableHeaderCell>Päiväys</TableHeaderCell><TableHeaderCell>Versio</TableHeaderCell><TableHeaderCell align="right">Pisteet</TableHeaderCell><TableHeaderCell>Vastaavuus</TableHeaderCell><TableHeaderCell>Puuttuu</TableHeaderCell><TableHeaderCell>Tila</TableHeaderCell></TableHeader>
      <TableBody>{rows.map((row, index) => {
        // The date is the row's own rendered source record(s), never a chosen one: one date is shown as it is,
        // several are listed in the row's own source order. Nothing is rendered when no rendered source carries
        // a date; `formatDate`'s "Päiväys puuttuu" fallback is for a value that is present but unusable.
        const dates = row.lahteet.map(id => sourceById.get(id)?.date).filter(Boolean);
        // A count of the supplied items, recomputable from the item list this row renders below: an item is
        // answered when it has a `pisteet`. No sum, no band, no percentage with a verdict.
        const answered = row.osiot.filter(item => item.pisteet !== null && item.pisteet !== undefined).length;
        const missing = row.osiot.length - answered;
        return <Fragment key={`${row.instrumentti}-${index}`}>
        <TableRow showArrow={false}><th scope="row" className="t-body-strong">{row.instrumentti}</th>
          <TableCell>{dates.length > 0 && dates.map(date => formatDate(date)).join(' · ')}</TableCell>
          {/* Gate 4c: a presented record, not an implemented instrument. With no specification for the
              instrument there is no total to show, and "Ei kokonaispisteitä" alone rendered a record whose
              source answers exist identically to one that carried nothing. The count is of the record
              rendered as this row's own `Lähde` metadata, so it is recomputable in this view
              (`CONTEXT.md:49`), and it is a count and not a score: no sum, no band, no cut-off. */}
          <TableCell>{row.instrument_version}</TableCell><TableCell numeric>{row.valmis
            ? `${row.total} / ${row.max_total}`
            : `Ei kokonaispisteitä${row.source_answers ? ` · lähteessä ${row.source_answers} vastausta` : ''}`}</TableCell>
          <TableCell>{row.osiot.length > 0 && (answered > 0 ? `${answered}/${row.osiot.length} osiota vastattu` : 'Ei vastauksia lähteessä')}</TableCell>
          {/* A missing answer is named as missing, never shown as a value: the item codes are in the evidence
              detail below, and a missing answer is not a zero and not "low risk". */}
          <TableCell>{missing > 0 && `${missing} osiota ilman vastausta`}</TableCell>
          <TableCell>{row.tulkinta ?? 'Ei tulkintaa'}</TableCell></TableRow>
        {/* The supplied answers, read-only, in a disclosure under the row. `osiot` has always been on the wire
            and was rendered nowhere, so a PHQ-9 item — including item 9, the one whose score most changes what a
            professional does next — could not be seen at any value through any view. This closes that
            without interpreting anything: no sum, no band, no colour, no icon, no ordering by severity,
            and an answer the source did not supply renders as absent rather than as 0.
            What it deliberately does NOT render is `kysymys`, the instrument's own Finnish item wording.
            That text is already in `assess.py` and already serialized, but displaying it is a new
            exposure of instrument wording, and Step 1 of `.scratch/work-orders/phq9-item9-decision.md`
            says "no per-item wording is added, because the item text is the instrument's, not ours".
            The source's own item code identifies the item; the wording is a separate decision for the
            instrument's owner, not one to take inside a UI round. */}
        {row.osiot.length > 0 &&
          <TableRow showArrow={false} className="brief-assessment-items"><TableCell colSpan={7}>
            <details className="t-caption">
              <summary className="t-control">{`Lähteen vastaukset (${row.osiot.length})`}</summary>
              {/* One string per answer, joined: adjacent JSX text nodes render with React's comment
                  separators between them, which is noise in the DOM and in every assertion about it. */}
              <p>{row.osiot.map(item => `${item.osio} ${item.pisteet ?? 'ei vastausta'}`).join(' · ')}</p>
            </details>
          </TableCell></TableRow>}
        {row.lahteet.some(id => sourceById.has(id)) &&
          <TableRow showArrow={false} className="brief-source-metadata"><TableCell colSpan={7}>{row.lahteet.map(id => {
            const source = sourceById.get(id);
            // The anchor lives here, in the row that actually renders, and the label is emitted for EVERY
            // source of the row. It used to sit on the row's first source only (an id on a `<tr>`) while the
            // label was emitted only from index 1 upward, so one assessment source per row carried a link
            // with no anchor and the row's own first source was reachable only through the assessment.
            // A link, never an anchor. The canonical `id="source-<id>"` for a record is emitted once, by the
            // record's own row in the status table; this metadata row is a second view of a record that is
            // already anchored, so emitting an id here produced two elements with one id and the anchor
            // resolved to whichever rendered first. The link is kept so the row is still citable.
            const owns = anchorOwner?.get(id) === 'assessments';
            // The link and its anchor are unconditional for every source that renders. Only the optional metadata
            // depends on what this source carries, so a minimal source still has its Lähde link.
            return source && <div key={id}><a id={owns ? `source-${id}` : undefined} href={sourceHref(id)}>Lähde</a>
              {hasSourceDetails(source, narration) && <><RecordStateBadge state={source.state} /><EntryMetadata source={source} />
              <SelectionEvidence source={source} narration={narration} /></>}</div>;
          })}</TableCell></TableRow>}
      </Fragment>; })}</TableBody>
    </Table>}
  </Block>;
}

// The briefing's own context: the person, the professional's role and the service purpose, always on screen.
// It is not the header's line, which the library lays out by its own breakpoints and can lose.
export function EncounterContext({ presentation }) {
  // No person value at all means no Henkilö fact (the loading panel); a person value that the Picture does not
  // identify is said in words. The filter below drops any fact whose value is absent.
  const person = presentation.identityAvailable ? presentation.personName
    : presentation.personName === undefined ? undefined : 'Henkilöllisyystieto ei saatavilla';
  const facts = [
    ['Henkilö', person],
    ['Ammattirooli', presentation.roleLabel],
    ['Palvelukonteksti', presentation.purposeLabel],
  ].filter(([, value]) => value);
  return <section className="brief-context-strip" aria-label="Palvelutilanteen konteksti">
    <dl>{facts.map(([label, value]) => <div key={label}><dt className="t-caption">{label}</dt><dd className="t-body">{value}</dd></div>)}</dl>
  </section>;
}

function PatientPills({ sources, presentation }) {
  // One chip per distinct diagnosis name: the same code recorded on several contacts is one fact, linked to
  // its latest record (the sources are ordered newest first by the adapter's date sort within a type).
  const diagnoses = [...new Map(sources.filter(source => source.type === 'diagnoosi' && source.payload.nimi)
    .map(source => [source.payload.nimi, source])).values()];
  const authors = sources.filter(source => typeof source.payload.kirjaaja === 'string' && source.payload.kirjaaja.trim());
  if (!diagnoses.length && !authors.length && !presentation.roleLabel) return null;
  return <section className="brief-patient-pills" aria-label="Lähteisiin perustuvat tunnisteet">
    <div>{diagnoses.map(source => <a className="t-tag brief-diagnosis-tag" href={sourceHref(source.id)} key={source.id}>{source.payload.nimi}</a>)}
      {authors.map(source => <a className="t-tag brief-professional-chip" href={sourceHref(source.id)} key={source.id}>
        <span>Kirjaaja</span> {source.payload.kirjaaja}
      </a>)}
    </div>
  </section>;
}

function EmptyPanel() {
  return <p className="t-caption brief-empty-panel">{EMPTY_RECORDS}</p>;
}

function EntryMetadata({ source }) {
  if (!source) return null;
  const fields = [];
  if (source.encounterId) fields.push(['Kohtaaminen', source.encounterId]);
  if (source.caseId) fields.push(['Asia', source.caseId]);
  if (source.käyntiaika) fields.push(['Käyntiaika', formatDateTime(source.käyntiaika)]);
  const recipients = Array.isArray(source.vastaanottajat) ? source.vastaanottajat.filter(value => typeof value === 'string' && value.trim()) : [];
  if (recipients.length) fields.push(['Vastaanottajat', recipients.join(', ')]);
  if (source.erikoisala) fields.push(['Erikoisala', source.erikoisala]);
  if (source.suorituspaikka) fields.push(['Kirjaava yksikkö', source.suorituspaikka]);
  const author = source.payload?.kirjaaja || source.authoredByRole;
  if (author) fields.push(['Kirjaaja', author]);
  if (source.responsibleClinician) fields.push(['Vastuuhenkilö', source.responsibleClinician]);
  if (source.responsibleApproverLabel) fields.push(['Vastuullinen hyväksyjä', source.responsibleApproverLabel]);
  return fields.length ? <dl className="brief-entry-metadata">{fields.map(([label, value]) =>
    <div key={label}><dt className="t-caption">{label}</dt><dd className="t-body">{value}</dd></div>)}</dl> : null;
}

// The record's lifecycle state is a NEUTRAL tag, never a status tag: `Tag` with a `status` adds a colour
// and a fixed 12px icon, and `Luonnos` must stay grey and italic (architecture-as-built). `brief-record-state`
// is kept as the app's own hook, and `brief-state-draft` as the draft marker, both pinned by tests.
function RecordStateBadge({ state }) {
  const labels = { draft: 'Luonnos', final: 'Valmis', approved: 'Hyväksytty' };
  const label = labels[state];
  return label ? <Tag className={`brief-record-state${state === 'draft' ? ' brief-state-draft' : ''}`}>{label}</Tag> : null;
}

// The selection and risk trace of a record. The three machinery paragraphs sit in a closed disclosure: they
// are model/pipeline explanations, and the professional reads them on request. The out-of-generation warning
// is the exception — it is a warning about what the professional is looking at, so it stays visible.
function SelectionEvidence({ source, narration }) {
  const reasons = source.selectionReasonTexts ?? [];
  const riskReasons = source.riskReasonTexts ?? [];
  const outcome = source.decisionOutcome;
  const outOfGeneration = narration?.generationStatus === 'generated' && !narration.inputRecordIds.includes(source.id);
  if (!reasons.length && !riskReasons.length && !outcome && !outOfGeneration) return null;
  // Decision-1's read on this record, in words, where the record is: `relevant` added or emphasised it for this
  // purpose, `irrelevant` kept it visible but out of the narration. Abstention gets no chip — restraint is not
  // a finding (ADR-0009), and the status colour rules put both neutral states under "no tint".
  const why = outcome?.status === 'relevant' ? 'Olennainen tälle käynnille'
    : outcome?.status === 'irrelevant' ? 'Taustatieto tälle käynnille' : null;
  // Decision-1's time-sensitivity on the same record: `current` or `historical`, as the model read it. `unclear`
  // and an unanswered question get no chip. It is an ordering fact about this record — the same caveat the day
  // view's lead line carries — and never a statement about the care's urgency.
  const timing = outcome?.time_sensitivity === 'current' ? 'Ajankohtainen'
    : outcome?.time_sensitivity === 'historical' ? 'Aiempi (mallin mukaan)' : null;
  return <div className="t-caption brief-selection-evidence">
    {(why || timing) && <p className="brief-why">
      {why && <Tag className="brief-role-chip">{why}</Tag>}
      {timing && <>{why ? ' ' : ''}<Tag className="brief-role-chip">{timing}</Tag></>}
    </p>}
    {(reasons.length > 0 || riskReasons.length > 0 || outcome) && <details className="t-caption">
      <summary className="t-control">Valinnan perusteet</summary>
      {reasons.length > 0 && <p><strong>Valinnan peruste:</strong> {reasons.join(' · ')}</p>}
      {/* The risk verdict's reason, on the record that produced it, rather than only as a
          patient-level label in the alerts panel. The strings come from the backend's rule trace;
          the frontend never derives or re-words them. */}
      {riskReasons.length > 0 && <p><strong>Riskihavainnon peruste:</strong> {riskReasons.join(' · ')}</p>}
      {outcome && <p><strong>Decision-1:</strong> {decisionOutcomeText(outcome.status)}</p>}
    </details>}
    {outOfGeneration && <p className="brief-out-generation"><SeverityIcon tone="warning" /><strong>Varoitus.</strong> Ei mukana Foundry-syötteessä.</p>}
  </div>;
}

function LabResultsTable({ sources, narration, anchorOwner }) {
  const rows = sources.filter(source => source.type === 'tutkimustulos');
  if (!rows.length) return null;
  return <Block title="Tutkimustulokset" headingLevel={2} id="section-labs" className="brief-data-section"
    actions={rows.length > 0 ? <span className="t-caption">{rows.length} tutkimusta</span> : undefined}>
    <Table label="Tutkimustulosten taulukko"><TableHeader>
      <TableHeaderCell>Tutkimus</TableHeaderCell><TableHeaderCell align="right">Tulos</TableHeaderCell><TableHeaderCell>Yksikkö</TableHeaderCell><TableHeaderCell align="right">Viitearvo</TableHeaderCell><TableHeaderCell>Tulkinta</TableHeaderCell><TableHeaderCell>Kirjattu</TableHeaderCell>
    </TableHeader><TableBody>
      {rows.length ? rows.map(row => <Fragment key={row.id}>
          <TableRow showArrow={false} id={anchorOwner?.get(row.id) === 'research' ? `source-${row.id}` : undefined}><th scope="row" className="t-body-strong">{labLabel(row.payload.tutkimus)}</th><TableCell numeric>{row.payload.arvo}</TableCell>
            <TableCell>{row.payload.yksikkö}</TableCell><TableCell numeric>{row.payload.viitearvo}</TableCell><TableCell>{row.payload.tulkinta}</TableCell><TableCell>{formatDate(row.date)}</TableCell></TableRow>
          {hasSourceDetails(row, narration) && <TableRow showArrow={false} className="brief-source-metadata"><TableCell colSpan={6}><RecordStateBadge state={row.state} />
            <EntryMetadata source={row} /><SelectionEvidence source={row} narration={narration} /></TableCell></TableRow>}
      </Fragment>) : <TableRow showArrow={false}><TableCell colSpan={6}><EmptyPanel /></TableCell></TableRow>}
    </TableBody></Table>
  </Block>;
}

// NEW: coverage disclosure. Per-row outcomes, and it never reports a withheld denominator.
export function SelectionCoverage({ presentation }) {
  const sources = presentation.sources ?? [];
  const notes = presentation.selection?.decision_notes ?? [];
  const visibleTextCount = sources.filter(source => source.type === 'vapaateksti').length;
  return <Block title="Kattavuus" headingLevel={2} id="section-coverage" className="brief-data-section brief-coverage"
    actions={<span className="t-caption">{notes.length} Decision-1-tulosriviä / {visibleTextCount} näkyvää vapaatekstikirjausta.</span>}>
    {notes.length > 0 && <ul>{notes.map(note => <li key={note.record_id}>
      <SourceLink id={note.record_id} targets={anchorTargets(presentation)}>Lähde</SourceLink> · {decisionOutcomeText(note.status)}
    </li>)}</ul>}
  </Block>;
}

export function ImpactEstimate({ sources }) {
  // Counts only source labels and form tuples on the rendered, permitted source list; no withheld denominator.
  const sourceLabels = new Set(sources.map(source => source.source).filter(Boolean));
  const formTypes = new Set(sources.filter(source => source.form?.form_type)
    .map(source => JSON.stringify([source.form.form_type, source.form.version, source.form.service])));
  return <Block title="Tekoälyn vaikutus" headingLevel={2} id="section-impact" className="brief-data-section brief-impact">
    <p>{sourceLabels.size} lähdelabelia · {formTypes.size} lomaketyyppiä valituissa kirjauksissa.</p>
    <p className="t-caption brief-small">Synteettisen näkymän lähde- ja lomakelaskenta; ei ajansäästöarviota.</p>
  </Block>;
}

function SelectedRecords({ sources, narration }) {
  // Types with a section of their own, excluded from this general list. `asiointitapahtuma` was in this list
  // and had no section anywhere, so a visit was fetched, cited by the narration and rendered NOWHERE — the
  // one record type a reader could never see. It renders here now, titled by its subject, with every declared
  // field in its excerpt. Nothing is fetched that is not shown: `challenge-demo.test.mjs` holds a fixture with
  // one record of every declared type and asserts each one's own value reaches the markup.
  const tableTypes = ['perustieto', 'lääkitys', 'mittaus', 'diagnoosi', 'päätös', 'arviointi', 'tutkimustulos'];
  const rows = sources.filter(source => !tableTypes.includes(source.type));
  const groups = new Map();
  for (const source of rows) {
    const heading = source.heading?.national || null;
    groups.set(heading, [...(groups.get(heading) ?? []), source]);
  }
  return <Block title="Valitut kirjaukset" headingLevel={2} id="section-records" className="brief-data-section brief-selected-records"
    actions={rows.length > 0 ? <span className="t-caption">{rows.length} kirjausta</span> : undefined}>
    {!rows.length ? <EmptyPanel /> : [...groups].map(([heading, entries]) => <div className="brief-heading-group" key={heading ?? 'unheaded'}>
      {heading && <h3 className="t-title-sm brief-record-group-heading">{heading}</h3>}
      {entries.map(source => {
        const formText = source.form && [source.form.form_type, source.form.version, source.form.filled_by_role].filter(Boolean).join(' · ');
        return <article className={`brief-selected-record${source.state === 'draft' ? ' brief-record-draft' : ''}`}
          id={`source-${source.id}`} key={source.id}>
          <div className="brief-record-heading">
            <div>{source.heading?.local && <p className="t-caption brief-local-heading">{source.heading.local}</p>}
              <h3 className="t-body-strong">{source.title}</h3><RecordStateBadge state={source.state} /></div>
            <time className="t-caption" dateTime={source.date}>{formatDate(source.date)}</time>
          </div>
          <EntryMetadata source={source} />
          {source.excerpt && (source.excerpt.length > EXCERPT_DISCLOSURE_CHARS
            ? <details className="brief-source-more"><summary className="t-control">Näytä koko kirjaus</summary><p>{source.excerpt}</p></details>
            : <p>{source.excerpt}</p>)}
          {(source.source || formText) && <p className="t-caption brief-small">{[source.source, formText].filter(Boolean).join(' · ')}</p>}
          <SelectionEvidence source={source} narration={narration} />
        </article>;
      })}
    </div>)}
  </Block>;
}

// Gate 3 — the presenter's surface, and where the demo's two comparison instruments now live.
//
// They were scattered: the mode was reachable only through `?mode=`, the three-way recomputation sat inside
// the collapsed technical disclosure *and* inside the third tab, and the sibling-lens comparison sat in the
// third tab. A presenter had to hunt; a reader met them mid-briefing.
//
// This is one labelled surface, after every section of the briefing and before the technical trace. It says
// what it is, and what it is not: the encounter is recomputed for the *presenter*, and nothing here changes
// what the professional is shown or decides. Nothing that used to be visible without a click became a click
// away — both comparisons moved the other way, out of a collapsed disclosure and out of a tab.
//
// The mode switch is a real control, not a decoration: it sets the same `mode` the picture route takes, so
// switching it recomputes *this* encounter rather than describing a different one.
const PICTURE_MODE_LABELS = {
  normal: 'Normaali',
  'decision-off': 'Decision-1 pois päältä',
  'foundry-off': 'Foundry pois päältä',
};

function ModeSwitch({ mode, onChange }) {
  return <fieldset className="brief-mode-switch">
    <legend className="t-caption">Laskennan tila</legend>
    {PICTURE_MODES.map(value => <label className="t-control" key={value}>
      <input type="radio" name="brief-picture-mode" value={value} checked={mode === value}
        onChange={() => onChange?.(value)} />{PICTURE_MODE_LABELS[value]}
    </label>)}
  </fieldset>;
}

export function PresenterTools({ mode, onModeChange, presentation, comparison, comparisonLoading, comparisonError,
  onRunFailureComparison, lensSiblings }) {
  return <section className="brief-presenter prose" id="presenter-tools" aria-label="Esittäjän työkalut">
    <p className="t-overline">Esittäjän työkalut</p>
    {/* Gate 8: the innovation claim, in one line, where a presenter can point at it. It states this
        system's own boundary — what the model is allowed to produce — and makes no claim about any other
        system, because we have measured none. */}
    <p className="t-body-strong">Tekoäly tekee tässä yhden asian: järjestää, suodattaa ja kirjoittaa
      tarkistettua tekstiä. Se ei päätä.</p>
    <p className="t-caption brief-small">Sallitut kirjaukset, sääntöpohjainen päätös ja arvioinnit tulevat
      lähdetiedoista ja sääntötaulukoista. Malli ei voi muuttaa niitä — se voi muuttaa vain sen, mitä niistä
      kirjoitetaan. Siksi tämä vertailu on mahdollinen: sama Kohtaaminen kolmena laskentana, ja päätös on
      sama kaikissa.</p>
    <p className="t-caption brief-small">Esityksen apuvälineet. Sama Kohtaaminen lasketaan tässä uudelleen
      ilman päätösmallia ja ilman Foundrya, ja sama henkilö näytetään toisessa näkökulmassa. Nämä eivät ole osa
      ammattilaisen tilannekuvaa eivätkä muuta sitä, mitä hänelle näytetään.</p>
    <ModeSwitch mode={mode} onChange={onModeChange} />
    {presentation.encounterId && <FailureDemo presentation={presentation} comparison={comparison}
      loading={comparisonLoading} error={comparisonError} onRun={onRunFailureComparison} key={presentation.encounterId} />}
    <LensDemo siblings={lensSiblings} />
  </section>;
}

// NEW: the fail-closed comparison. Prose withheld, deterministic briefing intact and deliberate-looking.
function FailureDemo({ presentation, comparison, loading, error, onRun }) {
  // Gate 8: the comparison's real subject is not only what each layer ADDS. The three numbers below are
  // computed from the picture this view is already rendering -- the risk row in the alerts panel, the
  // action list in the rule-based assessment card, and the assessment rows, all filtered exactly as
  // `AssessmentRows` filters them -- so they are recomputable in this view (`CONTEXT.md:49`) and a reader
  // can check the claim against what is on screen. The claim itself is checked by a test that the three
  // modes agree on `situation`, `risk`, `actions` and `assessments`.
  const selected = new Set(presentation.selectedSourceIds ?? []);
  const renderedAssessments = (presentation.assessments ?? [])
    .filter(row => row.lahteet?.length && row.lahteet.every(id => selected.has(id)));
  return <section className="brief-failure-demo prose" aria-label="Tekoälyn vaikutuksen vertailu">
    <h2 className="t-title-sm">Tekoälyn vaikutuksen vertailu</h2>
    <p>Palvelu laskee saman Kohtaamisen uudelleen Decision-1 pois päältä ja Foundry pois päältä. Päätösperusteinen tilannekuva pysyy sääntöpohjaisena.</p>
    <div className="brief-comparison-invariant">
      <p className="t-body-strong">Mikä ei muutu</p>
      <p className="t-caption brief-small">Sääntöpohjainen päätös ({presentation.risk?.risk_level ?? 'arvioimaton'}),
        {presentation.actions.length} toimenpidettä ja {renderedAssessments.length} arviointia tulevat
        sääntötaulukoista ja sallituista kirjauksista. Decision-1 ja Foundry eivät voi muuttaa niitä; ne muuttavat
        vain sen, mitä niistä kirjoitetaan.{isIllustrativeThreshold(presentation.risk) && ` ${ILLUSTRATIVE_NOTE}`}</p>
    </div>
    <Button variant="primary" loading={loading} onClick={onRun}>Vertaa tekoälyn vaikutusta</Button>
    {loading && <p role="status" className="visually-hidden brief-transient-status">Lasketaan vertailua…</p>}
    {error && <p role="alert" className="t-caption brief-inline-status brief-transient-status">{error}</p>}
    {comparison && <div className="brief-comparison-grid" aria-live="polite">
      <ComparisonCard title="Normaali · Decision-1 + Foundry" picture={presentation} />
      <ComparisonCard title="Decision-1 pois päältä" picture={picturePresentation(comparison.decisionOff)} />
      <ComparisonCard title="Foundry pois päältä" picture={picturePresentation(comparison.foundryOff)} />
    </div>}
  </section>;
}

function generationPathLabel(path) {
  if (path === 'agent_reference') return 'Versionoitu Prompt Agent';
  if (path === 'direct_fallback') return 'Suora mallikutsu (varapolku)';
  if (path === 'direct_chat_completions') return 'Direct chat-completions';
  return 'Ei generointia';
}

// NEW: the Decision-1 panel — what Decision-1 did versus what it assessed and rejected, including withheld sentences
// with their reasons.
export function AIProvenance({ presentation }) {
  const selection = presentation.selection;
  // `notes` is already filtered to selected IDs; both counts describe rows rendered below, not all candidates.
  const notes = selection?.decision_notes ?? [];
  const narration = presentation.narration;
  const rejectedSentences = presentation.grounding?.rejected_sentences ?? [];
  const inputIds = new Set(narration.generationStatus === 'generated' ? narration.inputRecordIds : []);
  const additions = selection?.additional_record_ids ?? [];
  const emphasized = selection?.emphasized_record_ids ?? [];
  const targets = anchorTargets(presentation);
  const probabilities = values => Object.entries(values ?? {}).map(([key, value]) => `${key}: ${Number(value).toFixed(2)}`).join(' · ');
  return <section className="brief-ai-evidence" aria-label="Tekoälyn päätökset ja jäljitettävyys">
    <h2 className="t-title-sm">Mitä tekoäly teki</h2>
    {/* The rule's confidence, moved here from the decision card so it is available without reading as part of
        the clinical signal. */}
    <p><strong>Triage-signaalin luottamus:</strong> {presentation.situation?.confidence ?? 'ei tietoa'}.</p>
    <p className={additions.length ? 'brief-selection-state brief-selection-info brief-info' : undefined}>
      {additions.length > 0 && <SeverityIcon tone="info" />}<strong>Decision-1 lisäsi:</strong> {additions.length
      ? additions.map(id => <SourceLink key={id} id={id} targets={targets}>{id} </SourceLink>) : 'ei uusia lähteitä'}.</p>
    <p className={emphasized.length ? 'brief-selection-state brief-selection-success brief-success' : undefined}>
      {emphasized.length > 0 && <SeverityIcon tone="success" />}<strong>Decision-1 korosti:</strong> {emphasized.length
      ? emphasized.map(id => <SourceLink key={id} id={id} targets={targets}>{id} </SourceLink>) : 'ei lähteitä'}.</p>
    {notes.length > 0 && <details className="brief-decision-assessed" open><summary className="t-control">Mitä Decision-1 arvioi ({notes.length}) — sisältää hylätyt</summary><ul className="brief-decision-outcomes">{notes.map(note => {
      const outOfGeneration = narration.generationStatus === 'generated' && !inputIds.has(note.record_id);
      // Only an actual out-of-generation omission carries a status token. Abstention — `unclear` and
      // `gate_refused`, which the reader sees as "säilyi mallisyötteessä" — stays NEUTRAL: the binding
      // table puts the two Decision-1 states under "must not carry status colour", and restraint is correct
      // behaviour rather than an error to be flagged. The states stay distinct in wording instead.
      const tone = outOfGeneration ? 'warning' : null;
      const severity = tone === 'warning' ? 'Varoitus.' : tone === 'info' ? 'Tieto.' : null;
      return <li className={`brief-decision-outcome${tone ? ` brief-${tone}` : ''}`} key={note.record_id}>
        {tone && <><SeverityIcon tone={tone} /><strong>{severity}</strong> </>}
        <SourceLink id={note.record_id} targets={targets}>{note.record_id}</SourceLink> — {decisionOutcomeText(note.status)}
        {outOfGeneration && <span className="brief-omission-fact"> Ei mukana Foundry-syötteessä.</span>}
        {note.relevance_probabilities && Object.keys(note.relevance_probabilities).length > 0 && <span>: {probabilities(note.relevance_probabilities)}</span>}
        {Object.entries(note.gate_probabilities ?? {}).map(([gate, values]) => {
          // The urgency distribution, plus the classifier's own leading level in words. The bracket names the
          // *limit* rather than the level: this is an ordering basis (ADR-0009), so it is never presented as
          // how urgent the patient's care is, and the label is the classifier's answer, not our sentence.
          if (gate === 'urgency') {
            const label = leadingUrgencyLabel(values);
            return <span key={gate}> · kiireellisyys (järjestysperuste, ei hoidon kiireellisyys): {probabilities(values)}{label ? ` — ${label}` : ''}</span>;
          }
          return <span key={gate}> · {gate}: {probabilities(values)}</span>;
        })}
        {note.relevance_confidence != null && <span> · confidence {Number(note.relevance_confidence).toFixed(2)}</span>}
        {note.time_sensitivity && <span> · aika: {note.time_sensitivity}</span>}
        {note.subject_binding && <span> · kohde: {note.subject_binding}</span>}
        {note.negation_probability != null && <span> · kielto {Number(note.negation_probability).toFixed(2)}</span>}
        {note.gate_refused && <span> · kynnys ei ylittynyt</span>}
        <Tag className="brief-small"> · {note.model ?? 'Decision-1'}{Number.isFinite(note.elapsed_ms) ? ` · ${note.elapsed_ms} ms` : ''}{note.input_tokens != null ? ` · ${note.input_tokens} syötetokenia` : ''}</Tag>
      </li>;
    })}</ul></details>}
    <p><strong>Kerronnan reitti:</strong> {generationPathLabel(narration.generationPath)}.</p>
    <p><strong>Foundry:</strong> {narration.generationDeployment ?? 'ei deployment-tietoa'} / {narration.generationModel ?? 'ei mallia'}
      {narration.generationElapsedMs != null ? ` · ${narration.generationElapsedMs} ms` : ''}
      {narration.generationInputTokens != null ? ` · ${narration.generationInputTokens} syötetokenia` : ''}
      {narration.generationOutputTokens != null ? ` · ${narration.generationOutputTokens} tulostokenia` : ''}.</p>
    <p><strong>Decision-1-verifiointi:</strong> {narration.supportModel ?? 'ei ajettu'}
      {narration.supportElapsedMs != null ? ` · ${narration.supportElapsedMs} ms` : ''}
      {narration.supportInputTokens != null ? ` · ${narration.supportInputTokens} syötetokenia` : ''}
      {narration.supportOutputTokens != null ? ` · ${narration.supportOutputTokens} tulostokenia` : ''} · {supportStatus(narration.supportStatus)}.</p>
    <p>{groundingLine(narration, rejectedSentences)}</p>
    {/* The per-sentence machinery for withholding lives here, on the technical page, and
        nowhere the professional reads. `RejectedSentence` carries only a bounded id and the verifier's
        reason — never the withheld text or a record id it cited — so this list stays auditable without
        leaking unverified prose into the briefing. The mapped reason is for a reader; the raw code stays
        beside it as the machine-readable value the backend actually emitted. */}
    {rejectedSentences.length > 0 && <div className="t-caption brief-withheld-sentences">
      <h3 className="t-title-sm">Näyttämättä jätetyt lauseet ({rejectedSentences.length})</h3>
      <ul>{rejectedSentences.map(sentence => <li key={sentence.sentence_id}>
        Lause {sentence.sentence_id} · hylätty · syy: {verdictReason(sentence.reason)} ({sentence.reason})
      </li>)}</ul>
    </div>}
    {(presentation.pipelineTrace ?? []).length > 0 && <div className="t-caption brief-stage-trace">
      <h3 className="t-title-sm">Vaihekohtainen jälki</h3>
      {presentation.pipelineTraceId && <p className="t-caption brief-small">Pyyntö: {presentation.pipelineTraceId.slice(0, 12)}</p>}
      <ol>{presentation.pipelineTrace.map((entry, index) => <li key={`${entry.stage}-${index}`}>
        <strong>{entry.stage}</strong> · {entry.elapsed_ms} ms
        {entry.model && ` · ${entry.model}`}
        {entry.input_tokens != null && ` · ${entry.input_tokens} syötetokenia`}
        {entry.output_tokens != null && ` · ${entry.output_tokens} tulostokenia`}
        {entry.status && ` · ${entry.status}`}
      </li>)}</ol>
    </div>}
    <details><summary className="t-control">Pipeline-jälki</summary><ol className="t-caption">{(presentation.trace ?? []).map((entry, index) => <li key={`${entry.table}-${entry.rule_id}-${index}`}>{entry.table} / {entry.rule_id}: {JSON.stringify(entry.output)}</li>)}</ol></details>
  </section>;
}

// The duplicate-fact group. Both rows of a duplicate are shown, marked as the same fact, unmerged, with no
// winning source. The catalogue's absence rows ("Palvelukatalogi · Ei näytetä tässä koosteessa") are no longer
// rendered: beside the pre-filled forms the same names read as a contradiction (owner, 2026-10-14); a `due`
// entry still reaches the attention list.
function FormOverview({ presentation }) {
  const sources = presentation.sources ?? [];
  const sourceById = new Map(sources.map(source => [source.id, source]));
  const duplicates = presentation.duplicateFacts ?? [];
  if (!duplicates.length) return null;
  return <>
    {duplicates.length > 0 && <Block title="Päällekkäiset kirjaukset" headingLevel={2} className="brief-data-section brief-form-overview">
      <h3 className="t-title-sm">Sama mitattu tieto usealla lomakkeella</h3>
      <ul>{duplicates.map(group => <li key={group.recordIds.join('-')}>
        {sourceById.get(group.recordIds[0])?.topic || 'Sama tieto'} — {group.recordIds.map((id, index) => {
          const source = sourceById.get(id);
          // Target-aware, and the label survives a missing source: `sourceById` is built from the visible
          // sources, so an id whose record is not rendered must not become a dead link.
          const label = source?.form?.form_type ?? source?.title ?? 'Lähde';
          return <span key={id}>{index ? ' · ' : ''}<SourceLink id={id} targets={anchorTargets(presentation)}>{label}</SourceLink></span>;
        })}
      </li>)}</ul>
    </Block>}
  </>;
}

// NEW: the lens comparison. It renders on the technical page beside the presenter tools, and it never fetches
// a sibling lens's Picture.
function LensDemo({ siblings }) {
  // Show only already-loaded picker metadata; never fetch another lens's Picture into this view.
  if (!siblings?.length) return null;
  return <section className="brief-lens-demo prose" aria-label="Näkökulmavertailu">
    <h2 className="t-title-sm">Näkökulmavertailu</h2>
    <p>Sama henkilö, eri mallinnettu näkökulma. Toisen näkökulman lähteitä ei eritellä eikä lasketa tässä.
      Valitse haluamasi synteettinen palvelutilanne sivupalkista.</p>
    <div className="brief-comparison-grid brief-lens-grid">
      {siblings.map(encounter => <section className="brief-comparison-card" key={encounter.encounter_id}>
        <h3 className="t-body-strong">{encounter.picker_label}</h3>
      </section>)}
    </div>
  </section>;
}

function ComparisonCard({ title, picture }) {
  const selection = picture.selection;
  const added = selection?.additional_record_ids ?? [];
  const emphasized = selection?.emphasized_record_ids ?? [];
  return <section className="brief-comparison-card">
    <h3 className="t-body-strong">{title}</h3>
    <p>Lisätyt lähteet: {added.length ? added.join(', ') : 'ei yhtään'}.</p>
    <p>Korostetut lähteet: {emphasized.length ? emphasized.join(', ') : 'ei yhtään'}.</p>
    <p>{picture.summary ? `Kooste: ${picture.summary}` : `Koostetta ei näytetä: ${picture.narration.rejectionReason ?? supportStatus(picture.narration.supportStatus)}.`}</p>
    <p className="t-caption brief-small">Riski {picture.risk?.risk_level ?? 'arvioimaton'}
      {isIllustrativeThreshold(picture.risk) && ` · ${ILLUSTRATIVE_NOTE}`}</p>
  </section>;
}

function Provenance({ summary, children }) {
  return <details className="brief-provenance"><summary className="t-control">{summary}</summary>{children}</details>;
}

function Proposal({ presentation, proposalReviews = {}, onProposalReview, onRetryProposalReview }) {
  if (presentation.proposals.length === 0) return null;
  return <section className="brief-proposal brief-card">
    <h2 className="t-title-sm">Tarkistettavaksi</h2>
    <p className="t-caption brief-small">Ehdotus ammattilaisen harkittavaksi, ei hoitomääräys.</p>
    {presentation.proposals.map(proposal => <ProposalReview
        key={proposal.proposal_id}
        proposal={proposal}
        targets={anchorTargets(presentation)}
        review={proposalReviews[proposal.proposal_id]}
        onReview={onProposalReview}
        onRetry={onRetryProposalReview}
      />)}
  </section>;
}

// The proposal decisions in the past tense the recorded state needs, one per literal the write route
// accepts. A decided proposal must show *what* was decided, not only that something was: the loaded
// decision carries the action, so the rail renders it rather than a bare "saved" that would hide it.
const PROPOSAL_DECISION_LABELS = { accept: 'Hyväksytty', edit: 'Muokattu', dismiss: 'Hylätty' };

function ProposalReview({ proposal, targets, review, onReview, onRetry }) {
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(proposal.text);
  // A recorded decision is an outcome, not an offer: `saved` (recorded on this visit or loaded from the caller's
  // own decisions) and `decided` (the same fact reported by the server as a 409 when the caller submits a second
  // decision, for example from another tab) render their recorded line and NO controls, not even disabled ones.
  const recorded = review?.status === 'saved' || review?.status === 'decided';
  // The states that still render a control (`saving`, `ambiguous`) keep it disabled. The library's Button keeps a
  // disabled control focusable with `aria-disabled` precisely so the reason is reachable; that only holds if the
  // reason is actually supplied. It goes in `title` (and a tooltip shows it), so a keyboard user learns why the
  // action is unavailable rather than meeting a dead control. `failed` and no status keep their controls enabled.
  const disabled = ['saving', 'ambiguous'].includes(review?.status);
  const disabledReason = review?.status === 'saving' ? 'Tallennetaan…'
    : review?.status === 'ambiguous' ? 'Tallennuksen tila on epäselvä. Yritä samaa tallennusta uudelleen.'
      : undefined;
  const evidenceIds = proposal.evidence_record_ids ?? [];
  // F5: the id ties a definite rejection to the actions it belongs to. `failed` is not in `disabled`, so the
  // buttons come back the moment the write is refused — without a visible error and a described-by link the
  // click looked like it did nothing.
  const failureId = `proposal-error-${proposal.proposal_id}`;
  const describedBy = review?.status === 'failed' ? failureId : undefined;

  async function submit(event) {
    event.preventDefault();
    if (!text.trim()) return;
    const saved = await onReview(proposal, 'edit', text);
    if (saved) setEditing(false);
  }

  return <article className="brief-proposal-item">
    <p>{proposal.text}</p>
    <Provenance summary="Ehdotuksen perusteet"><p className="t-caption brief-small">Lähteet: {evidenceIds.map((id, index) => <span key={id}>{index ? ', ' : ''}<SourceLink id={id} targets={targets}>{id}</SourceLink></span>)} · sääntö {proposal.rule_version}</p></Provenance>
    {/* Library Button: disabled is aria-disabled so the control stays focusable and `title` can carry the
        reason; group order is secondary first, primary last, per the component's own doc. */}
    {!recorded && <div className="brief-button-row">
      <Button disabled={disabled} title={disabled ? disabledReason : undefined} aria-describedby={describedBy}
        onClick={() => setEditing(value => !value)}>Muokkaa ehdotusta</Button>
      <Button disabled={disabled} title={disabled ? disabledReason : undefined} aria-describedby={describedBy}
        onClick={() => onReview(proposal, 'dismiss')}>Hylkää ehdotus</Button>
      <Button variant="primary" disabled={disabled} title={disabled ? disabledReason : undefined}
        aria-describedby={describedBy}
        loading={review?.status === 'saving'}
        onClick={() => onReview(proposal, 'accept')}>Hyväksy ehdotus</Button>
    </div>}
    {!recorded && editing && <form onSubmit={submit}>
      {/* The library's TextArea, not a raw `<textarea>`, because it wires the label, the hint and the error
          together through `Field`: the error turns on `aria-invalid` and lands in `aria-describedby` on the
          control itself. The raw element it replaces was described only by a separate `role="alert"` line
          that no field pointed at, so the failure was announced once and the field stayed "valid"
          (WCAG 3.3.1 / 1.3.1). The live announcement is preserved by the visually-hidden alert below. */}
      <Field label="Muokkaa ehdotuksen tekstiä"
        error={review?.status === 'failed' ? `Palvelu hylkäsi tallennuksen: ${review.error}` : undefined}>
        <Textarea id={`proposal-edit-${proposal.proposal_id}`}
          required maxLength={2000} value={text} onChange={event => setText(event.target.value)} />
      </Field>
      <Button type="submit" variant="primary" disabled={disabled || !text.trim()}
        title={!text.trim() ? 'Kirjoita ensin muokattu teksti.' : disabled ? disabledReason : undefined}
        loading={review?.status === 'saving'}>Tallenna muokkaus</Button>
    </form>}
    {review?.status === 'saving' && <p className="t-caption brief-inline-status brief-transient-status" role="status">Tallennetaan…</p>}
    {/* A loaded decision and a live save reach the same state, so a reload renders what the click did: the
        action taken and when it was recorded. The action is omitted when the record could not carry it
        (an older `saved` entry), and the time is a machine-readable `<time>` as well as Finnish text. */}
    {review?.status === 'saved' && <p className="t-caption brief-inline-status brief-transient-status" role="status">
      Tallennettu palveluun.
      {PROPOSAL_DECISION_LABELS[review.action] && ` Päätös: ${PROPOSAL_DECISION_LABELS[review.action]}.`}
      {review.recordedAt && <> Aika: <time dateTime={review.recordedAt}>{formatDecisionTime(review.recordedAt)}</time>.</>}
    </p>}
    {/* The caller's own duplicate, reported by the server's `reason: already_decided`. It is not a generic
        rejection: the click did exactly what the panel would have done had it known, so it is announced as
        the earlier decision it is and no second decision is offered. */}
    {review?.status === 'decided' && <p className="t-caption brief-inline-status brief-transient-status" role="status">
      Olet jo päättänyt tämän ehdotuksen. Tallennettua päätöstä ei voi tehdä uudelleen.
    </p>}
    {review?.status === 'ambiguous' && <div className="t-caption brief-inline-status brief-transient-status" role="alert">
      <p>Tallennuksen tila on epäselvä: {review.error} Tee sama tallennus uudelleen samalla tapahtumatunnisteella ennen muutosta.</p>
      <Button variant="primary" onClick={() => onRetry(proposal)}>Yritä samaa tallennusta uudelleen</Button>
    </div>}
    {review?.status === 'failed' && <p className="t-caption brief-inline-status brief-transient-status" role="alert" id={failureId}>Palvelu hylkäsi tallennuksen: {review.error}</p>}
  </article>;
}

function verdictReason(reason) {
  return ({
    supported: 'tuettu', unsupported: 'ei tuettu', unknown: 'näyttö ei riitä',
    empty_narration: 'tyhjä lause', invalid_support_ids: 'virheelliset lähdetunnukset',
    missing_verdict: 'arvio puuttuu', invalid_verdicts: 'virheellinen arvio',
  })[reason] ?? reason;
}

function supportStatus(status) {
  return ({ not_run: 'ei ajettu', supported: 'tuettu', unsupported: 'ei tuettu', unknown: 'tuntematon', invalid: 'virheellinen', timed_out: 'aikaraja' })[status] ?? status ?? 'ei tietoa';
}

// The grounding row says what the verification did, not what the reason code alone suggests.
//
// `narration_rejection_reason` carries `partial_support` whenever *any* generated sentence was withheld,
// but in that case the briefing is still ACCEPTED (`narration_acceptance_status === 'accepted'` and the
// support status is `supported`). The old wording — "Grounding: verifioitu · hylkäyksen syy:
// partial_support" — therefore asserted a rejection that never happened and contradicted the sentence
// above it. This row now states (a) whether verification ran at all — it did not when support is
// `not_run`, e.g. Foundry unavailable — (b) whether the briefing was accepted, and (c) only when
// sentences were actually withheld, that some were left unshown for insufficient support. The machine
// code (`partial_support` or whatever else `rejection_reason` carries) stays visible for audit; it just
// no longer leads the sentence as though it were a rejection.
function groundingLine(narration, rejectedSentences) {
  const reason = narration.rejectionReason ? ` · syykoodi: ${narration.rejectionReason}` : '';
  const verified = narration.supportStatus === 'not_run' ? 'verifiointia ei ajettu' : 'verifiointi ajettu';
  if (narration.acceptanceStatus !== 'accepted') return `Lähdetarkistus: ${verified} · koostetta ei hyväksytty${reason}.`;
  if (rejectedSentences.length > 0) {
    return `Lähdetarkistus: ${verified} · kooste hyväksytty, mutta osa lauseista jätettiin näyttämättä,`
      + ` koska niiden tuki lähteissä jäi riittämättömäksi${reason}.`;
  }
  return `Lähdetarkistus: ${verified} · kooste hyväksytty · kaikki lauseet vahvistettiin lähteisiin${reason}.`;
}
function formatDate(value) {
  if (!value) return 'Päiväys puuttuu';
  const parsed = new Date(value);
  return Number.isNaN(parsed.valueOf()) ? value : new Intl.DateTimeFormat('fi-FI', { dateStyle: 'medium', timeZone: 'UTC' }).format(parsed);
}
function formatDateTime(value) {
  const parsed = new Date(value);
  return Number.isNaN(parsed.valueOf()) ? value : new Intl.DateTimeFormat('fi-FI', {
    dateStyle: 'medium', timeStyle: 'short', timeZone: 'Europe/Helsinki',
  }).format(parsed);
}
