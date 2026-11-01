/* One portal's "Newest first" (MS19) as the Browse page wires it: the header names the
 * order and each card shows its date on that portal beside its own first seen. Which
 * filters turn it on is queries.test.ts's (orderPortal); these pin the wiring from the
 * view to the header and to the cards. Also the card's "N inzeráty" badge, from the same
 * read, and the Cover pick (#1726) beside them. The reads are stubbed and the map is
 * collapsed (tests never render a live map). */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';

import BrowseExperience, { type BrowseFeatures } from './BrowseExperience';
import { NewPropertyProvider } from './NewPropertyProvider';
import { useMemoryBrowseState } from '@/lib/browseState';
import { familyTargets } from '@/lib/curationSync';
import { revalidate } from '@/lib/revalidate';
import { DEFAULT_FILTERS, type ListingFilters } from '@/lib/filters';
import * as api from '@/lib/api';
import * as auth from '@/lib/auth';
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
vi.mock('@/components/brokers/BrokerBoard', () => ({
  default: ({ filters, board }: { filters: ListingFilters; board: { horizonDays: number } }) => (
    <div data-testid="broker-board" data-horizon={board.horizonDays}>
      {filters.portals.join(',')}
    </div>
  ),
}));
/* Who is signed in: the "Nová nemovitost" provider (admins only, v1) and the Makléři tab's agenda gate. */
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
  /* The header chip names the portal; the card keeps the day of its newest ad
     there and names no portal (P01's note, 2026-10-27). */
  it('names the order in the header and dates each card on that portal', async () => {
    renderBrowse(['idnes']);
    const chip = await screen.findByText('newest on iDNES Reality');
    expect(chip.getAttribute('title')).toMatch(
      /^Newest first follows iDNES Reality: each property is placed by when its newest iDNES Reality ad was first seen/,
    );
    expect(await lifespan()).toContain(`·od${fmtShortDate(IDNES_DAY)}`);
    expect(await lifespan()).not.toContain('iDNES');
  });

  it('names "Oldest first" the same way', async () => {
    renderBrowse(['idnes'], { field: 'first_seen_at', direction: 'asc' });
    expect(await screen.findByText('oldest on iDNES Reality')).toBeInTheDocument();
  });

  it('keeps the property\'s own order and dates with two portals', async () => {
    renderBrowse(['idnes', 'sreality']);
    expect(await lifespan()).not.toContain(fmtShortDate(IDNES_DAY));
    expect(screen.queryByText(/^(newest|oldest) on /)).toBeNull();
  });
});

/* Ruling Q3(c) with the pointer hold (2026-11-01, docs/design/curation-sync.md
 * Decision 7b): a dismissal on another device moves the count at once and the
 * cards once the pointer leaves them (lib/useListHold.test.tsx has every case). */
describe('<BrowseExperience> a change made elsewhere', () => {
  afterEach(() => vi.mocked(queries.fetchBrowseCount).mockResolvedValue(1));

  it('re-reads the count at once, and the cards only once the pointer leaves them', async () => {
    const OTHER = { ...ROW, property_id: 43, listing_id: 112, sreality_id: 901, display_label: 'Lipová 3, Praha' };
    vi.mocked(queries.fetchListingsForCards).mockResolvedValue({ rows: [ROW, OTHER], nextCursor: null });
    vi.mocked(queries.fetchBrowseCount).mockResolvedValue(2);
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={qc}>
        <MemoryRouter>
          <Host filters={DEFAULT_FILTERS} sort={DEFAULT_SORT} />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    const card = await screen.findByText('Sadová 12, Praha');
    await waitFor(() => expect(screen.getByText('2 of 2')).toBeInTheDocument());
    fireEvent.pointerOver(card);
    const cards = vi.mocked(queries.fetchListingsForCards).mock.calls.length;

    vi.mocked(queries.fetchListingsForCards).mockResolvedValue({ rows: [OTHER], nextCursor: null });
    vi.mocked(queries.fetchBrowseCount).mockResolvedValue(1);
    await act(async () => {
      void revalidate(qc, familyTargets(qc, 'dismissals', null));
    });
    await waitFor(() => expect(screen.getByText('2 of 1')).toBeInTheDocument());
    expect(queries.fetchListingsForCards).toHaveBeenCalledTimes(cards);
    expect(screen.getByText('Sadová 12, Praha')).toBeInTheDocument();

    fireEvent.pointerLeave(card);
    await waitFor(() => expect(screen.queryByText('Sadová 12, Praha')).toBeNull());
    expect(screen.getByText('1 of 1')).toBeInTheDocument();
  });
});

/* Ruling P09 (2026-10-27): the header keeps the count and what is counted; every
 * filter beyond the defaults is a removable pill under it, × resetting that one. */
describe('<BrowseExperience> the header', () => {
  const headline = () =>
    screen.getByText((_, el) => el?.tagName === 'P' && /apartments for sale$/.test(el.textContent ?? ''));
  const pills = () => screen.queryByRole('list', { name: 'Applied filters' });
  afterEach(() => vi.mocked(queries.fetchBrowseCount).mockResolvedValue(1));

  it('says the count and what is counted, and the filters as pills under it', async () => {
    vi.mocked(queries.fetchBrowseCount).mockResolvedValue(12_345);
    renderBrowse(['idnes']);
    const count = (12_345).toLocaleString('cs-CZ');
    await waitFor(() => expect(headline().textContent).toBe(`${count} apartments for sale`));
    expect(within(headline()).getByText(/^12\s345$/).className).toContain('tabular-nums');
    expect(screen.queryByText(/^Showing/)).toBeNull();

    fireEvent.click(within(pills()!).getByRole('button', { name: 'Source portal: on iDNES' }));
    await waitFor(() => expect(pills()).toBeNull());
    expect(queries.fetchBrowseCount).toHaveBeenLastCalledWith(
      expect.objectContaining({ portals: [] }),
      expect.anything(),
    );
  });

  /* The map sets its area on the first pan: the line the pill lands on is kept
   * on the defaults too, so the tabs, cards and map do not jump down under it. */
  it('keeps the pills\' line when no filter is set', async () => {
    renderBrowse([]);
    await waitFor(() => expect(headline().textContent).toBe('1 apartments for sale'));
    expect(pills()).toBeNull();
    expect(screen.getByTestId('applied-filters-row').className).toMatch(/min-h-/);
  });
});

/* Ruling P10 (2026-10-27): "Sloučit…" beside Cover and Sort above the cards opens
 * the merge bar in its row under the tabs; Cancel brings the button back. */
describe('<BrowseExperience> merge mode', () => {
  function MergeHost() {
    const view = useMemoryBrowseState({ filters: DEFAULT_FILTERS, sort: DEFAULT_SORT, tab: 'map' });
    return (
      <BrowseExperience
        view={view}
        layout="modal"
        features={{ presetBar: false, watchdog: false, mergeMode: true, title: false, sidebar: false }}
      />
    );
  }

  it('is entered from the cards header, not the tab row', async () => {
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <MemoryRouter>
          <MergeHost />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    const enter = await screen.findByRole('button', { name: 'Sloučit…' });
    const sort = screen.getByRole('combobox', { name: /Sort/ });
    expect(enter.parentElement).toBe(sort.closest('label')!.parentElement);
    expect(screen.queryByRole('button', { name: 'Merge mode' })).toBeNull();

    fireEvent.click(enter);
    const letters = screen.getByRole('group', { name: 'Písmeno, které zapíše kliknutí' });
    expect(letters).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Sloučit…' })).toBeNull();
    // Its own row under the tab strip's rule, never on the strip.
    expect(screen.getByRole('tablist', { name: 'Zobrazení výsledků' }).parentElement).not.toContainElement(letters);

    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(screen.getByRole('button', { name: 'Sloučit…' })).toBeInTheDocument();
    expect(screen.queryByRole('group', { name: 'Písmeno, které zapíše kliknutí' })).toBeNull();
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
 * with the same card's ad count and newest-ad date still on it (the card names no
 * portal: P01's note, 2026-10-27). */
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
    const card = screen.getByTitle(/^Aktivní/).closest('li')!;
    expect(within(card).getByText(counterIs('3 / 4'))).toBeInTheDocument();
    expect(within(card).queryByTitle(/^Portály/)).toBeNull();
    expect(within(card).getByTitle(/^Nemovitost spojuje 2 inzeráty/)).toHaveTextContent(/^2\s*inzeráty$/);
    expect(within(card).getByTitle(/^Aktivní/).textContent).toContain(`·od${fmtShortDate(IDNES_DAY)}`);
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

  /* Ruling S07 (2026-10-27): these two labels only; every other English word stays. */
  it('names its tabs Nemovitosti and Statistiky', async () => {
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <MemoryRouter>
          <StatsHost filters={DEFAULT_FILTERS} />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    expect(await screen.findByRole('tab', { name: 'Statistiky' })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByRole('tab', { name: /^Nemovitosti/ })).toHaveAttribute('aria-selected', 'false');
    expect(screen.queryByRole('tab', { name: /^(Stats|Properties)/ })).toBeNull();
  });
});

/* The Makléři tab: the board over Browse's own filters, gated as the /brokers
 * nav entry is (the plan's `brokers` agenda), hidden with Statistiky by a scoped surface. */
describe('<BrowseExperience> Makléři tab', () => {
  function BoardHost({ features }: { features?: BrowseFeatures }) {
    const view = useMemoryBrowseState({ filters: { ...DEFAULT_FILTERS, portals: ['idnes'] }, sort: DEFAULT_SORT, tab: 'brokers' });
    return (
      <BrowseExperience
        view={view}
        layout="modal"
        features={{ presetBar: false, watchdog: false, mergeMode: false, title: false, sidebar: false, ...features }}
      />
    );
  }
  const renderAs = (who: { isAdmin: boolean; agendas?: Record<string, boolean> }, features?: BrowseFeatures) => {
    vi.mocked(auth.useAuth).mockReturnValue(who as unknown as ReturnType<typeof auth.useAuth>);
    return render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <MemoryRouter>
          <BoardHost features={features} />
        </MemoryRouter>
      </QueryClientProvider>,
    );
  };
  const tabNames = () => screen.getAllByRole('tab').map((t) => t.textContent?.replace(/\d/g, '').trim());
  afterEach(() => vi.mocked(auth.useAuth).mockReturnValue({ isAdmin: true } as ReturnType<typeof auth.useAuth>));

  it('names the tab Makléři after Statistiky for a plan with the agenda and renders the board of the current filters', async () => {
    renderAs({ isAdmin: false, agendas: { brokers: true } });
    expect(tabNames()).toEqual(['Nemovitosti', 'Statistiky', 'Makléři']);
    const board = await screen.findByTestId('broker-board');
    expect(board).toHaveTextContent('idnes');
    expect(board).toHaveAttribute('data-horizon', '30');
    expect(screen.getByRole('tabpanel')).toHaveAttribute('aria-labelledby', screen.getByRole('tab', { name: 'Makléři' }).id);
  });

  it.each([
    ['a plan without the agenda, a ?tab=brokers link opening the Properties tab', { isAdmin: false, agendas: { brokers: false } }, undefined, ['Nemovitosti', 'Statistiky']],
    ['a surface that turns the aggregate tabs off', { isAdmin: true }, { stats: false }, ['Nemovitosti']],
  ])('is not shown to %s', (_, who, features, tabs) => {
    renderAs(who, features);
    expect(tabNames()).toEqual(tabs);
    expect(screen.getByRole('tab', { name: /^Nemovitosti/ })).toHaveAttribute('aria-selected', 'true');
    expect(screen.queryByTestId('broker-board')).toBeNull();
  });
});

/* The tab-strip ruling (2026-10-27, option A): one strip whose rule runs under the
 * whole row, the toggles at its right end above the rule, one count pill, arrow keys. */
describe('<BrowseExperience> tab strip', () => {
  it('puts Split | Cards and the image size on the tab strip, above its rule', async () => {
    renderBrowse([]);
    const list = await screen.findByRole('tablist', { name: 'Zobrazení výsledků' });
    const strip = list.parentElement!;
    expect(strip.className.split(' ')).toEqual(expect.arrayContaining(['border-b', 'border-[var(--color-rule)]']));
    for (const name of ['Properties layout', 'Card image size']) {
      const group = screen.getByRole('group', { name });
      expect(strip).toContainElement(group);
      expect(list).not.toContainElement(group);
    }

    fireEvent.click(screen.getByRole('tab', { name: 'Statistiky' }));
    expect(screen.queryByRole('group', { name: 'Properties layout' })).toBeNull();
    expect(screen.queryByRole('group', { name: 'Card image size' })).toBeNull();
  });

  /* Split | Cards is the one joined bar (ruling 2026-10-27), drawn as its
   * neighbour the image size is: hugging, at the reading size. */
  it('draws Split | Cards as the hugging joined bar beside the image size', async () => {
    renderBrowse([]);
    const layout = await screen.findByRole('group', { name: 'Properties layout' });
    const size = screen.getByRole('group', { name: 'Card image size' });
    expect(layout.className).toBe(size.className);
    expect((layout.firstElementChild as HTMLElement).className).toBe((size.firstElementChild as HTMLElement).className);
    const split = within(layout).getByRole('button', { name: 'Split' });
    const cards = within(layout).getByRole('button', { name: 'Cards' });
    expect(split.parentElement).toBe(layout.firstElementChild);
    expect(split).toHaveAttribute('title', 'Show the map beside the cards');
    expect(split.querySelector('svg')).not.toBeNull();
    expect(split).toHaveClass('shrink-0', 'px-2', 'py-1', 'text-xs');
    /* The suite starts with the map folded away (no map engine in jsdom). */
    expect(split).toHaveAttribute('aria-pressed', 'false');
    expect(cards).toHaveAttribute('aria-pressed', 'true');
    expect(cards).toHaveClass('bg-[var(--color-copper)]', 'text-[var(--color-on-copper)]');
    expect(cards).toHaveAttribute('title', 'Hide the map — full-width cards');
  });

  it('shows the cohort total in the tab’s count pill', async () => {
    vi.mocked(queries.fetchBrowseCount).mockResolvedValueOnce(12_345);
    renderBrowse([]);
    const tab = await screen.findByRole('tab', { name: /^Nemovitosti/ });
    const pill = await within(tab).findByText(/^12\s345$/);
    expect(pill.className.split(' ')).toEqual(expect.arrayContaining(['rounded-full', 'bg-[var(--color-inset)]']));
  });

  it('switches to Statistiky with ArrowRight, focus following, each tab naming its panel', async () => {
    renderBrowse([]);
    const cards = await screen.findByRole('tab', { name: /^Nemovitosti/ });
    expect(document.getElementById(cards.getAttribute('aria-controls')!)).toHaveAttribute('role', 'tabpanel');

    fireEvent.keyDown(cards, { key: 'ArrowRight' });
    const stats = screen.getByRole('tab', { name: 'Statistiky' });
    expect(stats).toHaveAttribute('aria-selected', 'true');
    expect(document.activeElement).toBe(stats);
    const panel = document.getElementById(stats.getAttribute('aria-controls')!);
    expect(panel).toHaveAttribute('role', 'tabpanel');
    expect(panel).toHaveAttribute('aria-labelledby', stats.id);
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
