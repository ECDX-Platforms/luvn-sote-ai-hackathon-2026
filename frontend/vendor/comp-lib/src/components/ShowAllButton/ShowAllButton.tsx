import { forwardRef, useState, type ButtonHTMLAttributes, type MouseEvent } from 'react';
import { ChevronDown, ChevronRight } from 'lucide-react';
import styles from './ShowAllButton.module.css';

export interface ShowAllButtonProps extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, 'children'> {
  /**
   * Figma: Label ("Show all N"). N: the number of rows in the list that the client sees. Count only rows that are
   * shown in the list; hidden sensitive items are never counted.
   */
  count: number;
  /** Figma: State = Expanded. Controlled open state: the rest of the list is shown and the label reads "Show fewer". Use with `onExpandedChange`, or use `defaultExpanded` instead. */
  expanded?: boolean;
  /** Open on first render (uncontrolled). */
  defaultExpanded?: boolean;
  /** Called with the new open state when the button is clicked or toggled with Enter or Space. */
  onExpandedChange?: (expanded: boolean) => void;
}

/**
 * NEW. Show all button: sits under a list that is cut at 4 rows. "Show all N" with a › arrow; when open the label is
 * "Show fewer" with a ⌄ arrow. A button with `aria-expanded`: it reveals the rest of the list in place and focus
 * stays on it. Point `aria-controls` at the list's id. Collapsed, Hover (browser state) and Expanded are Figma's set.
 * Hover and pressed (interaction tint) and focus are browser states, never props. Styleguide v2.3: Thin record page.
 */
export const ShowAllButton = forwardRef<HTMLButtonElement, ShowAllButtonProps>(function ShowAllButton(
  { count, expanded, defaultExpanded = false, onExpandedChange, onClick, className, ...rest },
  ref,
) {
  const [ownExpanded, setOwnExpanded] = useState(defaultExpanded);
  const isControlled = expanded !== undefined;
  const open = isControlled ? expanded : ownExpanded;

  const handleClick = (event: MouseEvent<HTMLButtonElement>) => {
    onClick?.(event);
    if (event.defaultPrevented) return;
    const next = !open;
    if (!isControlled) setOwnExpanded(next);
    onExpandedChange?.(next);
  };

  const Chevron = open ? ChevronDown : ChevronRight;

  return (
    <button
      {...rest}
      type="button"
      ref={ref}
      className={[styles.button, 't-control', className].filter(Boolean).join(' ')}
      aria-expanded={open}
      onClick={handleClick}
    >
      <span className={styles.label}>{open ? 'Show fewer' : `Show all ${count}`}</span>
      <span className={styles.icon} aria-hidden>
        <Chevron size={16} strokeWidth={1.5} aria-hidden />
      </span>
    </button>
  );
});
