import { forwardRef, useId, useLayoutEffect, useRef, useState, type HTMLAttributes, type ReactNode } from 'react';
import { Check } from 'lucide-react';
import { Button } from '../Button';
import { Link } from '../Link';
import { Skeleton } from '../Skeleton';
import { Tag } from '../Tag';
import styles from './AiSummaryBlock.module.css';

export type AiSummaryState = 'shown' | 'partly-withheld' | 'withheld' | 'loading';
/** The professional's answer to the suggestion. none = not answered yet (Keep and Not useful are shown). */
export type AiSummaryFeedback = 'none' | 'not-useful' | 'kept' | 'hidden';
/** The four preset reasons after "Not useful" (one tap, optional). */
export type AiSummaryReason = 'wrong' | 'not-relevant' | 'already-known' | 'too-long';
export type AiSummaryHeadingLevel = 2 | 3 | 4;

export interface AiSummarySentence {
  /** The sentence, with the source's exact figures (no hedges like "about"). A sentence supported by a draft says "draft" in its words. */
  text: ReactNode;
  /** Its own source: a Source reference (inline layout) that opens the one record in the Source dialog. Draft records show "· Draft". */
  source: ReactNode;
  /** ID of the record the source points to. "Based on" counts distinct records from these, not citations. */
  recordId: string;
}

interface AiSummaryBlockBaseProps extends Omit<HTMLAttributes<HTMLElement>, 'title' | 'children'> {
  /** Figma: Question. The title: the one question this role asks, in title-sm. The sentences answer it. Shown in every state. */
  question: string;
  /** How the text was made, in caption next to the Suggestion tag. Shown in every state except Withheld. Default `labels.method`. */
  method?: ReactNode;
  /** Sentences, at most 4 (only the first 4 are shown). Only records the role is allowed to see. Ignored in Withheld and Loading. With no sentences, Shown and Partly withheld fall back to Withheld (fail-closed). */
  sentences?: AiSummarySentence[];
  /** Sentences that failed the check against their own sources and are not shown. Shown in the footer ("withheld: N sentences"). Default 0 in Shown, 1 in Partly withheld. Never counts records the role may not see. */
  withheldCount?: number;
  /** Withheld: the sentence shown instead of the summary. Default `labels.withheldText`. */
  withheldText?: ReactNode;
  /** Where "Based on <n> records" goes: the full source list, one tap away. */
  sourcesHref: string;
  /** Heading level of the question. Default 2, like a Block. Use 3 inside a Block. */
  headingLevel?: AiSummaryHeadingLevel;
  /** Every fixed word the block renders, for another language. Each key defaults to the English (Figma) copy; `reasons` merges per reason. */
  labels?: Partial<AiSummaryBlockLabels>;
}

/** The fixed copy of the block. Defaults word for word from Figma (180:1229, 234:2326, 234:2409, 234:2477). */
export interface AiSummaryBlockLabels {
  /** The neutral tag in the head. */
  suggestion: string;
  /** Default of the `method` prop. */
  method: ReactNode;
  /** Default of the `withheldText` prop. */
  withheldText: ReactNode;
  /** Read out instead of the Loading skeleton bars. */
  loading: string;
  /** Start of the footer line, before the record count link. */
  basedOn: string;
  /** The record count link text ("1 record", "3 records"). */
  records: (count: number) => string;
  /** The withheld count after the "·" ("withheld: 1 sentence (its sources did not support it)"). */
  withheld: (count: number) => string;
  /** The Keep button. */
  keep: string;
  /** The Not useful button. */
  notUseful: string;
  /** The caption under the two buttons. */
  keepCaption: string;
  /** The status line after Keep. */
  keptStatus: string;
  /** The status line after a reason. */
  hiddenStatus: string;
  /** The question after Not useful. */
  ask: string;
  /** The caption after the question ("(optional, one tap)"). */
  askHint: string;
  /** The four reason buttons. */
  reasons: Record<AiSummaryReason, string>;
  /** The Undo button. */
  undo: string;
}

/**
 * Shown and Partly withheld: a suggestion with text always has "Keep for this visit" and an equal-weight "Not useful"
 * (styleguide rule 13), so the handlers that change something outside the block are required.
 */
interface AiSummaryWithTextProps extends AiSummaryBlockBaseProps {
  /** Figma: State (Shown, Partly withheld, Withheld, Loading). The feedback states are the `feedback` prop. */
  state?: 'shown' | 'partly-withheld';
  /**
   * Figma: State (Not useful, Kept, Hidden for this visit). Controlled: pass it together with the handlers and update it
   * from them. Leave it out to let the block keep the feedback itself (start value `defaultFeedback`).
   */
  feedback?: AiSummaryFeedback;
  /** Uncontrolled start value. Default "none". Ignored when `feedback` is given. */
  defaultFeedback?: AiSummaryFeedback;
  /** "Keep for this visit": add the summary to the visit preparation. Never writes to the record. Next state: kept. */
  onKeep: () => void;
  /** "Not useful": the block asks for an optional reason. Nothing is hidden yet. Next state: not-useful. Required, so the reject always works (rule 13). */
  onNotUseful: () => void;
  /** One of the four reasons was chosen: save it and hide the summary for this visit. Next state: hidden. */
  onReason: (reason: AiSummaryReason) => void;
  /** "Undo" in Kept or Hidden for this visit: reverse what was saved. Gets the state that is undone. Next state: none. */
  onUndo: (undone: 'kept' | 'hidden') => void;
}

/** Withheld and Loading: no actions and no feedback. Handlers may be passed (e.g. when the state changes at run time) and are not used. */
interface AiSummaryWithoutTextProps extends AiSummaryBlockBaseProps {
  /** Figma: State. Withheld = no AI text at all (fail-closed). Loading = static skeleton bars, aria-busy. */
  state: 'withheld' | 'loading';
  feedback?: AiSummaryFeedback;
  defaultFeedback?: AiSummaryFeedback;
  onKeep?: () => void;
  onNotUseful?: () => void;
  onReason?: (reason: AiSummaryReason) => void;
  onUndo?: (undone: 'kept' | 'hidden') => void;
}

export type AiSummaryBlockProps = AiSummaryWithTextProps | AiSummaryWithoutTextProps;

const MAX_SENTENCES = 4;

const AI_SUMMARY_REASONS: readonly AiSummaryReason[] = ['wrong', 'not-relevant', 'already-known', 'too-long'];

// Fixed copy: word for word from Figma (180:1229, 234:2326, 234:2409, 234:2477).
const DEFAULT_LABELS: AiSummaryBlockLabels = {
  suggestion: 'Suggestion',
  method: 'AI-written · each sentence checked against its sources',
  withheldText: 'No summary is shown. Read the records below.',
  loading: 'Loading summary…',
  basedOn: 'Based on',
  records: (n) => (n === 1 ? '1 record' : `${n} records`),
  // Figma shows 0 and 1. The plural reason wording for 2 or more is ours.
  withheld: (n) =>
    n === 0 ? 'withheld: 0 sentences'
      : n === 1 ? 'withheld: 1 sentence (its sources did not support it)'
        : `withheld: ${n} sentences (their sources did not support them)`,
  keep: 'Keep for this visit',
  notUseful: 'Not useful',
  keepCaption: 'Keep adds it to your visit preparation. It never writes to the record.',
  keptStatus: 'Kept for your visit preparation. Nothing was written to the record.',
  hiddenStatus: 'Reason saved. The summary is hidden for this visit.',
  ask: 'Thanks. Why was it not useful?',
  askHint: '(optional, one tap)',
  reasons: {
    wrong: 'Wrong',
    'not-relevant': 'Not relevant for my role',
    'already-known': 'Already known',
    'too-long': 'Too long',
  },
  undo: 'Undo',
};

type FocusTarget = 'keep' | 'notUseful' | 'firstReason' | 'undo';

/**
 * AI-written answer to one role question, shown as a suggestion: Suggestion tag, method line, the question,
 * numbered sentences each with their own source, "Based on" line, and equal-weight "Keep for this visit" and "Not useful".
 * NEW as a component. Figma: Data display / AI summary block (180:1229).
 * Styleguide: rule 13, Suggestion (system output), Empty, loading and error states, Accessibility (4.1.3).
 */
export const AiSummaryBlock = forwardRef<HTMLElement, AiSummaryBlockProps>(function AiSummaryBlock(
  {
    state = 'shown',
    question,
    method,
    sentences = [],
    withheldCount,
    withheldText,
    sourcesHref,
    feedback: feedbackProp,
    defaultFeedback = 'none',
    onKeep,
    onNotUseful,
    onReason,
    onUndo,
    headingLevel = 2,
    labels,
    className,
    ...rest
  },
  ref,
) {
  const t: AiSummaryBlockLabels = { ...DEFAULT_LABELS, ...labels, reasons: { ...DEFAULT_LABELS.reasons, ...labels?.reasons } };
  const shownMethod = method ?? t.method;
  const shownWithheldText = withheldText ?? t.withheldText;
  const questionId = useId();
  const askId = useId();
  const Heading = `h${headingLevel}` as const;

  const [ownFeedback, setOwnFeedback] = useState<AiSummaryFeedback>(defaultFeedback);
  const controlled = feedbackProp !== undefined;
  const setFeedback = (next: AiSummaryFeedback) => { if (!controlled) setOwnFeedback(next); };

  // Fail-closed: Shown or Partly withheld without any sentence is shown as Withheld (no "Based on 0 records", no actions).
  const effectiveState: AiSummaryState =
    (state === 'shown' || state === 'partly-withheld') && sentences.length === 0 ? 'withheld' : state;
  const withText = effectiveState === 'shown' || effectiveState === 'partly-withheld';
  // Feedback only exists for a summary with text. Withheld and Loading have none.
  const feedback: AiSummaryFeedback = withText ? (controlled ? feedbackProp : ownFeedback) : 'none';
  const showSentences = withText && feedback !== 'hidden';
  const shownSentences = showSentences ? sentences.slice(0, MAX_SENTENCES) : [];
  const recordCount = new Set(shownSentences.map((s) => s.recordId)).size;
  const withheld = withheldCount ?? (effectiveState === 'partly-withheld' ? 1 : 0);

  // Focus moves (Figma): Not useful → first reason; Keep or a reason → Undo; Undo → the button that started it.
  // The move waits until the block shows the expected feedback, so it also works when the app controls `feedback`.
  const keepRef = useRef<HTMLButtonElement>(null);
  const notUsefulRef = useRef<HTMLButtonElement>(null);
  const firstReasonRef = useRef<HTMLButtonElement>(null);
  const undoRef = useRef<HTMLButtonElement>(null);
  const pendingFocus = useRef<{ expect: AiSummaryFeedback; target: FocusTarget } | null>(null);
  useLayoutEffect(() => {
    const pending = pendingFocus.current;
    pendingFocus.current = null;
    if (!pending || pending.expect !== feedback) return;
    const targets = { keep: keepRef, notUseful: notUsefulRef, firstReason: firstReasonRef, undo: undoRef };
    targets[pending.target].current?.focus();
  }, [feedback]);

  const keep = () => {
    pendingFocus.current = { expect: 'kept', target: 'undo' };
    setFeedback('kept');
    onKeep?.();
  };
  const notUseful = () => {
    pendingFocus.current = { expect: 'not-useful', target: 'firstReason' };
    setFeedback('not-useful');
    onNotUseful?.();
  };
  const reason = (value: AiSummaryReason) => {
    pendingFocus.current = { expect: 'hidden', target: 'undo' };
    setFeedback('hidden');
    onReason?.(value);
  };
  const undo = () => {
    if (feedback !== 'kept' && feedback !== 'hidden') return;
    pendingFocus.current = { expect: 'none', target: feedback === 'kept' ? 'keep' : 'notUseful' };
    setFeedback('none');
    onUndo?.(feedback);
  };

  const status = feedback === 'kept' ? t.keptStatus : feedback === 'hidden' ? t.hiddenStatus : null;

  return (
    <section
      {...rest}
      ref={ref}
      className={[styles.summary, className].filter(Boolean).join(' ')}
      aria-labelledby={questionId}
      aria-busy={effectiveState === 'loading' || undefined}
    >
      <div className={styles.head}>
        <Tag>{t.suggestion}</Tag>
        {effectiveState === 'withheld' ? null : <p className={[styles.method, 't-caption'].join(' ')}>{shownMethod}</p>}
      </div>

      <Heading id={questionId} className={[styles.question, 't-title-sm'].join(' ')}>{question}</Heading>

      {shownSentences.length > 0 ? (
        // role="list": Safari drops list semantics when list-style is none.
        <ol className={styles.sentences} role="list">
          {shownSentences.map((sentence, index) => (
            <li key={index} className={styles.sentence}>
              {/* The ol already says the position; the visible number is not read twice. */}
              <span className={[styles.number, 't-body'].join(' ')} aria-hidden>{index + 1}.</span>
              {/* div, not p: the source slot holds a button (the Source reference). */}
              <div className={styles.line}>
                <p className={[styles.text, 't-body'].join(' ')}>{sentence.text}</p>
                <div className={styles.source}>{sentence.source}</div>
              </div>
            </li>
          ))}
        </ol>
      ) : null}
      {effectiveState === 'withheld' ? <p className={[styles.withheld, 't-body', 'muted'].join(' ')}>{shownWithheldText}</p> : null}
      {effectiveState === 'loading' ? <Skeleton lines={3} label={t.loading} /> : null}

      {withText ? (
        <div className={styles.footer}>
          {showSentences ? (
            <p className={[styles.basedOn, 't-caption'].join(' ')}>
              {t.basedOn}{' '}
              <Link href={sourcesHref} size="sm">{t.records(recordCount)}</Link>
              {' '}<span aria-hidden>·</span> {t.withheld(withheld)}
            </p>
          ) : null}

          <div className={styles.actions}>
            {/* One live region that stays mounted, so screen readers announce the line when it appears (WCAG 4.1.3).
                Empty, it is taken out of the row's flow but stays in the accessibility tree. */}
            <p role="status" className={[styles.status, 't-body'].join(' ')}>
              {status ? (
                <>
                  <span className={styles.statusIcon} aria-hidden><Check size={16} strokeWidth={1.5} /></span>
                  <span className={styles.statusText}>{status}</span>
                </>
              ) : null}
            </p>

            {feedback === 'none' ? (
              <>
                <Button ref={keepRef} variant="secondary" size="sm" onClick={keep}>{t.keep}</Button>
                <Button ref={notUsefulRef} variant="secondary" size="sm" onClick={notUseful}>{t.notUseful}</Button>
                <p className={[styles.caption, 't-caption'].join(' ')}>{t.keepCaption}</p>
              </>
            ) : null}

            {feedback === 'not-useful' ? (
              <>
                <p id={askId} className={styles.ask}>
                  <span className="t-body">{t.ask}</span>{' '}
                  <span className="t-caption">{t.askHint}</span>
                </p>
                {/* The group is named by the question, so focus on the first reason reads it out. */}
                <div className={styles.reasons} role="group" aria-labelledby={askId}>
                  {AI_SUMMARY_REASONS.map((value, i) => (
                    <Button key={value} ref={i === 0 ? firstReasonRef : undefined} variant="secondary" size="sm" onClick={() => reason(value)}>
                      {t.reasons[value]}
                    </Button>
                  ))}
                </div>
              </>
            ) : null}

            {status ? <Button ref={undoRef} variant="secondary" size="sm" onClick={undo}>{t.undo}</Button> : null}
          </div>
        </div>
      ) : null}
    </section>
  );
});
