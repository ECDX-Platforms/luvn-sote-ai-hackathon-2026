import { forwardRef, isValidElement, useId, type HTMLAttributes, type ReactNode } from 'react';
import { Check } from 'lucide-react';
import { SourceReference } from '../SourceReference';
import { StatusTag } from '../StatusTag';
import styles from './TodayBriefing.module.css';

export type TodayBriefingHeadingLevel = 2 | 3 | 4;

/** The record a line comes from, shown as an inline Source reference (standalone). Same shape as Caution item's source. */
export interface TodayBriefingSource {
  /** Opens this record in the Source dialog (Source reference `onOpen`). The caller keeps the dialog's open state. */
  onOpen: () => void;
  /** The record type ("Meeting note"). */
  label: string;
  /** The record date, content format ("12 Oct 2026"). Never broken inside. */
  date: string;
  /** "Health care" or "Social care". Not shown inline, but read out as part of the button name. */
  origin?: string;
  /** The record is a draft: the reference shows "· Draft". A draft is never shown as settled. */
  draft?: boolean;
}

/**
 * A line quoted word for word from the source, in the source's own language. Rendered in curly quotes (“…”), with
 * `lang` on the quoted words so screen readers read them in that language.
 */
export interface TodayBriefingQuote {
  /** The source's own words. Never translated or shortened. */
  quote: string;
  /** Language of the quote, e.g. "fi". */
  lang?: string;
}

/** Text in a line: plain text, or a word-for-word quote. */
export type TodayBriefingText = ReactNode | TodayBriefingQuote;

/** One summary value in the band: a caption key and a body-strong value. */
export interface TodayBriefingValue {
  /** Key, caption in on-inverse-muted. */
  label: ReactNode;
  /** Value, body-strong in on-inverse. A source value or a plain word, never a computed verdict. */
  value: ReactNode;
}

/** Watch: a reported side effect. Always listed first in Watch (safety first) and never hidden. */
export interface TodayBriefingSideEffect {
  kind: 'side-effect';
  /** The substance or medicine, in body-strong, next to the Status tag "Side effect". */
  name: ReactNode;
  /** The record it comes from. */
  source?: TodayBriefingSource;
}

/** Any other line: a title, a text and a detail, each optional, then its source. Used in Watch and Not on record yet. */
export interface TodayBriefingFact {
  kind?: 'fact';
  /** body-strong, e.g. the name of the list the line comes from. */
  title?: ReactNode;
  /** body. Plain text or a word-for-word quote. */
  text?: TodayBriefingText;
  /** caption, text-muted: a date, a source name, or a quote. */
  detail?: TodayBriefingText;
  /** The record it comes from. */
  source?: TodayBriefingSource;
}

export type TodayBriefingWatchItem = TodayBriefingSideEffect | TodayBriefingFact;

/** Since the last contact: one change (date, text, source), or a count line (text only, e.g. "N contacts recorded"). */
export interface TodayBriefingChange {
  /** caption, text-muted, above the text. Content format ("6 Oct 2026"). Leave out for a count line. */
  date?: string;
  /** body. */
  text: TodayBriefingText;
  /** The record it comes from. */
  source?: TodayBriefingSource;
}

export interface TodayBriefingProps extends Omit<HTMLAttributes<HTMLElement>, 'title' | 'children'> {
  /** Today's date for the caption, content format ("13 Oct 2026"). */
  date: string;
  /** Figma: Role. The role the view is for, shown in the caption: "Today · <date> · <role>". The component shows the same parts for every role. */
  roleLabel: string;
  /** The kind of contact this role has, for the middle column's heading: "Since the last <contact>, <date>". */
  contactLabel: string;
  /** Date of that last contact, for the middle column's heading. Leave out when there is none on record: the heading is then "Since the last <contact>". */
  lastContactDate?: string;
  /** What is today, in title-lg on-inverse: a visit or contact today with its time, or the last one and how long ago. A fact, never a verdict. */
  headline: ReactNode;
  /** One detail line under the headline, body in on-inverse-muted. */
  detail?: ReactNode;
  /** 2 to 3 summary values (only the first 3 are shown). */
  values?: TodayBriefingValue[];
  /** Watch: side effects first (the component puts them first, in their given order), then the role's other lines in the given order. */
  watch?: TodayBriefingWatchItem[];
  /**
   * Slot at the very end of the Watch list, for the Hidden item row (Figma 195:1631). PROPOSAL: the Hidden item row is
   * not in the library; it is not for code until the devs agree. The slot brings its own rules.
   */
  watchHiddenRow?: ReactNode;
  /** Since the last contact: changes with dates and sources, and contacts as one count line. At most 6 lines are shown. Empty = "Nothing new". */
  since?: TodayBriefingChange[];
  /** All lines since the last contact, when the app has more than it passes. Default: `since.length`. Lines past the 6 shown are counted in "and N more in the timeline". */
  sinceTotal?: number;
  /** Not on record yet: what the records leave out, as facts, or the source's own list of missing information, quoted word for word. */
  notOnRecord?: TodayBriefingFact[];
  /** Heading level of the headline. The three column headings are one level lower. Default 2, like a Block. */
  headingLevel?: TodayBriefingHeadingLevel;
}

const MAX_VALUES = 3;
const MAX_SINCE = 6;

// Fixed copy: word for word from Figma (245:4789).
const sideEffectTag = 'Side effect';
const sideEffectCaption = 'Reported side effect, not a confirmed allergy';
const watchHeading = 'Watch';
const notOnRecordHeading = 'Not on record yet';
const nothingNew = 'Nothing new';
const captionText = (date: string, role: string) => `Today · ${date} · ${role}`;
const sinceHeading = (contact: string, date?: string) => (date ? `Since the last ${contact}, ${date}` : `Since the last ${contact}`);
const moreInTimeline = (n: number) => `and ${n} more in the timeline`;

function isQuote(text: TodayBriefingText): text is TodayBriefingQuote {
  return typeof text === 'object' && text !== null && !isValidElement(text) && 'quote' in text;
}

/** Plain text as it is; a quote in curly quotes, with its language on the quoted words. */
function renderText(text: TodayBriefingText) {
  if (!isQuote(text)) return text;
  return (
    <>
      “<span lang={text.lang}>{text.quote}</span>”
    </>
  );
}

function Source({ source }: { source?: TodayBriefingSource }) {
  if (!source) return null;
  return (
    <div className={styles.source}>
      <SourceReference
        standalone
        layout="inline"
        onOpen={source.onOpen}
        label={source.label}
        date={source.date}
        origin={source.origin}
        draft={source.draft}
      />
    </div>
  );
}

function FactLines({ item }: { item: TodayBriefingFact }) {
  return (
    <>
      {item.title ? <p className={[styles.line, 't-body-strong'].join(' ')}>{item.title}</p> : null}
      {item.text ? <p className={[styles.line, 't-body'].join(' ')}>{renderText(item.text)}</p> : null}
      {item.detail ? <p className={[styles.line, 't-caption'].join(' ')}>{renderText(item.detail)}</p> : null}
      <Source source={item.source} />
    </>
  );
}

/**
 * Today briefing: the first block of a role's client view. An inverse band says what is today, with 2 to 3 summary values;
 * under it three columns: Watch · Since the last contact · Not on record yet. Built from record facts only, so it works
 * when the AI summary is withheld. NEW (styleguide v2.3: Today briefing). Figma: Data display / Today briefing (245:4789).
 */
export const TodayBriefing = forwardRef<HTMLElement, TodayBriefingProps>(function TodayBriefing(
  {
    date,
    roleLabel,
    contactLabel,
    lastContactDate,
    headline,
    detail,
    values = [],
    watch = [],
    watchHiddenRow,
    since = [],
    sinceTotal,
    notOnRecord = [],
    headingLevel = 2,
    className,
    ...rest
  },
  ref,
) {
  const captionId = useId();
  const headlineId = useId();
  const Heading = `h${headingLevel}` as const;
  const ColumnHeading = `h${headingLevel + 1}` as 'h3' | 'h4' | 'h5';

  const shownValues = values.slice(0, MAX_VALUES);
  // Safety first: side effects lead the Watch list for every role, whatever order the app passes.
  const watchItems = [
    ...watch.filter((item) => item.kind === 'side-effect'),
    ...watch.filter((item) => item.kind !== 'side-effect'),
  ];
  const shownSince = since.slice(0, MAX_SINCE);
  const more = Math.max(0, (sinceTotal ?? since.length) - shownSince.length);

  return (
    <section
      {...rest}
      ref={ref}
      className={[styles.briefing, className].filter(Boolean).join(' ')}
      aria-labelledby={`${captionId} ${headlineId}`}
    >
      <div className={styles.band}>
        <div className={styles.today}>
          <p id={captionId} className={[styles.caption, 't-caption'].join(' ')}>{captionText(date, roleLabel)}</p>
          <Heading id={headlineId} className={[styles.headline, 't-title-lg'].join(' ')}>{headline}</Heading>
          {detail ? <p className={[styles.detail, 't-body'].join(' ')}>{detail}</p> : null}
        </div>
        {shownValues.length > 0 ? (
          <dl className={styles.values}>
            {shownValues.map((v, i) => (
              <div key={i} className={styles.value}>
                <dt className={[styles.valueLabel, 't-caption'].join(' ')}>{v.label}</dt>
                <dd className={[styles.valueText, 't-body-strong'].join(' ')}>{v.value}</dd>
              </div>
            ))}
          </dl>
        ) : null}
      </div>

      <div className={styles.columns}>
        <div className={styles.column}>
          <ColumnHeading className={[styles.columnHeading, 't-title-sm'].join(' ')}>{watchHeading}</ColumnHeading>
          {watchItems.length > 0 || watchHiddenRow ? (
            // role="list": Safari drops list semantics when list-style is none.
            <ul className={styles.list} role="list">
              {watchItems.map((item, i) =>
                item.kind === 'side-effect' ? (
                  <li key={i} className={styles.item}>
                    <div className={styles.tagLine}>
                      <StatusTag status="warning">{sideEffectTag}</StatusTag>
                      <span className={[styles.name, 't-body-strong'].join(' ')}>{item.name}</span>
                    </div>
                    <p className={[styles.line, 't-caption'].join(' ')}>{sideEffectCaption}</p>
                    <Source source={item.source} />
                  </li>
                ) : (
                  <li key={i} className={styles.item}>
                    <FactLines item={item} />
                  </li>
                ),
              )}
              {watchHiddenRow ? <li className={styles.slot}>{watchHiddenRow}</li> : null}
            </ul>
          ) : null}
        </div>

        <div className={styles.column}>
          <ColumnHeading className={[styles.columnHeading, 't-title-sm'].join(' ')}>
            {sinceHeading(contactLabel, lastContactDate)}
          </ColumnHeading>
          <ul className={styles.list} role="list">
            {shownSince.length === 0 && more === 0 ? (
              <li className={styles.item}>
                <p className={[styles.nothingNew, 't-body'].join(' ')}>
                  <span className={styles.checkIcon} aria-hidden><Check size={16} strokeWidth={1.5} /></span>
                  <span className={styles.line}>{nothingNew}</span>
                </p>
              </li>
            ) : null}
            {shownSince.map((change, i) => (
              <li key={i} className={styles.item}>
                {change.date ? <p className={[styles.line, styles.date, 't-caption'].join(' ')}>{change.date}</p> : null}
                <p className={[styles.line, 't-body'].join(' ')}>{renderText(change.text)}</p>
                <Source source={change.source} />
              </li>
            ))}
            {more > 0 ? (
              <li className={styles.item}>
                <p className={[styles.line, 't-body', 'muted'].join(' ')}>{moreInTimeline(more)}</p>
              </li>
            ) : null}
          </ul>
        </div>

        <div className={styles.column}>
          <ColumnHeading className={[styles.columnHeading, 't-title-sm'].join(' ')}>{notOnRecordHeading}</ColumnHeading>
          {notOnRecord.length > 0 ? (
            <ul className={styles.list} role="list">
              {notOnRecord.map((item, i) => (
                <li key={i} className={styles.item}>
                  <FactLines item={item} />
                </li>
              ))}
            </ul>
          ) : null}
        </div>
      </div>
    </section>
  );
});
