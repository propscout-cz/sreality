/* The overrides a Valuo POST carries are exactly the user's changes from the defaults. */
import { describe, expect, it } from 'vitest';

import { fmtDistanceM, fmtPct, fmtTomDays } from './format';
import {
  band, formErrors, missingRequired, reliability, shownPerM2, shownPrice, withEdit, type Valuation, type ValuoField, type ValuoSummary,
} from './valuo';

describe('withEdit', () => {
  const defaults = { kind: 'sale', floor: 3 };

  it('keeps a change, drops a value set back, and keeps a clear as null', () => {
    const changed = withEdit({}, defaults, 'floor', 4);
    expect(changed).toEqual({ floor: 4 });
    expect(withEdit(changed, defaults, 'floor', 3)).toEqual({});
    expect(withEdit({}, defaults, 'kind', null)).toEqual({ kind: null });
  });

  it('treats a parameter with no default as null: clearing a pick sends nothing', () => {
    expect(withEdit(withEdit({}, defaults, 'rating', 'good'), defaults, 'rating', null)).toEqual({});
  });
});

it('missingRequired names the required parameters still empty', () => {
  const f = (name: string, required: boolean) =>
    ({ name, label: name.toUpperCase(), required }) as ValuoField;
  expect(missingRequired([f('rating', true), f('floor', false), f('kind', true)], { kind: 'sale' })).toEqual([
    'RATING',
  ]);
});

it('formErrors reads the issues of a form_errors refusal only, and none from a row that predates them', () => {
  const issues = [{ field: 'rating', message: 'Invalid enum value' }, { field: null, message: 'Required' }];
  const row = (error: Valuation['error']) => ({ error }) as Valuation;
  expect(formErrors(row({ code: 'form_errors', message: 'x', form_errors: issues }))).toEqual(issues);
  expect(formErrors(row({ code: 'form_errors', message: 'x' }))).toEqual([]);
  expect(formErrors(row({ code: 'timeout', message: 'x', form_errors: issues }))).toEqual([]);
  expect(formErrors(row(null))).toEqual([]);
  expect(formErrors(null)).toEqual([]);
});

it('band reads range_price as [low, high], and nothing from a row without one', () => {
  const row = (result: unknown) => ({ result }) as Valuation;
  expect(band(row({ range_price: [5_600_000, 6_600_000] }), 'range_price')).toEqual([5_600_000, 6_600_000]);
  expect(band(row({ range_price_m2: [100, 120] }), 'range_price')).toBeNull();
  expect(band(row({ range_price: [5_600_000] }), 'range_price')).toBeNull();
  expect(band(row({ range_price: null }), 'range_price')).toBeNull();
  expect(band(row(null), 'range_price_m2')).toBeNull();
});

it('shownPrice rounds a sale to 10 000 Kč and a monthly rent to 100 Kč', () => {
  expect(shownPrice(6_123_456.7, false)).toBe(6_120_000);
  expect(shownPrice(6_125_000, false)).toBe(6_130_000);
  expect(shownPrice(21_037, true)).toBe(21_000);
  expect(shownPrice(21_050, true)).toBe(21_100);
});

it('shownPerM2 rounds a sale per m² to 100 Kč and a monthly rent per m² to 1 Kč', () => {
  expect(shownPerM2(112_912, false)).toBe(112_900);
  expect(shownPerM2(126_585, false)).toBe(126_600);
  expect(shownPerM2(318.6, true)).toBe(319);
});

describe('reliability', () => {
  // Each part is unbreakable (no-break spaces inside); compared here as plain text.
  const plain = (parts: string[]) => parts.map((p) => p.replace(/\s/g, ' '));
  const full: ValuoSummary = {
    count: 12, avg_distance: 406, avg_age: 30, avg_duration: 21, avg_score: 0.8, distance: 2000, age: 90, not_enough: false,
  };

  it('says how many comparables, how near and how alike they are, and how far Valuo reached', () => {
    expect(plain(reliability(full))).toEqual(plain([
      '12 srovnání', `ø ${fmtDistanceM(406)}`, `shoda ${fmtPct(80, { digits: 0 })}`,
      `okruh ${fmtDistanceM(2000)} / ${fmtTomDays(90)}`,
    ]));
    // A line wraps between parts, never inside one ("okruh 2,0 km" / "/ 180 dní" on the phone).
    expect(reliability(full).some((p) => p.includes(' '))).toBe(false);
  });

  it('leaves out every part Valuo did not report', () => {
    expect(plain(reliability({ ...full, count: null, avg_score: null, distance: null }))).toEqual(plain([
      `ø ${fmtDistanceM(406)}`, `okruh ${fmtTomDays(90)}`,
    ]));
    expect(plain(reliability({ ...full, count: 0, avg_distance: null, avg_score: null, distance: null, age: null }))).toEqual([
      '0 srovnání',
    ]);
  });
});
