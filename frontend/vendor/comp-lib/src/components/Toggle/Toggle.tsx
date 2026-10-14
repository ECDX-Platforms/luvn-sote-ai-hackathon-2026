import { forwardRef, useState, type ButtonHTMLAttributes, type MouseEvent, type ReactNode } from 'react';
import styles from './Toggle.module.css';

export interface ToggleProps extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, 'disabled' | 'onChange' | 'children' | 'role' | 'aria-checked'> {
  /** Figma: Checked (Off / On). Controlled: use with `onCheckedChange`. */
  checked?: boolean;
  /** Uncontrolled start value. */
  defaultChecked?: boolean;
  /** Called with the new value when people switch it. The change should take effect at once (no Save button). */
  onCheckedChange?: (checked: boolean) => void;
  /** Figma: State = Disabled. Line border, muted thumb and label. Stays focusable (aria-disabled) so a hint can say why. */
  disabled?: boolean;
  /** Figma: Label. Part of the click target; it is the switch's accessible name. Wraps when long. */
  children: ReactNode;
}

/** NEW (not in the styleguide). Switch for an instant on/off setting, e.g. a list filter. `role="switch"` with aria-checked. */
export const Toggle = forwardRef<HTMLButtonElement, ToggleProps>(function Toggle(
  { checked, defaultChecked = false, onCheckedChange, disabled = false, type = 'button', className, onClick, children, ...rest },
  ref,
) {
  const [own, setOwn] = useState(defaultChecked);
  const isOn = checked ?? own;
  const handleClick = (event: MouseEvent<HTMLButtonElement>) => {
    if (disabled) {
      event.preventDefault();
      return;
    }
    onClick?.(event);
    if (event.defaultPrevented) return;
    if (checked === undefined) setOwn(!isOn);
    onCheckedChange?.(!isOn);
  };

  return (
    <button
      {...rest}
      ref={ref}
      type={type}
      role="switch"
      aria-checked={isOn}
      aria-disabled={disabled || undefined}
      className={[styles.toggle, 't-body', className].filter(Boolean).join(' ')}
      onClick={handleClick}
    >
      <span className={styles.slot} aria-hidden>
        <span className={styles.track}>
          <span className={styles.thumb} />
        </span>
      </span>
      <span className={styles.label}>{children}</span>
    </button>
  );
});
