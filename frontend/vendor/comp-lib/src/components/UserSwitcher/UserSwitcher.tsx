import { forwardRef, useId, type ReactElement, type ReactNode } from 'react';
import { Menu, MenuSeparator, type MenuProps } from '../Menu';
import { UserMenuItem } from '../UserMenuItem';
import styles from './UserSwitcher.module.css';

export interface UserSwitcherUser {
  /** Stable id, passed to `onSelect`. */
  id: string;
  /** Figma: Name (User menu item). */
  name: string;
  /** Figma: Role (User menu item). */
  role: string;
  /** One or two letters for the avatar. */
  initials: string;
}

/** The caption under the hairline in Figma. */
export const USER_SWITCHER_NOTE = 'Demo users: each role is a view of the same client, not a real sign-in.';

export interface UserSwitcherProps extends Omit<MenuProps, 'children' | 'label' | 'onSelect'> {
  /** The button that opens the switcher (in the app: the Header user button). It gets aria-haspopup, aria-expanded and aria-controls. */
  trigger: ReactElement;
  /** The users to switch between, in order. Figma shows three. */
  users: UserSwitcherUser[];
  /** Id of the signed-in user. That item is selected (Figma: State = Current), the others are not. */
  currentUserId?: string;
  /** Called with the chosen user's id. The switcher closes and focus returns to the trigger; switch the view at once. */
  onSelect?: (userId: string) => void;
  /** Overline above the users, and the menu's accessible name. Sentence case: the overline style shows it in capitals. */
  heading?: string;
  /** Caption under the hairline (Figma text by default). It is also the menu's accessible description. Pass null to leave it out. */
  note?: ReactNode;
}

/**
 * User switcher: a Menu with an overline, one User menu item per user, a hairline and a caption.
 * Keyboard, placement, Esc, outside click and focus return all come from Menu. Figma: Overlays / User switcher (203:228).
 */
export const UserSwitcher = forwardRef<HTMLDivElement, UserSwitcherProps>(function UserSwitcher(
  { users, currentUserId, onSelect, heading = 'Switch user', note = USER_SWITCHER_NOTE, className, ...rest },
  ref,
) {
  const noteId = useId();
  const hasNote = note !== null && note !== undefined && note !== false && note !== '';

  return (
    <Menu
      {...rest}
      ref={ref}
      label={heading}
      // Joins a description the caller passed with the note's id.
      aria-describedby={[rest['aria-describedby'], hasNote ? noteId : undefined].filter(Boolean).join(' ') || undefined}
      className={[styles.switcher, className].filter(Boolean).join(' ')}
    >
      {/* Shown for sighted users; screen readers get the same words as the menu's name. */}
      <div className={[styles.heading, 't-overline'].join(' ')} aria-hidden>
        {heading}
      </div>
      {users.map((user) => (
        <UserMenuItem
          key={user.id}
          name={user.name}
          role={user.role}
          initials={user.initials}
          selected={user.id === currentUserId}
          onSelect={() => onSelect?.(user.id)}
        />
      ))}
      {hasNote ? (
        <>
          <MenuSeparator />
          {/* A div, not a p: a paragraph role is not allowed inside role="menu". */}
          <div id={noteId} className={[styles.note, 't-caption'].join(' ')}>
            {note}
          </div>
        </>
      ) : null}
    </Menu>
  );
});
