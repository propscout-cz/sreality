/* The estimations chapter shows the PROPERTY's MF result, by shape, and nothing
 * else: no run's frozen reference rent stands in for a missing one, and there
 * is no English placeholder of its own. */
import { describe, expect, it, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import type { ReferenceRent } from '@/lib/mfReference';
import type { EstimationRun } from '@/lib/types';

vi.mock('@/lib/api', async (orig) => ({
  ...(await orig<typeof import('@/lib/api')>()),
  listEstimations: vi.fn(),
}));
vi.mock('@/components/estimation/RunPanel', () => ({
  RunBody: () => <div data-testid="run-body" />,
  ConfidencePill: () => null,
  RunStatusChip: () => null,
}));
const openEstimation = vi.hoisted(() => vi.fn());
vi.mock('@/components/NewEstimationModal', () => ({
  useNewEstimationModal: () => ({ open: openEstimation }),
}));

import { listEstimations } from '@/lib/api';
import type { PropertyPublic } from '@/lib/queries';
import EstimationsBlock from './EstimationsBlock';

const fetchRuns = vi.mocked(listEstimations);

const MF_LABEL = 'Odhad nájmu · cenová mapa MF';

const FROZEN_REF: ReferenceRent = {
  territory: { ruian_code: 698903, level: 'ku', name: 'Moravské Budějovice', kraj: null },
  vk: 3,
  is_novostavba: false,
  source_revision: 1,
  base_per_m2: 184,
  adjustments: [],
  total_per_m2: 184,
  area_m2: 75,
  monthly_rent_czk: 13_800,
};

const run = {
  id: 77,
  status: 'success',
  mode: 'comparables',
  estimate_kind: 'rent',
  estimated_monthly_rent_czk: 21_000,
  estimated_sale_price_czk: null,
  gross_yield_pct: null,
  confidence: 'medium',
  created_at: '2026-09-01T10:00:00Z',
  reference_rent: FROZEN_REF,
} as unknown as EstimationRun;

const property = (mf: Partial<PropertyPublic>): PropertyPublic =>
  ({
    id: 11,
    property_id: 5,
    category_main: 'byt',
    category_type: 'prodej',
    mf_gross_yield_pct: null,
    mf_reference_rent: null,
    ...mf,
  }) as unknown as PropertyPublic;

function renderBlock(listing: PropertyPublic, prefill?: { listingId: number }, readOnly = false) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <EstimationsBlock listing={listing} listingIds={[listing.id]} prefill={prefill} readOnly={readOnly} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('EstimationsBlock MF card', () => {
  beforeEach(() => fetchRuns.mockReset());

  it('never borrows a run frozen reference rent for a property without MF', async () => {
    fetchRuns.mockResolvedValue({ data: [run], total: 1, limit: 50, offset: 0, next_cursor: null });
    const { container } = renderBlock(property({}));
    await waitFor(() => expect(screen.getByTestId('run-body')).toBeInTheDocument());
    expect(container).not.toHaveTextContent(MF_LABEL);
    expect(container).not.toHaveTextContent('13 800');
    expect(container).not.toHaveTextContent('No MF reference');
  });

  /* C10 (section cards, operator ruling 2026-10-28): a section with nothing keeps its card,
     its title and count 0, with one muted line; the way to start one is the title bar's
     "Nový odhad" (as the picture draws Moje fotky), never a second copy in the body — no dashed tile. */
  it('keeps its card with no MF result and no runs: count 0, one line, the action in the title bar', async () => {
    fetchRuns.mockResolvedValue({ data: [], total: 0, limit: 50, offset: 0, next_cursor: null });
    const { container } = renderBlock(property({}), { listingId: 11 });
    const card = await screen.findByRole('region', { name: 'Estimates' });
    expect(card).toHaveAttribute('id', 'estimations');
    expect(await within(card).findByText('No estimation runs for this property yet.')).toBeInTheDocument();
    expect(within(card).getByText('0')).toBeInTheDocument();
    expect(within(card).getByRole('button', { name: 'Nový odhad' })).toBeInTheDocument();
    expect(within(card).queryByRole('button', { name: /Run an estimation/ })).toBeNull();
    expect(container.querySelector('.border-dashed')).toBeNull();
  });

  it('shows the property value with the property yield', async () => {
    fetchRuns.mockResolvedValue({ data: [], total: 0, limit: 50, offset: 0, next_cursor: null });
    const { container } = renderBlock(
      property({ mf_reference_rent: FROZEN_REF, mf_gross_yield_pct: 4.6 }),
    );
    await waitFor(() => expect(container).toHaveTextContent('13 800 Kč/měs'));
    expect(container).toHaveTextContent('hrubý výnos 4,6 %');
  });

  it('shows the property reason as its note, in place of any placeholder', async () => {
    const note = '(a reason note, from SQL)';
    fetchRuns.mockResolvedValue({ data: [], total: 0, limit: 50, offset: 0, next_cursor: null });
    const { container } = renderBlock(
      property({
        mf_reference_rent: { status: 'location_unknown', note } as unknown as ReferenceRent,
      }),
    );
    await waitFor(() => expect(container).toHaveTextContent(note));
    expect(container).toHaveTextContent(MF_LABEL);
  });
});

describe('EstimationsBlock first run', () => {
  beforeEach(() => {
    fetchRuns.mockReset();
    openEstimation.mockClear();
  });

  it('starts the first run on the ad the page names, by its listing id', async () => {
    fetchRuns.mockResolvedValue({ data: [], total: 0, limit: 50, offset: 0, next_cursor: null });
    renderBlock(property({ mf_reference_rent: FROZEN_REF }), { listingId: 11 });
    fireEvent.click(await screen.findByRole('button', { name: 'Nový odhad' }));
    expect(openEstimation).toHaveBeenCalledWith({ listingId: 11 });
  });

  it('offers no first run without an ad to estimate', async () => {
    fetchRuns.mockResolvedValue({ data: [], total: 0, limit: 50, offset: 0, next_cursor: null });
    renderBlock(property({ mf_reference_rent: FROZEN_REF }));
    await screen.findByText('No estimation runs for this property yet.');
    expect(screen.queryByRole('button', { name: 'Nový odhad' })).toBeNull();
  });
});

/* The binding picture (docs/design/propscout-section-cards.png) draws "Nový odhad" in
   the card's title bar (C6): a small secondary button opening the SAME flow as the
   page header's "New estimation" — the one modal, with the page's prefill. */
describe('EstimationsBlock title-bar Nový odhad', () => {
  beforeEach(() => {
    fetchRuns.mockReset();
    openEstimation.mockClear();
  });

  it('sits in the title bar beside the count and opens the new-estimation flow', async () => {
    fetchRuns.mockResolvedValue({ data: [run], total: 1, limit: 50, offset: 0, next_cursor: null });
    renderBlock(property({}), { listingId: 11 });
    const card = await screen.findByRole('region', { name: 'Estimates' });
    await within(card).findByTestId('run-body');
    const bar = within(card).getByRole('heading', { level: 2, name: 'Estimates' }).parentElement!;
    const button = within(bar).getByRole('button', { name: 'Nový odhad' });
    expect(button.className).not.toMatch(/bg-\[var\(--color-copper\)\]/);
    fireEvent.click(button);
    expect(openEstimation).toHaveBeenCalledWith({ listingId: 11 });
  });

  it('is not offered read-only (the phone) or without an ad to estimate', async () => {
    fetchRuns.mockResolvedValue({ data: [run], total: 1, limit: 50, offset: 0, next_cursor: null });
    const { unmount } = renderBlock(property({}), { listingId: 11 }, true);
    await screen.findByTestId('run-body');
    expect(screen.queryByRole('button', { name: 'Nový odhad' })).toBeNull();
    unmount();
    renderBlock(property({}));
    await screen.findByTestId('run-body');
    expect(screen.queryByRole('button', { name: 'Nový odhad' })).toBeNull();
  });
});
