/* Own place search (POST /places/suggest, location_data/place_search.py): typed text to
 * register places that are already chips, for every place box (#2453). A row is a chip
 * {level, id, name, context} with a fly-to point or none; a městská část is a shortcut
 * whose `places` are its quarters' chips; a door (`adresni_misto`) carries its street's
 * chip in `street`, null for a door with no street. */

import { apiPost, ApiError } from './api';
import { ZOOM_BY_LEVEL, type ZoomLevel } from './zoomByLevel';

export type PlaceLevel = ZoomLevel;

export interface PlaceChipRow {
  level: string;
  id: number;
  name: string;
  context: string | null;
}

export interface PlaceRow extends PlaceChipRow {
  level: PlaceLevel;
  point: { lat: number; lng: number } | null;
  places?: ReadonlyArray<PlaceChipRow>;
  street?: PlaceChipRow | null;
}

export const PLACES_UNAVAILABLE = 'place_search_unavailable';

/* The route's own bound (api/schemas.PlaceSuggestIn). */
const SUGGEST_LIMIT = 10;

export const fetchPlaces = async (query: string, signal?: AbortSignal): Promise<PlaceRow[]> => {
  try {
    const res = await apiPost<{ items: PlaceRow[] }>('/places/suggest', { query, limit: SUGGEST_LIMIT }, signal);
    return res.items ?? [];
  } catch (err) {
    if (err instanceof ApiError && err.status === 503) throw new Error(PLACES_UNAVAILABLE);
    throw err;
  }
};

/* Each row's level, as the list labels it (ruling 2026-11-22: "Městská část"). */
export const PLACE_LEVEL_LABEL: Record<PlaceLevel, string> = {
  kraj: 'Kraj',
  okres: 'Okres',
  obec: 'Obec',
  momc: 'Městská část',
  cast_obce: 'Část obce',
  ulice: 'Ulice',
  adresni_misto: 'Adresa',
};

/* Where the map flies for a picked row: its point at its level's zoom; none without a point. */
export const flyToPlace = (row: PlaceRow): { lat: number; lng: number; zoom: number } | null =>
  row.point ? { lat: row.point.lat, lng: row.point.lng, zoom: ZOOM_BY_LEVEL[row.level] } : null;
