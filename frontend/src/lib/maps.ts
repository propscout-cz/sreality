/* Mapy.cz suggest + resolve via the FastAPI proxy. The frontend never
 * holds the Mapy.cz key — see api/maps.py for the server side. Also the
 * sreality Cenová mapa place lookup (api/sreality_price_map.py), proxied
 * for a different reason: sreality's suggest sends no CORS header.
 *
 * `MapySuggestion` mirrors the subset of Mapy.cz's /v1/suggest item shape
 * we actually use. Unknown fields pass through untouched. */

import { apiGet, apiPost, ApiError } from './api';

export interface MapySuggestionPosition {
  lon: number;
  lat: number;
}

export interface MapyRegionalEntry {
  name: string;
  type: string;
}

export interface MapySuggestion {
  name: string;
  label: string;
  type: string;
  position?: MapySuggestionPosition;
  location?: string;
  regionalStructure?: MapyRegionalEntry[];
  [extra: string]: unknown;
}

/* What `/maps/resolve` answers for a pick: ONE RÚIAN unit at the level picked.
 * `id` is its code, or null when the register cannot bind the pick; `level` is
 * null for a pick that names no level (a country). `name` is the register's name
 * of the unit, `context` the register name of its obec below the obec level. */
export interface LocationResolution {
  level: string | null;
  id: number | null;
  name: string | null;
  context: string | null;
}

interface SuggestResponse {
  items: MapySuggestion[];
}

export const SUGGEST_NOT_CONFIGURED = 'suggest_not_configured';

export const fetchSuggest = async (
  query: string,
  signal?: AbortSignal,
): Promise<MapySuggestion[]> => {
  try {
    const res = await apiGet<SuggestResponse>(
      '/maps/suggest',
      { query, limit: 10, lang: 'cs' },
      signal,
    );
    return res.items ?? [];
  } catch (err) {
    if (err instanceof ApiError && err.status === 503) {
      throw new Error(SUGGEST_NOT_CONFIGURED);
    }
    throw err;
  }
};

/* The request is strict (no nulls), so a pick without a position is refused here,
 * the same way a failed call is. */
export const resolveSuggestion = (pick: MapySuggestion): Promise<LocationResolution> => {
  if (!pick.position) return Promise.reject(new Error('suggestion has no position'));
  return apiPost<LocationResolution>('/maps/resolve', {
    lat: pick.position.lat,
    lng: pick.position.lon,
    type: pick.type,
    /* Read server-side for the levels with no polygon to point-in-polygon
     * against: the quarter and the street (`name`, or an address pick's
     * `regional.street` entry). */
    name: pick.name,
    regional_structure: (pick.regionalStructure ?? []).map(({ name, type }) => ({ name, type })),
  });
};

/* Short, human-readable label for the suggestion's type — rendered as a
 * subdued tag next to the suggestion's main label. Czech UI copy. */
export const typeBadge = (type: string): string => {
  if (type.startsWith('regional.address')) return 'Adresa';
  if (type === 'regional.street') return 'Ulice';
  if (type === 'regional.municipality_part') return 'Část obce';
  if (type === 'regional.municipality') return 'Obec';
  if (type === 'regional.region.district') return 'Okres';
  if (type === 'regional.region') return 'Kraj';
  if (type === 'regional.country') return 'Stát';
  return type.replace(/^regional\./, '');
};

/* A listing's place on sreality's Cenová mapa. `url` is null when nothing near
 * the listing's point matched — the caller links the national map instead.
 * `level` says how specific the match is: a street, a part of town, or the town. */
export interface SrealityPriceMapPlace {
  url: string | null;
  level: 'street' | 'ward' | 'municipality' | null;
  name: string | null;
}

export const fetchSrealityPriceMap = (
  label: string,
  lat: number,
  lng: number,
  signal?: AbortSignal,
): Promise<SrealityPriceMapPlace> =>
  apiGet<SrealityPriceMapPlace>('/maps/sreality-price-map', { label, lat, lng }, signal);
