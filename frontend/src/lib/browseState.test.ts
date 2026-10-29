import { createElement, type ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { act, renderHook } from '@testing-library/react';
import { MemoryRouter, useLocation, useNavigationType } from 'react-router-dom';
import { bboxAround, fromSearchParams, DEFAULT_FILTERS } from './filters';
import {
  DEFAULT_OVERLAY,
  PIN_LIMIT_DEFAULT,
  applySavedFilter,
  PIN_LIMIT_MAX,
  PIN_LIMIT_MIN,
  browseFiltersForBroker,
  browseUrlFromState,
  parsePinLimit,
  parseTab,
  useMemoryBrowseState,
  useUrlBrowseState,
} from './browseState';
import { DEFAULT_BOARD, boardFromSearchParams } from './brokerBoard';
import { DEFAULT_SCOPE, neighbourhoodFilters } from './neighbourhood';
import { layersFromSearchParams } from './mapOverlays';
import { DEFAULT_SORT, parseSort } from './queries';
import type { SavedDashboard } from './types';

const TREBIC = { lat: 49.2147, lng: 15.8816 };

describe('bboxAround', () => {
  it('produces a well-ordered box ~km across centred on the point', () => {
    const b = bboxAround(TREBIC.lat, TREBIC.lng, 5);
    expect(b.west).toBeLessThan(b.east);
    expect(b.south).toBeLessThan(b.north);
    // centre is (lat, lng)
    expect((b.north + b.south) / 2).toBeCloseTo(TREBIC.lat, 6);
    expect((b.east + b.west) / 2).toBeCloseTo(TREBIC.lng, 6);
    // N–S span ≈ 5 km (1° lat ≈ 111.32 km)
    expect((b.north - b.south) * 111.32).toBeCloseTo(5, 1);
  });
});

/* An unset ("Vše") category must resolve to NO
 * constraint — not Browse's narrow default (['byt'] / 'pronajem'). A broker who
 * mostly sells houses would otherwise open to an empty or wrong map. */
describe('browseFiltersForBroker', () => {
  it('seeds only the broker id for Vše/Vše — no category constraint, no viewport', () => {
    const f = browseFiltersForBroker({ brokerId: 527, categoryMain: null, categoryType: null });
    expect(f.brokerId).toBe(527);
    expect(f.categoryMain).toEqual([]);
    expect(f.categoryType).toBeNull();
    expect(f.bounds).toBeNull();
    expect(f.status).toBe('any');
  });

  it('carries a picked Typ/Nabídka through', () => {
    const f = browseFiltersForBroker({ brokerId: 527, categoryMain: 'dum', categoryType: 'prodej' });
    expect(f.categoryMain).toEqual(['dum']);
    expect(f.categoryType).toBe('prodej');
  });

  it("drops an unknown category to NO constraint, never to Browse's default", () => {
    const f = browseFiltersForBroker({ brokerId: 527, categoryMain: 'spaceship', categoryType: 'lease' });
    expect(f.categoryMain).toEqual([]);
    expect(f.categoryType).toBeNull();
  });
});

describe('browseUrlFromState ↔ fromSearchParams round-trip', () => {
  it('carries a property neighbourhood (type, deal, disposition, ownership, centre + radius) and its lens to the Browse URL', () => {
    const { filters } = neighbourhoodFilters(
      { ...TREBIC, category_main: 'byt', category_type: 'prodej', disposition: '3+kk', ownership: 'osobni' },
      DEFAULT_SCOPE,
    )!;
    const url = browseUrlFromState({
      filters,
      sort: DEFAULT_SORT,
      tab: 'stats',
      overlay: DEFAULT_OVERLAY,
      statsMarket: 'all',
    });
    const sp = new URLSearchParams(url.split('?')[1]);
    expect(sp.get('market')).toBe('all');
    const parsed = fromSearchParams(sp);
    expect(parsed.categoryMain).toEqual(['byt']);
    expect(parsed.categoryType).toBe('prodej');
    expect(parsed.dispositions).toEqual(['3+kk']);
    expect(parsed.ownership).toEqual(['osobni']);
    expect(parsed.locationMode).toBe('center_radius');
    expect(parsed.centerRadius?.radius_m).toBe(500);
    expect(parsed.centerRadius?.lat).toBeCloseTo(TREBIC.lat, 4);
  });

  /* The rent map is a registry layer now, its size category the variant: the
   * modal's link spells it in `layers`, and the old `rentmap=1&rentvk=N` link
   * still reads as the very same selection. */
  it('encodes the rent map + VK as a `layers` token that an old rentmap/rentvk link reads as too', () => {
    const url = browseUrlFromState({
      filters: DEFAULT_FILTERS,
      sort: DEFAULT_SORT,
      tab: 'map',
      overlay: { ...DEFAULT_OVERLAY, layers: 'rent_map:vk3' },
    });
    const sp = new URLSearchParams(url.split('?')[1]);
    expect(sp.get('layers')).toBe('rent_map:vk3');
    expect(sp.has('rentmap')).toBe(false);
    expect(sp.has('rentvk')).toBe(false);
    expect(layersFromSearchParams(new URLSearchParams('rentmap=1&rentvk=3'))).toBe('rent_map:vk3');
  });
});

/* The map's Kč/m² toggle is a VIEW knob on MapOverlayState: URL-serialised so
 * the view is shareable, and deliberately outside preset identity so flipping it
 * can never mark a saved preset dirty. */
describe('priceMetric on the map overlay', () => {
  const state = (priceMetric: 'total' | 'per_m2') => ({
    filters: DEFAULT_FILTERS,
    sort: DEFAULT_SORT,
    tab: 'map' as const,
    overlay: { ...DEFAULT_OVERLAY, priceMetric },
  });

  it('defaults to the total price', () => {
    expect(DEFAULT_OVERLAY.priceMetric).toBe('total');
  });

  it('serialises per_m2 into the shareable URL under `pm`', () => {
    expect(browseUrlFromState(state('per_m2'))).toContain('pm=per_m2');
  });

  /* The default must not appear in the URL — a default-state map has to
   * serialise to the plain /browse link, like every other overlay knob. */
  it('leaves the URL untouched at the default', () => {
    expect(browseUrlFromState(state('total'))).not.toContain('pm=');
  });

  /* `pm` is a view knob, NOT a cohort filter: it must not reach the filter
   * spec, or a shared Kč/m² map would look like a different search. */
  it('is invisible to the filter parser', () => {
    const withPm = fromSearchParams(new URLSearchParams('pm=per_m2'));
    const without = fromSearchParams(new URLSearchParams(''));
    expect(withPm).toEqual(without);
  });
});

/* The pin limit (`pins`) and the price-label switch (`labels`): view knobs like
 * `pm`. The limit IS the server's point budget (docs/design/map-counts.md), so a
 * shared link carries it; the default state serialises to the plain /browse URL. */
describe('pin limit and price labels on the map overlay', () => {
  const state = (overlay: Partial<typeof DEFAULT_OVERLAY>) => ({
    filters: DEFAULT_FILTERS,
    sort: DEFAULT_SORT,
    tab: 'map' as const,
    overlay: { ...DEFAULT_OVERLAY, ...overlay },
  });

  it('defaults to 200 pins with labels on, and neither default appears in the URL', () => {
    expect(DEFAULT_OVERLAY.pinLimit).toBe(PIN_LIMIT_DEFAULT);
    expect(DEFAULT_OVERLAY.priceLabels).toBe(true);
    const url = browseUrlFromState(state({}));
    expect(url).not.toContain('pins=');
    expect(url).not.toContain('labels=');
  });

  it('serialises a changed limit under `pins` and hidden labels under `labels=0`', () => {
    expect(browseUrlFromState(state({ pinLimit: 500 }))).toContain('pins=500');
    expect(browseUrlFromState(state({ priceLabels: false }))).toContain('labels=0');
  });

  it('clamps the limit into range and reads nonsense as the default', () => {
    expect(parsePinLimit(null)).toBe(PIN_LIMIT_DEFAULT);
    expect(parsePinLimit('abc')).toBe(PIN_LIMIT_DEFAULT);
    expect(parsePinLimit('1.5')).toBe(PIN_LIMIT_DEFAULT);
    expect(parsePinLimit('5')).toBe(PIN_LIMIT_MIN);
    expect(parsePinLimit('99999')).toBe(PIN_LIMIT_MAX);
    expect(parsePinLimit('350')).toBe(350);
  });

  /* View knobs, NOT cohort filters: they must not reach the filter spec. */
  it('are invisible to the filter parser', () => {
    const withKnobs = fromSearchParams(new URLSearchParams('pins=500&labels=0'));
    const without = fromSearchParams(new URLSearchParams(''));
    expect(withKnobs).toEqual(without);
  });
});

/* `?tab=` names the stats tab, the brokers tab or nothing: an old `?tab=table` link opens the Properties tab, not a blank page. */
describe('the tab in the URL', () => {
  const tabOf = (initial: string) =>
    renderHook(() => useUrlBrowseState(), {
      wrapper: ({ children }: { children: ReactNode }) =>
        createElement(MemoryRouter, { initialEntries: [initial] }, children),
    }).result.current.tab;

  it('reads stats and brokers as themselves, and anything else as the map', () => {
    expect(parseTab('stats')).toBe('stats');
    expect(parseTab('brokers')).toBe('brokers');
    expect(parseTab(null)).toBe('map');
    expect(parseTab('table')).toBe('map');
    expect(parseTab('nonsense')).toBe('map');
  });

  it('opens an old table link on the map', () => {
    expect(tabOf('/browse?tab=table')).toBe('map');
    expect(tabOf('/browse?tab=stats')).toBe('stats');
    expect(tabOf('/browse?tab=brokers')).toBe('brokers');
  });
});

/* The Makléři tab's options: view knobs in the URL (a default deletes its key),
 * carried through a filter change, outside the filters, in memory in a modal. */
describe('the broker board options', () => {
  const urlState = (initial: string) =>
    renderHook(() => ({ view: useUrlBrowseState(), search: useLocation().search }), {
      wrapper: ({ children }: { children: ReactNode }) =>
        createElement(MemoryRouter, { initialEntries: [initial] }, children),
    });

  it('reads every option from its key, and nonsense as the default', () => {
    expect(
      urlState('/browse?tab=brokers&horizon=60&rank_by=speed&top=50&min_props=5&firm=3,7').result.current.view.board,
    ).toEqual({ horizonDays: 60, sort: 'speed', limit: 50, minProperties: 5, firmIds: [3, 7] });
    expect(boardFromSearchParams(new URLSearchParams('horizon=45&rank_by=fast&top=900&min_props=0&firm=a,-2,4'))).toEqual(
      { ...DEFAULT_BOARD, firmIds: [4] },
    );
    expect(fromSearchParams(new URLSearchParams('horizon=14&min_props=3'))).toEqual(fromSearchParams(new URLSearchParams()));
  });

  it('writes a change as its own key, keeps it through a filter change, and deletes it at the default', () => {
    const { result } = urlState('/browse?cat=dum&tab=brokers');
    act(() => result.current.view.setBoard({ sort: 'speed', firmIds: [3] }));
    act(() => result.current.view.setFilters({ ...DEFAULT_FILTERS, categoryMain: ['dum', 'byt'] }));
    let sp = new URLSearchParams(result.current.search);
    expect([sp.get('rank_by'), sp.get('firm'), sp.get('tab'), sp.get('cat')]).toEqual(['speed', '3', 'brokers', 'dum,byt']);
    act(() => result.current.view.setBoard({ sort: 'properties', firmIds: [] }));
    sp = new URLSearchParams(result.current.search);
    expect([sp.has('rank_by'), sp.has('firm')]).toEqual([false, false]);
  });

  it('holds the options in memory in the explore modals, and Go to Browse carries them', () => {
    const { result } = renderHook(() => useMemoryBrowseState({ filters: DEFAULT_FILTERS, tab: 'brokers' }));
    expect(result.current.board).toEqual(DEFAULT_BOARD);
    act(() => result.current.setBoard({ horizonDays: 90 }));
    const sp = new URLSearchParams(browseUrlFromState({ ...result.current }).split('?')[1]);
    expect([sp.get('horizon'), sp.get('tab')]).toEqual(['90', 'brokers']);
  });
});

/* The reference-overlay selection (`layers`): a view knob like `pm`, written to
 * the URL and to this browser's remembered selection on every change, with the
 * old rent-panel checkbox `kraje=1` still read as the kraje layer and the old
 * rent-map knobs `rentmap=1&rentvk=N` as `rent_map:vkN`. */
describe('layers on the map overlay', () => {
  const REMEMBERED = 'map.layers';
  beforeEach(() => localStorage.clear());
  afterEach(() => localStorage.clear());

  const urlState = (initial: string) =>
    renderHook(() => ({ view: useUrlBrowseState(), search: useLocation().search }), {
      wrapper: ({ children }: { children: ReactNode }) =>
        createElement(MemoryRouter, { initialEntries: [initial] }, children),
    });

  it('round-trips through the shareable URL', () => {
    const url = browseUrlFromState({
      filters: DEFAULT_FILTERS,
      sort: DEFAULT_SORT,
      tab: 'map',
      overlay: { ...DEFAULT_OVERLAY, layers: 'flood_q5,metro' },
    });
    const sp = new URLSearchParams(url.split('?')[1]);
    expect(sp.get('layers')).toBe('flood_q5,metro');
    expect(layersFromSearchParams(sp)).toBe('flood_q5,metro');
    expect(fromSearchParams(sp)).toEqual(fromSearchParams(new URLSearchParams('')));
  });

  it('leaves the URL plain when nothing is drawn', () => {
    const url = browseUrlFromState({ filters: DEFAULT_FILTERS, sort: DEFAULT_SORT, tab: 'map', overlay: DEFAULT_OVERLAY });
    expect(url).not.toContain('layers=');
    expect(url).not.toContain('kraje=');
  });

  it('reads the legacy `kraje=1` as the kraje layer, and an explicit `layers` over it', () => {
    expect(urlState('/browse?kraje=1').result.current.view.overlay.layers).toBe('kraje');
    expect(urlState('/browse?kraje=1&layers=metro').result.current.view.overlay.layers).toBe('metro');
  });

  it('reads an old rent-map link as the rent_map layer, beside whatever else the URL selects', () => {
    const layersOf = (url: string) => urlState(url).result.current.view.overlay.layers;
    expect(layersOf('/browse?rentmap=1&rentvk=2')).toBe('rent_map:vk2');
    expect(layersOf('/browse?rentmap=1')).toBe('rent_map');
    expect(layersOf('/browse?rentmap=1&kraje=1')).toBe('kraje,rent_map');
    expect(layersOf('/browse?layers=metro&rentmap=1&rentvk=4')).toBe('metro,rent_map:vk4');
    expect(layersOf('/browse?layers=&rentmap=1')).toBe('rent_map');
  });

  it('writes a change over an old rent-map link as `layers`, retiring `rentmap` and `rentvk`', () => {
    const { result } = urlState('/browse?cat=byt&rentmap=1&rentvk=2');
    act(() => result.current.view.setOverlay({ layers: 'metro,rent_map:vk2' }));
    const sp = new URLSearchParams(result.current.search);
    expect(sp.get('layers')).toBe('metro,rent_map:vk2');
    expect(sp.has('rentmap')).toBe(false);
    expect(sp.has('rentvk')).toBe(false);
    expect(sp.get('cat')).toBe('byt');
    expect(localStorage.getItem(REMEMBERED)).toBe('metro,rent_map:vk2');
  });

  it('falls back to the remembered selection only when the URL has no `layers` key', () => {
    localStorage.setItem(REMEMBERED, 'metro');
    expect(urlState('/browse').result.current.view.overlay.layers).toBe('metro');
    expect(urlState('/browse?layers=').result.current.view.overlay.layers).toBe('');
  });

  it('writes a change to the URL and the remembered selection, retiring `kraje`', () => {
    const { result } = urlState('/browse?cat=byt&kraje=1');
    act(() => result.current.view.setOverlay({ layers: 'metro,kraje' }));
    const sp = new URLSearchParams(result.current.search);
    expect(sp.get('layers')).toBe('kraje,metro');
    expect(sp.has('kraje')).toBe(false);
    expect(sp.get('cat')).toBe('byt');
    expect(localStorage.getItem(REMEMBERED)).toBe('kraje,metro');
    expect(result.current.view.overlay.layers).toBe('kraje,metro');
  });

  it('writes an emptied selection explicitly, so a reload does not bring layers back', () => {
    localStorage.setItem(REMEMBERED, 'metro');
    const { result } = urlState('/browse');
    act(() => result.current.view.setOverlay({ layers: '' }));
    expect(new URLSearchParams(result.current.search).get('layers')).toBe('');
    expect(localStorage.getItem(REMEMBERED)).toBe('');
  });

  it('carries `layers` and the legacy keys through a filter change', () => {
    const { result } = urlState('/browse?kraje=1&rentmap=1&rentvk=3');
    act(() => result.current.view.setFilters({ ...DEFAULT_FILTERS, priceMax: 5_000_000 }));
    expect(result.current.view.overlay.layers).toBe('kraje,rent_map:vk3');
    act(() => result.current.view.setOverlay({ layers: 'metro' }));
    act(() => result.current.view.setFilters({ ...DEFAULT_FILTERS, priceMax: 4_000_000 }));
    expect(new URLSearchParams(result.current.search).get('layers')).toBe('metro');
  });

  it('seeds the in-memory (Explore) view from the remembered selection and writes it back', () => {
    localStorage.setItem(REMEMBERED, 'metro');
    const { result } = renderHook(() => useMemoryBrowseState({ filters: DEFAULT_FILTERS }));
    expect(result.current.overlay.layers).toBe('metro');
    act(() => result.current.setOverlay({ layers: 'flood_q5' }));
    expect(result.current.overlay.layers).toBe('flood_q5');
    expect(localStorage.getItem(REMEMBERED)).toBe('flood_q5');
    act(() => result.current.setOverlay({ priceMetric: 'per_m2' }));
    expect(localStorage.getItem(REMEMBERED)).toBe('flood_q5');
  });
});

describe('the dashboard on the URL', () => {
  const urlState = (initial: string) =>
    renderHook(
      () => ({ view: useUrlBrowseState(), search: useLocation().search, nav: useNavigationType() }),
      {
        wrapper: ({ children }: { children: ReactNode }) =>
          createElement(MemoryRouter, { initialEntries: [initial] }, children),
      },
    );
  const params = (search: string) => new URLSearchParams(search);
  const dashboard = (over: Partial<SavedDashboard> = {}): SavedDashboard => ({
    id: 'd1',
    name: 'Ceny',
    charts: [],
    filter_spec: null,
    position: 0,
    color: null,
    created_at: '2026-10-20T10:00:00+00:00',
    updated_at: '2026-10-20T10:00:00+00:00',
    owner_email: null,
    shared_with: [],
    ...over,
  });
  const SPEC = { filters: { ...DEFAULT_FILTERS, priceMax: 5_000_000 }, sort: '-price_czk' };

  it('reads `dashboard` and carries it through a filter change and a tab switch', () => {
    const { result } = urlState('/browse?tab=stats&dashboard=d1');
    expect(result.current.view.activeDashboardId).toBe('d1');
    act(() => result.current.view.setFilters({ ...DEFAULT_FILTERS, priceMax: 4_000_000 }));
    expect(params(result.current.search).get('dashboard')).toBe('d1');
    act(() => result.current.view.setTab('map'));
    expect(result.current.view.activeDashboardId).toBe('d1');
  });

  it('reads, sets and clears the Stats lens `market=sold` alone, replacing the entry and keeping the filter', () => {
    const { result } = urlState('/browse?tab=stats&cat=byt');
    expect(result.current.view.statsMarket).toBe('filter');
    act(() => result.current.view.setStatsMarket('sold'));
    expect(params(result.current.search).get('market')).toBe('sold');
    expect(result.current.view.statsMarket).toBe('sold');
    expect(result.current.nav).toBe('REPLACE');
    expect(params(result.current.search).get('cat')).toBe('byt');
    expect(result.current.view.filters.status).toBe('any');
    act(() => result.current.view.setFilters({ ...DEFAULT_FILTERS, priceMax: 4_000_000 }));
    expect(params(result.current.search).get('market')).toBe('sold');
    act(() => result.current.view.setStatsMarket('filter'));
    expect(params(result.current.search).has('market')).toBe(false);
  });

  it('a dashboard loads its saved switch: on sets `small=1`, off clears it; the modal state follows too', () => {
    const { result } = urlState('/browse?tab=stats&small=1');
    act(() => result.current.view.loadDashboard(dashboard({ id: 'd-off', show_small: false })));
    expect(params(result.current.search).has('small')).toBe(false);
    expect(params(result.current.search).get('dashboard')).toBe('d-off');
    expect(result.current.nav).toBe('REPLACE');
    act(() => result.current.view.loadDashboard(dashboard({ id: 'd-on', show_small: true, filter_spec: SPEC })));
    expect(params(result.current.search).get('small')).toBe('1');
    expect(result.current.nav).toBe('PUSH');
    const memory = renderHook(() => useMemoryBrowseState({ filters: DEFAULT_FILTERS }));
    act(() => memory.result.current.loadDashboard(dashboard({ id: 'd-on', show_small: true })));
    expect(memory.result.current.statsSmall).toBe(true);
  });

  it('reads, sets and clears the small-samples switch `small=1` alone, replacing the entry', () => {
    const { result } = urlState('/browse?tab=stats&cat=byt');
    expect(result.current.view.statsSmall).toBe(false);
    act(() => result.current.view.setStatsSmall(true));
    expect(params(result.current.search).get('small')).toBe('1');
    expect(result.current.view.statsSmall).toBe(true);
    expect(result.current.nav).toBe('REPLACE');
    act(() => result.current.view.setFilters({ ...DEFAULT_FILTERS, priceMax: 4_000_000 }));
    expect(params(result.current.search).get('small')).toBe('1');
    act(() => result.current.view.setStatsSmall(false));
    expect(params(result.current.search).has('small')).toBe(false);
  });

  it('the explore modals hold the switch in memory and Go to Browse carries it', () => {
    const { result } = renderHook(() => useMemoryBrowseState({ filters: DEFAULT_FILTERS }));
    expect(result.current.statsSmall).toBe(false);
    act(() => result.current.setStatsSmall(true));
    expect(result.current.statsSmall).toBe(true);
    const href = browseUrlFromState({
      filters: result.current.filters, sort: result.current.sort, tab: 'stats', overlay: result.current.overlay,
      statsSmall: result.current.statsSmall,
    });
    expect(new URLSearchParams(href.split('?')[1]).get('small')).toBe('1');
  });

  it('the explore modals hold the same dashboard and lens in memory, and Go to Browse carries both', () => {
    const { result } = renderHook(() => useMemoryBrowseState({ filters: { ...DEFAULT_FILTERS, priceMax: 9_000_000 } }));
    expect(result.current.activeDashboardId).toBeNull();
    expect(result.current.statsMarket).toBe('filter');
    act(() => result.current.loadDashboard(dashboard({ id: 'd-plain' })));
    expect(result.current.activeDashboardId).toBe('d-plain');
    expect(result.current.filters.priceMax).toBe(9_000_000);
    act(() => result.current.loadDashboard(dashboard({ id: 'd-spec', filter_spec: SPEC })));
    expect(result.current.activeDashboardId).toBe('d-spec');
    expect(result.current.filters.priceMax).toBe(5_000_000);
    expect(result.current.sort).toEqual(parseSort('-price_czk'));
    act(() => result.current.setStatsMarket('sold'));
    expect(result.current.statsMarket).toBe('sold');
    const href = browseUrlFromState({
      filters: result.current.filters, sort: result.current.sort, tab: 'stats', overlay: result.current.overlay,
      activeDashboardId: result.current.activeDashboardId, statsMarket: result.current.statsMarket,
    });
    const sp = new URLSearchParams(href.split('?')[1]);
    expect(sp.get('dashboard')).toBe('d-spec');
    expect(sp.get('market')).toBe('sold');
    expect(sp.get('tab')).toBe('stats');
  });

  it('sets and clears the id alone, replacing the entry', () => {
    const { result } = urlState('/browse?tab=stats&cat=byt');
    act(() => result.current.view.setActiveDashboardId('d2'));
    expect(params(result.current.search).get('dashboard')).toBe('d2');
    expect(result.current.nav).toBe('REPLACE');
    act(() => result.current.view.setActiveDashboardId(null));
    expect(params(result.current.search).has('dashboard')).toBe(false);
    expect(params(result.current.search).get('cat')).toBe('byt');
  });

  it('loads a dashboard without a filter as its id alone: the filters and the preset stay', () => {
    const { result } = urlState('/browse?tab=stats&preset=p1&cat=dum');
    const before = result.current.view.filters;
    expect(before.categoryMain).toEqual(['dum']);
    act(() => result.current.view.loadDashboard(dashboard()));
    const sp = params(result.current.search);
    expect(sp.get('dashboard')).toBe('d1');
    expect(sp.get('preset')).toBe('p1');
    expect(sp.get('cat')).toBe('dum');
    expect(result.current.view.filters).toEqual(before);
    expect(result.current.nav).toBe('REPLACE');
  });

  it('loads a dashboard with a filter as a preset load would, deselecting the preset, pushed', () => {
    const { result } = urlState('/browse?tab=stats&preset=p1&pm=per_m2');
    act(() => result.current.view.loadDashboard(dashboard({ filter_spec: SPEC })));
    const sp = params(result.current.search);
    expect(sp.get('dashboard')).toBe('d1');
    expect(sp.has('preset')).toBe(false);
    expect(sp.get('sort')).toBe('-price_czk');
    expect(sp.get('tab')).toBe('stats');
    expect(sp.get('pm')).toBe('per_m2');
    expect(result.current.view.filters.priceMax).toBe(5_000_000);
    expect(result.current.nav).toBe('PUSH');
  });

  it('replaces the entry instead when asked (an arrival from a link)', () => {
    const { result } = urlState('/browse?tab=stats&dashboard=d1');
    act(() => result.current.view.loadDashboard(dashboard({ filter_spec: SPEC }), { replace: true }));
    expect(params(result.current.search).get('dashboard')).toBe('d1');
    expect(result.current.view.filters.priceMax).toBe(5_000_000);
    expect(result.current.nav).toBe('REPLACE');
  });

  it('writes a preset and a dashboard filter through ONE body (applySavedFilter)', () => {
    const current = new URLSearchParams('tab=stats&sort=-price_czk&layers=metro');
    const viaHelper = applySavedFilter({ filters: SPEC.filters, sort: null }, current);
    expect(viaHelper.has('sort')).toBe(false);
    expect(viaHelper.get('layers')).toBe('metro');
    const { result } = urlState(`/browse?${current.toString()}`);
    act(() =>
      result.current.view.loadPreset({
        id: 'p9',
        name: 'x',
        filter_spec: { filters: SPEC.filters, sort: null },
        created_at: '',
        updated_at: '',
        position: 0,
        color: null,
        group_id: null,
      }),
    );
    const sp = params(result.current.search);
    expect(sp.get('preset')).toBe('p9');
    sp.delete('preset');
    expect(sp.toString()).toBe(viaHelper.toString());
  });

});
