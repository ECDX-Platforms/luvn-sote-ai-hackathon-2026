import { forwardRef, type HTMLAttributes } from 'react';
import { Link } from '../Link';
import styles from './Breadcrumb.module.css';

export interface BreadcrumbItem {
  /** Name of the parent page. */
  label: string;
  /** Where the parent page is. */
  href: string;
}

export interface BreadcrumbProps extends Omit<HTMLAttributes<HTMLElement>, 'children'> {
  /** Figma: Level 1, Level 2 and Show level 2. The parent pages, top level first. Each one is a small link. */
  items: BreadcrumbItem[];
  /** Figma: Current. The page you are on: last, plain caption text, marked aria-current="page". */
  current: string;
  /** Accessible name of the navigation landmark. */
  label?: string;
}

/** Path above the page title in the header. Styleguide: Header (breadcrumb), Colors → link. */
export const Breadcrumb = forwardRef<HTMLElement, BreadcrumbProps>(function Breadcrumb(
  { items, current, label = 'Breadcrumb', className, ...rest },
  ref,
) {
  return (
    <nav {...rest} ref={ref} aria-label={label} className={[styles.breadcrumb, className].filter(Boolean).join(' ')}>
      <ol className={styles.list}>
        {items.map((item) => (
          <li key={`${item.href}-${item.label}`} className={styles.item}>
            <Link href={item.href} size="sm">{item.label}</Link>
            <span className={`${styles.separator} t-caption`} aria-hidden>›</span>
          </li>
        ))}
        <li className={styles.item}>
          <span className="t-caption" aria-current="page">{current}</span>
        </li>
      </ol>
    </nav>
  );
});
