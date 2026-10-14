import { forwardRef, useId, useState, type HTMLAttributes, type ReactNode } from 'react';
import { ShowAllButton } from '../ShowAllButton';
import styles from './RecordBlock.module.css';

export type RecordBlockHeadingLevel = 2 | 3 | 4;

export interface RecordBlockRow {
  /** A stable key for the row (e.g. the record's id). Falls back to the row's position. */
  id?: string;
  /** Figma: Row name. Body-strong, left. One key fact's name; wraps when long. */
  name: ReactNode;
  /** Figma: Row meta. Caption, text-muted, right. The one key fact ("Record type · 4 Sep 2026"). Follow the Content rules for dates and put a non-breaking space inside each date so it never splits. */
  meta?: ReactNode;
}

export interface RecordBlockProps extends Omit<HTMLAttributes<HTMLElement>, 'title' | 'children'> {
  /** Figma: Title. The kind of record ("Kind A"). Title-sm, sentence case, short. Names the section for screen readers too. */
  title: ReactNode;
  /** Heading level of the title. Default 2: blocks sit under the page's h1. */
  headingLevel?: RecordBlockHeadingLevel;
  /**
   * Figma: Rows. The client's records of this kind that the role may see, in the order to show them. Never empty:
   * with no rows (and no `hiddenRow`) nothing is rendered; a kind with no records goes into the Not on record line
   * instead. Figma: Length = Short (up to 4 rows, no button) or Cut at 4 (more than 4 rows: the first 4, then the
   * button). The count on the button is `rows.length`; hidden sensitive items are never counted.
   */
  rows: RecordBlockRow[];
  /**
   * PROPOSAL (Figma Hidden item row 195:1631, 13 Oct), off by default; not for use until the devs agree. A slot for
   * the "hidden sensitive information" row of this list: always rendered last in the list, in every Length, and never
   * counted. The component does not provide the row itself. With no `rows`, a block that has only this row is shown
   * (a kind whose only records are hidden is not missing).
   */
  hiddenRow?: ReactNode;
  /** Figma: Length = Show all. Controlled open state of a long list. Use with `onExpandedChange`, or use `defaultExpanded` instead. Ignored when the list has 4 rows or fewer. */
  expanded?: boolean;
  /** Open on first render (uncontrolled). */
  defaultExpanded?: boolean;
  /** Called with the new open state when the Show all button is toggled. */
  onExpandedChange?: (expanded: boolean) => void;
}

/** The list is cut after this many rows (Figma: Cut at 4). */
const CUT_AT = 4;

/**
 * NEW. Record block: one kind of record, shown only when the client has at least one. A title-sm title, a list of
 * rows (name left, meta right), and, when there are more than 4 rows, a Show all button that opens the rest in place.
 * Never an empty block. Styleguide v2.3: Thin record page, Block (section).
 */
export const RecordBlock = forwardRef<HTMLElement, RecordBlockProps>(function RecordBlock(
  { title, headingLevel = 2, rows, hiddenRow, expanded, defaultExpanded = false, onExpandedChange, className, ...rest },
  ref,
) {
  const titleId = useId();
  const listId = useId();
  const [ownExpanded, setOwnExpanded] = useState(defaultExpanded);
  const isControlled = expanded !== undefined;
  const open = isControlled ? expanded : ownExpanded;
  const Heading = `h${headingLevel}` as const;

  // Never an empty block. A kind with no records is named in the Not on record line instead.
  if (rows.length === 0 && !hiddenRow) return null;

  const cut = rows.length > CUT_AT;
  const shown = cut && !open ? rows.slice(0, CUT_AT) : rows;

  const handleExpandedChange = (next: boolean) => {
    if (!isControlled) setOwnExpanded(next);
    onExpandedChange?.(next);
  };

  return (
    <section {...rest} ref={ref} className={[styles.block, className].filter(Boolean).join(' ')} aria-labelledby={titleId}>
      <Heading id={titleId} className={[styles.title, 't-title-sm'].join(' ')}>{title}</Heading>
      {/* role="list": Safari drops list semantics when list-style is none. */}
      <ul id={listId} className={styles.rows} role="list">
        {shown.map((row, index) => (
          <li key={row.id ?? index} className={styles.row}>
            <span className={[styles.name, 't-body-strong'].join(' ')}>{row.name}</span>
            {row.meta ? <span className={[styles.meta, 't-caption'].join(' ')}>{row.meta}</span> : null}
          </li>
        ))}
        {hiddenRow ? <li className={styles.hidden}>{hiddenRow}</li> : null}
      </ul>
      {cut ? (
        <ShowAllButton
          count={rows.length}
          expanded={open}
          onExpandedChange={handleExpandedChange}
          aria-controls={listId}
          aria-describedby={titleId}
        />
      ) : null}
    </section>
  );
});
