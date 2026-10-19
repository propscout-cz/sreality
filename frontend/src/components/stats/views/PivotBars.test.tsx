import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import PivotBars from './PivotBars';
import { cell, pivotOf } from '../testPivot';

vi.mock('recharts', async () => {
  const actual = await vi.importActual<typeof import('recharts')>('recharts');
  return {
    ...actual,
    ResponsiveContainer: ({ children }: { children: React.ReactElement }) => (
      <div>{{ ...children, props: { ...children.props, width: 640, height: 240 } }}</div>
    ),
  };
});

const pivot = pivotOf({
  spec: { rows: ['has_lift'], cols: [], measure: 'price' },
  basis: 'rent_monthly_czk_m2',
  category_type: 'pronajem',
  cells: [cell([], 40, 20_000), cell(['ne'], 20, 18_000), cell(['ano'], 20, 22_000)],
});

describe('<PivotBars>', () => {
  it('draws one bar per x value from a zero-based axis', () => {
    const { container } = render(<PivotBars pivot={pivot} stat="mean" />);
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
    render(<PivotBars pivot={pivot} stat="mean" />);
    expect(screen.getByText('průměr · Kč / měs')).toBeInTheDocument();
    expect(screen.queryByRole('list', { name: 'Řady grafu' })).toBeNull();
  });
});
