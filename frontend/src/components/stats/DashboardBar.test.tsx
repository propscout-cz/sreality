/* DashboardBar — the dashboard instance of THE saved-set bar, over the real URL
 * adapter (lib/browseState) in a MemoryRouter. The working board is a stand-in
 * that records each load's write and the URL it saw; the last case mounts the
 * real StatsDashboard to pin that a load goes through the board's one write
 * (saveDashboard, PUT /stats/dashboard). */

import { useState } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, useLocation } from 'react-router-dom';

import { DEFAULT_FILTERS, fromSearchParams } from '@/lib/filters';
import { useUrlBrowseState } from '@/lib/browseState';
import type { ChartSpec } from '@/lib/stats';
import type { SavedDashboard } from '@/lib/types';

vi.mock('@/lib/api', async (orig) => ({
  ...(await orig<typeof import('@/lib/api')>()),
  isApiConfigured: () => true,
}));

vi.mock('@/lib/stats', async (orig) => {
  const actual = await orig<typeof import('@/lib/stats')>();
  return {
    ...actual,
    fetchDashboard: vi.fn(),
    saveDashboard: vi.fn(async (charts: ChartSpec[]) => charts),
    fetchStatsPivot: vi.fn(),
    listStatsDashboards: vi.fn(),
    createStatsDashboard: vi.fn(),
    updateStatsDashboard: vi.fn(),
    deleteStatsDashboard: vi.fn(),
    reorderStatsDashboards: vi.fn(),
    shareStatsDashboard: vi.fn(),
    unshareStatsDashboard: vi.fn(),
  };
});

import * as stats from '@/lib/stats';
import {
  createStatsDashboard,
  listStatsDashboards,
  shareStatsDashboard,
  unshareStatsDashboard,
  updateStatsDashboard,
} from '@/lib/stats';
import DashboardBar from './DashboardBar';
import StatsDashboard from './StatsDashboard';
import { pivotOf } from './testPivot';

const chart = (id: string, over: Partial<ChartSpec> = {}): ChartSpec => ({
  id,
  view: 'table',
  rows: [],
  cols: [],
  measure: 'ppm2',
  stats: ['median'],
  ...over,
});
const A = chart('a');
const B = chart('b', { measure: 'price' });

const row = (over: Partial<SavedDashboard>): SavedDashboard => ({
  id: 'x',
  name: 'x',
  charts: [],
  filter_spec: null,
  position: 0,
  color: null,
  created_at: '2026-10-20T10:00:00+00:00',
  updated_at: '2026-10-20T10:00:00+00:00',
  owner_email: null,
  shared_with: [],
  ...over,
});
const OWN = row({ id: 'd-own', name: 'Ceny', charts: [A] });
const WITH_FILTER = row({
  id: 'd-filter',
  name: 'Domy',
  charts: [B],
  position: 1,
  filter_spec: { filters: { ...DEFAULT_FILTERS, categoryMain: ['dum'] }, sort: null },
});
const SHARED = row({
  id: 'd-shared',
  name: 'Od Jany',
  charts: [B],
  owner_email: 'jana@example.com',
  shared_with: [{ account_id: 'acct-me', email: null }],
});
const LIST = [OWN, WITH_FILTER, SHARED];

/* Each load's board write, with the URL as it stood when the write was issued. */
let writes: Array<{ charts: ChartSpec[]; search: string }> = [];

function Harness({ working: initial }: { working: ChartSpec[] }) {
  const view = useUrlBrowseState();
  const { search } = useLocation();
  const [working, setWorking] = useState(initial);
  return (
    <>
      <DashboardBar
        view={view}
        working={working}
        onLoadCharts={(charts) => {
          writes.push({ charts, search });
          setWorking(charts);
        }}
      />
      <output data-testid="search">{search}</output>
    </>
  );
}

const client = () => new QueryClient({ defaultOptions: { queries: { retry: false } } });

function renderBar(url = '/browse?tab=stats', working: ChartSpec[] = [A], qc = client()) {
  const utils = render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[url]}>
        <Harness working={working} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { ...utils, qc };
}

const url = () => new URLSearchParams(screen.getByTestId('search').textContent ?? '');
const chip = (name: string | RegExp) => screen.findByRole('button', { name });

beforeEach(() => {
  writes = [];
  sessionStorage.clear();
  vi.mocked(listStatsDashboards).mockReset().mockResolvedValue({ data: LIST, total: LIST.length });
  vi.mocked(createStatsDashboard).mockReset();
  vi.mocked(updateStatsDashboard).mockReset();
  vi.mocked(shareStatsDashboard).mockReset();
  vi.mocked(unshareStatsDashboard).mockReset();
  vi.mocked(stats.saveDashboard).mockClear();
});

describe('<DashboardBar> the list', () => {
  it('shows own chips with a grip and a shared one with its owner, never draggable', async () => {
    renderBar();
    expect(await chip('Ceny')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Domy' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Od Jany jana@example.com' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Přesunout dashboard: Ceny' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^Přesunout dashboard: Od Jany/ })).toBeNull();
  });

  it('offers rename, share and a two-step delete on an own chip; load, copy and leave on a shared one', async () => {
    renderBar();
    fireEvent.click(await chip('Možnosti dashboardu: Ceny'));
    expect(within(screen.getByRole('menu')).getAllByRole('menuitem').map((m) => m.textContent)).toEqual([
      'Přejmenovat',
      'Sdílet…',
      'Smazat',
    ]);
    fireEvent.click(screen.getByRole('button', { name: 'Možnosti dashboardu: Od Jany' }));
    expect(within(screen.getByRole('menu')).getAllByRole('menuitem').map((m) => m.textContent)).toEqual([
      'Načíst',
      'Uložit jako vlastní',
      'Přestat sledovat',
    ]);
  });
});

describe('<DashboardBar> loading', () => {
  it('copies the charts into the working board, then writes the filter and the id to the URL', async () => {
    renderBar('/browse?tab=stats&preset=p1');
    fireEvent.click(await chip('Domy'));
    expect(writes).toHaveLength(1);
    expect(writes[0].charts).toEqual([B]);
    expect(new URLSearchParams(writes[0].search).has('dashboard')).toBe(false);
    await waitFor(() => expect(url().get('dashboard')).toBe('d-filter'));
    expect(url().has('preset')).toBe(false);
    expect(fromSearchParams(url()).categoryMain).toEqual(['dum']);
  });

  it('writes only the id for a dashboard without a filter, and no board write when the charts already match', async () => {
    renderBar('/browse?tab=stats&cat=pozemek');
    fireEvent.click(await chip('Ceny'));
    await waitFor(() => expect(url().get('dashboard')).toBe('d-own'));
    expect(url().get('cat')).toBe('pozemek');
    expect(writes).toHaveLength(0);
  });

  it('loads a shared dashboard from its menu like a click', async () => {
    renderBar();
    fireEvent.click(await chip('Možnosti dashboardu: Od Jany'));
    fireEvent.click(screen.getByRole('menuitem', { name: 'Načíst' }));
    expect(writes.map((w) => w.charts)).toEqual([[B]]);
    await waitFor(() => expect(url().get('dashboard')).toBe('d-shared'));
  });
});

describe('<DashboardBar> arrival with ?dashboard=', () => {
  it('loads the named dashboard once, as a click would, and not again on a remount', async () => {
    const { unmount } = renderBar('/browse?tab=stats&dashboard=d-filter', [A]);
    await waitFor(() => expect(writes).toHaveLength(1));
    expect(writes[0].charts).toEqual([B]);
    await waitFor(() => expect(fromSearchParams(url()).categoryMain).toEqual(['dum']));
    expect(url().get('dashboard')).toBe('d-filter');
    await new Promise((r) => setTimeout(r, 20));
    expect(writes).toHaveLength(1);
    /* The tab switch / Back / reload case: this tab already loaded it, so the
     * working board (edited since) stays as it is. */
    unmount();
    renderBar('/browse?tab=stats&dashboard=d-filter', [A, B]);
    await chip('Domy');
    await new Promise((r) => setTimeout(r, 20));
    expect(writes).toHaveLength(1);
    expect(screen.getByRole('button', { name: 'Domy' })).toHaveAccessibleDescription('Upraveno od načtení');
  });

  it('clears a dashboard the caller can no longer read', async () => {
    renderBar('/browse?tab=stats&dashboard=gone');
    await chip('Ceny');
    await waitFor(() => expect(url().has('dashboard')).toBe(false));
    expect(url().get('tab')).toBe('stats');
    expect(writes).toHaveLength(0);
  });
});

describe('<DashboardBar> saving', () => {
  it('marks a drifted board dirty and saves the working charts back, the filter untouched', async () => {
    sessionStorage.setItem('stats.loadedDashboard', 'd-own');
    vi.mocked(updateStatsDashboard).mockResolvedValue({ ...OWN, charts: [A, B] });
    renderBar('/browse?tab=stats&dashboard=d-own', [A, B]);
    expect(await chip('Ceny')).toHaveAccessibleDescription('Upraveno od načtení');
    fireEvent.click(screen.getByRole('button', { name: 'Uložit změny' }));
    const dialog = screen.getByRole('dialog', { name: 'Uložit změny dashboardu' });
    expect(within(dialog).getByRole('button', { name: 'Uložit i nastavení filtru' })).toHaveAttribute('aria-pressed', 'false');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Uložit změny' }));
    await waitFor(() => expect(updateStatsDashboard).toHaveBeenCalledTimes(1));
    expect(updateStatsDashboard).toHaveBeenCalledWith('d-own', { name: 'Ceny', color: null, charts: [A, B] });
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Uložit změny' })).toBeNull());
    expect(screen.getByRole('button', { name: 'Ceny' })).not.toHaveAccessibleDescription('Upraveno od načtení');
  });

  it('marks a drifted filter dirty and saves the current filter into a dashboard that carries one', async () => {
    sessionStorage.setItem('stats.loadedDashboard', 'd-filter');
    vi.mocked(updateStatsDashboard).mockResolvedValue(WITH_FILTER);
    renderBar('/browse?tab=stats&dashboard=d-filter&cat=byt', [B]);
    expect(await chip('Domy')).toHaveAccessibleDescription('Upraveno od načtení');
    fireEvent.click(screen.getByRole('button', { name: 'Uložit změny' }));
    const dialog = screen.getByRole('dialog', { name: 'Uložit změny dashboardu' });
    expect(within(dialog).getByRole('button', { name: 'Uložit i nastavení filtru' })).toHaveAttribute('aria-pressed', 'true');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Uložit změny' }));
    await waitFor(() => expect(updateStatsDashboard).toHaveBeenCalledTimes(1));
    const [, patch] = vi.mocked(updateStatsDashboard).mock.calls[0];
    expect(patch.charts).toEqual([B]);
    expect(patch.filter_spec?.filters.categoryMain).toEqual(['byt']);
    expect(patch.filter_spec?.filters.bounds).toBeNull();
    expect(patch.filter_spec?.sort).toBe('-first_seen_at');
  });

  it('saves the working charts as a new dashboard without the filter, and selects it', async () => {
    const created = row({ id: 'd-new', name: 'Nový', charts: [A, B], position: 3 });
    vi.mocked(createStatsDashboard).mockResolvedValue(created);
    renderBar('/browse?tab=stats', [A, B]);
    /* The re-read after the POST holds the new row, as the server's would. */
    vi.mocked(listStatsDashboards).mockResolvedValue({ data: [...LIST, created], total: LIST.length + 1 });
    fireEvent.click(await chip('+ Uložit jako dashboard'));
    const dialog = screen.getByRole('dialog', { name: 'Uložit grafy jako dashboard' });
    fireEvent.change(within(dialog).getByRole('textbox', { name: 'Název dashboardu' }), { target: { value: 'Nový' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Uložit dashboard' }));
    await waitFor(() => expect(createStatsDashboard).toHaveBeenCalledTimes(1));
    expect(createStatsDashboard).toHaveBeenCalledWith({ name: 'Nový', charts: [A, B], color: null });
    await waitFor(() => expect(url().get('dashboard')).toBe('d-new'));
    expect(await chip('Nový')).not.toHaveAccessibleDescription('Upraveno od načtení');
    expect(writes).toHaveLength(0);
  });

  it('saves the current filter with a new dashboard when the box is ticked', async () => {
    vi.mocked(createStatsDashboard).mockResolvedValue(row({ id: 'd-new', name: 'Byty' }));
    renderBar('/browse?tab=stats&cat=byt', [A]);
    fireEvent.click(await chip('+ Uložit jako dashboard'));
    const dialog = screen.getByRole('dialog', { name: 'Uložit grafy jako dashboard' });
    fireEvent.change(within(dialog).getByRole('textbox', { name: 'Název dashboardu' }), { target: { value: 'Byty' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Uložit i nastavení filtru' }));
    fireEvent.click(within(dialog).getByRole('button', { name: 'Uložit dashboard' }));
    await waitFor(() => expect(createStatsDashboard).toHaveBeenCalledTimes(1));
    const input = vi.mocked(createStatsDashboard).mock.calls[0][0];
    expect(input.charts).toEqual([A]);
    expect(input.filter_spec?.filters.categoryMain).toEqual(['byt']);
  });

  it('copies a shared dashboard as the caller\'s own', async () => {
    vi.mocked(createStatsDashboard).mockResolvedValue(row({ id: 'd-copy', name: 'Od Jany', charts: [B] }));
    renderBar();
    fireEvent.click(await chip('Možnosti dashboardu: Od Jany'));
    fireEvent.click(screen.getByRole('menuitem', { name: 'Uložit jako vlastní' }));
    await waitFor(() =>
      expect(createStatsDashboard).toHaveBeenCalledWith({ name: 'Od Jany', charts: [B], color: null }),
    );
  });
});

describe('<DashboardBar> sharing', () => {
  it('shares from "Sdílet…" through POST and lists the recipient', async () => {
    vi.mocked(shareStatsDashboard).mockResolvedValue({
      ...OWN,
      shared_with: [{ account_id: 'acct-b', email: 'b@example.com' }],
    });
    renderBar();
    fireEvent.click(await chip('Možnosti dashboardu: Ceny'));
    fireEvent.click(screen.getByRole('menuitem', { name: 'Sdílet…' }));
    const dialog = screen.getByRole('dialog', { name: 'Sdílet „Ceny“' });
    expect(within(dialog).getByText('Zatím s nikým nesdíleno.')).toBeInTheDocument();
    fireEvent.change(within(dialog).getByRole('textbox', { name: 'E-mail' }), { target: { value: 'b@example.com' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Sdílet' }));
    await waitFor(() => expect(shareStatsDashboard).toHaveBeenCalledWith('d-own', 'b@example.com'));
    expect(await within(dialog).findByText('b@example.com')).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Zavřít' }));
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('leaves a shared dashboard behind a confirm, with the caller\'s own grant', async () => {
    vi.mocked(unshareStatsDashboard).mockResolvedValue({ deleted: true });
    renderBar();
    fireEvent.click(await chip('Možnosti dashboardu: Od Jany'));
    fireEvent.click(screen.getByRole('menuitem', { name: 'Přestat sledovat' }));
    expect(unshareStatsDashboard).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('menuitem', { name: 'Opravdu přestat sledovat' }));
    await waitFor(() => expect(unshareStatsDashboard).toHaveBeenCalledWith('d-shared', 'acct-me'));
  });
});

describe('<StatsDashboard> with the bar', () => {
  it('loads a dashboard through the board\'s one write (PUT /stats/dashboard)', async () => {
    vi.mocked(stats.fetchDashboard).mockResolvedValue([A]);
    vi.mocked(stats.fetchStatsPivot).mockImplementation(async (_f, spec) => pivotOf({ spec }));
    function Page() {
      const view = useUrlBrowseState();
      return <StatsDashboard filters={view.filters} view={view} />;
    }
    render(
      <QueryClientProvider client={client()}>
        <MemoryRouter initialEntries={['/browse?tab=stats']}>
          <Page />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    fireEvent.click(await chip('Domy'));
    await waitFor(() => expect(stats.saveDashboard).toHaveBeenCalledTimes(1));
    expect(vi.mocked(stats.saveDashboard).mock.calls[0][0]).toEqual([B]);
  });
});
