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
  id: 'c1', view: 'table', rows: ['disposition'], cols: [], measure: 'area', stats: ['median'],
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
const item = (name: string) => screen.getByRole('menuitem', { name });
const MENU = 'Další akce grafu Plocha · Dispozice';

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

  /* Ruling P14 (2026-10-27): Upravit stays; ↑, ↓ and Odebrat sit in a ⋯ menu. */
  it('shows Upravit and a ⋯ menu that holds the moves and Odebrat', () => {
    const later = vi.fn();
    renderChart({ onMove: { later } });
    const header = card('Plocha · Dispozice').querySelector('header')!;
    expect(within(header).getAllByRole('button').map((b) => b.getAttribute('aria-label'))).toEqual([
      'Upravit graf Plocha · Dispozice',
      'Další akce grafu Plocha · Dispozice',
    ]);
    fireEvent.click(button(MENU));
    expect(screen.getAllByRole('menuitem').map((i) => i.textContent)).toEqual([
      '↑ Posunout dopředu',
      '↓ Posunout dozadu',
      'Odebrat',
    ]);
    expect(item('↑ Posunout dopředu')).toHaveAttribute('aria-disabled', 'true');
    fireEvent.click(item('↓ Posunout dozadu'));
    expect(later).toHaveBeenCalledTimes(1);
  });

  it('offers no moves where the board gives none', () => {
    renderChart();
    fireEvent.click(button(MENU));
    expect(screen.getAllByRole('menuitem').map((i) => i.textContent)).toEqual(['Odebrat']);
  });

  it('shows no actions on a read-only card', async () => {
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <StatsChart chart={CHART} filters={DEFAULT_FILTERS} />
      </QueryClientProvider>,
    );
    await screen.findByRole('table');
    expect(screen.queryByRole('button', { name: /Upravit|Další akce/ })).toBeNull();
  });

  it('removes only after one confirm step', () => {
    const { onRemove } = renderChart();
    fireEvent.click(button(MENU));
    fireEvent.click(item('Odebrat'));
    expect(onRemove).not.toHaveBeenCalled();
    fireEvent.click(button('Ponechat graf'));
    fireEvent.click(button(MENU));
    fireEvent.click(item('Odebrat'));
    fireEvent.click(button('Ano, odebrat graf Plocha · Dispozice'));
    expect(onRemove).toHaveBeenCalledTimes(1);
  });

  it('moves keyboard focus to where the next action is on every flip', async () => {
    renderChart();
    fireEvent.click(button(MENU));
    fireEvent.click(item('Odebrat'));
    expect(document.activeElement).toBe(button('Ponechat graf'));
    fireEvent.click(button('Ponechat graf'));
    expect(document.activeElement).toBe(button(MENU));
    fireEvent.click(button('Upravit graf Plocha · Dispozice'));
    expect(document.activeElement).toBe(screen.getByRole('group', { name: 'Úprava grafu Plocha · Dispozice' }));
    fireEvent.click(button('Zrušit úpravy grafu'));
    expect(document.activeElement).toBe(button('Upravit graf Plocha · Dispozice'));
    fireEvent.click(button('Upravit graf Plocha · Dispozice'));
    fireEvent.click(button('Uložit graf'));
    await waitFor(() => expect(document.activeElement).toBe(button('Upravit graf Plocha · Dispozice')));
  });

  /* Ruling S09 (2026-10-27): every cell under the minimum is said once, above the
   * table, with a way to show the small samples; the rows and their counts stay. */
  describe('when every cell is under the minimum', () => {
    const NOTE = 'Všechny buňky mají méně než 5 nemovitostí, takže statistiky jsou skryté.';
    const allSmall = (celkem = 3) =>
      vi.mocked(stats.fetchStatsPivot).mockImplementation(async (_f, spec) =>
        pivotOf({
          spec,
          cells: [cell([], celkem, celkem >= 5 ? 50 : null), ...(spec.rows.length ? [cell(['1+kk'], 2, null), cell(['2+kk'], 1, null)] : [])],
        }),
      );
    const note = () => screen.queryByText(/^Všechny buňky mají méně než/);
    const qc = () => new QueryClient({ defaultOptions: { queries: { retry: false } } });

    it('says once, under the legend, when every cell is under the minimum, and turns the small samples on', async () => {
      allSmall();
      const onShowSmall = vi.fn();
      const client = qc();
      const box: stats.ChartSpec = { ...CHART, view: 'box' };
      const at = (showSmall: boolean) => (
        <QueryClientProvider client={client}>
          <StatsChart chart={box} filters={DEFAULT_FILTERS} onSave={vi.fn()} onRemove={vi.fn()} onShowSmall={onShowSmall} showSmall={showSmall} />
        </QueryClientProvider>
      );
      const { rerender } = render(at(false));
      const c = card('Plocha · Dispozice');
      const line = await within(c).findByText(/^Všechny buňky mají méně než/);
      expect(line.textContent).toBe(NOTE);
      expect(within(c).getAllByText(/^Všechny buňky/)).toHaveLength(1);
      /* The picture's order: the legend, the note, the column heads. */
      const legend = within(c).getByText(/vousy p10–p90/);
      const heads = within(c).getByRole('columnheader', { name: 'n' });
      expect(legend.compareDocumentPosition(line) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
      expect(line.compareDocumentPosition(heads) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
      /* The table keeps the legend as its name; the note and its button stay out of it. */
      const table = within(c).getByRole('table', { name: /vousy p10–p90/ });
      expect(table).not.toHaveAccessibleName(/Zobrazit|Všechny/);
      /* Every row and its count stay, each with the dashed mark. */
      expect(within(c).getAllByRole('rowheader')).toHaveLength(3);
      expect(within(c).getAllByText('pod 5')).toHaveLength(3);
      fireEvent.click(within(c).getByRole('button', { name: 'Zobrazit malé vzorky' }));
      expect(onShowSmall).toHaveBeenCalledTimes(1);
      /* The pressed button goes with the note: focus waits on the card's heading. */
      expect(document.activeElement).toBe(within(c).getByRole('heading', { level: 3 }));
      rerender(at(true));
      expect(note()).toBeNull();
      expect(screen.queryByRole('button', { name: 'Zobrazit malé vzorky' })).toBeNull();
      expect(document.activeElement).toBe(within(c).getByRole('heading', { level: 3 }));
    });

    it('puts the note directly above a plain table', async () => {
      allSmall();
      renderChart({ onShowSmall: vi.fn() });
      const c = card('Plocha · Dispozice');
      const table = await within(c).findByRole('table');
      const line = within(c).getByText(/^Všechny buňky mají méně než/);
      expect(line.textContent).toBe(NOTE);
      expect(within(c).getByText(/Ve filtru/).compareDocumentPosition(line) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
      expect(line.compareDocumentPosition(table) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
      expect(within(c).getAllByRole('rowheader')).toHaveLength(3);
    });

    it('keeps quiet when the switch is on, when any cell reaches the minimum, on a read-only card and on a line chart', async () => {
      const quiet = async (props: Partial<Parameters<typeof StatsChart>[0]>, readOnly = false) => {
        const { unmount } = render(
          <QueryClientProvider client={qc()}>
            {readOnly ? (
              <StatsChart chart={CHART} filters={DEFAULT_FILTERS} {...props} />
            ) : (
              <StatsChart chart={CHART} filters={DEFAULT_FILTERS} onSave={vi.fn()} onRemove={vi.fn()} onShowSmall={vi.fn()} {...props} />
            )}
          </QueryClientProvider>,
        );
        await screen.findByText(/Ve filtru/);
        expect(note()).toBeNull();
        expect(screen.queryByRole('button', { name: 'Zobrazit malé vzorky' })).toBeNull();
        unmount();
      };
      allSmall();
      await quiet({ showSmall: true });
      await quiet({}, true);
      await quiet({ chart: { ...CHART, view: 'line' } });
      allSmall(5);
      await quiet({});
      await quiet({ chart: { ...CHART, view: 'box' } });
    });
  });

  it('opens a new chart with focus in its builder', () => {
    renderChart({ startEditing: true, onCancelNew: vi.fn() });
    expect(document.activeElement).toBe(screen.getByRole('group', { name: 'Úprava grafu Plocha · Dispozice' }));
  });
});
