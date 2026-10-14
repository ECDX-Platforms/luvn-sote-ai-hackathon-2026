import { forwardRef, useId, type HTMLAttributes, type ReactNode } from 'react';
import styles from './Block.module.css';

export type BlockEmphasis = 'white' | 'tint';
export type BlockHeadingLevel = 2 | 3 | 4;

export interface BlockProps extends Omit<HTMLAttributes<HTMLElement>, 'title'> {
  /** Figma: Title. Title-sm (17/24, 600, text colour), sentence case, short. Names the section for screen readers too. Styleguide v2.2: was overline. */
  title: ReactNode;
  /** Figma: Show action + Action link. A link or small buttons at the right of the title row. Wraps below the title when there is no room; each label must fit the column on its own. */
  actions?: ReactNode;
  /** Figma: Emphasis. White (default) or Tint (soft grouping, e.g. a draft). Inverse and Primary are not in Figma yet. */
  emphasis?: BlockEmphasis;
  /** Content runs edge to edge (e.g. a Metric row): no padding, only the title row keeps it. Styleguide: block--flush. */
  flush?: boolean;
  /** Heading level of the title. Default 2: blocks sit under the page's h1. */
  headingLevel?: BlockHeadingLevel;
  /** Figma: Content. Any text or component. */
  children?: ReactNode;
}

/** Section of a page: one topic, a title row, then content. Hairline at the bottom. Styleguide: Block (section), Emphasis states. */
export const Block = forwardRef<HTMLElement, BlockProps>(function Block(
  { title, actions, emphasis = 'white', flush = false, headingLevel = 2, className, children, ...rest },
  ref,
) {
  const titleId = useId();
  const Heading = `h${headingLevel}` as const;

  return (
    <section
      {...rest}
      ref={ref}
      className={[styles.block, styles[emphasis], flush && styles.flush, className].filter(Boolean).join(' ')}
      aria-labelledby={titleId}
    >
      <div className={styles.head}>
        <Heading id={titleId} className={[styles.title, 't-title-sm'].join(' ')}>{title}</Heading>
        {actions ? <div className={styles.actions}>{actions}</div> : null}
      </div>
      {children}
    </section>
  );
});
