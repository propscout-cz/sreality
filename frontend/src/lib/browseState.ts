/* The Browse "view state" contract + its two adapters.
 *
 * `<BrowseExperience>` (the filter sidebar + Map/Table/Stats + map overlays)
 * is driven by this contract instead of reading the URL directly, so the SAME
 * experience powers both the Browse page (URL-backed, shareable links) and the
 * "Explore area" modal (in-memory, seeded from a listing). The two adapters are
 * the only place that knows where the state lives:
 *   - useUrlBrowseState()   — searchParams-backed (the Browse page); preserves
 *                             the page's exact replace/push history semantics
 *                             and the overlay-knob "preserveExtras" carry that
 *                             toSearchParams doesn't cover.
 *   - useMemoryBrowseState() — plain useState (the modal). Precedent: WatchdogEdit
 *                             already runs the registry FilterForm URL-free.
 */
import { ROUTES } from './routes';
import { useCallback, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import {
  DEFAULT_FILTERS,
  bboxAround,
  fromSearchParams,
  pipelineViewFilters,
  readPresetSpec,
  toSearchParams,
  withPresetLenses,
  type ListingFilters,
  type MapBounds,
} from '@/lib/filters';
import {
  DEFAULT_SORT,
  parseSort,
  sortToParam,
  type SortSpec,
} from '@/lib/queries';
import type { ImageRef } from '@/lib/imageUrl';
import {
  canonicalLayers,
  layersFromSearchParams,
  readRememberedLayers,
  useRememberedLayers,
  writeRememberedLayers,
} from '@/lib/mapOverlays';
import type { Disposition, FilterPreset, ListingPublic } from '@/lib/types';

export type TabKey = 'map' | 'table' | 'stats';

/* Map-overlay UI knobs. NOT part of the cohort filter spec — they paint
 * layers (city pins, the price-growth choropleth, reference overlays).
 * On the Browse page they live in the URL (shareable view); in the modal
 * they're in-memory. */
export interface MapOverlayState {
  showCities: boolean;
  colorByIndexName: string | null;
  /* The reference overlays drawn — the MF rent map among them, its size
   * category the variant (`rent_map:vk2`) — in lib/mapOverlays grammar,
   * canonical; '' = none. URL key `layers` — present, even empty, it wins;
   * absent, the selection this browser remembers applies. Every change
   * writes both. */
  layers: string;
  /* What the pin LABEL shows: the total price or the per-m² measure. A view
   * knob, not a cohort filter — it belongs here for the three properties this
   * interface guarantees: it is URL-serialised (so a Kč/m² map is a shareable
   * link), it is carried by preserveExtras (so filtering does not silently
   * reset it), and it sits OUTSIDE preset identity (so flipping it can never
   * mark a saved preset dirty — the same reasoning rule 22 applies to the
   * pipeline scope). The UNIT is still decided per pin, never per map. */
  priceMetric: PriceMetric;
  /* Whether the pins print their price label at all (URL key `labels`, `0` hides). */
  priceLabels: boolean;
  /* Up to this many properties in view the map draws every one as its own pin, at any
   * zoom; above it the server counts them by world tile (docs/design/map-counts.md).
   * It IS the server's point budget, so it sits in the map query's key (URL key `pins`,
   * absent = PIN_LIMIT_DEFAULT). A view knob like `priceMetric`: URL-serialised,
   * carried by preserveExtras, outside preset identity. */
  pinLimit: number;
}

/* The two things a pin can be labelled with. */
export type PriceMetric = 'total' | 'per_m2';

export const PIN_LIMIT_DEFAULT = 200;
export const PIN_LIMIT_MIN = 20;
/* A 2 000-point read is about 0.9 MB (453 B per row, measured 2026-08-26). */
export const PIN_LIMIT_MAX = 2_000;

/* The URL's `pins` value as a limit: clamped into range, anything unreadable = the default. */
export const parsePinLimit = (raw: string | null): number => {
  const n = raw == null ? NaN : Number(raw);
  if (!Number.isInteger(n)) return PIN_LIMIT_DEFAULT;
  return Math.min(PIN_LIMIT_MAX, Math.max(PIN_LIMIT_MIN, n));
};

export const DEFAULT_OVERLAY: MapOverlayState = {
  showCities: true,
  colorByIndexName: null,
  layers: '',
  priceMetric: 'total',
  priceLabels: true,
  pinLimit: PIN_LIMIT_DEFAULT,
};

/* The contract <BrowseExperience> consumes. Setters carry the page's history
 * semantics in the URL adapter (filter/sort/preset = push; bounds/tab/overlay =
 * replace); the memory adapter ignores that (no history) and just sets state. */
export interface BrowseViewState {
  filters: ListingFilters;
  sort: SortSpec;
  tab: TabKey;
  overlay: MapOverlayState;
  activePresetId: string | null;
  setFilters: (next: ListingFilters) => void;
  setBounds: (b: MapBounds | null) => void;
  setSort: (next: SortSpec) => void;
  setTab: (next: TabKey) => void;
  setOverlay: (patch: Partial<MapOverlayState>) => void;
  setActivePresetId: (id: string | null) => void;
  loadPreset: (p: FilterPreset) => void;
  /* Load the built-in Pipeline view: the deal-pipeline scope over a NEUTRAL
   * cohort, with any active preset deselected. One action rather than
   * setFilters + setActivePresetId, because in the URL adapter those are two
   * writes against the same searchParams snapshot — the second would clobber
   * the first. Deselecting matters beyond tidiness: leaving a preset marked
   * active after replacing its filters would render it dirty and pop the
   * "Update preset" button, which this chip must never do. */
  loadPipelineView: () => void;
}

/* The legacy spellings of a `layers` selection — `kraje` and the rent map's
 * `rentmap` + `rentvk` (layersFromSearchParams) — carried until a layer change
 * rewrites them, so an old link keeps its layers through filtering. */
const LEGACY_LAYER_KEYS = ['kraje', 'rentmap', 'rentvk'];
const OVERLAY_URL_KEYS = [
  'tab', 'sort', 'cities', 'colorby', 'layers', ...LEGACY_LAYER_KEYS, 'preset', 'pm', 'labels', 'pins',
];

/* The Browse page adapter. Lifted verbatim from the page so its behaviour —
 * the dual serialization (filters via toSearchParams + overlay knobs carried by
 * preserveExtras) and the per-setter replace/push choice — is byte-identical. */
export function useUrlBrowseState(): BrowseViewState {
  const [searchParams, setSearchParams] = useSearchParams();
  const filters = useMemo(() => fromSearchParams(searchParams), [searchParams]);
  const sort = useMemo(() => parseSort(searchParams.get('sort')), [searchParams]);
  const tab = (searchParams.get('tab') ?? 'map') as TabKey;
  /* The live remembered selection: a bare /browse shows what the operator last
   * chose on any map, and this page's own writes survive a navigation that drops
   * the `layers` key. */
  const [rememberedLayers] = useRememberedLayers();
  const overlay = useMemo<MapOverlayState>(() => {
    return {
      showCities: searchParams.get('cities') !== '0',
      colorByIndexName: searchParams.get('colorby') ?? null,
      layers: layersFromSearchParams(searchParams) ?? rememberedLayers,
      priceMetric: searchParams.get('pm') === 'per_m2' ? 'per_m2' : 'total',
      priceLabels: searchParams.get('labels') !== '0',
      pinLimit: parsePinLimit(searchParams.get('pins')),
    };
  }, [searchParams, rememberedLayers]);
  const activePresetId = searchParams.get('preset');

  const preserveExtras = useCallback(
    (sp: URLSearchParams): URLSearchParams => {
      for (const key of OVERLAY_URL_KEYS) {
        const v = searchParams.get(key);
        if (v != null) sp.set(key, v);
      }
      return sp;
    },
    [searchParams],
  );

  const setFilters = useCallback(
    (next: ListingFilters) => {
      setSearchParams(preserveExtras(toSearchParams(next)), { replace: false });
    },
    [preserveExtras, setSearchParams],
  );

  const setBounds = useCallback(
    (b: MapBounds | null) => {
      const next: ListingFilters = { ...filters, bounds: b };
      setSearchParams(preserveExtras(toSearchParams(next)), { replace: true });
    },
    [filters, preserveExtras, setSearchParams],
  );

  const setSort = useCallback(
    (next: SortSpec) => {
      const sp = new URLSearchParams(searchParams);
      if (sortToParam(next) === sortToParam(DEFAULT_SORT)) sp.delete('sort');
      else sp.set('sort', sortToParam(next));
      setSearchParams(sp, { replace: false });
    },
    [searchParams, setSearchParams],
  );

  const setTab = useCallback(
    (next: TabKey) => {
      const sp = new URLSearchParams(searchParams);
      if (next === 'map') sp.delete('tab');
      else sp.set('tab', next);
      setSearchParams(sp, { replace: true });
    },
    [searchParams, setSearchParams],
  );

  const setOverlay = useCallback(
    (patch: Partial<MapOverlayState>) => {
      const sp = new URLSearchParams(searchParams);
      if ('showCities' in patch) patch.showCities ? sp.delete('cities') : sp.set('cities', '0');
      if ('colorByIndexName' in patch)
        patch.colorByIndexName ? sp.set('colorby', patch.colorByIndexName) : sp.delete('colorby');
      if ('layers' in patch) {
        const layers = canonicalLayers(patch.layers);
        sp.set('layers', layers);
        for (const key of LEGACY_LAYER_KEYS) sp.delete(key);
        writeRememberedLayers(layers);
      }
      /* Default ('total') deletes the key, like every other knob here — a
       * default-state map must serialise to the plain /browse URL. */
      if ('priceMetric' in patch)
        patch.priceMetric === 'per_m2' ? sp.set('pm', 'per_m2') : sp.delete('pm');
      if ('priceLabels' in patch) patch.priceLabels ? sp.delete('labels') : sp.set('labels', '0');
      if ('pinLimit' in patch) {
        const limit = parsePinLimit(String(patch.pinLimit));
        limit === PIN_LIMIT_DEFAULT ? sp.delete('pins') : sp.set('pins', String(limit));
      }
      setSearchParams(sp, { replace: true });
    },
    [searchParams, setSearchParams],
  );

  const setActivePresetId = useCallback(
    (id: string | null) => {
      const sp = new URLSearchParams(searchParams);
      if (id) sp.set('preset', id);
      else sp.delete('preset');
      setSearchParams(sp, { replace: true });
    },
    [searchParams, setSearchParams],
  );

  const loadPreset = useCallback(
    (p: FilterPreset) => {
      const { filters: pf, sort: ps } = readPresetSpec(p.filter_spec);
      const sp = preserveExtras(toSearchParams(withPresetLenses(pf, filters)));
      const presetSort = ps ?? sortToParam(DEFAULT_SORT);
      if (presetSort === sortToParam(DEFAULT_SORT)) sp.delete('sort');
      else sp.set('sort', presetSort);
      sp.set('preset', p.id);
      setSearchParams(sp, { replace: false });
    },
    [filters, preserveExtras, setSearchParams],
  );

  const loadPipelineView = useCallback(() => {
    // Same shape as loadPreset: one write, filters + preset id together. Pushed
    // (not replaced) so Back returns the operator to the view they came from —
    // that is the undo for "it wiped my filters".
    const sp = preserveExtras(toSearchParams(pipelineViewFilters()));
    sp.delete('preset');
    setSearchParams(sp, { replace: false });
  }, [preserveExtras, setSearchParams]);

  return {
    filters, sort, tab, overlay, activePresetId,
    setFilters, setBounds, setSort, setTab, setOverlay, setActivePresetId, loadPreset,
    loadPipelineView,
  };
}

/* The in-memory adapter (the modal). No URL, no history — every setter just
 * updates React state. Preset loading is supported but the modal mounts the
 * preset bar off, so it's effectively unused there. */
export function useMemoryBrowseState(init: {
  filters: ListingFilters;
  sort?: SortSpec;
  tab?: TabKey;
  overlay?: MapOverlayState;
}): BrowseViewState {
  const [filters, setFiltersState] = useState<ListingFilters>(init.filters);
  const [sort, setSortState] = useState<SortSpec>(init.sort ?? DEFAULT_SORT);
  const [tab, setTabState] = useState<TabKey>(init.tab ?? 'map');
  const [overlay, setOverlayState] = useState<MapOverlayState>(
    () => init.overlay ?? { ...DEFAULT_OVERLAY, layers: readRememberedLayers() },
  );
  const [activePresetId, setActivePresetId] = useState<string | null>(null);

  return useMemo<BrowseViewState>(
    () => ({
      filters, sort, tab, overlay, activePresetId,
      setFilters: (next) => setFiltersState(next),
      setBounds: (b) => setFiltersState((f) => ({ ...f, bounds: b })),
      setSort: (next) => setSortState(next),
      setTab: (next) => setTabState(next),
      setOverlay: (patch) => {
        const next = 'layers' in patch ? { ...patch, layers: canonicalLayers(patch.layers) } : patch;
        if (next.layers != null) writeRememberedLayers(next.layers);
        setOverlayState((o) => ({ ...o, ...next }));
      },
      setActivePresetId,
      loadPreset: (p) => {
        const { filters: pf, sort: ps } = readPresetSpec(p.filter_spec);
        setFiltersState((f) => withPresetLenses(pf, f));
        if (ps) setSortState(parseSort(ps));
      },
      loadPipelineView: () => {
        setFiltersState(pipelineViewFilters());
        setActivePresetId(null);
      },
    }),
    [filters, sort, tab, overlay, activePresetId],
  );
}

const CATEGORY_MAINS = ['byt', 'dum', 'komercni', 'pozemek', 'ostatni'] as const;
const CATEGORY_TYPES = ['prodej', 'pronajem'] as const;

/* Default viewport span when opening Browse focused on a property: the camera
 * frames ~5 km across and the cohort is "everything in that viewport". */
export const EXPLORE_VIEWPORT_KM = 5;

/* The property the operator opened "Explore area" FROM. Carried through the
 * payload from the trigger site (where it's already fully loaded), never
 * re-fetched — so the anchor pin + the origin-property panel show EXACTLY the
 * bytes the operator saw one click ago (no skew, no loading flicker). OPTIONAL:
 * a trigger that only has coordinates (a future Region "explore here") still
 * opens the modal, just with no anchor/panel. Images typed as the minimal
 * ImageRef shape; ImagePublic satisfies it structurally. */
export interface ExploreOrigin {
  listing: ListingPublic;
  images: ImageRef[];
}

export interface ExploreAreaSeed {
  lat: number;
  lng: number;
  categoryMain: string | null;
  categoryType: string | null;
  disposition: Disposition | null;
  origin?: ExploreOrigin;
}

/* Build the modal's initial cohort filter from a listing: same category +
 * disposition, scoped to a ~5 km viewport around the property's coordinates.
 * Disposition is dropped when null (komerční / pozemek / unparsed). Category
 * falls back to the Browse default if the listing isn't one of the three the
 * UI supports (e.g. pozemek) — Browse can't render those anyway. */
export const browseFiltersForArea = (
  seed: ExploreAreaSeed,
  km: number = EXPLORE_VIEWPORT_KM,
): ListingFilters => {
  const categoryMain: ListingFilters['categoryMain'] =
    (CATEGORY_MAINS as readonly string[]).includes(seed.categoryMain ?? '')
      ? [seed.categoryMain as ListingFilters['categoryMain'][number]]
      : [...DEFAULT_FILTERS.categoryMain];
  const categoryType = (CATEGORY_TYPES as readonly string[]).includes(seed.categoryType ?? '')
    ? (seed.categoryType as ListingFilters['categoryType'])
    : DEFAULT_FILTERS.categoryType;
  return {
    ...DEFAULT_FILTERS,
    categoryMain,
    categoryType,
    dispositions: seed.disposition ? [seed.disposition] : [],
    bounds: bboxAround(seed.lat, seed.lng, km),
    locationMode: 'viewport',
  };
};

/* The broker-explore modal's seed: the broker page's OWN Typ/Nabídka pill
 * selection (BrokerDetail.tsx), carried over so filtering the inventory table
 * first and then exploring opens the map already scoped the same way. */
export interface ExploreBrokerSeed {
  brokerId: number;
  categoryMain: string | null;
  categoryType: string | null;
}

/* Build the broker-explore modal's initial cohort filter. Unlike
 * browseFiltersForArea above, an unset ("Vše") category must resolve to NO
 * constraint (`[]` / `null`), not Browse's own narrow default (`['byt']` /
 * `'pronajem'`) — a broker who mostly sells houses would otherwise show an
 * empty or wrong map. No bounds/viewport seed either: ListingMap already
 * auto-fits to the first non-empty result set, so the map frames itself
 * around wherever this broker's listings actually are. */
export const browseFiltersForBroker = (seed: ExploreBrokerSeed): ListingFilters => {
  const categoryMain: ListingFilters['categoryMain'] =
    seed.categoryMain != null && (CATEGORY_MAINS as readonly string[]).includes(seed.categoryMain)
      ? [seed.categoryMain as ListingFilters['categoryMain'][number]]
      : [];
  const categoryType: ListingFilters['categoryType'] =
    seed.categoryType != null && (CATEGORY_TYPES as readonly string[]).includes(seed.categoryType)
      ? (seed.categoryType as ListingFilters['categoryType'])
      : null;
  return {
    ...DEFAULT_FILTERS,
    categoryMain,
    categoryType,
    brokerId: seed.brokerId,
  };
};

/* Serialize a full Browse view-state to a `/browse?…` URL. The inverse of the
 * URL adapter's reads, so the modal's "Go to Browse" link reproduces exactly
 * what the operator built in the modal (filters + sort + tab + overlay). */
export const browseUrlFromState = (s: {
  filters: ListingFilters;
  sort: SortSpec;
  tab: TabKey;
  overlay: MapOverlayState;
}): string => {
  const sp = toSearchParams(s.filters);
  if (sortToParam(s.sort) !== sortToParam(DEFAULT_SORT)) sp.set('sort', sortToParam(s.sort));
  if (s.tab !== 'map') sp.set('tab', s.tab);
  if (!s.overlay.showCities) sp.set('cities', '0');
  if (s.overlay.colorByIndexName) sp.set('colorby', s.overlay.colorByIndexName);
  if (s.overlay.layers) sp.set('layers', s.overlay.layers);
  if (s.overlay.priceMetric === 'per_m2') sp.set('pm', 'per_m2');
  if (!s.overlay.priceLabels) sp.set('labels', '0');
  if (s.overlay.pinLimit !== PIN_LIMIT_DEFAULT) sp.set('pins', String(s.overlay.pinLimit));
  const qs = sp.toString();
  // Concatenated rather than withQuery(): `sp` is built here key by key and may
  // one day carry a repeated key, which a Record round-trip would collapse.
  const base = ROUTES.browse.build();
  return qs ? `${base}?${qs}` : base;
};
