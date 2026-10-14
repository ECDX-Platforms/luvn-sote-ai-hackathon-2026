import { forwardRef, type HTMLAttributes } from 'react';
import styles from './Avatar.module.css';

export interface AvatarProps extends Omit<HTMLAttributes<HTMLSpanElement>, 'children'> {
  /** Figma: Initials. One or two letters. */
  initials: string;
  /** The person's name, read out instead of the initials. Leave it out when the name is shown next to the avatar: the avatar is then hidden from screen readers, so the name is not read twice. */
  label?: string;
}

/** Person marker: 28px circle, avatar fill, initials. Styleguide: Shapes → Avatar, Header (user). Not interactive. */
export const Avatar = forwardRef<HTMLSpanElement, AvatarProps>(function Avatar(
  { initials, label, className, ...rest },
  ref,
) {
  return (
    <span
      {...rest}
      ref={ref}
      className={[styles.avatar, 't-initials', className].filter(Boolean).join(' ')}
      role={label ? 'img' : undefined}
      aria-label={label}
      aria-hidden={label ? undefined : true}
    >
      {initials}
    </span>
  );
});
