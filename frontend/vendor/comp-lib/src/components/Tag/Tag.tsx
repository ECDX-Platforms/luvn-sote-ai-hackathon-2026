import { forwardRef, type HTMLAttributes, type ReactNode } from 'react';
import styles from './Tag.module.css';

export interface TagProps extends HTMLAttributes<HTMLSpanElement> {
  /** Figma: Label. A short noun in sentence case ("Draft"). Long labels wrap. */
  children: ReactNode;
}

/** Neutral tag: a non-clickable label on an object. For statuses and risks use Status tag once it is in the library. Styleguide: Status flag and tag. */
export const Tag = forwardRef<HTMLSpanElement, TagProps>(function Tag({ className, children, ...rest }, ref) {
  return (
    <span {...rest} ref={ref} className={[styles.tag, 't-tag', className].filter(Boolean).join(' ')}>
      <span className={styles.label}>{children}</span>
    </span>
  );
});
