/* The phone's Poloha reads: where the property is (the canonical ad's point, its
 * granularity, label, zoom and a note when the point is coarse) and Mapy's static map
 * of it, which the API streams on every view and never stores (Mapy's terms). An <img>
 * cannot send the JWT, so the PNG comes through the transport as bytes and is shown
 * from a blob URL that dies with the component. */

import { useEffect, useState } from 'react';
import { useQuery } from '@tanstack/react-query';

import { ApiError, apiGet, apiGetBlob } from '@/lib/api';
import { appleMapsPointUrl, googleMapsPointUrl, mapyCzPointUrl } from '@/lib/geoLinks';

export interface PropertyLocation {
  lat: number;
  lon: number;
  granularity: string | null;
  granularity_rank: number | null;
  label: string | null;
  zoom: number;
  note: string | null;
}

/* The one map size the API serves: a 4:3 box 1080 px wide. */
export const STATIC_MAP_W = 1080;
export const STATIC_MAP_H = 810;

/* An hour, as the API's private Cache-Control. */
const HOUR = 60 * 60_000;

export const fetchPropertyLocation = (propertyId: number, signal?: AbortSignal) =>
  apiGet<PropertyLocation>(`/properties/${propertyId}/location`, undefined, signal);

export const fetchPropertyStaticMap = (propertyId: number, w: number, signal?: AbortSignal) =>
  apiGetBlob(`/properties/${propertyId}/static-map.png`, { w }, signal);

/* The API's refusal reason (`{detail: {reason}}`), when it gave one. */
export function refusalReason(err: unknown): string | null {
  if (!(err instanceof ApiError)) return null;
  const detail = (err.body as { detail?: { reason?: unknown } } | null)?.detail;
  return typeof detail?.reason === 'string' ? detail.reason : null;
}

export function usePropertyLocation(propertyId: number, enabled: boolean) {
  return useQuery({
    queryKey: ['property-location', propertyId],
    queryFn: ({ signal }) => fetchPropertyLocation(propertyId, signal),
    staleTime: HOUR,
    enabled,
  });
}

/* The map as a blob URL, minted per mount and revoked on unmount. A sheet reopened within
 * the hour is served from the query cache or the browser's private hour of HTTP cache,
 * never a second Mapy credit. */
export function useStaticMap(propertyId: number, enabled: boolean) {
  const q = useQuery({
    queryKey: ['property-static-map', propertyId, STATIC_MAP_W],
    queryFn: ({ signal }) => fetchPropertyStaticMap(propertyId, STATIC_MAP_W, signal),
    staleTime: HOUR,
    enabled,
  });
  const [url, setUrl] = useState<string | null>(null);
  useEffect(() => {
    if (!q.data) {
      setUrl(null);
      return;
    }
    const next = URL.createObjectURL(q.data);
    setUrl(next);
    return () => URL.revokeObjectURL(next);
  }, [q.data]);
  return { url, isPending: q.isPending && enabled, isError: q.isError, error: q.error };
}

/* The three map apps an iPhone hands a point to, at the zoom the point's precision allows
 * (Google's search form takes none). Here rather than in lib/geoLinks, which the first
 * visit already loads: only the phone's lazy chunks need it. */
export function phoneMapLinks(
  lat: number,
  lng: number,
  label: string | null,
  zoom: number,
): Array<{ key: 'apple' | 'google' | 'mapy'; label: string; url: string }> {
  return [
    { key: 'apple', label: 'Apple Maps', url: appleMapsPointUrl(lat, lng, label, zoom) },
    { key: 'google', label: 'Google Maps', url: googleMapsPointUrl(lat, lng) },
    { key: 'mapy', label: 'Mapy.cz', url: mapyCzPointUrl(lat, lng, zoom) },
  ];
}
