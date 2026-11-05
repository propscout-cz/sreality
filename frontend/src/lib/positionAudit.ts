/* The location position audit (W6-c, migration 733): where every ad's point came from.
 *
 * Two reads, one vocabulary. The GRID is the location gauge's stored `positions` fact on each portal's town cell
 * ({kind: {grain: n}}, the listing cohort the gauge counts), read through Health's own query like every other number on
 * the Location quality page (ruling Q7: nothing measured twice). The LIST is `location_position_audit`, the admin-gated
 * view over the hourly `location_position_mv`: a keyset page of 100 rows, the first page carrying the exact count.
 *
 * A kind is decided by geometric identity (location_steps.position_kind_sql); `unexplained` is shown, not judged, and
 * `unclassified` is a grid-only kind: a row re-resolved since the hourly refresh, which the list cannot show yet.
 * The approximate label is the map's own rule (`pinPrecisionShort`, ruling Q3), never a second definition.
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
import { pinPrecisionShort } from '@/lib/pinPrecision';

export const POSITION_AUDIT_RELATION = 'location_position_audit';
export const POSITION_AUDIT_PAGE_SIZE = 100;
/* The relation is listing-grain: `listing_id` is its key and the keyset tiebreaker. */
const TIEBREAK = 'listing_id';

/* location_steps.POSITION_KINDS, in the resolver's precedence, then the grid-only kind. */
export const POSITION_KINDS = [
  'door', 'street_point', 'own_pin', 'part_point', 'town_point', 'unit_point', 'foreign', 'none', 'unexplained',
] as const;
export const UNCLASSIFIED = 'unclassified';

export const KIND_LABEL: Record<string, { label: string; title: string }> = {
  door: { label: 'RÚIAN door', title: 'The RÚIAN address point the ad binds' },
  street_point: { label: 'Street point', title: 'The centroid of the bound street’s doors' },
  own_pin: { label: 'Own pin', title: 'The ad’s own portal pin' },
  part_point: { label: 'Část point', title: 'The centroid of the bound část obce’s doors' },
  town_point: { label: 'Town point', title: 'The town’s RÚIAN point' },
  unit_point: { label: 'Unit point', title: 'The RÚIAN point of a KÚ, okres or kraj' },
  foreign: { label: 'Abroad', title: 'Determined abroad, with no point' },
  none: { label: 'No position', title: 'No point and not determined abroad (rule 25’s broken set)' },
  unexplained: { label: 'Unexplained', title: 'A point none of the resolver’s points explains: a finding' },
  [UNCLASSIFIED]: { label: 'Not yet classified', title: 'Re-resolved since the hourly refresh' },
};

/* location_steps.POSITION_GRAINS: the scorecard's four classes, then the rung of the rest. Each head carries the
 * map's precision words for the class's floor rung (location_granularity_rank), so Q3 reads off the grid. */
export const POSITION_GRAINS: ReadonlyArray<{ key: string; label: string; rank: number | null; always: boolean }> = [
  { key: 'address', label: 'address', rank: 90, always: true },
  { key: 'street', label: 'street', rank: 60, always: true },
  { key: 'quarter', label: 'část', rank: 50, always: true },
  { key: 'obec', label: 'obec', rank: 40, always: true },
  { key: 'other', label: 'other', rank: null, always: false },
  { key: 'okres', label: 'okres', rank: 30, always: true },
  { key: 'kraj', label: 'kraj', rank: 20, always: false },
  { key: 'country', label: 'country', rank: 10, always: true },
  { key: 'unknown', label: 'unknown', rank: null, always: true },
];

/* The one approximate label: the map's rule, read for a rank. */
export const precisionWords = (rank: number | null): string => pinPrecisionShort({ granularity_rank: rank });

export type PositionGrid = Record<string, Record<string, number>>;

/* The town cell's stored fact, or an empty grid for anything else. */
export function positionGrid(fact: unknown): PositionGrid {
  const out: PositionGrid = {};
  if (!fact || typeof fact !== 'object') return out;
  for (const [kind, grains] of Object.entries(fact)) {
    if (!grains || typeof grains !== 'object') continue;
    for (const [grain, n] of Object.entries(grains)) {
      if (typeof n === 'number') (out[kind] ??= {})[grain] = n;
    }
  }
  return out;
}

/* The grid's rows: every audit kind, then `unclassified` and any kind the gauge stored that this list does not name. */
export function gridKinds(grid: PositionGrid): string[] {
  const extra = Object.keys(grid).filter((k) => !(POSITION_KINDS as readonly string[]).includes(k));
  return [...POSITION_KINDS, ...extra.sort((a, b) => (a === UNCLASSIFIED ? -1 : b === UNCLASSIFIED ? 1 : a.localeCompare(b)))];
}

/* The grid's columns: the always-shown grains, a rarer one only when it holds a row, then any unknown class. */
export function gridGrains(grid: PositionGrid): string[] {
  const held = new Set(Object.values(grid).flatMap((g) => Object.keys(g).filter((k) => g[k] > 0)));
  const known = POSITION_GRAINS.filter((g) => g.always || held.has(g.key)).map((g) => g.key);
  const extra = [...held].filter((k) => !POSITION_GRAINS.some((g) => g.key === k)).sort();
  return [...known, ...extra];
}

export const kindTotal = (grid: PositionGrid, kind: string): number =>
  Object.values(grid[kind] ?? {}).reduce((a, n) => a + n, 0);

export const grainTotal = (grid: PositionGrid, grain: string): number =>
  Object.values(grid).reduce((a, g) => a + (g[grain] ?? 0), 0);

/* ---------- the cell in the URL: ?portal=&kind=&grain= ---------- */

/* `kind` opens the list (`all` = every kind); `grain` narrows it (absent = every grain). */
export interface PositionCell {
  kind: string;
  grain: string | null;
}

export const ALL_KINDS = 'all';

export function cellFromParams(params: URLSearchParams): PositionCell | null {
  const kind = params.get('kind');
  if (!kind) return null;
  return { kind, grain: params.get('grain') || null };
}

export function paramsForCell(portal: string, cell: PositionCell | null): Record<string, string> {
  const out: Record<string, string> = { portal };
  if (cell) {
    out.kind = cell.kind;
    if (cell.grain) out.grain = cell.grain;
  }
  return out;
}

/* ---------- the list ---------- */

export interface PositionAuditRow {
  listing_id: number;
  /* The LIVE property (a merge never leaves a stale link); null for an ad no property holds yet. */
  property_id: number | null;
  source: string;
  is_active: boolean;
  first_seen_at: string;
  last_seen_at: string;
  category_main: string | null;
  category_type: string | null;
  disposition: string | null;
  area_m2: number | null;
  price_czk: number | null;
  source_url: string | null;
  display_label: string | null;
  granularity: string | null;
  granularity_rank: number | null;
  grain: string;
  position_kind: string;
  country_status: string | null;
  resolved_at: string | null;
  /* On `none` rows only (null elsewhere). */
  town_evidence: boolean | null;
  sibling_placed: boolean | null;
  queued: boolean | null;
  refreshed_at: string;
}

export interface PositionListFilters {
  portal: string;
  cell: PositionCell;
  /* Empty / 'all' = no constraint (the fail-open filter idiom). */
  status: 'all' | 'active' | 'delisted';
  evidence: 'all' | 'unresolved' | 'withheld';
  sibling: 'all' | 'yes' | 'no';
}

/* `none` opens on every status: the delisted ads with no answer are the old audit's rows, all reachable. */
export const listFilters = (portal: string, cell: PositionCell): PositionListFilters => ({
  portal, cell, status: 'all', evidence: 'all', sibling: 'all',
});

const ROW_COLS = [
  'listing_id', 'property_id', 'source', 'is_active', 'first_seen_at', 'last_seen_at', 'category_main',
  'category_type', 'disposition', 'area_m2', 'price_czk', 'source_url', 'display_label', 'granularity',
  'granularity_rank', 'grain', 'position_kind', 'country_status', 'resolved_at', 'town_evidence',
  'sibling_placed', 'queued', 'refreshed_at',
].join(',');

interface FilterBuilder {
  eq: (column: string, value: boolean | string) => FilterBuilder;
}

function applyFilters<T extends FilterBuilder>(query: T, f: PositionListFilters): T {
  let q = query.eq('source', f.portal) as T;
  if (f.cell.kind !== ALL_KINDS) q = q.eq('position_kind', f.cell.kind) as T;
  if (f.cell.grain) q = q.eq('grain', f.cell.grain) as T;
  if (f.status !== 'all') q = q.eq('is_active', f.status === 'active') as T;
  /* The evidence split and the sibling are facts of a `none` row only; elsewhere they would empty the list. */
  if (f.cell.kind === 'none') {
    if (f.evidence !== 'all') q = q.eq('town_evidence', f.evidence === 'unresolved') as T;
    if (f.sibling !== 'all') q = q.eq('sibling_placed', f.sibling === 'yes') as T;
  }
  return q;
}

export interface PositionAuditPage {
  rows: PositionAuditRow[];
  nextCursor: KeysetCursor | null;
  /* The filtered list's exact size, read with the first page only (null after). */
  total: number | null;
}

export const fetchPositionAuditPage = async (
  f: PositionListFilters,
  sort: SortSpec,
  cursor: KeysetCursor | null,
  { signal }: PgReadOptions = {},
): Promise<PositionAuditPage> => {
  const base = supabase
    .from(POSITION_AUDIT_RELATION)
    .select(withKeysetColumns(ROW_COLS, sort, TIEBREAK), cursor == null ? { count: 'exact' } : undefined);
  const scoped = applyFilters(base as unknown as FilterBuilder, f);
  const keyed = applyKeyset(scoped as unknown as KeysetBuilder, sort, cursor, TIEBREAK) as unknown as typeof base;
  const { data, count } = await pgRead<PositionAuditRow[] | null>(keyed.limit(POSITION_AUDIT_PAGE_SIZE), { signal });
  const rows = data ?? [];
  return {
    rows,
    total: cursor == null ? count : null,
    nextCursor: nextCursorFrom(rows as unknown as Record<string, unknown>[], sort, TIEBREAK),
  };
};
