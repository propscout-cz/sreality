import { describe, expect, it, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { DEFAULT_FILTERS } from '@/lib/filters';
import * as stats from '@/lib/stats';
import { pushToast } from '@/lib/toast';
import StatsDashboard from './StatsDashboard';
import { pivotOf } from './testPivot';

/* A stand-in for the account's row: no row until the first save, then the
 * saved list — what RLS would hand back after PUT /stats/dashboard. */
let stored: stats.ChartSpec[] | null = null;

/* A small cap stands in for the registry's, so the at-cap board stays cheap to render. */
const CAP = 6;

vi.mock('@/lib/stats', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/stats')>();
  return {
    ...actual,
    MAX_CHARTS: 6,
    fetchStatsPivot: vi.fn(async (_f: unknown, spec: import('@/lib/stats').StatsSpec) => pivotOf({ spec })),
    fetchDashboard: vi.fn(async () => stored ?? actual.DEFAULT_CHARTS),
    saveDashboard: vi.fn(async (charts: import('@/lib/stats').ChartSpec[]) => {
      stored = charts;
      return charts;
    }),
  };
});

vi.mock('@/lib/toast', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/toast')>()),
  pushToast: vi.fn(),
}));

const renderBoard = (
  market?: { value: stats.StatsMarket; set: (m: stats.StatsMarket) => void },
  small?: { value: boolean; set: (on: boolean) => void },
) =>
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <StatsDashboard filters={DEFAULT_FILTERS} market={market} small={small} />
    </QueryClientProvider>,
  );

const titles = () => screen.getAllByRole('heading', { level: 3 }).map((h) => h.textContent);
const button = (name: string) => screen.getByRole('button', { name });

beforeEach(() => {
  stored = null;
  vi.mocked(stats.saveDashboard).mockClear();
  vi.mocked(pushToast).mockClear();
});

describe('<StatsDashboard>', () => {
  it('shows the four default charts when the account has no board', async () => {
    renderBoard();
    await screen.findAllByRole('heading', { level: 3 });
    expect(titles()).toEqual(['Cena', 'Cena za m²', 'Cena za m² · Dispozice', 'Cena za m² · Stav']);
    expect(button('Přidat graf')).toBeInTheDocument();
  });

  it('adds a chart in edit mode and saves the whole list', async () => {
    renderBoard();
    fireEvent.click(await screen.findByRole('button', { name: 'Přidat graf' }));
    expect(screen.queryByRole('button', { name: 'Přidat graf' })).toBeNull();
    fireEvent.click(button('Přidat Materiál do Řádků'));
    fireEvent.click(button('Uložit graf'));
    await waitFor(() => expect(stats.saveDashboard).toHaveBeenCalledTimes(1));
    const saved = vi.mocked(stats.saveDashboard).mock.calls[0][0];
    expect(saved).toHaveLength(5);
    expect(saved[4]).toMatchObject({ view: 'table', rows: ['building_type'], measure: 'ppm2' });
    /* Saved: listed once, out of edit mode, focus on its Upravit. */
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Uložit graf' })).toBeNull());
    expect(screen.getAllByRole('heading', { name: 'Cena za m² · Materiál' })).toHaveLength(1);
    expect(document.activeElement).toBe(button('Upravit graf Cena za m² · Materiál'));
  });

  it('keeps a new chart and its draft when the save is refused, and saves it on retry', async () => {
    vi.mocked(stats.saveDashboard).mockRejectedValueOnce(new Error('at most 50 charts on a board'));
    renderBoard();
    fireEvent.click(await screen.findByRole('button', { name: 'Přidat graf' }));
    fireEvent.click(button('Přidat Materiál do Řádků'));
    fireEvent.click(button('Uložit graf'));
    await waitFor(() => expect(pushToast).toHaveBeenCalledWith('err', 'Grafy se nepodařilo uložit: at most 50 charts on a board'));
    /* Rolled back to the four, the new card still open on its draft. */
    await waitFor(() => expect(button('Uložit graf')).toBeEnabled());
    expect(titles()).toEqual(['Cena', 'Cena za m²', 'Cena za m² · Dispozice', 'Cena za m² · Stav', 'Cena za m² · Materiál']);
    expect(screen.getByRole('group', { name: 'Řádky' })).toBeInTheDocument();
    fireEvent.click(button('Uložit graf'));
    await waitFor(() => expect(stats.saveDashboard).toHaveBeenCalledTimes(2));
    expect(vi.mocked(stats.saveDashboard).mock.calls[1][0]).toHaveLength(5);
    await waitFor(() => expect(screen.queryByRole('group', { name: 'Řádky' })).toBeNull());
  });

  it('discards a new chart on Zrušit without saving, focus back on the add tile', async () => {
    renderBoard();
    fireEvent.click(await screen.findByRole('button', { name: 'Přidat graf' }));
    fireEvent.click(button('Zrušit úpravy grafu'));
    expect(titles()).toHaveLength(4);
    expect(stats.saveDashboard).not.toHaveBeenCalled();
    expect(document.activeElement).toBe(button('Přidat graf'));
  });

  it('removes a chart behind one confirm and reorders by its arrows, keeping focus', async () => {
    renderBoard();
    const first = await screen.findByRole('article', { name: 'Cena' });
    fireEvent.click(within(first).getByRole('button', { name: 'Odebrat graf Cena' }));
    fireEvent.click(within(first).getByRole('button', { name: 'Ano, odebrat graf Cena' }));
    await waitFor(() => expect(stats.saveDashboard).toHaveBeenCalledTimes(1));
    expect(vi.mocked(stats.saveDashboard).mock.calls[0][0].map((c) => c.id)).toEqual([
      'default-2', 'default-3', 'default-4',
    ]);
    await waitFor(() => expect(titles()).toHaveLength(3));
    /* The card that took its place has focus. */
    expect(document.activeElement).toBe(screen.getByRole('heading', { name: 'Cena za m²' }));
    expect(button('Posunout graf Cena za m² dopředu')).toBeDisabled();
    await waitFor(() => expect(button('Posunout graf Cena za m² dozadu')).not.toHaveAttribute('aria-disabled'));
    fireEvent.click(button('Posunout graf Cena za m² dozadu'));
    await waitFor(() => expect(stats.saveDashboard).toHaveBeenCalledTimes(2));
    expect(vi.mocked(stats.saveDashboard).mock.calls[1][0].map((c) => c.id)).toEqual([
      'default-3', 'default-2', 'default-4',
    ]);
    expect(document.activeElement).toBe(button('Posunout graf Cena za m² dozadu'));
  });

  it('offers no new chart at the cap, and says why', async () => {
    stored = Array.from({ length: CAP }, (_, i) => ({ ...stats.DEFAULT_CHARTS[0], id: `c${i}` }));
    renderBoard();
    expect(await screen.findByText(`Sestava má nejvýš ${CAP} grafů — další přidáte, až některý odeberete.`)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Přidat graf' })).toBeNull();
  });

  it('never falls back to the defaults when the board cannot be read', async () => {
    vi.mocked(stats.fetchDashboard).mockRejectedValueOnce(new Error('relation does not exist'));
    renderBoard();
    expect(await screen.findByText(/relation does not exist/)).toBeInTheDocument();
    expect(screen.queryByRole('heading', { level: 3 })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Přidat graf' })).toBeNull();
  });

  it('offers the sample lens and counts every chart over the sold market when it is on', async () => {
    vi.mocked(stats.fetchStatsPivot).mockClear();
    const set = vi.fn();
    renderBoard({ value: 'sold', set });
    await screen.findAllByRole('heading', { level: 3 });
    const lens = screen.getByRole('group', { name: 'Vzorek' });
    expect(within(lens).getAllByRole('button').map((b) => b.textContent)).toEqual(['Jako ve filtru', 'Prodáno (trh)', 'Vše']);
    await waitFor(() => expect(vi.mocked(stats.fetchStatsPivot)).toHaveBeenCalled());
    for (const [f] of vi.mocked(stats.fetchStatsPivot).mock.calls) {
      expect(f).toEqual(stats.soldMarketFilters(DEFAULT_FILTERS));
    }
    expect(screen.getByText(/^Prodáno \(trh\): jen neaktivní/)).toBeInTheDocument();
    fireEvent.click(within(lens).getByRole('button', { name: 'Jako ve filtru' }));
    expect(set).toHaveBeenCalledWith('filter');
  });

  it('counts every chart over all listings under Vše: any status, dismissed included, no time-on-market bound', async () => {
    vi.mocked(stats.fetchStatsPivot).mockClear();
    renderBoard({ value: 'all', set: vi.fn() });
    await screen.findAllByRole('heading', { level: 3 });
    await waitFor(() => expect(vi.mocked(stats.fetchStatsPivot)).toHaveBeenCalled());
    for (const [f] of vi.mocked(stats.fetchStatsPivot).mock.calls) {
      expect(f).toEqual({ ...DEFAULT_FILTERS, status: 'any', showDismissed: true, tomObservedOnly: false, tomDaysMin: null, tomDaysMax: null });
    }
    expect(screen.getByText(/^Vše: aktivní i neaktivní/)).toBeInTheDocument();
  });

  it('shows no lens where Browse hands none over, and counts the filter as set', async () => {
    vi.mocked(stats.fetchStatsPivot).mockClear();
    renderBoard();
    await screen.findAllByRole('heading', { level: 3 });
    expect(screen.queryByRole('group', { name: 'Vzorek' })).toBeNull();
    await waitFor(() => expect(vi.mocked(stats.fetchStatsPivot)).toHaveBeenCalled());
    for (const [f] of vi.mocked(stats.fetchStatsPivot).mock.calls) expect(f).toEqual(DEFAULT_FILTERS);
  });

  it('offers the small-samples switch beside the lens and hands its state to the switch owner', async () => {
    const set = vi.fn();
    renderBoard(undefined, { value: false, set });
    await screen.findAllByRole('heading', { level: 3 });
    const toggle = screen.getByRole('button', { name: 'Malé vzorky (pod 5): min, max, průměr, medián' });
    expect(toggle).toHaveAttribute('aria-pressed', 'false');
    fireEvent.click(toggle);
    expect(set).toHaveBeenCalledWith(true);
  });

  it('shows no small-samples switch where none is handed over', async () => {
    renderBoard();
    await screen.findAllByRole('heading', { level: 3 });
    expect(screen.queryByRole('button', { name: /^Malé vzorky/ })).toBeNull();
  });
});
