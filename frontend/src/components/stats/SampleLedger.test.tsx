import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import SampleLedger from './SampleLedger';
import { TooFew } from './views/cells';
import { badSpecPivot, cell, pivotOf } from './testPivot';

const text = (el: Element | null) => (el?.textContent ?? '').replace(/\s/g, ' ');

describe('<SampleLedger> — the mark on every chart', () => {
  it('states what the filter selected, what made the sample and what was set aside', () => {
    const { container } = render(
      <SampleLedger
        pivot={pivotOf({
          spec: { rows: ['disposition'], cols: [], measure: 'area' },
          filtered: 73_537,
          sample: 60_123,
          excluded: 13_414,
          cells: [cell([], 60_123, 50), cell(['1+kk'], 60_119, 40), cell(['2+kk'], 4, null)],
        })}
      />,
    );
    expect(text(container.querySelector('p'))).toBe(
      'Ve filtru 73 537 · vzorek 60 123 · vyřazeno 13 414 (chybí údaje) · 4 v buňkách pod 5',
    );
  });

  it('draws three ink segments: behind a number, under the minimum, excluded', () => {
    const { container } = render(
      <SampleLedger
        pivot={pivotOf({
          spec: { rows: ['disposition'], cols: [], measure: 'area' },
          cells: [cell([], 80, 50), cell(['1+kk'], 76, 40), cell(['2+kk'], 4, null)],
        })}
      />,
    );
    const segs = [...container.querySelectorAll('[data-segment]')] as HTMLElement[];
    expect(segs.map((s) => [s.dataset.segment, s.style.flexGrow])).toEqual([
      ['stats', '76'],
      ['under', '4'],
      ['excluded', '20'],
    ]);
    expect(segs.map((s) => s.style.background)).toEqual([
      'var(--color-ink-2)',
      'var(--color-ink-4)',
      'var(--color-rule-strong)',
    ]);
    expect(screen.getByRole('img').getAttribute('aria-label')).toBe(
      've statistikách 76, 4 v buňkách pod 5, vyřazeno 20',
    );
  });

  it('names the cohort from the payload', () => {
    render(<SampleLedger pivot={pivotOf({ status: 'inactive' })} />);
    const chips = screen.getByRole('list', { name: 'Vzorek' });
    expect([...chips.querySelectorAll('li')].map((li) => li.textContent)).toEqual([
      'byty',
      'prodej',
      'neaktivní',
    ]);
  });

  it('keeps the counts on a refusal', () => {
    const { container } = render(
      <SampleLedger pivot={pivotOf({ refused: 'mixed_status', status: 'mixed', cells: [] })} />,
    );
    expect(text(container.querySelector('p'))).toContain('Ve filtru 100 · vzorek 80 · vyřazeno 20');
    expect(
      [...container.querySelectorAll('[data-segment]')].map((s) => (s as HTMLElement).dataset.segment),
    ).toEqual(['sample', 'excluded']);
    expect(screen.getByText('aktivní i neaktivní')).toBeInTheDocument();
  });

  it('renders nothing when the payload has no counts', () => {
    const { container } = render(<SampleLedger pivot={badSpecPivot()} />);
    expect(container).toBeEmptyDOMElement();
  });
});

describe('<TooFew>', () => {
  it('shows the minimum and says there are no statistics, never a number', () => {
    render(<TooFew minN={5} />);
    expect(screen.getByText('pod 5')).toBeInTheDocument();
    expect(screen.getByText('méně než 5 nemovitostí, bez statistik')).toBeInTheDocument();
  });
});
