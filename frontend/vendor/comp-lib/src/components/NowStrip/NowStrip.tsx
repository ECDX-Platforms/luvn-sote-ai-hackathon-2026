import { forwardRef, type HTMLAttributes, type ReactNode } from 'react';
import styles from './NowStrip.module.css';

export interface NowStripProps extends HTMLAttributes<HTMLDivElement> {
  /** Four Now cells, in reading order. Figma: Now strip holds 4 Now cell instances. */
  children: ReactNode;
}

/**
 * NEW. Now strip: the first look at the top of a client view, under the identity strip. Four Now cells side by side,
 * equal height, 1px line rules between them and below; 2 per row under 1024px. The strip owns every rule.
 * Styleguide v2.2: Now strip (first look), Layout → Breakpoints.
 */
export const NowStrip = forwardRef<HTMLDivElement, NowStripProps>(function NowStrip(
  { className, children, ...rest },
  ref,
) {
  return (
    <div {...rest} ref={ref} className={[styles.strip, className].filter(Boolean).join(' ')}>
      {children}
    </div>
  );
});
