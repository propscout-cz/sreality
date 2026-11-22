/* The sreality Cenová mapa place lookup (api/sreality_price_map.py), proxied because
 * sreality's suggest sends no CORS header. Place search is lib/places. */

import { apiGet } from './api';

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
