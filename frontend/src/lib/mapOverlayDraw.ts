/* Drawing the reference overlays (docs/design/map-overlays.md) on any MapLibre
 * map: the `overlay://` tile protocol, the style layers one registry layer
 * needs, the one hook that keeps a map in step with the selection, and the
 * click popup. Only the lazily loaded maps import this; maplibre-gl is
 * imported for its TYPES only, and basemap.ts hands in `addProtocol`. */

import { useEffect, useRef, useState } from 'react';
import type {
  AddProtocolAction,
  FilterSpecification,
  LayerSpecification,
  Map as MaplibreMap,
  VectorSourceSpecification,
} from 'maplibre-gl';

import { ApiError, apiGetBlob } from './api';
import { escapeHtml } from './escapeHtml';
import {
  activeVariant,
  layersOf,
  parseLayers,
  type LayerSelection,
  type MapOverlayRegistry,
  type OverlayLayer,
  type OverlayLegendEntry,
  type OverlayScale,
  type OverlaySection,
} from './mapOverlays';
import { PPM2_UNIT } from './measure';

/* ------------------------------------------------------------- tile protocol */

export interface OverlayTileRequest {
  section: string;
  z: number;
  x: number;
  y: number;
  v: string | null;
}

const TILE_URL = /^overlay:\/\/([^/?#]+)\/(\d+)\/(\d+)\/(\d+)(?:\?(.*))?$/;

export function parseOverlayTileUrl(url: string): OverlayTileRequest | null {
  const m = TILE_URL.exec(url);
  if (!m) return null;
  try {
    return {
      section: decodeURIComponent(m[1]),
      z: Number(m[2]),
      x: Number(m[3]),
      y: Number(m[4]),
      v: new URLSearchParams(m[5] ?? '').get('v'),
    };
  } catch {
    return null;
  }
}

/* One tile through the API transport (JWT, deadline, ApiError). An empty tile
 * (204) and a signed-out tab (401) both draw nothing rather than raising a
 * MapLibre error per tile. */
export async function fetchOverlayTile(url: string, signal: AbortSignal): Promise<ArrayBuffer> {
  const t = parseOverlayTileUrl(url);
  if (!t) throw new Error(`Not an overlay tile URL: ${url}`);
  try {
    const blob = await apiGetBlob(
      `/map-overlays/${encodeURIComponent(t.section)}/${t.z}/${t.x}/${t.y}.mvt`,
      { v: t.v },
      signal,
    );
    return await blob.arrayBuffer();
  } catch (err) {
    if (err instanceof ApiError && err.status === 401) return new ArrayBuffer(0);
    throw err;
  }
}

let protocolRegistered = false;

export function registerOverlayProtocol(
  addProtocol: (name: string, load: AddProtocolAction) => void,
): void {
  if (protocolRegistered) return;
  protocolRegistered = true;
  addProtocol('overlay', async (params, abortController) => ({
    data: await fetchOverlayTile(params.url, abortController.signal),
  }));
}

/* ------------------------------------------------------------- style layers */

/* Shared by the paint and the legend swatches, so the two cannot drift. */
export const OVERLAY_FILL_OPACITY = 0.4;
export const OVERLAY_LINE_WIDTH = 2.2;
export const OVERLAY_DASH: [number, number] = [2, 1.5];
export const OVERLAY_POINT_RADIUS = 4;
const UNMATCHED_COLOR = '#8c9196';
const LABEL_COLOR = '#1f2a28';

type FillLayerSpec = Extract<LayerSpecification, { type: 'fill' }>;
type Expression = Extract<NonNullable<NonNullable<FillLayerSpec['paint']>['fill-color']>, unknown[]>;
type ColorValue = Expression | string;

const CLASS: Expression = ['to-string', ['get', 'class']];
const IS_POLYGON: Expression = ['==', ['geometry-type'], 'Polygon'];
const IS_LINE: Expression = ['==', ['geometry-type'], 'LineString'];
const IS_POINT: Expression = ['==', ['geometry-type'], 'Point'];

export const overlaySourceId = (sectionKey: string): string => `ov-${sectionKey}`;
export const overlayFillId = (layerKey: string): string => `ov-${layerKey}-fill`;

export const overlaySource = (section: OverlaySection): VectorSourceSpecification => ({
  type: 'vector',
  tiles: [`overlay://${encodeURIComponent(section.key)}/{z}/{x}/{y}?v=${section.version}`],
  minzoom: section.minzoom,
  maxzoom: section.maxzoom,
  attribution: section.attribution,
});

function classColor(legend: readonly OverlayLegendEntry[]): ColorValue {
  const pairs: string[] = [];
  const seen = new Set<string>();
  for (const entry of legend) {
    const cls = String(entry.class);
    if (seen.has(cls)) continue;
    seen.add(cls);
    pairs.push(cls, entry.color);
  }
  if (pairs.length === 0) return UNMATCHED_COLOR;
  const [firstClass, firstColor, ...rest] = pairs;
  return ['match', CLASS, firstClass, firstColor, ...rest, UNMATCHED_COLOR];
}

function scaleColor(scale: OverlayScale, prop: string): ColorValue {
  const stops = [...scale.stops].sort((a, b) => a[0] - b[0]);
  if (stops.length === 0) return scale.null_color;
  const value: Expression = ['to-number', ['coalesce', ['get', prop], -1], -1];
  return [
    'case',
    ['<', value, 0], scale.null_color,
    ['interpolate', ['linear'], value, ...stops.flat()],
  ];
}

/* A variant change repaints only this — the tiles carry every variant's value. */
export const overlayFillColor = (layer: OverlayLayer, variantKey: string | null): ColorValue =>
  layer.scale
    ? scaleColor(layer.scale, activeVariant(layer, variantKey)?.prop ?? layer.scale.prop)
    : classColor(layer.legend);

/* Bottom to top: areas, lines, points, labels — no area ever covers a line. */
type Tier = 0 | 1 | 2 | 3;

export interface OverlayStyleLayer {
  tier: Tier;
  spec: LayerSpecification;
}

/* Every style layer one registry layer needs. Features are split by geometry
 * type, not by a declared kind, so one layer may mix lines and points (metro). */
export function overlayStyleLayers(
  section: OverlaySection,
  layer: OverlayLayer,
  visible: boolean,
  variantKey: string | null = null,
): OverlayStyleLayer[] {
  const src = { source: overlaySourceId(section.key), 'source-layer': layer.key };
  const visibility = visible ? 'visible' : 'none';
  const color = classColor(layer.legend);
  const dashed = [...new Set(layer.legend.filter((e) => e.dash).map((e) => String(e.class)))];
  /* A line is dashed by its class (the legend) OR by its own `dash` flag — the metro B
   * extension is class B (solid in the legend) and a proposal (dashed), per feature. */
  const isDashed: Expression = [
    'any',
    ['in', CLASS, ['literal', dashed]],
    ['==', ['coalesce', ['get', 'dash'], false], true],
  ];
  const lineFilter: FilterSpecification = ['all', IS_LINE, ['!', isDashed]];
  const out: OverlayStyleLayer[] = [
    {
      tier: 0,
      spec: {
        ...src,
        id: overlayFillId(layer.key),
        type: 'fill',
        filter: IS_POLYGON,
        layout: { visibility },
        paint: {
          'fill-color': overlayFillColor(layer, variantKey),
          'fill-opacity': layer.scale?.opacity ?? OVERLAY_FILL_OPACITY,
          'fill-antialias': false,
        },
      },
    },
    {
      tier: 1,
      spec: {
        ...src,
        id: `ov-${layer.key}-line`,
        type: 'line',
        filter: lineFilter,
        layout: { visibility, 'line-join': 'round', 'line-cap': 'round' },
        paint: { 'line-color': color, 'line-width': OVERLAY_LINE_WIDTH },
      },
    },
  ];
  out.push({
    tier: 1,
    spec: {
      ...src,
      id: `ov-${layer.key}-dash`,
      type: 'line',
      filter: ['all', IS_LINE, isDashed],
      layout: { visibility, 'line-join': 'round' },
      paint: {
        'line-color': color,
        'line-width': OVERLAY_LINE_WIDTH,
        'line-dasharray': OVERLAY_DASH,
      },
    },
  });
  out.push(
    {
      tier: 2,
      spec: {
        ...src,
        id: `ov-${layer.key}-point`,
        type: 'circle',
        filter: IS_POINT,
        layout: { visibility },
        paint: {
          'circle-radius': OVERLAY_POINT_RADIUS,
          'circle-color': color,
          'circle-stroke-color': '#ffffff',
          'circle-stroke-width': 1.5,
        },
      },
    },
    {
      tier: 3,
      spec: {
        ...src,
        id: `ov-${layer.key}-label`,
        type: 'symbol',
        filter: ['all', IS_POINT, ['has', 'label']],
        layout: {
          visibility,
          'text-field': ['get', 'label'],
          'text-font': ['Noto Sans Regular'],
          'text-size': 11,
          'text-offset': [0, 0.8],
          'text-anchor': 'top',
          'text-optional': true,
        },
        paint: {
          'text-color': LABEL_COLOR,
          'text-halo-color': '#ffffff',
          'text-halo-width': 1.4,
        },
      },
    },
  );
  const outline = layer.scale?.outline;
  if (outline) {
    /* A choropleth's borders: each place's own rings, a hairline over its fill. */
    out.push({
      tier: 0,
      spec: {
        ...src,
        id: `ov-${layer.key}-outline`,
        type: 'line',
        filter: IS_POLYGON,
        layout: { visibility },
        paint: { 'line-color': outline.color, 'line-width': outline.width },
      },
    });
  }
  return out;
}

/* Every overlay style-layer id, bottom to top: tier by tier, and within a tier
 * sections and layers in REVERSE sort, so sort 1 draws on top — except that a
 * choropleth's area (a scale layer's fill and its borders) is the floor under
 * every other area: it covers every place, so anything beneath would wash out. */
export function overlayDrawOrder(registry: MapOverlayRegistry): string[] {
  const bands: string[][] = [[], [], [], [], []];
  const descending = <T extends { sort: number }>(xs: readonly T[]): T[] =>
    [...xs].sort((a, b) => b.sort - a.sort);
  for (const section of descending(registry.sections)) {
    for (const layer of descending(section.layers)) {
      for (const { tier, spec } of overlayStyleLayers(section, layer, false)) {
        bands[tier === 0 && layer.scale ? 0 : tier + 1].push(spec.id);
      }
    }
  }
  return bands.flat();
}

export const overlayFillIds = (registry: MapOverlayRegistry): string[] =>
  registry.sections.flatMap((s) => s.layers.map((l) => overlayFillId(l.key)));

export function overlayFillOwner(
  registry: MapOverlayRegistry,
  styleLayerId: string,
): { section: OverlaySection; layer: OverlayLayer } | null {
  for (const section of registry.sections) {
    for (const layer of section.layers) {
      if (overlayFillId(layer.key) === styleLayerId) return { section, layer };
    }
  }
  return null;
}

/* --------------------------------------------------------------------- hook */

interface DrawnSection {
  version: number;
  layerIds: Map<string, string[]>;
  fillVariant: Map<string, string | null>;
}

/* Where an id goes so the map keeps overlayDrawOrder whatever order sections
 * were switched on in: under the nearest overlay above it, else under `fallback`. */
function anchorAbove(
  map: MaplibreMap,
  order: readonly string[],
  id: string,
  fallback: string | undefined,
): string | undefined {
  for (let i = order.indexOf(id) + 1; i < order.length; i++) {
    if (map.getLayer(order[i])) return order[i];
  }
  return fallback;
}

const firstBasemapSymbolId = (map: MaplibreMap): string | undefined =>
  map.getLayersOrder().find((id) => !id.startsWith('ov-') && map.getLayer(id)?.type === 'symbol');

function drawSection(
  map: MaplibreMap,
  section: OverlaySection,
  sel: LayerSelection,
  order: readonly string[],
  fallback: string | undefined,
): DrawnSection {
  map.addSource(overlaySourceId(section.key), overlaySource(section));
  const drawn: DrawnSection = { version: section.version, layerIds: new Map(), fillVariant: new Map() };
  for (const layer of section.layers) {
    const variant = sel.get(layer.key) ?? null;
    const styleLayers = overlayStyleLayers(section, layer, sel.has(layer.key), variant);
    for (const { spec } of styleLayers) map.addLayer(spec, anchorAbove(map, order, spec.id, fallback));
    drawn.layerIds.set(layer.key, styleLayers.map((s) => s.spec.id));
    drawn.fillVariant.set(layer.key, variant);
  }
  return drawn;
}

function syncSection(map: MaplibreMap, section: OverlaySection, sel: LayerSelection, drawn: DrawnSection): void {
  for (const layer of section.layers) {
    const visible = sel.has(layer.key);
    for (const id of drawn.layerIds.get(layer.key) ?? []) {
      if (map.getLayer(id)) map.setLayoutProperty(id, 'visibility', visible ? 'visible' : 'none');
    }
    const variant = sel.get(layer.key) ?? null;
    const fillId = overlayFillId(layer.key);
    if (layer.scale && visible && drawn.fillVariant.get(layer.key) !== variant && map.getLayer(fillId)) {
      map.setPaintProperty(fillId, 'fill-color', overlayFillColor(layer, variant));
      drawn.fillVariant.set(layer.key, variant);
    }
  }
}

function eraseSection(map: MaplibreMap, sectionKey: string, drawn: DrawnSection): void {
  for (const ids of drawn.layerIds.values()) {
    for (const id of ids) if (map.getLayer(id)) map.removeLayer(id);
  }
  if (map.getSource(overlaySourceId(sectionKey))) map.removeSource(overlaySourceId(sectionKey));
}

/* Draws the selected reference layers on `map` under `beforeId` (fallback: the
 * basemap's first label layer). A section's source and style layers are added
 * the first time any of its layers is selected and then only shown or hidden;
 * a new `version` redraws it. `ready` = the map's style has loaded. */
export function useMapOverlays(
  map: MaplibreMap | null,
  ready: boolean,
  registry: MapOverlayRegistry | undefined,
  selection: string,
  beforeId: string,
): void {
  const drawnRef = useRef(new Map<string, DrawnSection>());

  useEffect(() => {
    const drawn = drawnRef.current;
    return () => {
      /* The owner usually removes the map first, and a removed map has no style. */
      if (map?.style) for (const [key, d] of drawn) eraseSection(map, key, d);
      drawn.clear();
    };
  }, [map]);

  useEffect(() => {
    if (!map || !ready || !registry) return;
    const drawn = drawnRef.current;
    const sel = layersOf(parseLayers(selection), registry);
    const versions = new Map(registry.sections.map((s) => [s.key, s.version]));
    for (const [key, d] of drawn) {
      if (versions.get(key) === d.version) continue;
      eraseSection(map, key, d);
      drawn.delete(key);
    }
    const order = overlayDrawOrder(registry);
    const fallback = map.getLayer(beforeId) ? beforeId : firstBasemapSymbolId(map);
    for (const section of registry.sections) {
      const d = drawn.get(section.key);
      if (d) syncSection(map, section, sel, d);
      else if (section.layers.some((l) => sel.has(l.key))) {
        drawn.set(section.key, drawSection(map, section, sel, order, fallback));
      }
    }
  }, [map, ready, registry, selection, beforeId]);
}

/* The map's zoom, floored: the overlay hint compares it with an integer
 * minzoom, and the pin labels with an integer threshold. */
export function useMapZoom(map: MaplibreMap | null, ready: boolean): number | null {
  const [zoom, setZoom] = useState<number | null>(null);
  useEffect(() => {
    if (!map || !ready) return;
    const sync = () => setZoom(Math.floor(map.getZoom()));
    sync();
    map.on('zoomend', sync);
    return () => {
      map.off('zoomend', sync);
    };
  }, [map, ready]);
  return zoom;
}

/* -------------------------------------------------------------------- text */

const czNumber = new Intl.NumberFormat('cs-CZ', { maximumFractionDigits: 1 });

export const formatOverlayNumber = (n: number): string => czNumber.format(n);

/* What a scale's unit token reads as: the unit after a figure, the legend's
 * caption, and the popup's words for the place and the figure. `rent_per_m2`
 * is the MF reference rent — the shared monthly-rent unit (rule 23) and the
 * words its map has always used; any other token is just its own unit. */
export interface OverlayScaleWords {
  unit: string | null;
  caption: string | null;
  place: string | null;
  value: string | null;
}

export const overlayScaleWords = (unit: string | null | undefined): OverlayScaleWords =>
  unit === 'rent_per_m2'
    ? { unit: PPM2_UNIT.rent, caption: `Nájemné (${PPM2_UNIT.rent})`, place: 'Obec', value: 'Nájemné referenčního bytu' }
    : { unit: unit || null, caption: unit || null, place: null, value: null };

const dateIn = (timeZone: string) =>
  new Intl.DateTimeFormat('cs-CZ', { day: 'numeric', month: 'numeric', year: 'numeric', timeZone });
const CALENDAR_DAY = dateIn('UTC');
const PRAGUE_DAY = dateIn('Europe/Prague');

/* "31. 8. 2025": the publisher's revision date, else the day we fetched it. */
export function overlayDataDate(
  section: Pick<OverlaySection, 'source_revised_at' | 'fetched_at'>,
): string | null {
  const day = /^(\d{4})-(\d{2})-(\d{2})/.exec(section.source_revised_at ?? '');
  if (day) return CALENDAR_DAY.format(Date.UTC(Number(day[1]), Number(day[2]) - 1, Number(day[3])));
  const fetched = section.fetched_at ? new Date(section.fetched_at) : null;
  return fetched && !Number.isNaN(fetched.getTime()) ? PRAGUE_DAY.format(fetched) : null;
}

const textOf = (v: unknown): string | null =>
  typeof v === 'string' && v.trim() !== '' ? v : typeof v === 'number' ? String(v) : null;

/* The popup for a click on an overlay area: kicker = section, title = the
 * feature's name, meta = its class label — or, on a scale layer, the place and
 * the shown variant's figure with its unit ("—" when the place has none): for
 * the rent map "Obec: Brno, Jihomoravský, Nájemné referenčního bytu: 312 Kč/m²/měs". */
export function overlayPopupHtml(
  sectionLabel: string,
  props: Record<string, unknown>,
  layer: OverlayLayer,
  variantKey: string | null = null,
): string {
  const cls = textOf(props.class);
  const classLabel = cls == null ? null : layer.legend.find((e) => String(e.class) === cls)?.label ?? null;
  const title = textOf(props.name) ?? textOf(props.label) ?? classLabel ?? layer.label;
  let meta = classLabel && classLabel !== title ? classLabel : null;
  if (layer.scale) {
    const words = overlayScaleWords(layer.scale.unit);
    const variant = activeVariant(layer, variantKey);
    const raw = props[variant?.prop ?? layer.scale.prop];
    const value = typeof raw === 'number' ? raw : typeof raw === 'string' && raw.trim() !== '' ? Number(raw) : NaN;
    const figure = Number.isFinite(value) && value >= 0
      ? `${formatOverlayNumber(value)}${words.unit ? `\u00a0${words.unit}` : ''}`
      : '—';
    const kraj = textOf(props.kraj);
    const place = words.place && textOf(props.name) ? `${words.place}: ${title}${kraj ? `, ${kraj}` : ''}, ` : '';
    const named = words.value ?? variant?.label;
    meta = `${place}${named ? `${named}: ` : ''}${figure}`;
  }
  return `
    <div class="lp">
      <p class="lp-kicker">${escapeHtml(sectionLabel)}</p>
      <div class="lp-row">
        <p class="lp-price">${escapeHtml(title)}</p>
      </div>
      ${meta ? `<p class="lp-meta">${escapeHtml(meta)}</p>` : ''}
    </div>
  `;
}
