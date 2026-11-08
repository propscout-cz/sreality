/* The property page on the phone (#1767): the same route and reads as the
 * desktop page, drawn as the prototype's "Nemovitost" — and rule 23 holds on it:
 * the per-m² figure is the server's measure under the server's basis label, a
 * sale in Kč/m² and a rent in Kč/m²/měs. No desktop map is mounted on the phone (Poloha
 * draws its own) and no estimation can be started from it (#1760 Q10). */
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

import * as api from '@/lib/api';
import * as auth from '@/lib/auth';
import * as brokers from '@/lib/brokers';
import { PHONE_SHELL_QUERY } from '@/lib/phoneShell';
import * as queries from '@/lib/queries';
import * as shareFiles from '@/lib/shareFiles';
import type { PropertyPublic } from '@/lib/queries';
import type { ImagePublic, PropertySource } from '@/lib/types';
import PropertyDetail, { AdvertRedirect } from '@/pages/PropertyDetail';
import { fakeCreateMap, resetFakeMaps } from '@/test/fakeMap';

/* Czech formatting uses no-break spaces; compare as the eye reads it. */
const plain = (s: string | null | undefined) => s?.replace(/ /g, ' ') ?? '';

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>();
  const empty = async () => ({ data: [], total: 0 });
  return {
    ...actual,
    fetchPropertyOrigins: vi.fn(),
    listCollections: vi.fn(empty),
    listTags: vi.fn(empty),
    listPropertyNotes: vi.fn(empty),
    listEstimations: vi.fn(empty),
    /* Poloha: the location JSON. */
    apiGet: vi.fn(async (path: string) =>
      path.endsWith('/location')
        ? { lat: 50.105, lon: 14.5, granularity: 'street', granularity_rank: 60,
            label: 'Freyova, Praha 9 – Vysočany', precision: 'street', note: null }
        : null,
    ),
  };
});
vi.mock('@/lib/basemap', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/basemap')>()),
  createMap: (await import('@/test/fakeMap')).fakeCreateMap,
}));
vi.mock('@/lib/queries', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/queries')>();
  return {
    ...actual,
    fetchProperty: vi.fn(),
    fetchAdvertProperty: vi.fn(),
    fetchPropertySources: vi.fn(),
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
    fetchSoldCoverage: vi.fn(async () => null),
    fetchSoldComparables: vi.fn(async () => []),
  };
});
vi.mock('@/lib/auth', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/auth')>()),
  useAuth: vi.fn(() => ({ isAdmin: false })),
}));
vi.mock('@/lib/brokers', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/brokers')>()),
  fetchListingBrokersByIds: vi.fn(async () => new Map()),
}));
vi.mock('@/lib/shareFiles', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/shareFiles')>()),
  saveBlob: vi.fn(),
}));
vi.mock('@/lib/imageUrl', () => ({
  imageSrc: (img: { storage_path: string | null }, opts: { w?: number } = {}) =>
    `/images/${img.storage_path}${opts.w ? `?w=${opts.w}` : ''}`,
}));
/* The desktop maps and the estimation starters: mounting any of them on the phone fails a
 * test. The phone draws its own Poloha map. */
vi.mock('@/components/listing-detail/DetailMap', () => ({
  default: () => <div data-testid="detail-map" />,
}));
vi.mock('@/components/estimation/ComparablesMap', () => ({
  default: () => <div data-testid="comparables-map" />,
}));
const openExplore = vi.fn();
const openEstimation = vi.fn();
vi.mock('@/components/ExploreAreaModal', () => ({
  useExploreAreaModal: () => ({ open: openExplore }),
}));
vi.mock('@/components/NewEstimationModal', () => ({
  useNewEstimationModal: () => ({ open: openEstimation }),
}));

const SALE = {
  id: 9001,
  property_id: 304388,
  sreality_id: 2861372748,
  first_seen_at: '2026-09-30T08:00:00Z',
  last_seen_at: '2026-10-12T08:00:00Z',
  is_active: true,
  source: 'sreality',
  source_id_native: '2861372748',
  source_url: null,
  category_main: 'byt',
  category_type: 'prodej',
  price_czk: 7_490_000,
  price_unit: null,
  price_per_m2: 138_704,
  price_per_m2_basis: 'sale_capital_czk_m2',
  area_m2: 54,
  disposition: '2+kk',
  subtype: null,
  display_label: 'Freyova, Praha 9 – Vysočany',
  lat: 50.105,
  lng: 14.5,
  floor: 4,
  total_floors: 8,
  building_type: 'panel',
  condition: 'po rekonstrukci',
  energy_rating: 'C',
  ownership: 'osobni',
  has_lift: true,
  cellar: true,
  has_balcony: null,
  description: 'Světlý byt 2+kk po rekonstrukci, 4 minuty od metra.',
  tom_days: 12,
  mf_gross_yield_pct: 4.1,
  mf_reference_rent: null,
  price_change_count: 1,
  total_price_change_pct: -5.1,
} as unknown as PropertyPublic;

const RENT = {
  ...SALE,
  id: 5005,
  property_id: 5,
  sreality_id: -4411,
  source: 'bezrealitky',
  category_type: 'pronajem',
  price_czk: 22_500,
  price_per_m2: 375,
  price_per_m2_basis: 'rent_monthly_czk_m2',
  area_m2: 60,
  disposition: '2+1',
  mf_gross_yield_pct: null,
  total_price_change_pct: null,
  tom_days: 0,
} as unknown as PropertyPublic;

const ad = (over: Partial<PropertySource>): PropertySource => ({
  property_id: 304388,
  id: 9001,
  source: 'sreality',
  source_url: 'https://www.sreality.cz/detail/prodej/byt/2+kk/praha-vysocany-freyova/2861372748',
  source_id_native: '2861372748',
  ad_reference: null,
  is_active: true,
  price_czk: 7_490_000,
  first_seen_at: '2026-09-30T08:00:00Z',
  last_seen_at: '2026-10-12T08:00:00Z',
  ...over,
});

const SALE_ADS = [
  ad({}),
  ad({
    id: 9002,
    source: 'idnes',
    source_url: 'https://reality.idnes.cz/detail/prodej/byt/praha-9/6a147cfde222cf687509e018/',
    source_id_native: '6a147cfde222cf687509e018',
  }),
];

const photo = (seq: number): ImagePublic =>
  ({
    id: seq,
    sreality_id: 2861372748,
    sequence: seq,
    sreality_url: `https://cdn.example/${seq}.jpg`,
    storage_path: `9001/000${seq}.jpg`,
    clip_fine_tag: null,
    clip_logical_tag: null,
    clip_confidence: null,
    phash: null,
  }) as ImagePublic;

const originalMatchMedia = window.matchMedia;
function setNarrow(narrow: boolean) {
  window.matchMedia = ((query: string) => ({
    matches: query === PHONE_SHELL_QUERY ? narrow : false,
    media: query,
    onchange: null,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
    dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia;
}

function renderAt(path: string | { pathname: string; state?: unknown }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="property/:propertyId" element={<PropertyDetail />} />
          <Route path="listing/:source/:nativeId" element={<AdvertRedirect />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  resetFakeMaps();
  localStorage.clear();
  setNarrow(true);
  vi.mocked(auth.useAuth).mockReturnValue({ isAdmin: false } as ReturnType<typeof auth.useAuth>);
  vi.mocked(queries.fetchProperty).mockReset().mockResolvedValue(SALE);
  vi.mocked(queries.fetchPropertySources).mockReset().mockResolvedValue(SALE_ADS);
  vi.mocked(queries.fetchImagesByListing).mockReset().mockResolvedValue([photo(1), photo(2), photo(3)]);
  vi.mocked(brokers.fetchListingBrokersByIds).mockReset().mockResolvedValue(new Map());
  openExplore.mockReset();
  openEstimation.mockReset();
});

afterEach(() => {
  window.matchMedia = originalMatchMedia;
});

/* PhoneProperty and Poloha's map are lazy chunks (the first-visit budget): import them once
 * up front so no test pays the module load inside a findBy window on a loaded runner. */
beforeAll(async () => {
  await import('./PhoneProperty');
  await import('./PhoneMap');
}, 30_000);

const LOADED = { timeout: 5_000 };

const noMaps = () => {
  expect(screen.queryByTestId('detail-map')).toBeNull();
  expect(screen.queryByTestId('comparables-map')).toBeNull();
  expect(screen.queryByText('Explore area')).toBeNull();
  expect(screen.queryByRole('button', { name: /New estimation/ })).toBeNull();
  expect(screen.queryByText(/Run an estimation/)).toBeNull();
};

describe('the phone property page', () => {
  it('draws a sale: price, its move, per-m² in Kč/m², the MF badge, facts and the ads', async () => {
    renderAt('/property/304388');
    const page = await screen.findByRole('article', { name: 'Nemovitost' }, LOADED);
    const head = within(page.querySelector('header') as HTMLElement);
    expect(plain(head.getByText(/7\s490\s000/).textContent)).toBe('7 490 000 Kč');
    expect(head.getByText((_, el) => plain(el?.textContent) === '−5 %' && el?.tagName === 'SPAN'))
      .toBeInTheDocument();
    /* Rule 23: the server's measure, under the sale basis's unit. */
    expect(head.getByText((_, el) => plain(el?.textContent) === '138 704 Kč/m²' && el?.tagName === 'SPAN'))
      .toBeInTheDocument();
    expect(head.getByText(/^Výnos MF 4,1\s%$/)).toBeInTheDocument();
    expect(head.getByText((_, el) => plain(el?.textContent) === '2+kk · 54 m² · Freyova, Praha 9 – Vysočany' && el?.tagName === 'P'))
      .toBeInTheDocument();

    /* Facts, in Czech, from the one facts definition. */
    const facts = within(screen.getByRole('region', { name: 'Fakta' }));
    expect(facts.getByText('Patro').nextSibling).toHaveTextContent('4. patro z 8 podlaží');
    expect(facts.getByText('Výtah').nextSibling).toHaveTextContent('ano');
    expect(facts.getByText('Vlastnictví').nextSibling).toHaveTextContent('Osobní');

    /* Every ad, the canonical one marked, each a same-window link to its portal. */
    const ads = within(await screen.findByRole('region', { name: 'Inzeráty' }, LOADED));
    expect(ads.getByText('hlavní inzerát')).toBeInTheDocument();
    const links = ads.getAllByRole('link', { name: /Otevřít na portálu/ });
    expect(links).toHaveLength(2);
    expect(links[0]).toHaveAttribute('href', SALE_ADS[0].source_url);
    for (const a of links) expect(a).not.toHaveAttribute('target');

    /* The gallery: 1080 px copies, "3 fotky". */
    const slides = within(screen.getByRole('group', { name: 'Fotky' })).getAllByRole('button');
    expect(slides).toHaveLength(3);
    expect(slides[0].querySelector('img')).toHaveAttribute('src', '/images/9001/0001.jpg?w=1080');
    expect(screen.getByText('3 fotky')).toBeInTheDocument();

    /* The four curation cells, and the sections reused as they are. */
    expect(screen.getByRole('button', { name: 'Přidat do pipeline' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Uložit do sbírky' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Skrýt nemovitost' })).toBeInTheDocument();
    expect(await screen.findByText('Vývoj ceny', {}, LOADED)).toBeInTheDocument();
    expect(await screen.findByText('Poznámky', {}, LOADED)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Stáhnout \(PDF \+ fotky\)/ })).toBeInTheDocument();
    await waitFor(() => expect(api.listEstimations).toHaveBeenCalled());
    noMaps();
    /* The whole page with its lazy chunks: slower than one card on a loaded runner. */
  }, 15_000);

  it('puts Poloha first after the curation row: the map, the place and the map apps', async () => {
    renderAt('/property/304388');
    const section = await screen.findByRole('region', { name: 'Poloha' }, LOADED);
    /* The first of the hairline-separated sections, straight under the curation row. */
    expect(section.parentElement?.firstElementChild).toBe(section);
    expect(await within(section).findByText('Freyova, Praha 9 – Vysočany', {}, LOADED)).toBeInTheDocument();
    expect(within(section).getAllByRole('link').map((a) => a.textContent)).toEqual([
      'Apple Maps',
      'Google Maps',
      'Mapy.cz',
    ]);
    await waitFor(() => expect(fakeCreateMap).toHaveBeenCalledTimes(1), LOADED);
    const [container, options] = fakeCreateMap.mock.calls[0];
    expect(section).toContainElement(container);
    expect(options).toMatchObject({ center: [14.5, 50.105], zoom: 15, cooperativeGestures: true });
    noMaps();
  });

  /* One pattern for the app (section cards, operator ruling 2026-10-28): every section of the
     phone page is the desktop's card — a region named by its title, a bare count beside it — 16 px
     apart, and one with nothing keeps its card and says so in one line (C10), with no action the
     phone does not offer (#1760 Q10). */
  it('draws every section as a card, 16 px apart; an empty one keeps its card in one line', async () => {
    vi.mocked(queries.fetchProperty).mockResolvedValue({ ...SALE, description: null } as PropertyPublic);
    renderAt('/property/304388');
    const card = (name: string) => screen.findByRole('region', { name }, LOADED);
    const bar = (region: HTMLElement) =>
      within(region).getByRole('heading', { level: 2 }).parentElement as HTMLElement;

    const poloha = await card('Poloha');
    expect(poloha.parentElement).toHaveClass('space-y-4');
    const ads = await card('Inzeráty');
    expect(within(bar(ads)).getByText('2')).toBeInTheDocument();
    expect(within(bar(ads)).getByText('2 portály')).toBeInTheDocument();
    expect(within(await card('Popis')).getByText('Inzerát nemá popis.')).toBeInTheDocument();
    const makleri = await card('Makléři');
    expect(await within(makleri).findByText('Makléř není uveden.', {}, LOADED)).toBeInTheDocument();
    expect(within(bar(makleri)).getByText('0')).toBeInTheDocument();
    const odhady = await card('Odhady');
    expect(await within(odhady).findByText('No estimation runs for this property yet.', {}, LOADED)).toBeInTheDocument();
    expect(within(odhady).queryByRole('button')).toBeNull();
    expect(await within(await card('Valuo')).findByText('Zatím bez ocenění.', {}, LOADED)).toBeInTheDocument();
    for (const name of ['Moje fotky', 'Fakta', 'Vývoj ceny', 'Prodané v okolí', 'Čerstvost', 'Štítky', 'Poznámky']) {
      expect(await card(name)).toBeInTheDocument();
    }
    noMaps();
  }, 15_000);

  it('draws a rent: the monthly price and per-m² in Kč/m²/měs, no MF badge', async () => {
    vi.mocked(queries.fetchProperty).mockResolvedValue(RENT);
    vi.mocked(queries.fetchPropertySources).mockResolvedValue([
      ad({ property_id: 5, id: 5005, source: 'bezrealitky', price_czk: 22_500 }),
    ]);
    renderAt('/property/5');
    const page = await screen.findByRole('article', { name: 'Nemovitost' }, LOADED);
    const head = within(page.querySelector('header') as HTMLElement);
    expect(head.getByText('/měs')).toBeInTheDocument();
    expect(head.getByText((_, el) => plain(el?.textContent) === '375 Kč/m²/měs' && el?.tagName === 'SPAN'))
      .toBeInTheDocument();
    expect(head.queryByText(/Kč\/m²$/)).toBeNull();
    expect(head.queryByText(/^MF /)).toBeNull();
    expect(head.getByText(/Nová dnes · 1 portál/)).toBeInTheDocument();
    noMaps();
  });

  it('shows no per-m² figure when the basis is missing — never a bare number', async () => {
    vi.mocked(queries.fetchProperty).mockResolvedValue({ ...SALE, price_per_m2_basis: null } as PropertyPublic);
    renderAt('/property/304388');
    const page = await screen.findByRole('article', { name: 'Nemovitost' }, LOADED);
    expect(within(page.querySelector('header') as HTMLElement).queryByText(/138\s704/)).toBeNull();
  });

  it('names the stage in the curation row, never over the photos, and offers the stage sheet', async () => {
    vi.mocked(queries.fetchPipelineMembers).mockResolvedValue(
      new Map([
        [
          304388,
          { stage_id: 2, stage_label: 'Prohlídka', stage_color: 'ochre', stage_code: 'P1', is_terminal: false },
        ],
      ]) as unknown as queries.PipelineMembers,
    );
    renderAt('/property/304388');
    const cell = await screen.findByRole('button', { name: 'Fáze Prohlídka, změnit' }, LOADED);
    expect(cell).toHaveTextContent('P1 · Prohlídka');
    /* The gallery shows the photos themselves: no stage wash, no badge or name over them. */
    const gallery = within(screen.getByRole('group', { name: 'Fotky' }));
    expect(gallery.getAllByRole('button')).toHaveLength(3);
    expect(gallery.queryByText('P1')).toBeNull();
    expect(gallery.queryByText('Prohlídka')).toBeNull();
    expect(screen.getByRole('group', { name: 'Fotky' }).querySelector('[aria-hidden="true"]')).toBeNull();
    /* A live deal is never hidden. */
    expect(screen.getByRole('button', { name: 'Rozpracovaný obchod nelze skrýt' })).toHaveAttribute(
      'aria-disabled',
      'true',
    );
    await userEvent.click(cell);
    expect(await screen.findByRole('dialog', { name: 'Přesunout do fáze' }, LOADED)).toBeInTheDocument();
  });

  it('says in Czech that a pasted advert is not in the database, with the portal link', async () => {
    vi.mocked(queries.fetchAdvertProperty).mockResolvedValue(null);
    const url = 'https://reality.bazos.cz/inzerat/196123456/byt.php';
    renderAt({ pathname: '/listing/bazos/196123456', state: { pastedUrl: url } });
    expect(await screen.findByText('Tento inzerát v databázi nemáme.', {}, LOADED)).toBeInTheDocument();
    expect(screen.getByText('196123456')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /Otevřít na portálu Bazoš/ })).toHaveAttribute('href', url);
    expect(screen.getByRole('link', { name: /Otevřít na portálu Bazoš/ })).not.toHaveAttribute('target');
  });

  it('leaves the desktop page as it was on a wide screen', async () => {
    setNarrow(false);
    renderAt('/property/304388');
    expect(await screen.findByRole('heading', { level: 1 })).toBeInTheDocument();
    expect(screen.queryByRole('article', { name: 'Nemovitost' })).toBeNull();
    expect(await screen.findByTestId('detail-map')).toBeInTheDocument();
    expect(screen.queryByRole('region', { name: 'Poloha' })).toBeNull();
  });
});

/* Operator 2026-11-06: the broker one tap away under the price: the dialer, not a copy,
 * and the contact saved as a .vcf (lib/vcard) naming the property it came from. */
describe('the broker strip', () => {
  const broker = (over: Partial<brokers.ListingBroker>): brokers.ListingBroker => ({
    primary_email: 'jan@reality.cz',
    primary_phone: '420731404040',
    sreality_id: null,
    listing_id: 9001,
    broker_id: 7,
    broker_display_name: 'Ing. Jan Novák',
    broker_firm_label: 'Reality Praha',
    ...over,
  });

  it('calls the lead broker and saves the contact, the other brokers one tap away', async () => {
    const user = userEvent.setup();
    vi.mocked(queries.fetchPropertySources).mockResolvedValue([
      ad({ ad_reference: 'RP-123' }),
      ad({ id: 9002, source: 'idnes', source_url: 'https://reality.idnes.cz/detail/x/', source_id_native: 'x' }),
    ]);
    vi.mocked(brokers.fetchListingBrokersByIds).mockResolvedValue(
      new Map([
        [9001, broker({})],
        [9002, broker({ listing_id: 9002, broker_id: 8, broker_display_name: 'Eva Malá', primary_phone: null })],
      ]),
    );
    renderAt('/property/304388');
    const strip = await screen.findByRole('region', { name: 'Makléř' }, LOADED);
    expect(within(strip).getByText('Ing. Jan Novák')).toBeInTheDocument();
    expect(within(strip).getByRole('link', { name: 'Zavolat' })).toHaveAttribute('href', 'tel:+420731404040');
    expect(within(strip).getByRole('link', { name: '+ 1 další makléř' })).toHaveAttribute('href', '#makleri');

    await user.click(within(strip).getByRole('button', { name: 'Uložit kontakt' }));
    const [blob, name] = vi.mocked(shareFiles.saveBlob).mock.calls[0];
    expect(name).toBe('Ing. Jan Novák.vcf');
    expect(blob.type).toBe('text/vcard;charset=utf-8');
    /* jsdom's Blob has no text(): read it the way FileReader does. */
    const card = await new Promise<string>((resolve) => {
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result as string);
      reader.readAsText(blob);
    });
    expect(card).toContain('N:Novák;Jan;;Ing.;');
    expect(card).toContain('TEL;TYPE=WORK,VOICE:+420731404040');
    expect(card).toContain('ORG:Reality Praha');
    expect(card).toContain('URL:http://localhost:3000/property/304388');
    expect(card.replace(/\r\n /g, '')).toMatch(/NOTE:Nemovitost: .*\\nSreality: č\. zakázky RP-123/);
  });

  it('draws nothing when the broker left no number and no e-mail', async () => {
    vi.mocked(brokers.fetchListingBrokersByIds).mockResolvedValue(
      new Map([[9001, broker({ primary_phone: null, primary_email: null })]]),
    );
    renderAt('/property/304388');
    await screen.findByRole('region', { name: 'Makléři' }, LOADED);
    await waitFor(() => expect(screen.queryByText('Načítám…')).toBeNull());
    expect(screen.queryByRole('region', { name: 'Makléř' })).toBeNull();
    expect(screen.queryByRole('link', { name: 'Zavolat' })).toBeNull();
  });
});
