/* The feed's pure half (#1766): which cohort a scope chip reads, and the day
 * rules — Prague calendar days, whatever zone the phone is in. */
import { describe, expect, it } from 'vitest';

import { DEFAULT_FILTERS, type ListingFilters } from './filters';
import {
  FEED_ALL_FILTERS,
  dayRuleLabel,
  feedFiltersForPreset,
  feedTimestamp,
  groupByDay,
  pragueClock,
  pragueDayKey,
  withPortals,
} from './feed';
import type { CardRow } from './queries';
import type { FilterPreset } from './types';

const preset = (filter_spec: FilterPreset['filter_spec']): FilterPreset => ({
  id: 'p1',
  name: 'Vysočany byty',
  filter_spec,
  created_at: '2026-10-01T00:00:00Z',
  updated_at: '2026-10-01T00:00:00Z',
  position: 0,
  color: null,
});

describe('scope → filters', () => {
  it('reads "Vše" as the whole market: every category, sale and rent, live ads', () => {
    expect(feedFiltersForPreset(null)).toBe(FEED_ALL_FILTERS);
    expect(FEED_ALL_FILTERS.categoryMain).toEqual([]);
    expect(FEED_ALL_FILTERS.categoryType).toBeNull();
    expect(FEED_ALL_FILTERS.status).toBe('active');
    expect(FEED_ALL_FILTERS.showDismissed).toBe(false);
  });

  it("applies a preset's criteria and drops the lenses that ride outside it", () => {
    const saved: ListingFilters = {
      ...DEFAULT_FILTERS,
      categoryMain: ['byt'],
      categoryType: 'prodej',
      dispositions: ['2+kk'],
      priceMax: 8_000_000,
      showDismissed: true,
      pipeline: { stage_ids: [] },
      collections: [4],
    };
    const f = feedFiltersForPreset(preset({ filters: saved, sort: '-price_czk' }));
    expect(f.categoryType).toBe('prodej');
    expect(f.dispositions).toEqual(['2+kk']);
    expect(f.priceMax).toBe(8_000_000);
    expect(f.showDismissed).toBe(false);
    expect(f.pipeline).toBeNull();
    expect(f.collections).toEqual([]);
  });

  it('keeps a map area the operator saved with the preset', () => {
    const bounds = { north: 50.12, south: 50.09, east: 14.52, west: 14.46 };
    const f = feedFiltersForPreset(
      preset({ filters: { ...DEFAULT_FILTERS, bounds } as ListingFilters }),
    );
    expect(f.bounds).toEqual(bounds);
  });

  it('reads a legacy bare-filters preset blob', () => {
    const f = feedFiltersForPreset(
      preset({ ...DEFAULT_FILTERS, categoryMain: 'dum' } as unknown as ListingFilters),
    );
    expect(f.categoryMain).toEqual(['dum']);
  });

  it("lets the portal chips replace the scope's own portals, and only when set", () => {
    const f = { ...FEED_ALL_FILTERS, portals: ['bazos'] };
    expect(withPortals(f, null).portals).toEqual(['bazos']);
    expect(withPortals(f, ['sreality', 'idnes']).portals).toEqual(['sreality', 'idnes']);
    expect(withPortals(f, []).portals).toEqual([]);
  });
});

describe('Prague days', () => {
  it('files an instant under its Prague day, not its UTC one', () => {
    /* 22:30 UTC on 11 Oct is 00:30 on 12 Oct in Prague (CEST, UTC+2). */
    expect(pragueDayKey('2026-10-11T22:30:00Z')).toBe('2026-10-12');
    expect(pragueDayKey('2026-10-11T21:59:00Z')).toBe('2026-10-11');
    /* Winter time: UTC+1. */
    expect(pragueDayKey('2026-12-31T23:30:00Z')).toBe('2027-01-01');
    expect(pragueDayKey('2026-12-31T22:30:00Z')).toBe('2026-12-31');
  });

  it('reads the clock in Prague', () => {
    expect(pragueClock(Date.parse('2026-10-12T12:32:00Z'))).toBe('14:32');
  });

  it('labels today, yesterday, then dates (with the year once it changes)', () => {
    expect(dayRuleLabel('2026-10-12', '2026-10-12')).toBe('Dnes');
    expect(dayRuleLabel('2026-10-11', '2026-10-12')).toBe('Včera');
    expect(dayRuleLabel('2026-10-05', '2026-10-12')).toBe('5. 10.');
    expect(dayRuleLabel('2026-12-31', '2027-01-01')).toBe('Včera');
    expect(dayRuleLabel('2026-12-30', '2027-01-01')).toBe('30. 12. 2026');
    /* Yesterday across a month end. */
    expect(dayRuleLabel('2026-09-30', '2026-10-01')).toBe('Včera');
  });

  it('cuts the roll into runs of one Prague day, in order', () => {
    const rows = [
      { id: 1, at: '2026-10-12T08:00:00Z' },
      { id: 2, at: '2026-10-11T22:10:00Z' }, // 00:10 on the 12th in Prague
      { id: 3, at: '2026-10-11T21:50:00Z' }, // 23:50 on the 11th
      { id: 4, at: null },
      { id: 5, at: '2026-10-09T10:00:00Z' },
    ];
    const days = groupByDay(rows, (r) => r.at);
    expect(days.map((d) => [d.dayKey, d.rows.map((r) => r.id)])).toEqual([
      ['2026-10-12', [1, 2]],
      ['2026-10-11', [3, 4]],
      ['2026-10-09', [5]],
    ]);
  });
});

describe('the timestamp a row is ordered by', () => {
  const row = {
    first_seen_at: '2026-09-01T10:00:00Z',
    newest_ad_at_bazos: '2026-10-12T07:00:00Z',
  } as unknown as CardRow;

  it("is the property's first seen without a portal order", () => {
    expect(feedTimestamp(row, null)).toBe('2026-09-01T10:00:00Z');
  });

  it("is the newest ad on the one chosen portal (MS19's order)", () => {
    expect(feedTimestamp(row, 'bazos')).toBe('2026-10-12T07:00:00Z');
    expect(feedTimestamp(row, 'idnes')).toBe('2026-09-01T10:00:00Z');
  });
});
