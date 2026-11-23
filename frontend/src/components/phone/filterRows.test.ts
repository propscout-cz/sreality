/* The filters a cohort sets beyond its base, one entry per filter: the phone's
 * Filtry count and Browse's applied-filter pills (ruling P09) read this one list,
 * so the two can never disagree about what "one filter" is. */
import { describe, expect, it } from 'vitest';

import { DEFAULT_FILTERS, type ListingFilters } from '@/lib/filters';
import type { Disposition } from '@/lib/types';

import { UNROWED_FILTERS, appliedFilters, filtersBeyond, withoutApplied } from './filterRows';

const AREA = { west: 14.4, south: 50.06, east: 14.44, north: 50.1 };
const at = (patch: Partial<ListingFilters>): ListingFilters => ({ ...DEFAULT_FILTERS, ...patch });

describe('appliedFilters', () => {
  it('lists nothing for the base itself', () => {
    expect(appliedFilters(DEFAULT_FILTERS, DEFAULT_FILTERS)).toEqual([]);
  });

  it('is one entry for a min/max pair, under the sidebar row and label that draw it', () => {
    const list = appliedFilters(at({ priceMin: 3_000_000, priceMax: 5_000_000 }), DEFAULT_FILTERS);
    expect(list).toHaveLength(1);
    expect(list[0]).toMatchObject({ label: 'Price', fields: ['priceMin', 'priceMax'] });
    expect(list[0].row?.label).toBe('Price');
  });

  it('gives a field no row draws its own entry, a URL-only min/max pair as one', () => {
    expect(appliedFilters(at({ bounds: AREA }), DEFAULT_FILTERS)).toEqual([
      { key: 'bounds', label: 'Map area', row: null, fields: ['bounds'] },
    ]);
    expect(appliedFilters(at({ gardenAreaMin: 50, gardenAreaMax: 200 }), DEFAULT_FILTERS)).toEqual([
      { key: 'gardenArea', label: 'Garden area', row: null, fields: ['gardenAreaMin', 'gardenAreaMax'] },
    ]);
  });

  it('keeps the sidebar order, the fields no row draws last', () => {
    const f = at({ bounds: AREA, dispositions: ['2+kk' as Disposition], status: 'active', priceMin: 1 });
    expect(appliedFilters(f, DEFAULT_FILTERS).map((a) => a.label)).toEqual(['Status', 'Price', 'Disposition', 'Map area']);
  });

  it('is exactly what the phone counts', () => {
    const f = at({
      priceMin: 1,
      priceMax: 2,
      dispositions: ['2+kk' as Disposition],
      bounds: AREA,
      brokerId: 7,
      status: 'active',
      gardenAreaMin: 10,
      gardenAreaMax: 20,
    });
    expect(filtersBeyond(f, DEFAULT_FILTERS)).toBe(6);
    expect(filtersBeyond(f, DEFAULT_FILTERS)).toBe(appliedFilters(f, DEFAULT_FILTERS).length);
  });

  /* The census: a ListingFilters field the sidebar does not draw still narrows the
   * cohort, so it needs a name of its own before a pill can say it. */
  it('names every field no sidebar row draws', () => {
    expect(UNROWED_FILTERS.map((g) => g.key)).toEqual(['categorySubCb', 'gardenArea', 'bounds']);
    for (const g of UNROWED_FILTERS) expect(g.label).not.toBe(g.key);
  });
});

describe('withoutApplied', () => {
  it('sets only that entry\'s fields back to the base', () => {
    const f = at({ priceMin: 1, priceMax: 2, dispositions: ['2+kk' as Disposition] });
    const price = appliedFilters(f, DEFAULT_FILTERS).find((a) => a.label === 'Price')!;
    expect(withoutApplied(f, DEFAULT_FILTERS, price)).toEqual({ ...f, priceMin: null, priceMax: null });
  });
});
