/* Tests for the spatial-predicate helpers in queries.ts.
 *
 * The interesting logic is `effectiveBbox`: when the operator picks
 * centre+radius mode, the cohort filter sends the bbox of the
 * circumscribing square of the radius circle (haversine math, no
 * dependency on PostGIS). Viewport mode falls through to whatever
 * the map's panning has set as `bounds`. These are pure functions
 * we want pinned against accidental edits.
 */

import { afterEach, describe, expect, it, vi } from 'vitest';
import { QueryClient } from '@tanstack/react-query';

/* The broker allowlist is the one prefilter resolved over the JWT-gated API. */
vi.mock('./brokers', async (importOriginal) => ({
  ...(await importOriginal<typeof import('./brokers')>()),
  fetchBrokerPropertyIds: vi.fn(async () => [1]),
}));

import { CONDITION_CANONICAL, DEFAULT_FILTERS, FURNISHED_CANONICAL } from './filters';
import { ApiError } from './api';
import { supabase } from './supabase';
import {
  BROWSE_SELECT_COLUMNS,
  CARD_SORTS,
  adScope,
  applyPortalRule,
  applyPrefilters,
  buildBrowseStatsArgs,
  fetchBrowseCount,
  fetchIsDismissed,
  fetchNoteCounts,
  fetchPropertySourcesByPropertyIds,
  fetchListingsForCards,
  fetchListingsForMap,
  districtsFilterClause,
  effectiveBbox,
  effectiveSort,
  matchesDistricts,
  orderPortal,
  parseSort,
  priceNullTolerantOr,
  sortToParam,
  DEFAULT_SORT,
  type BrowsePrefilters,
  type DistrictMatchRow,
} from './queries';
import { fetchBrokerPropertyIds } from './brokers';
import { fetchStatsPivot } from './stats';
import { filterById } from './filterRegistry.generated';
import type { DistrictChip, ListingFilters } from './filters';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

/* No prefilter active: the id allowlists buildBrowseStatsArgs is handed when none narrows. */
const NO_PREFILTERS: BrowsePrefilters = { obecIds: null, propertyIds: null, empty: false };

/* THE PostgREST stand-in for this file: every builder method chains (including
 * the `.retry()` / `.abortSignal()` pgRead sets, and the `.range()` fetchAllRows
 * drives), `.in()` records the ids it was handed, and awaiting the chain answers
 * with the rows registered for that relation, or with `error` as a 500.
 * `.rpc()` answers the same way and records its argument object. */
const stubReads = (rows: Record<string, object[]> = {}, error: unknown = null) => {
  const inIds: number[][] = [];
  const rpcArgs: Array<Record<string, unknown>> = [];
  const calls: Array<[string, unknown[]]> = [];
  const chain = (relation: string): unknown => {
    const data = rows[relation] ?? [];
    const answer = () =>
      Promise.resolve(
        error
          ? { data: null, error, count: null, status: 500, statusText: '' }
          : { data, error: null, count: data.length, status: 200, statusText: 'OK' },
      );
    const page: unknown = new Proxy(() => {}, {
      get: (_t, prop) => {
        if (prop === 'then') return (resolve: (v: unknown) => void) => answer().then(resolve);
        if (prop === 'in') {
          return (_column: string, ids: number[]) => {
            inIds.push(ids);
            return page;
          };
        }
        return (...args: unknown[]) => {
          calls.push([String(prop), args]);
          return page;
        };
      },
    });
    return page;
  };
  return {
    from: vi.spyOn(supabase, 'from').mockImplementation(((rel: string) => chain(rel)) as never),
    rpc: vi.spyOn(supabase, 'rpc').mockImplementation(((name: string, args: unknown) => {
      rpcArgs.push(args as Record<string, unknown>);
      return chain(name);
    }) as never),
    inIds,
    rpcArgs,
    calls,
  };
};

describe('priceNullTolerantOr', () => {
  it('AND-groups both bounds with the NULL disjunct', () => {
    expect(priceNullTolerantOr(1_000_000, 5_000_000)).toBe(
      'and(price_czk.gte.1000000,price_czk.lte.5000000),price_czk.is.null',
    );
  });
  it('keeps a single bound un-grouped', () => {
    expect(priceNullTolerantOr(1_000_000, null)).toBe(
      'price_czk.gte.1000000,price_czk.is.null',
    );
    expect(priceNullTolerantOr(null, 5_000_000)).toBe(
      'price_czk.lte.5000000,price_czk.is.null',
    );
  });
});

/* Gate-2: the city-quality allowlist must be AND'd onto the cohort via the
 * surrogate `listing_id`, NOT `sreality_id`. A post-Gate-2 non-sreality repr has
 * a NULL sreality_id (IN never matches NULL → the listing silently vanishes from
 * Map/Cards/Count while browse_stats still counts it), and — worse — the
 * id-spaces overlap by ~435, so a sreality_id passed into an `IN listing_id`
 * predicate would match a DIFFERENT listing. Pin the filter column here. */
describe('applyPrefilters (prefilter id-spaces)', () => {
  const record = () => {
    const calls: Array<{ col: string; vals: readonly unknown[] }> = [];
    const q = {
      in(col: string, vals: readonly unknown[]) {
        calls.push({ col, vals });
        return q;
      },
    };
    return { q, calls };
  };
  const base: BrowsePrefilters = NO_PREFILTERS;

  /* W3 S4 deleted the legacy city-quality listing-id allowlist together with
   * the `?cityQualityLegacy=1` hatch that was its only producer; city-quality
   * has resolved to an OBEC allowlist since W5 (migration 436). What remains is
   * that every surviving grain lands on its own column, in order. */
  it('leaves each prefilter grain on its own column', () => {
    const { q, calls } = record();
    applyPrefilters(q, {
      ...base,
      obecIds: [500],
      propertyIds: [7],
    });
    expect(calls).toEqual([
      { col: 'obec_id', vals: [500] },
      { col: 'property_id', vals: [7] },
    ]);
    expect(calls.some((c) => c.col === 'sreality_id')).toBe(false);
  });

  it('applies no id filter when every prefilter is inactive (null)', () => {
    const { q, calls } = record();
    applyPrefilters(q, base);
    expect(calls).toEqual([]);
  });

  it('applies an EMPTY allowlist — "scope on, nothing matched" is not "no constraint"', () => {
    const { q, calls } = record();
    applyPrefilters(q, { ...base, propertyIds: [] });
    expect(calls).toEqual([{ col: 'property_id', vals: [] }]);
  });

});

describe('effectiveBbox', () => {
  it('returns null when both modes are empty', () => {
    expect(effectiveBbox(DEFAULT_FILTERS)).toBeNull();
  });

  it('returns the literal viewport bounds in viewport mode', () => {
    const bounds = { west: 14.3, south: 50.0, east: 14.5, north: 50.2 };
    const got = effectiveBbox({
      ...DEFAULT_FILTERS,
      bounds,
    });
    expect(got).toEqual(bounds);
  });

  it('ignores viewport bounds in centre+radius mode', () => {
    // Even with a viewport bbox set on the side, centre+radius wins.
    const got = effectiveBbox({
      ...DEFAULT_FILTERS,
      locationMode: 'center_radius',
      centerRadius: { lat: 50, lng: 14, radius_m: 1000 },
      bounds: { west: 14.3, south: 50.0, east: 14.5, north: 50.2 },
    });
    expect(got).not.toBeNull();
    // The viewport bbox would have been (14.3, 50.0, 14.5, 50.2); the
    // 1km centre+radius around (50, 14) sits a hair north + south of
    // lat 50 and is nowhere near the viewport rectangle.
    expect(got!.north).toBeLessThan(50.2);
    expect(got!.west).toBeLessThan(14.3);
  });

  it('returns null in centre+radius mode when no centre is set', () => {
    const got = effectiveBbox({
      ...DEFAULT_FILTERS,
      locationMode: 'center_radius',
      centerRadius: null,
    });
    expect(got).toBeNull();
  });

  it('produces a centre-symmetric bbox around the point', () => {
    const lat = 50;
    const lng = 14;
    const got = effectiveBbox({
      ...DEFAULT_FILTERS,
      locationMode: 'center_radius',
      centerRadius: { lat, lng, radius_m: 1000 },
    });
    expect(got).not.toBeNull();
    expect(got!.north - lat).toBeCloseTo(lat - got!.south, 6);
    expect(got!.east - lng).toBeCloseTo(lng - got!.west, 6);
  });

  it('produces a wider bbox at higher latitudes for the same radius', () => {
    // Longitude degrees shrink as |lat| → 90°; the bbox must compensate
    // so the circle still fits at the poles. Compare two centres with
    // the same radius and check the longitude span widens with lat.
    const near_equator = effectiveBbox({
      ...DEFAULT_FILTERS,
      locationMode: 'center_radius',
      centerRadius: { lat: 10, lng: 14, radius_m: 1000 },
    });
    const near_pole = effectiveBbox({
      ...DEFAULT_FILTERS,
      locationMode: 'center_radius',
      centerRadius: { lat: 70, lng: 14, radius_m: 1000 },
    });
    const equator_span = (near_equator!.east - near_equator!.west);
    const pole_span = (near_pole!.east - near_pole!.west);
    expect(pole_span).toBeGreaterThan(equator_span);
  });

  it('approximates roughly 1km ↔ 0.009 deg at typical Prague latitude', () => {
    // 1 deg latitude ≈ 111.32 km. A 1km radius circle around lat 50
    // should produce a bbox with dLat ≈ 1/111.32 ≈ 0.00899 deg
    // either side of the centre.
    const got = effectiveBbox({
      ...DEFAULT_FILTERS,
      locationMode: 'center_radius',
      centerRadius: { lat: 50, lng: 14, radius_m: 1000 },
    });
    expect(got).not.toBeNull();
    expect(got!.north - 50).toBeCloseTo(0.00899, 4);
  });
});

/* The chip predicate itself moved to `lib/districtCodes.ts` and is pinned
 * against the CROSS-LANGUAGE table in `districtCodes.test.ts` (the same
 * `tests/fixtures/district_chip_plan.json` the API and the two RPC bodies are
 * tested against). What stays here is the queries.ts contract: the re-exports
 * exist, and Browse applies the clause to its read. */
describe('districtsFilterClause, re-exported by queries.ts', () => {
  it('returns null with no chips', () => {
    expect(districtsFilterClause([])).toBeNull();
  });

  it('compiles a chip to one code equality at its level', () => {
    expect(districtsFilterClause([
      { name: 'Jihlava', context: null, level: 'obec', id: 586846 },
    ])).toBe('and(or(obec_id.in.(586846)))');
  });

  it('compiles the quarter level Browse gained in W3', () => {
    expect(districtsFilterClause([
      { name: 'Žižkov', context: 'Praha', level: 'cast_obce', id: 490067 },
    ])).toBe('and(or(cast_obce_id.in.(490067)))');
  });

  it('compiles a street chip at the street, never at its town', () => {
    expect(districtsFilterClause([
      { name: 'Na Rybníčkách', context: 'Pardubice', level: 'ulice', id: 666742 },
    ])).toBe('and(or(ulice_id.in.(666742)))');
  });

  /* The two Stats/map RPCs compile the same CASE over the RAW level: an alias
   * mapped here (a street sent as its town) would widen Stats and the map. */
  it('ships each chip\'s raw level and code to the Stats/map RPCs', () => {
    const args = buildBrowseStatsArgs({
      ...DEFAULT_FILTERS,
      districts: [
        { name: 'Na Rybníčkách', context: 'Pardubice', level: 'ulice', id: 666742 },
        { name: 'Žižkov', context: 'Praha', level: 'cast_obce', id: 490067, excluded: true },
      ],
    }, NO_PREFILTERS);
    expect(args.districts_levels).toEqual(['ulice', 'cast_obce']);
    expect(args.districts_ids).toEqual([666742, 490067]);
    expect(args.districts_excluded_filter).toEqual([false, true]);
  });
});

describe('matchesDistricts, re-exported by queries.ts', () => {
  const mkRow = (o: Partial<DistrictMatchRow>): DistrictMatchRow => ({
    obec_id: null, okres_id: null, region_id: null, cast_obce_id: null, ulice_id: null, ...o,
  });

  it('matches any row when there are no chips', () => {
    expect(matchesDistricts(mkRow({ obec_id: 1 }), [])).toBe(true);
  });

  it('matches on the code at the chip level, never on a name', () => {
    const chip: DistrictChip = { name: 'Jihlava', context: null, level: 'obec', id: 586846 };
    expect(matchesDistricts(mkRow({ obec_id: 586846 }), [chip])).toBe(true);
    expect(matchesDistricts(mkRow({ obec_id: 999 }), [chip])).toBe(false);
  });

  it('splits include and exclude: included AND not excluded', () => {
    const inc: DistrictChip = { name: 'Jihlava', context: null, level: 'obec', id: 586846 };
    const exc: DistrictChip = {
      name: 'Modřany', context: null, level: 'cast_obce', id: 490017, excluded: true,
    };
    expect(matchesDistricts(mkRow({ obec_id: 586846 }), [inc, exc])).toBe(true);
    expect(
      matchesDistricts(mkRow({ obec_id: 586846, cast_obce_id: 490017 }), [inc, exc]),
    ).toBe(false);
    expect(matchesDistricts(mkRow({ obec_id: 1 }), [exc])).toBe(true);
  });
});

/* ---------------------------------------------------------------------- */
/* THE portal rule (MS19), against the table tests/test_portal_rule.py     */
/* runs through portal_status_matches, the RPCs and the Watchdog. Pure      */
/* semantics: every recorded predicate is evaluated against the case row.   */
/* ---------------------------------------------------------------------- */

type RuleRow = Record<string, unknown>;
interface RuleCase {
  name: string;
  is_active: boolean;
  all_sources: string[];
  active_sources: string[];
  portals: string[];
  status: 'any' | 'active' | 'inactive';
  excluded: string[];
  excluded_active_only: boolean;
  expected: boolean;
}
interface BrokerCase {
  name: string;
  ads: Array<{ broker: string; source: string; is_active: boolean }>;
  broker: string;
  portals: string[];
  status: 'any' | 'active' | 'inactive';
  expected: boolean;
}

/* Records what applyPortalRule asks PostgREST for, as predicates over a row. */
const PORTAL_RULE = JSON.parse(
  readFileSync(join(process.cwd(), '..', 'tests', 'fixtures', 'portal_rule.json'), 'utf-8'),
) as { cases: RuleCase[]; broker_cases: BrokerCase[] };
const PORTALS = (filterById('portals')?.enum_values ?? []).map((o) => String(o.value));

class RuleRecorder {
  preds: Array<(row: RuleRow) => boolean> = [];
  ops: string[] = [];
  eq(c: string, v: unknown) {
    this.ops.push(`${c}.eq`);
    this.preds.push((row) => row[c] === v);
    return this;
  }
  overlaps(c: string, v: readonly string[]) {
    this.ops.push(`${c}.ov`);
    this.preds.push((row) => (row[c] as string[]).some((x) => v.includes(x)));
    return this;
  }
  not(c: string, op: string, v: unknown) {
    this.ops.push(`${c}.not.${op}`);
    if (op === 'ov') {
      const vals = String(v).replace(/^\{|\}$/g, '').split(',');
      this.preds.push((row) => !(row[c] as string[]).some((x) => vals.includes(x)));
    } else if (op === 'is' && v === null) {
      this.preds.push((row) => row[c] != null);
    } else {
      throw new Error(`unexpected not.${op}`);
    }
    return this;
  }
}

/* A browse_list row as the rollup stores it: listed <=> dated, per portal. */
const ruleRow = (c: Pick<RuleCase, 'is_active' | 'all_sources' | 'active_sources'>): RuleRow => {
  const row: RuleRow = { ...c };
  for (const p of PORTALS) {
    row[`newest_ad_at_${p}`] = c.all_sources.includes(p) ? '2026-09-01T00:00:00+00:00' : null;
  }
  return row;
};

const ruleMatches = (f: ListingFilters, row: RuleRow): boolean =>
  applyPortalRule(new RuleRecorder(), f).preds.every((p) => p(row));

describe('the portal rule (MS19)', () => {
  const filtersFor = (c: Partial<RuleCase> & Pick<RuleCase, 'portals' | 'status'>): ListingFilters => ({
    ...DEFAULT_FILTERS, portals: c.portals, status: c.status,
    portalsExcluded: c.excluded ?? [], portalsExcludedActiveOnly: c.excluded_active_only ?? false,
  });

  for (const c of PORTAL_RULE.cases) {
    it(c.name, () => {
      expect(ruleMatches(filtersFor(c), ruleRow(c))).toBe(c.expected);
    });
  }

  /* The broker lookup answers the same rule over ONE broker's ads (portal and broker =
   * one ad): the same rendering over the aggregate of the broker's ads agrees with it. */
  for (const c of PORTAL_RULE.broker_cases) {
    it(`broker: ${c.name}`, () => {
      const own = c.ads.filter((ad) => ad.broker === c.broker);
      const sources = (ads: typeof own) => [...new Set(ads.map((ad) => ad.source))].sort();
      const row = ruleRow({
        is_active: own.some((ad) => ad.is_active),
        all_sources: sources(own),
        active_sources: sources(own.filter((ad) => ad.is_active)),
      });
      expect(ruleMatches(filtersFor(c), row)).toBe(c.expected);
    });
  }

  it('steps aside under a broker: the server judged portal and status on its ads', () => {
    const f = { ...DEFAULT_FILTERS, brokerId: 527, portals: ['idnes', 'remax'], status: 'inactive' as const };
    expect(adScope(f)).toEqual({ portals: [], status: 'any' });
    expect(applyPortalRule(new RuleRecorder(), f).ops).toEqual([]);
  });

  it('keeps the exclusion under a broker: it is a statement about the property', () => {
    const f = { ...DEFAULT_FILTERS, brokerId: 527, portals: ['idnes'], portalsExcluded: ['sreality'] };
    expect(applyPortalRule(new RuleRecorder(), f).ops)
      .toEqual(['all_sources.not.ov', 'newest_ad_at_idnes.not.is']);
    const live = { ...f, portalsExcludedActiveOnly: true };
    expect(applyPortalRule(new RuleRecorder(), live).ops[0]).toBe('active_sources.not.ov');
  });

  it('an excluded portal is never the one portal: no conjunct, no order', () => {
    const f = { ...DEFAULT_FILTERS, portalsExcluded: ['sreality'] };
    expect(applyPortalRule(new RuleRecorder(), f).ops).toEqual(['all_sources.not.ov']);
    expect(orderPortal(f, DEFAULT_SORT)).toBeNull();
  });

  it('keeps the one-portal conjunct under a broker, so the order stays indexable', () => {
    const f = { ...DEFAULT_FILTERS, brokerId: 527, portals: ['idnes'], status: 'active' as const };
    expect(applyPortalRule(new RuleRecorder(), f).ops).toEqual(['newest_ad_at_idnes.not.is']);
  });

  it('adds the conjunct for exactly one portal, and it narrows nothing', () => {
    const one = applyPortalRule(new RuleRecorder(), { ...DEFAULT_FILTERS, portals: ['maxima'] });
    expect(one.ops).toEqual(['all_sources.ov', 'newest_ad_at_maxima.not.is']);
    const two = applyPortalRule(new RuleRecorder(), { ...DEFAULT_FILTERS, portals: ['maxima', 'remax'] });
    expect(two.ops).toEqual(['all_sources.ov']);
    expect(applyPortalRule(new RuleRecorder(), DEFAULT_FILTERS).ops).toEqual([]);
  });
});

/* One portal's "Newest first" (MS19, Q49 b). */
describe('the one-portal order', () => {
  const withPortals = (portals: string[]) => ({ ...DEFAULT_FILTERS, portals });

  it('orders "newest/oldest first" by the portal\'s newest ad, both directions', () => {
    expect(orderPortal(withPortals(['idnes']), DEFAULT_SORT)).toBe('idnes');
    expect(effectiveSort(withPortals(['idnes']), { field: 'first_seen_at', direction: 'desc' }))
      .toEqual({ field: 'newest_ad_at_idnes', direction: 'desc' });
    expect(effectiveSort(withPortals(['maxima']), { field: 'first_seen_at', direction: 'asc' }))
      .toEqual({ field: 'newest_ad_at_maxima', direction: 'asc' });
  });

  it('falls back to the property\'s first seen with no portal or several', () => {
    for (const f of [DEFAULT_FILTERS, withPortals(['idnes', 'remax'])]) {
      expect(orderPortal(f, DEFAULT_SORT)).toBeNull();
      expect(effectiveSort(f, DEFAULT_SORT)).toEqual(DEFAULT_SORT);
    }
  });

  it('passes every other sort straight through', () => {
    for (const field of ['price_czk', 'price_per_m2', 'area_m2', 'plot_area_m2', 'last_seen_at',
                         'mf_gross_yield_pct'] as const) {
      expect(orderPortal(withPortals(['idnes']), { field, direction: 'desc' })).toBeNull();
      expect(effectiveSort(withPortals(['idnes']), { field, direction: 'desc' }))
        .toEqual({ field, direction: 'desc' });
    }
  });

  it('is not changed by a broker filter', () => {
    expect(effectiveSort({ ...withPortals(['idnes']), brokerId: 527 }, DEFAULT_SORT))
      .toEqual({ field: 'newest_ad_at_idnes', direction: 'desc' });
    expect(effectiveSort({ ...DEFAULT_FILTERS, brokerId: 527 }, DEFAULT_SORT)).toEqual(DEFAULT_SORT);
  });

  it('never reaches a URL: no ?sort= can pin it', () => {
    expect(parseSort('-newest_ad_at_idnes')).toEqual(DEFAULT_SORT);
  });

  it('pages the cards on the portal\'s column, with the rule and the conjunct on the chain', async () => {
    const s = stubReads();
    await fetchListingsForCards(withPortals(['idnes']), DEFAULT_SORT, null);
    const select = s.calls.find(([m]) => m === 'select')![1][0] as string;
    expect(select.split(',')).toEqual(expect.arrayContaining(
      ['newest_ad_at_idnes', 'property_id', 'all_sources', 'active_sources']));
    expect(s.calls).toContainEqual(['overlaps', ['all_sources', ['idnes']]]);
    expect(s.calls).toContainEqual(['not', ['newest_ad_at_idnes', 'is', null]]);
    expect(s.calls.filter(([m]) => m === 'order').map(([, a]) => a)).toEqual([
      ['newest_ad_at_idnes', { ascending: false, nullsFirst: undefined }],
      ['property_id', { ascending: false }],
    ]);
    vi.restoreAllMocks();
  });

  it('counts with the same chain the list pages with', async () => {
    const s = stubReads();
    await fetchBrowseCount({ ...withPortals(['idnes']), status: 'active' });
    expect(s.calls).toContainEqual(['overlaps', ['active_sources', ['idnes']]]);
    expect(s.calls).toContainEqual(['not', ['newest_ad_at_idnes', 'is', null]]);
    expect(s.calls.some(([m, a]) => m === 'eq' && a[0] === 'is_active')).toBe(false);
    vi.restoreAllMocks();
  });
});

/* The cards' sort menu is Browse's only sort control (operator ruling, Plocha
 * both ways and Naposledy viděno); a sort field without a control reads as the default. */
describe('the cards sort menu', () => {
  it('offers area both ways and last seen, most recent first', () => {
    const byLabel = new Map(CARD_SORTS.map((o) => [o.label, o.spec]));
    expect(byLabel.get('Plocha: od nejmenší')).toEqual({ field: 'area_m2', direction: 'asc' });
    expect(byLabel.get('Plocha: od největší')).toEqual({ field: 'area_m2', direction: 'desc' });
    expect(byLabel.get('Naposledy viděno')).toEqual({ field: 'last_seen_at', direction: 'desc' });
  });

  it('reads last seen either way round as the one last-seen order the menu offers', () => {
    expect(parseSort('last_seen_at')).toEqual({ field: 'last_seen_at', direction: 'desc' });
    expect(parseSort('-last_seen_at')).toEqual({ field: 'last_seen_at', direction: 'desc' });
  });

  it('round-trips every entry through the URL, one entry per order', () => {
    for (const { spec } of CARD_SORTS) expect(parseSort(sortToParam(spec))).toEqual(spec);
    const params = CARD_SORTS.map((o) => sortToParam(o.spec));
    expect(new Set(params).size).toBe(params.length);
  });

  it('reads a saved estate_area sort as the default, either way round', () => {
    expect(parseSort('estate_area')).toEqual(DEFAULT_SORT);
    expect(parseSort('-estate_area')).toEqual(DEFAULT_SORT);
    expect(CARD_SORTS.map((o) => o.spec.field)).not.toContain('estate_area');
  });

  it('reads every sort field without a control as the default', () => {
    for (const field of ['display_label', 'disposition', 'is_active', 'usable_area',
                         'parking_lots']) {
      expect(parseSort(field)).toEqual(DEFAULT_SORT);
      expect(parseSort(`-${field}`)).toEqual(DEFAULT_SORT);
    }
  });

  it('pages the cards by area with the rows that have none last', async () => {
    const s = stubReads();
    await fetchListingsForCards(DEFAULT_FILTERS, { field: 'area_m2', direction: 'desc' }, null);
    expect(s.calls.filter(([m]) => m === 'order').map(([, a]) => a)).toEqual([
      ['area_m2', { ascending: false, nullsFirst: false }],
      ['property_id', { ascending: false }],
    ]);
    vi.restoreAllMocks();
  });
  /* Lot area is THE plot measure (the Lot area filter's column), never the bare
   * estate_area, which four portals leave empty on land. */
  it('offers lot area both ways, on the plot measure', () => {
    const byLabel = new Map(CARD_SORTS.map((o) => [o.label, o.spec]));
    expect(byLabel.get('Pozemek: od nejmenšího')).toEqual({ field: 'plot_area_m2', direction: 'asc' });
    expect(byLabel.get('Pozemek: od největšího')).toEqual({ field: 'plot_area_m2', direction: 'desc' });
    expect(parseSort('-plot_area_m2')).toEqual({ field: 'plot_area_m2', direction: 'desc' });
  });

  it('pages the cards by lot area with the rows that have no lot last, and shows the lot', async () => {
    const s = stubReads();
    await fetchListingsForCards(
      DEFAULT_FILTERS,
      { field: 'plot_area_m2', direction: 'asc' },
      { value: 850, id: 7 },
    );
    expect(s.calls.filter(([m]) => m === 'order').map(([, a]) => a)).toEqual([
      ['plot_area_m2', { ascending: true, nullsFirst: false }],
      ['property_id', { ascending: true }],
    ]);
    expect(s.calls).toContainEqual(
      ['or', ['plot_area_m2.gt.850,and(plot_area_m2.eq.850,property_id.gt.7),plot_area_m2.is.null']]);
    vi.restoreAllMocks();
    expect(BROWSE_SELECT_COLUMNS.cards.split(',')).toContain('plot_area_m2');
  });
});

/* The measure travels with its PUBLISHED LABEL on every Browse lane, or the
 * number arrives unlabelable. All six migration-425 relations publish
 * `price_per_m2_basis` — including `browse_list`, whose rebuild runs inside
 * the migration and whose column presence § 9 asserts before it commits — so
 * no surface re-derives the basis in TypeScript. `category_main` rides along
 * for what the basis token does not say, the denominator (plot vs floor area);
 * `category_type` for the monthly period, on the lanes that print one (not the map). */
describe('Browse select-lists carry the measure with its published basis', () => {
  const cols = (list: string): string[] => list.split(',');

  it('selects the measure, its published basis, and the category columns each lane prints', () => {
    for (const [lane, list] of Object.entries(BROWSE_SELECT_COLUMNS)) {
      const c = cols(list);
      expect(c, `${lane}: price_per_m2`).toContain('price_per_m2');
      expect(c, `${lane}: price_per_m2_basis`).toContain('price_per_m2_basis');
      expect(c, `${lane}: category_main`).toContain('category_main');
      if (lane !== 'map') expect(c, `${lane}: category_type`).toContain('category_type');
    }
  });

  /* Not the measure, but the same failure shape: the card's "N inzeráty" badge
   * reads `source_count` off the row (migration 590), and every component test
   * feeds the row directly, so dropping the column here would delete the badge
   * from Browse with all of them green. */
  it('selects the ad count the card badge reads', () => {
    expect(cols(BROWSE_SELECT_COLUMNS.cards)).toContain('source_count');
  });
});

/* The "~NaN" Browse header (2026-09-25). The count asks the dismissal-aware
 * FUNCTION for an exact total, and when that misses its budget falls back to the
 * planner's estimate. PostgREST plans a count only for a table or view: over a
 * function call its Content-Range total is `*`, which the client parses to NaN.
 * Driven through the real client against a stubbed PostgREST, so the parse that
 * produced the NaN is the one under test. */
/* A refused answer as it arrives on the wire. Every count read is `head: true`,
 * which supabase-js sends as an HTTP HEAD, and a HEAD response carries no body,
 * so a count's SQLSTATE never reaches the client: only its status does. */
const failed = (status: number, body: object) => (init: RequestInit) =>
  ({
    ok: false,
    status,
    statusText: '',
    headers: new Headers(),
    text: async () => (init.method === 'HEAD' ? '' : JSON.stringify(body)),
  }) as unknown as Response;
/* An exact count that never answers: it settles only when its signal aborts,
 * on our own budget or the caller's say. */
const STALLS = (init: RequestInit) =>
  new Promise<Response>((_resolve, reject) => {
    init.signal!.addEventListener('abort', () => reject(init.signal!.reason));
  });

describe('the Browse cohort total is exact, or an error — never an estimate', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  /* PostgREST as the SPA sees it; `prefers` logs every count mode asked for,
   * with the path it went to. */
  const stubPostgrest = (answer: (init: RequestInit) => Response | Promise<Response>) => {
    const prefers: string[] = [];
    const fetch = vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
      const mode = (new Headers(init.headers).get('Prefer') ?? '').match(/count=(\w+)/)?.[1];
      if (mode) prefers.push(`${mode} ${new URL(String(input)).pathname}`);
      return answer(init);
    });
    vi.stubGlobal('fetch', fetch);
    return { prefers, fetch };
  };
  const counted = (total: string) => () =>
    ({
      ok: true,
      status: 200,
      statusText: 'OK',
      headers: new Headers({ 'Content-Range': `*/${total}` }),
      text: async () => '',
    }) as unknown as Response;
  /* The 20 s deadline runs 1000x faster here. */
  const fastDeadline = () => {
    const real = AbortSignal.timeout.bind(AbortSignal);
    return vi.spyOn(AbortSignal, 'timeout').mockImplementation((ms: number) => real(ms / 1000));
  };

  it('is the exact count of the dismissal-aware list, one HEAD', async () => {
    const { prefers, fetch } = stubPostgrest(counted('4577'));
    expect(await fetchBrowseCount(DEFAULT_FILTERS)).toBe(4577);
    expect(prefers).toEqual(['exact /rest/v1/rpc/browse_list_visible']);
    expect(fetch.mock.calls[0][1]?.method).toBe('HEAD');
  });

  it('counts a one-portal cohort with the rule and its conjunct', async () => {
    const { fetch } = stubPostgrest(counted('15'));
    expect(await fetchBrowseCount({ ...DEFAULT_FILTERS, portals: ['bazos'] })).toBe(15);
    const url = new URL(String(fetch.mock.calls.at(-1)![0]));
    expect(url.searchParams.get('all_sources')).toBe('ov.{bazos}');
    expect(url.searchParams.get('newest_ad_at_bazos')).toBe('not.is.null');
  });

  /* The 2026-10-13 fallback asked the plain relation for the planner's estimate
   * here; it answered "72 of ~15" on 2026-10-16. A slow count is now the
   * header's error with its retry, and nothing else is asked for. */
  it('fails a slow count at the one deadline, asking for no estimate', async () => {
    const budgets = fastDeadline();
    const { prefers } = stubPostgrest(STALLS);
    await expect(fetchBrowseCount(DEFAULT_FILTERS)).rejects.toMatchObject({
      name: 'ApiError',
      kind: 'timeout',
    });
    expect(budgets).toHaveBeenCalledWith(20_000);
    expect(prefers).toEqual(['exact /rest/v1/rpc/browse_list_visible']);
  });

  it('fails the count, which the header shows as an error, when there is no number at all', async () => {
    stubPostgrest(counted('*'));
    await expect(fetchBrowseCount(DEFAULT_FILTERS)).rejects.toThrow(/count unavailable/);
  });

  /* A caller abort is a cancellation, not an error banner. */
  it('cancels when the caller aborts during the count', async () => {
    const { fetch } = stubPostgrest(STALLS);
    const ctrl = new AbortController();
    const p = fetchBrowseCount(DEFAULT_FILTERS, { signal: ctrl.signal });
    await vi.waitFor(() => expect(fetch).toHaveBeenCalled());
    ctrl.abort();
    const err = await p.catch((e: unknown) => e);
    expect(err).not.toBeInstanceOf(ApiError);
    expect((err as Error).name).toBe('AbortError');
  });

  it('surfaces a refused count as the failure it is', async () => {
    const { prefers } = stubPostgrest(failed(400, { code: 'PGRST100', message: 'failed to parse filter' }));
    await expect(fetchBrowseCount(DEFAULT_FILTERS)).rejects.toMatchObject({
      name: 'ApiError',
      status: 400,
      kind: 'http',
    });
    expect(prefers).toEqual(['exact /rest/v1/rpc/browse_list_visible']);
  });
});

/* Migrations 537 + 629. Every Browse cohort read starts from ONE source, the
 * read model's source function, whatever the lenses: the dismissed exclusion
 * and the curated-set scope are its arguments, judged server-side under the
 * caller's RLS — neither set ever rides in the URL as property ids. */
describe('dismissed properties and curated sets are judged at the source', () => {
  afterEach(() => vi.restoreAllMocks());

  const revealed = { ...DEFAULT_FILTERS, showDismissed: true };
  const onePortal = { ...DEFAULT_FILTERS, portals: ['bazos'] };

  it('reads the source function by default, for cards and count', async () => {
    const s = stubReads();
    await fetchListingsForCards(DEFAULT_FILTERS, DEFAULT_SORT, null);
    await fetchBrowseCount(DEFAULT_FILTERS);
    expect(s.rpc.mock.calls.map((c) => c[0])).toEqual([
      'browse_list_visible', 'browse_list_visible',
    ]);
    expect(s.rpc.mock.calls[0].slice(1)).toEqual([{ hide_dismissed: true }, { get: true }]);
    expect(s.rpc.mock.calls[1].slice(1)).toEqual([
      { hide_dismissed: true }, { get: true, count: 'exact', head: true },
    ]);
    expect(s.from).not.toHaveBeenCalled();
  });

  it('reads the same source when dismissed properties are revealed, with the flag off', async () => {
    const s = stubReads();
    await fetchListingsForCards(revealed, DEFAULT_SORT, null);
    await fetchBrowseCount(revealed);
    expect(s.rpc.mock.calls.map((c) => c.slice(0, 2))).toEqual([
      ['browse_list_visible', { hide_dismissed: false }],
      ['browse_list_visible', { hide_dismissed: false }],
    ]);
    expect(s.from).not.toHaveBeenCalled();
  });

  it('reads the same source under one portal: there is no second relation', async () => {
    const s = stubReads();
    await fetchListingsForCards(onePortal, DEFAULT_SORT, null);
    await fetchListingsForCards({ ...onePortal, showDismissed: true }, DEFAULT_SORT, null);
    expect(s.rpc.mock.calls.map((c) => c[0])).toEqual(['browse_list_visible', 'browse_list_visible']);
    expect(s.from).not.toHaveBeenCalled();
  });

  /* Only the ACTIVE sides travel: postgrest-js would stringify a null GET
   * argument to the text 'null'. No membership read happens in the SPA. */
  it('hands the curated-set scope to the source as arguments, never as ids', async () => {
    const s = stubReads();
    const f = {
      ...DEFAULT_FILTERS,
      tags: { set: null, in: [5, 6], out: [7] },
      collections: { set: 'out' as const, in: [], out: [] },
      pipeline: { set: 'in' as const, in: [], out: [9] },
    };
    await fetchListingsForCards(f, DEFAULT_SORT, null);
    await fetchListingsForMap(f, 200);
    const list = s.rpc.mock.calls[0];
    expect(list[1]).toEqual({
      hide_dismissed: true,
      tags_in: [5, 6],
      tags_out: [7],
      collections_set: 'out',
      pipeline_set: 'in',
      pipeline_out: [9],
    });
    const [, cells, pins] = s.rpc.mock.calls;
    expect(cells[0]).toBe('browse_map_cells');
    expect(cells[1]).toMatchObject({ tags_in: [5, 6], collections_set: 'out', pipeline_out: [9] });
    expect(pins.slice(0, 2)).toEqual(['browse_list_visible', list[1]]);
    expect(s.inIds).toEqual([]);
    expect(s.from).not.toHaveBeenCalled();
  });

  it('tells the map cells and reads the map pins through the same twin', async () => {
    const s = stubReads();
    await fetchListingsForMap(DEFAULT_FILTERS, 200);
    const [cells, pins] = s.rpc.mock.calls;
    expect(cells[0]).toBe('browse_map_cells');
    expect((cells[1] as Record<string, unknown>).hide_dismissed).toBe(true);
    /* The pin limit IS the server's point budget: one number end to end. */
    expect((cells[1] as Record<string, unknown>).point_budget).toBe(200);
    expect(pins[0]).toBe('browse_list_visible');
  });

  it('puts the flag on the one Stats/map argument object', () => {
    expect(buildBrowseStatsArgs(DEFAULT_FILTERS, NO_PREFILTERS).hide_dismissed).toBe(true);
    expect(buildBrowseStatsArgs(revealed, NO_PREFILTERS).hide_dismissed).toBe(false);
  });
});

/* Rule #16: Stats and the map must describe the cohort the list describes. The
 * five Size-group controls (plot, usable, garden, parking count) narrowed the
 * list and were silently dropped on the way to the two aggregate RPCs, whose
 * parameters have existed since migrations 133/439. */
describe('buildBrowseStatsArgs sends the size bounds', () => {

  it('passes every Size-group bound through', () => {
    const args = buildBrowseStatsArgs({
      ...DEFAULT_FILTERS,
      estateAreaMin: 400, estateAreaMax: 1200,
      usableAreaMin: 60, usableAreaMax: 90,
      gardenAreaMin: 100, gardenAreaMax: 300,
      parkingLotsMin: 2,
    }, NO_PREFILTERS);
    expect(args.estate_area_min_filter).toBe(400);
    expect(args.estate_area_max_filter).toBe(1200);
    expect(args.usable_area_min_filter).toBe(60);
    expect(args.usable_area_max_filter).toBe(90);
    expect(args.garden_area_min_filter).toBe(100);
    expect(args.garden_area_max_filter).toBe(300);
    expect(args.parking_lots_min_filter).toBe(2);
  });

  it('sends null when unset, never undefined (the RPC default is the same)', () => {
    const args = buildBrowseStatsArgs(DEFAULT_FILTERS, NO_PREFILTERS);
    for (const k of [
      'estate_area_min_filter', 'estate_area_max_filter',
      'usable_area_min_filter', 'usable_area_max_filter',
      'garden_area_min_filter', 'garden_area_max_filter',
      'parking_lots_min_filter',
    ]) {
      expect(args[k]).toBeNull();
    }
  });
});

/* A page of cards asks per property; the view is read once per batch. */
describe('fetchIsDismissed batches per task', () => {
  afterEach(() => vi.restoreAllMocks());

  it('answers every call made in one task with one read', async () => {
    const { inIds } = stubReads({ property_dismissals_public: [{ property_id: 2 }] });
    const answers = await Promise.all([1, 2, 3, 2].map((id) => fetchIsDismissed(id)));
    expect(answers).toEqual([false, true, false, true]);
    expect(inIds).toEqual([[1, 2, 3]]);
    expect(supabase.from).toHaveBeenCalledWith('property_dismissals_public');
  });

  it('never sends more than 200 ids in one URL', async () => {
    const { inIds } = stubReads();
    await Promise.all(Array.from({ length: 450 }, (_, i) => fetchIsDismissed(i + 1)));
    expect(inIds.map((c) => c.length)).toEqual([200, 200, 50]);
  });

  it('fails every waiter of a failed read', async () => {
    stubReads({}, new Error('boom'));
    const outcomes = await Promise.allSettled([fetchIsDismissed(1), fetchIsDismissed(2)]);
    expect(outcomes.map((o) => o.status)).toEqual(['rejected', 'rejected']);
  });
});

/* One whole-set read answers every Browse row's note mark (MS16), and one batch
 * every board card's ads (MS7) — never one request per row or card. */
describe('note counts and board ads, one read each', () => {
  afterEach(() => vi.restoreAllMocks());

  it('counts the caller’s notes per property', async () => {
    stubReads({ property_notes_public: [1, 2, 3].map((id) => ({ id, property_id: id < 3 ? 42 : 7 })) });
    expect([...(await fetchNoteCounts())]).toEqual([[42, 2], [7, 1]]);
    expect(supabase.from).toHaveBeenCalledTimes(1);
  });

  it('groups the cards’ ads by property', async () => {
    const { inIds } = stubReads({
      property_sources_public: [{ id: 1, property_id: 42 }, { id: 2, property_id: 43 }, { id: 3, property_id: 42 }],
    });
    const byProperty = await fetchPropertySourcesByPropertyIds([42, 43]);
    expect(inIds).toEqual([[42, 43]]);
    expect(byProperty.get(42)?.map((a) => a.id)).toEqual([1, 3]);
  });
});
/* Rule #16 again, on the Stats pivot (lib/stats.ts fetchStatsPivot), whose
 * predecessor named its prefilters by hand. Every property-grain prefilter resolves ONCE, in resolveBrowsePrefilters,
 * and reaches list, map and Stats through the same `property_ids_filter` seam
 * (migration 378); the curated sets reach all three as the same scope arguments
 * (migration 629) — so no filter can narrow the list and leave the panel above
 * it counting the whole market. */
describe('Browse Stats resolves through the one prefilter path', () => {
  afterEach(() => vi.restoreAllMocks());

  const SPEC = { rows: ['disposition'], cols: [], measure: 'ppm2' };

  it('sends the chart spec beside the cohort to the one pivot RPC (migration 626)', async () => {
    const { rpcArgs } = stubReads();
    await fetchStatsPivot(DEFAULT_FILTERS, SPEC, { client: new QueryClient() });
    expect(supabase.rpc).toHaveBeenCalledWith('browse_stats_properties', expect.anything());
    /* The map's cohort object, byte for byte, plus the spec — one builder. */
    expect(rpcArgs[0]).toEqual({
      ...buildBrowseStatsArgs(DEFAULT_FILTERS, NO_PREFILTERS),
      spec: SPEC,
    });
  });

  /* Migration 629: the curated sets travel as the scope itself — the nine
   * `<set>_set/_in/_out` arguments, only the active sides — and the server
   * judges membership; Stats reads no membership view and sends no id list. */
  it('sends the curated-set scope as arguments, never as resolved ids', async () => {
    const { rpcArgs } = stubReads();
    await fetchStatsPivot({
      ...DEFAULT_FILTERS,
      tags: { set: null, in: [5, 6], out: [7] },
      collections: { set: null, in: [10, 12], out: [] },
      pipeline: { set: 'out', in: [], out: [] },
    }, SPEC, { client: new QueryClient() });
    expect(rpcArgs[0]).toMatchObject({
      tags_in: [5, 6],
      tags_out: [7],
      collections_in: [10, 12],
      pipeline_set: 'out',
      property_ids_filter: null,
    });
    for (const key of ['tags_set', 'collections_set', 'collections_out', 'pipeline_in',
      'pipeline_out', 'property_ids_excluded']) {
      expect(rpcArgs[0]).not.toHaveProperty(key);
    }
    expect(supabase.from).not.toHaveBeenCalledWith('property_tags_public');
    expect(supabase.from).not.toHaveBeenCalledWith('collection_properties_public');
  });

  it('resolves the prefilters once per filter set, however many charts read it', async () => {
    const { from, rpcArgs } = stubReads({ property_estimates_public: [{ property_id: 7 }] });
    /* The app's client keeps a read fresh for a minute (main.tsx). */
    const client = new QueryClient({ defaultOptions: { queries: { staleTime: 60_000 } } });
    const f = { ...DEFAULT_FILTERS, withEstimates: true };
    const reads = () => from.mock.calls.filter(([rel]) => rel === 'property_estimates_public');
    await Promise.all([
      fetchStatsPivot(f, SPEC, { client }),
      fetchStatsPivot(f, { ...SPEC, measure: 'price' }, { client }),
      fetchStatsPivot(f, { ...SPEC, rows: ['condition'] }, { client }),
    ]);
    expect(reads()).toHaveLength(1);
    expect(rpcArgs.map((a) => a.property_ids_filter)).toEqual([[7], [7], [7]]);
    /* The builder's next preview spec reuses them... */
    await fetchStatsPivot(f, { ...SPEC, measure: 'area' }, { client });
    expect(reads()).toHaveLength(1);
    /* ...and a Browse sweep (invalidating the stats root) re-resolves them. */
    await client.invalidateQueries({ queryKey: ['stats'] });
    await fetchStatsPivot(f, SPEC, { client });
    expect(reads()).toHaveLength(2);
  });

  it('never sends the parameters the RPCs ignore — estimates resolve to ids', async () => {
    const { rpcArgs } = stubReads();
    await fetchStatsPivot({ ...DEFAULT_FILTERS, withEstimates: true }, SPEC, { client: new QueryClient() });
    for (const key of ['tag_ids', 'with_estimates', 'city_index_rules', 'city_proximity',
      'districts_context_filter']) {
      expect(rpcArgs[0]).not.toHaveProperty(key);
    }
  });

  it('carries the broker in property_ids_filter; the server judged its portal and status', async () => {
    const { rpcArgs } = stubReads();
    await fetchStatsPivot({
      ...DEFAULT_FILTERS, brokerId: 527, portals: ['idnes'], status: 'active',
      portalsExcluded: ['sreality'], portalsExcludedActiveOnly: true,
    }, SPEC, { client: new QueryClient() });
    expect(fetchBrokerPropertyIds).toHaveBeenCalledWith(527, 'active', ['idnes']);
    expect(rpcArgs[0].property_ids_filter).toEqual([1]);
    expect(rpcArgs[0].portal_filter).toBeNull();
    expect(rpcArgs[0].active_only_filter).toBe(false);
    /* The exclusion is about the property, so it reaches the RPC under a broker too. */
    expect(rpcArgs[0].portal_excluded_filter).toEqual(['sreality']);
    expect(rpcArgs[0].portal_excluded_active_only).toBe(true);
  });

  it('sends the portal rule\'s two inputs without a broker', () => {
    const args = buildBrowseStatsArgs(
      { ...DEFAULT_FILTERS, portals: ['idnes'], status: 'inactive' },
      NO_PREFILTERS,
    );
    expect(args.portal_filter).toEqual(['idnes']);
    expect([args.active_only_filter, args.inactive_only_filter]).toEqual([false, true]);
  });

  it('never names the retired listing_ids_filter on the map, and carries the broker', async () => {
    const { rpcArgs } = stubReads();
    await fetchListingsForMap({ ...DEFAULT_FILTERS, brokerId: 527 }, 200);
    expect(rpcArgs[0]).not.toHaveProperty('listing_ids_filter');
    expect(rpcArgs[0].property_ids_filter).toEqual([1]);
    expect(rpcArgs[0].point_budget).toBe(200);
  });
});

/* A pin is a listed property with a point: the pin read is the list's read, one
 * relation under one filter chain, so no status, portal or dismissal rule can hold
 * on one lane and not the other. Driven through the real client, so the URLs
 * PostgREST receives are what is compared. */
describe('the map pins read the list\'s relation through the list\'s chain', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('sends one relation and one filter set; only select, order, limit and the point guard differ', async () => {
    const urls: URL[] = [];
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = new URL(String(input));
      urls.push(url);
      const body = url.pathname.endsWith('/browse_map_cells')
        ? { clustered: false, cells: null, level: null, total: 0 }
        : [];
      return {
        ok: true,
        status: 200,
        statusText: 'OK',
        headers: new Headers(),
        text: async () => JSON.stringify(body),
      } as unknown as Response;
    }));
    const filters = (u: URL): string[] => [...u.searchParams]
      .filter(([k]) => !['select', 'order', 'limit'].includes(k))
      .map(([k, v]) => `${k}=${v}`)
      .sort();
    for (const showDismissed of [false, true]) {
      urls.length = 0;
      const f: ListingFilters = {
        ...DEFAULT_FILTERS,
        showDismissed,
        portals: ['sreality'],
        bounds: { north: 50.18, south: 49.94, east: 14.71, west: 14.22 },
      };
      await fetchListingsForMap(f, 200);
      await fetchListingsForCards(f, DEFAULT_SORT, null);
      const [pins, ...lists] = urls.filter((u) => !u.pathname.endsWith('/browse_map_cells'));
      if (!showDismissed) expect(pins.pathname).toBe('/rest/v1/rpc/browse_list_visible');
      expect(filters(pins)).toEqual(expect.arrayContaining([
        'all_sources=ov.{sreality}', 'newest_ad_at_sreality=not.is.null',
        'lat=gte.49.94', 'lat=lte.50.18', 'lng=gte.14.22', 'lng=lte.14.71',
      ]));
      expect(pins.searchParams.has('order') || pins.searchParams.has('limit')).toBe(false);
      expect(lists).toHaveLength(1);
      for (const list of lists) {
        expect(list.pathname, `showDismissed=${showDismissed}`).toBe(pins.pathname);
        expect(filters(pins)).toEqual([...filters(list), 'lat=not.is.null', 'lng=not.is.null'].sort());
        expect(list.searchParams.has('order') && list.searchParams.has('limit')).toBe(true);
      }
    }
  });
});

/* The '__unknown__' chip ("Neuvedeno") on furnished, ownership and condition: NULL or a
 * value outside the canon (toolkit.filter_compiler._enum_or_unknown_clause, the RPCs'
 * own arrays). Without it a selection keeps the auto path's shape: one value is `eq`. */
describe('the Neuvedeno chip reaches the list as NULL or off-canon', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  const sent = async (patch: Partial<ListingFilters>): Promise<URLSearchParams> => {
    const fetch = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => ({
      ok: true,
      status: 200,
      statusText: 'OK',
      headers: new Headers({ 'Content-Range': '*/1' }),
      text: async () => '',
    }) as unknown as Response);
    vi.stubGlobal('fetch', fetch);
    await fetchBrowseCount({ ...DEFAULT_FILTERS, ...patch });
    return new URL(String(fetch.mock.calls.at(-1)![0])).searchParams;
  };
  const conditionCanon = CONDITION_CANONICAL.join(',');

  it('keeps one stated condition an equality and several an IN', async () => {
    expect((await sent({ conditionMatch: ['novostavba'] })).get('condition')).toBe('eq.novostavba');
    expect((await sent({ conditionMatch: ['novostavba', 'dobry'] })).get('condition'))
      .toBe('in.(novostavba,dobry)');
  });

  it('sends Neuvedeno alone in the shape furnished sends it', async () => {
    const condition = await sent({ conditionMatch: ['__unknown__'] });
    expect(condition.get('condition')).toBeNull();
    expect(condition.getAll('or')).toContain(`(condition.is.null,condition.not.in.(${conditionCanon}))`);
    const furnished = await sent({ furnished: ['__unknown__'] });
    expect(furnished.getAll('or'))
      .toContain(`(furnished.is.null,furnished.not.in.(${FURNISHED_CANONICAL.join(',')}))`);
  });

  it('ORs stated conditions with Neuvedeno', async () => {
    const q = await sent({ conditionMatch: ['novostavba', '__unknown__'] });
    expect(q.getAll('or'))
      .toContain(`(condition.in.(novostavba),condition.is.null,condition.not.in.(${conditionCanon}))`);
  });

  it('sends the selection to the aggregates as it stands, the sentinel included', () => {
    const args = buildBrowseStatsArgs({ ...DEFAULT_FILTERS, conditionMatch: ['__unknown__'] }, NO_PREFILTERS);
    expect(args.condition_match_filter).toEqual(['__unknown__']);
  });
});
