import { forwardRef, type LiHTMLAttributes, type ReactNode } from 'react';
import styles from './TimelineTodayLine.module.css';

export interface TimelineTodayLineProps extends Omit<LiHTMLAttributes<HTMLLIElement>, 'children'> {
  /** Figma: Label (first part). The word before the date. Default "Today". */
  label?: ReactNode;
  /** Figma: Label (date part). The as-of date as shown, following the Content rules: `13 Oct 2026`. Never breaks. */
  date: ReactNode;
  /** Machine-readable date for the `<time>` element, e.g. `2026-10-13`. */
  dateTime: string;
}

/**
 * NEW. Timeline today line: marks the as-of date at the top of a newest-first Timeline. Renders an `<li>`: put it
 * first in a Timeline, before the TimelineEntry elements, and set `showTopRule={false}` on the first entry after it
 * (only the today rule sits above that entry). Not interactive.
 * Code over Figma: Figma asks for role="separator" on the list item. That makes the list invalid (an `<ol>` may only
 * own list items; axe `list`, WCAG 1.3.1) and hides the date from screen readers (a separator's content is not
 * read out), so it stays a plain list item that reads "Today · <date>". The rule is decorative (aria-hidden).
 */
export const TimelineTodayLine = forwardRef<HTMLLIElement, TimelineTodayLineProps>(function TimelineTodayLine(
  { label = 'Today', date, dateTime, className, ...rest },
  ref,
) {
  return (
    <li {...rest} ref={ref} className={[styles.today, className].filter(Boolean).join(' ')}>
      <span className={[styles.label, 't-body-strong'].join(' ')}>
        {label} · <time dateTime={dateTime} className={styles.date}>{date}</time>
      </span>
      <span className={styles.rule} aria-hidden />
    </li>
  );
});
