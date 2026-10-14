import { forwardRef, useLayoutEffect, useRef, type HTMLAttributes, type ReactNode } from 'react';
import styles from './AppShell.module.css';

export interface AppShellProps extends HTMLAttributes<HTMLDivElement> {
  /** A `Sidebar`. Shown from 640px up (from 640 to 1023px too: the tablet menu is not yet in Figma). */
  sidebar: ReactNode;
  /** A `Header`. It sits above `main`, so it is the page banner and holds the page's one h1. */
  header: ReactNode;
  /** An `IdentityStrip`, placed first in `main` so it sticks to the top of the content column. */
  identityStrip?: ReactNode;
  /**
   * A `BottomNav` with the same items as the Sidebar. Shown under 640px only, fixed to the bottom of the screen.
   * Required: the sidebar is hidden on phones, so this is the only navigation there.
   */
  bottomNav: ReactNode;
  /** The page content inside `main`: e.g. a `TabBar`, then a layout (`MainSide`, `OneColumn` or `BlockGrid`) holding Blocks. */
  children: ReactNode;
}

/**
 * Page frame: sidebar plus a content column with the header and `main`. Phones: one column and a bottom navigation bar.
 * Figma: Layouts (125:131), Breakpoint Desktop / Laptop / Phone. Styleguide: App shell, Layout (Breakpoints, Screen anatomy), Accessibility.
 */
export const AppShell = forwardRef<HTMLDivElement, AppShellProps>(function AppShell(
  { sidebar, header, identityStrip, bottomNav, className, children, ...rest },
  ref,
) {
  const hasBottomNav = bottomNav != null && bottomNav !== false;
  // Plain JavaScript callers are not stopped by the type. Then the sidebar stays visible on phones (see the CSS).
  if (!hasBottomNav) console.error('AppShell: bottomNav is required. Without it phones have no bottom navigation; the sidebar is shown instead.');

  // The page end and scrolled-to focus keep the bar's real height free (labels that wrap, larger text, the
  // home-indicator inset), not a fixed 72px. --bottom-nav-h goes on <html>, where scroll-padding is set.
  const barRef = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    const bar = barRef.current;
    if (!bar) return undefined;
    const root = bar.ownerDocument.documentElement;
    const measure = () => root.style.setProperty('--bottom-nav-h', `${bar.getBoundingClientRect().height}px`);
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(bar);
    return () => {
      observer.disconnect();
      root.style.removeProperty('--bottom-nav-h');
    };
  }, [hasBottomNav]);

  return (
    <div {...rest} ref={ref} className={[styles.appShell, hasBottomNav ? styles.withBottomNav : null, className].filter(Boolean).join(' ')}>
      <div className={styles.sidebar}>{sidebar}</div>
      <div className={styles.content}>
        {header}
        <main className={styles.main}>
          {identityStrip}
          {children}
        </main>
      </div>
      {hasBottomNav ? <div ref={barRef} className={styles.bottomNav}>{bottomNav}</div> : null}
    </div>
  );
});
