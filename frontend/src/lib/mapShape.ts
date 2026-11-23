/* One dashed copper shape on a maplibre map: MapAreaControl's map area (a box,
 * or a watchdog's circle) paints through these two calls. */
import type { GeoJSONSource, Map as MaplibreMap } from 'maplibre-gl';

import type { MapBounds } from './filters';

const COLOR = '#b58438';

export function addDashedShape(map: MaplibreMap, source: string): void {
  map.addSource(source, { type: 'geojson', data: shapeFeature(null) });
  map.addLayer({
    id: `${source}-fill`,
    type: 'fill',
    source,
    paint: { 'fill-color': COLOR, 'fill-opacity': 0.1 },
  });
  map.addLayer({
    id: `${source}-outline`,
    type: 'line',
    source,
    paint: { 'line-color': COLOR, 'line-width': 1.4, 'line-dasharray': [2, 2] },
  });
}

/* `ring` is a closed [lng, lat] ring; null empties the shape. False until the
 * source exists, i.e. before the style has loaded. */
export function setDashedShape(
  map: MaplibreMap,
  source: string,
  ring: GeoJSON.Position[] | null,
): boolean {
  const src = map.getSource(source) as GeoJSONSource | undefined;
  if (!src) return false;
  src.setData(shapeFeature(ring));
  return true;
}

export const boxRing = (b: MapBounds): GeoJSON.Position[] => [
  [b.west, b.south],
  [b.east, b.south],
  [b.east, b.north],
  [b.west, b.north],
  [b.west, b.south],
];

function shapeFeature(ring: GeoJSON.Position[] | null): GeoJSON.Feature<GeoJSON.Polygon> {
  return {
    type: 'Feature',
    properties: {},
    geometry: { type: 'Polygon', coordinates: [ring ?? []] },
  };
}
