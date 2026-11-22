/* Where the map flies for a picked row: the shared zoom-by-level table (#2446), its point, or nowhere. */
import { describe, expect, it } from 'vitest';

import { flyToPlace, type PlaceRow } from './places';

const row = (level: PlaceRow['level'], point: PlaceRow['point']): PlaceRow => ({
  level, id: 1, name: 'x', context: null, point,
});

describe('flyToPlace', () => {
  it('flies to a town at 11 and a street at 15, one step wider than the old 16', () => {
    expect(flyToPlace(row('obec', { lat: 49.19, lng: 16.61 }))).toEqual({ lat: 49.19, lng: 16.61, zoom: 11 });
    expect(flyToPlace(row('ulice', { lat: 49.2, lng: 16.6 }))).toEqual({ lat: 49.2, lng: 16.6, zoom: 15 });
  });

  it('flies to a door at 16 and a městská část at 12', () => {
    expect(flyToPlace(row('adresni_misto', { lat: 49.205, lng: 16.6 }))?.zoom).toBe(16);
    expect(flyToPlace(row('momc', { lat: 50.1, lng: 14.38 }))?.zoom).toBe(12);
  });

  it('does not fly for a row with no point', () => {
    expect(flyToPlace(row('kraj', null))).toBeNull();
  });
});
