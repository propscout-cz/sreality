/* The count rule is pinned to tests/fixtures/map_tile_lattice.json, the SAME
 * fixture migration 602's SQL is pinned to, so the browser and the server can
 * never disagree on which tile a coordinate falls in. */
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';

import {
  COUNTS,
  PIN_ZOOM,
  countLayer,
  countRule,
  groupByTile,
  tileCentre,
  tileKey,
  tileOf,
  toCountFeatureCollection,
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
    expect(mark.key).toBe(tileKey(tile, 12));
    const [lng, lat] = tileCentre(tile.x, tile.y, 12);
    expect([mark.lng, mark.lat]).toEqual([lng, lat]);
  });

  it('a cell with no id is always a count, even for one property', () => {
    const g = groupByTile([{ ...BRNO, n: 1 }], 12);
    expect(g.lone).toEqual([]);
    expect(g.marks.map((m) => m.n)).toEqual([1]);
  });

  it('conserves the total at every level', () => {
    for (const level of [5, 10, 14, 18]) {
      const g = groupByTile(points, level);
      const total = g.marks.reduce((s, m) => s + m.n, 0) + g.lone.length;
      expect(total).toBe(points.length);
    }
  });

  it('nests exactly: regrouping marks at a coarser level equals grouping the inputs there', () => {
    const cells = Array.from({ length: 40 }, (_, i) => ({
      lng: 14.2 + (i % 8) * 0.07,
      lat: 49.9 + Math.floor(i / 8) * 0.05,
      n: i + 1,
    }));
    const fine = groupByTile(cells, 16).marks;
    const viaFine = groupByTile(fine, 13).marks;
    const direct = groupByTile(cells, 13).marks;
    const byKey = (ms: typeof direct) => new Map(ms.map((m) => [m.key, m.n]));
    expect(byKey(viaFine)).toEqual(byKey(direct));
    expect(viaFine.reduce((s, m) => s + m.n, 0)).toBe(cells.reduce((s, c) => s + c.n, 0));
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

  it('keeps a five-digit count under 0.55 of the 90 px minimum tile pitch', () => {
    /* Noto Sans Bold at 11 px: a digit advances about 6.3 px, the no-break
     * group separator about 2.9 px. The padding is the layer's own. */
    const [, right, , left] = layout['icon-text-fit-padding'] as number[];
    const widestNumber = 5 * 6.3 + 2.9;
    expect(layout['text-size']).toBe(11);
    expect(widestNumber + left + right).toBeLessThanOrEqual(0.55 * 90);
  });
});
