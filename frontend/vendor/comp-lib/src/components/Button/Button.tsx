import { forwardRef, type ButtonHTMLAttributes, type MouseEvent, type ReactNode } from 'react';
import { Loader } from 'lucide-react';
import styles from './Button.module.css';

export type ButtonVariant = 'primary' | 'secondary' | 'danger';
export type ButtonSize = 'sm' | 'md' | 'lg';

export interface ButtonProps extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, 'disabled'> {
  /** Figma: Variant. Primary = the one main action of a bar or block. Danger always opens a typed confirmation. */
  variant?: ButtonVariant;
  /** Figma: Size. sm 28, md 36, lg 44. In comfortable density every size is 44 high. */
  size?: ButtonSize;
  /** Figma: Show icon + Icon. A 16px leading icon, e.g. a lucide icon at size 16. */
  icon?: ReactNode;
  /** Keeps the label and swaps the leading icon for a spinner. Not clickable: sets aria-busy and aria-disabled, but keeps the variant colours. */
  loading?: boolean;
  /** Muted text, line border. Stays focusable (aria-disabled) so a hint or tooltip can say why. */
  disabled?: boolean;
  /** Figma: Label. Verb + object, sentence case ("Save changes"). */
  children: ReactNode;
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = 'secondary', size = 'md', icon, loading = false, disabled = false, type = 'button', className, onClick, children, ...rest },
  ref,
) {
  const inactive = disabled || loading;
  const handleClick = (event: MouseEvent<HTMLButtonElement>) => {
    if (inactive) {
      event.preventDefault();
      event.stopPropagation(); // a disabled button inside a clickable row or card must not trigger it
      return;
    }
    onClick?.(event);
  };
  const leading = loading ? <Loader className={styles.spinner} size={16} strokeWidth={1.5} aria-hidden /> : icon;

  return (
    <button
      {...rest}
      ref={ref}
      type={type}
      className={[styles.button, styles[variant], styles[size], 't-control', className].filter(Boolean).join(' ')}
      aria-disabled={inactive || undefined}
      aria-busy={loading || undefined}
      onClick={handleClick}
    >
      {leading ? <span className={styles.icon} aria-hidden>{leading}</span> : null}
      <span>{children}</span>
    </button>
  );
});
