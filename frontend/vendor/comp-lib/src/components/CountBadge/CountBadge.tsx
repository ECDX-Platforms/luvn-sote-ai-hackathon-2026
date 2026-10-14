import { forwardRef, type HTMLAttributes } from 'react';
import styles from './CountBadge.module.css';

export interface CountBadgeProps extends Omit<HTMLAttributes<HTMLSpanElement>, 'children'> {
  /** Figma: Count. A whole number. Above 99 it shows "99+". 0, less, a fraction, NaN or Infinity renders nothing. */
  count: number;
}

/**
 * NEW (not in the styleguide). Small neutral number on an icon or nav item, e.g. new items on the bell.
 * Neutral only: the meaning goes in the button label ("Notifications, 3 new, 1 critical"), never in a badge colour.
 */
export const CountBadge = forwardRef<HTMLSpanElement, CountBadgeProps>(function CountBadge({ count, className, ...rest }, ref) {
  // Whole numbers from 1 only: 0, negatives, fractions, NaN and Infinity render nothing.
  if (!Number.isSafeInteger(count) || count <= 0) return null;
  return (
    <span {...rest} ref={ref} className={[styles.countBadge, 't-tag', className].filter(Boolean).join(' ')}>
      {count > 99 ? '99+' : count}
    </span>
  );
});
