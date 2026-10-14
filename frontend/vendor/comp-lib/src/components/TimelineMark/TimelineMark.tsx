import { forwardRef, type CSSProperties, type HTMLAttributes } from 'react';
import styles from './TimelineMark.module.css';

/** Figma: Kind. The shape says the kind of record. */
export type TimelineMarkKind =
  | 'running'
  | 'as-needed'
  | 'paused'
  | 'single-record'
  | 'visit'
  | 'remote-visit'
  | 'urgent-visit'
  | 'contacts-per-week';

/** The kinds drawn as a bar from a start to an end. */
export const TIMELINE_BAR_KINDS: readonly TimelineMarkKind[] = ['running', 'as-needed', 'paused'];

export interface TimelineMarkProps extends Omit<HTMLAttributes<HTMLSpanElement>, 'children'> {
  /** Figma: Kind. Running = 8px bar (data). As needed = 8px outlined bar (data border, surface fill). Paused = hatched bar (text-muted). Single record = 2×20 tick, Visit = 10px dot, Remote visit = 10px ring, Urgent visit = 10px square (all in text). Contacts per week = 3px bar (data) whose height shows the week's minutes. */
  kind?: TimelineMarkKind;
  /**
   * Where the mark sits in a Timeline row track, in months from the start of the track (64px per month). With it, the
   * mark is placed absolutely: a point is centred on it, a bar starts at it, a contacts bar starts at its week. Leave it
   * out for a free-standing mark (a legend key or a story): bars then fill their container's width.
   */
  at?: number;
  /** Bars only (running, as needed, paused), with `at`: where the bar ends, in months from the start of the track. A bar that runs past today ends at today: pass today's position, never a planned end. */
  until?: number;
  /** Contacts per week only: this week's recorded minutes as a share of the busiest week (0 to 1). The bar is 24px at 1. A week with 0 minutes draws nothing. */
  share?: number;
  /** Legend keys use Figma's smaller sizes: bars 24 wide, the tick 14 high, the contacts bar 12 high. */
  size?: 'track' | 'legend';
}

/**
 * Timeline mark: one shape in a Timeline row track. NEW: composed from the styleguide's Timeline (client view) and
 * Charts rules. Figma: Data display / Timeline mark (239:2969). Decorative (`aria-hidden`): the row button and the legend
 * carry the meaning.
 */
export const TimelineMark = forwardRef<HTMLSpanElement, TimelineMarkProps>(function TimelineMark(
  { kind = 'running', at, until, share, size = 'track', className, style, ...rest },
  ref,
) {
  const placed = at !== undefined && Number.isFinite(at);
  const bar = TIMELINE_BAR_KINDS.includes(kind);
  const vars: Record<string, number> = {};
  if (placed) vars['--at'] = at;
  if (placed && bar) vars['--until'] = Math.max(until ?? at, at);
  if (kind === 'contacts-per-week') vars['--share'] = Math.min(Math.max(share ?? 1, 0), 1);
  if (kind === 'contacts-per-week' && share !== undefined && share <= 0) return null;

  return (
    <span
      {...rest}
      ref={ref}
      aria-hidden
      className={[
        styles.mark,
        styles[kind],
        placed ? styles.placed : undefined,
        size === 'legend' ? styles.legend : undefined,
        className,
      ].filter(Boolean).join(' ')}
      style={{ ...vars, ...style } as CSSProperties}
    />
  );
});
