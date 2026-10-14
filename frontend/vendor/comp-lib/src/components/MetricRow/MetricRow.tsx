import { forwardRef, type HTMLAttributes, type ReactNode } from 'react';
import styles from './MetricRow.module.css';

export interface MetricRowProps extends HTMLAttributes<HTMLDivElement> {
  /** Metric cells, usually 4. Figma: Metric row holds 4 Metric instances. */
  children: ReactNode;
}

/** Row of Metric cells divided by hairlines. 4 columns from 1024px, 2 below. Styleguide: Metric (vitals), Layout → Inner grids. */
export const MetricRow = forwardRef<HTMLDivElement, MetricRowProps>(function MetricRow(
  { className, children, ...rest },
  ref,
) {
  return (
    <div {...rest} ref={ref} className={[styles.row, className].filter(Boolean).join(' ')}>
      {children}
    </div>
  );
});
