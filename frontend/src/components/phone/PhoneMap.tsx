/* Poloha's map on the phone: the location ad's point on the shared basemap, at the zoom
 * its precision allows. A lazy chunk (LocationBody), so MapLibre never joins the feed's
 * first visit. */

import { useEffect, useRef, useState } from 'react';

import { addPointPin, createMap, guardMap } from '@/lib/basemap';

import { PHOTO_DARK } from './CardPhotoStrip';

export default function PhoneMap({ lat, lon, zoom }: { lat: number; lon: number; zoom: number }) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [failure, setFailure] = useState<Error | null>(null);

  useEffect(() => {
    if (!containerRef.current) return;
    const container = containerRef.current;
    const map = createMap(container, {
      center: [lon, lat],
      zoom,
      cooperativeGestures: true,
    });
    const unguard = guardMap(map, setFailure);

    map.on('load', () => {
      // MapLibre opens the compact attribution; fold it to the (i), as on DetailMap.
      container.querySelector('.maplibregl-ctrl-attrib')?.classList.remove('maplibregl-compact-show');
      addPointPin(map, [lon, lat]);
    });

    return () => {
      unguard();
      map.remove();
    };
  }, [lat, lon, zoom]);

  /* Thrown here, after every hook, so MapSlot's boundary takes the map down. */
  if (failure) throw failure;

  return (
    <div
      ref={containerRef}
      className={`absolute inset-0 ${PHOTO_DARK}`}
      style={{ position: 'absolute', top: 0, right: 0, bottom: 0, left: 0, width: '100%', height: '100%' }}
    />
  );
}
