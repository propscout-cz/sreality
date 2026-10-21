/* The Valuo section draws its editor from the API's fields[] alone, tags every
 * value with where it came from, sends only what the user changed, and renders
 * every server sentence exactly as sent. */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import { ApiError, apiGet, apiPost, listEstimations } from '@/lib/api';
import {
  fmtArea, fmtCount, fmtCzk, fmtDateSlash, fmtDistanceM, fmtMeasuredPricePerM2, fmtPct, fmtTime24, fmtTomDays,
} from '@/lib/format';
import { estimationKeys, type PropertyPublic } from '@/lib/queries';
import type { EstimationRun } from '@/lib/types';
import type { Valuation, ValuoField, ValuoPage, ValuoRecord } from '@/lib/valuo';

vi.mock('@/lib/api', async (orig) => ({
  ...(await orig<typeof import('@/lib/api')>()),
  apiGet: vi.fn(),
  apiPost: vi.fn(),
  listEstimations: vi.fn(),
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
    avg_price_m2: 111_937, range_price_m2: [103_540, 120_870], std_price_m2: 7_200, calc_area: 54.5, flags: [],
  } as Valuation['result'],
  summary: {
    count: 12, avg_distance: 406, avg_score: 0.8, distance: 2000, age: 90, not_enough: false,
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
  id: 'r1', place: 'Testovací 148, Sandboxov', local_type: '2+kk', calc_area: 52,
  price: 5_900_000, price_m2: 113_462, sale_price: true, adjusted_price: 6_050_000, adjusted_price_m2: 116_346,
  distance: 350, score_prct: 87, days: 41,
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

function renderSection(p: ValuoPage | Error, { readOnly = false, runs, adUrls }: {
  readOnly?: boolean; runs?: EstimationRun[]; adUrls?: (string | null)[];
} = {}) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  if (runs) qc.setQueryData(estimationKeys.byListing([11]), { data: runs, total: runs.length });
  if (p instanceof Error) vi.mocked(apiGet).mockRejectedValue(p);
  else vi.mocked(apiGet).mockResolvedValue(p);
  return render(
    <QueryClientProvider client={qc}>
      <ValuoSection property={PROPERTY} listingIds={[11]} adUrls={adUrls} readOnly={readOnly} />
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
  vi.mocked(listEstimations).mockReset().mockResolvedValue({ data: [], total: 0, limit: 100, offset: 0, next_cursor: null });
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

  /* One form box for every control (controls.tsx `formBox`): 34 px, dashed while nothing is sent, the unit inside. */
  it('draws every control as the form box, dashed where nothing is sent, the unit inside its box', async () => {
    renderSection(page({ defaults: { ...DEFAULTS, new_building: null } }));
    const area = await screen.findByRole('textbox', { name: 'Plocha' });
    expect(area).toHaveClass('h-[2.125rem]', 'pr-8');
    expect(area).not.toHaveClass('border-dashed');
    expect(screen.getByText('m²')).toHaveClass('absolute', 'right-2');
    expect(placeBox()).toHaveClass('h-[2.125rem]');
    const rating = screen.getByRole('combobox', { name: 'Stav' }); // no default: nothing is sent
    expect(rating).toHaveClass('h-[2.125rem]', 'border-dashed');
    for (const b of param('Novostavba').getAllByRole('button')) expect(b).toHaveClass('h-[2.125rem]', 'border-dashed');
    expect(screen.getByRole('button', { name: 'Prodej' })).not.toHaveClass('border-dashed');
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
    // By the button, above the fields: seen right after the click, with no "failed:" prefix the sentence already says.
    const banner = screen.getByRole('alert');
    expect(banner.compareDocumentPosition(placeBox()) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(banner.textContent?.trim()).toBe('Valuo neodpovědělo do 20 sekund.');
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
    expect(param('Stav').getByText('Valuo: „Invalid enum value“')).toBeInTheDocument();
    expect(screen.getByText('Required')).toBeInTheDocument(); // names no parameter
    expect(screen.getByText('country_code: Invalid literal')).toBeInTheDocument(); // names none on this form
    // A refused box says so to a screen reader, and quotes what Valuo said about it, as Valuo's (its words are English).
    expect(placeBox()).toHaveAttribute('aria-invalid', 'true');
    expect(placeBox()).toHaveAccessibleDescription('Valuo: „Place not found“');
    expect(screen.getByRole('textbox', { name: 'Plocha' })).not.toHaveAttribute('aria-invalid');
    const rating = screen.getByRole('combobox', { name: 'Stav' });
    expect(rating).toHaveAttribute('aria-invalid', 'true');
    expect(rating).toHaveAccessibleDescription('Valuo: „Invalid enum value“');
    // The folded band counts its refused parameter, so folding never hides one.
    fireEvent.click(screen.getByRole('button', { name: 'Další parametry · 1 vyplněno · 1 předpoklad · 1 chyba' }));
    expect(param('Podlaží').getByText('Valuo: „Expected number“')).toBeInTheDocument();
    const floor = screen.getByRole('textbox', { name: 'Podlaží' });
    expect(floor).toHaveAttribute('aria-invalid', 'true');
    expect(floor).toHaveAccessibleDescription('Valuo: „Expected number“');
  });

  it('opens the editor of a refused row with its issues under their fields, the folded band open', async () => {
    renderSection(page({ blocking: [], defaults: { ...DEFAULTS, rating: 'good' }, history: [REFUSED] }));
    expect(await screen.findByText('Valuo odmítlo některé parametry.')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Nové ocenění →' }));
    expect(param('Stav').getByText('Valuo: „Invalid enum value“')).toBeInTheDocument();
    expect(param('Podlaží').getByText('Valuo: „Expected number“')).toBeInTheDocument();
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

  it('says in Czech that the section failed to load, and offers the retry', async () => {
    renderSection(new ApiError('HTTP 502', 502, null));
    expect((await screen.findByRole('alert')).textContent).toBe('Ocenění se nepodařilo načíst: HTTP 502Zkusit znovu');
  });

  it('reads a caller with no account as a sentence, not a retryable error', async () => {
    // The app-wide no-account 400 (api/tenant_pool.require_account_id), English until the one translation pass.
    renderSection(new ApiError('no account for caller', 400, { detail: 'no account for caller' }));
    expect(await screen.findByText('no account for caller')).toBeInTheDocument();
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

  it("shows the latest answer on the ruler with the asking price and our run, off the estimations block's query", async () => {
    const ours = { id: 9, status: 'success', estimate_kind: 'sale', estimated_sale_price_czk: 5_900_000 } as EstimationRun;
    renderSection(page({ history: [SALE] }), { runs: [ours] });
    // Rounded to 10 000 Kč as shown, the exact figure in its title.
    const headline = await screen.findByText(t(fmtCzk(6_120_000)));
    expect(headline).toHaveAttribute('title', fmtCzk(6_123_456.7));
    expect(screen.getByText(t(fmtMeasuredPricePerM2(111_900, 'sale')))).toHaveAttribute('title', fmtMeasuredPricePerM2(111_937, 'sale'));
    expect(screen.getByText('Odhad ceny · Valuo')).toBeInTheDocument();
    expect(screen.queryByText(/^\d+ ocenění$/)).not.toBeInTheDocument(); // the ledger counts its rows, the header does not
    expect(screen.getByText('Sandbox')).toBeInTheDocument();
    // The ruler is Valuo's confidence band, never its min/max.
    expect(screen.getByText('pásmo spolehlivosti (Valuo)')).toBeInTheDocument();
    expect(screen.getByText(t(fmtCzk(5_640_000)))).toBeInTheDocument();
    expect(screen.getByText(t(fmtCzk(6_590_000)))).toBeInTheDocument();
    expect(screen.queryByText(t(fmtCzk(4_100_000)))).not.toBeInTheDocument();
    expect(screen.queryByText(t(fmtCzk(8_900_000)))).not.toBeInTheDocument();
    // The per-m² band rounds to 100 Kč like the headline rounds, the exact ends in its title.
    expect(within(screen.getByText('Za m² (pásmo)').parentElement!).getByText(t(`103 500–${fmtMeasuredPricePerM2(120_900, 'sale')}`)))
      .toHaveAttribute('title', `${fmtCount(103_540)}–${fmtMeasuredPricePerM2(120_870, 'sale')}`);
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
    expect(listEstimations).not.toHaveBeenCalled(); // the block's cached runs: no second fetch
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
    expect(screen.getByTitle(t(`${fmtCzk(21_037)} /měs`))).toHaveTextContent(t(`${fmtCzk(21_000)}/měs`));
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
    expect(screen.queryByText(/Odeslaná poloha|Valuo: „/)).not.toBeInTheDocument(); // only an engine refusal says them
    // The ledger rounds the average and its per-m² as the card does, the exact ones in their titles.
    expect(within(rows[2]).getByTitle(t(fmtCzk(6_123_456.7)))).toHaveTextContent(t(fmtCzk(6_120_000)));
    expect(within(rows[2]).getByTitle(t(fmtMeasuredPricePerM2(111_937, 'sale')))).toHaveTextContent(t(fmtMeasuredPricePerM2(111_900, 'sale')));
    fireEvent.click(within(rows[2]).getByRole('button')); // the keyboard's way in
    expect(screen.getAllByText(t(fmtCzk(6_120_000))).length).toBeGreaterThan(1);
    expect(screen.queryByText('Valuo hlásí příliš mnoho dotazů, zkuste to později.')).not.toBeInTheDocument();
  });

  it("quotes Valuo's own words once, as Valuo's, under the API's sentence, with the place that was sent", async () => {
    const refused: Valuation = {
      ...SALE, id: 4, status: 'failed', result: null, records: [], summary: null,
      error: { code: 'calculation', message: 'Valuo nemovitost neocenilo: Place could not be resolved' },
    };
    renderSection(page({ history: [refused] }));
    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toBe('Valuo nemovitost neocenilo.'); // the sentence says what failed: no title
    expect(alert.querySelector('strong')).toBeNull();
    expect(screen.getByText('Valuo: „Place could not be resolved“')).toBeInTheDocument();
    expect(screen.getByText(PLACE).parentElement).toHaveTextContent(`Odeslaná poloha: ${PLACE}`);
  });

  it('lists the comparables Valuo sent under the card, in its order, each per-m² figure with its unit', async () => {
    renderSection(page({ history: [V3] }));
    const label = await screen.findByText(/^Srovnávací nemovitosti/);
    expect(label).toHaveTextContent('Srovnávací nemovitosti · 2');
    const table = screen.getByRole('table', { name: 'Srovnávací nemovitosti Valuo' });
    const rows = within(table).getAllByRole('row');
    // Short one-line headers, the layout and the area in one cell, the per-m² under its price: no sideways scroll at 1024 px.
    expect(within(rows[0]).getAllByRole('columnheader').map((h) => t(h.textContent!))).toEqual([
      'Foto', 'Adresa', 'Velikost', 'Cena', 'Upravená', 'Vzdálenost', 'Shoda', 'V inzerci',
    ]);
    const first = within(rows[1]);
    expect(rows[1]).toHaveTextContent(t(`2+kk${fmtArea(52)}`));
    for (const text of [
      fmtMeasuredPricePerM2(113_462, 'sale'), fmtCzk(6_050_000), fmtDistanceM(350), fmtPct(87, { digits: 0 }), fmtTomDays(41),
    ]) expect(first.getByText(t(text))).toBeInTheDocument();
    // The address opens Valuo's advert in a new tab.
    const link = first.getByRole('link', { name: 'Testovací 148, Sandboxov' });
    expect(link).toHaveAttribute('href', 'https://www.example.test/inzerat/r1');
    expect(link).toHaveAttribute('target', '_blank');
    expect(link.getAttribute('rel')).toContain('noopener');
    expect(link.parentElement).toHaveAttribute('title', 'Testovací 148, Sandboxov'); // two lines at most, the whole address on hover
    expect(link.parentElement).toHaveClass('line-clamp-2');
    // A realised sale, tagged under its figures and explained.
    expect(rows[1]).toHaveTextContent(t(`${fmtCzk(5_900_000)}${fmtMeasuredPricePerM2(113_462, 'sale')}prodáno`));
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
    expect(within(dialog).getByTestId('lightbox-actions')).toHaveTextContent(t(`Testovací 148, Sandboxov · ${fmtCzk(5_900_000)}`));
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
    expect(screen.queryByText(/^\d+ ocenění$/)).not.toBeInTheDocument(); // one card, no count
    unmount();

    const { container } = renderSection(page({ history: [] }), { readOnly: true });
    await waitFor(() => expect(apiGet).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });
});

/* Our recalculation: the comparables are real answers' numbers (no address, link or id), so Valuo's own average is the
 * one the recipe reproduces and the tick boxes are offered. */
const FLAT_ROWS: [number, number][] = [
  [123029, 89], [121000, 84], [120820, 80], [111774, 80], [117808, 80], [117799, 77], [110070, 75], [110395, 74],
];
const LEASE_ROWS: [number, number][] = [
  [380, 85], [343, 79], [382, 78], [365, 77], [353, 77], [365, 76], [423, 74], [412, 74],
];
const comparables = (rows: [number, number][], advertUrl?: (i: number) => string | undefined) => rows.map(([adjusted_price_m2, score_prct], i) =>
  record({ id: `c${i + 1}`, place: `Srovnání ${i + 1}`, address: `Srovnání ${i + 1}`, adjusted_price_m2, score_prct,
           sale_price: false, advert_url: advertUrl?.(i), photos: undefined }));
const FLAT: Valuation = {
  ...SALE, id: 20, records: comparables(FLAT_ROWS),
  result: { ...SALE.result!, avg_price: 7_702_860, avg_price_m2: 116_710, std_price_m2: 4_847, calc_area: 66,
            range_price: [7_381_000, 8_023_000], range_price_m2: [111_863, 121_557] },
};
const LEASE: Valuation = {
  ...FLAT, id: 21, request: { ...SALE.request, kind: 'lease' }, kind_label: 'Pronájem', ppm2_basis: 'rent_monthly_czk_m2',
  records: comparables(LEASE_ROWS),
  result: { ...FLAT.result!, avg_price: 18_850, avg_price_m2: 377, std_price_m2: 25, calc_area: 50,
            range_price: [17_600, 20_100], range_price_m2: [352, 402] },
};
const box = (n: number) => screen.getByRole('checkbox', { name: `Zahrnout do přepočtu: Srovnání ${n}` });
const boxes = () => screen.getAllByRole('checkbox');
const card = () => screen.getByText(/^Náš přepočet/).closest('[class*="min-h-"]') as HTMLElement;
const untick = (...ns: number[]) => ns.forEach((n) => fireEvent.click(box(n)));
const whole = (text: string) => (_c: string, el: Element | null) => el?.tagName === 'P' && t(el.textContent ?? '') === t(text);
const SALE_KEPT = (n: string) => whole(`Náš přepočet · ${n} z 8 srovnání`);

describe('<ValuoSection> our recalculation', () => {
  it('opens identical to Valuo: every comparable ticked, a hint above, Valuo card and band untouched', async () => {
    renderSection(page({ history: [FLAT] }));
    expect(await screen.findByText(SALE_KEPT('8'))).toBeInTheDocument();
    expect(boxes()).toHaveLength(8);
    expect(boxes().every((b) => (b as HTMLInputElement).checked)).toBe(true);
    expect(card().querySelector('[aria-live="polite"]')).not.toBeNull(); // a screen reader hears the figure change
    expect(screen.getByText('Odškrtněte srovnání a přepočítáme odhad z ostatních.')).toBeInTheDocument();
    // Our figure at 8 of 8 is Valuo's own to within its rounding: 116 708 x 66, shown to 10 000 Kč like Valuo's card.
    const ours = within(card());
    expect(ours.getByText(t(fmtCzk(7_700_000)))).toHaveAttribute('title', fmtCzk(7_702_728));
    expect(ours.getByText(t(fmtMeasuredPricePerM2(116_700, 'sale')))).toHaveAttribute('title', fmtMeasuredPricePerM2(116_708, 'sale'));
    expect(ours.getByText('stejně jako Valuo')).toBeInTheDocument();
    expect(screen.getByText('Odhad ceny · Valuo')).toBeInTheDocument();
    expect(screen.getByText('pásmo spolehlivosti (Valuo)')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Vrátit výchozí' })).toBeDisabled();
  });

  it('puts the adjusted price per m² under the adjusted price: the number our figure is built from', async () => {
    renderSection(page({ history: [FLAT] }));
    await screen.findByText(SALE_KEPT('8'));
    const first = within(screen.getAllByRole('row')[1]);
    expect(first.getByText(t(fmtMeasuredPricePerM2(123_029, 'sale')))).toBeInTheDocument();
    expect(screen.getAllByRole('row')[1]).toHaveTextContent(t(`${fmtCzk(6_050_000)}${fmtMeasuredPricePerM2(123_029, 'sale')}`));
  });

  it('recomputes from the comparables left ticked, against Valuo, with the band from five on', async () => {
    renderSection(page({ history: [FLAT] }));
    await screen.findByText(SALE_KEPT('8'));
    untick(1);
    // 116 717 / 4 453 over the other seven: 7 637 322 Kč, 0,85 % under Valuo's 116 710.
    expect(screen.getByText(SALE_KEPT('7'))).toBeInTheDocument();
    const ours = within(card());
    expect(ours.getByText(t(fmtCzk(7_640_000)))).toHaveAttribute('title', fmtCzk(7_637_322));
    expect(ours.getByText(t(fmtMeasuredPricePerM2(115_700, 'sale')))).toBeInTheDocument();
    expect(ours.getByText(t(`o ${fmtPct(0.8508)} pod Valuo`))).toBeInTheDocument();
    // The band is one standard deviation each way: 111 264 to 120 170 Kč/m², times Valuo's 66 m².
    const band = ours.getByText('pásmo').parentElement!;
    expect(band).toHaveTextContent(t(`${fmtCount(7_340_000)}–${fmtCzk(7_930_000)}`));
    expect(within(band).getByTitle(/Průměr ± jedna směrodatná odchylka/)).toHaveAttribute(
      'title', expect.stringContaining(`${fmtCount(111_264)}–${fmtMeasuredPricePerM2(120_170, 'sale')}`));
    expect(ours.queryByText('orientační')).not.toBeInTheDocument();
    expect(box(2)).toBeChecked();
    expect(box(1)).not.toBeChecked();
    // Valuo's card did not move.
    expect(screen.getByText(t(fmtCzk(7_700_000)), { selector: 'span[title]' })).toBeInTheDocument();
    expect(screen.getByText('pásmo spolehlivosti (Valuo)')).toBeInTheDocument();
  });

  it('marks 3 or 4 kept as orientační with no band, and asks for 3 below that, at one fixed card height', async () => {
    const { container } = renderSection(page({ history: [FLAT] }));
    await screen.findByText(SALE_KEPT('8'));
    const height = card().className.match(/min-h-\[[^\]]+\]/)![0];
    expect(container.querySelectorAll(`[class*="${height}"]`)).toHaveLength(1);
    untick(1, 2, 3);
    expect(screen.getByText(SALE_KEPT('5'))).toBeInTheDocument(); // five is the first with a band
    expect(within(card()).getByText('pásmo')).toBeInTheDocument();
    expect(within(card()).queryByText('orientační')).not.toBeInTheDocument();
    untick(4);
    expect(screen.getByText(SALE_KEPT('4'))).toBeInTheDocument();
    expect(within(card()).getByText('orientační')).toBeInTheDocument();
    expect(within(card()).queryByText('pásmo')).not.toBeInTheDocument();
    expect(within(card()).getByText(t(fmtCzk(7_530_000)))).toBeInTheDocument(); // 114 054 x 66
    untick(5);
    expect(screen.getByText(SALE_KEPT('3'))).toBeInTheDocument();
    expect(within(card()).getByText('orientační')).toBeInTheDocument();
    untick(6);
    expect(screen.getByText(SALE_KEPT('2'))).toBeInTheDocument();
    expect(within(card()).getByText(whole('Pro přepočet ponechte alespoň 3 srovnání'))).toBeInTheDocument();
    expect(within(card()).queryByText(/Kč/)).not.toBeInTheDocument();
    expect(within(card()).queryByText('orientační')).not.toBeInTheDocument();
    // The same card, the same height class, the same place in the page in every state.
    expect(container.querySelectorAll(`[class*="${height}"]`)).toHaveLength(1);
    expect(card()).toHaveClass(height);
  });

  it('gives the values back with Vrátit výchozí', async () => {
    renderSection(page({ history: [FLAT] }));
    await screen.findByText(SALE_KEPT('8'));
    untick(1, 2);
    const reset = screen.getByRole('button', { name: 'Vrátit výchozí' });
    expect(reset).toBeEnabled();
    fireEvent.click(reset);
    expect(screen.getByText(SALE_KEPT('8'))).toBeInTheDocument();
    expect(boxes().every((b) => (b as HTMLInputElement).checked)).toBe(true);
    expect(reset).toBeDisabled();
    fireEvent.click(box(3)); // unticking and re-ticking undoes as well
    fireEvent.click(box(3));
    expect(screen.getByText(SALE_KEPT('8'))).toBeInTheDocument();
  });

  it('reads a lease monthly: /měs on the total and the band, the rent unit on the per-m²', async () => {
    renderSection(page({ history: [LEASE] }));
    await screen.findByText(SALE_KEPT('8'));
    untick(1);
    // 376 Kč/m² x 50 m² = 18 800 Kč a month; the band 349 to 403 Kč/m²: 17 450 to 20 150.
    const ours = within(card());
    expect(ours.getByText(t(fmtCzk(18_800)), { exact: false })).toHaveTextContent(t(`${fmtCzk(18_800)}/měs`));
    expect(ours.getByText(t(fmtMeasuredPricePerM2(376, 'rent')))).toBeInTheDocument();
    expect(ours.getByText('pásmo').parentElement).toHaveTextContent(t(`${fmtCount(17_500)}–${fmtCzk(20_200)}/měs`));
    expect(ours.getByText(t(`o ${fmtPct(0.2653)} pod Valuo`))).toBeInTheDocument();
    expect(within(screen.getAllByRole('row')[1]).getByText(t(fmtMeasuredPricePerM2(380, 'rent')))).toBeInTheDocument();
  });

  it('tags the subject own ad, even a delisted one, and leaves it ticked', async () => {
    const OWN_ID = 'https://www.example.invalid/detail/prodej/byt/2+kk/praha-1/4700001';
    const answerWithOwn: Valuation = {
      ...FLAT, records: comparables(FLAT_ROWS, (i) => (i === 0 ? `${OWN_ID}/` : `https://www.example.invalid/detail/x/${4800000 + i}`)),
    };
    // The property's ads as the page holds them, the delisted one too: only its id (not its slug or host) is the same.
    const ours = ['https://www.example.invalid/detail/prodej/byt/2+kk/praha-2/4700099', 'https://m.example.invalid/x/4700001', null];
    renderSection(page({ history: [answerWithOwn] }), { adUrls: ours });
    await screen.findByText(SALE_KEPT('8'));
    expect(screen.getAllByText('tento inzerát')).toHaveLength(1);
    expect(within(screen.getAllByRole('row')[1]).getByText('tento inzerát')).toHaveAttribute('title', expect.stringContaining('Inzerát této nemovitosti'));
    expect(box(1)).toBeChecked();
  });

  it('tags nothing when no ad of the property shares the id', async () => {
    const answerWithOwn: Valuation = { ...FLAT, records: comparables(FLAT_ROWS, (i) => `https://www.example.invalid/detail/x/${4800000 + i}`) };
    renderSection(page({ history: [answerWithOwn] }), { adUrls: ['https://www.example.invalid/detail/x/4700001'] });
    await screen.findByText(SALE_KEPT('8'));
    expect(screen.queryByText('tento inzerát')).not.toBeInTheDocument();
  });

  it('forgets the ticks when another history row is picked', async () => {
    const other: Valuation = { ...FLAT, id: 22, created_at: '2026-10-18T12:02:00Z' };
    renderSection(page({ history: [FLAT, other] }));
    await screen.findByText(SALE_KEPT('8'));
    untick(1, 2);
    expect(screen.getByText(SALE_KEPT('6'))).toBeInTheDocument();
    const pick = (row: Valuation) => fireEvent.click(screen.getByRole('button', { name: (n) => t(n).startsWith(t(fmtDateSlash(row.created_at))) }));
    pick(other);
    expect(await screen.findByText(SALE_KEPT('8'))).toBeInTheDocument();
    expect(boxes().every((b) => (b as HTMLInputElement).checked)).toBe(true);
    untick(3);
    pick(FLAT); // and back: the first row's ticks are not remembered either
    expect(screen.getByText(SALE_KEPT('8'))).toBeInTheDocument();
  });

  it('draws the table exactly as before when the recipe does not land on Valuo (older row, new method, other type)', async () => {
    const off: Valuation = { ...FLAT, result: { ...FLAT.result!, avg_price_m2: 120_500 } }; // 3.2 % away
    renderSection(page({ history: [off] }), { adUrls: ['https://www.example.invalid/x/4700001'] });
    await screen.findByText(/^Srovnávací nemovitosti/);
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument();
    expect(screen.queryByText(/^Náš přepočet/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Odškrtněte/)).not.toBeInTheDocument();
    expect(screen.queryByText('tento inzerát')).not.toBeInTheDocument();
    const second = screen.getAllByRole('row')[1];
    expect(second).toHaveTextContent(t(fmtCzk(6_050_000))); // the adjusted price, with no per-m² line under it
    expect(second).not.toHaveTextContent(fmtMeasuredPricePerM2(123_029, 'sale'));
  });

  it('leaves the table, the ticks and our card off the phone', async () => {
    renderSection(page({ history: [FLAT] }), { readOnly: true });
    expect(await screen.findByText(t(fmtCzk(7_700_000)))).toBeInTheDocument();
    expect(screen.queryByRole('table')).not.toBeInTheDocument();
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument();
    expect(screen.queryByText(/Náš přepočet/)).not.toBeInTheDocument();
  });
});
