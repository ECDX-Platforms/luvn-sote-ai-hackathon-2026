/**
 * Places a position: fixed overlay (tooltip, menu) next to its trigger. Shared by Tooltip and Menu.
 * Flips to the other side when there is no room, and keeps the overlay `gap` inside the viewport (both axes).
 * A transformed ancestor makes position: fixed relative to that ancestor instead of the viewport
 * (Storybook's Docs page has one): the function measures where top 0, left 0 lands and corrects for it.
 * Known limitation: this handles translation only. Under transform: scale() the correction (viewport pixels) is applied
 * in the ancestor's scaled pixels, so the overlay is misplaced. Not supported until fixed (placeFixed.test.ts pins it).
 */
export type FixedSide = 'top' | 'bottom';
export type FixedAlign = 'center' | 'start' | 'end';

export function placeFixed(
  overlay: HTMLElement,
  trigger: HTMLElement,
  { side, align, gap }: { side: FixedSide; align: FixedAlign; gap: number },
): FixedSide {
  overlay.style.top = '0px';
  overlay.style.left = '0px';
  const origin = overlay.getBoundingClientRect();
  const box = trigger.getBoundingClientRect();
  const { width, height } = origin;
  const viewW = document.documentElement.clientWidth;
  const viewH = document.documentElement.clientHeight;

  const roomAbove = box.top - gap - height >= gap;
  const roomBelow = box.bottom + gap + height <= viewH - gap;
  let placed = side;
  if (placed === 'top' && !roomAbove && roomBelow) placed = 'bottom';
  if (placed === 'bottom' && !roomBelow && roomAbove) placed = 'top';
  const wantedTop = placed === 'top' ? box.top - gap - height : box.bottom + gap;
  // No room on either side (a long menu): stay inside the screen. CSS caps the overlay's height to the screen.
  const top = Math.min(Math.max(wantedTop, gap), Math.max(gap, viewH - gap - height));

  // In right-to-left text, start is the right edge.
  const rtl = getComputedStyle(trigger).direction === 'rtl';
  const startEdge = rtl ? box.right - width : box.left;
  const endEdge = rtl ? box.left : box.right - width;
  const wanted = align === 'center' ? box.left + box.width / 2 - width / 2 : align === 'start' ? startEdge : endEdge;
  const left = Math.min(Math.max(wanted, gap), Math.max(gap, viewW - gap - width));

  overlay.style.top = `${top - origin.top}px`;
  overlay.style.left = `${left - origin.left}px`;
  overlay.dataset.placement = placed;
  return placed;
}

/** Reads a length custom property (e.g. --tooltip-gap: var(--space-xs)) as pixels. */
export function readGap(element: HTMLElement, property: string): number {
  return parseFloat(getComputedStyle(element).getPropertyValue(property)) || 0;
}
