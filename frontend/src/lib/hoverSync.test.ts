import { describe, expect, it } from 'vitest';

import { haloPointsOf, NO_HOVER, nextHover } from './hoverSync';

describe('the locator halo takes the hovered rows\' own points', () => {
  it('keeps the rows that have a point and drops the rest', () => {
    expect(
      haloPointsOf([
        { listing_id: 1, lat: 50.08, lng: 14.43 },
        { listing_id: 2, lat: null, lng: null },
        { listing_id: 3, lat: 49.2, lng: null },
      ]),
    ).toEqual([{ lat: 50.08, lng: 14.43 }]);
  });
});

describe('the hover state: ids for the highlight, points for the halo', () => {
  const point = { lat: 50.08, lng: 14.43 };

  /* The case the operator asked for: a card hover above the pin limit, where the
   * map holds no row — the halo still has a point, from the card's own row. */
  it('a list hover carries its points, a map hover none', () => {
    const list = nextHover(NO_HOVER, [111], [point], 'list');
    expect([...list.ids]).toEqual([111]);
    expect(list).toMatchObject({ origin: 'list', points: [point] });
    const map = nextHover(NO_HOVER, [111], [], 'map');
    expect(map).toMatchObject({ origin: 'map', points: [] });
  });

  it('keeps the previous state for the same ids from the same origin, and clears on leave', () => {
    const list = nextHover(NO_HOVER, [111], [point], 'list');
    expect(nextHover(list, [111], [point], 'list')).toBe(list);
    expect(nextHover(list, [111], [], 'map')).not.toBe(list);
    expect(nextHover(list, null, [], 'list')).toBe(NO_HOVER);
    expect(nextHover(NO_HOVER, [], [], 'list')).toBe(NO_HOVER);
  });
});
