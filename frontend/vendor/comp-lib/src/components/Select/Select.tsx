import { forwardRef, type ReactNode, type SelectHTMLAttributes } from 'react';
import { ChevronDown } from 'lucide-react';
import styles from './Select.module.css';

export interface SelectProps extends Omit<SelectHTMLAttributes<HTMLSelectElement>, 'aria-invalid' | 'multiple' | 'size'> {
  /** Figma: State = Error. 2px critical border drawn inside (no layout shift) and aria-invalid. Put the message in Field. */
  invalid?: boolean;
  /**
   * Figma: State = Disabled. Muted text and chevron, line border.
   * Exception to the aria-disabled rule: a native select still opens and changes with aria-disabled,
   * so Select uses native `disabled` (not focusable). Say why in Field's hint.
   */
  disabled?: boolean;
  /** Figma: Value. The `<option>` elements. */
  children: ReactNode;
}

/** Native single select with the Input look and a 16px chevron. Styleguide: Text input, textarea, select. Use it inside Field. `className` and `style` go on the wrapper; every other prop goes on the `<select>`. */
export const Select = forwardRef<HTMLSelectElement, SelectProps>(function Select(
  { invalid = false, disabled = false, className, style, children, ...rest },
  ref,
) {
  return (
    <span className={[styles.wrap, disabled ? styles.disabled : null, className].filter(Boolean).join(' ')} style={style}>
      <select
        {...rest}
        ref={ref}
        className={[styles.select, 't-body'].join(' ')}
        disabled={disabled}
        aria-invalid={invalid || undefined}
      >
        {children}
      </select>
      <span className={styles.chevron} aria-hidden>
        <ChevronDown size={16} strokeWidth={1.5} />
      </span>
    </span>
  );
});
