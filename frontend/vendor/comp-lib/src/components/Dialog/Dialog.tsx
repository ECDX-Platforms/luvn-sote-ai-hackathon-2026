import {
  forwardRef,
  useEffect,
  useId,
  useLayoutEffect,
  useRef,
  type DialogHTMLAttributes,
  type KeyboardEvent,
  type ReactNode,
  type Ref,
} from 'react';
import styles from './Dialog.module.css';

export type DialogSize = 'sm' | 'md';

export interface DialogProps extends Omit<DialogHTMLAttributes<HTMLDialogElement>, 'open' | 'title' | 'children' | 'onClose' | 'onCancel'> {
  /** Shows the dialog as a modal (native showModal) on the scrim. */
  open: boolean;
  /** Called when the dialog asks to close: Esc (unless `closeOnEscape` is false), or the browser closing it. Set `open` to false here. */
  onClose: () => void;
  /** Figma: Title. A question or the action, sentence case ("Publish this item?"). Names the dialog (aria-labelledby). */
  title: string;
  /** Figma: Size. sm 480 (default), md 640. Never wider than the screen minus 16 on each side. */
  size?: DialogSize;
  /** Figma: Content slot. Body text, or a form built from Field and Input. Scrolls when it is taller than the screen. */
  children: ReactNode;
  /** The buttons, bottom right: secondary first, primary (or danger) last. Use Button. */
  actions?: ReactNode;
  /** Esc closes the dialog (default). Typed confirmation sets false: Esc then does nothing. */
  closeOnEscape?: boolean;
}

const focusable =
  'a[href], area[href], button:not([disabled]), input:not([disabled]):not([type="hidden"]), select:not([disabled]), textarea:not([disabled]), [tabindex], [contenteditable="true"]';

/**
 * The elements Tab actually stops on, in order. Leaves out tabindex="-1" (menu items, inactive tabs), hidden and inert
 * elements, and the radios of a group that Tab skips (only the checked one, or the first when none is checked, is a stop).
 */
function tabStops(root: HTMLElement): HTMLElement[] {
  const all = Array.from(root.querySelectorAll<HTMLElement>(focusable)).filter(
    (el) => el.tabIndex >= 0 && !el.closest('[hidden], [inert], [aria-hidden="true"]') && el.getClientRects().length > 0,
  );
  const seenGroups = new Set<string>();
  return all.filter((el) => {
    if (!(el instanceof HTMLInputElement) || el.type !== 'radio' || !el.name) return true;
    const key = `${el.form?.id ?? ''}|${el.name}`;
    const group = all.filter(
      (other): other is HTMLInputElement =>
        other instanceof HTMLInputElement && other.type === 'radio' && other.name === el.name && other.form === el.form,
    );
    const checked = group.find((radio) => radio.checked);
    if (checked) return el === checked;
    if (seenGroups.has(key)) return false;
    seenGroups.add(key);
    return true; // no radio checked: the first one is the stop
  });
}

function assignRef<T>(ref: Ref<T> | undefined, value: T | null) {
  if (typeof ref === 'function') ref(value);
  else if (ref) (ref as { current: T | null }).current = value;
}

/**
 * Modal dialog on the scrim, built on the native <dialog> (showModal): the page behind is inert, focus moves
 * in and Tab wraps inside it, Esc closes, and focus returns to the element that opened it.
 * Esc rule (see Tooltip): an Escape that a tooltip or menu inside already handled (defaultPrevented) does not
 * close the dialog. The browser skips the dialog's cancel step for such a key, so nothing else is needed.
 */
export const Dialog = forwardRef<HTMLDialogElement, DialogProps>(function Dialog(
  { open, onClose, title, size = 'sm', children, actions, closeOnEscape = true, className, onKeyDown, ...rest },
  ref,
) {
  const titleId = useId();
  const dialogRef = useRef<HTMLDialogElement | null>(null);
  const contentRef = useRef<HTMLDivElement | null>(null);
  /** The element that had focus before the dialog opened. Focus goes back to it on close. */
  const returnTo = useRef<HTMLElement | null>(null);
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;
  const openRef = useRef(open);
  openRef.current = open;
  const escapeRef = useRef(closeOnEscape);
  escapeRef.current = closeOnEscape;

  // Open and close the native dialog from the prop. Layout effect: focus moves in before the first paint.
  useLayoutEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    if (open && !dialog.open) {
      returnTo.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
      dialog.showModal(); // focuses the first focusable element (or one with autoFocus)
      // showModal may pick an element Tab skips (an inactive tab, an unchecked radio): move to the first real tab stop.
      const stops = tabStops(dialog);
      if (!stops.includes(document.activeElement as HTMLElement)) stops[0]?.focus();
    } else if (!open && dialog.open) {
      dialog.close();
    }
  }, [open]);

  // Native events: Esc asks to close (cancel), and the browser may close the dialog itself (close).
  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    const onCancel = (event: Event) => {
      event.preventDefault(); // the parent decides through `open`
      if (escapeRef.current) onCloseRef.current();
    };
    const onNativeClose = () => {
      // close() only queues this event. If the dialog was opened again before it arrived, it is stale: it must not
      // close the new opening, move focus to the old trigger, or show the dialog again.
      if (dialog.open) return;
      // Chrome can close a modal dialog on Esc without a cancel event (e.g. a second Esc, or focus on the page body).
      if (openRef.current && !escapeRef.current) {
        dialog.showModal(); // Esc must not close a typed confirmation: show it again, focus moves back in
        return;
      }
      const target = returnTo.current;
      returnTo.current = null;
      if (target?.isConnected) target.focus();
      if (openRef.current) onCloseRef.current(); // keep the parent in step
    };
    dialog.addEventListener('cancel', onCancel);
    dialog.addEventListener('close', onNativeClose);
    return () => {
      dialog.removeEventListener('cancel', onCancel);
      dialog.removeEventListener('close', onNativeClose);
    };
  }, []);

  // Typed confirmation: stop the Escape key itself wherever focus is, not only inside the dialog (runbook).
  // Window, bubble phase: an inner tooltip (document, capture) still closes first.
  useEffect(() => {
    if (!open || closeOnEscape) return;
    const onKey = (event: globalThis.KeyboardEvent) => {
      if (event.key === 'Escape') event.preventDefault();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, closeOnEscape]);

  // Content that scrolls must be reachable with the keyboard (axe scrollable-region-focusable): a tab stop only while it overflows.
  useEffect(() => {
    const content = contentRef.current;
    if (!open || !content || typeof ResizeObserver === 'undefined') return;
    const update = () => {
      if (content.scrollHeight > content.clientHeight + 1) content.tabIndex = 0;
      else content.removeAttribute('tabindex');
    };
    update();
    const observer = new ResizeObserver(update);
    observer.observe(content);
    Array.from(content.children).forEach((child) => observer.observe(child));
    return () => observer.disconnect();
  }, [open, children]);

  // Unmounting while open (e.g. the page changes) still returns focus.
  useEffect(
    () => () => {
      const target = returnTo.current;
      if (target?.isConnected) target.focus();
    },
    [],
  );

  const handleKeyDown = (event: KeyboardEvent<HTMLDialogElement>) => {
    onKeyDown?.(event);
    if (event.key === 'Escape' && !escapeRef.current) {
      // Stop the Escape key itself (runbook): Chrome may close a native dialog even when cancel is prevented.
      event.preventDefault();
      return;
    }
    if (event.key !== 'Tab' || event.defaultPrevented) return;
    // Keep Tab inside the dialog: wrap from the last element to the first, and back with Shift+Tab.
    const dialog = dialogRef.current;
    if (!dialog) return;
    const items = tabStops(dialog);
    if (items.length === 0) return;
    const first = items[0];
    const last = items[items.length - 1];
    const active = document.activeElement;
    if (event.shiftKey && (active === first || active === dialog)) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && active === last) {
      event.preventDefault();
      first.focus();
    }
  };

  return (
    <dialog
      {...rest}
      ref={(node) => {
        dialogRef.current = node;
        assignRef(ref, node);
      }}
      aria-labelledby={titleId}
      className={[styles.dialog, styles[size], className].filter(Boolean).join(' ')}
      onKeyDown={handleKeyDown}
    >
      <h2 id={titleId} className={[styles.title, 't-title-md'].join(' ')}>{title}</h2>
      <div ref={contentRef} className={[styles.content, 't-body'].join(' ')}>{children}</div>
      {actions ? <div className={styles.actions}>{actions}</div> : null}
    </dialog>
  );
});
