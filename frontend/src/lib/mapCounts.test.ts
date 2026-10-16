/* The count rule is pinned to tests/fixtures/map_tile_lattice.json, the SAME fixture
 * migration 602's SQL is pinned to, so the browser and the server can never disagree on
 * which tile a coordinate falls in. */
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { act, renderHook } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import type maplibregl from 'maplibre-gl';

import type { MapRow } from './queries';
import {
  COUNTS,
  PILL_PADDING,
  PIN_ZOOM,
  countLayer,
  countRule,
  fitExtent,
  groupByTile,
  marksFor,
  pillImageOptions,
  tileCentre,
  tileOf,
  toCountFeatureCollection,
  useCountRule,
} from './mapCounts';

interface LatticeCase { name: string; lng: number; lat: number; level: number; x: number; y: number }
interface RoundTrip {
  level: number; x: number; y: number; centre_lng: number; centre_lat: number; parent_x: number; parent_y: number;
}
const lattice = JSON.parse(
  readFileSync(join(process.cwd(), '..', 'tests', 'fixtures', 'map_tile_lattice.json'), 'utf-8'),
) as { cases: LatticeCase[]; centre_round_trip: RoundTrip[] };

describe('tileOf', () => {
  it('matches every fixture case, including the latitude clamp and the longitude outliers', () => {
    expect(lattice.cases.length).toBeGreaterThan(50);
    for (const c of lattice.cases) {
      expect(tileOf(c.lng, c.lat, c.level), c.name).toEqual({ x: c.x, y: c.y });
    }
  });
});

describe('tileCentre', () => {
  it('round-trips to its own tile and to its parent', () => {
    for (const r of lattice.centre_round_trip) {
      const [lng, lat] = tileCentre(r.x, r.y, r.level);
      expect(lng).toBeCloseTo(r.centre_lng, 5);
      expect(lat).toBeCloseTo(r.centre_lat, 5);
      expect(tileOf(lng, lat, r.level)).toEqual({ x: r.x, y: r.y });
      expect(tileOf(lng, lat, r.level - 1)).toEqual({ x: r.parent_x, y: r.parent_y });
    }
  });
});

describe('countRule', () => {
  it('groups at round(zoom) + 2, a 90 to 181 px pitch on MapLibre\'s 512 px world', () => {
    expect(countRule(12).level).toBe(14);
    expect(countRule(12.49).level).toBe(14);
    expect(countRule(12.5).level).toBe(15);
    expect(countRule(12.9).level).toBe(15);
    for (const zoom of [12, 12.49, 12.5, 12.9]) {
      const pitchPx = 512 * 2 ** (zoom - countRule(zoom).level);
      expect(pitchPx).toBeGreaterThanOrEqual(90);
      expect(pitchPx).toBeLessThan(181.1);
    }
  });
  it('draws a point cohort pin by pin from PIN_ZOOM', () => {
    expect(countRule(PIN_ZOOM - 0.01).pins).toBe(false);
    expect(countRule(PIN_ZOOM).pins).toBe(true);
  });
});

const PRAHA = { lng: 14.437, lat: 50.075 };
const BRNO = { lng: 16.608, lat: 49.195 };
const row = (id: number, p: { lng: number; lat: number }): MapRow =>
  ({ listing_id: id, lng: p.lng, lat: p.lat } as unknown as MapRow);

describe('groupByTile', () => {
  const points = [
    { ...PRAHA, n: 1, id: 1 },
    { lng: PRAHA.lng + 0.0001, lat: PRAHA.lat, n: 1, id: 2 },
    { ...BRNO, n: 1, id: 3 },
  ];

  it('a tile holding one known property is that pin; two or more are a count at the tile centre', () => {
    const g = groupByTile(points, 12);
    expect(g.lone.map((p) => p.id)).toEqual([3]);
    expect(g.marks).toHaveLength(1);
    const [mark] = g.marks;
    expect(mark.n).toBe(2);
    expect(mark.ids).toEqual([1, 2]);
    const tile = tileOf(PRAHA.lng, PRAHA.lat, 12);
    expect([mark.lng, mark.lat]).toEqual(tileCentre(tile.x, tile.y, 12));
  });

  it('a cell with no id is always a count, even for one property', () => {
    const g = groupByTile([{ ...BRNO, n: 1 }], 12);
    expect(g.lone).toEqual([]);
    expect(g.marks.map((m) => m.n)).toEqual([1]);
  });

  it('conserves the total at every level', () => {
    for (const level of [5, 10, 14, 18]) {
      const g = groupByTile(points, level);
      expect(g.marks.reduce((s, m) => s + m.n, 0) + g.lone.length).toBe(points.length);
    }
  });

  it('nests exactly: regrouping marks at a coarser level equals grouping the inputs there', () => {
    const cells = Array.from({ length: 40 }, (_, i) => ({
      lng: 14.2 + (i % 8) * 0.07,
      lat: 49.9 + Math.floor(i / 8) * 0.05,
      n: i + 1,
    }));
    const viaFine = groupByTile(groupByTile(cells, 16).marks, 13).marks;
    const direct = groupByTile(cells, 13).marks;
    const byKey = (ms: typeof direct) => new Map(ms.map((m) => [m.key, m.n]));
    expect(byKey(viaFine)).toEqual(byKey(direct));
    expect(viaFine.reduce((s, m) => s + m.n, 0)).toBe(cells.reduce((s, c) => s + c.n, 0));
  });

  it('keys a tile with its level, so a feature from another level can never resolve', () => {
    const [fine] = groupByTile([{ ...PRAHA, n: 2 }], 16).marks;
    const [coarse] = groupByTile([{ ...PRAHA, n: 2 }], 15).marks;
    expect(fine.key).not.toBe(coarse.key);
    expect(fine.key % 32).toBe(16);
    expect(coarse.key % 32).toBe(15);
  });

  it('puts an edge tile\'s mark on the part of the tile that is in view', () => {
    const tile = tileOf(PRAHA.lng, PRAHA.lat, 12);
    const [cx, cy] = tileCentre(tile.x, tile.y, 12);
    /* A view whose east edge cuts the tile just east of the point: the centre would be
     * off screen, the mark must not be. */
    const view = { west: PRAHA.lng - 1, east: PRAHA.lng + 0.001, south: PRAHA.lat - 1, north: PRAHA.lat + 1 };
    const [mark] = groupByTile([{ ...PRAHA, n: 5 }], 12, view).marks;
    expect(mark.lng).toBeLessThanOrEqual(view.east);
    expect(mark.lng).not.toBe(cx);
    expect(mark.lat).toBeCloseTo(cy, 3);
    expect(tileOf(mark.lng, mark.lat, 12)).toEqual(tile);
    /* A fully visible tile keeps its centre. */
    const wide = { west: 0, east: 30, south: 40, north: 60 };
    expect(groupByTile([{ ...PRAHA, n: 5 }], 12, wide).marks[0]).toMatchObject({ lng: cx, lat: cy });
  });
});

describe('marksFor', () => {
  const rows = [row(1, PRAHA), row(2, { lng: PRAHA.lng + 0.0001, lat: PRAHA.lat }), row(3, BRNO)];

  it('below PIN_ZOOM a lone property is a pin and a shared tile is a count; from PIN_ZOOM all are pins', () => {
    const grouped = marksFor(rows, null, countRule(12), null);
    expect(grouped.pins.map((r) => r.listing_id)).toEqual([3]);
    expect(grouped.marks.map((m) => m.n)).toEqual([2]);
    const pins = marksFor(rows, null, countRule(PIN_ZOOM), null);
    expect(pins.pins).toHaveLength(3);
    expect(pins.marks).toEqual([]);
  });

  it('never regroups the server\'s cells finer than their level', () => {
    const counts = { level: 12, cells: [{ ...PRAHA, n: 7 }, { ...BRNO, n: 3 }] };
    const atFiner = marksFor([], counts, countRule(15), null);
    expect(atFiner.pins).toEqual([]);
    expect(atFiner.marks.map((m) => m.key % 32)).toEqual([12, 12]);
    const atCoarser = marksFor([], counts, countRule(5), null);
    expect(atCoarser.marks.map((m) => m.key % 32)).toEqual([7]);
    expect(atCoarser.marks[0].n).toBe(10);
  });
});

describe('fitExtent', () => {
  it('fits the tiles holding 99 % of the count plus every pin, so a stray far-off cell cannot drag the view out', () => {
    const marks = groupByTile([{ ...PRAHA, n: 9_000 }, { ...BRNO, n: 900 }, { lng: -118.2, lat: 34.0, n: 1 }], 10).marks;
    const extent = fitExtent(marks, [])!;
    expect(extent.west).toBeGreaterThan(14);
    expect(extent.east).toBeLessThan(17);
    const withPin = fitExtent(marks, [row(9, { lng: 12.1, lat: 50.5 })])!;
    expect(withPin.west).toBeCloseTo(12.1, 5);
    expect(fitExtent([], [])).toBeNull();
  });
});

describe('the count feature collection', () => {
  it('carries the tile key as the feature id and the exact count', () => {
    const [mark] = groupByTile([{ ...PRAHA, n: 1465 }], 12).marks;
    const fc = toCountFeatureCollection([mark]);
    expect(fc.features[0].id).toBe(mark.key);
    expect(fc.features[0].properties).toEqual({ n: 1465 });
    expect(fc.features[0].geometry.coordinates).toEqual([mark.lng, mark.lat]);
  });
});

describe('the count layer', () => {
  const layer = countLayer();
  const layout = layer.layout!;

  it('is one symbol: the cs-CZ number inside the stretchable pill, never dropped, still a placement obstacle', () => {
    expect(layer.id).toBe(COUNTS);
    expect(layer.source).toBe(COUNTS);
    expect(layout['text-field']).toEqual(['number-format', ['get', 'n'], { locale: 'cs-CZ' }]);
    expect(layout['icon-text-fit']).toBe('both');
    expect(layout['icon-allow-overlap']).toBe(true);
    expect(layout['text-allow-overlap']).toBe(true);
    expect(layout['icon-ignore-placement']).toBe(false);
    expect(layout['text-ignore-placement']).toBe(false);
  });

  it('draws the pill at the fitted box: 17 px tall, a five-digit count under 0.55 of the 90 px minimum pitch', () => {
    /* With a content box smaller than the image MapLibre draws the fixed corners OUTSIDE
     * the fitted box (the pill came out twice its size); the content box must be the
     * whole image, and the fitted box must cover the fixed corners. */
    const img = pillImageOptions();
    const [cw, ch] = [img.content[2] - img.content[0], img.content[3] - img.content[1]];
    expect([cw, ch]).toEqual([24 * img.pixelRatio, 18 * img.pixelRatio]);
    const fixedY = (img.stretchY[0][0] + ch - img.stretchY[0][1]) / img.pixelRatio;
    const [top, right, bottom, left] = layout['icon-text-fit-padding'] as number[];
    expect(layout['text-line-height']).toBe(1);
    const height = (layout['text-size'] as number) * (layout['text-line-height'] as number) + top + bottom;
    expect(height).toBe(17);
    expect(height).toBeGreaterThanOrEqual(fixedY);
    /* Noto Sans Bold at 11 px: a digit advances about 6.3 px, the no-break group
     * separator about 2.9 px. */
    expect(5 * 6.3 + 2.9 + left + right).toBeLessThanOrEqual(0.55 * 90);
    expect(PILL_PADDING).toEqual([top, right, bottom, left]);
  });
});

describe('useCountRule', () => {
  class FakeMap {
    zoom = 12.2;
    handlers = new Map<string, () => void>();
    getZoom() { return this.zoom; }
    on(ev: string, fn: () => void) { this.handlers.set(ev, fn); }
    off(ev: string) { this.handlers.delete(ev); }
  }
  const asMap = (m: FakeMap) => m as unknown as maplibregl.Map;

  it('answers once ready and again only when the level or the pin threshold changes', () => {
    const fake = new FakeMap();
    const { result, rerender } = renderHook((p: { ready: boolean }) => useCountRule(asMap(fake), p.ready), {
      initialProps: { ready: false },
    });
    expect(result.current).toBeNull();
    rerender({ ready: true });
    const first = result.current;
    expect(first).toEqual({ level: 14, pins: false });
    fake.zoom = 12.4;
    act(() => fake.handlers.get('zoom')!());
    expect(result.current).toBe(first);
    fake.zoom = 12.6;
    act(() => fake.handlers.get('zoom')!());
    expect(result.current).toEqual({ level: 15, pins: false });
    fake.zoom = 14;
    act(() => fake.handlers.get('zoom')!());
    expect(result.current).toEqual({ level: 16, pins: true });
  });
});
