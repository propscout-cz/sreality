/* Prodeje v domě: held sales and bought deeds in one list; the one paid action shows its price and sends it; a
 * building with nothing to show and nothing to ask draws no card. */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

vi.mock('@/lib/api', async (orig) => ({
  ...(await orig<typeof import('@/lib/api')>()),
  fetchPropertyBuildingSales: vi.fn(),
  fetchBuildingPrices: vi.fn(),
  buyBuildingPrices: vi.fn(),
}));

import {
  ApiError, buyBuildingPrices, fetchBuildingPrices, fetchPropertyBuildingSales, type BuildingPrices, type BuildingSale,
} from '@/lib/api';
import BuildingSalesCard from './BuildingSalesCard';

const sales = vi.mocked(fetchPropertyBuildingSales);
const prices = vi.mocked(fetchBuildingPrices);
const buy = vi.mocked(buyBuildingPrices);

const SALE: BuildingSale = {
  source: 'reas', source_record_id: '1_unit_22906291-105-37', sold_at: '2025-03-14', price_czk: 6450000,
  asking_last_czk: null, category_main: 'byt', disposition: '3+kk', area_m2: 62, unit_label: '105/37',
  source_url: 'https://example.test/x', price_per_m2: 104032.26, price_per_m2_basis: 'sale_capital_czk_m2',
};
const NONE: BuildingPrices = {
  available: false, login_set: false, login_refused: false, price_czk: 50, purchase: null, deeds: [],
};
const READY: BuildingPrices = { ...NONE, available: true, login_set: true };
const BOUGHT: BuildingPrices = {
  ...READY,
  purchase: { status: 'done', cost_czk: 50, object_count: 46, requested_at: '2026-11-22T10:00:00Z',
              finished_at: '2026-11-22T10:00:09Z' },
  deeds: [{ proceeding: 'V-1/2024-101', kind: 'Smlouva kupní', price: 7300000, currency: 'CZK',
            signed_on: '2024-01-30', effective_on: '2024-02-02', entered_on: '2024-02-28',
            properties: [{ kind: 'jednotka', iskn_id: 1, label: '565/31' }, { kind: 'parcela', iskn_id: 3, label: 'St. 115' }] }],
};

function mount() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}><BuildingSalesCard propertyId={7} /></QueryClientProvider>);
}

beforeEach(() => {
  sales.mockReset().mockResolvedValue({ sales: [] });
  prices.mockReset().mockResolvedValue(NONE);
  buy.mockReset().mockResolvedValue(BOUGHT);
});

describe('<BuildingSalesCard>', () => {
  it('shows a held sale with its unit, price, per-m² and source, and no buy button without a building', async () => {
    sales.mockResolvedValue({ sales: [SALE] });
    mount();
    expect(await screen.findByText('105/37')).toBeInTheDocument();
    expect(screen.getByText(/6\s450\s000/)).toBeInTheDocument();
    expect(screen.getByText(/104\s032/)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Reas' })).toHaveAttribute('href', SALE.source_url);
    expect(screen.queryByRole('button')).toBeNull();
  });

  it('draws nothing with no sale and nothing to ask', async () => {
    const { container } = mount();
    await waitFor(() => expect(prices).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });

  it('never buys on one click: the button opens a step that names the price, and only that step sends it', async () => {
    prices.mockResolvedValue(READY);
    mount();
    fireEvent.click(await screen.findByRole('button', { name: 'Načíst ceny z katastru · 50 Kč' }));
    expect(buy).not.toHaveBeenCalled();
    const step = screen.getByRole('group', { name: 'Potvrzení placeného načtení' });
    expect(step).toHaveTextContent('stojí 50 Kč na vašem účtu u ČÚZK');
    expect(step).toHaveTextContent('Platí se i tehdy, když katastr k budově žádnou cenu neeviduje');
    fireEvent.click(screen.getByRole('button', { name: 'Zrušit' }));
    expect(screen.queryByRole('group', { name: 'Potvrzení placeného načtení' })).toBeNull();
    expect(buy).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Načíst ceny z katastru · 50 Kč' }));
    fireEvent.click(screen.getByRole('button', { name: 'Zaplatit 50 Kč a načíst' }));
    await waitFor(() => expect(buy).toHaveBeenCalledWith(7, 50, false));
    expect(await screen.findByText('565/31')).toBeInTheDocument();
    expect(screen.getByText('+ pozemek')).toBeInTheDocument();
    expect(screen.getByText(/vidí jen\s+váš účet/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Načíst znovu z katastru · 50 Kč' })).toBeInTheDocument();
  });

  it('pays again for a building already bought only from a step that names the earlier purchase', async () => {
    prices.mockResolvedValue(BOUGHT);
    mount();
    fireEvent.click(await screen.findByRole('button', { name: 'Načíst znovu z katastru · 50 Kč' }));
    expect(buy).not.toHaveBeenCalled();
    expect(screen.getByText(/Ceny z katastru už máte \(načteny 22\/11\/2026\)\. Nové načtení stojí dalších 50 Kč/))
      .toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Zaplatit 50 Kč a načíst' }));
    await waitFor(() => expect(buy).toHaveBeenCalledWith(7, 50, true));
  });

  it('finishes a purchase in flight with one priced click, and says it is the same order', async () => {
    const ordered = { ...READY, purchase: { ...BOUGHT.purchase!, status: 'ordered' as const, finished_at: null } };
    prices.mockResolvedValue(ordered);
    buy.mockResolvedValue(ordered);
    mount();
    expect(await screen.findByText(/stále\s+táž objednávka za 50 Kč/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Dokončit načtení z katastru · 50 Kč' }));
    await waitFor(() => expect(buy).toHaveBeenCalledWith(7, 50, false));
    /* ČÚZK is still preparing: the answer is the same state, and the card keeps its button and its note. */
    expect(await screen.findByRole('button', { name: 'Dokončit načtení z katastru · 50 Kč' })).toBeInTheDocument();
    expect(screen.getByText(/ČÚZK zprávu připravuje/)).toBeInTheDocument();
  });

  it('offers no paid button on a login ČÚZK refused, and says the ČÚZK account is not locked', async () => {
    prices.mockResolvedValue({ ...READY, login_refused: true });
    mount();
    expect(await screen.findByText(/ČÚZK vaše uložené přihlášení odmítl/)).toBeInTheDocument();
    expect(screen.getByText(/zablokován není/)).toBeInTheDocument();
    expect(screen.queryByRole('button')).toBeNull();
  });

  it('says a finished purchase found no price, instead of "not loaded"', async () => {
    prices.mockResolvedValue({ ...BOUGHT, deeds: [] });
    mount();
    expect(await screen.findByText(/Katastr k této budově neeviduje žádnou cenu \(načteno 22\/11\/2026\)/))
      .toBeInTheDocument();
    expect(screen.queryByText(/Z katastru zatím nenačteno/)).toBeNull();
  });

  it('offers a free re-read of a report that was paid for and not read, never a new purchase', async () => {
    prices.mockResolvedValue({ ...READY, purchase: { ...BOUGHT.purchase!, status: 'paid_unread' } });
    mount();
    expect(await screen.findByText(/budovu znovu nekupujte/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Zkusit přečíst znovu (zdarma)' }));
    await waitFor(() => expect(buy).toHaveBeenCalledWith(7, 50, false));
    expect(screen.queryByRole('group', { name: 'Potvrzení placeného načtení' })).toBeNull();
  });

  it('asks for the ČÚZK login first and offers no button without it', async () => {
    prices.mockResolvedValue({ ...READY, login_set: false });
    mount();
    expect(await screen.findByText(/po uložení přihlášení k ČÚZK/)).toBeInTheDocument();
    expect(screen.queryByRole('button')).toBeNull();
  });

  it('says why a request failed', async () => {
    prices.mockResolvedValue(READY);
    buy.mockRejectedValue(new ApiError('ČÚZK did not answer.', 502, null, 'http'));
    mount();
    fireEvent.click(await screen.findByRole('button', { name: /Načíst ceny/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Zaplatit 50 Kč a načíst' }));
    expect(await screen.findByText(/ČÚZK did not answer/)).toBeInTheDocument();
  });
});
