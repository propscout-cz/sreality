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

/* Section cards (operator ruling 2026-10-28, system.md § Section cards): on the
 * run's own page each section is a card whose region is named by its title; the
 * property page's Odhady card embeds the same body, where the sections are L3
 * groups, never a card in a card. */
const SUCCESS = {
  ...RUN,
  id: 92,
  status: 'success',
  error_message: null,
  confidence: 'medium',
  estimated_monthly_rent_czk: 18_000,
  rent_p25_czk: null,
  rent_p75_czk: null,
  gross_yield_pct: null,
  scenario: null,
  reference_rent: { note: '(a reason note, from SQL)', monthly_rent_czk: null },
} as unknown as EstimationRun;

function renderBody(run: EstimationRun, extra: { embedded?: boolean; readOnly?: boolean } = {}) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <RunBody
          run={run}
          subject={null}
          onRerun={extra.readOnly ? undefined : vi.fn()}
          rerunPending={false}
          rerunError={null}
          embedded={extra.embedded}
        />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function expectCards(names: string[]) {
  const cards = screen.getAllByRole('region');
  expect(cards.map((c) => within(c).getAllByRole('heading', { level: 2 })[0].textContent)).toEqual(names);
  for (const c of cards) {
    expect(c.tagName).toBe('SECTION');
    expect(c).toHaveClass('border', 'bg-[var(--color-paper-2)]', 'rounded-[var(--radius-md)]');
    expect(c.parentElement).toHaveClass('space-y-4');
  }
}

describe('<RunBody> section cards', () => {
  it('stacks a finished run as cards: the estimate, its yield, the MF reference, detail and re-run', () => {
    renderBody(SUCCESS);
    expectCards(['Estimate run', 'Yield', 'Reference · Cenová mapa MF', 'Detail and re-run']);
    const yieldCard = screen.getByRole('region', { name: 'Yield' });
    expect(within(yieldCard).getByText('live calculation')).toBeInTheDocument();
    expect(within(yieldCard).getByText('Gross yield')).toBeInTheDocument();
    expect(within(screen.getByRole('region', { name: 'Detail and re-run' }))
      .getByRole('button', { name: 'Show estimation detail' })).toBeInTheDocument();
    /* The figure is the card's body, not a second heading. */
    expect(within(screen.getByRole('region', { name: 'Estimate run' })).getAllByRole('heading')).toHaveLength(1);
  });

  it('keeps the yield reset as a title-bar action once a value is edited', () => {
    renderBody(SUCCESS);
    fireEvent.change(screen.getByRole('textbox', { name: 'Monthly rent' }), { target: { value: '20000' } });
    const yieldCard = screen.getByRole('region', { name: 'Yield' });
    expect(within(yieldCard).getByText('edited · synced')).toBeInTheDocument();
    expect(within(yieldCard).getByRole('button', { name: 'Reset' })).toBeInTheDocument();
  });

  it('a failed run: its error, inputs, trace and re-run, each a card', () => {
    renderBody(RUN);
    expectCards(['Error', 'Inputs', 'Trace', 'Re-run']);
    expect(within(screen.getByRole('region', { name: 'Error' })).getByText('boom')).toBeInTheDocument();
  });

  it('a queued run: the queue state and its inputs', () => {
    renderBody({ ...RUN, status: 'pending', error_message: null } as unknown as EstimationRun);
    expectCards(['Queued', 'Inputs']);
  });

  it('read-only (no re-run) keeps the detail button in its own card', () => {
    renderBody(SUCCESS, { readOnly: true });
    expect(within(screen.getByRole('region', { name: 'Estimation detail' }))
      .getByRole('button', { name: 'Show estimation detail' })).toBeInTheDocument();
  });

  it('embedded in the property page card, its sections are L3 groups, never a box in a box', () => {
    const { container } = renderBody(SUCCESS, { embedded: true });
    expect(container.querySelector('section')).toBeNull();
    expect(screen.queryByRole('heading', { level: 2 })).toBeNull();
    expect(screen.getAllByRole('heading', { level: 3 }).map((h) => h.textContent))
      .toEqual(['Yield', 'Detail and re-run']);
  });
});
