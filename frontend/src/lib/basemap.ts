/* Single source of truth for map construction across the whole app. Every
 * surface (Browse, listing detail, comparables, datasets choropleth, the
 * location-filter widget, the phone's Poloha) builds its MapLibre map through
 * `createMap` so the basemap style, the standard controls, and the interaction
 * posture are defined once here — change them in one place and all surfaces
 * follow.
 *
 * Swap basemap styles via TILE_STYLE (positron | bright | liberty | dark |
 * fiord). */

import maplibregl, { type MapOptions } from 'maplibre-gl';

import { registerOverlayProtocol } from '@/lib/mapOverlayDraw';

export const TILE_STYLE = 'https://tiles.openfreemap.org/styles/liberty';

/* Every map can draw the reference overlays (lib/mapOverlayDraw): their tiles
 * load through the API with the caller's session, not a bare fetch. */
registerOverlayProtocol(maplibregl.addProtocol);

type CreateMapOptions = Omit<MapOptions, 'container'> & {
  /* Add the metric ScaleControl (bottom-left). On for every map except the
   * Datasets choropleth, which omits it. */
  scaleControl?: boolean;
  /* Add MapLibre's zoom buttons (top-right). Browse's map draws its own on its rail. */
  navigationControl?: boolean;
};

/* Build an app-standard MapLibre map: the shared basemap style, compact
 * attribution, (by default) a zoom-only NavigationControl, and (by default) a metric scale.
 *
 * Rotation and tilt are disabled on EVERY surface — the product is a north-up,
 * flat 2D map. Hiding the compass button alone (showCompass:false) only removes
 * the UI affordance; the rotate/tilt GESTURES (right-drag, two-finger twist,
 * multi-finger pitch, Shift+arrows) stay live unless the handlers are switched
 * off. We do that here, once, so no surface can drift. The disable is applied
 * AFTER the caller's overrides so it can never be re-enabled by accident.
 *
 * NOTE: `touchZoomRotate` is left enabled — it also drives pinch-to-ZOOM, which
 * we keep. Only its rotation half is turned off via `disableRotation()`. */
export function createMap(
  container: HTMLElement,
  { scaleControl = true, navigationControl = true, ...overrides }: CreateMapOptions = {},
): maplibregl.Map {
  const map = new maplibregl.Map({
    container,
    style: TILE_STYLE,
    attributionControl: { compact: true },
    ...overrides,
    dragRotate: false,
    pitchWithRotate: false,
    touchPitch: false,
  });
  map.touchZoomRotate.disableRotation();
  map.keyboard.disableRotation();

  if (navigationControl) {
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'top-right');
  }
  if (scaleControl) {
    map.addControl(new maplibregl.ScaleControl({ maxWidth: 120, unit: 'metric' }), 'bottom-left');
  }
  return map;
}

/* Tells `fail` when the map cannot draw: its WebGL context was lost, or it erred before
 * its style first loaded. A later error (a tile that 404s) leaves the map up and stays
 * MapLibre's to log. These arrive as MapLibre events, outside React's render, so no error
 * boundary sees them on its own. Returns the unguard: call it before `map.remove()`, which
 * drops the context itself. */
export function guardMap(map: maplibregl.Map, fail: (error: Error) => void): () => void {
  const onContextLost = () => fail(new Error('The map lost its WebGL context'));
  const onError = (e: ErrorEvent) => fail(e.error);
  const onStyleLoad = () => map.off('error', onError);
  map.on('webglcontextlost', onContextLost);
  map.on('error', onError);
  map.once('style.load', onStyleLoad);
  return () => {
    map.off('webglcontextlost', onContextLost);
    map.off('error', onError);
    map.off('style.load', onStyleLoad);
  };
}

/* The one pin for a property's point (DetailMap, the phone's Poloha), dimmed for an
 * inactive one. Call it once the map's style has loaded. */
export function addPointPin(
  map: maplibregl.Map,
  [lng, lat]: [number, number],
  { active = true }: { active?: boolean } = {},
): void {
  map.addSource('pin', {
    type: 'geojson',
    data: {
      type: 'Feature',
      properties: {},
      geometry: { type: 'Point', coordinates: [lng, lat] },
    },
  });
  map.addLayer({
    id: 'pin',
    type: 'circle',
    source: 'pin',
    paint: {
      'circle-radius': 7,
      'circle-color': '#3c6e63',
      'circle-stroke-color': '#ffffff',
      'circle-stroke-width': 2,
      'circle-opacity': active ? 1 : 0.6,
    },
  });
}

export const AERIAL_LAYER = 'aerial';

/* ČÚZK's aerial photo (Ortofoto ČR, CC BY 4.0), added HIDDEN: MapLibre fetches and credits
 * only a source that a visible layer uses. Call it once the style has loaded and before
 * addPointPin, so it draws over the basemap and under the pin. The tile path is ArcGIS's,
 * y before x; tiles stop at zoom 20 (deeper zooms overscale), and abroad they 404 and the
 * basemap shows through. */
export function addAerialLayer(map: maplibregl.Map): void {
  map.addSource(AERIAL_LAYER, {
    type: 'raster',
    tiles: ['https://ags.cuzk.gov.cz/arcgis1/rest/services/ORTOFOTO_WM/MapServer/tile/{z}/{y}/{x}'],
    tileSize: 256,
    maxzoom: 20,
    attribution: '<a href="https://geoportal.cuzk.gov.cz/" target="_blank">© ČÚZK, CC BY 4.0</a>',
  });
  map.addLayer({ id: AERIAL_LAYER, type: 'raster', source: AERIAL_LAYER, layout: { visibility: 'none' } });
}
