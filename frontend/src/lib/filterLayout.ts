/* Browse's filter set as LAYOUT DATA: bands → rows → blocks, in the order and
 * under the words the desktop sidebar draws them. One heading level: a band's
 * title; a row has none, its filters' own labels name it. Two surfaces render this one
 * table — `FilterSidebar` (components/Filters.tsx) and the phone's Filtry sheet
 * (components/phone/FilterSheet.tsx) — so the phone shows the same filters,
 * banded, ordered, labelled and optioned the same, and a filter placed here
 * lands on both. Nothing here decides what a filter MEANS: that is the registry
 * (filterRegistry.generated), the URL contract (filters.ts toSearchParams) and
 * the reads (queries.ts), rule 16.
 *
 * A `form` block is one <FilterForm> slice: its ids render in REGISTRY order with
 * min/max pairs folded into one row (FilterForm `formRows`), whatever order they
 * are listed in here. A `custom` block is a control the registry form cannot
 * draw (the place typeahead, the two-sided portal chips, …); what it
 * edits is the phone's concern (components/phone/filterRows `CUSTOM_REACH`),
 * kept out of here so the desktop's first visit does not carry it. */

import { SUBTYPE_LABELS_BY_MAIN } from './enums';
import type { ListingFilters } from './filters';

export type CustomBlockKey =
  | 'subtype'
  | 'districts'
  | 'include_no_price'
  | 'show_dismissed'
  | 'city_population_hint'
  | 'market_growth'
  | 'portals'
  | 'location_mode';

export interface FormBlock {
  kind: 'form';
  ids: readonly string[];
  labels: Readonly<Record<string, string>>;
}

export interface CustomBlock {
  kind: 'custom';
  key: CustomBlockKey;
  /* The words that name it: the sidebar's caption when `caption` is set, and
   * the phone row's label always. */
  label: string;
  caption?: boolean;
  /* Spans every column of a grid row. */
  span?: boolean;
  /* Not a filter: a note beside the filters. */
  note?: boolean;
}

export type FilterBlock = FormBlock | CustomBlock;

export interface FilterRow {
  /* 'stack' (default): one filter per line. 'grid': side by side, in `cols`
   * equal columns, or as many ~11rem columns as the sidebar's width allows.
   * 'line': each of its filters as one segmented control, side by side while
   * the sidebar is wide enough, under the first filter's caption (desktop); the
   * phone keeps a row per filter. */
  layout?: 'grid' | 'line';
  cols?: 2 | 3;
  /* Drawn only while the cohort gives the row a meaning. */
  when?: (f: ListingFilters) => boolean;
  /* Drives the full-page map's spatial mode: the phone has no map. */
  mapOnly?: boolean;
  blocks: readonly FilterBlock[];
}

export interface FilterBand {
  title: string;
  defaultOpen?: boolean;
  /* The keys that light the band's "active" dot while it is folded. `bounds` is
   * deliberately absent: the viewport changes on every pan, so counting it
   * would pin the dot on. */
  keys: readonly (keyof ListingFilters)[];
  rows: readonly FilterRow[];
}

/* Subtype refines houses and commercial units; the options are the union over
 * whichever of the two the cohort selects. */
export const subtypeMains = (categoryMain: ReadonlyArray<string>): Array<'dum' | 'komercni'> =>
  categoryMain.filter((c): c is 'dum' | 'komercni' => c === 'dum' || c === 'komercni');

export const subtypeOptions = (
  categoryMain: ReadonlyArray<string>,
): Array<{ value: string; label: string }> => {
  const seen = new Set<string>();
  return subtypeMains(categoryMain)
    .flatMap((c) => SUBTYPE_LABELS_BY_MAIN[c])
    .filter((o) => (seen.has(o.slug) ? false : (seen.add(o.slug), true)))
    .map((o) => ({ value: o.slug, label: o.label }));
};

const form = (labels: Record<string, string>): FormBlock => ({
  kind: 'form',
  ids: Object.keys(labels),
  labels,
});

export const BROWSE_FILTER_BANDS: readonly FilterBand[] = [
  {
    title: 'Essentials',
    defaultOpen: true,
    keys: [
      'categoryType', 'status', 'tomObservedOnly', 'showDismissed', 'categoryMain', 'subtype', 'districts',
      'priceMin', 'priceMax', 'pricePerM2Min', 'pricePerM2Max',
      'recentlyAddedDays', 'recentlyChangedDays', 'ownership', 'includeNoPrice', 'dispositions',
      'areaMin', 'areaMax', 'estateAreaMin', 'estateAreaMax', 'usableAreaMin', 'usableAreaMax',
      'conditionMatch', 'buildingMaterial', 'furnished',
      'hasBalcony', 'hasLift', 'hasParking', 'terrace', 'cellar', 'garage', 'parkingLotsMin',
    ],
    rows: [
      { blocks: [form({ category_type: 'Listing for' })] },
      /* "Which properties count", together: status, seen at birth, dismissed. */
      {
        layout: 'line',
        blocks: [
          form({ status: 'Status' }),
          form({ tom_observed_only: 'Seen at birth' }),
          { kind: 'custom', key: 'show_dismissed', label: 'Show dismissed' },
        ],
      },
      { blocks: [form({ category_main_in: 'Type' })] },
      /* Directly under Type: it refines the chosen category. Cleared with the
       * last house / commercial category (applySidebarUpdates). */
      {
        when: (f) => subtypeMains(f.categoryMain).length > 0,
        blocks: [{ kind: 'custom', key: 'subtype', label: 'Subtype', caption: true }],
      },
      { blocks: [{ kind: 'custom', key: 'districts', label: 'Location', caption: true }] },
      { blocks: [form({ min_price_czk: 'Price' })] },
      /* The Kč/m² bound's unit follows the cohort (rule 23): both surfaces
       * pass `ppm2BoundUnit`, never the registry's static string. */
      { blocks: [form({ min_price_per_m2: 'Price / m²' })] },
      {
        layout: 'grid',
        cols: 2,
        blocks: [form({ recently_added_days: 'Recently added', recently_changed_days: 'Recently changed' })],
      },
      { blocks: [form({ ownership: 'Ownership' })] },
      { blocks: [{ kind: 'custom', key: 'include_no_price', label: 'Include listings without a price' }] },
      { blocks: [form({ dispositions: 'Disposition' })] },
      {
        layout: 'grid',
        cols: 3,
        blocks: [form({ min_area_m2: 'Area', min_estate_area: 'Lot area', min_usable_area: 'Usable area' })],
      },
      { blocks: [form({ condition_match: 'Condition' })] },
      { blocks: [form({ building_material: 'Building material' })] },
      { blocks: [form({ furnished: 'Furnished' })] },
      {
        layout: 'grid',
        blocks: [
          form({
            has_balcony: 'Balcony',
            has_lift: 'Lift',
            has_parking: 'Parking',
            terrace: 'Terrace',
            cellar: 'Cellar',
            garage: 'Garage',
          }),
        ],
      },
      { blocks: [form({ min_parking_lots: 'Min parking spaces' })] },
    ],
  },
  {
    title: 'Curation',
    keys: [
      'pipeline', 'collections', 'tags',
      'portals', 'portalsExcluded', 'portalsExcludedActiveOnly', 'withEstimates',
    ],
    rows: [
      { blocks: [form({ pipeline: 'Pipeline' })] },
      { blocks: [form({ collections: 'Collections' })] },
      { blocks: [form({ tags: 'Tags' })] },
      { blocks: [{ kind: 'custom', key: 'portals', label: 'Source portal', caption: true }] },
      { blocks: [form({ with_estimates: 'With estimates' })] },
    ],
  },
  {
    title: 'Velocity & price activity',
    keys: [
      'priceChangeCountMin', 'priceChangeWindowDays', 'totalPriceChangePct',
      'firstSeenMinDays', 'firstSeenMaxDays', 'lastSeenMinDays', 'lastSeenMaxDays',
      'tomDaysMin', 'tomDaysMax',
    ],
    rows: [
      {
        layout: 'grid',
        cols: 2,
        blocks: [form({ price_change_count_min: 'Price changed N+ times', price_change_window_days: 'Within' })],
      },
      { blocks: [form({ total_price_change_pct: 'Total price change % (− = drop)' })] },
      /* Two blocks, not one: a form draws its ids in REGISTRY order, which
       * puts last seen before first seen. */
      {
        layout: 'grid',
        cols: 2,
        blocks: [form({ first_seen_min_days: 'First seen (days ago)' }), form({ last_seen_min_days: 'Last seen (days ago)' })],
      },
      { blocks: [form({ tom_days_min: 'Time on market (days)' })] },
    ],
  },
  /* Rarely touched, parked at the bottom by design. */
  {
    title: 'Advanced',
    keys: [
      'energyRatingMatch', 'mfGrossYieldPctMin', 'mfGrossYieldPctMax', 'locationMode', 'centerRadius',
      'cityIndexRules', 'minCityPopulation', 'maxCityPopulation',
      'nearPop5kmMin', 'nearPop15kmMin', 'nearJobs5kmMin', 'nearJobs15kmMin',
      'nearYouth5kmMin', 'nearYouth15kmMin', 'nearOverall5kmMin', 'nearOverall15kmMin',
      'priceGrowthRules',
    ],
    rows: [
      /* PENB A–G. G is also the legal "no certificate" placeholder (registry note). */
      { blocks: [form({ energy_rating_match: 'Energy rating (PENB)' })] },
      /* MF reference rent ÷ asking price: sale flats only, so Prodej only;
       * cleared on leaving Prodej (applySidebarUpdates). */
      {
        when: (f) => f.categoryType === 'prodej',
        blocks: [form({ min_mf_gross_yield_pct: 'MF gross yield %' })],
      },
      {
        mapOnly: true,
        blocks: [{ kind: 'custom', key: 'location_mode', label: 'Map filter' }],
      },
      {
        layout: 'grid',
        blocks: [
          form({
            min_city_population: 'Min population (own city)',
            max_city_population: 'Max population (own city)',
            near_pop_5km_min: 'Pop ≥ N within 5 km',
            near_pop_15km_min: 'Pop ≥ N within 15 km',
            near_jobs_5km_min: 'Jobs index ≥ T within 5 km',
            near_jobs_15km_min: 'Jobs index ≥ T within 15 km',
            near_youth_5km_min: 'Young-migration ≥ T within 5 km',
            near_youth_15km_min: 'Young-migration ≥ T within 15 km',
            near_overall_5km_min: 'Overall index ≥ T within 5 km',
            near_overall_15km_min: 'Overall index ≥ T within 15 km',
          }),
          { kind: 'custom', key: 'city_population_hint', label: 'Population data', span: true, note: true },
        ],
      },
      /* The flexible legacy filter for the ~30 indexes the precomputed columns
       * above do not cover; matched inside the curated-city footprint. */
      { blocks: [form({ city_index_rules: 'Advanced city-quality rules (any of ~30 indexes)' })] },
      {
        blocks: [{ kind: 'custom', key: 'market_growth', label: 'Market growth datasets', caption: true }],
      },
    ],
  },
];
