import { useEffect, useRef, useState } from 'react';
import maplibregl from 'maplibre-gl';

import MapOverlayControls from '@/components/MapOverlayControls';
import { createMap } from '@/lib/basemap';
import { useMapOverlays } from '@/lib/mapOverlayDraw';
import { useMapOverlayRegistry, useRememberedLayers } from '@/lib/mapOverlays';

interface Props {
  lat: number;
  lng: number;
  isActive: boolean;
  /* Height override (default h-40) — the listing header embeds the map in
   * its top-right column with a taller frame. */
  heightClass?: string;
}

export default function DetailMap({ lat, lng, isActive, heightClass }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  const [ready, setReady] = useState(false);
  const overlayRegistry = useMapOverlayRegistry().data;
  const [overlayLayers, setOverlayLayers] = useRememberedLayers();

  useEffect(() => {
    if (!containerRef.current) return;
    const map = createMap(containerRef.current, {
      center: [lng, lat],
      // Two levels out from the original 14.5 — the header map is for grasping
      // the neighbourhood context (river, arterials, districts), not the street.
      zoom: 12.5,
      interactive: true,
      cooperativeGestures: false,
    });
    mapRef.current = map;

    map.on('load', () => {
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
      setReady(true);
    });

    return () => {
      setReady(false);
      map.remove();
      mapRef.current = null;
    };
  }, [lat, lng, isActive]);

  useMapOverlays(mapRef.current, ready, overlayRegistry, overlayLayers, 'pin');

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
      {/* Top-left: the zoom buttons own the top-right corner, the scale and
          attribution the bottom. */}
      <div className="pointer-events-none absolute top-2 left-2 right-12">
        <MapOverlayControls
          compact
          registry={overlayRegistry}
          selection={overlayLayers}
          onChange={setOverlayLayers}
        />
      </div>
    </div>
  );
}
