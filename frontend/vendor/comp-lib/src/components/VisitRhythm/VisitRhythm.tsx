import { forwardRef, useId, type HTMLAttributes } from 'react';
import { formatDate, localToday, parseDate } from '../ResultSeries/dates';
import {
  NBSP,
  buildRhythm,
  formatDateNoBreak,
  formatNumber,
  partWeekNote,
  planCaption,
  totalsText,
  weekTitle,
  type Rhythm,
  type RhythmVisit,
} from './rhythm';
import styles from './VisitRhythm.module.css';

export type VisitRhythmHeadingLevel = 3 | 4 | 5 | 6;

/** One recorded visit or contact: `{ date: "YYYY-MM-DD", minutes }`. */
export type VisitRhythmVisit = RhythmVisit;

export interface VisitRhythmPlan {
  /** Planned minutes a day, the plan's own number. The dashed line is this × the days of service in each week. */
  minutesPerDay: number;
  /** True when the plan is a draft (Figma: "(draft plan)" in the key line). Default true: say so when it is a draft. */
  draft?: boolean;
}

export interface VisitRhythmProps extends Omit<HTMLAttributes<HTMLDivElement>, 'children' | 'title'> {
  /** Figma: Title. What is counted, in body-strong, a heading ("Home visits"). Also names the chart for screen readers. */
  title: string;
  /**
   * Every recorded visit, from ONE source table only (the same visit can be stored in two tables; counting both doubles
   * the totals). Any order. Summed into Monday-to-Sunday weeks. Computed from these, so they cannot disagree with the data:
   * the head totals (N recorded · N min), the bars, the scale and every week's title. Visits before `start`, after `end`
   * or after `today`, without a valid date, or without a finite, non-negative number of minutes are left out.
   */
  visits: readonly VisitRhythmVisit[];
  /** First day of service, `YYYY-MM-DD`. The chart starts on its Monday; a start mid-week makes a narrower part week. */
  start: string;
  /**
   * Last day of service, `YYYY-MM-DD`, when the service ended. On or before `today`: the chart ends there and shows
   * "ended <date>" instead of the today line. After `today`: ignored until that day (the service is still running).
   */
  end?: string;
  /** Today, `YYYY-MM-DD`. The chart of a running service ends today. Default: the viewer's local date. Pass a fixed date in stories and tests. */
  today?: string;
  /**
   * Figma: Plan. With a plan (Plan=Yes): a dashed line of the planned minutes for each week (minutes a day × days of
   * service in that week, so a part week shows a shorter plan), a key line and the part-week note. Without (Plan=No): bars only.
   */
  plan?: VisitRhythmPlan;
  /** Heading level of the title. Default 3: a visit rhythm usually sits in a Block, whose title is an h2. */
  headingLevel?: VisitRhythmHeadingLevel;
}

const cx = (...names: (string | false | undefined)[]) => names.filter(Boolean).join(' ');

/** Plan line as SVG lines in percent of the plot: one run per stretch of equal weeks, a riser where the plan steps. */
function planLines(r: Rhythm, x: (day: number) => number, y: (minutes: number) => number) {
  const lines: { x1: number; y1: number; x2: number; y2: number }[] = [];
  let run: { x1: number; x2: number; y: number } | undefined;
  for (const w of r.weeks) {
    const level = y(w.planned ?? 0);
    const x1 = x(w.from);
    const x2 = x(w.to + 1);
    if (run && run.y === level) {
      run.x2 = x2;
      continue;
    }
    if (run) {
      lines.push({ x1: run.x1, y1: run.y, x2: run.x2, y2: run.y });
      lines.push({ x1: run.x2, y1: run.y, x2: run.x2, y2: level });
    }
    run = { x1, x2, y: level };
  }
  if (run) lines.push({ x1: run.x1, y1: run.y, x2: run.x2, y2: run.y });
  return lines;
}

/**
 * How much care was recorded, week by week, against the plan where a plan exists: recorded minutes per week as bars,
 * the plan as a dashed line that follows the days of service. No over or under verdict: the reader compares.
 * NEW: Figma: Data display / Visit rhythm (248:4877), Plan=Yes (248:4818), Plan=No (248:4854). Styleguide v2.3: Visit rhythm, Charts.
 */
export const VisitRhythm = forwardRef<HTMLDivElement, VisitRhythmProps>(function VisitRhythm(
  { title, visits, start, end, today, plan, headingLevel = 3, className, ...rest },
  ref,
) {
  const titleId = useId();
  const Heading = `h${headingLevel}` as const;
  const now = (today !== undefined ? parseDate(today) : undefined) ?? localToday();

  const activePlan = plan && Number.isFinite(plan.minutesPerDay) && plan.minutesPerDay >= 0 ? plan : undefined;
  if (plan && !activePlan) console.error('VisitRhythm: plan.minutesPerDay must be a number of 0 or more. The plan line is left out.');
  const r = buildRhythm({ visits, start, end, today: now, minutesPerDay: activePlan?.minutesPerDay });
  if (!r) {
    console.error('VisitRhythm: start must be a valid YYYY-MM-DD date on or before today (or the end date). Nothing is drawn.');
    return null;
  }

  // Geometry in percent of the plot. x: from the Monday of the first week (0) to the end of the last day (100).
  // y: bar height from the baseline, the top of the scale = 100.
  const x = (day: number) => ((day - r.axisStart) / r.axisDays) * 100;
  const top = r.scaleTop > 0 ? r.scaleTop : 1;
  const h = (minutes: number) => (minutes / top) * 100;
  const fromTop = (minutes: number) => 100 - h(minutes);

  const chartLabel =
    `${title}, minutes recorded per week, from ${formatDate(r.start)} to ` +
    `${r.ended ? `${formatDate(r.last)}, when the service ended` : `today, ${formatDate(r.last)}`}. ` +
    `Scale 0 to ${formatNumber(r.scaleTop)} min. ` +
    r.weeks.map(weekTitle).join('. ') +
    '.';
  const note = activePlan ? partWeekNote(r) : undefined;

  return (
    <div {...rest} ref={ref} role="group" aria-labelledby={titleId} className={cx(styles.rhythm, className)}>
      <div className={styles.head}>
        <Heading id={titleId} className={cx(styles.title, 't-body-strong')}>{title}</Heading>
        <p className={cx(styles.totals, 't-caption')}>{totalsText(r)}</p>
      </div>

      {/* One image for assistive tech: its label holds the range, the scale and every week's title. Children are
          presentational; each bar's `title` gives the same week text as a pointer tooltip. */}
      <div className={styles.chart} role="img" aria-label={chartLabel}>
        <div className={cx(styles.scale, 't-caption')}>
          <span className={styles.sizer}>{formatNumber(r.scaleTop)}</span>
          {r.scaleTop > 0 ? <span className={styles.scaleTop}>{formatNumber(r.scaleTop)}</span> : null}
          <span className={styles.scaleZero}>0</span>
        </div>

        <div className={styles.bars}>
          <div className={styles.plot}>
            {r.weeks.map((w) =>
              w.minutes > 0 ? (
                <span
                  key={w.from}
                  className={styles.bar}
                  title={weekTitle(w)}
                  style={{ left: `${x(w.from)}%`, width: `max(1px, calc(${((w.days / r.axisDays) * 100).toFixed(4)}% - 2px))`, height: `${h(w.minutes)}%` }}
                />
              ) : null,
            )}
            {activePlan ? (
              <svg className={styles.plan} focusable="false" aria-hidden shapeRendering="crispEdges">
                {planLines(r, x, fromTop).map((l, i) => (
                  <line key={i} x1={`${l.x1}%`} y1={`${l.y1}%`} x2={`${l.x2}%`} y2={`${l.y2}%`} />
                ))}
              </svg>
            ) : null}
          </div>
          {r.ended ? (
            <span className={cx(styles.ended, 't-caption')}>{`ended ${formatDateNoBreak(r.last)}`}</span>
          ) : (
            <span className={styles.today} />
          )}
        </div>

        <div className={cx(styles.months, 't-caption')}>
          {/* Gives the row one caption line of height; the labels are placed by date. */}
          {NBSP}
          <span className={styles.monthsPlot}>
            {r.months.map((m) => (
              <span key={m.offset} className={styles.month} style={{ left: `${(m.offset / r.axisDays) * 100}%` }}>{m.label}</span>
            ))}
          </span>
        </div>
      </div>

      {activePlan ? (
        <>
          <p className={cx(styles.text, 't-caption')}>{planCaption(activePlan.minutesPerDay, activePlan.draft ?? true)}</p>
          {note ? <p className={cx(styles.text, 't-caption')}>{note}</p> : null}
        </>
      ) : null}
    </div>
  );
});
