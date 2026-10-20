/* The Valuo section draws its editor from the API's fields[] alone, tags every
 * value with where it came from, sends only what the user changed, and renders
 * every server sentence exactly as sent. */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import { ApiError, apiGet, apiPost } from '@/lib/api';
import { fmtCzk, fmtDateSlash, fmtMeasuredPricePerM2, fmtPct, fmtTime24 } from '@/lib/format';
import { estimationKeys, fetchEstimationsForListings, type PropertyPublic } from '@/lib/queries';
import type { EstimationRun } from '@/lib/types';
import type { Valuation, ValuoField, ValuoPage } from '@/lib/valuo';

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
  unit: null, options: [], required: false, group: 'more', fixed: false, ...f,
});
const opts = (...pairs: [string, string][]) => pairs.map(([value, label]) => ({ value, label }));

const FIELDS: ValuoField[] = [
  field({ name: 'property_type', label: 'Nemovitost', type: 'enum', fixed: true, required: true, group: 'basic',
          options: opts(['flat', 'Byt'], ['house', 'Dům'], ['land', 'Pozemek']) }),
  field({ name: 'kind', label: 'Typ obchodu', type: 'enum', required: true, group: 'basic',
          options: opts(['sale', 'Prodej'], ['lease', 'Pronájem']) }),
  field({ name: 'floor_area', label: 'Plocha', type: 'number', unit: 'm²', required: true, group: 'basic' }),
  field({ name: 'rating', label: 'Stav', type: 'enum', required: true, group: 'basic',
          options: opts(['bad', 'Neobyvatelný'], ['nothing_much', 'Před rekonstrukcí'], ['good', 'Průměrný'],
                        ['very_good', 'Po kompletní rekonstrukci']) }),
  field({ name: 'as_of', label: 'Ke dni', type: 'date', group: 'basic' }),
  field({ name: 'floor', label: 'Podlaží', type: 'integer' }),
  field({ name: 'energy_performance', label: 'Energetická třída', type: 'enum',
          options: opts(...[...'ABCDEFG'].map((c): [string, string] => [c, c])) }),
  field({ name: 'elevator', label: 'Výtah', type: 'boolean' }),
];
const DEFAULTS = { property_type: 'flat', kind: 'sale', floor_area: 54.5, as_of: '2026-10-19', floor: 3, elevator: true };
const PROVENANCE: ValuoPage['provenance'] = {
  property_type: { class: 'ad', note: null },
  kind: { class: 'ad', note: null },
  floor_area: { class: 'ad', note: null },
  rating: { class: 'missing', note: null },
  as_of: { class: 'assumed', note: 'Dnešní datum.' },
  floor: { class: 'ad', note: null },
  energy_performance: { class: 'assumed', note: 'Třídu G uvádějí i inzeráty bez průkazu PENB.' },
  elevator: { class: 'property', note: null },
};

const SALE: Valuation = {
  id: 1,
  created_at: '2026-10-19T12:02:00Z',
  environment: 'sandbox',
  status: 'success',
  request: { ...DEFAULTS, rating: 'very_good' },
  provenance: PROVENANCE,
  result: {
    avg_price: 6_100_000, min_price: 5_600_000, max_price: 6_600_000, avg_price_m2: 111_900,
    min_price_m2: 102_700, max_price_m2: 121_100, std_price_m2: 7_200, calc_area: 54.5, distance: 2000,
    flags: [],
  },
  ignored_ids: ['floor'],
  ignored_labels: ['Podlaží'],
  kind_label: 'Prodej',
  error: null,
  ppm2_basis: 'sale_capital_czk_m2',
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
    expect(screen.getByRole('button', { name: 'Prodej' })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByRole('textbox', { name: 'Plocha' })).toHaveValue('54,5');
    expect(screen.getByText('m²')).toBeInTheDocument();
    const rating = screen.getByRole('combobox', { name: 'Stav' });
    expect(within(rating).getAllByRole('option')[0]).toHaveTextContent('Nevyplněno');
    expect(container.querySelector('input[type="date"]')).toHaveValue('2026-10-19');

    openMore();
    expect(screen.getByRole('textbox', { name: 'Podlaží' })).toHaveValue('3');
    expect(screen.getByRole('combobox', { name: 'Energetická třída' })).toBeInTheDocument();
    for (const name of ['neuvedeno', 'ano', 'ne']) expect(screen.getByRole('button', { name })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'ano' })).toHaveAttribute('aria-pressed', 'true');
  });

  it('tags every value with where it came from, and counts the folded group', async () => {
    renderSection(page());
    await screen.findByText('Byt · 2+kk · Vinohradská 12, Praha');
    expect(screen.getAllByText('z inzerátu')).toHaveLength(3); // property_type, kind, floor_area
    expect(screen.getByText('chybí')).toBeInTheDocument();
    expect(screen.getByText('předpoklad')).toBeInTheDocument();
    expect(screen.getByRole('img', { name: 'Dnešní datum.' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Další parametry · 2 vyplněno · 1 předpoklad' })).toBeInTheDocument();

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
    openMore();
    fireEvent.click(screen.getByRole('button', { name: 'neuvedeno' }));
    fireEvent.click(cta());

    await waitFor(() => expect(apiPost).toHaveBeenCalledTimes(1));
    expect(apiPost).toHaveBeenCalledWith('/properties/5/valuo', { overrides: { rating: 'good', elevator: null } });
    // The answer replaces the editor in place.
    expect(await screen.findByText(t(fmtCzk(6_100_000)))).toBeInTheDocument();
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

  it('marks the parameters Valuo ignored on its last answer', async () => {
    renderSection(page({ history: [SALE] }));
    fireEvent.click(await screen.findByRole('button', { name: 'Nové ocenění →' }));
    openMore();
    expect(screen.getAllByText('Valuo ignorovalo')).toHaveLength(1);
  });
});

describe('<ValuoSection> states', () => {
  it('renders a refusal exactly as the server sent it, with no way to call, its stored answer still in Czech', async () => {
    renderSection(page({ refusal: { code: 'anything', message: 'Libovolná věta, jak ji poslal server.' }, fields: [], history: [SALE] }));
    expect(await screen.findByText('Libovolná věta, jak ji poslal server.')).toBeInTheDocument();
    expect(screen.getByText('Valuo')).toBeInTheDocument();
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
    expect(within(screen.getByText('Valuo ignorovalo').parentElement!).getByText('Podlaží')).toBeInTheDocument();
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
    expect(await screen.findByText(t(fmtCzk(6_100_000)))).toBeInTheDocument();
    expect(screen.getByText('Odhad ceny · Valuo')).toBeInTheDocument();
    expect(screen.getByText('1 ocenění')).toBeInTheDocument();
    expect(screen.getByText('Sandbox')).toBeInTheDocument();
    expect(screen.getByText('min · průměr · max (Valuo)')).toBeInTheDocument();
    expect(screen.getByText('nabídka')).toBeInTheDocument();
    expect(screen.getByText('náš odhad')).toBeInTheDocument();
    expect(screen.queryByText('MF')).not.toBeInTheDocument(); // a sale has no rent-map mark
    const above = ((6_450_000 - 6_100_000) / 6_100_000) * 100;
    expect(screen.getByText(t(`Nabídka je o ${fmtPct(above)} nad průměrem Valuo.`))).toBeInTheDocument();
    expect(screen.getByText(t(`± ${fmtMeasuredPricePerM2(7_200, 'sale')}`))).toBeInTheDocument();
    expect(screen.queryByText('Příznaky')).not.toBeInTheDocument(); // an empty list is an answer, not a gap
    expect(screen.queryByText('—')).not.toBeInTheDocument();
    expect(within(screen.getByText('Valuo ignorovalo').parentElement!).getByText('Podlaží')).toBeInTheDocument();
    expect(screen.queryByText('Poloha')).not.toBeInTheDocument(); // a street-level point needs no note
    expect(fetchEstimationsForListings).not.toHaveBeenCalled();
  });

  it('reads a lease monthly on its own basis, against MF, never against a sale ask', async () => {
    const lease: Valuation = {
      ...SALE,
      request: { ...SALE.request, kind: 'lease' },
      kind_label: 'Pronájem',
      ppm2_basis: 'rent_monthly_czk_m2',
      provenance: { ...PROVENANCE, place: { class: 'inferred', note: 'Poloha jen na úrovni obce' } },
      result: { ...SALE.result!, avg_price: 21_000, min_price: 18_000, max_price: 24_000, avg_price_m2: 385, flags: null },
    };
    renderSection(page({ history: [lease] }));
    expect(await screen.findByText('Odhad nájmu · Valuo')).toBeInTheDocument();
    expect(screen.getByText('/měs')).toBeInTheDocument();
    expect(screen.getByText(t(fmtMeasuredPricePerM2(385, 'rent')))).toBeInTheDocument();
    expect(screen.getByText('MF')).toBeInTheDocument();
    expect(screen.queryByText('nabídka')).not.toBeInTheDocument();
    // The card says the point sent was only a town's (it was valued over the town, not blocked).
    expect(within(screen.getByText('Poloha').parentElement!).getByText('Poloha jen na úrovni obce')).toBeInTheDocument();
    expect(within(screen.getByText('Příznaky').parentElement!).getByText('—')).toBeInTheDocument(); // no list: the gap
  });

  it('reads a row from an API that predates its Czech labels (the API and the SPA deploy apart)', async () => {
    const old: Valuation = { ...SALE };
    delete old.kind_label;
    delete old.ignored_labels;
    renderSection(page({ history: [old, { ...old, id: 2 }] }));
    expect(await screen.findByText('Odhad ceny · Valuo')).toBeInTheDocument();
    expect(screen.queryByText('Valuo ignorovalo')).not.toBeInTheDocument();
    expect(within(screen.getAllByRole('row')[1]).getByText('—')).toBeInTheDocument();
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
    fireEvent.click(within(rows[2]).getByRole('button')); // the keyboard's way in
    expect(screen.getAllByText(t(fmtCzk(6_100_000))).length).toBeGreaterThan(1);
    expect(screen.queryByText('Valuo hlásí příliš mnoho dotazů, zkuste to později.')).not.toBeInTheDocument();
  });

  it('on the phone reads the latest answer with no editor and no button, and nothing without one', async () => {
    const { unmount } = renderSection(page({ history: [SALE] }), { readOnly: true });
    expect(await screen.findByText(t(fmtCzk(6_100_000)))).toBeInTheDocument();
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
    unmount();

    const { container } = renderSection(page({ history: [] }), { readOnly: true });
    await waitFor(() => expect(apiGet).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });
});
