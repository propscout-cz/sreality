/* The broker board's request and read (lib/brokerBoard.ts). The request is held
 * to the generated filter registry, the source BoardArgs is built from too
 * (tests/api/test_broker_board.py pins it there): a key BoardArgs refuses
 * (extra='forbid'), one it requires and the SPA leaves out, or a value under a
 * registry floor fails here before it can 422 in production. */

import { afterEach, describe, expect, it, vi } from 'vitest';

import { filterLabel } from '@/components/phone/filterRows';
import { BOARD_META } from '@/test/brokerBoard';

import * as api from './api';
import { filterById, filtersForAgenda } from './filterRegistry.generated';
import {
  DEFAULT_BOARD,
  boardIgnores,
  boardRequest,
  fetchBrokerBoard,
  marketFilters,
  speedOf,
  type BoardOptions,
} from './brokerBoard';
import { DEFAULT_FILTERS, applyRegistryUpdates, effectiveBbox, fromSearchParams, type ListingFilters } from './filters';

vi.mock('./api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('./api')>()),
  apiPost: vi.fn(),
}));

/* BoardArgs' registry filters, and the board's own options beside them. */
const FILTER_IDS = filtersForAgenda('browse')
  .filter((d) => d.id !== 'location' && d.category !== 'Curation')
  .map((d) => d.id);
const OPTIONS = ['bounds', 'horizon_days', 'sort', 'min_properties', 'limit', 'firm_ids', 'broker_id'];

/* The registry's constraints as the server renders them (api/filter_args): `min` a floor, `enum` the vocabulary. */
function refused(body: Record<string, unknown>): string[] {
  return FILTER_IDS.filter((id) => {
    const c = filterById(id)?.constraints ?? {};
    return [body[id]].flat().some(
      (v) =>
        v != null &&
        ((typeof c.min === 'number' && typeof v === 'number' && v < c.min) ||
          (Array.isArray(c.enum) && !c.enum.includes(v))),
    );
  });
}

const PRAHA = { name: 'Praha', context: null, level: 'obec', id: 554782 } as const;
const ZIZKOV = { name: 'Žižkov', context: 'Praha', level: 'cast_obce', id: 490067, excluded: true } as const;

/* A cohort with a value set on the constrained filters (a floor, a vocabulary) and the ones the request maps. */
const FILLED: ListingFilters = {
  ...DEFAULT_FILTERS,
  categoryMain: ['dum', 'komercni'],
  categoryType: 'pronajem',
  districts: [PRAHA, ZIZKOV],
  priceMin: 10_000,
  priceMax: 40_000,
  areaMin: 50,
  status: 'active',
  lastSeenMaxDays: 7,
  hasBalcony: 'yes',
  hasLift: 'no',
  portals: ['sreality'],
  priceChangeWindowDays: 90,
  priceChangeCountMin: 1,
  nearPop5kmMin: 20_000,
  bounds: { west: 14.3, south: 50.0, east: 14.6, north: 50.2 },
  brokerId: 7,
};

describe('the board request against the registry', () => {
  it.each<[string, ListingFilters, BoardOptions]>([
    ['the defaults', DEFAULT_FILTERS, DEFAULT_BOARD],
    ['a filled cohort', FILLED, { horizonDays: 90, sort: 'speed', limit: 200, minProperties: 5, firmIds: [3] }],
    ['every deal type', { ...DEFAULT_FILTERS, categoryType: null, categoryMain: [] }, DEFAULT_BOARD],
    [
      'a typed value under a floor',
      applyRegistryUpdates(DEFAULT_FILTERS, [
        { id: 'near_pop_5km_min', value: 5_000 },
        { id: 'price_change_count_min', value: 0 },
        { id: 'min_parking_lots', value: -2 },
      ]),
      DEFAULT_BOARD,
    ],
    ['a linked value under a floor', fromSearchParams(new URLSearchParams('np5=5000&changes_min=0&parking_min=-2')), DEFAULT_BOARD],
  ])('sends exactly the keys BoardArgs takes, every value inside its constraints: %s', (_, f, o) => {
    const body = boardRequest(f, o);
    expect(Object.keys(body).sort()).toEqual([...FILTER_IDS, ...OPTIONS].sort());
    expect(Object.values(body)).not.toContain(undefined);
    expect(refused(body)).toEqual([]);
  });

  /* Browse holds the same value, so it and the board judge one cohort. */
  it('holds a value under a floor at the floor', () => {
    const f = fromSearchParams(new URLSearchParams('np5=5000&changes_min=0&parking_min=-2'));
    expect([f.nearPop5kmMin, f.priceChangeCountMin, f.parkingLotsMin]).toEqual([10_000, 1, 0]);
    expect(refused({ near_pop_5km_min: 5_000, price_change_count_min: 0 }).sort()).toEqual([
      'near_pop_5km_min',
      'price_change_count_min',
    ]);
  });

  it('is the registry view of the cohort, the map box, the broker pill and the options', () => {
    const body = boardRequest(FILLED, { horizonDays: 14, sort: 'speed', limit: 50, minProperties: 3, firmIds: [3, 7] });
    expect(body).toMatchObject({
      category_main_in: ['dum', 'komercni'],
      category_type: 'pronajem',
      districts: [PRAHA, ZIZKOV],
      status: 'active',
      has_balcony: true,
      has_lift: false,
      portals: ['sreality'],
      bounds: FILLED.bounds,
      broker_id: 7,
      horizon_days: 14,
      sort: 'speed',
      limit: 50,
      min_properties: 3,
      firm_ids: [3, 7],
    });
    const circle: ListingFilters = {
      ...DEFAULT_FILTERS,
      bounds: { west: 0, south: 0, east: 1, north: 1 },
      locationMode: 'center_radius',
      centerRadius: { lat: 50.08, lng: 14.42, radius_m: 2000 },
    };
    expect(boardRequest(circle, DEFAULT_BOARD).bounds).toEqual(effectiveBbox(circle));
  });

  it('never sends an account lens, and names the set ones', () => {
    const lensed: ListingFilters = {
      ...DEFAULT_FILTERS,
      tags: { set: null, in: [4], out: [] },
      collections: { set: 'in', in: [], out: [] },
      pipeline: { set: 'out', in: [], out: [] },
      withEstimates: true,
      showDismissed: true,
      priceGrowthRules: [{ datasetId: 1, fromYm: null, toYm: null, rentMinPct: 3, saleMinPct: null }],
    };
    expect(boardRequest(lensed, DEFAULT_BOARD)).toEqual(boardRequest(DEFAULT_FILTERS, DEFAULT_BOARD));
    expect(boardIgnores(lensed)).toEqual(['štítky', 'kolekce', 'pipeline', 'jen s odhadem', 'růst trhu']);
    expect(boardIgnores(DEFAULT_FILTERS)).toEqual([]);
    /* The key carries the cohort without them, so a lens edit does not refetch. */
    expect(marketFilters(lensed)).toEqual(DEFAULT_FILTERS);
  });

  /* metadata.speed_refused_by names BoardArgs filters by id: each has the label
   * the sidebar draws it under, so a newly refusing filter is never named by its id. */
  it('names every board filter by its sidebar label', () => {
    expect(FILTER_IDS.filter((id) => filterLabel(id) === id)).toEqual([]);
    expect(['status', 'tom_days_max', 'portals_excluded_active_only', 'category_type'].map(filterLabel)).toEqual([
      'Status',
      'Time on market (days)',
      'Source portal',
      'Listing for',
    ]);
  });
});

describe('the read', () => {
  afterEach(() => vi.mocked(api.apiPost).mockReset());

  it('is k of n and the share once the server gives one, only n under min_n, nothing when refused', () => {
    expect(speedOf({ n: 12, exited: 3, share: 0.25, rank: 0.09 }, BOARD_META)).toEqual({
      kind: 'share',
      exited: 3,
      n: 12,
      share: 0.25,
      rank: 0.09,
    });
    expect(speedOf({ n: 3, exited: 3, share: null, rank: null }, BOARD_META)).toEqual({ kind: 'small', n: 3 });
    expect(speedOf({ n: 12, exited: 3, share: null, rank: null }, { ...BOARD_META, speed_refused: 'mixed_category' })).toEqual(
      { kind: 'refused' },
    );
  });

  it('POSTs the request to /brokers/board, and throws on a 200 that carries no envelope', async () => {
    vi.mocked(api.apiPost).mockResolvedValue({ data: [], metadata: BOARD_META });
    const args = boardRequest(DEFAULT_FILTERS, DEFAULT_BOARD);
    const signal = new AbortController().signal;
    expect(await fetchBrokerBoard(args, signal)).toEqual({ rows: [], meta: BOARD_META });
    expect(api.apiPost).toHaveBeenCalledWith('/brokers/board', args, signal);
    vi.mocked(api.apiPost).mockResolvedValue('<html></html>');
    await expect(fetchBrokerBoard({})).rejects.toThrow('malformed /brokers/board response');
  });
});
