/* One portal's "Newest first" (MS19) as the Browse page wires it: the header names the
 * order and each card shows its date on that portal beside its own first seen. Which
 * filters turn it on is queries.test.ts's (orderPortal); these pin the wiring from the
 * view to the header and to the cards. Also the card's "N inzeráty" badge, from the same
 * read, and the Cover pick (#1726) beside them. The reads are stubbed and the map is
 * collapsed (tests never render a live map). */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';

import BrowseExperience, { type BrowseFeatures } from './BrowseExperience';
import { NewPropertyProvider } from './NewPropertyProvider';
import { useMemoryBrowseState } from '@/lib/browseState';
import { DEFAULT_FILTERS, type ListingFilters } from '@/lib/filters';
import * as api from '@/lib/api';
import { fmtShortDate } from '@/lib/format';
import * as queries from '@/lib/queries';
import { DEFAULT_SORT, type CardRow, type SortSpec } from '@/lib/queries';
import type { ImagePublic } from '@/lib/types';

vi.mock('@/lib/queries', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/queries')>()),
  fetchListingsForCards: vi.fn(),
  fetchBrowseCount: vi.fn(async () => 1),
  fetchImagesForListingIds: vi.fn(async () => new Map()),
  fetchListingCovers: vi.fn(async () => new Map()),
  fetchPropertyCollectionMemberSet: vi.fn(async () => new Map()),
  fetchPipelineMembers: vi.fn(async () => new Map()),
  fetchPipelineStages: vi.fn(async () => []),
  fetchIsDismissed: vi.fn(async () => false),
  fetchNoteCounts: vi.fn(async () => new Map()),
}));
vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  listCollections: vi.fn(() => new Promise<never>(() => {})),
  listTags: vi.fn(() => new Promise<never>(() => {})),
  createEstimation: vi.fn(),
  latestEstimationsByListing: vi.fn(async () => ({})),
}));
vi.mock('@/lib/pgRead', () => ({ pgRead: vi.fn(() => new Promise<never>(() => {})) }));
/* The Stats tab's board has its own tests (components/stats); here only the wiring. */
vi.mock('@/components/stats/StatsDashboard', () => ({
  default: ({ filters, view, market }: { filters: ListingFilters; view?: unknown; market?: { value: string } }) => (
    <div data-testid="stats-dashboard" data-view={view ? 'yes' : 'no'} data-market={market?.value ?? ''}>
      {filters.portals.join(',')}
    </div>
  ),
}));
/* Only the "Nová nemovitost" provider asks who is signed in (admins only, v1). */
vi.mock('@/lib/auth', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/auth')>()),
  useAuth: vi.fn(() => ({ isAdmin: true })),
}));
vi.mock('@/lib/brokers', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/brokers')>()),
  fetchListingBrokersByIds: vi.fn(async () => new Map()),
}));

const MAP_COLLAPSED = 'sreality.browse.mapCollapsed';
const IDNES_DAY = '2026-03-05T10:00:00Z';
const ROW = {
  property_id: 42,
  listing_id: 111,
  sreality_id: 900,
  price_czk: 5_400_000,
  area_m2: 62,
  display_label: 'Sadová 12, Praha',
  disposition: '2+kk',
  first_seen_at: '2026-01-01T00:00:00Z',
  last_seen_at: '2026-01-02T00:00:00Z',
  is_active: true,
  tom_days: 3,
  category_main: 'byt',
  category_type: 'prodej',
  source: 'sreality',
  source_id_native: '900',
  mf_gross_yield_pct: null,
  total_price_change_pct: null,
  price_change_count: null,
  all_sources: ['idnes', 'sreality'],
  active_sources: ['idnes', 'sreality'],
  source_count: 2,
  newest_ad_at_idnes: IDNES_DAY,
} as unknown as CardRow;

function Host({
  filters,
  sort,
  sidebar = false,
}: {
  filters: ListingFilters;
  sort: SortSpec;
  sidebar?: boolean;
}) {
  const view = useMemoryBrowseState({ filters, sort, tab: 'map' });
  return (
    <BrowseExperience
      view={view}
      layout="modal"
      features={{ presetBar: false, watchdog: false, mergeMode: false, title: false, sidebar }}
    />
  );
}

const renderBrowse = (portals: string[], sort: SortSpec = DEFAULT_SORT) =>
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter>
        <Host filters={{ ...DEFAULT_FILTERS, portals }} sort={sort} />
      </MemoryRouter>
    </QueryClientProvider>,
  );

const lifespan = async () => (await screen.findByTitle(/^Aktivní/)).textContent;

beforeEach(() => {
  localStorage.setItem(MAP_COLLAPSED, '1');
  vi.mocked(queries.fetchListingsForCards).mockResolvedValue({ rows: [ROW], nextCursor: null });
});
afterEach(() => localStorage.removeItem(MAP_COLLAPSED));

describe('<BrowseExperience> one portal\'s "Newest first"', () => {
  it('names the order in the header and dates each card on that portal', async () => {
    renderBrowse(['idnes']);
    const chip = await screen.findByText('newest on iDNES Reality');
    expect(chip.getAttribute('title')).toMatch(
      /^Newest first follows iDNES Reality: each property is placed by when its newest iDNES Reality ad was first seen/,
    );
    expect(await lifespan()).toContain(`·iDNESod${fmtShortDate(IDNES_DAY)}`);
  });

  it('names "Oldest first" the same way', async () => {
    renderBrowse(['idnes'], { field: 'first_seen_at', direction: 'asc' });
    expect(await screen.findByText('oldest on iDNES Reality')).toBeInTheDocument();
  });

  it('keeps the property\'s own order and dates with two portals', async () => {
    renderBrowse(['idnes', 'sreality']);
    expect(await lifespan()).not.toContain('iDNES');
    expect(screen.queryByText(/^(newest|oldest) on /)).toBeNull();
  });
});

/* The badge reads the property's ad count off the cards read (`source_count`,
 * migration 590): the page wires no second read for it. */
describe('<BrowseExperience> the cards\' ad counts', () => {
  it('draws "3 inzeráty" from the cards read', async () => {
    vi.mocked(queries.fetchListingsForCards).mockResolvedValue({
      rows: [{ ...ROW, source_count: 3 } as CardRow],
      nextCursor: null,
    });
    renderBrowse([]);

    const badge = await screen.findByTitle(/^Nemovitost spojuje 3 inzeráty/);
    expect(badge).toHaveTextContent(/^3\s*inzeráty$/);
  });
});

/* The Cover dropdown beside Sort (#1726) as the page wires it: the pick is this
 * browser's own (`sreality.browse.cardCoverTag`) and opens each card on that photo,
 * with the same card's portal badge, ad count and portal date still on it. */
describe('<BrowseExperience> the cards\' cover photo', () => {
  const COVER_KEY = 'sreality.browse.cardCoverTag';
  const tagged = (id: number, clip_fine_tag: string) =>
    ({ id, sreality_url: `https://img/${id}.jpg`, storage_path: null, clip_fine_tag,
       clip_confidence: 0.9, tag_head_scores: null }) as unknown as ImagePublic;
  const counterIs = (want: string) => (_t: string, el: Element | null) =>
    el != null && el.children.length === 0 && el.textContent?.trim() === want;

  beforeEach(() => {
    vi.mocked(queries.fetchImagesForListingIds).mockResolvedValue(
      new Map([[111, [tagged(1, 'exterior_facade'), tagged(2, 'hallway'), tagged(3, 'kitchen'), tagged(4, 'bedroom')]]]),
    );
  });
  afterEach(() => {
    localStorage.removeItem(COVER_KEY);
    vi.mocked(queries.fetchImagesForListingIds).mockResolvedValue(new Map());
  });

  it('opens the card on the picked photo and keeps the pick for this browser', async () => {
    renderBrowse(['idnes']);
    expect(await screen.findByText(counterIs('1 / 4'))).toBeInTheDocument();

    fireEvent.change(screen.getByRole('combobox', { name: /Cover/ }), { target: { value: 'kitchen' } });

    expect(await screen.findByText(counterIs('3 / 4'))).toBeInTheDocument();
    expect(localStorage.getItem(COVER_KEY)).toBe('kitchen');
    const card = screen.getByTitle(/^Portály/).closest('li')!;
    expect(within(card).getByText(counterIs('3 / 4'))).toBeInTheDocument();
    expect(within(card).getByTitle(/^Portály/).textContent).toBe('iDNES Reality · Sreality');
    expect(within(card).getByTitle(/^Nemovitost spojuje 2 inzeráty/)).toHaveTextContent(/^2\s*inzeráty$/);
    expect(within(card).getByTitle(/^Aktivní/).textContent).toContain(`·iDNESod${fmtShortDate(IDNES_DAY)}`);
  });

  it('opens on the stored pick when the page loads', async () => {
    localStorage.setItem(COVER_KEY, 'kitchen');
    renderBrowse([]);
    expect(await screen.findByText(counterIs('3 / 4'))).toBeInTheDocument();
  });
});

describe('<BrowseExperience> the filters column', () => {
  const SIDEBAR_COLLAPSED = 'sreality.browse.sidebarCollapsed';
  const renderWithSidebar = () =>
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <MemoryRouter>
          <Host filters={DEFAULT_FILTERS} sort={DEFAULT_SORT} sidebar />
        </MemoryRouter>
      </QueryClientProvider>,
    );
  afterEach(() => localStorage.removeItem(SIDEBAR_COLLAPSED));

  it('folds to a rail and unfolds again, and remembers the fold for this browser', async () => {
    const first = renderWithSidebar();
    expect(await screen.findByRole('heading', { name: 'Filters' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Show filters' })).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: 'Hide filters' }));
    expect(screen.queryByRole('heading', { name: 'Filters' })).toBeNull();
    expect(screen.queryByRole('separator', { name: /Resize the filters sidebar/ })).toBeNull();
    expect(localStorage.getItem(SIDEBAR_COLLAPSED)).toBe('1');
    first.unmount();

    renderWithSidebar();
    fireEvent.click(await screen.findByRole('button', { name: 'Show filters' }));
    expect(await screen.findByRole('heading', { name: 'Filters' })).toBeInTheDocument();
    expect(localStorage.getItem(SIDEBAR_COLLAPSED)).toBe('0');
  });
});

/* The Stats tab is the account's chart board over the page's filters — one
 * component, no stats read of the page's own. */
describe('<BrowseExperience> Stats tab', () => {
  function StatsHost({ filters }: { filters: ListingFilters }) {
    const view = useMemoryBrowseState({ filters, sort: DEFAULT_SORT, tab: 'stats' });
    return (
      <BrowseExperience
        view={view}
        layout="modal"
        features={{ presetBar: false, watchdog: false, mergeMode: false, title: false, sidebar: false }}
      />
    );
  }

  it('renders <StatsDashboard> with the current filters', async () => {
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <MemoryRouter>
          <StatsHost filters={{ ...DEFAULT_FILTERS, portals: ['idnes'] }} />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    const board = await screen.findByTestId('stats-dashboard');
    expect(board).toHaveTextContent('idnes');
    /* The modal layout (no preset bar) still hands over the view and the lens: one Stats tab everywhere. */
    expect(board).toHaveAttribute('data-view', 'yes');
    expect(board).toHaveAttribute('data-market', 'filter');
  });
});

/* "Nová nemovitost" beside "+ Create watchdog" (docs/design/new-property-from-link.md
 * §3.6): outlined, not a second copper primary, and a page feature — both Explore
 * modals turn it off. */
describe('<BrowseExperience> Nová nemovitost', () => {
  function FeatureHost({ features }: { features: BrowseFeatures }) {
    const view = useMemoryBrowseState({ filters: DEFAULT_FILTERS, sort: DEFAULT_SORT, tab: 'map' });
    return <BrowseExperience view={view} layout="modal" features={features} />;
  }
  const renderWith = (features: BrowseFeatures) =>
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <MemoryRouter>
          <NewPropertyProvider>
            <FeatureHost features={features} />
          </NewPropertyProvider>
        </MemoryRouter>
      </QueryClientProvider>,
    );
  const OFF = { presetBar: false, watchdog: true, mergeMode: false, title: false, sidebar: false };

  it('sits beside the watchdog CTA, outlined, and opens the dialog', async () => {
    renderWith(OFF);
    const button = await screen.findByRole('button', { name: 'Nová nemovitost' });
    expect(button.className).not.toContain('bg-[var(--color-copper)]');
    expect(screen.getByRole('button', { name: '+ Create watchdog' })).toBeInTheDocument();
    fireEvent.click(button);
    expect(await screen.findByRole('dialog', { name: 'Nová nemovitost' })).toBeInTheDocument();
  });

  it('is absent when the surface turns the feature off', async () => {
    renderWith({ ...OFF, newProperty: false });
    expect(await screen.findByRole('button', { name: '+ Create watchdog' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Nová nemovitost' })).toBeNull();
  });
});

/* A submit the API refuses (an ad it cannot place, docs/design/new-property-from-link.md
 * §3.5) is a 422 with no run, so the card cannot learn of it from the runs read: the
 * refusal itself has to stay on the card, or the click looks like it did nothing. */
describe('<BrowseExperience> the on-card estimate', () => {
  it("keeps a refused submit on the card, titled with the API's sentence", async () => {
    const said = 'sreality ad 900 needs a location before it can be estimated';
    vi.mocked(api.createEstimation).mockRejectedValue(new api.ApiError(said, 422, { detail: said }));
    renderBrowse([]);

    fireEvent.click(await screen.findByRole('button', { name: 'Odhad' }));

    const refused = await screen.findByRole('button', { name: 'Odhad ↻' });
    expect(refused).toHaveAttribute('title', said);
    expect(api.createEstimation).toHaveBeenCalledWith(
      expect.objectContaining({ sreality_id: 900 }),
    );
  });
});
