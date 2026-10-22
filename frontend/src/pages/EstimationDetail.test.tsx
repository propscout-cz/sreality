/* /estimation/:id is the fallback surface. A run of an ad we hold — on ANY
 * portal — redirects to that ad's page by its natural key (the run projection's
 * subject_source + subject_source_id_native); it used to redirect only when the
 * run carried a sreality id, so a bound idnes / bezrealitky run stayed here as
 * "not in our database". Only a run that never had an ad renders here, from its
 * stored spec. */
import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';

import type { EstimationRun } from '@/lib/types';

vi.mock('@/lib/queries', async (orig) => ({
  ...(await orig<typeof import('@/lib/queries')>()),
  fetchEstimation: vi.fn(),
}));
vi.mock('@/components/estimation/RunPanel', () => ({
  RunBody: () => <div data-testid="run-body" />,
  ConfidencePill: () => null,
  SourceBadge: () => null,
}));
vi.mock('@/components/listing-detail/ListingOverview', () => ({
  ListingOverview: ({ listing }: { listing: { disposition: string | null; area_m2: number | null } }) => (
    <p data-testid="subject">{`${listing.disposition} · ${listing.area_m2}`}</p>
  ),
}));

import { fetchEstimation } from '@/lib/queries';
import EstimationDetail from './EstimationDetail';

const fetchRun = vi.mocked(fetchEstimation);

const run = (over: Partial<EstimationRun>): EstimationRun =>
  ({
    id: 41,
    status: 'success',
    source: 'ui',
    created_at: '2026-10-01T10:00:00Z',
    confidence: 'medium',
    input_sreality_id: null,
    input_listing_id: null,
    subject_source: null,
    subject_source_id_native: null,
    input_spec: null,
    ...over,
  }) as EstimationRun;

function Where() {
  const l = useLocation();
  return <p data-testid="where">{l.pathname + l.search + l.hash}</p>;
}

function renderAt(path: string) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="estimation/:id" element={<EstimationDetail />} />
          <Route path="*" element={null} />
        </Routes>
        <Where />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const where = () => screen.getByTestId('where').textContent;

beforeEach(() => {
  fetchRun.mockReset();
});

describe('<EstimationDetail> redirect', () => {
  it('sends a run of a non-sreality ad to that ad’s page', async () => {
    fetchRun.mockResolvedValue(run({
      input_listing_id: 105053, subject_source: 'idnes', subject_source_id_native: '6a147cfde222cf687509e018',
    }));
    renderAt('/estimation/41');
    await waitFor(() =>
      expect(where()).toBe('/listing/idnes/6a147cfde222cf687509e018?run=41#estimations'));
  });

  it('keeps a #feedback deep link through the redirect', async () => {
    fetchRun.mockResolvedValue(run({
      input_sreality_id: 999, input_listing_id: 105054, subject_source: 'sreality', subject_source_id_native: '999',
    }));
    renderAt('/estimation/41#feedback');
    await waitFor(() => expect(where()).toBe('/listing/sreality/999?run=41#feedback'));
  });

  it('renders a run that never had an ad here, from its stored spec', async () => {
    fetchRun.mockResolvedValue(run({
      input_url: 'https://example.cz/inzerat/1',
      input_spec: { lat: 50.1, lng: 14.4, area_m2: 61, disposition: '2+kk', floor: 2 },
    }));
    renderAt('/estimation/41');
    expect(await screen.findByText('not in our database')).toBeInTheDocument();
    expect(screen.getByTestId('subject')).toHaveTextContent('2+kk · 61');
    expect(screen.getByTestId('run-body')).toBeInTheDocument();
    expect(where()).toBe('/estimation/41');
  });
});
