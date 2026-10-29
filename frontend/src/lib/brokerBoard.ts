/* The broker board (docs/design/broker-board.md): Browse's cohort counted by
 * broker, one POST /brokers/board, read by Browse's Makléři tab and the phone's
 * Makléři screen. A market read: the account lenses (the registry's Curation
 * group) cannot be judged on the service connection, so they are never sent. */

import { keepPreviousData, useQuery } from '@tanstack/react-query';

import { ApiError, apiPost } from './api';
import { browseKeys } from './browseKeys';
import type { BrokerContactFields } from './brokers';
import { filtersForAgenda } from './filterRegistry.generated';
import { DEFAULT_FILTERS, effectiveBbox, listingFiltersToRegistryView, type ListingFilters } from './filters';
import { scopeActive } from './setScope';

export const BOARD_HORIZONS = [14, 30, 60, 90] as const;
type BoardHorizon = (typeof BOARD_HORIZONS)[number];
export type BoardSort = 'properties' | 'speed';
export const BOARD_LIMITS = [50, 100, 200] as const;

/* The tab's own knobs: view state beside the filters, outside preset identity. */
export interface BoardOptions {
  horizonDays: BoardHorizon;
  sort: BoardSort;
  limit: number;
  minProperties: number;
  /* An OUTPUT filter on the broker's firm; the cohort is the filters'. */
  firmIds: number[];
}

export const DEFAULT_BOARD: BoardOptions = {
  horizonDays: 30,
  sort: 'properties',
  limit: 100,
  minProperties: 1,
  firmIds: [],
};

/* Each option's URL key; a default deletes its key, so a default board is the plain URL. */
const URL_KEY = { horizonDays: 'horizon', sort: 'rank_by', limit: 'top', minProperties: 'min_props', firmIds: 'firm' };
export const BOARD_URL_KEYS = Object.values(URL_KEY);

const positiveInt = (raw: string | null): number | null => {
  const n = raw == null ? NaN : Number(raw);
  return Number.isInteger(n) && n >= 1 ? n : null;
};

export function boardFromSearchParams(sp: URLSearchParams): BoardOptions {
  const horizon = positiveInt(sp.get('horizon'));
  const top = positiveInt(sp.get('top'));
  return {
    horizonDays: BOARD_HORIZONS.find((h) => h === horizon) ?? DEFAULT_BOARD.horizonDays,
    sort: sp.get('rank_by') === 'speed' ? 'speed' : 'properties',
    limit: top != null && top <= BOARD_LIMITS[BOARD_LIMITS.length - 1] ? top : DEFAULT_BOARD.limit,
    minProperties: positiveInt(sp.get('min_props')) ?? DEFAULT_BOARD.minProperties,
    firmIds: (sp.get('firm') ?? '').split(',').flatMap((s) => positiveInt(s) ?? []),
  };
}

/* Writes `b` into `sp` (mutating it) and returns it. */
export function writeBoard(sp: URLSearchParams, b: BoardOptions): URLSearchParams {
  for (const [k, key] of Object.entries(URL_KEY) as [keyof BoardOptions, string][]) {
    if (String(b[k]) === String(DEFAULT_BOARD[k])) sp.delete(key);
    else sp.set(key, String(b[k]));
  }
  return sp;
}

/* BoardArgs' registry filters: Browse's, less `location` (the board takes the
 * place as `bounds` and `districts`) and the account lenses. */
const BOARD_FILTER_IDS = filtersForAgenda('browse')
  .filter((d) => d.id !== 'location' && d.category !== 'Curation')
  .map((d) => d.id);

/* What the board cannot honour, at Browse's defaults: the Curation lenses and
 * the market-growth filter (resolved in the browser, no registry id). The query
 * key carries the filters through this, so a lens edit does not refetch. */
export const marketFilters = (f: ListingFilters): ListingFilters => ({
  ...f,
  tags: DEFAULT_FILTERS.tags,
  collections: DEFAULT_FILTERS.collections,
  pipeline: DEFAULT_FILTERS.pipeline,
  withEstimates: DEFAULT_FILTERS.withEstimates,
  showDismissed: DEFAULT_FILTERS.showDismissed,
  priceGrowthRules: DEFAULT_FILTERS.priceGrowthRules,
});

/* The set filters the board leaves out, in words. Dismissed properties are
 * always counted (CoverageLine says so); the rest are named when set. */
export function boardIgnores(f: ListingFilters): string[] {
  return [
    scopeActive(f.tags) && 'štítky',
    scopeActive(f.collections) && 'kolekce',
    scopeActive(f.pipeline) && 'pipeline',
    f.withEstimates && 'jen s odhadem',
    f.priceGrowthRules.length > 0 && 'růst trhu',
  ].filter((x): x is string => Boolean(x));
}

/* The POST body: the registry view narrowed to BoardArgs, the map box Browse
 * filters on, the broker pill as a cohort scope, the tab's options. */
export function boardRequest(f: ListingFilters, o: BoardOptions): Record<string, unknown> {
  const view = listingFiltersToRegistryView(f);
  return {
    ...Object.fromEntries(BOARD_FILTER_IDS.map((id) => [id, view[id]])),
    bounds: effectiveBbox(f),
    broker_id: f.brokerId,
    horizon_days: o.horizonDays,
    sort: o.sort,
    limit: o.limit,
    min_properties: o.minProperties,
    firm_ids: o.firmIds,
  };
}

/* One broker: the Ledger's identity fields, contacts per apply_pii_policy. */
export interface BoardRow extends BrokerContactFields {
  broker_id: number;
  display_name: string | null;
  firm_id: number | null;
  firm_name: string | null;
  firm_domain: string | null;
  /* Volume: distinct cohort properties credited to the broker. */
  properties: number;
  /* Followed spells (exited + not_exited), and the exited among them. */
  n: number;
  exited: number;
  unknown: number;
  /* exited / n; null under min_n or when speed is refused. */
  share: number | null;
  /* The Wilson lower bound (z = 1.96); null likewise. */
  rank: number | null;
}

export interface BoardMeta {
  queried_at?: string;
  cohort_properties: number;
  attributed_properties: number;
  unattributed_share: number | null;
  /* Pair-weighted over every attributed (property, broker) pair of the cohort. */
  market: { n: number; exited: number; share: number | null };
  unknown: number;
  horizon_days: number;
  clean_exits_since: string;
  margin_days: number;
  min_n: number;
  speed_refused: 'outcome_selected' | 'mixed_category' | null;
  /* The registry filters to change for speed to answer: the outcome ones set, or the category ones the cohort mixes. */
  speed_refused_by: string[];
  uncovered_portals: string[];
}

export interface BrokerBoardResult {
  rows: BoardRow[];
  meta: BoardMeta;
}

export async function fetchBrokerBoard(
  args: Record<string, unknown>,
  signal?: AbortSignal,
): Promise<BrokerBoardResult> {
  const r = await apiPost<{ data?: BoardRow[]; metadata?: BoardMeta }>('/brokers/board', args, signal);
  // A 200 with no envelope is an SPA fallback or a proxy page, not an empty board.
  if (!r?.data || !r.metadata) throw new ApiError('malformed /brokers/board response', 0, r);
  return { rows: r.data, meta: r.metadata };
}

/* The board of the current filters: the previous answer stays (dimmed) while the next loads. */
export function useBrokerBoard(f: ListingFilters, o: BoardOptions) {
  return useQuery<BrokerBoardResult, Error>({
    queryKey: browseKeys.brokers(marketFilters(f), o),
    queryFn: ({ signal }) => fetchBrokerBoard(boardRequest(f, o), signal),
    staleTime: 60_000,
    placeholderData: keepPreviousData,
  });
}

/* A row's speed as the board shows it: refused, only n under min_n, or k of n and the share. */
export type Speed =
  | { kind: 'refused' }
  | { kind: 'small'; n: number }
  | { kind: 'share'; exited: number; n: number; share: number; rank: number | null };

export function speedOf(r: Pick<BoardRow, 'n' | 'exited' | 'share' | 'rank'>, meta: BoardMeta): Speed {
  if (meta.speed_refused) return { kind: 'refused' };
  if (r.share == null) return { kind: 'small', n: r.n };
  return { kind: 'share', exited: r.exited, n: r.n, share: r.share, rank: r.rank };
}

/* The order the rows are in: a refused speed ranks nothing, so volume orders them. */
export const shownSort = (o: BoardOptions, meta: BoardMeta | undefined): BoardSort =>
  meta?.speed_refused ? 'properties' : o.sort;

/* The entry window a speed answer covers: spells started from E0 up to N + M
 * days before the read. Until E0 + N + M it is empty, and `opens` is that day:
 * the first on which a spell begun at E0 has been followed N days and M more. */
export function entryWindow(meta: BoardMeta): { from: Date; to: Date; opens: Date | null } {
  const at = Date.parse(meta.queried_at ?? '') || Date.now();
  const span = (meta.horizon_days + meta.margin_days) * 86_400_000;
  const from = new Date(meta.clean_exits_since);
  const to = new Date(at - span);
  return { from, to, opens: to < from ? new Date(from.getTime() + span) : null };
}
