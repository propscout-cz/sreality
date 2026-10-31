/* Reads for the audit page (`location_pin_audit_mv`, migration 514).
 *
 * The relation is exactly the set W5 hides from consumers: a SERVED listing
 * (live, or the display listing of a live property) whose location the store
 * cannot answer for. It is a WORK QUEUE, not a one-off review — a row leaves it
 * the moment the resolver lane places the listing.
 *
 * TWO STATES, and the page is always looking at exactly one of them (W7-b,
 * migration 518). `pending` = the lane has not finished with the listing (no
 * verdict yet, queued for one, or the ad changed after the verdict) — normal
 * traffic, not a finding. `unresolved` = a verdict exists, nothing is queued,
 * and there is still no location — the issue. The four `quality` buckets refine
 * `unresolved` only; under `pending` there is no verdict to explain.
 *
 * One read, `fetchPinAuditPage`: a keyset page of 100 rows, the first page
 * carrying the exact count of the filtered list, so the page never counts
 * anything itself. Keyset, not offset: `.range()` is banned outside
 * fetchAllRows (it does not lift PostgREST's row cap), and the matview carries
 * a (last_seen_at, listing_id) btree that the shared applyKeyset emits against
 * directly. W1 deleted the summary RPC's reader and the nav badge: the location
 * gauge's stored cells (Health) are the one location count.
 */

import { supabase } from '@/lib/supabase';
import { pgRead, type PgReadOptions } from '@/lib/pgRead';
import {
  applyKeyset,
  nextCursorFrom,
  withKeysetColumns,
  type KeysetBuilder,
  type KeysetCursor,
} from '@/lib/keyset';
import type { SortSpec } from '@/lib/queries';

export const PIN_AUDIT_RELATION = 'location_pin_audit_mv';
export const PIN_AUDIT_PAGE_SIZE = 100;

/* The keyset tiebreaker. `property_id` — applyKeyset's default — is NOT legal
 * here: the relation is listing-grain, and `listing_id` is its primary key. */
const TIEBREAK = 'listing_id';

export type PinAuditState = 'pending' | 'unresolved';

export const PIN_AUDIT_STATES: ReadonlyArray<PinAuditState> = [
  'unresolved',
  'pending',
];

export type PinAuditQuality =
  | 'active_no_claims'
  | 'active_unresolved'
  | 'delisted_no_claims'
  | 'delisted_unresolved';

export const PIN_AUDIT_QUALITIES: ReadonlyArray<PinAuditQuality> = [
  'active_no_claims',
  'active_unresolved',
  'delisted_no_claims',
  'delisted_unresolved',
];

export interface PinAuditRow {
  listing_id: number;
  property_id: number;
  sreality_id: number | null;
  source: string;
  source_id_native: string | null;
  source_url: string | null;
  category_main: string | null;
  category_type: string | null;
  disposition: string | null;
  area_m2: number | null;
  /* The ONE place string (rule 25). NULL on most of this set by construction —
   * that is the finding, not a defect. */
  display_label: string | null;
  price_czk: number | null;
  is_active: boolean;
  first_seen_at: string;
  last_seen_at: string;
  country_status: string | null;
  granularity: string | null;
  match_confidence: string | null;
  resolver_version: string | null;
  resolved_at: string | null;
  has_row: boolean;
  has_claims: boolean;
  /* Evidence under an ACTIVE contract — the only evidence there is, since W6-a
   * deletes a claim the moment its contract version is retired. Measured
   * 2026-09-13 this is exactly the `has_claims` set: the lane has consumed
   * everything a live contract offers, so there is no backlog to wait for. */
  claims_now: boolean;
  sibling_has_pin: boolean;
  quality: PinAuditQuality;
  /* Has the lane finished with this listing? (migration 518) */
  state: PinAuditState;
  refreshed_at: string;
}

export interface PinAuditFilters {
  /* NOT fail-open: every read is scoped to ONE state, because the two sets
   * answer different questions and a total over both means nothing. */
  state: PinAuditState;
  /* Empty array = no constraint on that axis (the fail-open filter idiom). */
  sources: ReadonlyArray<string>;
  categories: ReadonlyArray<string>;
  qualities: ReadonlyArray<PinAuditQuality>;
  status: 'all' | 'active' | 'delisted';
  /* Does another listing of the same property already have a pin? That is the
   * property-level fallback the operator can take without a resolver change. */
  sibling: 'all' | 'yes' | 'no';
}

/* The default is the ISSUE. Opening the page on `pending` would show a list
 * that empties itself and hide the one the operator came for. */
export const EMPTY_PIN_AUDIT_FILTERS: PinAuditFilters = {
  state: 'unresolved',
  sources: [],
  categories: [],
  qualities: [],
  status: 'all',
  sibling: 'all',
};

const ROW_COLS = [
  'listing_id', 'property_id', 'sreality_id', 'source', 'source_id_native',
  'source_url', 'category_main', 'category_type', 'disposition', 'area_m2',
  'display_label', 'price_czk', 'is_active', 'first_seen_at',
  'last_seen_at', 'country_status', 'granularity',
  'match_confidence', 'resolver_version', 'resolved_at', 'has_row',
  'has_claims', 'claims_now', 'sibling_has_pin', 'quality', 'state',
  'refreshed_at',
].join(',');

/* A PostgREST builder narrowed to the filter methods this module uses, so the
 * helper below is shared without leaking supabase-js's generics. */
interface FilterBuilder {
  in: (column: string, values: readonly string[]) => FilterBuilder;
  eq: (column: string, value: boolean | string) => FilterBuilder;
}

function applyPinAuditFilters<T extends FilterBuilder>(
  query: T,
  f: PinAuditFilters,
): T {
  let q = query.eq('state', f.state) as T;
  if (f.sources.length > 0) q = q.in('source', f.sources) as T;
  if (f.categories.length > 0) q = q.in('category_main', f.categories) as T;
  /* The quality buckets explain a VERDICT, so they only mean something under
   * `unresolved` — carrying them into `pending` would silently hide rows. */
  if (f.state === 'unresolved' && f.qualities.length > 0) {
    q = q.in('quality', f.qualities) as T;
  }
  if (f.status !== 'all') q = q.eq('is_active', f.status === 'active') as T;
  if (f.sibling !== 'all') {
    q = q.eq('sibling_has_pin', f.sibling === 'yes') as T;
  }
  return q;
}

export interface PinAuditPage {
  rows: PinAuditRow[];
  nextCursor: KeysetCursor | null;
  /* The filtered list's exact size, read with the first page only (null after). */
  total: number | null;
}

export const fetchPinAuditPage = async (
  f: PinAuditFilters,
  sort: SortSpec,
  cursor: KeysetCursor | null,
  { signal }: PgReadOptions = {},
): Promise<PinAuditPage> => {
  const base = supabase
    .from(PIN_AUDIT_RELATION)
    .select(withKeysetColumns(ROW_COLS, sort, TIEBREAK), cursor == null ? { count: 'exact' } : undefined);
  const scoped = applyPinAuditFilters(base as unknown as FilterBuilder, f);
  const keyed = applyKeyset(
    scoped as unknown as KeysetBuilder,
    sort,
    cursor,
    TIEBREAK,
  ) as unknown as typeof base;
  const { data, count } = await pgRead<PinAuditRow[] | null>(keyed.limit(PIN_AUDIT_PAGE_SIZE), { signal });
  const rows = data ?? [];
  return {
    rows,
    total: cursor == null ? count : null,
    nextCursor: nextCursorFrom(
      rows as unknown as Record<string, unknown>[],
      sort,
      TIEBREAK,
    ),
  };
};
