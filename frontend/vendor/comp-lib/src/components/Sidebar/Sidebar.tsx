import { Children, forwardRef, isValidElement, type HTMLAttributes, type ReactNode } from 'react';
import styles from './Sidebar.module.css';

export interface SidebarProps extends HTMLAttributes<HTMLDivElement> {
  /** Figma: Product name. Shown in the logo row in title-sm. */
  productName: ReactNode;
  /** Name of the `nav` landmark, read by screen readers ("Main navigation"). Give each nav on a page its own name. */
  navLabel?: string;
  /** The `NavItem` elements, in order. Each one is put in a list item. Mark exactly one as `selected` (the page you are on). */
  children: ReactNode;
}

/** Side navigation column of the app shell: logo row plus a labelled nav landmark. Styleguide: App shell, Side navigation. */
export const Sidebar = forwardRef<HTMLDivElement, SidebarProps>(function Sidebar(
  { productName, navLabel = 'Main', className, children, ...rest },
  ref,
) {
  const items = Children.toArray(children).filter(isValidElement);

  return (
    <div {...rest} ref={ref} className={[styles.sidebar, className].filter(Boolean).join(' ')}>
      <div className={[styles.logo, 't-title-sm'].join(' ')}>
        <span className={styles.name}>{productName}</span>
      </div>
      <nav className={styles.nav} aria-label={navLabel}>
        <ul className={styles.list}>
          {items.map((item) => (
            <li key={item.key} className={styles.item}>{item}</li>
          ))}
        </ul>
      </nav>
    </div>
  );
});
