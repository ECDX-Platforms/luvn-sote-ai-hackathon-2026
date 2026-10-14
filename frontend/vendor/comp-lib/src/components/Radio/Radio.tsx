import { createContext, forwardRef, useContext, useId, type ChangeEvent, type FieldsetHTMLAttributes, type InputHTMLAttributes, type ReactNode } from 'react';
import styles from './Radio.module.css';

interface RadioGroupContextValue {
  name: string;
  value?: string;
  defaultValue?: string;
  disabled: boolean;
  onChange?: (value: string) => void;
}
const RadioGroupContext = createContext<RadioGroupContextValue | null>(null);

export interface RadioProps extends Omit<InputHTMLAttributes<HTMLInputElement>, 'type' | 'value' | 'children'> {
  /** The value this option stands for. Inside RadioGroup it decides which option is selected. */
  value: string;
  /**
   * Figma: State = Disabled. Muted circle and label.
   * Exception to the aria-disabled rule: native `disabled`, because arrow keys would still select an
   * aria-disabled radio. Arrow keys and Tab skip it. Say why in the group hint.
   */
  disabled?: boolean;
  /** Figma: Label. Part of the click target. Wraps when long; the circle stays on the first line. */
  children: ReactNode;
}

/**
 * One option of a radio group. Figma: Selected = the native `checked` state (set by RadioGroup's `value`).
 * Styleguide: Checkbox and task item (Radio). `className` and `style` go on the label row; every other prop goes on the `<input>`.
 */
export const Radio = forwardRef<HTMLInputElement, RadioProps>(function Radio(
  { value, disabled = false, className, style, onChange, children, ...rest },
  ref,
) {
  const group = useContext(RadioGroupContext);
  const isDisabled = disabled || Boolean(group?.disabled);
  const handleChange = (event: ChangeEvent<HTMLInputElement>) => {
    onChange?.(event);
    if (event.target.checked) group?.onChange?.(value);
  };
  const groupState = group
    ? group.value !== undefined
      ? { checked: group.value === value }
      : { defaultChecked: group.defaultValue === value }
    : {};

  return (
    <label className={[styles.radio, isDisabled ? styles.disabled : null, 't-body', className].filter(Boolean).join(' ')} style={style}>
      <span className={styles.slot}>
        <span className={styles.circle}>
          <input
            {...rest}
            {...groupState}
            ref={ref}
            type="radio"
            name={group?.name ?? rest.name}
            value={value}
            className={styles.input}
            disabled={isDisabled}
            onChange={handleChange}
          />
          <span className={styles.dot} aria-hidden />
        </span>
      </span>
      <span className={styles.label}>{children}</span>
    </label>
  );
});

export interface RadioGroupProps extends Omit<FieldsetHTMLAttributes<HTMLFieldSetElement>, 'onChange' | 'defaultValue' | 'children'> {
  /** The question the options answer, in body-strong above them (a `<legend>`). */
  legend: string;
  /** Caption under the legend, text-muted. Linked with aria-describedby. Say here why an option is disabled. */
  hint?: string;
  /** Shared name of the radios. Made for you when left out. */
  name?: string;
  /** The selected value (controlled). Use with `onChange`. */
  value?: string;
  /** The value selected at first (uncontrolled). */
  defaultValue?: string;
  /** Called with the new value when people select an option. */
  onChange?: (value: string) => void;
  /** Disables every option (native `disabled` on the fieldset). */
  disabled?: boolean;
  /** Radio options. */
  children: ReactNode;
}

/**
 * A set of Radio options with one legend. Native radios with one name: Tab enters the group at the selected
 * option, arrow keys move and select, and disabled options are skipped. No extra ARIA is needed.
 */
export const RadioGroup = forwardRef<HTMLFieldSetElement, RadioGroupProps>(function RadioGroup(
  { legend, hint, name, value, defaultValue, onChange, disabled = false, className, children, ...rest },
  ref,
) {
  const autoId = useId();
  const hintId = hint ? `${autoId}-hint` : undefined;
  const describedBy = [rest['aria-describedby'], hintId].filter(Boolean).join(' ') || undefined;

  return (
    <fieldset {...rest} ref={ref} className={[styles.group, className].filter(Boolean).join(' ')} disabled={disabled} aria-describedby={describedBy}>
      <legend className={[styles.legend, 't-body-strong'].join(' ')}>{legend}</legend>
      {hint ? <p id={hintId} className={[styles.hint, 't-caption'].join(' ')}>{hint}</p> : null}
      <RadioGroupContext.Provider value={{ name: name ?? autoId, value, defaultValue, disabled, onChange }}>
        <div className={styles.options}>{children}</div>
      </RadioGroupContext.Provider>
    </fieldset>
  );
});
