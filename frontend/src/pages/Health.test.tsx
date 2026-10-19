/* Health: the platform's health on one screen, from ONE read of pipeline_checks_public
 * (docs/design/health-dashboard.md § 5, wave 8).
 *
 * Hermetic: the read is mocked at pgRead (so the real fetchHealthChecks runs, empty-read throw
 * included) and the one write at lib/api apiPost. Date is faked to one instant; React Query and
 * waitFor keep real timers (components/phone/PhonePipeline.test.tsx's idiom) except in the
 * polling test, which fakes setInterval as well.
 *
 * Pins:
 *   * the strip counts: Open incidents = ALARM incidents only (count + oldest); Problems and Watch
 *     = alarm checks by effective status; Not measured = every check; Gauges worse than last week
 *     = gauge cells with worse_7d true, and "not comparable" while no cell carries a boolean; a
 *     tile carries its status mark and word only beside a count that is not zero;
 *   * the matrix sorts worst portal first, an alarm before a gauge on a tie; fleet cells and a
 *     cell-less legacy row never enter it; no current portal cell at all reads Not measured (a
 *     status line with the hollow ring), never a brick alert;
 *   * unknown and stale never render as OK: a stale row reads Not measured with its stored cells
 *     withheld, a stale column reads Not measured for every portal, a stale watcher row too; a row
 *     the view reads Not measured draws neither its stored value nor an OK cell, even one stored
 *     'unknown' over OK cells (the liveness row, migration 633), in its row and in the header;
 *   * the header renders from the liveness row's cells (each non-OK watcher quoted as written),
 *     and a watcher row that measured "Not measured" says so with its cell, never OK;
 *   * a check whose domain names no section shows under "Other checks", never dropped;
 *   * a row lists every non-OK cell without "Show all" (an unknown cell with its own reason); OK
 *     cells wait behind it;
 *   * a failed refetch keeps its data and says "refetch failed at T: <error> — Retry" in a status
 *     region that was there before the failure; a Retry that reads lands focus on the read line;
 *   * a failed first read shows ErrorBanner, an alert, with an English Retry that reads again;
 *   * an empty result is an error, never "Nothing open" or any other all clear;
 *   * Acknowledge posts /admin/health/acknowledge {key}, shows only while an open incident is
 *     unacknowledged, is aria-disabled while pending (a second click posts nothing), and a failure
 *     is said inline, never by the global toast; a success patches the read before the refetch (a
 *     refetch that fails still reads acknowledged, no Acknowledge left), lands focus on the row's
 *     heading, and the status region that was already there says "Acknowledged 1 incident";
 *   * below 768 px OK cells hide and, when every cell behind "Show all" is OK, so does the button,
 *     the "N OK cells … wider screen" note standing in; phone controls are 44 px; the matrix
 *     chevron is ink-3, important over the twisty's ink-4 (class pins: jsdom loads no CSS, the
 *     browser check proves the reflow); a section with nothing open says "Nothing open";
 *   * anchors: #key on every check row, #key:subject on every listed cell; the target opens what
 *     hides it (past the top five, an OK cell on the phone), is scrolled to (optional call) and
 *     focused; #dedup lands on the Engine row, never on its section;
 *   * polling: every 5 min while visible and on focus (observer options), no tick while the tab
 *     is hidden (behaviour, fake setInterval), and an offline poll fails into "refetch failed"
 *     instead of pausing; one page clock moves "read X ago" and every "measured X ago" (fake timers);
 *   * the rendering rules: value + unit counted from cells ("3 cells" on a row that stores 7), a
 *     cell-less row renders its message only, share and lower bound are fractions shown as percent
 *     with one decimal, a per-m² figure only with its basis, messages quoted verbatim, a trend from
 *     2 points and none from 1, "worse than 7 d ago" only where worse_7d is true, a unit's first
 *     plural word singular at 1 ("1 object absent"; "1 USD" untouched), the owner link named from
 *     the route's title, the message toggle named "Show more / less of the message";
 *   * no interactive control is nested inside another (matrix row and cell list expanded);
 *   * no threshold constant in client code (a source census over the page, components/health and
 *     lib/healthChecks), and the page never fetches /workflow-docs.json.
 */
import { readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider, onlineManager } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';

vi.mock('@/lib/pgRead', async (orig) => ({ ...(await orig<typeof import('@/lib/pgRead')>()), pgRead: vi.fn() }));
vi.mock('@/lib/api', async (orig) => ({ ...(await orig<typeof import('@/lib/api')>()), apiPost: vi.fn() }));
vi.mock('@/lib/toast', async (orig) => ({ ...(await orig<typeof import('@/lib/toast')>()), pushToast: vi.fn() }));

import Health from './Health';
import { fmtValue } from '@/components/health/CheckRow';
import { apiPost } from '@/lib/api';
import { fmtMeasuredPricePerM2, fmtTime24 } from '@/lib/format';
import { HEALTH_QUERY, anchorId, type HealthCell, type HealthIncident, type HealthRow, type Status } from '@/lib/healthChecks';
import { createMutationCache } from '@/lib/mutationCache';
import { pgRead } from '@/lib/pgRead';
import { portalShort } from '@/lib/portals';
import { pushToast } from '@/lib/toast';
import { expectNoNestedInteractive } from '@/test/a11y';

const NOW = new Date('2026-10-19T18:00:00Z');
const ago = (min: number) => new Date(NOW.getTime() - min * 60_000).toISOString();

const cell = (subject: string, status: Status, over: Partial<HealthCell> = {}): HealthCell => ({
  subject,
  portal: null,
  status,
  value: null,
  unit: '',
  message: `${subject} reads ${status}`,
  facts: {},
  ...over,
});
const arm = (portal: string, name: string, status: Status, over: Partial<HealthCell> = {}) =>
  cell(`${portal}/${name}`, status, { portal, ...over });

const incident = (over: Partial<HealthIncident> = {}): HealthIncident => ({
  id: 1,
  subject: 'x',
  first_seen_at: ago(320),
  last_seen_at: ago(5),
  resolved_at: null,
  acknowledged_at: null,
  notice_count: 1,
  last_notice_at: ago(320),
  ...over,
});

const row = (check_key: string, over: Partial<HealthRow> = {}): HealthRow => ({
  check_key,
  run_at: ago(4),
  status: 'ok',
  value: 0,
  details: { cells: [], message: '' },
  created_at: ago(4),
  label: check_key,
  unit: 'cells',
  domain: 'platform',
  class: 'alarm',
  host: 'realtime-worker',
  cadence_s: 600,
  owner_path: '',
  sort: 0,
  declared_at: ago(60 * 24),
  last_measured_at: ago(4),
  effective_status: 'ok',
  incidents: [],
  ...over,
});
const verdict = (s: Status) => ({ status: s, effective_status: s });

const LIVENESS = row('liveness', {
  label: 'Watchers', unit: 'min', value: 3, host: 'pg_cron', cadence_s: 300, sort: 999,
  details: {
    message: 'Watchers: all alive',
    cells: [cell('worker', 'ok', { value: 3, unit: 'min', message: 'realtime-worker last beat 3 min ago' }), cell('api', 'ok', { message: 'held' })],
  },
});
const CAPACITY_MESSAGE = '1 Problem — cron/health-liveness: 24 h p95 62 s is 104% of its 60 s statement_timeout. 1 OK.';
const CAPACITY = row('capacity', {
  label: 'Capacity', sort: 2, value: 1, ...verdict('fail'),
  details: {
    message: CAPACITY_MESSAGE,
    cells: [
      cell('cron/health-liveness', 'fail', { value: 62, unit: 's', message: '24 h p95 62 s is 104% of its 60 s statement_timeout' }),
      cell('connections', 'ok', { value: 31, unit: '%' }),
    ],
  },
  incidents: [incident({ id: 150, subject: 'cron/health-liveness', notice_count: 2 })],
});
// Stores 7 though it has 3 cells: a 'cells' row's value is counted from its cells, never read.
const JOBS = row('jobs', {
  label: 'Jobs', sort: 1, owner_path: '/settings', value: 7,
  details: { message: '3 OK.', cells: [cell('gh/bazos', 'ok'), cell('gh/index_walk', 'ok'), cell('cron/refresh', 'ok')] },
});
// Stale: its newest stored result reads Problem, but the view says it is past its staleness budget,
// so that Problem (and its cell, which would list first) is history, never drawn as now.
const DRIFT = row('migration_drift', {
  label: 'Migration drift', unit: 'files', host: 'github-actions', sort: 12, run_at: ago(1800), last_measured_at: ago(1800),
  status: 'fail', effective_status: 'unknown', value: 1,
  details: { message: '1 Problem — 633_health_explicit_cells.sql: 2 objects absent.', cells: [cell('633_health_explicit_cells.sql', 'fail')] },
});
const MAXIMA_REASON = 'Not measured: no walk since the registry declared the check';
const SCRAPING = row('scraping', {
  label: 'Scraping', domain: 'portals', sort: 4, owner_path: '/scrapers', value: 1, ...verdict('fail'),
  details: {
    message: '1 Problem — remax/walks: no walk reached the end for 44 h. 4 OK. 2 Not measured.',
    cells: [
      arm('bazos', 'walks', 'ok'), arm('ceskereality', 'walks', 'ok'), arm('idnes', 'walks', 'ok'),
      arm('maxima', 'walks', 'unknown', { message: MAXIMA_REASON }),
      arm('remax', 'walks', 'fail', { value: 44, unit: 'h', message: 'no walk reached the end for 44 h' }),
      arm('sreality', 'walks', 'ok'), arm('sreality', 'inflow', 'unknown'),
      cell('fleet/dirty', 'fail', { message: 'a fleet cell never enters the matrix' }),
    ],
  },
});
const DELISTING = row('delisting', {
  label: 'Delisting', domain: 'portals', sort: 6, value: 1, ...verdict('warn'),
  details: {
    message: '1 Watch — sreality/precision: 11.9% of delisted ads came back.',
    cells: [
      arm('bazos', 'precision', 'ok'), arm('ceskereality', 'precision', 'ok'), arm('idnes', 'precision', 'ok'),
      arm('maxima', 'precision', 'unknown'), arm('remax', 'precision', 'ok'),
      arm('sreality', 'precision', 'warn', {
        value: 11.9, unit: '%', message: '11.9% of delisted ads came back (Watch over 10%)',
        facts: { share: 0.119, lower_bound: 0.1053 },
      }),
    ],
  },
});
const COVERAGE = row('coverage', {
  label: 'Coverage', domain: 'portals', class: 'gauge', sort: 7, cadence_s: 3600, value: 1, ...verdict('fail'),
  details: {
    message: '1 Problem — ceskereality/residual/byt-prodej: 13,373 active ads, 1,600 in dwell.',
    cells: [
      arm('bazos', 'unnominated', 'ok', { facts: { trend: [['2026-10-18', 1, 'ok'], ['2026-10-19', 0, 'ok']], worse_7d: false } }),
      arm('ceskereality', 'residual/byt-prodej', 'fail', {
        value: 36.9, unit: '%', message: '13,373 active ads, 1,600 in dwell',
        facts: { trend: [['2026-10-17', 30.1, 'fail'], ['2026-10-18', 33.5, 'fail'], ['2026-10-19', 36.9, 'fail']], worse_7d: true },
      }),
      arm('idnes', 'unnominated', 'ok'), arm('maxima', 'unnominated', 'unknown'), arm('remax', 'unnominated', 'ok'),
      arm('sreality', 'unnominated', 'ok', { facts: { trend: [['2026-10-19', 0, 'ok']], worse_7d: null } }),
    ],
  },
  incidents: [incident({ id: 151, subject: 'ceskereality/residual/byt-prodej' })],
});
const DEDUP = row('dedup', {
  label: 'Engine', domain: 'dedup', sort: 8, owner_path: '/autodedup/progress',
  details: { message: '2 OK.', cells: [cell('state', 'ok', { message: 'merging since 3 h ago', facts: { state: 'merging' } }), cell('lease', 'ok')] },
});
const FLOORS = ['bazos', 'idnes', 'maxima', 'mmreality', 'realitymix/byt-prodej', 'realitymix/komercni-pronajem', 'sreality'];
const DATA_QUALITY = row('data_quality', {
  label: 'Data quality', domain: 'data_quality', class: 'gauge', sort: 10, owner_path: '/scrapers', value: 7, ...verdict('fail'),
  details: {
    message: '7 Problem — floor/bazos: ….',
    cells: [
      ...FLOORS.map((f) => cell(`floor/${f}`, 'fail', { value: 12.5, unit: '%', facts: { worse_7d: false } })),
      cell('url/sreality', 'ok', { value: 0, unit: 'ads', facts: { worse_7d: null } }),
    ],
  },
  incidents: [incident({ id: 160, subject: 'floor/realitymix/komercni-pronajem', acknowledged_at: ago(30) })],
});
// Written before wave 6: no `cells` key, a percent under the registry unit `cells`.
const OUTBOUND_MESSAGE = '13.64% of the sampled portal links do not open the right page.';
const OUTBOUND = row('outbound_url_conformance', {
  label: 'Portal links open the right page', domain: 'data_quality', host: 'github-actions', sort: 13, value: 13.64,
  ...verdict('warn'), details: { message: OUTBOUND_MESSAGE, sampled: 22 },
});

const ROWS = [LIVENESS, CAPACITY, JOBS, DRIFT, SCRAPING, DELISTING, COVERAGE, DEDUP, DATA_QUALITY, OUTBOUND];

const read = (rows: HealthRow[]) => vi.mocked(pgRead).mockResolvedValue({ data: rows, count: null });

function renderPage(entry = '/health') {
  const qc = new QueryClient({ mutationCache: createMutationCache(), defaultOptions: { queries: { retry: false } } });
  const view = render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[entry]}>
        <Health />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { qc, ...view };
}

const loaded = () => screen.findByRole('article', { name: 'Capacity' });
const article = (name: string) => screen.getByRole('article', { name });
const header = () => screen.getByRole('heading', { level: 1, name: 'Health' }).closest('header') as HTMLElement;

/* A Stat tile by its label: label span → label row → tile; children: label row, value, hint. */
function tile(label: string): HTMLElement {
  const strip = screen.getByRole('group', { name: 'Summary' });
  const el = within(strip).getAllByText(label).find((e) => !e.classList.contains('sr-only'));
  return el!.parentElement!.parentElement as HTMLElement;
}
const tileValue = (label: string) => tile(label).children[1].textContent;
const tileHint = (label: string) => tile(label).children[2]?.textContent;
const matrix = () => screen.getByRole('table', { name: 'Portals by check, worst portal first' });

let scrolled: ReturnType<typeof vi.fn>;

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['Date'] });
  vi.setSystemTime(NOW);
  scrolled = vi.fn();
  Element.prototype.scrollIntoView = scrolled as unknown as Element['scrollIntoView'];
  read(ROWS);
});

afterEach(() => {
  vi.useRealTimers();
  vi.clearAllMocks();
  delete (Element.prototype as Partial<Element>).scrollIntoView;
});

describe('Health', () => {
  it('counts the strip: alarm incidents, alarm Problems and Watch, every Not measured, worse gauge cells', async () => {
    renderPage();
    await loaded();
    expect(tileValue('Open incidents')).toBe('1');
    expect(tileHint('Open incidents')).toBe(`alarms · oldest since ${fmtTime24(CAPACITY.incidents[0].first_seen_at)}`);
    expect(tileValue('Problems')).toBe('2');
    expect(tileValue('Watch')).toBe('2');
    expect(tileValue('Not measured')).toBe('1');
    expect(tileValue('Gauges worse than last week')).toBe('1');
    expect(tileHint('Gauges worse than last week')).toBe('gauge cells');
    // A count that is not zero carries its mark and its word (sr-only).
    expect(tile('Problems').querySelector('[aria-hidden="true"]')?.getAttribute('class')).toContain('bg-[var(--color-brick)]');
    expect(tile('Problems').textContent).toBe('ProblemProblems2alarm checks');
    expect(tile('Watch').querySelector('[aria-hidden="true"]')?.getAttribute('class')).toContain('bg-[var(--color-ochre)]');
    expect(tile('Not measured').querySelector('[aria-hidden="true"]')?.getAttribute('class')).toContain('border-[var(--color-ink-3)]');
  });

  it('draws no status mark or word beside a zero count: a calm page shows no red', async () => {
    read([JOBS, DEDUP]);
    renderPage();
    await screen.findByRole('article', { name: 'Jobs' });
    for (const label of ['Problems', 'Watch', 'Not measured']) {
      expect(tileValue(label)).toBe('0');
      expect(tile(label).querySelector('[aria-hidden="true"]')).toBeNull();
      expect(tile(label).textContent).toBe(`${label}0${tileHint(label)}`);
    }
  });

  it('says "not comparable" while no gauge cell has a week of trend', async () => {
    const young = (r: HealthRow): HealthRow => ({
      ...r,
      details: { ...r.details, cells: (r.details?.cells ?? []).map((c) => ({ ...c, facts: { ...c.facts, worse_7d: null } })) },
    });
    read([CAPACITY, young(COVERAGE), young(DATA_QUALITY)]);
    renderPage();
    await loaded();
    expect(tileValue('Gauges worse than last week')).toBe('—');
    expect(tileHint('Gauges worse than last week')).toBe('not comparable yet');
  });

  it('sorts the matrix worst portal first, an alarm before a gauge on a tie; fleet and legacy rows stay out', async () => {
    const LEGACY = row('legacy_walks', { label: 'Legacy walks', domain: 'portals', sort: 3, ...verdict('warn'), details: { message: 'a pre-wave-6 result' } });
    read([...ROWS, LEGACY]);
    renderPage();
    await loaded();
    const order = within(matrix()).getAllByRole('rowheader').map((th) => th.textContent);
    expect(order).toEqual(['remax', 'ceskereality', 'sreality', 'maxima', 'bazos', 'idnes'].map(portalShort));
    const heads = within(matrix()).getAllByRole('columnheader').map((th) => th.textContent);
    expect(heads.map((h) => h?.replace(/(gauge)?(OK|Watch|Problem|Not measured)?$/, ''))).toEqual(['Portal', 'Scraping', 'Delisting', 'Coverage']);
    expect(within(matrix()).queryByText(/fleet/)).toBeNull();
    expect(within(article('Legacy walks')).getByText('a pre-wave-6 result')).toBeInTheDocument();
    // The failing cell says its word, value and arm.
    const remax = within(matrix()).getByRole('rowheader', { name: portalShort('remax') }).closest('tr') as HTMLElement;
    expect(within(remax).getByText('Problem')).toBeInTheDocument();
    expect(within(remax).getByText('walks')).toBeInTheDocument();
  });

  it('reads a matrix with no current portal cell as Not measured, never as a brick alert', async () => {
    const blank = (r: HealthRow, details: HealthRow['details']): HealthRow => ({
      ...r, ...verdict('unknown'), value: null, details, incidents: [],
      ...(details == null ? { run_at: null, status: null, last_measured_at: null } : {}),
    });
    read([
      CAPACITY,
      blank(SCRAPING, { cells: [], message: 'Not measured: timed out after 60 s.' }),
      blank(DELISTING, null),
      blank(COVERAGE, { cells: [], message: 'Not measured: the check raised.' }),
    ]);
    renderPage();
    await loaded();
    const portals = screen.getByRole('region', { name: 'Portals' });
    expect(within(portals).queryByRole('table')).toBeNull();
    expect(within(portals).queryByRole('alert')).toBeNull();
    const empty = within(portals).getByRole('status');
    expect(empty).toHaveTextContent(/^Not measured· no portal check has a current portal cell$/);
    expect(empty.querySelector('[aria-hidden="true"]')?.getAttribute('class')).toContain('border-[var(--color-ink-3)]');
    expect(empty.outerHTML).not.toContain('brick');
  });

  it('lists every non-OK cell without "Show all"; only OK cells wait behind it', async () => {
    renderPage();
    await loaded();
    const unknown = document.getElementById(anchorId('scraping', 'maxima/walks')) as HTMLElement;
    expect(within(unknown).getByText(MAXIMA_REASON)).toBeInTheDocument();
    expect(within(unknown).getByText('Not measured')).toBeInTheDocument();
    expect(document.getElementById(anchorId('scraping', 'sreality/inflow'))).not.toBeNull();
    expect(document.getElementById(anchorId('scraping', 'bazos/walks'))).toBeNull();
    expect(within(article('Scraping')).getByRole('button', { name: /Show all 8\scells/ })).toHaveAttribute('aria-expanded', 'false');
  });

  it('never renders Not measured or stale as OK', async () => {
    const PUBLISHING = row('publishing', {
      label: 'Publishing', domain: 'portals', sort: 5, run_at: ago(45), last_measured_at: ago(45), status: 'ok', effective_status: 'unknown',
      details: { message: '3 OK.', cells: [arm('bazos', 'ingest', 'ok'), arm('remax', 'ingest', 'ok'), arm('sreality', 'ingest', 'ok')] },
    });
    const LLM = row('llm', {
      label: 'LLM providers', sort: 3, ...verdict('unknown'), run_at: ago(3), last_measured_at: null,
      details: { cells: [], message: 'Not measured: timed out after 60 s.', skipped: 'timed out' },
    });
    read([...ROWS, PUBLISHING, LLM]);
    renderPage();
    await loaded();

    const drift = article('Migration drift');
    expect(within(drift).getByText('Not measured')).toBeInTheDocument();
    expect(within(drift).queryByText('OK')).toBeNull();
    expect(within(drift).getByText(/its newest result \(Problem\) is past its staleness budget/)).toBeInTheDocument();
    expect(within(drift).queryByText(/objects absent/)).toBeNull();
    expect(document.getElementById(anchorId('migration_drift', '633_health_explicit_cells.sql'))).toBeNull();

    // A stale column: every portal reads Not measured there, never its stored OK.
    const heads = within(matrix()).getAllByRole('columnheader');
    const at = heads.findIndex((th) => th.textContent?.startsWith('Publishing'));
    for (const tr of within(matrix()).getAllByRole('row').slice(1)) {
      if (!within(tr).queryAllByRole('rowheader').length) continue;
      const td = tr.children[at];
      expect(td.textContent).toContain('Not measured');
      expect(td.textContent).not.toContain('OK');
    }
    expect(document.getElementById(anchorId('publishing', 'bazos/ingest'))).toBeNull();

    const llm = article('LLM providers');
    expect(within(llm).getByText('Not measured')).toBeInTheDocument();
    expect(within(llm).getByText('Not measured: timed out after 60 s.')).toBeInTheDocument();
    expect(tileValue('Not measured')).toBe('3');
  });

  it('renders the header from the liveness cells, and a stale watcher row as Not measured', async () => {
    const DOWN = row('liveness', {
      ...LIVENESS, ...verdict('fail'), value: 11,
      details: {
        message: 'realtime-worker last beat 11 min ago',
        cells: [cell('worker', 'fail', { value: 11, unit: 'min', message: 'realtime-worker last beat 11 min ago' }), cell('api', 'ok', { message: 'held' })],
      },
    });
    read([DOWN, CAPACITY]);
    const first = renderPage();
    await loaded();
    expect(within(header()).getAllByText('Problem').length).toBeGreaterThan(0);
    expect(within(header()).getByText('realtime-worker last beat 11 min ago')).toBeInTheDocument();
    expect(within(header()).queryByText('held')).toBeNull();
    first.unmount();

    read([LIVENESS, CAPACITY]);
    const second = renderPage();
    await loaded();
    expect(within(header()).getByText('OK')).toBeInTheDocument();
    expect(within(header()).getByText(/checks measured 4\smin ago/)).toBeInTheDocument();
    second.unmount();

    read([{ ...LIVENESS, run_at: ago(41), last_measured_at: ago(41), effective_status: 'unknown' }, CAPACITY]);
    const third = renderPage();
    await loaded();
    expect(within(header()).getByText('Not measured')).toBeInTheDocument();
    expect(within(header()).queryByText('OK')).toBeNull();
    expect(within(header()).getByText(/past its staleness budget/)).toBeInTheDocument();
    expect(within(header()).queryByText('realtime-worker last beat 3 min ago')).toBeNull();
    third.unmount();

    // Measured now, but one watcher could not judge: Not measured, its cell quoted, never OK.
    const RELEASED = { ...LIVENESS, ...verdict('unknown' as const), last_measured_at: ago(90),
      details: { message: 'the matcher lease was released', cells: [cell('worker', 'ok'), cell('api', 'unknown', { message: 'the matcher lease was released' })] } };
    read([RELEASED, CAPACITY]);
    renderPage();
    await loaded();
    expect(within(header()).getAllByText('Not measured').length).toBeGreaterThan(0);
    expect(within(header()).queryByText('OK')).toBeNull();
    expect(within(header()).getByText('the matcher lease was released')).toBeInTheDocument();
    expect(within(header()).getByText(/checks measured 4\smin ago/)).toBeInTheDocument();
  });

  it('draws no OK cell and no stored value on a row the view reads Not measured, stored unknown or not', async () => {
    // health_liveness() stores 'unknown' over OK cells and keeps its value (migration 633); here
    // that result is also past its staleness budget.
    const STALE = row('liveness', {
      ...LIVENESS, run_at: ago(180), last_measured_at: ago(185), status: 'unknown', effective_status: 'unknown', value: 0.3,
      details: {
        message: 'lease released; a clean API shutdown',
        cells: [
          cell('worker', 'ok', { value: 0.3, unit: 'min', message: 'realtime-worker last beat 0 min ago' }),
          cell('api', 'unknown', { message: 'lease released; a clean API shutdown' }),
        ],
      },
    });
    read([STALE, CAPACITY]);
    renderPage();
    await loaded();
    for (const at of [article('Watchers'), header()]) {
      expect(within(at).getAllByText('Not measured').length).toBeGreaterThan(0);
      expect(within(at).queryByText('OK')).toBeNull();
      expect(within(at).queryByText(/0\.3/)).toBeNull();
      expect(within(at).queryByText('realtime-worker last beat 0 min ago')).toBeNull();
    }
    expect(within(article('Watchers')).queryByRole('button', { name: /Show all/ })).toBeNull();
    expect(document.getElementById(anchorId('liveness', 'worker'))).toBeNull();
  });

  it('shows a check whose domain names no section under "Other checks", never drops it', async () => {
    read([...ROWS, row('walks_v0', { label: 'Old walks', domain: 'scraping', ...verdict('fail'), details: { cells: [cell('x', 'fail')], message: 'x' } })]);
    renderPage();
    await loaded();
    expect(within(screen.getByRole('region', { name: 'Other checks' })).getByRole('article', { name: 'Old walks' })).toBeInTheDocument();
  });

  it('keeps its data when a refetch fails and says why in a live region; a Retry that reads focuses the read line', async () => {
    const { qc } = renderPage();
    await loaded();
    const live = within(header()).getByRole('status');
    expect(live).toBeEmptyDOMElement();
    vi.mocked(pgRead).mockRejectedValueOnce(new Error('HTTP 503'));
    await act(() => qc.refetchQueries({ queryKey: HEALTH_QUERY.queryKey }));
    const failed = await within(header()).findByText(/^refetch failed at \d\d:\d\d: HTTP 503 —/);
    expect(failed).toBe(live);
    expect(failed).not.toHaveAttribute('title');
    expect(within(failed).getByRole('button', { name: 'Retry' }).className).toContain('max-md:min-h-11');
    expect(article('Capacity')).toBeInTheDocument();
    expect(screen.queryByText('Failed to load:')).toBeNull();

    fireEvent.click(within(failed).getByRole('button', { name: 'Retry' }));
    const readLine = within(header()).getByText(/^read /);
    await waitFor(() => expect(document.activeElement).toBe(readLine));
    expect(live).toBeEmptyDOMElement();
  });

  it('fails an offline poll into "refetch failed" instead of pausing it', async () => {
    const { qc } = renderPage();
    await loaded();
    onlineManager.setOnline(false);
    try {
      vi.mocked(pgRead).mockRejectedValueOnce(new TypeError('Failed to fetch'));
      await act(() => qc.refetchQueries({ queryKey: HEALTH_QUERY.queryKey }));
      expect(await within(header()).findByText(/^refetch failed at \d\d:\d\d: Failed to fetch/)).toBeInTheDocument();
    } finally {
      act(() => onlineManager.setOnline(true));
    }
  });

  it('shows ErrorBanner with Retry when the first read fails, and Retry reads again', async () => {
    vi.mocked(pgRead).mockRejectedValueOnce(new Error('HTTP 500'));
    renderPage();
    expect(await screen.findByText('Failed to load:')).toBeInTheDocument();
    expect(screen.getByText(/HTTP 500/)).toBeInTheDocument();
    expect(screen.getByRole('alert')).toHaveTextContent(/^Failed to load: HTTP 500/);
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    expect(await loaded()).toBeInTheDocument();
  });

  it('reads an empty result as an error, never as all clear', async () => {
    read([]);
    renderPage();
    expect(await screen.findByText('Empty read:')).toBeInTheDocument();
    expect(screen.getByText(/never 'all clear'/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Retry' })).toBeInTheDocument();
    expect(screen.queryByText(/Nothing open/)).toBeNull();
    expect(screen.queryByRole('group', { name: 'Summary' })).toBeNull();
  });

  it('posts Acknowledge to the admin route, only while an open incident is unacknowledged', async () => {
    vi.mocked(apiPost).mockResolvedValue({ key: 'capacity', acknowledged: 1 });
    renderPage();
    await loaded();
    expect(within(article('Data quality')).queryByRole('button', { name: 'Acknowledge' })).toBeNull();
    expect(within(article('Data quality')).getByText(/acknowledged \d\d:\d\d/)).toBeInTheDocument();
    expect(within(article('Engine')).queryByRole('button', { name: 'Acknowledge' })).toBeNull();
    fireEvent.click(within(article('Capacity')).getByRole('button', { name: 'Acknowledge' }));
    await waitFor(() => expect(apiPost).toHaveBeenCalledWith('/admin/health/acknowledge', { key: 'capacity' }));
    await waitFor(() => expect(pgRead).toHaveBeenCalledTimes(2));
  });

  it('acknowledges into the read before refetching: focus lands on the heading and the result is announced', async () => {
    let answer: (value: { key: string; acknowledged: number }) => void = () => undefined;
    vi.mocked(apiPost).mockImplementation(() => new Promise((resolve) => { answer = resolve as typeof answer; }));
    renderPage();
    await loaded();
    const said = within(article('Capacity')).getByRole('status');
    expect(said).toBeEmptyDOMElement();

    fireEvent.click(within(article('Capacity')).getByRole('button', { name: 'Acknowledge' }));
    const pending = await within(article('Capacity')).findByRole('button', { name: 'Acknowledging…' });
    expect(pending).toHaveAttribute('aria-disabled', 'true');
    expect(pending).not.toBeDisabled();
    expect(pending.className).toContain('max-md:h-11');
    fireEvent.click(pending);
    await waitFor(() => expect(apiPost).toHaveBeenCalledTimes(1));

    // The follow-up read fails: the row still reads acknowledged, from the patched cache, and the
    // patch is not a read: three minutes on, the header still dates the read to the one that succeeded.
    vi.mocked(pgRead).mockRejectedValueOnce(new Error('HTTP 504'));
    vi.setSystemTime(new Date(NOW.getTime() + 3 * 60_000));
    await act(async () => answer({ key: 'capacity', acknowledged: 1 }));
    await waitFor(() => expect(said).toHaveTextContent('Acknowledged 1 incident'));
    await within(header()).findByText(/^refetch failed at \d\d:\d\d: HTTP 504/);
    expect(within(header()).getByText((_, el) => el?.tagName === 'P' && /^read 3\smin ago/.test(el.textContent ?? ''))).toBeInTheDocument();
    expect(pgRead).toHaveBeenCalledTimes(2);
    expect(apiPost).toHaveBeenCalledTimes(1);
    const capacity = article('Capacity');
    expect(within(capacity).queryByRole('button', { name: /^Acknowledg/ })).toBeNull();
    expect(within(capacity).getByText(/acknowledged \d\d:\d\d/)).toBeInTheDocument();
    expect(within(capacity).queryByText(/not acknowledged/)).toBeNull();
    expect(within(capacity).getByRole('status')).toBe(said);
    expect(document.activeElement).toBe(within(capacity).getByRole('heading', { name: 'Capacity' }));
  });

  it('says a failed Acknowledge inline, never with the global toast', async () => {
    vi.mocked(apiPost).mockRejectedValue(new Error('HTTP 403'));
    renderPage();
    await loaded();
    fireEvent.click(within(article('Capacity')).getByRole('button', { name: 'Acknowledge' }));
    expect(await within(article('Capacity')).findByRole('alert')).toHaveTextContent('Acknowledge failed: HTTP 403');
    expect(pushToast).not.toHaveBeenCalled();
  });

  it('hides OK cells below 768 px (class pins) and says "Nothing open" only where nothing is', async () => {
    renderPage();
    await loaded();
    const bazos = within(matrix()).getByRole('rowheader', { name: portalShort('bazos') }).closest('tr') as HTMLElement;
    expect(bazos.className).toContain('hidden md:table-row');
    for (const td of Array.from(bazos.querySelectorAll('td'))) expect(td.className).toContain('hidden md:table-cell');
    const remax = within(matrix()).getByRole('rowheader', { name: portalShort('remax') }).closest('tr') as HTMLElement;
    expect(remax.className).toContain('block md:table-row');
    const tds = Array.from(remax.querySelectorAll('td'));
    expect(tds[0].className).toContain('block md:table-cell');
    expect(tds[1].className).toContain('hidden md:table-cell');
    // Not measured is not OK: maxima's hollow cells stay on the phone.
    const maxima = within(matrix()).getByRole('rowheader', { name: portalShort('maxima') }).closest('tr') as HTMLElement;
    expect(maxima.querySelector('td')?.className).toContain('block md:table-cell');

    // Only OK cells behind "Show all": the phone gets no button that would show nothing, only the note.
    const jobs = article('Jobs');
    const showAll = within(jobs).getByRole('button', { name: /Show all 3\scells/ });
    expect(showAll.className).toContain('hidden md:inline-flex');
    expect(within(jobs).getByText(/^3\sOK cells are listed on a wider screen\.$/).className).toContain('md:hidden');
    fireEvent.click(showAll);
    expect(document.getElementById(anchorId('jobs', 'gh/bazos'))?.className).toContain('hidden md:flex');
    // A Problem behind it too (7 Problems, five listed): the button stays, 44 px on the phone.
    const dataQuality = within(article('Data quality')).getByRole('button', { name: /Show all 8\scells/ });
    expect(dataQuality.className).not.toContain('hidden');
    expect(dataQuality.className).toContain('max-md:inline-flex max-md:min-h-11 max-md:items-center');
    expect(within(article('Data quality')).queryByText(/listed on a wider screen/)).toBeNull();

    expect(within(screen.getByRole('region', { name: 'Engine' })).getByText(/Nothing open/)).toBeInTheDocument();
    expect(within(screen.getByRole('region', { name: 'Portals' })).queryByText(/Nothing open/)).toBeNull();
    expect(within(screen.getByRole('region', { name: 'Platform' })).queryByText(/Nothing open/)).toBeNull();
  });

  it('carries #key on every row and lands a #key link on the row', async () => {
    renderPage('/health#capacity');
    await loaded();
    for (const r of ROWS) expect(document.getElementById(anchorId(r.check_key))?.tagName).toBe('ARTICLE');
    await waitFor(() => expect(scrolled).toHaveBeenCalledTimes(1));
    expect(scrolled.mock.contexts[0]).toBe(article('Capacity'));
    expect(document.activeElement).toBe(article('Capacity'));
  });

  it('opens a #key:subject target past the top five, and scrolls to that cell', async () => {
    const subject = 'floor/realitymix/komercni-pronajem';
    renderPage(`/health#${anchorId('data_quality', subject)}`);
    await loaded();
    const el = document.getElementById(anchorId('data_quality', subject));
    expect(el).not.toBeNull();
    expect(el?.className).toContain('border-[var(--color-copper)]');
    expect(within(article('Data quality')).getByRole('button', { name: 'Show fewer' })).toHaveAttribute('aria-expanded', 'true');
    await waitFor(() => expect(scrolled).toHaveBeenCalledTimes(1));
    expect(scrolled.mock.contexts[0]).toBe(el);
    expect(document.activeElement).toBe(el);
  });

  it('un-hides a targeted OK cell on the phone, and lands #dedup on the Engine row', async () => {
    const first = renderPage(`/health#${anchorId('jobs', 'gh/bazos')}`);
    await loaded();
    const cellLine = document.getElementById(anchorId('jobs', 'gh/bazos'));
    expect(cellLine?.className).not.toContain('hidden');
    await waitFor(() => expect(scrolled).toHaveBeenCalledTimes(1));
    expect(scrolled.mock.contexts[0]).toBe(cellLine);
    expect(document.activeElement).toBe(cellLine);
    first.unmount();

    renderPage('/health#dedup');
    await loaded();
    await waitFor(() => expect(scrolled).toHaveBeenCalledTimes(2));
    expect(scrolled.mock.contexts[1]).toBe(article('Engine'));
    expect(document.activeElement).toBe(article('Engine'));
    expect(document.getElementById('section-dedup')?.tagName).toBe('SECTION');
  });

  it('polls every 5 minutes while visible and on focus', async () => {
    const { qc } = renderPage();
    await loaded();
    const options = qc.getQueryCache().find({ queryKey: HEALTH_QUERY.queryKey })?.observers[0]?.options;
    expect(options?.refetchInterval).toBe(300_000);
    expect(options?.refetchOnWindowFocus).toBe(true);
    expect(options?.refetchIntervalInBackground).toBeFalsy();
  });

  it('does not poll while the tab is hidden', async () => {
    vi.useRealTimers();
    vi.useFakeTimers({ toFake: ['Date', 'setInterval', 'clearInterval'], now: NOW });
    const visibility = (value: DocumentVisibilityState) =>
      Object.defineProperty(document, 'visibilityState', { configurable: true, value });
    try {
      renderPage();
      await loaded();
      expect(pgRead).toHaveBeenCalledTimes(1);
      await act(() => vi.advanceTimersByTimeAsync(300_000));
      expect(pgRead).toHaveBeenCalledTimes(2);
      visibility('hidden');
      await act(() => vi.advanceTimersByTimeAsync(900_000));
      expect(pgRead).toHaveBeenCalledTimes(2);
    } finally {
      visibility('visible');
    }
  });

  it('moves "read X ago" and every "measured X ago" on one page clock between polls', async () => {
    vi.useRealTimers();
    vi.useFakeTimers({ toFake: ['Date', 'setInterval', 'clearInterval'], now: NOW });
    renderPage();
    await loaded();
    const readLine = within(header()).getByText(/^read /);
    expect(readLine).toHaveTextContent(/^read 0\ss ago/);
    expect(within(article('Capacity')).getByText(/^measured 4\smin ago$/)).toBeInTheDocument();
    await act(() => vi.advanceTimersByTimeAsync(60_000));
    expect(readLine).toHaveTextContent(/^read 1\smin ago/);
    expect(within(article('Capacity')).getByText(/^measured 5\smin ago$/)).toBeInTheDocument();
    expect(pgRead).toHaveBeenCalledTimes(1);
  });

  it('singularises the first plural word of a unit at 1, and leaves a unit with none alone', () => {
    expect(fmtValue(1, 'objects absent')).toBe('1\u00a0object absent');
    expect(fmtValue(1, 'failed runs')).toBe('1\u00a0failed run');
    expect(fmtValue(1, 'decisions/h')).toBe('1\u00a0decision/h');
    expect(fmtValue(2, 'objects absent')).toBe('2\u00a0objects absent');
    for (const unit of ['USD', 'min', 'h', 's', 'pp']) expect(fmtValue(1, unit)).toBe(`1\u00a0${unit}`);
  });

  it('names the message toggle by what it opens and keeps its visible word', async () => {
    // jsdom lays nothing out: make every clamp overflow so the toggle renders.
    const tall = vi.spyOn(Element.prototype, 'scrollHeight', 'get').mockReturnValue(80);
    const clamped = vi.spyOn(Element.prototype, 'clientHeight', 'get').mockReturnValue(40);
    try {
      renderPage();
      await loaded();
      const toggle = within(article('Capacity')).getByRole('button', { name: 'Show more of the message' });
      expect(toggle).toHaveTextContent(/^more$/);
      expect(toggle.className).toContain('max-md:min-h-11');
      fireEvent.click(toggle);
      expect(toggle).toHaveAccessibleName('Show less of the message');
      expect(toggle).toHaveTextContent(/^less$/);
      expect(toggle).toHaveAttribute('aria-expanded', 'true');
    } finally {
      tall.mockRestore();
      clamped.mockRestore();
    }
  });

  it('follows the rendering rules', async () => {
    const MEDIANS = row('parsing', {
      label: 'Parsing', domain: 'portals', sort: 9, owner_path: '/scrapers', value: 2, ...verdict('warn'),
      details: {
        message: '2 Watch — sreality/median/byt-prodej: the median moved 1.4×.',
        cells: [
          arm('sreality', 'median/byt-prodej', 'warn', { value: 1.4, unit: '×', facts: { median: 91535.4, price_per_m2_basis: 'sale_capital_czk_m2' } }),
          arm('idnes', 'median/byt-prodej', 'warn', { value: 1.4, unit: '×', facts: { median: 88000, price_per_m2_basis: null } }),
        ],
      },
    });
    read([...ROWS, MEDIANS]);
    renderPage();
    await loaded();

    // Testing Library folds the no-break space between a figure and its unit into a space.
    expect(within(article('Data quality')).getByText('7 cells')).toBeInTheDocument();
    // Jobs stores 7 and has 3 cells: the figure is counted, never the stored value.
    expect(within(article('Jobs')).getByText('3 cells')).toBeInTheDocument();
    expect(within(article('Jobs')).queryByText('7 cells')).toBeNull();
    expect(within(article('Capacity')).getByText('1 cell')).toBeInTheDocument();
    expect(within(article('Capacity')).getByText(CAPACITY_MESSAGE)).toBeInTheDocument();
    expect(within(article('Capacity')).getByText('62 s')).toBeInTheDocument();

    const outbound = article('Portal links open the right page');
    expect(within(outbound).getByText(OUTBOUND_MESSAGE)).toBeInTheDocument();
    expect(within(outbound).queryByText(/13\.64\s?cells/)).toBeNull();
    expect(within(outbound).queryByRole('button', { name: /Show all/ })).toBeNull();

    const precision = document.getElementById(anchorId('delisting', 'sreality/precision')) as HTMLElement;
    expect(within(precision).getByText('share 11.9%')).toBeInTheDocument();
    expect(within(precision).getByText('lower bound 10.5%')).toBeInTheDocument();
    expect(within(precision).getByText('11.9%')).toBeInTheDocument();

    const sale = document.getElementById(anchorId('parsing', 'sreality/median/byt-prodej')) as HTMLElement;
    const priced = `median ${fmtMeasuredPricePerM2(91535.4, 'sale')}`.replace(/\s+/g, ' ');
    expect(within(sale).getByText(priced)).toBeInTheDocument();
    const unlabelled = document.getElementById(anchorId('parsing', 'idnes/median/byt-prodej')) as HTMLElement;
    expect(within(unlabelled).queryByText(/^median /)).toBeNull();

    const residual = document.getElementById(anchorId('coverage', 'ceskereality/residual/byt-prodej')) as HTMLElement;
    expect(within(residual).getByRole('img', { name: /^Trend, 3 measured days/ })).toBeInTheDocument();
    expect(within(residual).getByText('worse than 7 d ago')).toBeInTheDocument();
    const floor = document.getElementById(anchorId('data_quality', 'floor/bazos')) as HTMLElement;
    expect(within(floor).queryByText(/worse than/)).toBeNull();
    fireEvent.click(within(article('Coverage')).getByRole('button', { name: /Show all/ }));
    const pair = document.getElementById(anchorId('coverage', 'bazos/unnominated')) as HTMLElement;
    expect(within(pair).getByRole('img', { name: /^Trend, 2 measured days/ })).toBeInTheDocument();
    expect(within(pair).queryByText(/worse than/)).toBeNull();
    const single = document.getElementById(anchorId('coverage', 'sreality/unnominated')) as HTMLElement;
    expect(within(single).queryByRole('img')).toBeNull();
    expect(within(single).queryByText(/worse than/)).toBeNull();

    expect(within(article('Engine')).getByText('merging')).toBeInTheDocument();
    expect(within(article('Scraping')).getByRole('link', { name: 'Open Scrapers' })).toHaveAttribute('href', '/scrapers');
    expect(within(article('Engine')).getByRole('link', { name: 'Open AUTODEDUP · Progress' })).toHaveAttribute('href', '/autodedup/progress');
    expect(within(article('Capacity')).queryByRole('link')).toBeNull();
  });

  it('nests no interactive control inside another', async () => {
    const { container } = renderPage();
    await loaded();
    const head = within(matrix()).getByRole('button', { name: portalShort('remax') });
    // The head's only cue that it opens: ink-3, important over the twisty's own ink-4.
    expect(head.querySelector('svg')?.getAttribute('class')).toContain('text-[var(--color-ink-3)]!');
    expect(head.className).toContain('max-md:min-h-11');
    fireEvent.click(head);
    expect(within(matrix()).getByRole('link', { name: 'walks' })).toHaveAttribute('href', `/health#${anchorId('scraping', 'remax/walks')}`);
    fireEvent.click(within(article('Jobs')).getByRole('button', { name: /Show all/ }));
    expectNoNestedInteractive(container);
  });

  it('never fetches /workflow-docs.json', async () => {
    const fetchSpy = vi.spyOn(globalThis, 'fetch');
    renderPage();
    await loaded();
    expect(fetchSpy.mock.calls.filter(([url]) => String(url).includes('workflow-docs'))).toEqual([]);
    fetchSpy.mockRestore();
    for (const file of SCANNED) expect(source(file)).not.toMatch(/workflow-?[dD]ocs/);
  });
});

/* The browser holds no threshold (design § 1, corollary 4): statuses come from the view and the
 * checks. A threshold hides two ways, both caught here: a comparison against a number
 * (`share > 0.1`, `ageH >= 36`; only 0 is allowed, for "any") and a named number
 * (`const STALE_H = 36`; only the display constants below are allowed, each with its reason). Its
 * blind spots: a number in an object property (HEALTH_QUERY's polling cadence lives there, and is
 * not a threshold) and a comparison against a variable that holds a literal. */
const SCANNED = ['pages/Health.tsx', 'components/health/CheckRow.tsx', 'components/health/PortalMatrix.tsx', 'lib/healthChecks.ts'];
const ALLOWED_NUMBERS: Record<string, string> = {
  TOP_CELLS: 'how many non-OK cells a row lists before "Show all" (design § 5: top five, then all)',
  MIN_TREND_POINTS: 'a line needs two points',
  DAY_MS: 'a calendar day, to space trend points by date',
  TICK_MS: 'how often the page re-renders its "X ago" stamps, a display cadence',
};
const source = (rel: string) =>
  readFileSync(join(process.cwd(), 'src', rel), 'utf8')
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/(^|[^:'"`])\/\/.*$/gm, '$1');
const NUMBER = String.raw`(?:\d[\d_]*(?:\.\d+)?|\.\d+)(?:e-?\d+)?`;
const COMPARED = new RegExp(String.raw`(?:(?<![=>-])(?:<=?|>=?)\s*-?(${NUMBER})(?![\w.])|(?<![\w.])(${NUMBER})\s*(?:<=?|>=?)(?![=>]))`, 'g');
const NAMED = new RegExp(String.raw`\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*(?::[^=;]+)?=\s*-?(${NUMBER})\s*[;,\n]`, 'g');
const zero = (n: string) => Number(n.replace(/_/g, '')) === 0;

describe('no threshold in client code', () => {
  it('compares no measurement against a number and names no number but the display constants', () => {
    const offenders: string[] = [];
    for (const file of SCANNED) {
      const text = source(file);
      for (const m of text.matchAll(COMPARED)) if (!zero(m[1] ?? m[2])) offenders.push(`${file}: ${m[0].trim()}`);
      for (const m of text.matchAll(NAMED)) if (!zero(m[2]) && !(m[1] in ALLOWED_NUMBERS)) offenders.push(`${file}: ${m[0].trim()}`);
    }
    expect(offenders).toEqual([]);
  });

  it('sees the files and every allowed constant (so a silent path miss or a stale allowlist cannot pass)', () => {
    const health = readdirSync(join(process.cwd(), 'src/components/health')).filter((f) => !/\.test\.tsx?$/.test(f));
    expect(health.map((f) => `components/health/${f}`).sort()).toEqual(SCANNED.filter((f) => f.startsWith('components/')).sort());
    const named = SCANNED.flatMap((f) => [...source(f).matchAll(NAMED)].map((m) => m[1]));
    for (const name of Object.keys(ALLOWED_NUMBERS)) expect(named).toContain(name);
    expect('share > 0.1'.match(COMPARED)).not.toBeNull();
    expect('const STALE_H = 36;'.match(NAMED)).not.toBeNull();
    expect('(a) => 5'.match(COMPARED)).toBeNull();
  });
});
