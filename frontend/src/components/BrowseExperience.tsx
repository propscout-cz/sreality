/* The Browse "experience": the filter sidebar + Map/Stats tabs + the
 * map-overlay layers, with all data fetching and transient view-state (hover
 * sync, growth pickers, resizable columns, merge mode, on-card estimates).
 *
 * Lifted out of pages/Browse.tsx so the SAME experience powers both the Browse
 * page (URL-backed via useUrlBrowseState) and the "Explore area" modal
 * (in-memory via useMemoryBrowseState). The page-vs-modal differences are two
 * small props — `layout` (sticky full-page vs contained) and `features` (which
 * page-chrome bits to show). Everything cohort-related is identical, so the two
 * surfaces can never drift. */
import { haloPointsOf, NO_HOVER, nextHover, type HoverState, type HoverTarget } from '@/lib/hoverSync';
import {
  Suspense,
  useCallback,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
} from 'react';
import { lazyChunk } from '@/lib/lazyChunk';
import { useNewPropertyDialog } from '@/components/NewPropertyProvider';
import Skeleton from '@/components/Skeleton';
import { useIsFetching, useQuery } from '@tanstack/react-query';
import Tabs, { type Tab } from '@/components/Tabs';
import ResizeHandle from '@/components/ResizeHandle';
import {
  useSidebarWidth,
  useSidebarCollapsed,
  useMapSplitFraction,
  useMapCollapsed,
  useCardImageLarge,
  useCardCoverTag,
} from '@/lib/browseLayout';
import ImageSizeToggle from '@/components/ImageSizeToggle';
import { FilterSidebar, FilterSidebarRail } from '@/components/Filters';
import ListingCards, { CARD_PHOTOS_PER_CARD } from '@/components/ListingCards';
/* The Stats tab (the chart builder, dnd-kit, recharts) is its own chunk: it is a
 * secondary tab, and the first-visit bundle has a budget (scripts/bundle-budget.mjs). */
const StatsDashboard = lazyChunk(() => import('@/components/stats/StatsDashboard'));
import type { AnchorPoint, MapFlyToCommand, MapFrameCommand } from '@/components/ListingMap';
import type { MapySuggestion } from '@/lib/maps';
import { fetchDatasets, fetchGrowth, fetchGrowthShapes, fetchSeries, priceStatsKeys } from '@/lib/priceStats';
import { buildHoverData, type GrowthMetric } from '@/lib/growthChoropleth';
import { useMapOverlayRegistry } from '@/lib/mapOverlays';
import {
  countedWords,
  isDefault,
  browseTitleSummary,
  toSearchParams,
  savedMapArea,
  cohortKindOf,
  DEFAULT_FILTERS,
  type ListingFilters,
} from '@/lib/filters';
import { usePageTitle } from '@/lib/pageTitle';
import { portalLabel } from '@/lib/portals';
import CreateWatchdogModal from '@/components/CreateWatchdogModal';
import MergeBar from '@/components/MergeBar';
import AppliedFilterPills from '@/components/AppliedFilterPills';
import FilterPresetBar from '@/components/FilterPresetBar';
import type { FilterPreset } from '@/lib/types';
import { isApiConfigured } from '@/lib/api';
import { useCardEstimates } from '@/lib/useCardEstimates';
import { pushToast } from '@/lib/toast';
import { browseKeys } from '@/lib/browseKeys';
import { useMergeMode } from '@/lib/useMergeMode';
import { useMergeProperties } from '@/lib/useMergeProperties';
import {
  cityQualityKeys,
  fetchCityIndexDefinitions,
  fetchCityIndexValues,
  fetchCuratedCities,
  fetchCuratedCityPolygons,
  fetchListingsForCards,
  fetchListingsForMap,
  fetchBrowseCount,
  orderPortal,
  sortToParam,
  CARD_PAGE_SIZE,
  type CardRow,
  type CityIndexDefinition,
  type CityIndexValue,
  type CityPolygon,
  type CuratedCity,
  type MapResult,
  type SortDirection,
} from '@/lib/queries';
import { useInfiniteList } from '@/lib/useInfiniteList';
import { CardHydrationProvider } from '@/lib/hydration';
import type { KeysetCursor } from '@/lib/keyset';
import type { BrowseViewState, TabKey } from '@/lib/browseState';

const ListingMap = lazyChunk(() => import('@/components/ListingMap'));

export interface BrowseFeatures {
  /* The saved-set bars (filter presets; the Stats tab's dashboards) — page only. */
  presetBar?: boolean;
  /* "Create watchdog" CTA + name-prompt modal. */
  watchdog?: boolean;
  /* "Nová nemovitost" (paste an ad link) beside it — page only. */
  newProperty?: boolean;
  /* Dedup merge mode toggle on the Properties cards — page only. */
  mergeMode?: boolean;
  /* The big "Browse" heading in the summary block — hidden in the modal,
   * which has its own header. */
  title?: boolean;
  /* The filter sidebar (+ its resize handle) — hidden for a fully-scoped,
   * non-interactive explore view (the broker-explore modal), which seeds its
   * own fixed cohort and has no sidebar-driven filter for the operator to
   * change. Page and the area-explore modal both keep it shown. */
  sidebar?: boolean;
  /* The Stats tab — hidden by the broker-explore modal, a read-only scoped
   * view. Stats itself carries the broker scope (property_ids_filter, MS19). */
  stats?: boolean;
}

const DEFAULT_FEATURES: Required<BrowseFeatures> = {
  presetBar: true,
  watchdog: true,
  newProperty: true,
  mergeMode: true,
  title: true,
  sidebar: true,
  stats: true,
};

export default function BrowseExperience({
  view,
  layout = 'page',
  features,
  anchor = null,
}: {
  view: BrowseViewState;
  layout?: 'page' | 'modal';
  features?: BrowseFeatures;
  /* The "Explore area" origin property, pinned on the map independent of the
   * filter cohort (Explore-area modal only; undefined on the Browse page). */
  anchor?: AnchorPoint | null;
}) {
  const f = { ...DEFAULT_FEATURES, ...features };
  const isModal = layout === 'modal';
  const newProperty = useNewPropertyDialog();
  const { filters, sort, tab, overlay, activePresetId } = view;

  // Browser-tab title reflects the active filters ("PS: 2+kk · 60–90 m² · Praha")
  // so multiple Browse tabs are distinguishable. Skipped in the Explore-area
  // modal, which reuses this component but must not own the page title.
  usePageTitle(isModal ? null : browseTitleSummary(filters));

  /* Price-stats growth overlay control state — transient map-exploration knob,
   * kept in component state (not the view contract) on both surfaces. */
  const [showGrowth, setShowGrowth] = useState(false);
  const [growthDatasetId, setGrowthDatasetId] = useState<number | null>(null);
  const [growthMetric, setGrowthMetric] = useState<GrowthMetric>('rent_cagr_pct');
  const [growthFrom, setGrowthFrom] = useState('2015-01');
  const [growthTo, setGrowthTo] = useState(
    () => `${new Date().getFullYear()}-${String(new Date().getMonth() + 1).padStart(2, '0')}`,
  );
  const [growthChartOnHover, setGrowthChartOnHover] = useState(false);

  /* Map flyTo command. Set when the operator picks a place in the District
   * typeahead; ListingMap animates to the new centre. */
  const [mapFlyTo, setMapFlyTo] = useState<MapFlyToCommand | null>(null);
  /* Map frame command. Set when the operator loads a preset that saved a map
   * area; ListingMap fits the camera to it. */
  const [mapFrame, setMapFrame] = useState<MapFrameCommand | null>(null);
  const loadPreset = useCallback(
    (p: FilterPreset) => {
      view.loadPreset(p);
      const area = savedMapArea(p.filter_spec);
      if (area) setMapFrame({ bounds: area, ts: Date.now() });
    },
    [view],
  );
  const [watchdogModalOpen, setWatchdogModalOpen] = useState(false);

  /* Merge mode (page only): one statement of letters over the cards. */
  const mergeMode = useMergeMode();
  const mergeMut = useMergeProperties(mergeMode.exit);
  const handleLocationPick = useCallback((s: MapySuggestion) => {
    if (!s.position) return;
    setMapFlyTo({
      lat: s.position.lat,
      lng: s.position.lon,
      zoom: zoomForSuggestionType(s.type),
      ts: Date.now(),
    });
  }, []);

  /* Hover-sync between cards / map: ephemeral state, one transition
   * (lib/hoverSync). The cards hand over their rows' points for the map's
   * locator halo; a map-origin hover carries none (the cursor is already
   * on the spot). */
  const [hoverState, setHoverState] = useState<HoverState>(NO_HOVER);
  const hoveredIds = hoverState.ids;
  const setHoveredFromMap = useCallback(
    (ids: ReadonlyArray<number> | null) => setHoverState((prev) => nextHover(prev, ids, [], 'map')),
    [],
  );
  const setHoveredFromCards = useCallback(
    (targets: ReadonlyArray<HoverTarget> | null) =>
      setHoverState((prev) =>
        nextHover(prev, targets?.map((t) => t.listing_id) ?? null, haloPointsOf(targets ?? []), 'list'),
      ),
    [],
  );

  /* Operator-resizable columns + the sidebar/map-collapsed preferences (all per-browser
   * layout state, not part of the shareable view). When the map is collapsed
   * the cards take the full width and reflow to more columns automatically. */
  const sidebar = useSidebarWidth();
  const sidebarCollapsed = useSidebarCollapsed();
  const mapSplit = useMapSplitFraction();
  const mapCollapsed = useMapCollapsed();
  const cardImageLarge = useCardImageLarge();
  const cardCoverTag = useCardCoverTag();
  const sortOrderPortal = orderPortal(filters, sort);
  const cohortKind = cohortKindOf(filters);
  /* The map is only present on the Properties tab AND only when not collapsed —
   * the single source of truth the data-fetch gates and the layout both read,
   * so they can never disagree. */
  const mapVisible = tab === 'map' && !mapCollapsed.value;
  const outerRef = useRef<HTMLDivElement>(null);
  const innerRef = useRef<HTMLDivElement>(null);
  const onSidebarDrag = useCallback(
    (clientX: number) => {
      const left = outerRef.current?.getBoundingClientRect().left ?? 0;
      sidebar.set(clientX - left);
    },
    [sidebar],
  );
  const onMapSplitDrag = useCallback(
    (clientX: number) => {
      const r = innerRef.current?.getBoundingClientRect();
      if (!r || r.width === 0) return;
      mapSplit.set((r.right - clientX) / r.width);
    },
    [mapSplit],
  );

  /* Map tab fetches the map cohort (points, or counted by tile above the point
   * budget) + the paginated card slice. */
  const mapQuery = useQuery<MapResult, Error>({
    queryKey: browseKeys.map(filters, overlay.pinLimit),
    queryFn: ({ signal }) => fetchListingsForMap(filters, overlay.pinLimit, { signal }),
    placeholderData: (prev) => prev,
    enabled: mapVisible,
  });

  /* All three feed cityOverlay below, which only the map (ListingMap) reads —
   * same gate as the sibling cityPolygons query just below. */
  const citiesQuery = useQuery<CuratedCity[], Error>({
    queryKey: cityQualityKeys.cities,
    queryFn: fetchCuratedCities,
    enabled: mapVisible,
    staleTime: Infinity,
    gcTime: Infinity,
  });
  const cityDefsQuery = useQuery<CityIndexDefinition[], Error>({
    queryKey: cityQualityKeys.definitions,
    queryFn: fetchCityIndexDefinitions,
    enabled: mapVisible,
    staleTime: Infinity,
    gcTime: Infinity,
  });
  const cityValuesQuery = useQuery<CityIndexValue[], Error>({
    queryKey: cityQualityKeys.values,
    queryFn: fetchCityIndexValues,
    enabled: mapVisible,
    staleTime: Infinity,
    gcTime: Infinity,
  });

  const cityPolygonsQuery = useQuery<CityPolygon[], Error>({
    queryKey: ['curated_city_polygons'],
    queryFn: fetchCuratedCityPolygons,
    enabled: mapVisible,
    staleTime: Infinity,
    gcTime: Infinity,
  });
  const cityPolygonsMap = useMemo(() => {
    const m = new Map<number, string>();
    for (const p of cityPolygonsQuery.data ?? []) m.set(p.city_id, p.geojson);
    return m;
  }, [cityPolygonsQuery.data]);

  /* The reference-overlay registry (metro, flood zones, kraje, the rent map,
   * …): tiny, and the same cache entry the listing-detail and comparables maps
   * read. */
  const overlayRegistryQuery = useMapOverlayRegistry(mapVisible);
  /* Only worth a toast when layers are selected: they would silently not draw. */
  const overlayRegistryFailed = overlayRegistryQuery.isError && overlay.layers !== '';
  useEffect(() => {
    if (overlayRegistryFailed) {
      pushToast('err', 'Referenční vrstvy mapy se nepodařilo načíst — zůstanou skryté.');
    }
  }, [overlayRegistryFailed]);

  const psDatasetsQuery = useQuery({
    queryKey: priceStatsKeys.datasets,
    queryFn: fetchDatasets,
    enabled: mapVisible && showGrowth,
    staleTime: 60_000,
  });
  const psGrowthDatasetId = growthDatasetId ?? psDatasetsQuery.data?.[0]?.id ?? null;
  const psGrowthQuery = useQuery({
    queryKey: priceStatsKeys.growth(psGrowthDatasetId ?? -1, growthFrom, growthTo),
    queryFn: ({ signal }) =>
      fetchGrowth(psGrowthDatasetId as number, growthFrom, growthTo, { signal }),
    enabled: mapVisible && showGrowth && psGrowthDatasetId != null,
    staleTime: 60_000,
  });
  // Window-invariant polygons (W10b) — keyed on the dataset alone (not
  // growthFrom/growthTo), so dragging the window never re-fetches them.
  const psShapesQuery = useQuery({
    queryKey: priceStatsKeys.growthShapes(psGrowthDatasetId ?? -1),
    queryFn: ({ signal }) => fetchGrowthShapes(psGrowthDatasetId as number, { signal }),
    enabled: mapVisible && showGrowth && psGrowthDatasetId != null,
    staleTime: Infinity,
    gcTime: Infinity,
  });
  /* The growth overlay draws only obce whose polygon arrived (growthChoropleth
   * drops the rest), so a failed shapes read paints an EMPTY overlay that is
   * indistinguishable from "no obec qualifies" — while the operator is
   * actively looking at a growth filter. Reads fail silently app-wide
   * (main.tsx wires onError on the MutationCache only, never the QueryCache),
   * so say it here. Toast rather than inline: the overlay is a transient map
   * layer, not a section of the page. */
  const psShapesFailed = psShapesQuery.isError;
  useEffect(() => {
    if (psShapesFailed) {
      pushToast('err', 'Podklad mapy růstu se nepodařilo načíst — vrstva zůstane prázdná.');
    }
  }, [psShapesFailed]);

  const psSeriesQuery = useQuery({
    queryKey: priceStatsKeys.obecSeries(psGrowthDatasetId ?? -1, growthFrom, growthTo),
    queryFn: ({ signal }) =>
      fetchSeries(psGrowthDatasetId as number, growthFrom, growthTo, { signal }),
    enabled: mapVisible && showGrowth && growthChartOnHover && psGrowthDatasetId != null,
    staleTime: 60_000,
  });
  const psHoverData = useMemo(
    () => (psSeriesQuery.data ? buildHoverData(psSeriesQuery.data, growthMetric) : null),
    [psSeriesQuery.data, growthMetric],
  );

  const cards = useInfiniteList<CardRow>({
    queryKey: browseKeys.cards(filters, sort),
    queryFn: (cursor, signal) =>
      fetchListingsForCards(filters, sort, cursor as KeysetCursor | null, { signal }),
    pageSize: CARD_PAGE_SIZE,
    getRowId: (r) => r.property_id,
    enabled: tab === 'map',
    gcTime: 10 * 60_000,
  });

  /* W7a: the visible card cohort, for the shared hydration layer. Photos used to
   * be awaited INSIDE the cards queryFn, so no card painted until every card's
   * carousel had landed (measured live on 24 ids: 178 image rows, 178 correlated
   * CLIP-tag lookups, 750 buffers, ~131 ms — all of it on the paint path). Cards
   * now paint from browse_list alone and the carousels stream in behind them.
   *
   * `listing_id` is the key, never sreality_id: a post-Gate-2 non-sreality card
   * carries a NULL sreality_id and would silently lose its photos. perId 50 is
   * the same client-side retention cap the inline read used — comfortably above
   * any real listing (typical max ~25), so no carousel is truncated. This wave
   * is the non-blocking split; it is emphatically NOT a move to cover-only, and
   * a card must keep every photo it had. */
  const cardListingIds = useMemo(
    () => cards.rows.map((r) => r.listing_id),
    [cards.rows],
  );

  /* The ONE canonical cohort total — consumed by the header, the tab badge,
   * the cards' "of N" label, and (as the denominator of its mappable
   * subset) the map pill. Enabled on EVERY tab so the number never changes
   * just by switching tabs, and never goes stale-until-refresh because a tab
   * gate suppressed its refetch. Its fetch/error state is surfaced in the
   * header (FilterSummary) — a lagging or failed count must look different
   * from a settled one, never silently pin the previous cohort's value. */
  const browseCountQuery = useQuery<number, Error>({
    queryKey: browseKeys.count(filters),
    queryFn: ({ signal }) => fetchBrowseCount(filters, { signal }),
    placeholderData: (prev) => prev,
    staleTime: 60_000,
  });
  const cohortTotal = browseCountQuery.data ?? null;
  /* True while a refetch for a NEW cohort is in flight and we are still
   * showing the PREVIOUS cohort's number (placeholderData). This is exactly
   * the "looks settled but is stale" window that made the count appear stuck. */
  const cohortCountStale =
    browseCountQuery.isPlaceholderData && browseCountQuery.isFetching;

  const cardsRestorationKey = useMemo(() => {
    const sp = toSearchParams(filters);
    sp.set('sort', sortToParam(sort));
    return `cards:${sp.toString()}`;
  }, [filters, sort]);

  /* The on-card estimate (lib/useCardEstimates, shared with the collection page). */
  const { estimates, estimatingIds, estimateRefusals, onEstimate } = useCardEstimates({
    rows: cards.rows,
    enabled: tab === 'map' && isApiConfigured(),
    resetKey: cardsRestorationKey,
  });

  /* The Stats tab is the account's chart board (components/stats); each card reads
   * its own pivot, so the header's spinner follows any of them. */
  const statsFetching = useIsFetching({ queryKey: browseKeys.statsAll });

  const cityOverlay = useMemo(() => {
    const cities = citiesQuery.data;
    const defs = cityDefsQuery.data;
    const values = cityValuesQuery.data;
    if (!cities || !defs || !values) {
      return {
        cities: [] as CuratedCity[],
        cityIndexValues: new Map<number, number>(),
        cityIndexValuesAll: new Map<string, number>(),
        colorByIndex: null as CityIndexDefinition | null,
      };
    }
    const allMap = new Map<string, number>();
    for (const v of values) {
      allMap.set(`${v.city_id}:${v.index_name}`, v.value);
    }
    const colorDef = overlay.colorByIndexName
      ? defs.find((d) => d.index_name === overlay.colorByIndexName) ?? null
      : null;
    const colorMap = new Map<number, number>();
    if (colorDef) {
      for (const v of values) {
        if (v.index_name === colorDef.index_name) {
          colorMap.set(v.city_id, v.value);
        }
      }
    }
    const rulesPass = (city: CuratedCity): boolean => {
      for (const r of filters.cityIndexRules) {
        const v = allMap.get(`${city.city_id}:${r.index_name}`);
        if (v == null) return false;
        const op = r.op ?? '>=';
        if (op === '>=' && !(v >= r.value)) return false;
        if (op === '<=' && !(v <= r.value)) return false;
        if (op === '>' && !(v > r.value)) return false;
        if (op === '<' && !(v < r.value)) return false;
        if (op === '==' && !(v === r.value)) return false;
        if (op === '!=' && !(v !== r.value)) return false;
      }
      if (filters.minCityPopulation != null) {
        if (city.population == null || city.population < filters.minCityPopulation) return false;
      }
      if (filters.maxCityPopulation != null) {
        if (city.population == null || city.population > filters.maxCityPopulation) return false;
      }
      return true;
    };
    const matching = cities.filter(rulesPass);
    return {
      cities: matching,
      cityIndexValues: colorMap,
      cityIndexValuesAll: allMap,
      colorByIndex: colorDef,
    };
  }, [
    citiesQuery.data,
    cityDefsQuery.data,
    cityValuesQuery.data,
    overlay.colorByIndexName,
    filters.cityIndexRules,
    filters.minCityPopulation,
    filters.maxCityPopulation,
  ]);

  /* Unique per mount: an Explore window's Browse can sit over a page that has tabs. */
  const tabIds = useId();
  const tabs: ReadonlyArray<Tab<TabKey>> = [
    { key: 'map', label: 'Nemovitosti', count: cohortTotal?.toLocaleString('cs-CZ') },
    ...(f.stats ? [{ key: 'stats', label: 'Statistiky' } as Tab<TabKey>] : []),
  ];

  /* The listings PANEL's error drives the page banner. On the map tab that's the
   * cards list (cards.error) — a MAP-query failure is surfaced locally over the
   * map (mapError below), never blanking the listings: the two are independent
   * surfaces. */
  const activeError = tab === 'map' ? cards.error : null;
  const retryActive = () => {
    if (tab === 'map') cards.refetch();
  };
  const mapError = mapVisible ? mapQuery.error : null;

  return (
    <div className={`flex${isModal ? ' h-full' : ''}`} ref={outerRef}>
      {f.sidebar && (
        sidebarCollapsed.value ? (
          <FilterSidebarRail
            active={!isDefault(filters)}
            onExpand={() => sidebarCollapsed.set(false)}
            layout={layout}
          />
        ) : (
          <>
            <FilterSidebar
              filters={filters}
              onChange={view.setFilters}
              onLocationPick={handleLocationPick}
              width={sidebar.value}
              layout={layout}
              onCollapse={() => sidebarCollapsed.set(true)}
            />

            <ResizeHandle
              ariaLabel="Resize the filters sidebar"
              onMove={onSidebarDrag}
              onEnd={sidebar.persist}
              onReset={sidebar.reset}
              className={
                isModal
                  ? 'hidden lg:flex self-stretch h-full w-3 -mx-1.5'
                  : 'hidden lg:flex sticky top-14 self-start h-[calc(100dvh-3.5rem)] w-3 -mx-1.5'
              }
            />
          </>
        )
      )}

      <div
        className={`flex-1 min-w-0 flex flex-col${
          isModal
            ? ' h-full min-h-0'
            : tab === 'map'
              ? ' h-[calc(100dvh-3.5rem)]'
              : ''
        }`}
      >
        <div className="px-6 pt-5">
          <FilterSummary
            filters={filters}
            order={sortOrderPortal ? { portal: sortOrderPortal, direction: sort.direction } : null}
            count={cohortTotal}
            countStale={cohortCountStale}
            countError={browseCountQuery.error}
            onRetryCount={() => browseCountQuery.refetch()}
            showTitle={f.title}
            loading={
              tab === 'map' ? mapQuery.isLoading || cards.isLoading :
              tab === 'stats' ? statsFetching > 0 :
              false
            }
            onFiltersChange={view.setFilters}
            onClearBounds={() => view.setBounds(null)}
            onCreateWatchdog={f.watchdog ? () => setWatchdogModalOpen(true) : undefined}
            onNewProperty={f.newProperty && newProperty ? () => newProperty.open() : undefined}
          />
          {f.presetBar && (
            <div className="mt-3">
              <FilterPresetBar
                filters={filters}
                sort={sort}
                activePresetId={activePresetId}
                onLoad={loadPreset}
                onActivePresetIdChange={view.setActivePresetId}
                onFiltersChange={view.setFilters}
                onLoadPipelineView={view.loadPipelineView}
              />
            </div>
          )}
          {/* The tab strip (components/Tabs.tsx) carries the map tab's two toggles
              at its right end, above its rule. A merge in progress takes a row of
              its own under the strip: its letters and statement grow with every
              group, and beside the tabs they pushed Merge and Cancel off the screen. */}
          <div className="mt-4">
            <Tabs
              tabs={tabs}
              active={tab}
              onChange={view.setTab}
              ariaLabel="Zobrazení výsledků"
              idPrefix={tabIds}
              trailing={
                tab === 'map' ? (
                  <>
                    <MapViewToggle
                      collapsed={mapCollapsed.value}
                      onChange={mapCollapsed.set}
                    />
                    <ImageSizeToggle
                      large={cardImageLarge.value}
                      onChange={cardImageLarge.set}
                      label="Card image size"
                    />
                  </>
                ) : undefined
              }
            />
            {tab === 'map' && f.mergeMode && mergeMode.active && (
              <div className="mt-3 min-w-0">
                <MergeBar
                  merge={mergeMode}
                  merging={mergeMut.isPending}
                  onMerge={() => mergeMut.mutate(mergeMode.letters)}
                />
              </div>
            )}
          </div>
        </div>

        {tab === 'map' && (
          <div
            role="tabpanel"
            id={`${tabIds}panel-map`}
            aria-labelledby={`${tabIds}tab-map`}
            className="px-6 pt-5 pb-6 flex-1 min-h-0"
          >
            <div
              ref={innerRef}
              className={
                mapCollapsed.value
                  ? 'grid grid-cols-1 h-full min-h-[560px]'
                  : 'grid grid-cols-1 lg:grid-cols-[minmax(0,1fr)_1rem_var(--map-w)] h-full min-h-[560px]'
              }
              style={
                mapCollapsed.value
                  ? undefined
                  : ({ '--map-w': `${mapSplit.value * 100}%` } as CSSProperties)
              }
            >
              {/* Photos and the storey ONLY. The card grid renders no cover
                  thumbnail and no broker line, so it must not fetch either —
                  see CardDecorations. */}
              <CardHydrationProvider
                listingIds={cardListingIds}
                renders={{ photos: CARD_PHOTOS_PER_CARD, floors: true }}
              >
              <ListingCards
                rows={cards.isLoading ? null : cards.rows}
                total={cohortTotal}
                sort={sort}
                orderPortal={sortOrderPortal}
                cohortKind={cohortKind}
                isLoading={cards.isLoading}
                isError={cards.isError}
                isFetchingNextPage={cards.isFetchingNextPage}
                hasNextPage={cards.hasNextPage}
                onReachEnd={cards.fetchNextPage}
                restorationKey={cardsRestorationKey}
                imageLarge={cardImageLarge.value}
                coverTag={cardCoverTag.value}
                onCoverTag={cardCoverTag.set}
                hasFilters={!isDefault(filters)}
                hasBounds={filters.bounds != null}
                hoveredIds={hoveredIds}
                hoverOrigin={hoverState.origin}
                onHover={setHoveredFromCards}
                onSort={view.setSort}
                onClearFilters={() => view.setFilters(DEFAULT_FILTERS)}
                onClearBounds={() => view.setBounds(null)}
                merge={mergeMode.active ? mergeMode : null}
                onEnterMerge={f.mergeMode ? mergeMode.enter : undefined}
                estimates={estimates}
                estimatingIds={estimatingIds}
                estimateRefusals={estimateRefusals}
                onEstimate={onEstimate}
              />
              </CardHydrationProvider>
              {!mapCollapsed.value && (
                <>
              <ResizeHandle
                ariaLabel="Resize the listings and map columns"
                onMove={onMapSplitDrag}
                onEnd={mapSplit.persist}
                onReset={mapSplit.reset}
                className="hidden lg:flex h-full"
              />
              <div className="min-h-0 h-full relative">
                {mapError && <MapErrorOverlay error={mapError} />}
                <Suspense fallback={<MapSkeleton />}>
                  <ListingMap
                    rows={mapQuery.data?.rows ?? []}
                    counts={mapQuery.data?.counts ?? null}
                    total={mapQuery.data?.total ?? null}
                    cohortTotal={cohortTotal}
                    isLoading={mapQuery.isLoading}
                    bounds={filters.bounds}
                    onBoundsChange={view.setBounds}
                    hoveredIds={hoveredIds}
                    haloPoints={hoverState.points}
                    onHover={setHoveredFromMap}
                    centerCircle={
                      filters.locationMode === 'center_radius'
                        ? filters.centerRadius
                        : null
                    }
                    flyTo={mapFlyTo}
                    frame={mapFrame}
                    anchor={anchor}
                    cities={cityOverlay.cities}
                    cityPolygons={cityPolygonsMap}
                    showCities={overlay.showCities}
                    onToggleShowCities={(next) => view.setOverlay({ showCities: next })}
                    priceMetric={overlay.priceMetric}
                    onPriceMetricChange={(next) => view.setOverlay({ priceMetric: next })}
                    priceLabels={overlay.priceLabels}
                    onPriceLabelsChange={(next) => view.setOverlay({ priceLabels: next })}
                    pinLimit={overlay.pinLimit}
                    onPinLimitChange={(next) => view.setOverlay({ pinLimit: next })}
                    colorByIndex={cityOverlay.colorByIndex}
                    cityIndexValues={cityOverlay.cityIndexValues}
                    cityIndexValuesAll={cityOverlay.cityIndexValuesAll}
                    cityIndexDefinitions={cityDefsQuery.data ?? []}
                    onColorByIndexChange={(name) => view.setOverlay({ colorByIndexName: name })}
                    overlayRegistry={overlayRegistryQuery.data}
                    overlayLayers={overlay.layers}
                    onOverlayLayersChange={(layers) => view.setOverlay({ layers })}
                    growthRows={psGrowthQuery.data ?? []}
                    growthShapes={psShapesQuery.data ?? []}
                    growthDatasets={psDatasetsQuery.data ?? []}
                    showGrowth={showGrowth}
                    growthDatasetId={psGrowthDatasetId}
                    growthMetric={growthMetric}
                    growthFrom={growthFrom}
                    growthTo={growthTo}
                    onToggleShowGrowth={setShowGrowth}
                    onGrowthDatasetChange={setGrowthDatasetId}
                    onGrowthMetricChange={setGrowthMetric}
                    onGrowthFromChange={setGrowthFrom}
                    onGrowthToChange={setGrowthTo}
                    growthChartOnHover={growthChartOnHover}
                    growthHoverData={psHoverData}
                    onToggleGrowthChartOnHover={setGrowthChartOnHover}
                  />
                </Suspense>
              </div>
                </>
              )}
            </div>
          </div>
        )}

        {tab === 'stats' && (
          <div
            role="tabpanel"
            id={`${tabIds}panel-stats`}
            aria-labelledby={`${tabIds}tab-stats`}
            className={`px-6 pt-5 pb-8${isModal ? ' flex-1 min-h-0 overflow-y-auto' : ''}`}
          >
            <Suspense fallback={<Skeleton height={320} className="my-8" />}>
              <StatsDashboard
                filters={filters}
                view={view}
                market={{ value: view.statsMarket, set: view.setStatsMarket }}
                small={{ value: view.statsSmall, set: view.setStatsSmall }}
                onIncludeDismissed={() => view.setFilters({ ...filters, showDismissed: true })}
              />
            </Suspense>
          </div>
        )}

        {activeError && (
          <div className="px-6 pb-6">
            <ErrorBanner error={activeError} onRetry={retryActive} />
          </div>
        )}
      </div>

      {f.watchdog && watchdogModalOpen
        ? (
            <CreateWatchdogModal filters={filters} onClose={() => setWatchdogModalOpen(false)} />
          )
        : null}
    </div>
  );
}

function zoomForSuggestionType(type: string): number {
  if (type.startsWith('regional.address')) return 16;
  if (type === 'regional.street') return 16;
  if (type === 'regional.municipality_part') return 13;
  if (type === 'regional.municipality') return 11;
  if (type === 'regional.region') return 8;
  if (type === 'regional.country') return 6;
  return 13;
}

/* One portal's "Newest first" / "Oldest first" (MS19, queries.ts orderPortal), named in
 * the header: the rows are placed by their newest ad on that portal, not by their own
 * first seen, and nothing else on the row says so. */
function PortalOrderChip({ portal, direction }: { portal: string; direction: SortDirection }) {
  const label = portalLabel(portal);
  return (
    <span
      className="inline-flex items-center px-2 py-0.5 text-[0.7rem] tracking-wide rounded-[var(--radius-sm)] bg-[var(--color-copper-soft)] text-[var(--color-copper)]"
      title={`Newest first follows ${label}: each property is placed by when its newest ${label} ad was first seen, so a property advertised there again moves to the top. Dates on the rows are the property's own. With no portal or several, Newest first follows when the property was first seen.`}
    >
      {direction === 'desc' ? 'newest' : 'oldest'} on {label}
    </span>
  );
}

function FilterSummary({
  filters,
  order,
  count,
  countStale,
  countError,
  onRetryCount,
  loading,
  showTitle,
  onFiltersChange,
  onClearBounds,
  onCreateWatchdog,
  onNewProperty,
}: {
  filters: ListingFilters;
  order: { portal: string; direction: SortDirection } | null;
  count: number | null;
  /* The displayed count is the PREVIOUS cohort's value while a refetch for the
   * new cohort is still in flight. Surfaced so a lagging count is visibly
   * provisional instead of looking settled (the bug this page had). */
  countStale: boolean;
  /* The canonical-count query failed. We show whatever number we still have,
   * dimmed, plus a retry — never a silently-wrong total with no signal. */
  countError: Error | null;
  onRetryCount: () => void;
  loading: boolean;
  showTitle: boolean;
  /* Each applied-filter pill's ×: one filter back to its default. */
  onFiltersChange: (next: ListingFilters) => void;
  onClearBounds: () => void;
  onCreateWatchdog?: () => void;
  onNewProperty?: () => void;
}) {
  return (
    <div className="flex items-start justify-between gap-4">
      <div className="min-w-0">
        {showTitle && <h1 className="text-2xl leading-tight">Browse</h1>}
        <div className={showTitle ? 'mt-1 flex items-center gap-2 flex-wrap' : 'flex items-center gap-2 flex-wrap'}>
          <p
            className={`text-sm text-[var(--color-ink-2)]${
              countStale || (countError && count != null) ? ' opacity-60' : ''
            }`}
            aria-busy={countStale || undefined}
          >
            {loading && count == null ? (
              'Loading…'
            ) : (
              <>
                <span className="font-mono tabular-nums">{count == null ? '…' : count.toLocaleString('cs-CZ')}</span>{' '}
                {countedWords(filters)}
              </>
            )}
          </p>
          {order && <PortalOrderChip portal={order.portal} direction={order.direction} />}
          {countStale && (
            <span
              className="inline-flex items-center text-[0.7rem] tracking-wide text-[var(--color-ink-3)]"
              title="Recounting for the updated filters…"
            >
              updating…
            </span>
          )}
          {countError && (
            <button
              type="button"
              onClick={onRetryCount}
              className="inline-flex items-center gap-1 px-2 py-0.5 text-[0.7rem] tracking-wide rounded-[var(--radius-sm)] bg-[var(--color-brick-soft)] text-[var(--color-brick)] hover:bg-[var(--color-brick)]/15 transition-colors"
              title={`Couldn't refresh the count: ${countError.message}`}
            >
              <span>Count may be stale — retry</span>
            </button>
          )}
        </div>
        {/* Ruling P09: the count says what is counted, the pills under it say the
            rest. The line is kept when no filter is set, so the first pill (a map
            pan sets the map area) does not push the tabs, cards and map down. */}
        <div data-testid="applied-filters-row" className="mt-1.5 min-h-6">
          <AppliedFilterPills filters={filters} onChange={onFiltersChange} onClearBounds={onClearBounds} />
        </div>
      </div>
      <div className="shrink-0 flex items-center gap-2">
        {/* Outlined: the copper primary on this header stays the watchdog. */}
        {onNewProperty && (
          <button
            type="button"
            onClick={onNewProperty}
            className="px-3 py-1.5 text-sm rounded-[var(--radius-sm)] border border-[var(--color-rule-strong)] text-[var(--color-ink-2)] hover:border-[var(--color-copper)] hover:text-[var(--color-copper)] transition-colors"
          >
            Nová nemovitost
          </button>
        )}
        {onCreateWatchdog && (
          <button
            type="button"
            onClick={onCreateWatchdog}
            className="px-3 py-1.5 text-sm rounded-[var(--radius-sm)] bg-[var(--color-copper)] text-[var(--color-on-copper)] hover:bg-[var(--color-copper-2)] transition-colors"
            title="Save the current filters as a watchdog — get notified when a new listing matches"
          >
            + Create watchdog
          </button>
        )}
      </div>
    </div>
  );
}

function MapSkeleton() {
  return (
    <div className="h-full min-h-[480px] rounded-[var(--radius-md)] border border-[var(--color-rule)] bg-[var(--color-paper-2)] flex items-center justify-center">
      <p className="text-sm text-[var(--color-ink-3)] tracking-wide">Loading map…</p>
    </div>
  );
}

/* Properties-tab layout switch: fold the map panel away to give the cards the
 * full width (they reflow to more columns automatically), or bring it back.
 * Reuses the app's segmented-control idiom (LocationModeSection): paper-2
 * track, copper-fill active, borders-only — civic-archive consistent. The
 * choice persists per-browser (useMapCollapsed), not in the shareable URL. */
function MapViewToggle({
  collapsed,
  onChange,
}: {
  collapsed: boolean;
  onChange: (collapsed: boolean) => void;
}) {
  const seg = (active: boolean) =>
    [
      'inline-flex items-center gap-1.5 px-2.5 py-1 text-[0.7rem] rounded-[var(--radius-xs)] transition-colors',
      active
        ? 'bg-[var(--color-copper)] text-[var(--color-on-copper)]'
        : 'text-[var(--color-ink-3)] hover:text-[var(--color-ink-2)]',
    ].join(' ');
  return (
    <div
      role="group"
      aria-label="Properties layout"
      className="inline-flex items-center gap-0.5 p-0.5 rounded-[var(--radius-sm)] bg-[var(--color-paper-2)] border border-[var(--color-rule)]"
    >
      <button
        type="button"
        onClick={() => onChange(false)}
        aria-pressed={!collapsed}
        title="Show the map beside the cards"
        className={seg(!collapsed)}
      >
        <SplitGlyph />
        Split
      </button>
      <button
        type="button"
        onClick={() => onChange(true)}
        aria-pressed={collapsed}
        title="Hide the map — full-width cards"
        className={seg(collapsed)}
      >
        <CardsGlyph />
        Cards
      </button>
    </div>
  );
}

/* Two panes (wide cards | narrow map). */
function SplitGlyph() {
  return (
    <svg
      width="13"
      height="13"
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.3"
      strokeLinejoin="round"
      aria-hidden
    >
      <rect x="2" y="3" width="12" height="10" rx="1.5" />
      <line x1="9.5" y1="3" x2="9.5" y2="13" />
    </svg>
  );
}

/* A full grid of cards. */
function CardsGlyph() {
  return (
    <svg
      width="13"
      height="13"
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.3"
      strokeLinejoin="round"
      aria-hidden
    >
      <rect x="2.5" y="2.5" width="4.5" height="4.5" rx="1" />
      <rect x="9" y="2.5" width="4.5" height="4.5" rx="1" />
      <rect x="2.5" y="9" width="4.5" height="4.5" rx="1" />
      <rect x="9" y="9" width="4.5" height="4.5" rx="1" />
    </svg>
  );
}

/* Map-local failure notice. The map is its own surface — a map error shows here,
 * floating over the map, and never blanks the listings list (which loads fine on
 * its own keyset query). The timeout copy is a guide for an exceptionally broad area. */
function MapErrorOverlay({ error }: { error: Error }) {
  const isTimeout = /statement timeout|57014/i.test(error.message);
  return (
    <div className="absolute inset-x-0 top-0 z-10 m-3 p-3 rounded-[var(--radius-sm)] border border-[var(--color-brick)]/30 bg-[var(--color-brick-soft)] text-sm text-[var(--color-brick)] shadow-sm">
      {isTimeout ? (
        <>
          <strong className="font-medium">This area is too broad to map.</strong>{' '}
          Zoom in or add a filter — the listings list is unaffected.
        </>
      ) : (
        <>
          <strong className="font-medium">Map failed to load:</strong> {error.message}
        </>
      )}
    </div>
  );
}

function ErrorBanner({ error, onRetry }: { error: Error; onRetry?: () => void }) {
  /* A statement-timeout means the list query didn't finish under the caller's
   * budget (8s for `authenticated`). Cohort breadth is NOT the usual cause and
   * the copy must not imply it is — the last occurrence was a query-shape
   * defect: a single-value filter emitted as `in.()`, which cost the ordered
   * index and made the planner scan the whole band (fixed in
   * registryQueryBuilder.ts; see the comment there). Retrying that could never
   * have helped, so the copy no longer promises it is "usually transient" —
   * it states what happened, offers the retry for the genuinely transient
   * case (a cold band right after a read-model rebuild), and says plainly that
   * a repeat is a defect worth reporting rather than an operator error. */
  const isTimeout = /statement timeout|57014/i.test(error.message);
  return (
    <div className="mt-4 p-3 rounded-[var(--radius-sm)] border border-[var(--color-brick)]/30 bg-[var(--color-brick-soft)] text-sm text-[var(--color-brick)] flex items-center justify-between gap-3">
      <span>
        {isTimeout ? (
          <>
            <strong className="font-medium">The list took too long to load.</strong>{' '}
            The database cut the query off. Try again — if it keeps happening on
            the same view, that's a bug worth reporting, not a filter to narrow.
          </>
        ) : (
          <>
            <strong className="font-medium">Query failed:</strong> {error.message}
          </>
        )}
      </span>
      {onRetry && (
        <button
          type="button"
          onClick={onRetry}
          className="shrink-0 px-2 py-0.5 text-[0.75rem] tracking-wide rounded-[var(--radius-sm)] border border-[var(--color-brick)]/40 hover:bg-[var(--color-brick)]/10 transition-colors"
        >
          Retry
        </button>
      )}
    </div>
  );
}
