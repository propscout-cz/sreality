/* Poloha's map on the phone: the location ad's point on the shared basemap, at the zoom
 * its precision allows, and a Mapa / Letecká bar that lays ČÚZK's aerial photo over it. A
 * lazy chunk (LocationBody), so MapLibre never joins the feed's first visit. */

import { useEffect, useRef, useState } from 'react';

import { SegmentedBar, type SegmentOption } from '@/components/controls';
import { AERIAL_LAYER, addAerialLayer, addPointPin, createMap, guardMap } from '@/lib/basemap';

import { PHOTO_DARK } from './CardPhotoStrip';

type Base = 'map' | 'aerial';

const BASES: ReadonlyArray<SegmentOption<Base>> = [
  { value: 'map', label: 'Mapa' },
  { value: 'aerial', label: 'Letecká' },
];

export default function PhoneMap({ lat, lon, zoom }: { lat: number; lon: number; zoom: number }) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<ReturnType<typeof createMap> | null>(null);
  const [failure, setFailure] = useState<Error | null>(null);
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
      addAerialLayer(map);
      addPointPin(map, [lon, lat]);
    });

    return () => {
      unguard();
      map.remove();
    };
  }, [lat, lon, zoom]);

  /* Its own effect: a tap flips the photo's visibility and never builds a new map. */
  useEffect(() => {
    const map = mapRef.current;
    if (!map?.getLayer(AERIAL_LAYER)) return;
    map.setLayoutProperty(AERIAL_LAYER, 'visibility', base === 'aerial' ? 'visible' : 'none');
  }, [base]);

  /* Thrown here, after every hook, so MapSlot's boundary takes the map down. */
  if (failure) throw failure;

  return (
    <>
      <div
        ref={containerRef}
        className={`absolute inset-0 ${PHOTO_DARK}`}
        style={{ position: 'absolute', top: 0, right: 0, bottom: 0, left: 0, width: '100%', height: '100%' }}
      />
      {/* Beside the map's container, never in it: MapLibre owns that one's children. */}
      <div className="absolute right-2 top-2 z-10 rounded-[var(--radius-sm)] bg-[var(--color-paper)]">
        <SegmentedBar tone="phone" label="Podklad mapy" options={BASES} value={base} onChange={setBase} />
      </div>
    </>
  );
}
