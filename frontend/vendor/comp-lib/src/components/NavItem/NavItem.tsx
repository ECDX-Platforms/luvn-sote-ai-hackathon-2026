import { forwardRef, type AnchorHTMLAttributes, type ReactNode } from 'react';
import styles from './NavItem.module.css';

export type NavItemLayout = 'sidebar' | 'bottom-bar';

export interface NavItemProps extends AnchorHTMLAttributes<HTMLAnchorElement> {
  /** Where the item goes. */
  href: string;
  /** Figma: Layout. sidebar = icon left of the label, 36 high (44 in comfortable density). bottom-bar = phones: icon above the label, at least 56 high. */
  layout?: NavItemLayout;
  /** Figma: State = Current. The page you are on: text colour, weight 600, 2px bar (left edge in the sidebar, top edge in the bottom bar). Sets aria-current="page". */
  selected?: boolean;
  /** Figma: Icon. A 16px icon, e.g. a lucide icon at size 16. Always given: a nav item is icon plus label, and the bottom bar puts the icon above the label. */
  icon: ReactNode;
  /** Figma: Label. Always shown: icons never stand alone in navigation. */
  children: ReactNode;
}

/** One item in the side navigation or the phone bottom bar. Styleguide: Side navigation, Overview rule 9. */
export const NavItem = forwardRef<HTMLAnchorElement, NavItemProps>(function NavItem(
  { href, layout = 'sidebar', selected = false, icon, className, children, ...rest },
  ref,
) {
  return (
    <a
      {...rest}
      ref={ref}
      href={href}
      aria-current={selected ? 'page' : undefined}
      className={[styles.navItem, layout === 'bottom-bar' ? styles.bottomBar : styles.sidebar, layout === 'bottom-bar' ? 't-tag' : 't-control', className].filter(Boolean).join(' ')}
    >
      <span className={styles.icon} aria-hidden>{icon}</span>
      <span className={styles.label}>{children}</span>
    </a>
  );
});
