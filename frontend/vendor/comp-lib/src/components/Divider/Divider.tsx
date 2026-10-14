import { forwardRef, type HTMLAttributes } from 'react';
import styles from './Divider.module.css';

export type DividerOrientation = 'horizontal' | 'vertical';

export interface DividerProps extends Omit<HTMLAttributes<HTMLDivElement>, 'children'> {
  /** Figma: Orientation. Horizontal fills the width of its container. Vertical stretches to the height of its flex or grid row. */
  orientation?: DividerOrientation;
  /** Decorative (default): hidden from screen readers, like every hairline. Set false when the line separates content a screen reader user should know about: it becomes role="separator". */
  decorative?: boolean;
}

/** Hairline that separates regions: blocks, rows, columns, header. Styleguide: Shapes → Hairline. */
export const Divider = forwardRef<HTMLDivElement, DividerProps>(function Divider(
  { orientation = 'horizontal', decorative = true, className, ...rest },
  ref,
) {
  return (
    <div
      {...rest}
      ref={ref}
      className={[styles.divider, styles[orientation], className].filter(Boolean).join(' ')}
      role={decorative ? undefined : 'separator'}
      aria-orientation={decorative ? undefined : orientation}
      aria-hidden={decorative || undefined}
    />
  );
});
