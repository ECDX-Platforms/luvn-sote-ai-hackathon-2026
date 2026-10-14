/**
 * Date and number rules for Result series (Figma description, 13 Oct 2026, option A). Pure functions, no locale:
 * the same input always gives the same text, so stories and tests can pass a fixed `today`.
 */

/** A calendar date with no time and no time zone. */
export interface CalendarDate {
  year: number;
  /** 1–12 */
  month: number;
  /** 1–31 */
  day: number;
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const NBSP = ' ';
const MINUS = '−';

/** Reads `YYYY-MM-DD`. Returns undefined for anything else, including dates that do not exist (2026-02-30). */
export function parseDate(text: string): CalendarDate | undefined {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(text);
  if (!match) return undefined;
  const [year, month, day] = [Number(match[1]), Number(match[2]), Number(match[3])];
  const check = new Date(Date.UTC(year, month - 1, day));
  if (check.getUTCFullYear() !== year || check.getUTCMonth() !== month - 1 || check.getUTCDate() !== day) return undefined;
  return { year, month, day };
}

/** The viewer's local date today. */
export function localToday(): CalendarDate {
  const now = new Date();
  return { year: now.getFullYear(), month: now.getMonth() + 1, day: now.getDate() };
}

/** Whole days since 1 Jan 1970: compares and subtracts dates without time-zone drift. */
export function dayNumber(d: CalendarDate): number {
  return Date.UTC(d.year, d.month - 1, d.day) / 86_400_000;
}

/** Adds whole months. A day that does not exist in the target month moves to its last day (31 Jan + 1 month = 28 Feb). */
function addMonths(d: CalendarDate, months: number): CalendarDate {
  const index = d.year * 12 + (d.month - 1) + months;
  const year = Math.floor(index / 12);
  const month = (index % 12) + 1;
  const lastDay = new Date(Date.UTC(year, month, 0)).getUTCDate();
  return { year, month, day: Math.min(d.day, lastDay) };
}

/**
 * Whole years, months and days from `from` to `to`, always rounded down (Figma: 1 year 7 months 29 days is
 * "1 year 7 months"). A `to` before `from` counts as no time at all.
 */
export function elapsed(from: CalendarDate, to: CalendarDate): { years: number; months: number; days: number } {
  if (dayNumber(to) <= dayNumber(from)) return { years: 0, months: 0, days: 0 };
  let months = (to.year - from.year) * 12 + (to.month - from.month);
  if (dayNumber(addMonths(from, months)) > dayNumber(to)) months -= 1;
  const days = dayNumber(to) - dayNumber(addMonths(from, months));
  return { years: Math.floor(months / 12), months: months % 12, days };
}

const plural = (n: number, one: string, many: string) => `${n}${NBSP}${n === 1 ? one : many}`;

/**
 * Figma: Age. "1 month ago", "1 year 7 months ago", "12 days ago", "Today". Years and months only, once it is a month
 * or more; days only under a month. Numbers never break from their word (non-breaking space).
 */
export function ageText(latest: CalendarDate, today: CalendarDate): string {
  const { years, months, days } = elapsed(latest, today);
  if (years === 0 && months === 0) return days === 0 ? 'Today' : `${plural(days, 'day', 'days')} ago`;
  const parts = [years > 0 ? plural(years, 'year', 'years') : '', months > 0 ? plural(months, 'month', 'months') : ''];
  return `${parts.filter(Boolean).join(' ')} ago`;
}

/** Figma: Gap. The time from the previous result to the latest: "1 mo", "1 y 2 mo", "12 days". Rounded down. */
export function gapText(previous: CalendarDate, latest: CalendarDate): string {
  const { years, months, days } = elapsed(previous, latest);
  if (years === 0 && months === 0) return plural(days, 'day', 'days');
  return [years > 0 ? `${years}${NBSP}y` : '', months > 0 ? `${months}${NBSP}mo` : ''].filter(Boolean).join(' ');
}

/** True when the latest result is more than one year old: today is after the date one year on. */
export function isOverAYearOld(latest: CalendarDate, today: CalendarDate): boolean {
  return dayNumber(today) > dayNumber(addMonths(latest, 12));
}

/** English UI date (styleguide Content): "4 Sep 2026". Always with the year. */
export function formatDate(d: CalendarDate): string {
  return `${d.day} ${MONTHS[d.month - 1]} ${d.year}`;
}

/** Month and year: "Jan 2026". */
export function formatMonth(d: CalendarDate): string {
  return `${MONTHS[d.month - 1]} ${d.year}`;
}

/** A value rounded to `decimals`, with a real minus sign. Never "−0.0". */
export function formatValue(value: number, decimals: number): string {
  const text = Math.abs(value).toFixed(decimals);
  return value < 0 && Number(text) !== 0 ? `${MINUS}${text}` : text;
}

/** A number as written, with a real minus sign ("30", "0", "−2.5"). For the scale caption. */
export function formatPlain(value: number): string {
  return value < 0 ? `${MINUS}${Math.abs(value)}` : String(value);
}

/**
 * Figma: Change. The latest value minus the previous one, from the values as shown (so the numbers on screen add up),
 * with a sign and the percentage of the previous value in whole percent: "−0.7 mmol/mol (−1%)". No sign on zero.
 * No percentage when the previous value is zero.
 */
export function changeText(previous: number, latest: number, decimals: number, unit: string): string {
  const shownPrevious = Number(previous.toFixed(decimals));
  const shownLatest = Number(latest.toFixed(decimals));
  const diff = Number((shownLatest - shownPrevious).toFixed(decimals));
  const signed = (n: number, text: string) => (n > 0 ? `+${text}` : n < 0 ? `${MINUS}${text}` : text);
  const amount = `${signed(diff, Math.abs(diff).toFixed(decimals))}${NBSP}${unit}`;
  if (shownPrevious === 0) return amount;
  const percent = Math.round(Math.abs((diff / shownPrevious) * 100)) * Math.sign(diff);
  return `${amount} (${signed(percent, String(Math.abs(percent)))}%)`;
}
