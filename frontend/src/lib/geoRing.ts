const EARTH_RADIUS_M = 6_371_000;

/* A circle of `radiusM` metres around (lat, lng) as a closed [lng, lat] ring.
 * MapLibre has no metres-radius circle layer, so every map that draws one
 * (Browse's centre+radius filter and city fallback, the location widget) draws
 * this polygon. 96 points reads as a circle and stays cheap. */
export function circleRing(
  lat: number,
  lng: number,
  radiusM: number,
  points = 96,
): [number, number][] {
  const latRad = (lat * Math.PI) / 180;
  const ring: [number, number][] = [];
  for (let i = 0; i < points; i++) {
    const theta = (i / points) * 2 * Math.PI;
    const dLng = ((radiusM * Math.cos(theta)) / (EARTH_RADIUS_M * Math.cos(latRad))) * (180 / Math.PI);
    const dLat = ((radiusM * Math.sin(theta)) / EARTH_RADIUS_M) * (180 / Math.PI);
    ring.push([lng + dLng, lat + dLat]);
  }
  ring.push(ring[0]);
  return ring;
}
