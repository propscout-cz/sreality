import { describe, expect, it } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import BoxTable, { boxAxis } from './BoxTable';
import { pivotRows } from '@/lib/stats';
import { cell, pivotOf } from '../testPivot';

const sp = (s: string | null | undefined) => (s ?? '').replace(/\s/g, ' ').trim();

/* p10..p90: Celkem 100..300, 1+kk 120..200, 2+kk 220..300, 3+kk under the minimum. */
const pivot = pivotOf({
  spec: { rows: ['disposition'], cols: [], measure: 'tom_days' },
  cells: [
    cell([], 90, 200, null, 50),
    cell(['1+kk'], 40, 160, null, 20),
    cell(['2+kk'], 45, 260, null, 20),
    cell(['3+kk'], 4, null),
  ],
});

describe('<BoxTable> — THE box primitive', () => {
  it('spans one shared axis over every drawn row\'s p10..p90, nothing else', () => {
    const axis = boxAxis(pivotRows(pivot))!;
    expect(axis.domain[0]).toBeLessThanOrEqual(100);
    expect(axis.domain[1]).toBeGreaterThanOrEqual(300);
    const step = axis.ticks[1] - axis.ticks[0];
    expect(100 - axis.domain[0]).toBeLessThan(step);
    expect(axis.domain[1] - 300).toBeLessThan(step);
  });

  it('places each box on that axis', () => {
    const { container } = render(<BoxTable pivot={pivot} />);
    const medians = [...container.querySelectorAll('[data-median]')] as HTMLElement[];
    expect(medians).toHaveLength(3);
    const axis = boxAxis(pivotRows(pivot))!;
    const pct = (v: number) => ((v - axis.domain[0]) / (axis.domain[1] - axis.domain[0])) * 100;
    expect(parseFloat(medians[1].style.left)).toBeCloseTo(pct(160));
    const whisker = container.querySelectorAll('[data-whisker]')[2] as HTMLElement;
    expect(parseFloat(whisker.style.left)).toBeCloseTo(pct(220));
    expect(parseFloat(whisker.style.width)).toBeCloseTo(pct(300) - pct(220));
  });

  it('lists the five numbers it draws as columns, with n and the unit', () => {
    render(<BoxTable pivot={pivot} />);
    expect(screen.getByText(/Doba na trhu · dní/)).toBeInTheDocument();
    const heads = screen.getAllByRole('columnheader').map((h) => h.textContent);
    expect(heads.slice(3)).toEqual(['p10', 'p25', 'medián', 'p75', 'p90']);
    const row = screen.getByRole('rowheader', { name: '1+kk' }).closest('tr')!;
    expect(within(row).getAllByRole('cell').map((c) => sp(c.textContent))).toEqual([
      '40', '', '120', '140', '160', '180', '200',
    ]);
  });

  it('shows a row under the minimum as its n and the dashed mark, no box', () => {
    render(<BoxTable pivot={pivot} />);
    const row = screen.getByRole('rowheader', { name: '3+kk' }).closest('tr')!;
    expect(row.querySelector('[data-box]')).toBeNull();
    expect(within(row).getAllByRole('cell').map((c) => sp(c.textContent))).toEqual([
      '4', 'pod 5méně než 5 nemovitostí, bez statistik',
    ]);
  });

  it('labels the shared axis with distinct ticks', () => {
    const { container } = render(<BoxTable pivot={pivot} />);
    const ticks = [...container.querySelectorAll('[data-tick]')].map((t) => t.textContent);
    expect(ticks.length).toBeGreaterThan(1);
    expect(new Set(ticks).size).toBe(ticks.length);
  });
});
