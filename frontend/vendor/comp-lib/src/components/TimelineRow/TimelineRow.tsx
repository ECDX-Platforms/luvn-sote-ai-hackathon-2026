import {
  createContext,
  forwardRef,
  useContext,
  useId,
  useRef,
  useState,
  type CSSProperties,
  type HTMLAttributes,
  type MouseEvent,
  type ReactNode,
} from 'react';
import { ChevronDown, ChevronRight } from 'lucide-react';
import { SourceReference, type SourceReferenceProps } from '../SourceReference';
import { Tag } from '../Tag';
import { compareIso, formatDay } from './dates';
import styles from './TimelineRow.module.css';

/** At most this many records show in an open row, then "and N more" (Figma). */
export const TIMELINE_ROW_MAX_RECORDS = 10;

/** One record shown in an open row: date · title · facts · Source reference. */
export interface TimelineRecord {
  /** Stable key. */
  id: string;
  /** The record's date, as an ISO day ("2026-09-20"). Records are shown newest first by this date. Shown as "20 Sep 2026". */
  date: string;
  /** For a record that covers a period: its end, as an ISO day. Shown as "20 Sep 2026 – 13 Oct 2026". */
  end?: string;
  /** The record's title (body-strong). */
  title: ReactNode;
  /** One line of facts (caption, text-muted), e.g. "Code: A1 · From: 20 Sep 2026 · Rows in source: 1". */
  facts?: ReactNode;
  /** The record is a draft: a "Draft" tag follows the title. A draft is never shown as settled. */
  draft?: boolean;
  /** Where the record came from: an inline Source reference (a button that opens the Source dialog). `date` is the date as shown. */
  source?: Pick<SourceReferenceProps, 'label' | 'date' | 'origin' | 'draft' | 'onOpen'>;
}

export interface TimelineRowProps extends Omit<HTMLAttributes<HTMLDivElement>, 'children'> {
  /** Figma: Label. The item's name (body-strong): the one thing in the row the eye lands on. */
  label: ReactNode;
  /** Figma: Meta. One caption line under the name: code · kind · latest or end. Counts are totals and match the marks. A bar that runs past today stops at today: put the planned end here ("active, to 3 Mar 2027 (planned)"), never in the track. */
  meta?: ReactNode;
  /** Figma: Marks (slot). TimelineMark elements placed by `at` / `until` (months from the start of the track). */
  marks?: ReactNode;
  /** The row's records, shown when the row is open: newest first, at most 10, then "and N more". Records after today must be left out before they are passed in. */
  records?: TimelineRecord[];
  /** Figma: State = Open. Controlled: use with `onOpenChange`. */
  open?: boolean;
  /** Open at first (uncontrolled). */
  defaultOpen?: boolean;
  /** Called with the new open state when people press the row. */
  onOpenChange?: (open: boolean) => void;
  /** Length of the track in months (64px each). Inside a Timeline the Timeline sets it; on its own the default is 6. */
  months?: number;
  /** id for the row's button, e.g. to move focus to it from a change list. */
  buttonId?: string;
}

/** Set by Timeline: the row sits in its sideways-scrolling region, so the focus ring follows the visible part of the row. */
export const TimelineScrollContext = createContext(false);

function RecordDate({ date, end }: Pick<TimelineRecord, 'date' | 'end'>) {
  return (
    <span className={[styles.recordDate, 't-caption'].join(' ')}>
      <time dateTime={date}>{formatDay(date)}</time>
      {end ? (
        <>
          {' – '}
          <time dateTime={end}>{formatDay(end)}</time>
        </>
      ) : null}
    </span>
  );
}

/**
 * Timeline row: one item on the timeline (an item, a result series, contacts, a service). NEW: the styleguide's
 * Timeline (client view) row. Figma: Data display / Timeline row (239:3026); State Default 239:2970, Hover 239:2981,
 * Open 239:2992. The whole row is one button (aria-expanded) that opens the row's records right under it.
 */
export const TimelineRow = forwardRef<HTMLDivElement, TimelineRowProps>(function TimelineRow(
  { label, meta, marks, records = [], open, defaultOpen = false, onOpenChange, months, buttonId, className, style, ...rest },
  ref,
) {
  const inScroll = useContext(TimelineScrollContext);
  const [own, setOwn] = useState(defaultOpen);
  const isOpen = open ?? own;
  const recordsId = useId();
  const buttonRef = useRef<HTMLButtonElement>(null);

  const toggle = () => {
    if (open === undefined) setOwn(!isOpen);
    onOpenChange?.(!isOpen);
  };

  // The whole row is the target. The button holds the row's name, so it is the one thing a keyboard or screen reader
  // meets; a press anywhere else on the row is passed on to it (and focuses it, also in Safari).
  const onRowClick = (event: MouseEvent<HTMLDivElement>) => {
    const button = buttonRef.current;
    if (!button || button.contains(event.target as Node)) return;
    button.focus({ preventScroll: true });
    button.click();
  };

  const sorted = [...records].sort((a, b) => compareIso(b.date, a.date) || compareIso(b.end ?? b.date, a.end ?? a.date));
  const shown = sorted.slice(0, TIMELINE_ROW_MAX_RECORDS);
  const more = sorted.length - shown.length;
  const hasRecords = shown.length > 0;

  const vars = months !== undefined ? ({ '--tl-months': months } as CSSProperties) : undefined;

  return (
    <div
      {...rest}
      ref={ref}
      className={[styles.item, inScroll ? styles.inScroll : undefined, className].filter(Boolean).join(' ')}
      style={{ ...vars, ...style }}
    >
      {/* Figma: Main. Pointer presses are forwarded to the button; keyboard users use the button itself. */}
      <div className={[styles.row, isOpen ? styles.open : undefined].filter(Boolean).join(' ')} onClick={onRowClick}>
        <div className={styles.label}>
          <button
            ref={buttonRef}
            id={buttonId}
            type="button"
            className={styles.button}
            aria-expanded={isOpen}
            aria-controls={hasRecords ? recordsId : undefined}
            onClick={toggle}
          >
            <span className={[styles.name, 't-body-strong'].join(' ')}>{label}</span>
            {meta ? (
              <>
                {' '}
                <span className={[styles.meta, 't-caption'].join(' ')}>{meta}</span>
              </>
            ) : null}
          </button>
        </div>
        {/* Decorative: the button names the row; the marks are aria-hidden. */}
        <div className={styles.track} aria-hidden>
          {marks}
        </div>
        <div className={styles.arrow} aria-hidden>
          {isOpen ? <ChevronDown size={16} strokeWidth={1.5} /> : <ChevronRight size={16} strokeWidth={1.5} />}
        </div>
      </div>
      {hasRecords ? (
        <div id={recordsId} className={styles.records} hidden={!isOpen}>
          <ul className={styles.recordList}>
            {shown.map((record) => (
              <li key={record.id} className={styles.record}>
                <RecordDate date={record.date} end={record.end} />
                <div className={styles.recordBody}>
                  <p className={styles.recordTitle}>
                    <span className="t-body-strong">{record.title}</span>
                    {record.draft ? (
                      <>
                        {' '}
                        <Tag>Draft</Tag>
                      </>
                    ) : null}
                  </p>
                  {record.facts ? <p className={[styles.facts, 't-caption'].join(' ')}>{record.facts}</p> : null}
                  {record.source ? (
                    <div className={styles.source}>
                      <SourceReference {...record.source} standalone />
                    </div>
                  ) : null}
                </div>
              </li>
            ))}
          </ul>
          {more > 0 ? <p className={[styles.more, 't-caption'].join(' ')}>and {more} more</p> : null}
        </div>
      ) : null}
    </div>
  );
});
