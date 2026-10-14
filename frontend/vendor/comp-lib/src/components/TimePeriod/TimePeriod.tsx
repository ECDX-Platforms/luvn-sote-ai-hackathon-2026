import { forwardRef, type CSSProperties, type HTMLAttributes, type ReactNode } from 'react';
import { CirclePause } from 'lucide-react';
import styles from './TimePeriod.module.css';

export type TimePeriodLayout = 'full' | 'compact';
export type TimePeriodStatus = 'ongoing' | 'ended' | 'upcoming' | 'open-ended' | 'paused';

/** The visits recorded in a paused period, from the source. Records after `today` are cut before they are passed in. */
export interface TimePeriodVisits {
  /** The first visit recorded, as an ISO date ("2026-03-04"). */
  first: string;
  /** The last visit recorded, as an ISO date ("2026-08-12"). There are none after it up to `today`. */
  last: string;
  /** How many visits are recorded from first to last. */
  count: number;
}

export interface TimePeriodProps extends Omit<HTMLAttributes<HTMLDivElement>, 'children'> {
  /** Figma: Layout. Full = dates and status, a track, then progress and today. Compact = one line for tables and lists: dates · status. */
  layout?: TimePeriodLayout;
  /** Figma: Dates (start). The start date from the source, as an ISO date ("2026-09-01"). Shown as "1 Sep 2026". */
  start: string;
  /** Figma: Dates (end). The end date from the source, as an ISO date. Leave it out (or pass null or an empty string) when the source has no end date: the period is then open-ended ("no end date", "until further notice"). Never invent one. */
  end?: string | null;
  /** The as-of date (the demo date), as an ISO date. Status, progress and the today marker are computed only from start, end and this date. */
  today: string;
  /**
   * Figma: Status = Paused. Only when the source marks the service temporarily interrupted; it cannot be computed from the dates.
   * Shows the pause icon and "Paused". Full adds a hatched track for the period up to today, the visits bar and the two source lines.
   */
  paused?: boolean;
  /** Paused, Full only: the visits recorded, from the source. Drawn as a bar in the data colour with "Visits recorded <first> – <last> (N), none after". Leave it out when the source has none to show. */
  visits?: TimePeriodVisits;
  /** Paused, Full only: what the source says, word for word, without quote marks. Caption, text-muted, under `pausedSourceTitle`. */
  pausedSource?: ReactNode;
  /** Paused, Full only: the heading of the source line (body-strong). Default `labels.pausedSourceTitle`. */
  pausedSourceTitle?: ReactNode;
  /** Every fixed word the component renders, for another language. Each key defaults to the English (Figma) copy. */
  labels?: Partial<TimePeriodLabels>;
  /**
   * Shows a parsed date your way ("4.9.2026"). Gets the ISO date. Default: the Content format with `labels.months`
   * ("4 Sep 2026"). With it, the visits range shows both dates in full (no year is dropped).
   */
  formatDate?: (iso: string) => string;
}

/** The fixed copy of the component. Defaults from Figma and the styleguide's Content rules. */
export interface TimePeriodLabels {
  /** Month abbreviations for the default date format, January first. */
  months: readonly string[];
  /** Shown as the end when the source has none. */
  noEndDate: string;
  /** Figma: Status words. Upcoming says "Starts later"; Open-ended that has started says "Ongoing". */
  ongoing: string;
  ended: string;
  upcoming: string;
  openEnded: string;
  paused: string;
  /** The today marker's caption, and the start of the "Today: <date>" caption. */
  today: string;
  /** "Today: <date>" under the track. */
  todayIs: (date: ReactNode) => ReactNode;
  /** Upcoming: "Starts in 20 days", from the day count text (`days`). */
  startsIn: (days: string) => string;
  todayBeforeStart: string;
  /** Open-ended: "Day 60, until further notice". */
  dayUntilFurtherNotice: (day: number) => string;
  todayAfterEnd: string;
  /** Ongoing: "Day 43 of 91". */
  dayOf: (day: number, total: number) => string;
  /** A day count ("1 day", "91 days"). */
  days: (count: number) => string;
  /** Paused: the caption under the hatched track. */
  servicePeriod: string;
  /** Paused: the visits caption, from the range ("4 Mar – 12 Aug 2026") and the count. */
  visitsRecorded: (range: ReactNode, count: number) => ReactNode;
  /** Paused: default of the `pausedSourceTitle` prop. */
  pausedSourceTitle: ReactNode;
  /** Paused: the heading and text of the second source line. */
  notInSource: string;
  notInSourceText: string;
}

const DEFAULT_LABELS: TimePeriodLabels = {
  months: ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'],
  noEndDate: 'no end date',
  ongoing: 'Ongoing',
  ended: 'Ended',
  upcoming: 'Starts later',
  openEnded: 'Ongoing',
  paused: 'Paused',
  today: 'Today',
  todayIs: (date) => <>Today: {date}</>,
  startsIn: (days) => `Starts in ${days}`,
  todayBeforeStart: 'Today is before the start',
  dayUntilFurtherNotice: (day) => `Day ${day}, until further notice`,
  todayAfterEnd: 'Today is after the end',
  dayOf: (day, total) => `Day ${day} of ${total}`,
  days: (count) => `${count} ${count === 1 ? 'day' : 'days'}`,
  servicePeriod: 'Service period · pause start not in the source',
  visitsRecorded: (range, count) => <>Visits recorded {range} ({count}), none after</>,
  pausedSourceTitle: 'Marked temporarily interrupted',
  notInSource: 'Not in the source',
  notInSourceText: 'When the pause started, why, and when it ends. None is shown or guessed.',
};

const DAY_MS = 86_400_000;

/** Parses "YYYY-MM-DD" as a calendar day (UTC, so time zones and daylight saving never shift it). Null when invalid. */
function parseDay(iso: string): number | null {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso);
  if (!match) return null;
  const [year, month, day] = [Number(match[1]), Number(match[2]), Number(match[3])];
  const time = Date.UTC(year, month - 1, day);
  const date = new Date(time);
  if (date.getUTCFullYear() !== year || date.getUTCMonth() !== month - 1 || date.getUTCDate() !== day) return null;
  return time / DAY_MS;
}

/** Content format: "21 Oct 2024" (styleguide Content). */
function formatDay(day: number, months: readonly string[]): string {
  const date = new Date(day * DAY_MS);
  return `${date.getUTCDate()} ${months[date.getUTCMonth()]} ${date.getUTCFullYear()}`;
}

/** "4 Mar": the year is dropped when the range ends in the same year (styleguide Content). */
function formatDayMonth(day: number, months: readonly string[]): string {
  const date = new Date(day * DAY_MS);
  return `${date.getUTCDate()} ${months[date.getUTCMonth()]}`;
}

/** How dates are shown: the caller's `formatDate`, or the Content format with the month abbreviations. */
interface DateFormat {
  full: (iso: string, day: number) => string;
  /** The year-less form for the first date of a same-year range; absent with a caller's `formatDate`. */
  dayMonth?: (day: number) => string;
}

function dateFormat(formatDate: ((iso: string) => string) | undefined, months: readonly string[]): DateFormat {
  if (formatDate) return { full: (iso) => formatDate(iso) };
  return { full: (_iso, day) => formatDay(day, months), dayMonth: (day) => formatDayMonth(day, months) };
}

/** A missing, null or empty end means "no end date in the source". */
function hasEnd(end: string | null | undefined): end is string {
  return typeof end === 'string' && end.trim() !== '';
}

/** A parsed date goes in a time element; one that cannot be parsed is shown as given in a span (no invalid dateTime). */
function DateText({ iso, day, format }: { iso: string; day: number | null; format: DateFormat }) {
  return day === null ? <span className={styles.date}>{iso}</span> : <time className={styles.date} dateTime={iso}>{format.full(iso, day)}</time>;
}

/** Figma: Status. Derived from the dates, so the word never contradicts them. Open-ended = no end date in the source and already started. Paused comes from the source (`paused`), never from the dates. Null when a date is invalid or the end is before the start. */
export function getTimePeriodStatus(start: string, end: string | null | undefined, today: string, paused = false): TimePeriodStatus | null {
  const s = parseDay(start);
  const t = parseDay(today);
  const e = hasEnd(end) ? parseDay(end) : undefined;
  if (s === null || t === null || e === null) return null;
  if (e !== undefined && e < s) return null; // an end before the start is invalid: compute nothing
  if (paused) return 'paused';
  if (t < s) return 'upcoming';
  if (e === undefined) return 'open-ended';
  return t > e ? 'ended' : 'ongoing';
}

/** The status word's key in the labels. */
const WORD_KEY: Record<TimePeriodStatus, 'ongoing' | 'ended' | 'upcoming' | 'openEnded' | 'paused'> = {
  ongoing: 'ongoing',
  ended: 'ended',
  upcoming: 'upcoming',
  'open-ended': 'openEnded',
  paused: 'paused',
};

/** A range of two parsed days; the first drops its year when both are in the same year ("4 Mar – 12 Aug 2026"). */
function DayRange({ first, last, format }: { first: TimePeriodVisits['first']; last: TimePeriodVisits['last']; format: DateFormat }) {
  const f = parseDay(first);
  const l = parseDay(last);
  const year = (day: number) => new Date(day * DAY_MS).getUTCFullYear();
  return (
    <span className={styles.dates}>
      {f !== null && l !== null && format.dayMonth && year(f) === year(l) ? (
        <time className={styles.date} dateTime={first}>{format.dayMonth(f)}</time>
      ) : (
        <DateText iso={first} day={f} format={format} />
      )}
      {' – '}
      <DateText iso={last} day={l} format={format} />
    </span>
  );
}

/**
 * Time period: start, end and where today falls. NEW: not in the styleguide; composed from its type roles, hairline and chart rules.
 * Figma: Data display / Time period (172:388); Paused: 224:2143 (Full), 224:2171 (Compact).
 */
export const TimePeriod = forwardRef<HTMLDivElement, TimePeriodProps>(function TimePeriod(
  {
    layout = 'full',
    start,
    end,
    today,
    paused = false,
    visits,
    pausedSource,
    pausedSourceTitle,
    labels,
    formatDate,
    className,
    ...rest
  },
  ref,
) {
  const t: TimePeriodLabels = { ...DEFAULT_LABELS, ...labels };
  const format = dateFormat(formatDate, t.months);
  const shownPausedSourceTitle = pausedSourceTitle ?? t.pausedSourceTitle;
  const s = parseDay(start);
  const td = parseDay(today);
  const endGiven = hasEnd(end);
  const e = endGiven ? parseDay(end) : undefined;
  const status = getTimePeriodStatus(start, end, today, paused);

  const dates = (
    <span className={styles.dates}>
      <DateText iso={start} day={s} format={format} />
      {' – '}
      {endGiven ? <DateText iso={end} day={e ?? null} format={format} /> : <span className={styles.date}>{t.noEndDate}</span>}
    </span>
  );

  // Paused: icon + word. The icon is decorative; the word carries the status.
  const pausedWord = (
    <span className={styles.status}>
      <span className={styles.icon} aria-hidden>
        <CirclePause size={16} strokeWidth={1.5} />
      </span>
      <span className={[styles.word, 't-body-strong'].join(' ')}>{t.paused}</span>
    </span>
  );

  // Invalid dates (or an end before the start): show them as given and compute nothing (never guess a status).
  // Paused still says Paused: that comes from the source, not from the dates.
  // Paused uses the one-line layout so the word sits beside the dates; otherwise the layout is unchanged.
  if (status === null || s === null || td === null || e === null) {
    return (
      <div {...rest} ref={ref} className={[styles.timePeriod, paused ? styles.compact : styles[layout], 't-body', className].filter(Boolean).join(' ')}>
        {dates}
        {paused ? (
          <>
            <span className={styles.separator} aria-hidden>·</span>
            {pausedWord}
          </>
        ) : null}
      </div>
    );
  }

  const word = status === 'paused' ? pausedWord : <span className={[styles.word, 't-body-strong'].join(' ')}>{t[WORD_KEY[status]]}</span>;

  if (layout === 'compact') {
    return (
      <div {...rest} ref={ref} className={[styles.timePeriod, styles.compact, 't-body', className].filter(Boolean).join(' ')}>
        {dates}
        <span className={styles.separator} aria-hidden>·</span>
        {word}
      </div>
    );
  }

  if (status === 'paused') {
    // Both tracks run from the start to today (records after today are cut). The hatch covers all of it: the pause start is not in the source.
    // Days are counted inclusively: day d fills [d − start, d − start + 1) of the span.
    const span = td - s + 1;
    const first = visits ? parseDay(visits.first) : null;
    const last = visits ? parseDay(visits.last) : null;
    const from = first === null ? null : Math.min(Math.max(first - s, 0), span) / span;
    const to = last === null ? null : Math.min(Math.max(last - s + 1, 0), span) / span;
    const showTracks = span > 0; // paused before the start: nothing to draw
    return (
      <div {...rest} ref={ref} className={[styles.timePeriod, styles.full, className].filter(Boolean).join(' ')}>
        <div className={[styles.top, styles.topPaused, 't-body'].join(' ')}>
          {dates}
          {word}
        </div>
        {showTracks ? (
          <>
            {/* Decorative: the captions carry the meaning. */}
            <div className={styles.track} aria-hidden>
              <span className={styles.hatch} />
              <span className={[styles.today, styles.todayEnd].join(' ')} />
            </div>
            <div className={[styles.bottom, 't-caption'].join(' ')}>
              <span className={styles.text}>{t.servicePeriod}</span>
              <span className={styles.text}>{t.today}</span>
            </div>
          </>
        ) : null}
        {visits ? (
          <>
            {showTracks && from !== null && to !== null && to > from ? (
              <div className={styles.track} style={{ '--from': from, '--to': to } as CSSProperties} aria-hidden>
                <span className={styles.period} />
                <span className={styles.visits} />
                <span className={[styles.today, styles.todayEnd].join(' ')} />
              </div>
            ) : null}
            <p className={[styles.caption, 't-caption'].join(' ')}>
              {t.visitsRecorded(<DayRange first={visits.first} last={visits.last} format={format} />, visits.count)}
            </p>
          </>
        ) : null}
        <div className={styles.sourceLines}>
          <div className={styles.line}>
            <p className={[styles.lineTitle, 't-body-strong'].join(' ')}>{shownPausedSourceTitle}</p>
            {pausedSource ? <p className={[styles.lineText, 't-caption'].join(' ')}>{pausedSource}</p> : null}
          </div>
          <div className={styles.line}>
            <p className={[styles.lineTitle, 't-body-strong'].join(' ')}>{t.notInSource}</p>
            <p className={[styles.lineText, 't-caption'].join(' ')}>{t.notInSourceText}</p>
          </div>
        </div>
      </div>
    );
  }

  // Progress and the today label, computed only from the dates shown and today (days counted inclusively).
  const todayDate = t.todayIs(<time className={styles.date} dateTime={today}>{format.full(today, td)}</time>);
  let progress: string;
  let todayLabel: ReactNode;
  let elapsed = 0; // share of the track, 0 to 1
  if (status === 'upcoming') {
    progress = t.startsIn(t.days(s - td));
    todayLabel = t.todayBeforeStart;
  } else if (status === 'open-ended') {
    progress = t.dayUntilFurtherNotice(td - s + 1);
    todayLabel = todayDate;
  } else if (status === 'ended') {
    progress = t.days((e as number) - s + 1);
    todayLabel = t.todayAfterEnd;
    elapsed = 1;
  } else {
    const total = (e as number) - s + 1;
    const day = td - s + 1;
    progress = t.dayOf(day, total);
    todayLabel = todayDate;
    elapsed = day / total;
  }

  return (
    <div {...rest} ref={ref} className={[styles.timePeriod, styles.full, className].filter(Boolean).join(' ')}>
      <div className={[styles.top, 't-body'].join(' ')}>
        {dates}
        {word}
      </div>
      {/* Decorative: the words above and below carry the meaning. */}
      <div
        className={[styles.track, endGiven ? undefined : styles.noEnd].filter(Boolean).join(' ')}
        style={{ '--elapsed': elapsed } as CSSProperties}
        aria-hidden
      >
        <span className={styles.period} />
        {elapsed > 0 ? <span className={styles.elapsed} /> : null}
        {status === 'ongoing' ? <span className={styles.today} /> : null}
      </div>
      <div className={[styles.bottom, 't-caption'].join(' ')}>
        <span className={styles.text}>{progress}</span>
        <span className={styles.text}>{todayLabel}</span>
      </div>
    </div>
  );
});
