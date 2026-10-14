import { forwardRef, useId, type HTMLAttributes } from 'react';
import { Info } from 'lucide-react';
import {
  ageText,
  changeText,
  dayNumber,
  formatDate,
  formatMonth,
  formatPlain,
  formatValue,
  gapText,
  isOverAYearOld,
  localToday,
  parseDate,
  type CalendarDate,
} from './dates';
import type { ResultSeriesScale } from './scales';
import styles from './ResultSeries.module.css';

export type ResultSeriesHeadingLevel = 3 | 4 | 5 | 6;

export interface ResultSeriesResult {
  /** The date of the result, `YYYY-MM-DD`. Places the point by date and orders the results. */
  date: string;
  /** The value as a number. Shown rounded to `decimals`. */
  value: number;
}

export interface ResultSeriesProps extends Omit<HTMLAttributes<HTMLDivElement>, 'children' | 'results'> {
  /** Figma: Test. The test name as the source writes it ("B-HbA1c"). Body-strong, a heading. Also names the chart for screen readers. */
  test: string;
  /** Figma: Unit. Shown after the latest value in caption, text-muted ("mmol/mol"), and in the change and the chart label. */
  unit: string;
  /**
   * Every result of this test, from the source, in any order (sorted by date). Results without a valid date or a finite
   * value are left out rather than drawn in the wrong place. With no valid result the component renders nothing.
   * Computed from these, so they cannot disagree with the data: Figma: Count (one result = Single, two or more = Series),
   * Latest value, Latest date, Age, Change, Gap, Summary and No newer results.
   */
  results: readonly ResultSeriesResult[];
  /**
   * Figma: Scale. The fixed scale of this test: `min` is the bottom gridline, `max` the top. Set once per test and the
   * same on every screen; never fitted to the data. Sample values: `resultSeriesSampleScales` (a professional must check them).
   * A value outside the scale is drawn on the nearest edge; the chart label keeps the true value.
   */
  scale: ResultSeriesScale;
  /**
   * Today, `YYYY-MM-DD`. The chart runs from the first result to today, and the age and "No newer results on record" are
   * counted to it. Default: the viewer's local date. Pass a fixed date in stories and tests.
   */
  today?: string;
  /** Decimals shown for every value (Figma: values rounded to one decimal). Default 1. */
  decimals?: number;
  /** Heading level of the test name. Default 3: a result series usually sits in a Block, whose title is an h2. */
  headingLevel?: ResultSeriesHeadingLevel;
}

const NBSP = ' ';
const clamp = (n: number) => Math.min(100, Math.max(0, n));

/**
 * One lab test over time, values only: the latest value with its age, the change since the previous result, and a
 * line chart on a fixed scale from the first result to today. No flags, no range lines, no trend words.
 * NEW: Figma: Data display / Result series (177:962), option A. Styleguide v2.3: Charts.
 */
export const ResultSeries = forwardRef<HTMLDivElement, ResultSeriesProps>(function ResultSeries(
  { test, unit, results, scale, today, decimals = 1, headingLevel = 3, className, ...rest },
  ref,
) {
  const titleId = useId();
  const Heading = `h${headingLevel}` as const;

  const valid = results
    .map((r) => ({ value: r.value, date: r.date, at: parseDate(r.date) }))
    .filter((r): r is { value: number; date: string; at: CalendarDate } => r.at !== undefined && Number.isFinite(r.value))
    .sort((a, b) => dayNumber(a.at) - dayNumber(b.at));
  const latest = valid[valid.length - 1];
  if (!latest) return null;
  const previous = valid.length > 1 ? valid[valid.length - 2] : undefined;
  const first = valid[0];
  const now = (today !== undefined ? parseDate(today) : undefined) ?? localToday();

  const show = (v: number) => formatValue(v, decimals);
  const values = valid.map((r) => r.value);
  const summary = previous
    ? `${valid.length} results since ${formatMonth(first.at)}, between ${show(Math.min(...values))} and ${show(Math.max(...values))}`
    : 'Only result';
  const scaleText = `${formatPlain(scale.min)}–${formatPlain(scale.max)}`;

  // Chart geometry in percent of the plot. x: first result (0) to today (100); a result after today sits on the today line.
  // y: the fixed scale, top = max. A value outside it is clamped to the edge (the label keeps the true value).
  const t0 = dayNumber(first.at);
  const span = dayNumber(now) - t0;
  const scaleOk = Number.isFinite(scale.min) && Number.isFinite(scale.max) && scale.max > scale.min;
  if (!scaleOk) console.error('ResultSeries: scale.max must be a number above scale.min. The points are drawn in the middle.');
  const points = valid.map((r, i) => ({
    key: i,
    x: span > 0 ? clamp(((dayNumber(r.at) - t0) / span) * 100) : 100,
    y: scaleOk ? clamp(((scale.max - r.value) / (scale.max - scale.min)) * 100) : 50,
  }));
  const chartLabel =
    `${test}, ${unit}, fixed scale ${scaleText}, from ${formatDate(first.at)} to today, ${formatDate(now)}. ` +
    `${valid.length === 1 ? '1 result' : `${valid.length} results`}: ` +
    valid.map((r) => `${show(r.value)} ${unit} on ${formatDate(r.at)}`).join(', ') +
    '.';

  return (
    <div {...rest} ref={ref} role="group" aria-labelledby={titleId} className={[styles.series, className].filter(Boolean).join(' ')}>
      <Heading id={titleId} className={[styles.test, 't-body-strong'].join(' ')}>{test}</Heading>

      <p className={styles.row}>
        <span className={[styles.latestValue, 't-metric'].join(' ')}>{show(latest.value)}</span>
        <span className={[styles.unit, 't-caption'].join(' ')}>
          {/* A space for screen readers and copy; the gap draws it. */}
          <span className="visually-hidden">{NBSP}</span>
          {unit}
        </span>
      </p>

      <p className={[styles.row, styles.tight].join(' ')}>
        <span className={[styles.strong, 't-body-strong'].join(' ')}>{ageText(latest.at, now)}</span>
        <span className={[styles.meta, 't-caption'].join(' ')}>
          {'· '}
          <time dateTime={latest.date}>{formatDate(latest.at)}</time>
        </span>
      </p>

      {previous ? (
        <p className={[styles.row, styles.tight].join(' ')}>
          <span className={[styles.strong, 't-body-strong'].join(' ')}>
            {changeText(previous.value, latest.value, decimals, unit)}
          </span>
          <span className={[styles.meta, 't-caption'].join(' ')}>{`· ${gapText(previous.at, latest.at)} since previous`}</span>
        </p>
      ) : null}

      {/* The chart is one image for assistive tech: its label lists every value and date. Children are presentational. */}
      <div className={styles.chart} role="img" aria-label={chartLabel}>
        <span className={[styles.gridline, styles.gridTop].join(' ')} />
        <span className={[styles.gridline, styles.gridBottom].join(' ')} />
        <span className={styles.today} />
        <div className={styles.plot}>
          {points.length > 1 ? (
            <svg className={styles.line} viewBox="0 0 100 100" preserveAspectRatio="none" focusable="false" aria-hidden>
              <polyline points={points.map((p) => `${p.x},${p.y}`).join(' ')} vectorEffect="non-scaling-stroke" />
            </svg>
          ) : null}
          {points.map((p) => (
            <span key={p.key} className={styles.point} style={{ left: `${p.x}%`, top: `${p.y}%` }} />
          ))}
        </div>
      </div>

      <p className={[styles.scaleRow, 't-caption'].join(' ')}>
        <span className={styles.meta}>{`fixed scale ${scaleText} · ${formatMonth(first.at)}`}</span>
        <span className={styles.todayLabel}>Today</span>
      </p>

      <p className={[styles.text, 't-body'].join(' ')}>{summary}</p>

      {isOverAYearOld(latest.at, now) ? (
        <p className={[styles.noNewer, 't-caption'].join(' ')}>
          <Info size={16} strokeWidth={1.5} aria-hidden />
          <span className={styles.text}>No newer results on record</span>
        </p>
      ) : null}

      <p className={[styles.text, 't-caption'].join(' ')}>No reference range in the source.</p>
    </div>
  );
});
