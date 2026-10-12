/* Nové (#1766): the roll is Browse's read — `fetchListingsForCards` with the
 * scope's filters and Browse's newest-first sort — cut into Prague days under
 * "Dnes HH:MM" / "Včera" / dated rules; a preset chip swaps the cohort, a
 * portal chip narrows it, and "Skryté" switches to the hidden properties. */
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import * as api from '@/lib/api';
import { DEFAULT_FILTERS, type ListingFilters } from '@/lib/filters';
import { FEED_ALL_FILTERS, FEED_SCOPE_KEY } from '@/lib/feed';
import * as queries from '@/lib/queries';
import type { CardRow, DismissedCardRow, PipelineMembers } from '@/lib/queries';
import type { FilterPreset } from '@/lib/types';

import Feed from './Feed';

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  listFilterPresets: vi.fn(),
}));
vi.mock('@/lib/queries', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/queries')>();
  return {
    ...actual,
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
};

beforeEach(() => {
  /* 14:32 in Prague on 12 Oct 2026; only Date is faked, so React Query's
   * timers still run. */
  vi.useFakeTimers({ toFake: ['Date'] });
  vi.setSystemTime(new Date('2026-10-12T12:32:00Z'));
  localStorage.clear();
  window.scrollTo = vi.fn() as unknown as typeof window.scrollTo;
  vi.mocked(api.listFilterPresets).mockResolvedValue({ data: [PRESET], total: 1 });
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

function renderFeed() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={['/feed']}>
        <Feed />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const lastFilters = (): ListingFilters => {
  const calls = vi.mocked(queries.fetchListingsForCards).mock.calls;
  return calls[calls.length - 1][0];
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

  it('offers a retry when the read fails', async () => {
    vi.mocked(queries.fetchListingsForCards).mockRejectedValue(new Error('57014'));
    renderFeed();
    expect(await screen.findByText('Nové se nepodařilo načíst.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Zkusit znovu' })).toBeInTheDocument();
  });
});
