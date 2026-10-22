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
import { arrayMove } from '@dnd-kit/sortable';
import { supabase } from './supabase';
import { pgRead, type PgReadOptions } from './pgRead';
import { apiDelete, apiGet, apiPost, apiPut } from './api';
import { browseKeys } from './browseKeys';
import { buildBrowseStatsArgs, resolveBrowsePrefilters, type BrowsePrefilters } from './queries';
import type { ListingFilters, PresetSpec } from './filters';
import { PPM2_UNIT, PRICE_PERIOD_UNIT, ppm2BasisFromToken } from './measure';
import { categoryMainLabelPlural, categoryTypeLabel } from './enums';
import { filterById } from './filterRegistry.generated';
import { fmtCount } from './format';
import { csCollator } from './collator';
import type { SavedDashboard, TagColor } from './types';
import {
  STATS_REGISTRY,
  type ChartStat,
  type CohortStatus,
  type MeasureUnit,
  type StatId,
  type StatsDimension,
  type StatsMeasure,
  type StatsRefusal,
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

/* One saved chart. The title is derived (chartTitle), never stored. `stats` is
 * the statistics a line, a bar and a table with a column draw: one or more, in
 * the order they were picked (the first one leads, in the copper series). */
export interface ChartSpec extends StatsSpec {
  id: string;
  view: ViewId;
  stats: ChartStat[];
}

/* A rollup cell: `keys.length` is its level (0 = the whole sample, per column
 * value). Statistics are null below `min_n` — server-enforced, all six together. */
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

/* A pivot's place names, (dimension, RÚIAN code) → name (placeKey); null when
 * the register has no live name for the code. Built once, in normalizePivot. */
type PlaceNames = ReadonlyMap<string, string | null>;

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
  places: PlaceNames;
  cells: StatsCell[];
}

/* The RPC's two answers (migration 626): the full payload, whose `places` is a
 * list of rows, and the stub a `bad_spec` refusal returns before the cohort is read. */
export type PivotPayload = Omit<StatsPivot, 'places' | 'reason'> & {
  places: ReadonlyArray<{ dim: string; key: string; name: string | null }>;
};
interface BadSpecStub {
  refused: 'bad_spec';
  reason: string;
  cells: [];
}

/* -------------------------------------------------------------------------- */
/* Fetchers                                                                    */
/* -------------------------------------------------------------------------- */

const placeKey = (dim: string, key: string): string => `${dim}:${key}`;

/* A token of one of the registry's lists, or null for one this build does not know. */
const member = <T extends string>(list: readonly T[], v: unknown): T | null =>
  (list as readonly unknown[]).includes(v) ? (v as T) : null;

/* The wire payload → the typed one: the place names become one lookup, and the
 * bad_spec stub gets the counts and facts it leaves out (null) and the spec the
 * chart asked for. */
export function normalizePivot(raw: PivotPayload | BadSpecStub, requested: StatsSpec): StatsPivot {
  if (!('spec' in raw)) {
    return {
      spec: requested,
      filtered: null,
      sample: null,
      excluded: null,
      category_main: null,
      category_type: null,
      status: null,
      basis: null,
      min_n: STATS_REGISTRY.min_n,
      refused: raw.refused,
      reason: raw.reason,
      places: new Map(),
      cells: [],
    };
  }
  return {
    ...raw,
    refused: member(STATS_REGISTRY.refusals, raw.refused),
    status: member(STATS_REGISTRY.statuses, raw.status),
    reason: null,
    places: new Map(raw.places.map((p) => [placeKey(p.dim, p.key), p.name])),
  };
}

/* The Browse prefilters of one filter set (estimates, broker, city quality,
 * growth; the curated sets ride as scope arguments), resolved ONCE per set however many
 * charts read it: every pivot awaits the same cached query, so a board of N
 * charts, and each spec change of the builder's preview, reuses one resolution.
 * Keyed under the stats root, so every Browse sweep that refreshes a pivot also
 * refreshes the ids it was cut from. */
const statsPrefilters = (client: QueryClient, f: ListingFilters): Promise<BrowsePrefilters> =>
  client.fetchQuery({
    queryKey: browseKeys.statsPrefilters(f),
    queryFn: ({ signal }) => resolveBrowsePrefilters(f, signal),
  });

/* One chart's pivot over the Browse cohort, read under `browseKeys.stats(f, spec)`.
 * The cohort is the SAME argument object the map reads (buildBrowseStatsArgs over
 * the resolved prefilters), plus the chart's spec; a resolved-but-empty allowlist
 * travels as [] and the server answers `empty`. */
export async function fetchStatsPivot(
  f: ListingFilters,
  spec: StatsSpec,
  { signal, client }: PgReadOptions & { client: QueryClient },
): Promise<StatsPivot> {
  const pre = await statsPrefilters(client, f);
  const { data } = await pgRead<PivotPayload | BadSpecStub>(
    supabase.rpc('browse_stats_properties', { ...buildBrowseStatsArgs(f, pre), spec }),
    { signal },
  );
  return normalizePivot(data, spec);
}

/* The account's board. Not a Browse read (the filter only picks the sample), so
 * it lives outside the browse sweep. */
export const statsDashboardKey = ['stats-dashboard'] as const;

/* GET /stats/dashboard: the account's WORKING board (the unnamed row, migration
 * 647), resolved like the PUT's. No row ⇒ the default board, and the first save
 * persists the whole list. A saved EMPTY list is the user's choice and stays empty. */
export async function fetchDashboard({ signal }: PgReadOptions = {}): Promise<ChartSpec[]> {
  const row = await apiGet<{ id: string; charts: unknown; updated_at: string } | null>(
    '/stats/dashboard',
    undefined,
    signal,
  );
  if (row == null) return DEFAULT_CHARTS;
  return sanitizeCharts(row.charts);
}

/* PUT /stats/dashboard {charts}: the whole document, validated server-side by
 * toolkit.stats_registry.validate_chart; the answer is the board as stored. */
export async function saveDashboard(charts: ChartSpec[]): Promise<ChartSpec[]> {
  const res = await apiPut<{ charts: unknown }>('/stats/dashboard', { charts });
  return sanitizeCharts(res.charts);
}

/* The NAMED dashboards (migration 647): the filter presets' twin in the one
 * saved-set primitive (docs/design/saved-dashboards.md) — the same six routes
 * under /stats/dashboards, plus read-only sharing. The list holds the caller's
 * own and those shared with it (`owner_email` set); a write to a shared one is
 * the owner's alone (404 for a recipient). On a PUT an explicit
 * `filter_spec: null` clears the filter, an absent one keeps it. They live here,
 * in the Stats chunk, not in lib/api: the first-visit bundle never pays for them. */
export const statsDashboardKeys = {
  all: ['stats-dashboards'] as const,
};

export type DashboardList = { data: SavedDashboard[]; total: number };
const dashboardPath = (id: string, tail = ''): string =>
  `/stats/dashboards/${encodeURIComponent(id)}${tail}`;

export const listStatsDashboards = (): Promise<DashboardList> =>
  apiGet<DashboardList>('/stats/dashboards');

/* The Stats tab's sample lens (operator 2026-10-22). "filter" = the Browse filter as set;
 * "sold" = the sold market over it: inactive only, seen from birth, dismissed included,
 * 2–60 days on market; every other filter holds. Browse's own filter never changes. */
export type StatsMarket = 'filter' | 'sold';
export const SOLD_MARKET_DAYS = { min: 2, max: 60 } as const;
export const soldMarketFilters = (f: ListingFilters): ListingFilters => ({
  ...f,
  status: 'inactive',
  tomObservedOnly: true,
  showDismissed: true,
  tomDaysMin: SOLD_MARKET_DAYS.min,
  tomDaysMax: SOLD_MARKET_DAYS.max,
});

export const createStatsDashboard = (input: {
  name: string;
  charts: ChartSpec[];
  filter_spec?: PresetSpec;
  color?: TagColor | null;
}): Promise<SavedDashboard> => apiPost<SavedDashboard>('/stats/dashboards', input);

export const updateStatsDashboard = (
  id: string,
  patch: { name?: string; charts?: ChartSpec[]; filter_spec?: PresetSpec | null; color?: TagColor | null },
): Promise<SavedDashboard> => apiPut<SavedDashboard>(dashboardPath(id), patch);

export const deleteStatsDashboard = (id: string): Promise<unknown> => apiDelete(dashboardPath(id));

/* The caller's own dashboards in their new order; answers the canonical list. */
export const reorderStatsDashboards = (ids: string[]): Promise<DashboardList> =>
  apiPut<DashboardList>('/stats/dashboards/reorder', { ids });

/* Answers the dashboard with its `shared_with`. */
export const shareStatsDashboard = (id: string, email: string): Promise<SavedDashboard> =>
  apiPost<SavedDashboard>(dashboardPath(id, '/shares'), { email });

/* The owner revokes a recipient, or a recipient leaves (its own grant's account_id). */
export const unshareStatsDashboard = (id: string, account_id: string): Promise<unknown> =>
  apiDelete(dashboardPath(id, `/shares/${encodeURIComponent(account_id)}`));

/* -------------------------------------------------------------------------- */
/* The registry by id, and the names the UI gives it                           */
/* -------------------------------------------------------------------------- */

const DIMS: ReadonlyMap<string, StatsDimension> = new Map(STATS_REGISTRY.dimensions.map((d) => [d.id, d]));
const MEASURES: ReadonlyMap<string, StatsMeasure> = new Map(STATS_REGISTRY.measures.map((m) => [m.id, m]));
/* Each enum / bool dimension's vocabulary by value: its rank in the registry
 * order and its label. Empty for a band or a place. */
const OPTIONS: ReadonlyMap<string, ReadonlyMap<string, { rank: number; label: string }>> = new Map(
  STATS_REGISTRY.dimensions.map((d) => [
    d.id,
    new Map((d.options ?? []).map((o, i) => [String(o.value), { rank: i, label: o.label_cs }])),
  ]),
);

export const dimLabel = (id: string): string => DIMS.get(id)?.label_cs ?? id;
export const measureLabel = (id: string): string => MEASURES.get(id)?.label_cs ?? id;

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

const lower = (s: string): string => s.toLocaleLowerCase('cs');
const sentence = (s: string): string => s.charAt(0).toLocaleUpperCase('cs') + s.slice(1);

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

/* The views that take exactly one row (the x axis), as the view picker names
 * them: "čáry a pruhy". */
const ONE_ROW_VIEWS = STATS_REGISTRY.views
  .filter((v) => maxRowsFor(v) === 1)
  .map((v) => lower(VIEW_LABEL[v]))
  .join(' a ');

export const LINE_NEEDS_ROW = `${sentence(ONE_ROW_VIEWS)} potřebují jednu dimenzi v Řádcích (osa x).`;

/* Why a view cannot take the chart's current shelves (null = it can). */
export function viewBlock(chart: StatsSpec, view: ViewId): string | null {
  if (chart.rows.length > maxRowsFor(view)) return `${ONE_ROW_VIEWS} mají v Řádcích jen jednu dimenzi`;
  if (chart.cols.length > maxColsFor(view)) return `${lower(VIEW_LABEL[view])} nemá Sloupce`;
  return null;
}

/* The Sloupce shelf of a view that takes no column. */
export const noColumnsText = (view: ViewId): string =>
  `${VIEW_LABEL[view]} nemá Sloupce — dimenze patří do Řádků.`;

/* Why the chart cannot be saved (null = it can). Only line / bar have a row
 * minimum (one dimension on x). */
export function chartProblem(chart: ChartSpec): string | null {
  if (chart.rows.length < minRowsFor(chart.view)) return LINE_NEEDS_ROW;
  return viewBlock(chart, chart.view);
}

/* Whether the chart draws its picked `stats`: line, bar, and a table with a
 * column dimension (each column value carries one sub-column per statistic). */
export const statUsed = (chart: Pick<ChartSpec, 'view' | 'cols'>): boolean =>
  chart.view === 'line' || chart.view === 'bar' || (chart.view === 'table' && chart.cols.length > 0);

const strArray = (v: unknown): string[] =>
  Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string') : [];

/* A statistic added at the end (it follows the leading ones) or removed — but
 * never the last one: a chart always draws at least one. The one rule the
 * builder's picker toggles through. */
export function toggleStat(stats: readonly ChartStat[], s: ChartStat): ChartStat[] {
  if (!stats.includes(s)) return [...stats, s];
  return stats.length > 1 ? stats.filter((x) => x !== s) : [...stats];
}

/* A saved chart this build can draw, or null. Drops what the registry no longer
 * knows rather than letting one stale chart make the whole board unsaveable. */
export function sanitizeChart(raw: unknown): ChartSpec | null {
  if (raw == null || typeof raw !== 'object') return null;
  const c = raw as Record<string, unknown>;
  const id = c.id;
  if (typeof id !== 'string' || id.length < 1 || id.length > 64) return null;
  if (typeof c.view !== 'string' || !VIEWS.has(c.view)) return null;
  const measure = typeof c.measure === 'string' ? c.measure : '';
  if (!MEASURES.has(measure)) return null;
  const rows = strArray(c.rows);
  const cols = strArray(c.cols);
  const dims = [...rows, ...cols];
  if (dims.some((d) => !DIMS.has(d)) || new Set(dims).size !== dims.length) return null;
  /* Known statistics only, once each, in the saved order; none usable reads as the default. */
  const picked = [...new Set(strArray(c.stats).filter((x) => CHART_STATS.has(x)))] as ChartStat[];
  const stats = picked.length > 0 ? picked : [STATS_REGISTRY.default_stat];
  const chart: ChartSpec = { id, view: c.view as ViewId, rows, cols, measure, stats };
  return chartProblem(chart) == null ? chart : null;
}

export const sanitizeCharts = (raw: unknown): ChartSpec[] =>
  Array.isArray(raw) ? raw.map(sanitizeChart).filter((c): c is ChartSpec => c != null) : [];

/* Two boards hold the same charts in the same order. sanitizeChart builds every
 * chart with one key order, so the JSON of two sanitized lists is a stable compare. */
export const chartsEqual = (a: readonly ChartSpec[], b: readonly ChartSpec[]): boolean =>
  JSON.stringify(a.map(sanitizeChart)) === JSON.stringify(b.map(sanitizeChart));

type Shelf = 'rows' | 'cols';

export const removeDim = (chart: ChartSpec, dim: string): ChartSpec => ({
  ...chart,
  rows: chart.rows.filter((d) => d !== dim),
  cols: chart.cols.filter((d) => d !== dim),
});

/* Put `dim` on a shelf (moving it if it is already on one), at `index` within
 * Řádky. A move the view's limits forbid returns the chart unchanged — the one
 * acceptance rule every drop, chip and slot button asks. */
export function placeDim(chart: ChartSpec, dim: string, to: Shelf, index?: number): ChartSpec {
  if (!DIMS.has(dim)) return chart;
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
  return { ...chart, rows: arrayMove(chart.rows, from, to) };
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

/* A dimension that does not exist for the cohort's category (area bands off byty). */
export function dimBlock(dim: string, categoryMain: string | null): string | null {
  const d = DIMS.get(dim);
  if (!d?.categories || categoryMain == null || d.categories.includes(categoryMain)) return null;
  return `jen ${d.categories.map((c) => lower(categoryMainLabelPlural(c))).join(', ')}`;
}

/* The one sentence naming the dimensions the category rules out, and why:
 * "Plocha po 5 m², Plocha po 10 m²: jen byty." — null when none is. */
export function categoryBlockNote(dims: readonly string[], categoryMain: string | null): string | null {
  const off = dims.filter((d) => dimBlock(d, categoryMain) != null);
  return off.length > 0 ? `${off.map(dimLabel).join(', ')}: ${dimBlock(off[0], categoryMain)}.` : null;
}

/* The builder's palette: the registry's dimensions grouped by their `group`, the
 * groups and their members in registry order. */
export const DIMENSION_GROUPS: ReadonlyArray<{ label: string; dims: readonly string[] }> = (() => {
  const groups = new Map<string, string[]>();
  for (const d of STATS_REGISTRY.dimensions) {
    const dims = groups.get(d.group);
    if (dims) dims.push(d.id);
    else groups.set(d.group, [d.id]);
  }
  return [...groups].map(([label, dims]) => ({ label, dims }));
})();

/* -------------------------------------------------------------------------- */
/* Order, labels, units, titles                                                */
/* -------------------------------------------------------------------------- */

const CS = new Intl.Collator('cs');
const NBSP = ' ';

/* A place key reads as its register name; a code the register cannot name reads as itself. */
const placeLabel = (dim: string, key: string, places: PlaceNames): string =>
  places.get(placeKey(dim, key)) ?? key;

/* The one order of a dimension's keys: enum/bool by the registry's option order
 * (unknown members after, alphabetically), bands by their lower bound, places
 * by their label with Czech collation (two places of one name by code). */
export function sortKeys(dimId: string, keys: readonly string[], places: PlaceNames): string[] {
  const d = DIMS.get(dimId);
  const out = [...keys];
  if (d?.kind === 'band') return out.sort((a, b) => Number(a) - Number(b));
  if (d?.kind === 'geo') {
    const label = new Map(keys.map((k) => [k, placeLabel(dimId, k, places)]));
    return out.sort((a, b) => CS.compare(label.get(a)!, label.get(b)!) || csCollator.compare(a, b));
  }
  const order = d?.options ? OPTIONS.get(dimId) : undefined;
  if (!order) return out.sort(CS.compare);
  return out.sort((a, b) => {
    const ia = order.get(a)?.rank;
    const ib = order.get(b)?.rank;
    if (ia != null && ib != null) return ia - ib;
    if (ia != null) return -1;
    if (ib != null) return 1;
    return CS.compare(a, b);
  });
}

const isRent = (basis: string | null): boolean => ppm2BasisFromToken(basis) === 'rent';

/* A unit kind, spelled for the payload's basis. Price and per-m² have no unit
 * without one — a visible gap, never a guess. */
function unitFor(unit: MeasureUnit, basis: string | null): string {
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
  const m = MEASURES.get(measureId);
  return m ? unitFor(m.unit, basis) : '';
};

/* "Plocha · m²": a label and the unit the payload's basis names, the label alone without one. */
export const withUnit = (label: string, unit: string): string => (unit ? `${label} · ${unit}` : label);

/* The measure as a table's value header names it: "Cena za m² · Kč/m²". */
export const quantityLabel = (measureId: string, basis: string | null): string =>
  withUnit(measureLabel(measureId), measureUnit(measureId, basis));

/* The band's width for the payload's basis: {all} for area, else rent or sale. */
function bandStep(d: StatsDimension, basis: string | null): number | null {
  if (!d.steps) return null;
  if (d.steps.all != null) return d.steps.all;
  return (isRent(basis) ? d.steps.rent : d.steps.sale) ?? null;
}

/* A key as the reader sees it: a band ⇒ "80 – 90 m²", an enum ⇒ its label_cs,
 * a place ⇒ its register name from the pivot's `places` (the code when the
 * register has none). An unknown member reads as itself. */
export function keyLabel(dimId: string, key: string, basis: string | null, places: PlaceNames): string {
  const d = DIMS.get(dimId);
  if (!d) return key;
  if (d.kind === 'band') {
    const lo = Number(key);
    const step = bandStep(d, basis);
    if (!Number.isFinite(lo) || step == null) return key;
    const unit = d.measure ? measureUnit(d.measure, basis) : '';
    return `${fmtCount(lo)} – ${fmtCount(lo + step)}${unit ? NBSP + unit : ''}`;
  }
  if (d.kind === 'geo') return placeLabel(dimId, key, places);
  return OPTIONS.get(dimId)?.get(key)?.label ?? key;
}

/* A dimension as a chart names it. A band is named by its step in its measure's
 * unit for the payload's basis ("Cena po 2 500 Kč / měs", "Cena za m² po 5 000
 * Kč/m²"); with no single basis the step is unknown and the registry's two-step
 * label stands (the palette chip always shows that one: it serves every cohort). */
export function dimTitle(id: string, basis: string | null): string {
  const d = DIMS.get(id);
  const m = d?.kind === 'band' && d.measure ? MEASURES.get(d.measure) : undefined;
  if (!d || !m) return dimLabel(id);
  const step = bandStep(d, basis);
  const unit = unitFor(m.unit, basis);
  if (step == null || !unit) return dimLabel(id);
  return `${m.label_cs} po ${fmtCount(step)}${NBSP}${unit}`;
}

/* "Dispozice › Stav": the nested row dimensions, bands named for the basis. */
export const rowsTitle = (spec: StatsSpec, basis: string | null): string =>
  spec.rows.map((d) => dimTitle(d, basis)).join(' › ');

/* "Cena za m² · Dispozice › Stav × Materiál": the measure, the nested rows, the
 * column; a band named for the payload's basis when one is known. */
export function chartTitle(chart: StatsSpec, basis: string | null = null): string {
  const dims = [rowsTitle(chart, basis), ...chart.cols.map((d) => dimTitle(d, basis))].filter(Boolean).join(' × ');
  return dims ? `${measureLabel(chart.measure)} · ${dims}` : measureLabel(chart.measure);
}

const FMT0 = new Intl.NumberFormat('cs-CZ', { maximumFractionDigits: 0 });
const FMT1 = new Intl.NumberFormat('cs-CZ', { maximumFractionDigits: 1 });

/* A measure's number format (its unit rides in the header), resolved once per
 * view: money to the crown, areas to a tenth, days whole. */
export const statFormat = (measureId: string): ((v: number) => string) =>
  (MEASURES.get(measureId)?.unit === 'm2' ? FMT1 : FMT0).format;

/* -------------------------------------------------------------------------- */
/* Cells → nested rows                                                         */
/* -------------------------------------------------------------------------- */

interface PivotNode {
  keys: string[];
  cells: Map<string, StatsCell>;
  children: PivotNode[];
}

/* The no-column cell is keyed ''. */
const colKey = (col: string | null): string => col ?? '';

/* Children by key prefix: a level-L cell hangs under the level-(L-1) node with
 * its first L-1 keys. Each level's keys are ordered ONCE by its own dimension,
 * and every node's children by that one rank. */
function cellTree(pivot: StatsPivot): PivotNode {
  const { spec, places } = pivot;
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
  for (const c of pivot.cells) node(c.keys).cells.set(colKey(c.col), c);
  const last = (n: PivotNode): string => n.keys[n.keys.length - 1];
  const levels = spec.rows.map(() => new Set<string>());
  for (const n of byPath.values()) if (n.keys.length > 0) levels[n.keys.length - 1]?.add(last(n));
  const ranks = levels.map((keys, i) => new Map(sortKeys(spec.rows[i], [...keys], places).map((k, r) => [k, r])));
  for (const n of byPath.values()) {
    const rank = ranks[n.keys.length];
    if (rank && n.children.length > 1) {
      n.children.sort((a, b) => (rank.get(last(a)) ?? 0) - (rank.get(last(b)) ?? 0));
    }
  }
  return root;
}

/* One row of a nested pivot table. */
export interface PivotRow {
  /* React key: the row's keys joined. */
  key: string;
  depth: number;
  /* A row with deeper rows under it (the whole sample, when there are rows). */
  rollup: boolean;
  /* Its last key's label, or "Celkem" for the whole sample. */
  label: string;
  /* The cell per column value; '' is the no-column cell. */
  cells: ReadonlyMap<string, StatsCell>;
}

/* The pivot's rows in reading order: every rollup above its children. */
export function pivotRows(pivot: StatsPivot): PivotRow[] {
  const { spec, basis, places } = pivot;
  const out: PivotRow[] = [];
  const walk = (n: PivotNode): void => {
    const depth = n.keys.length;
    out.push({
      key: n.keys.join('›'),
      depth,
      rollup: depth < spec.rows.length,
      label: depth === 0 ? 'Celkem' : keyLabel(spec.rows[depth - 1], n.keys[depth - 1], basis, places),
      cells: n.cells,
    });
    n.children.forEach(walk);
  };
  walk(cellTree(pivot));
  return out;
}

/* The column dimension's values in its order; [] without one. */
export function colValuesOf(pivot: StatsPivot): string[] {
  const dim = pivot.spec.cols[0];
  if (!dim) return [];
  const values = new Set<string>();
  for (const c of pivot.cells) if (c.col != null) values.add(c.col);
  return sortKeys(dim, [...values], pivot.places);
}

/* -------------------------------------------------------------------------- */
/* Series (line / bar): one row dimension on x, the column dimension as series  */
/* -------------------------------------------------------------------------- */

export interface SeriesModel {
  series: Array<{ key: string; stat: ChartStat; label: string; dataKey: string }>;
  data: Array<Record<string, string | number | null>>;
  /* Some (x, series) has fewer than min_n properties, or none — drawn as a gap. */
  gaps: boolean;
}

/* An (x, column) pair's cell key. */
const xcKey = (x: string, col: string | null): string => `${x}|${colKey(col)}`;

/* Built for a spec with exactly one row (the view's minimum, which the card
 * checks before it draws). */
export function seriesModel(pivot: StatsPivot, stats: readonly ChartStat[]): SeriesModel {
  const { spec, basis, places } = pivot;
  const xDim = spec.rows[0];
  const colDim = spec.cols[0];
  const level1 = pivot.cells.filter((c) => c.keys.length === 1);
  const xs = sortKeys(xDim, [...new Set(level1.map((c) => c.keys[0]))], places);
  /* One series per (column value, statistic), a column's statistics side by side
   * in the picked order. A lone statistic is not named in the legend (the caption
   * names it); several are, so each line says which it is. */
  const several = stats.length > 1;
  const columns = colDim
    ? colValuesOf(pivot).map((k) => ({ key: k, label: keyLabel(colDim, k, basis, places) }))
    : [{ key: '', label: measureLabel(spec.measure) }];
  const series = columns.flatMap((col, ci) =>
    stats.map((st, si) => ({
      key: col.key,
      stat: st,
      label: !several ? col.label : colDim ? `${col.label} · ${STAT_LABEL[st]}` : STAT_LABEL[st],
      dataKey: `s${ci * stats.length + si}`,
    })),
  );
  const at = new Map(level1.map((c) => [xcKey(c.keys[0], c.col), c]));
  let gaps = false;
  const data = xs.map((x) => {
    const row: Record<string, string | number | null> = { x, label: keyLabel(xDim, x, basis, places) };
    for (const s of series) {
      const v = at.get(xcKey(x, s.key))?.[s.stat] ?? null;
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

export const tooManySeriesText = (): string =>
  `Řad je víc než ${MAX_SERIES} — použijte Tabulku, zužte filtr nebo vyberte méně statistik.`;

/* -------------------------------------------------------------------------- */
/* The sample ledger                                                           */
/* -------------------------------------------------------------------------- */

interface LedgerCounts {
  filtered: number;
  sample: number;
  excluded: number;
  /* Of the sample: in a leaf cell with statistics, and in one under min_n.
   * Null when the payload has no cells (a refusal) — the split is unknown. */
  split: { withStats: number; underMin: number } | null;
}

/* Every sample property lands in exactly one leaf cell (level = rows.length,
 * per column value), so the leaves split the sample into what is behind a
 * number and what is only counted. Null on a bad_spec, which counts nothing. */
export function ledgerCounts(pivot: StatsPivot): LedgerCounts | null {
  const { filtered, sample, excluded } = pivot;
  if (filtered == null || sample == null || excluded == null) return null;
  const leaves = pivot.cells.filter((c) => c.keys.length === pivot.spec.rows.length);
  let withStats = 0;
  let underMin = 0;
  for (const c of leaves) {
    if (c.median != null) withStats += c.n;
    else underMin += c.n;
  }
  return { filtered, sample, excluded, split: leaves.length > 0 ? { withStats, underMin } : null };
}

const optionLabel = (filterId: string, value: string): string | null => {
  const o = filterById(filterId)?.enum_values?.find((e) => String(e.value) === value);
  return o ? o.label_cs : null;
};

/* The cohort facts the payload states (byty · prodej · aktivní). */
export function cohortChips(pivot: StatsPivot): string[] {
  if (pivot.filtered == null || pivot.filtered === 0) return [];
  const chips: string[] = [];
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
      return `Graf by měl víc než ${fmtCount(STATS_REGISTRY.max_cells)} buněk — uberte dimenzi nebo zužte filtr.`;
    case 'bad_spec':
      return 'Nastavení grafu je neplatné — upravte ho.';
  }
}

const stats: ChartStat[] = [STATS_REGISTRY.default_stat];

/* The seed board. Every chart must answer on DEFAULT_FILTERS (status 'any',
 * byty): no turnover (it needs one status) and no byty-only dimension that a
 * cohort switch would empty. stats.test.ts pins both. */
export const DEFAULT_CHARTS: ChartSpec[] = [
  { id: 'default-1', view: 'table', rows: [], cols: [], measure: 'price', stats },
  { id: 'default-2', view: 'table', rows: [], cols: [], measure: 'ppm2', stats },
  { id: 'default-3', view: 'box', rows: ['disposition'], cols: [], measure: 'ppm2', stats },
  { id: 'default-4', view: 'box', rows: ['condition'], cols: [], measure: 'ppm2', stats },
];

const newChartId = (): string =>
  `c-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;

export const newChart = (): ChartSpec => ({
  id: newChartId(),
  view: 'table',
  rows: [],
  cols: [],
  measure: 'ppm2',
  stats,
});
