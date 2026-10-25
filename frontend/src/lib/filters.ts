import type { Disposition } from './types';
import {
  BUILDING_MATERIAL_BUCKETS,
  CONDITION_CANONICAL,
  FURNISHED_CANONICAL,
  OWNERSHIP_CANONICAL,
  filterById,
} from './filterRegistry.generated';
import {
  DEFAULT_WATCHDOG_FILTER_SPEC,
  type WatchdogFilterSpec,
} from './types';
import { fmtArea } from './format';
/* Value import, but not a runtime cycle: districtCodes imports only TYPES from
 * this file, and the level list and the chip guard must have ONE definition. */
import { isDistrictChip, type LocationLevel } from './districtCodes';
import { portalScopeLabel } from './portals';
import { parseSort, sortToParam } from './browseSort';
import {
  CURATED_SET_NAMES,
  normalizeScope,
  parseSetScope,
  scopeActive,
  scopeWords,
  setScopeToParams,
  type CuratedSet,
  type SetScope,
} from './setScope';

export type TriState = 'any' | 'yes' | 'no';
export type ListingStatus = 'active' | 'inactive' | 'any';

/* All five category_main values. Browse + Watchdog MULTISELECT them
 * (`ListingFilters.categoryMain` is an array, wired to the registry's
 * `category_main_in` filter); the analytical surfaces still anchor a cohort
 * to ONE category via the scalar `category_main` filter. */
export type CategoryMain = 'byt' | 'dum' | 'komercni' | 'pozemek' | 'ostatni';

/* CHECK constraint on listings.category_type allows all four. Deal-type stays
 * SINGLE-select in Browse — rent (monthly Kč) and sale (total Kč) are different
 * price scales, so mixing them would make the price tooling cross-scale. */
export type CategoryType = 'pronajem' | 'prodej' | 'drazba' | 'podil';

/* Building material buckets surfaced in the filter panel. Each maps to
 * one or more building_type values via the generated BUILDING_MATERIAL_BUCKETS. */
export type BuildingMaterial = 'cihla' | 'panel' | 'smisena' | 'ostatni';

/* Map-viewport rectangle. west < east, south < north, all WGS84
 * degrees. Acts as an additional filter alongside the sidebar fields:
 * cards / stats all narrow to listings whose (lng, lat) falls
 * inside the rectangle. NULL = no map area applied. */
export interface MapBounds {
  west: number;
  south: number;
  east: number;
  north: number;
}

/* Operator-set point + radius for the "dot + circle on the map" mode.
 * When `locationMode === 'center_radius'` the cohort is filtered by an
 * approximate bbox around (lat, lng) within `radius_m` metres; the
 * sidebar's <LocationControl> owns the point + radius UI and the main
 * map renders a dashed circle for visual context. Viewport bounds are
 * ignored in this mode. */
export interface CenterRadius {
  lat: number;
  lng: number;
  radius_m: number;
}

export type LocationMode = 'viewport' | 'center_radius';

/* A WGS84 bounding box roughly `km` kilometres across (half-extent each
 * side), centred on (lat, lng). Longitude degrees shrink with latitude so
 * the east/west extent scales by cos(lat). Used to frame the map AND seed
 * the viewport cohort when opening Browse focused on a single point (the
 * "Explore area" modal) — the same `bounds` value both fits the camera and
 * narrows the cohort, so no separate flyTo is needed. */
const KM_PER_DEG = 111.32;

export const bboxAround = (lat: number, lng: number, km: number): MapBounds => {
  const half = km / 2;
  const dLat = half / KM_PER_DEG;
  const dLng = half / (KM_PER_DEG * Math.max(Math.cos((lat * Math.PI) / 180), 0.01));
  return { west: lng - dLng, south: lat - dLat, east: lng + dLng, north: lat + dLat };
};

/* THE box Browse filters on, from the location mode: viewport → the map's
 * `bounds` (or null); centre+radius → the square around the circle, through the
 * one box-around-a-point formula above. A circumscribed square admits a few
 * corner listings beyond the true radius — accepted since the mode shipped; the
 * main map still draws the exact circle. One rule, every reader: the list
 * (queries.applyFilters), both stats RPCs (`bbox_*`) and a watchdog saved from
 * Browse (filtersToWatchdogSpec), so what Browse shows is what a watchdog
 * watches (rule 16). */
export const effectiveBbox = (f: ListingFilters): MapBounds | null => {
  if (f.locationMode === 'center_radius') {
    return f.centerRadius
      ? bboxAround(f.centerRadius.lat, f.centerRadius.lng, (2 * f.centerRadius.radius_m) / 1000)
      : null;
  }
  return f.bounds;
};

/* The box in words for a summary line: its extent in km and its centre. */
export const mapAreaLabel = (b: MapBounds): string => {
  const lat = (b.north + b.south) / 2;
  const lng = (b.east + b.west) / 2;
  const h = (b.north - b.south) * KM_PER_DEG;
  const w = (b.east - b.west) * KM_PER_DEG * Math.cos((lat * Math.PI) / 180);
  return `map area ≈ ${w.toFixed(1)} × ${h.toFixed(1)} km around ${lat.toFixed(4)}, ${lng.toFixed(4)}`;
};

/* Phase QUAL — one entry in `cityIndexRules`. Snake_case keys mirror
 * the wire shape consumed by the `listings_with_city_quality` RPC
 * and `api/notifications._build_match_clauses`, so the same picker
 * output flows unchanged to Browse and Watchdog. `index_name` is the
 * slug from `city_index_definitions_public` (e.g. `bezpecnost`,
 * `prakticti_lekari`); `value` is the threshold the city must meet
 * under `op` (defaults to `>=`). Multiple rules AND. */
export interface CityIndexRule {
  index_name: string;
  op?: '>=' | '<=' | '==' | '!=' | '>' | '<';
  value: number;
}

/* One entry of the district chip list. A chip is ONE RÚIAN unit — a LEVEL
 * plus its CODE — and it compiles to `<level>_id = any(codes)`: plain equality,
 * nothing else (lib/districtCodes). What you pick is what you filter: a street
 * chip is its street, never its town. A chip without a valid (level, code)
 * cannot exist: the typeahead never makes one and `parseDistrictChips` drops one.
 * `name` and `context` are the register's names (`context` = the unit's obec,
 * for a level below obec) — display only, never a predicate.
 * `excluded` flips the chip from an INCLUDE to an EXCLUDE filter: an excluded
 * chip removes its matches from the cohort instead of requiring them. The same
 * shape is sent to the watchdog matcher
 * (`api/notifications.WatchdogFilterSpec.districts`) so Browse and
 * Watchdog stay aligned via the shared filter registry. */
export interface DistrictChip {
  name: string;
  context: string | null;
  excluded?: boolean;
  level: LocationLevel;
  id: number;
}

/* One active price-stats growth filter: keep listings whose obec meets/exceeds
 * the entered rent/sale growth p.a. (CAGR ≥), computed for the [fromYm, toYm]
 * window of a chosen dataset by the price_stat_growth RPC. Multiple rules AND.
 * BROWSE-only (window-dependent, live-computed). */
export interface PriceGrowthRule {
  datasetId: number;
  fromYm: string | null;   // 'YYYY-MM' or null = dataset's full window
  toYm: string | null;
  rentMinPct: number | null;
  saleMinPct: number | null;
}

export interface ListingFilters {
  /* Multi-select (Browse + Watchdog): a property matches if its category_main
   * is in the list. Default ['byt']; empty [] = no category constraint. */
  categoryMain: CategoryMain[];
  /* Deal type (rent / sale / auction / fractional). NULL = no deal-type
   * constraint — the "Vše" pill. Nullable because every backend consumer
   * already treats a missing category_type that way (comparables, the watchdog
   * matcher, browse_stats_properties all guard the clause), and because a
   * cohort like "everything in my pipeline" spans rent AND sale. The default
   * is `prodej` (operator, 2026-10-20; Browse's own — the registry's shared
   * default stays `pronajem` for estimation and comparables). */
  categoryType: CategoryType | null;
  districts: DistrictChip[];
  dispositions: Disposition[];
  priceMin: number | null;
  priceMax: number | null;
  /* When true AND a price bound is set, KEEP no-price listings
   * (price_czk IS NULL) instead of dropping them via SQL three-valued
   * logic. A no-op when no bound is set. Browse + Watchdog only. */
  includeNoPrice: boolean;
  /* Bounds on THE per-m² measure — `price_per_m2`, i.e. migration 425's
   * measure_price_per_m2(price, area, category_main, category_type),
   * basis-resolved and floored. Every consumer reads that one published
   * column; nothing re-derives price_czk / area_m2 any more, which is why a
   * row under its basis floor is excluded here as well as blank on screen.
   * Rows with no measure (NULL area_m2, or below the floor) fall out when
   * either bound is set. The UNIT of the bound follows the cohort: capital
   * Kč/m² on a sale cohort, Kč/m²/měs on a rent one — see the sidebar hint. */
  pricePerM2Min: number | null;
  pricePerM2Max: number | null;
  /* MF gross rental yield % bounds (migration 133). Sale apartments only;
   * NULL on everything else, so non-matching listings fall out when set. */
  mfGrossYieldPctMin: number | null;
  mfGrossYieldPctMax: number | null;
  areaMin: number | null;
  areaMax: number | null;
  status: ListingStatus;
  /* Days-ago range on last_seen_at. min = most recent allowed (so
   * lastSeenMinDays=3 hides listings seen in the last 2 days);
   * max = oldest allowed. Either end null = unbounded. Replaces the
   * 1d/7d/30d/any preset. */
  lastSeenMinDays: number | null;
  lastSeenMaxDays: number | null;
  /* Days-ago range on first_seen_at — same semantics. */
  firstSeenMinDays: number | null;
  firstSeenMaxDays: number | null;
  /* Status-section recency presets (one of 1/3/7/14/30, or null = any).
   * `recentlyAddedDays` → first_seen_at >= now() - N days (the preset twin of
   * firstSeenMaxDays). `recentlyChangedDays` → last_change_at >= now() - N days
   * (properties.last_change_at = newest content snapshot). Browse-only. */
  recentlyAddedDays: number | null;
  recentlyChangedDays: number | null;
  /* Days on market (= last_seen_at - first_seen_at, or now() -
   * first_seen_at for active listings): the read models' `tom_days` column
   * (migration 054), which the Browse list, count, map and Stats all filter. */
  tomDaysMin: number | null;
  tomDaysMax: number | null;
  /* Only ads whose TOM is observed from the start (read-model `tom_observed`,
   * migration 625): first seen on an ordinary day, not in a sweep. */
  tomObservedOnly: boolean;
  hasBalcony: TriState;
  hasLift: TriState;
  hasParking: TriState;
  /* Migration 022 — granular amenities and category-relevant fields. */
  terrace: TriState;
  cellar: TriState;
  garage: TriState;
  /* Multi-select enums. The special '__unknown__' value matches listings whose
   * value is NULL or stored under a non-canonical label (see FURNISHED_VALUES /
   * UNKNOWN_FILTER_VALUE and queries.ts:applyFilters). Empty = no constraint. */
  furnished: string[];
  ownership: string[];
  /* The portal rule's two sides (MS19 + migration 597, queries.ts applyPortalRule):
   * an ad on one of `portals`, and none on `portalsExcluded` — any ad, or only
   * active ones when `portalsExcludedActiveOnly`. */
  portals: string[];
  portalsExcluded: string[];
  portalsExcludedActiveOnly: boolean;
  /* Carries the '__unknown__' sentinel like furnished / ownership. */
  conditionMatch: string[];
  categorySubCb: number | null;
  /* Portal-agnostic property sub-type (multi-select). Only surfaced for
   * categoryMain in (dum, komercni); see SUBTYPE_LABELS_BY_MAIN. */
  subtype: string[];
  buildingMaterial: BuildingMaterial[];
  /* PENB letters A–G (registry `energy_rating_match`): a property matches when its
   * rating — the first non-null across its ads in canonical order — is one of them;
   * an unrated property never does. Empty = no constraint. */
  energyRatingMatch: string[];
  estateAreaMin: number | null;
  estateAreaMax: number | null;
  usableAreaMin: number | null;
  usableAreaMax: number | null;
  parkingLotsMin: number | null;
  /* Migration 022 — house listings carry a separate garden area
   * (`garden_area`) distinct from the lot area (`estate_area`). Wired
   * to the registry; not yet exposed in the Browse sidebar UI
   * (the Filters.tsx `includeOnly` for the Size group would need to
   * include them), but settable via URL params and honoured by
   * Watchdog. */
  gardenAreaMin: number | null;
  gardenAreaMax: number | null;
  /* Price-history signals (migration 173). Derived columns on `properties`,
   * maintained by the recompute job; property grain, applied against
   * properties_public. `priceChangeCountMin` counts cuts AND raises across
   * the union of a property's snapshot history, inside the
   * `priceChangeWindowDays` window (30/90/365, null = all time).
   * `totalPriceChangePct` is signed: -10 = "dropped 10%+ overall from the
   * first observed price", +10 = "rose 10%+ overall". */
  priceChangeCountMin: number | null;
  priceChangeWindowDays: number | null;
  totalPriceChangePct: number | null;
  /* Browse-only: restrict to properties with at least one successful
   * estimation run (property_estimates_public, migration 173). */
  withEstimates: boolean;
  /* Browse-only. False (the default) hides the caller's dismissed properties,
   * server-side (migration 537); true reveals them. A lens over the operator's
   * own state: a preset keeps it, a dashboard does not (DASHBOARD_EXCLUDED_KEYS). */
  showDismissed: boolean;
  /* Browse-only deal-pipeline scope (rule #22, lib/setScope; elements = stage
   * ids). Null = off. A lens over the operator's own state, like tags +
   * collections: a preset keeps it, a dashboard does not (DASHBOARD_EXCLUDED_KEYS). */
  pipeline: SetScope | null;
  /* Browse-only broker scope ("Explore this broker's listings"). Null = off.
   * A preset keeps it, a dashboard does not (DASHBOARD_EXCLUDED_KEYS); it is
   * NOT in toolkit/filter_registry.py (must stay invisible to the agent /
   * comparables / estimation agendas). Set by the explore-broker modal's seed,
   * a `?broker=` URL param, or the sidebar's Advanced → Broker search
   * (filter-controls/BrokerFilterControl). A property-id allowlist (MS19): the
   * properties where one of this broker's ads is on the portals in the status
   * switch's status, so one ad satisfies both (queries.ts:adPortals). */
  brokerId: number | null;
  /* Operator tags (migration 025, lib/setScope): the in side needs EVERY element
   * (AND). Ids, not names, so renames stay queryable. Null = off; a lens. */
  tags: SetScope | null;
  /* Browse-only collection scope (rule #18, lib/setScope; own + shared). Null = off. */
  collections: SetScope | null;
  bounds: MapBounds | null;
  /* `viewport` (default) = map pan/zoom emits bounds, those filter
   * the cohort. `center_radius` = a sidebar-set point + radius drives
   * the spatial predicate; bounds is ignored on the SQL side. */
  locationMode: LocationMode;
  centerRadius: CenterRadius | null;
  /* Phase QUAL — curated-city quality filters. Browse + Watchdog only;
   * intentionally not surfaced to the estimation agent / comparables
   * tool (the registry's agenda gating enforces that). The Browse
   * map renders matching cities as a separate pin layer on top of
   * the listing dots, and the listing query is restricted to the
   * cities' footprints via the `listings_with_city_quality` RPC. */
  cityIndexRules: CityIndexRule[];
  minCityPopulation: number | null;
  maxCityPopulation: number | null;
  /* Fast polygon-edge proximity (migration 142). Each is a precomputed
   * `properties` column filtered as `>= value`: the MAX metric found within
   * a fixed 5 / 15 km of the listing (population of obce >= 10k, or one of
   * the three curated-city indexes). No per-request spatial RPC. */
  nearPop5kmMin: number | null;
  nearPop15kmMin: number | null;
  nearJobs5kmMin: number | null;
  nearJobs15kmMin: number | null;
  nearYouth5kmMin: number | null;
  nearYouth15kmMin: number | null;
  nearOverall5kmMin: number | null;
  nearOverall15kmMin: number | null;
  priceGrowthRules: PriceGrowthRule[];
}

export const DEFAULT_FILTERS: ListingFilters = {
  categoryMain: ['byt'],
  categoryType: 'prodej',
  districts: [],
  dispositions: [],
  priceMin: null,
  priceMax: null,
  includeNoPrice: false,
  pricePerM2Min: null,
  pricePerM2Max: null,
  mfGrossYieldPctMin: null,
  mfGrossYieldPctMax: null,
  areaMin: null,
  areaMax: null,
  status: 'any',
  lastSeenMinDays: null,
  lastSeenMaxDays: null,
  firstSeenMinDays: null,
  firstSeenMaxDays: null,
  recentlyAddedDays: null,
  recentlyChangedDays: null,
  tomDaysMin: null,
  tomDaysMax: null,
  tomObservedOnly: false,
  hasBalcony: 'any',
  hasLift: 'any',
  hasParking: 'any',
  terrace: 'any',
  cellar: 'any',
  garage: 'any',
  furnished: [],
  ownership: [],
  portals: [],
  portalsExcluded: [],
  portalsExcludedActiveOnly: false,
  conditionMatch: [],
  categorySubCb: null,
  subtype: [],
  buildingMaterial: [],
  energyRatingMatch: [],
  estateAreaMin: null,
  estateAreaMax: null,
  usableAreaMin: null,
  usableAreaMax: null,
  parkingLotsMin: null,
  gardenAreaMin: null,
  gardenAreaMax: null,
  priceChangeCountMin: null,
  priceChangeWindowDays: null,
  totalPriceChangePct: null,
  withEstimates: false,
  showDismissed: false,
  pipeline: null,
  brokerId: null,
  tags: null,
  collections: null,
  bounds: null,
  locationMode: 'viewport',
  centerRadius: null,
  cityIndexRules: [],
  minCityPopulation: null,
  maxCityPopulation: null,
  nearPop5kmMin: null,
  nearPop15kmMin: null,
  nearJobs5kmMin: null,
  nearJobs15kmMin: null,
  nearYouth5kmMin: null,
  nearYouth15kmMin: null,
  nearOverall5kmMin: null,
  nearOverall15kmMin: null,
  priceGrowthRules: [],
};

/* Expand a multi-select of operator-friendly buckets into the union of
 * granular `building_type` values to match against (deduped). The expansion
 * is generated from toolkit.filter_registry.building_material_values, the one
 * the Watchdog matcher runs; "Ostatní" (and any unknown bucket) is every canon
 * value outside the explicit three. A NULL building_type falls out of any
 * selection. Empty in, empty out — the caller skips the predicate entirely. */
export const buildingMaterialToValues = (
  materials: readonly BuildingMaterial[],
): readonly string[] => [
  ...new Set(
    materials.flatMap((m) => BUILDING_MATERIAL_BUCKETS[m] ?? BUILDING_MATERIAL_BUCKETS.ostatni),
  ),
];

/* Read off the registry, like CONDITION_VALUES below: this list is what
 * survives a URL round-trip, so a hand-kept copy silently discards every pill
 * the canon gains (it stopped at 5+1 while the pills reached 9+1). */
const ALL_DISPOSITIONS: ReadonlyArray<string> = (
  filterById('dispositions')?.enum_values ?? []
).map((o) => String(o.value));

const TRI_VALUES: ReadonlyArray<TriState> = ['any', 'yes', 'no'];
const STATUS_VALUES: ReadonlyArray<ListingStatus> = ['active', 'inactive', 'any'];
/* Multi-select wire values incl. the '__unknown__' sentinel (NULL / non-canonical).
 * The canonical sets are re-exported from the generated registry
 * (toolkit.filter_registry.{FURNISHED,OWNERSHIP}_CANONICAL and
 * COLUMN_CANONICAL_VALUES['condition']) — one source, no hand-coded duplicate
 * that can drift from the SQL RPC / Watchdog / toolkit (architectural rule #16).
 * queries.ts:applyFilters uses them for the `not.in.(…)` unknown predicate. */
export const UNKNOWN_FILTER_VALUE = '__unknown__';
export { CONDITION_CANONICAL, FURNISHED_CANONICAL, OWNERSHIP_CANONICAL };
const FURNISHED_VALUES: ReadonlyArray<string> = [...FURNISHED_CANONICAL, UNKNOWN_FILTER_VALUE];
const OWNERSHIP_VALUES: ReadonlyArray<string> = [...OWNERSHIP_CANONICAL, UNKNOWN_FILTER_VALUE];
const CONDITION_VALUES: ReadonlyArray<string> = (
  filterById('condition_match')?.enum_values ?? []
).map((o) => String(o.value));
/* One selected portal names a browse_list column (its newest-ad date, read by the
 * one-portal order and its conjunct in queries.ts), so a code outside the registry is
 * dropped like any stale enum value: kept, it would answer HTTP 400 on every Browse read. */
const PORTAL_VALUES: ReadonlyArray<string> = (
  filterById('portals')?.enum_values ?? []
).map((o) => String(o.value));
const CATEGORY_MAIN_VALUES: ReadonlyArray<CategoryMain> = [
  'byt', 'dum', 'komercni', 'pozemek', 'ostatni',
];
/* The single-element default cohort. `cat` is omitted from the URL when the
 * selection equals this, and emitted as an empty `cat=` token for [] (all). */
const DEFAULT_CATEGORY_MAIN: ReadonlyArray<CategoryMain> = ['byt'];
const CATEGORY_TYPE_VALUES: ReadonlyArray<CategoryType> = [
  'pronajem', 'prodej', 'drazba', 'podil',
];
const BUILDING_MATERIAL_VALUES: ReadonlyArray<BuildingMaterial> = [
  'cihla', 'panel', 'smisena', 'ostatni',
];
const ENERGY_RATING_VALUES: ReadonlyArray<string> = ['A', 'B', 'C', 'D', 'E', 'F', 'G'];

const splitCsv = (s: string | null): string[] =>
  s == null || s === '' ? [] : s.split(',').map(decodeURIComponent);

const joinCsv = (xs: string[]): string => xs.map(encodeURIComponent).join(',');

/* Parse the parallel `districts` (names) + `districts_ctx` (contexts) +
 * `districts_excl` (exclude flags) + `districts_lvl` (level) + `districts_id`
 * (RÚIAN code) query params into a `DistrictChip[]`. An empty context entry is
 * "no context"; an absent `districts_excl` means every chip is an include. A
 * chip whose level is not one of the five or whose code is not a positive
 * integer is DROPPED — an old bookmark (`?districts=Brno`, the retired street
 * alias) carries no unit to filter on. */
export const parseDistrictChips = (
  namesRaw: string | null,
  ctxRaw: string | null,
  exclRaw: string | null,
  lvlRaw: string | null,
  idRaw: string | null,
): DistrictChip[] => {
  const ctxs = splitCsv(ctxRaw);
  const excls = splitCsv(exclRaw);
  const lvls = splitCsv(lvlRaw);
  const ids = splitCsv(idRaw);
  return splitCsv(namesRaw).flatMap((name, i) => {
    const chip = {
      name, context: ctxs[i] || null, level: lvls[i], id: Number(ids[i]),
      ...(excls[i] === '1' ? { excluded: true } : {}),
    };
    return isDistrictChip(chip) ? [chip] : [];
  });
};

const parseInt0 = (s: string | null): number | null => {
  if (s == null || s === '') return null;
  const n = Number(s);
  return Number.isFinite(n) && n >= 0 ? n : null;
};

const parseRange = (s: string | null): [number | null, number | null] => {
  if (!s) return [null, null];
  const [a, b] = s.split('-');
  return [parseInt0(a ?? null), parseInt0(b ?? null)];
};

const enumOr = <T extends string>(
  v: string | null,
  values: ReadonlyArray<T>,
  fallback: T,
): T => (v != null && (values as ReadonlyArray<string>).includes(v) ? (v as T) : fallback);

const parseIntOrNull = (s: string | null): number | null => {
  if (s == null || s === '') return null;
  const n = Number(s);
  return Number.isFinite(n) ? Math.trunc(n) : null;
};

const parseFloatOrNull = (s: string | null): number | null => {
  if (s == null || s === '') return null;
  const n = Number(s);
  return Number.isFinite(n) ? n : null;
};

/* Legal `priceChangeWindowDays` values — must mirror the registry's
 * `price_change_window_days` enum and the precomputed properties columns
 * (price_change_count_30d / _90d / _365d, migration 173). Null = all time. */
export const PRICE_CHANGE_WINDOWS = [30, 90, 365] as const;

const parsePriceChangeWindow = (s: string | null): number | null => {
  const n = parseIntOrNull(s);
  return n != null && (PRICE_CHANGE_WINDOWS as ReadonlyArray<number>).includes(n)
    ? n
    : null;
};

/* Which precomputed properties_public count column `priceChangeCountMin`
 * reads for a given window. Mirrors the backend's canonical
 * `toolkit.filter_registry.PRICE_CHANGE_COUNT_COLUMNS` (the watchdog
 * matcher) and the CASE inside browse_stats_properties (migration 173). */
export const priceChangeCountColumn = (windowDays: number | null): string => {
  switch (windowDays) {
    case 30: return 'price_change_count_30d';
    case 90: return 'price_change_count_90d';
    case 365: return 'price_change_count_365d';
    default: return 'price_change_count';
  }
};

export const fromSearchParams = (sp: URLSearchParams): ListingFilters => {
  const dispRaw = splitCsv(sp.get('disposition'));
  const dispositions = dispRaw.filter((d): d is Disposition =>
    ALL_DISPOSITIONS.includes(d),
  );
  const [priceMin, priceMax] = parseRange(sp.get('price'));
  const [ppm2Min, ppm2Max] = parseRange(sp.get('ppm2'));
  const [yieldMin, yieldMax] = parseRange(sp.get('yield'));
  const [areaMin, areaMax] = parseRange(sp.get('area'));
  const [estateMin, estateMax] = parseRange(sp.get('estate'));
  const [usableMin, usableMax] = parseRange(sp.get('usable'));
  const [lastMin, lastMax] = parseRange(sp.get('seen'));
  const [firstMin, firstMax] = parseRange(sp.get('first'));
  const [tomMin, tomMax] = parseRange(sp.get('tom'));
  const portalsExcluded = splitCsv(sp.get('portal_excl')).filter((v) => PORTAL_VALUES.includes(v));
  return {
    /* Absent `cat` = the default cohort (['byt']); an empty `cat=` token =
     * the deliberate "all categories" ([]). Otherwise CSV, filtered to the
     * known set (so an old ?cat=dum still parses, and junk is dropped). */
    categoryMain: sp.get('cat') == null
      ? [...DEFAULT_CATEGORY_MAIN]
      : splitCsv(sp.get('cat')).filter((c): c is CategoryMain =>
          (CATEGORY_MAIN_VALUES as ReadonlyArray<string>).includes(c)),
    /* `deal=any` is the explicit "no constraint" state; an ABSENT param means
     * the default (`prodej` since 2026-10-20 — a link that omits it opens on sale). */
    categoryType: sp.get('deal') === ANY_DEAL_TYPE
      ? null
      : enumOr(sp.get('deal'), CATEGORY_TYPE_VALUES, DEFAULT_FILTERS.categoryType ?? 'prodej'),
    districts: parseDistrictChips(
      sp.get('districts'),
      sp.get('districts_ctx'),
      sp.get('districts_excl'),
      sp.get('districts_lvl'),
      sp.get('districts_id'),
    ),
    dispositions,
    priceMin,
    priceMax,
    includeNoPrice: sp.get('no_price') === '1',
    pricePerM2Min: ppm2Min,
    pricePerM2Max: ppm2Max,
    mfGrossYieldPctMin: yieldMin,
    mfGrossYieldPctMax: yieldMax,
    areaMin,
    areaMax,
    status: enumOr(sp.get('status'), STATUS_VALUES, 'any'),
    lastSeenMinDays: lastMin,
    lastSeenMaxDays: lastMax,
    firstSeenMinDays: firstMin,
    firstSeenMaxDays: firstMax,
    recentlyAddedDays: parseIntOrNull(sp.get('added')),
    recentlyChangedDays: parseIntOrNull(sp.get('changed')),
    tomDaysMin: tomMin,
    tomDaysMax: tomMax,
    tomObservedOnly: sp.get('observed') === '1',
    hasBalcony: enumOr(sp.get('balcony'), TRI_VALUES, 'any'),
    hasLift: enumOr(sp.get('lift'), TRI_VALUES, 'any'),
    hasParking: enumOr(sp.get('parking'), TRI_VALUES, 'any'),
    terrace: enumOr(sp.get('terrace'), TRI_VALUES, 'any'),
    cellar: enumOr(sp.get('cellar'), TRI_VALUES, 'any'),
    garage: enumOr(sp.get('garage'), TRI_VALUES, 'any'),
    furnished: splitCsv(sp.get('furnished')).filter((v) => FURNISHED_VALUES.includes(v)),
    ownership: splitCsv(sp.get('ownership')).filter((v) => OWNERSHIP_VALUES.includes(v)),
    portals: splitCsv(sp.get('portal')).filter((v) => PORTAL_VALUES.includes(v)),
    portalsExcluded,
    /* Meaningful only beside an exclusion (cyclePortal drops it with the last one). */
    portalsExcludedActiveOnly: portalsExcluded.length > 0 && sp.get('portal_excl_active') === '1',
    conditionMatch: splitCsv(sp.get('condition')).filter(
      (c) => CONDITION_VALUES.includes(c),
    ),
    categorySubCb: parseIntOrNull(sp.get('subcat')),
    subtype: splitCsv(sp.get('subtype')),
    buildingMaterial: splitCsv(sp.get('build')).filter((m): m is BuildingMaterial =>
      (BUILDING_MATERIAL_VALUES as ReadonlyArray<string>).includes(m),
    ),
    energyRatingMatch: splitCsv(sp.get('energy')).filter((v) => ENERGY_RATING_VALUES.includes(v)),
    estateAreaMin: estateMin,
    estateAreaMax: estateMax,
    usableAreaMin: usableMin,
    usableAreaMax: usableMax,
    parkingLotsMin: parseIntOrNull(sp.get('parking_min')),
    gardenAreaMin: parseIntOrNull(sp.get('garden_min')),
    gardenAreaMax: parseIntOrNull(sp.get('garden_max')),
    priceChangeCountMin: parseIntOrNull(sp.get('changes_min')),
    priceChangeWindowDays: parsePriceChangeWindow(sp.get('changes_window')),
    totalPriceChangePct: parseFloatOrNull(sp.get('total_change_pct')),
    withEstimates: sp.get('with_est') === '1',
    showDismissed: sp.get('dismissed') === 'show',
    pipeline: parseSetScope(sp, 'pipeline'),
    brokerId: parseIntOrNull(sp.get('broker')),
    tags: parseSetScope(sp, 'tags'),
    collections: parseSetScope(sp, 'collections'),
    bounds: parseBounds(sp.get('bbox')),
    locationMode: sp.get('locmode') === 'center_radius'
      ? 'center_radius'
      : 'viewport',
    centerRadius: parseCenterRadius(sp.get('center')),
    cityIndexRules: parseCityIndexRules(sp.get('cq_rules')),
    minCityPopulation: parseIntOrNull(sp.get('cq_pop_min')),
    maxCityPopulation: parseIntOrNull(sp.get('cq_pop_max')),
    nearPop5kmMin: parseIntOrNull(sp.get('np5')),
    nearPop15kmMin: parseIntOrNull(sp.get('np15')),
    nearJobs5kmMin: parseFloatOrNull(sp.get('nj5')),
    nearJobs15kmMin: parseFloatOrNull(sp.get('nj15')),
    nearYouth5kmMin: parseFloatOrNull(sp.get('ny5')),
    nearYouth15kmMin: parseFloatOrNull(sp.get('ny15')),
    nearOverall5kmMin: parseFloatOrNull(sp.get('no5')),
    nearOverall15kmMin: parseFloatOrNull(sp.get('no15')),
    priceGrowthRules: parsePriceGrowthRules(sp.get('pg')),
  };
};

/* `cq_rules` URL shape: `indexName:value[:op],indexName:value[:op]`.
 * `op` defaults to `>=` when omitted, which is the only operator the
 * Browse UI exposes (matches Watchdog parity). */
const _VALID_OPS = new Set(['>=', '<=', '==', '!=', '>', '<']);
const parseCityIndexRules = (s: string | null): CityIndexRule[] => {
  if (!s) return [];
  const out: CityIndexRule[] = [];
  for (const raw of s.split(',')) {
    const parts = raw.split(':');
    if (parts.length < 2) continue;
    const [name, valStr, opStr] = parts;
    if (!name) continue;
    const value = Number(valStr);
    if (!Number.isFinite(value)) continue;
    const op = opStr && _VALID_OPS.has(opStr)
      ? (opStr as CityIndexRule['op'])
      : '>=';
    out.push({ index_name: name, op, value });
  }
  return out;
};

const fmtCityIndexRules = (rules: CityIndexRule[]): string =>
  rules
    .map((r) => {
      const op = r.op && r.op !== '>=' ? `:${r.op}` : '';
      return `${r.index_name}:${r.value}${op}`;
    })
    .join(',');

/* `pg` URL shape: rules joined by `;`, each `datasetId:fromYm:toYm:rentMin:saleMin`.
 * fromYm/toYm are 'YYYY-MM' (no colons) or empty; rent/sale min empty = no bound. */
const _YM_RE = /^\d{4}-\d{2}$/;
const parsePriceGrowthRules = (s: string | null): PriceGrowthRule[] => {
  if (!s) return [];
  const out: PriceGrowthRule[] = [];
  for (const raw of s.split(';')) {
    const parts = raw.split(':');
    const datasetId = Number(parts[0]);
    if (!Number.isInteger(datasetId)) continue;
    const ym = (v: string | undefined): string | null => (v && _YM_RE.test(v) ? v : null);
    const num = (v: string | undefined): number | null => {
      if (v == null || v === '') return null;
      const n = Number(v);
      return Number.isFinite(n) ? n : null;
    };
    out.push({
      datasetId,
      fromYm: ym(parts[1]),
      toYm: ym(parts[2]),
      rentMinPct: num(parts[3]),
      saleMinPct: num(parts[4]),
    });
  }
  return out;
};

const fmtPriceGrowthRules = (rules: PriceGrowthRule[]): string =>
  rules
    .map((r) =>
      [r.datasetId, r.fromYm ?? '', r.toYm ?? '', r.rentMinPct ?? '', r.saleMinPct ?? ''].join(':'),
    )
    .join(';');

const parseBounds = (s: string | null): MapBounds | null => {
  if (!s) return null;
  const parts = s.split(',');
  if (parts.length !== 4) return null;
  const [w, sLat, e, n] = parts.map(Number);
  if (![w, sLat, e, n].every((x) => Number.isFinite(x))) return null;
  if (w >= e || sLat >= n) return null;
  return { west: w, south: sLat, east: e, north: n };
};

const fmtBoundsCoord = (n: number): string => Number(n.toFixed(5)).toString();

const parseCenterRadius = (s: string | null): CenterRadius | null => {
  if (!s) return null;
  const parts = s.split(',');
  if (parts.length !== 3) return null;
  const [lat, lng, radius] = parts.map(Number);
  if (![lat, lng, radius].every((x) => Number.isFinite(x))) return null;
  if (radius <= 0) return null;
  return { lat, lng, radius_m: Math.trunc(radius) };
};

const fmtRange = (lo: number | null, hi: number | null): string =>
  `${lo ?? ''}-${hi ?? ''}`;

/* Serialise a `DistrictChip[]` into the parallel `districts` / `districts_ctx` /
 * `districts_excl` / `districts_lvl` / `districts_id` CSV shape `parseDistrictChips`
 * reads back — the one wire format every location-filterable surface uses (Browse's
 * URL here; the API params in `lib/api.ts`). The level and code are always
 * written; context and exclude flags only when a chip needs them. */
export const districtChipsToCsvParams = (
  districts: ReadonlyArray<DistrictChip>,
): Record<string, string> => {
  if (!districts.length) return {};
  const out: Record<string, string> = {
    districts: joinCsv(districts.map((d) => d.name)),
  };
  if (districts.some((d) => d.context !== null)) {
    out.districts_ctx = joinCsv(districts.map((d) => d.context ?? ''));
  }
  if (districts.some((d) => d.excluded)) {
    out.districts_excl = joinCsv(districts.map((d) => (d.excluded ? '1' : '0')));
  }
  out.districts_lvl = joinCsv(districts.map((d) => d.level));
  out.districts_id = joinCsv(districts.map((d) => String(d.id)));
  return out;
};

export const toSearchParams = (f: ListingFilters): URLSearchParams => {
  const sp = new URLSearchParams();
  /* Emit `cat` whenever the set differs from the default ['byt'] — including
   * the empty set ("all categories"), serialised as an empty `cat=` so a
   * deliberate "all" round-trips distinctly from the omitted default. */
  if (!(f.categoryMain.length === 1 && f.categoryMain[0] === 'byt')) {
    sp.set('cat', f.categoryMain.join(','));
  }
  if (f.categoryType == null) sp.set('deal', ANY_DEAL_TYPE);
  else if (f.categoryType !== DEFAULT_FILTERS.categoryType) sp.set('deal', f.categoryType);
  for (const [k, v] of Object.entries(districtChipsToCsvParams(f.districts))) {
    sp.set(k, v);
  }
  if (f.dispositions.length) sp.set('disposition', f.dispositions.join(','));
  if (f.priceMin != null || f.priceMax != null) {
    sp.set('price', fmtRange(f.priceMin, f.priceMax));
  }
  if (f.includeNoPrice) sp.set('no_price', '1');
  if (f.pricePerM2Min != null || f.pricePerM2Max != null) {
    sp.set('ppm2', fmtRange(f.pricePerM2Min, f.pricePerM2Max));
  }
  if (f.mfGrossYieldPctMin != null || f.mfGrossYieldPctMax != null) {
    sp.set('yield', fmtRange(f.mfGrossYieldPctMin, f.mfGrossYieldPctMax));
  }
  if (f.areaMin != null || f.areaMax != null) {
    sp.set('area', fmtRange(f.areaMin, f.areaMax));
  }
  if (f.status !== 'any') sp.set('status', f.status);
  if (f.lastSeenMinDays != null || f.lastSeenMaxDays != null) {
    sp.set('seen', fmtRange(f.lastSeenMinDays, f.lastSeenMaxDays));
  }
  if (f.firstSeenMinDays != null || f.firstSeenMaxDays != null) {
    sp.set('first', fmtRange(f.firstSeenMinDays, f.firstSeenMaxDays));
  }
  if (f.recentlyAddedDays != null) sp.set('added', String(f.recentlyAddedDays));
  if (f.recentlyChangedDays != null) sp.set('changed', String(f.recentlyChangedDays));
  if (f.tomDaysMin != null || f.tomDaysMax != null) {
    sp.set('tom', fmtRange(f.tomDaysMin, f.tomDaysMax));
  }
  if (f.tomObservedOnly) sp.set('observed', '1');
  if (f.hasBalcony !== 'any') sp.set('balcony', f.hasBalcony);
  if (f.hasLift !== 'any') sp.set('lift', f.hasLift);
  if (f.hasParking !== 'any') sp.set('parking', f.hasParking);
  if (f.terrace !== 'any') sp.set('terrace', f.terrace);
  if (f.cellar !== 'any') sp.set('cellar', f.cellar);
  if (f.garage !== 'any') sp.set('garage', f.garage);
  if (f.furnished.length) sp.set('furnished', f.furnished.join(','));
  if (f.ownership.length) sp.set('ownership', f.ownership.join(','));
  if (f.portals.length) sp.set('portal', f.portals.join(','));
  if (f.portalsExcluded.length) sp.set('portal_excl', f.portalsExcluded.join(','));
  if (f.portalsExcluded.length && f.portalsExcludedActiveOnly) sp.set('portal_excl_active', '1');
  if (f.conditionMatch.length) sp.set('condition', f.conditionMatch.join(','));
  if (f.categorySubCb != null) sp.set('subcat', String(f.categorySubCb));
  if (f.subtype.length) sp.set('subtype', f.subtype.join(','));
  if (f.buildingMaterial.length) sp.set('build', f.buildingMaterial.join(','));
  if (f.energyRatingMatch.length) sp.set('energy', f.energyRatingMatch.join(','));
  if (f.estateAreaMin != null || f.estateAreaMax != null) {
    sp.set('estate', fmtRange(f.estateAreaMin, f.estateAreaMax));
  }
  if (f.usableAreaMin != null || f.usableAreaMax != null) {
    sp.set('usable', fmtRange(f.usableAreaMin, f.usableAreaMax));
  }
  if (f.parkingLotsMin != null) sp.set('parking_min', String(f.parkingLotsMin));
  if (f.gardenAreaMin != null) sp.set('garden_min', String(f.gardenAreaMin));
  if (f.gardenAreaMax != null) sp.set('garden_max', String(f.gardenAreaMax));
  if (f.priceChangeCountMin != null) sp.set('changes_min', String(f.priceChangeCountMin));
  if (f.priceChangeWindowDays != null) sp.set('changes_window', String(f.priceChangeWindowDays));
  if (f.totalPriceChangePct != null) sp.set('total_change_pct', String(f.totalPriceChangePct));
  if (f.withEstimates) sp.set('with_est', '1');
  if (f.showDismissed) sp.set('dismissed', 'show');
  setScopeToParams(sp, 'pipeline', f.pipeline);
  if (f.brokerId != null) sp.set('broker', String(f.brokerId));
  setScopeToParams(sp, 'tags', f.tags);
  setScopeToParams(sp, 'collections', f.collections);
  if (f.bounds) {
    const { west, south, east, north } = f.bounds;
    sp.set(
      'bbox',
      `${fmtBoundsCoord(west)},${fmtBoundsCoord(south)},${fmtBoundsCoord(east)},${fmtBoundsCoord(north)}`,
    );
  }
  if (f.locationMode === 'center_radius') sp.set('locmode', 'center_radius');
  if (f.centerRadius) {
    const { lat, lng, radius_m } = f.centerRadius;
    sp.set(
      'center',
      `${fmtBoundsCoord(lat)},${fmtBoundsCoord(lng)},${radius_m}`,
    );
  }
  if (f.cityIndexRules.length) {
    sp.set('cq_rules', fmtCityIndexRules(f.cityIndexRules));
  }
  if (f.minCityPopulation != null) sp.set('cq_pop_min', String(f.minCityPopulation));
  if (f.maxCityPopulation != null) sp.set('cq_pop_max', String(f.maxCityPopulation));
  if (f.nearPop5kmMin != null) sp.set('np5', String(f.nearPop5kmMin));
  if (f.nearPop15kmMin != null) sp.set('np15', String(f.nearPop15kmMin));
  if (f.nearJobs5kmMin != null) sp.set('nj5', String(f.nearJobs5kmMin));
  if (f.nearJobs15kmMin != null) sp.set('nj15', String(f.nearJobs15kmMin));
  if (f.nearYouth5kmMin != null) sp.set('ny5', String(f.nearYouth5kmMin));
  if (f.nearYouth15kmMin != null) sp.set('ny15', String(f.nearYouth15kmMin));
  if (f.nearOverall5kmMin != null) sp.set('no5', String(f.nearOverall5kmMin));
  if (f.nearOverall15kmMin != null) sp.set('no15', String(f.nearOverall15kmMin));
  if (f.priceGrowthRules.length) sp.set('pg', fmtPriceGrowthRules(f.priceGrowthRules));
  return sp;
};

/* Convert a "days ago" integer to an ISO timestamp for PostgREST
 * predicates. n=7 -> seven days ago. */
export const isoNDaysAgo = (days: number): string =>
  new Date(Date.now() - days * 86_400_000).toISOString();

const CATEGORY_MAIN_PLURAL: Record<CategoryMain, string> = {
  byt: 'apartments',
  dum: 'houses',
  komercni: 'commercial',
  pozemek: 'land',
  ostatni: 'other',
};

/* Join the multi-select category_main into a human label, e.g.
 * ['byt','dum'] -> "apartments / houses". Empty = "properties" (no constraint). */
const categoryMainLabel = (cats: ReadonlyArray<CategoryMain>): string =>
  cats.length === 0
    ? 'properties'
    : cats.map((c) => CATEGORY_MAIN_PLURAL[c]).join(' / ');

/* URL token for "no deal-type constraint". Spelled the same as a curated
 * set's whole-set `any` (lib/setScope) so the two read as one vocabulary. */
export const ANY_DEAL_TYPE = 'any';

const CATEGORY_TYPE_LABEL: Record<CategoryType, string> = {
  pronajem: 'for rent',
  prodej: 'for sale',
  drazba: 'at auction',
  podil: 'fractional',
};

/* "for sale" / "for rent" / … — empty when no deal type is constrained, so the
 * caller's sentence reads "12 apartments" rather than "12 apartments for null". */
export const categoryTypeLabel = (t: CategoryType | null): string =>
  t == null ? '' : CATEGORY_TYPE_LABEL[t];

const fmtDaysRange = (lo: number | null, hi: number | null): string => {
  if (lo == null && hi == null) return '';
  if (lo != null && hi != null) return `${lo}–${hi} d`;
  if (lo != null)               return `≥ ${lo} d`;
  return `≤ ${hi} d`;
};

export const summarise = (f: ListingFilters, count: number | null): string => {
  const bits: string[] = [];
  bits.push(f.status === 'active' ? 'active' : f.status === 'inactive' ? 'inactive' : 'all');
  const countStr = count == null ? '…' : count.toLocaleString('cs-CZ');
  bits.push(`${countStr} ${categoryMainLabel(f.categoryMain)}`);
  const dealLabel = categoryTypeLabel(f.categoryType);
  if (dealLabel) bits.push(dealLabel);
  if (f.districts.length) {
    const shown = f.districts
      .slice(0, 3)
      .map((d) => {
        const base = d.context ? `${d.name} · ${d.context}` : d.name;
        return d.excluded ? `−${base}` : base;
      })
      .join(', ');
    const extra = f.districts.length > 3 ? ` +${f.districts.length - 3}` : '';
    bits.push(`in ${shown}${extra}`);
  }
  if (f.dispositions.length) {
    bits.push(`(${f.dispositions.slice(0, 4).join(', ')}${f.dispositions.length > 4 ? '…' : ''})`);
  }
  const portalScope = portalScopeLabel(f.portals, f.portalsExcluded, f.portalsExcludedActiveOnly);
  if (portalScope) bits.push(portalScope);
  const seenLabel = fmtDaysRange(f.lastSeenMinDays, f.lastSeenMaxDays);
  if (seenLabel) bits.push(`last seen ${seenLabel}`);
  if (f.recentlyAddedDays != null) bits.push(`added ≤ ${f.recentlyAddedDays} d`);
  if (f.recentlyChangedDays != null) bits.push(`changed ≤ ${f.recentlyChangedDays} d`);
  const tomLabel = fmtDaysRange(f.tomDaysMin, f.tomDaysMax);
  if (tomLabel) bits.push(`TOM ${tomLabel}`);
  if (f.tomObservedOnly) bits.push('newly listed');
  for (const name of CURATED_SET_NAMES) {
    const words = scopeWords(name, f[name]);
    if (words) bits.push(words);
  }
  if (f.brokerId != null) bits.push('for one broker');
  if (f.bounds) bits.push('in this map area');
  return `Showing ${bits.join(' ')}`;
};

/* A short, human default name for a watchdog created from the current Browse
 * filters — `category type · dispositions · districts`, e.g.
 * "byt prodej · 2+kk, 2+1 · Jihlava, HB". Operator can overwrite it in the
 * Create-watchdog dialog. */
export const watchdogNameSuggestion = (f: ListingFilters): string => {
  const parts: string[] = [
    `${f.categoryMain.join('+') || 'vše'} ${f.categoryType ?? 'vše'}`,
  ];
  if (f.dispositions.length) {
    parts.push(
      f.dispositions.slice(0, 4).join(', ')
      + (f.dispositions.length > 4 ? '…' : ''),
    );
  }
  if (f.districts.length) {
    parts.push(
      f.districts
        .slice(0, 3)
        .map((d) => (d.excluded ? `−${d.name}` : d.name))
        .join(', ')
      + (f.districts.length > 3 ? '…' : ''),
    );
  }
  const portalScope = portalScopeLabel(f.portals, f.portalsExcluded, f.portalsExcludedActiveOnly);
  if (portalScope) parts.push(portalScope);
  return parts.join(' · ');
};

/* Deterministic "disposition · area · city" summary of the active Browse
 * filters, for the browser-tab title (the constraints that most identify a
 * Browse tab at a glance). Each segment is omitted when empty; returns null
 * when nothing relevant is set, so the caller falls back to the static
 * "Browse" route title. Reuses fmtArea; only INCLUDE district chips name the
 * place (an excluded district is a subtraction, not where the cohort is), one
 * name + "+N" because tab space is narrow. Dispositions are joined with ", " (they already contain
 * "+", e.g. "2+kk"). */
export const browseTitleSummary = (f: ListingFilters): string | null => {
  const segments: string[] = [];

  if (f.dispositions.length) {
    segments.push(
      f.dispositions.length <= 3
        ? f.dispositions.join(', ')
        : `${f.dispositions.slice(0, 2).join(', ')} +${f.dispositions.length - 2}`,
    );
  }

  const { areaMin, areaMax } = f;
  if (areaMin != null && areaMax != null) {
    segments.push(`${areaMin.toLocaleString('cs-CZ')}–${fmtArea(areaMax)}`);
  } else if (areaMin != null) {
    segments.push(`≥ ${fmtArea(areaMin)}`);
  } else if (areaMax != null) {
    segments.push(`≤ ${fmtArea(areaMax)}`);
  }

  const cities = f.districts.filter((d) => !d.excluded).map((d) => d.name.trim()).filter(Boolean);
  if (cities.length === 1) segments.push(cities[0]);
  else if (cities.length > 1) segments.push(`${cities[0]} +${cities.length - 1}`);

  return segments.length ? segments.join(' · ') : null;
};

/* Generic compare against DEFAULT_FILTERS so new fields can never be
 * forgotten here (the old hand-maintained &&-chain silently missed
 * priceGrowthRules). Arrays count as default when empty; object-valued
 * fields (bounds, centerRadius) default to null, so a
 * null-vs-set check is exact. */
export const isDefault = (f: ListingFilters): boolean =>
  (Object.keys(DEFAULT_FILTERS) as Array<keyof ListingFilters>).every((k) => {
    const value = f[k];
    const def = DEFAULT_FILTERS[k];
    /* Array-valued fields are default iff they equal the default array
     * (order-insensitive). Every array default is [] except categoryMain
     * (['byt']) — for [] this reduces to the old "is empty" check. */
    if (Array.isArray(def)) {
      return (
        Array.isArray(value)
        && value.length === def.length
        && def.every((d) => (value as unknown[]).includes(d))
      );
    }
    if (def === null) return value == null;
    return value === def;
  });


/* -------------------------------------------------------------------------- */
/* Saved filter presets                                                        */
/*                                                                            */
/* A preset stores the full ListingFilters. These helpers normalise what we   */
/* persist and detect whether a loaded preset has since been edited. Equality  */
/* is computed on the canonical URL form (`toSearchParams`) — the single       */
/* source of truth for what a filter set "is" — rather than a hand-rolled      */
/* deep-equal that would rot as fields are added.                              */
/* -------------------------------------------------------------------------- */

/** Persisted preset payload. The opaque `filter_spec` blob the API stores
 *  verbatim. `sort` is the serialized sort param (`sortToParam` form, e.g.
 *  "-price_czk"), stored as a plain string. `null`/absent means the default
 *  sort. */
export interface PresetSpec {
  filters: ListingFilters;
  sort?: string | null;
}

/* The Pipeline lens as a COMPLETE view, not a modifier.
 *
 * The chip lives in the preset row, and every other chip in that row REPLACES
 * the filter set. Ours ANDed itself onto whatever was set instead — and Browse's
 * default cohort is already narrow (byt + pronájem), so "show me my pipeline"
 * showed 1 of 45 deals: the sale flats, the houses and the commercial units were
 * all filtered out by defaults the operator never chose for this purpose.
 *
 * So loading the lens loads a NEUTRAL cohort: no category, no deal type, no
 * price/area/location narrowing — every deal on the board, whatever it is. The
 * operator then narrows from there (Byty, Prodej, a district …) and the count
 * moves, which is the "still use the other filters" half of the ask.
 *
 * `status` deliberately stays at its default `any`: a deal whose listing was
 * delisted mid-negotiation must not vanish from the pipeline view.
 *
 * The modifier semantics are still available — the sidebar's Curation → Pipeline
 * control ANDs the scope onto the current filters without resetting anything.
 * Chip = view, sidebar control = modifier, exactly like preset chip vs sidebar. */
export const pipelineViewFilters = (): ListingFilters => ({
  ...DEFAULT_FILTERS,
  categoryMain: [],
  categoryType: null,
  pipeline: { set: 'in', in: [], out: [] },
});

/* A preset keeps EVERY filter, the lenses included — the curated sets (tags,
 * collections, pipeline), the broker and the dismissed reveal: they are in what
 * it stores and in its dirty comparison, and a preset click applies them
 * (operator ruling 2026-10-22, reversing 2026-10-19; a preset saved before
 * stored them off, so it turns them off). A saved Stats DASHBOARD leaves them
 * out: it can be shared (migration 647), and they are lenses over the owner's
 * own state — tag, collection and stage ids, dismissals — that mean nothing in
 * a recipient's account. Reset before persisting, ignored when deciding whether
 * a loaded dashboard is dirty. */
const DASHBOARD_EXCLUDED_KEYS = [
  'tags', 'collections', 'pipeline', 'brokerId', 'showDismissed',
] as const satisfies ReadonlyArray<keyof ListingFilters>;

const stripDashboardExcluded = (f: ListingFilters): ListingFilters => {
  const out = { ...f };
  for (const k of DASHBOARD_EXCLUDED_KEYS) {
    (out as Record<string, unknown>)[k] = DEFAULT_FILTERS[k];
  }
  return out;
};

/** A preset's filters to persist: every filter, the transient map viewport
 *  (`bounds`) only while the operator keeps the current map area ticked, so a
 *  criteria-only preset doesn't pin a stale bounding box. */
export const filtersForPreset = (
  f: ListingFilters,
  includeMapArea: boolean,
): ListingFilters => (includeMapArea ? f : { ...f, bounds: null });

/** A dashboard's filters to persist: never the map viewport, never a lens. */
export const filtersForDashboard = (f: ListingFilters): ListingFilters =>
  stripDashboardExcluded(filtersForPreset(f, false));

/* Merge a stored (possibly stale-typed) filter blob onto DEFAULT_FILTERS,
 * dropping any field whose CARDINALITY drifted since it was saved — e.g. a
 * field that used to be a scalar (`furnished: null`) and later became a
 * multi-select array (`furnished: []`). Without this guard, loading an old
 * preset feeds `toSearchParams` a wrong-shaped value (`null.length`) and the
 * whole load throws, so clicking the chip silently does nothing. Unknown /
 * removed keys are dropped too. */
const coerceStoredFilters = (stored: unknown): ListingFilters => {
  const def = DEFAULT_FILTERS as unknown as Record<string, unknown>;
  const out: Record<string, unknown> = { ...def };
  if (stored && typeof stored === 'object') {
    const blob = stored as Record<string, unknown>;
    for (const name of CURATED_SET_NAMES) out[name] = storedScope(name, blob);
    for (const [k, vRaw] of Object.entries(blob)) {
      if (!(k in def) || (CURATED_SET_NAMES as string[]).includes(k)) continue;
      // Migration shim: categoryMain went scalar -> array. Lift an old stored
      // scalar (e.g. "dum") to ["dum"] so a preset saved before the multiselect
      // change keeps its category instead of silently reverting to ['byt'].
      const v =
        k === 'categoryMain' && typeof vRaw === 'string' ? [vRaw] : vRaw;
      // Array-vs-scalar mismatch ⇒ the field's type evolved; keep the default.
      if (Array.isArray(def[k]) !== Array.isArray(v)) continue;
      // A stored chip passes the same guard a URL chip does (parseDistrictChips).
      out[k] = k === 'districts' ? (v as unknown[]).filter(isDistrictChip) : v;
    }
  }
  return out as unknown as ListingFilters;
};

const idList = (v: unknown): number[] | null =>
  Array.isArray(v) && v.every((x) => Number.isInteger(x) && x > 0) ? (v as number[]) : null;

/* A curated set as a stored blob may hold it: a SetScope, or a shape from
 * before lib/setScope — `tags: number[]` + `tagsExcluded: number[]`,
 * `collections: number[]`, `pipeline: {stage_ids}` ([] = any stage) — lifted
 * losslessly; anything else is dropped (off). */
function storedScope(name: CuratedSet, blob: Record<string, unknown>): SetScope | null {
  const v = blob[name];
  const ids = idList(v);
  if (ids) {
    const out = name === 'tags' ? idList(blob.tagsExcluded) ?? [] : [];
    return normalizeScope({ set: null, in: ids, out });
  }
  if (!v || typeof v !== 'object') return null;
  const o = v as Record<string, unknown>;
  if (name === 'pipeline' && 'stage_ids' in o) {
    const stages = idList(o.stage_ids);
    if (!stages) return null;
    return stages.length ? { set: null, in: stages, out: [] } : { set: 'in', in: [], out: [] };
  }
  const inc = idList(o.in);
  const exc = idList(o.out);
  if (!inc || !exc || !(o.set === null || o.set === 'in' || o.set === 'out')) return null;
  return normalizeScope({ set: o.set, in: inc, out: exc });
}

/** Read a stored preset blob, tolerating the legacy bare-`ListingFilters`
 *  shape (presets saved before sort was captured) and fields whose type drifted
 *  after the preset was saved. Always returns a complete, shape-safe filter set
 *  merged onto DEFAULT_FILTERS, plus the saved sort as parseSort reads it, so a
 *  preset whose sort has no control loads clean, not dirty. */
export const readPresetSpec = (
  spec: PresetSpec | ListingFilters,
): { filters: ListingFilters; sort: string } => {
  const wrapped =
    spec != null && (spec as PresetSpec).filters != null;
  const filters = wrapped
    ? (spec as PresetSpec).filters
    : (spec as ListingFilters);
  const sort = wrapped ? (spec as PresetSpec).sort ?? null : null;
  return { filters: coerceStoredFilters(filters), sort: sortToParam(parseSort(sort)) };
};

/* The area a saved filter puts in the map frame when it is loaded: the box it
 * filters on (effectiveBbox — a saved map area, or the square around a saved
 * centre+radius); null when it saved no area, and the camera stays put. */
export const savedMapArea = (spec: PresetSpec | ListingFilters): MapBounds | null =>
  effectiveBbox(readPresetSpec(spec).filters);

const canonicalParams = (sp: URLSearchParams): string =>
  [...sp.entries()]
    .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))
    .map(([k, v]) => `${k}=${v}`)
    .join('&');

/** True when `current` matches the `saved` preset. The map viewport is
 *  ignored unless the preset deliberately stored one, so panning the map
 *  after loading a criteria-only preset doesn't mark it dirty. Both sides
 *  are merged onto DEFAULT_FILTERS first so a preset persisted under an
 *  older schema (missing a newer field) still compares cleanly. */
export const filtersEqualForPreset = (
  current: ListingFilters,
  saved: ListingFilters,
): boolean => {
  const a = toSearchParams({ ...DEFAULT_FILTERS, ...current });
  const b = toSearchParams({ ...DEFAULT_FILTERS, ...saved });
  if (!b.has('bbox')) a.delete('bbox');
  return canonicalParams(a) === canonicalParams(b);
};

/** The dashboard's twin: the lenses sit outside a dashboard's identity, so
 *  switching one on never lights up "Uložit změny". */
export const filtersEqualForDashboard = (
  current: ListingFilters,
  saved: ListingFilters,
): boolean =>
  filtersEqualForPreset(
    stripDashboardExcluded({ ...DEFAULT_FILTERS, ...current }),
    stripDashboardExcluded({ ...DEFAULT_FILTERS, ...saved }),
  );


/* -------------------------------------------------------------------------- */
/* Registry adapter                                                            */
/*                                                                            */
/* Browse keeps its camelCase `ListingFilters` shape; the unified              */
/* `<FilterForm>` reads snake_case registry ids. These two helpers bridge      */
/* the gap at the boundary so we don't have to rename the type or the 40+     */
/* references inside `queries.ts`. Tri-state amenities pivot here too:        */
/* `'any' | 'yes' | 'no'` ⇄ `null | true | false`.                            */
/* -------------------------------------------------------------------------- */

/** Registry id → ListingFilters key. Keys not present here aren't part
 *  of the Browse filter set (e.g. `radius_m` and `area_band_pct` are
 *  cohort-tuning knobs that don't surface on Browse). */
export const REGISTRY_KEY_MAP = {
  category_main_in: 'categoryMain',
  category_type: 'categoryType',
  category_sub_cb: 'categorySubCb',
  subtype: 'subtype',
  dispositions: 'dispositions',
  districts: 'districts',
  status: 'status',
  min_price_czk: 'priceMin',
  max_price_czk: 'priceMax',
  include_no_price: 'includeNoPrice',
  min_price_per_m2: 'pricePerM2Min',
  max_price_per_m2: 'pricePerM2Max',
  min_mf_gross_yield_pct: 'mfGrossYieldPctMin',
  max_mf_gross_yield_pct: 'mfGrossYieldPctMax',
  min_area_m2: 'areaMin',
  max_area_m2: 'areaMax',
  min_estate_area: 'estateAreaMin',
  max_estate_area: 'estateAreaMax',
  min_usable_area: 'usableAreaMin',
  max_usable_area: 'usableAreaMax',
  has_balcony: 'hasBalcony',
  has_lift: 'hasLift',
  has_parking: 'hasParking',
  terrace: 'terrace',
  cellar: 'cellar',
  garage: 'garage',
  furnished: 'furnished',
  ownership: 'ownership',
  portals: 'portals',
  portals_excluded: 'portalsExcluded',
  portals_excluded_active_only: 'portalsExcludedActiveOnly',
  condition_match: 'conditionMatch',
  building_material: 'buildingMaterial',
  energy_rating_match: 'energyRatingMatch',
  min_parking_lots: 'parkingLotsMin',
  min_garden_area: 'gardenAreaMin',
  max_garden_area: 'gardenAreaMax',
  price_change_count_min: 'priceChangeCountMin',
  price_change_window_days: 'priceChangeWindowDays',
  total_price_change_pct: 'totalPriceChangePct',
  with_estimates: 'withEstimates',
  show_dismissed: 'showDismissed',
  pipeline: 'pipeline',
  tags: 'tags',
  collections: 'collections',
  tom_days_min: 'tomDaysMin',
  tom_days_max: 'tomDaysMax',
  tom_observed_only: 'tomObservedOnly',
  last_seen_min_days: 'lastSeenMinDays',
  last_seen_max_days: 'lastSeenMaxDays',
  first_seen_min_days: 'firstSeenMinDays',
  first_seen_max_days: 'firstSeenMaxDays',
  recently_added_days: 'recentlyAddedDays',
  recently_changed_days: 'recentlyChangedDays',
  city_index_rules: 'cityIndexRules',
  min_city_population: 'minCityPopulation',
  max_city_population: 'maxCityPopulation',
  near_pop_5km_min: 'nearPop5kmMin',
  near_pop_15km_min: 'nearPop15kmMin',
  near_jobs_5km_min: 'nearJobs5kmMin',
  near_jobs_15km_min: 'nearJobs15kmMin',
  near_youth_5km_min: 'nearYouth5kmMin',
  near_youth_15km_min: 'nearYouth15kmMin',
  near_overall_5km_min: 'nearOverall5kmMin',
  near_overall_15km_min: 'nearOverall15kmMin',
} as const satisfies Record<string, keyof ListingFilters>;

type RegistryKey = keyof typeof REGISTRY_KEY_MAP;

const TRISTATE_KEYS: ReadonlyArray<RegistryKey> = [
  'has_balcony', 'has_lift', 'has_parking', 'terrace', 'cellar', 'garage',
];

const triToBoolNullable = (v: TriState): boolean | null =>
  v === 'any' ? null : v === 'yes';

const boolNullableToTri = (v: unknown): TriState => {
  if (v === null || v === undefined) return 'any';
  return v ? 'yes' : 'no';
};

/** Project a `ListingFilters` onto the snake_case shape that
 *  `<FilterForm>` reads. Tri-state amenities convert to `bool | null`;
 *  an empty list becomes `null` (the registry's "no constraint"). */
export function listingFiltersToRegistryView(
  filters: ListingFilters,
): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [registryId, key] of Object.entries(REGISTRY_KEY_MAP)) {
    const v = filters[key as keyof ListingFilters];
    if ((TRISTATE_KEYS as ReadonlyArray<string>).includes(registryId)) {
      out[registryId] = triToBoolNullable(v as TriState);
    } else if (Array.isArray(v)) {
      out[registryId] = v.length === 0 ? null : v;
    } else {
      out[registryId] = v;
    }
  }
  return out;
}

/** Reverse of `listingFiltersToRegistryView`. Given a `<FilterForm>`
 *  onChange `(id, value)`, returns the next `ListingFilters`. Unknown
 *  ids are no-ops — Browse doesn't track every registry filter. */
export function applyRegistryUpdate(
  filters: ListingFilters,
  id: string,
  value: unknown,
): ListingFilters {
  if (!(id in REGISTRY_KEY_MAP)) return filters;
  const key = REGISTRY_KEY_MAP[id as RegistryKey];
  if ((TRISTATE_KEYS as ReadonlyArray<string>).includes(id)) {
    return { ...filters, [key]: boolNullableToTri(value) };
  }
  if (Array.isArray(DEFAULT_FILTERS[key])) {
    return { ...filters, [key]: value ?? [] } as ListingFilters;
  }
  return { ...filters, [key]: value } as ListingFilters;
}

/** Apply a batch of `<FilterForm>` updates atomically. Loops
 *  `applyRegistryUpdate` so paired range edits (min + max from one
 *  slider drag) compose against the same starting state. Without
 *  this, callers that route each update through a non-functional
 *  setter (e.g. Browse's URL writer) would see the second call use
 *  the same stale `filters` and overwrite the first — that's what
 *  made the dual-thumb slider and paired number inputs refuse to
 *  update under PR #112 before this fix. */
export function applyRegistryUpdates(
  filters: ListingFilters,
  updates: ReadonlyArray<{ id: string; value: unknown }>,
): ListingFilters {
  let next = filters;
  for (const u of updates) next = applyRegistryUpdate(next, u.id, u.value);
  return next;
}

/** The sidebar's apply: a batch of registry updates plus the two clean-ups the
 *  filter UI owes the cohort, so a filter the UI no longer shows can never stay
 *  active and silently empty it. Yield exists for sale flats only, so it goes
 *  when the cohort leaves Prodej; a subtype refines houses / commercial units,
 *  so it goes with the last of them. Browse's sidebar and the phone's Filtry
 *  sheet both apply through this. */
export function applySidebarUpdates(
  filters: ListingFilters,
  updates: ReadonlyArray<{ id: string; value: unknown }>,
): ListingFilters {
  let next = applyRegistryUpdates(filters, updates);
  if (
    next.categoryType !== 'prodej' &&
    (next.mfGrossYieldPctMin != null || next.mfGrossYieldPctMax != null)
  ) {
    next = { ...next, mfGrossYieldPctMin: null, mfGrossYieldPctMax: null };
  }
  if (
    !next.categoryMain.some((c) => c === 'dum' || c === 'komercni') &&
    next.subtype.length > 0
  ) {
    next = { ...next, subtype: [] };
  }
  return next;
}

/* ------------------------------------------------------------------------- *
 * Browse filters → Watchdog spec (Create-watchdog-from-Browse).
 *
 * The watchdog matcher (`api/notifications._build_match_clauses`) honours a
 * subset of the Browse filter set — the attribute predicates that make sense
 * "the moment a new listing matches". Browse filters that the matcher has no
 * clause for are NOT silently dropped: `filtersToWatchdogSpec` reports them in
 * `unsupported` so the UI can tell the operator what won't be watched.
 *
 * Honoured (mapped): category, disposition, district chips, THE map box
 * (`bounds` — effectiveBbox, rule 16: a centre+radius pick saves as the square
 * Browse filtered on, always; the viewport box unless the caller opts it out,
 * which is the create dialog's one checkbox), price / price-per-m² / MF-yield /
 * area / usable / estate / garden bounds, building material, tri-state
 * amenities, furnished, ownership, both sides of the portal rule (MS19 +
 * migration 597), condition_match, parking-lots min, the price-history pair
 * (price-change count in a window, total price change %), and the city-quality
 * predicates (index rules, population min/max, the near-* minimums).
 *
 * NOT honoured by the matcher (reported as unsupported when set): listing
 * `status`, the last-seen / first-seen / time-on-market day ranges and the
 * `recently added/changed` presets (a watchdog already fires on brand-new /
 * changed listings, so a recency window is redundant there), `tags` and
 * `collections`. */

const UNSUPPORTED_LABELS: ReadonlyArray<{
  test: (f: ListingFilters) => boolean;
  label: string;
}> = [
  { test: (f) => f.status !== 'any', label: 'listing status' },
  {
    test: (f) =>
      f.lastSeenMinDays != null || f.lastSeenMaxDays != null
      || f.firstSeenMinDays != null || f.firstSeenMaxDays != null,
    label: 'last/first-seen date range',
  },
  {
    test: (f) => f.recentlyAddedDays != null || f.recentlyChangedDays != null,
    label: 'recently added/changed',
  },
  { test: (f) => f.tomDaysMin != null || f.tomDaysMax != null, label: 'time on market' },
  { test: (f) => f.tomObservedOnly, label: 'newly listed only' },
  { test: (f) => f.priceGrowthRules.length > 0, label: 'market growth (datasets)' },
  { test: (f) => f.withEstimates, label: 'with estimates' },
  /* A watchdog never surfaces a dismissed property, whatever the spec says. */
  { test: (f) => f.showDismissed, label: 'dismissed properties' },
  /* The curated sets are the operator's own state: a watchdog scoped to one
   * would only ever fire on properties they already put there — and "new
   * listing" events can't match a card, or a membership, that doesn't exist
   * yet. */
  ...CURATED_SET_NAMES.map((name) => ({ test: (f: ListingFilters) => scopeActive(f[name]), label: name })),
  /* "Alert me when broker X lists something new" is a real, separable feature
   * idea, not built yet — the matcher has no broker join today. */
  { test: (f) => f.brokerId != null, label: 'broker' },
];

export interface FiltersToWatchdogResult {
  spec: WatchdogFilterSpec;
  /* Human-readable names of set-but-unmonitored Browse filters. */
  unsupported: string[];
}

export function filtersToWatchdogSpec(
  filters: ListingFilters,
  { includeMapArea = true }: { includeMapArea?: boolean } = {},
): FiltersToWatchdogResult {
  const f = filters;
  const arr = <T>(xs: T[]): T[] | null => (xs.length === 0 ? null : xs);
  /* A centre+radius pick is a sidebar filter, never a stale viewport, so it
   * always rides along; the viewport box only when the caller keeps it. */
  const bounds = f.locationMode === 'center_radius' || includeMapArea ? effectiveBbox(f) : null;

  const spec: WatchdogFilterSpec = {
    ...DEFAULT_WATCHDOG_FILTER_SPEC,
    category_main_in: arr(f.categoryMain),
    category_type: f.categoryType,
    category_sub_cb: f.categorySubCb,
    subtype: arr(f.subtype),
    dispositions: arr(f.dispositions),
    districts: arr(f.districts),
    bounds,
    min_price_czk: f.priceMin,
    max_price_czk: f.priceMax,
    include_no_price: f.includeNoPrice,
    min_price_per_m2: f.pricePerM2Min,
    max_price_per_m2: f.pricePerM2Max,
    min_mf_gross_yield_pct: f.mfGrossYieldPctMin,
    max_mf_gross_yield_pct: f.mfGrossYieldPctMax,
    min_area_m2: f.areaMin,
    max_area_m2: f.areaMax,
    min_usable_area: f.usableAreaMin,
    max_usable_area: f.usableAreaMax,
    min_estate_area: f.estateAreaMin,
    max_estate_area: f.estateAreaMax,
    building_material: f.buildingMaterial.length ? [...f.buildingMaterial] : null,
    energy_rating_match: f.energyRatingMatch.length ? [...f.energyRatingMatch] : null,
    min_garden_area: f.gardenAreaMin,
    max_garden_area: f.gardenAreaMax,
    has_balcony: triToBoolNullable(f.hasBalcony),
    has_lift: triToBoolNullable(f.hasLift),
    has_parking: triToBoolNullable(f.hasParking),
    terrace: triToBoolNullable(f.terrace),
    cellar: triToBoolNullable(f.cellar),
    garage: triToBoolNullable(f.garage),
    furnished: arr(f.furnished),
    ownership: arr(f.ownership),
    portals: arr(f.portals),
    portals_excluded: arr(f.portalsExcluded),
    portals_excluded_active_only: f.portalsExcludedActiveOnly,
    condition_match: arr(f.conditionMatch),
    min_parking_lots: f.parkingLotsMin,
    price_change_count_min: f.priceChangeCountMin,
    price_change_window_days:
      f.priceChangeWindowDays != null
      && (PRICE_CHANGE_WINDOWS as ReadonlyArray<number>).includes(f.priceChangeWindowDays)
        ? (f.priceChangeWindowDays as 30 | 90 | 365)
        : null,
    total_price_change_pct: f.totalPriceChangePct,
    city_index_rules: arr(f.cityIndexRules),
    min_city_population: f.minCityPopulation,
    max_city_population: f.maxCityPopulation,
    near_pop_5km_min: f.nearPop5kmMin,
    near_pop_15km_min: f.nearPop15kmMin,
    near_jobs_5km_min: f.nearJobs5kmMin,
    near_jobs_15km_min: f.nearJobs15kmMin,
    near_youth_5km_min: f.nearYouth5kmMin,
    near_youth_15km_min: f.nearYouth15kmMin,
    near_overall_5km_min: f.nearOverall5kmMin,
    near_overall_15km_min: f.nearOverall15kmMin,
  };

  const unsupported = UNSUPPORTED_LABELS.filter((u) => u.test(f)).map((u) => u.label);
  return { spec, unsupported };
}
