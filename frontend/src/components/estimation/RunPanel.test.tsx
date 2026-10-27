/* The "Adjust & re-run" panel's pick-ones are the ONE joined bar (operator
 * ruling 2026-10-27, system.md § Option groups); the Disposition grid, more
 * than six options, keeps its chips and its second-click clear. */
import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';

import { RunBody } from './RunPanel';
import type { EstimationRun } from '@/lib/types';

const RUN = {
  id: 91,
  created_at: '2026-10-01T10:00:00Z',
  source: 'ui',
  mode: 'agent',
  status: 'failed',
  input_url: null,
  input_sreality_id: null,
  input_listing_id: null,
  input_spec: { lat: 50.08, lng: 14.42, area_m2: 54, disposition: '2+kk', floor: 3 },
  estimate_kind: 'rent',
  input_purchase_price_czk: null,
  trace: null,
  warnings: null,
  error_message: 'boom',
  parent_run_id: null,
  rerun_reason: null,
} as unknown as EstimationRun;

function renderPanel() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <RunBody run={RUN} subject={null} onRerun={vi.fn()} rerunPending={false} rerunError={null} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  fireEvent.click(screen.getByRole('button', { name: 'Adjust inputs' }));
}

describe('<RunBody> adjust panel', () => {
  it('draws each pick-one as one joined bar across the panel, the chosen segment filled copper', () => {
    renderPanel();
    for (const [name, lit, idle] of [
      ['Estimate kind', 'Rent (monthly)', 'Sale price'],
      ['Model provider', 'Claude', 'Gemini'],
      ['Comparable lifecycle', 'Active', 'Both'],
    ]) {
      const bar = screen.getByRole('group', { name });
      expect(bar).toHaveClass('grid', 'w-full', 'border', 'bg-[var(--color-paper-2)]');
      expect(within(bar).getByRole('button', { name: lit })).toHaveAttribute('aria-pressed', 'true');
      expect(within(bar).getByRole('button', { name: lit })).toHaveClass('bg-[var(--color-copper)]', 'text-[var(--color-on-copper)]');
      expect(within(bar).getByRole('button', { name: idle })).toHaveAttribute('aria-pressed', 'false');
      for (const b of within(bar).getAllByRole('button')) expect(b).not.toHaveClass('border');
    }
  });

  it('sets the value clicked: a sale estimate drops the rent-only rows', () => {
    renderPanel();
    fireEvent.click(screen.getByRole('button', { name: 'Delisted' }));
    expect(screen.getByRole('button', { name: 'Delisted' })).toHaveAttribute('aria-pressed', 'true');
    fireEvent.click(screen.getByRole('button', { name: 'Sale price' }));
    expect(screen.getByRole('button', { name: 'Sale price' })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.queryByRole('group', { name: 'Model provider' })).not.toBeInTheDocument();
  });

  it('keeps the Disposition grid as chips that clear on a second click', () => {
    renderPanel();
    const chip = screen.getByRole('button', { name: '2+kk' });
    expect(chip).toHaveClass('border', 'bg-[var(--color-copper)]');
    expect(chip.closest('[role="group"]')).toBeNull();
    fireEvent.click(chip);
    expect(chip).toHaveAttribute('aria-pressed', 'false');
  });
});
