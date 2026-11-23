/* Datasets' map metric is a pick-one, so it is the ONE joined bar (operator
 * ruling 2026-10-27, system.md § Option groups): the page's own MetricToggle
 * copy is gone, and the pick now has a pressed state a screen reader hears. */
import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import type { PriceStatDataset } from '@/lib/priceStats';

/* Hoisted: the mock factories below run before the module body. */
const { DATASET, pending } = vi.hoisted(() => ({
  DATASET: {
    id: 3, slug: 'byty', name: 'Byty', description: null, category_main_cb: 1, building_condition: null,
    building_type: null, ownership: null, usable_area_from: null, usable_area_to: null, distance: 0,
  } satisfies PriceStatDataset,
  pending: () => new Promise<never>(() => {}),
}));

vi.mock('@/lib/priceStats', async (orig) => ({
  ...(await orig<typeof import('@/lib/priceStats')>()),
  fetchDatasets: vi.fn(async () => [DATASET]),
  fetchGrowth: vi.fn(pending),
  fetchGrowthShapes: vi.fn(pending),
  fetchLatestRun: vi.fn(pending),
  fetchNoData: vi.fn(pending),
  fetchObecTree: vi.fn(pending),
  fetchSeries: vi.fn(pending),
}));
vi.mock('@/lib/queries', async (orig) => ({
  ...(await orig<typeof import('@/lib/queries')>()),
  fetchCuratedCities: vi.fn(pending),
  fetchCityIndexDefinitions: vi.fn(pending),
  fetchCityIndexValues: vi.fn(pending),
}));
vi.mock('@/components/DatasetMap', async () => ({
  default: () => null,
  METRICS: (await import('@/lib/growthChoropleth')).GROWTH_METRICS,
}));

import Datasets from './Datasets';

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <Datasets />
    </QueryClientProvider>,
  );
}

describe('<Datasets> metric', () => {
  it('draws the metric as one joined bar, the chosen segment filled copper', async () => {
    renderPage();
    const bar = await screen.findByRole('group', { name: 'Metric' });
    expect(bar.firstElementChild).toHaveClass('inline-flex', 'border', 'bg-[var(--color-paper-2)]');
    const segments = within(bar).getAllByRole('button');
    expect(segments.map((b) => b.textContent)).toEqual(['Rent growth p.a.', 'Sale-price growth p.a.', 'Yield change p.a.']);
    expect(segments[0]).toHaveAttribute('aria-pressed', 'true');
    expect(segments[0]).toHaveClass('bg-[var(--color-copper)]', 'text-[var(--color-on-copper)]');
    for (const b of segments) expect(b).not.toHaveClass('border-l');

    fireEvent.click(segments[2]);
    expect(segments[2]).toHaveAttribute('aria-pressed', 'true');
    expect(segments[0]).toHaveAttribute('aria-pressed', 'false');
  });
});
