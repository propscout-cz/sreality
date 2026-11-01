/* lib/useInfiniteList: every page carries the time it was read (the stale-side
 * judge, lib/curationSettle), and the hook's own `refetch` is a read the user
 * asked for (lib/revalidate ASKED: a hand on the list never parks it). */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { renderHook, waitFor } from '@testing-library/react';
import { createElement, type ReactNode } from 'react';
import { describe, expect, it, vi } from 'vitest';

import { ASKED } from './revalidate';
import { useInfiniteList, type InfiniteListPage } from './useInfiniteList';

const KEY = ['list'];

function list(fn: (cursor: unknown) => Promise<InfiniteListPage<number>>) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = ({ children }: { children: ReactNode }) =>
    createElement(QueryClientProvider, { client: qc }, children);
  const hook = renderHook(
    () => useInfiniteList<number>({ queryKey: KEY, queryFn: (cursor) => fn(cursor), pageSize: 2, getRowId: (r) => r }),
    { wrapper },
  );
  return { qc, ...hook };
}

describe('useInfiniteList', () => {
  it('stamps each page with the time it was read', async () => {
    const before = Date.now();
    const { qc, result } = list(async (cursor) => (cursor == null ? { rows: [1, 2], nextCursor: 1 } : { rows: [3] }));
    await waitFor(() => expect(result.current.rows).toEqual([1, 2]));
    result.current.fetchNextPage();
    await waitFor(() => expect(result.current.rows).toEqual([1, 2, 3]));
    const pages = qc.getQueryData<{ pages: InfiniteListPage<number>[] }>(KEY)!.pages;
    expect(pages).toHaveLength(2);
    for (const p of pages) expect(p.readAt).toBeGreaterThanOrEqual(before);
    expect(pages[1].readAt).toBeGreaterThanOrEqual(pages[0].readAt!);
  });

  it('refetch is a read the user asked for: it carries ASKED while it runs', async () => {
    let release!: () => void;
    const fn = vi.fn(async () => ({ rows: [1] }));
    const { qc, result } = list(fn);
    await waitFor(() => expect(result.current.rows).toEqual([1]));
    fn.mockImplementationOnce(() => new Promise((resolve) => {
      release = () => resolve({ rows: [9] });
    }));
    result.current.refetch();
    await waitFor(() => expect(qc.getQueryState(KEY)?.fetchStatus).toBe('fetching'));
    expect(qc.getQueryState(KEY)?.fetchMeta).toEqual(ASKED);
    release();
    await waitFor(() => expect(result.current.rows).toEqual([9]));
  });
});
