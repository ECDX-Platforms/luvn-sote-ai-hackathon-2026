import { forwardRef, type HTMLAttributes, type ReactNode } from 'react';
import { CircleAlert, CircleCheck, Info, TriangleAlert, type LucideIcon } from 'lucide-react';
import styles from './Flag.module.css';

export type FlagStatus = 'critical' | 'warning' | 'success' | 'info';

/** One fixed icon per status (Figma: Icon/alert-circle, alert-triangle, check-circle, info). */
const icons: Record<FlagStatus, LucideIcon> = {
  critical: CircleAlert,
  warning: TriangleAlert,
  success: CircleCheck,
  info: Info,
};

export interface FlagProps extends HTMLAttributes<HTMLSpanElement> {
  /** Figma: Status. Sets the colour and the icon. Required, so a status colour never appears by default with a wrong word. */
  status: FlagStatus;
  /** Figma: Label. The real status word. Expert users: short terms ("High", "Low", "Normal"). Other users: plain words ("A little high"). */
  children: ReactNode;
}

/** Inline status in text and tables: 16px icon + word in the status colour, no background. Styleguide: Status flag and tag. */
export const Flag = forwardRef<HTMLSpanElement, FlagProps>(function Flag({ status, className, children, ...rest }, ref) {
  const Icon = icons[status];
  return (
    <span {...rest} ref={ref} className={[styles.flag, styles[status], 't-control', className].filter(Boolean).join(' ')}>
      <span className={styles.icon} aria-hidden>
        <Icon size={16} strokeWidth={1.5} />
      </span>
      <span className={styles.label}>{children}</span>
    </span>
  );
});
