/* Poloha's map on the phone: the location ad's point on the shared basemap, at the zoom
 * its precision allows, and a Mapa / Letecká / Terén bar that lays ČÚZK's aerial photo, or
 * its shaded relief and contour lines, over it. A lazy chunk (LocationBody), so MapLibre
 * never joins the feed's first visit. */

import { useEffect, useRef, useState } from 'react';

import { SegmentedBar, type SegmentOption } from '@/components/controls';
import {
  AERIAL_LAYER,
  CONTOURS_LAYER,
  RELIEF_LAYER,
  addAerialLayer,
  addPointPin,
  addTerrainLayers,
  createMap,
  guardMap,
} from '@/lib/basemap';

import { PHOTO_DARK } from './CardPhotoStrip';

type Base = 'map' | 'aerial' | 'terrain';

const BASES: ReadonlyArray<SegmentOption<Base>> = [
  { value: 'map', label: 'Mapa' },
  { value: 'aerial', label: 'Letecká' },
  { value: 'terrain', label: 'Terén' },
];

/* The layers each pick shows; every other one of the three is hidden. */
const SHOWN: Record<Base, readonly string[]> = {
  map: [],
  aerial: [AERIAL_LAYER],
  terrain: [RELIEF_LAYER, CONTOURS_LAYER],
};

export default function PhoneMap({ lat, lon, zoom }: { lat: number; lon: number; zoom: number }) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<ReturnType<typeof createMap> | null>(null);
  const [failure, setFailure] = useState<Error | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [base, setBase] = useState<Base>('map');

  useEffect(() => {
    if (!containerRef.current) return;
    const container = containerRef.current;
    const map = createMap(container, {
      center: [lon, lat],
      zoom,
      cooperativeGestures: true,
      locale: { 'CooperativeGesturesHandler.MobileHelpText': 'Mapou posunete dvěma prsty' },
      navigationControl: false,
      scaleControl: false,
    });
    mapRef.current = map;
    const unguard = guardMap(map, setFailure);

    map.on('load', () => {
      // MapLibre opens the compact attribution; fold it to the (i), as on DetailMap.
      container.querySelector('.maplibregl-ctrl-attrib')?.classList.remove('maplibregl-compact-show');
      addTerrainLayers(map);
      addAerialLayer(map);
      addPointPin(map, [lon, lat]);
      setLoaded(true);
    });

    return () => {
      unguard();
      map.remove();
      setLoaded(false);
    };
  }, [lat, lon, zoom]);

  /* Its own effect: a tap flips the layers' visibility and never builds a new map; a new
   * map (a moved point) takes the pick once it has loaded. */
  useEffect(() => {
    const map = mapRef.current;
    if (!loaded || !map?.getLayer(AERIAL_LAYER)) return;
    for (const id of [AERIAL_LAYER, RELIEF_LAYER, CONTOURS_LAYER]) {
      map.setLayoutProperty(id, 'visibility', SHOWN[base].includes(id) ? 'visible' : 'none');
    }
  }, [base, loaded]);

  /* Thrown here, after every hook, so MapSlot's boundary takes the map down. */
  if (failure) throw failure;

  return (
    <>
      <div
        ref={containerRef}
        className={`absolute inset-0 ${PHOTO_DARK}`}
        style={{ position: 'absolute', top: 0, right: 0, bottom: 0, left: 0, width: '100%', height: '100%' }}
      />
      {/* Once the map has loaded, as the photo exists only from then on; beside the map's
        * container, never in it: MapLibre owns that one's children. */}
      {loaded && (
        <div className="absolute right-2 top-2 z-10 rounded-[var(--radius-sm)] bg-[var(--color-paper)]">
          <SegmentedBar tone="phone" label="Podklad mapy" options={BASES} value={base} onChange={setBase} />
        </div>
      )}
    </>
  );
}
