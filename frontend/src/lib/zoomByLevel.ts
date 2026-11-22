/* THE zoom a map opens at for a place of each level, app-wide (MapLibre's 512 px tiles).
 * No MapLibre import here, so a non-map module can read it without pulling the map in.
 * The settlement-unit level joins with #2461. */

export type ZoomLevel = 'kraj' | 'okres' | 'obec' | 'momc' | 'cast_obce' | 'ulice' | 'adresni_misto';

export const ZOOM_BY_LEVEL: Record<ZoomLevel, number> = {
  kraj: 8,
  okres: 9,
  obec: 11,
  momc: 12,
  cast_obce: 13,
  ulice: 15,
  adresni_misto: 16,
};
