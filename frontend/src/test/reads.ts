import type { QueryClient, QueryKey } from '@tanstack/react-query';

import { isOwed } from '@/lib/revalidate';

/* What a write re-read, as a test sees it (lib/revalidate): a query under the
 * key that fetched since `watchReads` (or the last `clear`), or one still owed —
 * an inactive read is paid at its next mount, so it never fetches in a test that
 * does not mount it. Prefix match, as every re-read is. */
export function watchReads(qc: QueryClient) {
  const fetched = new Set<string>();
  const stop = qc.getQueryCache().subscribe((event) => {
    if (event.type === 'updated' && event.action.type === 'fetch') fetched.add(event.query.queryHash);
  });
  const reread = (key: QueryKey): boolean =>
    qc
      .getQueryCache()
      .findAll({ queryKey: key })
      .some((q) => fetched.has(q.queryHash) || isOwed(qc, q.queryKey));
  return { reread, clear: () => fetched.clear(), stop };
}
