import { forwardRef, type ButtonHTMLAttributes, type MouseEvent } from 'react';
import { FileText } from 'lucide-react';
import { Tag } from '../Tag';
import styles from './SourceReference.module.css';

export type SourceReferenceLayout = 'inline' | 'row';

export interface SourceReferenceProps extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, 'children' | 'type' | 'onClick'> {
  /**
   * Opens the Source dialog with this record. A source reference is a button (`type="button"`), not a link: the app keeps
   * the dialog's open state and renders `<SourceDialog open … />` for this record. Focus is on this button when it is called
   * (also in Safari, which does not focus a pressed button), so the dialog returns focus here when it closes.
   */
  onOpen: (event: MouseEvent<HTMLButtonElement>) => void;
  /** Figma: Layout. Inline = after a sentence or item (small link-styled button + date). Row = one row in a source list (record type, origin · date, optional Draft tag). */
  layout?: SourceReferenceLayout;
  /** Figma: Label (Row) and Inline label (Inline). The record type, the same words in both layouts ("Meeting note"). Figma keeps two properties only because the layouts use different text styles; code sets the style from the layout. */
  label: string;
  /** Figma: Date. Content format "12 Oct 2026" (styleguide Content). Never broken inside. */
  date: string;
  /** Figma: Origin. "Health care" or "Social care", so social-care context is never read as a diagnosis. Shown in Row. In Inline it is not shown, but it is read out as part of the button name when given. */
  origin?: string;
  /** Figma: Draft. For draft records (decision or plan drafts). Inline shows "· Draft", Row shows the Draft tag. A draft is never shown as settled. */
  draft?: boolean;
  /** Figma: Show bottom rule. Row only: the 1px hairline under the row. Turn it off on the last row of a list. */
  showBottomRule?: boolean;
  /** Inline only. Turn on when the reference stands on its own (in a list item or a column), not inside a sentence: its invisible hit area is then 44 high in comfortable density (styleguide Accessibility, 2.5.8). Leave it off inside a sentence: the WCAG inline exception applies and the hit area stays 24. No visual change either way. */
  standalone?: boolean;
}

/* Real spaces sit between the parts ({' '}), so the button name reads "Nurse visit Health care 2 Mar 2026" whatever the
   layout. Inside a flex box a space-only text run is not rendered, so the layout does not change. */

/**
 * Source reference: a button that opens the one record an item or AI sentence came from, in the Source dialog.
 * NEW: composed from the styleguide's Link, caption and Tag rules; v2.3 "Source reference and dialog".
 * Figma: Data display / Source reference (165:253).
 */
export const SourceReference = forwardRef<HTMLButtonElement, SourceReferenceProps>(function SourceReference(
  { onOpen, layout = 'inline', label, date, origin, draft = false, showBottomRule = true, standalone = false, className, ...rest },
  ref,
) {
  const handleClick = (event: MouseEvent<HTMLButtonElement>) => {
    // Safari does not focus a button on a mouse press. Focus it, so the dialog finds it and returns focus here on close.
    if (document.activeElement !== event.currentTarget) event.currentTarget.focus();
    onOpen(event);
  };

  if (layout === 'row') {
    return (
      <button
        {...rest}
        ref={ref}
        type="button"
        aria-haspopup="dialog"
        className={[styles.reset, styles.row, showBottomRule ? styles.rule : undefined, className].filter(Boolean).join(' ')}
        onClick={handleClick}
      >
        <span className={styles.source}>
          <span className={styles.rowIcon} aria-hidden>
            <FileText size={16} strokeWidth={1.5} />
          </span>
          <span className={styles.rowText}>
            <span className={[styles.label, 't-control'].join(' ')}>{label}</span>{' '}
            <span className={[styles.meta, 't-caption'].join(' ')}>
              {origin ? (
                <>
                  <span className={styles.part}>{origin}</span>{' '}
                  <span aria-hidden>·</span>{' '}
                </>
              ) : null}
              <span className={styles.date}>{date}</span>
            </span>
          </span>
        </span>
        {draft ? <>{' '}<Tag className={styles.draftTag}>Draft</Tag></> : null}
      </button>
    );
  }

  return (
    <button
      {...rest}
      ref={ref}
      type="button"
      aria-haspopup="dialog"
      className={[styles.reset, styles.inline, standalone ? styles.standalone : undefined, className].filter(Boolean).join(' ')}
      onClick={handleClick}
    >
      <span className={styles.inlineMain}>
        <span className={styles.inlineIcon} aria-hidden>
          <FileText size={12} strokeWidth={2} />
        </span>
        <span className={[styles.label, 't-tag'].join(' ')}>{label}</span>
      </span>{' '}
      {/* Origin is not shown inline, but the button name still says it (Figma: name = record type + origin + date). */}
      {origin ? <><span className="visually-hidden">{origin}</span>{' '}</> : null}
      <span className={[styles.date, 't-caption'].join(' ')}>{date}</span>
      {draft ? (
        <>
          {' '}
          <span className={[styles.draft, 't-tag'].join(' ')}>
            <span aria-hidden>·</span> Draft
          </span>
        </>
      ) : null}
    </button>
  );
});
