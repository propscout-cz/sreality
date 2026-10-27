/* Nové (#1766): the roll is Browse's read — `fetchListingsForCards` with the
 * scope's filters and Browse's newest-first sort — cut into Prague days under
 * "Dnes HH:MM" / "Včera" / dated rules; a preset chip swaps the cohort, a
 * portal chip narrows it, "Skryté" switches to the hidden properties, and the
 * Filtry sheet edits the view, which lives in the URL as Browse's does. */
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { usePhoneHeaderAside } from '@/components/phone/PhoneShell';
import * as api from '@/lib/api';
import { DEFAULT_FILTERS, type ListingFilters } from '@/lib/filters';
import { FEED_ALL_FILTERS, FEED_QUERY_KEY, FEED_SCOPE_KEY, feedSearch } from '@/lib/feed';
import * as queries from '@/lib/queries';
import type { CardRow, DismissedCardRow, PipelineMembers } from '@/lib/queries';
import type { FilterPreset } from '@/lib/types';

import Feed from './Feed';

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  listFilterPresets: vi.fn(),
}));
vi.mock('@/components/phone/PhoneShell', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/components/phone/PhoneShell')>()),
  usePhoneHeaderAside: vi.fn(),
}));
vi.mock('@/lib/pgRead', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/pgRead')>()),
  pgRead: vi.fn(() => new Promise<never>(() => {})),
}));
vi.mock('@/lib/queries', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/queries')>();
  return {
    ...actual,
    fetchBrowseCount: vi.fn(),
    fetchListingsForCards: vi.fn(),
    fetchDismissedCards: vi.fn(),
    fetchDismissedCount: vi.fn(),
    fetchPipelineMembers: vi.fn(),
    fetchPipelineStages: vi.fn(),
    fetchPropertyCollectionMemberSet: vi.fn(),
    fetchIsDismissed: vi.fn(),
    fetchImagesForListingIds: vi.fn(),
  };
});

const row = (id: number, first_seen_at: string, extra: Partial<CardRow> = {}): CardRow => ({
  property_id: id,
  listing_id: id * 10,
  lat: 50.08,
  lng: 14.43,
  sreality_id: null,
  display_label: `Místo ${id}`,
  disposition: '2+kk',
  subtype: null,
  area_m2: 50,
  price_czk: 5_000_000 + id,
  price_per_m2: 100_000,
  price_per_m2_basis: 'sale_capital_czk_m2',
  first_seen_at,
  last_seen_at: first_seen_at,
  is_active: true,
  tom_days: 1,
  category_main: 'byt',
  category_type: 'prodej',
  source: 'sreality',
  source_id_native: String(id),
  mf_gross_yield_pct: null,
  plot_area_m2: null,
  total_price_change_pct: null,
  price_change_count: 0,
  all_sources: ['sreality'],
  active_sources: ['sreality'],
  source_count: 1,
  ...extra,
});

const PRESET: FilterPreset = {
  id: 'vys',
  name: 'Vysočany byty',
  filter_spec: { filters: { ...DEFAULT_FILTERS, categoryType: 'prodej', dispositions: ['2+kk'] }, sort: '-price_czk' },
  created_at: '2026-10-01T00:00:00Z',
  updated_at: '2026-10-01T00:00:00Z',
  position: 0,
  color: 'sage',
  group_id: null,
};

beforeEach(() => {
  /* 14:32 in Prague on 12 Oct 2026; only Date is faked, so React Query's
   * timers still run. */
  vi.useFakeTimers({ toFake: ['Date'] });
  vi.setSystemTime(new Date('2026-10-12T12:32:00Z'));
  localStorage.clear();
  window.scrollTo = vi.fn() as unknown as typeof window.scrollTo;
  vi.mocked(api.listFilterPresets).mockResolvedValue({ data: [PRESET], total: 1, groups: [] });
  vi.mocked(queries.fetchListingsForCards).mockResolvedValue({
    rows: [
      row(1, '2026-10-12T09:00:00Z'),
      row(2, '2026-10-11T22:30:00Z'), // 00:30 on the 12th in Prague
      row(3, '2026-10-11T08:00:00Z'),
      row(4, '2026-10-05T08:00:00Z'),
    ],
    nextCursor: null,
  });
  vi.mocked(queries.fetchDismissedCards).mockResolvedValue({
    rows: [{ ...row(9, '2026-09-01T08:00:00Z'), dismissed_at: '2026-10-12T07:00:00Z' } as DismissedCardRow],
    nextCursor: null,
  });
  vi.mocked(queries.fetchDismissedCount).mockResolvedValue(6);
  vi.mocked(queries.fetchBrowseCount).mockResolvedValue(1234);
  vi.mocked(queries.fetchPipelineMembers).mockResolvedValue(new Map() as PipelineMembers);
  vi.mocked(queries.fetchPipelineStages).mockResolvedValue([]);
  vi.mocked(queries.fetchPropertyCollectionMemberSet).mockResolvedValue(new Map());
  vi.mocked(queries.fetchIsDismissed).mockResolvedValue(false);
  vi.mocked(queries.fetchImagesForListingIds).mockResolvedValue(new Map());
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

function renderFeed(path = '/feed') {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[path]}>
        <Feed />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const lastFilters = (): ListingFilters => {
  const calls = vi.mocked(queries.fetchListingsForCards).mock.calls;
  return calls[calls.length - 1][0];
};

const lastAside = (): string | null => {
  const calls = vi.mocked(usePhoneHeaderAside).mock.calls;
  return calls[calls.length - 1][0];
};

/* The preset's own filters as the feed reads them, two steps further. */
const PRESET_FILTERS: ListingFilters = { ...DEFAULT_FILTERS, categoryType: 'prodej', dispositions: ['2+kk'] };
const feedPath = (filters: ListingFilters, extra: Record<string, string> = {}, presetId: string | null = 'vys') => {
  const sp = feedSearch({ filters, sort: queries.DEFAULT_SORT, presetId });
  for (const [k, v] of Object.entries(extra)) sp.set(k, v);
  return `/feed?${sp.toString()}`;
};

describe('<Feed>', () => {
  it("reads Browse's cards, newest first, over the whole market under Vše", async () => {
    renderFeed();
    await screen.findByText('Místo 1', { exact: false });
    const [filters, sort, cursor] = vi.mocked(queries.fetchListingsForCards).mock.calls[0];
    expect(filters).toEqual(FEED_ALL_FILTERS);
    expect(sort).toEqual(queries.DEFAULT_SORT);
    expect(cursor).toBeNull();
  });

  it('cuts the roll into Prague days under Dnes HH:MM, Včera and a date', async () => {
    renderFeed();
    await screen.findByText('Místo 1', { exact: false });
    const rules = ['Dnes 14:32', 'Včera', '5. 10.'];
    for (const r of rules) expect(screen.getByText(r)).toBeInTheDocument();
    /* 00:30 Prague on the 12th is today, though it is the 11th in UTC. */
    const text = document.body.textContent ?? '';
    expect(text.indexOf('Místo 2')).toBeLessThan(text.indexOf('Včera'));
    expect(text.indexOf('Místo 3')).toBeGreaterThan(text.indexOf('Včera'));
    expect(text.indexOf('Místo 4')).toBeGreaterThan(text.indexOf('5. 10.'));
  });

  it("swaps to a preset's cohort and remembers the choice on this device", async () => {
    renderFeed();
    await userEvent.click(await screen.findByRole('button', { name: 'Vysočany byty' }));
    await waitFor(() => expect(lastFilters().dispositions).toEqual(['2+kk']));
    expect(lastFilters().categoryType).toBe('prodej');
    expect(localStorage.getItem(FEED_SCOPE_KEY)).toBe('vys');
    expect(screen.getByRole('button', { name: 'Vysočany byty' })).toHaveAttribute('aria-pressed', 'true');
    /* The preset's saved sort is not the feed's: newest first, always. */
    const calls = vi.mocked(queries.fetchListingsForCards).mock.calls;
    expect(calls[calls.length - 1][1]).toEqual(queries.DEFAULT_SORT);
  });

  it('opens on the remembered preset', async () => {
    localStorage.setItem(FEED_SCOPE_KEY, 'vys');
    renderFeed();
    await waitFor(() => expect(queries.fetchListingsForCards).toHaveBeenCalled());
    expect(lastFilters().dispositions).toEqual(['2+kk']);
  });

  it('narrows to a portal with a chip', async () => {
    renderFeed();
    const portals = await screen.findByRole('group', { name: 'Portály' });
    await userEvent.click(within(portals).getByRole('button', { name: 'Bazoš' }));
    await waitFor(() => expect(lastFilters().portals).toEqual(['bazos']));
  });

  it('a second tap excludes the portal; then only its active ads can count', async () => {
    renderFeed();
    const portals = await screen.findByRole('group', { name: 'Portály' });
    await userEvent.click(within(portals).getByRole('button', { name: 'Sreality' }));
    await userEvent.click(within(portals).getByRole('button', { name: 'Sreality' }));
    await waitFor(() => expect(lastFilters().portalsExcluded).toEqual(['sreality']));
    expect(lastFilters().portals).toEqual([]);
    expect(within(portals).getByRole('button', { name: 'Sreality (vyloučeno)' }).textContent)
      .toBe('−Sreality');
    await userEvent.click(within(portals).getByRole('button', { name: 'jen aktivní' }));
    await waitFor(() => expect(lastFilters().portalsExcludedActiveOnly).toBe(true));
  });

  it('switches to the hidden properties only with Skryté', async () => {
    renderFeed();
    await screen.findByText('Místo 1', { exact: false });
    const chip = screen.getByRole('button', { name: /Skryté/ });
    expect(chip.textContent).toContain('6');
    await userEvent.click(chip);
    expect(await screen.findByText('Místo 9', { exact: false })).toBeInTheDocument();
    expect(screen.queryByText('Místo 1', { exact: false })).toBeNull();
    expect(screen.getByText('Skryto dnes')).toBeInTheDocument();
    expect(queries.fetchDismissedCards).toHaveBeenCalled();
  });

  it('says so when the scope is empty', async () => {
    vi.mocked(queries.fetchListingsForCards).mockResolvedValue({ rows: [], nextCursor: null });
    renderFeed();
    expect(await screen.findByText('V tomhle výběru nic není.')).toBeInTheDocument();
  });

  it("opens a Browse link's scope, marking the preset it has left and counting what it added", async () => {
    renderFeed(feedPath({ ...PRESET_FILTERS, priceMax: 6_000_000, hasLift: 'yes' }));
    await waitFor(() => expect(lastFilters().priceMax).toBe(6_000_000));
    expect(lastFilters().dispositions).toEqual(['2+kk']);
    const chip = await screen.findByRole('button', { name: 'Vysočany byty (upraveno)' });
    expect(chip).toHaveAttribute('aria-pressed', 'true');
    expect(chip.textContent).toBe('Vysočany byty· (upraveno)');
    expect(screen.getByRole('button', { name: 'Filtry (2 navíc)' })).toBeInTheDocument();
    /* Past the preset, the header counts the scope rather than today. */
    await waitFor(() => expect(lastAside()).toBe('1\u00a0234 nemovitostí'));
    expect(localStorage.getItem(FEED_QUERY_KEY)).toContain('price=');
  });

  it('marks the preset left under a lens it does not store, like any filter (ruling 2026-10-22)', async () => {
    renderFeed(feedPath({ ...PRESET_FILTERS, pipeline: { set: 'in', in: [], out: [] } }));
    expect(await screen.findByRole('button', { name: 'Vysočany byty (upraveno)' })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByRole('button', { name: 'Filtry (1 navíc)' })).toBeInTheDocument();
  });

  it('reads an unmodified scope with no marks and remembers only the scope', async () => {
    renderFeed(feedPath(PRESET_FILTERS));
    await screen.findByText('Místo 1', { exact: false });
    expect(screen.getByRole('button', { name: 'Vysočany byty' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Filtry' })).toBeInTheDocument();
    await waitFor(() => expect(localStorage.getItem(FEED_SCOPE_KEY)).toBe('vys'));
    expect(localStorage.getItem(FEED_QUERY_KEY)).toBeNull();
  });

  it('reopens a modified view remembered on this device from a bare /feed', async () => {
    localStorage.setItem(FEED_QUERY_KEY, 'cat=&deal=any&status=active&disposition=3%2Bkk');
    renderFeed();
    await waitFor(() => expect(lastFilters().dispositions).toEqual(['3+kk']));
    expect(lastFilters().categoryType).toBeNull();
  });

  it('draws another order as one roll under its name, with the total in the header', async () => {
    renderFeed(feedPath(FEED_ALL_FILTERS, { sort: 'price_czk' }, null));
    await screen.findByText('Místo 1', { exact: false });
    const calls = vi.mocked(queries.fetchListingsForCards).mock.calls;
    expect(calls[calls.length - 1][1]).toEqual({ field: 'price_czk', direction: 'asc' });
    expect(screen.getByText('Price: low → high · 14:32')).toBeInTheDocument();
    expect(screen.queryByText('Včera')).toBeNull();
    await waitFor(() => expect(lastAside()).toBe('1\u00a0234 nemovitostí'));
  });

  it('opens the Filtry sheet and applies its draft to the roll', async () => {
    const user = userEvent.setup();
    renderFeed();
    await screen.findByText('Místo 1', { exact: false });
    await user.click(screen.getByRole('button', { name: 'Filtry' }));
    /* The sheet is a chunk of its own: a loading sheet holds its place first. */
    expect(await screen.findByRole('dialog', { name: 'Filtry' })).toBeInTheDocument();
    await user.click(await screen.findByRole('button', { name: /^Disposition/ }, { timeout: 5000 }));
    const sheet = screen.getByRole('dialog', { name: 'Filtry' });
    await user.click(within(sheet).getByRole('button', { name: '3+kk' }));
    await user.click(await within(sheet).findByRole('button', { name: 'Zobrazit 1\u00a0234 nemovitostí' }));
    await waitFor(() => expect(lastFilters().dispositions).toEqual(['3+kk']));
    expect(screen.queryByRole('dialog', { name: 'Filtry' })).toBeNull();
    expect(screen.getByRole('button', { name: 'Vše (upraveno)' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Filtry (1 navíc)' })).toBeInTheDocument();
  });

  it('offers a retry when the read fails', async () => {
    vi.mocked(queries.fetchListingsForCards).mockRejectedValue(new Error('57014'));
    renderFeed();
    expect(await screen.findByText('Nové se nepodařilo načíst.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Zkusit znovu' })).toBeInTheDocument();
  });
});
