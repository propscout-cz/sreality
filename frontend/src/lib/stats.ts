/* Browse › Stats: the dashboard the user composes (docs/design/stats-dashboard.md).
 *
 * ONE sample, ONE key, ONE box. Every chart reads the Browse cohort through ONE
 * pivot RPC (`browse_stats_properties` with a `spec`, migration 626); every
 * dimension value is ONE key, labelled and ordered here from ONE registry
 * (statsRegistry.generated.ts, emitted from toolkit/stats_registry.py); every
 * unit is named from the PAYLOAD's basis, never from the filter spec.
 *
 * This module is the SPA's whole vocabulary for it: the payload types, the
 * fetchers, the tree/sort/label/unit/title rules, the shelf rules the builder
 * enforces, the default board and the refusal sentences. Components render;
 * they decide nothing a test here cannot pin. */

import type { QueryClient } from '@tanstack/react-query';
import { supabase } from './supabase';
import { pgRead, type PgReadOptions } from './pgRead';
import { apiPut } from './api';
import { browseKeys } from './browseKeys';
import { buildBrowseStatsArgs, resolveBrowsePrefilters, type BrowsePrefilters } from './queries';
import type { ListingFilters } from './filters';
import { PPM2_UNIT, PRICE_PERIOD_UNIT, ppm2BasisFromToken } from './measure';
import { categoryMainLabelPlural, categoryTypeLabel } from './enums';
import { filterById } from './filterRegistry.generated';
import {
  STATS_REGISTRY,
  statsDimension,
  statsMeasure,
  type ChartStat,
  type MeasureUnit,
  type StatId,
  type StatsDimension,
  type ViewId,
} from './statsRegistry.generated';

/* -------------------------------------------------------------------------- */
/* Types — the RPC payload and the saved chart                                 */
/* -------------------------------------------------------------------------- */

export interface StatsSpec {
  rows: string[];
  cols: string[];
  measure: string;
}

/* One saved chart. The title is derived (chartTitle), never stored. */
export interface ChartSpec extends StatsSpec {
  id: string;
  view: ViewId;
  stat: ChartStat;
}

export type StatsRefusal =
  | 'empty'
  | 'mixed_category'
  | 'mixed_status'
  | 'too_many_cells'
  | 'bad_spec';

export type CohortStatus = 'active' | 'inactive' | 'mixed';

/* A rollup cell: `keys.length` is its level (0 = the whole sample, per column
 * value). Statistics are null below `min_n` — server-enforced. */
export interface StatsCell {
  keys: string[];
  col: string | null;
  n: number;
  mean: number | null;
  median: number | null;
  p10: number | null;
  p25: number | null;
  p75: number | null;
  p90: number | null;
}

/* A place key's register name (migration 626's `places`): `name` is null when
 * the RÚIAN register has no live name for the code. */
export interface PlaceName {
  dim: string;
  key: string;
  name: string | null;
}

export interface StatsPivot {
  /* The spec the server ANSWERED (labels come from here, not from the request). */
  spec: StatsSpec;
  /* Null only on a `bad_spec` refusal, which carries no counts. */
  filtered: number | null;
  sample: number | null;
  excluded: number | null;
  category_main: string | null;
  category_type: string | null;
  status: CohortStatus | null;
  /* Migration 425's token; `rent_monthly_czk_m2` ⇒ rent steps and monthly units. */
  basis: string | null;
  min_n: number;
  refused: StatsRefusal | null;
  reason: string | null;
  /* Every place key among the cells with its name; a place cell keys on the
   * RÚIAN code, so two towns sharing a name are two keys. */
  places: ReadonlyArray<PlaceName>;
  cells: StatsCell[];
}

/* -------------------------------------------------------------------------- */
/* Fetchers                                                                    */
/* -------------------------------------------------------------------------- */

const num = (v: unknown): number | null => {
  if (v == null) return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
};

const strArray = (v: unknown): string[] =>
  Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string') : [];

const REFUSALS: ReadonlyArray<StatsRefusal> = [
  'empty',
  'mixed_category',
  'mixed_status',
  'too_many_cells',
  'bad_spec',
];

/* The wire payload → the typed one. Defensive about jsonb numerics and about the
 * keys a `bad_spec` refusal leaves out; the requested spec stands in only when
 * the server echoed none. */
export function normalizePivot(raw: unknown, requested: StatsSpec): StatsPivot {
  const r = (raw ?? {}) as Record<string, unknown>;
  const s = (r.spec ?? null) as Record<string, unknown> | null;
  const spec: StatsSpec = s
    ? { rows: strArray(s.rows), cols: strArray(s.cols), measure: String(s.measure ?? requested.measure) }
    : requested;
  const refused = REFUSALS.includes(r.refused as StatsRefusal) ? (r.refused as StatsRefusal) : null;
  const status = r.status === 'active' || r.status === 'inactive' || r.status === 'mixed'
    ? r.status
    : null;
  const cells = Array.isArray(r.cells)
    ? (r.cells as Array<Record<string, unknown>>).map((c) => ({
        keys: strArray(c.keys),
        col: typeof c.col === 'string' ? c.col : null,
        n: num(c.n) ?? 0,
        mean: num(c.mean),
        median: num(c.median),
        p10: num(c.p10),
        p25: num(c.p25),
        p75: num(c.p75),
        p90: num(c.p90),
      }))
    : [];
  const places = Array.isArray(r.places)
    ? (r.places as Array<Record<string, unknown> | null>).flatMap((p) =>
        p && typeof p.dim === 'string' && typeof p.key === 'string'
          ? [{ dim: p.dim, key: p.key, name: typeof p.name === 'string' ? p.name : null }]
          : [],
      )
    : [];
  return {
    spec,
    filtered: num(r.filtered),
    sample: num(r.sample),
    excluded: num(r.excluded),
    category_main: typeof r.category_main === 'string' ? r.category_main : null,
    category_type: typeof r.category_type === 'string' ? r.category_type : null,
    status,
    basis: typeof r.basis === 'string' ? r.basis : null,
    min_n: num(r.min_n) ?? STATS_REGISTRY.min_n,
    refused,
    reason: typeof r.reason === 'string' ? r.reason : null,
    places,
    cells,
  };
}

/* The Browse prefilters of one filter set (tags, collections, pipeline,
 * estimates, broker, city quality, growth), resolved ONCE per set however many
 * charts read it: every pivot awaits the same cached query, so a board of N
 * charts, and each spec change of the builder's preview, reuses one resolution.
 * Keyed under the stats root, so every Browse sweep that refreshes a pivot also
 * refreshes the ids it was cut from. */
const statsPrefilters = (client: QueryClient, f: ListingFilters): Promise<BrowsePrefilters> =>
  client.fetchQuery({
    queryKey: browseKeys.statsPrefilters(f),
    queryFn: ({ signal }) => resolveBrowsePrefilters(f, signal),
  });

/* One chart's pivot over the Browse cohort. The cohort is the SAME argument
 * object the map reads (buildBrowseStatsArgs over resolveBrowsePrefilters), plus
 * the chart's spec; a resolved-but-empty allowlist travels as [] and the server
 * answers `empty`. */
export async function fetchStatsPivot(
  f: ListingFilters,
  spec: StatsSpec,
  { signal, client }: PgReadOptions & { client: QueryClient },
): Promise<StatsPivot> {
  const pre = await statsPrefilters(client, f);
  const { data } = await pgRead<unknown>(
    supabase.rpc('browse_stats_properties', {
      ...buildBrowseStatsArgs(f, {
        obec_ids_filter: pre.obecIds,
        property_ids_filter: pre.propertyIds,
        property_ids_excluded: pre.propertyIdsExcluded,
      }),
      spec,
    }),
    { signal },
  );
  return normalizePivot(data, spec);
}

export const statsPivotKey = (f: ListingFilters, spec: StatsSpec) => browseKeys.stats(f, spec);

/* The account's board. Not a Browse read (the filter only picks the sample), so
 * it lives outside the browse sweep. */
export const statsDashboardKey = ['stats-dashboard'] as const;

/* RLS returns the caller's row; no row ⇒ the default board, and the first save
 * persists the whole list. A saved EMPTY list is the user's choice and stays empty.
 * Single-membership assumption: RLS reads every account the user belongs to
 * (current_account_ids) while PUT writes the first one (require_account_id). One
 * user has one membership today; a second one (out of scope) needs a
 * GET /stats/dashboard on the write's account resolution instead of this read. */
export async function fetchDashboard({ signal }: PgReadOptions = {}): Promise<ChartSpec[]> {
  const { data } = await pgRead<{ charts: unknown } | null>(
    supabase.from('stats_dashboards').select('charts').limit(1).maybeSingle(),
    { signal },
  );
  if (data == null) return DEFAULT_CHARTS;
  return sanitizeCharts(data.charts);
}

/* PUT /stats/dashboard {charts}: the whole document, validated server-side by
 * toolkit.stats_registry.validate_chart. */
export async function saveDashboard(charts: ChartSpec[]): Promise<ChartSpec[]> {
  const res = await apiPut<{ charts?: unknown } | null>('/stats/dashboard', { charts });
  return res && Array.isArray(res.charts) ? sanitizeCharts(res.charts) : charts;
}

/* -------------------------------------------------------------------------- */
/* The chart rules (the registry's, enforced in the UI exactly as on the API)   */
/* -------------------------------------------------------------------------- */

const VIEWS = new Set<string>(STATS_REGISTRY.views);
const CHART_STATS = new Set<string>(STATS_REGISTRY.chart_stats);

/* Ruling 7, per view, from the registry: line / bar take exactly one row. */
export const minRowsFor = (view: ViewId): number => STATS_REGISTRY.view_limits[view].min_rows;
export const maxRowsFor = (view: ViewId): number => STATS_REGISTRY.view_limits[view].max_rows;
export const maxColsFor = (view: ViewId): number => STATS_REGISTRY.view_limits[view].max_cols;

/* The board's size guard (PUT /stats/dashboard refuses more). */
export const MAX_CHARTS = STATS_REGISTRY.max_charts;

/* A saved chart this build can draw, or null. Drops what the registry no longer
 * knows rather than letting one stale chart make the whole board unsaveable. */
export function sanitizeChart(raw: unknown): ChartSpec | null {
  if (raw == null || typeof raw !== 'object') return null;
  const c = raw as Record<string, unknown>;
  const id = c.id;
  if (typeof id !== 'string' || id.length < 1 || id.length > 64) return null;
  if (typeof c.view !== 'string' || !VIEWS.has(c.view)) return null;
  const view = c.view as ViewId;
  const measure = typeof c.measure === 'string' ? c.measure : '';
  if (!statsMeasure(measure)) return null;
  const rows = strArray(c.rows);
  const cols = strArray(c.cols);
  const dims = [...rows, ...cols];
  if (dims.some((d) => !statsDimension(d)) || new Set(dims).size !== dims.length) return null;
  if (rows.length < minRowsFor(view) || rows.length > maxRowsFor(view) || cols.length > maxColsFor(view)) {
    return null;
  }
  const stat = (typeof c.stat === 'string' && CHART_STATS.has(c.stat) ? c.stat : 'median') as ChartStat;
  return { id, view, rows, cols, measure, stat };
}

export const sanitizeCharts = (raw: unknown): ChartSpec[] =>
  Array.isArray(raw) ? raw.map(sanitizeChart).filter((c): c is ChartSpec => c != null) : [];

export type Shelf = 'rows' | 'cols';

export const removeDim = (chart: ChartSpec, dim: string): ChartSpec => ({
  ...chart,
  rows: chart.rows.filter((d) => d !== dim),
  cols: chart.cols.filter((d) => d !== dim),
});

/* Put `dim` on a shelf (moving it if it is already on one), at `index` within
 * Řádky. A move the view's limits forbid returns the chart unchanged. */
export function placeDim(chart: ChartSpec, dim: string, to: Shelf, index?: number): ChartSpec {
  if (!statsDimension(dim)) return chart;
  const base = removeDim(chart, dim);
  if (to === 'cols') {
    if (maxColsFor(chart.view) < 1) return chart;
    /* One column slot: a dimension already there swaps back into Řádky if they have room. */
    const displaced = base.cols[0];
    const rows = displaced != null ? [...base.rows, displaced] : base.rows;
    if (rows.length > maxRowsFor(chart.view)) return chart;
    return { ...base, rows, cols: [dim] };
  }
  if (base.rows.length >= maxRowsFor(chart.view)) return chart;
  const rows = [...base.rows];
  const at = index == null ? rows.length : Math.max(0, Math.min(index, rows.length));
  rows.splice(at, 0, dim);
  return { ...base, rows };
}

export function moveRow(chart: ChartSpec, from: number, to: number): ChartSpec {
  if (from === to || from < 0 || to < 0 || from >= chart.rows.length || to >= chart.rows.length) {
    return chart;
  }
  const rows = [...chart.rows];
  const [d] = rows.splice(from, 1);
  rows.splice(to, 0, d);
  return { ...chart, rows };
}

/* Where a drop lands: a shelf, or (Řádky) the row slot it was dropped on. */
export type DropTarget = { shelf: 'rows'; at?: string } | { shelf: 'cols' };

/* What dropping `dim` on `target` does. A dimension already in Řádky (its slot,
 * or its chosen palette chip) is MOVED to the slot it lands on, so a downward
 * move lands where it was dropped; anything else is placed. `refused` = the
 * view's limits forbade it and the chart is unchanged. */
export function dropOutcome(
  chart: ChartSpec,
  dim: string,
  target: DropTarget,
): { next: ChartSpec; refused: boolean } {
  if (target.shelf === 'cols') {
    const next = placeDim(chart, dim, 'cols');
    return { next, refused: next === chart };
  }
  const from = chart.rows.indexOf(dim);
  if (from >= 0) {
    const to = target.at != null ? chart.rows.indexOf(target.at) : chart.rows.length - 1;
    return { next: moveRow(chart, from, to), refused: false };
  }
  const next = placeDim(chart, dim, 'rows', target.at != null ? chart.rows.indexOf(target.at) : undefined);
  return { next, refused: next === chart };
}

/* Where a click on an unchosen chip puts it: Řádky while they have room, then
 * Sloupce while the view takes a column; null when both are full. */
export function chipTarget(chart: ChartSpec): Shelf | null {
  if (chart.rows.length < maxRowsFor(chart.view)) return 'rows';
  if (chart.cols.length < maxColsFor(chart.view)) return 'cols';
  return null;
}

/* Why a view cannot take the chart's current shelves (null = it can). */
export function viewBlock(chart: StatsSpec, view: ViewId): string | null {
  if (chart.rows.length > maxRowsFor(view)) return 'čáry a pruhy mají v Řádcích jen jednu dimenzi';
  if (chart.cols.length > maxColsFor(view)) return 'krabice nemá Sloupce';
  return null;
}

export const LINE_NEEDS_ROW = 'Čáry a pruhy potřebují jednu dimenzi v Řádcích (osa x).';

/* Why the chart cannot be saved (null = it can). Only line / bar have a row
 * minimum (one dimension on x). */
export function chartProblem(chart: ChartSpec): string | null {
  if (chart.rows.length < minRowsFor(chart.view)) return LINE_NEEDS_ROW;
  return viewBlock(chart, chart.view);
}

/* A dimension that does not exist for the cohort's category (area bands off byty). */
export function dimBlock(dim: string, categoryMain: string | null): string | null {
  const d = statsDimension(dim);
  if (!d?.categories || categoryMain == null || d.categories.includes(categoryMain)) return null;
  return `jen ${d.categories.map((c) => categoryMainLabelPlural(c).toLocaleLowerCase('cs')).join(', ')}`;
}

/* The builder's palette groups. Presentation only — every dimension of the
 * registry appears in exactly one (stats.test.ts pins it). */
export const DIMENSION_GROUPS: ReadonlyArray<{ label: string; dims: readonly string[] }> = [
  { label: 'Vlastnost', dims: ['disposition', 'area_band_5', 'area_band_10', 'condition', 'building_type'] },
  { label: 'Cena', dims: ['ppm2_band_s', 'ppm2_band_l', 'price_band_s', 'price_band_m', 'price_band_l'] },
  { label: 'Místo', dims: ['kraj', 'okres', 'obec', 'cast_obce', 'ulice'] },
  { label: 'Vybavení', dims: ['furnished', 'has_balcony', 'has_lift', 'has_parking', 'terrace', 'cellar', 'garage'] },
];

/* -------------------------------------------------------------------------- */
/* Order, labels, units, titles                                                */
/* -------------------------------------------------------------------------- */

const CS = new Intl.Collator('cs');
const CODES = new Intl.Collator('cs', { numeric: true });
const NBSP = ' ';

export const dimLabel = (id: string): string => statsDimension(id)?.label_cs ?? id;
export const measureLabel = (id: string): string => statsMeasure(id)?.label_cs ?? id;

/* A pivot's place names as one lookup, (dimension, code) → name. Built once per
 * pivot (memoised on its `places`) and handed to every label and sort. */
export type PlaceNames = ReadonlyMap<string, string | null>;

export const NO_PLACES: PlaceNames = new Map();

const placeId = (dim: string, key: string): string => `${dim}|${key}`;
const placeMaps = new WeakMap<ReadonlyArray<PlaceName>, PlaceNames>();

export function placeNames(pivot: Pick<StatsPivot, 'places'>): PlaceNames {
  let names = placeMaps.get(pivot.places);
  if (!names) {
    names = new Map(pivot.places.map((p) => [placeId(p.dim, p.key), p.name]));
    placeMaps.set(pivot.places, names);
  }
  return names;
}

/* A place key reads as its register name; a code the register cannot name reads as itself. */
const placeLabel = (dim: string, key: string, places: PlaceNames): string =>
  places.get(placeId(dim, key)) ?? key;

/* The one order of a dimension's keys: enum/bool by the registry's option order
 * (unknown members after, alphabetically), bands by their lower bound, places
 * by their label with Czech collation (two places of one name by code). */
export function sortKeys(dimId: string, keys: readonly string[], places: PlaceNames): string[] {
  const d = statsDimension(dimId);
  const out = [...keys];
  if (d?.kind === 'band') return out.sort((a, b) => Number(a) - Number(b));
  if (d?.kind === 'geo') {
    const label = (k: string) => placeLabel(dimId, k, places);
    return out.sort((a, b) => CS.compare(label(a), label(b)) || CODES.compare(a, b));
  }
  if (!d || !d.options) return out.sort(CS.compare);
  const order = new Map(d.options.map((o, i) => [String(o.value), i]));
  return out.sort((a, b) => {
    const ia = order.get(a);
    const ib = order.get(b);
    if (ia != null && ib != null) return ia - ib;
    if (ia != null) return -1;
    if (ib != null) return 1;
    return CS.compare(a, b);
  });
}

const isRent = (basis: string | null): boolean => basis === STATS_REGISTRY.rent_basis;

/* A unit kind, spelled for the payload's basis. Price and per-m² have no unit
 * without one — a visible gap, never a guess. */
export function unitFor(unit: MeasureUnit, basis: string | null): string {
  switch (unit) {
    case 'm2':
      return 'm²';
    case 'days':
      return 'dní';
    case 'price':
      if (basis == null) return '';
      return isRent(basis) ? PRICE_PERIOD_UNIT.monthly : PRICE_PERIOD_UNIT.capital;
    case 'ppm2': {
      const b = ppm2BasisFromToken(basis);
      return b == null || b === 'mixed' ? '' : PPM2_UNIT[b];
    }
  }
}

export const measureUnit = (measureId: string, basis: string | null): string => {
  const m = statsMeasure(measureId);
  return m ? unitFor(m.unit, basis) : '';
};

/* The band's width for the payload's basis: {all} for area, else rent or sale. */
export function bandStep(d: StatsDimension, basis: string | null): number | null {
  if (!d.steps) return null;
  if (d.steps.all != null) return d.steps.all;
  return (isRent(basis) ? d.steps.rent : d.steps.sale) ?? null;
}

const countFmt = new Intl.NumberFormat('cs-CZ');

/* A key as the reader sees it: a band ⇒ "80 – 90 m²", an enum ⇒ its label_cs,
 * a place ⇒ its register name from the pivot's `places` (the code when the
 * register has none). An unknown member reads as itself. */
export function keyLabel(dimId: string, key: string, basis: string | null, places: PlaceNames): string {
  const d = statsDimension(dimId);
  if (!d) return key;
  if (d.kind === 'band') {
    const lo = Number(key);
    const step = bandStep(d, basis);
    if (!Number.isFinite(lo) || step == null) return key;
    const unit = d.unit ? unitFor(d.unit, basis) : '';
    return `${countFmt.format(lo)} – ${countFmt.format(lo + step)}${unit ? NBSP + unit : ''}`;
  }
  if (d.kind === 'geo') return placeLabel(dimId, key, places);
  if (!d.options) return key;
  return d.options.find((o) => String(o.value) === key)?.label_cs ?? key;
}

/* A dimension as a chart names it. A band is named by its step in the
 * payload's unit ("Cena po 2 500 Kč / měs", "Cena za m² po 5 000 Kč/m²"); with
 * no single basis the step is unknown and the registry's two-step label stands
 * (the palette chip always shows that one: it serves every cohort). */
export function dimTitle(id: string, basis: string | null): string {
  const d = statsDimension(id);
  if (d?.kind !== 'band' || !d.unit) return dimLabel(id);
  const step = bandStep(d, basis);
  const unit = unitFor(d.unit, basis);
  const quantity = STATS_REGISTRY.measures.find((m) => m.unit === d.unit)?.label_cs;
  if (step == null || !unit || !quantity) return dimLabel(id);
  return `${quantity} po ${countFmt.format(step)}${NBSP}${unit}`;
}

/* "Cena za m² · Dispozice › Stav × Materiál": the measure, the nested rows, the
 * column; a band named for the payload's basis when one is known. */
export function chartTitle(chart: StatsSpec, basis: string | null = null): string {
  const name = (d: string) => dimTitle(d, basis);
  const rows = chart.rows.map(name).join(' › ');
  const dims = [rows, ...chart.cols.map(name)].filter(Boolean).join(' × ');
  return dims ? `${measureLabel(chart.measure)} · ${dims}` : measureLabel(chart.measure);
}

export const STAT_LABEL: Record<StatId, string> = {
  n: 'n',
  mean: 'průměr',
  median: 'medián',
  p10: 'p10',
  p25: 'p25',
  p75: 'p75',
  p90: 'p90',
};

export const VIEW_LABEL: Record<ViewId, string> = {
  table: 'Tabulka',
  line: 'Čáry',
  bar: 'Pruhy',
  box: 'Krabice',
};

const statFmt = new Map<number, Intl.NumberFormat>();

/* A statistic's number (its unit rides in the header). Money to the crown,
 * areas to a tenth, days whole. */
export function fmtStat(v: number, measureId: string): string {
  const unit = statsMeasure(measureId)?.unit;
  const decimals = unit === 'm2' ? 1 : 0;
  let f = statFmt.get(decimals);
  if (!f) {
    f = new Intl.NumberFormat('cs-CZ', { maximumFractionDigits: decimals });
    statFmt.set(decimals, f);
  }
  return f.format(v);
}

export const fmtN = (n: number): string => countFmt.format(n);

/* -------------------------------------------------------------------------- */
/* Cells → nested rows                                                         */
/* -------------------------------------------------------------------------- */

export interface PivotNode {
  keys: string[];
  /* The cell per column value; '' is the no-column cell. */
  cells: Map<string, StatsCell>;
  children: PivotNode[];
}

export interface PivotTree {
  root: PivotNode;
  /* The column dimension's values in registry order; [] without one. */
  cols: string[];
}

export const colKey = (col: string | null): string => col ?? '';

/* React key of a row / of a cell: the keys joined, plus the column. */
export const rowKeyOf = (keys: readonly string[]): string => keys.join('›');
export const cellKeyOf = (keys: readonly string[], col: string | null): string =>
  `${rowKeyOf(keys)}|${colKey(col)}`;

/* Children by key prefix: a level-L cell hangs under the level-(L-1) node with
 * its first L-1 keys. Each level sorted by its own dimension's order. */
export function cellTree(cells: readonly StatsCell[], spec: StatsSpec, places: PlaceNames): PivotTree {
  const byPath = new Map<string, PivotNode>();
  const node = (keys: string[]): PivotNode => {
    const path = JSON.stringify(keys);
    let n = byPath.get(path);
    if (!n) {
      n = { keys, cells: new Map(), children: [] };
      byPath.set(path, n);
      if (keys.length > 0) node(keys.slice(0, -1)).children.push(n);
    }
    return n;
  };
  const root = node([]);
  const colValues = new Set<string>();
  for (const c of cells) {
    node(c.keys).cells.set(colKey(c.col), c);
    if (c.col != null) colValues.add(c.col);
  }
  const sortChildren = (n: PivotNode): void => {
    if (n.children.length === 0) return;
    const dim = spec.rows[n.keys.length];
    const order = sortKeys(dim ?? '', n.children.map((c) => c.keys[c.keys.length - 1]), places);
    const rank = new Map(order.map((k, i) => [k, i]));
    n.children.sort(
      (a, b) => (rank.get(a.keys[a.keys.length - 1]) ?? 0) - (rank.get(b.keys[b.keys.length - 1]) ?? 0),
    );
    n.children.forEach(sortChildren);
  };
  sortChildren(root);
  return { root, cols: spec.cols.length ? sortKeys(spec.cols[0], [...colValues], places) : [] };
}

/* The rows in reading order: every parent (rollup) above its children. */
export function flattenTree(root: PivotNode): PivotNode[] {
  const out: PivotNode[] = [];
  const walk = (n: PivotNode): void => {
    out.push(n);
    n.children.forEach(walk);
  };
  walk(root);
  return out;
}

/* A row's own label: its last key, or "Celkem" for the whole sample. */
export function nodeLabel(n: PivotNode, spec: StatsSpec, basis: string | null, places: PlaceNames): string {
  if (n.keys.length === 0) return 'Celkem';
  return keyLabel(spec.rows[n.keys.length - 1], n.keys[n.keys.length - 1], basis, places);
}

/* -------------------------------------------------------------------------- */
/* Series (line / bar): one row dimension on x, the column dimension as series  */
/* -------------------------------------------------------------------------- */

export interface SeriesModel {
  series: Array<{ key: string; label: string; dataKey: string }>;
  data: Array<Record<string, string | number | null>>;
  /* Some (x, series) has fewer than min_n properties, or none — drawn as a gap. */
  gaps: boolean;
}

export function seriesModel(pivot: StatsPivot, stat: ChartStat): SeriesModel {
  const { spec } = pivot;
  if (spec.rows.length !== 1) return { series: [], data: [], gaps: false };
  const places = placeNames(pivot);
  const level1 = pivot.cells.filter((c) => c.keys.length === 1);
  const xs = sortKeys(spec.rows[0], [...new Set(level1.map((c) => c.keys[0]))], places);
  const colDim = spec.cols[0];
  const colValues = [...new Set(level1.map((c) => c.col).filter((c): c is string => c != null))];
  const series = colDim
    ? sortKeys(colDim, colValues, places).map((k, i) => ({
        key: k,
        label: keyLabel(colDim, k, pivot.basis, places),
        dataKey: `s${i}`,
      }))
    : [{ key: '', label: measureLabel(spec.measure), dataKey: 's0' }];
  const at = new Map(level1.map((c) => [`${c.keys[0]}|${colKey(c.col)}`, c]));
  let gaps = false;
  const data = xs.map((x) => {
    const row: Record<string, string | number | null> = {
      x,
      label: keyLabel(spec.rows[0], x, pivot.basis, places),
    };
    for (const s of series) {
      const v = at.get(`${x}|${s.key}`)?.[stat] ?? null;
      if (v == null) gaps = true;
      row[s.dataKey] = v;
    }
    return row;
  });
  return { series, data, gaps };
}

/* Copper first, then the ink tints — the one series palette, as CSS tokens.
 * No ink-4: at about 2:1 on the card it fails the 3:1 a data mark needs. */
export const SERIES_TOKENS = [
  '--color-copper',
  '--color-ink',
  '--color-ink-2',
  '--color-ink-3',
] as const;

/* The most series a line or bar chart draws: each palette colour solid, then
 * dashed. A presentation limit (the payload is fine), so it lives here. */
export const MAX_SERIES = SERIES_TOKENS.length * 2;

/* How many series the pivot's column dimension would draw (1 without one). */
export function seriesCount(pivot: StatsPivot): number {
  if (pivot.spec.cols.length === 0) return 1;
  return new Set(pivot.cells.filter((c) => c.keys.length === 1 && c.col != null).map((c) => c.col)).size;
}

export const tooManySeriesText = (): string =>
  `Řad je víc než ${MAX_SERIES} — použijte Tabulku, nebo zužte filtr.`;

/* -------------------------------------------------------------------------- */
/* The sample ledger                                                           */
/* -------------------------------------------------------------------------- */

export interface LedgerCounts {
  filtered: number;
  sample: number;
  excluded: number;
  /* Of the sample: in a leaf cell with statistics, and in one under min_n.
   * Null when the payload has no cells (a refusal) — the split is unknown. */
  withStats: number | null;
  underMin: number | null;
}

/* Every sample property lands in exactly one leaf cell (level = rows.length,
 * per column value), so the leaves split the sample into what is behind a
 * number and what is only counted. */
export function ledgerCounts(pivot: StatsPivot): LedgerCounts | null {
  if (pivot.filtered == null || pivot.sample == null) return null;
  const leaves = pivot.cells.filter((c) => c.keys.length === pivot.spec.rows.length);
  const split = leaves.length > 0;
  return {
    filtered: pivot.filtered,
    sample: pivot.sample,
    excluded: pivot.excluded ?? pivot.filtered - pivot.sample,
    withStats: split ? leaves.reduce((s, c) => s + (c.median != null ? c.n : 0), 0) : null,
    underMin: split ? leaves.reduce((s, c) => s + (c.median == null ? c.n : 0), 0) : null,
  };
}

const optionLabel = (filterId: string, value: string): string | null => {
  const o = filterById(filterId)?.enum_values?.find((e) => String(e.value) === value);
  return o ? o.label_cs : null;
};

/* The cohort facts the payload states (byty · prodej · aktivní). */
export function cohortChips(pivot: StatsPivot): string[] {
  if (pivot.filtered == null || pivot.filtered === 0) return [];
  const chips: string[] = [];
  const lower = (s: string) => s.toLocaleLowerCase('cs');
  chips.push(pivot.category_main ? lower(categoryMainLabelPlural(pivot.category_main)) : 'více typů nemovitosti');
  chips.push(pivot.category_type ? lower(categoryTypeLabel(pivot.category_type)) : 'více typů nabídky');
  if (pivot.status === 'mixed') chips.push('aktivní i neaktivní');
  else if (pivot.status) chips.push(lower(optionLabel('status', pivot.status) ?? pivot.status));
  return chips;
}

/* -------------------------------------------------------------------------- */
/* Refusals and the default board                                              */
/* -------------------------------------------------------------------------- */

export function refusalText(refusal: StatsRefusal): string {
  switch (refusal) {
    case 'empty':
      return 'Ve filtru nejsou žádné nemovitosti.';
    case 'mixed_category':
      return 'Vzorek míchá různé typy nemovitostí nebo nabídek — ve filtru zvolte jeden typ nemovitosti a jeden typ nabídky.';
    case 'mixed_status':
      return 'Doba na trhu potřebuje jeden stav — ve filtru zvolte jen aktivní, nebo jen neaktivní nabídky.';
    case 'too_many_cells':
      return `Graf by měl víc než ${fmtN(STATS_REGISTRY.max_cells)} buněk — uberte dimenzi nebo zužte filtr.`;
    case 'bad_spec':
      return 'Nastavení grafu je neplatné — upravte ho.';
  }
}

/* The seed board. Every chart must answer on DEFAULT_FILTERS (status 'any',
 * byty): no turnover (it needs one status) and no byty-only dimension that a
 * cohort switch would empty. stats.test.ts pins both. */
export const DEFAULT_CHARTS: ChartSpec[] = [
  { id: 'default-1', view: 'table', rows: [], cols: [], measure: 'price', stat: 'median' },
  { id: 'default-2', view: 'table', rows: [], cols: [], measure: 'ppm2', stat: 'median' },
  { id: 'default-3', view: 'box', rows: ['disposition'], cols: [], measure: 'ppm2', stat: 'median' },
  { id: 'default-4', view: 'box', rows: ['condition'], cols: [], measure: 'ppm2', stat: 'median' },
];

export const newChartId = (): string =>
  `c-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;

export const newChart = (): ChartSpec => ({
  id: newChartId(),
  view: 'table',
  rows: [],
  cols: [],
  measure: 'ppm2',
  stat: 'median',
});
