/* Past runs' "Filter runs" pick-ones are the ONE joined bar (operator ruling
 * 2026-10-27, system.md § Option groups): the page's own Pill copy is gone,
 * and "all" still clears the URL param. */
import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, useLocation } from 'react-router-dom';

vi.mock('@/lib/queries', async (orig) => ({
  ...(await orig<typeof import('@/lib/queries')>()),
  fetchEstimationsList: vi.fn().mockResolvedValue({ data: [], total: 0, next_cursor: null }),
}));

import EstimationList from './EstimationList';

function Where() {
  return <p data-testid="where">{useLocation().search}</p>;
}

function renderAt(path: string) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[path]}>
        <EstimationList />
        <Where />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const where = () => screen.getByTestId('where').textContent;

describe('<EstimationList> filter bar', () => {
  it('draws Source and Status as joined bars, the chosen segment filled copper, wording and order kept', () => {
    renderAt('/estimations?source=api');
    const source = screen.getByRole('group', { name: 'Source' });
    const status = screen.getByRole('group', { name: 'Status' });
    expect(within(source).getAllByRole('button').map((b) => b.textContent)).toEqual(['all', 'ui', 'api', 'clickup']);
    expect(within(status).getAllByRole('button').map((b) => b.textContent)).toEqual(
      ['all', 'success', 'failed', 'pending', 'running'],
    );
    for (const [bar, lit] of [[source, 'api'], [status, 'all']] as const) {
      expect(bar.firstElementChild).toHaveClass('inline-flex', 'border', 'bg-[var(--color-paper-2)]');
      expect(within(bar).getByRole('button', { name: lit })).toHaveAttribute('aria-pressed', 'true');
      expect(within(bar).getByRole('button', { name: lit })).toHaveClass('bg-[var(--color-copper)]', 'text-[var(--color-on-copper)]');
    }
    expect(within(source).getByRole('button', { name: 'all' })).toHaveAttribute('aria-pressed', 'false');
  });

  it('writes the pick to the URL, and "all" drops the param', () => {
    renderAt('/estimations?source=api');
    fireEvent.click(within(screen.getByRole('group', { name: 'Status' })).getByRole('button', { name: 'failed' }));
    expect(where()).toBe('?source=api&status=failed');
    fireEvent.click(within(screen.getByRole('group', { name: 'Source' })).getByRole('button', { name: 'all' }));
    expect(where()).toBe('?status=failed');
  });
});
