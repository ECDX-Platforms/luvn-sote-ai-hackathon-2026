import { forwardRef, type HTMLAttributes, type ReactNode } from 'react';
import styles from './NowCell.module.css';

export type NowCellValueKind = 'word' | 'number';

interface NowCellBaseProps extends Omit<HTMLAttributes<HTMLDivElement>, 'children'> {
  /** Figma: Label. What the fact is ("Allergies"). Body, text-muted. Wraps when long. */
  label: ReactNode;
  /**
   * Figma: Value (text). The fact itself: a source value or a plain word ("None known", "2"), never a computed status.
   * Text colour. Style follows `valueKind`.
   */
  value: ReactNode;
  /**
   * Figma: Detail. One short line under the value ("As reported by the patient", "since 4 Mar 2026"). Caption, text-muted.
   * Replaced by the hidden-information line when `hiddenIn` is set.
   */
  detail?: ReactNode;
}

/** A word or a short list of names. Only a word value can say that something is hidden. */
interface NowCellWordProps extends NowCellBaseProps {
  /** Figma: Value = Word | Number. Word (default): title-lg (24/32, 600). Number: metric (35/40, 500, tabular figures). */
  valueKind?: 'word';
  /**
   * The places in this topic that hold hidden information, e.g. `['Medicines']`. When set (not empty), the detail line
   * reads "Hidden information in <list>" instead of `detail`. A count must never leave out hidden items, so a cell
   * with hidden information shows names (a word value), never a number: TypeScript rejects `hiddenIn` with
   * `valueKind="number"`. Never set it on safety facts (allergies, reported side effects): they are never hidden.
   */
  hiddenIn?: readonly string[];
}

/** A number (a count). Not allowed when something in the topic is hidden. */
interface NowCellNumberProps extends NowCellBaseProps {
  /** Figma: Value = Word | Number. Word (default): title-lg (24/32, 600). Number: metric (35/40, 500, tabular figures). */
  valueKind: 'number';
  /** Not allowed with a number: when something is hidden, show names (`valueKind="word"`) instead of a count. */
  hiddenIn?: undefined;
}

export type NowCellProps = NowCellWordProps | NowCellNumberProps;

/** "Hidden information in A, B" (Figma and styleguide v2.2 wording). */
const hiddenInformationText = (places: readonly string[]) => `Hidden information in ${places.join(', ')}`;

/**
 * NEW. Now cell: one key fact for the first look, in the Now strip. Label, value, detail. Not interactive.
 * Styleguide v2.2: Now strip (first look), Typography → Roles (title-lg, metric).
 */
export const NowCell = forwardRef<HTMLDivElement, NowCellProps>(function NowCell(
  { label, value, valueKind = 'word', detail, hiddenIn, className, ...rest },
  ref,
) {
  const hidden = hiddenIn != null && hiddenIn.length > 0;
  if (hidden && valueKind === 'number') {
    // Only reachable from untyped code. The count may leave out the hidden items: say so loudly.
    console.error('NowCell: hiddenIn is set on a number value. A count must never leave out hidden items: show names (valueKind="word") instead.');
  }
  const detailLine = hidden ? hiddenInformationText(hiddenIn) : detail;

  return (
    <div {...rest} ref={ref} className={[styles.cell, className].filter(Boolean).join(' ')}>
      <p className={[styles.label, 't-body', 'muted'].join(' ')}>{label}</p>
      <p className={valueKind === 'number' ? [styles.number, 't-metric'].join(' ') : [styles.word, 't-title-lg'].join(' ')}>
        {value}
      </p>
      {detailLine ? <p className={[styles.detail, 't-caption'].join(' ')}>{detailLine}</p> : null}
    </div>
  );
});
