import { forwardRef, type HTMLAttributes, type MouseEventHandler, type ReactNode } from 'react';
import { Bell, ChevronDown } from 'lucide-react';
import { Avatar } from '../Avatar';
import { CountBadge } from '../CountBadge';
import { IconButton } from '../IconButton';
import { UserSwitcher, type UserSwitcherProps } from '../UserSwitcher';
import styles from './Header.module.css';

export interface HeaderNotifications {
  /** Accessible name of the bell button. It carries the meaning of the count, e.g. "Notifications, 3 new". */
  label: string;
  /** Figma: Show count. Number of new items, shown as a neutral count badge on the bell. 0 or left out shows no badge. */
  count?: number;
  /** Opens the notifications. */
  onClick?: MouseEventHandler<HTMLButtonElement>;
}

export interface HeaderUser {
  /** Figma: User name. Shown next to the avatar in the control role. */
  name: string;
  /** One or two letters for the avatar. */
  initials: string;
  /** Figma: User role. Shown under the name in caption, text-muted. Code only shows it when given (Figma default "Doctor"). */
  role?: string;
}

/** The User switcher the user button opens: everything but its trigger, which the Header provides. */
export type HeaderUserSwitcher = Omit<UserSwitcherProps, 'trigger'>;

export interface HeaderProps extends Omit<HTMLAttributes<HTMLElement>, 'title'> {
  /** Figma: Page title. The page's one h1, in the page-title role (title-lg on phones). */
  title: ReactNode;
  /** Figma: Show breadcrumb. A `Breadcrumb` element, shown above the title. Leave it out on top-level pages. */
  breadcrumb?: ReactNode;
  /** The header search: a `Search` element (min width 240). */
  search?: ReactNode;
  /** The notifications icon button (bell) with an optional count badge. */
  notifications?: HeaderNotifications;
  /** The signed-in user: avatar, name and (optional) role. Without `userSwitcher` it is plain text, not a button. */
  user?: HeaderUser;
  /**
   * Makes the user area a button (aria-haspopup="menu", aria-expanded, chevron-down) that opens this User switcher,
   * lined up with the button's end edge. Needs `user`. Keep `user` in step with `currentUserId` in `onSelect`.
   */
  userSwitcher?: HeaderUserSwitcher;
}

/** Page header: breadcrumb and page title on the left; search, notifications and user on the right. Styleguide: Header, App shell. */
export const Header = forwardRef<HTMLElement, HeaderProps>(function Header(
  { title, breadcrumb, search, notifications, user, userSwitcher, className, ...rest },
  ref,
) {
  const hasTools = Boolean(search || notifications || user);

  const userContent = user ? (
    <>
      <Avatar initials={user.initials} />
      <span className={styles.userText}>
        <span className={[styles.userName, 't-control'].join(' ')}>{user.name}</span>
        {/* A hidden comma, so the name reads "User A, Doctor" and the stacked spans never run together. */}
        {user.role ? <span className="visually-hidden">, </span> : null}
        {user.role ? <span className={[styles.userRole, 't-caption'].join(' ')}>{user.role}</span> : null}
      </span>
    </>
  ) : null;

  return (
    <header {...rest} ref={ref} className={[styles.header, className].filter(Boolean).join(' ')}>
      <div className={styles.titleBlock}>
        {breadcrumb}
        <h1 className={[styles.title, 't-page-title'].join(' ')}>{title}</h1>
      </div>
      {hasTools ? (
        <div className={styles.tools}>
          {search ? <span className={styles.search}>{search}</span> : null}
          {notifications ? (
            <span className={styles.notifications}>
              <IconButton icon={<Bell size={16} strokeWidth={1.5} />} label={notifications.label} onClick={notifications.onClick} />
              {/* The badge is decoration: the count is in the button label. It never takes the click. */}
              <CountBadge className={styles.badge} count={notifications.count ?? 0} aria-hidden />
            </span>
          ) : null}
          {user && userSwitcher ? (
            <UserSwitcher
              align="end"
              {...userSwitcher}
              trigger={
                // The name comes from the visible text ("User A, Doctor"), so it always matches what people see
                // (WCAG 2.5.3, axe label-content-name-mismatch). Menu adds aria-haspopup, aria-expanded and aria-controls.
                <button type="button" className={[styles.user, styles.userButton].join(' ')}>
                  {userContent}
                  <ChevronDown className={styles.chevron} size={16} strokeWidth={1.5} aria-hidden />
                </button>
              }
            />
          ) : user ? (
            <span className={styles.user}>{userContent}</span>
          ) : null}
        </div>
      ) : null}
    </header>
  );
});
