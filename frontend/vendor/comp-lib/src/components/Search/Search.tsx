import { forwardRef, useEffect, useRef, useSyncExternalStore, type InputHTMLAttributes, type KeyboardEvent as ReactKeyboardEvent, type Ref } from 'react';
import { Search as SearchIcon } from 'lucide-react';
import { KeyHint } from '../KeyHint';
import { TEXT_INPUT_TYPES } from '../Input/Input';
import styles from './Search.module.css';

export interface SearchProps extends Omit<InputHTMLAttributes<HTMLInputElement>, 'disabled' | 'type' | 'size' | 'aria-label'> {
  /** Accessible name, read out instead of a visible label (e.g. "Search items"). Required: the placeholder is never the only label. */
  label: string;
  /** Figma: Placeholder. text-muted, shown while empty. */
  placeholder?: string;
  /**
   * Figma: Show key hint. One letter, e.g. "k". Cmd/Ctrl + that letter focuses the field from anywhere on the page,
   * except while people type in another text field, textarea, select or contenteditable. The key hint shows "⌘K" on
   * Apple devices or "Ctrl K" elsewhere, only while the shortcut works (not when disabled). Opt-in: leave it out for
   * no shortcut and no hint. One Search per page.
   */
  shortcut?: string;
  /** Figma: State = Disabled. Muted text, line border. Stays focusable (aria-disabled); cannot be typed in. The shortcut is off and its hint is hidden. */
  disabled?: boolean;
}

const isApple = () => /Mac|iPhone|iPad|iPod/.test(navigator.platform || navigator.userAgent);

// The platform is read in the browser only. On the server and during hydration it is unknown (null), so the
// first render is neutral (no key hint) and matches the server HTML; React then renders the real hint.
const noSubscribe = () => () => {};
const usePlatformIsApple = () => useSyncExternalStore(noSubscribe, isApple, () => null);

/** Input types people type text into (the same ones Input supports). */
const TEXT_TYPES: ReadonlySet<string> = new Set(TEXT_INPUT_TYPES);

/** True when the key press comes from another place people type in (text input, textarea, select, contenteditable). */
function isOtherEditable(target: EventTarget | null, own: HTMLInputElement | null) {
  if (!(target instanceof HTMLElement) || target === own) return false;
  if (target.isContentEditable || target instanceof HTMLTextAreaElement || target instanceof HTMLSelectElement) return true;
  return target instanceof HTMLInputElement && TEXT_TYPES.has(target.type);
}

/** Clears the field the way typing does, so React's onChange fires (controlled and uncontrolled use both work). */
function clearLikeTyping(input: HTMLInputElement) {
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set?.call(input, '');
  input.dispatchEvent(new Event('input', { bubbles: true }));
}

function setRef<T>(ref: Ref<T> | undefined, value: T | null) {
  if (typeof ref === 'function') ref(value);
  else if (ref) (ref as { current: T | null }).current = value;
}

/** Header search field with a search icon and an optional ⌘K key hint. Styleguide: Header (search). `className` and `style` go on the field box; every other prop goes on the `<input>`. */
export const Search = forwardRef<HTMLInputElement, SearchProps>(function Search(
  { label, shortcut, disabled = false, readOnly, className, style, onKeyDown, ...rest },
  ref,
) {
  const inputRef = useRef<HTMLInputElement | null>(null);
  const key = shortcut?.toLowerCase();
  const apple = usePlatformIsApple();
  const shortcutOn = Boolean(key) && !disabled && apple !== null;

  useEffect(() => {
    if (!key || disabled || apple === null) return;
    const onDocumentKeyDown = (event: KeyboardEvent) => {
      if (isOtherEditable(event.target, inputRef.current)) return; // never take the keys while people type elsewhere
      if ((apple ? event.metaKey : event.ctrlKey) && !event.altKey && !event.shiftKey && event.key.toLowerCase() === key) {
        event.preventDefault();
        inputRef.current?.focus();
        inputRef.current?.select();
      }
    };
    document.addEventListener('keydown', onDocumentKeyDown);
    return () => document.removeEventListener('keydown', onDocumentKeyDown);
  }, [key, disabled, apple]);

  // Esc clears the text in every browser (natively only Chromium and Safari do). An empty field lets Esc
  // through, e.g. to close a dialog around it.
  const handleKeyDown = (event: ReactKeyboardEvent<HTMLInputElement>) => {
    onKeyDown?.(event);
    const input = event.currentTarget;
    if (event.defaultPrevented || event.key !== 'Escape' || disabled || readOnly || input.value === '') return;
    event.preventDefault();
    clearLikeTyping(input);
  };

  return (
    <label className={[styles.search, disabled ? styles.disabled : null, className].filter(Boolean).join(' ')} style={style}>
      <span className={styles.icon} aria-hidden>
        <SearchIcon size={16} strokeWidth={1.5} />
      </span>
      <input
        {...rest}
        ref={(node) => {
          inputRef.current = node;
          setRef(ref, node);
        }}
        type="search"
        className={[styles.input, 't-body'].join(' ')}
        aria-label={label}
        aria-keyshortcuts={shortcutOn && key ? `${apple ? 'Meta' : 'Control'}+${key.toUpperCase()}` : undefined}
        // Disabled keeps focus (aria-disabled) and blocks typing through readOnly.
        readOnly={readOnly || disabled}
        aria-disabled={disabled || undefined}
        onKeyDown={handleKeyDown}
      />
      {/* The hint shows only while the shortcut works. Coordinator decision over Figma, which draws it on Disabled too. */}
      {shortcutOn && key ? (
        <KeyHint className={styles.hint} aria-hidden>
          {apple ? `⌘${key.toUpperCase()}` : `Ctrl ${key.toUpperCase()}`}
        </KeyHint>
      ) : null}
    </label>
  );
});
