import { describe, expect, it } from 'vitest';

import { haloFeatures } from './useMapFeatureHover';

describe('the locator halo source', () => {
  it('is one point feature per hovered row, as [lng, lat]', () => {
    expect(haloFeatures([{ lat: 50.08, lng: 14.43 }])).toEqual({
      type: 'FeatureCollection',
      features: [
        { type: 'Feature', geometry: { type: 'Point', coordinates: [14.43, 50.08] }, properties: {} },
      ],
    });
    expect(haloFeatures([]).features).toEqual([]);
  });
});
