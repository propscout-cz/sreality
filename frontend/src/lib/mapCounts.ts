import { useEffect, useState } from 'react';
import type maplibregl from 'maplibre-gl';

/* ONE count rule for the Browse map (docs/design/map-counts.md).
 *
 * A count belongs to a world tile: the fixed square grid every web map uses,
 * level L cutting the world into 2^L x 2^L squares. MapLibre's world is 512 px
 * wide at zoom 0, so a level-L tile is 512 * 2^(zoom - L) px on screen and
 * `countLevel` keeps the pitch between 90 and 181 px. The same formula groups a
 * point cohort in the browser and, since migration 602, the server's cells
 * (which arrive as tile centres at the finest level that fits 1 024 tiles);
 * tests/fixtures/map_tile_lattice.json pins both implementations to one truth. */

const MAX_LAT = 85.05112878;

/* At this zoom and above every property of a point cohort is its own pin (and
 * carries its price label); below it, tiles holding two or more share one count
 * mark. */
export const PIN_ZOOM = 14;

export interface CountRule {
  /* The tile level marks are grouped at: round(zoom) + 2, a 90 to 181 px pitch. */
  level: number;
  /* Whether a point cohort is drawn pin by pin (zoom >= PIN_ZOOM). */
  pins: boolean;
}

export const countRule = (zoom: number): CountRule => ({
  level: Math.round(zoom) + 2,
  pins: zoom >= PIN_ZOOM,
});

/* The source and layer id of the count marks. */
export const COUNTS = 'counts';

export interface Tile { x: number; y: number }

const clamp = (v: number, lo: number, hi: number): number => Math.min(hi, Math.max(lo, v));

export const tileOf = (lng: number, lat: number, level: number): Tile => {
  const n = 2 ** level;
  const s = Math.sin(clamp(lat, -MAX_LAT, MAX_LAT) * Math.PI / 180);
  const x = Math.floor((lng + 180) / 360 * n);
  const y = Math.floor((1 - Math.log((1 + s) / (1 - s)) / (2 * Math.PI)) / 2 * n);
  return { x: clamp(x, 0, n - 1), y: clamp(y, 0, n - 1) };
};

const round5 = (v: number): number => Math.round(v * 1e5) / 1e5;

export const tileCentre = (x: number, y: number, level: number): [number, number] => {
  const n = 2 ** level;
  const lng = (x + 0.5) / n * 360 - 180;
  const lat = Math.atan(Math.sinh(Math.PI * (1 - 2 * (y + 0.5) / n))) * 180 / Math.PI;
  return [round5(lng), round5(lat)];
};

/* Numeric feature id for a tile, unique within a level (x * 2^L + y < 2^53 for
 * every level MapLibre reaches), so setFeatureState could address it. */
export const tileKey = (t: Tile, level: number): number => t.x * 2 ** level + t.y;

export interface CountInput {
  lng: number;
  lat: number;
  /* Properties this input stands for: 1 for a point, the cell's count for a cell. */
  n: number;
  /* The property's id when the input IS one property (a point); absent for a cell. */
  id?: number;
}

export interface CountMark {
  key: number;
  lng: number;
  lat: number;
  n: number;
  /* Member property ids, known only when the inputs were points. */
  ids: number[];
}

export interface TileGrouping<T extends CountInput> {
  marks: CountMark[];
  /* Inputs that are one known property alone in their tile: drawn as that pin. */
  lone: T[];
}

/* The one rule: a tile holding exactly one known property shows that property;
 * any other tile shows its count at the tile centre. Totals are conserved
 * (sum of marks' n + lone.length == sum of inputs' n) and tiles nest, so
 * regrouping a result at a coarser level equals grouping the inputs there. */
export function groupByTile<T extends CountInput>(inputs: readonly T[], level: number): TileGrouping<T> {
  const byKey = new Map<number, { tile: Tile; n: number; ids: number[]; members: T[] }>();
  for (const p of inputs) {
    const tile = tileOf(p.lng, p.lat, level);
    const key = tileKey(tile, level);
    let g = byKey.get(key);
    if (!g) {
      g = { tile, n: 0, ids: [], members: [] };
      byKey.set(key, g);
    }
    g.n += p.n;
    if (p.id != null) g.ids.push(p.id);
    g.members.push(p);
  }
  const marks: CountMark[] = [];
  const lone: T[] = [];
  for (const [key, g] of byKey) {
    if (g.n === 1 && g.members.length === 1 && g.members[0].id != null) {
      lone.push(g.members[0]);
      continue;
    }
    const [lng, lat] = tileCentre(g.tile.x, g.tile.y, level);
    marks.push({ key, lng, lat, n: g.n, ids: g.ids });
  }
  return { marks, lone };
}

export type CountFC = GeoJSON.FeatureCollection<GeoJSON.Point, { n: number }>;

export const toCountFeatureCollection = (marks: readonly CountMark[]): CountFC => ({
  type: 'FeatureCollection',
  features: marks.map((m) => ({
    type: 'Feature',
    id: m.key,
    geometry: { type: 'Point', coordinates: [m.lng, m.lat] },
    properties: { n: m.n },
  })),
});

/* The rule for the map's current zoom, re-evaluated on every 'zoom' event (not
 * only zoomend) so marks regroup mid-gesture instead of crowding until the
 * wheel stops; the state only changes when the rule's answer does. Null until
 * the map is ready. */
export function useCountRule(map: maplibregl.Map | null, ready: boolean): CountRule | null {
  const [rule, setRule] = useState<CountRule | null>(null);
  useEffect(() => {
    if (!ready || !map) return;
    const sync = () => {
      const next = countRule(map.getZoom());
      setRule((prev) => (prev && prev.level === next.level && prev.pins === next.pins ? prev : next));
    };
    sync();
    map.on('zoom', sync);
    return () => {
      map.off('zoom', sync);
    };
  }, [map, ready]);
  return rule;
}

/* The mark: the count inside a dark pill, drawn as ONE symbol so the number and
 * its background are placed, collided and hit-tested together. The pill is a
 * stretchable image (a 9-slice rounded rectangle) that `icon-text-fit` sizes to
 * the number, so a five-digit count costs about 50 x 17 px against a 90 px
 * minimum tile pitch. Our marks never overlap each other (one per tile, at the
 * centre), so overlap is allowed; placement is NOT ignored, so basemap labels
 * still step aside for them. */
export const COUNT_INK = '#2f5750';
export const COUNT_PILL_IMAGE = 'count-pill';
const PILL_H = 18;
const PILL_W = 24;
const PILL_R = 8;
const PILL_SCALE = 2;

export function addCountPillImage(map: maplibregl.Map): void {
  if (map.hasImage(COUNT_PILL_IMAGE)) return;
  const canvas = document.createElement('canvas');
  canvas.width = PILL_W * PILL_SCALE;
  canvas.height = PILL_H * PILL_SCALE;
  const ctx = canvas.getContext('2d');
  if (!ctx) return;
  ctx.scale(PILL_SCALE, PILL_SCALE);
  const [x, y, w, h, r] = [0.5, 0.5, PILL_W - 1, PILL_H - 1, PILL_R - 0.5];
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
  ctx.fillStyle = COUNT_INK;
  ctx.fill();
  ctx.strokeStyle = '#ffffff';
  ctx.lineWidth = 1;
  ctx.stroke();
  const s = PILL_SCALE;
  map.addImage(COUNT_PILL_IMAGE, ctx.getImageData(0, 0, canvas.width, canvas.height), {
    pixelRatio: s,
    stretchX: [[PILL_R * s, (PILL_W - PILL_R) * s]],
    stretchY: [[PILL_R * s, (PILL_H - PILL_R) * s]],
    content: [PILL_R * s, PILL_R * s, (PILL_W - PILL_R) * s, (PILL_H - PILL_R) * s],
  });
}

export const countLayer = (): maplibregl.SymbolLayerSpecification => ({
  id: COUNTS,
  type: 'symbol',
  source: COUNTS,
  layout: {
    'icon-image': COUNT_PILL_IMAGE,
    'icon-text-fit': 'both',
    'icon-text-fit-padding': [3, 6, 3, 6],
    'icon-allow-overlap': true,
    'icon-ignore-placement': false,
    'text-field': ['number-format', ['get', 'n'], { locale: 'cs-CZ' }],
    'text-font': ['Noto Sans Bold'],
    'text-size': 11,
    'text-allow-overlap': true,
    'text-ignore-placement': false,
  },
  paint: {
    'text-color': '#ffffff',
  },
});
