/* Poloha's map on the phone: the location ad's point on the shared basemap, at the zoom
 * its precision allows, drawn with the layers this browser remembers (every map's one
 * `layers` selection, lib/usePointMap). Its Vrstvy button opens a sheet holding the same
 * list every map offers — Podklad (Mapa | Letecká), the reference layers, Reliéf a
 * vrstevnice (operator 2026-11-20) — and the keys of what is on go under the plate
 * (`legendSlot`), never on a map this small. A lazy chunk (LocationBody), so MapLibre
 * never joins the feed's first visit. */

import { useRef, useState } from 'react';
import { createPortal } from 'react-dom';

import MapOverlayControls, { MapOverlayLegends } from '@/components/MapOverlayControls';
import { CentreMark } from '@/components/OverlayChip';
import { layerGroupsOn, useRememberedLayers } from '@/lib/mapOverlays';
import { usePointMap } from '@/lib/usePointMap';

import BottomSheet from './BottomSheet';
import { PHOTO_DARK } from './CardPhotoStrip';
import { LayersIcon } from './phoneIcons';

export default function PhoneMap({
  lat,
  lon,
  zoom,
  legendSlot = null,
}: {
  lat: number;
  lon: number;
  zoom: number;
  /* Where the keys of the layers that are on go: under the plate, in the page. */
  legendSlot?: HTMLElement | null;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [layers, setLayers] = useRememberedLayers();
  const [sheet, setSheet] = useState(false);
  const map = usePointMap(
    containerRef,
    {
      lng: lon,
      lat,
      zoom,
      options: {
        cooperativeGestures: true,
        locale: { 'CooperativeGesturesHandler.MobileHelpText': 'Mapou posunete dvěma prsty' },
        navigationControl: false,
        scaleControl: false,
      },
    },
    layers,
  );

  /* Thrown here, after every hook, so MapSlot's boundary takes the map down. */
  if (map.failure) throw map.failure;

  const on = layerGroupsOn(layers, map.registry);
  return (
    <>
      <div
        ref={containerRef}
        className={`absolute inset-0 ${PHOTO_DARK}`}
        style={{ position: 'absolute', top: 0, right: 0, bottom: 0, left: 0, width: '100%', height: '100%' }}
      />
      {/* The point the rent map's area card reads. */}
      {map.places.size > 0 && <CentreMark />}
      {/* Once the map has loaded, as the layers exist only from then on; beside the map's
        * container, never in it: MapLibre owns that one's children. */}
      {map.ready && (
        <button
          type="button"
          onClick={() => setSheet(true)}
          className="absolute right-2 top-2 z-10 inline-flex h-8 items-center gap-1.5 rounded-[var(--radius-sm)] border border-[var(--color-rule-strong)] bg-[var(--color-paper)] px-2.5 text-[13px] text-[var(--color-ink)] active:bg-[var(--color-paper-2)]"
        >
          <LayersIcon className="h-4 w-4" />
          Vrstvy
          {on > 0 && <span className="font-mono tabular-nums text-[var(--color-copper)]">{on}</span>}
        </button>
      )}
      {sheet && (
        <BottomSheet label="Vrstvy" onClose={() => setSheet(false)}>
          <div className="px-4 pb-2">
            <MapOverlayControls
              tone="phone"
              registry={map.registry}
              selection={layers}
              onChange={setLayers}
              zoom={map.zoom}
            />
          </div>
        </BottomSheet>
      )}
      {legendSlot &&
        createPortal(
          <MapOverlayLegends inline registry={map.registry} selection={layers} places={map.places} onChange={setLayers} />,
          legendSlot,
        )}
    </>
  );
}
