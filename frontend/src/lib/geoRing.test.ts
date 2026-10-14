import { describe, expect, it } from 'vitest';

import { circleRing } from './geoRing';

const EARTH_RADIUS_M = 6_371_000;

/* Great-circle distance, independent of the ring's own flat approximation. */
function haversineM([lng1, lat1]: [number, number], [lng2, lat2]: [number, number]): number {
  const rad = (d: number) => (d * Math.PI) / 180;
  const a =
    Math.sin(rad(lat2 - lat1) / 2) ** 2 +
    Math.cos(rad(lat1)) * Math.cos(rad(lat2)) * Math.sin(rad(lng2 - lng1) / 2) ** 2;
  return 2 * EARTH_RADIUS_M * Math.asin(Math.sqrt(a));
}

const PRAGUE: [number, number] = [14.4378, 50.0755];

describe('circleRing', () => {
  it('is a closed ring of 96 points plus the closing one by default', () => {
    const ring = circleRing(PRAGUE[1], PRAGUE[0], 1_000);
    expect(ring).toHaveLength(97);
    expect(ring[96]).toEqual(ring[0]);
  });

  it('takes the point count', () => {
    const ring = circleRing(PRAGUE[1], PRAGUE[0], 1_000, 8);
    expect(ring).toHaveLength(9);
    expect(ring[8]).toEqual(ring[0]);
  });

  it('puts every vertex the radius away from the centre', () => {
    for (const radius of [100, 2_500, 10_000]) {
      for (const p of circleRing(PRAGUE[1], PRAGUE[0], radius)) {
        expect(Math.abs(haversineM(PRAGUE, p) - radius) / radius).toBeLessThan(0.002);
      }
    }
  });

  it('widens in longitude with latitude, so the circle stays round on the ground', () => {
    const [east] = circleRing(50, 14, 1_000, 4);
    const [eastAtEquator] = circleRing(0, 14, 1_000, 4);
    expect(east[0] - 14).toBeGreaterThan(eastAtEquator[0] - 14);
    expect(east[1]).toBeCloseTo(50, 9);
  });
});
