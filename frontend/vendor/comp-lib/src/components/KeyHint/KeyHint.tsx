import { forwardRef, type HTMLAttributes, type ReactNode } from 'react';
import styles from './KeyHint.module.css';

export interface KeyHintProps extends HTMLAttributes<HTMLElement> {
  /** Figma: Keys. The shortcut as people press it, e.g. "⌘K" or "Ctrl K". */
  children: ReactNode;
}

/** Keyboard hint, e.g. ⌘K in the search field. Styleguide: Header (search spec), Shapes → Radius (sm). Not interactive. */
export const KeyHint = forwardRef<HTMLElement, KeyHintProps>(function KeyHint({ className, children, ...rest }, ref) {
  return (
    <kbd {...rest} ref={ref} className={[styles.keyHint, 't-caption', className].filter(Boolean).join(' ')}>
      {children}
    </kbd>
  );
});
