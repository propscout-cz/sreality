/* Prodeje v domě: held sales and bought deeds in one list. The paid action never leaves on one click; a purchase in
 * flight is continued by its id and nothing else; every state says truthfully whether money moved and offers a way
 * forward; a building with nothing to show and nothing to ask draws no card. */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

vi.mock('@/lib/api', async (orig) => ({
  ...(await orig<typeof import('@/lib/api')>()),
  fetchPropertyBuildingSales: vi.fn(),
  fetchBuildingPrices: vi.fn(),
  buyBuildingPrices: vi.fn(),
  cancelBuildingPurchase: vi.fn(),
  buyBuildingUnit: vi.fn(),
  cancelBuildingUnitPurchase: vi.fn(),
}));

import {
  ApiError, buyBuildingPrices, buyBuildingUnit, cancelBuildingPurchase, cancelBuildingUnitPurchase, fetchBuildingPrices,
  fetchPropertyBuildingSales, type BuildingPrices, type BuildingSale, type CadastreUnit,
} from '@/lib/api';
import BuildingSalesCard from './BuildingSalesCard';

const sales = vi.mocked(fetchPropertyBuildingSales);
const prices = vi.mocked(fetchBuildingPrices);
const buy = vi.mocked(buyBuildingPrices);
const cancel = vi.mocked(cancelBuildingPurchase);
const buyUnit = vi.mocked(buyBuildingUnit);
const cancelUnit = vi.mocked(cancelBuildingUnitPurchase);

const SALE: BuildingSale = {
  source: 'reas', source_record_id: '1_unit_22906291-105-37', sold_at: '2025-03-14', price_czk: 6450000,
  asking_last_czk: null, category_main: 'byt', disposition: '3+kk', area_m2: 62, unit_label: '105/37',
  source_url: 'https://example.test/x', price_per_m2: 104032.26, price_per_m2_basis: 'sale_capital_czk_m2',
};
const NONE: BuildingPrices = {
  available: false, login_set: false, login_refused: false, running: false, price_czk: 50, purchase: null, deeds: [],
};
const READY: BuildingPrices = { ...NONE, available: true, login_set: true };
const PURCHASE: NonNullable<BuildingPrices['purchase']> = {
  id: 31, status: 'done', cost_czk: 50, object_count: 46, fetch_sent: true, file_kept: false, fetching: false,
  gone: false,
  requested_at: '2026-11-22T10:00:00Z', finished_at: '2026-11-22T10:00:09Z',
};
const BOUGHT: BuildingPrices = {
  ...READY,
  purchase: PURCHASE,
  deeds: [{ proceeding: 'V-1/2024-101', kind: 'Smlouva kupní', price: 7300000, currency: 'CZK',
            signed_on: '2024-01-30', effective_on: '2024-02-02', entered_on: '2024-02-28',
            properties: [{ kind: 'jednotka', iskn_id: 1, label: '565/31' }, { kind: 'parcela', iskn_id: 3, label: 'St. 115' }] }],
};
const ORDERED: BuildingPrices = {
  ...READY, purchase: { ...PURCHASE, status: 'ordered', cost_czk: null, fetch_sent: false, finished_at: null },
};
const STEP = 'Potvrzení placeného načtení';
const UNIT_STEP = 'Potvrzení placeného načtení jednotky';
/* A deed over one unit, and the 10 Kč question about that unit. */
const ONE_UNIT: BuildingPrices = {
  ...BOUGHT, unit_price_czk: 10, units: [],
  deeds: [{ ...BOUGHT.deeds[0], properties: [{ kind: 'jednotka', iskn_id: 77, label: '565/31' }] }],
};
const UNIT_PURCHASE: CadastreUnit['purchase'] = {
  id: 41, status: 'done', cost_czk: 10, fetch_sent: true, file_kept: false, fetching: false, gone: false,
};
const FACTS: NonNullable<CadastreUnit['facts']> = {
  floor_area_m2: null, area_state: 'empty', shape: [], use_code: '1', use_label: 'byt', share_num: 344, share_den: 35966,
};
const withUnit = (unit: Partial<CadastreUnit>): BuildingPrices => ({
  ...ONE_UNIT, units: [{ unit_iskn_id: 77, purchase: UNIT_PURCHASE, facts: null, ...unit }],
});

function mount() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}><BuildingSalesCard propertyId={7} /></QueryClientProvider>);
}

beforeEach(() => {
  sales.mockReset().mockResolvedValue({ sales: [] });
  prices.mockReset().mockResolvedValue(NONE);
  buyUnit.mockReset();
  cancelUnit.mockReset();
  buy.mockReset().mockResolvedValue(BOUGHT);
  cancel.mockReset().mockResolvedValue(READY);
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
    const step = screen.getByRole('group', { name: STEP });
    expect(step).toHaveTextContent('stojí 50 Kč na vašem účtu u ČÚZK');
    expect(step).toHaveTextContent('Platí se i tehdy, když katastr k budově žádnou cenu neeviduje');
    fireEvent.click(screen.getByRole('button', { name: 'Zrušit' }));
    expect(screen.queryByRole('group', { name: STEP })).toBeNull();
    expect(buy).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Načíst ceny z katastru · 50 Kč' }));
    fireEvent.click(screen.getByRole('button', { name: 'Zaplatit 50 Kč a načíst' }));
    await waitFor(() => expect(buy).toHaveBeenCalledWith(7, 50, { repeat: false }));
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
    await waitFor(() => expect(buy).toHaveBeenCalledWith(7, 50, { repeat: true }));
  });

  it('continues a purchase in flight by its id, on a button that names the price, and never as a new purchase', async () => {
    prices.mockResolvedValue(ORDERED);
    buy.mockResolvedValue(ORDERED);
    mount();
    expect(await screen.findByText(/Dokončením zaplatíte nejvýše jednou, 50 Kč/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Dokončit načtení z katastru · 50 Kč' }));
    await waitFor(() => expect(buy).toHaveBeenCalledWith(7, 50, { resume: 31 }));
    /* ČÚZK is still preparing: the answer is the same state, and the card keeps its button and its note. */
    expect(await screen.findByRole('button', { name: 'Dokončit načtení z katastru · 50 Kč' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Načíst (ceny|znovu)/ })).toBeNull();
  });

  it('gives a purchase in flight up by its id, which buys nothing', async () => {
    prices.mockResolvedValue(ORDERED);
    mount();
    expect(await screen.findByText(/můžete toto načtení zrušit; tím se nic neodesílá\s+a nic se neúčtuje/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Zrušit toto načtení' }));
    await waitFor(() => expect(cancel).toHaveBeenCalledWith(7, 31));
    expect(buy).not.toHaveBeenCalled();
    expect(await screen.findByRole('button', { name: 'Načíst ceny z katastru · 50 Kč' })).toBeInTheDocument();
  });

  it('says a lost answer may have been charged, and finishes the same report at no further cost', async () => {
    prices.mockResolvedValue({ ...ORDERED, purchase: { ...ORDERED.purchase!, fetch_sent: true } });
    mount();
    expect(await screen.findByText(/mohla být zaúčtována \(50 Kč\)\. Nezačínejte nový nákup/)).toBeInTheDocument();
    expect(screen.getByText(/sestava zůstane vedená jako možná zaplacená/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Dokončit načtení (celkem nejvýše 50 Kč)' }));
    await waitFor(() => expect(buy).toHaveBeenCalledWith(7, 50, { resume: 31 }));
  });

  it('offers nothing while a run of the account is live, and keeps reading the state', async () => {
    prices.mockResolvedValue({ ...ORDERED, running: true });
    mount();
    expect(await screen.findByText(/Načítání z katastru právě probíhá/)).toBeInTheDocument();
    expect(screen.queryByRole('button')).toBeNull();
    expect(screen.queryByText(/Dokončením zaplatíte/)).toBeNull();
  });

  it('offers nothing more while its own request is on its way', async () => {
    prices.mockResolvedValue(ORDERED);
    buy.mockReturnValue(new Promise(() => {}));
    mount();
    fireEvent.click(await screen.findByRole('button', { name: 'Dokončit načtení z katastru · 50 Kč' }));
    expect(await screen.findByText(/Načítání z katastru právě probíhá/)).toBeInTheDocument();
    expect(screen.queryByRole('button')).toBeNull();
    expect(buy).toHaveBeenCalledTimes(1);
  });

  it('offers no paid button on a login ČÚZK refused, and says the ČÚZK account is not locked', async () => {
    prices.mockResolvedValue({ ...READY, login_refused: true });
    mount();
    expect(await screen.findByText(/ČÚZK vaše uložené přihlášení odmítl/)).toBeInTheDocument();
    expect(screen.getByText(/aby se váš účet u ČÚZK\s+nezablokoval/)).toBeInTheDocument();
    expect(screen.queryByRole('button')).toBeNull();
  });

  it('asks for the ČÚZK login first and offers no button without it', async () => {
    prices.mockResolvedValue({ ...READY, login_set: false });
    mount();
    expect(await screen.findByText(/po uložení přihlášení k ČÚZK/)).toBeInTheDocument();
    expect(screen.queryByRole('button')).toBeNull();
  });

  it('says a finished purchase found no price, instead of "not loaded"', async () => {
    prices.mockResolvedValue({ ...BOUGHT, deeds: [] });
    mount();
    expect(await screen.findByText(/V sestavě z katastru jsme nenašli žádný prodej \(načteno 22\/11\/2026\)/))
      .toBeInTheDocument();
    expect(screen.queryByText(/Z katastru zatím nenačteno/)).toBeNull();
  });

  it('re-reads a kept report for free, and a new purchase of that building still takes the step', async () => {
    prices.mockResolvedValue({ ...READY, purchase: { ...PURCHASE, status: 'paid_unread', file_kept: true } });
    mount();
    expect(await screen.findByText(/Je u nás uložená; přečíst ji lze zkusit znovu bez placení/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Zkusit přečíst znovu (zdarma)' }));
    await waitFor(() => expect(buy).toHaveBeenCalledWith(7, 50, { resume: 31 }));
  });

  it('says truthfully that a report with nothing kept may have been charged, fetches it again for free or buys anew', async () => {
    const lost = { ...READY, purchase: { ...PURCHASE, status: 'paid_unread' as const, cost_czk: null, file_kept: false } };
    prices.mockResolvedValue(lost);
    buy.mockResolvedValue(lost);
    mount();
    expect(await screen.findByText(/mohla být zaúčtována, ale nemáme ji/)).toBeInTheDocument();
    expect(screen.queryByText(/Je u nás uložená/)).toBeNull();
    expect(screen.getByText(/nezaplatíte celkem víc než 50 Kč/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Zkusit vyzvednout znovu (celkem nejvýše 50 Kč)' }));
    await waitFor(() => expect(buy).toHaveBeenLastCalledWith(7, 50, { resume: 31 }));
    fireEvent.click(await screen.findByRole('button', { name: 'Načíst znovu z katastru · 50 Kč' }));
    expect(screen.getByRole('group', { name: STEP })).toHaveTextContent('Za tuto budovu jste už jednou platili');
    fireEvent.click(screen.getByRole('button', { name: 'Zaplatit 50 Kč a načíst' }));
    await waitFor(() => expect(buy).toHaveBeenLastCalledWith(7, 50, { repeat: true }));
  });

  it('waits, with no button, while a fetch sent moments ago may still be being served', async () => {
    prices.mockResolvedValue({ ...ORDERED, purchase: { ...ORDERED.purchase!, fetch_sent: true, fetching: true } });
    mount();
    expect(await screen.findByText(/mezitím se nic znovu neodesílá, aby nemohla být zaúčtována dvakrát/)).toBeInTheDocument();
    expect(screen.queryByRole('button')).toBeNull();
  });

  it('says a report ČÚZK deleted cannot be tried again, and offers only the confirmed new purchase', async () => {
    prices.mockResolvedValue({
      ...READY, purchase: { ...PURCHASE, status: 'paid_unread', cost_czk: null, file_kept: false, gone: true },
    });
    mount();
    expect(await screen.findByText(/už nenabízí \(je smazaná\).*ověříte to ve vyúčtování svého účtu u ČÚZK/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Zkusit/ })).toBeNull();
    expect(screen.getByRole('button', { name: 'Načíst znovu z katastru · 50 Kč' })).toBeInTheDocument();
  });

  it('says so when a request did nothing because another one is using the login', async () => {
    prices.mockResolvedValue(READY);
    buy.mockResolvedValue({ ...READY, notice: 'busy' });
    mount();
    fireEvent.click(await screen.findByRole('button', { name: /Načíst ceny/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Zaplatit 50 Kč a načíst' }));
    expect(await screen.findByText(/právě používá jiný požadavek, proto se teď nic neprovedlo/)).toBeInTheDocument();
  });

  it('gives up and re-reads a kept file without a stored login, and offers nothing that needs one', async () => {
    prices.mockResolvedValue({ ...ORDERED, login_set: false });
    mount();
    expect(await screen.findByRole('button', { name: 'Zrušit toto načtení' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Dokončit/ })).toBeNull();
  });

  it('offers a free re-read of a report in which no sale was found, when its file is kept', async () => {
    prices.mockResolvedValue({ ...BOUGHT, deeds: [], purchase: { ...PURCHASE, file_kept: true } });
    mount();
    fireEvent.click(await screen.findByRole('button', { name: 'Zkusit přečíst znovu (zdarma)' }));
    await waitFor(() => expect(buy).toHaveBeenCalledWith(7, 50, { resume: 31 }));
  });

  it('shows the server\'s own words when a request fails, and reads the state again', async () => {
    prices.mockResolvedValue(READY);
    buy.mockRejectedValue(new ApiError('ČÚZK neodpověděl nebo odpověděl chybou.', 502, null, 'http'));
    mount();
    fireEvent.click(await screen.findByRole('button', { name: /Načíst ceny/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Zaplatit 50 Kč a načíst' }));
    expect(await screen.findByText(/ČÚZK neodpověděl nebo odpověděl chybou/)).toBeInTheDocument();
    await waitFor(() => expect(prices.mock.calls.length).toBeGreaterThan(1));
  });

  it('never buys a unit\'s information on one click: the step names 10 Kč and that the area may be missing', async () => {
    prices.mockResolvedValue(ONE_UNIT);
    buyUnit.mockResolvedValue(withUnit({ facts: FACTS }));
    mount();
    fireEvent.click(await screen.findByRole('button', { name: 'z katastru · 10 Kč' }));
    expect(buyUnit).not.toHaveBeenCalled();
    const step = screen.getByRole('group', { name: UNIT_STEP });
    expect(step.textContent).toContain('565/31');
    expect(step.textContent).toContain('10 Kč');
    expect(step.textContent).toContain('platí se i tak');
    fireEvent.click(screen.getByRole('button', { name: 'Zaplatit 10 Kč a načíst' }));
    await waitFor(() => expect(buyUnit).toHaveBeenCalledWith(7, 77, 10, { unit: 77, repeat: false }));
    expect(await screen.findByText(/katastr plochu neeviduje · podíl 344\/35966/)).toBeTruthy();
    expect(buy).not.toHaveBeenCalled();
  });

  it('keeps "the cadastre records none" apart from "we could not find it"', async () => {
    prices.mockResolvedValue(withUnit({ facts: { ...FACTS, area_state: 'unknown' } }));
    mount();
    expect(await screen.findByText(/plochu se v sestavě nepodařilo zjistit/)).toBeTruthy();
    expect(screen.queryByText(/katastr plochu neeviduje/)).toBeNull();
    expect(screen.queryByRole('button', { name: /z katastru · 10 Kč/ })).toBeNull();
  });

  it('continues or gives up a unit\'s purchase in flight by its id, and never starts another', async () => {
    prices.mockResolvedValue(withUnit({ purchase: { ...UNIT_PURCHASE, status: 'ordered', cost_czk: null, fetch_sent: false } }));
    buyUnit.mockResolvedValue(ONE_UNIT);
    cancelUnit.mockResolvedValue(ONE_UNIT);
    mount();
    fireEvent.click(await screen.findByRole('button', { name: 'dokončit (10 Kč)' }));
    await waitFor(() => expect(buyUnit).toHaveBeenCalledWith(7, 77, 10, { unit: 77, resume: 41 }));
    expect(screen.queryByRole('group', { name: UNIT_STEP })).toBeNull();
  });

  it('says a unit report with nothing kept may have been charged, and buys anew only behind the step', async () => {
    prices.mockResolvedValue(withUnit({ purchase: { ...UNIT_PURCHASE, status: 'paid_unread', cost_czk: null, gone: true } }));
    buyUnit.mockResolvedValue(ONE_UNIT);
    mount();
    expect(await screen.findByText('mohlo být zaúčtováno, nepřečteno')).toBeTruthy();
    expect(screen.queryByRole('button', { name: /přečíst znovu|vyzvednout znovu/ })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'načíst nově · 10 Kč' }));
    expect(buyUnit).not.toHaveBeenCalled();
    expect(screen.getByRole('group', { name: UNIT_STEP }).textContent).toContain('už jednou platili');
    fireEvent.click(screen.getByRole('button', { name: 'Zaplatit 10 Kč a načíst' }));
    await waitFor(() => expect(buyUnit).toHaveBeenCalledWith(7, 77, 10, { unit: 77, repeat: true }));
  });

  it('offers no unit question without a usable login, and none on a deed over several units', async () => {
    prices.mockResolvedValue({ ...ONE_UNIT, login_set: false });
    mount();
    await screen.findByText('565/31');
    expect(screen.queryByRole('button', { name: /z katastru · 10 Kč/ })).toBeNull();
  });
});
