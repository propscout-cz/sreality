/* The Health page's one read and the pure rules it renders by (docs/design/health-dashboard.md
 * § 5, wave 8). `pipeline_checks_public` (migration 619) serves the registry, each check's newest
 * result, the view's own judgement (`effective_status`: Not measured when that result is past its
 * staleness budget) and the check's incidents; everything below ranks, counts or pivots that one
 * read. The browser holds no threshold and no per-key table: sections, labels, units, order and
 * owner links come from the registry, every status from the checks themselves.
 *
 * Only the lazy Health chunk imports this module. Keep it out of lib/queries.ts and lib/api.ts:
 * both are first-visit JS, which has a blocking size budget (scripts/bundle-budget.mjs). */
import { matchRoutes } from 'react-router-dom';
import { routes } from '@/routes';
import { apiPost } from '@/lib/api';
import { pgRead, type PgReadOptions } from '@/lib/pgRead';
import { supabase } from '@/lib/supabase';

/* The four-word scale (GLOSSARY.md § Health): OK, Watch, Problem, Not measured. */
export type Status = 'ok' | 'warn' | 'fail' | 'unknown';

/* One judged unit of a check (a portal arm, a job, a lane); scripts/checks/common.py `cell`.
 * `portal` is null for a fleet-wide cell (`fleet/stock`, parsing's `census`, every platform
 * cell). The page never parses a subject: it reads `portal`. */
export type HealthCell = {
  subject: string;
  portal: string | null;
  status: Status;
  value: number | null;
  unit: string | null;
  message: string;
  facts: Record<string, unknown>;
};

/* One `ops_incidents` row of a cell (signature `cell:<key>:<subject>`): open, or closed in
 * the last 14 days. `notice_count` / `last_notice_at` are the ladder's onset and reminders
 * (`alert_count` / `alerted_at`; since ruling R2b, 2026-10-21, no system alert reaches the bell). */
export type HealthIncident = {
  id: number;
  subject: string;
  first_seen_at: string;
  last_seen_at: string;
  resolved_at: string | null;
  acknowledged_at: string | null;
  notice_count: number;
  last_notice_at: string | null;
};

/* One row of pipeline_checks_public: all 18 columns (migration 619). */
export type HealthRow = {
  check_key: string;
  /* null: not measured since the registry declared the check (`declared_at`). */
  run_at: string | null;
  /* The newest STORED verdict. The page shows `effective_status`, never this. */
  status: Status | null;
  value: number | null;
  /* `cells` is absent only on a result written before wave 6 (the legacy outbound row);
   * a check that measured nothing stores `cells: []` and says why in `message`/`skipped`. */
  details: {
    cells?: HealthCell[];
    message?: string;
    not_measured?: Record<string, string>;
    skipped?: string;
    error?: string;
    [key: string]: unknown;
  } | null;
  created_at: string | null;
  label: string;
  unit: string;
  domain: string;
  class: 'alarm' | 'gauge';
  host: string;
  cadence_s: number;
  owner_path: string;
  sort: number;
  declared_at: string;
  last_measured_at: string | null;
  /* The view's judgement: unknown when never measured, measured nothing, or stale. */
  effective_status: Status;
  incidents: HealthIncident[];
};

/* The one check the page treats by key: the watchers' row is the header (design § 5.1). */
export const LIVENESS_KEY = 'liveness';

/* The page's four sections in their fixed order, keyed by the registry's `domain`. */
export const SECTIONS = [
  ['portals', 'Portals'],
  ['dedup', 'Engine'],
  ['data_quality', 'Data quality'],
  ['platform', 'Platform'],
] as const;

/* Zero rows is never "all clear": the view returns none (HTTP 200) to a session that is not a
 * platform admin, and to a registry no host has projected. */
export class HealthEmptyError extends Error {
  constructor() {
    super(
      "The health view returned no checks. An empty read is never 'all clear': this session may " +
        'not be a platform admin, or no host has projected the check registry.',
    );
    this.name = 'HealthEmptyError';
  }
}

const COLUMNS =
  'check_key,run_at,status,value,details,created_at,label,unit,domain,class,host,cadence_s,' +
  'owner_path,sort,declared_at,last_measured_at,effective_status,incidents';

const SCALE = new Set<string>(['ok', 'warn', 'fail', 'unknown']);

/* Anything off the scale (a null, a future word) reads Not measured, never OK. */
const asStatus = (s: unknown): Status => (typeof s === 'string' && SCALE.has(s) ? (s as Status) : 'unknown');

function normalizeRow(raw: HealthRow): HealthRow {
  const details = raw.details && typeof raw.details === 'object' ? raw.details : null;
  const cells = Array.isArray(details?.cells)
    ? details.cells.map((c) => ({
        ...c,
        portal: c.portal || null,
        status: asStatus(c.status),
        facts: c.facts && typeof c.facts === 'object' ? c.facts : {},
      }))
    : null;
  return {
    ...raw,
    status: raw.status == null ? null : asStatus(raw.status),
    effective_status: asStatus(raw.effective_status),
    details: details && cells ? { ...details, cells } : details,
    incidents: Array.isArray(raw.incidents) ? raw.incidents : [],
  };
}

/* The page's one read: every column, ordered by key; statuses normalised onto the scale. */
export async function fetchHealthChecks({ signal }: PgReadOptions = {}): Promise<HealthRow[]> {
  const { data } = await pgRead<HealthRow[] | null>(
    supabase.from('pipeline_checks_public').select(COLUMNS).order('check_key', { ascending: true }),
    { signal },
  );
  const rows = (data ?? []).map(normalizeRow);
  if (!rows.length) throw new HealthEmptyError();
  return rows;
}

/* Every 5 minutes while the tab is visible (TanStack pauses the interval while hidden:
 * refetchIntervalInBackground defaults to false), and again on focus (main.tsx turns that off
 * globally). A polling cadence, not a threshold. `networkMode: 'always'`: TanStack's default
 * pauses a fetch, silently, while the browser reports itself offline; here an offline poll fails
 * into the page's "refetch failed" line instead. */
export const HEALTH_QUERY = {
  queryKey: ['health-checks'] as const,
  queryFn: fetchHealthChecks,
  refetchInterval: 300_000,
  refetchOnWindowFocus: true,
  staleTime: 60_000,
  networkMode: 'always' as const,
};

/* Worst first: Problem, Watch, Not measured, OK. */
export const STATUS_RANK: Readonly<Record<Status, number>> = { fail: 0, warn: 1, unknown: 2, ok: 3 };

const rank = (s: unknown): number => STATUS_RANK[asStatus(s)];
const cmp = (a: string, b: string): number => (a < b ? -1 : a > b ? 1 : 0);

export const isOpen = (inc: HealthIncident): boolean => inc.resolved_at == null;

/* An OPEN incident nobody acknowledged: what the Acknowledge button can still stop. */
export const isUnacknowledged = (inc: HealthIncident): boolean =>
  isOpen(inc) && inc.acknowledged_at == null;

const cellsOf = (row: HealthRow): HealthCell[] => row.details?.cells ?? [];
const effective = (row: HealthRow): Status => asStatus(row.effective_status);

/* Rows worst first: status rank; then alarm before gauge, a row with an unacknowledged open
 * incident before one without (acknowledging hands a row back to registry order), the
 * registry's sort, and the key. */
export function worstFirst(rows: HealthRow[]): HealthRow[] {
  const classRank = (r: HealthRow) => (r.class === 'alarm' ? 0 : 1);
  const ackRank = (r: HealthRow) => (r.incidents.some(isUnacknowledged) ? 0 : 1);
  return [...rows].sort(
    (a, b) =>
      rank(a.effective_status) - rank(b.effective_status) ||
      classRank(a) - classRank(b) ||
      ackRank(a) - ackRank(b) ||
      a.sort - b.sort ||
      cmp(a.check_key, b.check_key),
  );
}

/* Cells worst first (Problem, Watch, Not measured, OK), by subject inside a status. */
export function cellsWorstFirst(cells: HealthCell[]): HealthCell[] {
  return [...cells].sort((a, b) => rank(a.status) - rank(b.status) || cmp(a.subject, b.subject));
}

type MatrixCell = { status: Status; cell: HealthCell | null; measured: number; notMeasured: number };

/* One check's verdict for one portal, by the harness's own rule (common.result): the worst
 * MEASURED cell; Not measured only when nothing was. A row the view reads Not measured (stale
 * or unmeasured) measured nothing now, so its stored cells never vote. */
function portalVerdict(row: HealthRow, portal: string): MatrixCell {
  const mine = cellsOf(row).filter((c) => c.portal === portal);
  const measured = effective(row) === 'unknown' ? [] : mine.filter((c) => asStatus(c.status) !== 'unknown');
  const worst = cellsWorstFirst(measured)[0] ?? null;
  return {
    status: worst ? asStatus(worst.status) : 'unknown',
    cell: worst,
    measured: measured.length,
    notMeasured: mine.length - measured.length,
  };
}

/* The portal matrix: columns = the `portals` section's checks in registry order, rows = every
 * portal a cell of theirs names (alphabetical). Fleet cells (portal null) never enter. A result
 * with no `cells` array at all (pre-wave-6) is not a column; a check that measured nothing
 * (`cells: []`) or was never measured stays one, Not measured for every portal. */
export function pivotByPortal(rows: HealthRow[]): {
  columns: HealthRow[];
  portals: string[];
  cells: Record<string, Record<string, MatrixCell>>;
} {
  const columns = rows
    .filter((r) => r.domain === 'portals' && !(r.details && !Array.isArray(r.details.cells)))
    .sort((a, b) => a.sort - b.sort || cmp(a.check_key, b.check_key));
  const named = new Set<string>();
  for (const col of columns) for (const c of cellsOf(col)) if (c.portal) named.add(c.portal);
  const portals = [...named].sort(cmp);
  const cells: Record<string, Record<string, MatrixCell>> = {};
  for (const p of portals) {
    cells[p] = {};
    for (const col of columns) cells[p][col.check_key] = portalVerdict(col, p);
  }
  return { columns, portals, cells };
}

/* The top strip. Open incidents: ALARM incidents only (a standing gauge is not news), with the
 * oldest onset. Problems / Watch: alarm checks by the view's status; Not measured: every check.
 * Gauges worse than last week: gauge cells of currently measured rows whose `worse_7d` is true;
 * null ("not comparable") while none of them carries a boolean (no point 7 days back yet). */
export function stripCounts(rows: HealthRow[]): {
  openIncidents: { count: number; oldest: string | null };
  problems: number;
  watch: number;
  notMeasured: number;
  gaugesWorse: number | null;
} {
  let count = 0;
  let oldest: string | null = null;
  let oldestAt = Infinity;
  let problems = 0;
  let watch = 0;
  let notMeasured = 0;
  let comparable = false;
  let worse = 0;
  for (const r of rows) {
    const status = effective(r);
    if (status === 'unknown') notMeasured += 1;
    if (r.class === 'alarm') {
      if (status === 'fail') problems += 1;
      if (status === 'warn') watch += 1;
      for (const inc of r.incidents.filter(isOpen)) {
        count += 1;
        const at = Date.parse(inc.first_seen_at);
        if (at < oldestAt) {
          oldestAt = at;
          oldest = inc.first_seen_at;
        }
      }
    } else if (r.class === 'gauge' && status !== 'unknown') {
      for (const c of cellsOf(r)) {
        const w = c.facts?.worse_7d;
        if (typeof w !== 'boolean') continue;
        comparable = true;
        if (w) worse += 1;
      }
    }
  }
  return {
    openIncidents: { count, oldest },
    problems,
    watch,
    notMeasured,
    gaugesWorse: comparable ? worse : null,
  };
}

/* The element id AND the link hash (without '#') of a check row, or of one of its cells. Keys
 * hold no ':' (`^[a-z0-9_]+$`); subjects hold '/', ':', '?' and could hold anything, so the
 * subject is encodeURIComponent-ed. Never hand-join key and subject: look a target up with
 * document.getElementById(anchorId(key, subject)), never a CSS '#…' selector. */
export function anchorId(key: string, subject?: string): string {
  return subject ? `${key}:${encodeURIComponent(subject)}` : key;
}

/* `location.hash` ('#key' or '#key:subject', with or without the '#') → its target, the
 * subject decoded; split at the FIRST ':'. A subject that does not decode drops to the row. */
export function parseHash(hash: string): { key: string; subject?: string } | null {
  const [key, ...rest] = (hash.startsWith('#') ? hash.slice(1) : hash).split(':');
  if (!key) return null;
  const encoded = rest.join(':');
  if (!encoded) return { key };
  try {
    return { key, subject: decodeURIComponent(encoded) };
  } catch {
    return { key };
  }
}

/* Acknowledge every open, unacknowledged incident of one check (per key: the route refuses a
 * subject). It stops reminders; each incident still closes only on a measured OK. Resolves to
 * how many incidents the server acknowledged (0 when someone else already had). */
export async function acknowledge(key: string): Promise<number> {
  const { acknowledged } = await apiPost<{ key: string; acknowledged: number }>('/admin/health/acknowledge', { key });
  return acknowledged;
}

/* The owner link's whole label, without the arrow: "Open " + the route's `handle.title` from
 * routes.tsx ("Open LLM costs"), or 'Open page' for a path that names no titled route. Render
 * nothing for an empty `owner_path`. */
export function ownerTitle(path: string): string {
  const matches = path ? matchRoutes(routes, path) : null;
  let title: string | undefined;
  for (const m of matches ?? []) {
    if (m.route.path === '*') return 'Open page';
    const own = (m.route.handle as { title?: string } | undefined)?.title;
    if (own) title = own;
  }
  return title ? `Open ${title}` : 'Open page';
}
