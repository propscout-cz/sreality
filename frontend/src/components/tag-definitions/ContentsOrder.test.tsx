/* The tag-contents order is a pick-one, so it is the ONE joined bar (operator ruling
 * 2026-10-27, system.md § Option groups): the chosen order filled copper, never the
 * old copper outline; "Least like the rest" stays off, saying why, until the server
 * applied it. */
import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, within } from '@testing-library/react';

import ContentsOrderControl, { type ContentsOrder } from './ContentsOrder';

function renderControl(over: Partial<{ order: ContentsOrder; outlierApplied: boolean; truncated: boolean }> = {}) {
  const onOrderChange = vi.fn();
  render(
    <ContentsOrderControl
      order={over.order ?? 'outlier_first'}
      onOrderChange={onOrderChange}
      outlierApplied={over.outlierApplied ?? true}
      centroidPositives={12}
      minPositives={5}
      truncated={over.truncated ?? false}
      limit={300}
    />,
  );
  return onOrderChange;
}

describe('<ContentsOrderControl>', () => {
  it('is one named joined bar, the chosen order filled copper', () => {
    renderControl();
    const bar = screen.getByRole('group', { name: "Order this tag's images" });
    expect(bar).toHaveClass('inline-grid', 'border', 'bg-[var(--color-paper-2)]');
    const lit = within(bar).getByRole('button', { name: 'Least like the rest' });
    expect(lit).toHaveAttribute('aria-pressed', 'true');
    expect(lit).toHaveClass('bg-[var(--color-copper)]', 'text-[var(--color-on-copper)]');
    expect(lit).not.toHaveClass('border');
    expect(within(bar).getAllByRole('button').map((b) => b.textContent)).toEqual(['Least like the rest', 'Newest first']);
  });

  it('switches to the recent view, its promise on the hover box', () => {
    const onOrderChange = renderControl({ truncated: true });
    const recent = screen.getByRole('button', { name: 'Newest of these 300' });
    expect(recent).toHaveAttribute('title', expect.stringMatching(/selected by DISTANCE/));
    fireEvent.click(recent);
    expect(onOrderChange).toHaveBeenCalledWith('recent');
  });

  it('keeps the distance order off until the server applied it', () => {
    renderControl({ order: 'recent', outlierApplied: false });
    const outlier = screen.getByRole('button', { name: 'Least like the rest' });
    expect(outlier).toBeDisabled();
    expect(outlier).toHaveAttribute('title', expect.stringMatching(/Farthest first/));
    expect(screen.getByRole('button', { name: 'Newest first' })).toHaveClass('bg-[var(--color-copper)]');
  });
});
