import { forwardRef, type HTMLAttributes, type ReactNode } from 'react';
import { SourceReference } from '../SourceReference';
import { StatusTag, type StatusTagStatus } from '../StatusTag';
import { Tag } from '../Tag';
import styles from './CautionItem.module.css';

export type CautionItemKind = 'allergy' | 'side-effect' | 'none-known' | 'no-information';

/** The record the fact comes from, shown as an inline Source reference. */
export interface CautionItemSource {
  /** Opens this record in the Source dialog (Source reference `onOpen`). The caller keeps the dialog's open state. */
  onOpen: () => void;
  /** The record type ("Allergy record"). */
  label: string;
  /** The record date, content format ("3 Feb 2025"). */
  date: string;
  /** "Health care" or "Social care". Not shown inline, but read out as part of the button name. */
  origin?: string;
  /** The record is a draft. */
  draft?: boolean;
}

export interface CautionItemProps extends Omit<HTMLAttributes<HTMLDivElement>, 'children'> {
  /** Figma: Kind. Sets the tag, and its word is fixed per kind: Allergy (Status tag critical, "Allergy") = a confirmed allergy. Side effect (Status tag warning, "Side effect") = a reported reaction that is not a confirmed allergy. None known (neutral Tag, "None known") = the source says no known allergies. No information (Status tag info, "Not recorded") = the source has no information. */
  kind: CautionItemKind;
  /** Figma: Fact. "Substance: reaction" in body-strong, in the source's own terms. Every line names what it is about ("No known allergies", "No allergy information recorded"). */
  fact: ReactNode;
  /** Figma: Qualifier. Caption, text-muted, taken from the source's wording ("Reported side effect, not a confirmed allergy"). */
  qualifier?: ReactNode;
  /** The record the fact comes from. Left out = no source reference is shown (silent absence). */
  source?: CautionItemSource;
  /** Figma: Show bottom rule. The 1px hairline under the item. Turn it off on the last item of a list. */
  showBottomRule?: boolean;
  /** The tag word per kind, for another language. Each key defaults to the English (Figma) word. */
  labels?: Partial<CautionItemLabels>;
}

/** The fixed tag word per kind (Figma). "No information" and "None known" never look or read the same. */
export interface CautionItemLabels {
  /** Kind allergy: Status tag critical. */
  allergy: string;
  /** Kind side-effect: Status tag warning. */
  sideEffect: string;
  /** Kind none-known: neutral Tag. */
  noneKnown: string;
  /** Kind no-information: Status tag info. */
  notRecorded: string;
}

const DEFAULT_LABELS: CautionItemLabels = {
  allergy: 'Allergy',
  sideEffect: 'Side effect',
  noneKnown: 'None known',
  notRecorded: 'Not recorded',
};

/** Fixed Status tag level and label key per kind; none-known is the neutral Tag. */
const STATUS: Partial<Record<CautionItemKind, [StatusTagStatus, keyof CautionItemLabels]>> = {
  allergy: ['critical', 'allergy'],
  'side-effect': ['warning', 'sideEffect'],
  'no-information': ['info', 'notRecorded'],
};

/**
 * Caution item: one allergy or medicine-reaction fact, in the source's own terms, with its source.
 * NEW: composed from Status tag, Tag and Source reference. Figma: Data display / Caution item (176:830).
 */
export const CautionItem = forwardRef<HTMLDivElement, CautionItemProps>(function CautionItem(
  { kind, fact, qualifier, source, showBottomRule = true, labels, className, ...rest },
  ref,
) {
  const t = { ...DEFAULT_LABELS, ...labels };
  const status = STATUS[kind];
  return (
    <div
      {...rest}
      ref={ref}
      className={[styles.item, showBottomRule ? styles.rule : undefined, className].filter(Boolean).join(' ')}
    >
      <div className={styles.content}>
        <div className={styles.slot}>
          {status ? <StatusTag status={status[0]}>{t[status[1]]}</StatusTag> : <Tag>{t.noneKnown}</Tag>}
        </div>
        <div className={styles.lines}>
          <span className={[styles.line, 't-body-strong'].join(' ')}>{fact}</span>
          {qualifier ? <span className={[styles.line, 't-caption'].join(' ')}>{qualifier}</span> : null}
        </div>
      </div>
      {source ? (
        <SourceReference
          standalone
          className={styles.source}
          onOpen={source.onOpen}
          label={source.label}
          date={source.date}
          origin={source.origin}
          draft={source.draft}
        />
      ) : null}
    </div>
  );
});
