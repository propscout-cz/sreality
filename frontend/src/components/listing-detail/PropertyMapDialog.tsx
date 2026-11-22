import { useId, useRef } from 'react';

import Dialog, { DialogClose } from '@/components/Dialog';
import MapOverlayControls, { MapOverlayLegends } from '@/components/MapOverlayControls';
import {
  LayersIcon,
  MinusIcon,
  PlusIcon,
  RailButton,
  RailFlyout,
  RailGroup,
  useRailPanel,
} from '@/components/MapRail';
import { MapLegendDock } from '@/components/OverlayChip';
import { layerGroupsOn, registrySectionsOn, useRememberedLayers } from '@/lib/mapOverlays';
import { usePointMap } from '@/lib/usePointMap';

export interface MapView {
  center: [number, number];
  zoom: number;
}

/* The property page's large map (operator 2026-11-20): the minimap's view on most of the
 * screen, with Browse's rail, the same Vrstvy list every map offers (the basemap, the
 * reference layers, the terrain) and the keys of what is on. A pick here is the
 * remembered selection, so the minimap behind it draws it too. Opens on the list. */
export default function PropertyMapDialog({
  lat,
  lng,
  isActive,
  label,
  view,
  onClose,
}: {
  lat: number;
  lng: number;
  isActive: boolean;
  label?: string | null;
  view: MapView;
  onClose: () => void;
}) {
  const headingId = useId();
  return (
    <Dialog
      open
      onClose={onClose}
      labelledBy={headingId}
      className="flex flex-col w-[min(1200px,100%)] h-[min(860px,100%)]"
    >
      <div className="flex shrink-0 items-center justify-between gap-3 border-b border-[var(--color-rule)] px-4 py-2">
        <h2 id={headingId} className="min-w-0 truncate text-[0.9rem] font-semibold text-[var(--color-ink)]">
          Mapa
          {label && <span className="font-normal text-[var(--color-ink-2)]"> · {label}</span>}
        </h2>
        <DialogClose onClick={onClose} label="Zavřít mapu" />
      </div>
      <LargeMap lat={lat} lng={lng} isActive={isActive} view={view} />
    </Dialog>
  );
}

function LargeMap({ lat, lng, isActive, view }: { lat: number; lng: number; isActive: boolean; view: MapView }) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [layers, setLayers] = useRememberedLayers();
  const { map, registry, zoom, places } = usePointMap(
    containerRef,
    {
      lng,
      lat,
      active: isActive,
      zoom: view.zoom,
      center: view.center,
      options: { navigationControl: false },
      foldAttribution: false,
    },
    layers,
  );
  const rail = useRailPanel<'layers'>('layers');

  return (
    /* `@container`: the rail's panel turns into a bottom sheet on a narrow map (MapRail). */
    <div className="@container relative min-h-0 flex-1">
      <div
        ref={containerRef}
        className="absolute inset-0"
        style={{ position: 'absolute', top: 0, right: 0, bottom: 0, left: 0, width: '100%', height: '100%' }}
      />
      {/* z-4: the panel it opens (a bottom sheet on a narrow map) covers the keys. */}
      <div ref={rail.rootRef} className="pointer-events-none absolute z-[4] inset-0">
        <div className="absolute top-2 right-2 flex flex-col gap-2">
          <RailGroup>
            <RailButton
              label="Vrstvy"
              expanded={rail.open === 'layers'}
              badge={layerGroupsOn(layers, registry)}
              onClick={() => rail.toggle('layers')}
            >
              <LayersIcon />
            </RailButton>
          </RailGroup>
          <RailGroup>
            <RailButton label="Přiblížit" onClick={() => map?.zoomIn()}>
              <PlusIcon />
            </RailButton>
            <RailButton label="Oddálit" onClick={() => map?.zoomOut()}>
              <MinusIcon />
            </RailButton>
          </RailGroup>
        </div>
        {rail.open === 'layers' && (
          <RailFlyout title="Vrstvy" onClose={rail.close}>
            <MapOverlayControls registry={registry} selection={layers} onChange={setLayers} zoom={zoom} />
          </RailFlyout>
        )}
      </div>
      {registrySectionsOn(layers, registry) > 0 && (
        <MapLegendDock centreMark={places.size > 0}>
          <MapOverlayLegends registry={registry} selection={layers} places={places} onChange={setLayers} />
        </MapLegendDock>
      )}
    </div>
  );
}
