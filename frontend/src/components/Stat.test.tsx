/* One tile of a strip (wave 8, contract D3; wave 10 moved Costs' and LocationQuality's Stat
 * copies and AutodedupProgress's Tile onto it). Pins:
 * - the tile, label, value and hint classes (the hint in ink-3, never ink-4);
 * - the number is never coloured: a status is a dot beside the label, its word sr-only;
 * - no dot and no hint line unless asked. */
import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';

import Stat from './Stat';

const VALUE = 'font-mono tabular-nums text-xl text-[var(--color-ink)]';

describe('<Stat>', () => {
  it('draws the tile, the small-caps label, the mono value and an ink-3 hint', () => {
    const { container } = render(<Stat label="Open incidents" value={1} hint="oldest 06:40" />);
    expect(container.firstElementChild?.getAttribute('class')).toBe(
      'rounded-[var(--radius-sm)] border border-[var(--color-rule-soft)] bg-[var(--color-paper-2)] px-3 py-2',
    );
    expect(screen.getByText('Open incidents').parentElement?.getAttribute('class')).toBe(
      'flex items-center gap-1.5 text-[0.62rem] tracking-[0.1em] uppercase text-[var(--color-ink-3)]',
    );
    expect(screen.getByText('1').getAttribute('class')).toBe(VALUE);
    expect(screen.getByText('oldest 06:40').getAttribute('class')).toBe('text-[0.68rem] text-[var(--color-ink-3)]');
    expect(container.querySelector('[aria-hidden="true"]')).toBeNull();
  });

  it('carries a status as a dot beside its label, never as a coloured number', () => {
    const { container } = render(<Stat label="Problems" value={1} status="fail" />);
    expect(container.querySelector('[aria-hidden="true"]')?.getAttribute('class')).toContain('bg-[var(--color-brick)]');
    expect(screen.getByText('Problem').getAttribute('class')).toBe('sr-only');
    expect(screen.getByText('1').getAttribute('class')).toBe(VALUE);
    expect(container.textContent).toBe('ProblemProblems1');
  });

  it('takes a non-numeric value and draws no hint line without one', () => {
    const { container } = render(<Stat label="Gauges worse than last week" value="not comparable" />);
    expect(screen.getByText('not comparable').getAttribute('class')).toBe(VALUE);
    expect(container.firstElementChild?.children).toHaveLength(2);
  });
});
