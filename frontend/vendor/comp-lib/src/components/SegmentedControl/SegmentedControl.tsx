import { forwardRef, useState, type HTMLAttributes, type ReactNode } from 'react';
import styles from './SegmentedControl.module.css';

export interface SegmentOption {
  /** The value this segment stands for. */
  value: string;
  /** Figma: Segment / Label. Short: one or two words. */
  label: ReactNode;
}

export interface SegmentedControlProps extends Omit<HTMLAttributes<HTMLDivElement>, 'onChange' | 'defaultValue' | 'children' | 'role'> {
  /** Accessible name of the group, e.g. "Time range". Read out before the options. */
  label: string;
  /** 2 to 4 options (Figma). Each is a Segment. */
  options: SegmentOption[];
  /** Figma: Segment State = Selected. The selected value (controlled). Use with `onChange`. */
  value?: string;
  /** The value selected at first (uncontrolled). Defaults to the first option. */
  defaultValue?: string;
  /** Called with the new value when people pick a segment. The view should switch at once. */
  onChange?: (value: string) => void;
}

/** NEW (not in the styleguide). 2–4 options that switch a view, e.g. Day / Week / Month. Buttons with aria-pressed in a group. */
export const SegmentedControl = forwardRef<HTMLDivElement, SegmentedControlProps>(function SegmentedControl(
  { label, options, value, defaultValue, onChange, className, ...rest },
  ref,
) {
  const [own, setOwn] = useState(defaultValue ?? options[0]?.value);
  const selected = value ?? own;
  const pick = (next: string) => {
    if (value === undefined) setOwn(next);
    if (next !== selected) onChange?.(next);
  };

  return (
    <div {...rest} ref={ref} role="group" aria-label={label} className={[styles.control, className].filter(Boolean).join(' ')}>
      {options.map((option) => {
        const isSelected = option.value === selected;
        return (
          <button
            key={option.value}
            type="button"
            className={[styles.segment, 't-control'].join(' ')}
            aria-pressed={isSelected}
            onClick={() => pick(option.value)}
          >
            <span className={styles.label} data-label={typeof option.label === 'string' ? option.label : undefined}>
              {option.label}
            </span>
          </button>
        );
      })}
    </div>
  );
});
