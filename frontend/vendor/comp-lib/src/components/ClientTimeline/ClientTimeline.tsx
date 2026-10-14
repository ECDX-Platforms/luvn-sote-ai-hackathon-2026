import {
  forwardRef,
  useCallback,
  useEffect,
  useId,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type FocusEvent,
  type HTMLAttributes,
  type KeyboardEvent,
  type ReactNode,
} from 'react';
import { ChevronDown, ChevronRight } from 'lucide-react';
import { Button } from '../Button';
import { SegmentedControl } from '../SegmentedControl';
import segment from '../SegmentedControl/SegmentedControl.module.css';
import { TimelineMark, type TimelineMarkKind } from '../TimelineMark';
import { TimelineRow, TimelineScrollContext, type TimelineRecord } from '../TimelineRow/TimelineRow';
import {
  addMonths,
  compareIso,
  dayAt,
  formatDay,
  formatMonth,
  monthName,
  monthsBetween,
  parseDay,
  startOfMonth,
  toIso,
  type Day,
} from '../TimelineRow/dates';
import styles from './ClientTimeline.module.css';

/* ===== Data ===== */

/** A bar: something that runs from a start to an end (or to today). */
export interface TimelineBarData {
  kind: 'running' | 'as-needed' | 'paused';
  /** ISO day the bar starts. Long-term items start at their first record. */
  start: string;
  /** ISO day the bar ends. Leave out (or null) for "no end date": the bar runs to today. An end after today is cut at today; put the planned end in the row's meta. */
  end?: string | null;
}
/** A point: one record or one contact on one day. */
export interface TimelinePointData {
  kind: 'single-record' | 'visit' | 'remote-visit' | 'urgent-visit';
  /** ISO day. Points after today are not drawn. */
  date: string;
}
/** Contacts in one week, drawn as one count (a 3px bar, height by minutes). */
export interface TimelineWeekData {
  kind: 'contacts-per-week';
  /** ISO day the week starts. */
  week: string;
  /** Recorded minutes that week. The busiest week in the row is 24px high. */
  minutes: number;
}
export type TimelineMarkData = TimelineBarData | TimelinePointData | TimelineWeekData;

export interface TimelineRowData {
  /** Stable key; also what a change line names in `rowId`. */
  id: string;
  /** The item's name. */
  label: ReactNode;
  /** One caption line: code · kind · latest or end. Counts are totals and match the marks. */
  meta?: ReactNode;
  /** What the track shows. */
  marks: TimelineMarkData[];
  /** Shown when the row is open: newest first, at most 10. Records after today are left out. */
  records?: TimelineRecord[];
}

export interface TimelineLane {
  /** Stable key, used by `hiddenLanes`. */
  id: string;
  /** Lane name (overline, max 4 words). Also the label of its "Show" filter button. */
  label: string;
  /** Optional caption after the name (e.g. how the lane counts its contacts). */
  caption?: ReactNode;
  rows: TimelineRowData[];
  /** Slot after the lane's rows, e.g. a hidden-item row (a proposal in Figma: not built here). It spans the whole track. */
  after?: ReactNode;
}

export type TimelineEventKind = 'decision' | 'service-started' | 'note';
/** "today", or the kind of event the rows are lined up on. */
export type TimelineLineUp = 'today' | TimelineEventKind;

/** An event the rows can be lined up on. One per kind is used: the latest up to today. */
export interface TimelineEvent {
  kind: TimelineEventKind;
  /** How the range line and the Before heading name it, after "the": "decision" → "Lined up on the decision of 2 Mar 2026". */
  name: string;
  /** ISO day. */
  date: string;
  /** The event's text is a draft: "(draft)" follows its date in the range line. */
  draft?: boolean;
  /** The row the event is drawn in (as a tick) when lined up. */
  rowId?: string;
}

/** One line in the change lists under the timeline. */
export interface TimelineChange {
  id: string;
  /** ISO day of the change. Changes after today are left out. */
  date: string;
  /** What changed (body). */
  text: ReactNode;
  /** Where it came from (caption), e.g. the record type. */
  source?: ReactNode;
  /** The change's text is a draft: "· Draft" follows the source. */
  draft?: boolean;
  /** The row this line opens. Without it the line is not a button (e.g. a count of contacts). */
  rowId?: string;
  /** A count since the last contact (e.g. "9 contacts recorded"): its date shows as "since <date>". It is never a button. */
  since?: boolean;
}

/** The role's last contact: it names the zone and the first change list ("Since the last <label>"). */
export interface TimelineLastContact {
  /** Named per role, after "the last": "review" → "Since the last review". */
  label: string;
  /** ISO day. The zone runs from here to today. */
  date: string;
}

export interface ClientTimelineProps extends Omit<HTMLAttributes<HTMLDivElement>, 'children'> {
  /** The as-of date (the demo date), as an ISO day. Nothing after it is drawn or listed. */
  today: string;
  /** Lanes top to bottom. A lane with no rows is left out. */
  lanes: TimelineLane[];
  /** The role's last contact: draws the zone and the "Since the last …" list. */
  lastContact?: TimelineLastContact;
  /** Events the rows can be lined up on. "Line up on" offers only the kinds that are here. */
  events?: TimelineEvent[];
  /** Lines for the change lists under the timeline. */
  changes?: TimelineChange[];
  /** Figma: State = Today / Lined up. Controlled: use with `onLineUpChange`. */
  lineUp?: TimelineLineUp;
  /** Line-up at first (uncontrolled). Default "today". */
  defaultLineUp?: TimelineLineUp;
  /** Called with the new line-up when people pick one. */
  onLineUpChange?: (lineUp: TimelineLineUp) => void;
  /** Lane ids the "Show" filter hides. Controlled: use with `onHiddenLanesChange`. */
  hiddenLanes?: string[];
  /** Hidden lane ids at first (uncontrolled). */
  defaultHiddenLanes?: string[];
  /** Called with the new list of hidden lane ids. */
  onHiddenLanesChange?: (hiddenLanes: string[]) => void;
  /** Row ids that are open at first. */
  defaultOpenRows?: string[];
  /** Figma: State = Scrolled back. The ISO day at the right edge of the view when it opens. Default: today (the view opens on today). */
  openAt?: string;
  /** Heading level of the change-list titles. Default 3 (the timeline sits in a block with an h2). */
  headingLevel?: 3 | 4 | 5;
}

/* ===== Fixed copy (Figma, word for word) ===== */

const LINE_UP_LABELS: Record<TimelineLineUp, string> = {
  today: 'Today',
  decision: 'Decision',
  'service-started': 'Service started',
  note: 'Note',
};
const EVENT_KINDS: TimelineEventKind[] = ['decision', 'service-started', 'note'];
const LEGEND: { kind: TimelineMarkKind; label: string }[] = [
  { kind: 'running', label: 'Running' },
  { kind: 'as-needed', label: 'As needed / assessment' },
  { kind: 'paused', label: 'Paused' },
  { kind: 'single-record', label: 'Single record' },
  { kind: 'visit', label: 'Visit' },
  { kind: 'remote-visit', label: 'Remote' },
  { kind: 'urgent-visit', label: 'Urgent' },
  { kind: 'contacts-per-week', label: 'Contacts per week' },
];
const MINUS = '−';
const NBSP = ' ';
/** "‹ Earlier" and "Later ›" move the view by this many months. */
const SCROLL_STEP_MONTHS = 6;
/** Lined up: the window is the event ± this many months (cut at today). */
const LINE_UP_MONTHS = 6;
/** Room after the last day drawn, for the "Today" axis label (one month, 64px). */
const END_PAD_MONTHS = 1;

const plural = (count: number, one: string, many: string) => `${count}${NBSP}${count === 1 ? one : many}`;

/* ===== View model ===== */

interface View {
  /** Where the track starts. */
  origin: Day;
  /** Length of the track in months (with the end pad). */
  months: number;
  /** Last day drawn (today, or the end of the line-up window). */
  end: Day;
  endPos: number;
  /** Today line or event line position. */
  linePos: number;
  zone: [number, number] | null;
  gridStep: number;
  axis: { pos: number; text: string; strong?: boolean }[];
  event: TimelineEvent | null;
}

function dates(lanes: TimelineLane[], changes: TimelineChange[], events: TimelineEvent[], lastContact?: TimelineLastContact): string[] {
  const all: string[] = [];
  for (const lane of lanes) {
    for (const row of lane.rows) {
      for (const mark of row.marks) all.push('start' in mark ? mark.start : 'date' in mark ? mark.date : mark.week);
      for (const record of row.records ?? []) all.push(record.date);
    }
  }
  for (const change of changes) all.push(change.date);
  for (const event of events) all.push(event.date);
  if (lastContact) all.push(lastContact.date);
  return all;
}

function buildView(today: Day, lineUp: TimelineLineUp, event: TimelineEvent | null, earliest: Day, lastContact?: TimelineLastContact): View {
  const eventDay = event ? parseDay(event.date) : null;
  if (lineUp !== 'today' && event && eventDay) {
    const origin = addMonths(eventDay, -LINE_UP_MONTHS);
    const plus = addMonths(eventDay, LINE_UP_MONTHS);
    const end = compareIso(toIso(plus), toIso(today)) > 0 ? today : plus;
    const endPos = monthsBetween(origin, end);
    const linePos = monthsBetween(origin, eventDay);
    const steps = [-6, -3, 0, 3, 6];
    const axis = steps
      .map((step) => ({ step, pos: monthsBetween(origin, addMonths(eventDay, step)) }))
      .filter(({ pos }) => pos <= endPos + 0.001)
      .map(({ step, pos }) =>
        step === 0
          ? { pos, text: LINE_UP_LABELS[event.kind], strong: true }
          : { pos, text: `${step < 0 ? MINUS : '+'}${Math.abs(step)}${NBSP}mo` },
      );
    return { origin, months: endPos + END_PAD_MONTHS, end, endPos, linePos, zone: null, gridStep: 3, axis, event };
  }
  const origin = startOfMonth(earliest);
  const endPos = monthsBetween(origin, today);
  const axis: View['axis'] = [];
  for (let i = 0; i <= Math.floor(endPos); i += 1) {
    if (endPos - i < 0.75) continue; // the month of today: its label would run into "Today" (Figma leaves it out)
    const month = addMonths(origin, i);
    axis.push({ pos: i, text: month.month === 0 ? String(month.year) : monthName(month.month) }); // January shows the year only
  }
  axis.push({ pos: endPos, text: 'Today', strong: true });
  const contact = lastContact ? parseDay(lastContact.date) : null;
  const zone: View['zone'] = contact && compareIso(lastContact!.date, toIso(today)) <= 0 ? [Math.max(monthsBetween(origin, contact), 0), endPos] : null;
  return { origin, months: endPos + END_PAD_MONTHS, end: today, endPos, linePos: endPos, zone, gridStep: 1, axis, event: null };
}

/** Places a row's marks on the view's track. Nothing after the last day drawn; bars are cut at both ends. */
function placeMarks(row: TimelineRowData, view: View, todayIso: string): ReactNode[] {
  const originIso = toIso(view.origin);
  const endIso = toIso(view.end);
  const pos = (iso: string) => monthsBetween(view.origin, parseDay(iso)!);
  const inWindow = (iso: string) => parseDay(iso) !== null && compareIso(iso, originIso) >= 0 && compareIso(iso, endIso) <= 0;
  const weeks = row.marks.filter((m): m is TimelineWeekData => m.kind === 'contacts-per-week' && compareIso(m.week, todayIso) <= 0);
  const busiest = Math.max(0, ...weeks.map((w) => w.minutes));
  const out: ReactNode[] = [];
  row.marks.forEach((mark, i) => {
    if ('start' in mark) {
      if (!parseDay(mark.start)) return;
      const end = mark.end && parseDay(mark.end) ? mark.end : endIso;
      const from = compareIso(mark.start, originIso) < 0 ? originIso : mark.start;
      const to = compareIso(end, endIso) > 0 ? endIso : end;
      if (compareIso(from, to) > 0) return;
      out.push(<TimelineMark key={i} kind={mark.kind} at={pos(from)} until={pos(to)} />);
    } else if ('week' in mark) {
      if (!inWindow(mark.week) || busiest <= 0) return;
      out.push(<TimelineMark key={i} kind={mark.kind} at={pos(mark.week)} share={mark.minutes / busiest} />);
    } else if (inWindow(mark.date)) {
      out.push(<TimelineMark key={i} kind={mark.kind} at={pos(mark.date)} />);
    }
  });
  // Lined up: the event is drawn in its row, as a tick.
  if (view.event && view.event.rowId === row.id) out.push(<TimelineMark key="event" kind="single-record" at={view.linePos} />);
  return out;
}

/* ===== Parts ===== */

function ChangeList({
  changes,
  empty,
  onOpenRow,
}: {
  changes: TimelineChange[];
  empty?: ReactNode;
  onOpenRow: (rowId: string) => void;
}) {
  if (changes.length === 0) return empty ? <p className={[styles.empty, 't-body'].join(' ')}>{empty}</p> : null;
  return (
    <ul className={styles.changeList}>
      {changes.map((change) => {
        const date = change.since ? <>since <time dateTime={change.date}>{formatDay(change.date)}</time></> : <time dateTime={change.date}>{formatDay(change.date)}</time>;
        const content = (
          <>
            <span className={[styles.changeDate, 't-caption'].join(' ')}>{date}</span>{' '}
            <span className={styles.what}>
              <span className={[styles.changeText, 't-body'].join(' ')}>{change.text}</span>
              {change.source || change.draft ? (
                <>
                  {' '}
                  <span className={[styles.changeSource, 't-caption'].join(' ')}>
                    {change.source}
                    {change.source && change.draft ? ' · ' : null}
                    {change.draft ? 'Draft' : null}
                  </span>
                </>
              ) : null}
            </span>
          </>
        );
        const opens = change.rowId && !change.since ? change.rowId : null;
        return (
          <li key={change.id} className={styles.changeItem}>
            {opens ? (
              <button type="button" className={[styles.change, styles.changeButton].join(' ')} onClick={() => onOpenRow(opens)}>
                {content}
                <span className={styles.changeArrow} aria-hidden>
                  <ChevronRight size={16} strokeWidth={1.5} />
                </span>
              </button>
            ) : (
              <div className={styles.change}>{content}</div>
            )}
          </li>
        );
      })}
    </ul>
  );
}

/**
 * Timeline: what happened, by item, up to today. NEW: the styleguide's Timeline (client view), variant B (horizontal
 * scroll with scroll cues). Figma: Data display / Timeline (242:4559); State Today 241:2822, Scrolled back 242:3252,
 * Lined up 242:3889.
 */
export const ClientTimeline = forwardRef<HTMLDivElement, ClientTimelineProps>(function ClientTimeline(
  {
    today,
    lanes,
    lastContact,
    events = [],
    changes = [],
    lineUp: lineUpProp,
    defaultLineUp = 'today',
    onLineUpChange,
    hiddenLanes: hiddenProp,
    defaultHiddenLanes = [],
    onHiddenLanesChange,
    defaultOpenRows,
    openAt,
    headingLevel = 3,
    className,
    ...rest
  },
  ref,
) {
  const baseId = useId();
  const showId = `${baseId}-show`;
  const earlierId = `${baseId}-earlier`;
  const Heading = `h${headingLevel}` as const;
  const regionRef = useRef<HTMLDivElement>(null);
  const showGroupRef = useRef<HTMLDivElement>(null);
  /** Which edge button has keyboard focus, so focus can move on when that button goes away (WCAG 2.4.3). */
  const edgeFocus = useRef<'earlier' | 'later' | null>(null);

  const [ownLineUp, setOwnLineUp] = useState<TimelineLineUp>(defaultLineUp);
  const [ownHidden, setOwnHidden] = useState<string[]>(defaultHiddenLanes);
  const [openRows, setOpenRows] = useState<Set<string>>(() => new Set(defaultOpenRows));
  const [earlierOpen, setEarlierOpen] = useState(false);
  const [focusRow, setFocusRow] = useState<string | null>(null);
  const [canEarlier, setCanEarlier] = useState(false);
  const [canLater, setCanLater] = useState(false);
  const [scrollable, setScrollable] = useState(false);
  const [visible, setVisible] = useState<{ from: number; to: number } | null>(null);

  const todayDay = parseDay(today);
  const todayIso = todayDay ? toIso(todayDay) : today;
  const hidden = hiddenProp ?? ownHidden;

  // Events: the latest of each kind, up to today. "Line up on" offers only these.
  const eventByKind = useMemo(() => {
    const map = new Map<TimelineEventKind, TimelineEvent>();
    for (const event of events) {
      if (!parseDay(event.date) || compareIso(event.date, todayIso) > 0) continue;
      const current = map.get(event.kind);
      if (!current || compareIso(event.date, current.date) > 0) map.set(event.kind, event);
    }
    return map;
  }, [events, todayIso]);
  const lineUpOptions: TimelineLineUp[] = ['today', ...EVENT_KINDS.filter((kind) => eventByKind.has(kind))];
  const requested = lineUpProp ?? ownLineUp;
  const lineUp: TimelineLineUp = requested === 'today' || eventByKind.has(requested) ? requested : 'today';
  const event = lineUp === 'today' ? null : eventByKind.get(lineUp) ?? null;

  // `openAt` is for the view the timeline opens with; after a line-up change, Today opens on today.
  const lineUpChanged = useRef(false);
  const setLineUp = (next: TimelineLineUp) => {
    lineUpChanged.current = true;
    if (lineUpProp === undefined) setOwnLineUp(next);
    if (next !== lineUp) onLineUpChange?.(next);
  };
  const setHidden = (next: string[]) => {
    if (hiddenProp === undefined) setOwnHidden(next);
    onHiddenLanesChange?.(next);
  };

  const shownLanes = lanes.filter((lane) => lane.rows.length > 0 || lane.after);
  const visibleLanes = shownLanes.filter((lane) => !hidden.includes(lane.id));
  const hiddenCount = shownLanes.length - visibleLanes.length;

  const earliestIso = useMemo(() => {
    const valid = dates(lanes, changes, events, lastContact).filter((iso) => parseDay(iso) && compareIso(iso, todayIso) <= 0);
    valid.sort(compareIso);
    return valid[0] ?? todayIso;
  }, [lanes, changes, events, lastContact, todayIso]);
  const earliest = parseDay(earliestIso);

  // Cheap to build, so it is built on every render. Effects key on `viewKey` and read the latest view from a ref.
  const view = todayDay && earliest ? buildView(todayDay, lineUp, event, earliest, lastContact) : null;
  const viewRef = useRef(view);
  viewRef.current = view;

  /* ----- Scrolling ----- */

  const metrics = useCallback(() => {
    const region = regionRef.current;
    if (!region) return null;
    const month = parseFloat(getComputedStyle(region).getPropertyValue('--tl-month')) || 64;
    const axis = region.querySelector<HTMLElement>('[data-axis-track]');
    const label = region.querySelector<HTMLElement>('[data-axis-label]');
    const arrow = region.querySelector<HTMLElement>('[data-axis-arrow]');
    const track = region.clientWidth - (label?.offsetWidth ?? 0) - (arrow?.offsetWidth ?? 0);
    const max = Math.max(0, region.scrollWidth - region.clientWidth);
    return { region, month, track: Math.max(track, 0), max, axis };
  }, []);

  const rangeTimer = useRef<number | undefined>(undefined);
  const frame = useRef<number | undefined>(undefined);
  const measure = useCallback(
    (rangeNow: boolean) => {
      const m = metrics();
      const current = viewRef.current;
      if (!m || !current) return;
      const left = m.region.scrollLeft;
      setCanEarlier(left > 1);
      setCanLater(left < m.max - 1);
      setScrollable(m.max > 1);
      const updateRange = () => {
        const from = Math.max(0, left / m.month);
        const to = Math.max(from, Math.min(current.endPos, (left + m.track) / m.month));
        setVisible((prev) => (prev && Math.abs(prev.from - from) < 1e-6 && Math.abs(prev.to - to) < 1e-6 ? prev : { from, to }));
      };
      window.clearTimeout(rangeTimer.current);
      // The range line is a live region: update it once scrolling settles, not on every frame.
      if (rangeNow) updateRange();
      else rangeTimer.current = window.setTimeout(updateRange, 150);
    },
    [metrics],
  );

  const onScroll = () => {
    if (frame.current !== undefined) return;
    frame.current = window.requestAnimationFrame(() => {
      frame.current = undefined;
      measure(false);
    });
  };

  useEffect(
    () => () => {
      window.clearTimeout(rangeTimer.current);
      if (frame.current !== undefined) window.cancelAnimationFrame(frame.current);
    },
    [],
  );

  // The visible width: the open records and the focus ring follow it while the track scrolls.
  useLayoutEffect(() => {
    const region = regionRef.current;
    if (!region) return;
    const update = () => {
      region.style.setProperty('--tl-viewport', `${region.clientWidth}px`);
      measure(true);
    };
    update();
    if (typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver(update);
    observer.observe(region);
    return () => observer.disconnect();
  }, [measure]);

  // Opening position: on today (the end), on `openAt`, or centred on the line-up event.
  const viewKey = view ? `${lineUp}|${toIso(view.origin)}|${view.months}` : '';
  useLayoutEffect(() => {
    const m = metrics();
    if (!m || !view) return;
    let left = m.max;
    if (view.event) {
      left = view.linePos * m.month - m.track / 2;
    } else if (!lineUpChanged.current && openAt && parseDay(openAt) && compareIso(openAt, todayIso) < 0) {
      left = monthsBetween(view.origin, parseDay(openAt)!) * m.month - m.track;
    }
    m.region.scrollLeft = Math.min(Math.max(0, left), m.max);
    measure(true);
    // Only when the view itself changes (line-up, data range), not on every render.
  }, [viewKey]);

  const scrollByMonths = (count: number) => {
    const m = metrics();
    if (!m) return;
    m.region.scrollLeft = Math.min(Math.max(0, m.region.scrollLeft + count * m.month), m.max);
  };

  const goToday = () => {
    if (lineUp !== 'today') {
      setLineUp('today');
      return; // the new view opens on today
    }
    const m = metrics();
    if (m) m.region.scrollLeft = m.max;
  };

  // Arrow keys scroll the region one month; Home and End go to the start and to today.
  const onRegionKeyDown = (keyEvent: KeyboardEvent<HTMLDivElement>) => {
    const m = metrics();
    if (!m || keyEvent.altKey || keyEvent.ctrlKey || keyEvent.metaKey) return;
    const moves: Record<string, number> = { ArrowLeft: -m.month, ArrowRight: m.month, Home: -m.region.scrollWidth, End: m.region.scrollWidth };
    const move = moves[keyEvent.key];
    if (move === undefined) return;
    keyEvent.preventDefault();
    m.region.scrollLeft = Math.min(Math.max(0, m.region.scrollLeft + move), m.max);
  };

  // An edge button is shown only while there is more in its direction. When the one with focus goes away (its press
  // reached the end, or the view was scrolled), focus moves to the scroll region, the next stop, instead of the page.
  useEffect(() => {
    const gone = (edgeFocus.current === 'earlier' && !canEarlier) || (edgeFocus.current === 'later' && !canLater);
    if (!gone) return;
    edgeFocus.current = null;
    const active = document.activeElement;
    if (!active || active === document.body) regionRef.current?.focus({ preventScroll: true });
  }, [canEarlier, canLater]);
  const edgeFocusProps = (edge: 'earlier' | 'later') => ({
    onFocus: () => {
      edgeFocus.current = edge;
    },
    // Focus moving to another control clears it. A blur to nothing (relatedTarget null) is kept: browsers may report
    // the button's own removal that way.
    onBlur: (event: FocusEvent<HTMLButtonElement>) => {
      if (edgeFocus.current === edge && event.relatedTarget) edgeFocus.current = null;
    },
  });

  // "Show all lanes" goes away once pressed: focus moves to the first lane filter, which stays.
  const showAllLanes = () => {
    setHidden([]);
    showGroupRef.current?.querySelector<HTMLButtonElement>('button')?.focus();
  };

  /* ----- Rows ----- */

  const buttonId = (rowId: string) => `${baseId}-row-${rowId}`;
  const setRowOpen = (rowId: string, open: boolean) =>
    setOpenRows((current) => {
      const next = new Set(current);
      if (open) next.add(rowId);
      else next.delete(rowId);
      return next;
    });

  // A change line opens its row (showing its lane if the filter hid it) and moves focus to the row's button.
  const openRow = (rowId: string) => {
    const lane = shownLanes.find((l) => l.rows.some((r) => r.id === rowId));
    if (lane && hidden.includes(lane.id)) setHidden(hidden.filter((id) => id !== lane.id));
    setRowOpen(rowId, true);
    setFocusRow(rowId);
  };
  useEffect(() => {
    if (!focusRow) return;
    const button = document.getElementById(buttonId(focusRow));
    if (button) {
      button.focus({ preventScroll: true });
      button.scrollIntoView({ block: 'nearest', inline: 'nearest' });
    }
    setFocusRow(null);
  }, [focusRow]);

  if (!view || !todayDay) return null;

  /* ----- Text ----- */

  const validChanges = changes.filter((c) => parseDay(c.date) && compareIso(c.date, todayIso) <= 0);
  let rangeText: string;
  let windowStartIso = toIso(view.origin);
  let windowEndIso = toIso(view.end);
  if (view.event) {
    rangeText = `Lined up on the ${view.event.name} of ${formatDay(view.event.date)}${view.event.draft ? ' (draft)' : ''} · ${LINE_UP_MONTHS} months before and after`;
  } else {
    const from = dayAt(view.origin, visible?.from ?? 0);
    const to = dayAt(view.origin, visible?.to ?? view.endPos);
    windowStartIso = toIso(from);
    windowEndIso = toIso(to);
    rangeText = `Showing ${formatMonth(from)} – ${formatMonth(to)}`;
    // Code over Figma: the scroll cue is left out when there is nothing to scroll (the whole timeline fits).
    if (scrollable) rangeText += ` · full timeline from ${formatMonth(view.origin)}, scroll sideways`;
  }

  const byDateDesc = (a: TimelineChange, b: TimelineChange) => compareIso(b.date, a.date);
  const sinceList = lastContact
    ? validChanges
        .filter((c) => compareIso(c.date, lastContact.date) >= 0)
        .sort((a, b) => Number(Boolean(b.since)) - Number(Boolean(a.since)) || byDateDesc(a, b))
    : [];
  const earlierList = validChanges
    .filter(
      (c) =>
        compareIso(c.date, windowStartIso) >= 0 &&
        compareIso(c.date, windowEndIso) <= 0 &&
        (!lastContact || compareIso(c.date, lastContact.date) < 0),
    )
    .sort(byDateDesc);
  const eventIso = view.event?.date ?? '';
  const beforeList = view.event
    ? validChanges.filter((c) => compareIso(c.date, toIso(view.origin)) >= 0 && compareIso(c.date, eventIso) < 0).sort(byDateDesc)
    : [];
  const afterList = view.event
    ? validChanges.filter((c) => compareIso(c.date, eventIso) >= 0 && compareIso(c.date, toIso(view.end)) <= 0).sort((a, b) => -byDateDesc(a, b))
    : [];

  const vars = {
    '--tl-months': view.months,
    '--tl-grid-end': view.endPos,
    '--tl-grid-step': view.gridStep,
    '--tl-line-at': view.linePos,
    '--tl-zone-from': view.zone?.[0] ?? 0,
    '--tl-zone-to': view.zone?.[1] ?? 0,
  } as CSSProperties;

  const toggleLane = (laneId: string) =>
    setHidden(hidden.includes(laneId) ? hidden.filter((id) => id !== laneId) : [...hidden, laneId]);

  return (
    <div {...rest} ref={ref} className={[styles.timeline, className].filter(Boolean).join(' ')}>
      {/* 1. Controls: Show (lane filters) and Line up on. */}
      <div className={styles.controls}>
        <div className={styles.group}>
          <span id={showId} className={[styles.groupLabel, 't-caption'].join(' ')}>
            Show
          </span>
          <div ref={showGroupRef} role="group" aria-labelledby={showId} className={[segment.control, styles.segments].join(' ')}>
            {shownLanes.map((lane) => (
              <button
                key={lane.id}
                type="button"
                className={[segment.segment, 't-control'].join(' ')}
                aria-pressed={!hidden.includes(lane.id)}
                onClick={() => toggleLane(lane.id)}
              >
                <span className={segment.label} data-label={lane.label}>
                  {lane.label}
                </span>
              </button>
            ))}
          </div>
        </div>
        {lineUpOptions.length > 1 ? (
          <div className={styles.group}>
            {/* The group's name is "Line up on" (SegmentedControl label); the visible caption is not read twice. */}
            <span className={[styles.groupLabel, 't-caption'].join(' ')} aria-hidden>
              Line up on
            </span>
            <SegmentedControl
              className={styles.segments}
              label="Line up on"
              options={lineUpOptions.map((value) => ({ value, label: LINE_UP_LABELS[value] }))}
              value={lineUp}
              onChange={(value) => setLineUp(value as TimelineLineUp)}
            />
          </div>
        ) : null}
        {/* The active filter is stated in words (styleguide). A live region, so the change is announced. */}
        <div className={styles.filterNote} aria-live="polite">
          {hiddenCount > 0 ? (
            <>
              <span className={[styles.filterText, 't-caption'].join(' ')}>Your filter hides {plural(hiddenCount, 'lane', 'lanes')}</span>
              <Button size="sm" onClick={showAllLanes}>
                Show all lanes
              </Button>
            </>
          ) : null}
        </div>
      </div>

      {/* 2. Scroll buttons and 3. legend. */}
      <div className={styles.controls}>
        <div className={[segment.control, styles.segments].join(' ')}>
          <button
            type="button"
            className={[segment.segment, styles.scrollButton, 't-control'].join(' ')}
            aria-disabled={!canEarlier || undefined}
            onClick={() => canEarlier && scrollByMonths(-SCROLL_STEP_MONTHS)}
          >
            <span>
              <span aria-hidden>‹</span> Earlier
            </span>
          </button>
          <button type="button" className={[segment.segment, styles.scrollButton, 't-control'].join(' ')} onClick={goToday}>
            Today
          </button>
          <button
            type="button"
            className={[segment.segment, styles.scrollButton, 't-control'].join(' ')}
            aria-disabled={!canLater || undefined}
            onClick={() => canLater && scrollByMonths(SCROLL_STEP_MONTHS)}
          >
            <span>
              Later <span aria-hidden>›</span>
            </span>
          </button>
        </div>
        <ul className={styles.legend}>
          {LEGEND.map((key) => (
            <li key={key.kind} className={styles.key}>
              <TimelineMark kind={key.kind} size="legend" />
              <span className="t-caption">{key.label}</span>
            </li>
          ))}
          {lastContact ? (
            <li className={styles.key}>
              <span className={styles.zoneSwatch} aria-hidden />
              <span className="t-caption">Since the last {lastContact.label}</span>
            </li>
          ) : null}
        </ul>
      </div>

      {/* 4. Range line: live text, a scroll cue. */}
      <p className={[styles.range, 't-caption'].join(' ')} aria-live="polite">
        {rangeText}
      </p>

      {/* 5. Edge buttons and the scroll region. */}
      <div className={styles.scrollArea}>
        <div className={styles.edges}>
          {canEarlier ? (
            <Button size="sm" {...edgeFocusProps('earlier')} onClick={() => scrollByMonths(-SCROLL_STEP_MONTHS)}>
              <span aria-hidden>‹</span> Earlier
            </Button>
          ) : null}
          {canLater ? (
            <Button size="sm" className={styles.edgeLater} {...edgeFocusProps('later')} onClick={() => scrollByMonths(SCROLL_STEP_MONTHS)}>
              Later <span aria-hidden>›</span>
            </Button>
          ) : null}
        </div>
        <div
          ref={regionRef}
          role="region"
          tabIndex={0}
          aria-label="Timeline, scroll sideways"
          className={styles.region}
          style={vars}
          onScroll={onScroll}
          onKeyDown={onRegionKeyDown}
        >
          <TimelineScrollContext.Provider value>
            <div className={styles.content}>
              {/* Axis: decorative; the range line says what is shown. */}
              <div className={styles.axis} aria-hidden>
                <span className={styles.axisLabel} data-axis-label />
                <span className={styles.axisTrack} data-axis-track>
                  {view.axis.map((tick) => (
                    <span
                      key={`${tick.pos}-${tick.text}`}
                      className={[styles.tick, tick.strong ? styles.tickStrong : undefined, 't-caption'].filter(Boolean).join(' ')}
                      style={{ '--at': tick.pos } as CSSProperties}
                    >
                      {tick.text}
                    </span>
                  ))}
                </span>
                <span className={styles.axisArrow} data-axis-arrow />
              </div>
              {visibleLanes.map((lane) => {
                const laneId = `${baseId}-lane-${lane.id}`;
                return (
                  <div key={lane.id} role="group" aria-labelledby={laneId} className={styles.lane}>
                    <div className={styles.laneHeader}>
                      <span className={styles.laneText}>
                        <span id={laneId} className={[styles.laneName, 't-overline'].join(' ')}>
                          {lane.label}
                        </span>
                        {lane.caption ? <span className={[styles.laneCaption, 't-caption'].join(' ')}>{lane.caption}</span> : null}
                      </span>
                    </div>
                    {lane.rows.map((row) => (
                      <TimelineRow
                        key={row.id}
                        buttonId={buttonId(row.id)}
                        label={row.label}
                        meta={row.meta}
                        marks={placeMarks(row, view, todayIso)}
                        records={(row.records ?? []).filter((r) => parseDay(r.date) && compareIso(r.date, todayIso) <= 0)}
                        open={openRows.has(row.id)}
                        onOpenChange={(open) => setRowOpen(row.id, open)}
                      />
                    ))}
                    {lane.after}
                  </div>
                );
              })}
            </div>
          </TimelineScrollContext.Provider>
        </div>
      </div>

      {/* 6. Change lists. */}
      {view.event ? (
        <div className={styles.changes}>
          <section className={styles.changeColumn} aria-labelledby={`${baseId}-before`}>
            <Heading id={`${baseId}-before`} className={[styles.changeHeading, 't-title-sm'].join(' ')}>
              Before the {view.event.name} · {formatDay(view.event.date)}
            </Heading>
            <ChangeList changes={beforeList} empty="No changes recorded." onOpenRow={openRow} />
          </section>
          <section className={styles.changeColumn} aria-labelledby={`${baseId}-after`}>
            <Heading id={`${baseId}-after`} className={[styles.changeHeading, 't-title-sm'].join(' ')}>
              After
            </Heading>
            <ChangeList changes={afterList} empty="No changes recorded." onOpenRow={openRow} />
          </section>
        </div>
      ) : (
        <div className={styles.changes}>
          {lastContact ? (
            <section className={styles.changeColumn} aria-labelledby={`${baseId}-since`}>
              <Heading id={`${baseId}-since`} className={[styles.changeHeading, 't-title-sm'].join(' ')}>
                Since the last {lastContact.label} · {formatDay(lastContact.date)}
              </Heading>
              <ChangeList changes={sinceList} empty="No changes recorded." onOpenRow={openRow} />
            </section>
          ) : null}
          {earlierList.length > 0 ? (
            <div className={styles.changeColumn}>
              <button
                type="button"
                className={[styles.disclosure, 't-body'].join(' ')}
                aria-expanded={earlierOpen}
                aria-controls={earlierId}
                onClick={() => setEarlierOpen(!earlierOpen)}
              >
                <span className={styles.disclosureIcon} aria-hidden>
                  {earlierOpen ? <ChevronDown size={16} strokeWidth={1.5} /> : <ChevronRight size={16} strokeWidth={1.5} />}
                </span>
                <span className={styles.disclosureText}>Earlier in this window · {plural(earlierList.length, 'change', 'changes')}</span>
              </button>
              <div id={earlierId} hidden={!earlierOpen}>
                <ChangeList changes={earlierList} onOpenRow={openRow} />
              </div>
            </div>
          ) : null}
        </div>
      )}
    </div>
  );
});
