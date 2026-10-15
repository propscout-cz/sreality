import { useEffect, useState } from 'react';
import type maplibregl from 'maplibre-gl';
import type { MapBounds } from '@/lib/filters';
import type { MapCounts, MapRow } from '@/lib/queries';

/* ONE count rule for the Browse map (docs/design/map-counts.md). A count belongs to a
 * world tile (level L cuts the world into 2^L x 2^L squares; MapLibre's world is 512 px
 * wide at zoom 0, so a level-L tile is 512 * 2^(zoom - L) px). The same formula groups a
 * point cohort in the browser and the server's cells (migration 602, tile centres at the
 * finest level that fits 1 024 tiles); tests/fixtures/map_tile_lattice.json pins both. */

const MAX_LAT = 85.05112878;

/* At this zoom and above every property of a point cohort is its own pin (with its price
 * label); below it, tiles holding two or more share one count mark. */
export const PIN_ZOOM = 14;
/* The source and layer id of the count marks. */
export const COUNTS = 'counts';
/* The data ink: the count pill's fill and the price labels' text. */
export const COUNT_INK = '#2f5750';

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

const clamp = (v: number, lo: number, hi: number): number => Math.min(hi, Math.max(lo, v));
const round5 = (v: number): number => Math.round(v * 1e5) / 1e5;

/* Web Mercator in tile units at `level` (fractional; floor it for the tile). */
const mercator = (lng: number, lat: number, level: number): [number, number] => {
  const n = 2 ** level;
  const s = Math.sin(clamp(lat, -MAX_LAT, MAX_LAT) * Math.PI / 180);
  return [(lng + 180) / 360 * n, (1 - Math.log((1 + s) / (1 - s)) / (2 * Math.PI)) / 2 * n];
};

const unmercator = (x: number, y: number, level: number): [number, number] => {
  const n = 2 ** level;
  const lng = x / n * 360 - 180;
  const lat = Math.atan(Math.sinh(Math.PI * (1 - 2 * y / n))) * 180 / Math.PI;
  return [round5(lng), round5(lat)];
};

interface Tile { x: number; y: number }

export const tileOf = (lng: number, lat: number, level: number): Tile => {
  const n = 2 ** level;
  const [x, y] = mercator(lng, lat, level);
  return { x: clamp(Math.floor(x), 0, n - 1), y: clamp(Math.floor(y), 0, n - 1) };
};

export const tileCentre = (x: number, y: number, level: number): [number, number] =>
  unmercator(x + 0.5, y + 0.5, level);

/* A mark sits at the centre of the part of its tile that is in view, so a tile cut by
 * the window's edge (it holds only its in-view rows, the viewport being a filter) still
 * draws its count on screen. A fully visible tile's mark is the tile centre. */
const markPosition = (t: Tile, level: number, view: MapBounds | null): [number, number] => {
  if (!view) return tileCentre(t.x, t.y, level);
  const [vx0, vy0] = mercator(view.west, view.north, level);
  const [vx1, vy1] = mercator(view.east, view.south, level);
  const x0 = Math.max(t.x, vx0);
  const x1 = Math.min(t.x + 1, vx1);
  const y0 = Math.max(t.y, vy0);
  const y1 = Math.min(t.y + 1, vy1);
  if (!(x0 < x1 && y0 < y1)) return tileCentre(t.x, t.y, level);
  return unmercator((x0 + x1) / 2, (y0 + y1) / 2, level);
};

/* Numeric feature id: the tile and its level (x * 2^L + y < 2^45 at every level MapLibre
 * reaches, times 32 levels), so a stale feature from another level never resolves. */
const tileKey = (t: Tile, level: number): number => (t.x * 2 ** level + t.y) * 32 + level;

interface CountInput {
  lng: number;
  lat: number;
  /* Properties this input stands for: 1 for a point, the cell's count for a cell. */
  n: number;
  /* The property's id when the input IS one property; absent for a cell. */
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

/* The one rule: a tile holding exactly one known property shows that property (`lone`);
 * any other tile shows its count. Totals are conserved (marks' n + lone == inputs' n) and
 * tiles nest, so regrouping a result at a coarser level equals grouping the inputs there. */
export function groupByTile<T extends CountInput>(
  inputs: readonly T[],
  level: number,
  view: MapBounds | null = null,
): { marks: CountMark[]; lone: T[] } {
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
    const [lng, lat] = markPosition(g.tile, level, view);
    marks.push({ key, lng, lat, n: g.n, ids: g.ids });
  }
  return { marks, lone };
}

/* What the map draws: a point cohort is pins from PIN_ZOOM and grouped below it; the
 * server's cells are regrouped at the coarser of their level and the map's. */
export function marksFor(
  rows: readonly MapRow[],
  counts: MapCounts | null,
  rule: CountRule,
  view: MapBounds | null,
): { pins: MapRow[]; marks: CountMark[] } {
  if (counts) {
    return { pins: [], marks: groupByTile(counts.cells, Math.min(rule.level, counts.level), view).marks };
  }
  if (rule.pins) return { pins: [...rows], marks: [] };
  const grouped = groupByTile(
    rows.map((row) => ({ lng: row.lng, lat: row.lat, n: 1, id: row.listing_id, row })),
    rule.level,
    view,
  );
  return { pins: grouped.lone.map((p) => p.row), marks: grouped.marks };
}

/* The extent the map fits to on its first result: the marks that hold 99 % of the count,
 * largest first, plus every pin. A stray far-off tile (the data holds coordinates from
 * lng -118 to +125) is a count of one and so never drags the first view to world scale. */
export function fitExtent(marks: readonly CountMark[], pins: readonly MapRow[]): MapBounds | null {
  const points: Array<[number, number]> = pins.map((p) => [p.lng, p.lat]);
  const total = marks.reduce((s, m) => s + m.n, 0);
  let seen = 0;
  for (const m of [...marks].sort((a, b) => b.n - a.n)) {
    if (seen >= total * 0.99) break;
    points.push([m.lng, m.lat]);
    seen += m.n;
  }
  if (points.length === 0) return null;
  return {
    west: Math.min(...points.map((p) => p[0])),
    east: Math.max(...points.map((p) => p[0])),
    south: Math.min(...points.map((p) => p[1])),
    north: Math.max(...points.map((p) => p[1])),
  };
}

export const toCountFeatureCollection = (
  marks: readonly CountMark[],
): GeoJSON.FeatureCollection<GeoJSON.Point, { n: number }> => ({
  type: 'FeatureCollection',
  features: marks.map((m) => ({
    type: 'Feature',
    id: m.key,
    geometry: { type: 'Point', coordinates: [m.lng, m.lat] },
    properties: { n: m.n },
  })),
});

/* The rule for the map's current zoom, re-evaluated on every 'zoom' event (not only
 * zoomend) so marks regroup mid-gesture; the state changes only when the answer does. */
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

/* The mark: the count inside a dark pill, ONE symbol so the number and its background are
 * placed, collided and hit-tested together. The pill is a stretchable 9-slice image that
 * `icon-text-fit` sizes to the number; its content box is the WHOLE image, so the rounded
 * corners sit inside the fitted box (with a smaller content box MapLibre draws the fixed
 * corners outside it, and the pill came out twice its size). Our marks never overlap each
 * other (one per tile, inside its tile), so overlap is allowed; placement is NOT ignored,
 * so basemap labels still step aside for them. */
const PILL_IMAGE = 'count-pill';
const PILL_H = 18;
const PILL_W = 24;
const PILL_R = 8;
const PILL_SCALE = 2;
/* Fitted box = text (11 px at line height 1) + padding = 17 x (number + 12) px, never
 * smaller than the 16 px of fixed corners the stretch zones sit between. */
export const PILL_PADDING: [number, number, number, number] = [3, 6, 3, 6];

export function addCountPillImage(map: maplibregl.Map): void {
  if (map.hasImage(PILL_IMAGE)) return;
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
  map.addImage(PILL_IMAGE, ctx.getImageData(0, 0, canvas.width, canvas.height), pillImageOptions());
}

/* The 9-slice geometry, in image pixels: the stretch zones between the corners, the
 * content box the whole image. Exported for the test that pins the drawn size. */
export const pillImageOptions = (): {
  pixelRatio: number;
  stretchX: [[number, number]];
  stretchY: [[number, number]];
  content: [number, number, number, number];
} => {
  const s = PILL_SCALE;
  return {
    pixelRatio: s,
    stretchX: [[PILL_R * s, (PILL_W - PILL_R) * s]],
    stretchY: [[PILL_R * s, (PILL_H - PILL_R) * s]],
    content: [0, 0, PILL_W * s, PILL_H * s],
  };
};

export const countLayer = (): maplibregl.SymbolLayerSpecification => ({
  id: COUNTS,
  type: 'symbol',
  source: COUNTS,
  layout: {
    'icon-image': PILL_IMAGE,
    'icon-text-fit': 'both',
    'icon-text-fit-padding': PILL_PADDING,
    'icon-allow-overlap': true,
    'icon-ignore-placement': false,
    'text-field': ['number-format', ['get', 'n'], { locale: 'cs-CZ' }],
    'text-font': ['Noto Sans Bold'],
    'text-size': 11,
    'text-line-height': 1,
    'text-allow-overlap': true,
    'text-ignore-placement': false,
  },
  paint: {
    'text-color': '#ffffff',
  },
});
