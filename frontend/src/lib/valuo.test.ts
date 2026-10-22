/* The overrides a Valuo POST carries are exactly the user's changes from the defaults. */
import { describe, expect, it } from 'vitest';

import { fmtDistanceM, fmtPct, fmtTomDays } from './format';
import {
  BAND_FROM, MIN_KEPT, advertToken, band, formErrors, isOwnAd, missingRequired, recalc, recalcBase, reliability, shownPerM2, shownPrice,
  withEdit, type Valuation, type ValuoField, type ValuoRecord, type ValuoSummary,
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
    count: 12, avg_distance: 406, avg_score: 0.8, distance: 2000, age: 90, not_enough: false,
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

/* Numbers only, from five real production answers (no address, link or id): [Valuo's adjusted Kč/m², match in %, 1 = a
 * realised sale], and what Valuo itself answered for the same comparables. */
type Row = [number, number, number];
const REAL: { name: string; area: number; avg: number; std: number; band: [number, number]; rows: Row[] }[] = [
  { name: 'a monthly rent for a flat', area: 50, avg: 377, std: 25, band: [352, 402],
    rows: [[380, 85, 0], [343, 79, 0], [382, 78, 0], [365, 77, 0], [353, 77, 0], [365, 76, 0], [423, 74, 0], [412, 74, 0]] },
  { name: 'a plot of land', area: 426, avg: 11671, std: 1783, band: [9888, 13454],
    rows: [[10751, 100, 0], [11502, 90, 0], [11502, 90, 0], [10915, 90, 0], [15167, 86, 0], [11261, 75, 0], [8990, 75, 0], [14544, 75, 0]] },
  { name: 'a flat for sale', area: 66, avg: 116710, std: 4847, band: [111863, 121557],
    rows: [[123029, 89, 0], [121000, 84, 0], [120820, 80, 0], [111774, 80, 0], [117808, 80, 0], [117799, 77, 0], [110070, 75, 0], [110395, 74, 0]] },
  { name: 'a house with realised sales', area: 105, avg: 95108, std: 19205, band: [75903, 114313],
    rows: [[96968, 82, 1], [118169, 74, 0], [125856, 73, 0], [105377, 73, 0], [64744, 72, 1], [107649, 71, 0], [85082, 71, 1], [105886, 68, 1]] },
  { name: 'a flat with one realised sale', area: 120, avg: 154826, std: 7744, band: [147082, 162570],
    rows: [[149412, 84, 1], [159081, 79, 0], [159393, 72, 0], [145679, 71, 0], [173250, 70, 0], [159167, 69, 0], [148746, 68, 0], [152546, 68, 0]] },
];
const rec = ([adjusted_price_m2, score_prct, sold]: Row, i = 0, advert_url?: string): ValuoRecord => ({
  id: i, place: null, local_type: null, calc_area: null, price: null, price_m2: null, sale_price: sold === 1,
  adjusted_price: null, adjusted_price_m2, distance: null, score_prct, days: null, advert_url,
});
const answer = (rows: Row[], avg: number | null, area: number | null = 50): Valuation =>
  ({ result: { avg_price_m2: avg, calc_area: area }, records: rows.map((r, i) => rec(r, i)) }) as Valuation;
const within = (got: number, want: number, rel: number, abs = 0) => Math.abs(got - want) <= Math.max(abs, want * rel);

describe('recalc', () => {
  it.each(REAL)('with every comparable kept reproduces Valuo on $name', ({ avg, std, band: [low, high], rows }) => {
    const mine = recalc(rows.map((r, i) => rec(r, i)))!;
    expect(within(mine.avg, avg, 0.0015)).toBe(true);
    expect(within(mine.std, std, 0.003, 1)).toBe(true);
    expect(within(mine.avg - mine.std, low, 0.002, 1) && within(mine.avg + mine.std, high, 0.002, 1)).toBe(true);
  });

  it('is the geometric mean of the adjusted prices when every match is alike: 100 and 400 make 200', () => {
    expect(recalc([rec([100, 50, 0]), rec([400, 50, 0])])).toEqual({ avg: 200, std: 139 }); // 200 x ln 4 / 2
  });

  it('counts a realised sale twice: 100 sold and 400 asked make the cube root of 100 x 100 x 400', () => {
    expect(recalc([rec([100, 50, 1]), rec([400, 50, 0])])).toEqual({ avg: 159, std: 104 });
  });

  it('weights by match: 100 at 75 % and 400 at 25 % make 141', () => {
    expect(recalc([rec([100, 75, 0]), rec([400, 25, 0])])).toEqual({ avg: 141, std: 85 });
  });

  it('refuses a set it cannot weigh: none, or any comparable without a positive adjusted price or match', () => {
    const ok = rec([100, 50, 0]);
    expect(recalc([])).toBeNull();
    for (const bad of [{ adjusted_price_m2: null }, { adjusted_price_m2: 0 }, { score_prct: null }, { score_prct: 0 }]) {
      expect(recalc([ok, { ...ok, ...bad }])).toBeNull();
    }
  });

  it('asks for 3 comparables to give a figure and 5 to give a band', () => {
    expect([MIN_KEPT, BAND_FROM]).toEqual([3, 5]);
  });
});

describe('recalcBase', () => {
  it.each(REAL)('is offered on $name, with Valuo area and average as the anchors', ({ area, avg, rows }) => {
    expect(recalcBase(answer(rows, avg, area))).toEqual({ area, ref: avg });
  });

  const rows = REAL[2].rows;
  it('is offered within 1 % of Valuo and refused beyond it: Valuo changed its method, or this is not a type we measured', () => {
    expect(recalcBase(answer(rows, 116_708 * 0.991))).not.toBeNull();
    expect(recalcBase(answer(rows, 116_708 * 1.011))).toBeNull();
    expect(recalcBase(answer(rows, 116_708 * 0.985))).toBeNull();
  });

  it('refuses an answer it cannot anchor or weigh in full', () => {
    expect(recalcBase(answer(rows, null))).toBeNull();
    expect(recalcBase(answer(rows, 116_710, null))).toBeNull();
    expect(recalcBase(answer([], 116_710))).toBeNull();
    expect(recalcBase(answer([...rows, [100_000, null as unknown as number, 0]], 116_710))).toBeNull();
    expect(recalcBase({ ...answer(rows, 116_710), records: undefined })).toBeNull(); // a row from before v3
    expect(recalcBase({ ...answer(rows, 116_710), result: null })).toBeNull();
  });
});

describe('own ad', () => {
  const ad = 'https://www.example.invalid/detail/prodej/byt/2+kk/praha-1/4700001';

  it('reads the portal id as the last path segment, ignoring a trailing slash, a query and a fragment', () => {
    expect(advertToken(ad)).toBe('4700001');
    expect(advertToken(`${ad}/`)).toBe('4700001');
    expect(advertToken(`${ad}?utm=x#top`)).toBe('4700001');
    expect(advertToken('https://other.example.invalid/reality/slug/a1b2c3d4/')).toBe('a1b2c3d4');
  });

  it('reads nothing from a missing, empty, malformed or path-less address', () => {
    for (const bad of [null, undefined, '', 'not a link', 'https://www.example.invalid', 'https://www.example.invalid/']) {
      expect(advertToken(bad)).toBeNull();
    }
  });

  it('matches against every ad of the property, a delisted one too, whatever its slug or host', () => {
    const ours = ['https://www.example.invalid/detail/prodej/byt/2+kk/praha-9/9900009', null, 'https://m.example.invalid/x/4700001/'];
    expect(isOwnAd(rec([100, 50, 0], 0, ad), ours)).toBe(true);
    expect(isOwnAd(rec([100, 50, 0], 0, `${ad}/`), ['https://www.example.invalid/detail/other/4700001'])).toBe(true);
  });

  it('does not match another id, an ad without an address, or a property with no ads', () => {
    const ours = ['https://www.example.invalid/detail/prodej/byt/2+kk/praha-1/4700002'];
    expect(isOwnAd(rec([100, 50, 0], 0, ad), ours)).toBe(false);
    expect(isOwnAd(rec([100, 50, 0], 0), ours)).toBe(false);
    expect(isOwnAd(rec([100, 50, 0], 0, ad), [])).toBe(false);
    expect(isOwnAd(rec([100, 50, 0], 0, 'https://www.example.invalid/'), ['https://www.example.invalid/'])).toBe(false);
  });
});
