import { forwardRef, type ChangeEvent, type InputHTMLAttributes, type MouseEvent, type ReactNode } from 'react';
import { Check } from 'lucide-react';
import styles from './Checkbox.module.css';

export interface CheckboxProps extends Omit<InputHTMLAttributes<HTMLInputElement>, 'type' | 'disabled' | 'children'> {
  /** Figma: Checked (Off / On). Use `checked` + `onChange`, or `defaultChecked` for an uncontrolled checkbox. */
  checked?: boolean;
  /** Figma: State = Disabled. Muted box, tick and label. Stays focusable (aria-disabled) so a hint can say why; it cannot be changed. */
  disabled?: boolean;
  /** Figma: Label. Part of the click target. Wraps when long; the box stays on the first line. */
  children: ReactNode;
}

/** Checkbox with its label. Styleguide: Checkbox and task item. `className` and `style` go on the label row; every other prop goes on the `<input>`. */
export const Checkbox = forwardRef<HTMLInputElement, CheckboxProps>(function Checkbox(
  { disabled = false, className, style, onClick, onChange, children, ...rest },
  ref,
) {
  const handleClick = (event: MouseEvent<HTMLInputElement>) => {
    if (disabled) {
      event.preventDefault(); // the browser puts the old value back
      return;
    }
    onClick?.(event);
  };
  const handleChange = (event: ChangeEvent<HTMLInputElement>) => {
    if (!disabled) onChange?.(event);
  };

  return (
    <label className={[styles.checkbox, disabled ? styles.disabled : null, 't-body', className].filter(Boolean).join(' ')} style={style}>
      <span className={styles.slot}>
        <span className={styles.box}>
          <input
            {...rest}
            ref={ref}
            type="checkbox"
            className={styles.input}
            aria-disabled={disabled || undefined}
            onClick={handleClick}
            onChange={handleChange}
          />
          <Check className={styles.tick} size={12} strokeWidth={2} aria-hidden />
        </span>
      </span>
      <span className={styles.label}>{children}</span>
    </label>
  );
});
