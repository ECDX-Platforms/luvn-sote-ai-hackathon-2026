// Calendar helpers for the timeline components. Dates come in as ISO calendar days ("2026-10-13") and are read as
// calendar days in UTC, so time zones and daylight saving never shift them. 64px = one calendar month, whatever its
// length: a date's position is its month plus the share of that month before it.

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const NBSP = ' ';

export interface Day {
  year: number;
  /** 0 to 11 */
  month: number;
  /** 1 to 31 */
  day: number;
}

/** Parses "YYYY-MM-DD". Null when it is not a real calendar day. */
export function parseDay(iso: string | null | undefined): Day | null {
  if (!iso) return null;
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso);
  if (!match) return null;
  const [year, month, day] = [Number(match[1]), Number(match[2]) - 1, Number(match[3])];
  const date = new Date(Date.UTC(year, month, day));
  if (date.getUTCFullYear() !== year || date.getUTCMonth() !== month || date.getUTCDate() !== day) return null;
  return { year, month, day };
}

export function toIso({ year, month, day }: Day): string {
  return `${String(year).padStart(4, '0')}-${String(month + 1).padStart(2, '0')}-${String(day).padStart(2, '0')}`;
}

function daysInMonth(year: number, month: number): number {
  return new Date(Date.UTC(year, month + 1, 0)).getUTCDate();
}

/** Content format "4 Sep 2026" (styleguide Content), with non-breaking spaces so a date never breaks. A date that cannot be read is returned as given. */
export function formatDay(iso: string): string {
  const d = parseDay(iso);
  return d ? `${d.day}${NBSP}${MONTHS[d.month]}${NBSP}${d.year}` : iso;
}

/** "Oct 2025", with a non-breaking space. */
export function formatMonth(d: Pick<Day, 'year' | 'month'>): string {
  return `${MONTHS[d.month]}${NBSP}${d.year}`;
}

/** Short month name ("Oct"). */
export function monthName(month: number): string {
  return MONTHS[month];
}

/** Months from `origin` to `date`, as a fraction: the first of a month is a whole number. */
export function monthsBetween(origin: Day, date: Day): number {
  const whole = (date.year - origin.year) * 12 + (date.month - origin.month);
  return whole + (date.day - 1) / daysInMonth(date.year, date.month) - (origin.day - 1) / daysInMonth(origin.year, origin.month);
}

/** The same day `count` months later (or earlier), cut to the month's last day ("31 Mar" − 1 month = "28 Feb"). */
export function addMonths(d: Day, count: number): Day {
  const total = d.year * 12 + d.month + count;
  const year = Math.floor(total / 12);
  const month = total - year * 12;
  return { year, month, day: Math.min(d.day, daysInMonth(year, month)) };
}

/** The calendar day at `position` months after `origin` (the inverse of monthsBetween, to the day). */
export function dayAt(origin: Day, position: number): Day {
  const startShare = (origin.day - 1) / daysInMonth(origin.year, origin.month);
  const absolute = position + startShare;
  const whole = Math.floor(absolute);
  const first = addMonths({ ...origin, day: 1 }, whole);
  const length = daysInMonth(first.year, first.month);
  return { ...first, day: Math.min(length, Math.floor((absolute - whole) * length) + 1) };
}

export function startOfMonth(d: Day): Day {
  return { ...d, day: 1 };
}

/** Compares two ISO days as text (works because the format is fixed): negative when a is earlier. */
export function compareIso(a: string, b: string): number {
  return a < b ? -1 : a > b ? 1 : 0;
}
