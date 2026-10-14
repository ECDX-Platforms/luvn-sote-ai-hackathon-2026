import { forwardRef, useId, useState, type HTMLAttributes, type ReactNode } from 'react';
import { ChevronDown, ChevronRight } from 'lucide-react';
import { Tag } from '../Tag';
import styles from './GroupedRecordRow.module.css';

export interface GroupedRecordRowProps extends Omit<HTMLAttributes<HTMLDivElement>, 'children'> {
  /** Figma: Name. The code's own label from the agreed classification list (e.g. ICD-10), never one record's wording. Body-strong. Wraps when long. */
  name: ReactNode;
  /** Figma: Code. The code the records are grouped by (rule-based, never AI). Caption, text-muted, tabular figures; never broken. */
  code: ReactNode;
  /**
   * Figma: Summary. The one key fact on the collapsed row: "Latest <date>" (medicines: "Since <date>" or "Ended <date>").
   * Caption, text-muted. The date comes only from the records shown (after any cut), never from withheld ones.
   * Dates follow the Content rules (`21 Oct 2024`); put a non-breaking space inside each date so it never splits.
   * Counts and the first date go in `detail`, not here (styleguide v2.2: one key fact per row).
   */
  summary: ReactNode;
  /**
   * Figma: Detail. The line at the top of the open list: "N records · first <date>", or "1 record" for a single record.
   * Caption, text-muted. N counts only the records shown after the cut, never withheld ones; "first" is the earliest
   * shown date. Shown only when the row is open. Optional: leave it out and no line is drawn.
   */
  detail?: ReactNode;
  /** Figma: Long-term. Shows the "Long-term" tag. Only when a source record marks it long-term; never inferred from the number of records. */
  longTerm?: boolean;
  /** Figma: State = Expanded. Controlled open state. Use with `onExpandedChange`, or use `defaultExpanded` instead. */
  expanded?: boolean;
  /** Open on first render (uncontrolled). */
  defaultExpanded?: boolean;
  /** Called with the new open state when the row is clicked or toggled with Enter or Space. */
  onExpandedChange?: (expanded: boolean) => void;
  /**
   * The records, shown when the row is open (under the detail line): a list (`<ul>`) of links, one per record,
   * newest first, indented 24. Use Source reference rows (Layout Row).
   */
  children?: ReactNode;
}

/**
 * NEW. Grouped record row: one item recorded at many visits, shown once. The whole row is a button with
 * aria-expanded that opens the list of records it appears in. `className`, `style`, the ref and every other
 * attribute go on the outer `div`. Styleguide: Data table, Tag, Shared state model.
 */
export const GroupedRecordRow = forwardRef<HTMLDivElement, GroupedRecordRowProps>(function GroupedRecordRow(
  { name, code, summary, detail, longTerm = false, expanded, defaultExpanded = false, onExpandedChange, className, children, ...rest },
  ref,
) {
  const recordsId = useId();
  const [ownExpanded, setOwnExpanded] = useState(defaultExpanded);
  const isControlled = expanded !== undefined;
  const open = isControlled ? expanded : ownExpanded;

  const toggle = () => {
    const next = !open;
    if (!isControlled) setOwnExpanded(next);
    onExpandedChange?.(next);
  };

  const Chevron = open ? ChevronDown : ChevronRight;

  return (
    <div {...rest} ref={ref} className={[styles.group, className].filter(Boolean).join(' ')}>
      <button type="button" className={styles.row} aria-expanded={open} aria-controls={recordsId} onClick={toggle}>
        <span className={styles.lead}>
          <span className={styles.chevron} aria-hidden>
            <Chevron size={16} strokeWidth={1.5} aria-hidden />
          </span>
          <span className={styles.label}>
            <span className={[styles.name, 't-body-strong'].join(' ')}>{name}</span>
            <span className={[styles.code, 't-caption'].join(' ')}>{code}</span>
            {longTerm ? <Tag className={styles.tag}>Long-term</Tag> : null}
          </span>
        </span>
        <span className={[styles.summary, 't-caption'].join(' ')}>{summary}</span>
      </button>
      <div id={recordsId} className={styles.records} hidden={!open}>
        {detail ? <p className={[styles.detail, 't-caption'].join(' ')}>{detail}</p> : null}
        {children}
      </div>
    </div>
  );
});
