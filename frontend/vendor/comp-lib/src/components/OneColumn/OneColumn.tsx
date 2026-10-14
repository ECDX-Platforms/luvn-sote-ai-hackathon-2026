import { forwardRef, type HTMLAttributes, type ReactNode } from 'react';
import styles from './OneColumn.module.css';

export interface OneColumnProps extends HTMLAttributes<HTMLDivElement> {
  /** Figma: the column's Blocks, stacked full width. */
  children: ReactNode;
}

/** Content layout: one full-width column of Blocks, for forms, reading and long lists. Styleguide: Layout. */
export const OneColumn = forwardRef<HTMLDivElement, OneColumnProps>(function OneColumn(
  { className, children, ...rest },
  ref,
) {
  return (
    <div {...rest} ref={ref} className={[styles.oneColumn, className].filter(Boolean).join(' ')}>
      {children}
    </div>
  );
});
