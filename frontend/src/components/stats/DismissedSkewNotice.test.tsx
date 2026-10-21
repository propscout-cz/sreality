import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import { DEFAULT_FILTERS, type ListingFilters } from '@/lib/filters';
import * as queries from '@/lib/queries';
import DismissedSkewNotice from './DismissedSkewNotice';

vi.mock('@/lib/queries', async (importActual) => ({
  ...(await importActual<typeof import('@/lib/queries')>()),
  fetchDismissedCount: vi.fn(),
  fetchBrowseCount: vi.fn(),
}));

/* The cohort's totals with and without the caller's dismissed properties. */
const totals = (shown: number, hidden: number, anyDismissed = 3) => {
  vi.mocked(queries.fetchDismissedCount).mockResolvedValue(anyDismissed);
  vi.mocked(queries.fetchBrowseCount).mockImplementation(async (f: ListingFilters) => (f.showDismissed ? shown : hidden));
};

const renderNotice = (filters: ListingFilters, onInclude = vi.fn()) => {
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <DismissedSkewNotice filters={filters} onInclude={onInclude} />
    </QueryClientProvider>,
  );
  return onInclude;
};

describe('<DismissedSkewNotice>', () => {
  it('warns that the sample leaves out the dismissed properties, and offers to count them', async () => {
    totals(120, 108);
    const onInclude = renderNotice(DEFAULT_FILTERS);
    await screen.findByText(/12 nemovitostí z tohoto filtru, které jste skryli/);
    expect(screen.getByRole('status')).toHaveTextContent(
      'Statistiky nezapočítávají skryté nemovitosti. Ve filtru je zvoleno „Hide dismissed“, takže 12 nemovitostí z tohoto filtru, které jste skryli, ve vzorku chybí a výsledky mohou být zkreslené.',
    );
    await userEvent.setup().click(screen.getByRole('button', { name: 'Započítat skryté' }));
    expect(onInclude).toHaveBeenCalledOnce();
  });

  it('says nothing while dismissed properties are shown', async () => {
    totals(120, 108);
    renderNotice({ ...DEFAULT_FILTERS, showDismissed: true });
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.queryByRole('status')).toBeNull();
  });

  it('says nothing when this filter hides none of them', async () => {
    totals(120, 120);
    renderNotice(DEFAULT_FILTERS);
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.queryByRole('status')).toBeNull();
  });

  it('says nothing when nothing was ever dismissed', async () => {
    totals(120, 120, 0);
    renderNotice(DEFAULT_FILTERS);
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.queryByRole('status')).toBeNull();
  });
});
