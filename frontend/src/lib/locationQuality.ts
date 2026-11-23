/* The address picker's write (location W3b) — a typed wrapper over the admin-gated
 * `/location/*` API: the location tables are service-role-only, so the identity-gated API is
 * the SPA's only path to them. Envelope shape follows toolkit convention: `{ data }`.
 *
 * W1 deleted the two aggregate reads (`/location/quality/*`): the Location Quality page
 * renders the location gauge's stored cells (lib/healthChecks). W3b deleted the inspector. */

import { apiPost } from './api';

/* The picked register entity: an address point (kód ADM), a street or an obec, by code. */
export type LocationPick = { level: 'adresni_misto' | 'ulice' | 'obec'; code: number };

export type PickResult = {
  property_id: number;
  pick: LocationPick | null;
  ads: { listing_id: number; source: string; location_ad: boolean }[];
  written: number;
  enqueued: number;
  /* The location ad re-resolved at once; false = the drain converges within minutes. */
  resolved: boolean;
  location: { label: string | null; granularity: string | null } | null;
  /* Whether the location ad now binds the picked code; null for a clear. */
  held: boolean | null;
};

export const LOCATION_SOURCES = [
  'bezrealitky', 'sreality', 'bazos', 'idnes', 'mmreality', 'remax',
  'ceskereality', 'realitymix', 'maxima', 'web',
] as const;

/* Writes the pick on every ad of the property; `pick: null` hands them back to their own words. */
export const submitLocationPick = (input: { property_id: number; pick: LocationPick | null }) =>
  apiPost<{ data: PickResult }>('/location/corrections', input);
