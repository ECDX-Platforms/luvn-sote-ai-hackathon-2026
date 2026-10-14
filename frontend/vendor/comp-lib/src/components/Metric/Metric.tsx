import { forwardRef, type HTMLAttributes, type ReactNode } from 'react';
import { ArrowDown, ArrowUp } from 'lucide-react';
import styles from './Metric.module.css';

export type MetricStatus = 'normal' | 'above-range' | 'below-range';

export interface MetricProps extends Omit<HTMLAttributes<HTMLDivElement>, 'children'> {
  /** Figma: Value. The number. Never changes colour: only the trend arrow carries the status. */
  value: ReactNode;
  /** Figma: Unit. Shown under the value in caption, text-muted ("items", "%", "ms"). Optional. */
  unit?: ReactNode;
  /** Figma: Label. What the number is ("Item count"). Wraps when long. */
  label: ReactNode;
  /** Figma: Status. Out of range adds a 14px trend arrow in critical-fg next to the unit. */
  status?: MetricStatus;
  /** Text alternative for the trend arrow, read out instead of it. Defaults to "above range" or "below range". Plain words for end users ("a little high") go here. */
  statusText?: string;
}

const defaultStatusText: Record<Exclude<MetricStatus, 'normal'>, string> = {
  'above-range': 'above range',
  'below-range': 'below range',
};

/** One key number: value, unit, label. Styleguide: Metric (vitals). Put several in a Metric row. */
export const Metric = forwardRef<HTMLDivElement, MetricProps>(function Metric(
  { value, unit, label, status = 'normal', statusText, className, ...rest },
  ref,
) {
  const outOfRange = status !== 'normal';
  const Arrow = status === 'below-range' ? ArrowDown : ArrowUp;

  return (
    <div {...rest} ref={ref} className={[styles.metric, className].filter(Boolean).join(' ')}>
      <div className={[styles.value, 't-metric'].join(' ')}>{value}</div>
      {unit != null || outOfRange ? (
        <div className={[styles.unit, 't-caption'].join(' ')}>
          {unit != null ? <span>{unit}</span> : null}
          {outOfRange ? (
            <span className={styles.trend}>
              <Arrow size={14} aria-hidden />
              <span className="visually-hidden">{statusText ?? defaultStatusText[status]}</span>
            </span>
          ) : null}
        </div>
      ) : null}
      <div className={[styles.label, 't-body', 'muted'].join(' ')}>{label}</div>
    </div>
  );
});
