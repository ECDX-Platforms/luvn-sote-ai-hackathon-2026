import { forwardRef, type ButtonHTMLAttributes, type HTMLAttributes, type KeyboardEvent, type ReactNode } from 'react';
import styles from './Tabs.module.css';

export interface TabListProps extends Omit<HTMLAttributes<HTMLDivElement>, 'role'> {
  /** Accessible name of the tab list, e.g. the name of the page or block the tabs belong to. */
  label: string;
  /** The Tab elements. Exactly one of them is selected. */
  children: ReactNode;
}

/**
 * Row of tabs (`role="tablist"`). Arrow Left and Right move to the previous or next tab and select it; Home and End go to
 * the first and last. Only the selected tab is a Tab stop. The Tab bar layout (padding, hairline, primary button) is the App shell's.
 */
export const TabList = forwardRef<HTMLDivElement, TabListProps>(function TabList(
  { label, className, onKeyDown, children, ...rest },
  ref,
) {
  const handleKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    onKeyDown?.(event);
    if (event.defaultPrevented) return;
    const tabs = Array.from(event.currentTarget.querySelectorAll<HTMLButtonElement>('[role="tab"]'));
    const index = tabs.indexOf(event.target as HTMLButtonElement);
    if (index === -1 || tabs.length === 0) return;
    const next = {
      ArrowRight: (index + 1) % tabs.length,
      ArrowLeft: (index - 1 + tabs.length) % tabs.length,
      Home: 0,
      End: tabs.length - 1,
    }[event.key];
    if (next === undefined) return;
    event.preventDefault();
    tabs[next].focus();
    tabs[next].click(); // Automatic activation: moving to a tab selects it (WAI-ARIA tabs pattern).
  };

  return (
    <div
      {...rest}
      ref={ref}
      role="tablist"
      aria-label={label}
      aria-orientation="horizontal"
      className={[styles.tabList, className].filter(Boolean).join(' ')}
      onKeyDown={handleKeyDown}
    >
      {children}
    </div>
  );
});

/**
 * No disabled state (Figma: Default and Selected only): leave out a tab whose view is not available.
 * A disabled tab would also stop Arrow Left and Right, so `disabled` and `aria-disabled` are not accepted.
 */
export interface TabProps extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, 'role' | 'children' | 'disabled' | 'aria-disabled'> {
  /** Figma: State = Active. The tab whose panel is shown: text colour, weight 600, 2px bottom bar. Sets aria-selected. */
  selected?: boolean;
  /** Figma: Label. One or two words, sentence case. */
  children: string;
}

/** One tab. Use inside TabList. Select it with onClick; give it an `id` and `aria-controls` pointing to its TabPanel. */
export const Tab = forwardRef<HTMLButtonElement, TabProps>(function Tab(
  { selected = false, type = 'button', className, children, ...props },
  ref,
) {
  // Also drop them at run time: plain JavaScript callers are not stopped by the type.
  const { disabled: _disabled, 'aria-disabled': _ariaDisabled, ...rest } = props as ButtonHTMLAttributes<HTMLButtonElement>;
  return (
    <button
      {...rest}
      ref={ref}
      type={type}
      role="tab"
      aria-selected={selected}
      tabIndex={selected ? 0 : -1}
      className={[styles.tab, 't-control', className].filter(Boolean).join(' ')}
    >
      {/* data-label reserves the width of the 600 weight, so the row does not shift when the selection moves. */}
      <span className={styles.label} data-label={children}>{children}</span>
    </button>
  );
});

export interface TabPanelProps extends Omit<HTMLAttributes<HTMLDivElement>, 'role'> {
  /** The `id` of the Tab that shows this panel. */
  tabId: string;
  /** False hides the panel. */
  selected?: boolean;
}

/** Content shown by one tab (`role="tabpanel"`). It is a Tab stop, so keyboard users reach the content right after the tab list. */
export const TabPanel = forwardRef<HTMLDivElement, TabPanelProps>(function TabPanel(
  { tabId, selected = false, className, children, ...rest },
  ref,
) {
  return (
    <div
      {...rest}
      ref={ref}
      role="tabpanel"
      aria-labelledby={tabId}
      tabIndex={0}
      hidden={!selected}
      className={[styles.tabPanel, className].filter(Boolean).join(' ')}
    >
      {children}
    </div>
  );
});
