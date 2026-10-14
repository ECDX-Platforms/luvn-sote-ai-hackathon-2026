import { forwardRef, type HTMLAttributes, type LiHTMLAttributes, type ReactNode } from 'react';
import { CirclePause } from 'lucide-react';
import styles from './ProcessSteps.module.css';

export type ProcessStepState = 'done' | 'current' | 'upcoming' | 'paused';

export interface ProcessStepsProps extends HTMLAttributes<HTMLOListElement> {
  /** The steps: ProcessStep elements, in process order. Leave out steps the process does not have. */
  children: ReactNode;
}

/**
 * Process steps (`<ol>`: the order is the process order). A row of steps from 640px up; below 640px the steps stack
 * vertically. Figma: Data display / Process steps (174:563).
 */
export const ProcessSteps = forwardRef<HTMLOListElement, ProcessStepsProps>(function ProcessSteps(
  { className, children, ...rest },
  ref,
) {
  return (
    <ol {...rest} ref={ref} className={[styles.steps, className].filter(Boolean).join(' ')}>
      {children}
    </ol>
  );
});

export interface ProcessStepProps extends Omit<LiHTMLAttributes<HTMLLIElement>, 'children'> {
  /**
   * Figma: State. Each step has a 2px rule along its top. Done and Current = solid, text colour. Upcoming = solid, line colour.
   * Paused = dashed, text-muted, plus the pause icon before the label; only when the source marks the step temporarily interrupted.
   * Current gets `aria-current="step"`. The rule never shows an outcome: Detail carries it.
   */
  state?: ProcessStepState;
  /** Figma: Label. The step name ("Assessment"). Body-strong. */
  label: ReactNode;
  /**
   * Figma: Date. As shown, following the Content rules (`16 Sep 2026`, `8–15 Sep 2026`). Caption, text-muted. Without a date the step shows "–".
   * Paused: pass the date only; the step adds "Started" in front ("Started 22 Sep 2026"), never "Since".
   */
  date?: ReactNode;
  /** Machine-readable date, e.g. `2026-09-16`. With it, `date` is a `<time>` element. Leave it out for ranges and phrases ("Since 22 Sep 2026"). */
  dateTime?: string;
  /**
   * Figma: Detail. The outcome or status word, from the source only ("Granted", "Refused", "Draft", "In progress", "Not started", "Paused").
   * Caption, text colour. Paused defaults to "Paused": keep it, the word carries the state.
   */
  detail?: ReactNode;
  /** Read out before the label for Done and Upcoming steps (default "Done" / "Upcoming"). The current step is announced through `aria-current="step"`; a paused step through its Detail word. */
  stateText?: string;
}

const defaultStateText: Record<'done' | 'upcoming', string> = {
  done: 'Done',
  upcoming: 'Upcoming',
};

const isEmpty = (node: ReactNode) => node === undefined || node === null || node === '';

/** One step of a process: a 2px top rule, then label, date and detail. Renders an `<li>`: put it in ProcessSteps. Not interactive. */
export const ProcessStep = forwardRef<HTMLLIElement, ProcessStepProps>(function ProcessStep(
  { state = 'done', label, date, dateTime, detail, stateText, className, ...rest },
  ref,
) {
  const paused = state === 'paused';
  // Done and Upcoming get a hidden state word; Current has aria-current, Paused its visible Detail word.
  const hiddenState = state === 'done' || state === 'upcoming' ? stateText ?? defaultStateText[state] : undefined;
  const shownDetail = paused && isEmpty(detail) ? 'Paused' : detail;
  const dateNode = dateTime ? <time dateTime={dateTime}>{date}</time> : date;

  return (
    <li
      {...rest}
      ref={ref}
      className={[styles.step, styles[state], className].filter(Boolean).join(' ')}
      aria-current={state === 'current' ? 'step' : undefined}
    >
      <p className={[styles.label, 't-body-strong'].join(' ')}>
        {paused ? (
          <span className={styles.icon} aria-hidden>
            <CirclePause size={16} strokeWidth={1.5} />
          </span>
        ) : null}
        <span className={styles.labelText}>
          {hiddenState ? <span className="visually-hidden">{`${hiddenState}: `}</span> : null}
          {label}
        </span>
      </p>
      <p className={[styles.date, 't-caption'].join(' ')}>
        {isEmpty(date) ? (
          <>
            <span aria-hidden>–</span>
            <span className="visually-hidden">No date</span>
          </>
        ) : paused ? (
          <>Started {dateNode}</>
        ) : (
          dateNode
        )}
      </p>
      {isEmpty(shownDetail) ? null : <p className={[styles.detail, 't-caption'].join(' ')}>{shownDetail}</p>}
    </li>
  );
});
