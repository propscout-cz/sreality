/* Location inspector + corrections — typed wrappers over the admin-gated
 * `/location/*` API. The location tables are service-role-only, so the
 * identity-gated API is the SPA's only path to them. Envelope shape follows
 * toolkit convention: `{ data, metadata }`.
 *
 * W1 deleted the two aggregate reads (`/location/quality/*`): the Location
 * Quality page renders the location gauge's stored cells (lib/healthChecks). */

import { apiGet, apiPost } from './api';

export type InspectorClaim = {
  id: number;
  claim_type: string;
  surface: string;
  extraction_method: string;
  value_text: string | null;
  value_num: number | null;
  licence_class: string;
  claim_confidence: string | null;
  blur_evidence: string;
  first_observed_at: string;
  subject_scoped: boolean | null;
};

export type Inspector = {
  listing_id: number;
  projection: Record<string, unknown> | null;
  claims: InspectorClaim[];
};

export type CorrectionResult = {
  listing_id: number;
  source: string;
  claim_type: string;
  value_text: string;
  inserted: boolean;
  restatement: boolean;
  enqueued: boolean;
  registry_echo: Record<string, unknown> | null;
  resolved: boolean;
  projection: Record<string, unknown> | null;
};

type Envelope<T> = { data: T; metadata?: Record<string, unknown> };

export const LOCATION_SOURCES = [
  'bezrealitky', 'sreality', 'bazos', 'idnes', 'mmreality', 'remax',
  'ceskereality', 'realitymix', 'maxima', 'web',
] as const;

export const fetchInspector = (listingId: string) => {
  const trimmed = listingId.trim();
  return /^\d+$/.test(trimmed)
    ? apiGet<Envelope<Inspector>>(`/location/listing/${trimmed}`)
    : Promise.reject(new Error('enter a numeric listing id'));
};

export const fetchInspectorByNative = (source: string, nativeId: string) =>
  apiGet<Envelope<Inspector>>(
    `/location/listing/by-native/${source}/${encodeURIComponent(nativeId.trim())}`,
  );

export const submitCorrection = (input: {
  listing_id: number;
  claim_type: string;
  value_text: string;
  note?: string;
}) =>
  apiPost<Envelope<CorrectionResult>>('/location/corrections', input);
