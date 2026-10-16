import { useEffect, useState } from 'react';
import type maplibregl from 'maplibre-gl';
import type { MapBounds } from '@/lib/filters';
import type { MapCell, MapCounts, MapRow } from '@/lib/queries';

/* ONE count rule for the Browse map (docs/design/map-counts.md). Up to the pin limit
 * (MapOverlayState.pinLimit, the server's point budget) every property in view is its
 * own pin at any zoom; above it the server answers in WORLD TILES (level L cuts the
 * world into 2^L x 2^L squares; MapLibre's world is 512 px wide at zoom 0, so a
 * level-L tile is 512 * 2^(zoom - L) px) at the finest level that fits 1 024 tiles,
 * and the map regroups them at its own level with the same formula.
 * tests/fixtures/map_tile_lattice.json pins the formula for SQL and TypeScript alike. */

const MAX_LAT = 85.05112878;

/* The source and layer id of the count marks. */
export const COUNTS = 'counts';
/* The data ink: the count pill's fill and the price labels' text. */
export const COUNT_INK = '#2f5750';

/* The tile level marks are grouped at: round(zoom) + 2, a 90 to 181 px pitch. */
export const countLevel = (zoom: number): number => Math.round(zoom) + 2;

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

export interface CountMark {
  key: number;
  lng: number;
  lat: number;
  n: number;
}

/* Regroups the server's cells at `level`: counts sum, tiles nest, so regrouping a result
 * at a coarser level equals grouping the cells there, and the total is conserved. */
export function groupByTile(cells: readonly MapCell[], level: number, view: MapBounds | null = null): CountMark[] {
  const byKey = new Map<number, { tile: Tile; n: number }>();
  for (const c of cells) {
    const tile = tileOf(c.lng, c.lat, level);
    const key = tileKey(tile, level);
    const g = byKey.get(key);
    if (g) g.n += c.n;
    else byKey.set(key, { tile, n: c.n });
  }
  return [...byKey].map(([key, g]) => {
    const [lng, lat] = markPosition(g.tile, level, view);
    return { key, lng, lat, n: g.n };
  });
}

/* What the map draws: points are pins, every one of them; the server's cells (above the
 * pin limit) are counts, never regrouped finer than the level they were counted at. */
export function marksFor(
  rows: readonly MapRow[],
  counts: MapCounts | null,
  level: number,
  view: MapBounds | null,
): { pins: MapRow[]; marks: CountMark[] } {
  if (counts) return { pins: [], marks: groupByTile(counts.cells, Math.min(level, counts.level), view) };
  return { pins: [...rows], marks: [] };
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

/* The map's count level, re-evaluated on every 'zoom' event (not only zoomend) so marks
 * regroup mid-gesture; the state changes only when the level does. Null until ready. */
export function useCountLevel(map: maplibregl.Map | null, ready: boolean): number | null {
  const [level, setLevel] = useState<number | null>(null);
  useEffect(() => {
    if (!ready || !map) return;
    const sync = () => setLevel(countLevel(map.getZoom()));
    sync();
    map.on('zoom', sync);
    return () => {
      map.off('zoom', sync);
    };
  }, [map, ready]);
  return level;
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
