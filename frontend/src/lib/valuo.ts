/* The property page's Valuo section over GET/POST /properties/{id}/valuo (api/valuo.py page() /
 * present()). No Valuo vocabulary lives here: parameters, labels and options arrive in `fields`,
 * each value's origin in `provenance`. */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { apiGet, apiPost, type ApiError } from './api';
import { fmtCount, fmtDistanceM, fmtPct, fmtTomDays } from './format';

export type ValuoValue = string | number | boolean | null;
export type ValuoValues = Record<string, ValuoValue>;
export type Provenance = { class: 'ad' | 'property' | 'inferred' | 'assumed' | 'missing' | 'user'; note: string | null };
export type ValuoField = {
  name: string; label: string; type: 'enum' | 'number' | 'integer' | 'boolean' | 'date' | 'text'; unit: string | null;
  options: { value: string; label: string }[]; required: boolean; group: 'basic' | 'more'; fixed: boolean;
  max_length: number | null; // a text box's limit, the server's own
};
/* Valuo's answer under Valuo's own names. The band is range_price, never min/max (Valuo's docs keep them apart). */
type ResultKey = 'avg_price' | 'avg_price_m2' | 'std_price_m2' | 'calc_area';
type Band = 'range_price' | 'range_price_m2';
/* One photo of a comparable: Valuo's 450 px and 1200 px copies. */
export type ValuoPhoto = { thumb: string; full: string };
/* One of Valuo's comparables, in Valuo's order and under Valuo's names; `address`, `photos` and `advert_url`
 * come from Valuo's advert detail, absent when that call was not made or failed. */
export type ValuoRecord = {
  id: string | number; place: string | null; lat: number | null; lon: number | null; local_type: string | null;
  calc_area: number | null; lot_area: number | null; floor: number | null; total_floors: number | null;
  rating: string | null; new_building: boolean | null; kind: string | null; property_type: string | null;
  price: number | null; price_m2: number | null; sale_price: boolean | null; // true: a realised sale, not an ask
  adjusted_price: number | null; adjusted_price_m2: number | null; distance: number | null; score_prct: number | null;
  days: number | null; first_seen_at: string | null; last_seen_at: string | null;
  address?: string | null; photos?: ValuoPhoto[]; advert_url?: string | null;
};
/* What the answer stands on, as Valuo reports it: avg_score 0–1, distances in metres, age in days. */
export type ValuoSummary = Record<'count' | 'avg_distance' | 'avg_age' | 'avg_duration' | 'avg_score' | 'distance' | 'age', number | null>
  & { not_enough: boolean };
/* A parameter Valuo refused; `field` is the request key it names, null when it names none. */
export type ValuoIssue = { field: string | null; message: string };
export type Valuation = {
  id: number; created_at: string; environment: 'sandbox' | 'production'; status: 'pending' | 'success' | 'failed';
  request: ValuoValues; provenance: Record<string, Provenance>;
  result: (Record<ResultKey, number | null> & Partial<Record<Band, number[] | null>> & { flags: unknown }) | null;
  error: { code: string; message: string; form_errors?: ValuoIssue[] } | null;
  // Optional because the API and the SPA deploy apart: for a few minutes (or after an API rollback) this page
  // can meet an API that predates them. Rows from before Valuo v3 carry no records or summary at all.
  kind_label?: string; records?: ValuoRecord[]; summary?: ValuoSummary | null;
  ppm2_basis: string | null; // the REQUEST's basis token (rule 23): a flipped deal type moves it
};
export type ValuoPage = {
  environment: Valuation['environment'] | null; refusal: { code: string; message: string } | null;
  subject: { label: string; location: { label: string; note: string | null } | null };
  fields: ValuoField[]; defaults: ValuoValues; provenance: Record<string, Provenance>; blocking: string[]; history: Valuation[];
};
export type ValuoRun = { reused: boolean; valuation: Valuation };

/* The overrides a POST sends: only the user's changes from the defaults, null where one was cleared. */
export function withEdit(edits: ValuoValues, defaults: ValuoValues, name: string, value: ValuoValue): ValuoValues {
  const rest = { ...edits };
  delete rest[name];
  return value === (defaults[name] ?? null) ? rest : { ...rest, [name]: value };
}

export const missingRequired = (fields: readonly ValuoField[], values: ValuoValues): string[] =>
  fields.filter((f) => f.required && values[f.name] == null).map((f) => f.label);

/* The issues a failed row's Valuo refusal names; [] for any other row. */
export const formErrors = (v: Valuation | null | undefined): ValuoIssue[] =>
  v?.error?.code === 'form_errors' ? (v.error.form_errors ?? []) : [];

/* Valuo's band as [low, high], or null when the answer carries none. */
export function band(v: Valuation, key: Band): [number, number] | null {
  const [low, high] = v.result?.[key] ?? [];
  return typeof low === 'number' && typeof high === 'number' ? [low, high] : null;
}

/* Valuo's unrounded price as shown (its docs advise rounding): a sale to 10 000 Kč, a monthly rent to 100 Kč. */
export const shownPrice = (n: number, lease: boolean): number => {
  const step = lease ? 100 : 10_000;
  return Math.round(n / step) * step;
};

/* How far the answer reaches and how close its comparables sit: only the parts Valuo reported. */
export function reliability(s: ValuoSummary): string[] {
  const reach = [s.distance != null && fmtDistanceM(s.distance), s.age != null && fmtTomDays(s.age)].filter(Boolean);
  return [
    s.count != null && `${fmtCount(s.count)} srovnání`,
    s.avg_distance != null && `ø ${fmtDistanceM(s.avg_distance)}`,
    s.avg_score != null && `shoda ${fmtPct(s.avg_score * 100, { digits: 0 })}`,
    reach.length > 0 && `okruh ${reach.join(' / ')}`,
  ].filter((p): p is string => typeof p === 'string');
}

export const optionLabel = (fields: readonly ValuoField[], name: string, value: ValuoValue): string =>
  fields.find((f) => f.name === name)?.options.find((o) => o.value === value)?.label ?? String(value ?? '—');

const pageKey = (propertyId: number) => ['valuo', propertyId] as const;

export const useValuoPage = (propertyId: number) =>
  useQuery<ValuoPage | null, Error>({
    queryKey: pageKey(propertyId),
    queryFn: () => apiGet<ValuoPage | null>(`/properties/${propertyId}/valuo`),
    staleTime: 30_000,
  });

/* One paid call, never retried (a retry is a second payment); a new row lands on top of the cached
 * history, a reused one stays where it sits. */
export function useRunValuo(propertyId: number) {
  const qc = useQueryClient();
  return useMutation<ValuoRun, ApiError, ValuoValues>({
    mutationFn: (overrides) => apiPost<ValuoRun>(`/properties/${propertyId}/valuo`, { overrides }),
    retry: false,
    onSuccess: ({ valuation: v }) =>
      qc.setQueryData<ValuoPage | null>(pageKey(propertyId), (p) => p && {
        ...p, history: p.history.some((h) => h.id === v.id) ? p.history.map((h) => (h.id === v.id ? v : h)) : [v, ...p.history],
      }),
  });
}
