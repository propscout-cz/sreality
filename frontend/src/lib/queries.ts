import { supabase } from './supabase';
import { fetchAllRows } from './fetchAllRows';
import { pgRead, type PgReadOptions } from './pgRead';
import {
  composePipelineCards,
  PIPELINE_BOARD_COLS,
  type PipelineBoardRow,
} from './pipelineBoardModel';
import { fetchBrokerPropertyIds } from './brokers';
import type { LlmCostDailyRow, LlmCostHourlyRow } from './llmCosts';
import {
  type ListingFilters,
  type ListingStatus,
  buildingMaterialToValues,
  effectiveBbox,
  isoNDaysAgo,
  priceChangeCountColumn,
  UNKNOWN_FILTER_VALUE,
  CONDITION_CANONICAL,
  FURNISHED_CANONICAL,
  OWNERSHIP_CANONICAL,
} from './filters';
import { applyAgendaFilters, applyRegistryFilters } from './registryQueryBuilder';
import { scopeArgs } from './setScope';
import {
  applyKeyset,
  nextCursorFrom,
  withKeysetColumns,
  type KeysetBuilder,
  type KeysetCursor,
} from './keyset';
import { fetchGrowth } from './priceStats';
import type {
  ImagePublic,
  ListingFreshnessCheckPublic,
  ListingPublic,
  ListingSnapshotPublic,
  PropertySource,
  ReferenceRent,
  SoldComparable,
  SoldCoverage,
} from './types';
import type { BorderCase } from './api';

export const CARD_PAGE_SIZE = 24;

/* Every Browse row carries `listing_id` (the surrogate = the repr child's
 * listings.id, migration 343 on browse_list) — the stable,
 * NEVER-NULL identity used for React keys, the maplibre feature-state id, and
 * the hover-sync set. The card detail link is CANONICAL-first: `source` +
 * `source_id_native` (migration 362 exposed the latter on the Browse read path)
 * build `/listing/{source}/{native}`, so the URL bar never flashes the negative
 * synthetic id. `sreality_id` stays selected for the legacy fallback (and as a
 * sort field) but is NULLABLE post-Gate-2, so it must never be a key/feature id.
 * The map carries neither: its popup links by `property_id` (propertyPath, with
 * listing_id as the advert), so MAP_COLS selects `property_id` explicitly
 * (the cards get it from withKeysetColumns). */
/* `price_per_m2` is the SERVER measure (migration 425) and `price_per_m2_basis`
 * is the SERVER label for it — both published on browse_list, so the pin
 * reads the basis rather than re-deriving it.
 * Reading it PER FEATURE is not a convenience: Browse never forces a sale/rent
 * choice (rule 22 — `deal=any` is a first-class cohort), so one map can hold a
 * sale pin and a rent pin at once, and a map-wide unit would be wrong on one of
 * them by ~300x. `category_main` stays for what the basis does not say: whether
 * this row's area_m2 is floor area or PLOT area (the popup says so). */
/* Every column here is serialised into a GeoJSON feature property for EVERY pin, so
 * an unread column is pure wire cost. `subtype` (0 of 50,000 non-NULL in the default
 * cohort) and `tom_days` (fully populated, read nowhere) were both dropped in W6a --
 * ~1.5 MB of the measured 22.66 MB. Add a column here only if ListingMap actually
 * renders it. */
/* W3: `district` came OUT and `display_label` went in -- one string per pin, from
 * listing_location through the projection, so the popup no longer names the okres
 * when it knows the street. The two that joined it are what the PIN PRECISION
 * reads, and they are the only two columns on this list that ListingMap draws
 * rather than prints: a pin resolved below building level is drawn as an open
 * ring, the clicked one with a circle of `uncertainty_radius_m` metres around it,
 * and `granularity_rank` is the INT form of the rung (migration 380), because
 * granularity is compared by rank and never by enum text. */
const MAP_COLS = 'listing_id,property_id,lat,lng,price_czk,price_per_m2,price_per_m2_basis,category_main,disposition,area_m2,display_label,uncertainty_radius_m,granularity_rank,last_seen_at,is_active';
const CARD_COLS =
  'listing_id,property_id,sreality_id,source,source_id_native,display_label,disposition,subtype,area_m2,price_czk,first_seen_at,last_seen_at,is_active,tom_days,' +
  /* The point of the property (its location ad) — the hovered card hands it to the map. */
  'lat,lng,' +
  /* The Kč/m² figure reads the SERVER measure (migration 425), not price/area:
   * the column is what the sort and the Kč/m² filter already use, so selecting
   * it is what keeps the number on screen identical to the number the cohort
   * was built from. withKeysetColumns dedupes, so naming it here is safe even
   * when it is also the active sort field. */
  'price_per_m2,' +
  /* The published BASIS label of that measure, plus the two columns that feed
   * it. Without the label the Kč/m² figure has a number and no unit. Without
   * category_type the price cannot tell a monthly rent from a purchase price.
   * category_main also names the denominator (plot vs floor area) for surfaces
   * with room to say so. browse_list carries all three (migration 425 asserts
   * the basis column is present before it commits).
   * NOTE for future editors: tests/test_browse_read_path_guardrail.py parses
   * this constant by naive single-quote pairing AND stops at the first
   * semicolon, so an apostrophe or a semicolon anywhere in these comments
   * truncates the select-list into nonsense. Keep both out. */
  'price_per_m2_basis,category_main,category_type,' +
  /* The two price-history columns back <PriceDelta>. Both were already on
   * browse_list (migrations 276/343/363) and simply never selected — they
   * existed only as filter inputs, never as anything displayed. */
  'mf_gross_yield_pct,total_price_change_pct,price_change_count,' +
  /* The lot the card names after the floor area and the Lot area sort orders by:
   * THE plot measure (migration 534) and never the bare estate_area column. */
  'plot_area_m2,' +
  /* The portal badge (MS19, migration 590): the portals with an active ad, else every portal. */
  'all_sources,active_sources,' +
  /* The ad-count badge (migration 590): how many ads the property holds, active or not. */
  'source_count';

/* The two Browse select-lists, grouped for one purpose: a test can assert
 * that the measure's published LABEL travels with the measure on every lane.
 * `price_per_m2` without `price_per_m2_basis` is a number nothing can label,
 * and the failure mode is silent — a rent renders under a sale unit. */
export const BROWSE_SELECT_COLUMNS = {
  map: MAP_COLS,
  cards: CARD_COLS,
} as const;

/* Browse's sort (the field union, the URL token, the one menu) lives in a leaf
 * module so filters.ts can read a stored sort without importing this file; it
 * is re-exported here so every existing import site keeps working. */
import type { SortSpec } from './browseSort';
export {
  CARD_SORTS,
  DEFAULT_SORT,
  parseSort,
  sortToParam,
  type SortDirection,
  type SortField,
  type SortSpec,
} from './browseSort';

/* -------------------------------------------------------------------------- */
/* THE PORTAL RULE (MS19, migration 590; two-sided since 597): portal and     */
/* broker filters select ADS; rows are always properties.                     */
/* -------------------------------------------------------------------------- */
/* A property matches portal set P when any of its ads is on P, active or not
 * (`all_sources`); the status switch is judged on those ads: active = an active
 * ad on P (`active_sources`), inactive = ads on P and none of them active. With
 * no portal the status is the property's own. AND it has no ad on an excluded
 * portal — any ad, or only active ones under `portalsExcludedActiveOnly`. One
 * rule in three renderings, pinned to each other by tests/fixtures/
 * portal_rule.json: this one (list, cards, count, map points),
 * `public.portal_status_matches` (Stats, map cells, the broker lookup) and the
 * Watchdog's compiled clauses.
 *
 * A broker filter moves the include side onto the broker's own ads: the server
 * judges portal and status over them (fetchBrokerPropertyIds), so one ad
 * satisfies both and that side steps aside here. The exclusion is a statement
 * about the PROPERTY, so neither a broker nor the status switch moves it. */
export const adScope = (f: ListingFilters): { portals: string[]; status: ListingStatus } =>
  f.brokerId != null ? { portals: [], status: 'any' } : { portals: f.portals, status: f.status };

interface PortalRuleBuilder {
  eq(c: string, v: unknown): PortalRuleBuilder;
  overlaps(c: string, v: readonly string[]): PortalRuleBuilder;
  not(c: string, op: string, v: unknown): PortalRuleBuilder;
}

export const applyPortalRule = <T>(q: T, f: ListingFilters): T => {
  const { portals, status } = adScope(f);
  let r = q as unknown as PortalRuleBuilder;
  if (portals.length === 0) {
    if (status === 'active') r = r.eq('is_active', true);
    else if (status === 'inactive') r = r.eq('is_active', false);
  } else if (status === 'active') {
    r = r.overlaps('active_sources', portals);
  } else {
    r = r.overlaps('all_sources', portals);
    if (status === 'inactive') r = r.not('active_sources', 'ov', `{${portals.join(',')}}`);
  }
  if (f.portalsExcluded.length) {
    const ads = f.portalsExcludedActiveOnly ? 'active_sources' : 'all_sources';
    r = r.not(ads, 'ov', `{${f.portalsExcluded.join(',')}}`);
  }
  /* Redundant by the stored invariant (listed <=> dated): narrows nothing, lets the
   * portal's partial index serve every sort AND the count, so list, cards and count
   * keep ONE filter chain. */
  const sole = solePortal(f);
  if (sole) r = r.not(`newest_ad_at_${sole}`, 'is', null);
  return r as unknown as T;
};

/* The one INCLUDED portal, when exactly one is: it names the newest-ad column the
 * one-portal order sorts by and the conjunct above narrows on, so the two cannot
 * disagree (keyset.ts treats that column as NOT NULL on every paged row). */
const solePortal = (f: ListingFilters): string | null =>
  f.portals.length === 1 ? f.portals[0] : null;

/* Relation names, in one place so a fetcher cannot read one relation and count
 * another. */
const BROWSE_LIST_RELATION = 'browse_list';

/* The one Browse source function (migrations 537 + 629 + 630: list, counts and
 * map pins alike): the same rows, minus
 * the caller's dismissed properties unless revealed, narrowed by the curated-set
 * scope — membership judged server-side under the caller's RLS, because an
 * operator's own sets only grow and can never ride in the GET URL. */
const BROWSE_LIST_SOURCE = 'browse_list_visible';

interface CountOption {
  count?: 'exact';
  head?: boolean;
}

const selectFrom = (relation: string, columns: string, opts?: CountOption) =>
  supabase.from(relation).select(columns, opts);

/* THE start of every Browse cohort read, so the list, its count, the map pins
 * and the no-price hint cannot disagree about which properties are hidden. Only
 * the active scope sides are sent (lib/setScope.scopeArgs). */
const readSource = (
  fn: string,
  f: ListingFilters,
  columns: string,
  opts: CountOption = {},
): ReturnType<typeof selectFrom> =>
  supabase
    .rpc(fn, { hide_dismissed: !f.showDismissed, ...scopeArgs(f) }, { get: true, ...opts })
    .select(columns) as unknown as ReturnType<typeof selectFrom>;

const listSource = (f: ListingFilters, columns: string, opts?: CountOption) =>
  readSource(BROWSE_LIST_SOURCE, f, columns, opts);

/* One portal's "Newest first" (MS19, Q49 b): with exactly one portal P selected,
 * "Newest first" and "Oldest first" order properties by when their newest ad on
 * P was first seen (`newest_ad_at_<P>`, migration 588, one partial index per
 * portal on browse_list since 590), so a property advertised on P again rises
 * to the top. No portal, several, or any other sort: unchanged; a broker filter
 * changes neither rule. Only the order changes — every filter keeps its meaning
 * and rows keep the property's own dates. Never in SORTABLE_FIELDS or a URL. */
export const orderPortal = (f: ListingFilters, sort: SortSpec): string | null =>
  sort.field === 'first_seen_at' ? solePortal(f) : null;

export const effectiveSort = (f: ListingFilters, sort: SortSpec): SortSpec => {
  const p = orderPortal(f, sort);
  return p ? { field: `newest_ad_at_${p}`, direction: sort.direction } : sort;
};

/* The location chips compile to ONE code predicate, defined once in
 * `lib/districtCodes.ts` and re-exported here so every existing import site
 * keeps working. What used to live in this file — a PostgREST string builder
 * and a hand-kept in-memory twin, five predicates each, ILIKE patterns and all
 * — is now two renderings of one plan. See districtCodes.ts for the why. */
import { districtsFilterClause } from './districtCodes';

export {
  districtsFilterClause,
  matchesDistricts,
  districtCodePlan,
  type DistrictMatchRow,
} from './districtCodes';

/* Generic identity-typed helper. Postgrest's filter methods all return the
 * same builder, so passing the chain through any subset of them preserves
 * the input type at runtime.
 *
 * The straight-forward registry filters (min/max numeric ranges,
 * tristates, single-value enums, multi-value IN lists) are dispatched
 * automatically by `applyRegistryFilters` from registryQueryBuilder.ts.
 * What stays hand-coded here is the small set of irregular shapes:
 * the portal rule (`status` + `portals`, applyPortalRule), the
 * days-ago → ISO timestamp translation, the 1-enum → IN-over-many
 * `building_material` expansion, the multi-chip district predicate
 * (districtsFilterClause), and the bbox spatial predicates that aren't
 * registry filters at all. The drift test in registryQueryBuilder.test.ts
 * fails CI if a new registry filter is added that fits no path. */

/* PostgREST `.or()` argument for a NULL-tolerant absolute-price bound: keeps
 * no-price listings (price_czk IS NULL) alongside the [min,max] range. Only
 * used when `includeNoPrice` is on AND at least one bound is set. Mirrors the
 * SQL `(price >= lo and price <= hi) or price is null` that
 * browse_stats_properties + the watchdog matcher apply. Pure + exported so the
 * shape is unit-tested (like districtsFilterClause). */
export const priceNullTolerantOr = (
  min: number | null,
  max: number | null,
): string => {
  const bounds: string[] = [];
  if (min != null) bounds.push(`price_czk.gte.${min}`);
  if (max != null) bounds.push(`price_czk.lte.${max}`);
  const range = bounds.length > 1 ? `and(${bounds.join(',')})` : bounds[0];
  return `${range},price_czk.is.null`;
};

const applyFilters = <T>(q: T, f: ListingFilters): T => {
  let r = applyRegistryFilters(q, f) as unknown as {
    eq:  (c: string, v: unknown) => typeof r;
    gte: (c: string, v: unknown) => typeof r;
    lte: (c: string, v: unknown) => typeof r;
    in:  (c: string, v: readonly unknown[]) => typeof r;
    or:  (q: string) => typeof r;
  };
  r = applyPortalRule(r, f);
  /* Days-ago ranges: min_days = 3 means "seen at least 3 days ago"
   * (`last_seen <= now() - 3d`); max_days = 10 means "seen at most 10 days
   * ago" (`last_seen >= now() - 10d`). */
  if (f.lastSeenMaxDays != null) r = r.gte('last_seen_at', isoNDaysAgo(f.lastSeenMaxDays));
  if (f.lastSeenMinDays != null) r = r.lte('last_seen_at', isoNDaysAgo(f.lastSeenMinDays));
  if (f.firstSeenMaxDays != null) r = r.gte('first_seen_at', isoNDaysAgo(f.firstSeenMaxDays));
  if (f.firstSeenMinDays != null) r = r.lte('first_seen_at', isoNDaysAgo(f.firstSeenMinDays));
  /* On/off: only ads whose TOM is observed from the start (migration 625). */
  if (f.tomObservedOnly) r = r.eq('tom_observed', true);
  /* Status-section recency presets. "added" = first seen within N days;
   * "changed" = newest content snapshot (last_change_at) within N days. */
  if (f.recentlyAddedDays != null) r = r.gte('first_seen_at', isoNDaysAgo(f.recentlyAddedDays));
  if (f.recentlyChangedDays != null) r = r.gte('last_change_at', isoNDaysAgo(f.recentlyChangedDays));
  const districtsClause = districtsFilterClause(f.districts);
  if (districtsClause) r = r.or(districtsClause);
  if (f.buildingMaterial.length) {
    r = r.in('building_type', buildingMaterialToValues(f.buildingMaterial));
  }
  /* Multi-select enums with the '__unknown__' sentinel. The sentinel matches a
   * NULL or non-canonical value, which the plain `.in()` auto-dispatch can't
   * express — so they're hand-coded here as an `.or(in.(…),is.null,not.in.(…))`
   * clause. A selection without it keeps the auto path's shape (one value is
   * `.eq()`: the planner rule in registryQueryBuilder.ts). Mirrors
   * browse_stats_properties + the watchdog matcher (the shared
   * toolkit.filter_compiler._enum_or_unknown_clause). */
  const enumOrUnknown = (col: string, values: string[], canonical: readonly string[]) => {
    if (!values.includes(UNKNOWN_FILTER_VALUE)) {
      if (values.length) r = values.length === 1 ? r.eq(col, values[0]) : r.in(col, values);
      return;
    }
    const reals = values.filter((v) => v !== UNKNOWN_FILTER_VALUE);
    const known = reals.length ? [`${col}.in.(${reals.join(',')})`] : [];
    r = r.or([...known, `${col}.is.null`, `${col}.not.in.(${canonical.join(',')})`].join(','));
  };
  enumOrUnknown('furnished', f.furnished, FURNISHED_CANONICAL);
  enumOrUnknown('ownership', f.ownership, OWNERSHIP_CANONICAL);
  enumOrUnknown('condition', f.conditionMatch, CONDITION_CANONICAL);
  /* Absolute price bound (price_czk). Hand-coded — not the registry auto
   * `.gte`/`.lte` — so `includeNoPrice` can widen it to keep no-price listings:
   * a plain `.gte` already drops NULLs and a later `.or` can't add them back, so
   * the whole bound is re-expressed as one disjunction. Scope is price_czk only;
   * the price/m² + yield bounds deliberately keep dropping NULL-price rows. */
  if (f.priceMin != null || f.priceMax != null) {
    if (f.includeNoPrice) {
      r = r.or(priceNullTolerantOr(f.priceMin, f.priceMax));
    } else {
      if (f.priceMin != null) r = r.gte('price_czk', f.priceMin);
      if (f.priceMax != null) r = r.lte('price_czk', f.priceMax);
    }
  }
  /* Merged price-history filters (migration 173). The window picks which
   * precomputed count column the threshold reads; the signed total-change
   * threshold flips direction on sign (negative = "dropped at least",
   * positive = "rose at least"). Mirrors browse_stats_properties and the
   * watchdog matcher. */
  if (f.priceChangeCountMin != null) {
    r = r.gte(priceChangeCountColumn(f.priceChangeWindowDays), f.priceChangeCountMin);
  }
  if (f.totalPriceChangePct != null && f.totalPriceChangePct !== 0) {
    r = f.totalPriceChangePct < 0
      ? r.lte('total_price_change_pct', f.totalPriceChangePct)
      : r.gte('total_price_change_pct', f.totalPriceChangePct);
  }
  const bbox = effectiveBbox(f);
  if (bbox) {
    r = r.gte('lng', bbox.west)
         .lte('lng', bbox.east)
         .gte('lat', bbox.south)
         .lte('lat', bbox.north);
  }
  return r as unknown as T;
};

export interface MapRow {
  /* Surrogate identity (never null) — the maplibre feature-state id + hover-sync key. */
  listing_id: number;
  property_id: number;
  lat: number;
  lng: number;
  price_czk: number | null;
  /* The SERVER measure and the SERVER label for it. The map label switches
   * between price_czk and price_per_m2 (the "Ceny" control), and the unit is
   * decided PER PIN from `price_per_m2_basis` — see MAP_COLS. `category_main`
   * names the denominator (plot vs floor area) in the popup. */
  price_per_m2: number | null;
  price_per_m2_basis: string | null;
  category_main: string | null;
  disposition: string | null;
  area_m2: number | null;
  /* The one server-composed place string (migration 503) -- what the popup
   * prints. */
  display_label: string | null;
  /* The two the pin-precision rules read (lib/uncertaintyCircle). `granularity_rank`
   * is the INT rung from location_granularity_rank (migration 380): compare it,
   * never the enum text. A pin at BUILDING or better gets a black outer contour, and the pin
   * whose popup is open gets a circle of `uncertainty_radius_m` metres -- see
   * ListingMap. Both are NULL for an unresolved row, which draws no circle rather
   * than a circle of unknown size. */
  uncertainty_radius_m: number | null;
  granularity_rank: number | null;
  last_seen_at: string;
  is_active: boolean;
}

/* One cell from `browse_map_cells`: a world tile's centre and its exact count
 * (migration 602; lib/mapCounts regroups cells at the map's own level). */
export interface MapCell {
  lat: number;
  lng: number;
  n: number;
}

/* The server's answer above the point budget: the cells and the tile level they
 * were counted at. */
export interface MapCounts {
  level: number;
  cells: MapCell[];
}

export interface MapResult {
  /* Individual pins. Empty in count mode -- `counts` is populated instead, and
   * the two are never both non-empty. */
  rows: MapRow[];
  /* Non-null when the cohort was too large to plot point-by-point and the server
   * returned a bounded tile aggregate instead: the cells and the level they were
   * counted at (the finest that fits the server's bound; the map draws them at
   * that level or coarser). `null` means "these are points". */
  counts: MapCounts | null;
  /* The MAPPABLE cohort size — properties carrying coordinates. Deliberately
   * smaller than the header's cohortTotal, which counts coordinate-less rows
   * too (106,173 vs 104,232 on the default cohort, measured 2026-08-26). In
   * count mode this is the server's exact count of the whole cohort, not of
   * what was shipped. */
  total: number | null;
}

/* min/max city population and the near_* proximity filters are NOT here:
 * since migration 142 they're precomputed columns on properties_public, so
 * they dispatch directly via applyRegistryFilters (no prefilter RPC, no anon
 * 3s timeout). Only the flexible any-index rule list needs a prefilter. */
const hasCityQualityFilter = (f: ListingFilters): boolean =>
  f.cityIndexRules.length > 0;

/* Curated-city obec allowlist (migration 436). ONE SQL function owns rule evaluation and
 * every consumer reduces to `obec_id = ANY(...)`. <= 206 ids, ~1.5 kB, one round trip, no
 * paging — the whole point of W5 is that a Browse read path never fetches a market-scaled
 * id list again. (The path this replaces asked for 84k–262k listing ids and threw
 * FetchAllOverflowError before the SQL speed ever mattered.)
 *
 * Wire shape verified against live PostgREST: `RETURNS SETOF bigint` comes back as a bare
 * JSON array of numbers, e.g. [584495, 554782, …] — not an array of objects. */
const MAX_CURATED_OBEC_IDS = 512;   // 206 today; a band, not the count

async function resolveCityQualityObecPrefilter(
  f: ListingFilters,
  signal?: AbortSignal,
): Promise<number[] | null> {
  if (!hasCityQualityFilter(f)) return null;
  /* Untyped on purpose: the runtime check below is what narrows the wire shape. */
  const { data } = await pgRead(
    supabase.rpc('curated_cities_matching', { p_index_rules: f.cityIndexRules }),
    { signal },
  );
  const ids: unknown = data ?? [];
  if (!Array.isArray(ids) || ids.some((n) => typeof n !== 'number')) {
    throw new Error('curated_cities_matching: unexpected wire shape');
  }
  if (ids.length > MAX_CURATED_OBEC_IDS) {
    throw new Error(
      `curated_cities_matching returned ${ids.length} ids (max ${MAX_CURATED_OBEC_IDS}) — `
      + 'the curated-city set is meant to be operator-sized, not market-sized',
    );
  }
  return ids as number[];
}

/* Market-growth (price-stats datasets) prefilter. For each active rule the
 * price_stat_growth RPC computes per-obec CAGR over [fromYm, toYm]; we keep
 * obce meeting/exceeding the entered rent + sale growth thresholds (≥), then
 * INTERSECT across rules (AND across datasets). Returns null when no rule has a
 * threshold set, [] when no obec qualifies (caller short-circuits), else the
 * obec_id allowlist — applied via .in('obec_id', ids) on the cohort queries and
 * obec_ids_filter on browse_stats_properties. BROWSE-only (window-dependent). */
async function resolvePriceGrowthPrefilter(
  f: ListingFilters,
  signal?: AbortSignal,
): Promise<number[] | null> {
  const rules = f.priceGrowthRules.filter(
    (r) => r.rentMinPct != null || r.saleMinPct != null,
  );
  if (rules.length === 0) return null;
  const perRule = await Promise.all(
    rules.map(async (r) => {
      const rentMin = r.rentMinPct;
      const saleMin = r.saleMinPct;
      const rows = await fetchGrowth(r.datasetId, r.fromYm, r.toYm, { signal });
      return rows
        .filter(
          (g) =>
            (rentMin == null || (g.rent_cagr_pct != null && g.rent_cagr_pct >= rentMin))
            && (saleMin == null || (g.sale_cagr_pct != null && g.sale_cagr_pct >= saleMin)),
        )
        .map((g) => g.obec_id);
    }),
  );
  let acc = perRule[0] ?? [];
  for (let i = 1; i < perRule.length; i++) {
    const set = new Set(perRule[i]);
    acc = acc.filter((id) => set.has(id));
  }
  return acc;
}

/* With-estimates prefilter (migration 173). property_estimates_public is the
 * anon-readable property-grain projection of successful estimation runs —
 * tiny (one row per estimated property), so fetching the full id list and
 * AND'ing it via `.in('property_id', ids)` is cheap. Returns null when the
 * filter is off. */
async function resolveEstimatesPrefilter(
  f: ListingFilters,
  signal?: AbortSignal,
): Promise<number[] | null> {
  if (!f.withEstimates) return null;
  const rows = await fetchAllRows<{ property_id: number }>({
    relation: 'property_estimates_public',
    build: () =>
      supabase.from('property_estimates_public').select('property_id', { count: 'exact' }),
    orderBy: [{ column: 'property_id' }],
    key: ['property_id'],
    expectMax: 100_000,
    signal,
  });
  return rows.map((r) => r.property_id);
}

/* Broker scope prefilter ("Explore this broker's listings"). Broker data is
 * architecturally dark to the anon path this whole file otherwise reads —
 * migration 299 revoked every broker relation from anon AND authenticated —
 * so this is the one prefilter resolved over the JWT-gated /brokers/*
 * API (fetchBrokerPropertyIds) instead of PostgREST. A resolution failure
 * (e.g. no session) throws and surfaces as a normal query error like any other
 * prefilter failing — it must never silently degrade to "no constraint" (the
 * fail-open filter trap). The server applies the portal rule over the broker's
 * own ads (adScope), so this is a property_id allowlist like the others. */
async function resolveBrokerPrefilter(f: ListingFilters): Promise<number[] | null> {
  if (f.brokerId == null) return null;
  return fetchBrokerPropertyIds(f.brokerId, f.status, f.portals);
}

/* Intersect two prefilter id sets (null = "no constraint"). Used so a
 * filter that combines with-estimates + a broker applies both prefilters
 * before paging the main query. */
const intersectPrefilters = (
  a: number[] | null,
  b: number[] | null,
): number[] | null => {
  if (a == null) return b;
  if (b == null) return a;
  const set = new Set(b);
  return a.filter((id) => set.has(id));
};

/* All Browse prefilters resolved together. Each is an id allowlist at its
 * own grain (null = inactive); `empty` is true when any ACTIVE prefilter
 * matched nothing, so the caller can short-circuit to zero results without
 * issuing the main query. Shared by the Map / Cards fetchers and the
 * Stats pivot (lib/stats.ts). */
export interface BrowsePrefilters {
  obecIds: number[] | null;       // market growth (price-stats datasets)
  propertyIds: number[] | null;   // with-estimates ∩ broker
  empty: boolean;
}

export async function resolveBrowsePrefilters(
  f: ListingFilters,
  signal?: AbortSignal,
): Promise<BrowsePrefilters> {
  const [cityObec, growthObec, estimateProps, brokerProps] = await Promise.all([
    resolveCityQualityObecPrefilter(f, signal),
    resolvePriceGrowthPrefilter(f, signal),
    resolveEstimatesPrefilter(f, signal),
    resolveBrokerPrefilter(f),
  ]);
  // The property-grain allowlists intersect into the one .in('property_id', …)
  // applyPrefilters emits.
  const propertyIds = intersectPrefilters(estimateProps, brokerProps);
  // City-quality and market-growth are BOTH obec-grain now, and applyPrefilters applies
  // .in('obec_id', …) once — so they must be intersected here, not applied twice.
  const obecIds = intersectPrefilters(cityObec, growthObec);
  const empty =
    (obecIds != null && obecIds.length === 0)
    || (propertyIds != null && propertyIds.length === 0);
  return { obecIds, propertyIds, empty };
}

/* Exported for queries.test.ts. */
export const applyPrefilters = <T>(q: T, p: BrowsePrefilters): T => {
  let r = q as unknown as {
    in: (c: string, v: readonly unknown[]) => typeof r;
  };
  if (p.obecIds != null) r = r.in('obec_id', p.obecIds);
  if (p.propertyIds != null) r = r.in('property_id', p.propertyIds);
  return r as unknown as T;
};

/* Count of properties matching the CURRENT filters EXCEPT the price bound that
 * have no listed price (price_czk IS NULL) — i.e. how many a min/max price
 * bound is hiding (or, with the toggle on, including). Powers the discoverable
 * "N listings have no listed price" hint next to the Price section's toggle.
 * Reuses the exact cohort filter path (resolveBrowsePrefilters + applyFilters)
 * so it can never drift from the Map/Cards semantics. A `head:true` count, same
 * risk class as the result-badge count Browse already issues. Callers gate on a
 * price bound being set; on error the UI just omits the number (graceful). */
export const fetchNoPriceCount = async (
  f: ListingFilters,
  { signal }: PgReadOptions = {},
): Promise<number> => {
  const pre = await resolveBrowsePrefilters(f, signal);
  if (pre.empty) return 0;
  const base = listSource(f, 'property_id', { count: 'exact', head: true });
  // Strip the price bound (and the toggle) so the count is purely "no-price
  // rows in the rest of the cohort", then restrict to NULL price.
  const noPriceFilters: ListingFilters = {
    ...f, priceMin: null, priceMax: null, includeNoPrice: false,
  };
  const scoped = applyPrefilters(applyFilters(base, noPriceFilters), pre)
    .is('price_czk', null);
  const { count } = await pgRead(scoped, { signal });
  return count ?? 0;
};

interface MapGrid {
  clustered: boolean;
  cells: MapCell[] | null;
  /* The tile level of `cells` (migration 602); null with points. */
  level: number | null;
  total: number;
}

/* The per-point read, run only once the server has said the cohort fits the pin
 * limit, so it needs no limit of its own: the list's source and filter chain,
 * narrowed to rows with a point. */
const fetchMapPoints = async (
  f: ListingFilters,
  pre: BrowsePrefilters,
  signal?: AbortSignal,
): Promise<MapRow[]> => {
  const base = listSource(f, MAP_COLS)
    .not('lat', 'is', null)
    .not('lng', 'is', null);
  const scoped = applyPrefilters(applyFilters(base, f), pre);
  const { data } = await pgRead<MapRow[] | null>(scoped, { signal });
  return data ?? [];
};

export const fetchListingsForMap = async (
  f: ListingFilters,
  pinLimit: number,
  opts?: PgReadOptions,
): Promise<MapResult> => {
  // `opts?`, not `{ signal }: PgReadOptions = {}`: tests/test_browse_map_read_contract.py pins this
  // function's body by its first `{`, and a destructuring pattern or a `{}` default would be it.
  const signal = opts?.signal;
  const pre = await resolveBrowsePrefilters(f, signal);
  if (pre.empty) return { rows: [], counts: null, total: 0 };

  /* W6b, migration 439: the server answers first, with a bounded grid, or
   * `clustered: false` when the points fit. Same named parameters as
   * browse_stats_properties (one builder, so the two cohorts cannot diverge) plus
   * its own `point_budget`. The broker allowlist travels in `property_ids_filter`
   * like every other prefilter; tests/test_browse_map_read_contract.py pins every
   * emitted id space. */
  const { data: grid } = await pgRead<MapGrid>(
    supabase.rpc('browse_map_cells', {
      ...buildBrowseStatsArgs(f, pre),
      point_budget: pinLimit,
    }),
    { signal },
  );

  if (grid.clustered) {
    /* The count lane cannot truncate: the cell count is bounded by construction
     * (the finest tile level that fits the server's bound), with no LIMIT
     * anywhere in the function. */
    if (grid.level == null) throw new Error('browse_map_cells counted a cohort without naming its tile level');
    return {
      rows: [],
      counts: { level: grid.level, cells: grid.cells ?? [] },
      total: grid.total,
    };
  }

  /* Under the budget: a second round trip, on the cheap side of the threshold,
   * to get the pins themselves. Deliberate — the alternative (serialising
   * MAP_COLS inside the RPC) would put the map's select-list in two places, and
   * the alternative to THAT (probing with a limited point read first) spends
   * ~906 KB on every zoomed-out load to save a round trip on the zoomed-in one. */
  const rows = await fetchMapPoints(f, pre, signal);
  return {
    rows,
    counts: null,
    /* The server's count, not rows.length: it is the authority on how many of
     * the cohort are mappable, and the pill's "X of Y mapped" is built on it. */
    total: grid.total,
  };
};

/* The ONE cohort total — header, tab badge, the cards' "of N" label and
 * the map pill's denominator: PostgREST's exact count (a HEAD with
 * `count=exact`) over the same filter chain (resolveBrowsePrefilters +
 * applyFilters) the cards fetcher reads, so the total can never disagree
 * with the listed rows. Exact or nothing: the planner-estimate fallback of
 * 2026-10-13 answered a slow count with a figure that could sit below the
 * cards already on screen ("72 of ~15", 2026-10-16), so it is gone — a slow
 * count shows as loading, a failed one as the header's error with its retry.
 * Cost on the compact browse_list read model (2026-10-16, cold cache): 226 ms
 * market-wide (index-only), ~3 s for a one-category cohort with price and
 * disposition predicates (heap filter over 177k rows), ~1.4 s for a
 * no-category seq scan — all under the 8 s statement_timeout. */
export const fetchBrowseCount = async (
  f: ListingFilters,
  { signal }: PgReadOptions = {},
): Promise<number> => {
  const pre = await resolveBrowsePrefilters(f, signal);
  if (pre.empty) return 0;
  const { count } = await pgRead(
    applyPrefilters(
      applyFilters(listSource(f, 'property_id', { count: 'exact', head: true }), f),
      pre,
    ),
    { signal },
  );
  /* No number is an error the header shows as one (dimmed + retry), never a NaN. */
  if (count == null || !Number.isFinite(count)) throw new Error('Browse count unavailable');
  return count;
};

/* -------------------------------------------------------------------------- */
/* Cards (sreality-style image-first list): Browse's one listing lane. The    */
/* filter chain is the count's and the map's; the order is one of CARD_SORTS  */
/* (DEFAULT_SORT = first_seen_at desc, "what's new"), keyset-paged. Photos    */
/* arrive separately through the hydration layer (W7a, below).                */
/* -------------------------------------------------------------------------- */

export interface CardRow {
  /* The canonical property this card represents (Browse is property-grain via
   * properties_public). Used by the Browse merge-mode action. */
  property_id: number;
  /* Surrogate identity (never null) — the React key, the hover-sync key, and the
   * key the card image hydration batches on. `sreality_id` is nullable post-
   * Gate-2 and is only the fast detail link + the (sreality-backed) estimate. */
  listing_id: number;
  lat: number | null;
  lng: number | null;
  sreality_id: number | null;
  /* The one server-composed place string (migration 503): the five columns it
   * replaced (district / locality / obec / okres / street) are not selected. */
  display_label: string | null;
  disposition: string | null;
  /* Portal-agnostic property sub-type (migration 152) — the meaningful "kind"
   * for commercial/houses, where disposition is NULL. NULL for apartments. */
  subtype: string | null;
  area_m2: number | null;
  price_czk: number | null;
  /* The per-m² MEASURE (migration 425), not price/area: basis-resolved from
   * (category_main, category_type) and NULL below its basis floor, the same
   * column the Kč/m² sort and filter read — and its published basis label,
   * which is what the footer suffixes the figure with. */
  price_per_m2: number | null;
  price_per_m2_basis: string | null;
  first_seen_at: string;
  last_seen_at: string;
  is_active: boolean;
  tom_days: number | null;
  category_main: string | null;
  category_type: string | null;
  source: string | null;
  /* Portal-native id (migration 091). With `source`, builds the canonical
   * `/listing/{source}/{native}` link; null on pre-091 rows → legacy fallback. */
  source_id_native: string | null;
  /* MF gross rental yield % (migration 133). Non-null only on sale
   * apartments that resolved to an MF territory. */
  mf_gross_yield_pct: number | null;
  /* THE plot measure (migration 534): a house's parcel, a pozemek's own area
   * (area_m2 already shows it), NULL when the ad states no lot. */
  plot_area_m2: number | null;
  /* Signed percent across the representative listing's own price series, and
   * the per-child change count. NULL pct = fewer than two observed prices,
   * which <PriceDelta> renders as nothing rather than as "unchanged". */
  total_price_change_pct: number | null;
  price_change_count: number | null;
  /* Every portal with an ad, and those with an active ad (MS19; never NULL: the
   * projection coalesces them). The portal badge reads them. */
  all_sources: string[];
  active_sources: string[];
  /* How many ads the property holds, active or not (migration 590, the
   * recompute's count): the card's "N inzeráty" badge, drawn from two. */
  source_count: number;
  /* One portal's order column (effectiveSort), selected by withKeysetColumns
   * only while that order is on: the card shows it beside its first seen. */
  [orderColumn: `newest_ad_at_${string}`]: string | null | undefined;
}

export interface CardsResult {
  rows: CardRow[];
  nextCursor: KeysetCursor | null;
}

export const fetchListingsForCards = async (
  f: ListingFilters,
  sort: SortSpec,
  cursor: KeysetCursor | null,
  { signal }: PgReadOptions = {},
): Promise<CardsResult> => {
  const pre = await resolveBrowsePrefilters(f, signal);
  if (pre.empty) return { rows: [], nextCursor: null };
  const s = effectiveSort(f, sort);
  const base = listSource(f, withKeysetColumns(CARD_COLS, s));
  const scoped = applyPrefilters(applyFilters(base, f), pre);
  const keyed = applyKeyset(
    scoped as unknown as KeysetBuilder,
    s,
    cursor,
  ) as unknown as typeof scoped;
  const { data } = await pgRead<CardRow[] | null>(keyed.limit(CARD_PAGE_SIZE), { signal });
  const baseRows = data ?? [];
  const nextCursor = nextCursorFrom(baseRows as unknown as Record<string, unknown>[], s);
  /* W7a: the card photos USED to be awaited right here, before this function
   * would return a single row — so no card painted until every card's carousel
   * had landed. Measured live on 24 real ids, that await is 178 image rows, 178
   * correlated CLIP-tag lookups, 750 buffers and ~131 ms of server work, all of
   * it on the paint path. It is not wasteful work: the carousel renders those
   * rows, which is precisely why the fix is to move it off the paint path rather
   * than to shrink it to one cover. Photos now arrive through the shared
   * hydration layer (lib/hydration/useListingPhotos), keyed on the same
   * surrogate listing_id, non-blocking, in its own cache namespace — and this
   * read is one relation again. */
  return { rows: baseRows, nextCursor };
};

/* The feed's "Skryté" roll (#1766): the caller's dismissed properties ONLY,
 * most recently hidden first. Browse has no such lens — its switch
 * (`showDismissed`, readSource above) either hides them or shows them among the
 * rest — and the dismissed set cannot ride in a GET URL (it grows without
 * bound), so it is read a PAGE at a time: one page of dismissals, keyset-paged on
 * (dismissed_at, property_id), then those properties' card rows from the plain
 * browse_list — the reveal side of readSource, the same CARD_COLS. At most one
 * page of ids is ever in the URL. A dismissed property browse_list does not
 * serve (no served location, merged away) is simply absent from its page.
 * `nextCursor` follows the DISMISSAL page, so a page that lost rows still pages
 * on. */
export type DismissedCardRow = CardRow & { dismissed_at: string };

export interface DismissedCardsResult {
  rows: DismissedCardRow[];
  nextCursor: KeysetCursor | null;
}

const DISMISSED_SORT = { field: 'dismissed_at', direction: 'desc' } as unknown as SortSpec;

export const fetchDismissedCards = async (
  cursor: KeysetCursor | null,
  { signal }: PgReadOptions = {},
): Promise<DismissedCardsResult> => {
  const page = applyKeyset(
    supabase
      .from('property_dismissals_public')
      .select('property_id,dismissed_at') as unknown as KeysetBuilder,
    DISMISSED_SORT,
    cursor,
  ) as unknown as ReturnType<typeof selectFrom>;
  const { data: dismissals } = await pgRead<{ property_id: number; dismissed_at: string }[] | null>(
    page.limit(CARD_PAGE_SIZE),
    { signal },
  );
  const hidden = dismissals ?? [];
  if (hidden.length === 0) return { rows: [], nextCursor: null };
  const { data: cards } = await pgRead<CardRow[] | null>(
    selectFrom(BROWSE_LIST_RELATION, CARD_COLS).in(
      'property_id',
      hidden.map((d) => d.property_id),
    ),
    { signal },
  );
  const byId = new Map((cards ?? []).map((c) => [c.property_id, c]));
  const rows = hidden.flatMap((d) => {
    const card = byId.get(d.property_id);
    return card ? [{ ...card, dismissed_at: d.dismissed_at }] : [];
  });
  const nextCursor =
    hidden.length < CARD_PAGE_SIZE
      ? null
      : nextCursorFrom(hidden as unknown as Record<string, unknown>[], DISMISSED_SORT);
  return { rows, nextCursor };
};

/* The argument object `browse_stats_properties` takes.
 *
 * ONE builder, because there are TWO callers: lib/stats.ts fetchStatsPivot (plus
 * its own `spec`, migration 626) and fetchListingsForMap's `browse_map_cells` read
 * (migration 439), which takes the SAME named parameters plus its own `point_budget`. Built inline in each caller, this
 * object is the single richest opportunity for the Stats cohort and the map
 * cohort to silently disagree — a new filter wired into one literal and not the
 * other produces a map and a Stats tab describing different sets of properties,
 * with no error anywhere. Extracting it collapses that surface to zero.
 *
 * Pure: every value comes from `f` or from `pre`, the id allowlists the caller
 * has already resolved over the network (resolveBrowsePrefilters). Exported for
 * queries.test.ts. */
export const buildBrowseStatsArgs = (
  f: ListingFilters,
  pre: BrowsePrefilters,
): Record<string, unknown> => {
  const triToBool = (t: typeof f.hasBalcony): boolean | null =>
    t === 'any' ? null : t === 'yes';

  const buildingTypeArray = f.buildingMaterial.length
    ? [...buildingMaterialToValues(f.buildingMaterial)]
    : null;

  const effBbox = effectiveBbox(f);
  const scope = adScope(f);

  return {
    category_main_filter:    f.categoryMain.length ? f.categoryMain : null,
    category_type_filter:    f.categoryType,
    districts_filter:        f.districts.length ? f.districts.map((d) => d.name) : null,
    /* Parallel exclude flags (migration 146) — full-length array so the RPC's
     * unnest stays aligned with names; absent excluded => include. */
    districts_excluded_filter: f.districts.length
      ? f.districts.map((d) => d.excluded === true)
      : null,
    /* The raw level + RÚIAN code parallel to the names — the ONE code predicate
     * the two RPCs compile (latest: migration 605, lib/districtCodes);
     * `districts_filter` (the names) is now only the array that drives the
     * unnest. */
    districts_levels: f.districts.length ? f.districts.map((d) => d.level) : null,
    districts_ids: f.districts.length ? f.districts.map((d) => d.id) : null,
    dispositions_filter:     f.dispositions.length ? f.dispositions : null,
    price_min_filter:        f.priceMin,
    price_max_filter:        f.priceMax,
    include_no_price:        f.includeNoPrice,
    area_min_filter:         f.areaMin,
    area_max_filter:         f.areaMax,
    /* The four Size-group bounds + the parking count. The RPC parameters have
     * existed since migrations 133/439 and their predicates with them, but no
     * caller ever sent them — so an operator who set a plot or usable-area
     * bound got a narrowed LIST beside a Stats panel and a map describing the
     * whole cohort, with nothing on screen to say so (rule #16: one definition
     * of "matches"). `estate_area_*` narrows the PLOT MEASURE server-side
     * (migration 547), the same `plot_area_m2` column the list's registry
     * dispatcher reads, not the bare `estate_area` column. */
    estate_area_min_filter:  f.estateAreaMin,
    estate_area_max_filter:  f.estateAreaMax,
    usable_area_min_filter:  f.usableAreaMin,
    usable_area_max_filter:  f.usableAreaMax,
    garden_area_min_filter:  f.gardenAreaMin,
    garden_area_max_filter:  f.gardenAreaMax,
    parking_lots_min_filter: f.parkingLotsMin,
    /* The portal rule (MS19): the RPC judges status on the selected portals' ads
     * (portal_status_matches); under a broker the server already judged both. */
    active_only_filter:      scope.status === 'active',
    inactive_only_filter:    scope.status === 'inactive',
    last_seen_min_days:      f.lastSeenMinDays,
    last_seen_max_days:      f.lastSeenMaxDays,
    first_seen_min_days:     f.firstSeenMinDays,
    first_seen_max_days:     f.firstSeenMaxDays,
    /* Migration 159 — Status-section recency presets (first_seen_at /
     * last_change_at within N days). */
    recently_added_days:     f.recentlyAddedDays,
    recently_changed_days:   f.recentlyChangedDays,
    tom_days_min:            f.tomDaysMin,
    tom_days_max:            f.tomDaysMax,
    tom_observed_only:       f.tomObservedOnly,
    has_balcony_filter:      triToBool(f.hasBalcony),
    has_lift_filter:         triToBool(f.hasLift),
    has_parking_filter:      triToBool(f.hasParking),
    furnished_filter:        f.furnished.length ? f.furnished : null,
    energy_rating_filter:    f.energyRatingMatch.length ? f.energyRatingMatch : null,
    ownership_filter:        f.ownership.length ? f.ownership : null,
    terrace_filter:          triToBool(f.terrace),
    cellar_filter:           triToBool(f.cellar),
    garage_filter:           triToBool(f.garage),
    category_sub_cb_filter:  f.categorySubCb,
    subtype_filter:          f.subtype.length ? f.subtype : null,
    building_type_filter:    buildingTypeArray,
    condition_match_filter:  f.conditionMatch.length ? f.conditionMatch : null,
    bbox_west:               effBbox?.west  ?? null,
    bbox_south:              effBbox?.south ?? null,
    bbox_east:               effBbox?.east  ?? null,
    bbox_north:              effBbox?.north ?? null,
    city_pop_min:            f.minCityPopulation,
    city_pop_max:            f.maxCityPopulation,
    /* Migration 142/143 — fast polygon-edge proximity precomputed columns. */
    near_pop_5km_min:        f.nearPop5kmMin,
    near_pop_15km_min:       f.nearPop15kmMin,
    near_jobs_5km_min:       f.nearJobs5kmMin,
    near_jobs_15km_min:      f.nearJobs15kmMin,
    near_youth_5km_min:      f.nearYouth5kmMin,
    near_youth_15km_min:     f.nearYouth15kmMin,
    near_overall_5km_min:    f.nearOverall5kmMin,
    near_overall_15km_min:   f.nearOverall15kmMin,
    /* Migration 083 — price-per-m² bounds. NULL area_m2 listings fall
     * out when either bound is set. */
    price_per_m2_min:        f.pricePerM2Min,
    price_per_m2_max:        f.pricePerM2Max,
    /* Migration 133 — MF gross rental yield % bounds (sale apartments). */
    mf_gross_yield_pct_min:  f.mfGrossYieldPctMin,
    mf_gross_yield_pct_max:  f.mfGrossYieldPctMax,
    /* Migration 173 — merged price-history predicates. Property grain;
     * columns maintained by the recompute job. */
    price_change_count_min:        f.priceChangeCountMin,
    price_change_window_days:      f.priceChangeWindowDays,
    total_price_change_pct_filter: f.totalPriceChangePct,
    /* Migration 537 — the caller's dismissed properties, excluded server-side. */
    hide_dismissed:                !f.showDismissed,
    portal_filter:           scope.portals.length ? scope.portals : null,
    /* Migration 597 — the property-level side: under a broker too. */
    portal_excluded_filter:      f.portalsExcluded.length ? f.portalsExcluded : null,
    portal_excluded_active_only: f.portalsExcludedActiveOnly,
    /* Migration 162 — market-growth obec allowlist (price-stats datasets).
     * Migration 378 — generic property-id allowlist (with-estimates ∩ broker).
     * Both are resolved over the network by the caller (`pre`). */
    obec_ids_filter:         pre.obecIds,
    property_ids_filter:     pre.propertyIds,
    /* Migration 629 — the curated-set scope, judged server-side; only the
     * active sides are sent, as on the list read (readSource). */
    ...scopeArgs(f),
  };
};

/* The listings_public select list: every column the view carries (the width is
 * pinned to its readers by tests/test_location_w6c_narrow_public_views.py). */
const DETAIL_COLS =
  'id,sreality_id,first_seen_at,last_seen_at,is_active,source,source_id_native,property_id,tom_days,' +
  'category_main,category_type,price_czk,price_unit,' +
  'area_m2,disposition,subtype,display_label,' +
  'lat,lng,floor,total_floors,has_balcony,has_parking,has_lift,' +
  'building_type,condition,energy_rating,' +
  'estate_area,usable_area,garden_area,category_sub_cb,' +
  'furnished,terrace,cellar,garage,parking_lots,ownership,' +
  /* The measure AND its published label (migration 425): detail surfaces read
   * the published label rather than re-deriving it. (No apostrophes in here: a
   * guardrail test parses these constants by quote pairing.) */
  'price_per_m2,price_per_m2_basis,' +
  'description,source_url';

/* THE PROPERTY as its Browse card shows it (decision 11): the `properties_public`
 * row, whose every advert field is its canonical advert's and every physical fact
 * the first non-empty in the same order (migration 561). Selected in the
 * ListingPublic shape the detail components render, `id` being the canonical
 * advert (`listing_id`); the price-change pair is the canonical advert's own
 * series, as Browse filters on it. The row carries no portal URL: each advert's
 * is on its row of the merged-adverts section. (No apostrophes: see above.) */
const PROPERTY_COLS =
  'id:listing_id,property_id,sreality_id,first_seen_at,last_seen_at,is_active,source,source_id_native,tom_days,' +
  'category_main,category_type,price_czk,price_unit,' +
  'area_m2,disposition,subtype,display_label,' +
  'lat,lng,floor,total_floors,has_balcony,has_parking,has_lift,' +
  'building_type,condition,energy_rating,' +
  'estate_area,usable_area,garden_area,category_sub_cb,' +
  'furnished,terrace,cellar,garage,parking_lots,ownership,' +
  'price_per_m2,price_per_m2_basis,' +
  /* MF is property-grain: the page renders `mf_reference_rent` by shape
   * (lib/mfReference) and pairs a value with the property yield. */
  'description,mf_gross_yield_pct,mf_reference_rent,' +
  'price_change_count,total_price_change_pct';

export interface PropertyPublic extends ListingPublic {
  property_id: number;
  mf_gross_yield_pct: number | null;
  mf_reference_rent: ReferenceRent | null;
  price_change_count: number | null;
  total_price_change_pct: number | null;
}

/* An active property by id; null for a merged-away or unknown id. */
export const fetchProperty = async (
  propertyId: number,
  { signal }: PgReadOptions = {},
): Promise<PropertyPublic | null> => {
  const { data: row } = await pgRead<PropertyPublic | null>(
    supabase
      .from('properties_public')
      .select(PROPERTY_COLS)
      .eq('property_id', propertyId)
      .maybeSingle(),
    { signal },
  );
  if (!row) return null;
  return {
    ...row,
    source_url: null,
    // numeric arrives as a string from PostgREST.
    total_price_change_pct:
      row.total_price_change_pct == null ? null : Number(row.total_price_change_pct),
  };
};

/* An advert address (the natural key, or the legacy sreality id) → the advert's
 * surrogate id and its property, which is all the alias routes need to land on
 * the property page. listings_public is unfiltered, so a just-scraped advert
 * resolves too; (source, source_id_native) is unique (migration 091). */
export const fetchAdvertProperty = async (
  key: { source: string; nativeId: string } | { srealityId: number },
  { signal }: PgReadOptions = {},
): Promise<{ id: number; property_id: number | null } | null> => {
  let q = supabase.from('listings_public').select('id,property_id');
  q = 'srealityId' in key
    ? q.eq('sreality_id', key.srealityId)
    : q.eq('source', key.source).eq('source_id_native', key.nativeId);
  const { data } = await pgRead<{ id: number; property_id: number | null } | null>(
    q.maybeSingle(),
    { signal },
  );
  return data ?? null;
};

const PROPERTY_SOURCE_COLS =
  'id,property_id,sreality_id,source,source_url,source_id_native,is_active,price_czk,first_seen_at,last_seen_at';

/* Every advert of one property, oldest first — the merged-adverts section's rows. */
export const fetchPropertySources = async (
  propertyId: number,
  { signal }: PgReadOptions = {},
): Promise<PropertySource[]> => {
  const { data } = await pgRead<PropertySource[] | null>(
    supabase
      .from('property_sources_public')
      .select(PROPERTY_SOURCE_COLS)
      .eq('property_id', propertyId)
      .order('first_seen_at', { ascending: true }),
    { signal },
  );
  return data ?? [];
};

/* Price snapshots of the given adverts: the property page reads every advert's,
 * one chart line each (MS9). Keyed on the surrogate `listing_id`
 * (listing_snapshots_public.listing_id, migration 343), NOT sreality_id — a
 * post-Gate-2 non-sreality advert has a NULL sreality_id, and the chart would
 * silently go empty. Exhaustive, so a long history never loses its newest rows. */
export const fetchSnapshotsForListings = async (
  ids: number[],
  { signal }: PgReadOptions = {},
): Promise<ListingSnapshotPublic[]> => {
  if (ids.length === 0) return [];
  return fetchAllRows<ListingSnapshotPublic>({
    relation: 'listing_snapshots_public',
    build: () =>
      supabase
        .from('listing_snapshots_public')
        .select('id,listing_id,scraped_at,price_czk', { count: 'exact' })
        .in('listing_id', ids),
    orderBy: [{ column: 'scraped_at' }, { column: 'id' }],
    key: ['id'],
    expectMax: 50_000,
    signal,
  });
};

export const fetchFreshnessChecksByListing = async (
  sreality_id: number,
  { signal }: PgReadOptions = {},
): Promise<ListingFreshnessCheckPublic[]> => {
  const { data } = await pgRead<ListingFreshnessCheckPublic[] | null>(
    supabase
      .from('listing_freshness_checks_public')
      .select('id,sreality_id,checked_at,outcome')
      .eq('sreality_id', sreality_id)
      .order('checked_at', { ascending: true }),
    { signal },
  );
  return data ?? [];
};

/* Batch fetch of the listings_public rows behind a set of comparables.
 * Pulls the same field set as the detail page so the Estimate page's
 * comparable modal can render rich info without an extra round-trip
 * per listing. Returns a map keyed on sreality_id for O(1) lookup in
 * the renderer. */
export const fetchListingsByIds = async (
  ids: ReadonlyArray<number>,
  { signal }: PgReadOptions = {},
): Promise<Map<number, ListingPublic>> => {
  if (ids.length === 0) return new Map();
  const { data } = await pgRead<ListingPublic[] | null>(
    supabase.from('listings_public').select(DETAIL_COLS).in('sreality_id', ids as number[]),
    { signal },
  );
  const out = new Map<number, ListingPublic>();
  for (const row of data ?? []) {
    // Non-null by construction: the select above filters on sreality_id, so a
    // NULL-sreality row cannot be in `data`.
    out.set(row.sreality_id!, row);
  }
  return out;
};

/* Sibling of fetchListingsByIds keyed on the surrogate `id` (listings_public.id).
 * The estimation comparables track (RunPanel's ComparablesSection) carries
 * `listing_id` on every comparable emitted after R2 (#879/#892) — this is what
 * a comparable with a NULL sreality_id (post-Gate-2-flip non-sreality listing)
 * must be fetched through instead, mirroring fetchImagesForListingIds. */
export const fetchListingsForListingIds = async (
  ids: ReadonlyArray<number>,
  { signal }: PgReadOptions = {},
): Promise<Map<number, ListingPublic>> => {
  if (ids.length === 0) return new Map();
  const { data } = await pgRead<ListingPublic[] | null>(
    supabase.from('listings_public').select(DETAIL_COLS).in('id', ids as number[]),
    { signal },
  );
  const out = new Map<number, ListingPublic>();
  for (const row of data ?? []) {
    out.set(row.id, row);
  }
  return out;
};

/* A card's storey, for the Browse card's title. browse_list carries no floor
 * column (and the card read model is not widened for one attribute), so the
 * visible cards' canonical ads are read here, keyed on listings_public.id, three
 * columns wide. The scale is listings.floor's: ground = 0, below ground < 0. */
export type ListingFloor = Pick<ListingPublic, 'floor' | 'total_floors'>;

export const fetchListingFloors = async (
  ids: ReadonlyArray<number>,
  { signal }: PgReadOptions = {},
): Promise<Map<number, ListingFloor>> => {
  if (ids.length === 0) return new Map();
  const { data } = await pgRead<Array<ListingFloor & { id: number }> | null>(
    supabase.from('listings_public').select('id,floor,total_floors').in('id', ids as number[]),
    { signal },
  );
  const out = new Map<number, ListingFloor>();
  for (const row of data ?? []) {
    out.set(row.id, { floor: row.floor, total_floors: row.total_floors });
  }
  return out;
};

/* Batch image fetch for the comparables modal — first three per id is
 * enough for the modal's thumbnail strip; the Listing Detail page
 * still pulls the full set independently. */
/* Columns every image fetch pulls from images_public — incl. the CLIP tag
 * (clip_fine_tag / clip_logical_tag / clip_confidence, migration 236) so every
 * photo surface can render its bottom-left tag badge from the same read. */
const IMAGE_PUBLIC_COLS =
  'id,sreality_id,sequence,sreality_url,storage_path,clip_fine_tag,clip_logical_tag,clip_confidence,clip_render_score,phash';

/* Batch image fetch keyed on `sreality_id` — kept for the callers whose upstream
 * read model is still sreality-keyed and carries no surrogate id. These
 * CANNOT cut to listing_id without a backend change to those payloads, and
 * flipping this loader in place would be a catastrophic half-swap (a
 * sreality_id fed into an `IN listing_id` matches a DIFFERENT listing,
 * id-spaces overlap). The Browse card path uses fetchImagesForListingIds
 * below instead — as does RunPanel's ComparablesSection now, for the subset
 * of comparables (ComparableUsed) that carry a `listing_id`; this loader
 * only covers the sreality_id-only fallback for pre-#879 frozen runs
 * (see RunPanel.fetchComparableImages). */
export const fetchImagesByListingIds = async (
  ids: ReadonlyArray<number>,
  perId = 3,
  { signal }: PgReadOptions = {},
): Promise<Map<number, ImagePublic[]>> => {
  if (ids.length === 0) return new Map();
  const { data } = await pgRead<ImagePublic[] | null>(
    supabase
      .from('images_public')
      .select(IMAGE_PUBLIC_COLS)
      .in('sreality_id', ids as number[])
      .order('sequence', { ascending: true, nullsFirst: false })
      .order('id', { ascending: true }),
    { signal },
  );
  const out = new Map<number, ImagePublic[]>();
  for (const row of data ?? []) {
    const arr = out.get(row.sreality_id);
    if (arr) {
      if (arr.length < perId) arr.push(row);
    } else {
      out.set(row.sreality_id, [row]);
    }
  }
  return out;
};

/* Sibling of fetchImagesByListingIds keyed on the surrogate `listing_id`
 * (images_public.listing_id, migration 335). The Browse card hydration uses this
 * so a post-Gate-2 non-sreality card (sreality_id NULL) still gets its photos.
 * `listing_id` is appended to the select just for keying the result map, and
 * `tag_head_scores` for the card's cover choice — only this read asks for it, so
 * every other images_public reader skips that join (migration 591). */
export const fetchImagesForListingIds = async (
  ids: ReadonlyArray<number>,
  perId = 3,
  { signal }: PgReadOptions = {},
): Promise<Map<number, ImagePublic[]>> => {
  if (ids.length === 0) return new Map();
  const { data } = await pgRead<Array<ImagePublic & { listing_id: number }> | null>(
    supabase
      .from('images_public')
      .select(`${IMAGE_PUBLIC_COLS},listing_id,tag_head_scores`)
      .in('listing_id', ids as number[])
      .order('sequence', { ascending: true, nullsFirst: false })
      .order('id', { ascending: true }),
    { signal },
  );
  const out = new Map<number, ImagePublic[]>();
  for (const row of data ?? []) {
    const arr = out.get(row.listing_id);
    if (arr) {
      if (arr.length < perId) arr.push(row);
    } else {
      out.set(row.listing_id, [row]);
    }
  }
  return out;
};

/* W4: the ONE-cover-per-listing read (the card grid's 48px thumbnail),
 * against listing_cover_public — a server-side DISTINCT ON, not
 * fetchImagesForListingIds(ids, 1)'s fetch-every-image-then-discard.
 * Measured live: 44 real listing ids went from 901 image rows / 901
 * correlated CLIP-tag lookups / 3,995 buffers to 44 rows / 44 lookups / 788
 * buffers — the row count now equals what the card grid actually renders. */
/* NOTE for the W7a loader collapse: `listing_cover_public` is fast only when
 * the predicate is on `listing_id`. Its DISTINCT ON key is listing_id, but it
 * also projects `id` and `sreality_id` — a qual on either of those is applied
 * ABOVE the Unique node, so the full 10.4M-row images scan runs first and the
 * request dies on the 8s statement_timeout. Repointing a sreality-keyed loader
 * at this view is therefore not a drop-in. */
export const fetchListingCovers = async (
  ids: ReadonlyArray<number>,
  { signal }: PgReadOptions = {},
): Promise<Map<number, ImagePublic>> => {
  if (ids.length === 0) return new Map();
  const { data } = await pgRead<Array<ImagePublic & { listing_id: number }> | null>(
    supabase
      .from('listing_cover_public')
      .select(`${IMAGE_PUBLIC_COLS},listing_id`)
      .in('listing_id', ids as number[]),
    { signal },
  );
  const out = new Map<number, ImagePublic>();
  for (const row of data ?? []) {
    out.set(row.listing_id, row);
  }
  return out;
};

/* Each board card's ads, for its broker line (MS7): one batched read over the
 * cards' properties, keyed on property_id, each property's ads oldest first —
 * the order the property page lists them in. */
export const fetchPropertySourcesByPropertyIds = async (
  ids: ReadonlyArray<number>,
  { signal }: PgReadOptions = {},
): Promise<Map<number, PropertySource[]>> => {
  if (ids.length === 0) return new Map();
  const rows = await fetchAllRows<PropertySource>({
    relation: 'property_sources_public',
    build: () =>
      supabase
        .from('property_sources_public')
        .select(PROPERTY_SOURCE_COLS, { count: 'exact' })
        .in('property_id', ids as number[]),
    orderBy: [{ column: 'property_id' }, { column: 'first_seen_at' }, { column: 'id' }],
    key: ['id'],
    expectMax: 50_000,
    signal,
  });
  const out = new Map<number, PropertySource[]>();
  for (const row of rows) {
    const arr = out.get(row.property_id);
    if (arr) arr.push(row);
    else out.set(row.property_id, [row]);
  }
  return out;
};

/* The "Border case" button's flagged/unflagged state, batched per page-group.
 * image_id-keyed with no other payload — a border case carries no value of its
 * own; it is the whole-image quarantine flag, independent of any tag's
 * tri-state in the annotation matrix. */
export const fetchBorderCasesByImageIds = async (
  ids: ReadonlyArray<number>,
  { signal }: PgReadOptions = {},
): Promise<Set<number>> => {
  if (ids.length === 0) return new Set();
  const { data } = await pgRead<BorderCase[] | null>(
    supabase.from('image_border_cases_public').select('image_id').in('image_id', ids as number[]),
    { signal },
  );
  return new Set((data ?? []).map((r) => r.image_id));
};

/* The images behind a set of annotations. Sibling of fetchImagesByListingIds —
 * same view and columns, keyed on the image's own id instead of its listing's. */
export const fetchImagesByImageIds = async (
  ids: ReadonlyArray<number>,
  { signal }: PgReadOptions = {},
): Promise<Map<number, ImagePublic>> => {
  if (ids.length === 0) return new Map();
  const { data } = await pgRead<ImagePublic[] | null>(
    supabase.from('images_public').select(IMAGE_PUBLIC_COLS).in('id', ids as number[]),
    { signal },
  );
  const out = new Map<number, ImagePublic>();
  for (const row of data ?? []) out.set(row.id, row);
  return out;
};

/* Keyed on listing_id, not sreality_id (R2 Phase C resolver-chain cutover;
 * migration 335 exposes it on images_public).
 *
 * W7a: this is now a one-id call into fetchImagesForListingIds rather than a
 * fourth hand-rolled copy of the same select + double order-by. The two differed
 * only in `.eq` vs `.in` and in the uncapped result — so `perId: Infinity`,
 * which the retention loop reads as "keep them all". The listing gallery must
 * stay uncapped: it renders every photo, and a cap here would silently truncate
 * the lightbox. */
export const fetchImagesByListing = async (
  listing_id: number,
  { signal }: PgReadOptions = {},
): Promise<ImagePublic[]> => {
  const byListing = await fetchImagesForListingIds([listing_id], Infinity, { signal });
  return byListing.get(listing_id) ?? [];
};

/* -------------------------------------------------------------------------- */
/* Registered sales — migration 545's sold_comparables / sold_coverage.       */
/* `sold_comparables` is an inlinable SQL function, so the predicates, ORDER BY */
/* and LIMIT below reach the GiST index exactly as they would against a table:  */
/* the narrowing happens in Postgres, never in the browser.                     */
/* -------------------------------------------------------------------------- */

/* What the block RENDERS, and nothing else. The function returns more — the
 * RÚIAN codes, the sale's own point, `sold_age_days`, the secondary areas — but
 * those are there to be FILTERED on, and a PostgREST predicate does not need
 * its column selected. Carrying them would be dead weight on up to 200 rows per
 * listing open, and a standing invitation to assume the block plots them. */
const SOLD_COMP_COLS =
  'source,source_record_id,sold_at,price_czk,asking_last_czk,listed_at,' +
  'category_main,category_type,subtype,disposition,area_m2,area_basis,' +
  'address_text,photo_urls,source_url,fetched_at,' +
  /* The function's own derivation, plus migration 425's measure AND its
   * published basis label — read, never re-derived (lib/measure's north star). */
  'distance_m,price_per_m2,price_per_m2_basis';

/* A radius over a dense town can hold thousands of sales, so the read is
 * capped — and the cap cuts the FAR end: nearest first, so a truncated cohort
 * is still the neighbourhood rather than a scatter across the whole circle.
 * `source_record_id` is the final tiebreak because two flats in one building
 * share a point exactly (the source geocodes the building), and an order with
 * ties reshuffles between reads.
 *
 * The fetch asks for ONE row more than the cap: a full page is not the same
 * fact as a cohort that happens to hold exactly 200 sales, and the block says
 * "200+" only when the extra row proves there is more. */
export const SOLD_COMPS_LIMIT = 200;

export const fetchSoldComparables = async (
  lat: number,
  lng: number,
  radiusM: number,
  filters: Record<string, unknown>,
  { signal }: PgReadOptions = {},
): Promise<SoldComparable[]> => {
  const q = supabase
    .rpc('sold_comparables', { p_lat: lat, p_lng: lng, p_radius_m: radiusM }, { get: true })
    .select(SOLD_COMP_COLS)
    .order('distance_m', { ascending: true })
    .order('sold_at', { ascending: false })
    .order('source_record_id', { ascending: true })
    .limit(SOLD_COMPS_LIMIT + 1);
  const { data } = await pgRead<SoldComparable[] | null>(
    applyAgendaFilters(q, 'sold', (id) => filters[id]),
    { signal },
  );
  return data ?? [];
};

export const fetchSoldCoverage = async (
  lat: number,
  lng: number,
  { signal }: PgReadOptions = {},
): Promise<SoldCoverage | null> => {
  const { data } = await pgRead<SoldCoverage[] | null>(
    supabase
      .rpc('sold_coverage', { p_lat: lat, p_lng: lng }, { get: true })
      .select(
        'obec_kod,obec_name,fetched_at,record_count,source_total,truncated,' +
          'last_attempt_at,last_attempt_status',
      ),
    { signal },
  );
  /* No row means the point is in no municipality we hold a boundary for; a row
   * with a null `fetched_at` means nobody has ever successfully looked there.
   * Both differ from `record_count: 0`, and the block says which. */
  return (data ?? [])[0] ?? null;
};

/* -------------------------------------------------------------------------- */
/* Phase QUAL — curated cities (operator-curated qualitative indexes +        */
/* population). Browse map renders matching cities as a separate pin layer;   */
/* the filter UI picks rules + an optional color-coding index.                */
/* -------------------------------------------------------------------------- */

export interface CuratedCity {
  city_id: number;
  name: string;
  kraj_name: string;
  lat: number;
  lng: number;
  default_radius_m: number;
  population: number | null;
  population_as_of_year: number | null;
  /* The RÚIAN obec this curated city was matched to (migration 081), or null
   * when the name match failed. The view has always exposed it and
   * fetchCuratedCities has always `select('*')`-ed it — it was simply absent
   * from this type. It is the join key that lets a property carry its city's
   * indexes without a PostGIS round-trip: `properties.obec_id =
   * curated_cities.admin_boundary_id`. All 206 curated cities have one, so the
   * centroid+radius fallback arm in the SQL predicates is currently dead. */
  admin_boundary_id: number | null;
}

export interface CityIndexDefinition {
  index_name: string;
  label_cs: string;
  label_en: string | null;
  category: 'overall' | 'health_env' | 'material_edu' | 'services_relations' | 'sub_index';
  scale_min: number;
  scale_max: number;
  higher_is_better: boolean;
  sort_order: number;
  description: string | null;
}

export interface CityIndexValue {
  city_id: number;
  index_name: string;
  value: number;
}

/* Query keys for the three operator-static city-quality datasets. They were
 * inline string literals in BrowseExperience; anything else wanting the same
 * cached rows (the pipeline board's index strip) has to spell them identically
 * or it silently refetches into a parallel cache entry. */
export const cityQualityKeys = {
  cities: ['curated_cities'] as const,
  definitions: ['city_index_definitions'] as const,
  values: ['city_index_values'] as const,
};

export const fetchCuratedCities = async (
  { signal }: PgReadOptions = {},
): Promise<CuratedCity[]> => {
  /* 205 rows today; operator uploads grow the set. Two obce can share a name
   * (see the same-name-obce price-stats fix), hence the city_id tiebreak. */
  return await fetchAllRows<CuratedCity>({
    relation: 'curated_cities_public',
    build: () => supabase.from('curated_cities_public').select('*', { count: 'exact' }),
    orderBy: [{ column: 'name' }, { column: 'city_id' }],
    key: ['city_id'],
    expectMax: 25_000,
    signal,
  });
};

export const fetchCityIndexDefinitions = async (
  { signal }: PgReadOptions = {},
): Promise<CityIndexDefinition[]> => {
  /* ~33 rows today, operator additions grow it. Display order is sort_order,
   * applied by the consumers — the fetch orders by the unique name for paging. */
  return await fetchAllRows<CityIndexDefinition>({
    relation: 'city_index_definitions_public',
    build: () =>
      supabase.from('city_index_definitions_public').select('*', { count: 'exact' }),
    orderBy: [{ column: 'index_name' }],
    key: ['index_name'],
    expectMax: 25_000,
    signal,
  });
};

export const fetchCityIndexValues = async (
  { signal }: PgReadOptions = {},
): Promise<CityIndexValue[]> => {
  /* 205 cities × 33 indexes = 6,798 rows. This read shipped THE truncation bug
   * fetchAllRows exists to kill (only the first ~32 cities came back under the
   * then-1,000-row db-max-rows; Dobříš showed em-dashes for every index).
   * Cached (staleTime: Infinity), so the handful of pages is first-load only. */
  return await fetchAllRows<CityIndexValue>({
    relation: 'city_index_values_public',
    build: () =>
      supabase
        .from('city_index_values_public')
        .select('city_id,index_name,value', { count: 'exact' }),
    orderBy: [{ column: 'city_id' }, { column: 'index_name' }],
    key: ['city_id', 'index_name'],
    expectMax: 100_000,
    signal,
  });
};

export interface CityPolygon {
  city_id: number;
  geojson: string;
}

export const fetchCuratedCityPolygons = async (
  { signal }: PgReadOptions = {},
): Promise<CityPolygon[]> => {
  /* One simplified municipality boundary per curated city (205 rows).
   * `geojson` is the raw ST_AsGeoJSON string the map JSON.parses into a
   * Feature geometry. Fetched once and cached (staleTime: Infinity), and only
   * when the map tab is active. */
  return await fetchAllRows<CityPolygon>({
    relation: 'curated_city_polygons_public',
    build: () =>
      supabase
        .from('curated_city_polygons_public')
        .select('city_id,geojson', { count: 'exact' }),
    orderBy: [{ column: 'city_id' }],
    key: ['city_id'],
    expectMax: 25_000,
    signal,
  });
};

/* -------------------------------------------------------------------------- */
/* Estimations (U2). Hits the Railway FastAPI service via lib/api.ts; pages   */
/* combine these helpers with useQuery / useMutation directly, matching the   */
/* convention used by Supabase fetchers above.                                */
/* -------------------------------------------------------------------------- */

import { useQuery, type UseQueryResult } from '@tanstack/react-query';
import {
  ApiError,
  createEstimation,
  getEstimation,
  getTracePayload,
  listEstimations,
  type TracePayload,
} from './api';
import { sortedIds } from './hydration/keys';
import type {
  CreateEstimationIn,
  EstimationListParams,
  EstimationListResponse,
  PipelineBoardCard,
  PipelineStage,
  TagColor,
} from './types';

export const estimationKeys = {
  all: ['estimations'] as const,
  list: (params: EstimationListParams) =>
    ['estimations', 'list', params] as const,
  byListing: (ids: ReadonlyArray<number>) =>
    ['estimations', 'by-listing', ids] as const,
  detail: (id: number) =>
    ['estimations', 'detail', id] as const,
  tracePayload: (runId: number, stepN: number) =>
    ['estimations', 'detail', runId, 'trace', stepN, 'payload'] as const,
};

export const fetchEstimation = (id: number) => getEstimation(id);

export const useTracePayload = (
  runId: number,
  stepN: number,
  enabled: boolean,
): UseQueryResult<TracePayload, ApiError> =>
  useQuery<TracePayload, ApiError>({
    queryKey: estimationKeys.tracePayload(runId, stepN),
    queryFn: () => getTracePayload(runId, stepN),
    enabled,
    staleTime: Infinity,
  });
export const fetchEstimationsList = (params: EstimationListParams) =>
  listEstimations(params);

/* Property-grain run fetch for the Listing Detail estimations section:
 * every run on any of the property's child listings, newest first. List
 * rows carry the full run projection, so the section
 * renders the selected run without a second per-run request. */
export const fetchEstimationsForListings = (ids: ReadonlyArray<number>) =>
  listEstimations({ listing_ids: ids.join(','), limit: 100 });
/* That fetch as ONE query, for the estimations block and the Valuo card's
 * "náš odhad" mark alike: polled while a run is pending or running. A
 * non-empty array of junk ids never reaches the wire, where it would
 * serialize to an empty value rather than a filter. */
export const useEstimationRuns = (listingIds: readonly number[]) => {
  const ids = sortedIds(listingIds);
  return useQuery<EstimationListResponse, Error>({
    queryKey: estimationKeys.byListing(ids),
    queryFn: () => fetchEstimationsForListings(ids),
    enabled: ids.length > 0 && ids.every((n) => Number.isFinite(n)),
    staleTime: 15_000,
    refetchInterval: (q) =>
      (q.state.data?.data ?? []).some((r) => r.status === 'pending' || r.status === 'running') ? 3000 : false,
  });
};
export const submitEstimation = (input: CreateEstimationIn) =>
  createEstimation(input);

/* -------------------------------------------------------------------------- */
/* Curation (U2.6) — read paths.                                              */
/*                                                                            */
/* The "list collections / tags / notes" indices go through the bearer-gated  */
/* FastAPI service (lib/api.ts) so listing_count + ordering live in one      */
/* place. The reverse-index reads below — "which tags / collections does      */
/* property X belong to" — read the property-grain *_public views directly,  */
/* whole, matching the read-only pattern Browse / Region use. The Browse     */
/* cohort judges the same rows server-side (migration 629, lib/setScope); the */
/* maps here feed the glyphs, the pipeline board and in-place settling.       */
/* -------------------------------------------------------------------------- */

/* All (property_id → member ids) rows of one membership view in ONE read,
 * shared (React Query dedupes the key) by every card control, glyph and cohort
 * filter that asks — the curation analogue of fetchPipelineMembers, so Browse
 * fires one query instead of one-per-card.
 *
 * It is also the ONLY membership read of its view: a per-property key existed
 * beside the collection map until W1, and beside the tag map until the tags
 * sprint, which meant a writer had two keys to remember and every writer but
 * one forgot one (the same collapse pipelineKeys records below). One
 * property's ids are `members.get(property_id) ?? []`. fetchAllRows REJECTS
 * past expectMax rather than resolving a truncated map, so an outgrown map can
 * never read as a smaller one. A REJECTED read is never "no membership": it
 * says so and offers a retry (MS16); the board's curation filters alone also
 * fail open (rule #22). */
const fetchMemberSet = async <K extends string>(
  relation: 'collection_properties_public' | 'property_tags_public',
  member: K,
  { signal, actAs }: PgReadOptions,
): Promise<Map<number, number[]>> => {
  const rows = await fetchAllRows<{ property_id: number } & Record<K, number>>({
    relation,
    build: () =>
      supabase.from(relation).select(`property_id, ${member}`, { count: 'exact' }),
    orderBy: [{ column: 'property_id' }, { column: member }],
    key: ['property_id', member],
    expectMax: 100_000,
    signal,
    actAs,
  });
  const map = new Map<number, number[]>();
  for (const r of rows) {
    const arr = map.get(r.property_id);
    if (arr) arr.push(r[member]);
    else map.set(r.property_id, [r[member]]);
  }
  return map;
};

export const fetchPropertyCollectionMemberSet = (
  opts: PgReadOptions = {},
): Promise<Map<number, number[]>> =>
  fetchMemberSet('collection_properties_public', 'collection_id', opts);

export const fetchPropertyTagMemberSet = (
  opts: PgReadOptions = {},
): Promise<Map<number, number[]>> =>
  fetchMemberSet('property_tags_public', 'tag_id', opts);

/* How many notes the caller can READ on each property (MS16): the note mark on
 * every Browse row, from ONE whole-set read rather than one per row.
 * property_notes_public is security_invoker over RLS (migrations 290, 393, 610),
 * so the set is the caller's own notes plus other authors' notes marked shared
 * and in reach through a collection share. */
export const fetchNoteCounts = async (
  { signal }: PgReadOptions = {},
): Promise<Map<number, number>> => {
  const rows = await fetchAllRows<{ id: number; property_id: number }>({
    relation: 'property_notes_public',
    build: () =>
      supabase.from('property_notes_public').select('id, property_id', { count: 'exact' }),
    orderBy: [{ column: 'id' }],
    key: ['id'],
    expectMax: 100_000,
    signal,
  });
  const counts = new Map<number, number>();
  for (const r of rows) counts.set(r.property_id, (counts.get(r.property_id) ?? 0) + 1);
  return counts;
};

export const watchdogKeys = {
  all: ['watchdog'] as const,
  subscriptions: ['watchdog', 'subscriptions'] as const,
  subscription: (id: string) => ['watchdog', 'subscriptions', id] as const,
  dispatches: (params: Record<string, unknown>) =>
    ['watchdog', 'dispatches', params] as const,
};

/* Unified notifications feed (watchdog matches + collection-monitor events). */
export const notificationKeys = {
  all: ['notifications'] as const,
  feed: (params: Record<string, unknown>) =>
    ['notifications', 'feed', params] as const,
  unreadCount: ['notifications', 'unread-count'] as const,
};

export const filterPresetKeys = {
  all: ['filter-presets'] as const,
};

export const curationKeys = {
  collections: ['curation', 'collections'] as const,
  collection: (id: number) => ['curation', 'collection', id] as const,
  tags: ['curation', 'tags'] as const,
  propertyTagMembers: ['curation', 'property-tag-members'] as const,
  propertyCollectionMembers: ['curation', 'property-collection-members'] as const,
  propertyNotes: (property_id: number) =>
    ['curation', 'property-notes', property_id] as const,
  /* Prefix of every property's notes: a share change moves which notes I can read. */
  allPropertyNotes: ['curation', 'property-notes'] as const,
  artifactLinks: ['curation', 'artifact-links'] as const,
  noteCounts: ['curation', 'note-counts'] as const,
};

/* Deal pipeline (migration 205). The "is this property bookmarked + at which
 * stage" read pulls from property_pipeline_public, RLS-scoped as authenticated;
 * writes go through the FastAPI service. At most one card per property per account.
 *
 * W3 collapsed the per-property `card(id)` key into `members` — a single
 * property's card is just `members.get(id)`. The two used to be separate
 * reads (fetchPropertyPipeline + fetchPipelineMembers) that once drifted
 * out of sync on which columns they selected (a property badged "9" on a
 * card and "5" in its own header) — one shared query now backs every
 * surface that needs a property's pipeline state, so that class of bug
 * can't recur. */
export const pipelineKeys = {
  board: ['pipeline', 'board'] as const,
  stages: ['pipeline', 'stages'] as const,
  members: ['pipeline', 'members'] as const,
};

/* Both of the caller's pipeline-share lists (GET /pipeline/shares). Never
 * acting-prefixed: sharing is always the caller's own (lib/acting). */
export const pipelineShareKeys = {
  all: ['pipeline-shares'] as const,
};

export const dismissalKeys = {
  all: ['dismissals'] as const,
  count: ['dismissals', 'count'] as const,
  state: (property_id: number) => ['dismissals', 'state', property_id] as const,
};

/* Is this property dismissed by the caller? One entry per property, but never
 * one request per property: every call made in the same task (a page of cards
 * mounting, say) is answered by ONE read of the view per DISMISSAL_BATCH ids.
 * Never a whole-set read — the dismissed set only grows. A batch is per acting
 * identity (lib/acting): the owner's dismissals and the caller's never share a
 * read. '' keys the caller's own batch. */
const DISMISSAL_BATCH = 200;
type DismissalWaiter = { resolve: (dismissed: boolean) => void; reject: (e: unknown) => void };
const dismissalWaiters = new Map<string, Map<number, DismissalWaiter[]>>();

async function flushDismissalBatch(as: string): Promise<void> {
  const batch = dismissalWaiters.get(as) ?? new Map<number, DismissalWaiter[]>();
  dismissalWaiters.delete(as);
  const ids = [...batch.keys()];
  for (let i = 0; i < ids.length; i += DISMISSAL_BATCH) {
    const chunk = ids.slice(i, i + DISMISSAL_BATCH);
    const settle = (fn: (w: DismissalWaiter, id: number) => void) =>
      chunk.forEach((id) => batch.get(id)?.forEach((w) => fn(w, id)));
    try {
      /* No signal, deliberately: one read answers many queries, so one
       * observer's cancel must not fail the others. The deadline still holds. */
      const { data } = await pgRead<Array<{ property_id: number }> | null>(
        supabase.from('property_dismissals_public').select('property_id').in('property_id', chunk),
        { actAs: as || null },
      );
      const hit = new Set((data ?? []).map((r) => r.property_id));
      settle((w, id) => w.resolve(hit.has(id)));
    } catch (e) {
      settle((w) => w.reject(e));
    }
  }
}

export const fetchIsDismissed = (
  property_id: number,
  { actAs }: PgReadOptions = {},
): Promise<boolean> =>
  new Promise((resolve, reject) => {
    const as = actAs ?? '';
    let batch = dismissalWaiters.get(as);
    if (batch == null) {
      batch = new Map();
      dismissalWaiters.set(as, batch);
      setTimeout(() => void flushDismissalBatch(as), 0);
    }
    const waiters = batch.get(property_id) ?? [];
    waiters.push({ resolve, reject });
    batch.set(property_id, waiters);
  });

/* How many properties the caller has dismissed (migration 536). Only a gate —
 * a property two of the caller's accounts dismissed counts twice. */
export const fetchDismissedCount = async (
  { signal }: PgReadOptions = {},
): Promise<number> => {
  const { count } = await pgRead(
    supabase.from('property_dismissals_public').select('property_id', { count: 'exact', head: true }),
    { signal },
  );
  return count ?? 0;
};

/* Every pipeline card the caller's account holds, keyed by property_id — one
 * cheap read shared (React Query dedupes the key) by every funnel on every
 * surface (Browse cards), the board and lib/curationSettle.
 * It narrows no cohort: the pipeline scope is judged by migration 629.
 *
 * It carries the stage, not just membership, because the funnel renders the
 * stage badge (migration 377) — the earlier "set of ids" shape forced each
 * surface to either show a colourless funnel or issue its own per-property
 * read. `property_pipeline_public` is RLS-scoped + security_invoker (migration
 * 316), so this returns the caller's own board and nothing else.
 *
 * Exhaustive via fetchAllRows: a silently truncated membership map would blank
 * funnels and misjudge the in-place settle of a pipeline-scoped list. */
export interface PipelineMembership {
  property_id: number;
  stage_id: number;
  stage_label: string;
  stage_color: TagColor | null;
  stage_code: string | null;
  stage_position: number;
  is_terminal: boolean;
}

export type PipelineMembers = Map<number, PipelineMembership>;

export const fetchPipelineMembers = async (
  { signal, actAs }: PgReadOptions = {},
): Promise<PipelineMembers> => {
  const rows = await fetchAllRows<PipelineMembership>({
    relation: 'property_pipeline_public',
    build: () =>
      supabase
        .from('property_pipeline_public')
        .select(
          'property_id, stage_id, stage_label, stage_color, stage_code, stage_position, is_terminal',
          { count: 'exact' },
        ),
    orderBy: [{ column: 'property_id' }],
    key: ['property_id'],
    expectMax: 100_000,
    signal,
    actAs,
  });
  return new Map(rows.map((r) => [r.property_id, r]));
};

export const fetchPipelineStages = async (
  { signal, actAs }: PgReadOptions = {},
): Promise<PipelineStage[]> => {
  const { data } = await pgRead<PipelineStage[] | null>(
    supabase
      .from('pipeline_stages_public')
      /* `code` too (migration 377): the stage menu badges every row from this
       * list, and the stage editor pre-fills its code box from it — omitting the
       * column rendered every existing code as blank, inviting the operator to
       * overwrite intentional badges (several closed stages share a code). */
      .select(
        'id, key, label, position, color, is_terminal, is_entry, code, ' +
          /* The stage's delegated stamp (migration 641), for the stage editor. */
          'edited_by_account_id, edited_at',
      )
      .order('position'),
    { signal, actAs },
  );
  return data ?? [];
};

/* The kanban's STRUCTURAL read: which property sits in which stage, plus the
 * display fields the board can filter and sort on. ONE PostgREST read
 * (pipeline_board_public, migration 417, W5) — the pipeline/property join
 * that used to be two sequential round trips composed client-side now
 * happens server-side, so the second request never has to wait on the
 * first's ids.
 *
 * Decorations are deliberately NOT here. This function used to await, in one
 * promise, the cover images and two broker calls as well: six serialized
 * cross-origin round trips before the board could paint a single column, the
 * last of which existed only to fill a hover tooltip. They now load through
 * lib/hydration as independent non-blocking queries keyed on listing_id, so
 * the board paints as soon as this resolves and the thumbnails and broker
 * lines arrive behind it. Anything added to a card from here on is a
 * decoration until proven structural: if the board cannot filter, sort or
 * place a card without it, it does not belong in this queryFn. */
export const fetchPipelineBoard = async (
  { signal, actAs }: PgReadOptions = {},
): Promise<PipelineBoardCard[]> => {
  /* board_position is the MANUAL order and stays the default sort, but it is
   * not unique — it is assigned max+1 within the entry stage at bookmark time
   * and never renumbered on a stage move, so live data has collisions WITHIN
   * a stage. property_id is the deterministic tiebreak; without it equal
   * positions reshuffle between refetches. Any explicit sort re-sorts
   * client-side (lib/pipelineSort) and tiebreaks the same way. */
  const rows = await fetchAllRows<PipelineBoardRow>({
    relation: 'pipeline_board_public',
    build: () =>
      supabase.from('pipeline_board_public').select(PIPELINE_BOARD_COLS, { count: 'exact' }),
    orderBy: [{ column: 'board_position' }, { column: 'property_id' }],
    key: ['property_id'],
    expectMax: 100_000,
    signal,
    actAs,
  });
  return composePipelineCards(rows);
};

/* ---- LLM cost dashboard (/costs) -------------------------------------- */

/* Daily × feature × model spend aggregates from `llm_cost_daily_public`
 * (migration 280). numeric/bigint arrive as strings from PostgREST in
 * some paths — coerce every measure to a number once, here. */
export const fetchLlmCostDaily = async (
  days: number,
  { signal }: PgReadOptions = {},
): Promise<LlmCostDailyRow[]> => {
  const from = new Date();
  from.setUTCDate(from.getUTCDate() - days);
  const { data } = await pgRead<Record<string, unknown>[] | null>(
    supabase
      .from('llm_cost_daily_public')
      .select('*')
      .gte('day', from.toISOString().slice(0, 10))
      .order('day', { ascending: true }),
    { signal },
  );
  return (data ?? []).map((r) => ({
    day: String(r.day),
    called_for: String(r.called_for),
    provider: String(r.provider),
    model: String(r.model),
    calls: Number(r.calls ?? 0),
    error_calls: Number(r.error_calls ?? 0),
    cost_usd: Number(r.cost_usd ?? 0),
    input_tokens: Number(r.input_tokens ?? 0),
    output_tokens: Number(r.output_tokens ?? 0),
    cache_read_tokens: Number(r.cache_read_tokens ?? 0),
    cache_write_tokens: Number(r.cache_write_tokens ?? 0),
  }));
};

/* Hour-grain twin from `llm_cost_hourly_public` (migration 281); the
 * bucket timestamptz is normalized to a canonical ISO so client-side
 * zero-filling can key on exact string equality. */
export const fetchLlmCostHourly = async (
  hours: number,
  { signal }: PgReadOptions = {},
): Promise<LlmCostHourlyRow[]> => {
  const from = new Date();
  from.setUTCMinutes(0, 0, 0);
  from.setUTCHours(from.getUTCHours() - hours);
  const { data } = await pgRead<Record<string, unknown>[] | null>(
    supabase
      .from('llm_cost_hourly_public')
      .select('*')
      .gte('bucket', from.toISOString())
      .order('bucket', { ascending: true }),
    { signal },
  );
  return (data ?? []).map((r) => ({
    bucket: new Date(String(r.bucket)).toISOString(),
    called_for: String(r.called_for),
    provider: String(r.provider),
    model: String(r.model),
    calls: Number(r.calls ?? 0),
    error_calls: Number(r.error_calls ?? 0),
    cost_usd: Number(r.cost_usd ?? 0),
    input_tokens: Number(r.input_tokens ?? 0),
    output_tokens: Number(r.output_tokens ?? 0),
    cache_read_tokens: Number(r.cache_read_tokens ?? 0),
    cache_write_tokens: Number(r.cache_write_tokens ?? 0),
  }));
};
