import {
  Fragment,
  cloneElement,
  createContext,
  forwardRef,
  isValidElement,
  useCallback,
  useContext,
  useEffect,
  useId,
  useLayoutEffect,
  useRef,
  useState,
  type ButtonHTMLAttributes,
  type HTMLAttributes,
  type KeyboardEvent,
  type MouseEvent,
  type ReactElement,
  type ReactNode,
  type Ref,
} from 'react';
import { Trash } from 'lucide-react';
import { Divider } from '../Divider';
import { Tooltip } from '../Tooltip';
import { placeFixed, readGap } from '../Tooltip/placeFixed';
import styles from './Menu.module.css';

export type MenuAlign = 'start' | 'end';
export type MenuItemVariant = 'default' | 'danger';

type MenuContextValue = { close: (returnFocus: boolean) => void };
const MenuContext = createContext<MenuContextValue | null>(null);

function assignRef<T>(ref: Ref<T> | undefined, value: T | null) {
  if (typeof ref === 'function') ref(value);
  else if (ref) (ref as { current: T | null }).current = value;
}

type TriggerProps = {
  ref?: Ref<HTMLElement>;
  id?: string;
  onClick?: (event: MouseEvent<HTMLElement>) => void;
  onKeyDown?: (event: KeyboardEvent<HTMLElement>) => void;
};

export interface MenuProps extends Omit<HTMLAttributes<HTMLDivElement>, 'children' | 'role'> {
  /** The button that opens the menu (Button or IconButton). It gets aria-haspopup, aria-expanded and aria-controls. */
  trigger: ReactElement;
  /** MenuItem and MenuSeparator elements. */
  children: ReactNode;
  /** Which edge of the trigger the menu lines up with. Code only. Use end for row actions at the right edge. */
  align?: MenuAlign;
  /** Accessible name of the menu. By default the trigger's name is used (aria-labelledby). */
  label?: string;
  /** Called when the menu opens or closes. */
  onOpenChange?: (open: boolean) => void;
  /** Open on first render without moving focus (for docs and visual tests). */
  defaultOpen?: boolean;
}

/**
 * Menu button (WAI-ARIA menu button pattern). Opens on click, Enter, Space or the arrow keys. Arrow keys,
 * Home, End and type-ahead move between items. Esc closes it and returns focus to the trigger; Tab closes it
 * and moves on. Placed with position: fixed from the trigger, so scroll areas do not clip it.
 * Esc rule (see Tooltip): an Escape that is already defaultPrevented (a tooltip inside closed) is ignored.
 */
export const Menu = forwardRef<HTMLDivElement, MenuProps>(function Menu(
  { trigger, children, align = 'start', label, onOpenChange, defaultOpen = false, className, onKeyDown, ...rest },
  ref,
) {
  const baseId = useId();
  const menuId = `${baseId}-menu`;
  const [open, setOpenState] = useState(defaultOpen);
  const openRef = useRef(open);
  openRef.current = open;
  /** Which item gets focus once the menu is placed (runbook: a hidden element cannot take focus). None for defaultOpen. */
  const [focusTarget, setFocusTarget] = useState<'first' | 'last' | 'none'>('none');
  const triggerRef = useRef<HTMLElement | null>(null);
  const menuRef = useRef<HTMLDivElement | null>(null);
  const onOpenChangeRef = useRef(onOpenChange);
  onOpenChangeRef.current = onOpenChange;

  const setOpen = useCallback((next: boolean) => {
    if (next === openRef.current) return;
    openRef.current = next;
    setOpenState(next);
    onOpenChangeRef.current?.(next); // only on a real change, never inside a state updater
  }, []);

  const close = useCallback(
    (returnFocus: boolean) => {
      setOpen(false);
      setFocusTarget('none');
      if (returnFocus) triggerRef.current?.focus();
    },
    [setOpen],
  );

  const items = () =>
    Array.from(menuRef.current?.querySelectorAll<HTMLElement>('[role="menuitem"], [role="menuitemradio"]') ?? []);

  const place = useCallback(() => {
    const menu = menuRef.current;
    const anchor = triggerRef.current;
    if (!menu || !anchor) return;
    const gap = readGap(menu, '--menu-gap');
    // A long menu fits the roomier side of the trigger and scrolls, instead of covering the trigger.
    // Not below three items high: on a very short screen the CSS cap (screen minus 16 above and below) applies.
    const box = anchor.getBoundingClientRect();
    const viewH = document.documentElement.clientHeight;
    const room = Math.max(viewH - box.bottom, box.top) - 2 * gap;
    const natural = menu.scrollHeight + (menu.offsetHeight - menu.clientHeight); // full content height plus the borders
    menu.style.maxHeight = natural > room && room >= 3 * readGap(menu, '--control-h') ? `${room}px` : '';
    placeFixed(menu, anchor, { side: 'bottom', align, gap });
    // Mark a menu that scrolls (taller than its max-height): its items then draw the focus ring inside (see CSS).
    if (menu.scrollHeight > menu.clientHeight + 1) menu.dataset.scroll = '';
    else delete menu.dataset.scroll;
  }, [align]);

  // Place first, then move focus into the menu.
  useLayoutEffect(() => {
    if (!open) return;
    place();
    if (focusTarget === 'none') return;
    const list = items();
    const target = focusTarget === 'last' ? list[list.length - 1] : list[0];
    target?.focus();
    target?.scrollIntoView({ block: 'nearest' }); // a long menu that opens on its last item (ArrowUp) shows it
  }, [open, place, focusTarget]);

  // While open: follow scroll and resize (once per frame), close on a pointer press outside.
  useEffect(() => {
    if (!open) return;
    let frame = 0;
    const schedule = (event?: Event) => {
      if (event?.target === menuRef.current) return; // scrolling inside a long menu does not move it
      if (frame) return;
      frame = requestAnimationFrame(() => {
        frame = 0;
        place();
      });
    };
    const onPointerDown = (event: PointerEvent) => {
      const target = event.target as Node;
      if (menuRef.current?.contains(target) || triggerRef.current?.contains(target)) return;
      setOpen(false); // focus stays where the user pressed
    };
    window.addEventListener('scroll', schedule, true);
    window.addEventListener('resize', schedule);
    document.addEventListener('pointerdown', onPointerDown, true);
    return () => {
      if (frame) cancelAnimationFrame(frame);
      window.removeEventListener('scroll', schedule, true);
      window.removeEventListener('resize', schedule);
      document.removeEventListener('pointerdown', onPointerDown, true);
    };
  }, [open, place, setOpen]);

  const handleMenuKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    onKeyDown?.(event);
    if (event.defaultPrevented && event.key !== 'Tab') return; // Esc rule: an inner tooltip already handled it
    const list = items();
    const index = list.indexOf(document.activeElement as HTMLElement);
    const move = (to: number) => {
      event.preventDefault();
      const item = list[(to + list.length) % list.length];
      item?.focus();
      item?.scrollIntoView({ block: 'nearest' }); // keeps the focused item in view when the menu scrolls
    };
    switch (event.key) {
      case 'ArrowDown':
        return move(index + 1);
      case 'ArrowUp':
        return move(index - 1);
      case 'Home':
      case 'PageUp':
        return move(0);
      case 'End':
      case 'PageDown':
        return move(list.length - 1);
      case 'Escape':
        event.preventDefault(); // handled: a dialog around the menu stays open
        close(true);
        return;
      case 'Tab':
        // Close and let Tab move on from the trigger (WAI-ARIA menu button).
        setOpen(false);
        triggerRef.current?.focus();
        return;
      default:
        if (event.key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey && event.key !== ' ') {
          // Type-ahead: the next item whose label starts with the typed character.
          const char = event.key.toLocaleLowerCase();
          const ordered = [...list.slice(index + 1), ...list.slice(0, index + 1)];
          const hit = ordered.find((item) => item.textContent?.trim().toLocaleLowerCase().startsWith(char));
          if (hit) {
            event.preventDefault();
            hit.focus();
            hit.scrollIntoView({ block: 'nearest' });
          }
        }
    }
  };

  if (!isValidElement<TriggerProps>(trigger)) return null;
  const triggerElement = trigger as ReactElement<TriggerProps>;
  const triggerProps = triggerElement.props;
  const triggerId = triggerProps.id ?? `${baseId}-trigger`;

  const openWith = (target: 'first' | 'last') => {
    setFocusTarget(target);
    setOpen(true);
  };

  const triggerClone = cloneElement(triggerElement, {
    ref: (node: HTMLElement | null) => {
      triggerRef.current = node;
      assignRef(triggerProps.ref, node);
    },
    id: triggerId,
    'aria-haspopup': 'menu',
    'aria-expanded': open,
    'aria-controls': menuId,
    onClick: (event: MouseEvent<HTMLElement>) => {
      triggerProps.onClick?.(event);
      if (event.defaultPrevented) return; // e.g. a disabled Button
      if (open) setOpen(false);
      else openWith('first');
    },
    onKeyDown: (event: KeyboardEvent<HTMLElement>) => {
      triggerProps.onKeyDown?.(event);
      if (event.defaultPrevented) return;
      // A disabled trigger (aria-disabled, still focusable) must not open the menu. Its click is already blocked by Button.
      if (event.currentTarget.getAttribute('aria-disabled') === 'true') return;
      if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
        event.preventDefault();
        openWith(event.key === 'ArrowUp' ? 'last' : 'first');
      }
      // Enter and Space reach onClick through the native button.
    },
  } as Partial<TriggerProps> & Record<string, unknown>);

  return (
    <Fragment>
      {triggerClone}
      <MenuContext.Provider value={{ close }}>
        <div
          {...rest}
          ref={(node) => {
            menuRef.current = node;
            assignRef(ref, node);
          }}
          id={menuId}
          role="menu"
          aria-label={label}
          aria-labelledby={label ? undefined : triggerId}
          aria-orientation="vertical"
          hidden={!open}
          className={[styles.menu, className].filter(Boolean).join(' ')}
          onKeyDown={handleMenuKeyDown}
        >
          {children}
        </div>
      </MenuContext.Provider>
    </Fragment>
  );
});

export interface MenuItemProps extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, 'disabled' | 'children' | 'role' | 'onSelect'> {
  /** Figma: Label. Verb + object, the real action ("Delete item"), never a placeholder. */
  children: ReactNode;
  /** Figma: Type. Danger = critical-fg text and a fixed trash icon; it opens the typed confirmation. */
  variant?: MenuItemVariant;
  /** Figma: Show icon + Icon. A 16px leading icon (lucide at size 16). Ignored for danger, which always shows the trash icon. */
  icon?: ReactNode;
  /**
   * Figma: State = Selected. For a set of options (choose one): give every item `selected`, true for the current one.
   * The items then become menuitemradio with aria-checked. Leave it out for plain actions.
   */
  selected?: boolean;
  /** Figma: State = Disabled. Muted, stays focusable with the arrow keys (aria-disabled), does nothing when chosen. */
  disabled?: boolean;
  /** Why the item is disabled. Shown in a Tooltip on hover and keyboard focus (Figma description). */
  disabledReason?: string;
  /** Called when the item is chosen (click, Enter or Space). The menu then closes and focus returns to the trigger. */
  onSelect?: () => void;
}

/** One action or option in a Menu. Figma: Overlays / Menu item (51:158). */
export const MenuItem = forwardRef<HTMLButtonElement, MenuItemProps>(function MenuItem(
  { children, variant = 'default', icon, selected, disabled = false, disabledReason, onSelect, className, onClick, ...rest },
  ref,
) {
  const menu = useContext(MenuContext);
  const radio = selected !== undefined;
  const leading = variant === 'danger' ? <Trash size={16} strokeWidth={1.5} /> : icon;

  const handleClick = (event: MouseEvent<HTMLButtonElement>) => {
    if (disabled) {
      event.preventDefault();
      return;
    }
    onClick?.(event);
    if (event.defaultPrevented) return;
    menu?.close(true);
    onSelect?.();
  };

  const item = (
    <button
      {...rest}
      ref={ref}
      type="button"
      role={radio ? 'menuitemradio' : 'menuitem'}
      aria-checked={radio ? selected : undefined}
      aria-disabled={disabled || undefined}
      tabIndex={-1}
      className={[styles.item, styles[variant], 't-control', className].filter(Boolean).join(' ')}
      onClick={handleClick}
    >
      {leading ? <span className={styles.icon} aria-hidden>{leading}</span> : null}
      <span className={styles.label}>{children}</span>
    </button>
  );

  return disabled && disabledReason ? <Tooltip text={disabledReason}>{item}</Tooltip> : item;
});

export type MenuSeparatorProps = Omit<HTMLAttributes<HTMLDivElement>, 'children' | 'role'>;

/** Hairline between groups of items, e.g. before risky actions. Uses Divider, with 4px space above and below (Figma). */
export const MenuSeparator = forwardRef<HTMLDivElement, MenuSeparatorProps>(function MenuSeparator({ className, ...rest }, ref) {
  return (
    <div {...rest} ref={ref} role="separator" className={[styles.separator, className].filter(Boolean).join(' ')}>
      <Divider />
    </div>
  );
});
