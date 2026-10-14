import { forwardRef, type HTMLAttributes, type ReactNode } from 'react';
import styles from './MainSide.module.css';

export interface MainSideProps extends Omit<HTMLAttributes<HTMLDivElement>, 'children'> {
  /** Figma: Main slot. The Blocks the reader works through (2/3 of the width from 1280px). */
  main: ReactNode;
  /** Figma: Side slot. The Blocks that need action (1/3). An `aside` landmark. */
  side: ReactNode;
  /** Accessible name of the side column's `aside` landmark. Needed when the page has more than one `aside`. */
  sideLabel?: string;
  /** The `+` grid crosses where the column rule starts and ends (styleguide: optional, desktop only). Default on, as in Figma. */
  gridCross?: boolean;
}

/** Content layout: main column and side column split by a hairline. Styleguide: Layout (Main grid, Breakpoints), Shape inventory (Grid cross). */
export const MainSide = forwardRef<HTMLDivElement, MainSideProps>(function MainSide(
  { main, side, sideLabel, gridCross = true, className, ...rest },
  ref,
) {
  return (
    <div {...rest} ref={ref} className={[styles.mainSide, gridCross ? styles.cross : null, className].filter(Boolean).join(' ')}>
      <div className={styles.main}>{main}</div>
      <aside className={styles.side} aria-label={sideLabel}>{side}</aside>
    </div>
  );
});
