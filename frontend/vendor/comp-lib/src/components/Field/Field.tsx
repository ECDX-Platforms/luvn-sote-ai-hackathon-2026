import { cloneElement, forwardRef, isValidElement, useId, type HTMLAttributes, type ReactElement } from 'react';
import { CircleAlert } from 'lucide-react';
import styles from './Field.module.css';

/** Props Field sets on its control. Input, Textarea and Select accept all of them. */
interface FieldControlProps {
  id?: string;
  invalid?: boolean;
  'aria-describedby'?: string;
}

export interface FieldProps extends Omit<HTMLAttributes<HTMLDivElement>, 'children'> {
  /** Figma: Label. Above the control in body-strong. Sentence case, no colon. */
  label: string;
  /** Figma: Hint + Show hint. Under the label in caption, text-muted. Leave it out to hide it. */
  hint?: string;
  /** Figma: Error + State = Error. Says what is wrong and how to fix it. Sets the control's error border and aria-invalid. */
  error?: string;
  /** One control: Input, Textarea or Select. Field gives it an id and aria-describedby (hint, error), and `invalid` while there is an error. */
  children: ReactElement<FieldControlProps>;
}

/** Form field: label, hint, control and error message, wired together. Styleguide: Text input, textarea, select; Accessibility 3.3.1 / 3.3.3. */
export const Field = forwardRef<HTMLDivElement, FieldProps>(function Field({ label, hint, error, className, children, ...rest }, ref) {
  const autoId = useId();
  const control = isValidElement(children) ? children : null;
  const controlId = control?.props.id ?? `${autoId}-control`;
  const hintId = hint ? `${autoId}-hint` : undefined;
  const errorId = error ? `${autoId}-error` : undefined;
  const describedBy = [control?.props['aria-describedby'], hintId, errorId].filter(Boolean).join(' ') || undefined;

  return (
    <div {...rest} ref={ref} className={[styles.field, className].filter(Boolean).join(' ')}>
      <label htmlFor={controlId} className={[styles.label, 't-body-strong'].join(' ')}>{label}</label>
      {hint ? <p id={hintId} className="t-caption">{hint}</p> : null}
      <div className={styles.control}>
        {control
          ? cloneElement(control, { id: controlId, 'aria-describedby': describedBy, ...(error ? { invalid: true } : {}) })
          : children}
      </div>
      {error ? (
        <p id={errorId} className={[styles.error, 't-body'].join(' ')}>
          <span className={styles.errorIcon} aria-hidden>
            <CircleAlert size={16} strokeWidth={1.5} />
          </span>
          <span>{error}</span>
        </p>
      ) : null}
    </div>
  );
});
