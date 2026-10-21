/* MapAreaControl — the watchdog's map box, edited the way Browse makes one.
 *
 * A small map shows the box as a dashed rectangle. Pan and zoom, then "Use
 * current view" makes the box what the map shows — the same gesture that sets
 * Browse's viewport filter — and "Clear" drops it. No corner handles and no
 * number inputs: the box is a view, and a view is what the operator sees.
 *
 * State shape (`MapBounds | null`): west < east, south < north in WGS84
 * degrees, or null for no map area. The Watchdog editor is its one site;
 * Browse's sidebar draws the centre+radius circle with LocationControl. Both
 * paint through lib/mapShape. Carries maplibre-gl, so import it lazily. */

import { useEffect, useRef } from 'react';
import type { Map as MaplibreMap } from 'maplibre-gl';

import { createMap } from '@/lib/basemap';
import { mapAreaLabel, type MapBounds } from '@/lib/filters';
import { addDashedShape, boxRing, setDashedShape } from '@/lib/mapShape';

const PRAGUE = { lng: 14.4378, lat: 50.0755, zoom: 11 };
const BOX = 'map-area';
const FIT_PADDING = 24;

interface MapAreaControlProps {
  value: MapBounds | null;
  onChange: (next: MapBounds | null) => void;
  /** Optional explanation rendered under the buttons. */
  hint?: string;
}

export function MapAreaControl({ value, onChange, hint }: MapAreaControlProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<MaplibreMap | null>(null);
  // Latest value in a ref so the one-time `load` handler draws what is current.
  const valueRef = useRef(value);
  valueRef.current = value;
  /* The box this control emitted itself. A box from outside (the saved
   * watchdog arriving) fits the map to it; a box taken FROM the map must not —
   * the fit pads the view, so the next "Use current view" would grow it. */
  const emittedRef = useRef<MapBounds | null>(null);

  useEffect(() => {
    if (!containerRef.current) return;
    const map = createMap(containerRef.current, {
      center: [PRAGUE.lng, PRAGUE.lat],
      zoom: PRAGUE.zoom,
      keyboard: false,
    });
    mapRef.current = map;
    map.on('load', () => {
      addDashedShape(map, BOX);
      show(map, valueRef.current, true);
    });
    return () => {
      map.remove();
      mapRef.current = null;
    };
  }, []);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !map.isStyleLoaded()) return;
    show(map, value, value !== emittedRef.current);
  }, [value]);

  const useCurrentView = () => {
    const map = mapRef.current;
    if (!map) return;
    const b = map.getBounds();
    const next: MapBounds = {
      west: b.getWest(),
      south: b.getSouth(),
      east: b.getEast(),
      north: b.getNorth(),
    };
    emittedRef.current = next;
    onChange(next);
  };

  return (
    <div className="space-y-2">
      <div
        ref={containerRef}
        className="w-full h-[240px] rounded-[var(--radius-sm)] overflow-hidden border border-[var(--color-rule)]"
      />
      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={useCurrentView}
          className="px-2 py-1.5 text-xs rounded-[var(--radius-sm)] border border-[var(--color-copper)] text-[var(--color-copper)] hover:bg-[var(--color-copper-soft)]/60 transition-colors"
        >
          Use current view
        </button>
        <button
          type="button"
          onClick={() => onChange(null)}
          className="px-2 py-1.5 text-xs rounded-[var(--radius-sm)] border border-[var(--color-rule)] text-[var(--color-ink-3)] hover:text-[var(--color-ink-2)] hover:border-[var(--color-rule-strong)] transition-colors"
          aria-label="Clear map area"
          disabled={value == null}
        >
          Clear
        </button>
        <span className="text-[0.7rem] text-[var(--color-ink-3)]">
          {value ? mapAreaLabel(value) : 'No map area — the whole country.'}
        </span>
      </div>
      {hint ? (
        <p className="text-[0.7rem] text-[var(--color-ink-4)]">{hint}</p>
      ) : null}
    </div>
  );
}

function show(map: MaplibreMap, value: MapBounds | null, fit: boolean): void {
  setDashedShape(map, BOX, value ? boxRing(value) : null);
  if (value && fit) {
    map.fitBounds(
      [[value.west, value.south], [value.east, value.north]],
      { padding: FIT_PADDING, duration: 0 },
    );
  }
}
