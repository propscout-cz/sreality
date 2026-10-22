import { useQuery } from '@tanstack/react-query';

import { browseKeys } from '@/lib/browseKeys';
import type { ListingFilters } from '@/lib/filters';
import { dismissalKeys, fetchBrowseCount, fetchDismissedCount } from '@/lib/queries';

/* How many of the cohort's properties the caller has dismissed (migration 537):
 * the difference of the two cohort totals, shown and hidden, one of which is the
 * header's own cached count. Nothing extra is fetched until the caller has
 * dismissed something, and an approximate total yields null rather than a wrong
 * number. One copy for the sidebar's dismissed switch and the Stats warning. */
export function useHiddenDismissed(filters: ListingFilters): { hasAny: boolean; count: number | null } {
  const anyQ = useQuery({
    queryKey: dismissalKeys.count,
    queryFn: fetchDismissedCount,
    staleTime: 60_000,
  });
  const hasAny = (anyQ.data ?? 0) > 0;
  const totalWith = (showDismissed: boolean) => {
    const f = { ...filters, showDismissed };
    return {
      queryKey: browseKeys.count(f),
      queryFn: ({ signal }: { signal: AbortSignal }) => fetchBrowseCount(f, { signal }),
      enabled: hasAny,
      placeholderData: <T,>(prev: T) => prev,
      staleTime: 60_000,
    };
  };
  const hiddenQ = useQuery(totalWith(false));
  const shownQ = useQuery(totalWith(true));
  const count = hiddenQ.data != null && shownQ.data != null ? shownQ.data - hiddenQ.data : null;
  return { hasAny, count };
}
