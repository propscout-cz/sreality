/* The Browse sidebar: its two curation pickers (MS16) say a failed read failed
 * and read again, and a location pick lands on the LATEST filters. */

import { useState } from 'react';
import { describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';

import { CollectionsPicker, FilterSidebar } from './Filters';
import { AccountTagFilter } from './filter-controls/TagFilterControl';
import * as api from '@/lib/api';
import { DEFAULT_FILTERS, type ListingFilters } from '@/lib/filters';
import type { LocationResolution } from '@/lib/maps';

const never = vi.hoisted(() => () => new Promise<never>(() => {}));
const fetchSuggest = vi.hoisted(() => vi.fn());
const resolveSuggestion = vi.hoisted(() => vi.fn());
vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  listCollections: vi.fn(),
  listTags: vi.fn(),
}));
vi.mock('@/lib/maps', async (importActual) => ({
  ...(await importActual<typeof import('@/lib/maps')>()),
  fetchSuggest,
  resolveSuggestion,
}));
vi.mock('@/lib/queries', async (importActual) => ({
  ...(await importActual<typeof import('@/lib/queries')>()),
  fetchBrowseCount: vi.fn(never),
  fetchNoPriceCount: vi.fn(never),
  fetchDismissedCount: vi.fn(never),
}));
vi.mock('@/lib/pgRead', () => ({ pgRead: vi.fn(never) }));

const renderPicker = (ui: React.ReactElement) =>
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      {ui}
    </QueryClientProvider>,
  );

describe('Browse sidebar curation pickers', () => {
  it('says the collections failed instead of "none yet", and reads them again', async () => {
    vi.mocked(api.listCollections)
      .mockRejectedValueOnce(new Error('HTTP 500'))
      .mockResolvedValue({ data: [{ id: 7, name: 'Šortlist' }], total: 1 } as never);
    renderPicker(<CollectionsPicker value={null} onChange={() => {}} />);
    const retry = await screen.findByRole('button', { name: /Kolekce se nepodařilo načíst/ });
    expect(screen.queryByText(/No collections yet/)).toBeNull();
    fireEvent.click(retry);
    expect(await screen.findByRole('button', { name: 'Šortlist' })).toBeInTheDocument();
  });

  /* Ruling 3: one picker, one `collections` value; collections shared with me
   * are their own group, labelled with their owner. */
  it('groups mine and shared-with-me over one value, and keeps a vanished pick clearable', async () => {
    const base = {
      description: null, created_at: '2026-10-01T00:00:00Z', updated_at: '2026-10-01T00:00:00Z',
      listing_count: 1, monitoring_enabled: false, notify_channels: [], is_system: false,
    };
    vi.mocked(api.listCollections).mockResolvedValue({
      data: [
        { ...base, id: 7, name: 'Šortlist', owner_email: null, shared_with: [] },
        { ...base, id: 9, name: 'monitoring', owner_email: 'petr@example.cz',
          shared_with: [{ account_id: 'me', email: null }] },
      ],
      total: 2,
    });
    const onChange = vi.fn();
    renderPicker(<CollectionsPicker value={[9, 404]} onChange={onChange} />);

    const mine = await screen.findByRole('group', { name: 'Mine' });
    const shared = screen.getByRole('group', { name: 'Shared with me' });
    expect(within(mine).getByRole('button', { name: 'Šortlist' })).toBeInTheDocument();
    const picked = within(shared).getByRole('button', { name: 'monitoring · petr@example.cz' });
    expect(picked).toHaveAttribute('aria-pressed', 'true');

    // A pick the list no longer holds (revoked, deleted) stays visible and clears.
    const gone = within(screen.getByRole('group', { name: 'No longer available' }))
      .getByRole('button', { name: '#404' });
    fireEvent.click(gone);
    expect(onChange).toHaveBeenLastCalledWith([9]);

    // Both groups write the ONE value array.
    fireEvent.click(within(mine).getByRole('button', { name: 'Šortlist' }));
    expect(onChange).toHaveBeenLastCalledWith([9, 404, 7]);
  });

  it('draws no group headings while nothing is shared with me', async () => {
    vi.mocked(api.listCollections).mockResolvedValue({
      data: [{ id: 7, name: 'Šortlist', owner_email: null, shared_with: [] }],
      total: 1,
    } as never);
    renderPicker(<CollectionsPicker value={null} onChange={() => {}} />);
    expect(await screen.findByRole('button', { name: 'Šortlist' })).toBeInTheDocument();
    expect(screen.queryByRole('group', { name: 'Mine' })).toBeNull();
  });

  it('says the tags failed instead of "none yet", and reads them again', async () => {
    vi.mocked(api.listTags)
      .mockRejectedValueOnce(new Error('HTTP 500'))
      .mockResolvedValue({ data: [{ id: 3, name: 'k prohlídce', color: 'copper' }] } as never);
    renderPicker(<AccountTagFilter value={{ tags: [], tagsExcluded: [] }} onChange={() => {}} />);
    const retry = await screen.findByRole('button', { name: /Štítky se nepodařilo načíst/ });
    expect(screen.queryByText(/No tags yet/)).toBeNull();
    fireEvent.click(retry);
    expect(await screen.findByRole('button', { name: 'k prohlídce' })).toBeInTheDocument();
  });
});

describe('<FilterSidebar> location pick', () => {
  it('keeps a sidebar change made while the pick was resolving', async () => {
    vi.mocked(api.listCollections).mockImplementation(never);
    vi.mocked(api.listTags).mockImplementation(never);
    let latest: ListingFilters = DEFAULT_FILTERS;
    function Host() {
      const [filters, setFilters] = useState<ListingFilters>(DEFAULT_FILTERS);
      return (
        <FilterSidebar
          filters={filters}
          onChange={(next) => {
            latest = next;
            setFilters(next);
          }}
        />
      );
    }
    renderPicker(
      <MemoryRouter>
        <Host />
      </MemoryRouter>,
    );
    const user = userEvent.setup();

    let settle!: (r: LocationResolution) => void;
    resolveSuggestion.mockReturnValue(new Promise<LocationResolution>((r) => { settle = r; }));
    fetchSuggest.mockResolvedValue([{
      name: 'Na Rybníčkách', label: 'Ulice', type: 'regional.street',
      position: { lon: 15.77, lat: 50.03 },
    }]);
    await user.type(screen.getByRole('textbox', { name: 'District' }), 'Na Ryb');
    await user.click(within(await screen.findByRole('listbox')).getByRole('button'));

    /* While the pick resolves, narrow the cohort to 2+kk. */
    await user.click(screen.getByRole('button', { name: '2+kk' }));
    await waitFor(() => expect(latest.dispositions).toEqual(['2+kk']));

    await act(async () =>
      settle({ level: 'ulice', id: 666742, name: 'Na Rybníčkách', context: 'Pardubice' }),
    );
    expect(latest.dispositions).toEqual(['2+kk']);
    expect(latest.districts).toEqual([
      { name: 'Na Rybníčkách', context: 'Pardubice', level: 'ulice', id: 666742 },
    ]);
  });
});
