/**
 * Sample fixed scales, from the styleguide (v2.3, Charts) and the Figma description. Every test has one fixed scale,
 * set once for that test and the same on every screen; the axis is never zoomed to fit the data.
 * These are SAMPLE VALUES FOR THE DEMO, NOT CLINICAL GUIDANCE: a professional must check them before real use.
 */
export interface ResultSeriesScale {
  /** Bottom gridline. */
  min: number;
  /** Top gridline. */
  max: number;
}

/** Sample scales by test, with the unit each one is in. Use: `scale={resultSeriesSampleScales.HbA1c}`. */
export const resultSeriesSampleScales = {
  HbA1c: { min: 30, max: 70, unit: 'mmol/mol' },
  'fP-Gluk': { min: 4, max: 10, unit: 'mmol/l' },
  'P-Krea': { min: 40, max: 130, unit: 'µmol/l' },
  'P-TSH': { min: 0, max: 6, unit: 'mU/l' },
} as const satisfies Record<string, ResultSeriesScale & { unit: string }>;
