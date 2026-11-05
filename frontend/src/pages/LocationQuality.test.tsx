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
import { MemoryRouter, useLocation } from 'react-router-dom';

vi.mock('@/lib/pgRead', async (orig) => ({ ...(await orig<typeof import('@/lib/pgRead')>()), pgRead: vi.fn() }));
vi.mock('@/lib/api', async (orig) => ({
  ...(await orig<typeof import('@/lib/api')>()), apiGet: vi.fn(), apiPost: vi.fn(),
}));

import LocationQuality from './LocationQuality';
import * as lq from '../lib/locationQuality';
import { apiGet, apiPost } from '@/lib/api';
import { HEALTH_QUERY, type HealthCell, type HealthRow, type Status } from '@/lib/healthChecks';
import { pgRead } from '@/lib/pgRead';
import { propertyPath } from '@/lib/listingUrl';
import { expectNoNestedInteractive } from '@/test/a11y';

const at = new Date(Date.now() - 30 * 60_000).toISOString();
const cell = (subject: string, portal: string | null, status: Status, over: Partial<HealthCell> = {}): HealthCell => ({
  subject, portal, status, value: 0, unit: 'ads', message: `${subject} reads ${status}`, facts: {}, ...over,
});

/* The gauge's shape: per portal its arms in the gauge's order, then the fleet cells. */
/* The town cell's stored position grid (W6-c): {kind: {grain: n}}, its sum the cell's ads. */
const POSITIONS = {
  door: { address: 40 },
  own_pin: { street: 30, quarter: 20 },
  town_point: { obec: 6 },
  none: { unknown: 1 },
  unclassified: { street: 3 },
};
const CELLS: HealthCell[] = [
  cell('bazos/town', 'bazos', 'ok', { facts: { ads: 100, positions: POSITIONS } }),
  cell('bazos/outside', 'bazos', 'fail', {
    value: 12263, facts: { placed: 90000, disputed: { pin_outside_obec: 12263, pin_off_street: 4 } },
  }),
  cell('bazos/street', 'bazos', 'ok', { value: 61.5, unit: '%', facts: { grains: { address: 100, street: 23 } } }),
  cell('sreality/town', 'sreality', 'ok', { facts: { ads: 9, positions: { door: { address: 7 }, own_pin: { obec: 2 } } } }),
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

/* Where the page put the cell: the URL is the list's one state. */
function Search() {
  return <output data-testid="search">{useLocation().search}</output>;
}

function renderPage(qc = new QueryClient({ defaultOptions: { queries: { retry: false } } }), at = '/location-quality') {
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[at]}>
        <LocationQuality />
        <Search />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return qc;
}

/* The list's rows (location_position_audit), answered where Health's row is, by the relation the read names. */
const LIST_ROW = {
  listing_id: 501, property_id: 77, source: 'bazos', is_active: false, first_seen_at: '2026-09-01T10:00:00Z',
  last_seen_at: '2026-10-02T10:00:00Z', category_main: 'byt', category_type: 'prodej', disposition: '2+kk',
  area_m2: 54, price_czk: 5_000_000, source_url: 'https://www.bazos.cz/inzerat/1', display_label: null,
  granularity: 'unknown', granularity_rank: 0, grain: 'unknown', position_kind: 'none', country_status: 'undetermined',
  resolved_at: '2026-10-02T10:00:00Z', town_evidence: true, sibling_placed: true, queued: false,
  refreshed_at: '2026-11-05T22:07:00Z',
};
const listUrls = () => vi.mocked(pgRead).mock.calls
  .map(([q]) => decodeURIComponent(String((q as unknown as { url?: URL }).url ?? '')))
  .filter((u) => u.includes('location_position_audit'));

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(pgRead).mockImplementation(async (q: unknown) =>
    String((q as { url?: URL }).url ?? '').includes('location_position_audit')
      ? { data: [LIST_ROW], count: 4066 }
      : { data: [stored()], count: null });
});

describe('LocationQuality scorecard', () => {
  it('renders the matrix from the stored row: portals as rows, arms in the gauge order', async () => {
    renderPage();
    const bazos = await screen.findByTestId('location-matrix-bazos');
    const matrix = screen.getByRole('table', { name: /Location quality by portal/ });
    const heads = within(matrix).getAllByRole('columnheader').map((h) => h.textContent);
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
    expect(screen.getByText('Positions · Bazoš')).toBeInTheDocument();
    expect(screen.queryByText('Grain · Bazoš')).toBeNull();
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

describe('LocationQuality positions (W6-c)', () => {
  const grid = () => screen.getByRole('table', { name: /Positions/ });

  it("draws the portal's grid: kinds as rows, grains as columns, each head with the map's precision words", async () => {
    renderPage(undefined, '/location-quality?portal=bazos');
    await screen.findByText('Positions · Bazoš');
    const heads = within(grid()).getAllByRole('columnheader').map((h) => h.textContent);
    expect(heads).toEqual([
      'Kind', 'addressPřesná poloha', 'streetPřibližná poloha', 'částPřibližná poloha', 'obecPřibližná poloha',
      'okresPřibližná poloha', 'countryPřibližná poloha', 'unknownPřesnost polohy neznámá', 'Total']);
    const own = within(grid()).getByRole('row', { name: /Own pin/ });
    expect(within(own).getByRole('button', { name: 'Own pin · street: 30' })).toBeInTheDocument();
    expect(within(own).getByRole('button', { name: 'Own pin · every grain: 50' })).toBeInTheDocument();
    /* Shown, not judged: the unexplained row is there at 0, and a zero is no button. */
    const unexplained = within(grid()).getByRole('row', { name: /Unexplained/ });
    expect(within(unexplained).queryAllByRole('button')).toEqual([]);
    /* The column totals are the grain counts; the corner is the cell's ads. */
    expect(within(grid()).getByRole('button', { name: 'Every kind · street: 33' })).toBeInTheDocument();
    expect(within(grid()).getByRole('button', { name: 'Every kind · every grain: 100' })).toBeInTheDocument();
    /* A row the hourly refresh has not classified yet is counted, but the list cannot show it. */
    const later = within(grid()).getByRole('row', { name: /Not yet classified/ });
    expect(within(later).getAllByText('3')).toHaveLength(2);
    expect(within(later).queryAllByRole('button')).toEqual([]);
    /* The list is not read until a number is chosen. */
    expect(listUrls()).toEqual([]);
  });

  it('a number opens the list on its cell; the cell lives in the URL, copper only on the chosen one', async () => {
    renderPage(undefined, '/location-quality?portal=bazos');
    const button = await screen.findByRole('button', { name: 'Own pin · street: 30' });
    fireEvent.click(button);
    expect(screen.getByTestId('search')).toHaveTextContent('?portal=bazos&kind=own_pin&grain=street');
    expect(screen.getByRole('button', { name: 'Own pin · street: 30' })).toHaveAttribute('aria-pressed', 'true');
    expect(await screen.findByText('Ads')).toBeInTheDocument();
    await screen.findByTestId('position-row-501');
    const [url] = listUrls();
    expect(url).toContain('source=eq.bazos');
    expect(url).toContain('position_kind=eq.own_pin');
    expect(url).toContain('grain=eq.street');
    expect(url).toContain('order=last_seen_at.desc');
    expect(url).toContain('limit=100');
  });

  it('a no-position cell opens every status with its evidence, sibling and queue, each row linking its property', async () => {
    renderPage(undefined, '/location-quality?portal=bazos&kind=none&grain=unknown');
    const row = await screen.findByTestId('position-row-501');
    expect(screen.getByTestId('position-total')).toHaveTextContent('4,066 ads');
    expect(within(row).getByRole('link', { name: /Byt · 2\+kk/ })).toHaveAttribute('href', propertyPath(77, 501));
    expect(row).toHaveTextContent('unresolved');
    expect(row).toHaveTextContent('has a point');
    expect(row).toHaveTextContent('delisted');
    expect(row).toHaveTextContent('no point');
    expect(screen.getByRole('group', { name: 'Status' })).toBeInTheDocument();
    fireEvent.click(within(screen.getByRole('group', { name: 'Town evidence' })).getByRole('button', { name: 'withheld' }));
    await vi.waitFor(() => expect(listUrls().some((u) => u.includes('town_evidence=eq.false'))).toBe(true));
    expect(listUrls()[0]).not.toContain('is_active=eq');
  });

  it("the by-kind strip sums each portal's grid over grain, every number a way into the list", async () => {
    renderPage(undefined, '/location-quality?portal=bazos');
    const strip = await screen.findByRole('table', { name: /By kind/ });
    const sreality = within(strip).getByRole('row', { name: /Sreality/ });
    fireEvent.click(within(sreality).getByRole('button', { name: 'Sreality · Own pin: 2' }));
    expect(screen.getByTestId('search')).toHaveTextContent('?portal=sreality&kind=own_pin');
    expect(screen.getByText('Positions · Sreality')).toBeInTheDocument();
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
