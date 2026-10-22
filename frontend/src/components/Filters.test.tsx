/* The Browse sidebar: a location pick lands on the LATEST filters, and the
 * sidebar folds into its rail. The curated-set control has its own test
 * (filter-controls/CuratedSetFilter.test.tsx). */

import { useState } from 'react';
import { describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';

import { FilterSidebar, FilterSidebarRail } from './Filters';
import * as api from '@/lib/api';
import * as brokers from '@/lib/brokers';
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
vi.mock('@/lib/brokers', async (importActual) => ({
  ...(await importActual<typeof import('@/lib/brokers')>()),
  searchBrokersByName: vi.fn(),
  fetchBrokerDossier: vi.fn(),
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

describe('<FilterSidebar> status line', () => {
  it('draws status, seen at birth and dismissed as segmented controls that edit the filters', async () => {
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
    const status = screen.getByRole('group', { name: 'Status' });
    expect(within(status).getByRole('button', { name: 'Vše' })).toHaveAttribute('aria-pressed', 'true');
    await user.click(within(status).getByRole('button', { name: 'Aktivní' }));
    expect(latest.status).toBe('active');

    const born = screen.getByRole('group', { name: 'Seen at birth' });
    expect(within(born).getByRole('button', { name: 'All ads' })).toHaveAttribute('aria-pressed', 'true');
    await user.click(within(born).getByRole('button', { name: 'Seen at birth' }));
    expect(latest.tomObservedOnly).toBe(true);

    const dismissed = screen.getByRole('group', { name: 'Dismissed properties' });
    expect(within(dismissed).getByRole('button', { name: 'Hide dismissed' })).toHaveAttribute('aria-pressed', 'true');
    await user.click(within(dismissed).getByRole('button', { name: 'Show dismissed' }));
    expect(latest.showDismissed).toBe(true);
  });
});

describe('<FilterSidebar> broker', () => {
  it('finds a broker by name in Advanced, scopes the filters to them, and clears the scope', async () => {
    vi.mocked(api.listCollections).mockImplementation(never);
    vi.mocked(api.listTags).mockImplementation(never);
    vi.mocked(brokers.searchBrokersByName).mockResolvedValue([
      { broker_id: 527, display_name: 'Jana Nováková', firm_name: 'RE/MAX Alfa', firm_domain: 're-max.cz', cz_active_property_count: 41 } as brokers.BrokerPublic,
    ]);
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
    await user.click(screen.getByRole('button', { name: 'Advanced' }));
    await user.type(await screen.findByRole('textbox', { name: 'Broker' }), 'Nová');
    await user.click(await screen.findByRole('button', { name: /Jana Nováková/ }));
    expect(latest.brokerId).toBe(527);
    expect(latest.categoryType).toBe(DEFAULT_FILTERS.categoryType);

    await user.click(await screen.findByRole('button', { name: 'Remove broker Jana Nováková' }));
    expect(latest.brokerId).toBeNull();
    expect(brokers.fetchBrokerDossier).not.toHaveBeenCalled();
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
    await user.type(screen.getByRole('textbox', { name: 'Location' }), 'Na Ryb');
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

describe('<FilterSidebar> collapse', () => {
  const renderSidebar = (onCollapse?: () => void) => {
    vi.mocked(api.listCollections).mockImplementation(never);
    vi.mocked(api.listTags).mockImplementation(never);
    return renderPicker(
      <MemoryRouter>
        <FilterSidebar filters={DEFAULT_FILTERS} onChange={() => {}} onCollapse={onCollapse} />
      </MemoryRouter>,
    );
  };

  it('offers "Hide filters" only to a caller that can fold it', () => {
    const onCollapse = vi.fn();
    const { unmount } = renderSidebar(onCollapse);
    fireEvent.click(screen.getByRole('button', { name: 'Hide filters' }));
    expect(onCollapse).toHaveBeenCalledTimes(1);
    unmount();

    renderSidebar();
    expect(screen.queryByRole('button', { name: 'Hide filters' })).toBeNull();
  });
});

describe('<FilterSidebarRail>', () => {
  it('brings the filters back on a click', () => {
    const onExpand = vi.fn();
    render(<FilterSidebarRail active={false} onExpand={onExpand} />);
    fireEvent.click(screen.getByRole('button', { name: 'Show filters' }));
    expect(onExpand).toHaveBeenCalledTimes(1);
  });

  it('marks that a filter still narrows the results only when one does', () => {
    const { rerender } = render(<FilterSidebarRail active={false} onExpand={() => {}} />);
    expect(screen.queryByRole('img', { name: 'Filters active' })).toBeNull();
    rerender(<FilterSidebarRail active onExpand={() => {}} />);
    expect(screen.getByRole('img', { name: 'Filters active' })).toBeInTheDocument();
  });
});
