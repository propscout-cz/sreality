import { useRef, useState } from 'react';

import { LayersIcon, MinusIcon, PlusIcon, RailButton, RailGroup } from '@/components/MapRail';
import { layerGroupsOn, useRememberedLayers } from '@/lib/mapOverlays';
import { usePointMap } from '@/lib/usePointMap';

import PropertyMapDialog, { type MapView } from './PropertyMapDialog';

interface Props {
  lat: number;
  lng: number;
  isActive: boolean;
  /* Height override (default h-40) — the listing header embeds the map in
   * its top-right column with a taller frame. */
  heightClass?: string;
  /* The place, named in the large map's heading. */
  label?: string | null;
}

/* The header's minimap: where the property is, drawn with the layers this browser
 * remembers (every map's one `layers` selection). Its rail holds one Vrstvy button and
 * zoom; Vrstvy opens the large map (PropertyMapDialog) on its layer list, as a 170 px
 * map has no room for the panel or the keys (operator 2026-10-23, then 2026-11-20).
 * No scale; the attribution the tiles' licence asks for stays, folded into its (i). */
export default function DetailMap({ lat, lng, isActive, heightClass, label }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [layers] = useRememberedLayers();
  const { map, registry } = usePointMap(
    containerRef,
    {
      lng,
      lat,
      active: isActive,
      // Two levels out from the original 14.5 — the header map is for grasping
      // the neighbourhood context (river, arterials, districts), not the street.
      zoom: 12.5,
      options: { cooperativeGestures: false, navigationControl: false, scaleControl: false },
    },
    layers,
  );
  const [large, setLarge] = useState<MapView | null>(null);
  /* The large map opens on the view the minimap shows. */
  const openLarge = () => {
    const centre = map?.getCenter();
    setLarge({ center: centre ? [centre.lng, centre.lat] : [lng, lat], zoom: map?.getZoom() ?? 12.5 });
  };

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
      {/* z-4, over MapLibre's control corners (z-2), as Browse's rail. */}
      <div className="absolute z-[4] top-2 right-2 flex flex-col gap-2">
        <RailGroup>
          <RailButton label="Vrstvy (větší mapa)" badge={layerGroupsOn(layers, registry)} onClick={openLarge}>
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
      {large && (
        <PropertyMapDialog
          lat={lat}
          lng={lng}
          isActive={isActive}
          label={label}
          view={large}
          onClose={() => setLarge(null)}
        />
      )}
    </div>
  );
}
