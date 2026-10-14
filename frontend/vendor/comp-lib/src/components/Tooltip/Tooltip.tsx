import {
  Fragment,
  cloneElement,
  forwardRef,
  isValidElement,
  useCallback,
  useEffect,
  useId,
  useLayoutEffect,
  useRef,
  useState,
  type FocusEvent,
  type HTMLAttributes,
  type PointerEvent,
  type ReactElement,
  type Ref,
} from 'react';
import { placeFixed, readGap } from './placeFixed';
import styles from './Tooltip.module.css';

export type TooltipPlacement = 'top' | 'bottom';
export type TooltipRelationship = 'description' | 'label';

export interface TooltipProps extends Omit<HTMLAttributes<HTMLSpanElement>, 'children' | 'content' | 'role'> {
  /** Figma: Text. Short plain text, wraps at 240px. Never the only copy of critical information. */
  text: string;
  /**
   * The trigger: one focusable element (Button, IconButton, Link …) that accepts a ref and event props.
   * A disabled trigger must stay focusable (aria-disabled), or keyboard users never see the tooltip.
   */
  children: ReactElement;
  /** Preferred side. Flips to the other side when there is no room. Code only (not a Figma property). */
  placement?: TooltipPlacement;
  /**
   * description (default): the text adds to the trigger's name (aria-describedby), e.g. why a button is disabled.
   * In both cases the tooltip node itself is aria-hidden; the description is read with the trigger.
   * label: the text repeats the trigger's own accessible name, so it is hidden from screen readers to avoid
   * reading it twice. The trigger must then have its own accessible name (aria-label, as IconButton's `label`):
   * the tooltip only shows that name visually, it does not name the trigger.
   */
  relationship?: TooltipRelationship;
  /** Controlled open state. Leave it out to let hover and focus drive the tooltip. Without `onOpenChange` it cannot be closed, so it does not take Esc. */
  open?: boolean;
  /** Called with the new open state when hover, focus, blur or Esc would change it. Only called when the value changes. */
  onOpenChange?: (open: boolean) => void;
}

/** Hover opens after a short pause, so moving across a toolbar does not flash every tooltip. Focus opens at once. */
const OPEN_DELAY = 300;
/** Leaving the trigger waits a moment, so the pointer can move onto the tooltip (WCAG 1.4.13 hoverable). */
const CLOSE_DELAY = 100;

/**
 * Esc rule for every overlay in this library (Tooltip, Menu, Dialog): the innermost open overlay handles
 * Escape and calls preventDefault. An overlay that sees an Escape keydown with defaultPrevented ignores it.
 * The tooltip listens in the document capture phase, so it always runs first. It does not stop propagation.
 * preventDefault on the keydown also stops a native modal <dialog> from closing on that Esc.
 */
const isDev = (() => {
  try {
    return Boolean(import.meta.env?.DEV);
  } catch {
    return false;
  }
})();

function assignRef<T>(ref: Ref<T> | undefined, value: T | null) {
  if (typeof ref === 'function') ref(value);
  else if (ref) (ref as { current: T | null }).current = value;
}

type TriggerProps = {
  ref?: Ref<HTMLElement>;
  'aria-describedby'?: string;
  onPointerEnter?: (event: PointerEvent<HTMLElement>) => void;
  onPointerLeave?: (event: PointerEvent<HTMLElement>) => void;
  onPointerDown?: (event: PointerEvent<HTMLElement>) => void;
  onFocus?: (event: FocusEvent<HTMLElement>) => void;
  onBlur?: (event: FocusEvent<HTMLElement>) => void;
};

/**
 * Short text label for icon-only buttons and disabled controls. Opens on hover and keyboard focus,
 * closes on Esc, blur and pointer leave (WCAG 1.4.13). Placed with position: fixed from its trigger,
 * so scroll areas do not clip it. Rendered next to the trigger (not in a portal), so it also works
 * inside a modal dialog's top layer.
 */
export const Tooltip = forwardRef<HTMLSpanElement, TooltipProps>(function Tooltip(
  { text, children, placement = 'top', relationship = 'description', open: openProp, onOpenChange, className, onPointerEnter, onPointerLeave, ...rest },
  ref,
) {
  const id = useId();
  const [innerOpen, setInnerOpen] = useState(false);
  const controlled = openProp !== undefined;
  const open = controlled ? openProp : innerOpen;
  /** False when the child did not pass the ref through: there is nothing to place the tooltip from. */
  const [hasTrigger, setHasTrigger] = useState(true);
  const warned = useRef(false);

  // Latest values in refs, so handlers and listeners stay stable when callbacks are inline.
  const openRef = useRef(open);
  openRef.current = open;
  const onOpenChangeRef = useRef(onOpenChange);
  onOpenChangeRef.current = onOpenChange;
  /** A controlled tooltip without onOpenChange can never close, so it must not take Esc from the page. */
  const canClose = !controlled || onOpenChange !== undefined;

  const triggerRef = useRef<HTMLElement | null>(null);
  const tipRef = useRef<HTMLSpanElement | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  /** True while keyboard focus holds the tooltip open: pointer leave must not close it then. */
  const byFocus = useRef(false);

  const clearTimer = () => {
    if (timer.current !== undefined) clearTimeout(timer.current);
    timer.current = undefined;
  };

  const setOpen = useCallback(
    (next: boolean) => {
      clearTimer();
      if (next === openRef.current) return;
      if (!controlled) {
        openRef.current = next;
        setInnerOpen(next);
      }
      onOpenChangeRef.current?.(next);
    },
    [controlled],
  );

  const later = (next: boolean, delay: number) => {
    clearTimer();
    timer.current = setTimeout(() => setOpen(next), delay);
  };

  useEffect(() => clearTimer, []);

  // Place from the trigger's box. Runs before paint, so the tooltip never shows in the wrong place.
  const place = useCallback(() => {
    const trigger = triggerRef.current;
    const tip = tipRef.current;
    if (!trigger || !tip) return;
    placeFixed(tip, trigger, { side: placement, align: 'center', gap: readGap(tip, '--tooltip-gap') });
  }, [placement]);

  // Ref callbacks run before layout effects, so this sees whether the child passed the ref through.
  useLayoutEffect(() => {
    const found = triggerRef.current !== null;
    if (found !== hasTrigger) setHasTrigger(found);
    if (!found && isDev && !warned.current) {
      warned.current = true;
      console.warn('Tooltip: the trigger did not receive the ref, so the tooltip stays hidden. Wrap one element that passes its ref to a DOM node (Button, IconButton, a native element).');
    }
  });

  const visible = open && hasTrigger;

  useLayoutEffect(() => {
    if (visible) place();
  }, [visible, place, text]);

  // While visible: follow scroll and resize, at most once per frame.
  useEffect(() => {
    if (!visible) return;
    let frame = 0;
    const schedule = () => {
      if (frame) return;
      frame = requestAnimationFrame(() => {
        frame = 0;
        place();
      });
    };
    window.addEventListener('scroll', schedule, true);
    window.addEventListener('resize', schedule);
    return () => {
      if (frame) cancelAnimationFrame(frame);
      window.removeEventListener('scroll', schedule, true);
      window.removeEventListener('resize', schedule);
    };
  }, [visible, place]);

  // Esc dismisses it without moving focus. Only while it is visible and can actually close (see the Esc rule above).
  useEffect(() => {
    if (!visible || !canClose) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== 'Escape' || event.defaultPrevented) return;
      event.preventDefault(); // handled: a menu or dialog around the trigger ignores this Esc
      byFocus.current = false;
      setOpen(false);
    };
    document.addEventListener('keydown', onKey, true);
    return () => document.removeEventListener('keydown', onKey, true);
  }, [visible, canClose, setOpen]);

  if (!isValidElement<TriggerProps>(children)) return children;
  const child = children as ReactElement<TriggerProps>;
  const childProps = child.props;

  const setTrigger = (node: HTMLElement | null) => {
    triggerRef.current = node;
    assignRef(childProps.ref, node);
  };
  const setTip = (node: HTMLSpanElement | null) => {
    tipRef.current = node;
    assignRef(ref, node);
  };

  const trigger = cloneElement(child, {
    ref: setTrigger,
    'aria-describedby':
      relationship === 'description' ? [childProps['aria-describedby'], id].filter(Boolean).join(' ') : childProps['aria-describedby'],
    onPointerEnter: (event: PointerEvent<HTMLElement>) => {
      childProps.onPointerEnter?.(event);
      if (event.pointerType === 'touch') return; // touch has no hover; a tap must not leave a tooltip behind
      later(true, open ? 0 : OPEN_DELAY);
    },
    onPointerLeave: (event: PointerEvent<HTMLElement>) => {
      childProps.onPointerLeave?.(event);
      if (byFocus.current) {
        clearTimer();
        return; // keyboard focus still holds it open
      }
      later(false, CLOSE_DELAY);
    },
    onPointerDown: (event: PointerEvent<HTMLElement>) => {
      childProps.onPointerDown?.(event);
      byFocus.current = false;
      setOpen(false); // pressing the trigger means the label was read; get it out of the way
    },
    onFocus: (event: FocusEvent<HTMLElement>) => {
      childProps.onFocus?.(event);
      let keyboard = true;
      try {
        keyboard = event.currentTarget.matches(':focus-visible');
      } catch {
        /* old browsers: treat every focus as keyboard focus */
      }
      if (keyboard) {
        byFocus.current = true;
        setOpen(true);
      }
    },
    onBlur: (event: FocusEvent<HTMLElement>) => {
      childProps.onBlur?.(event);
      byFocus.current = false;
      setOpen(false);
    },
  });

  return (
    <Fragment>
      {trigger}
      <span
        {...rest}
        ref={setTip}
        id={id}
        role="tooltip"
        // Always hidden from screen readers as a node of its own. With relationship "description" its text still
        // reaches them through the trigger's aria-describedby (referenced hidden text counts), so it is read once,
        // with the trigger. This also keeps the tooltip valid inside roles with strict children (menu, list, row).
        aria-hidden
        hidden={!visible}
        className={[styles.tooltip, 't-caption', className].filter(Boolean).join(' ')}
        onPointerEnter={(event) => {
          onPointerEnter?.(event);
          clearTimer(); // the pointer moved onto the tooltip: keep it open
        }}
        onPointerLeave={(event) => {
          onPointerLeave?.(event);
          if (!byFocus.current) later(false, CLOSE_DELAY);
        }}
      >
        {text}
      </span>
    </Fragment>
  );
});
