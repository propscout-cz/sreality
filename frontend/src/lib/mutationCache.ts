/* App-wide mutation surfacing, both ways.
 *
 * A FAILED write that does NOT define its own onError gets its error toasted
 * here, so no write ever fails silently (e.g. a refused merge returning HTTP
 * 409). Mutations with their own onError own their messaging and are left
 * untouched — no double-surfacing.
 *
 * A COMMITTED write (its mutationFn resolved; the API commits before it answers)
 * is told to whoever listens on this cache (`onCommittedWrite`): the curation sync
 * owner's nudge to the other tabs (lib/curationSyncOwner). The cache's onSuccess
 * runs before the hook's own, so a write whose onSuccess or onSettled throws,
 * which query-core then settles as an error, is still told.
 *
 * A factory rather than a main.tsx local so a test can build the client the
 * app runs with and see the one toast a failed write produces. */

import { MutationCache } from '@tanstack/react-query';

import { ApiError } from '@/lib/api';
import { pushToast } from '@/lib/toast';

const committed = new WeakMap<MutationCache, Set<() => void>>();

/** Call `listener` after every write `cache` commits; the return value stops it. */
export function onCommittedWrite(cache: MutationCache, listener: () => void): () => void {
  let listeners = committed.get(cache);
  if (!listeners) committed.set(cache, (listeners = new Set()));
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function createMutationCache(): MutationCache {
  const cache: MutationCache = new MutationCache({
    onSuccess: () => {
      for (const listener of committed.get(cache) ?? []) {
        // A listener that throws must not turn a committed write into a failed one.
        try {
          listener();
        } catch (err) {
          console.error('[mutation cache] a committed-write listener threw', err);
        }
      }
    },
    onError: (error, _variables, _context, mutation) => {
      if (mutation.options.onError) return;
      const message =
        error instanceof ApiError || error instanceof Error
          ? error.message
          : 'Something went wrong';
      pushToast('err', message);
    },
  });
  return cache;
}
