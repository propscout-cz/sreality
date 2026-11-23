import type { UseQueryResult } from '@tanstack/react-query';

type Read = Pick<UseQueryResult<unknown, Error>, 'isError' | 'isFetching' | 'refetch'>;

export type ReadGroupFailure = { retrying: boolean; retry: () => void };

/* Reads that feed one layer fail as one: the layer is broken while any of them has failed.
 * Null while none has. `retry` re-reads only the failed ones (a loaded one is not fetched
 * again), and `retrying` holds while one of those is back in flight. A long-cached read
 * (staleTime Infinity) that failed stays failed until this retry or a reload: the app's one
 * automatic retry (main.tsx) is already spent. */
export function readGroupFailure(reads: ReadonlyArray<Read>): ReadGroupFailure | null {
  const failed = reads.filter((r) => r.isError);
  if (failed.length === 0) return null;
  return {
    retrying: failed.some((r) => r.isFetching),
    retry: () => {
      for (const r of failed) void r.refetch();
    },
  };
}
