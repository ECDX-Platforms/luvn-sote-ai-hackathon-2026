import { forwardRef, useId, type HTMLAttributes, type ReactNode } from 'react';
import { Check } from 'lucide-react';
import { Tag } from '../Tag';
import styles from './NotOnRecordLine.module.css';

export interface NotOnRecordLineProps extends Omit<HTMLAttributes<HTMLDivElement>, 'children'> {
  /**
   * Figma: State + the Tags. The kinds of record this role may see that this client does not have, in the page's
   * block order: one neutral Tag per kind, short nouns ("Kind A"). With at least one kind the line is State=Missing;
   * with none (empty or left out) it is State=Complete. List only kinds the role is allowed to see: a kind that is cut
   * for the role is never listed (that would reveal the cut). A kind whose only records are hidden is not missing:
   * its block shows (with just the hidden row), so it is not listed here.
   */
  kinds?: ReactNode[];
}

/**
 * NEW. Not on record line: one line at the top of a client view that names the kinds of record this role could see
 * but this client does not have. Replaces empty blocks ("No lab results"). Missing: "Not on record" and one neutral
 * Tag per missing kind. Complete: a check and "Every kind of record this role sees is present". Not interactive.
 * Styleguide v2.3: Thin record page (pattern).
 */
export const NotOnRecordLine = forwardRef<HTMLDivElement, NotOnRecordLineProps>(function NotOnRecordLine(
  { kinds = [], className, ...rest },
  ref,
) {
  const labelId = useId();
  const missing = kinds.length > 0;

  return (
    <div {...rest} ref={ref} className={[styles.line, className].filter(Boolean).join(' ')}>
      {missing ? (
        <>
          <p id={labelId} className={[styles.label, 't-body-strong'].join(' ')}>Not on record</p>
          {/* The tags are a list for screen readers ("list, 3 items"). The list box is display: contents, so the label
              and the tags wrap together in one flex row, as in Figma. */}
          <ul className={styles.kinds} aria-labelledby={labelId} role="list">
            {kinds.map((kind, index) => (
              <li key={index} className={styles.kind}>
                <Tag>{kind}</Tag>
              </li>
            ))}
          </ul>
        </>
      ) : (
        <>
          <span className={styles.check} aria-hidden>
            <Check size={16} strokeWidth={1.5} aria-hidden />
          </span>
          <p className={[styles.complete, 't-caption'].join(' ')}>Every kind of record this role sees is present</p>
        </>
      )}
    </div>
  );
});
