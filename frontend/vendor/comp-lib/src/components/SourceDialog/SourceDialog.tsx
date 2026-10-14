import {
  forwardRef,
  useId,
  useLayoutEffect,
  useRef,
  type MouseEvent,
  type PointerEvent,
  type ReactNode,
} from 'react';
import { Lock } from 'lucide-react';
import { Button } from '../Button';
import { Dialog, type DialogProps } from '../Dialog';
import { Divider } from '../Divider';
import { Tag } from '../Tag';
import styles from './SourceDialog.module.css';

/** One section of the source record, as written in the record. */
export interface SourceDialogSection {
  /** The section heading, in sentence case ("Reason for contact"). Shown as an overline (uppercase from CSS). */
  heading: string;
  /** The section's text as written in the record, in body. Leave it out when everything in this section is hidden. */
  content?: ReactNode;
  /**
   * Figma: Content = With hidden. This section's list holds hidden sensitive information: the Hidden item row
   * ("Sensitive information hidden", "Ask to see") is shown last in the section, in place of the hidden content.
   * One row per section, however many items are hidden. Never set it for safety information (allergies, side effects).
   */
  hasHidden?: boolean;
}

export interface SourceDialogProps
  extends Omit<DialogProps, 'title' | 'children' | 'actions' | 'size' | 'closeOnEscape'> {
  /** Figma: Title, first part. The record type, the same words as on the Source reference that opened it ("Meeting note"). */
  recordType: string;
  /** Figma: Title, second part. The record date in the content format ("2 Mar 2026"). Title reads "record type · date". */
  date: string;
  /** Figma: Subtitle. Whose record it is. The subtitle reads "Source record · <client>". */
  client: string;
  /** Figma: Content = Draft. Shows the neutral Draft tag next to the subtitle. A draft is never shown as settled. */
  draft?: boolean;
  /** The record's own sections, in the record's order. Only this record's content: never totals across records. */
  sections: readonly SourceDialogSection[];
  /**
   * Called with the section heading when "Ask to see" is pressed in a Hidden item row. In the same handler, close this
   * dialog and open the Access request dialog; after the request, open this dialog again with the opened content.
   */
  onAskToSee?: (heading: string) => void;
  /** The close button's label. */
  closeLabel?: string;
}

/**
 * Source dialog: shows the one source record a Source reference points to, so it can be read without leaving the page.
 * NEW (redesign v2.3). Built on Dialog md (640). Figma: Overlays / Source dialog (220:492).
 * Styleguide: Source reference and dialog, Dialog and typed confirmation, Typography (title-md, caption, overline, body, 75 characters).
 */
export const SourceDialog = forwardRef<HTMLDialogElement, SourceDialogProps>(function SourceDialog(
  { open, onClose, recordType, date, client, draft = false, sections, onAskToSee, closeLabel = 'Close', onPointerDown, onClick, ...rest },
  ref,
) {
  const baseId = useId();
  const closeRef = useRef<HTMLButtonElement | null>(null);
  /** The press started on the scrim. A drag that starts inside (selecting text) and ends on the scrim must not close it. */
  const pressOnScrim = useRef(false);

  // Focus goes to Close on open (Figma), not to the first control (which can be "Ask to see").
  // Runs after Dialog's own layout effect (children first), so showModal has already run.
  useLayoutEffect(() => {
    if (open) closeRef.current?.focus();
  }, [open]);

  /** True when the pointer is on the scrim: the event targets the dialog element itself, outside its box. */
  const onScrim = (event: MouseEvent<HTMLDialogElement>) => {
    if (event.target !== event.currentTarget) return false;
    const box = event.currentTarget.getBoundingClientRect();
    return event.clientX < box.left || event.clientX > box.right || event.clientY < box.top || event.clientY > box.bottom;
  };

  const handlePointerDown = (event: PointerEvent<HTMLDialogElement>) => {
    onPointerDown?.(event);
    pressOnScrim.current = onScrim(event);
  };

  // A click on the scrim closes it (Figma). Nothing can be lost: the dialog only shows a record.
  const handleClick = (event: MouseEvent<HTMLDialogElement>) => {
    onClick?.(event);
    const scrim = pressOnScrim.current && onScrim(event);
    pressOnScrim.current = false;
    if (scrim) onClose();
  };

  return (
    <Dialog
      {...rest}
      ref={ref}
      open={open}
      onClose={onClose}
      size="md"
      title={`${recordType} · ${date}`}
      onPointerDown={handlePointerDown}
      onClick={handleClick}
      actions={<Button ref={closeRef} onClick={onClose}>{closeLabel}</Button>}
    >
      <div className={styles.record}>
        <p className={styles.subtitle}>
          <span className={[styles.subtitleText, 't-caption'].join(' ')}>
            Source record <span aria-hidden>·</span> {client}
          </span>
          {draft ? <>{' '}<Tag className={styles.draftTag}>Draft</Tag></> : null}
        </p>
        {sections.map((section, index) => {
          const headingId = `${baseId}-section-${index}`;
          return (
            <section
              key={`${index}-${section.heading}`}
              className={[styles.section, section.hasHidden ? styles.withHidden : undefined].filter(Boolean).join(' ')}
              aria-labelledby={headingId}
            >
              <Divider className={styles.rule} />
              <h3 id={headingId} className={[styles.heading, 't-overline'].join(' ')}>{section.heading}</h3>
              {section.content != null ? <div className={[styles.body, 'prose'].join(' ')}>{section.content}</div> : null}
              {section.hasHidden ? (
                <div className={styles.hiddenRow}>
                  <span className={styles.hiddenMessage}>
                    <span className={styles.hiddenIcon} aria-hidden>
                      <Lock size={16} strokeWidth={1.5} />
                    </span>
                    <span className={styles.hiddenText}>Sensitive information hidden</span>
                  </span>
                  <Button
                    size="sm"
                    className={styles.askButton}
                    aria-label={`Ask to see hidden information in ${section.heading}`}
                    onClick={() => onAskToSee?.(section.heading)}
                  >
                    Ask to see
                  </Button>
                </div>
              ) : null}
            </section>
          );
        })}
      </div>
    </Dialog>
  );
});
