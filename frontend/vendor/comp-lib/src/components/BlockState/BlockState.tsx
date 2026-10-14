import { forwardRef, useCallback, useEffect, useLayoutEffect, useRef, useState, type ReactNode, type Ref } from 'react';
import { RefreshCw } from 'lucide-react';
import { Alert } from '../Alert';
import { Block, type BlockProps } from '../Block';
import { Button } from '../Button';
import { Skeleton } from '../Skeleton';
import styles from './BlockState.module.css';

export type BlockStateKind = 'empty' | 'loading' | 'error';
export type BlockStateErrorLevel = 'warning' | 'critical';

interface BlockStateBaseProps extends Omit<BlockProps, 'children' | 'flush'> {
  /** Figma: Title. The block keeps its title in every state. */
  title: ReactNode;
  /**
   * Empty: the one sentence that says why the block is empty (body, text-muted). Required.
   * Error: the alert message, saying what happened and what to do next. Required. Not used in Loading.
   */
  message?: ReactNode;
  /** Figma: Show action. Empty only: at most one action, e.g. a secondary Button. */
  action?: ReactNode;
  /** Loading only. Wait this long (ms) before the skeleton shows; until then the block shows only its title. Default 300 (styleguide). */
  loadingDelay?: number;
  /** Loading only. Read out by screen readers instead of the skeleton bars. Default "Loading…". */
  loadingLabel?: string;
  /** Error only. Alert level: warning (default) or critical. Only critical interrupts (`role="alert"`). */
  errorLevel?: BlockStateErrorLevel;
  /** Error only. Called by the "Try again" secondary button. Required. */
  onRetry?: () => void;
  /** Error only. Label of the retry button. Default "Try again". */
  retryLabel?: string;
  /** Error only. The retry is running: the button keeps its label and shows a spinner (aria-busy), and the loaded data stays. */
  retrying?: boolean;
  /** Error only. Data that was already loaded. It stays under the alert and the retry button. */
  children?: ReactNode;
}

/** Figma: Kind = Empty. */
export interface BlockStateEmptyProps extends BlockStateBaseProps {
  /** Figma: Kind. */
  kind: 'empty';
  message: ReactNode;
}
/** Figma: Kind = Loading. */
export interface BlockStateLoadingProps extends BlockStateBaseProps {
  /** Figma: Kind. */
  kind: 'loading';
}
/** Figma: Kind = Error. */
export interface BlockStateErrorProps extends BlockStateBaseProps {
  /** Figma: Kind. */
  kind: 'error';
  message: ReactNode;
  onRetry: () => void;
}

/** Empty needs a message; Error needs a message and onRetry. One props object can be passed with any `kind`. */
export type BlockStateProps = BlockStateEmptyProps | BlockStateLoadingProps | BlockStateErrorProps;

function setRef<T>(ref: Ref<T> | undefined, value: T | null) {
  if (typeof ref === 'function') ref(value);
  else if (ref) (ref as { current: T | null }).current = value;
}

/**
 * A block in its empty, loading or error state. Renders a Block, so title, padding and hairline stay the same.
 * Styleguide: Empty, loading and error states; Block (section).
 */
export const BlockState = forwardRef<HTMLElement, BlockStateProps>(function BlockState(
  {
    kind,
    message,
    action,
    loadingDelay = 300,
    loadingLabel,
    errorLevel = 'warning',
    onRetry,
    retryLabel = 'Try again',
    retrying = false,
    children,
    onFocus,
    onBlur,
    ...rest
  },
  ref,
) {
  const sectionRef = useRef<HTMLElement | null>(null);
  const handleRef = useCallback(
    (node: HTMLElement | null) => {
      sectionRef.current = node;
      setRef(ref, node);
    },
    [ref],
  );

  // Loading: show nothing under the delay, then the skeleton. The timer restarts each time the block enters Loading.
  const [delayPassed, setDelayPassed] = useState(false);
  const [previousKind, setPreviousKind] = useState(kind);
  if (kind !== previousKind) {
    setPreviousKind(kind);
    setDelayPassed(false);
  }
  useEffect(() => {
    if (kind !== 'loading' || loadingDelay <= 0) return undefined;
    const timer = window.setTimeout(() => setDelayPassed(true), loadingDelay);
    return () => window.clearTimeout(timer);
  }, [kind, loadingDelay]);
  const showSkeleton = kind === 'loading' && (loadingDelay <= 0 || delayPassed);

  // Focus: when the state changes, the focused action or retry button can disappear. If focus was inside
  // the block and is now lost (on the page body), move it to the block title so keyboard and screen reader
  // users stay in place (WCAG 2.4.3).
  const focusInside = useRef(false);
  const firstRender = useRef(true);
  useLayoutEffect(() => {
    if (firstRender.current) {
      firstRender.current = false;
      return;
    }
    const section = sectionRef.current;
    const active = section?.ownerDocument.activeElement;
    if (!section || !focusInside.current || (active && active !== section.ownerDocument.body)) return;
    const heading = section.querySelector<HTMLElement>('h2, h3, h4');
    if (!heading) return;
    // Focusable only for this move: the title leaves the Tab order again as soon as focus leaves it.
    heading.setAttribute('tabindex', '-1');
    heading.addEventListener('blur', () => heading.removeAttribute('tabindex'), { once: true });
    heading.focus();
  }, [kind]);

  // Announcing a warning error: a role="status" inserted together with its message is often not read out, so
  // one polite region stays mounted in every state (outside the block, which is aria-busy while loading) and
  // the message is written into it after the state has changed. The visible Alert is then not a live region,
  // so the message is read once. Critical errors interrupt through their own role="alert".
  const warningError = kind === 'error' && errorLevel !== 'critical';
  const [announcement, setAnnouncement] = useState<ReactNode>(null);
  useEffect(() => {
    setAnnouncement(warningError ? message : null);
  }, [warningError, message]);

  return (
    <>
      <Block
        {...rest}
        ref={handleRef}
        aria-busy={kind === 'loading' || undefined}
        onFocus={(event) => {
          focusInside.current = true;
          onFocus?.(event);
        }}
        onBlur={(event) => {
          // Focus that leaves for another element outside, or for nothing because the user clicked elsewhere,
          // counts as gone. A blur from an element that the state change removed is ignored: Chromium fires none,
          // other browsers may, and focus should still move to the title then.
          if ((event.target as Node).isConnected) {
            focusInside.current = Boolean(event.relatedTarget && event.currentTarget.contains(event.relatedTarget as Node));
          }
          onBlur?.(event);
        }}
      >
        {kind === 'empty' ? (
          <div className={styles.empty}>
            <p className={[styles.message, 't-body'].join(' ')}>{message}</p>
            {action ? <div className={styles.action}>{action}</div> : null}
          </div>
        ) : null}

        {showSkeleton ? <Skeleton lines={3} label={loadingLabel} /> : null}

        {kind === 'error' ? (
          <div className={styles.error}>
            {errorLevel === 'critical' ? <Alert level="critical">{message}</Alert> : <Alert level="warning" live={false}>{message}</Alert>}
            {onRetry ? (
              <div className={styles.action}>
                <Button icon={<RefreshCw size={16} strokeWidth={1.5} />} loading={retrying} onClick={onRetry}>
                  {retryLabel}
                </Button>
              </div>
            ) : null}
            {children}
          </div>
        ) : null}
      </Block>
      {/* Same keyword as the visible warning Alert. */}
      <div role="status" className="visually-hidden" data-blockstate-announcer="">
        {announcement ? <>Warning. {announcement}</> : null}
      </div>
    </>
  );
});
