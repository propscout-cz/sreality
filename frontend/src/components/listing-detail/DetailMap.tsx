import { useEffect, useRef } from 'react';

import { createMap } from '@/lib/basemap';

interface Props {
  lat: number;
  lng: number;
  isActive: boolean;
  /* Height override (default h-40) — the listing header embeds the map in
   * its top-right column with a taller frame. */
  heightClass?: string;
}

/* The header's minimap: where the property is, nothing else (operator 2026-10-23)
 * — zoom is its one control; no overlay toggles, no scale. The attribution the
 * tiles' licence asks for stays, folded into its (i) button until opened. */
export default function DetailMap({ lat, lng, isActive, heightClass }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!containerRef.current) return;
    const container = containerRef.current;
    const map = createMap(container, {
      center: [lng, lat],
      // Two levels out from the original 14.5 — the header map is for grasping
      // the neighbourhood context (river, arterials, districts), not the street.
      zoom: 12.5,
      interactive: true,
      cooperativeGestures: false,
      scaleControl: false,
    });

    map.on('load', () => {
      // MapLibre opens the compact attribution on a wide map; fold it to the (i).
      container.querySelector('.maplibregl-ctrl-attrib')?.classList.remove('maplibregl-compact-show');
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
          'circle-opacity': isActive ? 1 : 0.6,
        },
      });
    });

    return () => map.remove();
  }, [lat, lng, isActive]);

  return (
    <div
      className={[
        'relative rounded-[var(--radius-md)] overflow-hidden border border-[var(--color-rule)]',
        heightClass ?? 'h-40',
      ].join(' ')}
    >
      <div
        ref={containerRef}
        className="absolute inset-0"
        style={{ position: 'absolute', top: 0, right: 0, bottom: 0, left: 0, width: '100%', height: '100%' }}
      />
    </div>
  );
}
