/* /brokers/:id — the three states the B2 repoint made distinguishable.
 *
 * Before it, every broker read was dark and the page had exactly one outcome:
 * "Makléř nenalezen.". Now the dossier answers, so the page must tell apart
 * (1) a masked contact from an absent one, (2) a failed dossier read from an
 * unknown broker, and (3) a failed inventory read from a broker with no
 * properties — the last one contradicting the stats strip rendered right above it.
 */

import { describe, expect, it, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes } from 'react-router-dom';

import BrokerDetail from './BrokerDetail';
import * as brokers from '@/lib/brokers';

vi.mock('@/lib/brokers', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/brokers')>()),
  fetchBrokerDossier: vi.fn(),
  fetchBrokerProperties: vi.fn(),
}));

/* The explore-broker modal is provider-mounted in Shell (like the area modal);
 * the page only needs its `open` — same treatment PropertyDetail.test.tsx gives
 * useExploreAreaModal. Hoisted so the spy is reachable from the mock factory. */
const { openExploreBroker } = vi.hoisted(() => ({ openExploreBroker: vi.fn() }));
vi.mock('@/components/ExploreBrokerModal', () => ({
  useExploreBrokerModal: () => ({ open: openExploreBroker, close: vi.fn(), isOpen: false }),
}));

const property = (p: Partial<brokers.BrokerProperty>): brokers.BrokerProperty => ({
  property_id: 11,
  display_label: 'Praha',
  category_main: 'byt',
  category_type: 'prodej',
  disposition: '2+kk',
  subtype: null,
  area_m2: 50,
  is_active: true,
  all_sources: ['sreality'],
  active_sources: ['sreality'],
  price_czk: 5_000_000,
  price_change_pct: null,
  price_change_count: 0,
  last_seen_at: new Date().toISOString(),
  ...p,
});

const page = (rows: brokers.BrokerProperty[], nextCursor?: string) => ({ rows, nextCursor });

/* The dossier's category rows: what the inventory counts and what Typy lists. */
const CATEGORIES: brokers.BrokerCategoryShare[] = [
  { category_main: 'byt', category_type: 'prodej', properties: 30, active_properties: 20 },
  { category_main: 'dum', category_type: 'pronajem', properties: 12, active_properties: 12 },
];

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
  price_change: {
    all: { priced: 50, repriced: 10, repriced_pct: 20, median_pct: -7.3 },
    active: { priced: 35, repriced: 4, repriced_pct: 11.43, median_pct: null },
    inactive: { priced: 0, repriced: 0, repriced_pct: null, median_pct: null },
  },
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
  vi.mocked(brokers.fetchBrokerProperties).mockReset().mockResolvedValue(page([]));
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
    vi.mocked(brokers.fetchBrokerProperties).mockRejectedValue(new Error('timeout'));
    renderPage();
    expect(
      await screen.findByText(/Nemovitosti se nepodařilo načíst: timeout/),
    ).toBeInTheDocument();
    expect(screen.queryByText('Žádné nemovitosti.')).not.toBeInTheDocument();
  });

  it('reports a genuinely empty inventory as empty', async () => {
    renderPage();
    expect(await screen.findByText('Žádné nemovitosti.')).toBeInTheDocument();
  });
});

/* One row per property of the broker's book (#2654): however many ads the broker
 * runs on it, the row opens the property page and names the broker's portals. */
describe('<BrokerDetail> inventory rows are properties', () => {
  it('links each row to its property page, in a new tab', async () => {
    vi.mocked(brokers.fetchBrokerProperties).mockResolvedValue(page([
      property({ property_id: 42, display_label: 'S cilem' }),
    ]));
    renderPage();

    const link = await screen.findByRole('link', { name: /S cilem/ });
    expect(link).toHaveAttribute('href', '/property/42');
    expect(link).toHaveAttribute('target', '_blank');
  });

  it('names the portals the broker advertises the property on, an ended one receding', async () => {
    vi.mocked(brokers.fetchBrokerProperties).mockResolvedValue(page([
      property({ all_sources: ['idnes', 'sreality'], active_sources: ['sreality'] }),
    ]));
    renderPage();
    const table = await screen.findByRole('table');
    expect(within(table).getAllByRole('row')).toHaveLength(2);
    const cell = within(table).getAllByRole('row')[1].querySelectorAll('td')[5];
    expect(cell).toHaveTextContent('iDNES · Sreality');
    expect(cell.querySelector('a')).toBeNull();
    expect(within(cell).getByText(/iDNES/)).toHaveAttribute('title', 'inzerát ukončen');
    expect(within(cell).getByText(/Sreality/)).not.toHaveAttribute('title');
  });

  it('marks a property the broker no longer advertises', async () => {
    vi.mocked(brokers.fetchBrokerProperties).mockResolvedValue(page([
      property({ property_id: 1, display_label: 'Bezi' }),
      property({ property_id: 2, display_label: 'Skoncila', is_active: false, active_sources: [] }),
    ]));
    renderPage();
    expect(within(await screen.findByRole('link', { name: /Skoncila/ })).getByTitle('neaktivní')).toBeInTheDocument();
    expect(within(screen.getByRole('link', { name: /Bezi/ })).queryByTitle('neaktivní')).toBeNull();
  });

  it('pages the book on a click, the next page read from the cursor', async () => {
    vi.mocked(brokers.fetchBrokerProperties)
      .mockResolvedValueOnce(page(
        Array.from({ length: brokers.BROKER_PROPERTIES_PAGE }, (_, i) => property({ property_id: i + 1 })), 'CUR'))
      .mockResolvedValueOnce(page([property({ property_id: 1000 })]));
    renderPage();
    fireEvent.click(await screen.findByRole('button', { name: 'Načíst další' }));
    await waitFor(() => expect(screen.getAllByRole('row')).toHaveLength(brokers.BROKER_PROPERTIES_PAGE + 2));
    expect(vi.mocked(brokers.fetchBrokerProperties).mock.calls.map((c) => c[2])).toEqual([null, 'CUR']);
    expect(screen.queryByRole('button', { name: 'Načíst další' })).toBeNull();
  });
});

/* The modal seeds from the SAME Typ/Nabídka selection the inventory table is
 * filtered to (the state is lifted to the page for exactly this) — so what the
 * operator narrowed the table to is what the map opens scoped to. */
describe('<BrokerDetail> explore this broker', () => {
  beforeEach(() => openExploreBroker.mockClear());

  it('opens the modal scoped to this broker with the current Typ/Nabídka selection', async () => {
    vi.mocked(brokers.fetchBrokerDossier).mockResolvedValue({ ...dossier({}), categories: CATEGORIES });
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
    vi.mocked(brokers.fetchBrokerDossier).mockResolvedValue({ ...dossier({}), categories: CATEGORIES });
    vi.mocked(brokers.fetchBrokerProperties).mockResolvedValue(page([property({})]));
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

  it('narrows the read itself, and counts the selection from the dossier', async () => {
    vi.mocked(brokers.fetchBrokerDossier).mockResolvedValue({ ...dossier({}), categories: CATEGORIES });
    vi.mocked(brokers.fetchBrokerProperties).mockResolvedValue(page([property({})]));
    renderPage();
    const card = await screen.findByRole('region', { name: 'Inventář' });
    const count = () => within(card).getByRole('heading', { level: 2 }).nextElementSibling?.textContent;
    await within(card).findByRole('table');
    expect(count()).toBe('42');
    fireEvent.click(within(card).getByRole('button', { name: 'Domy' }));
    expect(count()).toBe('12 z 42');
    await waitFor(() => expect(vi.mocked(brokers.fetchBrokerProperties).mock.lastCall?.[1])
      .toEqual({ categoryMain: 'dum', categoryType: null }));
    fireEvent.click(within(card).getByRole('button', { name: 'Prodej' }));
    expect(count()).toBe('0 z 42');
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

  it('draws Kde inzeruje, Typy, Kanceláře and Inventář as cards with their counts', async () => {
    vi.mocked(brokers.fetchBrokerDossier).mockResolvedValue({
      ...dossier({}),
      region_shares: [
        { geo_id: 1, name: 'Praha', active_properties: 9 },
        { geo_id: 2, name: 'Brno', active_properties: 3 },
      ] as unknown as brokers.BrokerRegionShare[],
      memberships: [
        { firm_id: 5, firm_name: 'RE/MAX', firm_domain: null, is_current: true },
      ] as unknown as brokers.BrokerMembership[],
      categories: CATEGORIES,
    });
    vi.mocked(brokers.fetchBrokerProperties).mockResolvedValue(page([property({ property_id: 1 }), property({ property_id: 2 })]));
    renderPage();
    await screen.findByRole('table');
    for (const name of ['Kde inzeruje', 'Typy', 'Kanceláře', 'Inventář']) {
      const r = card(name);
      expect(r.tagName).toBe('SECTION');
      expect(r).toHaveClass('border', 'bg-[var(--color-paper-2)]', 'rounded-[var(--radius-md)]');
    }
    expect(countOf(card('Kde inzeruje'))).toBe('2');
    expect(countOf(card('Kanceláře'))).toBe('1');
    /* The inventory's count is the book's (the dossier's), not the page read. */
    expect(countOf(card('Inventář'))).toBe('42');
    /* Typy: active / total properties per type and deal, the total only when it differs. */
    expect(countOf(card('Typy'))).toBe('2');
    expect(within(card('Typy')).getAllByRole('listitem').map((li) => li.textContent))
      .toEqual(['Byty · prodej20 / 30', 'Domy · pronájem12']);
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

  it('keeps an empty section as its card with count 0 and one muted line', async () => {
    renderPage();
    expect(await screen.findByText('Žádné nemovitosti.')).toBeInTheDocument();
    expect(countOf(card('Kde inzeruje'))).toBe('0');
    expect(countOf(card('Typy'))).toBe('0');
    expect(within(card('Typy')).getByText('Žádná nemovitost.')).toBeInTheDocument();
    expect(within(card('Kde inzeruje')).getByText('Bez aktivního regionu.')).toBeInTheDocument();
    expect(countOf(card('Kanceláře'))).toBe('0');
    expect(countOf(card('Inventář'))).toBe('0');
    expect(within(card('Inventář')).getByText('Žádné nemovitosti.').closest('.border')).toBe(card('Inventář'));
  });
});

/* Migrations 768, 770: how much the broker changes prices. The tile reads the book's
 * figures under one status at a time; an inventory row carries the book's own pair. */
describe('<BrokerDetail> price changes', () => {
  it('shows the share repriced and the median, and switches the status slice', async () => {
    renderPage();
    const group = await screen.findByRole('group', { name: 'Změna ceny' });
    const tile = group.parentElement!.parentElement!;
    expect(within(group).getByRole('button', { name: 'Vše' })).toHaveAttribute('aria-pressed', 'true');
    expect(tile).toHaveTextContent(/20\s%/);
    expect(tile).toHaveTextContent(/medián -7,3\s%/);
    expect(tile).toHaveTextContent('přeceněno 10 z 50');

    fireEvent.click(within(group).getByRole('button', { name: 'Aktivní' }));
    expect(tile).toHaveTextContent('přeceněno 4 z 35');
    expect(tile).not.toHaveTextContent('medián');

    fireEvent.click(within(group).getByRole('button', { name: 'Neaktivní' }));
    expect(tile).toHaveTextContent('žádná cena k porovnání');
  });

  it('marks each property with the change the broker made, and leaves an unchanged one blank', async () => {
    vi.mocked(brokers.fetchBrokerProperties).mockResolvedValue(page([
      property({ property_id: 1, price_change_pct: -4.2, price_change_count: 1 }),
      property({ property_id: 2, price_change_pct: 0, price_change_count: 2 }),
      property({ property_id: 3, price_change_pct: null, price_change_count: 0 }),
    ]));
    renderPage();
    const table = await screen.findByRole('table');
    expect(within(table).getByRole('columnheader', { name: 'Změna ceny' })).toBeInTheDocument();
    const cells = within(table).getAllByRole('row').slice(1).map((r) => within(r).getAllByRole('cell')[4]);
    expect(cells[0].querySelector('span')).toHaveAttribute('title', expect.stringContaining('Pokles'));
    expect(cells[0]).toHaveTextContent(/4,2\s%/);
    expect(cells[1]).toHaveTextContent(/0,0\s%/);
    expect(cells[2]).toBeEmptyDOMElement();
  });
});
