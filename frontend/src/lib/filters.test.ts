/* Pure-function tests for the filter state plumbing.
 *
 * Covers:
 *   - URL round-trip through fromSearchParams / toSearchParams, including
 *     the new centre+radius + locationMode fields.
 *   - isDefault correctly returns false when any non-default field is set.
 *   - listingFiltersToRegistryView ↔ applyRegistryUpdate symmetry.
 *   - Tri-state amenities pivot 'any' / 'yes' / 'no' ↔ null / true / false
 *     at the adapter boundary.
 *
 * No jsdom needed — these are all pure transforms on the ListingFilters
 * shape. Render-time tests for FilterForm + filter-controls primitives
 * land in a follow-up batch once jsdom + @testing-library/react are
 * pulled in.
 */

import { describe, expect, it } from 'vitest';

import {
  DEFAULT_FILTERS,
  pipelineViewFilters,
  applyRegistryUpdate,
  applyRegistryUpdates,
  buildingMaterialToValues,
  filtersEqualForPreset,
  filtersForPreset,
  readPresetSpec,
  filtersToWatchdogSpec,
  fromSearchParams,
  isDefault,
  type BuildingMaterial,
  type ListingFilters,
  type MapBounds,
  type PresetSpec,
  listingFiltersToRegistryView,
  summarise,
  toSearchParams,
  watchdogNameSuggestion,
} from './filters';
import { districtsFilterClause } from './districtCodes';
import { DEFAULT_SORT, parseSort, sortToParam } from './browseSort';
import { BUILDING_MATERIAL_BUCKETS, filterById } from './filterRegistry.generated';
import type { Disposition } from './types';

describe('URL round-trip', () => {
  it('preserves the empty default state across an empty URL', () => {
    const sp = toSearchParams(DEFAULT_FILTERS);
    expect(sp.toString()).toBe('');
    expect(fromSearchParams(new URLSearchParams())).toEqual(DEFAULT_FILTERS);
  });

  it('round-trips price + area + tri-state + dispositions', () => {
    const f: ListingFilters = {
      ...DEFAULT_FILTERS,
      priceMin: 12_000,
      priceMax: 28_000,
      areaMin: 40,
      areaMax: 80,
      dispositions: ['2+kk', '2+1'],
      hasBalcony: 'yes',
      terrace: 'no',
      furnished: ['castecne', '__unknown__'],
      ownership: ['osobni'],
    };
    const round = fromSearchParams(toSearchParams(f));
    expect(round.priceMin).toBe(12_000);
    expect(round.priceMax).toBe(28_000);
    expect(round.areaMin).toBe(40);
    expect(round.areaMax).toBe(80);
    expect(round.dispositions).toEqual(['2+kk', '2+1']);
    expect(round.hasBalcony).toBe('yes');
    expect(round.terrace).toBe('no');
    expect(round.furnished).toEqual(['castecne', '__unknown__']);
    expect(round.ownership).toEqual(['osobni']);
  });

  /* Every disposition the registry offers must survive the URL, because Browse
   * state IS the URL: a value the allowlist drops deselects its own pill. */
  it('round-trips every registry disposition, not just the ones under 6', () => {
    const all = (filterById('dispositions')?.enum_values ?? []).map((o) => String(o.value));
    expect(all).toContain('6+kk');
    expect(all).toContain('9+1');
    const round = fromSearchParams(
      toSearchParams({ ...DEFAULT_FILTERS, dispositions: all as Disposition[] }),
    );
    expect(round.dispositions).toEqual(all);
  });

  it('round-trips the includeNoPrice toggle (emitted only when on)', () => {
    expect(toSearchParams(DEFAULT_FILTERS).has('no_price')).toBe(false);
    const f: ListingFilters = { ...DEFAULT_FILTERS, priceMax: 5_000_000, includeNoPrice: true };
    const sp = toSearchParams(f);
    expect(sp.get('no_price')).toBe('1');
    expect(fromSearchParams(sp).includeNoPrice).toBe(true);
    // Absent param parses back to false (old URLs / presets self-heal).
    expect(fromSearchParams(new URLSearchParams('price=,5000000')).includeNoPrice).toBe(false);
  });

  it('round-trips conditionMatch and drops unknown values', () => {
    const f: ListingFilters = {
      ...DEFAULT_FILTERS,
      conditionMatch: ['novostavba', 'po_rekonstrukci'],
    };
    const round = fromSearchParams(toSearchParams(f));
    expect(round.conditionMatch).toEqual(['novostavba', 'po_rekonstrukci']);
    // Unknown values in the URL must not leak into state — they
    // would otherwise reach Supabase and tighten the cohort to zero.
    const dirty = new URLSearchParams('condition=novostavba,bogus_value');
    expect(fromSearchParams(dirty).conditionMatch).toEqual(['novostavba']);
  });

  it("round-trips the condition filter's Neuvedeno chip to the URL and the Watchdog", () => {
    const f: ListingFilters = { ...DEFAULT_FILTERS, conditionMatch: ['novostavba', '__unknown__'] };
    expect(toSearchParams(f).get('condition')).toBe('novostavba,__unknown__');
    expect(fromSearchParams(toSearchParams(f)).conditionMatch).toEqual(['novostavba', '__unknown__']);
    expect(filtersToWatchdogSpec(f).spec.condition_match).toEqual(['novostavba', '__unknown__']);
  });

  it('round-trips the portals and drops a code that is not one of them', () => {
    const f: ListingFilters = { ...DEFAULT_FILTERS, portals: ['idnes', 'realitymix'] };
    expect(fromSearchParams(toSearchParams(f)).portals).toEqual(['idnes', 'realitymix']);
    // One portal names a column (its newest-ad date), so a stale or mistyped code
    // would answer 400 on the list, the count and the map.
    const dirty = new URLSearchParams('portal=idnes,IDNES,retired');
    expect(fromSearchParams(dirty).portals).toEqual(['idnes']);
  });

  it('round-trips the excluded portals and their active-ads switch', () => {
    const f: ListingFilters = {
      ...DEFAULT_FILTERS, portals: ['idnes'], portalsExcluded: ['sreality', 'bazos'],
      portalsExcludedActiveOnly: true,
    };
    const sp = toSearchParams(f);
    expect(sp.get('portal_excl')).toBe('sreality,bazos');
    expect(sp.get('portal_excl_active')).toBe('1');
    const back = fromSearchParams(sp);
    expect(back.portalsExcluded).toEqual(['sreality', 'bazos']);
    expect(back.portalsExcludedActiveOnly).toBe(true);
    expect(toSearchParams(DEFAULT_FILTERS).has('portal_excl')).toBe(false);
    expect(fromSearchParams(new URLSearchParams('portal_excl=sreality,bogus')).portalsExcluded)
      .toEqual(['sreality']);
  });

  it('round-trips the price-change filters', () => {
    const f: ListingFilters = {
      ...DEFAULT_FILTERS,
      priceChangeCountMin: 2,
      priceChangeWindowDays: 90,
      totalPriceChangePct: -10,
      withEstimates: true,
    };
    const round = fromSearchParams(toSearchParams(f));
    expect(round).toEqual(f);
    // A window outside the 30/90/365 set must not leak into state — the
    // backend only has precomputed columns for the legal windows.
    const dirty = new URLSearchParams('changes_min=2&changes_window=45');
    const parsed = fromSearchParams(dirty);
    expect(parsed.priceChangeCountMin).toBe(2);
    expect(parsed.priceChangeWindowDays).toBeNull();
    // The retired per-direction params are gone — old URLs degrade to
    // the default state rather than throwing.
    const legacy = new URLSearchParams('drops_min=2&sites_min=2&drop_pct_min=10');
    expect(fromSearchParams(legacy)).toEqual(DEFAULT_FILTERS);
  });

  it('the retired condition-level bounds degrade silently (condition scoring parked)', () => {
    const url = new URLSearchParams('bld_cond_min=3&bld_cond_max=5&apt_cond_min=2&apt_cond_max=4');
    expect(fromSearchParams(url)).toEqual(DEFAULT_FILTERS);
    const stored = { ...DEFAULT_FILTERS, buildingConditionLevelMin: 3, apartmentConditionLevelMax: 4 };
    expect(readPresetSpec(stored as ListingFilters).filters).toEqual(DEFAULT_FILTERS);
  });

  it('round-trips the subtype multi-select', () => {
    const f: ListingFilters = {
      ...DEFAULT_FILTERS,
      categoryMain: ['dum'],
      subtype: ['rodinny_dum', 'vila'],
    };
    const round = fromSearchParams(toSearchParams(f));
    expect(round.subtype).toEqual(['rodinny_dum', 'vila']);
    expect(toSearchParams(f).get('subtype')).toBe('rodinny_dum,vila');
    // Empty subtype emits no param.
    expect(toSearchParams(DEFAULT_FILTERS).has('subtype')).toBe(false);
  });

  it('round-trips the centre+radius mode and coordinates', () => {
    const f: ListingFilters = {
      ...DEFAULT_FILTERS,
      locationMode: 'center_radius',
      centerRadius: { lat: 50.0867, lng: 14.4205, radius_m: 1500 },
    };
    const round = fromSearchParams(toSearchParams(f));
    expect(round.locationMode).toBe('center_radius');
    expect(round.centerRadius).not.toBeNull();
    expect(round.centerRadius!.lat).toBeCloseTo(50.0867, 4);
    expect(round.centerRadius!.lng).toBeCloseTo(14.4205, 4);
    expect(round.centerRadius!.radius_m).toBe(1500);
  });

  it('round-trips the bounds box (viewport mode)', () => {
    const f = {
      ...DEFAULT_FILTERS,
      bounds: { west: 14.3, south: 50.0, east: 14.5, north: 50.2 },
    };
    const round = fromSearchParams(toSearchParams(f));
    expect(round.bounds).toEqual({
      west: 14.3, south: 50.0, east: 14.5, north: 50.2,
    });
    expect(round.locationMode).toBe('viewport');
  });

  it('omits locationMode and center from the URL on the default', () => {
    const sp = toSearchParams(DEFAULT_FILTERS);
    expect(sp.has('locmode')).toBe(false);
    expect(sp.has('center')).toBe(false);
  });

  const JIHLAVA = { name: 'Jihlava', context: null, level: 'obec' as const, id: 586846 };
  const BENESE_PLZEN = {
    name: 'Edvarda Beneše', context: 'Plzeň', level: 'ulice' as const, id: 700101,
  };

  it('round-trips a chip at each of the five levels, coarsest first', () => {
    const f = {
      ...DEFAULT_FILTERS,
      districts: [
        { name: 'Kraj Vysočina', context: null, level: 'kraj' as const, id: 108 },
        { name: 'Jihlava', context: null, level: 'okres' as const, id: 3707 },
        JIHLAVA,
        { name: 'Žižkov', context: 'Praha', level: 'cast_obce' as const, id: 490067 },
        BENESE_PLZEN,
      ],
    };
    const sp = toSearchParams(f);
    expect(sp.get('districts_lvl')).toBe('kraj,okres,obec,cast_obce,ulice');
    expect(sp.get('districts_id')).toBe('108,3707,586846,490067,700101');
    expect(fromSearchParams(sp).districts).toEqual(f.districts);
  });

  it('always writes the level and code; context and exclude flags only when a chip needs them', () => {
    const sp = toSearchParams({ ...DEFAULT_FILTERS, districts: [JIHLAVA] });
    expect(sp.get('districts_lvl')).toBe('obec');
    expect(sp.get('districts_id')).toBe('586846');
    expect(sp.has('districts_ctx')).toBe(false);
    expect(sp.has('districts_excl')).toBe(false);
  });

  it('keeps two same-name streets of two towns as two chips, with their contexts', () => {
    const f = {
      ...DEFAULT_FILTERS,
      districts: [
        BENESE_PLZEN,
        { ...BENESE_PLZEN, context: 'Olomouc', id: 700202 },
        JIHLAVA,
      ],
    };
    const sp = toSearchParams(f);
    expect(sp.get('districts_ctx')).toBe('Plze%C5%88,Olomouc,');
    expect(fromSearchParams(sp).districts).toEqual(f.districts);
  });

  it('round-trips include and exclude chips through the parallel flags', () => {
    const f = {
      ...DEFAULT_FILTERS,
      districts: [{ ...JIHLAVA, excluded: true }, BENESE_PLZEN],
    };
    const sp = toSearchParams(f);
    expect(sp.get('districts_excl')).toBe('1,0');
    expect(fromSearchParams(sp).districts).toEqual(f.districts);
  });

  /* A chip without a (level, code) cannot exist: an old bookmark carries no unit
   * to filter on, so the parser drops it rather than guessing one by name. */
  it.each([
    ['a name-only chip', 'districts=Brno'],
    ['an unknown level', 'districts=Brno&districts_lvl=toString&districts_id=582786'],
    ['a missing code', 'districts=Brno&districts_lvl=obec'],
    ['the old no-match code', 'districts=Brno&districts_lvl=obec&districts_id=-1'],
    ['a zero code', 'districts=Brno&districts_lvl=obec&districts_id=0'],
    ['a fractional code', 'districts=Brno&districts_lvl=obec&districts_id=1.5'],
    ['a non-numeric code', 'districts=Brno&districts_lvl=obec&districts_id=abc'],
  ])('drops %s from a URL', (_label, qs) => {
    expect(fromSearchParams(new URLSearchParams(qs)).districts).toEqual([]);
  });

  /* A stored preset is read without a URL in between (the Feed reads it straight
   * into a query), so it passes the same chip guard the URL parser does. */
  it('drops a stored preset chip without a level and code, and the clause ignores it', () => {
    const { filters } = readPresetSpec({
      filters: {
        ...DEFAULT_FILTERS,
        districts: [
          { name: 'Brno', context: null },
          { name: 'Sadová', context: 'Praha', level: 'locality', id: 554782 },
          JIHLAVA,
        ],
      } as unknown as ListingFilters,
    });
    expect(filters.districts).toEqual([JIHLAVA]);
    expect(districtsFilterClause(filters.districts)).toBe('and(or(obec_id.in.(586846)))');
  });

  it('drops only the code-less chip and keeps its neighbours aligned', () => {
    const sp = new URLSearchParams(
      'districts=Brno,Jihlava&districts_ctx=,&districts_excl=1,0&districts_lvl=,obec&districts_id=,586846',
    );
    expect(fromSearchParams(sp).districts).toEqual([JIHLAVA]);
  });
});

/* The bug this fixes: the chip ANDed the pipeline onto Browse's DEFAULT cohort
 * (byt + pronájem), so an operator with 45 deals — sale flats, houses,
 * commercial — saw 1. The lens has to load a neutral cohort, not narrow one. */
describe('pipelineViewFilters', () => {
  it('constrains nothing except the pipeline itself', () => {
    const f = pipelineViewFilters();
    expect(f.pipeline).toEqual({ set: 'in', in: [], out: [] });
    expect(f.categoryMain).toEqual([]);   // not ['byt']
    expect(f.categoryType).toBeNull();    // not 'pronajem'
    expect(f.districts).toEqual([]);
    expect(f.priceMin).toBeNull();
    expect(f.priceMax).toBeNull();
    expect(f.areaMin).toBeNull();
    expect(f.bounds).toBeNull();
  });

  it('keeps status "any" so a delisted deal stays visible', () => {
    // A property whose listing went inactive mid-negotiation is exactly the one
    // an operator needs to see on the board.
    expect(pipelineViewFilters().status).toBe('any');
  });

  it('round-trips through the URL', () => {
    const sp = toSearchParams(pipelineViewFilters());
    expect(sp.get('pipeline')).toBe('any');
    expect(sp.get('deal')).toBe('any');
    expect(fromSearchParams(sp)).toEqual(pipelineViewFilters());
  });
});

describe('nullable deal type', () => {
  it('encodes "no deal-type constraint" as deal=any', () => {
    const f: ListingFilters = { ...DEFAULT_FILTERS, categoryType: null };
    expect(toSearchParams(f).get('deal')).toBe('any');
    expect(fromSearchParams(toSearchParams(f)).categoryType).toBeNull();
  });

  it('reads an ABSENT param as the prodej default, and writes pronajem out', () => {
    // The empty URL stays byte-identical to DEFAULT_FILTERS; since 2026-10-20
    // that default is Prodej, so a rent cohort names its deal in the link.
    expect(fromSearchParams(new URLSearchParams()).categoryType).toBe('prodej');
    expect(toSearchParams(DEFAULT_FILTERS).has('deal')).toBe(false);
    const rent = toSearchParams({ ...DEFAULT_FILTERS, categoryType: 'pronajem' });
    expect(rent.get('deal')).toBe('pronajem');
    expect(fromSearchParams(rent).categoryType).toBe('pronajem');
  });

  it('drops the deal phrase from the headline instead of printing null', () => {
    const line = summarise({ ...DEFAULT_FILTERS, categoryType: null }, 12);
    expect(line).not.toMatch(/null|undefined/);
    expect(line).toMatch(/12/);
  });

  it('names both sides of the portal filter, so an exclusion is never invisible', () => {
    expect(summarise(DEFAULT_FILTERS, 1)).not.toMatch(/Sreality|iDNES/);
    expect(summarise({ ...DEFAULT_FILTERS, portals: ['idnes'], portalsExcluded: ['sreality'] }, 1))
      .toMatch(/on iDNES −Sreality$/);
    expect(summarise({
      ...DEFAULT_FILTERS, portalsExcluded: ['sreality'], portalsExcludedActiveOnly: true,
    }, 1)).toMatch(/−Sreality \(active ads\)$/);
  });
});

describe('pipeline scope URL codec', () => {
  it('round-trips "any stage"', () => {
    const f: ListingFilters = { ...DEFAULT_FILTERS, pipeline: { set: 'in', in: [], out: [] } };
    const sp = toSearchParams(f);
    expect(sp.get('pipeline')).toBe('any');
    expect(fromSearchParams(sp)).toEqual(f);
  });

  it('round-trips a stage narrowing', () => {
    const f: ListingFilters = { ...DEFAULT_FILTERS, pipeline: { set: null, in: [12, 13], out: [] } };
    const sp = toSearchParams(f);
    expect(sp.get('pipeline')).toBe('12,13');
    expect(fromSearchParams(sp)).toEqual(f);
  });

  it('emits nothing when the scope is off', () => {
    expect(toSearchParams(DEFAULT_FILTERS).has('pipeline')).toBe(false);
    expect(fromSearchParams(new URLSearchParams()).pipeline).toBeNull();
  });

  /* The one behaviour change of the curated-set codec (lib/setScope): junk once
   * meant "any stage" here alone; now junk on ANY side of ANY set reads as off,
   * uniformly. An old bundle shown a new param ignores it and shows a WIDER
   * cohort, never an inverted one. */
  it('degrades an unparseable stage list to off, like every curated-set side', () => {
    expect(fromSearchParams(new URLSearchParams('pipeline=abc')).pipeline).toBeNull();
    expect(fromSearchParams(new URLSearchParams('pipeline_excluded=abc')).pipeline).toBeNull();
  });

  it('round-trips "mimo pipeline" and a stage excluded from the whole pipeline', () => {
    for (const pipeline of [
      { set: 'out' as const, in: [], out: [] },
      { set: 'in' as const, in: [], out: [9] },
    ]) {
      const f: ListingFilters = { ...DEFAULT_FILTERS, pipeline };
      expect(fromSearchParams(toSearchParams(f))).toEqual(f);
    }
    expect(toSearchParams({ ...DEFAULT_FILTERS, pipeline: { set: 'out', in: [], out: [] } })
      .get('pipeline_excluded')).toBe('any');
  });
});

describe('collection scope URL codec', () => {
  it('round-trips a selection as a CSV of ids', () => {
    const f: ListingFilters = { ...DEFAULT_FILTERS, collections: { set: null, in: [4, 9], out: [] } };
    const sp = toSearchParams(f);
    expect(sp.get('collections')).toBe('4,9');
    expect(fromSearchParams(sp)).toEqual(f);
  });

  it('emits nothing when nothing is selected, and parses absence as off', () => {
    expect(toSearchParams(DEFAULT_FILTERS).has('collections')).toBe(false);
    expect(fromSearchParams(new URLSearchParams()).collections).toBeNull();
  });

  it('drops junk ids rather than inventing a selection', () => {
    expect(fromSearchParams(new URLSearchParams('collections=4,abc,0')).collections)
      .toEqual({ set: null, in: [4], out: [] });
  });

  it('round-trips "v žádné kolekci" as collections_excluded=any', () => {
    const f: ListingFilters = { ...DEFAULT_FILTERS, collections: { set: 'out', in: [], out: [] } };
    const sp = toSearchParams(f);
    expect(sp.toString()).toContain('collections_excluded=any');
    expect(fromSearchParams(sp)).toEqual(f);
  });
});

describe('broker scope URL codec', () => {
  it('round-trips a broker id', () => {
    const f: ListingFilters = { ...DEFAULT_FILTERS, brokerId: 527 };
    const sp = toSearchParams(f);
    expect(sp.get('broker')).toBe('527');
    expect(fromSearchParams(sp)).toEqual(f);
  });

  it('emits nothing when the scope is off', () => {
    expect(toSearchParams(DEFAULT_FILTERS).has('broker')).toBe(false);
    expect(fromSearchParams(new URLSearchParams()).brokerId).toBeNull();
  });

  it('drops an unparseable id to off, like every other integer param', () => {
    // There is no "any broker" to degrade to (unlike the pipeline's "any
    // stage"), so this follows parkingLotsMin & co: not a number → unset.
    expect(fromSearchParams(new URLSearchParams('broker=abc')).brokerId).toBeNull();
  });
});

/* Hidden is the default, so a URL (or preset) from before migration 537 means
 * exactly what it does now: only the reveal is ever spelled out. */
describe('dismissed reveal URL codec', () => {
  it('spells only the reveal', () => {
    expect(toSearchParams(DEFAULT_FILTERS).has('dismissed')).toBe(false);
    const f: ListingFilters = { ...DEFAULT_FILTERS, showDismissed: true };
    const sp = toSearchParams(f);
    expect(sp.get('dismissed')).toBe('show');
    expect(fromSearchParams(sp)).toEqual(f);
  });

  it('reads anything but `show` as hidden', () => {
    expect(fromSearchParams(new URLSearchParams('dismissed=1')).showDismissed).toBe(false);
    expect(fromSearchParams(new URLSearchParams()).showDismissed).toBe(false);
  });

  it('is reported to a watchdog rather than silently carried', () => {
    const { unsupported } = filtersToWatchdogSpec({ ...DEFAULT_FILTERS, showDismissed: true });
    expect(unsupported).toContain('dismissed properties');
    expect(filtersToWatchdogSpec(DEFAULT_FILTERS).unsupported).not.toContain('dismissed properties');
  });
});

describe('isDefault', () => {
  it('returns true for the canonical default state', () => {
    expect(isDefault(DEFAULT_FILTERS)).toBe(true);
  });

  it('returns false when any single filter is non-default', () => {
    expect(isDefault({ ...DEFAULT_FILTERS, priceMin: 10_000 })).toBe(false);
    expect(isDefault({ ...DEFAULT_FILTERS, hasBalcony: 'yes' })).toBe(false);
    expect(
      isDefault({ ...DEFAULT_FILTERS, locationMode: 'center_radius' }),
    ).toBe(false);
    expect(
      isDefault({
        ...DEFAULT_FILTERS,
        centerRadius: { lat: 50, lng: 14, radius_m: 1000 },
      }),
    ).toBe(false);
  });
});

describe('listingFiltersToRegistryView', () => {
  it('translates tri-state amenities to bool | null', () => {
    const f: ListingFilters = {
      ...DEFAULT_FILTERS,
      hasBalcony: 'yes',
      hasLift: 'any',
      garage: 'no',
    };
    const view = listingFiltersToRegistryView(f);
    expect(view.has_balcony).toBe(true);
    expect(view.has_lift).toBeNull();
    expect(view.garage).toBe(false);
  });

  it('normalises empty multi-value arrays to null for the registry', () => {
    const view = listingFiltersToRegistryView(DEFAULT_FILTERS);
    expect(view.tags).toBeNull();
    expect(view.dispositions).toBeNull();
    expect(view.districts).toBeNull();
    expect(view.condition_match).toBeNull();
  });

  it('passes a populated conditionMatch array through to the registry', () => {
    const view = listingFiltersToRegistryView({
      ...DEFAULT_FILTERS,
      conditionMatch: ['novostavba', 'velmi_dobry'],
    });
    expect(view.condition_match).toEqual(['novostavba', 'velmi_dobry']);
  });

  it('passes scalar fields through unchanged', () => {
    const f: ListingFilters = {
      ...DEFAULT_FILTERS,
      priceMin: 10_000,
      priceMax: null,
      categoryMain: ['dum'],
      status: 'active',
    };
    const view = listingFiltersToRegistryView(f);
    expect(view.min_price_czk).toBe(10_000);
    expect(view.max_price_czk).toBeNull();
    // category is multi-select now: array coerces to itself (empty -> null).
    expect(view.category_main_in).toEqual(['dum']);
    expect(view.status).toBe('active');
  });
});

describe('applyRegistryUpdate', () => {
  it('pivots bool | null back to tri-state on tri-state amenities', () => {
    let f: ListingFilters = { ...DEFAULT_FILTERS };
    f = applyRegistryUpdate(f, 'has_balcony', true);
    expect(f.hasBalcony).toBe('yes');
    f = applyRegistryUpdate(f, 'has_balcony', false);
    expect(f.hasBalcony).toBe('no');
    f = applyRegistryUpdate(f, 'has_balcony', null);
    expect(f.hasBalcony).toBe('any');
  });

  it('normalises null to empty arrays for the multi-value fields', () => {
    let f: ListingFilters = { ...DEFAULT_FILTERS, dispositions: ['2+kk'] };
    f = applyRegistryUpdate(f, 'dispositions', null);
    expect(f.dispositions).toEqual([]);
  });

  it('passes a curated-set scope through both ways, null = off', () => {
    const tags = { set: null, in: [5], out: [7] };
    let f = applyRegistryUpdate(DEFAULT_FILTERS, 'tags', tags);
    expect(f.tags).toEqual(tags);
    expect(listingFiltersToRegistryView(f).tags).toEqual(tags);
    f = applyRegistryUpdate(f, 'tags', null);
    expect(f.tags).toBeNull();
  });

  it('round-trips the tag rule\'s second side through the URL (`tags_excluded`)', () => {
    const tags = { set: null, in: [5], out: [7, 9] };
    const sp = toSearchParams({ ...DEFAULT_FILTERS, tags });
    expect(sp.get('tags')).toBe('5');
    expect(sp.get('tags_excluded')).toBe('7,9');
    expect(fromSearchParams(sp).tags).toEqual(tags);
    expect(toSearchParams(DEFAULT_FILTERS).has('tags_excluded')).toBe(false);
    /* The watchdog has no tag clause on either side (rule 16). */
    expect(filtersToWatchdogSpec({ ...DEFAULT_FILTERS, tags: { set: null, in: [], out: [7] } })
      .unsupported).toContain('tags');
  });

  it('names every active curated set in the headline, in Czech', () => {
    const line = summarise({
      ...DEFAULT_FILTERS,
      tags: { set: 'out', in: [], out: [] },
      collections: { set: null, in: [4], out: [] },
      pipeline: { set: 'out', in: [], out: [] },
    }, 3);
    expect(line).toContain('bez štítků');
    expect(line).toContain('v kolekci (1 kolekce)');
    expect(line).toContain('mimo pipeline');
    expect(summarise(DEFAULT_FILTERS, 3)).not.toMatch(/pipeline|kolekc|štít/);
  });

  it('accepts DistrictChip[] for districts; null clears the list', () => {
    let f = { ...DEFAULT_FILTERS };
    const chip = { name: 'Edvarda Beneše', context: 'Plzeň', level: 'ulice', id: 700101 };
    f = applyRegistryUpdate(f, 'districts', [chip]);
    expect(f.districts).toEqual([chip]);

    f = applyRegistryUpdate(f, 'districts', null);
    expect(f.districts).toEqual([]);
  });

  it('normalises conditionMatch null ↔ array at the registry boundary', () => {
    let f: ListingFilters = {
      ...DEFAULT_FILTERS,
      conditionMatch: ['novostavba'],
    };
    f = applyRegistryUpdate(f, 'condition_match', null);
    expect(f.conditionMatch).toEqual([]);
    f = applyRegistryUpdate(f, 'condition_match', ['velmi_dobry', 'dobry']);
    expect(f.conditionMatch).toEqual(['velmi_dobry', 'dobry']);
  });

  it('ignores unknown registry ids without mutating filters', () => {
    const before = { ...DEFAULT_FILTERS, priceMin: 10_000 };
    const after = applyRegistryUpdate(before, 'no_such_filter', 'x');
    expect(after).toBe(before); // identity preserved for unknown ids
  });

  it('round-trips through view → update for a scalar field', () => {
    const f0 = { ...DEFAULT_FILTERS, priceMin: 12_000 };
    const view = listingFiltersToRegistryView(f0);
    const f1 = applyRegistryUpdate(DEFAULT_FILTERS, 'min_price_czk', view.min_price_czk);
    expect(f1.priceMin).toBe(12_000);
  });
});

describe('applyRegistryUpdates (batched)', () => {
  it('applies min and max in one transaction', () => {
    // Regression for the slider-frozen bug: when the FilterForm
    // emitted two onChange(id, value) calls in a row, a non-functional
    // setter saw both against the same stale state and the second
    // overwrote the first. The batched helper threads each update
    // through the prior result, so both sides land.
    const next = applyRegistryUpdates(DEFAULT_FILTERS, [
      { id: 'min_price_czk', value: 5_000 },
      { id: 'max_price_czk', value: 25_000 },
    ]);
    expect(next.priceMin).toBe(5_000);
    expect(next.priceMax).toBe(25_000);
  });

  it('preserves a value when the corresponding update has the same value', () => {
    const start: ListingFilters = {
      ...DEFAULT_FILTERS,
      priceMin: 5_000,
      priceMax: 25_000,
    };
    const next = applyRegistryUpdates(start, [
      { id: 'min_price_czk', value: 8_000 },
      { id: 'max_price_czk', value: 25_000 },
    ]);
    expect(next.priceMin).toBe(8_000);
    expect(next.priceMax).toBe(25_000);
  });

  it('returns the original filters for an empty batch', () => {
    expect(applyRegistryUpdates(DEFAULT_FILTERS, [])).toBe(DEFAULT_FILTERS);
  });
});

describe('filtersToWatchdogSpec', () => {
  it('maps category, dispositions and district chips', () => {
    const f: ListingFilters = {
      ...DEFAULT_FILTERS,
      categoryMain: ['byt', 'dum'],
      categoryType: 'prodej',
      dispositions: ['2+kk', '2+1'],
      districts: [{ name: 'Jihlava', context: null, level: 'obec', id: 586846 }],
    };
    const { spec } = filtersToWatchdogSpec(f);
    expect(spec.category_main_in).toEqual(['byt', 'dum']);
    expect(spec.category_type).toBe('prodej');
    expect(spec.dispositions).toEqual(['2+kk', '2+1']);
    expect(spec.districts).toEqual(f.districts);
  });

  it('empty multi-selects become null (matcher "no constraint" sentinel)', () => {
    const { spec } = filtersToWatchdogSpec(DEFAULT_FILTERS);
    expect(spec.dispositions).toBeNull();
    expect(spec.districts).toBeNull();
    expect(spec.portals).toBeNull();
    expect(spec.portals_excluded).toBeNull();
    expect(spec.condition_match).toBeNull();
    expect(spec.city_index_rules).toBeNull();
  });

  it('carries both sides of the portal rule to the Watchdog', () => {
    const { spec, unsupported } = filtersToWatchdogSpec({
      ...DEFAULT_FILTERS, portals: ['idnes'], portalsExcluded: ['sreality'],
      portalsExcludedActiveOnly: true,
    });
    expect(spec.portals).toEqual(['idnes']);
    expect(spec.portals_excluded).toEqual(['sreality']);
    expect(spec.portals_excluded_active_only).toBe(true);
    expect(unsupported).toEqual([]);
  });

  it('reports the broker scope as unmonitored instead of silently dropping it', () => {
    // The matcher has no broker join; a watchdog created from a broker-scoped
    // Browse view must SAY the scope is not part of what it watches.
    const { spec, unsupported } = filtersToWatchdogSpec({ ...DEFAULT_FILTERS, brokerId: 527 });
    expect(unsupported).toContain('broker');
    expect(Object.keys(spec).some((k) => k.includes('broker'))).toBe(false);
  });

  it('carries the advanced predicates the matcher honours', () => {
    const f: ListingFilters = {
      ...DEFAULT_FILTERS,
      priceMin: 1_000_000,
      priceMax: 5_000_000,
      mfGrossYieldPctMin: 4,
      totalPriceChangePct: -10,
      priceChangeCountMin: 1,
      priceChangeWindowDays: 90,
      minCityPopulation: 50_000,
      maxCityPopulation: 200_000,
      cityIndexRules: [{ index_name: 'safety', op: '>=', value: 7 }],
    };
    const { spec } = filtersToWatchdogSpec(f);
    expect(spec.min_price_czk).toBe(1_000_000);
    expect(spec.max_price_czk).toBe(5_000_000);
    expect(spec.min_mf_gross_yield_pct).toBe(4);
    expect(spec.total_price_change_pct).toBe(-10);
    expect(spec.price_change_count_min).toBe(1);
    expect(spec.price_change_window_days).toBe(90);
    expect(spec.min_city_population).toBe(50_000);
    expect(spec.max_city_population).toBe(200_000);
    expect(spec.city_index_rules).toEqual([{ index_name: 'safety', op: '>=', value: 7 }]);
  });

  it('center+radius location maps to lat/lng/radius_m; viewport does not', () => {
    const cr = { lat: 50.08, lng: 14.42, radius_m: 1500 };
    const viewport = filtersToWatchdogSpec({
      ...DEFAULT_FILTERS,
      locationMode: 'viewport',
      centerRadius: cr,
    });
    expect(viewport.spec.lat).toBeNull();
    expect(viewport.spec.radius_m).toBeNull();

    const centered = filtersToWatchdogSpec({
      ...DEFAULT_FILTERS,
      locationMode: 'center_radius',
      centerRadius: cr,
    });
    expect(centered.spec.lat).toBe(50.08);
    expect(centered.spec.lng).toBe(14.42);
    expect(centered.spec.radius_m).toBe(1500);
  });

  it('tri-state amenities pivot to bool | null', () => {
    const { spec } = filtersToWatchdogSpec({
      ...DEFAULT_FILTERS,
      hasBalcony: 'yes',
      garage: 'no',
      cellar: 'any',
    });
    expect(spec.has_balcony).toBe(true);
    expect(spec.garage).toBe(false);
    expect(spec.cellar).toBeNull();
  });

  it('reports set-but-unmonitored Browse filters in `unsupported`', () => {
    const { unsupported } = filtersToWatchdogSpec({
      ...DEFAULT_FILTERS,
      status: 'active',
      lastSeenMaxDays: 7,
      tags: { set: null, in: [3], out: [] },
      collections: { set: 'out', in: [], out: [] },
    });
    expect(unsupported).toContain('listing status');
    expect(unsupported).toContain('last/first-seen date range');
    expect(unsupported).toContain('tags');
    expect(unsupported).toContain('collections');
  });

  it('carries building material and garden bounds (lost by merge f2d7b359)', () => {
    const { spec, unsupported } = filtersToWatchdogSpec({
      ...DEFAULT_FILTERS,
      buildingMaterial: ['cihla', 'ostatni'],
      gardenAreaMin: 100,
      gardenAreaMax: 800,
    });
    expect(spec.building_material).toEqual(['cihla', 'ostatni']);
    expect(spec.min_garden_area).toBe(100);
    expect(spec.max_garden_area).toBe(800);
    expect(unsupported).toEqual([]);
    expect(filtersToWatchdogSpec(DEFAULT_FILTERS).spec.building_material).toBeNull();
  });

  it('a default filter set has nothing unsupported', () => {
    expect(filtersToWatchdogSpec(DEFAULT_FILTERS).unsupported).toEqual([]);
  });
});

describe('buildingMaterialToValues', () => {
  it('expands each bucket exactly as the generated table (the Python expansion)', () => {
    for (const [bucket, values] of Object.entries(BUILDING_MATERIAL_BUCKETS)) {
      expect(buildingMaterialToValues([bucket as BuildingMaterial])).toEqual(values);
    }
    expect(Object.keys(BUILDING_MATERIAL_BUCKETS)).toEqual(['cihla', 'panel', 'smisena', 'ostatni']);
  });

  it('dedupes a union of buckets in first-seen order', () => {
    expect(buildingMaterialToValues(['panel', 'cihla', 'panel'])).toEqual(['panel', 'cihla']);
    expect(buildingMaterialToValues([])).toEqual([]);
  });
});

describe('watchdogNameSuggestion', () => {
  it('builds category · dispositions · districts', () => {
    expect(
      watchdogNameSuggestion({
        ...DEFAULT_FILTERS,
        categoryMain: ['byt', 'dum'],
        categoryType: 'prodej',
        dispositions: ['2+kk', '2+1'],
        districts: [
          { name: 'Jihlava', context: null, level: 'obec', id: 586846 },
          { name: 'Havlíčkův Brod', context: null, level: 'obec', id: 568414 },
        ],
      }),
    ).toBe('byt+dum prodej · 2+kk, 2+1 · Jihlava, Havlíčkův Brod');
  });

  it('falls back to just the category when nothing else is set', () => {
    expect(watchdogNameSuggestion(DEFAULT_FILTERS)).toBe('byt prodej');
  });
});

describe('filter presets', () => {
  const BOUNDS: MapBounds = { west: 14.3, south: 50.0, east: 14.6, north: 50.2 };

  it('filtersForPreset drops bounds unless the map area is included', () => {
    const f: ListingFilters = { ...DEFAULT_FILTERS, priceMax: 6_000_000, bounds: BOUNDS };
    expect(filtersForPreset(f, false).bounds).toBeNull();
    expect(filtersForPreset(f, true).bounds).toEqual(BOUNDS);
    // Non-viewport filters survive either way.
    expect(filtersForPreset(f, false).priceMax).toBe(6_000_000);
  });

  it('filtersForPreset always strips the pipeline scope', () => {
    const f: ListingFilters = {
      ...DEFAULT_FILTERS,
      priceMax: 6_000_000,
      pipeline: { set: null, in: [12], out: [] },
    };
    // Both branches: the scope is a lens, not preset criteria.
    expect(filtersForPreset(f, false).pipeline).toBeNull();
    expect(filtersForPreset(f, true).pipeline).toBeNull();
  });

  it('never marks a preset dirty because of the pipeline scope', () => {
    const saved: ListingFilters = { ...DEFAULT_FILTERS, priceMax: 5_000_000 };
    // This is the operator-visible contract: toggling Pipeline (or picking
    // stages inside it) must NOT surface the "Update preset" button.
    expect(
      filtersEqualForPreset({ ...saved, pipeline: { set: 'in', in: [], out: [] } }, saved),
    ).toBe(true);
    expect(
      filtersEqualForPreset({ ...saved, pipeline: { set: null, in: [3, 9], out: [] } }, saved),
    ).toBe(true);
    expect(
      filtersEqualForPreset({ ...saved, pipeline: { set: 'out', in: [], out: [] } }, saved),
    ).toBe(true);
    // …and a real criteria change still is dirty, scope on or not.
    expect(
      filtersEqualForPreset(
        { ...saved, priceMax: 4_000_000, pipeline: { set: 'in', in: [], out: [] } },
        saved,
      ),
    ).toBe(false);
  });

  it('treats the dismissed reveal as a lens: stripped from presets, never dirtying one', () => {
    const f: ListingFilters = { ...DEFAULT_FILTERS, priceMax: 6_000_000, showDismissed: true };
    expect(filtersForPreset(f, false).showDismissed).toBe(false);
    expect(filtersForPreset(f, true).showDismissed).toBe(false);
    const saved: ListingFilters = { ...DEFAULT_FILTERS, priceMax: 5_000_000 };
    expect(filtersEqualForPreset({ ...saved, showDismissed: true }, saved)).toBe(true);
    expect(
      filtersEqualForPreset({ ...saved, priceMax: 4_000_000, showDismissed: true }, saved),
    ).toBe(false);
  });

  it('treats the broker scope as a lens too: stripped from presets, never dirtying one', () => {
    const f: ListingFilters = { ...DEFAULT_FILTERS, priceMax: 6_000_000, brokerId: 527 };
    expect(filtersForPreset(f, false).brokerId).toBeNull();
    expect(filtersForPreset(f, true).brokerId).toBeNull();
    const saved: ListingFilters = { ...DEFAULT_FILTERS, priceMax: 5_000_000 };
    expect(filtersEqualForPreset({ ...saved, brokerId: 527 }, saved)).toBe(true);
    expect(
      filtersEqualForPreset({ ...saved, priceMax: 4_000_000, brokerId: 527 }, saved),
    ).toBe(false);
  });

  it('treats the collection and tag scopes as lenses too: stripped, never dirtying a preset', () => {
    const four = { set: null, in: [4], out: [] };
    for (const key of ['collections', 'tags'] as const) {
      const f: ListingFilters = { ...DEFAULT_FILTERS, priceMax: 6_000_000, [key]: four };
      expect(filtersForPreset(f, false)[key]).toBeNull();
      expect(filtersForPreset(f, true)[key]).toBeNull();
      const saved: ListingFilters = { ...DEFAULT_FILTERS, priceMax: 5_000_000 };
      expect(filtersEqualForPreset({ ...saved, [key]: { set: 'out', in: [], out: [] } }, saved))
        .toBe(true);
      expect(filtersEqualForPreset({ ...saved, priceMax: 4_000_000, [key]: four }, saved))
        .toBe(false);
    }
  });

  /* 0 of 7 live presets held a tag (2026-10-19), but a blob saved before
   * lib/setScope must still load its lens losslessly, and a stored preset that
   * held one must not read as dirty against the same lens. */
  it('lifts a stored pre-setScope blob losslessly into the scope shape', () => {
    const { filters } = readPresetSpec({
      filters: {
        ...DEFAULT_FILTERS,
        tags: [1, 2],
        tagsExcluded: [3],
        collections: [4],
        pipeline: { stage_ids: [] },
      } as unknown as ListingFilters,
    });
    expect(filters.tags).toEqual({ set: null, in: [1, 2], out: [3] });
    expect(filters.collections).toEqual({ set: null, in: [4], out: [] });
    expect(filters.pipeline).toEqual({ set: 'in', in: [], out: [] });
    expect('tagsExcluded' in filters).toBe(false);
    const legacyEmpty = readPresetSpec({
      filters: { ...DEFAULT_FILTERS, tags: [], tagsExcluded: [], collections: [], pipeline: null },
    } as unknown as PresetSpec).filters;
    expect(legacyEmpty.tags).toBeNull();
    expect(legacyEmpty.collections).toBeNull();
    expect(readPresetSpec({
      filters: { ...DEFAULT_FILTERS, pipeline: { stage_ids: [5, 6] } } as unknown as ListingFilters,
    }).filters.pipeline).toEqual({ set: null, in: [5, 6], out: [] });
  });

  it('drops a stored curated value of the wrong kind rather than mis-scoping', () => {
    const { filters } = readPresetSpec({
      filters: {
        ...DEFAULT_FILTERS,
        tags: 'x',
        collections: [1, 'a'],
        pipeline: { set: 'sideways', in: [], out: [] },
      } as unknown as ListingFilters,
    });
    expect(filters.tags).toBeNull();
    expect(filters.collections).toBeNull();
    expect(filters.pipeline).toBeNull();
    expect(readPresetSpec({
      filters: { ...DEFAULT_FILTERS, collections: { set: 'out', in: [1], out: [] } },
    } as unknown as PresetSpec).filters.collections).toEqual({ set: 'out', in: [], out: [] });
  });

  it('matches identical filter sets', () => {
    const f: ListingFilters = { ...DEFAULT_FILTERS, priceMax: 5_000_000, dispositions: ['2+kk'] };
    expect(filtersEqualForPreset(f, { ...f })).toBe(true);
  });

  it('detects a changed filter as not matching', () => {
    const saved: ListingFilters = { ...DEFAULT_FILTERS, priceMax: 5_000_000 };
    const current: ListingFilters = { ...DEFAULT_FILTERS, priceMax: 4_000_000 };
    expect(filtersEqualForPreset(current, saved)).toBe(false);
  });

  it('ignores the map viewport when the preset stored none', () => {
    const saved: ListingFilters = { ...DEFAULT_FILTERS, priceMax: 5_000_000, bounds: null };
    const current: ListingFilters = { ...DEFAULT_FILTERS, priceMax: 5_000_000, bounds: BOUNDS };
    // Panning the map after loading a criteria-only preset must NOT mark it dirty.
    expect(filtersEqualForPreset(current, saved)).toBe(true);
  });

  it('honours the map viewport when the preset stored one', () => {
    const saved: ListingFilters = { ...DEFAULT_FILTERS, bounds: BOUNDS };
    const moved: MapBounds = { ...BOUNDS, north: 50.5 };
    const current: ListingFilters = { ...DEFAULT_FILTERS, bounds: moved };
    expect(filtersEqualForPreset(current, saved)).toBe(false);
    expect(filtersEqualForPreset({ ...DEFAULT_FILTERS, bounds: BOUNDS }, saved)).toBe(true);
  });

  it('treats a preset persisted without a newer field as equal to current defaults', () => {
    // Simulate an older spec missing a field added later.
    const saved = { ...DEFAULT_FILTERS } as Record<string, unknown>;
    delete saved.nearOverall15kmMin;
    expect(
      filtersEqualForPreset(DEFAULT_FILTERS, saved as unknown as ListingFilters),
    ).toBe(true);
  });

  it('readPresetSpec parses the new { filters, sort } blob', () => {
    const filters: ListingFilters = { ...DEFAULT_FILTERS, priceMax: 7_000_000 };
    const got = readPresetSpec({ filters, sort: '-price_czk' });
    expect(got.filters.priceMax).toBe(7_000_000);
    expect(got.sort).toBe('-price_czk');
  });

  it('readPresetSpec tolerates a legacy bare-ListingFilters preset (the default sort)', () => {
    const legacy: ListingFilters = { ...DEFAULT_FILTERS, areaMin: 50 };
    const got = readPresetSpec(legacy);
    expect(got.filters.areaMin).toBe(50);
    expect(got.sort).toBe(sortToParam(DEFAULT_SORT));
    // And it still round-trips through the URL serializer.
    expect(fromSearchParams(toSearchParams(got.filters)).areaMin).toBe(50);
  });

  it('readPresetSpec merges partial filters onto DEFAULT_FILTERS', () => {
    const got = readPresetSpec({
      filters: { priceMin: 1000 } as unknown as ListingFilters,
    });
    // Missing fields fall back to defaults, so toSearchParams won't throw.
    expect(got.filters.categoryMain).toEqual(['byt']);
    expect(got.filters.priceMin).toBe(1000);
    expect(got.sort).toBe(sortToParam(DEFAULT_SORT));
  });

  /* A preset saved under a sort with no control would load the default order while
   * holding the old token, and read dirty before the user touched anything. */
  it('readPresetSpec reads a sort Browse no longer offers as the default, so the preset loads clean', () => {
    const got = readPresetSpec({ filters: { ...DEFAULT_FILTERS, areaMin: 50 }, sort: '-estate_area' });
    expect(got.sort).toBe(sortToParam(DEFAULT_SORT));
    // What Browse holds once the preset is loaded: its filters through the URL,
    // and the sort the URL parses to. Both halves of PresetBar's dirty check agree.
    const loadedFilters = fromSearchParams(toSearchParams(got.filters));
    const loadedSort = sortToParam(parseSort(got.sort));
    expect(filtersEqualForPreset(loadedFilters, got.filters)).toBe(true);
    expect(loadedSort).toBe(got.sort);
  });

  it('readPresetSpec coerces stale scalar fields that became arrays (the load crash)', () => {
    // A preset saved when `furnished` / `ownership` were scalars (stored null);
    // they later became multi-select arrays. The raw blob would feed
    // toSearchParams a `null` where it now does `.length`/`.join`, throwing and
    // making the whole load silently no-op. readPresetSpec must repair the shape.
    const stale = {
      ...DEFAULT_FILTERS,
      furnished: null,
      ownership: null,
      categoryType: 'prodej',
      areaMin: 35,
    } as unknown as ListingFilters;
    const got = readPresetSpec({ filters: stale, sort: '-mf_gross_yield_pct' });
    expect(Array.isArray(got.filters.furnished)).toBe(true);
    expect(Array.isArray(got.filters.ownership)).toBe(true);
    expect(got.filters.categoryType).toBe('prodej');
    expect(got.filters.areaMin).toBe(35);
    // The actual symptom: serializing the loaded filters must NOT throw.
    expect(() => toSearchParams(got.filters)).not.toThrow();
    expect(got.sort).toBe('-mf_gross_yield_pct');
  });

  it('readPresetSpec lifts a legacy scalar categoryMain to an array', () => {
    // categoryMain went scalar -> array (multiselect). A preset saved with the
    // old scalar "dum" must keep its category, lifted to ["dum"], not reset to
    // the default — the migration shim in coerceStoredFilters.
    const stale = { ...DEFAULT_FILTERS, categoryMain: 'dum' } as unknown as ListingFilters;
    const got = readPresetSpec(stale);
    expect(got.filters.categoryMain).toEqual(['dum']);
    expect(() => toSearchParams(got.filters)).not.toThrow();
  });
});
