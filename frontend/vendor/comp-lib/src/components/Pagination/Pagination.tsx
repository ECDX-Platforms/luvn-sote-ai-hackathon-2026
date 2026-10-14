import { forwardRef, useId, type HTMLAttributes } from 'react';
import { ChevronLeft, ChevronRight } from 'lucide-react';
import { IconButton } from '../IconButton';
import styles from './Pagination.module.css';

export interface PaginationLabels {
  /** Accessible name of the previous button. */
  previous: string;
  /** Accessible name of the next button. */
  next: string;
  /** Why the previous button is disabled on the first page. */
  onFirstPage: string;
  /** Why the next button is disabled on the last page. */
  onLastPage: string;
}

export interface PaginationProps extends Omit<HTMLAttributes<HTMLElement>, 'children'> {
  /** Current page, starting at 1. Figma: Position = First on page 1, Last on the last page, Middle in between. */
  page: number;
  /** Rows per page. */
  pageSize: number;
  /** Number of rows in all pages. */
  total: number;
  /** Called with the new page number when Previous or Next is pressed. */
  onPageChange: (page: number) => void;
  /** Figma: Range. Text of the range, "1–20 of 86" by default. */
  formatRange?: (from: number, to: number, total: number) => string;
  /** Button names and the reasons a button is disabled. English by default. */
  labels?: Partial<PaginationLabels>;
  /** Accessible name of the navigation landmark. Give each one on a page its own name. */
  label?: string;
}

const defaultLabels: PaginationLabels = {
  previous: 'Previous page',
  next: 'Next page',
  onFirstPage: 'This is the first page.',
  onLastPage: 'This is the last page.',
};

const defaultRange = (from: number, to: number, total: number) => `${from}–${to} of ${total}`;

/** Range plus Previous and Next, under a data table. NEW: composed from Icon button sm and caption. */
export const Pagination = forwardRef<HTMLElement, PaginationProps>(function Pagination(
  { page, pageSize, total, onPageChange, formatRange = defaultRange, labels, label = 'Pagination', className, ...rest },
  ref,
) {
  const text = { ...defaultLabels, ...labels };
  const hintId = useId();
  const pageCount = Math.max(1, Math.ceil(total / pageSize));
  const current = Math.min(Math.max(1, page), pageCount);
  const from = total === 0 ? 0 : (current - 1) * pageSize + 1;
  const to = Math.min(current * pageSize, total);
  const lastFrom = total === 0 ? 0 : (pageCount - 1) * pageSize + 1;
  const isFirst = current === 1;
  const isLast = current === pageCount;

  return (
    <nav {...rest} ref={ref} aria-label={label} className={[styles.pagination, className].filter(Boolean).join(' ')}>
      <p className={`${styles.range} t-caption`}>
        {/* Polite live region: after Previous or Next, screen readers read the new range. */}
        <span aria-live="polite">{formatRange(from, to, total)}</span>
        {/* Hidden copy of the last page's range: keeps the width, so the buttons do not move while paging. */}
        <span className={styles.reserve} aria-hidden>{formatRange(lastFrom, total, total)}</span>
      </p>
      <IconButton
        size="sm"
        icon={<ChevronLeft size={16} strokeWidth={1.5} />}
        label={text.previous}
        disabled={isFirst}
        aria-describedby={isFirst ? `${hintId}-first` : undefined}
        onClick={() => onPageChange(current - 1)}
      />
      <IconButton
        size="sm"
        icon={<ChevronRight size={16} strokeWidth={1.5} />}
        label={text.next}
        disabled={isLast}
        aria-describedby={isLast ? `${hintId}-last` : undefined}
        onClick={() => onPageChange(current + 1)}
      />
      {/* The reasons a button is disabled. Hidden, so they are read only with the button (aria-describedby reads hidden text).
          The IconButtons' own tooltips show their names; these reasons are read as their descriptions. */}
      <span id={`${hintId}-first`} hidden>{text.onFirstPage}</span>
      <span id={`${hintId}-last`} hidden>{text.onLastPage}</span>
    </nav>
  );
});
