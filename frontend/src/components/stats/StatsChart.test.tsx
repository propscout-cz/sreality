import { describe, expect, it, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { DEFAULT_FILTERS } from '@/lib/filters';
import * as stats from '@/lib/stats';
import StatsChart from './StatsChart';
import { cell, pivotOf } from './testPivot';

vi.mock('@/lib/stats', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/stats')>()),
  fetchStatsPivot: vi.fn(),
}));

const CHART: stats.ChartSpec = {
  id: 'c1', view: 'table', rows: ['disposition'], cols: [], measure: 'area', stat: 'median',
};

const renderChart = (props: Partial<Parameters<typeof StatsChart>[0]> = {}) => {
  const onSave = vi.fn(async () => undefined);
  const onRemove = vi.fn();
  const utils = render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <StatsChart chart={CHART} filters={DEFAULT_FILTERS} onSave={onSave} onRemove={onRemove} {...props} />
    </QueryClientProvider>,
  );
  return { ...utils, onSave, onRemove };
};

const card = (name: string) => screen.getByRole('article', { name });
const button = (name: string) => screen.getByRole('button', { name });

beforeEach(() => {
  vi.mocked(stats.fetchStatsPivot).mockImplementation(async (_f, spec) =>
    pivotOf({
      spec,
      cells: [cell([], 80, 50), ...(spec.rows.length ? [cell(['1+kk'], 40, 40), cell(['2+kk'], 40, 60)] : [])],
    }),
  );
});

describe('<StatsChart>', () => {
  it('derives its title and puts the sample ledger above the view', async () => {
    renderChart();
    const c = card('Plocha · Dispozice');
    const table = await within(c).findByRole('table');
    const ledger = within(c).getByText(/Ve filtru/);
    expect(ledger.compareDocumentPosition(table) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(within(c).getByRole('heading', { level: 3 })).toHaveTextContent('Plocha · Dispozice');
    expect(stats.fetchStatsPivot).toHaveBeenCalledWith(
      DEFAULT_FILTERS,
      { rows: ['disposition'], cols: [], measure: 'area' },
      expect.objectContaining({ client: expect.any(QueryClient) }),
    );
  });

  it('renders a refusal as one sentence, and the ledger still counts', async () => {
    vi.mocked(stats.fetchStatsPivot).mockResolvedValueOnce(
      pivotOf({ refused: 'mixed_category', category_main: null, cells: [] }),
    );
    renderChart();
    expect(await screen.findByText(stats.refusalText('mixed_category'))).toBeInTheDocument();
    expect(screen.getByText(/Ve filtru/)).toHaveTextContent(/vzorek\s80/);
    expect(screen.queryByRole('table')).toBeNull();
  });

  it('says which dimension emptied the sample when it does not exist for the category', async () => {
    vi.mocked(stats.fetchStatsPivot).mockResolvedValueOnce(
      pivotOf({ spec: { rows: ['area_band_5'], cols: [], measure: 'area' }, category_main: 'dum', sample: 0, excluded: 100 }),
    );
    renderChart({ chart: { ...CHART, rows: ['area_band_5'] } });
    expect(
      await screen.findByText('Žádná nemovitost ve filtru nemá všechny potřebné údaje. Plocha po 5 m²: jen byty.'),
    ).toBeInTheDocument();
  });

  it('flips the same card into the builder, previews the draft and saves it', async () => {
    const { onSave } = renderChart();
    const c = card('Plocha · Dispozice');
    fireEvent.click(within(c).getByRole('button', { name: 'Upravit graf Plocha · Dispozice' }));
    expect(within(c).getByRole('group', { name: 'Řádky' })).toBeInTheDocument();
    fireEvent.click(within(c).getByRole('button', { name: 'Přidat Stav do Řádků' }));
    expect(within(c).getByRole('heading', { level: 3 })).toHaveTextContent('Plocha · Dispozice › Stav');
    expect(stats.fetchStatsPivot).toHaveBeenLastCalledWith(
      DEFAULT_FILTERS,
      { rows: ['disposition', 'condition'], cols: [], measure: 'area' },
      expect.anything(),
    );
    fireEvent.click(within(c).getByRole('button', { name: 'Uložit graf' }));
    expect(onSave).toHaveBeenCalledWith({ ...CHART, rows: ['disposition', 'condition'] });
    await waitFor(() => expect(within(c).queryByRole('group', { name: 'Řádky' })).toBeNull());
  });

  it('stays in edit mode with the draft when the board refuses the save', async () => {
    const onSave = vi.fn(async () => {
      throw new Error('422');
    });
    renderChart({ onSave });
    fireEvent.click(button('Upravit graf Plocha · Dispozice'));
    fireEvent.click(button('Přidat Stav do Řádků'));
    fireEvent.click(button('Uložit graf'));
    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(button('Uložit graf')).toBeEnabled());
    expect(screen.getByRole('group', { name: 'Řádky' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { level: 3 })).toHaveTextContent('Plocha · Dispozice › Stav');
  });

  it('Zrušit drops the draft', () => {
    const { onSave } = renderChart();
    fireEvent.click(button('Upravit graf Plocha · Dispozice'));
    fireEvent.click(button('Odebrat Dispozice'));
    fireEvent.click(button('Zrušit úpravy grafu'));
    expect(onSave).not.toHaveBeenCalled();
    expect(screen.getByRole('heading', { level: 3 })).toHaveTextContent('Plocha · Dispozice');
  });

  it('will not save a line without its one row, and says why in the footer and the preview', async () => {
    renderChart({ chart: { ...CHART, rows: [] } });
    await screen.findByRole('table');
    fireEvent.click(button('Upravit graf Plocha'));
    fireEvent.click(button('Zobrazení: Čáry'));
    expect(button('Uložit graf')).toBeDisabled();
    const hits = screen.getAllByText(stats.LINE_NEEDS_ROW);
    expect(hits).toHaveLength(2);
    expect(hits.filter((h) => h.closest('footer'))).toHaveLength(1);
  });

  it('holds back a placeholder answer of another spec that the new view cannot draw', async () => {
    /* The two-row answer arrives; the one-row answer the line asks for never does. */
    vi.mocked(stats.fetchStatsPivot).mockImplementation((_f, spec) =>
      spec.rows.length === 2
        ? Promise.resolve(pivotOf({ spec, cells: [cell([], 80, 50)] }))
        : new Promise(() => {}),
    );
    renderChart({ chart: { ...CHART, rows: ['disposition', 'condition'] } });
    const c = card('Plocha · Dispozice › Stav');
    await within(c).findByRole('table');
    fireEvent.click(button('Upravit graf Plocha · Dispozice › Stav'));
    fireEvent.click(button('Odebrat Stav'));
    fireEvent.click(button('Zobrazení: Čáry'));
    expect(within(c).queryByRole('table')).toBeNull();
    expect(screen.getAllByText(/Ve filtru/)).toHaveLength(1);
    expect(screen.queryByText(stats.LINE_NEEDS_ROW)).toBeNull();
  });

  it('refuses more series than the palette tells apart, in one sentence', async () => {
    const obce = Array.from({ length: stats.MAX_SERIES + 1 }, (_, i) => `Obec ${i}`);
    vi.mocked(stats.fetchStatsPivot).mockImplementation(async (_f, spec) =>
      pivotOf({ spec, cells: obce.map((o) => cell(['1+kk'], 20, 40, o)) }),
    );
    renderChart({ chart: { ...CHART, view: 'line', cols: ['obec'] } });
    expect(await screen.findByText(stats.tooManySeriesText())).toBeInTheDocument();
  });

  it('removes only after one confirm step', () => {
    const { onRemove } = renderChart();
    fireEvent.click(button('Odebrat graf Plocha · Dispozice'));
    expect(onRemove).not.toHaveBeenCalled();
    fireEvent.click(button('Ponechat graf'));
    fireEvent.click(button('Odebrat graf Plocha · Dispozice'));
    fireEvent.click(button('Ano, odebrat graf Plocha · Dispozice'));
    expect(onRemove).toHaveBeenCalledTimes(1);
  });

  it('moves keyboard focus to where the next action is on every flip', async () => {
    renderChart();
    fireEvent.click(button('Odebrat graf Plocha · Dispozice'));
    expect(document.activeElement).toBe(button('Ponechat graf'));
    fireEvent.click(button('Ponechat graf'));
    expect(document.activeElement).toBe(button('Odebrat graf Plocha · Dispozice'));
    fireEvent.click(button('Upravit graf Plocha · Dispozice'));
    expect(document.activeElement).toBe(screen.getByRole('group', { name: 'Úprava grafu Plocha · Dispozice' }));
    fireEvent.click(button('Zrušit úpravy grafu'));
    expect(document.activeElement).toBe(button('Upravit graf Plocha · Dispozice'));
    fireEvent.click(button('Upravit graf Plocha · Dispozice'));
    fireEvent.click(button('Uložit graf'));
    await waitFor(() => expect(document.activeElement).toBe(button('Upravit graf Plocha · Dispozice')));
  });

  it('opens a new chart with focus in its builder', () => {
    renderChart({ startEditing: true, onCancelNew: vi.fn() });
    expect(document.activeElement).toBe(screen.getByRole('group', { name: 'Úprava grafu Plocha · Dispozice' }));
  });
});
