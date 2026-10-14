import { forwardRef, type InputHTMLAttributes } from 'react';
import styles from './Input.module.css';

/** Input types people type text into. Only these: disabled works through readOnly, which browsers ignore for
 * checkbox, radio, range, color, file and the like. Use Checkbox, Radio or Toggle for those. */
export const TEXT_INPUT_TYPES = ['text', 'email', 'password', 'search', 'tel', 'url', 'number', 'date', 'datetime-local', 'month', 'time', 'week'] as const;
export type InputType = (typeof TEXT_INPUT_TYPES)[number];
const textTypes: ReadonlySet<string> = new Set(TEXT_INPUT_TYPES);

export interface InputProps extends Omit<InputHTMLAttributes<HTMLInputElement>, 'disabled' | 'size' | 'aria-invalid' | 'type'> {
  /** A type people type text into (default text). Checkbox, radio, range, color, file: use Checkbox, Radio or Toggle. */
  type?: InputType;
  /** Figma: State = Error. 2px critical border drawn inside (no layout shift) and aria-invalid. Put the message in Field. */
  invalid?: boolean;
  /** Figma: State = Disabled. Muted text, line border. Stays focusable (aria-disabled) so a hint can say why; the value cannot be changed. */
  disabled?: boolean;
  /** Figma: State = Read-only. No border, no padding: the value reads as plain body text. Still focusable and copyable. */
  readOnly?: boolean;
  /** Figma: Placeholder. Shown only while empty, in text-muted. Never the only label: use Input inside Field. */
  placeholder?: string;
}

/** One-line text input. Styleguide: Text input, textarea, select. Use it inside Field (label, hint, error message). */
export const Input = forwardRef<HTMLInputElement, InputProps>(function Input(
  { invalid = false, disabled = false, readOnly = false, type = 'text', className, ...rest },
  ref,
) {
  // Plain JavaScript callers are not stopped by the type: fall back to a text field, which disabled does block.
  const safeType = textTypes.has(type) ? type : 'text';
  if (safeType !== type) console.error(`Input: type="${type}" is not supported (disabled could not block it). Rendering type="text". Use Checkbox, Radio or Toggle.`);
  return (
    <input
      {...rest}
      ref={ref}
      type={safeType}
      className={[styles.input, readOnly && !disabled ? styles.readOnly : null, 't-body', className].filter(Boolean).join(' ')}
      // Disabled keeps focus (aria-disabled) and blocks typing through readOnly.
      readOnly={readOnly || disabled}
      aria-disabled={disabled || undefined}
      aria-invalid={invalid || undefined}
    />
  );
});
