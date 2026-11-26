/* Single source of truth for map construction across the whole app. Every
 * surface (Browse, listing detail, comparables, datasets choropleth, the
 * location-filter widget, the phone's Poloha) builds its MapLibre map through
 * `createMap` so the basemap style, the standard controls, and the interaction
 * posture are defined once here — change them in one place and all surfaces
 * follow.
 *
 * Swap basemap styles via TILE_STYLE (positron | bright | liberty | dark |
 * fiord). */

import { useEffect } from 'react';
import maplibregl, { type MapOptions } from 'maplibre-gl';

import { asksParcel, lookUpParcel, parcelPopupHtml, type ParcelLookup } from '@/lib/cadastre';
import { registerOverlayProtocol } from '@/lib/mapOverlayDraw';
import { CADASTRE_MINZOOM, CONTOURS_MINZOOM, basemapPicks, type BasemapPicks } from '@/lib/mapOverlays';

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

/* The one pin for a property's point (DetailMap and its dialog, the phone's Poloha), dimmed for an
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

const CUZK_ATTRIBUTION = '<a href="https://geoportal.cuzk.gov.cz/" target="_blank">© ČÚZK, CC BY 4.0</a>';

export const AERIAL_LAYER = 'aerial';

/* ČÚZK's aerial photo (Ortofoto ČR, CC BY 4.0), added HIDDEN: MapLibre fetches and credits
 * only a source that a visible layer uses. Over the whole basemap, labels included, so it
 * reads as a photo, not as a map drawn on one. The tile path is ArcGIS's, y before x; tiles
 * stop at zoom 20 (deeper zooms overscale), and abroad they 404 and the basemap shows
 * through. */
function addAerialLayer(map: maplibregl.Map): void {
  map.addSource(AERIAL_LAYER, {
    type: 'raster',
    tiles: ['https://ags.cuzk.gov.cz/arcgis1/rest/services/ORTOFOTO_WM/MapServer/tile/{z}/{y}/{x}'],
    tileSize: 256,
    maxzoom: 20,
    attribution: CUZK_ATTRIBUTION,
  });
  map.addLayer({ id: AERIAL_LAYER, type: 'raster', source: AERIAL_LAYER, layout: { visibility: 'none' } });
}

export const RELIEF_LAYER = 'relief';
export const CONTOURS_LAYER = 'contours';

/* Where the terrain sits on the map: under the basemap's roads (its first `transportation`
 * layer, else its first label), so streets, buildings and names stay on top. */
const roadsId = (map: maplibregl.Map): string | undefined => {
  const layers = map.getStyle().layers;
  return (
    layers.find((l) => 'source-layer' in l && l['source-layer'] === 'transportation') ??
    layers.find((l) => l.type === 'symbol')
  )?.id;
};

/* ČÚZK's terrain (CC BY 4.0), both added HIDDEN like the aerial photo: the shaded relief of
 * DMR 5G (the laser-scanned ground model) and ZABAGED's contour lines. Neither is cached as
 * tiles, so each tile is an export of its bbox, drawn on request; abroad both come back
 * transparent. Under the roads (`roadsId`) while the basemap shows; setBasemapPicks lifts
 * them over the photo while it does. The contours are asked at 512 px and 192 dpi: the
 * 256 px tile's scale (ZABAGED's 1:10 000 floor holds), lines sharp on a retina screen.
 * `ZOBRAZ_K` is ZABAGED's own cartographic pick: it drops the base lines where a steep slope
 * would pack them into one band and leaves gentle ground untouched. */
function addTerrainLayers(map: maplibregl.Map): void {
  const beforeId = roadsId(map);
  map.addSource(RELIEF_LAYER, {
    type: 'raster',
    tiles: [
      'https://ags.cuzk.gov.cz/arcgis2/rest/services/dmr5g/ImageServer/exportImage?bbox={bbox-epsg-3857}' +
        '&bboxSR=3857&imageSR=3857&size=256,256&format=png&f=image' +
        `&renderingRule=${encodeURIComponent('{"rasterFunction":"GrayscaleHillshade"}')}`,
    ],
    tileSize: 256,
    maxzoom: 18,
    attribution: CUZK_ATTRIBUTION,
  });
  map.addSource(CONTOURS_LAYER, {
    type: 'raster',
    tiles: [
      'https://ags.cuzk.gov.cz/arcgis/rest/services/ZABAGED_VRSTEVNICE/MapServer/export?bbox={bbox-epsg-3857}' +
        '&bboxSR=3857&imageSR=3857&size=512,512&dpi=192&format=png32&transparent=true&f=image' +
        `&layerDefs=${encodeURIComponent(`{"0":"ZOBRAZ_K='1'"}`)}`,
    ],
    tileSize: 256,
    minzoom: CONTOURS_MINZOOM,
    attribution: CUZK_ATTRIBUTION,
  });
  map.addLayer(
    {
      id: RELIEF_LAYER,
      type: 'raster',
      source: RELIEF_LAYER,
      layout: { visibility: 'none' },
      // The contrast lifts flat ground toward white, so only the slopes darken the basemap.
      paint: { 'raster-opacity': 0.35, 'raster-contrast': 0.3 },
    },
    beforeId,
  );
  map.addLayer(
    { id: CONTOURS_LAYER, type: 'raster', source: CONTOURS_LAYER, layout: { visibility: 'none' } },
    beforeId,
  );
}

export const CADASTRE_LAYER = 'cadastre';
export const CADASTRE_ON_PHOTO_LAYER = 'cadastre-photo';

/* The basemap's first label: the cadastral map sits right under it, so street names read
 * over the parcel lines. */
const labelsId = (map: maplibregl.Map): string | undefined =>
  map.getStyle().layers.find((l) => l.type === 'symbol')?.id;

/* ČÚZK's cadastral map (Katastrální mapa, KN; CC BY 4.0), cached WMTS tiles in web
 * mercator, row before column. Two layers, both added HIDDEN: ČÚZK's black lines over the
 * basemap, its yellow ones ("pro zobrazení nad ortofotem") over the photo; only the shown
 * one's tiles load. ČÚZK draws parcels only from zoom 17 (a zoomed-out tile is an HTML
 * notice, not a PNG), so the layers start there too and the panel's hint and the parcel
 * click (lib/cadastre) agree with what is drawn. Abroad the tiles come back empty. */
function addCadastreLayers(map: maplibregl.Map): void {
  const beforeId = labelsId(map);
  for (const [id, style] of [
    [CADASTRE_LAYER, 'default'],
    [CADASTRE_ON_PHOTO_LAYER, 'Yellow'],
  ] as const) {
    map.addSource(id, {
      type: 'raster',
      tiles: [`https://services.cuzk.cz/wmts/local-km-wmts-google/rest/WMTS/${style}/KN/{z}/{y}/{x}`],
      tileSize: 256,
      minzoom: CADASTRE_MINZOOM,
      maxzoom: 22,
      attribution: CUZK_ATTRIBUTION,
    });
    map.addLayer(
      { id, type: 'raster', source: id, minzoom: CADASTRE_MINZOOM, layout: { visibility: 'none' } },
      beforeId,
    );
  }
}

/* Every map's basemap picks (lib/mapOverlays BasemapPicks), all HIDDEN until picked: the
 * terrain under the roads, the cadastral map under the labels, the photo over the whole
 * basemap. Call it once the style has loaded and before any layer of ours, so each of
 * ours draws over all of them. */
export function addBasemapLayers(map: maplibregl.Map): void {
  addTerrainLayers(map);
  addCadastreLayers(map);
  addAerialLayer(map);
}

/* Shows the picked ones and hides the rest. The terrain and the cadastral map move with
 * the base: over the photo while it shows (under it the photo would hide them; the
 * cadastre over the terrain, in its yellow), back under the roads and the labels when the
 * basemap does (right under the photo on a basemap with neither). A no-op on a map
 * without addBasemapLayers. */
export function setBasemapPicks(
  map: maplibregl.Map,
  { aerial, relief, contours, cadastre }: BasemapPicks,
): void {
  if (!map.getLayer(AERIAL_LAYER)) return;
  const order = map.getLayersOrder();
  const moving = new Set([RELIEF_LAYER, CONTOURS_LAYER, CADASTRE_LAYER, CADASTRE_ON_PHOTO_LAYER]);
  const overPhoto = order.slice(order.indexOf(AERIAL_LAYER) + 1).find((id) => !moving.has(id));
  const terrainBefore = aerial ? overPhoto : roadsId(map) ?? AERIAL_LAYER;
  const cadastreBefore = aerial ? overPhoto : labelsId(map) ?? AERIAL_LAYER;
  map.moveLayer(RELIEF_LAYER, terrainBefore);
  map.moveLayer(CONTOURS_LAYER, terrainBefore);
  map.moveLayer(CADASTRE_LAYER, cadastreBefore);
  map.moveLayer(CADASTRE_ON_PHOTO_LAYER, cadastreBefore);
  const show = (id: string, on: boolean) => map.setLayoutProperty(id, 'visibility', on ? 'visible' : 'none');
  show(AERIAL_LAYER, aerial);
  show(RELIEF_LAYER, relief);
  show(CONTOURS_LAYER, contours);
  show(CADASTRE_LAYER, cadastre && !aerial);
  show(CADASTRE_ON_PHOTO_LAYER, cadastre && aerial);
}

/* The one popup a click on a map's reference data opens (Browse's map, lib/usePointMap):
 * the overlay area's card (lib/mapOverlayDraw overlayPopupAt's html), then, while the
 * cadastral map is drawn, the parcel under the click (lib/cadastre), its card filled in
 * when ČÚZK answers; closing the popup drops the question. Null when neither has anything
 * to say. */
export function openReferencePopup(
  map: maplibregl.Map,
  at: maplibregl.LngLat,
  overlayHtml: string | null,
  selection: string,
): maplibregl.Popup | null {
  const parcel = asksParcel(map, selection);
  if (!overlayHtml && !parcel) return null;
  const html = (lookup: ParcelLookup | null): string =>
    `${overlayHtml ?? ''}${lookup ? parcelPopupHtml(lookup) : ''}`;
  const popup = new maplibregl.Popup({
    closeButton: true,
    closeOnClick: true,
    maxWidth: '300px',
    className: 'listing-popup overlay-popup',
  })
    .setLngLat(at)
    .setHTML(html(parcel ? { state: 'loading' } : null))
    .addTo(map);
  if (parcel) {
    const question = new AbortController();
    popup.on('close', () => question.abort());
    void lookUpParcel(at, question.signal).then((lookup) => {
      if (!question.signal.aborted) popup.setHTML(html(lookup));
    });
  }
  return popup;
}

/* Keeps a map's basemap picks in step with a `layers` selection; `ready` = its style has
 * loaded and addBasemapLayers ran. A pick flips visibility, never builds a new map. */
export function useBasemapPicks(map: maplibregl.Map | null, ready: boolean, selection: string): void {
  const { aerial, relief, contours, cadastre } = basemapPicks(selection);
  useEffect(() => {
    if (map && ready) setBasemapPicks(map, { aerial, relief, contours, cadastre });
  }, [map, ready, aerial, relief, contours, cadastre]);
}
