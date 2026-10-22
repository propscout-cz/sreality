/* The property page (decision 11): the header is the property as its Browse
 * card shows it, the merged-adverts section is the only advert list, and every
 * old advert address lands here with that advert's row open. Plus the freshness
 * affordance, the broker list (MS7), the lowest active price (MS8) and every
 * advert's price moves (MS10).
 */

import { describe, expect, it, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';

import PropertyDetail, { AdvertRedirect, FreshnessBlock } from './PropertyDetail';
import * as api from '@/lib/api';
import * as auth from '@/lib/auth';
import * as brokers from '@/lib/brokers';
import * as queries from '@/lib/queries';
import { fmtCzk } from '@/lib/format';
import { act } from '@testing-library/react';
import { getActiveBoard, resetActiveBoard, setActiveBoard, setEditAsOwner } from '@/lib/acting';
import { PHONE_SHELL_QUERY } from '@/lib/phoneShell';
import type { PipelineShare } from '@/lib/types';
import type { ListingPublic, PropertySource } from '@/lib/types';

/* The curation reads (header controls, curation block) answer empty, so every
   page test sees the normal header, not their failed-read retries (MS16). */
vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>();
  const empty = async () => ({ data: [], total: 0 });
  return {
    ...actual,
    verifyListingFreshness: vi.fn(),
    fetchPropertyOrigins: vi.fn(),
    listCollections: vi.fn(empty),
    listTags: vi.fn(empty),
    listPropertyNotes: vi.fn(empty),
    listPipelineShares: vi.fn(async () => ({ shared_with: [], shared_with_me: [] })),
    fetchArtifactLinks: vi.fn(async () => new Map()),
    addPipelineCard: vi.fn(async () => ({ property_id: 774, stage_key: 'review', added: true })),
  };
});

const verifyMock = vi.mocked(api.verifyListingFreshness);

function renderBlock(qc: QueryClient) {
  return render(
    <QueryClientProvider client={qc}>
      <FreshnessBlock sreality_id={123} checks={[]} />
    </QueryClientProvider>,
  );
}

function makeResult(
  outcome: api.FreshnessOutcome,
  whatChanged: string[] = [],
): api.VerifyFreshnessResult {
  return {
    data: {
      sreality_id: 123,
      outcome,
      verified: outcome !== 'cached',
      cached: outcome === 'cached',
      age_hours: 0,
      what_changed: whatChanged,
      snapshot_id: outcome === 'updated' ? 999 : null,
      current: null,
    },
    metadata: {
      tool: 'verify_listing_freshness',
      filters_used: { sreality_id: 123, max_age_hours: 0 },
      result_count: 1,
      queried_at: '2026-05-28T00:00:00Z',
      data_freshness: '2026-05-28T00:00:00Z',
    },
  };
}

describe('<FreshnessBlock> verify button', () => {
  beforeEach(() => {
    verifyMock.mockReset();
  });

  it('renders the verify button and empty-log copy', () => {
    renderBlock(new QueryClient());
    expect(
      screen.getByRole('button', { name: 'Ověřit aktuálnost' }),
    ).toBeInTheDocument();
    expect(
      screen.getByText('No on-demand freshness checks recorded.'),
    ).toBeInTheDocument();
  });

  it('calls the API, surfaces an "updated" outcome, and invalidates queries', async () => {
    let resolve!: (v: api.VerifyFreshnessResult) => void;
    verifyMock.mockReturnValue(
      new Promise<api.VerifyFreshnessResult>((r) => {
        resolve = r;
      }),
    );

    const qc = new QueryClient();
    const invalidateSpy = vi.spyOn(qc, 'invalidateQueries');
    renderBlock(qc);

    fireEvent.click(
      screen.getByRole('button', { name: 'Ověřit aktuálnost' }),
    );

    // The mutation runs async; the call + pending UI land after a tick.
    await waitFor(() => expect(verifyMock).toHaveBeenCalledWith(123));
    expect(
      screen.getByRole('button', { name: 'Ověřuji…' }),
    ).toBeDisabled();
    expect(
      screen.getByText('Re-fetching the listing from the source…'),
    ).toBeInTheDocument();

    resolve(makeResult('updated', ['price_czk']));

    await waitFor(() =>
      expect(
        screen.getByText(/Still listed — updated: price_czk\./),
      ).toBeInTheDocument(),
    );

    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['freshness', 123] });
    // FreshnessBlock knows only the sreality_id, so the page's property and
    // snapshot reads are invalidated by their bare prefix.
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['property'] });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['snapshots'] });
  });

  it('surfaces a "gone" outcome', async () => {
    verifyMock.mockResolvedValue(makeResult('gone'));
    renderBlock(new QueryClient());

    fireEvent.click(
      screen.getByRole('button', { name: 'Ověřit aktuálnost' }),
    );

    await waitFor(() =>
      expect(
        screen.getByText('No longer listed — marked inactive.'),
      ).toBeInTheDocument(),
    );
  });
});

/* -------------------------------------------------------------------------- */
/* The property page and the old addresses                                    */
/* -------------------------------------------------------------------------- */

vi.mock('@/lib/queries', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/queries')>();
  return {
    ...actual,
    fetchProperty: vi.fn(),
    fetchAdvertProperty: vi.fn(),
    fetchPropertySources: vi.fn(async () => []),
    fetchSnapshotsForListings: vi.fn(async () => []),
    fetchFreshnessChecksByListing: vi.fn(async () => []),
    fetchImagesByListing: vi.fn(async () => []),
    fetchListingsForListingIds: vi.fn(async () => new Map()),
    fetchImagesForListingIds: vi.fn(async () => new Map()),
    fetchPipelineMembers: vi.fn(async () => new Map()),
    fetchPipelineStages: vi.fn(async () => []),
    fetchPropertyCollectionMemberSet: vi.fn(async () => new Map()),
    fetchPropertyTagIds: vi.fn(async () => []),
    fetchIsDismissed: vi.fn(async () => false),
    fetchPropertyTagMemberSet: vi.fn(async () => new Map()),
  };
});
vi.mock('@/lib/auth', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/auth')>()),
  useAuth: vi.fn(() => ({ isAdmin: false })),
}));
/* Only the network wrapper is stubbed — propertyBrokers/contactState/prettyPhone
   stay REAL, because the list's whole point is the rule and states they encode. */
vi.mock('@/lib/brokers', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/brokers')>()),
  fetchListingBrokersByIds: vi.fn(async () => new Map()),
}));
const openEstimation = vi.hoisted(() => vi.fn());
vi.mock('@/components/NewEstimationModal', () => ({
  useNewEstimationModal: () => ({ open: openEstimation }),
}));
vi.mock('@/components/listing-detail/ValuoSection', () => ({
  default: ({ adUrls }: { adUrls: (string | null)[] }) => <div data-testid="valuo-ads">{JSON.stringify(adUrls)}</div>,
}));
vi.mock('@/components/ExploreAreaModal', () => ({
  useExploreAreaModal: () => ({ open: vi.fn() }),
}));
const openNewProperty = vi.hoisted(() => vi.fn());
vi.mock('@/components/NewPropertyProvider', () => ({
  useNewPropertyDialog: () => ({ open: openNewProperty }),
}));

/* The property as properties_public answers it: its canonical advert is 105054
   (sreality), and every advert field is that advert's. */
const PROPERTY = {
  id: 105054,
  property_id: 774,
  sreality_id: 999,
  first_seen_at: '2025-11-01T00:00:00Z',
  last_seen_at: '2026-01-02T00:00:00Z',
  is_active: true,
  source: 'sreality',
  source_id_native: '999',
  source_url: null,
  category_main: 'byt',
  category_type: 'prodej',
  price_czk: 5_000_000,
  area_m2: 54,
  disposition: '2+kk',
  display_label: 'Kolbenova, Praha 9',
  tom_days: 62,
  price_change_count: 2,
  total_price_change_pct: -4.5,
} as unknown as queries.PropertyPublic;

const SOURCES: PropertySource[] = [
  {
    property_id: 774,
    id: 105053,
    sreality_id: -11876,
    source: 'idnes',
    source_url: 'https://reality.idnes.cz/detail/x/',
    source_id_native: '6a147cfde222cf687509e018',
    is_active: true,
    price_czk: 5_200_000,
    first_seen_at: '2025-11-01T00:00:00Z',
    last_seen_at: '2026-01-02T00:00:00Z',
  },
  {
    property_id: 774,
    id: 105054,
    sreality_id: 999,
    source: 'sreality',
    source_url: 'https://www.sreality.cz/detail/y',
    source_id_native: '999',
    is_active: true,
    price_czk: 5_000_000,
    first_seen_at: '2025-12-01T00:00:00Z',
    last_seen_at: '2026-01-02T00:00:00Z',
  },
];

function Where() {
  const l = useLocation();
  return <p data-testid="where">{l.pathname + l.search + l.hash}</p>;
}

function renderAt(path: string) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="property/:propertyId" element={<PropertyDetail />} />
          <Route path="listing" element={<AdvertRedirect />} />
          <Route path="listing/:source/:nativeId" element={<AdvertRedirect />} />
          <Route path="listing/:sreality_id" element={<AdvertRedirect />} />
        </Routes>
        <Where />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function renderAtState(pathname: string, state: unknown) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[{ pathname, state }]}>
        <Routes>
          <Route path="listing/:source/:nativeId" element={<AdvertRedirect />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const where = () => screen.getByTestId('where').textContent;
const rowToggle = (portal: string) =>
  within(screen.getAllByText(portal)[0].closest('li') as HTMLElement).getAllByRole('button')[0];

beforeEach(() => {
  vi.mocked(auth.useAuth).mockReturnValue({ isAdmin: false } as ReturnType<typeof auth.useAuth>);
  vi.mocked(queries.fetchProperty).mockReset();
  vi.mocked(queries.fetchProperty).mockResolvedValue(PROPERTY);
  vi.mocked(queries.fetchAdvertProperty).mockReset();
  vi.mocked(queries.fetchPropertySources).mockResolvedValue(SOURCES);
  vi.mocked(queries.fetchImagesByListing).mockClear();
  vi.mocked(queries.fetchSnapshotsForListings).mockReset().mockResolvedValue([]);
  vi.mocked(queries.fetchListingsForListingIds).mockResolvedValue(new Map());
});

describe('<PropertyDetail> one property, one voice', () => {
  it('heads the page with the property as its Browse card shows it — the canonical advert', async () => {
    renderAt('/property/774');

    const h1 = await screen.findByRole('heading', { level: 1 });
    // The canonical advert's price, never the other portal's 5.2M.
    expect(h1.textContent).toContain(fmtCzk(5_000_000));
    expect(screen.getByText('Kolbenova, Praha 9')).toBeInTheDocument();
    expect(queries.fetchProperty).toHaveBeenCalledWith(774, expect.anything());
    // Photos are the canonical advert's own; the chart reads every advert's.
    await waitFor(() => expect(queries.fetchImagesByListing).toHaveBeenCalledWith(105054, expect.anything()));
    await waitFor(() =>
      expect(queries.fetchSnapshotsForListings).toHaveBeenCalledWith([105053, 105054], expect.anything()));
    // The price moves are the property's, as Browse filters on them — on the overview's card.
    // The overview is its own chunk: the first test to open it waits for the import.
    const stat = async (label: string) =>
      (await screen.findByText(label, {}, { timeout: 5000 })).parentElement?.nextSibling;
    expect(await stat('Změny ceny')).toHaveTextContent('2');
    expect(await stat('Na trhu')).toHaveTextContent('62 dní');
    // The curation controls read, so the header offers them, never a retry.
    expect(await screen.findByRole('button', { name: /Přidat do pipeline/ })).toBeInTheDocument();
    expect(await screen.findByRole('button', { name: 'Uložit do kolekce' })).toBeInTheDocument();
  });

  it('hands the Valuo section the link of every advert, a delisted one too (its own-ad tag needs them)', async () => {
    vi.mocked(queries.fetchPropertySources).mockResolvedValue([SOURCES[0], { ...SOURCES[1], is_active: false }]);
    renderAt('/property/774?tab=hodnota');

    const ads = await screen.findByTestId('valuo-ads');
    expect(JSON.parse(ads.textContent ?? '[]')).toEqual(SOURCES.map((s) => s.source_url));
  });

  it('charts the canonical advert alone when the advert list cannot be read', async () => {
    vi.mocked(queries.fetchPropertySources).mockRejectedValueOnce(new Error('HTTP 500'));
    renderAt('/property/774');
    await waitFor(() =>
      expect(queries.fetchSnapshotsForListings).toHaveBeenCalledWith([105054], expect.anything()));
  });

  it('lists the adverts once, in the merged-adverts section, with no duplicate lists', async () => {
    renderAt('/property/774?tab=inzeraty');

    expect(await screen.findByText('Sloučené inzeráty')).toBeInTheDocument();
    expect(within(screen.getByText('Sreality').closest('li') as HTMLElement).getByText('hlavní inzerát'))
      .toBeInTheDocument();
    // The chips row, the "current active listing" jump, the history block's URL
    // list and the price-mismatch note are gone: one list, one price.
    expect(screen.queryByText('View the current active listing')).toBeNull();
    expect(screen.queryByText('this listing')).toBeNull();
    expect(screen.queryByText(/Pozn\.:/)).toBeNull();
    expect(screen.getAllByRole('link', { name: /Na portálu/ })).toHaveLength(2);
    // Nothing asked for, nothing opened.
    expect(rowToggle('iDNES Reality')).toHaveAttribute('aria-expanded', 'false');
  });

  /* MS8: a labelled, display-only line when an active advert of the property's
     deal type is cheaper than the header (a rental never counts for a sale). */
  it('names a cheaper active advert of the same deal under the header price', async () => {
    vi.mocked(queries.fetchPropertySources).mockResolvedValue([
      { ...SOURCES[0], price_czk: 4_900_000 },
      SOURCES[1],
      { ...SOURCES[0], id: 105055, price_czk: 25_000 },
    ]);
    const deal = (id: number, category_type: string) => [id, { id, category_type } as ListingPublic] as const;
    vi.mocked(queries.fetchListingsForListingIds).mockResolvedValue(
      new Map([deal(105053, 'prodej'), deal(105054, 'prodej'), deal(105055, 'pronajem')]),
    );
    renderAt('/property/774');
    // The matcher normalizes the formatter's no-break spaces to spaces.
    const line = await screen.findByText('Nejnižší aktivní cena: 4 900 000 Kč · iDNES Reality');
    expect(line).toHaveAttribute('title', expect.stringContaining('jen pro informaci'));
    expect(screen.getByRole('heading', { level: 1 }).textContent).toContain(fmtCzk(5_000_000));
  });

  it('still names the cheaper advert when the adverts’ deal types cannot be read', async () => {
    vi.mocked(queries.fetchPropertySources).mockResolvedValue([{ ...SOURCES[0], price_czk: 4_900_000 }, SOURCES[1]]);
    vi.mocked(queries.fetchListingsForListingIds).mockRejectedValue(new Error('HTTP 500'));
    renderAt('/property/774');
    expect(await screen.findByText('Nejnižší aktivní cena: 4 900 000 Kč · iDNES Reality')).toBeInTheDocument();
  });

  /* Like with like: the header's stored price lags an advert's price change until
     the recompute, and a lag is not two adverts disagreeing. */
  it('names no lower price when the cheapest advert is the canonical one', async () => {
    vi.mocked(queries.fetchPropertySources).mockResolvedValue([SOURCES[0], { ...SOURCES[1], price_czk: 4_800_000 }]);
    const deal = (id: number) => [id, { id, category_type: 'prodej', disposition: '3+1' } as ListingPublic] as const;
    vi.mocked(queries.fetchListingsForListingIds).mockResolvedValue(new Map([deal(105053), deal(105054)]));
    renderAt('/property/774?tab=inzeraty');
    expect((await screen.findAllByText(/3\+1/)).length).toBeGreaterThan(0);
    expect(screen.queryByText(/Nejnižší aktivní cena/)).toBeNull();
  });

  /* MS9/MS10: every advert draws its own line, so the dated moves are every
     advert's, each labelled by its portal. */
  it('lists every advert’s price moves, labelled by portal', async () => {
    const snap = (id: number, listing_id: number, scraped_at: string, price_czk: number) =>
      ({ id, listing_id, scraped_at, price_czk });
    vi.mocked(queries.fetchSnapshotsForListings).mockResolvedValue([
      snap(1, 105053, '2025-11-01T08:00:00Z', 5_400_000),
      snap(2, 105053, '2025-12-15T08:00:00Z', 5_200_000),
      snap(3, 105054, '2025-12-01T08:00:00Z', 5_200_000),
      snap(4, 105054, '2026-01-01T08:00:00Z', 5_000_000),
    ]);
    renderAt('/property/774?tab=hodnota');
    const moves = await screen.findByRole('list', { name: 'Price moves' });
    expect(within(moves).getAllByRole('listitem').map((li) => li.firstChild?.textContent))
      .toEqual(['Sreality', 'iDNES Reality']);
  });

  it('opens the asked-for advert’s row, but not the one the header already is', async () => {
    renderAt('/property/774?advert=105053');
    await screen.findByText('Sloučené inzeráty');
    expect(rowToggle('iDNES Reality')).toHaveAttribute('aria-expanded', 'true');
    expect(rowToggle('Sreality')).toHaveAttribute('aria-expanded', 'false');
  });

  it('follows a merged-away property to its survivor (admin)', async () => {
    vi.mocked(auth.useAuth).mockReturnValue({ isAdmin: true } as ReturnType<typeof auth.useAuth>);
    vi.mocked(queries.fetchProperty).mockImplementation(async (id) => (id === 774 ? PROPERTY : null));
    vi.mocked(api.fetchPropertyOrigins).mockResolvedValue({ property_id: 774, adverts: [] });

    renderAt('/property/500?advert=105053');

    await waitFor(() => expect(where()).toBe('/property/774?advert=105053'));
    expect(api.fetchPropertyOrigins).toHaveBeenCalledWith(500);
  });
});

/* Design A (operator 2026-10-22): the sections sit in tabs under the header; the
   open one is ?tab=, and the addresses handed out before the tabs still land on
   their section. */
describe('<PropertyDetail> tabs', () => {
  const selected = () => screen.getByRole('tab', { selected: true });

  it('opens the overview: the description above "is the price right", the brokers beside it', async () => {
    vi.mocked(queries.fetchProperty).mockResolvedValue({ ...PROPERTY, description: 'Světlý byt po rekonstrukci.' });
    renderAt('/property/774');

    await screen.findByRole('heading', { level: 1 });
    expect(selected()).toHaveTextContent('Přehled');
    const popis = await screen.findByText('Popis');
    const value = screen.getByText('Je cena v pořádku?');
    expect(popis.compareDocumentPosition(value) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    // Every other section waits for its tab.
    expect(screen.queryByText('Sloučené inzeráty')).toBeNull();
    expect(screen.queryByTestId('valuo-ads')).toBeNull();
  });

  it('switches tab in place, into the address, and keeps a visited tab mounted', async () => {
    renderAt('/property/774');
    await screen.findByRole('heading', { level: 1 });

    fireEvent.click(screen.getByRole('tab', { name: /Inzeráty/ }));
    expect(await screen.findByText('Sloučené inzeráty')).toBeInTheDocument();
    expect(where()).toBe('/property/774?tab=inzeraty');

    fireEvent.click(screen.getByRole('tab', { name: 'Přehled' }));
    expect(where()).toBe('/property/774?tab=prehled');
    // Hidden, not unmounted: an open advert row or a draft survives the look away.
    expect(screen.getByText('Sloučené inzeráty')).not.toBeVisible();
  });

  it('lands an estimation-run link on Cena a hodnota', async () => {
    renderAt('/property/774?run=5#estimations');
    await screen.findByRole('heading', { level: 1 });
    expect(selected()).toHaveTextContent('Cena a hodnota');
  });

  it('lands the canonical advert’s own address on the overview, another advert’s on Inzeráty', async () => {
    renderAt('/property/774?advert=105054');
    await screen.findByRole('heading', { level: 1 });
    expect(selected()).toHaveTextContent('Přehled');
  });

  it('counts the photos and the adverts on their tabs', async () => {
    renderAt('/property/774');
    expect(await screen.findByRole('tab', { name: 'Inzeráty 2' })).toBeInTheDocument();
  });
});

/* The identity track's ask: "New estimation" is the canonical advert's, by its
   listings.id, on any portal. It used to be bound to a portal URL and its runs to
   a sreality id, so a property with no sreality advert could not be estimated. */
describe('<PropertyDetail> New estimation', () => {
  it('offers the estimate on a property with no sreality advert, bound to its canonical ad', async () => {
    vi.mocked(queries.fetchProperty).mockResolvedValue({
      ...PROPERTY,
      id: 105053,
      sreality_id: null,
      source: 'idnes',
      source_id_native: '6a147cfde222cf687509e018',
      category_type: 'pronajem',
    } as unknown as queries.PropertyPublic);
    vi.mocked(queries.fetchPropertySources).mockResolvedValue([SOURCES[0]]);
    openEstimation.mockClear();
    renderAt('/property/774');

    fireEvent.click(await screen.findByRole('button', { name: /New estimation/ }));
    expect(openEstimation).toHaveBeenCalledWith({
      listingId: 105053,
      categoryMain: 'byt',
      estimateKind: 'rent',
    });
  });

  it('binds it to the canonical ad before the advert list arrives', async () => {
    vi.mocked(queries.fetchPropertySources).mockReturnValue(new Promise(() => {}));
    openEstimation.mockClear();
    renderAt('/property/774');

    fireEvent.click(await screen.findByRole('button', { name: /New estimation/ }));
    expect(openEstimation).toHaveBeenCalledWith(expect.objectContaining({ listingId: 105054 }));
  });
});

describe('<AdvertRedirect> old advert addresses', () => {
  it('lands the natural-key address on the property page with that advert’s row open', async () => {
    vi.mocked(queries.fetchAdvertProperty).mockResolvedValue({ id: 105053, property_id: 774 });

    renderAt('/listing/idnes/6a147cfde222cf687509e018');

    await waitFor(() => expect(where()).toBe('/property/774?advert=105053'));
    expect(queries.fetchAdvertProperty).toHaveBeenCalledWith(
      { source: 'idnes', nativeId: '6a147cfde222cf687509e018' },
      expect.anything(),
    );
    await screen.findByText('Sloučené inzeráty');
    expect(rowToggle('iDNES Reality')).toHaveAttribute('aria-expanded', 'true');
  });

  it('keeps ?run= and the hash through the legacy numeric address', async () => {
    vi.mocked(queries.fetchAdvertProperty).mockResolvedValue({ id: 105053, property_id: 774 });

    renderAt('/listing/-11876?run=9#estimations');

    await waitFor(() => expect(where()).toBe('/property/774?run=9&advert=105053#estimations'));
    expect(queries.fetchAdvertProperty).toHaveBeenCalledWith({ srealityId: -11876 }, expect.anything());
  });

  it('sends the old ?property= form straight to the property, with no read', async () => {
    renderAt('/listing?property=774');

    await waitFor(() => expect(where()).toBe('/property/774'));
    expect(queries.fetchAdvertProperty).not.toHaveBeenCalled();
  });

  it('says in Czech that we lack the advert, and offers to add it with the pasted URL', async () => {
    vi.mocked(queries.fetchAdvertProperty).mockResolvedValue(null);
    openNewProperty.mockClear();
    const url = 'https://reality.bazos.cz/inzerat/196123456/byt.php';
    renderAtState('/listing/bazos/196123456', { pastedUrl: url });
    expect(await screen.findByRole('heading', { name: 'Tento inzerát v databázi nemáme.' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /Otevřít na portálu Bazoš/ })).toHaveAttribute('href', url);
    fireEvent.click(screen.getByRole('button', { name: 'Přidat do databáze' }));
    expect(openNewProperty).toHaveBeenCalledWith({ url });
  });

  it('opens the dialog empty when the address carried no URL', async () => {
    vi.mocked(queries.fetchAdvertProperty).mockResolvedValue(null);
    openNewProperty.mockClear();
    renderAt('/listing/bazos/nope');
    fireEvent.click(await screen.findByRole('button', { name: 'Přidat do databáze' }));
    expect(openNewProperty).toHaveBeenCalledWith({});
    expect(screen.queryByRole('link', { name: /Otevřít na portálu/ })).toBeNull();
  });

  it('waits for an advert we hold whose property is not born yet, then lands', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'setInterval', 'clearInterval'] });
    try {
      vi.mocked(queries.fetchAdvertProperty)
        .mockResolvedValueOnce({ id: 105053, property_id: null })
        .mockResolvedValueOnce({ id: 105053, property_id: null })
        .mockResolvedValue({ id: 105053, property_id: 774 });
      renderAt('/listing/idnes/6a147cfde222cf687509e018');
      await act(() => vi.advanceTimersByTimeAsync(0));
      expect(screen.getByRole('status')).toHaveTextContent('Zakládám nemovitost…');
      expect(screen.queryByText('Tento inzerát v databázi nemáme.')).toBeNull();
      await act(() => vi.advanceTimersByTimeAsync(2_000));
      expect(queries.fetchAdvertProperty).toHaveBeenCalledTimes(2);
      expect(screen.getByRole('status')).toHaveTextContent('Zakládám nemovitost…');
      await act(() => vi.advanceTimersByTimeAsync(2_000));
      await act(() => vi.advanceTimersByTimeAsync(10));
      expect(queries.fetchAdvertProperty).toHaveBeenCalledTimes(3);
      expect(where()).toBe('/property/774?advert=105053');
    } finally {
      vi.useRealTimers();
    }
  });
});

/* -------------------------------------------------------------------------- */
/* Broker list (MS7) — the honest failed read, and the per-field 3-state      */
/* contact rendering                                                          */
/* -------------------------------------------------------------------------- */

/* One row per advert: /brokers/by-listings carries identity AND contact (419).
   Which contact half arrives (primary_* vs has_*) is still a property of the
   CALLER — admin vs not — so each test picks one. broker_id (7) and listing_id
   (105054) are deliberately different values; that difference is what pins the
   link below to the BROKER dossier rather than a listing-id-shaped route. */
const attribution = (
  contact: Partial<Pick<brokers.ListingBroker, 'primary_email' | 'primary_phone'>> = {},
): brokers.ListingBroker => ({
  sreality_id: 999,
  listing_id: 105054,
  broker_id: 7,
  broker_display_name: 'Jan Novák',
  broker_firm_label: 'RE/MAX Alfa',
  primary_email: null,
  primary_phone: null,
  ...contact,
});
const EVA = { ...attribution(), listing_id: 105053, broker_id: 8, broker_display_name: 'Eva Malá' };
const byAdvert = (...rows: brokers.ListingBroker[]) => new Map(rows.map((r) => [r.listing_id, r]));

describe('<BrokerList>', () => {
  beforeEach(() => {
    vi.mocked(brokers.fetchListingBrokersByIds).mockReset();
    vi.mocked(brokers.fetchListingBrokersByIds).mockResolvedValue(byAdvert(attribution()));
  });

  const renderListing = () => renderAt('/property/774');

  function withContact(contact: Parameters<typeof attribution>[0]) {
    vi.mocked(brokers.fetchListingBrokersByIds).mockResolvedValue(byAdvert(attribution(contact)));
  }

  it('shows the contact to every signed-in user, keyed on the attributed broker', async () => {
    withContact({ primary_phone: '420777123456', primary_email: 'jan@remax.cz' });

    renderListing();

    expect(await screen.findByText('+420 777 123 456')).toBeInTheDocument();
    expect(screen.getByText('jan@remax.cz')).toBeInTheDocument();
    expect(screen.getByText('Jan Novák')).toBeInTheDocument();
    expect(screen.getByText('RE/MAX Alfa')).toBeInTheDocument();
    // ONE batched read over every advert: the list and every advert row share it.
    expect(brokers.fetchListingBrokersByIds).toHaveBeenCalledWith([105053, 105054]);
    expect(brokers.fetchListingBrokersByIds).toHaveBeenCalledTimes(1);
    // ATTRIBUTION deliberately gives broker_id (7) and listing_id (105054)
    // different values — pins the link to the BROKER dossier, not a
    // listing-id-shaped route that would 404 on every click.
    expect(screen.getByRole('link', { name: /Jan Novák/ })).toHaveAttribute(
      'href',
      '/brokers/7',
    );
  });

  /* W6 deleted the two states that only existed because contact arrived on a
     SECOND, later request: "Načítám kontakt…" (identity painted, contact still in
     flight) and "Kontakt není k dispozici" (that read succeeded but held no row
     for this broker). Neither is reachable now — holding the broker row IS
     holding the answer — so the card paints complete in one pass. This pins the
     absence: if a chained contact read ever comes back, so will the reflow. */
  it('paints the contact in the same pass as the identity, with no interim state', async () => {
    withContact({ primary_phone: '420777123456', primary_email: 'jan@remax.cz' });

    renderListing();

    expect(await screen.findByText('Jan Novák')).toBeInTheDocument();
    expect(screen.getByText('+420 777 123 456')).toBeInTheDocument();
    expect(screen.queryByText('Načítám kontakt…')).toBeNull();
    expect(screen.queryByText('Kontakt není k dispozici')).toBeNull();
    expect(screen.queryByText('Kontakt se nepodařilo načíst')).toBeNull();
  });

  it('keeps the plain dash when the broker genuinely has no contact', async () => {
    withContact({});

    renderListing();

    expect(await screen.findByText(/telefon —/)).toBeInTheDocument();
    expect(screen.getByText(/e-mail —/)).toBeInTheDocument();
    expect(screen.queryByText(/na vyžádání/)).not.toBeInTheDocument();
  });

  it('renders nothing at all for a genuinely unattributed property', async () => {
    vi.mocked(brokers.fetchListingBrokersByIds).mockResolvedValue(new Map());

    renderListing();

    await waitFor(() => expect(brokers.fetchListingBrokersByIds).toHaveBeenCalled());
    expect(screen.queryByText('Makléř')).toBeNull();
    expect(screen.queryByText('Makléře se nepodařilo načíst')).toBeNull();
  });

  /* Rendering a failed read as an absent block asserted "no broker" for every
     outage — the dark state that hid the PostgREST revocation on this surface
     for a month. It says so, and its retry reads again. */
  it('says so when the attribution read fails instead of looking unattributed', async () => {
    vi.mocked(brokers.fetchListingBrokersByIds).mockRejectedValueOnce(
      new api.ApiError('Invalid token', 401, null),
    );

    renderListing();

    const failed = await screen.findByText('Makléře se nepodařilo načíst');
    fireEvent.click(within(failed).getByRole('button', { name: 'Zkusit znovu' }));
    expect(await screen.findByText('Jan Novák')).toBeInTheDocument();
  });

  /* The distinction that used to need its own read: a broker we could not fetch
     must never render as a broker with no reachable channel. With one read there
     is no half-loaded card left to get this wrong — a failure takes the whole
     block to the error line, and an em-dash is only ever drawn from a row we
     actually hold. */
  it('never draws an empty channel for a broker it failed to read', async () => {
    vi.mocked(brokers.fetchListingBrokersByIds).mockRejectedValue(new Error('HTTP 500'));

    renderListing();

    expect(
      await screen.findByText('Makléře se nepodařilo načíst'),
    ).toBeInTheDocument();
    expect(screen.queryByText(/telefon —/)).not.toBeInTheDocument();
    expect(screen.queryByText(/e-mail —/)).not.toBeInTheDocument();
  });

  it('lists each person once, the canonical advert’s broker first', async () => {
    vi.mocked(brokers.fetchListingBrokersByIds).mockResolvedValue(byAdvert(EVA, attribution()));
    renderListing();
    expect(await screen.findByText('Makléři')).toBeInTheDocument();
    expect(screen.getAllByRole('link', { name: /Jan Novák|Eva Malá/ }).map((a) => a.textContent))
      .toEqual(['Jan Novák', 'Eva Malá']);
  });

  it('hides an inactive advert’s broker while an advert is active', async () => {
    vi.mocked(queries.fetchPropertySources).mockResolvedValue([{ ...SOURCES[0], is_active: false }, SOURCES[1]]);
    vi.mocked(brokers.fetchListingBrokersByIds).mockResolvedValue(byAdvert(EVA, attribution()));
    renderListing();
    expect(await screen.findByText('Jan Novák')).toBeInTheDocument();
    expect(screen.queryByText('Eva Malá')).toBeNull();
  });

  it('with no active advert, lists every advert’s broker as from inactive adverts', async () => {
    vi.mocked(queries.fetchPropertySources).mockResolvedValue(SOURCES.map((s) => ({ ...s, is_active: false })));
    vi.mocked(brokers.fetchListingBrokersByIds).mockResolvedValue(byAdvert(EVA, attribution()));
    renderListing();
    expect(await screen.findByText('z neaktivních inzerátů')).toBeInTheDocument();
    expect(screen.getByText('Eva Malá')).toBeInTheDocument();
  });
});

/* -------------------------------------------------------------------------- */
/* Upravit jako {email} (design §3.5, §8-4, §8-10)                            */
/* -------------------------------------------------------------------------- */

const EDIT_BOARD: PipelineShare = { account_id: 'owner-1', email: 'owner@x.cz', mode: 'edit' };
const READ_BOARD: PipelineShare = { account_id: 'owner-2', email: 'reader@x.cz', mode: 'read' };

describe('<PropertyDetail> edit as the pipeline owner', () => {
  beforeEach(() => {
    resetActiveBoard();
    vi.mocked(api.listPipelineShares).mockReset().mockResolvedValue({
      shared_with: [],
      shared_with_me: [EDIT_BOARD, READ_BOARD],
    });
    for (const fn of [
      queries.fetchPipelineMembers,
      queries.fetchIsDismissed,
      queries.fetchPropertyTagMemberSet,
      queries.fetchPropertyCollectionMemberSet,
      api.listPropertyNotes,
      api.listCollections,
      api.listTags,
      api.fetchArtifactLinks,
      api.addPipelineCard,
    ]) {
      vi.mocked(fn).mockClear();
    }
  });

  const toggle = () => screen.queryByRole('button', { name: 'Upravit jako owner@x.cz' });
  const banner = () => screen.queryByText(/^Upravujete jako/);
  const actedAs = (owner: string) =>
    vi.mocked(queries.fetchPipelineMembers).mock.calls.some(([o]) => o?.actAs === owner);

  it('offers nothing on my own board, and reads no share list', async () => {
    renderAt('/property/774');
    await screen.findByRole('heading', { level: 1 });
    expect(toggle()).toBeNull();
    expect(api.listPipelineShares).not.toHaveBeenCalled();
  });

  it('offers nothing while the active board is a read share', async () => {
    act(() => setActiveBoard(READ_BOARD));
    renderAt('/property/774');
    await screen.findByRole('heading', { level: 1 });
    await waitFor(() => expect(api.listPipelineShares).toHaveBeenCalled());
    expect(screen.queryByRole('button', { name: /^Upravit jako/ })).toBeNull();
  });

  it('acts as the owner only once switched on, with the banner under the header', async () => {
    act(() => setActiveBoard(EDIT_BOARD));
    renderAt('/property/774');
    const button = await screen.findByRole('button', { name: 'Upravit jako owner@x.cz' });
    expect(button).toHaveAttribute('aria-pressed', 'false');
    expect(banner()).toBeNull();
    await waitFor(() => expect(queries.fetchPipelineMembers).toHaveBeenCalled());
    expect(actedAs('owner-1')).toBe(false);

    fireEvent.click(button);
    expect(toggle()).toHaveAttribute('aria-pressed', 'true');
    expect(banner()).toHaveTextContent('Upravujete jako owner@x.cz');
    // The header's pipeline control and the curation block read as the owner.
    await waitFor(() => expect(actedAs('owner-1')).toBe(true));
    await waitFor(() =>
      expect(api.listPropertyNotes).toHaveBeenCalledWith(774, { actAs: 'owner-1' }));
    // Every curation control is inside the wrapper: save, dismiss, tags, link.
    const OWNER = { actAs: 'owner-1' };
    await waitFor(() => expect(api.listCollections).toHaveBeenCalledWith(OWNER));
    await waitFor(() => expect(queries.fetchIsDismissed).toHaveBeenCalledWith(774, OWNER));
    await waitFor(() => expect(api.listTags).toHaveBeenCalledWith(OWNER));
    await waitFor(() =>
      expect(queries.fetchPropertyTagMemberSet).toHaveBeenCalledWith(expect.objectContaining(OWNER)));
    await waitFor(() => expect(api.fetchArtifactLinks).toHaveBeenCalledWith(OWNER));
    // Ruling 4: the property is added to the OWNER's board from here.
    fireEvent.click(await screen.findByRole('button', { name: /Přidat do pipeline/ }));
    await waitFor(() => expect(api.addPipelineCard).toHaveBeenCalledWith(774, OWNER));
  });

  it('stays on across property pages until Ukončit', async () => {
    act(() => setActiveBoard(EDIT_BOARD));
    const first = renderAt('/property/774');
    fireEvent.click(await screen.findByRole('button', { name: 'Upravit jako owner@x.cz' }));
    first.unmount();

    // Another property page, a fresh mount: still acting.
    renderAt('/property/775');
    expect(await screen.findByText(/^Upravujete jako/)).toBeInTheDocument();
    await waitFor(() => expect(api.listPropertyNotes).toHaveBeenCalledWith(775, { actAs: 'owner-1' }));
    fireEvent.click(screen.getByRole('button', { name: 'Ukončit' }));
    expect(banner()).toBeNull();
    expect(toggle()).toHaveAttribute('aria-pressed', 'false');
  });

  it('ends when the active board goes back to my own', async () => {
    act(() => setActiveBoard(EDIT_BOARD));
    renderAt('/property/774');
    fireEvent.click(await screen.findByRole('button', { name: 'Upravit jako owner@x.cz' }));
    expect(banner()).not.toBeNull();
    act(() => resetActiveBoard());
    expect(banner()).toBeNull();
    expect(toggle()).toBeNull();
  });

  it('ends on a switch to another owner\'s edit board — never acts as the first one there', async () => {
    const OTHER: PipelineShare = { account_id: 'owner-3', email: 'other@x.cz', mode: 'edit' };
    vi.mocked(api.listPipelineShares).mockResolvedValue({ shared_with: [], shared_with_me: [EDIT_BOARD, OTHER] });
    act(() => setActiveBoard(EDIT_BOARD));
    renderAt('/property/774');
    fireEvent.click(await screen.findByRole('button', { name: 'Upravit jako owner@x.cz' }));
    expect(banner()).not.toBeNull();
    act(() => setActiveBoard(OTHER));
    expect(banner()).toBeNull();
    expect(await screen.findByRole('button', { name: 'Upravit jako other@x.cz' })).toHaveAttribute(
      'aria-pressed',
      'false',
    );
  });

  it('ends, and offers nothing, once the owner lowers the share to read', async () => {
    act(() => setActiveBoard(EDIT_BOARD));
    const view = renderAt('/property/774');
    fireEvent.click(await screen.findByRole('button', { name: 'Upravit jako owner@x.cz' }));
    view.unmount();
    vi.mocked(api.listPipelineShares).mockResolvedValue({
      shared_with: [],
      shared_with_me: [{ ...EDIT_BOARD, mode: 'read' }],
    });
    renderAt('/property/774');
    await waitFor(() => expect(getActiveBoard()?.mode).toBe('read'));
    expect(banner()).toBeNull();
    expect(screen.queryByRole('button', { name: /^Upravit jako/ })).toBeNull();
  });

  it('offers nothing before the server confirms the remembered board', async () => {
    let answer!: (v: { shared_with: PipelineShare[]; shared_with_me: PipelineShare[] }) => void;
    vi.mocked(api.listPipelineShares).mockReturnValue(new Promise((r) => (answer = r)));
    act(() => setActiveBoard(EDIT_BOARD));
    renderAt('/property/774');
    await screen.findByRole('heading', { level: 1 });
    expect(toggle()).toBeNull();
    act(() => answer({ shared_with: [], shared_with_me: [EDIT_BOARD] }));
    expect(await screen.findByRole('button', { name: 'Upravit jako owner@x.cz' })).toBeInTheDocument();
  });

  it('offers nothing on the phone, and reads no share list there (ruling 6)', async () => {
    const original = window.matchMedia;
    window.matchMedia = ((query: string) => ({
      matches: query === PHONE_SHELL_QUERY,
      media: query,
      onchange: null,
      addEventListener: () => {},
      removeEventListener: () => {},
      addListener: () => {},
      removeListener: () => {},
      dispatchEvent: () => false,
    })) as unknown as typeof window.matchMedia;
    try {
      act(() => setActiveBoard(EDIT_BOARD));
      act(() => setEditAsOwner(true));
      renderAt('/property/774');
      await waitFor(() => expect(queries.fetchProperty).toHaveBeenCalled());
      await act(async () => {});
      expect(screen.queryByRole('button', { name: /^Upravit jako/ })).toBeNull();
      expect(banner()).toBeNull();
      expect(api.listPipelineShares).not.toHaveBeenCalled();
    } finally {
      window.matchMedia = original;
    }
  });

  it('drops a revoked board instead of offering to act on it', async () => {
    vi.mocked(api.listPipelineShares).mockResolvedValue({ shared_with: [], shared_with_me: [] });
    act(() => setActiveBoard(EDIT_BOARD));
    renderAt('/property/774');
    await waitFor(() => expect(getActiveBoard()).toBeNull());
    expect(toggle()).toBeNull();
  });
});
