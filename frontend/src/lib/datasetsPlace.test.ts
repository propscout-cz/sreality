/* Datasets' rows are per obec: a chip reaches them through the obec tree, and a
 * chip finer than an obec can neither select nor subtract one. */
import { describe, expect, it } from 'vitest';

import { hasChipFinerThanObec, keepObecFor } from './datasetsPlace';
import type { DistrictChip } from './filters';

const JIHLAVA = 586846;
const TELC = 588458;
const HUMPOLEC = 547492;
const PARDUBICE = 555134;
const TREE = {
  okresId: new Map([[JIHLAVA, 3707], [TELC, 3707], [HUMPOLEC, 3304], [PARDUBICE, 3606]]),
  krajId: new Map([[JIHLAVA, 108], [TELC, 108], [HUMPOLEC, 108], [PARDUBICE, 94]]),
};
const ALL = [JIHLAVA, TELC, HUMPOLEC, PARDUBICE];

const chip = (level: DistrictChip['level'], id: number, excluded = false): DistrictChip =>
  ({ name: `${level} ${id}`, context: null, level, id, excluded });
const kept = (chips: DistrictChip[]): number[] => ALL.filter(keepObecFor(chips, TREE));

describe('keepObecFor', () => {
  it('keeps every obec without a chip', () => {
    expect(kept([])).toEqual(ALL);
  });

  it('keeps an obec when it, its okres or its kraj is an include chip', () => {
    expect(kept([chip('obec', PARDUBICE)])).toEqual([PARDUBICE]);
    expect(kept([chip('okres', 3707)])).toEqual([JIHLAVA, TELC]);
    expect(kept([chip('kraj', 108)])).toEqual([JIHLAVA, TELC, HUMPOLEC]);
    expect(kept([chip('okres', 3304), chip('obec', PARDUBICE)])).toEqual([HUMPOLEC, PARDUBICE]);
  });

  it('subtracts an excluded obec, okres or kraj, alone or from an include', () => {
    expect(kept([chip('kraj', 108), chip('okres', 3707, true)])).toEqual([HUMPOLEC]);
    expect(kept([chip('kraj', 108), chip('obec', TELC, true)])).toEqual([JIHLAVA, HUMPOLEC]);
    expect(kept([chip('kraj', 94, true)])).toEqual([JIHLAVA, TELC, HUMPOLEC]);
  });

  it('selects no obec through a street or part-of-town include, and subtracts none through its exclude', () => {
    expect(kept([chip('ulice', 666742)])).toEqual([]);
    expect(kept([chip('cast_obce', 490067)])).toEqual([]);
    expect(kept([chip('obec', PARDUBICE), chip('ulice', 666742)])).toEqual([PARDUBICE]);
    expect(kept([chip('obec', PARDUBICE), chip('ulice', 666742, true)])).toEqual([PARDUBICE]);
    expect(kept([chip('cast_obce', 490067, true)])).toEqual(ALL);
  });

  it('places an obec missing from the tree only by its own code', () => {
    const tree = { okresId: new Map<number, number>(), krajId: new Map<number, number>() };
    expect(keepObecFor([chip('kraj', 108)], tree)(JIHLAVA)).toBe(false);
    expect(keepObecFor([chip('obec', JIHLAVA)], tree)(JIHLAVA)).toBe(true);
  });
});

describe('hasChipFinerThanObec', () => {
  it('is true only for a street or part-of-town chip, included or excluded', () => {
    expect(hasChipFinerThanObec([chip('kraj', 108), chip('okres', 3707), chip('obec', JIHLAVA)])).toBe(false);
    expect(hasChipFinerThanObec([chip('obec', JIHLAVA), chip('ulice', 666742)])).toBe(true);
    expect(hasChipFinerThanObec([chip('cast_obce', 490067, true)])).toBe(true);
  });
});
