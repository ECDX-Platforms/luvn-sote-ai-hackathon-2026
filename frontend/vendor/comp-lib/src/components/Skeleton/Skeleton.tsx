import { forwardRef, type HTMLAttributes } from 'react';
import styles from './Skeleton.module.css';

export type SkeletonLines = 1 | 3;

export interface SkeletonProps extends Omit<HTMLAttributes<HTMLDivElement>, 'children'> {
  /** Figma: Lines. 1 bar (60% wide) or 3 bars (80%, 60%, 40%). */
  lines?: SkeletonLines;
  /** Read out instead of the bars, which are hidden from screen readers. */
  label?: string;
}

/** Widths stay inside the styleguide's 40–80% of the container. */
const widths: Record<SkeletonLines, string[]> = {
  1: ['60%'],
  3: ['80%', '60%', '40%'],
};

/** Static loading placeholder. Styleguide: Empty, loading and error states → Loading. */
export const Skeleton = forwardRef<HTMLDivElement, SkeletonProps>(function Skeleton(
  { lines = 1, label = 'Loading…', className, ...rest },
  ref,
) {
  return (
    <div {...rest} ref={ref} className={[styles.skeleton, className].filter(Boolean).join(' ')}>
      {widths[lines].map((width, index) => (
        <span key={index} className={styles.bar} style={{ width }} aria-hidden />
      ))}
      <span className="visually-hidden">{label}</span>
    </div>
  );
});
