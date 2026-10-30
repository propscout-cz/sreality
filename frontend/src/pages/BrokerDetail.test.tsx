/* /brokers/:id — the three states the B2 repoint made distinguishable.
 *
 * Before it, every broker read was dark and the page had exactly one outcome:
 * "Makléř nenalezen.". Now the dossier answers, so the page must tell apart
 * (1) a masked contact from an absent one, (2) a failed dossier read from an
 * unknown broker, and (3) a failed inventory read from a broker with no
 * listings — the last one contradicting the stats strip rendered right above it.
 */

import { describe, expect, it, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes } from 'react-router-dom';

import BrokerDetail from './BrokerDetail';
import * as brokers from '@/lib/brokers';

vi.mock('@/lib/brokers', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/brokers')>()),
  fetchBrokerDossier: vi.fn(),
  fetchBrokerListings: vi.fn(),
}));

/* The explore-broker modal is provider-mounted in Shell (like the area modal);
 * the page only needs its `open` — same treatment PropertyDetail.test.tsx gives
 * useExploreAreaModal. Hoisted so the spy is reachable from the mock factory. */
const { openExploreBroker } = vi.hoisted(() => ({ openExploreBroker: vi.fn() }));
vi.mock('@/components/ExploreBrokerModal', () => ({
  useExploreBrokerModal: () => ({ open: openExploreBroker, close: vi.fn(), isOpen: false }),
}));

const listing = (l: Partial<brokers.BrokerListing>): brokers.BrokerListing =>
  ({
    broker_id: 7,
    sreality_id: null,
    listing_id: 1,
    source: 'sreality',
    source_url: '',
    locality: 'Praha',
    district: null,
    category_main: 'byt',
    category_type: 'prodej',
    disposition: '2+kk',
    subtype: null,
    area_m2: 50,
    price_czk: 5_000_000,
    is_active: true,
    last_seen_at: new Date().toISOString(),
    property_id: 11,
    ...l,
  }) as brokers.BrokerListing;

const dossier = (broker: Partial<brokers.BrokerPublic>): brokers.BrokerDossier => ({
  broker: {
    broker_id: 7,
    display_name: 'Jan Novák',
    firm_id: null,
    firm_domain: null,
    firm_name: 'RE/MAX',
    properties: 50,
    active_properties: 35,
    ...broker,
  } as brokers.BrokerPublic,
  memberships: [],
  region_shares: [],
  categories: [],
  pii_masked: true,
});

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={['/brokers/7']}>
        <Routes>
          <Route path="/brokers/:id" element={<BrokerDetail />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.mocked(brokers.fetchBrokerDossier).mockResolvedValue(dossier({}));
  vi.mocked(brokers.fetchBrokerListings).mockResolvedValue([]);
});

describe('<BrokerDetail> contact card', () => {
  it('shows the real contact for an admin session', async () => {
    vi.mocked(brokers.fetchBrokerDossier).mockResolvedValue(
      dossier({ primary_phone: '420777123456', primary_email: 'jan@remax.cz' }),
    );
    renderPage();
    expect(await screen.findByText('+420 777 123 456')).toBeInTheDocument();
    expect(screen.getByText('jan@remax.cz')).toBeInTheDocument();
  });

  /* The bug this wave exists to kill: a non-admin gets has_* instead of the
     values, and an em-dash there claims the broker is unreachable. */
  it('says a masked contact is on file rather than showing the empty dash', async () => {
    vi.mocked(brokers.fetchBrokerDossier).mockResolvedValue(
      dossier({ has_phone: true, has_email: true }),
    );
    renderPage();
    expect(await screen.findByText(/telefon · kontakt na vyžádání/)).toBeInTheDocument();
    expect(screen.getByText(/e-mail · kontakt na vyžádání/)).toBeInTheDocument();
  });

  it('keeps the plain dash when the broker genuinely has no contact', async () => {
    vi.mocked(brokers.fetchBrokerDossier).mockResolvedValue(
      dossier({ has_phone: false, has_email: false }),
    );
    renderPage();
    expect(await screen.findByText(/telefon —/)).toBeInTheDocument();
    expect(screen.queryByText(/na vyžádání/)).not.toBeInTheDocument();
  });
});

describe('<BrokerDetail> honest error states', () => {
  it('separates a failed dossier read from an unknown broker', async () => {
    vi.mocked(brokers.fetchBrokerDossier).mockRejectedValue(new Error('HTTP 500'));
    renderPage();
    expect(await screen.findByText(/Makléře se nepodařilo načíst: HTTP 500/)).toBeInTheDocument();
    expect(screen.queryByText('Makléř nenalezen.')).not.toBeInTheDocument();
  });

  it('still reports an unknown broker as not found', async () => {
    vi.mocked(brokers.fetchBrokerDossier).mockResolvedValue(null);
    renderPage();
    expect(await screen.findByText('Makléř nenalezen.')).toBeInTheDocument();
  });

  /* The dossier succeeds independently now, so an inventory failure rendered as
     "Žádné inzeráty." would sit directly under a stats strip asserting 35/50. */
  it('does not report a failed inventory read as an empty inventory', async () => {
    vi.mocked(brokers.fetchBrokerListings).mockRejectedValue(new Error('timeout'));
    renderPage();
    expect(
      await screen.findByText(/Inzeráty se nepodařilo načíst: timeout/),
    ).toBeInTheDocument();
    expect(screen.queryByText('Žádné inzeráty.')).not.toBeInTheDocument();
  });

  it('reports a genuinely empty inventory as empty', async () => {
    renderPage();
    expect(await screen.findByText('Žádné inzeráty.')).toBeInTheDocument();
  });
});

/* broker_listings_public declares BOTH sreality_id and property_id nullable and
 * carries no source_id_native, so a row here can genuinely have no in-app
 * destination. The cell used to pass `property_id ?? 0` and render a link to
 * `/listing?property=0`, which 404s. A row with a property opens the property
 * page on the advert's own row. */
describe('<BrokerDetail> locality cell never fabricates a listing link', () => {
  it('renders inert text when the row has no resolvable destination', async () => {
    vi.mocked(brokers.fetchBrokerListings).mockResolvedValue([
      listing({ listing_id: 1, sreality_id: null, property_id: null, display_label: 'Bez cile' }),
    ]);
    const { container } = renderPage();

    expect(await screen.findByText('Bez cile')).toBeInTheDocument();
    expect(container.querySelector('a[href*="property=0"]')).toBeNull();
    expect(screen.queryByRole('link', { name: /Bez cile/ })).toBeNull();
  });

  it('still links a row that carries a property id', async () => {
    vi.mocked(brokers.fetchBrokerListings).mockResolvedValue([
      listing({ listing_id: 2, sreality_id: null, property_id: 42, display_label: 'S cilem' }),
    ]);
    renderPage();

    const link = await screen.findByRole('link', { name: /S cilem/ });
    expect(link).toHaveAttribute('href', '/property/42?advert=2');
    expect(link).toHaveAttribute('target', '_blank');
  });

  it('still links a row that carries a sreality id', async () => {
    vi.mocked(brokers.fetchBrokerListings).mockResolvedValue([
      listing({ listing_id: 3, sreality_id: -284913, property_id: null, display_label: 'Legacy' }),
    ]);
    renderPage();

    const link = await screen.findByRole('link', { name: /Legacy/ });
    expect(link).toHaveAttribute('href', '/listing/-284913');
  });
});

/* The modal seeds from the SAME Typ/Nabídka selection the inventory table is
 * filtered to (the state is lifted to the page for exactly this) — so what the
 * operator narrowed the table to is what the map opens scoped to. */
describe('<BrokerDetail> explore this broker', () => {
  beforeEach(() => openExploreBroker.mockClear());

  it('opens the modal scoped to this broker with the current Typ/Nabídka selection', async () => {
    vi.mocked(brokers.fetchBrokerListings).mockResolvedValue([
      listing({ listing_id: 1, category_main: 'byt', category_type: 'prodej' }),
      listing({ listing_id: 2, category_main: 'dum', category_type: 'pronajem' }),
    ]);
    renderPage();
    const button = await screen.findByRole('button', { name: /Explore this broker's listings/ });

    // Vše / Vše (the page's default) seeds NO category constraint.
    fireEvent.click(button);
    expect(openExploreBroker).toHaveBeenLastCalledWith({
      brokerId: 7,
      brokerName: 'Jan Novák',
      categoryMain: null,
      categoryType: null,
    });

    // Narrow the table, then explore again — the seed follows the pills.
    fireEvent.click(await screen.findByText('Domy'));
    fireEvent.click(screen.getByText('Pronájem'));
    fireEvent.click(button);
    expect(openExploreBroker).toHaveBeenLastCalledWith({
      brokerId: 7,
      brokerName: 'Jan Novák',
      categoryMain: 'dum',
      categoryType: 'pronajem',
    });
  });
});

/* Typ and Nabídka are pick-ones, so each is the ONE joined bar (operator ruling
 * 2026-10-27, system.md § Option groups); Vše keeps its place, first. */
describe('<BrokerDetail> inventory filters', () => {
  it('draws Typ and Nabídka as joined bars, Vše lit by default, the chosen segment filled copper', async () => {
    vi.mocked(brokers.fetchBrokerListings).mockResolvedValue([listing({})]);
    renderPage();
    const typ = await screen.findByRole('group', { name: 'Typ' });
    const offer = screen.getByRole('group', { name: 'Nabídka' });
    expect(within(typ).getAllByRole('button').map((b) => b.textContent)).toEqual(['Vše', 'Byty', 'Domy', 'Pozemky', 'Komerční']);
    expect(within(offer).getAllByRole('button').map((b) => b.textContent)).toEqual(['Vše', 'Prodej', 'Pronájem']);
    for (const bar of [typ, offer]) {
      expect(bar.firstElementChild).toHaveClass('inline-flex', 'border', 'bg-[var(--color-paper-2)]');
      expect(within(bar).getByRole('button', { name: 'Vše' })).toHaveAttribute('aria-pressed', 'true');
      expect(within(bar).getByRole('button', { name: 'Vše' })).toHaveClass('bg-[var(--color-copper)]', 'text-[var(--color-on-copper)]');
      for (const b of within(bar).getAllByRole('button')) expect(b).not.toHaveClass('border');
    }
    fireEvent.click(within(typ).getByRole('button', { name: 'Domy' }));
    expect(within(typ).getByRole('button', { name: 'Domy' })).toHaveAttribute('aria-pressed', 'true');
    expect(within(typ).getByRole('button', { name: 'Vše' })).toHaveAttribute('aria-pressed', 'false');
  });
});

/* Section cards (operator ruling 2026-10-28, system.md § Section cards): the
 * stats strip stays; each section below it is a card named by its title, with
 * a bare count in the title bar (0 and one muted line when empty, C10), and no
 * bordered box inside the card except the inventory's scrolling table. */
describe('<BrokerDetail> section cards', () => {
  const card = (name: string) => screen.getByRole('region', { name });
  const countOf = (region: HTMLElement) =>
    within(region).getByRole('heading', { level: 2 }).nextElementSibling?.textContent;

  it('draws Kde inzeruje, Kanceláře and Inventář as cards with their counts', async () => {
    vi.mocked(brokers.fetchBrokerDossier).mockResolvedValue({
      ...dossier({}),
      region_shares: [
        { geo_id: 1, name: 'Praha', active_properties: 9 },
        { geo_id: 2, name: 'Brno', active_properties: 3 },
      ] as unknown as brokers.BrokerRegionShare[],
      memberships: [
        { firm_id: 5, firm_name: 'RE/MAX', firm_domain: null, is_current: true },
      ] as unknown as brokers.BrokerMembership[],
    });
    vi.mocked(brokers.fetchBrokerListings).mockResolvedValue([listing({ listing_id: 1 }), listing({ listing_id: 2 })]);
    renderPage();
    await screen.findByRole('table');
    for (const name of ['Kde inzeruje', 'Kanceláře', 'Inventář']) {
      const r = card(name);
      expect(r.tagName).toBe('SECTION');
      expect(r).toHaveClass('border', 'bg-[var(--color-paper-2)]', 'rounded-[var(--radius-md)]');
    }
    expect(countOf(card('Kde inzeruje'))).toBe('2');
    expect(countOf(card('Kanceláře'))).toBe('1');
    expect(countOf(card('Inventář'))).toBe('2');
    // Ruling 4: property grain, no ad counts.
    expect(screen.queryByText('Inzeráty')).toBeNull();
    /* No box in a box: the lists sit on the card itself; the table keeps its own. */
    expect(within(card('Kde inzeruje')).getByRole('list').parentElement).not.toHaveClass('border');
    expect(within(card('Kanceláře')).getByRole('list').parentElement).not.toHaveClass('border');
    expect(within(card('Inventář')).getByRole('table').parentElement).toHaveClass('overflow-x-auto', 'border');
    /* Typ / Nabídka are L4 field labels. */
    expect(within(card('Inventář')).getByText('Typ', { selector: 'span' }))
      .toHaveClass('uppercase', 'tracking-[0.08em]', 'font-semibold');
  });

  it('marks a full inventory page as a cap, not the whole book', async () => {
    vi.mocked(brokers.fetchBrokerListings).mockResolvedValue(
      Array.from({ length: brokers.BROKER_LISTINGS_LIMIT }, (_, i) => listing({ listing_id: i + 1 })),
    );
    renderPage();
    await screen.findByRole('table');
    expect(countOf(card('Inventář'))).toBe('500+');
    fireEvent.click(within(card('Inventář')).getByRole('button', { name: 'Domy' }));
    expect(countOf(card('Inventář'))).toBe('0 z 500+');
  });

  it('keeps an empty section as its card with count 0 and one muted line', async () => {
    renderPage();
    expect(await screen.findByText('Žádné inzeráty.')).toBeInTheDocument();
    expect(countOf(card('Kde inzeruje'))).toBe('0');
    expect(within(card('Kde inzeruje')).getByText('Bez aktivního regionu.')).toBeInTheDocument();
    expect(countOf(card('Kanceláře'))).toBe('0');
    expect(countOf(card('Inventář'))).toBe('0');
    expect(within(card('Inventář')).getByText('Žádné inzeráty.').closest('.border')).toBe(card('Inventář'));
  });
});
