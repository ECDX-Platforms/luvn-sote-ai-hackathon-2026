/**
 * Care overview layout: structured care data in, chart positions out. Pure (no React, no DOM), so it runs in node:
 * `node src/components/CareOverview/overview.check.ts`.
 *
 * Positions are fractions of the track width (0 = the window start, 1 = the end of today), so the chart is always drawn
 * to scale (styleguide Charts). Rules (Figma description, Data display / Care overview 207:2113):
 * - Dates are whole days (`YYYY-MM-DD`; a time after it is ignored). Day d covers [d, d + 1). Invalid dates are skipped.
 * - Today is the right end. Records after today are cut: a later start is not drawn, a later end stops at today.
 * - Hidden (sensitive) items are not drawn and leave no gap marker.
 * - A lane with nothing to show is left out entirely.
 * - Medicines: one bar per substance. Windows merge only where they touch (the next starts the day after the last day,
 *   or earlier) or overlap. A gap of one day or more stays visible.
 * - Social care: request (hollow dot) → decision → service bar. A draft decision reads "decision (draft)" and has no
 *   service bar. The visit count counts only visits up to today.
 */

/** One diagnosis: a filled dot with its name. */
export interface CareDiagnosis {
  /** The diagnosis as the source writes it. Diagnoses on the same day share one dot ("A, B"). */
  name: string;
  /** Date recorded, `YYYY-MM-DD`. */
  date: string;
  /** Sensitive and not allowed for this role: not drawn, no gap marker. */
  hidden?: boolean;
}

/** One prescription window of a substance. */
export interface CareMedicineWindow {
  /** First day, `YYYY-MM-DD`. */
  start: string;
  /** Last day (inclusive), `YYYY-MM-DD`. Leave out for an open (ongoing) window. An end after today is cut at today. */
  end?: string;
  /** Not drawn. */
  hidden?: boolean;
}

/** One substance: one bar (or several, where the windows leave gaps). */
export interface CareMedicine {
  /** The substance ("Metformin"). Starts the label. */
  substance: string;
  /** Short detail after the substance, from the source ("regular", "1 week"). Label: "Metformin, regular". */
  detail?: string;
  /** Every prescription or renewal window, any order. */
  windows: readonly CareMedicineWindow[];
  /** Not drawn. */
  hidden?: boolean;
}

/** The decision on a social care request. */
export interface CareSocialDecision {
  /** Date of the decision, `YYYY-MM-DD`. A decision after today is not shown yet. */
  date: string;
  /** A draft decision reads "decision (draft)" and has no service bar. */
  draft?: boolean;
}

/** The service granted by the decision. */
export interface CareSocialService {
  /** The service in words, lower case inside the label ("home care"). */
  name: string;
  /** First day, `YYYY-MM-DD`. */
  start: string;
  /** Last day (inclusive). Leave out while the service is ongoing. An end after today is cut at today. */
  end?: string;
  /** Visits: a count, or the visit dates (then only valid dates up to today are counted). Label: "105 visits". */
  visits?: number | readonly string[];
}

/** One social care case: request → decision → service. */
export interface CareSocialCase {
  /** Date of the request, `YYYY-MM-DD`. Drawn as a hollow dot. */
  requested: string;
  /** The decision. Leave out while there is none: the label then reads "Request". An invalid date or one after today is not shown. */
  decision?: CareSocialDecision;
  /** Drawn as a bar only after a decision that is not a draft. */
  service?: CareSocialService;
  /** Not drawn. */
  hidden?: boolean;
}

export interface CareOverviewInput {
  /** The as-of date, `YYYY-MM-DD`: the right end of the chart. */
  today: string;
  /** Start of the window, `YYYY-MM-DD`. Default: the first day of the month of the earliest record shown. Earlier records are cut at it. */
  from?: string;
  /** Health lane: diagnoses as dots. */
  diagnoses?: readonly CareDiagnosis[];
  /** Medicines lane: one bar per substance. */
  medicines?: readonly CareMedicine[];
  /** Social care lane: request → decision → service. */
  socialCare?: readonly CareSocialCase[];
}

/** A month label on the axis. `x` is the tick (the label's left edge). */
export interface CareTick { label: string; x: number }
/** A drawn span: `x0` to `x1`. */
export interface CareSpan { x0: number; x1: number }
export interface CarePoint { label: string; x: number }

/** Diagnoses that share one row: dots with their names above. */
export interface CarePointsRow { kind: 'points'; points: CarePoint[] }
/** One substance. `inline`: a short bar with the label beside it (Figma: Paracetamol); otherwise the label sits above. */
export interface CareBarsRow { kind: 'bars'; label: string; x: number; segments: CareSpan[]; inline: boolean }
/** One social care case. `request`: x of the hollow dot (left out when before the window). `service`: the bar. */
export interface CareCaseRow { kind: 'case'; label: string; x: number; request?: number; service?: CareSpan }
export type CareRow = CarePointsRow | CareBarsRow | CareCaseRow;

export type CareLaneKey = 'health' | 'medicines' | 'socialCare';
export interface CareLane { key: CareLaneKey; rows: CareRow[] }

export interface CareOverviewLayout {
  /** Window start and today as `YYYY-MM-DD`. */
  from: string;
  today: string;
  ticks: CareTick[];
  /** Only lanes with something to show, in the order Health, Medicines, Social care. */
  lanes: CareLane[];
}

/** Diagnoses closer than this (a quarter of the track) go on separate rows, so their names do not run into each other. */
export const MIN_POINT_GAP = 0.25;
/** A substance shorter than this (5% of the track) is a short bar: label beside it, if it starts by INLINE_MAX. */
export const SHORT_BAR = 0.05;
/** A short bar starting after this (70%) keeps its label above: beside it, the label would have under a quarter of the track. */
export const INLINE_MAX = 0.7;
/** At most this many month labels. */
export const MAX_TICKS = 4;
/** No month label in the last fifth: that room belongs to the last label and "Today". */
export const TICK_MAX_X = 0.8;

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const DAY_MS = 86_400_000;

/** Day number (days since 1970-01-01, UTC) of `YYYY-MM-DD`, or undefined when it is not a real date. */
export function parseDay(text: string | undefined): number | undefined {
  if (typeof text !== 'string') return undefined;
  const m = /^(\d{4})-(\d{2})-(\d{2})(?:T.*)?$/.exec(text.trim());
  if (!m) return undefined;
  const [y, mo, d] = [Number(m[1]), Number(m[2]), Number(m[3])];
  const date = new Date(Date.UTC(y, mo - 1, d));
  // Rejects 2026-02-30 and the like: the date must come back as written.
  if (date.getUTCFullYear() !== y || date.getUTCMonth() !== mo - 1 || date.getUTCDate() !== d) return undefined;
  return date.getTime() / DAY_MS;
}

const monthOf = (day: number) => {
  const d = new Date(day * DAY_MS);
  return d.getUTCFullYear() * 12 + d.getUTCMonth();
};
const monthStart = (month: number) => Date.UTC(Math.floor(month / 12), month % 12, 1) / DAY_MS;
const isoOf = (day: number) => new Date(day * DAY_MS).toISOString().slice(0, 10);
const monthLabel = (month: number) => `${MONTHS[month % 12]} ${Math.floor(month / 12)}`;

interface Window { start: number; end: number; ongoing: boolean } // end exclusive

/** Medicine windows that can be drawn: valid, not hidden, started by today, end not before start. End cut at today. */
function medicineWindows(med: CareMedicine, today: number): Window[] {
  const out: Window[] = [];
  for (const w of med.windows ?? []) {
    if (w.hidden) continue;
    const start = parseDay(w.start);
    if (start === undefined || start > today) continue;
    if (w.end !== undefined) {
      const last = parseDay(w.end);
      if (last === undefined || last < start) continue; // invalid end: skipped, never guessed as ongoing
      out.push({ start, end: Math.min(last, today) + 1, ongoing: last > today });
    } else {
      out.push({ start, end: today + 1, ongoing: true });
    }
  }
  return out;
}

/** Merges windows that touch or overlap (inclusive days: the next starts on or before the day after the last day). */
export function mergeWindows<T extends { start: number; end: number; ongoing?: boolean }>(windows: readonly T[]): Window[] {
  const sorted = [...windows].sort((a, b) => a.start - b.start);
  const merged: Window[] = [];
  for (const w of sorted) {
    const prev = merged[merged.length - 1];
    if (prev && w.start <= prev.end) {
      prev.end = Math.max(prev.end, w.end);
      prev.ongoing = prev.ongoing || Boolean(w.ongoing);
    } else {
      merged.push({ start: w.start, end: w.end, ongoing: Boolean(w.ongoing) });
    }
  }
  return merged;
}

interface Social {
  request: number;
  decision?: { date: number; draft: boolean };
  service?: { name: string; start: number; end: number; ongoing: boolean; visits?: number };
}

function socialCase(c: CareSocialCase, today: number): Social | undefined {
  if (c.hidden) return undefined;
  const request = parseDay(c.requested);
  if (request === undefined || request > today) return undefined;
  const out: Social = { request };
  const decided = c.decision ? parseDay(c.decision.date) : undefined;
  if (c.decision && decided !== undefined && decided <= today) {
    out.decision = { date: decided, draft: Boolean(c.decision.draft) };
    const s = c.service;
    const start = s ? parseDay(s.start) : undefined;
    if (s && !c.decision.draft && start !== undefined && start <= today) {
      const last = s.end !== undefined ? parseDay(s.end) : undefined;
      const validEnd = s.end === undefined || (last !== undefined && last >= start);
      if (validEnd) {
        let visits: number | undefined;
        if (typeof s.visits === 'number') {
          visits = Number.isInteger(s.visits) && s.visits >= 0 ? s.visits : undefined;
        } else if (Array.isArray(s.visits)) {
          visits = s.visits.filter((v) => {
            const day = parseDay(v);
            return day !== undefined && day <= today;
          }).length;
        }
        out.service = {
          name: s.name,
          start,
          end: last !== undefined ? Math.min(last, today) + 1 : today + 1,
          ongoing: last === undefined || last > today,
          visits,
        };
      }
    }
  }
  return out;
}

/** Month ticks from the first month start in the window: the smallest step (1, 2, 3, 6, 12 … months) giving at most MAX_TICKS labels. */
export function monthTicks(from: number, endExclusive: number): CareTick[] {
  const span = endExclusive - from;
  if (!(span > 0)) return [];
  const first = monthOf(from) + (monthStart(monthOf(from)) < from ? 1 : 0);
  const spanMonths = Math.ceil(span / 30.4375) + 1;
  const steps = [1, 2, 3, 6, 12, 24, 36, 60, 120];
  for (let i = 0; ; i += 1) {
    const step = i < steps.length ? steps[i] : 12 * Math.ceil(spanMonths / 12 / MAX_TICKS) * (i - steps.length + 1);
    const ticks: CareTick[] = [];
    for (let month = first; ; month += step) {
      const x = (monthStart(month) - from) / span;
      if (x > TICK_MAX_X) break;
      ticks.push({ label: monthLabel(month), x });
      if (ticks.length > MAX_TICKS) break;
    }
    if (ticks.length <= MAX_TICKS) return ticks;
  }
}

/** Greedy rows: each point goes on the first row whose last point is at least `gap` to its left. */
export function packPoints(xs: readonly number[], gap = MIN_POINT_GAP): number[] {
  const lastOnRow: number[] = [];
  return xs.map((x) => {
    let row = lastOnRow.findIndex((last) => x - last >= gap - 1e-9);
    if (row === -1) row = lastOnRow.push(x) - 1;
    else lastOnRow[row] = x;
    return row;
  });
}

const visitWord = (n: number) => `${n} ${n === 1 ? 'visit' : 'visits'}`;

/** The chart for the given data, or undefined when `today` is not a valid date. */
export function careOverviewLayout(input: CareOverviewInput): CareOverviewLayout | undefined {
  const today = parseDay(input.today);
  if (today === undefined) return undefined;
  const end = today + 1;

  // 1. What can be drawn at all (valid, not hidden, by today), before the window cut.
  const diagnoses = (input.diagnoses ?? [])
    .filter((d) => !d.hidden)
    .map((d) => ({ name: d.name, day: parseDay(d.date) }))
    .filter((d): d is { name: string; day: number } => d.day !== undefined && d.day <= today);
  const medicines = (input.medicines ?? [])
    .filter((m) => !m.hidden)
    .map((m) => ({ med: m, windows: mergeWindows(medicineWindows(m, today)) }))
    .filter((m) => m.windows.length > 0);
  const cases = (input.socialCare ?? [])
    .map((c) => socialCase(c, today))
    .filter((c): c is Social => c !== undefined);

  // 2. The window: `from`, or the first of the month of the earliest record shown.
  const starts = [
    ...diagnoses.map((d) => d.day),
    ...medicines.map((m) => m.windows[0].start),
    ...cases.map((c) => c.request),
  ];
  const given = parseDay(input.from);
  const from = given !== undefined && given <= today
    ? given
    : monthStart(monthOf(starts.length ? Math.min(...starts) : today));
  const x = (day: number) => (Math.min(Math.max(day, from), end) - from) / (end - from);
  const clip = (start: number, stop: number): CareSpan | undefined =>
    stop <= from ? undefined : { x0: x(start), x1: x(stop) };

  const lanes: CareLane[] = [];

  // 3. Health: dots, same-day diagnoses share one dot, rows packed so names do not collide.
  const byDay = new Map<number, string[]>();
  for (const d of diagnoses.filter((d) => d.day >= from).sort((a, b) => a.day - b.day)) {
    byDay.set(d.day, [...(byDay.get(d.day) ?? []), d.name]);
  }
  const points = [...byDay].map(([day, names]) => ({ label: names.join(', '), x: x(day) }));
  if (points.length) {
    const rowOf = packPoints(points.map((p) => p.x));
    const rows: CarePointsRow[] = [];
    points.forEach((p, i) => {
      (rows[rowOf[i]] ??= { kind: 'points', points: [] }).points.push(p);
    });
    lanes.push({ key: 'health', rows });
  }

  // 4. Medicines: one row per substance, in order of first use.
  const medRows: CareBarsRow[] = [];
  for (const { med, windows } of medicines) {
    const segments = windows.map((w) => clip(w.start, w.end)).filter((s): s is CareSpan => s !== undefined);
    if (!segments.length) continue;
    const ongoing = windows[windows.length - 1].ongoing;
    const label = `${med.substance}${med.detail ? `, ${med.detail}` : ''}${ongoing ? ' · ongoing' : ''}`;
    const x0 = segments[0].x0;
    const inline = segments[segments.length - 1].x1 - x0 < SHORT_BAR && x0 <= INLINE_MAX;
    medRows.push({ kind: 'bars', label, x: x0, segments, inline });
  }
  if (medRows.length) lanes.push({ key: 'medicines', rows: medRows.sort((a, b) => a.x - b.x) });

  // 5. Social care: one row per case.
  const caseRows: CareCaseRow[] = [];
  for (const c of cases) {
    const request = c.request >= from ? x(c.request) : undefined;
    const service = c.service ? clip(c.service.start, c.service.end) : undefined;
    if (request === undefined && !service) continue;
    const steps = ['Request'];
    if (c.decision) steps.push(c.decision.draft ? 'decision (draft)' : 'decision');
    if (c.service) steps.push(c.service.name);
    const parts = [steps.join(' → ')];
    if (c.service?.visits !== undefined) parts.push(visitWord(c.service.visits));
    if (c.service?.ongoing) parts.push('ongoing');
    caseRows.push({ kind: 'case', label: parts.join(' · '), x: request ?? service!.x0, request, service });
  }
  if (caseRows.length) lanes.push({ key: 'socialCare', rows: caseRows.sort((a, b) => a.x - b.x) });

  return { from: isoOf(from), today: isoOf(today), ticks: monthTicks(from, end), lanes };
}
