import { forwardRef } from 'react';
import { Avatar } from '../Avatar';
import { MenuItem, type MenuItemProps } from '../Menu';
import { Tag } from '../Tag';
import styles from './UserMenuItem.module.css';

export interface UserMenuItemProps
  extends Omit<MenuItemProps, 'children' | 'variant' | 'icon' | 'selected' | 'disabled' | 'disabledReason' | 'role'> {
  /** Figma: Name. The user's name, in the control role (body-strong when selected). Long names wrap. */
  name: string;
  /** Figma: Role. The user's role, in caption text-muted under the name. */
  role: string;
  /** One or two letters for the 28px avatar (Figma: the Avatar's Initials, overridden per instance). */
  initials: string;
  /**
   * Figma: State = Current. The signed-in user: name in body-strong, 2px selection bar on the left and a neutral
   * Tag "Current". Sets aria-checked="true". Every other item in the switcher is false.
   */
  selected?: boolean;
}

/**
 * One user in a User switcher (role="menuitemradio", aria-checked). Built on MenuItem, so it takes part in the
 * Menu's keyboard behaviour and closes the menu when chosen. Figma: Overlays / User menu item (203:227).
 */
export const UserMenuItem = forwardRef<HTMLButtonElement, UserMenuItemProps>(function UserMenuItem(
  { name, role, initials, selected = false, className, 'aria-label': ariaLabel, ...rest },
  ref,
) {
  return (
    <MenuItem
      {...rest}
      ref={ref}
      selected={selected}
      // One clear name ("User A, Doctor"): the stacked spans would otherwise run together in some screen readers.
      // aria-checked carries Current, so the Tag is not repeated in the name.
      aria-label={ariaLabel ?? `${name}, ${role}`}
      className={[styles.item, className].filter(Boolean).join(' ')}
    >
      <span className={styles.content}>
        <span className={styles.person}>
          <Avatar initials={initials} />
          <span className={styles.text}>
            <span className={[styles.name, selected ? 't-body-strong' : 't-control'].join(' ')}>{name}</span>
            <span className={[styles.role, 't-caption'].join(' ')}>{role}</span>
          </span>
        </span>
        {selected ? <Tag className={styles.current}>Current</Tag> : null}
      </span>
    </MenuItem>
  );
});
