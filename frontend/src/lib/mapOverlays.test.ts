/* The overlay library, both halves: the selection and registry
 * (mapOverlays.ts) and the drawing (mapOverlayDraw.ts). */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, renderHook } from '@testing-library/react';
import type { LayerSpecification, Map as MaplibreMap } from 'maplibre-gl';

vi.mock('./api', async (orig) => ({
  ...(await orig<typeof import('./api')>()),
  apiGetBlob: vi.fn(),
}));

import { ApiError, apiGetBlob } from './api';
import { PPM2_UNIT } from './measure';
import {
  fetchOverlayTile,
  overlayDataDate,
  overlayDrawOrder,
  overlayFillOwner,
  overlayPopupHtml,
  overlaySource,
  overlayStyleLayers,
  parseOverlayTileUrl,
  registerOverlayProtocol,
  useMapOverlays,
  useMapZoom,
} from './mapOverlayDraw';
import {
  canonicalLayers,
  layersFromSearchParams,
  layersOf,
  parseLayers,
  readRememberedLayers,
  serializeLayers,
  useRememberedLayers,
  writeRememberedLayers,
  type MapOverlayRegistry,
  type OverlayLayer,
  type OverlaySection,
} from './mapOverlays';

const section = (over: Partial<OverlaySection> & Pick<OverlaySection, 'key' | 'layers'>): OverlaySection => ({
  label: over.key,
  sort: 1,
  minzoom: 9,
  maxzoom: 12,
  attribution: 'zdroj',
  licence: 'CC BY 4.0',
  source_url: 'https://example.invalid/',
  note: null,
  source_revised_at: null,
  fetched_at: '2026-10-14T10:00:00Z',
  version: 100,
  ...over,
});

const layer = (over: Partial<OverlayLayer> & Pick<OverlayLayer, 'key'>): OverlayLayer => ({
  label: over.key,
  sort: 1,
  legend: [],
  scale: null,
  variants: null,
  feature_count: 1,
  ...over,
});

const METRO_LAYER = layer({
  key: 'metro',
  label: 'Metro',
  legend: [
    { class: 'A', label: 'Linka A', color: '#00A562' },
    { class: 'D', label: 'Metro D – návrh', color: '#1F77D0', dash: true },
    { class: 'reserve', label: 'Územní rezerva', color: '#8C9196', dash: true },
    { class: 'station', label: 'Plánovaná stanice', color: '#1F77D0', geom: 'point' },
  ],
});
const METRO = section({ key: 'metro', label: 'Metro', sort: 1, layers: [METRO_LAYER] });

const FLOOD = section({
  key: 'flood',
  label: 'Záplavová území',
  sort: 2,
  minzoom: 10,
  layers: [
    layer({ key: 'flood_q5', sort: 1, legend: [{ class: 'q5', label: 'Q5 – pětiletá voda', color: '#1D4F91' }] }),
    layer({ key: 'flood_q100', sort: 3, legend: [{ class: 'q100', label: 'Q100 – stoletá voda', color: '#7FB3E6' }] }),
    layer({ key: 'flood_az', sort: 5, legend: [{ class: 'az', label: 'Aktivní zóna', color: '#B3261E', dash: true }] }),
  ],
});

const RENT_LAYER = layer({
  key: 'rent_map',
  label: 'Cenová mapa nájemného',
  scale: {
    prop: 'vk1',
    stops: [[600, '#1a1a5e'], [150, '#eaf0f6']],
    null_color: 'rgba(140,140,140,0.35)',
    unit: 'rent_per_m2',
    opacity: 0.62,
    outline: { color: 'rgba(60, 60, 90, 0.25)', width: 0.4 },
  },
  variants: [
    { key: 'vk1', label: '1+kk, 1+1', prop: 'vk1' },
    { key: 'vk2', label: '2+kk, 2+1', prop: 'vk2' },
  ],
});
const RENT = section({ key: 'rent_map', label: 'Cenová mapa nájemného', sort: 3, minzoom: 7, layers: [RENT_LAYER] });

const REGISTRY: MapOverlayRegistry = { sections: [METRO, FLOOD, RENT] };

const specOf = (styleLayers: { spec: LayerSpecification }[], id: string) =>
  styleLayers.find((l) => l.spec.id === id)?.spec as (LayerSpecification & {
    filter?: unknown;
    layout?: Record<string, unknown>;
    paint?: Record<string, unknown>;
    source?: string;
    'source-layer'?: string;
  }) | undefined;

describe('the `layers` grammar', () => {
  it('parses keys and key:variant tokens, ignoring blanks, spaces and malformed tokens', () => {
    const sel = parseLayers(' metro , rent_map:vk2,,flood_q5, bad token,x:y:z,:vk1');
    expect([...sel.entries()]).toEqual([
      ['metro', null],
      ['rent_map', 'vk2'],
      ['flood_q5', null],
    ]);
    expect(parseLayers('').size).toBe(0);
    expect(parseLayers(null).size).toBe(0);
  });

  it('serialises sorted by key, so one selection has one spelling', () => {
    const sel = new Map<string, string | null>([
      ['rent_map', 'vk2'],
      ['metro', null],
      ['flood_q5', null],
    ]);
    expect(serializeLayers(sel)).toBe('flood_q5,metro,rent_map:vk2');
    expect(canonicalLayers('metro,flood_q5,metro')).toBe('flood_q5,metro');
    expect(canonicalLayers('metro:')).toBe('metro');
    expect(canonicalLayers('')).toBe('');
  });

  it('keeps only what the registry can draw, and spells the first variant as none', () => {
    const drawable = layersOf(parseLayers('ghost,metro,rent_map:vk1,flood_q5:nope'), REGISTRY);
    expect(serializeLayers(drawable)).toBe('flood_q5,metro,rent_map');
    expect(serializeLayers(layersOf(parseLayers('rent_map:vk2'), REGISTRY))).toBe('rent_map:vk2');
    expect(serializeLayers(layersOf(parseLayers('rent_map:vk9'), REGISTRY))).toBe('rent_map');
    expect(layersOf(parseLayers('metro'), undefined).size).toBe(0);
  });
});

describe('layersFromSearchParams — the URL rule and the legacy key', () => {
  const read = (qs: string) => layersFromSearchParams(new URLSearchParams(qs));

  it('says nothing when the URL says nothing, so the remembered selection applies', () => {
    expect(read('')).toBeNull();
    expect(read('cat=byt&kraje=0')).toBeNull();
  });

  it('treats `layers` as explicit even when empty, and canonicalises it', () => {
    expect(read('layers=')).toBe('');
    expect(read('layers=metro%2Cflood_q5')).toBe('flood_q5,metro');
  });

  it('reads the old rent-map panel checkbox `kraje=1` as the kraje layer', () => {
    expect(read('kraje=1')).toBe('kraje');
    expect(read('rentmap=1&kraje=1')).toBe('kraje,rent_map');
  });

  it('reads the old rent-map knobs `rentmap=1&rentvk=N` as `rent_map:vkN`, beside the rest', () => {
    expect(read('rentmap=1&rentvk=3')).toBe('rent_map:vk3');
    expect(read('rentmap=1')).toBe('rent_map');
    expect(read('rentmap=1&rentvk=9')).toBe('rent_map');
    expect(read('rentvk=2')).toBeNull();
    expect(read('rentmap=0&rentvk=2')).toBeNull();
    expect(read('layers=metro&rentmap=1&rentvk=2')).toBe('metro,rent_map:vk2');
    expect(read('layers=&rentmap=1')).toBe('rent_map');
    expect(read('layers=rent_map:vk4&rentmap=1&rentvk=2')).toBe('rent_map:vk4');
  });

  it('lets an explicit `layers` win over the legacy key', () => {
    expect(read('kraje=1&layers=metro')).toBe('metro');
    expect(read('kraje=1&layers=')).toBe('');
  });
});

describe('the remembered selection', () => {
  beforeEach(() => localStorage.clear());
  afterEach(() => localStorage.clear());

  it('is stored canonical under map.layers', () => {
    expect(readRememberedLayers()).toBe('');
    writeRememberedLayers('metro,flood_q5');
    expect(localStorage.getItem('map.layers')).toBe('flood_q5,metro');
    expect(readRememberedLayers()).toBe('flood_q5,metro');
  });

  it('seeds a URL-less map and is written back on change', () => {
    localStorage.setItem('map.layers', 'metro');
    const { result } = renderHook(() => useRememberedLayers());
    expect(result.current[0]).toBe('metro');
    act(() => result.current[1]('metro,kraje'));
    expect(result.current[0]).toBe('kraje,metro');
    expect(localStorage.getItem('map.layers')).toBe('kraje,metro');
  });
});

describe('style layers from the registry', () => {
  it('builds area, line, dashed-line, point and label layers for one MVT layer', () => {
    const styleLayers = overlayStyleLayers(METRO, METRO_LAYER, true);
    expect(styleLayers.map((l) => [l.spec.id, l.tier])).toEqual([
      ['ov-metro-fill', 0],
      ['ov-metro-line', 1],
      ['ov-metro-dash', 1],
      ['ov-metro-point', 2],
      ['ov-metro-label', 3],
    ]);
    for (const { spec } of styleLayers) {
      expect(spec).toMatchObject({ source: 'ov-metro', 'source-layer': 'metro', layout: { visibility: 'visible' } });
    }
  });

  it('colours by class from the legend and splits solid from dashed classes', () => {
    const styleLayers = overlayStyleLayers(METRO, METRO_LAYER, true);
    const colour = [
      'match', ['to-string', ['get', 'class']],
      'A', '#00A562', 'D', '#1F77D0', 'reserve', '#8C9196', 'station', '#1F77D0',
      '#8c9196',
    ];
    const dashed = [
      'any',
      ['in', ['to-string', ['get', 'class']], ['literal', ['D', 'reserve']]],
      ['==', ['coalesce', ['get', 'dash'], false], true],
    ];
    const isLine = ['==', ['geometry-type'], 'LineString'];
    expect(specOf(styleLayers, 'ov-metro-line')?.paint).toEqual({ 'line-color': colour, 'line-width': 2.2 });
    expect(specOf(styleLayers, 'ov-metro-line')?.filter).toEqual(['all', isLine, ['!', dashed]]);
    expect(specOf(styleLayers, 'ov-metro-dash')?.filter).toEqual(['all', isLine, dashed]);
    expect(specOf(styleLayers, 'ov-metro-dash')?.paint).toMatchObject({ 'line-dasharray': [2, 1.5] });
    expect(specOf(styleLayers, 'ov-metro-point')?.filter).toEqual(['==', ['geometry-type'], 'Point']);
    expect(specOf(styleLayers, 'ov-metro-label')?.layout).toMatchObject({ 'text-field': ['get', 'label'] });
  });

  it('draws areas translucent and un-antialiased; a feature-level dash flag still gets the dashed line layer', () => {
    const q5 = FLOOD.layers[0];
    const styleLayers = overlayStyleLayers(FLOOD, q5, false);
    expect(specOf(styleLayers, 'ov-flood_q5-dash')?.filter).toEqual([
      'all',
      ['==', ['geometry-type'], 'LineString'],
      ['any', ['in', ['to-string', ['get', 'class']], ['literal', []]], ['==', ['coalesce', ['get', 'dash'], false], true]],
    ]);
    expect(specOf(styleLayers, 'ov-flood_q5-fill')).toMatchObject({
      filter: ['==', ['geometry-type'], 'Polygon'],
      layout: { visibility: 'none' },
      paint: {
        'fill-color': ['match', ['to-string', ['get', 'class']], 'q5', '#1D4F91', '#8c9196'],
        'fill-opacity': 0.4,
        'fill-antialias': false,
      },
    });
  });

  it('fills a scale layer at its own opacity, every other area at the default', () => {
    const opacityOf = (sec: OverlaySection, l: OverlayLayer) =>
      specOf(overlayStyleLayers(sec, l, true), `ov-${l.key}-fill`)?.paint?.['fill-opacity'];
    expect(opacityOf(RENT, RENT_LAYER)).toBe(0.62);
    expect(opacityOf(FLOOD, FLOOD.layers[0])).toBe(0.4);
    const unset = { ...RENT_LAYER, scale: { ...RENT_LAYER.scale!, opacity: undefined } };
    expect(opacityOf(RENT, unset)).toBe(0.4);
  });

  it("draws a choropleth's borders from its own polygons, a hairline right over its fill", () => {
    expect(specOf(overlayStyleLayers(RENT, RENT_LAYER, true), 'ov-rent_map-outline')).toMatchObject({
      type: 'line',
      source: 'ov-rent_map',
      'source-layer': 'rent_map',
      filter: ['==', ['geometry-type'], 'Polygon'],
      layout: { visibility: 'visible' },
      paint: { 'line-color': 'rgba(60, 60, 90, 0.25)', 'line-width': 0.4 },
    });
    const order = overlayDrawOrder(REGISTRY);
    expect(order.indexOf('ov-rent_map-outline')).toBe(order.indexOf('ov-rent_map-fill') + 1);
    expect(order.indexOf('ov-rent_map-outline')).toBeLessThan(order.indexOf('ov-flood_az-fill'));
    const borderless = { ...RENT_LAYER, scale: { ...RENT_LAYER.scale!, outline: null } };
    expect(specOf(overlayStyleLayers(RENT, borderless, true), 'ov-rent_map-outline')).toBeUndefined();
    expect(overlayStyleLayers(METRO, METRO_LAYER, true).some((l) => l.spec.id.endsWith('-outline'))).toBe(false);
  });

  it('paints a scale layer from the shown variant, grey below zero', () => {
    const fillOf = (variant: string | null) =>
      specOf(overlayStyleLayers(RENT, RENT_LAYER, true, variant), 'ov-rent_map-fill')?.paint?.['fill-color'];
    const value = (prop: string) => ['to-number', ['coalesce', ['get', prop], -1], -1];
    expect(fillOf('vk2')).toEqual([
      'case',
      ['<', value('vk2'), 0], 'rgba(140,140,140,0.35)',
      ['interpolate', ['linear'], value('vk2'), 150, '#eaf0f6', 600, '#1a1a5e'],
    ]);
    expect(JSON.stringify(fillOf(null))).toContain('"vk1"');
  });

  it('points the source at the overlay protocol, versioned for the cache', () => {
    expect(overlaySource(FLOOD)).toEqual({
      type: 'vector',
      tiles: ['overlay://flood/{z}/{x}/{y}?v=100'],
      minzoom: 10,
      maxzoom: 12,
      attribution: 'zdroj',
    });
  });

  it('orders areas, then lines, then points, then labels; within each, sort 1 on top', () => {
    const order = overlayDrawOrder(REGISTRY);
    const at = (id: string) => order.indexOf(id);
    const fills = order.filter((id) => id.endsWith('-fill'));
    expect(Math.max(...fills.map(at))).toBeLessThan(at('ov-rent_map-line'));
    expect(at('ov-flood_az-dash')).toBeLessThan(at('ov-metro-point'));
    expect(at('ov-metro-point')).toBeLessThan(at('ov-flood_az-label'));
    expect(fills).toEqual([
      'ov-rent_map-fill',
      'ov-flood_az-fill',
      'ov-flood_q100-fill',
      'ov-flood_q5-fill',
      'ov-metro-fill',
    ]);
  });

  it('lays a choropleth under every other area, whatever its sort', () => {
    const first = { sections: [{ ...RENT, sort: 0 }, METRO, FLOOD] };
    const fills = overlayDrawOrder(first).filter((id) => id.endsWith('-fill'));
    expect(fills).toEqual([
      'ov-rent_map-fill',
      'ov-flood_az-fill',
      'ov-flood_q100-fill',
      'ov-flood_q5-fill',
      'ov-metro-fill',
    ]);
  });

  it('names the section and layer behind a clicked area', () => {
    expect(overlayFillOwner(REGISTRY, 'ov-flood_q100-fill')).toEqual({ section: FLOOD, layer: FLOOD.layers[1] });
    expect(overlayFillOwner(REGISTRY, 'ov-flood_q100-line')).toBeNull();
  });
});

describe('the overlay:// tile protocol', () => {
  beforeEach(() => vi.mocked(apiGetBlob).mockReset());

  it('parses a tile URL MapLibre built from the source template', () => {
    expect(parseOverlayTileUrl('overlay://metro/12/2210/1388?v=1760436000')).toEqual({
      section: 'metro', z: 12, x: 2210, y: 1388, v: '1760436000',
    });
    expect(parseOverlayTileUrl('overlay://flood/10/552/347')).toMatchObject({ section: 'flood', v: null });
    expect(parseOverlayTileUrl('https://example.com/10/552/347')).toBeNull();
    expect(parseOverlayTileUrl('overlay://flood/10/552')).toBeNull();
  });

  it('fetches the tile through the API transport, version and abort signal included', async () => {
    const bytes = new Uint8Array([0x1a, 0x02, 0x08]).buffer;
    vi.mocked(apiGetBlob).mockResolvedValue({ arrayBuffer: async () => bytes } as Blob);
    const signal = new AbortController().signal;
    const data = await fetchOverlayTile('overlay://metro/12/2210/1388?v=17', signal);
    expect(apiGetBlob).toHaveBeenCalledWith('/map-overlays/metro/12/2210/1388.mvt', { v: '17' }, signal);
    expect(data).toBe(bytes);
  });

  it('draws nothing, quietly, for an empty tile or a signed-out tab', async () => {
    vi.mocked(apiGetBlob).mockResolvedValueOnce({ arrayBuffer: async () => new ArrayBuffer(0) } as Blob);
    expect((await fetchOverlayTile('overlay://metro/9/1/1', new AbortController().signal)).byteLength).toBe(0);
    vi.mocked(apiGetBlob).mockRejectedValueOnce(new ApiError('Not authenticated', 401, null));
    expect((await fetchOverlayTile('overlay://metro/9/1/1', new AbortController().signal)).byteLength).toBe(0);
  });

  it('lets every other failure reach MapLibre', async () => {
    vi.mocked(apiGetBlob).mockRejectedValueOnce(new ApiError('boom', 500, null));
    await expect(fetchOverlayTile('overlay://metro/9/1/1', new AbortController().signal)).rejects.toThrow('boom');
    await expect(fetchOverlayTile('https://nope', new AbortController().signal)).rejects.toThrow();
  });

  it('registers the protocol once, answering with the tile bytes', async () => {
    const bytes = new ArrayBuffer(4);
    vi.mocked(apiGetBlob).mockResolvedValue({ arrayBuffer: async () => bytes } as Blob);
    const addProtocol = vi.fn();
    registerOverlayProtocol(addProtocol);
    registerOverlayProtocol(addProtocol);
    expect(addProtocol).toHaveBeenCalledTimes(1);
    const [name, load] = addProtocol.mock.calls[0];
    expect(name).toBe('overlay');
    await expect(load({ url: 'overlay://kraje/6/34/21?v=1' }, new AbortController())).resolves.toEqual({ data: bytes });
  });
});

describe('overlayPopupHtml', () => {
  it('heads the popup with the section, titles it by name and names the class', () => {
    const html = overlayPopupHtml('Záplavová území', { class: 'q5', name: 'Vltava' }, FLOOD.layers[0]);
    expect(html).toContain('<p class="lp-kicker">Záplavová území</p>');
    expect(html).toContain('<p class="lp-price">Vltava</p>');
    expect(html).toContain('<p class="lp-meta">Q5 – pětiletá voda</p>');
  });

  it('escapes what it interpolates and falls back to the class label for a nameless area', () => {
    expect(overlayPopupHtml('M', { class: 'A', name: '<b>x</b>' }, METRO_LAYER)).toContain('&lt;b&gt;x&lt;/b&gt;');
    const nameless = overlayPopupHtml('Metro', { class: 'D' }, METRO_LAYER);
    expect(nameless).toContain('<p class="lp-price">Metro D – návrh</p>');
    expect(nameless).not.toContain('lp-meta');
  });

  /* Word for word the popup the rent map had before it moved onto this path. */
  it("words a rent-map click as it always read: the place, its kraj and the shown size category's rent", () => {
    const props = { name: 'Brno', kraj: 'Jihomoravský', level: 'obec', vk1: 312, vk2: null };
    const html = overlayPopupHtml('Cenová mapa nájemného', props, RENT_LAYER, null);
    expect(html).toContain('<p class="lp-kicker">Cenová mapa nájemného</p>');
    expect(html).toContain('<p class="lp-price">Brno</p>');
    expect(html).toContain(
      `<p class="lp-meta">Obec: Brno, Jihomoravský, Nájemné referenčního bytu: 312\u00a0${PPM2_UNIT.rent}</p>`,
    );
    expect(overlayPopupHtml('Cenová mapa nájemného', props, RENT_LAYER, 'vk2')).toContain(
      '<p class="lp-meta">Obec: Brno, Jihomoravský, Nájemné referenčního bytu: —</p>',
    );
    expect(overlayPopupHtml('Cenová mapa nájemného', { name: 'Brno', vk2: 1250 }, RENT_LAYER, 'vk2')).toContain(
      `<p class="lp-meta">Obec: Brno, Nájemné referenčního bytu: 1\u00a0250\u00a0${PPM2_UNIT.rent}</p>`,
    );
  });

  it("names any other scale's figure by the shown variant, in its own unit", () => {
    const decibels = { ...RENT_LAYER, scale: { ...RENT_LAYER.scale!, unit: 'dB' } };
    const html = overlayPopupHtml('Hluk', { name: 'Brno', vk2: 55.5 }, decibels, 'vk2');
    expect(html).toContain('<p class="lp-meta">2+kk, 2+1: 55,5\u00a0dB</p>');
  });
});

describe('overlayDataDate', () => {
  it('prefers the publisher revision date, else the Prague day it was fetched', () => {
    expect(overlayDataDate({ source_revised_at: '2025-08-31', fetched_at: '2026-10-14T10:00:00Z' })).toBe('31. 8. 2025');
    expect(overlayDataDate({ source_revised_at: null, fetched_at: '2026-10-13T23:30:00Z' })).toBe('14. 10. 2026');
    expect(overlayDataDate({ source_revised_at: null, fetched_at: null })).toBeNull();
  });
});

/* Just enough of a MapLibre map: an ordered layer list, sources, and the
 * calls the hook makes. */
class FakeMap {
  style: object | undefined = {};
  zoom = 8.6;
  layers: { id: string; type: string; layout: Record<string, unknown>; paint: Record<string, unknown> }[] = [
    { id: 'background', type: 'background', layout: {}, paint: {} },
    { id: 'road-label', type: 'symbol', layout: {}, paint: {} },
    { id: 'city-fill', type: 'fill', layout: {}, paint: {} },
    { id: 'point', type: 'circle', layout: {}, paint: {} },
  ];
  sources = new Map<string, unknown>();
  handlers = new Map<string, () => void>();
  getLayer(id: string) {
    return this.layers.find((l) => l.id === id);
  }
  getLayersOrder() {
    return this.layers.map((l) => l.id);
  }
  addSource(id: string, spec: unknown) {
    this.sources.set(id, spec);
  }
  getSource(id: string) {
    return this.sources.get(id);
  }
  removeSource(id: string) {
    this.sources.delete(id);
  }
  addLayer(spec: LayerSpecification & { layout?: Record<string, unknown>; paint?: Record<string, unknown> }, before?: string) {
    const entry = { id: spec.id, type: spec.type, layout: { ...spec.layout }, paint: { ...spec.paint } };
    const at = before ? this.layers.findIndex((l) => l.id === before) : -1;
    if (at < 0) this.layers.push(entry);
    else this.layers.splice(at, 0, entry);
  }
  removeLayer(id: string) {
    this.layers = this.layers.filter((l) => l.id !== id);
  }
  setLayoutProperty(id: string, name: string, value: unknown) {
    this.getLayer(id)!.layout[name] = value;
  }
  setPaintProperty(id: string, name: string, value: unknown) {
    this.getLayer(id)!.paint[name] = value;
  }
  getZoom() {
    return this.zoom;
  }
  on(type: string, fn: () => void) {
    this.handlers.set(type, fn);
  }
  off(type: string) {
    this.handlers.delete(type);
  }
  ids() {
    return this.layers.map((l) => l.id);
  }
  visibility(id: string) {
    return this.getLayer(id)?.layout.visibility;
  }
}

const asMap = (fake: FakeMap) => fake as unknown as MaplibreMap;

describe('useMapOverlays', () => {
  const mount = (fake: FakeMap, selection: string, registry: MapOverlayRegistry = REGISTRY, beforeId = 'city-fill') =>
    renderHook(
      (p: { selection: string; registry: MapOverlayRegistry }) =>
        useMapOverlays(asMap(fake), true, p.registry, p.selection, beforeId),
      { initialProps: { selection, registry } },
    );

  it('adds nothing until a layer is selected', () => {
    const fake = new FakeMap();
    mount(fake, '');
    expect(fake.sources.size).toBe(0);
    expect(fake.ids().some((id) => id.startsWith('ov-'))).toBe(false);
  });

  it('draws a selected section under beforeId, areas below lines below points below labels', () => {
    const fake = new FakeMap();
    mount(fake, 'metro');
    expect(fake.sources.get('ov-metro')).toEqual(overlaySource(METRO));
    expect(fake.ids()).toEqual([
      'background', 'road-label',
      'ov-metro-fill', 'ov-metro-line', 'ov-metro-dash', 'ov-metro-point', 'ov-metro-label',
      'city-fill', 'point',
    ]);
    expect(fake.visibility('ov-metro-line')).toBe('visible');
  });

  it('keeps the global order whatever order sections are switched on in', () => {
    const fake = new FakeMap();
    const { rerender } = mount(fake, 'metro');
    rerender({ selection: 'flood_q5,metro', registry: REGISTRY });
    const ov = fake.ids().filter((id) => id.startsWith('ov-'));
    const expected = overlayDrawOrder(REGISTRY).filter((id) => ov.includes(id));
    expect(ov).toEqual(expected);
    expect(fake.visibility('ov-flood_q5-fill')).toBe('visible');
    expect(fake.visibility('ov-flood_q100-fill')).toBe('none');
    expect(fake.ids().indexOf('city-fill')).toBe(fake.ids().length - 2);
  });

  it('slides a choropleth switched on later under the areas already drawn, whatever its sort', () => {
    const registry = { sections: [{ ...RENT, sort: 0 }, METRO, FLOOD] };
    const fake = new FakeMap();
    const { rerender } = mount(fake, 'flood_q5,metro', registry);
    rerender({ selection: 'flood_q5,metro,rent_map', registry });
    const ov = fake.ids().filter((id) => id.startsWith('ov-'));
    expect(ov.slice(0, 3)).toEqual(['ov-rent_map-fill', 'ov-rent_map-outline', 'ov-flood_az-fill']);
    expect(ov).toEqual(overlayDrawOrder(registry).filter((id) => ov.includes(id)));
  });

  it('hides rather than removes a deselected layer, so switching back costs no fetch', () => {
    const fake = new FakeMap();
    const { rerender } = mount(fake, 'metro');
    rerender({ selection: '', registry: REGISTRY });
    expect(fake.sources.has('ov-metro')).toBe(true);
    expect(fake.visibility('ov-metro-fill')).toBe('none');
    expect(fake.visibility('ov-metro-label')).toBe('none');
  });

  it('repaints only the fill when the variant changes', () => {
    const fake = new FakeMap();
    const { rerender } = mount(fake, 'rent_map:vk2');
    expect(JSON.stringify(fake.getLayer('ov-rent_map-fill')!.paint['fill-color'])).toContain('"vk2"');
    expect(fake.getLayer('ov-rent_map-fill')!.paint['fill-opacity']).toBe(0.62);
    expect(fake.visibility('ov-rent_map-outline')).toBe('visible');
    rerender({ selection: 'rent_map', registry: REGISTRY });
    expect(JSON.stringify(fake.getLayer('ov-rent_map-fill')!.paint['fill-color'])).toContain('"vk1"');
    expect(fake.sources.size).toBe(1);
  });

  it('redraws a section whose registry version moved (a new ingest)', () => {
    const fake = new FakeMap();
    const { rerender } = mount(fake, 'metro');
    const next = { sections: [{ ...METRO, version: 200 }, FLOOD, RENT] };
    rerender({ selection: 'metro', registry: next });
    expect(fake.sources.get('ov-metro')).toMatchObject({ tiles: ['overlay://metro/{z}/{x}/{y}?v=200'] });
    expect(fake.ids().filter((id) => id === 'ov-metro-fill')).toHaveLength(1);
  });

  it('falls back to the first basemap label layer when beforeId is missing', () => {
    const fake = new FakeMap();
    mount(fake, 'kraje,metro', REGISTRY, 'no-such-layer');
    expect(fake.ids().indexOf('ov-metro-label')).toBe(fake.ids().indexOf('road-label') - 1);
  });

  it('removes its layers and sources on unmount, unless the map is already gone', () => {
    const fake = new FakeMap();
    const { unmount } = mount(fake, 'flood_q5,metro');
    unmount();
    expect(fake.sources.size).toBe(0);
    expect(fake.ids()).toEqual(['background', 'road-label', 'city-fill', 'point']);

    const removed = new FakeMap();
    const second = mount(removed, 'metro');
    removed.style = undefined;
    expect(() => second.unmount()).not.toThrow();
  });
});

describe('useMapZoom', () => {
  it('reports the floored zoom once ready, and again after each zoom', () => {
    const fake = new FakeMap();
    const { result, rerender } = renderHook((p: { ready: boolean }) => useMapZoom(asMap(fake), p.ready), {
      initialProps: { ready: false },
    });
    expect(result.current).toBeNull();
    rerender({ ready: true });
    expect(result.current).toBe(8);
    fake.zoom = 10.2;
    act(() => fake.handlers.get('zoomend')!());
    expect(result.current).toBe(10);
  });
});
