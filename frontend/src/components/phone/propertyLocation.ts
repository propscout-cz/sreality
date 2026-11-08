/* The phone's Poloha read: where the property is (its location ad's point, its
 * granularity, precision, label and a note when the point is coarse), the zoom its map
 * opens at, and the map apps it hands the point to. */

import { useQuery } from '@tanstack/react-query';

import { apiGet } from '@/lib/api';
import { appleMapsPointUrl, googleMapsPointUrl, mapyCzPointUrl } from '@/lib/geoLinks';
import { ZOOM_BY_LEVEL, type ZoomLevel } from '@/lib/zoomByLevel';

export type Precision = 'street' | 'quarter' | 'town' | 'coarse';

export interface PropertyLocation {
  lat: number;
  lon: number;
  granularity: string | null;
  granularity_rank: number | null;
  label: string | null;
  precision: Precision;
  note: string | null;
}

/* How finely the API knows the point, as the place level the map opens on. */
const PRECISION_LEVEL: Record<Precision, ZoomLevel> = {
  street: 'ulice',
  quarter: 'cast_obce',
  town: 'obec',
  coarse: 'okres',
};

export const mapZoom = (precision: Precision): number => ZOOM_BY_LEVEL[PRECISION_LEVEL[precision]];

/* The read's key: the address picker (W3b) invalidates it after a pick. */
export const propertyLocationKeys = {
  location: (propertyId: number) => ['property-location', propertyId] as const,
};

/* An hour: a property's place rarely moves, and a pick re-reads it at once. */
const HOUR = 60 * 60_000;

export const fetchPropertyLocation = (propertyId: number, signal?: AbortSignal) =>
  apiGet<PropertyLocation>(`/properties/${propertyId}/location`, undefined, signal);

export function usePropertyLocation(propertyId: number, enabled: boolean) {
  return useQuery({
    queryKey: propertyLocationKeys.location(propertyId),
    queryFn: ({ signal }) => fetchPropertyLocation(propertyId, signal),
    staleTime: HOUR,
    enabled,
  });
}

/* The three map apps an iPhone hands a point to, at the zoom the point's precision allows
 * (Google's search form takes none). Here rather than in lib/geoLinks, which the first
 * visit already loads: only the phone's lazy chunks need it. The apps count zoom on 256 px
 * tiles, MapLibre on 512 px ones, so one step past the map's zoom shows the same ground. */
export function phoneMapLinks(
  lat: number,
  lng: number,
  label: string | null,
  zoom: number,
): Array<{ key: 'apple' | 'google' | 'mapy'; label: string; url: string }> {
  const appZoom = zoom + 1;
  return [
    { key: 'apple', label: 'Apple Maps', url: appleMapsPointUrl(lat, lng, label, appZoom) },
    { key: 'google', label: 'Google Maps', url: googleMapsPointUrl(lat, lng) },
    { key: 'mapy', label: 'Mapy.cz', url: mapyCzPointUrl(lat, lng, appZoom) },
  ];
}
