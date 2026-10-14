import { forwardRef, type AnchorHTMLAttributes, type ReactNode } from 'react';
import styles from './Link.module.css';

export type LinkSize = 'md' | 'sm';

export interface LinkProps extends AnchorHTMLAttributes<HTMLAnchorElement> {
  /** Where the link goes. A link always has an href; for an action without a destination, use Button. */
  href: string;
  /** Figma: Link (md) or Link small (sm). md = control text 14/20 500, for running text and standalone links. sm = tag text 12/16 500, for caption lines and breadcrumb parent levels. In comfortable density md is 17/24 and sm 14/20. */
  size?: LinkSize;
  /** Figma: Label. Says where the link goes ("View all items"), never "click here". */
  children: ReactNode;
}

/** Text link: link colour, weight 500, always underlined. Styleguide: Colors → link, Content, Accessibility 1.4.1. */
export const Link = forwardRef<HTMLAnchorElement, LinkProps>(function Link(
  { href, size = 'md', className, children, ...rest },
  ref,
) {
  return (
    <a
      {...rest}
      ref={ref}
      href={href}
      className={[styles.link, size === 'sm' ? 't-tag' : 't-control', className].filter(Boolean).join(' ')}
    >
      {children}
    </a>
  );
});
