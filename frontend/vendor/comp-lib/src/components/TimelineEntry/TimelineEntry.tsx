import { forwardRef, type HTMLAttributes, type LiHTMLAttributes, type ReactNode } from 'react';
import { Tag } from '../Tag';
import styles from './TimelineEntry.module.css';

export type TimelineHeadingLevel = 3 | 4 | 5 | 6;

/** True when a slot has something to show: not undefined, null, false, an empty string or an empty array. */
const isSet = (value: ReactNode) =>
  value !== undefined && value !== null && value !== false && value !== '' && !(Array.isArray(value) && value.length === 0);

export interface TimelineProps extends HTMLAttributes<HTMLOListElement> {
  /** The entries: TimelineEntry elements, in time order. */
  children: ReactNode;
}

/** Timeline (`<ol>`: the order is the time order). Draws the hairline between entries; the first entry has none. */
export const Timeline = forwardRef<HTMLOListElement, TimelineProps>(function Timeline(
  { className, children, ...rest },
  ref,
) {
  return (
    <ol {...rest} ref={ref} className={[styles.timeline, className].filter(Boolean).join(' ')}>
      {children}
    </ol>
  );
});

export interface TimelineEntryProps extends Omit<LiHTMLAttributes<HTMLLIElement>, 'title'> {
  /**
   * Figma: Title. Body-strong, left. A heading, so screen reader users can jump between entries.
   * A draft record reads as a draft here ("Draft decision: …"), never "granted" or "decided".
   */
  title: ReactNode;
  /**
   * Figma: Show kind + Kind. The kind of entry, shown as a neutral Tag in a fixed slot before the title, so titles
   * line up down the timeline. A short noun in sentence case (Figma: Diagnosis, Medicine, Lab result, Decision,
   * Home care). Leave it out for no tag (Figma default: Show kind off).
   */
  kind?: ReactNode;
  /** Figma: Meta (first part). Who or what wrote the entry: a person or a source system. Caption, text-muted, right. */
  author?: ReactNode;
  /** Figma: Meta (second part). The time as shown, following the Content rules: `21 Oct 2024, 09:15`, or `14 Oct, 10:30` in the current year. */
  time?: ReactNode;
  /** Machine-readable time, e.g. `2024-10-21T09:15`. With it, `time` is a `<time>` element; set it whenever `time` is set. */
  dateTime?: string;
  /** Heading level of the title. Default 3: a timeline usually sits in a Block, whose title is an h2. */
  headingLevel?: TimelineHeadingLevel;
  /**
   * Figma: Show top rule. Default true. Set false on the first entry after a Timeline today line (only the today
   * rule sits above it). The first entry of a Timeline never has a rule, whatever this says.
   */
  showTopRule?: boolean;
  /**
   * Figma: Show body + Body. Running text, max 75ch. Paragraphs and lists (indented 16px); key words in `<strong>`,
   * at most two per bullet. Leave it out for a one-line change event (Figma: Show body off): no body row, no gap.
   */
  children?: ReactNode;
}

/** One entry of a timeline: title and meta on one row, the body under it. Renders an `<li>`: put it in a Timeline. Styleguide: Timeline entry. */
export const TimelineEntry = forwardRef<HTMLLIElement, TimelineEntryProps>(function TimelineEntry(
  { title, kind, author, time, dateTime, headingLevel = 3, showTopRule = true, className, children, ...rest },
  ref,
) {
  const Heading = `h${headingLevel}` as const;
  const hasKind = isSet(kind);
  const hasAuthor = isSet(author);
  const hasTime = isSet(time);

  return (
    <li
      {...rest}
      ref={ref}
      className={[styles.entry, !showTopRule && styles.noRule, hasKind && styles.hasKind, className].filter(Boolean).join(' ')}
    >
      <div className={styles.head}>
        {/* With a kind, the tag is part of the heading, so a jump from heading to heading also hears the kind.
            The hidden comma keeps the kind and the title apart when read out ("Decision, Entry title"). */}
        <Heading className={[styles.title, hasKind && styles.titleRow, 't-body-strong'].filter(Boolean).join(' ')}>
          {hasKind ? (
            <>
              <span className={styles.kind}>
                <Tag>{kind}</Tag>
                <span className="visually-hidden">, </span>
              </span>
              <span className={styles.titleText}>{title}</span>
            </>
          ) : (
            title
          )}
        </Heading>
        {hasAuthor || hasTime ? (
          <p className={[styles.meta, 't-caption'].join(' ')}>
            {hasAuthor ? author : null}
            {hasAuthor && hasTime ? ' · ' : null}
            {/* <time> only with a machine-readable value: without dateTime, "14 Oct, 10:30" is not a valid <time>. */}
            {hasTime ? (dateTime ? <time dateTime={dateTime}>{time}</time> : <span className={styles.time}>{time}</span>) : null}
          </p>
        ) : null}
      </div>
      {isSet(children) ? <div className={[styles.body, 't-body'].join(' ')}>{children}</div> : null}
    </li>
  );
});
