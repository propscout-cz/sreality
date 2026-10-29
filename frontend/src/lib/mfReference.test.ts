/* THE shape rule every MF surface renders by — the SPA card and, through the
 * same import, the extension panel and index badge (which have no test runner
 * of their own). Decided by the shape of the one result, never by its status.
 * The notes are stand-ins: the real sentences exist once, in the SQL. */
import { describe, expect, it } from 'vitest';

import { mfShape, mfYieldLine, mfYieldText, type ReferenceRent } from './mfReference';

const as = (o: object) => o as unknown as ReferenceRent;
const RANGE = {
  per_m2_min: 195, per_m2_max: 238, rent_min_czk: 14_625, rent_max_czk: 17_850,
  yield_min_pct: 3.44, yield_max_pct: 4.2,
};

describe('mfShape', () => {
  it('reads a stored breakdown with no status key as a value', () => {
    const ref = as({ monthly_rent_czk: 17_700, total_per_m2: 236, area_m2: 75 });
    expect(mfShape(ref)).toEqual({ kind: 'value', ref });
  });

  it('reads a value by its rent, whatever status it carries', () => {
    const ref = as({ status: 'ok', note: null, monthly_rent_czk: 17_700 });
    expect(mfShape(ref).kind).toBe('value');
  });

  it('reads a range with its note, and a range without one', () => {
    const note = '(the range note, from SQL)';
    const ref = as({ status: 'territory_coarse', note, monthly_rent_czk: null, range: RANGE });
    expect(mfShape(ref)).toEqual({ kind: 'range', ref, range: RANGE, note });
    expect(mfShape(as({ range: RANGE }))).toMatchObject({ kind: 'range', note: null });
  });

  it('reads a reason as its note alone', () => {
    const note = '(a reason note, from SQL)';
    expect(mfShape(as({ status: 'not_in_cz', note }))).toEqual({ kind: 'note', note });
  });

  it('reads no result, an empty object and an empty note as nothing', () => {
    expect(mfShape(null)).toEqual({ kind: 'none' });
    expect(mfShape(undefined)).toEqual({ kind: 'none' });
    expect(mfShape(as({}))).toEqual({ kind: 'none' });
    expect(mfShape(as({ status: 'x', note: '' }))).toEqual({ kind: 'none' });
  });
});

describe('mfYieldText', () => {
  const NB = '\u00a0';
  it("prints a value's yield, and the yield column alone the same", () => {
    const ref = as({ monthly_rent_czk: 17_700 });
    expect(mfYieldText(4.25, ref)).toBe(`4,3${NB}%`);
    expect(mfYieldText(4.25)).toBe(`4,3${NB}%`);
  });

  it("prints a range's two ends, unit once", () => {
    expect(mfYieldText(null, as({ range: RANGE }))).toBe(`3,4–4,2${NB}%`);
  });

  it('prints nothing for a rental range, a note, a value without a yield, or no result', () => {
    expect(mfYieldText(null, as({ range: { ...RANGE, yield_min_pct: null, yield_max_pct: null } }))).toBeNull();
    expect(mfYieldText(null, as({ note: '(a reason)' }))).toBeNull();
    expect(mfYieldText(null, as({ monthly_rent_czk: 17_700 }))).toBeNull();
    expect(mfYieldText(null)).toBeNull();
  });
});

describe('mfYieldLine', () => {
  const NB = ' ';
  it('labels the figure once, a range included, and prints nothing without one', () => {
    expect(mfYieldLine(4.25)).toBe(`Výnos MF 4,3${NB}%`);
    expect(mfYieldLine(null, as({ range: RANGE }))).toBe(`Výnos MF 3,4–4,2${NB}%`);
    expect(mfYieldLine(null)).toBeNull();
  });
});
