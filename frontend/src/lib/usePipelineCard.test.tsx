/* usePipelineCard settles Browse through lib/curationSettle (ruling 9): a card
 * funnelled while its cohort excludes the pipeline leaves the list at the
 * click, before the write lands, and comes back if the write fails. */

import { describe, expect, it, vi } from 'vitest';
import { act, renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider, type InfiniteData } from '@tanstack/react-query';
import type { ReactNode } from 'react';

import * as api from '@/lib/api';
import { browseKeys } from '@/lib/browseKeys';
import { DEFAULT_FILTERS } from '@/lib/filters';
import { DEFAULT_SORT, dismissalKeys, pipelineKeys, type PipelineMembers } from '@/lib/queries';
import type { PipelineStage } from '@/lib/types';
import { usePipelineCard } from './usePipelineCard';
import { watchReads } from '@/test/reads';

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>();
  return { ...actual, addPipelineCard: vi.fn() };
});

type Page = { rows: { property_id: number }[] };
const ENTRY: PipelineStage = {
  id: 1, key: 'review', label: '1. For Review', position: 1, color: 'copper',
  is_terminal: false, is_entry: true, code: '1',
};
const OUT = { ...DEFAULT_FILTERS, pipeline: { set: 'out' as const, in: [], out: [] } };
const CARDS = browseKeys.cards(OUT, DEFAULT_SORT);

function setup() {
  const qc = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
  qc.setQueryData(pipelineKeys.stages, [ENTRY]);
  qc.setQueryData<PipelineMembers>(pipelineKeys.members, new Map());
  qc.setQueryData<InfiniteData<Page>>(CARDS, {
    pages: [{ rows: [{ property_id: 42 }, { property_id: 43 }] }],
    pageParams: [null],
  });
  qc.setQueryData(browseKeys.count(OUT), 2);
  qc.setQueryData(browseKeys.map(OUT, 200), { points: [] });
  qc.setQueryData(browseKeys.stats(OUT, { rows: [], cols: [], measure: 'ppm2' }), { cells: [] });
  const { reread } = watchReads(qc);
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  );
  const { result } = renderHook(() => usePipelineCard(), { wrapper });
  const rows = () => qc.getQueryData<InfiniteData<Page>>(CARDS)!.pages[0].rows.map((r) => r.property_id);
  return { qc, result, rows, reread };
}

describe('usePipelineCard settles Browse', () => {
  it('drops the funnelled card from a "Mimo pipeline" list at the click, then re-reads map and stats', async () => {
    let land!: () => void;
    vi.mocked(api.addPipelineCard).mockReturnValue(
      new Promise((resolve) => (land = () => resolve({} as never))),
    );
    const { qc, result, rows, reread } = setup();

    act(() => result.current.add.mutate(42));
    await waitFor(() => expect(rows()).toEqual([43]));
    expect(qc.getQueryData(browseKeys.count(OUT))).toBe(1);
    expect(reread(['map'])).toBe(false);

    await act(async () => land());
    await waitFor(() => expect(reread(['map', OUT])).toBe(true));
    expect(reread(['stats', OUT])).toBe(true);
    expect(reread(['cards', OUT])).toBe(false);
    expect(rows()).toEqual([43]);
  });

  it('puts the card back and re-reads every Browse read when the write fails', async () => {
    vi.mocked(api.addPipelineCard).mockRejectedValue(new Error('HTTP 500'));
    const { qc, result, rows, reread } = setup();

    act(() => result.current.add.mutate(42));
    await waitFor(() => expect(reread(['cards'])).toBe(true));
    expect(rows()).toEqual([42, 43]);
    expect(qc.getQueryData(browseKeys.count(OUT))).toBe(2);
  });

  it('an add that lifts a dismissal re-reads every Browse read: the row may come back anywhere', async () => {
    vi.mocked(api.addPipelineCard).mockResolvedValue({} as never);
    const { qc, result, reread } = setup();
    qc.setQueryData(dismissalKeys.state(99), true);

    act(() => result.current.add.mutate(99));
    await waitFor(() => expect(reread(['cards'])).toBe(true));
    expect(reread(['map'])).toBe(true);
    expect(reread(dismissalKeys.state(99))).toBe(true);
  });
});
