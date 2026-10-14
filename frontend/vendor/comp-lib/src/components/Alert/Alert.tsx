import { forwardRef, type HTMLAttributes, type ReactNode } from 'react';
import { CircleAlert, CircleCheck, Info, TriangleAlert, X, type LucideIcon } from 'lucide-react';
import { IconButton } from '../IconButton';
import styles from './Alert.module.css';

export type AlertLevel = 'critical' | 'warning' | 'info' | 'done';

/** Icon and keyword are fixed per level (Figma: Icon/alert-circle, alert-triangle, info, check-circle). */
const levels: Record<AlertLevel, { icon: LucideIcon; keyword: string }> = {
  critical: { icon: CircleAlert, keyword: 'Critical.' },
  warning: { icon: TriangleAlert, keyword: 'Warning.' },
  info: { icon: Info, keyword: 'Info.' },
  done: { icon: CircleCheck, keyword: 'Done.' },
};

interface AlertBaseProps extends Omit<HTMLAttributes<HTMLDivElement>, 'role' | 'children'> {
  /** Figma: Message. One or two plain sentences: what happened and what to do next. Shown after the keyword, in the same paragraph, so inline content only (text, links, strong). */
  children: ReactNode;
  /**
   * Replaces the fixed English keyword shown before the message ("Critical.", "Warning.", "Info.", "Done."),
   * e.g. for a Finnish product. Keep it one word plus a full stop. Icon, colours and role stay set by `level`.
   */
  keyword?: string;
  /** Accessible name of the dismiss button. Default "Dismiss". */
  dismissLabel?: string;
  /**
   * Default true: the alert is a live region (`role="status"`, critical `role="alert"`). False shows it without a role,
   * when a live region that was already on the page announces the message instead (as BlockState does), so it is not read twice.
   */
  live?: boolean;
}

interface CriticalAlertProps extends AlertBaseProps {
  /** Figma: Level. Critical may interrupt (`role="alert"`). */
  level: 'critical';
  /** Not allowed: a critical alert cannot be dismissed without an action or a recorded reason. Resolve it from the page. */
  onDismiss?: never;
}

interface DismissibleAlertProps extends AlertBaseProps {
  /** Figma: Level. Warning and Info are alert levels (`role="status"`); Done is a confirmation, not an alert level. */
  level: 'warning' | 'info' | 'done';
  /** Figma: Show dismiss. Shows the dismiss button and is called when it is pressed. Remove the alert for good and record the dismissal. */
  onDismiss?: () => void;
}

export type AlertProps = CriticalAlertProps | DismissibleAlertProps;

/** Alert with a fixed icon and keyword per level. Styleguide: Alert, Overview rules 6 and 12, Accessibility 4.1.3. */
export const Alert = forwardRef<HTMLDivElement, AlertProps>(function Alert(
  { level, keyword, onDismiss, dismissLabel = 'Dismiss', live = true, className, children, ...rest },
  ref,
) {
  const { icon: Icon, keyword: defaultKeyword } = levels[level];
  // Critical never gets a dismiss button, whatever is passed in.
  const dismissible = level !== 'critical' && typeof onDismiss === 'function';

  return (
    <div
      {...rest}
      ref={ref}
      className={[styles.alert, styles[level], dismissible && styles.dismissible, className].filter(Boolean).join(' ')}
      role={live ? (level === 'critical' ? 'alert' : 'status') : undefined}
    >
      <span className={styles.icon} aria-hidden>
        <Icon size={16} strokeWidth={1.5} />
      </span>
      <p className={[styles.text, 't-body'].join(' ')}>
        <strong className="t-body-strong">{keyword ?? defaultKeyword}</strong> {children}
      </p>
      {dismissible ? (
        <IconButton
          className={styles.dismiss}
          size="sm"
          icon={<X size={16} strokeWidth={1.5} />}
          label={dismissLabel}
          onClick={onDismiss}
        />
      ) : null}
    </div>
  );
});
