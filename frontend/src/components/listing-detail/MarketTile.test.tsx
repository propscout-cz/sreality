/* The property page's "Okolí dashboard" tab: the account's ONE dashboard marked for
 * it (migration 664, any name), drawn over the property's neighbourhood
 * (lib/neighbourhood) under the Stats lens, and one click from the explore window over
 * the very same sample. */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';

import type { ChartSpec } from '@/lib/stats';
import type { PropertyPublic } from '@/lib/queries';
import type { SavedDashboard } from '@/lib/types';

vi.mock('@/lib/api', async (orig) => ({
  ...(await orig<typeof import('@/lib/api')>()),
  isApiConfigured: () => true,
}));
vi.mock('@/lib/stats', async (orig) => ({
  ...(await orig<typeof import('@/lib/stats')>()),
  listStatsDashboards: vi.fn(),
  fetchStatsPivot: vi.fn(),
}));
const openExplore = vi.hoisted(() => vi.fn());
vi.mock('@/components/ExploreAreaModal', () => ({
  useExploreAreaModal: () => ({ open: openExplore }),
}));

import { fetchStatsPivot, listStatsDashboards, marketSample } from '@/lib/stats';
import { DEFAULT_SCOPE, neighbourhoodFilters } from '@/lib/neighbourhood';
import { cell, pivotOf } from '@/components/stats/testPivot';
import MarketTile from './MarketTile';

const CHART: ChartSpec = { id: 'c1', view: 'table', rows: [], cols: [], measure: 'ppm2', stats: ['median'] };
const dashboard = (over: Partial<SavedDashboard>): SavedDashboard => ({
  id: 'd-tpl',
  name: 'Moje okolí',
  charts: [CHART],
  filter_spec: null,
  position: 0,
  color: null,
  created_at: '2026-10-23T08:00:00+00:00',
  updated_at: '2026-10-23T08:00:00+00:00',
  owner_email: null,
  shared_with: [],
  property_card: true,
  ...over,
});

const PROPERTY = {
  id: 105054,
  property_id: 774,
  lat: 50.1,
  lng: 14.45,
  category_main: 'byt',
  category_type: 'pronajem',
  disposition: '2+kk',
  ownership: 'osobni',
  display_label: 'Kolbenova, Praha 9',
  is_active: true,
} as unknown as PropertyPublic;

function renderTile(property: PropertyPublic = PROPERTY) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <MarketTile property={property} images={[]} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const lastPivotFilters = () => vi.mocked(fetchStatsPivot).mock.calls.at(-1)?.[0];

beforeEach(() => {
  localStorage.clear();
  openExplore.mockClear();
  vi.mocked(fetchStatsPivot).mockReset().mockResolvedValue(pivotOf({ spec: { rows: [], cols: [], measure: 'ppm2' } }));
  vi.mocked(listStatsDashboards).mockReset().mockResolvedValue({ data: [dashboard({})], total: 1 });
});

describe('<MarketTile>', () => {
  it('draws the marked dashboard, under its own name, over the property\'s neighbourhood, sold market within 500 m by default', async () => {
    renderTile();
    await screen.findByRole('heading', { level: 3 });
    expect(screen.getByText('Moje okolí')).toBeInTheDocument();
    expect(screen.getByText(/Byt · .*2\+kk · .* · do 500 m/)).toBeInTheDocument();
    const { filters } = neighbourhoodFilters(PROPERTY, DEFAULT_SCOPE)!;
    await waitFor(() => expect(lastPivotFilters()).toEqual(marketSample(filters, 'sold')));
    // Read-only: the template is edited in Browse › Statistiky, not here.
    expect(screen.queryByRole('button', { name: /^Upravit graf/ })).toBeNull();
  });

  it('counts everything under Vše, and widens the radius on a click; both are remembered', async () => {
    renderTile();
    await screen.findByRole('heading', { level: 3 });
    fireEvent.click(within(screen.getByRole('group', { name: 'Vzorek' })).getByRole('button', { name: 'Vše' }));
    fireEvent.click(within(screen.getByRole('group', { name: 'Okruh' })).getByRole('button', { name: '1 km' }));
    await waitFor(() => expect(lastPivotFilters()).toMatchObject({
      status: 'any', showDismissed: true, circle: { radius_m: 1000 },
    }));
    expect(JSON.parse(localStorage.getItem('sreality.marketTile') ?? '{}')).toEqual({ radiusM: 1000, market: 'all' });
  });

  /* A pick-one is the ONE joined bar (operator ruling 2026-10-27); Budova and Stav pick several, so they stay chips. */
  it('draws Okruh and Vzorek as joined bars, the chosen segment filled copper', async () => {
    renderTile();
    await screen.findByRole('heading', { level: 3 });
    for (const [name, lit] of [['Okruh', '500 m'], ['Vzorek', 'Prodáno (trh)']]) {
      const group = screen.getByRole('group', { name });
      expect(group.firstElementChild).toHaveClass('inline-flex', 'border', 'bg-[var(--color-paper-2)]');
      expect(within(group).getByRole('button', { name: lit })).toHaveAttribute('aria-pressed', 'true');
      expect(within(group).getByRole('button', { name: lit })).toHaveClass('bg-[var(--color-copper)]', 'text-[var(--color-on-copper)]');
      for (const b of within(group).getAllByRole('button')) expect(b).not.toHaveClass('border');
    }
    expect(screen.getByRole('button', { name: 'Panel' })).toHaveClass('border');
  });

  it('names an attribute the property does not state, and leaves it unset', async () => {
    renderTile({ ...PROPERTY, ownership: null } as PropertyPublic);
    expect(await screen.findByText(/vlastnictví neuvedeno, bez filtru/)).toBeInTheDocument();
    await waitFor(() => expect(lastPivotFilters()?.ownership).toEqual([]));
  });

  it('points to Browse › Statistiky while no own dashboard is marked (a shared one, or one merely named so, does not count)', async () => {
    vi.mocked(listStatsDashboards).mockResolvedValue({
      data: [
        dashboard({ owner_email: 'jana@example.com', property_card: false }),
        dashboard({ id: 'd-other', name: 'Trh v okolí', property_card: false }),
      ],
      total: 2,
    });
    const { container } = renderTile();
    expect(await screen.findByRole('link', { name: 'Otevřít Statistiky →' })).toHaveAttribute('href', '/browse?tab=stats');
    expect(fetchStatsPivot).not.toHaveBeenCalled();
    /* A section card (C1–C10): the region is named by its title; the hint is one muted line and its
       action (C10), never a dashed box inside the card (C9); the explore button is the title bar's. */
    const card = screen.getByRole('region', { name: 'Okolí dashboard' });
    const bar = within(card).getByRole('heading', { level: 2 }).parentElement as HTMLElement;
    expect(within(bar).getByRole('button', { name: /Prozkoumat okolí/ })).toBeInTheDocument();
    expect(container.querySelector('.border-dashed')).toBeNull();
  });

  it('opens the explore window over the same neighbourhood, lens and dashboard', async () => {
    renderTile();
    await screen.findByRole('heading', { level: 3 });
    fireEvent.click(within(screen.getByRole('group', { name: 'Vzorek' })).getByRole('button', { name: 'Vše' }));
    fireEvent.click(screen.getByRole('button', { name: /Prozkoumat okolí/ }));
    const { filters } = neighbourhoodFilters(PROPERTY, DEFAULT_SCOPE)!;
    await waitFor(() => expect(openExplore).toHaveBeenCalledWith(expect.objectContaining({
      filters, lat: 50.1, lng: 14.45, tab: 'stats', market: 'all', dashboard: 'd-tpl',
    })));
  });

  it('says so when the property has no point', async () => {
    renderTile({ ...PROPERTY, lat: null } as unknown as PropertyPublic);
    expect(await screen.findByText('Bez souřadnic nelze hledat okolí.')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Prozkoumat okolí/ })).toBeNull();
  });

  it('shows small samples on the property page exactly when its dashboard saved the switch on', async () => {
    const smallPivot = pivotOf({
      spec: { rows: [], cols: [], measure: 'ppm2' },
      cells: [{ ...cell([], 3, null), small: { min: 100_000, max: 140_000, mean: 120_000, median: 118_000 } }],
    });
    vi.mocked(fetchStatsPivot).mockResolvedValue(smallPivot);
    vi.mocked(listStatsDashboards).mockResolvedValue({ data: [dashboard({ show_small: true })], total: 1 });
    const { unmount } = renderTile();
    expect(await screen.findByText('malý vzorek')).toBeInTheDocument();
    unmount();
    vi.mocked(listStatsDashboards).mockResolvedValue({ data: [dashboard({ show_small: false })], total: 1 });
    renderTile();
    expect(await screen.findByText(/^pod 5$/)).toBeInTheDocument();
    expect(screen.queryByText('malý vzorek')).toBeNull();
  });
});
