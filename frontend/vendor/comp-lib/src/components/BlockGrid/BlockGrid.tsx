import { forwardRef, type HTMLAttributes, type ReactNode } from 'react';
import styles from './BlockGrid.module.css';

export interface BlockGridProps extends HTMLAttributes<HTMLDivElement> {
  /** Figma: the grid's Blocks, in reading order (left to right, then down). */
  children: ReactNode;
}

/** Content layout: Blocks in a grid divided by hairlines; 3 across from 1280px, 2 from 640px, 1 on phones. Styleguide: Layout (Breakpoints). */
export const BlockGrid = forwardRef<HTMLDivElement, BlockGridProps>(function BlockGrid(
  { className, children, ...rest },
  ref,
) {
  return (
    <div {...rest} ref={ref} className={[styles.blockGrid, className].filter(Boolean).join(' ')}>
      {children}
    </div>
  );
});
