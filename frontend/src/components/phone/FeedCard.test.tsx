/* The feed card (#1766) against rule 23: the per-m² figure is the server's
 * measure under the server's basis label — a sale in Kč/m², a rent in
 * Kč/m²/měs — and a row whose basis is missing or mixed shows no figure at all,
 * never a bare number. Plus the card's other promises: the 1080 px photo copy,
 * the stage wash on a filed property, the funnel's two meanings, and the photo
 * strip (a swipe turns the photos, a tap opens the property), and the map-pin
 * mark that opens Poloha. */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import * as api from '@/lib/api';
import { CardHydrationProvider } from '@/lib/hydration';
import * as queries from '@/lib/queries';
import type { CardRow, PipelineMembers } from '@/lib/queries';
import type { ImagePublic, PipelineStage } from '@/lib/types';

import FeedCard, { feedDelta, feedLine2, feedPricePerM2, portalsWord } from './FeedCard';

/* Czech formatting uses no-break spaces; compare as the eye reads it. */
const plain = (s: string | null) => s?.replace(/\u00a0/g, ' ') ?? null;

vi.mock('@/lib/queries', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/queries')>();
  return {
    ...actual,
    fetchPipelineMembers: vi.fn(),
    fetchPipelineStages: vi.fn(),
    fetchPropertyCollectionMemberSet: vi.fn(),
    fetchIsDismissed: vi.fn(),
    fetchImagesForListingIds: vi.fn(),
  };
});
vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  addPipelineCard: vi.fn(),
  apiGet: vi.fn(),
  apiGetBlob: vi.fn(),
}));
vi.mock('@/lib/imageUrl', () => ({
  imageSrc: (img: { storage_path: string | null }, opts: { w?: number } = {}) =>
    `/images/${img.storage_path}${opts.w ? `?w=${opts.w}` : ''}`,
}));

const STAGES: PipelineStage[] = [
  { id: 1, key: 'zajem', label: 'Zájem', position: 1, color: 'copper', is_terminal: false, is_entry: true, code: 'Z' },
  { id: 2, key: 'prohlidka', label: 'Prohlídka', position: 2, color: 'ochre', is_terminal: false, is_entry: false, code: 'P1' },
];

const base: CardRow = {
  property_id: 304388,
  listing_id: 9001,
  lat: 50.08,
  lng: 14.43,
  sreality_id: 123,
  display_label: 'Freyova, Praha 9 – Vysočany',
  disposition: '2+kk',
  subtype: null,
  area_m2: 54,
  price_czk: 7_490_000,
  price_per_m2: 138_704,
  price_per_m2_basis: 'sale_capital_czk_m2',
  first_seen_at: '2026-10-12T06:00:00Z',
  last_seen_at: '2026-10-12T10:00:00Z',
  is_active: true,
  tom_days: 12,
  category_main: 'byt',
  category_type: 'prodej',
  source: 'sreality',
  source_id_native: '123',
  mf_gross_yield_pct: 4.1,
  plot_area_m2: null,
  total_price_change_pct: -5.1,
  price_change_count: 1,
  all_sources: ['sreality', 'idnes', 'bazos'],
  active_sources: ['sreality', 'idnes', 'bazos'],
  source_count: 3,
};

const rent: CardRow = {
  ...base,
  property_id: 5,
  listing_id: 5005,
  lat: 50.08,
  lng: 14.43,
  disposition: '2+1',
  area_m2: 60,
  price_czk: 22_500,
  price_per_m2: 375,
  price_per_m2_basis: 'rent_monthly_czk_m2',
  category_type: 'pronajem',
  mf_gross_yield_pct: null,
  total_price_change_pct: null,
  tom_days: 0,
  all_sources: ['bezrealitky'],
  active_sources: ['bezrealitky'],
  source_count: 1,
};

const photo = (seq: number): ImagePublic => ({
  id: seq,
  sreality_id: 123,
  sequence: seq,
  sreality_url: `https://cdn.example/${seq}.jpg`,
  storage_path: `9001/000${seq}.jpg`,
  clip_fine_tag: null,
  clip_logical_tag: null,
  clip_confidence: null,
  phash: null,
});

function renderCard(r: CardRow) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={['/feed']}>
        <Routes>
          <Route
            path="/feed"
            element={
              <CardHydrationProvider listingIds={[r.listing_id]} renders={{ photos: 50 }}>
                <FeedCard r={r} coverTag="default" />
              </CardHydrationProvider>
            }
          />
          <Route path="/property/:id" element={<p>Stránka nemovitosti</p>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const photos = (n: number) => Array.from({ length: n }, (_, i) => photo(i + 1));
const strip = () => screen.getByRole('group', { name: /^Fotky/ });
const slides = () => within(strip()).getAllByRole('link');
const dots = () => strip().parentElement!.querySelectorAll('i');

/* jsdom has no layout and no PointerEvent: give the strip a width and a scroll
 * offset, and press with a MouseEvent named pointerdown (React reads it as one). */
function scrollStripTo(el: HTMLElement, left: number) {
  Object.defineProperty(el, 'clientWidth', { configurable: true, value: 390 });
  Object.defineProperty(el, 'scrollLeft', { configurable: true, writable: true, value: left });
}
const press = (el: HTMLElement, clientX: number, clientY: number) =>
  fireEvent(el, new MouseEvent('pointerdown', { bubbles: true, clientX, clientY }));

const FILED = new Map([
  [
    304388,
    {
      property_id: 304388,
      stage_id: 2,
      stage_label: 'Prohlídka',
      stage_color: 'ochre',
      stage_code: 'P1',
      stage_position: 2,
      is_terminal: false,
    },
  ],
]) as PipelineMembers;

beforeEach(() => {
  vi.mocked(queries.fetchPipelineMembers).mockResolvedValue(new Map() as PipelineMembers);
  vi.mocked(queries.fetchPipelineStages).mockResolvedValue(STAGES);
  vi.mocked(queries.fetchPropertyCollectionMemberSet).mockResolvedValue(new Map());
  vi.mocked(queries.fetchIsDismissed).mockResolvedValue(false);
  vi.mocked(queries.fetchImagesForListingIds).mockResolvedValue(new Map());
  vi.mocked(api.apiGet).mockReset();
  vi.mocked(api.apiGetBlob).mockReset();
});

describe('<FeedCard> — the per-m² figure carries its basis (rule 23)', () => {
  it('prints a sale in Kč/m² beside a capital price', () => {
    renderCard(base);
    expect(screen.getByText('7 490 000 Kč')).toBeInTheDocument();
    expect(screen.getByText('138 704 Kč/m²')).toBeInTheDocument();
    expect(screen.queryByText('/měs')).toBeNull();
  });

  it('prints a rent in Kč/m²/měs beside a monthly price', () => {
    renderCard(rent);
    expect(screen.getByText('22 500 Kč')).toBeInTheDocument();
    expect(screen.getByText('/měs')).toBeInTheDocument();
    expect(screen.getByText('375 Kč/m²/měs')).toBeInTheDocument();
  });

  it('shows no per-m² figure when the basis is missing or mixed — never a bare number', () => {
    const { container, unmount } = renderCard({ ...base, price_per_m2_basis: null });
    expect(container.textContent).not.toMatch(/Kč\/m²/);
    expect(container.textContent).not.toMatch(/138\s704/);
    unmount();
    expect(feedPricePerM2({ price_per_m2: 138_704, price_per_m2_basis: 'mixed' })).toBeNull();
    expect(feedPricePerM2({ price_per_m2: null, price_per_m2_basis: 'sale_capital_czk_m2' })).toBeNull();
    expect(plain(feedPricePerM2({ price_per_m2: 2450, price_per_m2_basis: 'land_capital_czk_m2' }))).toBe(
      '2 450 Kč/m² pozemku',
    );
  });
});

describe('<FeedCard> — the lines', () => {
  it('reads layout · area · place, then the portals, days, move and MF yield', () => {
    renderCard(base);
    expect(screen.getByText('2+kk · 54 m² · Freyova, Praha 9 – Vysočany')).toBeInTheDocument();
    expect(screen.getByText('Sreality')).toBeInTheDocument();
    expect(screen.getByText('iDNES')).toBeInTheDocument();
    expect(screen.getByText('Bazoš')).toBeInTheDocument();
    expect(screen.getByText('3 portály')).toBeInTheDocument();
    expect(screen.getByText('12 dní')).toBeInTheDocument();
    expect(screen.getByText('−5 %')).toBeInTheDocument();
    expect(screen.getByText('Výnos MF 4,1 %')).toBeInTheDocument();
  });

  it('keeps the MF badge to sale flats and says "dnes" for a fresh ad', () => {
    renderCard(rent);
    expect(screen.queryByText(/^MF /)).toBeNull();
    expect(screen.getByText('dnes')).toBeInTheDocument();
    expect(screen.getByText('1 portál')).toBeInTheDocument();
  });

  it('names the lot after the floor area, and a pozemek area once', () => {
    const house = { ...base, category_main: 'dum', disposition: '5+1', area_m2: 180, plot_area_m2: 850 };
    expect(plain(feedLine2(house))).toBe('Dům 5+1 · 180 m² · 850 m² pozemku · Freyova, Praha 9 – Vysočany');
    const land = { ...base, category_main: 'pozemek', disposition: null, area_m2: 1200, plot_area_m2: 1200 };
    expect(plain(feedLine2(land))).toBe('Pozemek · 1 200 m² pozemku · Freyova, Praha 9 – Vysočany');
    expect(plain(feedLine2({ ...base, plot_area_m2: null }))).toBe('2+kk · 54 m² · Freyova, Praha 9 – Vysočany');
  });

  it('formats the price move and the portal count in Czech', () => {
    expect(plain(feedDelta(-5.1))).toBe('−5 %');
    expect(plain(feedDelta(3))).toBe('+3 %');
    expect(plain(feedDelta(-0.4))).toBe('−0,4 %');
    expect(feedDelta(0)).toBeNull();
    expect(feedDelta(null)).toBeNull();
    expect(plain(portalsWord(5))).toBe('5 portálů');
  });

  it('opens the property page from the price link stretched over the text, not the photos', () => {
    renderCard(base);
    const link = screen.getByRole('link', { name: /7\s490\s000\sKč, 2\+kk/ });
    expect(link).toHaveAttribute('href', expect.stringContaining('/property/304388'));
    expect(link).toHaveClass('stretched-link');
    /* The ::after resolves against the nearest positioned box: the text body. A
     * link stretched over the strip would swallow every swipe. */
    const body = link.closest('.relative') as HTMLElement;
    expect(body.tagName).toBe('DIV');
    expect(within(body).queryByRole('group')).toBeNull();
  });
});

describe('<FeedCard> — photo, wash, funnel', () => {
  it('loads the 1080 px copy of the cover, lazily, and counts the photos', async () => {
    vi.mocked(queries.fetchImagesForListingIds).mockResolvedValue(
      new Map([[9001, [photo(1), photo(2), photo(3)]]]),
    );
    const { container } = renderCard(base);
    await waitFor(() => expect(container.querySelector('img')).not.toBeNull());
    const img = container.querySelector('img')!;
    expect(img).toHaveAttribute('src', '/images/9001/0001.jpg?w=1080');
    expect(img).toHaveAttribute('loading', 'lazy');
    expect(screen.getByText(/fot\./).textContent).toMatch(/3/);
  });

  it('adds an unfiled property at the entry stage with one tap', async () => {
    vi.mocked(api.addPipelineCard).mockResolvedValue(undefined as never);
    renderCard(base);
    await userEvent.click(await screen.findByRole('button', { name: 'Přidat do pipeline' }));
    expect(api.addPipelineCard).toHaveBeenCalledWith(304388);
  });

  it('washes a filed photo in its stage and opens the stage sheet, never removing', async () => {
    vi.mocked(queries.fetchPipelineMembers).mockResolvedValue(FILED);
    const { container } = renderCard(base);
    const funnel = await screen.findByRole('button', { name: 'Fáze Prohlídka, změnit' });
    /* The wash: the stage's code set large and its name, over the photo. */
    const wash = container.querySelector('[aria-hidden="true"].pointer-events-none') as HTMLElement;
    expect(within(wash).getByText('P1')).toBeInTheDocument();
    expect(within(wash).getByText('Prohlídka')).toBeInTheDocument();
    /* A live deal cannot be hidden. */
    expect(screen.getByRole('button', { name: 'Rozpracovaný obchod nelze skrýt' })).toHaveAttribute(
      'aria-disabled',
      'true',
    );
    await userEvent.click(funnel);
    const sheet = await screen.findByRole('dialog', { name: 'Přesunout do fáze' });
    expect(within(sheet).getByRole('menuitemradio', { name: /Prohlídka/ })).toHaveAttribute(
      'aria-checked',
      'true',
    );
    /* The remove is two-step, as everywhere (rule 22). */
    await userEvent.click(within(sheet).getByRole('menuitem', { name: 'Odebrat z pipeline' }));
    expect(within(sheet).getByText('Odebrat z pipeline?')).toBeInTheDocument();
  });
});

describe('<FeedCard> — the photo strip', () => {
  it('draws a slide per photo; the cover and the next load, the rest as the swipe nears them', async () => {
    vi.mocked(queries.fetchImagesForListingIds).mockResolvedValue(new Map([[9001, photos(5)]]));
    renderCard(base);
    await waitFor(() => expect(slides()).toHaveLength(5));
    for (const slide of slides()) {
      expect(slide).toHaveAttribute('href', expect.stringContaining('/property/304388'));
      expect(slide).toHaveAttribute('tabindex', '-1');
    }
    const el = strip();
    let imgs = el.querySelectorAll('img');
    expect(imgs).toHaveLength(2);
    expect(imgs[0]).toHaveAttribute('src', '/images/9001/0001.jpg?w=1080');
    expect(imgs[1]).toHaveAttribute('src', '/images/9001/0002.jpg?w=1080');
    for (const img of imgs) expect(img).toHaveAttribute('loading', 'lazy');
    expect(dots()).toHaveLength(5);
    expect(dots()[0]).toHaveAttribute('data-dot', 'on');

    scrollStripTo(el, 390);
    fireEvent.scroll(el);
    await waitFor(() => expect(el.querySelectorAll('img')).toHaveLength(3));
    imgs = el.querySelectorAll('img');
    expect(imgs[2]).toHaveAttribute('src', '/images/9001/0003.jpg?w=1080');
    expect(imgs[2]).toHaveAttribute('loading', 'lazy');
    expect(dots()[1]).toHaveAttribute('data-dot', 'on');
    expect(dots()[0]).not.toHaveAttribute('data-dot');
  });

  it('stops at eight and ends on "all N photos" when the ad has more', async () => {
    vi.mocked(queries.fetchImagesForListingIds).mockResolvedValue(new Map([[9001, photos(12)]]));
    renderCard(base);
    await waitFor(() => expect(slides()).toHaveLength(9));
    const last = slides()[8];
    expect(last).toHaveTextContent('Zobrazit všech 12 fotek');
    expect(last).toHaveAttribute('href', expect.stringContaining('/property/304388'));
    expect(dots()).toHaveLength(8);
    expect(screen.getByText(/fot\./).textContent).toMatch(/12/);
  });

  it('draws neither dots nor an end slide for a single photo', async () => {
    vi.mocked(queries.fetchImagesForListingIds).mockResolvedValue(new Map([[9001, photos(1)]]));
    renderCard(base);
    await waitFor(() => expect(slides()).toHaveLength(1));
    expect(dots()).toHaveLength(0);
    expect(screen.queryByText(/Zobrazit všech/)).toBeNull();
  });

  it('opens the property on a tap, never on a swipe', async () => {
    vi.mocked(queries.fetchImagesForListingIds).mockResolvedValue(new Map([[9001, photos(3)]]));
    renderCard(base);
    await waitFor(() => expect(slides()).toHaveLength(3));
    const el = strip();

    /* A drag across the photo: the click at its end is swallowed. */
    press(el, 100, 120);
    fireEvent.click(slides()[0], { clientX: 160, clientY: 122 });
    expect(screen.queryByText('Stránka nemovitosti')).toBeNull();

    /* A gesture that moved the strip, wherever it ended: swallowed too. */
    press(el, 100, 120);
    scrollStripTo(el, 390);
    fireEvent.click(slides()[1], { clientX: 100, clientY: 120 });
    expect(screen.queryByText('Stránka nemovitosti')).toBeNull();

    /* A tap: opens the property. */
    press(el, 100, 120);
    fireEvent.click(slides()[1], { clientX: 103, clientY: 121 });
    expect(await screen.findByText('Stránka nemovitosti')).toBeInTheDocument();
  });

  it('lays the stage wash over every photo of a filed property', async () => {
    vi.mocked(queries.fetchPipelineMembers).mockResolvedValue(FILED);
    vi.mocked(queries.fetchImagesForListingIds).mockResolvedValue(new Map([[9001, photos(4)]]));
    renderCard(base);
    await waitFor(() => expect(within(slides()[0]).queryByText('P1')).not.toBeNull());
    expect(slides()).toHaveLength(4);
    for (const slide of slides()) {
      expect(within(slide).getByText('P1')).toBeInTheDocument();
      expect(within(slide).getByText('Prohlídka')).toBeInTheDocument();
    }
  });
});

describe('<FeedCard> — Poloha', () => {
  it('carries the map-pin mark on every card, under the eye', () => {
    for (const r of [base, rent]) {
      const { unmount } = renderCard(r);
      const pin = screen.getByRole('button', { name: 'Poloha' });
      expect(pin).toHaveAttribute('aria-haspopup', 'dialog');
      /* A tick between, then the eye. */
      expect(pin.previousElementSibling?.previousElementSibling).toHaveAttribute(
        'aria-label',
        'Skrýt nemovitost',
      );
      unmount();
    }
    /* Nothing is read until the sheet opens. */
    expect(api.apiGet).not.toHaveBeenCalled();
    expect(api.apiGetBlob).not.toHaveBeenCalled();
  });

  it('opens the Poloha sheet with the map, the place and the three map apps', async () => {
    URL.createObjectURL = vi.fn(() => 'blob:map');
    URL.revokeObjectURL = vi.fn();
    vi.mocked(api.apiGet).mockResolvedValue({
      lat: 50.105,
      lon: 14.5,
      granularity: 'street',
      granularity_rank: 60,
      label: 'Freyova, Praha 9 – Vysočany',
      zoom: 16,
      note: null,
    });
    vi.mocked(api.apiGetBlob).mockResolvedValue(new Blob(['png'], { type: 'image/png' }));
    renderCard(base);
    await userEvent.click(screen.getByRole('button', { name: 'Poloha' }));
    const sheet = await screen.findByRole('dialog', { name: 'Poloha' });
    expect(api.apiGet).toHaveBeenCalledWith('/properties/304388/location', undefined, expect.anything());
    expect(await within(sheet).findByRole('img')).toHaveAttribute('src', 'blob:map');
    expect(within(sheet).getAllByRole('link').map((a) => a.textContent)).toEqual([
      'Apple Maps',
      'Google Maps',
      'Mapy.cz',
    ]);
  });

  it('says "Bez polohy" when the property has no point', async () => {
    const none = new api.ApiError('no_location', 404, { detail: { reason: 'no_location' } });
    vi.mocked(api.apiGet).mockRejectedValue(none);
    vi.mocked(api.apiGetBlob).mockRejectedValue(none);
    renderCard(base);
    await userEvent.click(screen.getByRole('button', { name: 'Poloha' }));
    const sheet = await screen.findByRole('dialog', { name: 'Poloha' });
    expect(await within(sheet).findByText('Bez polohy')).toBeInTheDocument();
    expect(within(sheet).queryByRole('link')).toBeNull();
  });
});
