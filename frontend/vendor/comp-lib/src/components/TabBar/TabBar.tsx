import { forwardRef, type HTMLAttributes, type ReactNode } from 'react';
import styles from './TabBar.module.css';

export interface TabBarProps extends HTMLAttributes<HTMLDivElement> {
  /** The tabs: one `TabList` with its `Tab` elements. */
  children: ReactNode;
  /** Figma: Show action. At most one primary `Button`, at the right, vertically centred. */
  action?: ReactNode;
}

/** Page-level bar for the tabs, under the header. Styleguide: Tabs, Screen anatomy. */
export const TabBar = forwardRef<HTMLDivElement, TabBarProps>(function TabBar(
  { action, className, children, ...rest },
  ref,
) {
  return (
    <div {...rest} ref={ref} className={[styles.tabBar, className].filter(Boolean).join(' ')}>
      <div className={styles.tabs}>{children}</div>
      {action ? <div className={styles.action}>{action}</div> : null}
    </div>
  );
});
