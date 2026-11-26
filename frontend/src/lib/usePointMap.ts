/* A map of one property's point — the property page's header map and its large dialog,
 * the phone's Poloha: the shared basemap around the pin, and every layer the `layers`
 * selection picks (lib/mapOverlays): the basemap picks under everything, the reference
 * overlays under the pin, a click on an overlay area (or, with the cadastral map drawn,
 * on a parcel) answered with its popup (lib/basemap openReferencePopup). ONE hook,
 * so the three maps cannot drift; each draws only its own controls around it. */

import { useEffect, useRef, useState, type RefObject } from 'react';
import maplibregl from 'maplibre-gl';

import {
  addBasemapLayers,
  addPointPin,
  createMap,
  guardMap,
  openReferencePopup,
  useBasemapPicks,
} from '@/lib/basemap';
import {
  overlayPopupAt,
  useMapOverlays,
  useMapZoom,
  useOverlayCursor,
  useOverlayPlacesAtCenter,
  type OverlayPlaces,
} from '@/lib/mapOverlayDraw';
import { useMapOverlayRegistry, type MapOverlayRegistry } from '@/lib/mapOverlays';

type CreateMapOptions = NonNullable<Parameters<typeof createMap>[1]>;

export interface PointMapSetup {
  lng: number;
  lat: number;
  /* An inactive property's pin is dimmed (addPointPin). */
  active?: boolean;
  /* The view the map opens at — the pin's point unless `center` says otherwise. Read
   * once per map: a later change moves nothing. */
  zoom: number;
  center?: [number, number];
  options?: Omit<CreateMapOptions, 'center' | 'zoom'>;
  /* MapLibre opens the compact attribution on a wide map; a small one folds it to its (i). */
  foldAttribution?: boolean;
}

export interface PointMap {
  map: maplibregl.Map | null;
  /* The style has loaded and the pin is drawn. */
  ready: boolean;
  /* The map cannot draw (guardMap); the caller decides whether that takes it down. */
  failure: Error | null;
  registry: MapOverlayRegistry | undefined;
  /* The map's floored zoom, for the panels' "zoom in" hints. */
  zoom: number | null;
  /* What lies under the map's centre, per layer keyed by an area card. */
  places: OverlayPlaces;
}

export function usePointMap(
  containerRef: RefObject<HTMLDivElement | null>,
  setup: PointMapSetup,
  layers: string,
): PointMap {
  const { lng, lat, active = true } = setup;
  const setupRef = useRef(setup);
  setupRef.current = setup;
  const mapRef = useRef<maplibregl.Map | null>(null);
  const [ready, setReady] = useState(false);
  const [failure, setFailure] = useState<Error | null>(null);
  const registry = useMapOverlayRegistry().data;
  /* The latest registry and selection for the click handler, registered once at load. */
  const registryRef = useRef(registry);
  registryRef.current = registry;
  const layersRef = useRef(layers);
  layersRef.current = layers;

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    const { zoom, center, options, foldAttribution = true } = setupRef.current;
    const map = createMap(container, { ...options, center: center ?? [lng, lat], zoom });
    mapRef.current = map;
    const unguard = guardMap(map, setFailure);
    let popup: maplibregl.Popup | null = null;

    map.on('load', () => {
      if (foldAttribution) {
        container.querySelector('.maplibregl-ctrl-attrib')?.classList.remove('maplibregl-compact-show');
      }
      addBasemapLayers(map);
      addPointPin(map, [lng, lat], { active });
      map.on('click', (e) => {
        const html = overlayPopupAt(map, e.point, registryRef.current, layersRef.current);
        const next = openReferencePopup(map, e.lngLat, html, layersRef.current);
        if (!next) return;
        popup?.remove();
        popup = next;
      });
      setReady(true);
    });

    return () => {
      unguard();
      popup?.remove();
      map.remove();
      mapRef.current = null;
      /* `ready` means THIS map has loaded; a replacement map must earn it again. */
      setReady(false);
    };
  }, [containerRef, lng, lat, active]);

  const map = mapRef.current;
  useBasemapPicks(map, ready, layers);
  useMapOverlays(map, ready, registry, layers, 'pin');
  useOverlayCursor(map, ready, registry);
  const zoom = useMapZoom(map, ready);
  const places = useOverlayPlacesAtCenter(map, ready, registry, layers);
  return { map, ready, failure, registry, zoom, places };
}
