/* LocationQuality — the gauge's stored cells, and the interactive-semantics rail.
 *
 * The scorecard is the `location_town_coverage` row of the Health page's own read (W1, ruling Q7:
 * nothing measured twice), so the read is mocked where Health's is (`pgRead`) and the page must
 * share Health's cache entry. The rail pins the ACCESSIBLE NAME of the page's source scope, and
 * that no control sits inside another. W3b deleted the inspector: the page calls no `/location/*`.
 */

import { describe, expect, it, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';

vi.mock('@/lib/pgRead', async (orig) => ({ ...(await orig<typeof import('@/lib/pgRead')>()), pgRead: vi.fn() }));
vi.mock('@/lib/api', async (orig) => ({
  ...(await orig<typeof import('@/lib/api')>()), apiGet: vi.fn(), apiPost: vi.fn(),
}));

import LocationQuality from './LocationQuality';
import * as lq from '../lib/locationQuality';
import { apiGet, apiPost } from '@/lib/api';
import { HEALTH_QUERY, type HealthCell, type HealthRow, type Status } from '@/lib/healthChecks';
import { pgRead } from '@/lib/pgRead';
import { expectNoNestedInteractive } from '@/test/a11y';

const at = new Date(Date.now() - 30 * 60_000).toISOString();
const cell = (subject: string, portal: string | null, status: Status, over: Partial<HealthCell> = {}): HealthCell => ({
  subject, portal, status, value: 0, unit: 'ads', message: `${subject} reads ${status}`, facts: {}, ...over,
});

/* The gauge's shape: per portal its arms in the gauge's order, then the fleet cells. */
const CELLS: HealthCell[] = [
  cell('bazos/town', 'bazos', 'ok'),
  cell('bazos/outside', 'bazos', 'fail', {
    value: 12263, facts: { placed: 90000, disputed: { pin_outside_obec: 12263, pin_off_street: 4 } },
  }),
  cell('bazos/street', 'bazos', 'ok', { value: 61.5, unit: '%', facts: { grains: { address: 100, street: 23 } } }),
  cell('sreality/town', 'sreality', 'ok'),
  cell('sreality/outside', 'sreality', 'ok'),
  cell('sreality/street', 'sreality', 'warn', {
    value: 48.25, unit: '%', facts: { grains: { address: 4000, street: 825, quarter: 7, obec: 300 } },
  }),
  cell('served/town', null, 'ok'),
  cell('registry', null, 'ok', { value: 5, unit: 'd' }),
];

const stored = (over: Partial<HealthRow> = {}): HealthRow => ({
  check_key: 'location_town_coverage', run_at: at, status: 'fail', value: 1,
  details: { cells: CELLS, message: '1 Problem' }, created_at: at, label: 'Location quality',
  unit: 'cells', domain: 'data_quality', class: 'gauge', host: 'realtime-worker', cadence_s: 21600,
  owner_path: '/location-quality', sort: 0, declared_at: at, last_measured_at: at,
  effective_status: 'fail', incidents: [], ...over,
});

function renderPage(qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })) {
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <LocationQuality />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return qc;
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(pgRead).mockResolvedValue({ data: [stored()], count: null });
});

describe('LocationQuality scorecard', () => {
  it('renders the matrix from the stored row: portals as rows, arms in the gauge order', async () => {
    renderPage();
    const bazos = await screen.findByTestId('location-matrix-bazos');
    const heads = within(screen.getByRole('table')).getAllByRole('columnheader').map((h) => h.textContent);
    expect(heads).toEqual(['Portal', 'town', 'outside', 'street']);
    expect(bazos).toHaveTextContent(/Problem\s*12,263\s*ads/);
    /* Health's own verdict cell: an OK arm is a quiet dot, its line the cell's title. */
    expect(within(bazos).getByTitle('bazos/street reads ok')).not.toHaveTextContent('61.5%');
    expect(screen.getByTestId('location-matrix-sreality')).toHaveTextContent(/Watch\s*48.3%/);
    const fleet = screen.getByTestId('location-fleet');
    expect(fleet).toHaveTextContent('served/town');
    expect(fleet).toHaveTextContent(/registry\s*OK\s*5\s*d/);
    /* The header and Health's own row say the same thing. */
    expect(screen.getAllByText('measured 30 min ago')).toHaveLength(2);
    /* The same row, drawn by Health's own component, its self-link hidden. */
    expect(screen.getByRole('heading', { level: 3, name: 'Location quality' })).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: /Open/ })).toBeNull();
  });

  it('a matrix row picks the source; its grains and disputed classes are the stored facts', async () => {
    renderPage();
    fireEvent.click(await screen.findByRole('button', { name: 'Bazoš' }));
    expect(screen.getByRole('combobox', { name: 'Source' })).toHaveValue('bazos');
    expect(screen.getByRole('button', { name: 'Bazoš' })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByText('Grain · Bazoš')).toBeInTheDocument();
    expect(screen.getByText('pin_outside_obec')).toBeInTheDocument();
    /* The matrix's figures, not a second number format. */
    expect(screen.getByText('12,263')).toBeInTheDocument();
  });

  it('reads a row the view reads Not measured as Not measured, never its stored verdicts', async () => {
    vi.mocked(pgRead).mockResolvedValue({ data: [stored({ effective_status: 'unknown' })], count: null });
    renderPage();
    const bazos = await screen.findByTestId('location-matrix-bazos');
    expect(bazos).not.toHaveTextContent('Problem');
    expect(bazos).not.toHaveTextContent('12,263');
    expect(within(bazos).getAllByText('Not measured')).toHaveLength(3);
  });

  it('never calls /location/quality/*: the two aggregate reads are gone', async () => {
    renderPage();
    await screen.findByTestId('location-matrix-bazos');
    expect(vi.mocked(apiGet).mock.calls.filter(([path]) => String(path).includes('/location/quality'))).toEqual([]);
    expect('fetchSourceOverview' in lq).toBe(false);
    expect('fetchCorpusSummary' in lq).toBe(false);
  });

  it('has no inspector and calls no /location/listing: the address picker replaced it (W3b)', async () => {
    renderPage();
    await screen.findByTestId('location-matrix-bazos');
    expect(screen.queryByRole('textbox')).toBeNull();
    expect(screen.queryByRole('button', { name: 'Inspect' })).toBeNull();
    const paths = [...vi.mocked(apiGet).mock.calls, ...vi.mocked(apiPost).mock.calls].map(([path]) => String(path));
    expect(paths.filter((path) => path.startsWith('/location/'))).toEqual([]);
    expect('fetchInspector' in lq || 'submitCorrection' in lq).toBe(false);
  });

  it("uses the HEALTH_QUERY key: Health's cache entry is the page's", async () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    qc.setQueryData(HEALTH_QUERY.queryKey, [stored()]);
    renderPage(qc);
    expect(await screen.findByTestId('location-matrix-bazos')).toHaveTextContent(/12,263/);
    expect(pgRead).not.toHaveBeenCalled();
    /* The page's one read. */
    expect(qc.getQueryCache().getAll().map((q) => q.queryKey)).toEqual([HEALTH_QUERY.queryKey]);
  });

  it('says an empty read is an empty read, never all clear', async () => {
    vi.mocked(pgRead).mockResolvedValue({ data: [], count: null });
    renderPage();
    expect(await screen.findByRole('alert')).toHaveTextContent(/Empty read:/);
    expect(screen.queryByTestId('location-matrix-bazos')).toBeNull();
  });
});

describe('LocationQuality accessible names', () => {
  it('names the page scope select after its visible caption', async () => {
    renderPage();
    expect(screen.getByRole('combobox', { name: 'Source' })).toHaveValue('bezrealitky');
  });

  it('puts no control inside another', async () => {
    renderPage();
    await screen.findByTestId('location-matrix-bazos');
    expectNoNestedInteractive(document.body);
  });
});
