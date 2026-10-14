/**
 * Week maths and text for Visit rhythm (Figma description, 13 Oct 2026). Pure functions, no locale: the same input
 * always gives the same result, so stories and tests can pass a fixed `today`. Dates come from Result series' rules
 * (`../ResultSeries/dates`): ISO in, "4 Sep 2026" out, whole days with no time-zone drift.
 */
import { dayNumber, formatDate, parseDate, type CalendarDate } from '../ResultSeries/dates';

/** One recorded visit or contact, from one source table only. */
export interface RhythmVisit {
  /** The date of the visit, `YYYY-MM-DD`. */
  date: string;
  /** Recorded minutes. */
  minutes: number;
}

/** One week on the chart, Monday to Sunday, cut to the days of service. */
export interface RhythmWeek {
  /** The Monday the week starts on (it names the week, also in a part week). */
  monday: CalendarDate;
  /** First and last day of service in this week, as day numbers (inclusive). */
  from: number;
  to: number;
  /** Days of service in this week, 1–7. */
  days: number;
  /** True when the week has fewer than 7 days of service (start or end in the middle of the week). */
  partWeek: boolean;
  /** Visits recorded in this week. */
  visits: number;
  /** Minutes recorded in this week. */
  minutes: number;
  /** Planned minutes: minutes a day × days of service. Undefined without a plan. */
  planned?: number;
}

export interface Rhythm {
  weeks: RhythmWeek[];
  /** Day number of the Monday the chart starts on (the left edge). */
  axisStart: number;
  /** Days from the left edge to the end of the last day of service (the right edge). */
  axisDays: number;
  start: CalendarDate;
  /** The last day on the chart: the end date when the service ended, otherwise today. */
  last: CalendarDate;
  /** True when the service has an end date on or before today. */
  ended: boolean;
  /** Totals over the whole service. */
  visits: number;
  minutes: number;
  /** Top of the scale: the highest week, or the highest planned week when the plan is higher. */
  scaleTop: number;
  /** Month labels: text and days from the left edge. */
  months: { label: string; offset: number }[];
  /** Why weeks are part weeks, for the note: start mid-week, end mid-week. */
  startsMidWeek: boolean;
  endsMidWeek: boolean;
}

const DAY = 86_400_000;
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const WEEKDAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'];
export const NBSP = ' ';

/** A day number back to a date. */
export function fromDayNumber(n: number): CalendarDate {
  const d = new Date(n * DAY);
  return { year: d.getUTCFullYear(), month: d.getUTCMonth() + 1, day: d.getUTCDate() };
}

/** 0 = Monday … 6 = Sunday. Day 0 (1 Jan 1970) was a Thursday. Weeks start on Monday. */
export function weekday(n: number): number {
  return (((n + 3) % 7) + 7) % 7;
}

/** The weekday name of a day number: "Wednesday". */
export function weekdayName(n: number): string {
  return WEEKDAYS[weekday(n)];
}

/** Whole number with a thousands comma: "3,790". Never breaks (no spaces inside). */
export function formatNumber(n: number): string {
  const r = Math.round(n);
  const digits = String(Math.abs(r)).replace(/\B(?=(\d{3})+(?!\d))/g, ',');
  return r < 0 ? `−${digits}` : digits;
}

/** A date that never breaks inside: "3 Jun 2026" with non-breaking spaces. */
export function formatDateNoBreak(d: CalendarDate): string {
  return formatDate(d).replace(/ /g, NBSP);
}

/** Month label (Figma): "Jul"; January shows the year only ("2027"), as in the Timeline. */
export function monthLabel(d: CalendarDate): string {
  return d.month === 1 ? String(d.year) : MONTHS[d.month - 1];
}

/**
 * Days a month label needs on the chart, from its first day to the right edge. A month that starts in the last days of
 * the chart has no room for its label, so it gets none (ASSUMPTION: 7 days, one full week).
 */
const MONTH_LABEL_MIN_DAYS = 7;

export interface BuildRhythmInput {
  visits: readonly RhythmVisit[];
  start: string;
  end?: string;
  today: CalendarDate;
  minutesPerDay?: number;
}

/**
 * Splits the service into Monday-to-Sunday weeks, cut to the days of service, and sums the visits into them.
 * Returns undefined when the start date is invalid or after the last day (nothing to draw).
 * Visits outside the service (before the start, after the end or after today), without a valid date, or without a
 * finite, non-negative number of minutes are left out.
 */
export function buildRhythm({ visits, start, end, today, minutesPerDay }: BuildRhythmInput): Rhythm | undefined {
  const startDate = parseDate(start);
  if (!startDate) return undefined;
  const endDate = end !== undefined ? parseDate(end) : undefined;
  const t = dayNumber(today);
  const ended = endDate !== undefined && dayNumber(endDate) <= t;
  const s = dayNumber(startDate);
  const l = ended && endDate ? dayNumber(endDate) : t;
  if (l < s) return undefined;

  const axisStart = s - weekday(s);
  const weeks: RhythmWeek[] = [];
  for (let monday = axisStart; monday <= l; monday += 7) {
    const from = Math.max(monday, s);
    const to = Math.min(monday + 6, l);
    const days = to - from + 1;
    weeks.push({
      monday: fromDayNumber(monday),
      from,
      to,
      days,
      partWeek: days < 7,
      visits: 0,
      minutes: 0,
      planned: minutesPerDay !== undefined ? minutesPerDay * days : undefined,
    });
  }

  let totalVisits = 0;
  let totalMinutes = 0;
  for (const v of visits) {
    const at = parseDate(v.date);
    if (!at || !Number.isFinite(v.minutes) || v.minutes < 0) continue;
    const n = dayNumber(at);
    if (n < s || n > l) continue;
    const week = weeks[Math.floor((n - axisStart) / 7)];
    week.visits += 1;
    week.minutes += v.minutes;
    totalVisits += 1;
    totalMinutes += v.minutes;
  }

  const scaleTop = Math.max(0, ...weeks.map((w) => w.minutes), ...weeks.map((w) => w.planned ?? 0));

  // Month labels: the first of every month in the service (Figma: a start on 3 Jun has no "Jun"), with room for its
  // label before the right edge.
  const months: Rhythm['months'] = [];
  const first = fromDayNumber(s);
  for (let index = first.year * 12 + first.month - (first.day === 1 ? 1 : 0); ; index += 1) {
    const d: CalendarDate = { year: Math.floor(index / 12), month: (index % 12) + 1, day: 1 };
    const n = dayNumber(d);
    if (n > l - MONTH_LABEL_MIN_DAYS + 1) break;
    months.push({ label: monthLabel(d), offset: n - axisStart });
  }

  return {
    weeks,
    axisStart,
    axisDays: l - axisStart + 1,
    start: startDate,
    last: fromDayNumber(l),
    ended,
    visits: totalVisits,
    minutes: totalMinutes,
    scaleTop,
    months,
    startsMidWeek: weekday(s) !== 0,
    endsMidWeek: weekday(l) !== 6,
  };
}

const plural = (n: number, one: string, many: string) => `${formatNumber(n)} ${n === 1 ? one : many}`;

/** Figma: the week's title. "Week of 1 Jun 2026: 4 visits, 120 min", plus ", part week" when it is one. */
export function weekTitle(w: RhythmWeek): string {
  return `Week of ${formatDate(w.monday)}: ${plural(w.visits, 'visit', 'visits')}, ${formatNumber(w.minutes)} min${w.partWeek ? ', part week' : ''}`;
}

/** Figma: the head totals. "· 132 recorded · 3,790 min · 3 Jun 2026 – 13 Oct 2026 (today)". */
export function totalsText(r: Rhythm): string {
  return (
    `· ${formatNumber(r.visits)}${NBSP}recorded · ${formatNumber(r.minutes)}${NBSP}min · ` +
    `${formatDateNoBreak(r.start)} – ${formatDateNoBreak(r.last)}${r.ended ? '' : `${NBSP}(today)`}`
  );
}

/** Figma: the key line under a chart with a plan. */
export function planCaption(minutesPerDay: number, draft: boolean): string {
  return `Dashed line: planned ${formatNumber(minutesPerDay)}${NBSP}min a day × days of service in that week${draft ? ' (draft plan)' : ''}.`;
}

/**
 * Figma: the part-week note. "2 part weeks (the service started on a Wednesday; this week runs to today): the plan line
 * follows the days of service, so part weeks compare like for like." Undefined when every week is a full week.
 */
export function partWeekNote(r: Rhythm): string | undefined {
  const count = r.weeks.filter((w) => w.partWeek).length;
  if (count === 0) return undefined;
  const reasons = [
    r.startsMidWeek ? `the service started on a ${weekdayName(dayNumber(r.start))}` : '',
    r.endsMidWeek ? (r.ended ? `the service ended on a ${weekdayName(dayNumber(r.last))}` : 'this week runs to today') : '',
  ].filter(Boolean);
  return `${plural(count, 'part week', 'part weeks')} (${reasons.join('; ')}): the plan line follows the days of service, so part weeks compare like for like.`;
}
