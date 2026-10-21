/* Hover-sync between a list surface and the map, without MapLibre: this module
 * sits in the first-visit bundle (Browse imports it), the drawing side stays in
 * the lazily loaded map chunks (lib/useMapFeatureHover). */

/* What a list surface reports for a hovered row: the hover-sync key and the
 * property's point. The map lights the matching pin by the key and drops its
 * locator halo on the point — the ROW's own coordinates, so the halo lands even
 * when the map holds no row for the property (its dot absorbed into a count mark,
 * or the cohort beyond the pin limit). A map-origin hover carries no point. */
export interface HoverTarget {
  listing_id: number;
  lat: number | null;
  lng: number | null;
}

export interface HaloPoint {
  lat: number;
  lng: number;
}

export const haloPointsOf = (targets: ReadonlyArray<HoverTarget>): HaloPoint[] =>
  targets.flatMap((t) => (t.lat != null && t.lng != null ? [{ lat: t.lat, lng: t.lng }] : []));

/* The shared hover state of a list + map surface: the ids light the matching
 * card / row / pin, the points are the map's locator halo. */
export interface HoverState {
  ids: ReadonlySet<number>;
  origin: 'map' | 'list' | null;
  points: ReadonlyArray<HaloPoint>;
}

export const NO_HOVER: HoverState = { ids: new Set(), origin: null, points: [] };

/* Its one transition. The same ids from the same origin keep the previous state
 * (no re-render); an empty hover clears it. */
export const nextHover = (
  prev: HoverState,
  ids: ReadonlyArray<number> | null,
  points: ReadonlyArray<HaloPoint>,
  origin: 'map' | 'list',
): HoverState => {
  if (ids == null || ids.length === 0) return prev.ids.size === 0 ? prev : NO_HOVER;
  if (prev.origin === origin && prev.ids.size === ids.length && ids.every((id) => prev.ids.has(id))) {
    return prev;
  }
  return { ids: new Set(ids), origin, points };
};
