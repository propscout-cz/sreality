/* Prodeje v domě: a sale shows its unit, price and measured per-m²; a whole-building sale says so; no sale, no card. */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

vi.mock('@/lib/api', async (orig) => ({
  ...(await orig<typeof import('@/lib/api')>()),
  fetchPropertyBuildingSales: vi.fn(),
}));

import { fetchPropertyBuildingSales, type BuildingSale } from '@/lib/api';
import BuildingSalesCard from './BuildingSalesCard';

const load = vi.mocked(fetchPropertyBuildingSales);
const SALE: BuildingSale = {
  source: 'reas', source_record_id: '1_unit_22906291-105-37', sold_at: '2025-03-14', price_czk: 6450000,
  asking_last_czk: null, category_main: 'byt', disposition: '3+kk', area_m2: 62, unit_label: '105/37',
  source_url: 'https://example.test/x', price_per_m2: 104032.26, price_per_m2_basis: 'sale_capital_czk_m2',
};

function mount() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}><BuildingSalesCard propertyId={7} /></QueryClientProvider>);
}

beforeEach(() => load.mockReset());

describe('<BuildingSalesCard>', () => {
  it('shows a sale with its unit, price, per-m² and source', async () => {
    load.mockResolvedValue({ sales: [SALE] });
    mount();
    expect(await screen.findByText('105/37')).toBeInTheDocument();
    expect(screen.getByText(/6\s450\s000/)).toBeInTheDocument();
    expect(screen.getByText(/104\s032/)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Reas' })).toHaveAttribute('href', SALE.source_url);
  });

  it('says a whole-building sale is one, and leaves a per-m² gap without an area', async () => {
    load.mockResolvedValue({ sales: [{ ...SALE, unit_label: null, area_m2: null, price_per_m2: null }] });
    mount();
    expect(await screen.findByText('celá budova')).toBeInTheDocument();
    expect(screen.getAllByText('—').length).toBeGreaterThan(0);
  });

  it('draws nothing when the building has no sale we hold', async () => {
    load.mockResolvedValue({ sales: [] });
    const { container } = mount();
    await waitFor(() => expect(load).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });
});
