/* The property page's Valuo section over GET/POST /properties/{id}/valuo (api/valuo.py page() /
 * present()). No Valuo vocabulary lives here: parameters, labels and options arrive in `fields`,
 * each value's origin in `provenance`. */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { apiGet, apiPost, type ApiError } from './api';

export type ValuoValue = string | number | boolean | null;
export type ValuoValues = Record<string, ValuoValue>;
export type Provenance = { class: 'ad' | 'property' | 'inferred' | 'assumed' | 'missing' | 'user'; note: string | null };
export type ValuoField = {
  name: string; label: string; type: 'enum' | 'number' | 'integer' | 'boolean' | 'date' | 'text'; unit: string | null;
  options: { value: string; label: string }[]; required: boolean; group: 'basic' | 'more'; fixed: boolean;
};
/* Valuo's answer under Valuo's own names (min/max/std are not quartiles). */
type ResultKey = 'avg_price' | 'min_price' | 'max_price' | 'avg_price_m2' | 'min_price_m2' | 'max_price_m2'
  | 'std_price_m2' | 'calc_area' | 'distance';
export type Valuation = {
  id: number; created_at: string; environment: 'sandbox' | 'production'; status: 'pending' | 'success' | 'failed';
  request: ValuoValues; provenance: Record<string, Provenance>; result: (Record<ResultKey, number | null> & { flags: unknown }) | null;
  ignored_ids: string[]; error: { code: string; message: string } | null;
  // Czech from the server, so a page with no fields still reads; optional because the API and the SPA deploy
  // apart: for a few minutes (or after an API rollback) this page can meet an API that predates them.
  ignored_labels?: string[]; kind_label?: string;
  ppm2_basis: string | null; // the REQUEST's basis token (rule 23): a flipped deal type moves it
};
export type ValuoPage = {
  environment: Valuation['environment'] | null; refusal: { code: string; message: string } | null;
  subject: { label: string; location: { label: string; note: string | null } | null };
  fields: ValuoField[]; defaults: ValuoValues; provenance: Record<string, Provenance>; blocking: string[]; history: Valuation[];
};
export type ValuoRun = { reused: boolean; valuation: Valuation };

/* UNVERIFIED until the sandbox probe (P1): a lease price reads as MONTHLY, as the server's lease basis does. */
export const LEASE_PERIOD = '/měs';

/* The overrides a POST sends: only the user's changes from the defaults, null where one was cleared. */
export function withEdit(edits: ValuoValues, defaults: ValuoValues, name: string, value: ValuoValue): ValuoValues {
  const rest = { ...edits };
  delete rest[name];
  return value === (defaults[name] ?? null) ? rest : { ...rest, [name]: value };
}

export const missingRequired = (fields: readonly ValuoField[], values: ValuoValues): string[] =>
  fields.filter((f) => f.required && values[f.name] == null).map((f) => f.label);

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
