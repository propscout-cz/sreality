/* The Health page's pure core (wave 8, contract D2). Pins:
 * - the scale, the four sections in their fixed order, the liveness key;
 * - worstFirst: status rank, then every tie-breaker in turn (alarm before gauge, an
 *   unacknowledged open incident first, registry sort, key); stale reads Not measured;
 * - cellsWorstFirst: status rank, then subject;
 * - pivotByPortal: columns are the `portals` checks in registry order; portals are the union of
 *   their cells' portals; a portal's verdict is the worst MEASURED cell with its counts; fleet
 *   cells never enter; a stale or unmeasured check votes Not measured; a cell-less legacy row is
 *   no column;
 * - stripCounts: alarm incidents only, oldest by time; Problems / Watch from alarms; Not
 *   measured from every check; gauges worse than last week, null while not comparable;
 * - anchors round-trip subjects with ':' and '/' and non-ASCII, split at the FIRST ':';
 * - the read selects all 18 columns, normalises off-scale statuses, and an empty read throws;
 * - the polling options (networkMode 'always': an offline poll fails, never pauses), the
 *   Acknowledge post and the count it resolves to, the owner link's label. */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { UseQueryOptions } from '@tanstack/react-query';

vi.mock('@/lib/api', async (orig) => ({
  ...(await orig<typeof import('@/lib/api')>()),
  apiPost: vi.fn(),
}));

import { apiPost } from '@/lib/api';
import {
  HEALTH_QUERY,
  HealthEmptyError,
  LIVENESS_KEY,
  SECTIONS,
  STATUS_RANK,
  acknowledge,
  anchorId,
  cellsWorstFirst,
  fetchHealthChecks,
  isOpen,
  isUnacknowledged,
  ownerTitle,
  parseHash,
  pivotByPortal,
  stripCounts,
  worstFirst,
  type HealthCell,
  type HealthIncident,
  type HealthRow,
  type Status,
} from '@/lib/healthChecks';

const cell = (
  subject: string,
  status: Status,
  portal: string | null = null,
  facts: Record<string, unknown> = {},
): HealthCell => ({ subject, portal, status, value: null, unit: '', message: `${subject}: ${status}`, facts });

const incident = (over: Partial<HealthIncident> = {}): HealthIncident => ({
  id: 1,
  subject: 'x',
  first_seen_at: '2026-10-19T06:40:00Z',
  last_seen_at: '2026-10-19T17:00:00Z',
  resolved_at: null,
  acknowledged_at: null,
  notice_count: 1,
  last_notice_at: '2026-10-19T06:40:00Z',
  ...over,
});

const row = (check_key: string, over: Partial<HealthRow> = {}): HealthRow => ({
  check_key,
  run_at: '2026-10-19T17:00:00Z',
  status: 'ok',
  value: 0,
  details: { cells: [], message: '' },
  created_at: '2026-10-19T17:00:01Z',
  label: check_key,
  unit: 'cells',
  domain: 'platform',
  class: 'alarm',
  host: 'realtime-worker',
  cadence_s: 600,
  owner_path: '',
  sort: 0,
  declared_at: '2026-10-19T00:00:00Z',
  last_measured_at: '2026-10-19T17:00:00Z',
  effective_status: 'ok',
  incidents: [],
  ...over,
});

const keys = (rows: HealthRow[]) => rows.map((r) => r.check_key);

describe('the scale and the page frame', () => {
  it('ranks Problem, Watch, Not measured, OK', () => {
    const order = (Object.keys(STATUS_RANK) as Status[]).sort((a, b) => STATUS_RANK[a] - STATUS_RANK[b]);
    expect(order).toEqual(['fail', 'warn', 'unknown', 'ok']);
  });

  it('keeps four sections in a fixed order, the dedup domain titled Engine', () => {
    expect(SECTIONS).toEqual([
      ['portals', 'Portals'],
      ['dedup', 'Engine'],
      ['data_quality', 'Data quality'],
      ['platform', 'Platform'],
    ]);
    expect(LIVENESS_KEY).toBe('liveness');
  });

  it('calls an incident open until resolved, and unacknowledged only while open', () => {
    expect(isOpen(incident())).toBe(true);
    expect(isOpen(incident({ resolved_at: '2026-10-19T09:00:00Z' }))).toBe(false);
    expect(isUnacknowledged(incident())).toBe(true);
    expect(isUnacknowledged(incident({ acknowledged_at: '2026-10-19T07:00:00Z' }))).toBe(false);
    // Closed with nobody acknowledging it: nothing left for Acknowledge to stop.
    expect(isUnacknowledged(incident({ resolved_at: '2026-10-19T09:00:00Z' }))).toBe(false);
  });
});

describe('worstFirst', () => {
  it('orders by the view status, and a stale row reads Not measured, never OK', () => {
    const rows = [
      row('fine', { effective_status: 'ok', sort: 1 }),
      // Stored OK, but past its staleness budget: the view says unknown.
      row('stale', { status: 'ok', effective_status: 'unknown', sort: 2 }),
      row('watch', { effective_status: 'warn', sort: 3 }),
      row('problem', { effective_status: 'fail', sort: 4 }),
    ];
    expect(keys(worstFirst(rows))).toEqual(['problem', 'watch', 'stale', 'fine']);
  });

  it('puts an alarm before a gauge at equal status, whatever the sort says', () => {
    const rows = [
      row('gauge', { class: 'gauge', effective_status: 'fail', sort: 0 }),
      row('alarm', { class: 'alarm', effective_status: 'fail', sort: 9 }),
    ];
    expect(keys(worstFirst(rows))).toEqual(['alarm', 'gauge']);
  });

  it('puts an unacknowledged open incident before an acknowledged one, then registry sort', () => {
    const rows = [
      row('acked', { effective_status: 'fail', sort: 1, incidents: [incident({ acknowledged_at: '2026-10-19T07:00:00Z' })] }),
      row('quiet', { effective_status: 'fail', sort: 2 }),
      row('closed', { effective_status: 'fail', sort: 3, incidents: [incident({ resolved_at: '2026-10-19T08:00:00Z' })] }),
      row('ringing', { effective_status: 'fail', sort: 4, incidents: [incident()] }),
    ];
    // Acknowledging hands a row back to registry order: acked, quiet and closed tie, by sort.
    expect(keys(worstFirst(rows))).toEqual(['ringing', 'acked', 'quiet', 'closed']);
  });

  it('applies the tie-breakers in order: class outranks the incident, sort then key last', () => {
    const rows = [
      row('gauge_ringing', { class: 'gauge', effective_status: 'warn', incidents: [incident()] }),
      row('alarm_quiet_b', { effective_status: 'warn', sort: 5 }),
      row('alarm_quiet_a', { effective_status: 'warn', sort: 5 }),
      row('alarm_quiet_first', { effective_status: 'warn', sort: 1 }),
    ];
    expect(keys(worstFirst(rows))).toEqual(['alarm_quiet_first', 'alarm_quiet_a', 'alarm_quiet_b', 'gauge_ringing']);
  });

  it('returns a new array and leaves its input alone', () => {
    const rows = [row('b', { effective_status: 'ok' }), row('a', { effective_status: 'fail' })];
    const sorted = worstFirst(rows);
    expect(sorted).not.toBe(rows);
    expect(keys(rows)).toEqual(['b', 'a']);
  });
});

describe('cellsWorstFirst', () => {
  it('orders Problem, Watch, Not measured, OK, by subject inside a status', () => {
    const cells = [
      cell('b/ok', 'ok'),
      cell('z/unknown', 'unknown'),
      cell('b/fail', 'fail'),
      cell('a/warn', 'warn'),
      cell('a/fail', 'fail'),
    ];
    expect(cellsWorstFirst(cells).map((c) => c.subject)).toEqual(['a/fail', 'b/fail', 'a/warn', 'z/unknown', 'b/ok']);
    expect(cells[0].subject).toBe('b/ok');
  });
});

describe('pivotByPortal', () => {
  const portalRow = (check_key: string, sort: number, cells: HealthCell[], over: Partial<HealthRow> = {}) =>
    row(check_key, { domain: 'portals', sort, details: { cells }, ...over });

  it('takes the portals checks as columns in registry sort, never another section', () => {
    const { columns } = pivotByPortal([
      portalRow('parsing', 9, [cell('bazos/payload', 'ok', 'bazos')]),
      portalRow('scraping', 4, [cell('bazos/walks', 'ok', 'bazos')]),
      portalRow('coverage', 7, [cell('bazos/unnominated', 'ok', 'bazos')], { class: 'gauge' }),
      // Portal-tagged cells outside the section: a lane and a quality gauge.
      row('jobs', { sort: 1, details: { cells: [cell('lane/probe/maxima', 'fail', 'maxima')] } }),
      row('data_quality', { domain: 'data_quality', class: 'gauge', details: { cells: [cell('url/remax', 'fail', 'remax')] } }),
    ]);
    expect(keys(columns)).toEqual(['scraping', 'coverage', 'parsing']);
  });

  it('names the portals its cells carry, alphabetically, and leaves fleet cells out', () => {
    const pivot = pivotByPortal([
      portalRow('publishing', 5, [
        cell('sreality/photos', 'ok', 'sreality'),
        cell('fleet/stock', 'fail', null),
        cell('fleet/dirty', 'warn', null),
      ]),
      portalRow('parsing', 9, [cell('census', 'fail', null), cell('bazos/payload', 'ok', 'bazos')]),
      row('jobs', { details: { cells: [cell('lane/probe/maxima', 'fail', 'maxima')] } }),
    ]);
    expect(pivot.portals).toEqual(['bazos', 'sreality']);
    expect(Object.keys(pivot.cells)).toEqual(['bazos', 'sreality']);
    // The fleet Problem and Watch never reach a portal's verdict or counts.
    expect(pivot.cells.sreality.publishing).toMatchObject({ status: 'ok', measured: 1, notMeasured: 0 });
    expect(pivot.cells.bazos.parsing).toMatchObject({ status: 'ok', measured: 1, notMeasured: 0 });
  });

  it("gives a portal its worst MEASURED cell; Not measured never outranks a measured OK", () => {
    const precision = cell('sreality/precision', 'warn', 'sreality');
    const pivot = pivotByPortal([
      portalRow('delisting', 6, [
        cell('sreality/outflow', 'ok', 'sreality'),
        cell('sreality/stuck', 'unknown', 'sreality'),
        precision,
        cell('remax/outflow', 'ok', 'remax'),
        cell('remax/stuck', 'unknown', 'remax'),
        cell('maxima/outflow', 'unknown', 'maxima'),
      ]),
    ]);
    expect(pivot.cells.sreality.delisting).toEqual({ status: 'warn', cell: precision, measured: 2, notMeasured: 1 });
    expect(pivot.cells.remax.delisting).toMatchObject({ status: 'ok', measured: 1, notMeasured: 1 });
    expect(pivot.cells.maxima.delisting).toEqual({ status: 'unknown', cell: null, measured: 0, notMeasured: 1 });
  });

  it('fills every portal × column, Not measured where a check holds no cell for the portal', () => {
    const pivot = pivotByPortal([
      portalRow('scraping', 4, [cell('bazos/walks', 'fail', 'bazos')]),
      portalRow('publishing', 5, [cell('idnes/visible', 'ok', 'idnes')]),
    ]);
    expect(pivot.cells.bazos.publishing).toEqual({ status: 'unknown', cell: null, measured: 0, notMeasured: 0 });
    expect(pivot.cells.idnes.scraping).toEqual({ status: 'unknown', cell: null, measured: 0, notMeasured: 0 });
    expect(pivot.cells.bazos.scraping.status).toBe('fail');
  });

  it('reads a stale check Not measured for every portal: its stored cells never vote', () => {
    const pivot = pivotByPortal([
      portalRow('scraping', 4, [cell('bazos/walks', 'ok', 'bazos'), cell('bazos/inflow', 'fail', 'bazos')], {
        status: 'fail',
        effective_status: 'unknown',
      }),
    ]);
    expect(pivot.portals).toEqual(['bazos']);
    expect(pivot.cells.bazos.scraping).toEqual({ status: 'unknown', cell: null, measured: 0, notMeasured: 2 });
  });

  it('keeps a check that measured nothing (or never ran) as a column; a cell-less legacy row is none', () => {
    const pivot = pivotByPortal([
      portalRow('scraping', 4, [cell('bazos/walks', 'ok', 'bazos')]),
      portalRow('parsing', 9, [], { status: 'unknown', effective_status: 'unknown', value: null }),
      row('coverage', { domain: 'portals', sort: 7, run_at: null, status: null, value: null, details: null, effective_status: 'unknown' }),
      // A pre-wave-6 result: measured, but no `cells` at all.
      row('legacy', { domain: 'portals', sort: 8, status: 'warn', effective_status: 'warn', value: 13.64, details: { message: '13.64 % of sampled ads off' } }),
    ]);
    expect(keys(pivot.columns)).toEqual(['scraping', 'coverage', 'parsing']);
    expect(pivot.portals).toEqual(['bazos']);
    expect(pivot.cells.bazos.parsing).toEqual({ status: 'unknown', cell: null, measured: 0, notMeasured: 0 });
    expect(pivot.cells.bazos.coverage.status).toBe('unknown');
    expect(pivot.cells.bazos).not.toHaveProperty('legacy');
  });
});

describe('stripCounts', () => {
  it('counts open ALARM incidents only, with the oldest onset by time', () => {
    const strip = stripCounts([
      row('capacity', {
        effective_status: 'fail',
        incidents: [
          incident({ id: 1, first_seen_at: '2026-10-19T06:30:00Z' }),
          // 06:00Z: earlier in time though later as text.
          incident({ id: 2, first_seen_at: '2026-10-19T08:00:00+02:00', acknowledged_at: '2026-10-19T09:00:00Z' }),
          incident({ id: 3, first_seen_at: '2026-10-18T01:00:00Z', resolved_at: '2026-10-18T05:00:00Z' }),
        ],
      }),
      row('coverage', {
        class: 'gauge',
        effective_status: 'fail',
        incidents: [incident({ id: 4, first_seen_at: '2026-10-10T00:00:00Z' })],
      }),
    ]);
    expect(strip.openIncidents).toEqual({ count: 2, oldest: '2026-10-19T08:00:00+02:00' });
  });

  it('has no oldest when nothing is open', () => {
    const strip = stripCounts([row('jobs', { incidents: [incident({ resolved_at: '2026-10-19T08:00:00Z' })] })]);
    expect(strip.openIncidents).toEqual({ count: 0, oldest: null });
  });

  it('counts Problems and Watch over alarm checks, Not measured over every check', () => {
    const strip = stripCounts([
      row('capacity', { effective_status: 'fail' }),
      row('delisting', { effective_status: 'warn' }),
      row('outbound', { effective_status: 'warn' }),
      // A stale alarm is Not measured, not a Problem: the view judged it.
      row('scraping', { status: 'fail', effective_status: 'unknown' }),
      row('coverage', { class: 'gauge', effective_status: 'fail' }),
      row('text_extraction', { class: 'gauge', effective_status: 'unknown' }),
      row('jobs', { effective_status: 'ok' }),
    ]);
    expect(strip).toMatchObject({ problems: 1, watch: 2, notMeasured: 2 });
  });

  it('reads gauges worse than last week from gauge cells; null while none is comparable', () => {
    const gauge = (check_key: string, worse: Array<boolean | null>, over: Partial<HealthRow> = {}) =>
      row(check_key, {
        class: 'gauge',
        details: { cells: worse.map((w, i) => cell(`${check_key}/${i}`, 'ok', null, { worse_7d: w, trend: [] })) },
        ...over,
      });
    expect(stripCounts([gauge('coverage', [null, null]), gauge('data_quality', [null])]).gaugesWorse).toBeNull();
    expect(stripCounts([gauge('coverage', [null, false])]).gaugesWorse).toBe(0);
    expect(stripCounts([gauge('coverage', [true, false, null]), gauge('data_quality', [true])]).gaugesWorse).toBe(2);
    // A stale gauge's cells are not this week's reading; an alarm carries no trend.
    expect(stripCounts([gauge('coverage', [true], { effective_status: 'unknown' })]).gaugesWorse).toBeNull();
    expect(
      stripCounts([row('jobs', { details: { cells: [cell('gh/x', 'fail', null, { worse_7d: true })] } })]).gaugesWorse,
    ).toBeNull();
  });
});

describe('anchors', () => {
  const SUBJECTS: Array<[string, string]> = [
    ['liveness', 'stale:jobs'],
    ['publishing', 'sreality/photos'],
    ['data_quality', 'floor/remax/?-prodej'],
    ['migration_drift', '604_health stale #2.sql'],
    ['location_town_coverage', 'Střední Čechy/obec'],
  ];

  it('is the bare key for a row, key and encoded subject for a cell', () => {
    expect(anchorId('dedup')).toBe('dedup');
    expect(anchorId('liveness', 'stale:jobs')).toBe('liveness:stale%3Ajobs');
    expect(anchorId('publishing', 'sreality/photos')).toBe('publishing:sreality%2Fphotos');
    expect(anchorId('location_town_coverage', 'Střední Čechy/obec')).toBe(
      'location_town_coverage:St%C5%99edn%C3%AD%20%C4%8Cechy%2Fobec',
    );
  });

  it.each(SUBJECTS)('round-trips %s : %s through a hash', (key, subject) => {
    expect(parseHash(`#${anchorId(key, subject)}`)).toEqual({ key, subject });
  });

  it('splits at the FIRST colon when the subject arrives unencoded', () => {
    expect(parseHash('#liveness:stale:jobs')).toEqual({ key: 'liveness', subject: 'stale:jobs' });
    expect(parseHash('#publishing:sreality/photos')).toEqual({ key: 'publishing', subject: 'sreality/photos' });
    // A browser percent-encodes a typed non-ASCII fragment; the lookup lands on the same id.
    const parsed = parseHash('#location_town_coverage:St%C5%99edn%C3%AD%20%C4%8Cechy/obec');
    expect(parsed).toEqual({ key: 'location_town_coverage', subject: 'Střední Čechy/obec' });
    expect(anchorId(parsed!.key, parsed!.subject)).toBe(anchorId('location_town_coverage', 'Střední Čechy/obec'));
  });

  it('reads a row hash with or without the #, and nothing from an empty one', () => {
    expect(parseHash('#dedup')).toEqual({ key: 'dedup' });
    expect(parseHash('dedup')).toEqual({ key: 'dedup' });
    expect(parseHash('#dedup:')).toEqual({ key: 'dedup' });
    expect(parseHash('')).toBeNull();
    expect(parseHash('#')).toBeNull();
    expect(parseHash('#:orphan')).toBeNull();
  });

  it('drops a subject that does not decode back to its row', () => {
    expect(parseHash('#parsing:bazos%E0%A4%A')).toEqual({ key: 'parsing' });
  });
});

describe('fetchHealthChecks', () => {
  const COLUMNS = [
    'check_key', 'run_at', 'status', 'value', 'details', 'created_at', 'label', 'unit', 'domain',
    'class', 'host', 'cadence_s', 'owner_path', 'sort', 'declared_at', 'last_measured_at',
    'effective_status', 'incidents',
  ];
  const answer = (body: unknown) => {
    const fetch = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) =>
      ({
        ok: true,
        status: 200,
        statusText: 'OK',
        headers: new Headers({ 'Content-Type': 'application/json' }),
        text: async () => JSON.stringify(body),
      }) as unknown as Response,
    );
    vi.stubGlobal('fetch', fetch);
    return fetch;
  };

  afterEach(() => vi.unstubAllGlobals());

  it('reads all 18 columns of the view, ordered by key', async () => {
    const fetch = answer([row('jobs')]);
    await fetchHealthChecks();
    const url = new URL(String(fetch.mock.calls[0][0]));
    expect(url.pathname).toBe('/rest/v1/pipeline_checks_public');
    expect(url.searchParams.get('select')?.split(',')).toEqual(COLUMNS);
    expect(url.searchParams.get('order')).toBe('check_key.asc');
  });

  it('puts every status on the scale: anything else reads Not measured', async () => {
    answer([
      {
        ...row('scraping'),
        effective_status: null,
        incidents: null,
        details: { message: 'm', cells: [{ ...cell('bazos/walks', 'ok', 'bazos'), status: 'skipped', facts: null }] },
      },
      { ...row('legacy'), status: 'warn', effective_status: 'warn', details: { message: 'no cells' } },
    ]);
    const [scraping, legacy] = await fetchHealthChecks();
    expect(scraping.effective_status).toBe('unknown');
    expect(scraping.incidents).toEqual([]);
    expect(scraping.details?.cells?.[0]).toMatchObject({ status: 'unknown', facts: {}, portal: 'bazos' });
    expect(legacy.details).toEqual({ message: 'no cells' });
    expect(legacy.effective_status).toBe('warn');
  });

  it('throws on an empty read: zero rows is never all clear', async () => {
    answer([]);
    const read = fetchHealthChecks();
    await expect(read).rejects.toBeInstanceOf(HealthEmptyError);
    await expect(read).rejects.toThrow(/never 'all clear'/);
  });
});

describe('HEALTH_QUERY', () => {
  it('polls every 5 minutes while visible and again on focus, offline polls included', () => {
    expect(HEALTH_QUERY).toEqual({
      queryKey: ['health-checks'],
      queryFn: fetchHealthChecks,
      refetchInterval: 300_000,
      refetchOnWindowFocus: true,
      staleTime: 60_000,
      // TanStack's default 'online' would pause an offline poll silently; 'always' lets it fail.
      networkMode: 'always',
    });
    // No background polling: TanStack's default pauses the interval while the tab is hidden.
    expect(HEALTH_QUERY).not.toHaveProperty('refetchIntervalInBackground');
    // A useQuery options object as it stands (tsc checks the assignment): the page passes it whole.
    const options: UseQueryOptions<HealthRow[], Error, HealthRow[], readonly ['health-checks']> = HEALTH_QUERY;
    expect(options.queryFn).toBe(fetchHealthChecks);
  });
});

describe('acknowledge', () => {
  beforeEach(() => vi.mocked(apiPost).mockReset());

  it('posts the check key to the admin route and resolves to how many it acknowledged', async () => {
    vi.mocked(apiPost).mockResolvedValue({ key: 'capacity', acknowledged: 2 });
    await expect(acknowledge('capacity')).resolves.toBe(2);
    expect(apiPost).toHaveBeenCalledWith('/admin/health/acknowledge', { key: 'capacity' });
  });
});

describe('ownerTitle', () => {
  it("labels the owner link with the route's own title", () => {
    expect(ownerTitle('/costs')).toBe('Open LLM costs');
    expect(ownerTitle('/scrapers')).toBe('Open Scrapers');
    expect(ownerTitle('/location-quality')).toBe('Open Location quality');
    expect(ownerTitle('/autodedup/progress')).toBe('Open AUTODEDUP · Progress');
    expect(ownerTitle('/settings')).toBe('Open Settings');
  });

  it('falls back to a plain label, never the catch-all title', () => {
    expect(ownerTitle('/no-such-page')).toBe('Open page');
    expect(ownerTitle('')).toBe('Open page');
  });
});
