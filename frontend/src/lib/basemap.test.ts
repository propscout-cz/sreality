/* The basemap picks on a map with a basemap of its own (lib/basemap): ČÚZK's terrain sits
 * under the roads, the photo over the whole basemap, and the terrain rides over the photo
 * while it shows. The fake map draws nothing; it keeps the layers' order and layout. */

import type { Map as MaplibreMap } from 'maplibre-gl';
import { describe, expect, it } from 'vitest';

import { FakeMap } from '@/test/fakeMap';

import { addBasemapLayers, setBasemapPicks } from './basemap';

/* A basemap in miniature: land, a road, a label; then a layer of ours. */
function mapWithBasemap(): { fake: FakeMap; map: MaplibreMap } {
  const fake = new FakeMap(document.createElement('div'), {});
  fake.addLayer({ id: 'land', type: 'fill' });
  fake.addLayer({ id: 'road', type: 'line', 'source-layer': 'transportation' });
  fake.addLayer({ id: 'label', type: 'symbol' });
  const map = fake as unknown as MaplibreMap;
  addBasemapLayers(map);
  fake.addLayer({ id: 'pin', type: 'circle' });
  return { fake, map };
}

const NONE = { aerial: false, relief: false, contours: false };

describe('addBasemapLayers', () => {
  it('lays the terrain under the roads and the photo over the whole basemap, all hidden', () => {
    const { fake } = mapWithBasemap();
    expect(fake.getLayersOrder()).toEqual(['land', 'relief', 'contours', 'road', 'label', 'aerial', 'pin']);
    for (const id of ['relief', 'contours', 'aerial']) expect(fake.layout.get(id)).toEqual({ visibility: 'none' });
  });
});

describe('setBasemapPicks', () => {
  it('shows only what is picked', () => {
    const { fake, map } = mapWithBasemap();
    setBasemapPicks(map, { ...NONE, contours: true });
    expect(fake.layout.get('contours')).toEqual({ visibility: 'visible' });
    expect(fake.layout.get('relief')).toEqual({ visibility: 'none' });
    expect(fake.layout.get('aerial')).toEqual({ visibility: 'none' });
  });

  it('lifts the terrain over the photo while it shows, under our layers, and back under the roads', () => {
    const { fake, map } = mapWithBasemap();
    setBasemapPicks(map, { aerial: true, relief: true, contours: true });
    expect(fake.getLayersOrder()).toEqual(['land', 'road', 'label', 'aerial', 'relief', 'contours', 'pin']);
    setBasemapPicks(map, { ...NONE, relief: true, contours: true });
    expect(fake.getLayersOrder()).toEqual(['land', 'relief', 'contours', 'road', 'label', 'aerial', 'pin']);
  });

  it('does nothing on a map without the basemap picks', () => {
    const fake = new FakeMap(document.createElement('div'), {});
    fake.addLayer({ id: 'pin', type: 'circle' });
    setBasemapPicks(fake as unknown as MaplibreMap, { aerial: true, relief: true, contours: true });
    expect(fake.getLayersOrder()).toEqual(['pin']);
  });
});
