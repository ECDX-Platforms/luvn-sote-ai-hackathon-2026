import { forwardRef, useLayoutEffect, useRef, type Ref, type TextareaHTMLAttributes } from 'react';
import styles from './Textarea.module.css';

export interface TextareaProps extends Omit<TextareaHTMLAttributes<HTMLTextAreaElement>, 'disabled' | 'aria-invalid'> {
  /** Figma: State = Error. 2px critical border drawn inside (no layout shift) and aria-invalid. Put the message in Field. */
  invalid?: boolean;
  /** Figma: State = Disabled. Muted text, line border. Stays focusable (aria-disabled) so a hint can say why; the text cannot be changed. */
  disabled?: boolean;
  /** Read-only (styleguide; not a Figma state for Textarea): no border, no padding, value in body, as high as its text. Still focusable and copyable. */
  readOnly?: boolean;
  /** Figma: Placeholder. Shown only while empty, in text-muted. Never the only label: use Textarea inside Field. */
  placeholder?: string;
}

const supportsFieldSizing = typeof CSS !== 'undefined' && CSS.supports('field-sizing', 'content');

function setRef<T>(ref: Ref<T> | undefined, value: T | null) {
  if (typeof ref === 'function') ref(value);
  else if (ref) (ref as { current: T | null }).current = value;
}

/** Multi-line text input. Styleguide: Text input, textarea, select. Use it inside Field (label, hint, error message). */
export const Textarea = forwardRef<HTMLTextAreaElement, TextareaProps>(function Textarea(
  { invalid = false, disabled = false, readOnly = false, className, ...rest },
  ref,
) {
  const own = useRef<HTMLTextAreaElement | null>(null);
  const fitsText = readOnly && !disabled;

  // Read-only fits its text. Browsers without CSS field-sizing get the height from the text here.
  useLayoutEffect(() => {
    const node = own.current;
    if (!node || !fitsText || supportsFieldSizing) return;
    const fit = () => {
      node.style.height = 'auto';
      node.style.height = `${node.scrollHeight}px`;
    };
    fit();
    const observer = new ResizeObserver(fit);
    observer.observe(node);
    return () => {
      observer.disconnect();
      node.style.height = '';
    };
  }, [fitsText, rest.value, rest.defaultValue]);

  return (
    <textarea
      {...rest}
      ref={(node) => {
        own.current = node;
        setRef(ref, node);
      }}
      className={[styles.textarea, readOnly && !disabled ? styles.readOnly : null, 't-body', className].filter(Boolean).join(' ')}
      // Disabled keeps focus (aria-disabled) and blocks typing through readOnly.
      readOnly={readOnly || disabled}
      aria-disabled={disabled || undefined}
      aria-invalid={invalid || undefined}
    />
  );
});
