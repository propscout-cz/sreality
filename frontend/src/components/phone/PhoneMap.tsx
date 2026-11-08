/* Poloha's map on the phone: the location ad's point on the shared basemap, at the zoom
 * its precision allows. A lazy chunk (LocationBody), so MapLibre never joins the feed's
 * first visit. */

import { useEffect, useRef } from 'react';

import { createMap } from '@/lib/basemap';

import { PHOTO_DARK } from './CardPhotoStrip';

export default function PhoneMap({ lat, lon, zoom }: { lat: number; lon: number; zoom: number }) {
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!containerRef.current) return;
    createMap(containerRef.current, {
      center: [lon, lat],
      zoom,
      cooperativeGestures: true,
    });
  }, [lat, lon, zoom]);

  return (
    <div
      ref={containerRef}
      className={`absolute inset-0 ${PHOTO_DARK}`}
      style={{ position: 'absolute', top: 0, right: 0, bottom: 0, left: 0, width: '100%', height: '100%' }}
    />
  );
}
