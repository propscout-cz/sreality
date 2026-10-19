import { afterAll, beforeAll, describe, expect, it, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import PivotLines from './PivotLines';
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

const pivot = pivotOf({
  spec: { rows: ['disposition'], cols: ['condition'], measure: 'area' },
  cells: [
    cell(['1+kk'], 20, 40, 'novostavba'),
    cell(['2+kk'], 20, 55, 'novostavba'),
    cell(['1+kk'], 20, 38, 'dobry'),
    cell(['2+kk'], 6, null, 'dobry'),
    cell(['1+kk'], 20, 30, 'spatny'),
    cell(['2+kk'], 20, 50, 'spatny'),
  ],
});

describe('<PivotLines>', () => {
  it('draws one line per column value, copper first, then the ink tints', () => {
    const { container } = render(<PivotLines pivot={pivot} stat="median" />);
    const strokes = [...container.querySelectorAll('.recharts-line-curve')].map((p) => p.getAttribute('stroke'));
    expect(strokes).toEqual(['#3c6e63', '#1a1c22', '#4a4d57']);
  });

  it('keys the series in the registry order and names the statistic and the unit', () => {
    render(<PivotLines pivot={pivot} stat="median" />);
    const legend = screen.getByRole('list', { name: 'Řady grafu' });
    expect([...legend.querySelectorAll('li')].map((li) => li.textContent)).toEqual([
      'Novostavba',
      'Dobrý',
      'Špatný',
    ]);
    expect(screen.getByText('medián · m²')).toBeInTheDocument();
  });

  it('labels the value axis through valueAxisSpec, with no repeated tick', () => {
    const { container } = render(<PivotLines pivot={pivot} stat="median" />);
    const ticks = [...container.querySelectorAll('.recharts-yAxis text')].map((t) => t.textContent);
    expect(ticks.length).toBeGreaterThan(1);
    expect(new Set(ticks).size).toBe(ticks.length);
    const x = [...container.querySelectorAll('.recharts-xAxis text')].map((t) => t.textContent);
    expect(x).toEqual(['1+kk', '2+kk']);
  });

  it('gives the plot a text alternative: the same numbers as a screen-reader table', () => {
    const { container } = render(<PivotLines pivot={pivot} stat="median" />);
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
    render(<PivotLines pivot={pivot} stat="median" />);
    expect(screen.getByText(/Chybějící bod = buňka pod 10 nemovitostí/)).toBeInTheDocument();
  });
});
