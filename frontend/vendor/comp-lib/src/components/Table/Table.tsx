import {
  forwardRef,
  useLayoutEffect,
  useRef,
  useState,
  type AnchorHTMLAttributes,
  type ButtonHTMLAttributes,
  type CSSProperties,
  type HTMLAttributes,
  type MouseEventHandler,
  type ReactNode,
  type Ref,
  type TableHTMLAttributes,
  type TdHTMLAttributes,
  type ThHTMLAttributes,
} from 'react';
import { ChevronDown, ChevronRight, ChevronUp } from 'lucide-react';
import { Link } from '../Link';
import styles from './Table.module.css';

export type TableAlign = 'left' | 'right';
export type TableSort = 'none' | 'ascending' | 'descending';

const join = (...names: Array<string | false | undefined>) => names.filter(Boolean).join(' ');

export interface TableProps extends Omit<TableHTMLAttributes<HTMLTableElement>, 'aria-label'> {
  /** Accessible name of the table, e.g. the title of the block it sits in. Also names the scroll area when the table is wider than its container. */
  label: string;
  /** Class for the outer box (the scroll container), e.g. to set a max-height. Every other attribute and the ref go to the `table`. */
  className?: string;
  /** Inline style for the outer box (the scroll container). */
  style?: CSSProperties;
  /** Class for the `table` element itself. */
  tableClassName?: string;
  /** TableHeader and TableBody. */
  children: ReactNode;
}

/**
 * Data table. Fills the width of its container; columns share the width. When the table is wider than the container,
 * it scrolls inside its own box, the page never scrolls sideways, and a 1px line marks each side that has more to
 * scroll to. The box is then a named region and a Tab stop, so keyboard users can scroll it with the arrow keys.
 */
export const Table = forwardRef<HTMLTableElement, TableProps>(function Table(
  { label, className, style, tableClassName, children, ...rest },
  ref,
) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const [scrollable, setScrollable] = useState(false);
  const [moreBefore, setMoreBefore] = useState(false);
  const [moreAfter, setMoreAfter] = useState(false);

  useLayoutEffect(() => {
    const scroller = scrollRef.current;
    const table = scroller?.firstElementChild;
    if (!scroller || !table) return;
    const check = () => {
      // Compare the table with the outer box, not scrollWidth with clientWidth: the box gains padding while it
      // scrolls, and this test gives the same answer in both modes, so the mode never flips back and forth.
      const overflows = table.getBoundingClientRect().width > scroller.getBoundingClientRect().width + 0.5;
      const offset = Math.abs(scroller.scrollLeft); // abs: right-to-left pages count scrollLeft down from 0
      setScrollable(overflows);
      setMoreBefore(overflows && offset > 0.5);
      setMoreAfter(overflows && offset + scroller.clientWidth < scroller.scrollWidth - 0.5);
    };
    check();
    const observer = new ResizeObserver(check);
    observer.observe(scroller);
    observer.observe(table);
    scroller.addEventListener('scroll', check, { passive: true });
    return () => {
      observer.disconnect();
      scroller.removeEventListener('scroll', check);
    };
  }, []);

  return (
    <div
      ref={scrollRef}
      style={style}
      className={join(styles.scroll, scrollable && styles.scrollable, className)}
      data-more-before={moreBefore || undefined}
      data-more-after={moreAfter || undefined}
      // Only a box that really scrolls is a region and a Tab stop (WCAG 2.1.1; axe scrollable-region-focusable).
      role={scrollable ? 'region' : undefined}
      aria-label={scrollable ? label : undefined}
      tabIndex={scrollable ? 0 : undefined}
    >
      <table {...rest} ref={ref} aria-label={rest['aria-labelledby'] ? undefined : label} className={join(styles.table, tableClassName)}>
        {children}
      </table>
    </div>
  );
});

export interface TableHeaderProps extends HTMLAttributes<HTMLTableSectionElement> {
  /** The TableHeaderCell elements, one per column. */
  children: ReactNode;
}

/** Figma: Table header. The header row of a table (`thead` with one `tr`). Holds one TableHeaderCell per column. */
export const TableHeader = forwardRef<HTMLTableSectionElement, TableHeaderProps>(function TableHeader({ children, ...rest }, ref) {
  return (
    <thead {...rest} ref={ref}>
      <tr>{children}</tr>
    </thead>
  );
});

export interface TableHeaderCellProps extends Omit<ThHTMLAttributes<HTMLTableCellElement>, 'scope' | 'align' | 'aria-sort'> {
  /** Figma: Align. Right for number columns, so the header sits over the right-aligned numbers. */
  align?: TableAlign;
  /** Figma: Sort. The column the table is sorted by shows a 12px chevron (up = ascending, down = descending) and sets `aria-sort`. Every other column is `none`: no chevron. */
  sort?: TableSort;
  /**
   * Makes the column sortable: the label becomes a button that fills the cell. Called with the new direction:
   * `ascending` first, then it toggles. The table keeps the state and sets every other column back to `none`.
   */
  onSortChange?: (next: Exclude<TableSort, 'none'>) => void;
  /** Figma: Label. The column name, at most four words. Shown in capitals (overline style); write it in sentence case. */
  children: ReactNode;
}

/** Figma: Table header cell. A column header (`th scope="col"`) in the overline style, with a hairline below. */
export const TableHeaderCell = forwardRef<HTMLTableCellElement, TableHeaderCellProps>(function TableHeaderCell(
  { align = 'left', sort = 'none', onSortChange, className, children, ...rest },
  ref,
) {
  const Chevron = sort === 'ascending' ? ChevronUp : sort === 'descending' ? ChevronDown : null;
  const content = (
    <span className={styles.thContent}>
      <span>{children}</span>
      {Chevron ? (
        <span className={styles.chevron} aria-hidden>
          <Chevron size={12} strokeWidth={2} />
        </span>
      ) : null}
    </span>
  );

  return (
    <th
      {...rest}
      ref={ref}
      scope="col"
      aria-sort={sort === 'none' ? undefined : sort}
      className={join(styles.th, align === 'right' && styles.right, onSortChange && styles.sortable, 't-overline', className)}
    >
      {onSortChange ? (
        <button type="button" className={styles.sortButton} onClick={() => onSortChange(sort === 'ascending' ? 'descending' : 'ascending')}>
          {content}
        </button>
      ) : (
        content
      )}
    </th>
  );
});

export interface TableBodyProps extends HTMLAttributes<HTMLTableSectionElement> {
  /** TableRow elements, or one TableEmptyRow when there is nothing to show. */
  children: ReactNode;
}

/** The body of a table (`tbody`). Holds the TableRow elements. */
export const TableBody = forwardRef<HTMLTableSectionElement, TableBodyProps>(function TableBody({ children, ...rest }, ref) {
  return (
    <tbody {...rest} ref={ref}>
      {children}
    </tbody>
  );
});

export interface TableRowProps extends Omit<HTMLAttributes<HTMLTableRowElement>, 'aria-current'> {
  /**
   * Figma: State = Selected. The row the user has open, e.g. its details show next to the table: a 2px bar in text
   * colour on the left edge, first cell padding 8. Sets `aria-current="true"` (`aria-selected` is not valid on rows of a plain table).
   */
  selected?: boolean;
  /**
   * Figma: Show arrow. A › in link colour at the end of a clickable row (v2.3). Default on: styleguide over Figma (Figma
   * default off; the styleguide says clickable rows always have the arrow). Only shows when the row
   * has a TableRowAction: a row that cannot be clicked has no hover and no arrow, so this prop does nothing there.
   * The arrow is decorative (`aria-hidden`); the row's action carries the name.
   */
  showArrow?: boolean;
  /** The TableCell elements. To make the whole row one link or button, put a TableRowAction in its main cell. */
  children: ReactNode;
}

/**
 * Figma: Table row. Body text, 36 high (44 comfortable), 1px hairline below (none on the last row). A row with a
 * TableRowAction is clickable: hover = interaction-tint, the whole row is the target, and the focus ring goes around the whole row.
 * `showArrow` adds a › in link colour at the end of a clickable row.
 */
export const TableRow = forwardRef<HTMLTableRowElement, TableRowProps>(function TableRow(
  { selected = false, showArrow = true, className, children, ...rest },
  ref,
) {
  return (
    <tr {...rest} ref={ref} aria-current={selected ? 'true' : undefined} className={join(styles.row, showArrow && styles.hasArrow, className)}>
      {children}
      {showArrow ? (
        // A decorative cell, taken out of the table layout in CSS (absolute, at the row end), so the column count and
        // the header are unchanged. It is hidden unless the row is clickable.
        <td className={styles.arrowCell} aria-hidden>
          <ChevronRight size={16} strokeWidth={1.5} aria-hidden />
        </td>
      ) : null}
    </tr>
  );
});

export interface TableCellProps extends Omit<TdHTMLAttributes<HTMLTableCellElement>, 'align'> {
  /** Right for numbers, so they line up under a right-aligned header. */
  align?: TableAlign;
  /** A number (count, amount, version): right-aligned, tabular figures, never broken across lines. Sets align right unless `align` says otherwise. */
  numeric?: boolean;
  /** The one value per row the eye should land on, in body-strong (600): the value the user reads (Figma: the record name in the first column). One strong cell per row. */
  strong?: boolean;
  /** Text, a number, a Flag, or a TableRowAction. */
  children?: ReactNode;
}

/** One data cell (`td`): body text, padding 8 16 8 0, top-aligned. Long text wraps; words stay whole. */
export const TableCell = forwardRef<HTMLTableCellElement, TableCellProps>(function TableCell(
  { align, numeric = false, strong = false, className, children, ...rest },
  ref,
) {
  const right = (align ?? (numeric ? 'right' : 'left')) === 'right';
  return (
    <td
      {...rest}
      ref={ref}
      className={join(styles.td, right && styles.right, numeric && styles.numeric, strong ? 't-body-strong' : 't-body', className)}
    >
      {children}
    </td>
  );
});

type TableRowActionBase = {
  /** Names where the row goes or what it opens, e.g. the item name. It is the cell's visible text. */
  children: ReactNode;
  /** Extra class for the action element (`a` or `button`). */
  className?: string;
};
export type TableRowActionProps =
  | (TableRowActionBase &
      Omit<AnchorHTMLAttributes<HTMLAnchorElement>, 'children' | 'className'> & {
        /** Where the row goes. With `href` the action is a link (`a`); use it when the row opens its own page. */
        href: string;
      })
  | (TableRowActionBase &
      Omit<ButtonHTMLAttributes<HTMLButtonElement>, 'children' | 'className' | 'type'> & {
        /** Leave out for a button (`button type="button"`); use onClick, e.g. to select the row or open a panel. */
        href?: undefined;
      });

/**
 * Makes the whole row one target. Put it in the row's main cell around the text that names the row (usually the strong
 * cell). With `href` it is a link (`a`), without it a button (use onClick). Only one per row, and no other links or
 * buttons in a clickable row: the target covers the whole row.
 */
export const TableRowAction = forwardRef<HTMLAnchorElement | HTMLButtonElement, TableRowActionProps>(function TableRowAction(
  props,
  ref,
) {
  if (props.href !== undefined) {
    const { className, children, ...rest } = props;
    return (
      <a {...rest} ref={ref as Ref<HTMLAnchorElement>} className={join(styles.rowAction, className)}>
        {children}
      </a>
    );
  }
  const { className, children, ...rest } = props;
  return (
    <button {...rest} ref={ref as Ref<HTMLButtonElement>} type="button" className={join(styles.rowAction, styles.rowButton, className)}>
      {children}
    </button>
  );
});

export interface TableEmptyRowProps extends Omit<HTMLAttributes<HTMLTableRowElement>, 'children'> {
  /** The number of columns, so the row spans the whole table. Required: the table does not count its columns. */
  colSpan: number;
  /** The search or filter text that found nothing. The message then reads: No results for ‘query’. */
  query?: string;
  /** Figma: Message. Replaces the default message, e.g. "No items yet." when nothing has been added. One sentence. */
  children?: ReactNode;
  /** Where "Clear filters" goes: the same view without filters. The link only shows when this is set. */
  clearFiltersHref?: string;
  /** Called when "Clear filters" is chosen, e.g. to reset filters without a page load (call event.preventDefault()). */
  onClearFilters?: MouseEventHandler<HTMLAnchorElement>;
}

/**
 * Figma: Table empty row. One row across all columns: the message in text-muted plus a "Clear filters" link (the
 * existing Link). Use it as the only row of TableBody when a search or filter finds nothing.
 */
export const TableEmptyRow = forwardRef<HTMLTableRowElement, TableEmptyRowProps>(function TableEmptyRow(
  { colSpan, query, children, clearFiltersHref, onClearFilters, className, ...rest },
  ref,
) {
  const message = children ?? (query ? `No results for ‘${query}’.` : 'No results.');
  return (
    <tr {...rest} ref={ref} className={join(styles.row, className)}>
      <td colSpan={colSpan} className={join(styles.td, styles.emptyCell, 't-body')}>
        <span className={styles.emptyContent}>
          <span className={styles.emptyMessage}>{message}</span>
          {clearFiltersHref !== undefined ? (
            <Link href={clearFiltersHref} onClick={onClearFilters}>
              Clear filters
            </Link>
          ) : null}
        </span>
      </td>
    </tr>
  );
});
