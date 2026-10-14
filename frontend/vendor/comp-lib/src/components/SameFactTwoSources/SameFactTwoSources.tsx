import { forwardRef, useId, useState, type HTMLAttributes, type ReactNode } from 'react';
import { ChevronDown, ChevronRight, CircleAlert } from 'lucide-react';
import { SourceReference } from '../SourceReference';
import styles from './SameFactTwoSources.module.css';

export type SameFactTwoSourcesKind = 'agrees' | 'only-in-one-source' | 'described-differently';

/** One source: the record it comes from (shown as an inline Source reference) and the text as written there. */
export interface SameFactSource {
  /** Opens this record in the Source dialog (Source reference `onOpen`). The caller keeps the dialog's open state. */
  onOpen: () => void;
  /** The record type ("Meeting note"). Same words as in every other Source reference. */
  label: string;
  /** The record date, content format ("14 Aug 2026"). Never broken. */
  date: string;
  /** "Health care" or "Social care". Not shown inline, but read out as part of the reference's button name. */
  origin?: string;
  /** The record is a draft: the Source reference shows "· Draft". A draft is never shown as settled. */
  draft?: boolean;
  /** The text as written in this source, word for word (quotes stay quotes). Never reworded or merged. */
  text: ReactNode;
  /**
   * The text is a social-care classification: adds the caption "A social-care classification. Not a clinical
   * diagnosis." under it, so it is never read as a diagnosis.
   */
  socialCareClassification?: boolean;
}

type BaseProps = Omit<HTMLAttributes<HTMLDivElement>, 'children'>;

/** Figma: Kind = Agrees. One row per pair where every compared item matches; the row opens both texts. */
export interface SameFactAgreesProps extends BaseProps {
  /** Figma: Kind. */
  kind: 'agrees';
  /** Figma: Topic. The fact both sources record ("Item A, weekly"), body-strong. */
  topic: ReactNode;
  /**
   * Figma: Count + Show count. Optional caption after the topic ("3 of 3 earlier versions match"). Shown when given.
   * Counts only what the reader may see: hidden pairs are never counted.
   */
  count?: ReactNode;
  /**
   * Figma: Meta. Caption on the right. Default: "Agrees in 2 sources · <newer label> · <older label>", built from the
   * two sources. Override only when the record names need other words.
   */
  meta?: ReactNode;
  /** The newer source, left (on top below 640px). On the same date the structured record (a list or table) comes first. */
  newer: SameFactSource;
  /** The older source, right (below on phones). */
  older: SameFactSource;
  /** Figma: State = Open. Controlled open state. Use with `onExpandedChange`, or use `defaultExpanded` instead. */
  expanded?: boolean;
  /** Open on first render (uncontrolled). */
  defaultExpanded?: boolean;
  /** Called with the new open state when the row is pressed (click, Enter or Space). */
  onExpandedChange?: (expanded: boolean) => void;
}

/** Figma: Kind = Only in one source. A value found in only one record. */
export interface SameFactOnlyInOneSourceProps extends BaseProps {
  /** Figma: Kind. */
  kind: 'only-in-one-source';
  /** Figma: Value. The value as the one source has it ("Version 2.4"), body-strong. */
  value: ReactNode;
  /**
   * Figma: Value meta, without the leading "·" (the component draws it). Caption: where the value is
   * ("value only in the note").
   */
  valueMeta: ReactNode;
  /** What the other source has instead ("3 entries in the version table, none with a value."). Caption. */
  otherSource: ReactNode;
  /** The source that has the value. Its `text` is the quote, word for word, shown in caption above its Source reference. */
  source: SameFactSource;
}

/** Figma: Kind = Described differently. Two records describe the same thing differently; both are shown, no winner. */
export interface SameFactDescribedDifferentlyProps extends BaseProps {
  /** Figma: Kind. */
  kind: 'described-differently';
  /** Figma: Subject. What both records describe ("Project scope, as each record describes it"), body-strong. */
  subject: ReactNode;
  /** The newer source, left (on top below 640px). On the same date the structured record comes first. */
  newer: SameFactSource;
  /** The older source, right (below on phones). */
  older: SameFactSource;
}

export type SameFactTwoSourcesProps = SameFactAgreesProps | SameFactOnlyInOneSourceProps | SameFactDescribedDifferentlyProps;

const cx = (...names: Array<string | false | undefined>) => names.filter(Boolean).join(' ');

/** One column of the two-sources area: Source reference, then the text word for word, then the classification caption. */
function Column({ source, className }: { source: SameFactSource; className: string }) {
  return (
    <li className={cx(styles.column, className)}>
      <SourceReference standalone onOpen={source.onOpen} label={source.label} date={source.date} origin={source.origin} draft={source.draft} />
      <div className={cx(styles.text, 't-body')}>{source.text}</div>
      {source.socialCareClassification ? (
        <p className={cx(styles.note, 't-caption')}>A social-care classification. Not a clinical diagnosis.</p>
      ) : null}
    </li>
  );
}

/** Both sources side by side, newest left; stacked below 640px. role="list": Safari drops list semantics when list-style is none. */
function TwoSources({ newer, older, className, id, hidden }: { newer: SameFactSource; older: SameFactSource; className?: string; id?: string; hidden?: boolean }) {
  return (
    <ul id={id} hidden={hidden} className={cx(styles.sources, className)} role="list">
      <Column source={newer} className={styles.newer} />
      <Column source={older} className={styles.older} />
    </ul>
  );
}

const Agrees = forwardRef<HTMLDivElement, SameFactAgreesProps>(function Agrees(
  { kind: _kind, topic, count, meta, newer, older, expanded, defaultExpanded = false, onExpandedChange, className, ...rest },
  ref,
) {
  const sourcesId = useId();
  const [ownExpanded, setOwnExpanded] = useState(defaultExpanded);
  const isControlled = expanded !== undefined;
  const open = isControlled ? expanded : ownExpanded;
  const toggle = () => {
    const next = !open;
    if (!isControlled) setOwnExpanded(next);
    onExpandedChange?.(next);
  };
  const Arrow = open ? ChevronDown : ChevronRight;

  return (
    <div {...rest} ref={ref} className={cx(styles.item, styles.agrees, className)}>
      <button type="button" className={styles.row} aria-expanded={open} aria-controls={sourcesId} onClick={toggle}>
        <span className={styles.topic}>
          <span className={cx(styles.wrap, 't-body-strong')}>{topic}</span>
          {count ? <>{' '}<span className={cx(styles.wrap, 't-caption')}>{count}</span></> : null}
        </span>{' '}
        <span className={styles.meta}>
          <span className={styles.alertIcon} aria-hidden>
            <CircleAlert size={16} strokeWidth={1.5} />
          </span>
          <span className={cx(styles.wrap, 't-caption')}>
            {meta ?? `Agrees in 2 sources · ${newer.label} · ${older.label}`}
          </span>
          <span className={styles.arrow} aria-hidden>
            <Arrow size={16} strokeWidth={1.5} />
          </span>
        </span>
      </button>
      <TwoSources id={sourcesId} hidden={!open} newer={newer} older={older} className={styles.openSources} />
    </div>
  );
});

const OnlyInOneSource = forwardRef<HTMLDivElement, SameFactOnlyInOneSourceProps>(function OnlyInOneSource(
  { kind: _kind, value, valueMeta, otherSource, source, className, ...rest },
  ref,
) {
  return (
    <div {...rest} ref={ref} className={cx(styles.item, styles.onlyInOne, className)}>
      <p className={styles.valueHead}>
        <span className={cx(styles.wrap, 't-body-strong')}>{value}</span>{' '}
        <span className={cx(styles.wrap, 't-caption')}>
          <span aria-hidden>·</span> {valueMeta}
        </span>
      </p>
      <p className={cx(styles.wrap, 't-caption')}>{otherSource}</p>
      <div className={cx(styles.wrap, 't-caption')}>{source.text}</div>
      <SourceReference standalone onOpen={source.onOpen} label={source.label} date={source.date} origin={source.origin} draft={source.draft} />
      {source.socialCareClassification ? (
        <p className={cx(styles.wrap, 't-caption')}>A social-care classification. Not a clinical diagnosis.</p>
      ) : null}
    </div>
  );
});

const DescribedDifferently = forwardRef<HTMLDivElement, SameFactDescribedDifferentlyProps>(function DescribedDifferently(
  { kind: _kind, subject, newer, older, className, ...rest },
  ref,
) {
  return (
    <div {...rest} ref={ref} className={cx(styles.item, styles.differently, className)}>
      <div className={styles.subjectHead}>
        <p className={cx(styles.wrap, 't-body-strong')}>{subject}</p>
        <p className={cx(styles.wrap, 't-caption')}>Newest first · no winner · read both</p>
      </div>
      <TwoSources newer={newer} older={older} />
    </div>
  );
});

/**
 * Same fact, two sources: one fact recorded in more than one place. Folds what agrees, shows what differs.
 * No winner, no merged value; each source keeps its own words. Three kinds (prop `kind`): Agrees (a row that opens),
 * Only in one source, Described differently. NEW: composed from Source reference and the styleguide's rules.
 * Figma: Data display / Same fact, two sources (178:1046).
 */
export const SameFactTwoSources = forwardRef<HTMLDivElement, SameFactTwoSourcesProps>(function SameFactTwoSources(props, ref) {
  if (props.kind === 'agrees') return <Agrees {...props} ref={ref} />;
  if (props.kind === 'only-in-one-source') return <OnlyInOneSource {...props} ref={ref} />;
  return <DescribedDifferently {...props} ref={ref} />;
});
