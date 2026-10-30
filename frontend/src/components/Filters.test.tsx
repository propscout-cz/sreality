/* The Browse sidebar: a location pick lands on the LATEST filters, and the
 * sidebar folds into its rail. The curated-set control has its own test
 * (filter-controls/CuratedSetFilter.test.tsx). */

import { useState } from 'react';
import { describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';

import { FilterSidebar, FilterSidebarRail, IncludeNoPriceToggle } from './Filters';
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

/* The circle Browse would place: the sidebar asks for it, it does not compute it. */
const CIRCLE = { lat: 50.08, lng: 14.42, radius_m: 1400 };
const PLACE = () => CIRCLE;

describe('<FilterSidebar> status line', () => {
  it('draws status, seen at birth and dismissed as segmented controls that edit the filters', async () => {
    vi.mocked(api.listCollections).mockImplementation(never);
    vi.mocked(api.listTags).mockImplementation(never);
    let latest: ListingFilters = DEFAULT_FILTERS;
    function Host() {
      const [filters, setFilters] = useState<ListingFilters>(DEFAULT_FILTERS);
      return (
        <FilterSidebar placeCircle={PLACE}
          onReset={() => {}}
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

const CHOSEN = ['bg-[var(--color-copper)]', 'text-[var(--color-on-copper)]'];

/* Every segment in one track, none a bordered chip of its own (SegmentedBar). */
function expectJoined(buttons: HTMLElement[]) {
  const track = buttons[0].parentElement!;
  for (const b of buttons) {
    expect(b.parentElement).toBe(track);
    expect(b).not.toHaveClass('border');
  }
  expect(track).toHaveClass('border', 'border-[var(--color-rule)]', 'bg-[var(--color-paper-2)]');
}

/* Every pick-one is the one joined bar (operator ruling 2026-10-27). */
describe('<FilterSidebar> pick-one bars', () => {
  function Host({ onChange, initial = DEFAULT_FILTERS }: { onChange: (f: ListingFilters) => void; initial?: ListingFilters }) {
    const [filters, setFilters] = useState<ListingFilters>(initial);
    return (
      <FilterSidebar placeCircle={PLACE}
        filters={filters}
        onReset={() => {}}
        onChange={(next) => {
          onChange(next);
          setFilters(next);
        }}
      />
    );
  }
  const renderHost = (onChange: (f: ListingFilters) => void, initial?: ListingFilters) => {
    vi.mocked(api.listCollections).mockImplementation(never);
    vi.mocked(api.listTags).mockImplementation(never);
    return renderPicker(
      <MemoryRouter>
        <Host onChange={onChange} initial={initial} />
      </MemoryRouter>,
    );
  };

  /* No Vše (ruling 2026-10-20): the lit deal releases to "no deal", which lights nothing. */
  it('draws Listing for as a joined bar whose lit deal releases', async () => {
    let latest: ListingFilters = DEFAULT_FILTERS;
    renderHost((f) => (latest = f));
    const user = userEvent.setup();
    const deal = screen.getByRole('group', { name: 'Listing for' });
    const deals = within(deal).getAllByRole('button');
    expectJoined(deals);
    expect(within(deal).getByRole('button', { name: 'Prodej' })).toHaveClass(...CHOSEN);
    await user.click(within(deal).getByRole('button', { name: 'Prodej' }));
    expect(latest.categoryType).toBeNull();
    for (const b of within(deal).getAllByRole('button')) expect(b).toHaveAttribute('aria-pressed', 'false');
    await user.click(within(deal).getByRole('button', { name: 'Pronájem' }));
    expect(latest.categoryType).toBe('pronajem');
  });

  it('draws With estimates as the two-segment bar', async () => {
    let latest: ListingFilters = DEFAULT_FILTERS;
    renderHost((f) => (latest = f));
    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: 'Curation' }));
    const bar = screen.getByRole('group', { name: 'With estimates' });
    expectJoined(within(bar).getAllByRole('button'));
    expect(within(bar).getByRole('button', { name: 'All properties' })).toHaveClass(...CHOSEN);
    await user.click(within(bar).getByRole('button', { name: 'With estimates' }));
    expect(latest.withEstimates).toBe(true);
  });

  it('draws the map filter mode as a joined bar', async () => {
    const onChange = vi.fn();
    vi.mocked(api.listCollections).mockImplementation(never);
    vi.mocked(api.listTags).mockImplementation(never);
    renderPicker(
      <MemoryRouter>
        <FilterSidebar placeCircle={PLACE} filters={DEFAULT_FILTERS} onChange={onChange} onReset={() => {}} />
      </MemoryRouter>,
    );
    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: 'Advanced' }));
    const mode = screen.getByRole('group', { name: 'Map filter' });
    const segs = within(mode).getAllByRole('button');
    expect(segs.map((b) => b.textContent)).toEqual(['Map viewport', 'Centre + radius']);
    expectJoined(segs);
    expect(segs[0]).toHaveClass(...CHOSEN);
    expect(segs[0].parentElement).toHaveClass('grid', 'w-full');
    await user.click(segs[1]);
    expect(onChange).toHaveBeenLastCalledWith({ ...DEFAULT_FILTERS, bounds: null, circle: CIRCLE });
  });

  /* Operator ruling 2026-10-30: the centre lives on the main map (placed and dragged there);
   * the sidebar keeps only the radius. No small map, no lat / lng boxes, no Clear. */
  it('keeps only the radius for a circle, in the slider domain, and switches back to the viewport', async () => {
    const onChange = vi.fn();
    vi.mocked(api.listCollections).mockImplementation(never);
    vi.mocked(api.listTags).mockImplementation(never);
    const filters = { ...DEFAULT_FILTERS, circle: CIRCLE };
    renderPicker(
      <MemoryRouter>
        <FilterSidebar placeCircle={PLACE} filters={filters} onChange={onChange} onReset={() => {}} />
      </MemoryRouter>,
    );
    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: 'Advanced' }));
    expect(screen.queryByLabelText('latitude')).toBeNull();
    expect(screen.queryByRole('button', { name: 'Clear spatial filter' })).toBeNull();
    fireEvent.change(screen.getByRole('slider', { name: 'Radius' }), { target: { value: '2500' } });
    expect(onChange).toHaveBeenLastCalledWith({ ...filters, bounds: null, circle: { ...CIRCLE, radius_m: 2500 } });
    const metres = screen.getByRole('textbox', { name: 'Radius in metres' });
    await user.clear(metres);
    await user.type(metres, '50000');
    expect(onChange).toHaveBeenLastCalledWith({ ...filters, bounds: null, circle: { ...CIRCLE, radius_m: 10_000 } });
    const mode = screen.getByRole('group', { name: 'Map filter' });
    await user.click(within(mode).getByRole('button', { name: 'Map viewport' }));
    expect(onChange).toHaveBeenLastCalledWith({ ...filters, bounds: null, circle: null });
  });
});

/* A lone on/off is the two-segment bar (ruling 2026-10-27); with no price bound
 * the choice does not apply, so the bar is off and lights nothing. */
describe('<IncludeNoPriceToggle>', () => {
  it('is a two-segment bar, disabled with nothing lit until a price bound is set', async () => {
    const onChange = vi.fn();
    const { rerender } = renderPicker(<IncludeNoPriceToggle filters={DEFAULT_FILTERS} onChange={onChange} />);
    const bar = screen.getByRole('group', { name: 'Include listings without a price' });
    const segs = within(bar).getAllByRole('button');
    expect(segs.map((b) => b.textContent)).toEqual(['Priced only', 'Include unpriced']);
    expectJoined(segs);
    for (const b of segs) {
      expect(b).toBeDisabled();
      expect(b).toHaveAttribute('aria-pressed', 'false');
    }

    const bounded = { ...DEFAULT_FILTERS, priceMax: 5_000_000 };
    rerender(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <IncludeNoPriceToggle filters={bounded} onChange={onChange} />
      </QueryClientProvider>,
    );
    const [priced, unpriced] = within(screen.getByRole('group', { name: 'Include listings without a price' }))
      .getAllByRole('button');
    expect(priced).toBeEnabled();
    expect(priced).toHaveClass(...CHOSEN);
    fireEvent.click(unpriced);
    expect(onChange).toHaveBeenLastCalledWith({ ...bounded, includeNoPrice: true });
  });
});

describe('<FilterSidebar> broker', () => {
  it('finds a broker by name in Advanced, scopes the filters to them, and clears the scope', async () => {
    vi.mocked(api.listCollections).mockImplementation(never);
    vi.mocked(api.listTags).mockImplementation(never);
    vi.mocked(brokers.searchBrokersByName).mockResolvedValue([
      { broker_id: 527, display_name: 'Jana Nováková', firm_name: 'RE/MAX Alfa', firm_domain: 're-max.cz', active_properties: 41 } as brokers.BrokerPublic,
    ]);
    let latest: ListingFilters = DEFAULT_FILTERS;
    function Host() {
      const [filters, setFilters] = useState<ListingFilters>(DEFAULT_FILTERS);
      return (
        <FilterSidebar placeCircle={PLACE}
          onReset={() => {}}
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
        <FilterSidebar placeCircle={PLACE}
          onReset={() => {}}
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
        <FilterSidebar placeCircle={PLACE} filters={DEFAULT_FILTERS} onChange={() => {}} onReset={() => {}} onCollapse={onCollapse} />
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

describe('<FilterSidebar> reset', () => {
  it('hands Reset to its own callback, never to onChange', () => {
    vi.mocked(api.listCollections).mockImplementation(never);
    vi.mocked(api.listTags).mockImplementation(never);
    const onChange = vi.fn();
    const onReset = vi.fn();
    renderPicker(
      <MemoryRouter>
        <FilterSidebar placeCircle={PLACE} filters={{ ...DEFAULT_FILTERS, priceMax: 5_000_000 }} onChange={onChange} onReset={onReset} />
      </MemoryRouter>,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Reset' }));
    expect(onReset).toHaveBeenCalledTimes(1);
    expect(onChange).not.toHaveBeenCalled();
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
