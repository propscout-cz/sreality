/* The Valuo section draws its editor from the API's fields[] alone, tags every
 * value with where it came from, sends only what the user changed, and renders
 * every server sentence exactly as sent. */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import { ApiError, apiGet, apiPost } from '@/lib/api';
import { fmtArea, fmtCzk, fmtDateSlash, fmtDistanceM, fmtMeasuredPricePerM2, fmtPct, fmtTime24, fmtTomDays } from '@/lib/format';
import { estimationKeys, fetchEstimationsForListings, type PropertyPublic } from '@/lib/queries';
import type { EstimationRun } from '@/lib/types';
import type { Valuation, ValuoField, ValuoPage, ValuoRecord } from '@/lib/valuo';

vi.mock('@/lib/api', async (orig) => ({
  ...(await orig<typeof import('@/lib/api')>()),
  apiGet: vi.fn(),
  apiPost: vi.fn(),
}));
vi.mock('@/lib/queries', async (orig) => ({
  ...(await orig<typeof import('@/lib/queries')>()),
  fetchEstimationsForListings: vi.fn(),
}));
vi.mock('@/components/estimation/RunPanel', () => ({
  RunStatusChip: ({ status }: { status: string }) => <span>{status}</span>,
}));

import ValuoSection from './ValuoSection';

/* Czech formatting uses no-break spaces; the DOM matcher reads them as spaces. */
const t = (s: string) => s.replace(/\s+/g, ' ');

const field = (f: Partial<ValuoField> & Pick<ValuoField, 'name' | 'label' | 'type'>): ValuoField => ({
  unit: null, options: [], required: false, group: 'more', fixed: false, max_length: null, ...f,
});
const opts = (...pairs: [string, string][]) => pairs.map(([value, label]) => ({ value, label }));

const FIELDS: ValuoField[] = [
  field({ name: 'property_type', label: 'Nemovitost', type: 'enum', fixed: true, required: true, group: 'basic',
          options: opts(['flat', 'Byt'], ['house', 'Dům'], ['land', 'Pozemek']) }),
  field({ name: 'place', label: 'Poloha (adresa nebo souřadnice)', type: 'text', required: true, group: 'basic',
          max_length: 200 }),
  field({ name: 'kind', label: 'Typ obchodu', type: 'enum', required: true, group: 'basic',
          options: opts(['sale', 'Prodej'], ['lease', 'Pronájem']) }),
  field({ name: 'floor_area', label: 'Plocha', type: 'number', unit: 'm²', required: true, group: 'basic' }),
  field({ name: 'rating', label: 'Stav', type: 'enum', required: true, group: 'basic',
          options: opts(['bad', 'Neobyvatelný'], ['nothing_much', 'Před rekonstrukcí'], ['good', 'Průměrný'],
                        ['very_good', 'Velmi dobrý'], ['excellent', 'Luxusní']) }),
  field({ name: 'as_of', label: 'Ke dni', type: 'date', group: 'basic' }),
  field({ name: 'new_building', label: 'Novostavba', type: 'boolean', group: 'basic' }),
  field({ name: 'floor', label: 'Podlaží', type: 'integer' }),
  field({ name: 'energy_performance', label: 'Energetická třída', type: 'enum',
          options: opts(...[...'ABCDEFG'].map((c): [string, string] => [c, c])) }),
];
const PLACE = '50.075500, 14.437800';
const DEFAULTS = {
  property_type: 'flat', place: PLACE, kind: 'sale', floor_area: 54.5, as_of: '2026-10-19', floor: 3, new_building: true,
};
const PROVENANCE: ValuoPage['provenance'] = {
  property_type: { class: 'ad', note: null },
  place: { class: 'ad', note: 'Vinohradská 12, Praha' },
  kind: { class: 'ad', note: null },
  floor_area: { class: 'ad', note: null },
  rating: { class: 'missing', note: null },
  as_of: { class: 'assumed', note: 'Dnešní datum.' },
  floor: { class: 'ad', note: null },
  energy_performance: { class: 'assumed', note: 'Třídu G uvádějí i inzeráty bez průkazu PENB.' },
  new_building: { class: 'inferred', note: 'Ve výstavbě → Novostavba' },
};

const SALE: Valuation = {
  id: 1,
  created_at: '2026-10-19T12:02:00Z',
  environment: 'sandbox',
  status: 'success',
  request: { ...DEFAULTS, rating: 'very_good' },
  provenance: PROVENANCE,
  // Unrounded, as Valuo sends it; min/max ride along but are not the band.
  result: {
    avg_price: 6_123_456.7, range_price: [5_643_210, 6_587_654], min_price: 4_100_000, max_price: 8_900_000,
    avg_price_m2: 111_900, range_price_m2: [103_540, 120_870], std_price_m2: 7_200, calc_area: 54.5, flags: [],
  } as Valuation['result'],
  summary: {
    count: 12, avg_distance: 406, avg_age: 30, avg_duration: 21, avg_score: 0.8, distance: 2000, age: 90, not_enough: false,
  },
  kind_label: 'Prodej',
  error: null,
  ppm2_basis: 'sale_capital_czk_m2',
};

const REFUSED: Valuation = {
  ...SALE, id: 8, status: 'failed', result: null, records: [], summary: null,
  error: {
    code: 'form_errors', message: 'Valuo odmítlo některé parametry.',
    form_errors: [
      { field: 'rating', message: 'Invalid enum value' }, { field: 'floor', message: 'Expected number' },
      { field: null, message: 'Required' }, { field: 'country_code', message: 'Invalid literal' },
      { field: 'place', message: 'Place not found' },
    ],
  },
};

const record = (over: Partial<ValuoRecord>): ValuoRecord => ({
  id: 'r1', place: 'Testovací 148, Sandboxov', lat: 50.07, lon: 14.43, local_type: '2+kk', calc_area: 52,
  lot_area: null, floor: 2, total_floors: 5, rating: 'good', new_building: false, kind: 'sale', property_type: 'flat',
  price: 5_900_000, price_m2: 113_462, sale_price: true, adjusted_price: 6_050_000, adjusted_price_m2: 116_346,
  distance: 350, score_prct: 87, days: 41, first_seen_at: '2026-08-01', last_seen_at: '2026-09-10',
  address: 'Testovací 148, Sandboxov', advert_url: 'https://www.example.test/inzerat/r1',
  photos: [{ thumb: 'https://pics.valuo.cz/pt/a/1.jpg', full: 'https://pics.valuo.cz/p/a/1.jpg' },
           { thumb: 'https://pics.valuo.cz/pt/a/2.jpg', full: 'https://pics.valuo.cz/p/a/2.jpg' }],
  ...over,
});
/* A v3 answer: Valuo's comparables ride with it, the second without its advert detail. */
const V3: Valuation = {
  ...SALE, id: 7,
  records: [
    record({}),
    record({ id: 'r2', place: 'Sandboxová 3', local_type: '3+1', sale_price: false, days: 1, address: undefined,
             advert_url: undefined, photos: undefined }),
  ],
};

const page = (over: Partial<ValuoPage> = {}): ValuoPage => ({
  environment: 'sandbox',
  refusal: null,
  subject: { label: 'Byt · 2+kk', location: { label: 'Vinohradská 12, Praha', note: null } },
  fields: FIELDS,
  defaults: DEFAULTS,
  provenance: PROVENANCE,
  blocking: ['rating'],
  history: [],
  ...over,
});

const PROPERTY = {
  id: 11,
  property_id: 5,
  price_czk: 6_450_000,
  mf_reference_rent: { monthly_rent_czk: 19_000 },
} as unknown as PropertyPublic;

function renderSection(p: ValuoPage | Error, { readOnly = false, runs }: { readOnly?: boolean; runs?: EstimationRun[] } = {}) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  if (runs) qc.setQueryData(estimationKeys.byListing([11]), { data: runs, total: runs.length });
  if (p instanceof Error) vi.mocked(apiGet).mockRejectedValue(p);
  else vi.mocked(apiGet).mockResolvedValue(p);
  return render(
    <QueryClientProvider client={qc}>
      <ValuoSection property={PROPERTY} listingIds={[11]} readOnly={readOnly} />
    </QueryClientProvider>,
  );
}

const cta = () => screen.getByRole('button', { name: 'Ocenit přes Valuo' });
const openMore = () => fireEvent.click(screen.getByRole('button', { name: /^Další parametry/ }));
/* One parameter's caption group: its control, its tag, and what Valuo said about it. */
const param = (label: string) => within(screen.getAllByRole('group', { name: label })[0]);
const placeBox = () => screen.getByRole('textbox', { name: 'Poloha (adresa nebo souřadnice)' });

beforeEach(() => {
  vi.mocked(apiGet).mockReset();
  vi.mocked(apiPost).mockReset();
  vi.mocked(fetchEstimationsForListings).mockReset();
});

describe('<ValuoSection> editor', () => {
  it('opens straight into the editor when a required value is missing, one control per field type', async () => {
    const { container } = renderSection(page());
    expect(await screen.findByText('Byt · 2+kk · Vinohradská 12, Praha')).toBeInTheDocument();
    expect(screen.getByText('Byt')).toBeInTheDocument(); // fixed: read-only text, no control
    expect(placeBox()).toHaveValue(PLACE); // text: an editable box
    expect(placeBox()).toHaveAttribute('maxLength', '200'); // the server's own limit
    expect(screen.getByRole('button', { name: 'Prodej' })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByRole('textbox', { name: 'Plocha' })).toHaveValue('54,5');
    expect(screen.getByText('m²')).toBeInTheDocument();
    const rating = screen.getByRole('combobox', { name: 'Stav' });
    expect(within(rating).getAllByRole('option')[0]).toHaveTextContent('Nevyplněno');
    expect(container.querySelector('input[type="date"]')).toHaveValue('2026-10-19');
    // A yes/no in the basic group, where the API's grouping puts it.
    for (const name of ['neuvedeno', 'ano', 'ne']) expect(param('Novostavba').getByRole('button', { name })).toBeInTheDocument();
    expect(param('Novostavba').getByRole('button', { name: 'ano' })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.queryByRole('textbox', { name: 'Podlaží' })).not.toBeInTheDocument();

    openMore();
    expect(screen.getByRole('textbox', { name: 'Podlaží' })).toHaveValue('3');
    expect(screen.getByRole('combobox', { name: 'Energetická třída' })).toBeInTheDocument();
  });

  it('tags every value with where it came from, and counts the folded group', async () => {
    renderSection(page());
    await screen.findByText('Byt · 2+kk · Vinohradská 12, Praha');
    expect(screen.getAllByText('z inzerátu')).toHaveLength(4); // property_type, place, kind, floor_area
    expect(screen.getByText('chybí')).toBeInTheDocument();
    expect(screen.getByText('předpoklad')).toBeInTheDocument();
    expect(screen.getByRole('img', { name: 'Dnešní datum.' })).toBeInTheDocument();
    expect(param('Poloha (adresa nebo souřadnice)').getByRole('img', { name: 'Vinohradská 12, Praha' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Další parametry · 1 vyplněno · 1 předpoklad' })).toBeInTheDocument();

    fireEvent.change(screen.getByRole('combobox', { name: 'Stav' }), { target: { value: 'good' } });
    expect(screen.queryByText('chybí')).not.toBeInTheDocument();
    expect(screen.getByText('upraveno')).toBeInTheDocument();
  });

  it('holds the paid call while a required value is missing, and names it', async () => {
    renderSection(page());
    await screen.findByText('Chybí povinné parametry: Stav.');
    expect(cta()).toBeDisabled();
    fireEvent.change(screen.getByRole('combobox', { name: 'Stav' }), { target: { value: 'good' } });
    expect(cta()).toBeEnabled();
    expect(screen.queryByText(/Chybí povinné parametry/)).not.toBeInTheDocument();
  });

  it('sends only what the user changed — a value set back is dropped, a cleared one is null', async () => {
    vi.mocked(apiPost).mockResolvedValue({ reused: false, valuation: { ...SALE, id: 2 } });
    const { container } = renderSection(page());
    await screen.findByRole('combobox', { name: 'Stav' });
    fireEvent.change(screen.getByRole('combobox', { name: 'Stav' }), { target: { value: 'good' } });
    fireEvent.change(screen.getByRole('textbox', { name: 'Plocha' }), { target: { value: '60' } });
    fireEvent.change(screen.getByRole('textbox', { name: 'Plocha' }), { target: { value: '54,5' } });
    fireEvent.click(screen.getByRole('button', { name: 'Pronájem' }));
    fireEvent.click(screen.getByRole('button', { name: 'Prodej' }));
    fireEvent.change(container.querySelector('input[type="date"]')!, { target: { value: '' } }); // never cleared
    fireEvent.click(param('Novostavba').getByRole('button', { name: 'neuvedeno' }));
    fireEvent.click(cta());

    await waitFor(() => expect(apiPost).toHaveBeenCalledTimes(1));
    expect(apiPost).toHaveBeenCalledWith('/properties/5/valuo', { overrides: { rating: 'good', new_building: null } });
    // The answer replaces the editor in place.
    expect(await screen.findByText(t(fmtCzk(6_120_000)))).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Ocenit přes Valuo' })).not.toBeInTheDocument();
  });

  it('says so when the same request already ran, and shows the stored answer', async () => {
    const today = { ...SALE, created_at: new Date().toISOString() };
    vi.mocked(apiPost).mockResolvedValue({ reused: true, valuation: today });
    renderSection(page({ blocking: [], defaults: { ...DEFAULTS, rating: 'good' } }));
    fireEvent.click(await screen.findByRole('button', { name: 'Ocenit přes Valuo →' }));
    fireEvent.click(cta());
    expect(
      await screen.findByText(
        `Stejný dotaz dnes už proběhl (${fmtTime24(today.created_at)}) — zobrazuji uložené ocenění.`,
      ),
    ).toBeInTheDocument();
  });

  it('dates a reused answer from another day, and leaves the ledger order alone', async () => {
    const old = { ...SALE, id: 4, created_at: '2026-09-01T10:00:00Z' };
    vi.mocked(apiPost).mockResolvedValue({ reused: true, valuation: old });
    renderSection(page({ blocking: [], defaults: { ...DEFAULTS, rating: 'good' }, history: [SALE, old] }));
    fireEvent.click(await screen.findByRole('button', { name: 'Nové ocenění →' }));
    fireEvent.click(cta());
    const day = fmtDateSlash(old.created_at);
    expect(await screen.findByText(`Stejný dotaz dne ${day} už proběhl (${fmtTime24(old.created_at)}) — zobrazuji uložené ocenění.`))
      .toBeInTheDocument();
    const rows = screen.getAllByRole('row');
    expect(rows[2]).toHaveTextContent(day);
    expect(within(rows[2]).getByText('zobrazeno')).toBeInTheDocument();
  });

  it('renders a refused POST and a failed paid call as their sentences, keeping the form', async () => {
    vi.mocked(apiPost).mockRejectedValueOnce(new ApiError('Stejný dotaz právě běží.', 409, null));
    renderSection(page({ blocking: [], defaults: { ...DEFAULTS, rating: 'good' } }));
    fireEvent.click(await screen.findByRole('button', { name: 'Ocenit přes Valuo →' }));
    fireEvent.click(cta());
    expect(await screen.findByText('Stejný dotaz právě běží.')).toBeInTheDocument();

    vi.mocked(apiPost).mockResolvedValueOnce({
      reused: false,
      valuation: { ...SALE, status: 'failed', result: null, error: { code: 'timeout', message: 'Valuo neodpovědělo do 20 sekund.' } },
    });
    fireEvent.click(cta());
    expect(await screen.findByText('Valuo neodpovědělo do 20 sekund.')).toBeInTheDocument();
    expect(cta()).toBeInTheDocument();
  });

  it('takes a typed place as an override, and holds the call while the box is empty', async () => {
    vi.mocked(apiPost).mockResolvedValue({ reused: false, valuation: { ...SALE, id: 2 } });
    renderSection(page({ blocking: [], defaults: { ...DEFAULTS, rating: 'good' } }));
    fireEvent.click(await screen.findByRole('button', { name: 'Ocenit přes Valuo →' }));
    expect(placeBox()).toHaveValue(PLACE);
    fireEvent.change(placeBox(), { target: { value: '' } });
    expect(screen.getByText('Chybí povinné parametry: Poloha (adresa nebo souřadnice).')).toBeInTheDocument();
    expect(cta()).toBeDisabled();
    fireEvent.change(placeBox(), { target: { value: 'Vinohradská 12, Praha 2' } });
    expect(param('Poloha (adresa nebo souřadnice)').getByText('upraveno')).toBeInTheDocument();
    fireEvent.click(cta());
    await waitFor(() => expect(apiPost).toHaveBeenCalledTimes(1));
    expect(apiPost).toHaveBeenCalledWith('/properties/5/valuo', { overrides: { place: 'Vinohradská 12, Praha 2' } });
  });

  it('reads the new-building flag as a yes/no choice with where it came from', async () => {
    vi.mocked(apiPost).mockResolvedValue({ reused: false, valuation: { ...SALE, id: 2 } });
    renderSection(page({ blocking: [], defaults: { ...DEFAULTS, rating: 'good' } }));
    fireEvent.click(await screen.findByRole('button', { name: 'Ocenit přes Valuo →' }));
    const nb = param('Novostavba');
    expect(nb.getByRole('button', { name: 'ano' })).toHaveAttribute('aria-pressed', 'true');
    expect(nb.getByText('odvozeno')).toBeInTheDocument();
    expect(nb.getByRole('img', { name: 'Ve výstavbě → Novostavba' })).toBeInTheDocument();
    fireEvent.click(nb.getByRole('button', { name: 'ne' }));
    fireEvent.click(cta());
    await waitFor(() => expect(apiPost).toHaveBeenCalledWith('/properties/5/valuo', { overrides: { new_building: false } }));
  });

  it('draws a commercial type from the fields the API sends, like any other choice', async () => {
    vi.mocked(apiPost).mockResolvedValue({ reused: false, valuation: { ...SALE, id: 2 } });
    const commercial = [
      FIELDS[0], FIELDS[2], FIELDS[3], FIELDS[4],
      field({ name: 'commercial_type', label: 'Druh komerční nemovitosti', type: 'enum', group: 'basic',
              options: opts(['office', 'Kancelář'], ['retail', 'Obchodní prostor'], ['storage', 'Sklad'], ['other', 'Jiný']) }),
    ];
    renderSection(page({ fields: commercial, blocking: [], defaults: { property_type: 'commercial', kind: 'sale', floor_area: 120, rating: 'good' } }));
    fireEvent.click(await screen.findByRole('button', { name: 'Ocenit přes Valuo →' }));
    const kind = screen.getByRole('combobox', { name: 'Druh komerční nemovitosti' });
    expect(within(kind).getAllByRole('option').map((o) => o.textContent)).toEqual([
      'Nevyplněno', 'Kancelář', 'Obchodní prostor', 'Sklad', 'Jiný',
    ]);
    expect(screen.queryByRole('textbox', { name: 'Poloha (adresa nebo souřadnice)' })).not.toBeInTheDocument();
    fireEvent.change(kind, { target: { value: 'office' } });
    fireEvent.click(cta());
    await waitFor(() => expect(apiPost).toHaveBeenCalledWith('/properties/5/valuo', { overrides: { commercial_type: 'office' } }));
  });

  it('puts each parameter Valuo refused under its field, the rest under the banner', async () => {
    vi.mocked(apiPost).mockResolvedValue({ reused: false, valuation: REFUSED });
    renderSection(page({ blocking: [], defaults: { ...DEFAULTS, rating: 'good' } }));
    fireEvent.click(await screen.findByRole('button', { name: 'Ocenit přes Valuo →' }));
    fireEvent.click(cta());
    expect(await screen.findByText('Valuo odmítlo některé parametry.')).toBeInTheDocument();
    expect(param('Stav').getByText('Invalid enum value')).toBeInTheDocument();
    expect(screen.getByText('Required')).toBeInTheDocument(); // names no parameter
    expect(screen.getByText('country_code: Invalid literal')).toBeInTheDocument(); // names none on this form
    // A refused box says so to a screen reader, and names what Valuo said about it.
    expect(placeBox()).toHaveAttribute('aria-invalid', 'true');
    expect(placeBox()).toHaveAccessibleDescription('Place not found');
    expect(screen.getByRole('textbox', { name: 'Plocha' })).not.toHaveAttribute('aria-invalid');
    const rating = screen.getByRole('combobox', { name: 'Stav' });
    expect(rating).toHaveAttribute('aria-invalid', 'true');
    expect(rating).toHaveAccessibleDescription('Invalid enum value');
    // The folded band counts its refused parameter, so folding never hides one.
    fireEvent.click(screen.getByRole('button', { name: 'Další parametry · 1 vyplněno · 1 předpoklad · 1 chyba' }));
    expect(param('Podlaží').getByText('Expected number')).toBeInTheDocument();
    const floor = screen.getByRole('textbox', { name: 'Podlaží' });
    expect(floor).toHaveAttribute('aria-invalid', 'true');
    expect(floor).toHaveAccessibleDescription('Expected number');
  });

  it('opens the editor of a refused row with its issues under their fields, the folded band open', async () => {
    renderSection(page({ blocking: [], defaults: { ...DEFAULTS, rating: 'good' }, history: [REFUSED] }));
    expect(await screen.findByText('Valuo odmítlo některé parametry.')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Nové ocenění →' }));
    expect(param('Stav').getByText('Invalid enum value')).toBeInTheDocument();
    expect(param('Podlaží').getByText('Expected number')).toBeInTheDocument();
  });

  it('never names an ignored parameter: v3 has no such echo, whatever ignored_ids a row carries', async () => {
    renderSection(page({ history: [{ ...SALE, ignored_ids: ['floor'] } as Valuation] }));
    expect(await screen.findByText('Odhad ceny · Valuo')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Nové ocenění →' }));
    openMore();
    expect(screen.queryByText(/ignorovalo/)).not.toBeInTheDocument();
  });
});

describe('<ValuoSection> states', () => {
  it('renders a refusal exactly as the server sent it, with no way to call, its stored answer still in Czech', async () => {
    renderSection(page({ refusal: { code: 'anything', message: 'Libovolná věta, jak ji poslal server.' }, fields: [], history: [SALE] }));
    expect(await screen.findByText('Libovolná věta, jak ji poslal server.')).toBeInTheDocument();
    expect(screen.getByText('Valuo')).toBeInTheDocument();
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
    expect(screen.getByText('Odhad ceny · Valuo')).toBeInTheDocument();
  });

  it('reads a caller with no account as a sentence, not a retryable error', async () => {
    renderSection(new ApiError('Přihlášený uživatel nemá účet, ocenění nelze uložit.', 400, { detail: { code: 'no_account' } }));
    expect(await screen.findByText('Přihlášený uživatel nemá účet, ocenění nelze uložit.')).toBeInTheDocument();
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
  });

  it('tags the answer on screen by its own environment, not the configured one', async () => {
    for (const environment of ['production', null] as const) {
      const { unmount } = renderSection(page({ environment, history: [SALE] }));
      expect(await screen.findByText('Sandbox')).toBeInTheDocument();
      unmount();
    }
    renderSection(page({ history: [{ ...SALE, environment: 'production' }] }));
    await screen.findByText('Odhad ceny · Valuo');
    expect(screen.queryByText('Sandbox')).not.toBeInTheDocument();
  });

  it('shows the latest answer on the ruler with the asking price and our run, from cache only', async () => {
    const ours = { id: 9, status: 'success', estimate_kind: 'sale', estimated_sale_price_czk: 5_900_000 } as EstimationRun;
    renderSection(page({ history: [SALE] }), { runs: [ours] });
    // Rounded to 10 000 Kč as shown, the exact figure in its title.
    const headline = await screen.findByText(t(fmtCzk(6_120_000)));
    expect(headline).toHaveAttribute('title', fmtCzk(6_123_456.7));
    expect(screen.getByText('Odhad ceny · Valuo')).toBeInTheDocument();
    expect(screen.getByText('1 ocenění')).toBeInTheDocument();
    expect(screen.getByText('Sandbox')).toBeInTheDocument();
    // The ruler is Valuo's confidence band, never its min/max.
    expect(screen.getByText('pásmo spolehlivosti (Valuo)')).toBeInTheDocument();
    expect(screen.getByText(t(fmtCzk(5_640_000)))).toBeInTheDocument();
    expect(screen.getByText(t(fmtCzk(6_590_000)))).toBeInTheDocument();
    expect(screen.queryByText(t(fmtCzk(4_100_000)))).not.toBeInTheDocument();
    expect(screen.queryByText(t(fmtCzk(8_900_000)))).not.toBeInTheDocument();
    expect(within(screen.getByText('Za m² (pásmo)').parentElement!)
      .getByText(t(`103 540–${fmtMeasuredPricePerM2(120_870, 'sale')}`))).toBeInTheDocument();
    expect(screen.getByText('nabídka')).toBeInTheDocument();
    expect(screen.getByText('náš odhad')).toBeInTheDocument();
    expect(screen.queryByText('MF')).not.toBeInTheDocument(); // a sale has no rent-map mark
    const above = ((6_450_000 - 6_123_456.7) / 6_123_456.7) * 100; // against the exact average
    expect(screen.getByText(t(`Nabídka je o ${fmtPct(above)} nad průměrem Valuo.`))).toBeInTheDocument();
    expect(screen.getByText(t(`± ${fmtMeasuredPricePerM2(7_200, 'sale')}`))).toBeInTheDocument();
    expect(screen.queryByText('Příznaky')).not.toBeInTheDocument(); // an empty list is an answer, not a gap
    expect(screen.queryByText('—')).not.toBeInTheDocument();
    // What the answer stands on, under the headline: only what Valuo reported, no grade of ours.
    expect(screen.getByText(t(`12 srovnání · ø 406 m · shoda ${fmtPct(80, { digits: 0 })} · okruh ${fmtDistanceM(2000)} / ${fmtTomDays(90)}`)))
      .toBeInTheDocument();
    expect(screen.queryByText(/nenašlo dost/)).not.toBeInTheDocument();
    // The point sent was coordinates; the card says which place they stand for.
    expect(within(screen.getByText('Poloha').parentElement!).getByText('Vinohradská 12, Praha')).toBeInTheDocument();
    expect(fetchEstimationsForListings).not.toHaveBeenCalled();
  });

  it('reads a lease monthly on its own basis, against MF, never against a sale ask', async () => {
    const lease: Valuation = {
      ...SALE,
      request: { ...SALE.request, kind: 'lease' },
      kind_label: 'Pronájem',
      ppm2_basis: 'rent_monthly_czk_m2',
      provenance: { ...PROVENANCE, place: { class: 'inferred', note: 'Poloha jen na úrovni obce' } },
      result: { ...SALE.result!, avg_price: 21_037, range_price: [18_049, 24_012], range_price_m2: [330, 440], avg_price_m2: 385, flags: null },
    };
    renderSection(page({ history: [lease] }));
    expect(await screen.findByText('Odhad nájmu · Valuo')).toBeInTheDocument();
    // A monthly rent rounds to 100 Kč.
    expect(screen.getByTitle(t(fmtCzk(21_037)))).toHaveTextContent(t(`${fmtCzk(21_000)}/měs`));
    expect(screen.getByText(t(fmtCzk(18_000)))).toBeInTheDocument();
    expect(screen.getByText(t(fmtCzk(24_000)))).toBeInTheDocument();
    expect(screen.getByText('/měs')).toBeInTheDocument();
    expect(screen.getByText(t(fmtMeasuredPricePerM2(385, 'rent')))).toBeInTheDocument();
    expect(screen.getByText('MF')).toBeInTheDocument();
    expect(screen.queryByText('nabídka')).not.toBeInTheDocument();
    // The card says the point sent was only a town's (it was valued over the town, not blocked).
    expect(within(screen.getByText('Poloha').parentElement!).getByText('Poloha jen na úrovni obce')).toBeInTheDocument();
    expect(within(screen.getByText('Příznaky').parentElement!).getByText('—')).toBeInTheDocument(); // no list: the gap
  });

  it('reads a row from an API that predates its Czech labels and its summary (the API and the SPA deploy apart)', async () => {
    const old: Valuation = { ...SALE };
    delete old.kind_label;
    delete old.summary;
    renderSection(page({ history: [old, { ...old, id: 2 }] }));
    expect(await screen.findByText('Odhad ceny · Valuo')).toBeInTheDocument();
    expect(screen.queryByText(/srovnání/)).not.toBeInTheDocument();
    expect(within(screen.getAllByRole('row')[1]).getByText('—')).toBeInTheDocument();
  });

  it('says once, beside an ochre mark, that Valuo ran out of comparables, and leaves out a part it did not report', async () => {
    const thin = { ...SALE, summary: { ...SALE.summary!, not_enough: true, count: null, avg_score: null, age: null } };
    renderSection(page({ history: [thin] }));
    expect(await screen.findByText('Valuo nenašlo dost srovnatelných nemovitostí ani v maximálním okruhu.')).toBeInTheDocument();
    expect(screen.getByText(t(`ø 406 m · okruh ${fmtDistanceM(2000)}`))).toBeInTheDocument();
  });

  it('shows a failed row as its stored sentence, and a picked history row as its card', async () => {
    const failed: Valuation = {
      ...SALE, id: 3, status: 'failed', result: null,
      error: { code: 'rate_limited', message: 'Valuo hlásí příliš mnoho dotazů, zkuste to později.' },
    };
    renderSection(page({ history: [failed, { ...SALE, environment: 'production' }] }));
    expect(await screen.findByText('Valuo hlásí příliš mnoho dotazů, zkuste to později.')).toBeInTheDocument();
    expect(screen.getByText('Všechna ocenění')).toBeInTheDocument();
    const rows = screen.getAllByRole('row');
    expect(within(rows[2]).getByText('Prodej')).toBeInTheDocument();
    expect(within(rows[2]).getByText('ostré')).toBeInTheDocument();
    // The ledger rounds the average as the card does, the exact one in its title.
    expect(within(rows[2]).getByTitle(t(fmtCzk(6_123_456.7)))).toHaveTextContent(t(fmtCzk(6_120_000)));
    fireEvent.click(within(rows[2]).getByRole('button')); // the keyboard's way in
    expect(screen.getAllByText(t(fmtCzk(6_120_000))).length).toBeGreaterThan(1);
    expect(screen.queryByText('Valuo hlásí příliš mnoho dotazů, zkuste to později.')).not.toBeInTheDocument();
  });

  it('lists the comparables Valuo sent under the card, in its order, each per-m² figure with its unit', async () => {
    renderSection(page({ history: [V3] }));
    const label = await screen.findByText(/^Srovnávací nemovitosti/);
    expect(label).toHaveTextContent('Srovnávací nemovitosti · 2');
    const table = screen.getByRole('table', { name: 'Srovnávací nemovitosti Valuo' });
    const rows = within(table).getAllByRole('row');
    expect(within(rows[0]).getAllByRole('columnheader').map((h) => h.textContent)).toEqual([
      'Foto', 'Adresa', 'Dispozice', 'Plocha', 'Cena', 'Za m²', 'Upravená cena', 'Vzdálenost', 'Shoda', 'V inzerci',
    ]);
    const first = within(rows[1]);
    for (const text of [
      '2+kk', fmtArea(52), fmtMeasuredPricePerM2(113_462, 'sale'), fmtCzk(6_050_000),
      fmtDistanceM(350), fmtPct(87, { digits: 0 }), fmtTomDays(41),
    ]) expect(first.getByText(t(text))).toBeInTheDocument();
    // The address opens Valuo's advert in a new tab.
    const link = first.getByRole('link', { name: 'Testovací 148, Sandboxov' });
    expect(link).toHaveAttribute('href', 'https://www.example.test/inzerat/r1');
    expect(link).toHaveAttribute('target', '_blank');
    expect(link.getAttribute('rel')).toContain('noopener');
    // A realised sale, tagged and explained.
    expect(rows[1]).toHaveTextContent(t(`prodáno${fmtCzk(5_900_000)}`));
    expect(first.getByText('prodáno')).toHaveAttribute('title', 'Realizovaná prodejní cena (ČÚZK / realitní kanceláře)');
    // Without the advert detail: the place as text, no photo, no link.
    const second = within(rows[2]);
    expect(second.getByText('Sandboxová 3')).toBeInTheDocument();
    expect(second.queryByRole('link')).not.toBeInTheDocument();
    expect(second.queryByRole('button')).not.toBeInTheDocument();
    expect(second.queryByText('prodáno')).not.toBeInTheDocument();
    expect(second.getByText(t(fmtTomDays(1)))).toBeInTheDocument();
  });

  it('opens a comparable photo in the app lightbox, and leaves a dashed box where one does not load', async () => {
    renderSection(page({ history: [V3] }));
    const thumb = await screen.findByRole('img', { name: 'Testovací 148, Sandboxov' });
    expect(thumb).toHaveAttribute('src', 'https://pics.valuo.cz/pt/a/1.jpg');
    expect(thumb).toHaveAttribute('loading', 'lazy');
    // Its own name: the address link in the next cell opens Valuo's advert, this opens the photos.
    fireEvent.click(screen.getByRole('button', { name: 'Fotografie (2): Testovací 148, Sandboxov' }));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText('1 / 2')).toBeInTheDocument();
    expect(dialog.querySelector('img')).toHaveAttribute('src', 'https://pics.valuo.cz/p/a/1.jpg');
    fireEvent.keyDown(document, { key: 'Escape' });
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());

    fireEvent.error(screen.getByRole('img', { name: 'Testovací 148, Sandboxov' }));
    expect(screen.queryByRole('img', { name: 'Testovací 148, Sandboxov' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^Fotografie/ })).not.toBeInTheDocument();
  });

  it('draws no comparables for a row without them', async () => {
    for (const row of [SALE, { ...V3, records: [] }]) {
      const { unmount } = renderSection(page({ history: [row] }));
      expect(await screen.findByText('Odhad ceny · Valuo')).toBeInTheDocument();
      expect(screen.queryByText(/^Srovnávací nemovitosti/)).not.toBeInTheDocument();
      expect(screen.queryByRole('table')).not.toBeInTheDocument();
      unmount();
    }
  });

  it('leaves the comparables off the phone card', async () => {
    renderSection(page({ history: [V3] }), { readOnly: true });
    expect(await screen.findByText(t(fmtCzk(6_120_000)))).toBeInTheDocument();
    expect(screen.queryByText(/^Srovnávací nemovitosti/)).not.toBeInTheDocument();
  });

  it('on the phone reads the latest answer on its band with no editor and no button, and nothing without one', async () => {
    const { unmount } = renderSection(page({ history: [SALE] }), { readOnly: true });
    expect(await screen.findByText(t(fmtCzk(6_120_000)))).toBeInTheDocument();
    expect(screen.getByText('pásmo spolehlivosti (Valuo)')).toBeInTheDocument();
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
    unmount();

    const { container } = renderSection(page({ history: [] }), { readOnly: true });
    await waitFor(() => expect(apiGet).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });
});
