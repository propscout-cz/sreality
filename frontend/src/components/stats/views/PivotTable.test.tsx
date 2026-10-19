import { describe, expect, it } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import PivotTable from './PivotTable';
import { cell, pivotOf } from '../testPivot';

const sp = (s: string | null | undefined) => (s ?? '').replace(/\s/g, ' ').trim();

describe('<PivotTable> without a column dimension', () => {
  const pivot = pivotOf({
    spec: { rows: ['disposition', 'condition'], cols: [], measure: 'area' },
    cells: [
      cell([], 60, 55.25),
      cell(['2+kk'], 30, 60),
      cell(['1+kk'], 30, 40),
      cell(['2+kk', 'spatny'], 4, null),
      cell(['2+kk', 'novostavba'], 26, 62),
      cell(['1+kk', 'dobry'], 30, 40),
    ],
  });

  it('carries the seven statistics, the unit named once from the payload', () => {
    render(<PivotTable pivot={pivot} stats={['median']} />);
    const heads = screen.getAllByRole('columnheader').map((h) => sp(h.textContent));
    expect(heads).toEqual([
      'Dispozice › Stav', 'n', 'Plocha · m²', 'průměr', 'medián', 'p10', 'p25', 'p75', 'p90',
    ]);
  });

  it('shows each rollup row above its children, indented by depth', () => {
    render(<PivotTable pivot={pivot} stats={['median']} />);
    const rowHeads = screen.getAllByRole('rowheader');
    expect(rowHeads.map((h) => h.textContent)).toEqual([
      'Celkem', '1+kk', 'Dobrý', '2+kk', 'Novostavba', 'Špatný',
    ]);
    const pad = rowHeads.map((h) => parseFloat(h.style.paddingLeft));
    expect(pad[0]).toBeLessThan(pad[1]);
    expect(pad[1]).toBeLessThan(pad[2]);
    expect(pad[3]).toBe(pad[1]);
  });

  it('sets a rollup row in medium weight and a leaf in normal — one weight class each', () => {
    render(<PivotTable pivot={pivot} stats={['median']} />);
    const weights = (name: string) =>
      screen.getByRole('rowheader', { name }).className.split(' ').filter((c) => c.startsWith('font-'));
    expect(weights('Celkem')).toEqual(['font-medium']);
    expect(weights('2+kk')).toEqual(['font-medium']);
    expect(weights('Novostavba')).toEqual(['font-normal']);
  });

  it('names the table for assistive tech: the chart title and the unit', () => {
    render(<PivotTable pivot={pivot} stats={['median']} />);
    expect(screen.getByRole('table', { name: 'Plocha · Dispozice › Stav (m²)' })).toBeInTheDocument();
  });

  it('gives a cell under the minimum its n and the dashed mark, never a number', () => {
    render(<PivotTable pivot={pivot} stats={['median']} />);
    const row = screen.getByRole('rowheader', { name: 'Špatný' }).closest('tr')!;
    const cells = within(row).getAllByRole('cell');
    expect(cells.map((c) => sp(c.textContent))).toEqual(['4', 'pod 10méně než 10 nemovitostí, bez statistik']);
    const full = screen.getByRole('rowheader', { name: 'Celkem' }).closest('tr')!;
    expect(within(full).getAllByRole('cell').map((c) => sp(c.textContent))).toEqual([
      '60', '55,3', '55,3', '35,3', '45,3', '65,3', '75,3',
    ]);
  });
});

describe('<PivotTable> with a column dimension', () => {
  const pivot = pivotOf({
    spec: { rows: ['disposition'], cols: ['has_lift'], measure: 'area' },
    cells: [
      cell([], 40, 50, 'ne'),
      cell([], 30, 60, 'ano'),
      cell(['1+kk'], 40, 50, 'ne'),
      cell(['2+kk'], 30, 60, 'ano'),
      cell(['1+kk'], 3, null, 'ano'),
    ],
  });

  it('gives each column value one sub-column per picked statistic, then its n', () => {
    render(<PivotTable pivot={pivot} stats={['p90']} />);
    const heads = screen.getAllByRole('columnheader').map((h) => sp(h.textContent));
    expect(heads).toEqual([
      'Dispozice', 'Výtah — Plocha · m²',
      'Ano', 'Ne',
      'p90', 'n', 'p90', 'n',
    ]);
    const row = screen.getByRole('rowheader', { name: '1+kk' }).closest('tr')!;
    expect(within(row).getAllByRole('cell').map((c) => sp(c.textContent))).toEqual([
      'pod 10méně než 10 nemovitostí, bez statistik', '3',
      '70', '40',
    ]);
  });

  it('shows every picked statistic in the picked order under each column value', () => {
    render(<PivotTable pivot={pivot} stats={['p90', 'median', 'p10']} />);
    const heads = screen.getAllByRole('columnheader').map((h) => sp(h.textContent));
    expect(heads.slice(4)).toEqual(['p90', 'medián', 'p10', 'n', 'p90', 'medián', 'p10', 'n']);
    const row = screen.getByRole('rowheader', { name: '1+kk' }).closest('tr')!;
    /* 'ne' (40 properties, median 50): p90 = 70, median = 50, p10 = 30, then n. */
    expect(within(row).getAllByRole('cell').map((c) => sp(c.textContent)).slice(-4)).toEqual(['70', '50', '30', '40']);
  });

  it('spans the under-minimum mark over the statistics and keeps the n', () => {
    render(<PivotTable pivot={pivot} stats={['median', 'p10']} />);
    const row = screen.getByRole('rowheader', { name: '1+kk' }).closest('tr')!;
    const first = within(row).getAllByRole('cell')[0];
    expect(first).toHaveAttribute('colspan', '2');
    expect(sp(first.textContent)).toContain('pod 10');
  });

  it('marks a combination with no property as empty, not as zero, across all its sub-columns', () => {
    render(<PivotTable pivot={pivot} stats={['median', 'p10']} />);
    const row = screen.getByRole('rowheader', { name: '2+kk' }).closest('tr')!;
    const empty = within(row).getAllByRole('cell').find((c) => c.textContent?.includes('žádná nemovitost'))!;
    expect(empty).toHaveAttribute('colspan', '3');
  });
});

describe('<PivotTable> on place dimensions', () => {
  /* Keys are RÚIAN codes; the payload's `places` names them. */
  const pivot = pivotOf({
    spec: { rows: ['obec'], cols: ['kraj'], measure: 'area' },
    places: [
      { dim: 'obec', key: '990500001', name: 'Lhota' },
      { dim: 'obec', key: '990500002', name: 'Lhota' },
      { dim: 'obec', key: '582786', name: 'Brno' },
      { dim: 'kraj', key: '116', name: 'Jihomoravský kraj' },
    ],
    cells: [
      cell([], 50, 50, '116'),
      cell(['990500002'], 10, 40, '116'),
      cell(['582786'], 30, 60, '116'),
      cell(['990500001'], 10, 45, '116'),
    ],
  });

  it('labels rows and columns by name, two places of one name as two rows', () => {
    render(<PivotTable pivot={pivot} stats={['median']} />);
    expect(screen.getAllByRole('rowheader').map((h) => h.textContent)).toEqual([
      'Celkem', 'Brno', 'Lhota', 'Lhota',
    ]);
    expect(screen.getByRole('columnheader', { name: 'Jihomoravský kraj' })).toBeInTheDocument();
  });
});
