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
  id: string | number; place: string | null; local_type: string | null; calc_area: number | null;
  price: number | null; price_m2: number | null; sale_price: boolean | null; // true: a realised sale, not an ask
  adjusted_price: number | null; adjusted_price_m2: number | null; distance: number | null; score_prct: number | null;
  days: number | null;
  address?: string | null; photos?: ValuoPhoto[]; advert_url?: string | null;
};
/* What the answer stands on, as Valuo reports it: avg_score 0–1, distances in metres, age in days. */
export type ValuoSummary = Record<'count' | 'avg_distance' | 'avg_score' | 'distance' | 'age', number | null>
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
export type NewValuation = { reused: boolean; valuation: Valuation };

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

/* Valuo's unrounded figures as shown (its docs advise rounding): a sale to 10 000 Kč and 100 Kč/m², a monthly
 * rent to 100 Kč and 1 Kč/m². */
const roundTo = (n: number, step: number): number => Math.round(n / step) * step;
export const shownPrice = (n: number, lease: boolean): number => roundTo(n, lease ? 100 : 10_000);
export const shownPerM2 = (n: number, lease: boolean): number => roundTo(n, lease ? 1 : 100);

/* How far the answer reaches and how close its comparables sit: only the parts Valuo reported, each one unbreakable. */
export function reliability(s: ValuoSummary): string[] {
  const reach = [s.distance != null && fmtDistanceM(s.distance), s.age != null && fmtTomDays(s.age)].filter(Boolean);
  return [
    s.count != null && `${fmtCount(s.count)} srovnání`,
    s.avg_distance != null && `ø ${fmtDistanceM(s.avg_distance)}`,
    s.avg_score != null && `shoda ${fmtPct(s.avg_score * 100, { digits: 0 })}`,
    reach.length > 0 && `okruh ${reach.join(' / ')}`,
  ].filter((p): p is string => typeof p === 'string').map((p) => p.replace(/ /g, '\u00a0'));
}

export const optionLabel = (fields: readonly ValuoField[], name: string, value: ValuoValue): string =>
  fields.find((f) => f.name === name)?.options.find((o) => o.value === value)?.label ?? String(value ?? '—');

/* Our recalculation over the comparables a user keeps: ours, never Valuo's, never stored (rule 12). Valuo's average is a
 * geometric mean of its comparables' adjusted Kč/m², each weighted by its match (score_prct), a realised sale twice;
 * re-run on five real answers it lands within 0.11 % of Valuo's own. The spread is the weighted standard deviation of
 * the same logarithms. Both stay Valuo's per-m² (rule 23): the totals below multiply them by Valuo's own area. */
export const MIN_KEPT = 3;
export const BAND_FROM = 5;
export type Recalc = { avg: number; std: number };
export type RecalcBase = { area: number; ref: number };
type Priced = ValuoRecord & { adjusted_price_m2: number; score_prct: number };
const priced = (r: ValuoRecord): r is Priced =>
  typeof r.adjusted_price_m2 === 'number' && r.adjusted_price_m2 > 0 && typeof r.score_prct === 'number' && r.score_prct > 0;

export function recalc(rs: readonly ValuoRecord[]): Recalc | null {
  if (rs.length === 0 || !rs.every(priced)) return null;
  const w = rs.map((r) => r.score_prct * (r.sale_price ? 2 : 1));
  const x = rs.map((r) => Math.log(r.adjusted_price_m2));
  const sum = w.reduce((a, b) => a + b, 0);
  const mu = x.reduce((a, v, i) => a + w[i] * v, 0) / sum;
  const geo = Math.exp(mu);
  return { avg: Math.round(geo), std: Math.round(geo * Math.sqrt(x.reduce((a, v, i) => a + w[i] * (v - mu) ** 2, 0) / sum)) };
}

/* Ticking is offered only while the recipe over EVERY comparable lands within 1 % of Valuo's own per-m² average (an older
 * row, a type never measured, a changed Valuo method: the table then stays as Valuo sent it). It answers the anchors
 * the card needs: Valuo's area and average. */
export function recalcBase(v: Valuation): RecalcBase | null {
  const all = recalc(v.records ?? []);
  const ref = v.result?.avg_price_m2, area = v.result?.calc_area;
  return all != null && typeof ref === 'number' && ref > 0 && typeof area === 'number' && Math.abs(all.avg / ref - 1) <= 0.01
    ? { area, ref } : null;
}

/* A portal's own id for an advert: the last segment of its address (the hosts and slugs of Valuo's links and ours differ). */
export function advertToken(url: string | null | undefined): string | null {
  try { return new URL(url ?? '').pathname.split('/').filter(Boolean).pop() ?? null; } catch { return null; }
}

/* Valuo counts the subject's own ad as a comparable, and by then it can be a delisted one: so the match runs over EVERY ad
 * of the property, active or not. */
export function isOwnAd(r: ValuoRecord, adUrls: readonly (string | null)[]): boolean {
  const token = advertToken(r.advert_url);
  return token != null && adUrls.some((u) => advertToken(u) === token);
}

const pageKey = (propertyId: number) => ['valuo', propertyId] as const;

export const useValuoPage = (propertyId: number) =>
  useQuery<ValuoPage | null, Error>({
    queryKey: pageKey(propertyId),
    queryFn: () => apiGet<ValuoPage | null>(`/properties/${propertyId}/valuo`),
    staleTime: 30_000,
  });

/* One paid call, never retried (a retry is a second payment); a new row lands on top of the cached
 * history, a reused one stays where it sits. */
export function useNewValuation(propertyId: number) {
  const qc = useQueryClient();
  return useMutation<NewValuation, ApiError, ValuoValues>({
    mutationFn: (overrides) => apiPost<NewValuation>(`/properties/${propertyId}/valuo`, { overrides }),
    retry: false,
    onSuccess: ({ valuation: v }) =>
      qc.setQueryData<ValuoPage | null>(pageKey(propertyId), (p) => p && {
        ...p, history: p.history.some((h) => h.id === v.id) ? p.history.map((h) => (h.id === v.id ? v : h)) : [v, ...p.history],
      }),
  });
}
