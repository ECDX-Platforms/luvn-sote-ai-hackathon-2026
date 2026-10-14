import { forwardRef, type CSSProperties, type HTMLAttributes, type ReactNode } from 'react';
import {
  careOverviewLayout,
  type CareDiagnosis,
  type CareLaneKey,
  type CareMedicine,
  type CareRow,
  type CareSocialCase,
} from './overview';
import styles from './CareOverview.module.css';

export interface CareOverviewWhyNow {
  /** The source's own words about this appointment. Shown in quotation marks. Never an inference from an older note or a past request. */
  quote: ReactNode;
  /** The Source reference of that record (`<SourceReference layout="inline" … />`). Required: a quote without its source is not shown. */
  source: ReactNode;
}

export interface CareOverviewProps extends Omit<HTMLAttributes<HTMLDivElement>, 'children'> {
  /** The as-of date, `YYYY-MM-DD`: the right end of the chart ("Today"). Records after it are cut. */
  today: string;
  /** Start of the window, `YYYY-MM-DD`. Default: the first day of the month of the earliest record shown. Earlier records are cut at it. */
  from?: string;
  /** Health lane: diagnoses, drawn as 8px filled dots with their names. Hidden ones are not drawn. */
  diagnoses?: readonly CareDiagnosis[];
  /** Medicines lane: one 6px data-colour bar per substance, from its prescription windows. Hidden ones are not drawn. */
  medicines?: readonly CareMedicine[];
  /** Social care lane: request (hollow dot) → decision → service bar, with the visit count. Hidden ones are not drawn. */
  socialCare?: readonly CareSocialCase[];
  /**
   * Figma: Show why now. Default off (leave it out). Only a source that refers to this appointment, quoted, with its
   * Source reference. Our data has no booking reasons, so expect it to be off.
   */
  whyNow?: CareOverviewWhyNow;
  /** Lane names, overline style. Defaults: "Health", "Medicines", "Social care". */
  laneLabels?: Partial<Record<CareLaneKey, string>>;
  /** The label at the right end of the axis. Default "Today". */
  todayLabel?: string;
}

const DEFAULT_LANE_LABELS: Record<CareLaneKey, string> = { health: 'Health', medicines: 'Medicines', socialCare: 'Social care' };

/** A fraction of the track as a CSS percentage. */
const pct = (x: number) => `${Math.round(x * 1e6) / 1e4}%`;
/** A dot centred on x, kept inside the track. */
const dotLeft = (x: number) => `clamp(0px, calc(${pct(x)} - var(--dot) / 2), calc(100% - var(--dot)))`;
/**
 * A bar: left edge kept far enough in for its minimum width (6, Figma). `next` = the left edge of the substance's next
 * segment: the bar stops at least 2px before it, so a short gap between windows stays visible (no implied continuous use).
 */
const barStyle = (x0: number, x1: number, next?: number): CSSProperties => {
  const width = `max(var(--bar), ${pct(x1 - x0)})`;
  return {
    left: `min(${pct(x0)}, calc(100% - var(--bar)))`,
    width: next === undefined ? width : `max(1px, min(${width}, calc(${pct(next - x0)} - 2px)))`,
  };
};

/** Labels above the marks: each label starts at its mark and runs to the next label on the row (the last one to the end). */
function LabelLine({ items, dotOffset }: { items: { label: string; x: number }[]; dotOffset: boolean }) {
  const lead = items[0].x;
  return (
    <div className={styles.labels}>
      {/* The label's left edge lines up with the mark's left edge (Figma): for a dot, half a dot before its centre. */}
      <span className={styles.lead} style={{ flexBasis: dotOffset ? `max(0px, calc(${pct(lead)} - var(--dot) / 2))` : pct(lead) }} />
      {items.map((item, i) => {
        const next = items[i + 1];
        return next ? (
          <span key={i} className={[styles.label, styles.cell, 't-caption'].join(' ')} style={{ flexBasis: pct(next.x - item.x) }}>{item.label}</span>
        ) : (
          <span key={i} className={[styles.label, styles.lastCell, 't-caption'].join(' ')}>{item.label}</span>
        );
      })}
    </div>
  );
}

function Row({ row }: { row: CareRow }) {
  if (row.kind === 'points') {
    return (
      <div className={styles.row}>
        <LabelLine items={row.points} dotOffset />
        <div className={styles.marks}>
          {row.points.map((p, i) => <span key={i} className={styles.dot} style={{ left: dotLeft(p.x) }} />)}
        </div>
      </div>
    );
  }
  if (row.kind === 'bars') {
    const bars = row.segments.map((s, i) => (
      <span key={i} className={styles.bar} style={barStyle(s.x0, s.x1, row.segments[i + 1]?.x0)} />
    ));
    if (row.inline) {
      const last = row.segments[row.segments.length - 1];
      // A short bar: the label follows the bar on the same line (Figma: Paracetamol), 4 after its end.
      const after = `calc(max(${pct(last.x1)}, ${pct(row.x)} + var(--bar)) + var(--space-2xs))`;
      return (
        <div className={[styles.row, styles.inlineRow].join(' ')}>
          <div className={styles.inlineLine}>
            <div className={[styles.marks, styles.inlineMarks].join(' ')}>{bars}</div>
            <span className={[styles.label, styles.inlineLabel, 't-caption'].join(' ')} style={{ paddingLeft: after }}>{row.label}</span>
          </div>
        </div>
      );
    }
    return (
      <div className={styles.row}>
        <LabelLine items={[{ label: row.label, x: row.x }]} dotOffset={false} />
        <div className={styles.marks}>{bars}</div>
      </div>
    );
  }
  return (
    <div className={styles.row}>
      <LabelLine items={[{ label: row.label, x: row.x }]} dotOffset={row.request !== undefined} />
      <div className={styles.marks}>
        {row.service ? <span className={styles.bar} style={barStyle(row.service.x0, row.service.x1)} /> : null}
        {/* After the bar, so the request dot sits on top where they meet. */}
        {row.request !== undefined ? <span className={styles.hollow} style={{ left: dotLeft(row.request) }} /> : null}
      </div>
    </div>
  );
}

/**
 * NEW. Care overview: what changed in this client's care, at a glance, up to today. A horizontal bar chart computed from
 * structured data (always to scale), with lanes for Health, Medicines and Social care. Figma: Data display / Care overview (207:2113).
 * The chart is `aria-hidden` and holds no links or focusable parts: put the "what changed" Timeline (with its links)
 * directly under it as the accessible version.
 */
export const CareOverview = forwardRef<HTMLDivElement, CareOverviewProps>(function CareOverview(
  { today, from, diagnoses, medicines, socialCare, whyNow, laneLabels, todayLabel = 'Today', className, ...rest },
  ref,
) {
  const layout = careOverviewLayout({ today, from, diagnoses, medicines, socialCare });
  const lanes = layout?.lanes ?? [];
  const showWhyNow = Boolean(whyNow && whyNow.quote && whyNow.source);
  if (!lanes.length && !showWhyNow) return null;
  const names = { ...DEFAULT_LANE_LABELS, ...laneLabels };
  const ticks = layout?.ticks ?? [];

  return (
    <div {...rest} ref={ref} className={[styles.overview, className].filter(Boolean).join(' ')}>
      {showWhyNow && whyNow ? (
        <p className={[styles.whyNow, 't-body'].join(' ')}>
          <strong className="t-body-strong">Why now:</strong>{' '}
          <span className={styles.quote}>“{whyNow.quote}”</span>{' '}
          {whyNow.source}
        </p>
      ) : null}

      {lanes.length ? (
        // Hidden from screen readers and holds nothing focusable: the Timeline list under it is the accessible version.
        <div className={styles.chart} aria-hidden>
          <div className={styles.axis}>
            <span className={styles.spacer} />
            <div className={styles.axisTrack}>
              {ticks.length ? <span className={styles.axisLead} style={{ flexBasis: pct(ticks[0].x) }} /> : null}
              {ticks.map((t, i) => {
                const next = ticks[i + 1];
                return next ? (
                  <span key={t.label} className={[styles.tick, 't-caption'].join(' ')} style={{ flexBasis: pct(next.x - t.x) }}>{t.label}</span>
                ) : (
                  <span key={t.label} className={[styles.tick, styles.lastTick, 't-caption'].join(' ')}>
                    <span>{t.label}</span>
                    <span className={styles.today}>{todayLabel}</span>
                  </span>
                );
              })}
              {ticks.length ? null : <span className={[styles.lastTick, 't-caption'].join(' ')}><span className={styles.today}>{todayLabel}</span></span>}
            </div>
          </div>

          <div className={styles.lanes}>
            {lanes.map((lane) => (
              <div key={lane.key} className={styles.lane}>
                <span className={[styles.laneLabel, 't-overline'].join(' ')}>{names[lane.key]}</span>
                <div className={styles.track}>
                  {lane.rows.map((row, i) => <Row key={i} row={row} />)}
                </div>
              </div>
            ))}
            {/* Today: 1px line-strong at the right end of the lanes. */}
            <span className={styles.todayLine} />
          </div>
        </div>
      ) : null}
    </div>
  );
});
