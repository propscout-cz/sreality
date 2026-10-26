/* DashboardBar — the dashboard instance of THE saved-set bar, over the real URL
 * adapter (lib/browseState) in a MemoryRouter. The working board is a stand-in:
 * one ordered log records each load's board write and URL write; a `deferred`
 * board holds the write open the way the optimistic PUT's patch lands a tick
 * later. The last case mounts the real StatsDashboard to pin that a load goes
 * through the board's one write (saveDashboard, PUT /stats/dashboard). */

import { useState } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from 'vitest';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, useLocation, useNavigate, useNavigationType } from 'react-router-dom';

import { DEFAULT_FILTERS, fromSearchParams } from '@/lib/filters';
import { applySavedFilter, useUrlBrowseState } from '@/lib/browseState';
import type { ChartSpec } from '@/lib/stats';
import type { SavedDashboard } from '@/lib/types';

vi.mock('@/lib/api', async (orig) => ({
  ...(await orig<typeof import('@/lib/api')>()),
  isApiConfigured: () => true,
}));

vi.mock('@/lib/toast', async (orig) => ({
  ...(await orig<typeof import('@/lib/toast')>()),
  pushToast: vi.fn(),
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
    setPropertyCardDashboard: vi.fn(),
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
  deleteStatsDashboard,
  listStatsDashboards,
  setPropertyCardDashboard,
  shareStatsDashboard,
  statsDashboardKeys,
  unshareStatsDashboard,
  updateStatsDashboard,
} from '@/lib/stats';
import { pushToast } from '@/lib/toast';
import DashboardBar, { __resetLoadedDashboardForTests, copyName, type DashboardView } from './DashboardBar';
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
const SHARED_FILTER = { filters: { ...DEFAULT_FILTERS, categoryMain: ['pozemek' as const] }, sort: null };
const SHARED = row({
  id: 'd-shared',
  name: 'Od Jany',
  charts: [B],
  filter_spec: SHARED_FILTER,
  owner_email: 'jana@example.com',
  shared_with: [{ account_id: 'acct-me', email: null }],
});
const LIST = [OWN, WITH_FILTER, SHARED];

/* One ordered log: `put` = the board write, `url:<id>` = the URL write. */
let log: string[] = [];
let writes: ChartSpec[][] = [];
/* A held board write: resolved by the test, the board then holds the charts. */
let release: (() => void) | null = null;

function Harness({ working: initial, deferred }: { working: ChartSpec[]; deferred: boolean }) {
  const view = useUrlBrowseState();
  const { pathname, search } = useLocation();
  const nav = useNavigationType();
  const navigate = useNavigate();
  const [working, setWorking] = useState(initial);
  const spied: DashboardView = {
    ...view,
    loadDashboard: (d, opts) => {
      log.push(`url:${d.id}`);
      view.loadDashboard(d, opts);
    },
  };
  return (
    <>
      <DashboardBar
        view={spied}
        working={working}
        onLoadCharts={(charts) => {
          log.push('put');
          writes.push(charts);
          if (!deferred) {
            setWorking(charts);
            return Promise.resolve();
          }
          return new Promise<void>((resolve) => {
            release = () => {
              setWorking(charts);
              resolve();
            };
          });
        }}
      />
      <output data-testid="search">{search}</output>
      <output data-testid="where">{`${nav} ${pathname}`}</output>
      <button type="button" onClick={() => navigate(-1)}>
        Zpět
      </button>
    </>
  );
}

const client = (staleTime = 0) => new QueryClient({ defaultOptions: { queries: { retry: false, staleTime } } });

function renderBar(
  url: string | string[] = '/browse?tab=stats',
  working: ChartSpec[] = [A],
  { qc = client(), deferred = false }: { qc?: QueryClient; deferred?: boolean } = {},
) {
  const entries = Array.isArray(url) ? url : [url];
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={entries} initialIndex={entries.length - 1}>
        <Harness working={working} deferred={deferred} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const url = () => new URLSearchParams(screen.getByTestId('search').textContent ?? '');
const where = () => screen.getByTestId('where').textContent;
const chip = (name: string | RegExp) => screen.findByRole('button', { name });
const menuFor = async (name: string) => fireEvent.click(await chip(`Možnosti dashboardu: ${name}`));
const menuItems = () => within(screen.getByRole('menu')).getAllByRole('menuitem').map((m) => m.textContent);
const EDITED = 'Upraveno od načtení';
const tick = () => act(() => new Promise((r) => setTimeout(r, 20)));
/* This tab already loaded `id` (a reload: only the storage marker survives). */
const loadedBefore = (id: string) => sessionStorage.setItem('stats.loadedDashboard', id);

let storageSpy: MockInstance | null = null;
afterEach(() => {
  storageSpy?.mockRestore();
  storageSpy = null;
});

beforeEach(() => {
  log = [];
  writes = [];
  release = null;
  sessionStorage.clear();
  __resetLoadedDashboardForTests();
  vi.mocked(listStatsDashboards).mockReset().mockResolvedValue({ data: LIST, total: LIST.length });
  vi.mocked(createStatsDashboard).mockReset();
  vi.mocked(setPropertyCardDashboard).mockReset();
  vi.mocked(updateStatsDashboard).mockReset();
  vi.mocked(deleteStatsDashboard).mockReset();
  vi.mocked(shareStatsDashboard).mockReset();
  vi.mocked(unshareStatsDashboard).mockReset();
  vi.mocked(stats.saveDashboard).mockClear();
  vi.mocked(pushToast).mockClear();
});

describe('<DashboardBar> the list', () => {
  it('shows own chips with a grip and a shared one named with its owner, never draggable', async () => {
    renderBar();
    expect(await chip('Ceny')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Domy' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Od Jany — sdílí jana@example.com' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Možnosti dashboardu: Od Jany — sdílí jana@example.com' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Přesunout dashboard: Ceny' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^Přesunout dashboard: Od Jany/ })).toBeNull();
  });

  it('offers "+ Okolí dashboard" while no own dashboard is the property page\'s: it saves the working board and marks it', async () => {
    const created = row({ id: 'd-card', name: 'Okolí dashboard', charts: [A] });
    const card = { ...created, property_card: true };
    vi.mocked(listStatsDashboards)
      .mockResolvedValueOnce({ data: LIST, total: LIST.length })
      .mockResolvedValue({ data: [...LIST, card], total: LIST.length + 1 });
    vi.mocked(createStatsDashboard).mockResolvedValue(created);
    vi.mocked(setPropertyCardDashboard).mockResolvedValue(card);
    renderBar();
    fireEvent.click(await chip('+ Okolí dashboard'));
    await waitFor(() => expect(createStatsDashboard).toHaveBeenCalledWith({ name: 'Okolí dashboard', charts: [A], show_small: false }));
    await waitFor(() => expect(setPropertyCardDashboard).toHaveBeenCalledWith('d-card'));
    // Marked, it is a chip that names its role — read without "sdílí" — and the offer is gone.
    expect(await chip('Okolí dashboard — okolí dashboard')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '+ Okolí dashboard' })).toBeNull();
    await waitFor(() => expect(url().get('dashboard')).toBe('d-card'));
  });

  it('is the marked dashboard under any name; a name alone or a shared mark makes none', async () => {
    const mine = row({ ...OWN, id: 'd-mine', name: 'Moje okolí', property_card: true });
    const foreign = row({ ...SHARED, id: 'd-foreign', name: 'Trh v okolí', property_card: false });
    vi.mocked(listStatsDashboards).mockResolvedValue({ data: [mine, foreign], total: 2 });
    renderBar();
    expect(await chip('Moje okolí — okolí dashboard')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '+ Okolí dashboard' })).toBeNull();
  });

  it('takes no dashboard for the property page\'s by its name alone (#1977\'s rule is gone)', async () => {
    vi.mocked(listStatsDashboards).mockResolvedValue({ data: [row({ ...OWN, name: 'Trh v okolí' })], total: 1 });
    renderBar();
    expect(await chip('+ Okolí dashboard')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Trh v okolí' })).toBeInTheDocument();
  });

  it('makes any other own dashboard the property page\'s from its menu, asking first when it carries a filter', async () => {
    const card = row({ ...OWN, id: 'd-card', name: 'Okolí', property_card: true });
    vi.mocked(listStatsDashboards).mockResolvedValue({ data: [card, OWN, WITH_FILTER], total: 3 });
    vi.mocked(setPropertyCardDashboard).mockImplementation(async (id) => ({ ...OWN, id, property_card: true }));
    renderBar();
    await menuFor('Okolí — okolí dashboard');
    expect(menuItems()).not.toContain('Použít jako okolí dashboard');
    await menuFor('Ceny');
    fireEvent.click(screen.getByRole('menuitem', { name: 'Použít jako okolí dashboard' }));
    await waitFor(() => expect(setPropertyCardDashboard).toHaveBeenCalledWith('d-own'));
    // The cache moves the mark at once: one chip says it.
    expect(await chip('Ceny — okolí dashboard')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Okolí' })).toBeInTheDocument();
    // A dashboard with a filter loses it as the card: the menu asks first.
    await menuFor('Domy');
    fireEvent.click(screen.getByRole('menuitem', { name: 'Použít jako okolí dashboard' }));
    expect(setPropertyCardDashboard).toHaveBeenCalledTimes(1);
    expect(screen.getByRole('menuitem', { name: 'Použít a odebrat jeho filtr' })).toBeInTheDocument();
  });

  it('keeps two shared chips of one name apart for a screen reader', async () => {
    const twin = row({ ...SHARED, id: 'd-twin', owner_email: 'petr@example.com' });
    vi.mocked(listStatsDashboards).mockResolvedValue({ data: [SHARED, twin], total: 2 });
    renderBar();
    expect(await chip('Možnosti dashboardu: Od Jany — sdílí jana@example.com')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Možnosti dashboardu: Od Jany — sdílí petr@example.com' })).toBeInTheDocument();
  });

  it('offers rename, duplicate, share and a two-step delete on an own chip; load, copy and leave on a shared one', async () => {
    renderBar();
    await menuFor('Ceny');
    expect(menuItems()).toEqual(['Přejmenovat', 'Duplikovat', 'Sdílet…', 'Použít jako okolí dashboard', 'Smazat…']);
    await menuFor('Od Jany — sdílí jana@example.com');
    expect(menuItems()).toEqual(['Načíst', 'Uložit jako vlastní', 'Přestat sledovat']);
  });

  it('duplicates an own dashboard as stored: a new row named "<name> (kopie)", nothing loaded or activated', async () => {
    const filtered = row({ id: 'd-filt', name: 'Pozemky', charts: [B], filter_spec: SHARED_FILTER, color: 'sage' });
    const copied = row({ id: 'd-copy', name: 'Pozemky (kopie)', charts: [B], filter_spec: SHARED_FILTER, color: 'sage' });
    vi.mocked(listStatsDashboards)
      .mockResolvedValueOnce({ data: [OWN, filtered], total: 2 })
      .mockResolvedValue({ data: [OWN, filtered, copied], total: 3 });
    vi.mocked(createStatsDashboard).mockResolvedValue(copied);
    renderBar();
    await menuFor('Pozemky');
    fireEvent.click(screen.getByRole('menuitem', { name: 'Duplikovat' }));
    await waitFor(() => expect(createStatsDashboard).toHaveBeenCalledTimes(1));
    expect(createStatsDashboard).toHaveBeenCalledWith({
      name: 'Pozemky (kopie)', charts: [B], filter_spec: SHARED_FILTER, color: 'sage', show_small: false,
    });
    expect(await screen.findByTitle('Načíst dashboard: Pozemky (kopie)')).toBeInTheDocument();
    expect(url().get('dashboard')).toBeNull();
  });

  it('keeps a duplicated name within the 200 characters the API accepts', () => {
    const long = 'x'.repeat(200);
    expect(copyName(long)).toHaveLength(200);
    expect(copyName(long).endsWith(' (kopie)')).toBe(true);
    expect(copyName('Ceny')).toBe('Ceny (kopie)');
  });

  it('points a dirty shared chip at "Uložit jako vlastní", never at an update it does not offer', async () => {
    loadedBefore('d-shared');
    renderBar('/browse?tab=stats&dashboard=d-shared', [A]);
    const load = await chip('Od Jany — sdílí jana@example.com');
    expect(load).toHaveAccessibleDescription(EDITED);
    expect(within(load).getByTitle(/Uložit jako vlastní$/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Uložit změny' })).toBeNull();
  });
});

describe('<DashboardBar> loading', () => {
  it('writes the board first, then the URL: the filter and the id, pushed', async () => {
    renderBar('/browse?tab=stats&preset=p1');
    fireEvent.click(await chip('Domy'));
    expect(log).toEqual(['put', 'url:d-filter']);
    expect(writes).toEqual([[B]]);
    await waitFor(() => expect(url().get('dashboard')).toBe('d-filter'));
    expect(url().has('preset')).toBe(false);
    expect(fromSearchParams(url()).categoryMain).toEqual(['dum']);
    expect(where()).toBe('PUSH /browse');
    fireEvent.click(screen.getByRole('button', { name: 'Zpět' }));
    expect(url().has('dashboard')).toBe(false);
  });

  it('writes only the id for a dashboard without a filter, and no board write when the charts already match', async () => {
    renderBar('/browse?tab=stats&cat=pozemek');
    fireEvent.click(await chip('Ceny'));
    await waitFor(() => expect(url().get('dashboard')).toBe('d-own'));
    expect(url().get('cat')).toBe('pozemek');
    expect(log).toEqual(['url:d-own']);
  });

  it('sets the "Malé vzorky" switch to the dashboard\'s saved value in the same URL write', async () => {
    const small = row({ id: 'd-small', name: 'Malé', charts: [A], show_small: true });
    vi.mocked(listStatsDashboards).mockResolvedValue({ data: [OWN, small], total: 2 });
    renderBar('/browse?tab=stats');
    fireEvent.click(await chip('Malé'));
    await waitFor(() => expect(url().get('dashboard')).toBe('d-small'));
    expect(url().get('small')).toBe('1');
    fireEvent.click(await chip('Ceny'));
    await waitFor(() => expect(url().get('dashboard')).toBe('d-own'));
    expect(url().has('small')).toBe(false);
  });

  it('loads a shared dashboard from its menu like a click', async () => {
    renderBar();
    await menuFor('Od Jany — sdílí jana@example.com');
    fireEvent.click(screen.getByRole('menuitem', { name: 'Načíst' }));
    expect(log).toEqual(['put', 'url:d-shared']);
    await waitFor(() => expect(url().get('dashboard')).toBe('d-shared'));
  });

  it('draws no dirty mark while the load\'s board write is still landing', async () => {
    renderBar('/browse?tab=stats', [A], { deferred: true });
    fireEvent.click(await chip('Domy'));
    await waitFor(() => expect(url().get('dashboard')).toBe('d-filter'));
    expect(screen.getByRole('button', { name: 'Domy' })).not.toHaveAccessibleDescription(EDITED);
    expect(screen.queryByRole('button', { name: 'Uložit změny' })).toBeNull();
    act(() => release?.());
    await tick();
    expect(screen.getByRole('button', { name: 'Domy' })).not.toHaveAccessibleDescription(EDITED);
  });
});

describe('<DashboardBar> arrival with ?dashboard=', () => {
  it('loads the named dashboard once, REPLACING the link\'s entry, and not again on a remount', async () => {
    const { unmount } = renderBar(['/notifications', '/browse?tab=stats&dashboard=d-filter'], [A]);
    await waitFor(() => expect(log).toEqual(['put', 'url:d-filter']));
    await waitFor(() => expect(fromSearchParams(url()).categoryMain).toEqual(['dum']));
    expect(where()).toBe('REPLACE /browse');
    await tick();
    expect(log).toHaveLength(2);
    /* Back leaves Browse: no second, filterless entry of the same dashboard. */
    fireEvent.click(screen.getByRole('button', { name: 'Zpět' }));
    expect(where()).toBe('POP /notifications');
    /* The tab switch / Back / reload case: this tab already loaded it, so the
     * working board (edited since) stays as it is. */
    unmount();
    renderBar('/browse?tab=stats&dashboard=d-filter', [A, B]);
    expect(await chip('Domy')).toHaveAccessibleDescription(EDITED);
    await tick();
    expect(log).toHaveLength(2);
  });

  it('re-reads a fresh cache on mount, so a dashboard shared a moment ago is found and loaded', async () => {
    const qc = client(60_000);
    qc.setQueryData(statsDashboardKeys.all, { data: [OWN], total: 1 });
    renderBar('/browse?tab=stats&dashboard=d-filter', [A], { qc });
    await waitFor(() => expect(log).toEqual(['put', 'url:d-filter']));
    expect(url().get('dashboard')).toBe('d-filter');
    expect(listStatsDashboards).toHaveBeenCalledTimes(1);
  });

  it('clears a dashboard the caller can no longer read, once the list is re-read', async () => {
    const qc = client(60_000);
    qc.setQueryData(statsDashboardKeys.all, { data: [...LIST, row({ id: 'gone' })], total: 4 });
    renderBar('/browse?tab=stats&dashboard=gone', [A], { qc });
    await waitFor(() => expect(url().has('dashboard')).toBe(false));
    expect(url().get('tab')).toBe('stats');
    expect(log).toEqual([]);
  });

  it('keeps one load per dashboard in memory when the storage refuses writes', async () => {
    storageSpy = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('QuotaExceededError');
    });
    const { unmount } = renderBar('/browse?tab=stats&dashboard=d-filter', [A]);
    await waitFor(() => expect(log).toEqual(['put', 'url:d-filter']));
    await tick();
    expect(log).toHaveLength(2);
    unmount();
    renderBar('/browse?tab=stats&dashboard=d-filter', [A]);
    await chip('Domy');
    await tick();
    expect(log).toHaveLength(2);
  });
});

describe('<DashboardBar> saving', () => {
  const openUpdate = async () => {
    fireEvent.click(await screen.findByRole('button', { name: 'Uložit změny' }));
    return screen.getByRole('dialog', { name: 'Uložit změny dashboardu' });
  };
  const box = (dialog: HTMLElement) => within(dialog).getByRole('button', { name: 'Uložit i nastavení filtru' });
  const submit = (dialog: HTMLElement) => fireEvent.click(within(dialog).getByRole('button', { name: 'Uložit změny' }));

  it('greys the filter box out on the property page\'s dashboard and never sends a filter for it', async () => {
    const card = row({ ...OWN, filter_spec: null, property_card: true });
    vi.mocked(listStatsDashboards).mockResolvedValue({ data: [card], total: 1 });
    loadedBefore('d-own');
    vi.mocked(updateStatsDashboard).mockResolvedValue({ ...card, charts: [A, B] });
    renderBar('/browse?tab=stats&dashboard=d-own', [A, B]);
    const dialog = await openUpdate();
    expect(box(dialog)).toBeDisabled();
    expect(box(dialog)).toHaveAttribute('aria-pressed', 'false');
    expect(within(dialog).getByText(/Okolí dashboard filtr nenese/)).toBeInTheDocument();
    submit(dialog);
    await waitFor(() =>
      expect(updateStatsDashboard).toHaveBeenCalledWith('d-own', { name: 'Ceny', color: null, charts: [A, B], show_small: false }),
    );
  });

  it('marks a drifted board dirty and saves the working charts back, the filter untouched', async () => {
    loadedBefore('d-own');
    vi.mocked(updateStatsDashboard).mockResolvedValue({ ...OWN, charts: [A, B] });
    renderBar('/browse?tab=stats&dashboard=d-own', [A, B]);
    expect(await chip('Ceny')).toHaveAccessibleDescription(EDITED);
    const dialog = await openUpdate();
    expect(box(dialog)).toHaveAttribute('aria-pressed', 'false');
    submit(dialog);
    await waitFor(() => expect(updateStatsDashboard).toHaveBeenCalledTimes(1));
    expect(updateStatsDashboard).toHaveBeenCalledWith('d-own', { name: 'Ceny', color: null, charts: [A, B], show_small: false });
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Uložit změny' })).toBeNull());
    expect(screen.getByRole('button', { name: 'Ceny' })).not.toHaveAccessibleDescription(EDITED);
  });

  it('marks the dashboard dirty when the "Malé vzorky" switch differs from the saved one, and saves the switch', async () => {
    loadedBefore('d-own');
    vi.mocked(updateStatsDashboard).mockResolvedValue({ ...OWN, show_small: true });
    renderBar('/browse?tab=stats&dashboard=d-own&small=1', [A]);
    expect(await chip('Ceny')).toHaveAccessibleDescription(EDITED);
    submit(await openUpdate());
    await waitFor(() => expect(updateStatsDashboard).toHaveBeenCalledTimes(1));
    expect(updateStatsDashboard).toHaveBeenCalledWith('d-own', { name: 'Ceny', color: null, charts: [A], show_small: true });
    await waitFor(() => expect(screen.getByRole('button', { name: 'Ceny' })).not.toHaveAccessibleDescription(EDITED));
  });

  it('marks a drifted filter dirty and saves the current filter into a dashboard that carries one, without the lenses', async () => {
    loadedBefore('d-filter');
    vi.mocked(updateStatsDashboard).mockResolvedValue(WITH_FILTER);
    renderBar('/browse?tab=stats&dashboard=d-filter&cat=byt&tags=4&broker=7&dismissed=show', [B]);
    expect(await chip('Domy')).toHaveAccessibleDescription(EDITED);
    const dialog = await openUpdate();
    expect(box(dialog)).toHaveAttribute('aria-pressed', 'true');
    submit(dialog);
    await waitFor(() => expect(updateStatsDashboard).toHaveBeenCalledTimes(1));
    const [, patch] = vi.mocked(updateStatsDashboard).mock.calls[0];
    expect(patch.charts).toEqual([B]);
    expect(patch.filter_spec?.filters.categoryMain).toEqual(['byt']);
    expect(patch.filter_spec?.filters.bounds).toBeNull();
    /* A dashboard can be shared: the owner's lenses never ride into it. */
    expect(patch.filter_spec?.filters.tags).toBeNull();
    expect(patch.filter_spec?.filters.brokerId).toBeNull();
    expect(patch.filter_spec?.filters.showDismissed).toBe(false);
    expect(patch.filter_spec?.sort).toBe('-first_seen_at');
  });

  it('clears the stored filter when the box is unticked', async () => {
    loadedBefore('d-filter');
    vi.mocked(updateStatsDashboard).mockResolvedValue({ ...WITH_FILTER, filter_spec: null });
    renderBar('/browse?tab=stats&dashboard=d-filter&cat=byt', [B]);
    const dialog = await openUpdate();
    fireEvent.click(box(dialog));
    submit(dialog);
    await waitFor(() => expect(updateStatsDashboard).toHaveBeenCalledTimes(1));
    expect(vi.mocked(updateStatsDashboard).mock.calls[0][1]).toEqual({
      name: 'Domy',
      color: null,
      charts: [B],
      show_small: false,
      filter_spec: null,
    });
  });

  it('marks a sort-only change dirty, as a preset does, and saves the current sort', async () => {
    loadedBefore('d-filter');
    vi.mocked(updateStatsDashboard).mockResolvedValue(WITH_FILTER);
    /* The URL a load of d-filter writes: its filters, the default sort. */
    const loaded = applySavedFilter(WITH_FILTER.filter_spec!, new URLSearchParams('tab=stats&dashboard=d-filter'));
    const { unmount } = renderBar(`/browse?${loaded}`, [B]);
    expect(await chip('Domy')).not.toHaveAccessibleDescription(EDITED);
    unmount();
    loaded.set('sort', 'price_czk');
    renderBar(`/browse?${loaded}`, [B]);
    expect(await chip('Domy')).toHaveAccessibleDescription(EDITED);
    submit(await openUpdate());
    await waitFor(() => expect(updateStatsDashboard).toHaveBeenCalledTimes(1));
    const [, patch] = vi.mocked(updateStatsDashboard).mock.calls[0];
    expect(patch.charts).toEqual([B]);
    expect(patch.filter_spec?.filters.categoryMain).toEqual(['dum']);
    expect(patch.filter_spec?.sort).toBe('price_czk');
  });

  it('ignores the sort for a dashboard without a filter', async () => {
    loadedBefore('d-own');
    renderBar('/browse?tab=stats&dashboard=d-own&sort=price_czk', [A]);
    expect(await chip('Ceny')).not.toHaveAccessibleDescription(EDITED);
    expect(screen.queryByRole('button', { name: 'Uložit změny' })).toBeNull();
  });

  it('adds the current filter to a dashboard without one when the box is ticked', async () => {
    loadedBefore('d-own');
    vi.mocked(updateStatsDashboard).mockResolvedValue(OWN);
    renderBar('/browse?tab=stats&dashboard=d-own&cat=dum', [A, B]);
    const dialog = await openUpdate();
    fireEvent.click(box(dialog));
    submit(dialog);
    await waitFor(() => expect(updateStatsDashboard).toHaveBeenCalledTimes(1));
    const [, patch] = vi.mocked(updateStatsDashboard).mock.calls[0];
    expect(patch.filter_spec?.filters.categoryMain).toEqual(['dum']);
  });

  it('saves the working charts as a new dashboard without the filter, and selects it', async () => {
    const created = row({ id: 'd-new', name: 'Nový', charts: [A, B], position: 3 });
    vi.mocked(createStatsDashboard).mockResolvedValue(created);
    renderBar('/browse?tab=stats', [A, B]);
    /* The re-read after the POST holds the new row, as the server's would. */
    await chip('Ceny');
    vi.mocked(listStatsDashboards).mockResolvedValue({ data: [...LIST, created], total: LIST.length + 1 });
    fireEvent.click(screen.getByRole('button', { name: '+ Uložit jako dashboard' }));
    const dialog = screen.getByRole('dialog', { name: 'Uložit grafy jako dashboard' });
    fireEvent.change(within(dialog).getByRole('textbox', { name: 'Název dashboardu' }), { target: { value: 'Nový' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Uložit dashboard' }));
    await waitFor(() => expect(createStatsDashboard).toHaveBeenCalledTimes(1));
    expect(createStatsDashboard).toHaveBeenCalledWith({ name: 'Nový', charts: [A, B], color: null, show_small: false });
    await waitFor(() => expect(url().get('dashboard')).toBe('d-new'));
    expect(await chip('Nový')).not.toHaveAccessibleDescription(EDITED);
    expect(log).toEqual([]);
  });

  it('saves the switch with a new dashboard when it is on', async () => {
    vi.mocked(createStatsDashboard).mockResolvedValue(row({ id: 'd-new', name: 'Nový', charts: [A], show_small: true }));
    renderBar('/browse?tab=stats&small=1', [A]);
    await chip('Ceny');
    fireEvent.click(screen.getByRole('button', { name: '+ Uložit jako dashboard' }));
    const dialog = screen.getByRole('dialog', { name: 'Uložit grafy jako dashboard' });
    fireEvent.change(within(dialog).getByRole('textbox', { name: 'Název dashboardu' }), { target: { value: 'Nový' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Uložit dashboard' }));
    await waitFor(() => expect(createStatsDashboard).toHaveBeenCalledTimes(1));
    expect(createStatsDashboard).toHaveBeenCalledWith({ name: 'Nový', charts: [A], color: null, show_small: true });
  });

  it('saves the current filter with a new dashboard when the box is ticked', async () => {
    vi.mocked(createStatsDashboard).mockResolvedValue(row({ id: 'd-new', name: 'Byty' }));
    renderBar('/browse?tab=stats&cat=byt', [A]);
    fireEvent.click(await chip('+ Uložit jako dashboard'));
    const dialog = screen.getByRole('dialog', { name: 'Uložit grafy jako dashboard' });
    fireEvent.change(within(dialog).getByRole('textbox', { name: 'Název dashboardu' }), { target: { value: 'Byty' } });
    fireEvent.click(box(dialog));
    fireEvent.click(within(dialog).getByRole('button', { name: 'Uložit dashboard' }));
    await waitFor(() => expect(createStatsDashboard).toHaveBeenCalledTimes(1));
    const input = vi.mocked(createStatsDashboard).mock.calls[0][0];
    expect(input.charts).toEqual([A]);
    expect(input.filter_spec?.filters.categoryMain).toEqual(['byt']);
  });

  it('copies the ACTIVE shared dashboard as shown — the edited charts, the current filter and sort — and selects the copy', async () => {
    loadedBefore('d-shared');
    const copy = row({ id: 'd-copy', name: 'Od Jany', charts: [A, B], filter_spec: SHARED_FILTER });
    vi.mocked(createStatsDashboard).mockResolvedValue(copy);
    renderBar('/browse?tab=stats&dashboard=d-shared&cat=byt&sort=price_czk', [A, B]);
    expect(await chip('Od Jany — sdílí jana@example.com')).toHaveAccessibleDescription(EDITED);
    await menuFor('Od Jany — sdílí jana@example.com');
    vi.mocked(listStatsDashboards).mockResolvedValue({ data: [...LIST, copy], total: LIST.length + 1 });
    fireEvent.click(screen.getByRole('menuitem', { name: 'Uložit jako vlastní' }));
    await waitFor(() => expect(createStatsDashboard).toHaveBeenCalledTimes(1));
    const input = vi.mocked(createStatsDashboard).mock.calls[0][0];
    expect(input).toMatchObject({ name: 'Od Jany', charts: [A, B], color: null });
    expect(input.filter_spec?.filters.categoryMain).toEqual(['byt']);
    expect(input.filter_spec?.filters.bounds).toBeNull();
    expect(input.filter_spec?.sort).toBe('price_czk');
    await waitFor(() => expect(url().get('dashboard')).toBe('d-copy'));
    expect(log).toEqual([]);
  });

  it('copies a shared dashboard that is not active as stored, and leaves the active one selected', async () => {
    loadedBefore('d-own');
    vi.mocked(createStatsDashboard).mockResolvedValue(row({ id: 'd-copy', name: 'Od Jany', charts: [B] }));
    renderBar('/browse?tab=stats&dashboard=d-own&cat=byt', [A, B]);
    await menuFor('Od Jany — sdílí jana@example.com');
    fireEvent.click(screen.getByRole('menuitem', { name: 'Uložit jako vlastní' }));
    await waitFor(() =>
      expect(createStatsDashboard).toHaveBeenCalledWith({
        name: 'Od Jany',
        charts: [B],
        filter_spec: SHARED_FILTER,
        color: null,
        show_small: false,
      }),
    );
    await tick();
    expect(url().get('dashboard')).toBe('d-own');
    expect(log).toEqual([]);
  });

  it('copies once on a double click (every entry waits while one runs)', async () => {
    vi.mocked(createStatsDashboard).mockReturnValue(new Promise(() => {}));
    renderBar();
    await menuFor('Od Jany — sdílí jana@example.com');
    const copyEntry = screen.getByRole('menuitem', { name: 'Uložit jako vlastní' });
    fireEvent.click(copyEntry);
    fireEvent.click(copyEntry);
    await tick();
    expect(createStatsDashboard).toHaveBeenCalledTimes(1);
    for (const entry of within(screen.getByRole('menu')).getAllByRole('menuitem')) expect(entry).toBeDisabled();
  });
});

describe('<DashboardBar> deleting and leaving', () => {
  const confirmDelete = async (name: string) => {
    await menuFor(name);
    fireEvent.click(screen.getByRole('menuitem', { name: 'Smazat…' }));
    fireEvent.click(screen.getByRole('menuitem', { name: 'Opravdu smazat' }));
  };

  it('deletes an own dashboard behind a confirm', async () => {
    vi.mocked(deleteStatsDashboard).mockResolvedValue({ deleted: true });
    renderBar();
    await chip('Ceny');
    vi.mocked(listStatsDashboards).mockResolvedValue({ data: [WITH_FILTER, SHARED], total: 2 });
    await confirmDelete('Ceny');
    await waitFor(() => expect(deleteStatsDashboard).toHaveBeenCalledWith('d-own'));
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Ceny' })).toBeNull());
  });

  it('keeps the active dashboard in the URL while its delete is pending, and when it is refused', async () => {
    loadedBefore('d-own');
    let refuse!: (e: Error) => void;
    vi.mocked(deleteStatsDashboard).mockReturnValue(new Promise((_res, rej) => (refuse = rej)));
    renderBar('/browse?tab=stats&dashboard=d-own', [A]);
    await confirmDelete('Ceny');
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Ceny' })).toBeNull());
    await tick();
    expect(url().get('dashboard')).toBe('d-own');
    act(() => refuse(new Error('nope')));
    expect(await chip('Ceny')).toBeInTheDocument();
    await tick();
    expect(url().get('dashboard')).toBe('d-own');
    expect(pushToast).toHaveBeenCalledWith('err', 'Dashboard se nepodařilo smazat: nope');
  });

  it('clears the active dashboard once its delete lands', async () => {
    loadedBefore('d-own');
    vi.mocked(deleteStatsDashboard).mockResolvedValue({ deleted: true });
    renderBar('/browse?tab=stats&dashboard=d-own', [A]);
    await chip('Ceny');
    vi.mocked(listStatsDashboards).mockResolvedValue({ data: [WITH_FILTER, SHARED], total: 2 });
    await confirmDelete('Ceny');
    await waitFor(() => expect(url().has('dashboard')).toBe(false));
  });

  it('leaves a shared dashboard with the caller\'s own grant: the chip goes, the active one clears', async () => {
    loadedBefore('d-shared');
    vi.mocked(unshareStatsDashboard).mockResolvedValue({ deleted: true });
    renderBar('/browse?tab=stats&dashboard=d-shared&cat=pozemek', [B]);
    await menuFor('Od Jany — sdílí jana@example.com');
    vi.mocked(listStatsDashboards).mockResolvedValue({ data: [OWN, WITH_FILTER], total: 2 });
    fireEvent.click(screen.getByRole('menuitem', { name: 'Přestat sledovat' }));
    expect(unshareStatsDashboard).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('menuitem', { name: 'Opravdu přestat sledovat' }));
    await waitFor(() => expect(unshareStatsDashboard).toHaveBeenCalledWith('d-shared', 'acct-me'));
    await waitFor(() => expect(screen.queryByRole('button', { name: /^Od Jany/ })).toBeNull());
    await waitFor(() => expect(url().has('dashboard')).toBe(false));
  });
});

describe('<DashboardBar> sharing', () => {
  it('shares from "Sdílet…" through POST and lists the recipient', async () => {
    vi.mocked(shareStatsDashboard).mockResolvedValue({
      ...OWN,
      shared_with: [{ account_id: 'acct-b', email: 'b@example.com' }],
    });
    renderBar();
    await menuFor('Ceny');
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

  it('revokes a recipient from the dialog through DELETE', async () => {
    const sharedOwn = { ...OWN, shared_with: [{ account_id: 'acct-b', email: 'b@example.com' }] };
    vi.mocked(listStatsDashboards).mockResolvedValue({ data: [sharedOwn], total: 1 });
    vi.mocked(unshareStatsDashboard).mockResolvedValue({ deleted: true });
    renderBar();
    await menuFor('Ceny');
    fireEvent.click(screen.getByRole('menuitem', { name: 'Sdílet…' }));
    const dialog = screen.getByRole('dialog', { name: 'Sdílet „Ceny“' });
    vi.mocked(listStatsDashboards).mockResolvedValue({ data: [OWN], total: 1 });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Přestat sdílet s b@example.com' }));
    fireEvent.click(within(dialog).getByRole('button', { name: 'Přestat sdílet' }));
    await waitFor(() => expect(unshareStatsDashboard).toHaveBeenCalledWith('d-own', 'acct-b'));
    expect(await within(dialog).findByText('Zatím s nikým nesdíleno.')).toBeInTheDocument();
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
