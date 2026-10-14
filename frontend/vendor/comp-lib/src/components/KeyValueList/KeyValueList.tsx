import { forwardRef, type HTMLAttributes, type Key, type ReactNode } from 'react';
import styles from './KeyValueList.module.css';

/** Figma: Key-value pair (49:141). */
export interface KeyValueItem {
  /** Figma: Label. Caption, text-muted. */
  label: ReactNode;
  /** Figma: Value. Body-strong, text colour: the one strong item. Long values wrap; an identifier is never cut off. */
  value: ReactNode;
  /** React key. Defaults to the label when it is a string. */
  id?: Key;
}

export interface KeyValueListProps extends Omit<HTMLAttributes<HTMLDListElement>, 'children'> {
  /** The pairs, in reading order. Figma: Key-value list holds Key-value pair instances. */
  items: KeyValueItem[];
}

/** Grid of label and value pairs: 6 columns from 1280px, 3 from 640px, 2 on phones. Styleguide: Key-value list. */
export const KeyValueList = forwardRef<HTMLDListElement, KeyValueListProps>(function KeyValueList(
  { items, className, ...rest },
  ref,
) {
  return (
    <dl {...rest} ref={ref} className={[styles.list, className].filter(Boolean).join(' ')}>
      {items.map((item, index) => (
        <div key={item.id ?? (typeof item.label === 'string' ? item.label : index)} className={styles.pair}>
          <dt className={[styles.label, 't-caption'].join(' ')}>{item.label}</dt>
          <dd className={[styles.value, 't-body-strong'].join(' ')}>{item.value}</dd>
        </div>
      ))}
    </dl>
  );
});
