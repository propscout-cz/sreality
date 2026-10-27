/* The on-card estimate state, as Browse keeps it: one latest-estimate read per
 * new sreality id, Browse's rent-estimate submit, the API's refusal kept on the
 * card, and a new cohort starting from nothing. */
import { act, renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import * as api from '@/lib/api';
import type { CardRow } from '@/lib/queries';
import type { EstimationRun, ListingEstimate } from '@/lib/types';
import { cardRow } from '@/test/cardRow';

import { useCardEstimates } from './useCardEstimates';

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  createEstimation: vi.fn(),
  latestEstimationsByListing: vi.fn(),
}));

const ESTIMATE: ListingEstimate = {
  sreality_id: 900,
  run_id: 77,
  status: 'success',
  estimate_kind: 'rent',
  gross_yield_pct: 4.2,
  estimated_monthly_rent_czk: null,
  created_at: null,
};

const RUN = {
  id: 5,
  status: 'pending',
  estimate_kind: 'rent',
  gross_yield_pct: null,
  estimated_monthly_rent_czk: null,
  created_at: '2026-10-27T08:00:00Z',
} as unknown as EstimationRun;

function wrapper({ children }: { children: ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

type Args = { rows: CardRow[]; enabled: boolean; resetKey: string };
const renderEstimates = (initial: Args) =>
  renderHook((props: Args) => useCardEstimates(props), { initialProps: initial, wrapper });

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.latestEstimationsByListing).mockResolvedValue({});
});

describe('useCardEstimates', () => {
  it('reads the latest estimate once per new sreality id, skipping null ids', async () => {
    vi.mocked(api.latestEstimationsByListing).mockResolvedValueOnce({ 900: ESTIMATE });
    const first = [cardRow(1, { sreality_id: 900 }), cardRow(2, { sreality_id: null })];
    const { result, rerender } = renderEstimates({ rows: first, enabled: true, resetKey: 'a' });

    await waitFor(() => expect(result.current.estimates[900]).toEqual(ESTIMATE));
    expect(api.latestEstimationsByListing).toHaveBeenCalledTimes(1);
    expect(api.latestEstimationsByListing).toHaveBeenCalledWith([900]);

    rerender({ rows: [...first, cardRow(3, { sreality_id: 901 })], enabled: true, resetKey: 'a' });
    await waitFor(() => expect(api.latestEstimationsByListing).toHaveBeenCalledTimes(2));
    expect(api.latestEstimationsByListing).toHaveBeenLastCalledWith([901]);
  });

  it('reads nothing while disabled', () => {
    renderEstimates({ rows: [cardRow(1, { sreality_id: 900 })], enabled: false, resetKey: 'a' });
    expect(api.latestEstimationsByListing).not.toHaveBeenCalled();
  });

  it("a submit posts Browse's rent-estimate body and marks the id estimating until it settles", async () => {
    let resolve: (run: EstimationRun) => void = () => {};
    vi.mocked(api.createEstimation).mockImplementation(
      () => new Promise<EstimationRun>((r) => { resolve = r; }),
    );
    const { result } = renderEstimates({ rows: [cardRow(1, { sreality_id: 900 })], enabled: false, resetKey: 'a' });

    act(() => result.current.onEstimate(900));
    await waitFor(() => expect(result.current.estimatingIds.has(900)).toBe(true));
    expect(api.createEstimation).toHaveBeenCalledWith({
      sreality_id: 900,
      source: 'ui',
      mode: 'agent',
      provider: 'anthropic',
      estimate_kind: 'rent',
      lifecycle: 'active',
    });

    await act(async () => resolve(RUN));
    await waitFor(() => expect(result.current.estimatingIds.has(900)).toBe(false));
    expect(result.current.estimates[900]).toEqual({
      sreality_id: 900,
      run_id: 5,
      status: 'pending',
      estimate_kind: 'rent',
      gross_yield_pct: null,
      estimated_monthly_rent_czk: null,
      created_at: '2026-10-27T08:00:00Z',
    });
  });

  it('a refused submit keeps the API sentence until the next attempt', async () => {
    vi.mocked(api.createEstimation)
      .mockRejectedValueOnce(new api.ApiError('Inzerát nelze umístit.', 422, null))
      .mockImplementationOnce(() => new Promise<EstimationRun>(() => {}));
    const { result } = renderEstimates({ rows: [cardRow(1, { sreality_id: 900 })], enabled: false, resetKey: 'a' });

    act(() => result.current.onEstimate(900));
    await waitFor(() => expect(result.current.estimateRefusals[900]).toBe('Inzerát nelze umístit.'));
    expect(result.current.estimatingIds.has(900)).toBe(false);

    act(() => result.current.onEstimate(900));
    await waitFor(() => expect(result.current.estimateRefusals[900]).toBeUndefined());
    expect(result.current.estimatingIds.has(900)).toBe(true);
  });

  it('a new resetKey drops what was read and reads the new cohort', async () => {
    vi.mocked(api.latestEstimationsByListing)
      .mockResolvedValueOnce({ 900: ESTIMATE })
      .mockImplementationOnce(() => new Promise(() => {}));
    const { result, rerender } = renderEstimates({
      rows: [cardRow(1, { sreality_id: 900 })], enabled: true, resetKey: 'a',
    });
    await waitFor(() => expect(result.current.estimates[900]).toEqual(ESTIMATE));

    rerender({ rows: [cardRow(1, { sreality_id: 900 }), cardRow(2, { sreality_id: 901 })], enabled: true, resetKey: 'b' });
    expect(result.current.estimates).toEqual({});
    // The reset forgets what was asked, so the whole new cohort is read again.
    await waitFor(() => expect(api.latestEstimationsByListing).toHaveBeenLastCalledWith([900, 901]));
  });

  it("a new cohort reads every card when its rows land before the previous rows' read answered", async () => {
    let answerPlaceholderRead: (map: Record<number, ListingEstimate>) => void = () => {};
    vi.mocked(api.latestEstimationsByListing)
      .mockResolvedValueOnce({ 900: ESTIMATE })
      .mockImplementationOnce(() => new Promise((r) => { answerPlaceholderRead = r; }))
      .mockResolvedValueOnce({ 900: ESTIMATE });
    const before = [cardRow(1, { sreality_id: 900 })];
    const { result, rerender } = renderEstimates({ rows: before, enabled: true, resetKey: 'a' });
    await waitFor(() => expect(result.current.estimates[900]).toEqual(ESTIMATE));

    // A re-sort: the list keeps the previous rows (the same reference) while the new order loads.
    rerender({ rows: before, enabled: true, resetKey: 'b' });
    expect(result.current.estimates).toEqual({});
    expect(api.latestEstimationsByListing).toHaveBeenCalledTimes(2);

    // The new rows land first: that read is dropped, and its ids are asked again.
    rerender({ rows: [cardRow(2, { sreality_id: 901 }), cardRow(1, { sreality_id: 900 })], enabled: true, resetKey: 'b' });
    expect(api.latestEstimationsByListing).toHaveBeenCalledTimes(3);
    expect(api.latestEstimationsByListing).toHaveBeenLastCalledWith([901, 900]);
    await waitFor(() => expect(result.current.estimates[900]).toEqual(ESTIMATE));

    await act(async () => answerPlaceholderRead({ 900: { ...ESTIMATE, run_id: 1 } }));
    expect(result.current.estimates[900]).toEqual(ESTIMATE);
  });
});
