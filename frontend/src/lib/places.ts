/* Own place search (POST /places/suggest, location_data/place_search.py): typed text to
 * register places that are already chips, for every place box (#2453). A row is a chip
 * {level, id, name, context} with a fly-to point or none; a městská část is a shortcut
 * whose `places` are its quarters' chips; a door (`adresni_misto`) carries its street's
 * chip in `street`, null for a door with no street. */

import { apiPost } from './api';
import { DISTRICT_LEVEL_LABEL, type LocationLevel } from './districtCodes';
import { ZOOM_BY_LEVEL } from './zoomByLevel';

export type PlaceLevel = LocationLevel | 'momc' | 'adresni_misto';

/* A quarter of a shortcut or a door's street: a chip as the route spells it. */
export interface PlaceChipRow {
  level: LocationLevel;
  id: number;
  name: string;
  context: string | null;
}

export interface PlaceRow {
  level: PlaceLevel;
  id: number;
  name: string;
  context: string | null;
  point: { lat: number; lng: number } | null;
  places?: ReadonlyArray<PlaceChipRow>;
  street?: PlaceChipRow | null;
}

/* The route's own bound (api/schemas.PlaceSuggestIn). */
const SUGGEST_LIMIT = 10;

export const fetchPlaces = async (query: string, signal?: AbortSignal): Promise<PlaceRow[]> =>
  (await apiPost<{ items: PlaceRow[] }>('/places/suggest', { query, limit: SUGGEST_LIMIT }, signal)).items ?? [];

/* Each row's level, as the list labels it (ruling 2026-11-22: "Městská část"). */
export const PLACE_LEVEL_LABEL: Record<PlaceLevel, string> = {
  ...DISTRICT_LEVEL_LABEL,
  momc: 'Městská část',
  adresni_misto: 'Adresa',
};

/* Where the map flies for a picked row: its point at its level's zoom; none without a point. */
export const flyToPlace = (row: PlaceRow): { lat: number; lng: number; zoom: number } | null =>
  row.point ? { lat: row.point.lat, lng: row.point.lng, zoom: ZOOM_BY_LEVEL[row.level] } : null;
