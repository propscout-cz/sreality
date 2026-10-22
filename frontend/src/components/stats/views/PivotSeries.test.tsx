import { afterAll, beforeAll, describe, expect, it, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import type { ChartStat } from '@/lib/statsRegistry.generated';
import { seriesModel, type StatsPivot } from '@/lib/stats';
import PivotSeries from './PivotSeries';
import { cell, pivotOf } from '../testPivot';

/* ResponsiveContainer measures a 0x0 parent in jsdom; a fixed size makes the
 * axes and lines actually render (the PriceLineChart test's recipe). */
vi.mock('recharts', async () => {
  const actual = await vi.importActual<typeof import('recharts')>('recharts');
  return {
    ...actual,
    ResponsiveContainer: ({ children }: { children: React.ReactElement }) => (
      <div>{{ ...children, props: { ...children.props, width: 640, height: 240 } }}</div>
    ),
  };
});

const TOKENS: Record<string, string> = {
  '--color-copper': '#3c6e63',
  '--color-ink': '#1a1c22',
  '--color-ink-2': '#4a4d57',
  '--color-ink-3': '#7a7d86',
  '--color-ink-4': '#b0b1b7',
};
beforeAll(() => {
  for (const [k, v] of Object.entries(TOKENS)) document.documentElement.style.setProperty(k, v);
});
afterAll(() => {
  for (const k of Object.keys(TOKENS)) document.documentElement.style.removeProperty(k);
});

/* The card builds the model (StatsChart) and hands it to the view. */
const draw = (pivot: StatsPivot, stats: ChartStat[], kind: 'line' | 'bar') =>
  render(<PivotSeries pivot={pivot} model={seriesModel(pivot, stats)} stats={stats} kind={kind} />);

describe('<PivotSeries kind="line">', () => {
  const pivot = pivotOf({
    spec: { rows: ['disposition'], cols: ['condition'], measure: 'area' },
    cells: [
      cell(['1+kk'], 20, 40, 'novostavba'),
      cell(['2+kk'], 20, 55, 'novostavba'),
      cell(['1+kk'], 20, 38, 'dobry'),
      cell(['2+kk'], 4, null, 'dobry'),
      cell(['1+kk'], 20, 30, 'spatny'),
      cell(['2+kk'], 20, 50, 'spatny'),
    ],
  });

  it('draws one line per column value, copper first, then the ink tints', () => {
    const { container } = draw(pivot, ['median'], 'line');
    const strokes = [...container.querySelectorAll('.recharts-line-curve')].map((p) => p.getAttribute('stroke'));
    expect(strokes).toEqual(['#3c6e63', '#1a1c22', '#4a4d57']);
  });

  it('draws one line per picked statistic, the first pick copper, and names them all in the caption', () => {
    const one = pivotOf({
      spec: { rows: ['disposition'], cols: [], measure: 'area' },
      cells: [cell(['1+kk'], 20, 40), cell(['2+kk'], 20, 55), cell(['3+kk'], 20, 70)],
    });
    const { container } = draw(one, ['p90', 'median', 'p10'], 'line');
    const strokes = [...container.querySelectorAll('.recharts-line-curve')].map((p) => p.getAttribute('stroke'));
    expect(strokes).toEqual(['#3c6e63', '#1a1c22', '#4a4d57']);
    expect(screen.getByText(/p90, medián, p10/)).toBeInTheDocument();
    const legend = screen.getByRole('list', { name: 'Řady grafu' });
    expect([...legend.querySelectorAll('li')].map((li) => li.textContent)).toEqual(['p90', 'medián', 'p10']);
  });

  it('keys the series in the registry order and names the statistic and the unit', () => {
    draw(pivot, ['median'], 'line');
    const legend = screen.getByRole('list', { name: 'Řady grafu' });
    expect([...legend.querySelectorAll('li')].map((li) => li.textContent)).toEqual([
      'Novostavba',
      'Dobrý',
      'Špatný',
    ]);
    expect(screen.getByText('medián · m²')).toBeInTheDocument();
  });

  it('labels the value axis through valueAxisSpec, with no repeated tick', () => {
    const { container } = draw(pivot, ['median'], 'line');
    const ticks = [...container.querySelectorAll('.recharts-yAxis text')].map((t) => t.textContent);
    expect(ticks.length).toBeGreaterThan(1);
    expect(new Set(ticks).size).toBe(ticks.length);
    const x = [...container.querySelectorAll('.recharts-xAxis text')].map((t) => t.textContent);
    expect(x).toEqual(['1+kk', '2+kk']);
  });

  it('gives the plot a text alternative: the same numbers as a screen-reader table', () => {
    const { container } = draw(pivot, ['median'], 'line');
    const table = screen.getByRole('table', { name: 'medián · m²' });
    expect(within(table).getAllByRole('columnheader').map((h) => h.textContent)).toEqual([
      'Dispozice', 'Novostavba', 'Dobrý', 'Špatný',
    ]);
    const row = within(table).getByRole('rowheader', { name: '2+kk' }).closest('tr')!;
    expect(within(row).getAllByRole('cell').map((c) => c.textContent)).toEqual(['55', '—', '50']);
    /* The SVG is geometry; assistive tech reads the table instead. */
    expect(container.querySelector('svg.recharts-surface')?.closest('[aria-hidden="true"]')).not.toBeNull();
  });

  it('leaves a gap for a cell under the minimum and says so', () => {
    draw(pivot, ['median'], 'line');
    expect(screen.getByText(/Chybějící bod = buňka pod 5 nemovitostí/)).toBeInTheDocument();
  });
});

describe('<PivotSeries kind="bar">', () => {
  const pivot = pivotOf({
    spec: { rows: ['has_lift'], cols: [], measure: 'price' },
    basis: 'rent_monthly_czk_m2',
    category_type: 'pronajem',
    cells: [cell([], 40, 20_000), cell(['ne'], 20, 18_000), cell(['ano'], 20, 22_000)],
  });

  it('draws one bar per x value from a zero-based axis', () => {
    const { container } = draw(pivot, ['mean'], 'bar');
    expect(container.querySelectorAll('.recharts-bar-rectangle')).toHaveLength(2);
    const ticks = [...container.querySelectorAll('.recharts-yAxis text')].map((t) => t.textContent);
    /* valueAxisSpec puts one scale unit on the whole axis, so zero reads "0 k". */
    expect(ticks[0]).toMatch(/^0(\s|$)/);
    expect([...container.querySelectorAll('.recharts-xAxis text')].map((t) => t.textContent)).toEqual([
      'Ano',
      'Ne',
    ]);
  });

  it('names the statistic and the rent period from the payload basis; one series needs no key', () => {
    draw(pivot, ['mean'], 'bar');
    expect(screen.getByText('průměr · Kč / měs')).toBeInTheDocument();
    expect(screen.queryByRole('list', { name: 'Řady grafu' })).toBeNull();
  });
});
