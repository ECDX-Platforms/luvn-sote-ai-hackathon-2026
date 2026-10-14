/**
 * Y-axis rule for Result series (Figma description, updated 13 Oct 2026; it matches the Figma picture 47.0–49.5):
 * 1. range = highest − lowest value.
 * 2. step = the smallest of 1, 2 or 5 × 10^k such that range ÷ step ≤ 4.
 * 3. axis min = lowest rounded down to the step; axis max = highest rounded up to the step.
 * 4. If a value sits exactly on the axis min or max, that end moves one step outward, so no point lies on a gridline.
 * 5. All values equal: range = one unit of the last decimal shown, then steps 2–4.
 * Labels use the step's decimals. The axis never starts at zero unless the rule lands there.
 */
export interface ResultSeriesAxis {
  min: number;
  max: number;
  step: number;
  /** Decimals of the axis labels: the step's decimals. */
  decimals: number;
}

/** Decimals written in a number as text: "47.90" → 2, "126" → 0, "1e-7" → 7. */
export function decimalsOf(text: string): number {
  const [mantissa, exponent] = text.toLowerCase().split('e');
  const fraction = mantissa.split('.')[1]?.length ?? 0;
  return Math.max(0, fraction - Number(exponent ?? 0));
}

/** Rounds away floating-point noise (0.1 + 0.2 → 0.3) at a given number of decimals. */
const clean = (n: number, decimals: number) => Number(n.toFixed(Math.min(Math.max(decimals, 0), 100)));

/** Used when the rule cannot run: no finite values, a range too large for a number, or a step too small to count in. */
function fallbackAxis(lo: number, hi: number, decimals: number): ResultSeriesAxis {
  if (!Number.isFinite(lo) || !Number.isFinite(hi)) return { min: 0, max: 1, step: 1, decimals: 0 };
  // hi / 2 - lo / 2 stays finite even when hi - lo would overflow (e.g. -1e308 and 1e308).
  const half = hi / 2 - lo / 2;
  if (half > 0) return { min: lo, max: hi, step: half, decimals };
  // All values equal and nothing finite to widen by: one unit either side, or the value itself if it cannot move.
  const pad = Math.max(Math.abs(lo) * 1e-6, 1);
  const min = lo - pad;
  const max = lo + pad;
  return Number.isFinite(min) && Number.isFinite(max) && min < max ? { min, max, step: pad, decimals } : { min: lo, max: lo, step: 1, decimals };
}

/**
 * The axis for a set of values. `lastDecimals` = decimals of the most precise value shown (used only when all values are equal).
 * Non-finite values are skipped. Always terminates and always returns finite min <= max that include every finite value.
 */
export function resultSeriesAxis(values: number[], lastDecimals = 0): ResultSeriesAxis {
  const finite = values.filter((v) => Number.isFinite(v));
  const shown = Number.isFinite(lastDecimals) ? Math.min(Math.max(Math.round(lastDecimals), 0), 20) : 0;
  if (finite.length === 0) return fallbackAxis(NaN, NaN, 0);
  const lo = Math.min(...finite);
  const hi = Math.max(...finite);
  const range = hi > lo ? hi - lo : 10 ** -shown;
  if (!Number.isFinite(range) || !(range > 0)) return fallbackAxis(lo, hi, 0);

  // Smallest step of 1, 2 or 5 × 10^k with range / step ≤ 4. Start one decade below range / 4: at most two decades to walk.
  const k0 = Math.floor(Math.log10(range / 4)) - 1;
  let found: { k: number; multiplier: number } | undefined;
  for (let k = k0; k <= k0 + 2 && !found; k += 1) {
    const multiplier = [1, 2, 5].find((m) => range / (m * 10 ** k) <= 4 + 1e-9);
    if (multiplier !== undefined) found = { k, multiplier };
  }
  if (!found) return fallbackAxis(lo, hi, shown);
  const decimals = Math.max(0, -found.k);
  if (decimals > 20) return fallbackAxis(lo, hi, 20); // labels cannot show a step this small
  const step = clean(found.multiplier * 10 ** found.k, decimals);
  if (!(step > 0) || !Number.isFinite(step)) return fallbackAxis(lo, hi, shown);

  // Count in whole steps. The tolerance is a millionth of a step (in value units, not in step counts), so it never grows past
  // a step for large values; it absorbs floating-point noise such as 0.1 + 0.2.
  const tolerance = step * 1e-6;
  let minSteps = Math.floor((lo + tolerance) / step);
  let maxSteps = Math.ceil((hi - tolerance) / step);
  if (Math.abs(lo - minSteps * step) <= tolerance) minSteps -= 1; // a value on the min: move it one step outward
  if (Math.abs(hi - maxSteps * step) <= tolerance) maxSteps += 1; // a value on the max: move it one step outward

  const min = clean(minSteps * step, decimals);
  const max = clean(maxSteps * step, decimals);
  // Safety net: the result must be finite, ordered and include every value; otherwise use the plain range.
  if (!Number.isFinite(min) || !Number.isFinite(max) || !(min < max) || min > lo || max < hi) return fallbackAxis(lo, hi, shown);
  return { min, max, step, decimals };
}
