import { Fragment, forwardRef, useCallback, useEffect, useRef, useState, type HTMLAttributes, type ReactNode, type Ref, type RefObject } from 'react';
import styles from './IdentityStrip.module.css';

export interface IdentityStripProps extends Omit<HTMLAttributes<HTMLDivElement>, 'children'> {
  /** Figma: Name. The subject's name, in body-strong. */
  name: ReactNode;
  /** Figma: Details. The key facts after the name (e.g. a date and a reference), in text-muted, separated by " · ". */
  details?: ReactNode[];
  /**
   * The identity block this strip stands in for. The strip stays hidden while that block is on screen or still below it,
   * and appears once it has scrolled above the top of the screen. Leave it out to show the strip all the time.
   * While the block is not on the page, the strip shows; it starts watching once the block appears.
   */
  watch?: RefObject<Element | null>;
  /** Accessible name of the strip (role="group"), e.g. "Current item". */
  label?: string;
}

function setRef<T>(ref: Ref<T> | undefined, value: T | null) {
  if (typeof ref === 'function') ref(value);
  else if (ref) (ref as { current: T | null }).current = value;
}

/**
 * Strip that keeps the subject's name and key facts visible once the identity block has scrolled away.
 * Styleguide: Patient block and identity strip (neutral wording here), Accessibility 2.4.11.
 */
export const IdentityStrip = forwardRef<HTMLDivElement, IdentityStripProps>(function IdentityStrip(
  { name, details = [], watch, label, className, ...rest },
  ref,
) {
  const stripRef = useRef<HTMLDivElement | null>(null);
  // Hidden until the observer has reported, so it never covers the page title on load.
  const [shown, setShown] = useState(!watch);

  useEffect(() => {
    if (!watch) {
      setShown(true);
      return;
    }
    // No IntersectionObserver (very old browsers): show the strip all the time, so the name is never missing.
    if (typeof IntersectionObserver === 'undefined') {
      setShown(true);
      return;
    }

    let observer: IntersectionObserver | undefined;
    let waiting: MutationObserver | undefined;
    // The top edge of the screen is moved down by the strip's height, so the strip appears as soon as the block
    // goes under it: the name is never covered and never missing.
    const observe = (target: Element) => {
      observer?.disconnect();
      const strip = stripRef.current;
      const height = strip ? parseFloat(getComputedStyle(strip).minHeight) || 0 : 0;
      observer = new IntersectionObserver(
        ([entry]) => setShown(!entry.isIntersecting && entry.boundingClientRect.top < height),
        { rootMargin: `-${height}px 0px 0px 0px` },
      );
      observer.observe(target);
    };

    // The strip height (--identity-h) is 44, and 64 on phones: watch again when the phone breakpoint changes.
    const phone = window.matchMedia('(max-width: 639px)');
    const onBreakpoint = () => {
      if (watch.current) observe(watch.current);
    };
    phone.addEventListener('change', onBreakpoint);

    if (watch.current) {
      observe(watch.current);
    } else {
      // The identity block is not on the page (yet): show the strip, so the name and details stay visible,
      // and start watching as soon as the block appears.
      setShown(true);
      waiting = new MutationObserver(() => {
        if (!watch.current) return;
        waiting?.disconnect();
        waiting = undefined;
        observe(watch.current);
      });
      waiting.observe(document.body, { childList: true, subtree: true });
    }

    return () => {
      phone.removeEventListener('change', onBreakpoint);
      waiting?.disconnect();
      observer?.disconnect();
    };
  }, [watch]);

  // One stable ref callback, so React does not detach and re-attach the ref on every render.
  const setRefs = useCallback(
    (node: HTMLDivElement | null) => {
      stripRef.current = node;
      setRef(ref, node);
    },
    [ref],
  );

  return (
    // The holder takes no space, so the strip overlays the content and nothing jumps.
    <div className={styles.holder}>
      <div
        {...rest}
        ref={setRefs}
        role={label ? 'group' : undefined}
        aria-label={label}
        hidden={!shown}
        className={[styles.strip, 't-body', className].filter(Boolean).join(' ')}
      >
        <span className={[styles.name, 't-body-strong'].join(' ')}>{name}</span>
        {details.map((detail, index) => (
          <Fragment key={index}>
            <span className={styles.separator} aria-hidden>·</span>
            <span className={styles.detail}>{detail}</span>
          </Fragment>
        ))}
      </div>
    </div>
  );
});
