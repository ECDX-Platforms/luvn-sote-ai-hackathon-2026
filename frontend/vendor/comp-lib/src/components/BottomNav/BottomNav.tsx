import { Children, cloneElement, forwardRef, isValidElement, type HTMLAttributes, type ReactElement, type ReactNode } from 'react';
import type { NavItemProps } from '../NavItem';
import styles from './BottomNav.module.css';

export interface BottomNavProps extends HTMLAttributes<HTMLElement> {
  /** Name of the `nav` landmark ("Main navigation"). Use the same name as the Sidebar: only one of them shows at a time. */
  label?: string;
  /** 4 `NavItem` elements (5 at most), in the same order as the Sidebar. They are shown in the bottom-bar layout. Mark one as `selected`. */
  children: ReactNode;
}

/**
 * Phone navigation bar: the phone form of the Sidebar. The AppShell shows it under 640px only, fixed to the bottom of the screen.
 * Styleguide: Side navigation (phone), Breakpoints. Not yet designed in Figma (the Layouts page has a placeholder frame).
 */
export const BottomNav = forwardRef<HTMLElement, BottomNavProps>(function BottomNav(
  { label = 'Main', className, children, ...rest },
  ref,
) {
  const items = Children.toArray(children).filter(isValidElement) as ReactElement<NavItemProps>[];

  return (
    <nav {...rest} ref={ref} aria-label={label} className={[styles.bottomNav, className].filter(Boolean).join(' ')}>
      <ul className={styles.list}>
        {items.map((item) => (
          <li key={item.key} className={styles.item}>
            {cloneElement(item, { layout: 'bottom-bar' })}
          </li>
        ))}
      </ul>
    </nav>
  );
});
