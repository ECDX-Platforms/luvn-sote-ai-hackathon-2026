import { forwardRef, type HTMLAttributes, type ReactNode } from 'react';
import { CircleAlert, CircleCheck, Info, TriangleAlert, type LucideIcon } from 'lucide-react';
import styles from './StatusTag.module.css';

export type StatusTagStatus = 'critical' | 'warning' | 'success' | 'info';

/** One fixed icon per status (Figma: Icon/alert-circle, alert-triangle, check-circle, info). */
const icons: Record<StatusTagStatus, LucideIcon> = {
  critical: CircleAlert,
  warning: TriangleAlert,
  success: CircleCheck,
  info: Info,
};

export interface StatusTagProps extends HTMLAttributes<HTMLSpanElement> {
  /** Figma: Status. Sets the colours and the icon. Required, so a status colour never appears by default with a wrong word. */
  status: StatusTagStatus;
  /** Figma: Label. The real status word in sentence case ("Expiring soon"). Long labels wrap. */
  children: ReactNode;
}

/** Status tag for status and risk labels on an object: status colours plus a 12px icon. Styleguide: Status flag and tag. */
export const StatusTag = forwardRef<HTMLSpanElement, StatusTagProps>(function StatusTag(
  { status, className, children, ...rest },
  ref,
) {
  const Icon = icons[status];
  return (
    <span {...rest} ref={ref} className={[styles.statusTag, styles[status], 't-tag', className].filter(Boolean).join(' ')}>
      <span className={styles.icon} aria-hidden>
        <Icon size={12} strokeWidth={2} />
      </span>
      <span className={styles.label}>{children}</span>
    </span>
  );
});
