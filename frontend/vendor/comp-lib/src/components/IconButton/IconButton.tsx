import { forwardRef, type ButtonHTMLAttributes, type MouseEvent, type ReactNode } from 'react';
import { Tooltip } from '../Tooltip';
import styles from './IconButton.module.css';

export type IconButtonSize = 'sm' | 'md' | 'lg';

export interface IconButtonProps extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, 'disabled' | 'children' | 'aria-label'> {
  /** Figma: Icon. A 16px icon, e.g. a lucide icon at size 16. */
  icon: ReactNode;
  /** Accessible name, read out instead of the icon. Also shown as the tooltip on hover and keyboard focus. */
  label: string;
  /** Figma: Size. Square 28, 36 or 44. In comfortable density every size is 44. */
  size?: IconButtonSize;
  /** Muted icon, no hover. Stays focusable (aria-disabled) so a tooltip can say why. */
  disabled?: boolean;
  /**
   * Shows `label` in a Tooltip on hover and keyboard focus (styleguide: every icon-only button needs one). Default true.
   * Set false only where the button already has its own Tooltip around it, so it never shows two.
   */
  tooltip?: boolean;
}

/** Ghost button for toolbar icons (search, close, bell, menu). Styleguide: Button → Ghost. */
export const IconButton = forwardRef<HTMLButtonElement, IconButtonProps>(function IconButton(
  { icon, label, size = 'md', disabled = false, tooltip = true, type = 'button', className, onClick, ...rest },
  ref,
) {
  const handleClick = (event: MouseEvent<HTMLButtonElement>) => {
    if (disabled) {
      event.preventDefault();
      event.stopPropagation(); // a disabled button inside a clickable row or card must not trigger it
      return;
    }
    onClick?.(event);
  };

  const button = (
    <button
      {...rest}
      ref={ref}
      type={type}
      className={[styles.iconButton, styles[size], className].filter(Boolean).join(' ')}
      aria-label={label}
      aria-disabled={disabled || undefined}
      onClick={handleClick}
    >
      <span className={styles.icon} aria-hidden>{icon}</span>
    </button>
  );

  // The tooltip repeats the aria-label, so it is hidden from screen readers (relationship "label").
  // It renders as a sibling right after the button (position: fixed, out of flow).
  return tooltip ? <Tooltip text={label} relationship="label">{button}</Tooltip> : button;
});
