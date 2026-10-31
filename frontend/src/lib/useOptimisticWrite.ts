/* The one optimistic write (rule #22's write policy) — the deal pipeline,
 * dismissals, the border-case flag, the autodedup verdict overlay, the admin
 * toggles, the training-set marks, the preset reorder and the exam-review
 * edits all go through this hook.
 *
 *   hold  →  patch  →  write  →  (failed? roll back)  →  revalidate + release
 *
 * HOLD (lib/revalidate `holdReads`): inside the click, with no await, the
 * queries a patch changes are held — their in-flight reads cancelled and owed,
 * any read that starts on them while the write is in flight cancelled and owed
 * too — and the patch is painted through the scheduler's ledger. The filters
 * held and the filters written are the same list, so the two cannot drift and
 * no site can forget the cancel. Overlapping writes compose: a rollback drops
 * its own patch and repaints the rest, and a settle's re-read of a key another
 * write still holds waits for that write (TkDodo's "invalidate only when the
 * last one settles", enforced by the cache).
 *
 * REVALIDATE + RELEASE in one batch: the settle owes its keys and lets its hold
 * go in the same call, so a key both parked by the hold and named by the settle
 * is read once.
 *
 * ROLLBACK FROM `onSettled`, NEVER `onError`. React Query's global
 * MutationCache.onError (lib/mutationCache) is the app's only "the write
 * failed" feedback, and it stays silent for any mutation that declares its own
 * `onError`. So the rollback lives here, runs BEFORE the re-read, and a site's
 * `onError` exists only to show its own message (inline text, a 409 prompt) —
 * it is handed no context, so it cannot roll anything back.
 *
 * PENDING PER KEY. `pendingFor(key)` answers from one index per MutationCache
 * (below), so every hook instance sharing a `mutationKey` sees every in-flight
 * write under it — the funnel and the stage menu agree — and a guard inside a
 * click handler is exact before the re-render lands. `pendingKey` must be a
 * pure function of the variables, and two surfaces sharing a `mutationKey` must
 * use disjoint key formats (the verdict overlays: "lo:hi" pairs, "50-…"
 * candidates, numeric cluster keys) or one would report the other's write as
 * its own.
 *
 * A WRITE RE-RENDERS ONLY WHAT ASKED ABOUT ITS KEY. Browse mounts five of these
 * hooks per card, and every render of each one tells the MutationCache its
 * options changed — so anything that woke on every cache event (useMutationState
 * did) cost rows × rows per render. An instance subscribes only if it has a
 * `pendingKey`, and re-renders only when a key it has asked `pendingFor` about
 * changes; a hook without one costs nothing beyond its own mutation.
 *
 * Mutation keys are `['write', <slice>, <action>]`, defined beside the write.
 * They live in the MutationCache, which no `invalidateQueries` reaches.
 */

import { useCallback, useState, useSyncExternalStore } from 'react';
import {
  hashKey,
  useMutation,
  useQueryClient,
  type Mutation,
  type MutationCache,
  type MutationKey,
  type QueryClient,
  type QueryFilters,
  type QueryKey,
  type UseMutationResult,
} from '@tanstack/react-query';

import { holdReads, revalidate, type CachePatch, type Hold, type Revalidation } from '@/lib/revalidate';

export type Rollback = () => void;

/* What onMutate hands onSettled: the hold of a cache patch, or the undo of a React-state paint. */
export type WriteContext = { hold: Hold; rollback?: undefined } | { rollback: Rollback; hold?: undefined };

type ReadList = readonly (Revalidation | QueryKey)[];

export type PendingKey = string | number;

export interface OptimisticWrite<V, R> {
  /** Names the write in the MutationCache; every hook instance with this key shares one pending set. */
  mutationKey: MutationKey;
  mutationFn: (vars: V) => Promise<R>;
  /** CachePatch[]: the hook holds those queries, then writes them.
   *  Rollback: a store the query cache can't see (React state) was already painted; this undoes it. */
  patch?: (vars: V, qc: QueryClient) => readonly CachePatch[] | Rollback;
  /** Reads owed after every settle (prefixes unless `exact`); `failed` lets a site widen them on failure. */
  revalidate?: ReadList | ((vars: V, failed: boolean, qc: QueryClient) => ReadList);
  /** Pure function of vars; what `pendingFor` is asked about. */
  pendingKey?: (vars: V) => PendingKey;
  onSuccess?: (data: R, vars: V) => void;
  /** ONLY for a site that shows its own message (inline text, a 409 prompt). It gets no context, so it
   *  cannot roll back; declaring it silences the global toast (lib/mutationCache). */
  onError?: (error: Error, vars: V) => void;
}

export type OptimisticWriteResult<V, R> = UseMutationResult<R, Error, V, WriteContext> & {
  pendingFor: (key: PendingKey) => boolean;
};

/* ── The pending index ────────────────────────────────────────────────────
 * One per MutationCache, one cache subscription however many hooks mount. A
 * keyed write carries its own `pendingKey` in `meta`, so the index can read the
 * key of a write whichever instance started it. Each cache event is O(1): the
 * render-driven `observerOptionsUpdated` events are dropped on the type check. */

const PENDING_META = 'optimisticPendingKey';

type KeyListener = (key: PendingKey) => void;

interface PendingIndex {
  isPending: (keyHash: string, key: PendingKey) => boolean;
  subscribe: (keyHash: string, listener: KeyListener) => () => void;
}

const indexes = new WeakMap<MutationCache, PendingIndex>();

function pendingIndex(cache: MutationCache): PendingIndex {
  let index = indexes.get(cache);
  if (!index) {
    index = createPendingIndex(cache);
    indexes.set(cache, index);
  }
  return index;
}

function createPendingIndex(cache: MutationCache): PendingIndex {
  const counts = new Map<string, Map<PendingKey, number>>();
  const inFlight = new Map<number, { keyHash: string; key: PendingKey }>();
  const listeners = new Map<string, Set<KeyListener>>();

  // Status is 'pending' from the click (synchronously) until after onSettled —
  // so a rolled-back write stays busy until its rollback has run.
  const sync = (m: Mutation, removed: boolean) => {
    const readKey = m.options.meta?.[PENDING_META] as ((vars: unknown) => PendingKey) | undefined;
    if (!readKey) return;
    const pending = !removed && m.state.status === 'pending';
    const entry = inFlight.get(m.mutationId);
    if (pending === (entry !== undefined)) return;
    let changed: { keyHash: string; key: PendingKey };
    if (pending) {
      changed = { keyHash: hashKey(m.options.mutationKey ?? []), key: readKey(m.state.variables) };
      inFlight.set(m.mutationId, changed);
      const perKey = counts.get(changed.keyHash) ?? new Map<PendingKey, number>();
      counts.set(changed.keyHash, perKey);
      perKey.set(changed.key, (perKey.get(changed.key) ?? 0) + 1);
    } else {
      changed = entry!;
      inFlight.delete(m.mutationId);
      const perKey = counts.get(changed.keyHash)!;
      const left = perKey.get(changed.key)! - 1;
      if (left > 0) perKey.set(changed.key, left);
      else perKey.delete(changed.key);
    }
    listeners.get(changed.keyHash)?.forEach((listener) => listener(changed.key));
  };

  for (const m of cache.getAll()) sync(m, false);
  cache.subscribe((event) => {
    if (event.type === 'added' || event.type === 'updated') sync(event.mutation, false);
    else if (event.type === 'removed') sync(event.mutation, true);
  });

  return {
    isPending: (keyHash, key) => (counts.get(keyHash)?.get(key) ?? 0) > 0,
    subscribe: (keyHash, listener) => {
      const set = listeners.get(keyHash) ?? new Set<KeyListener>();
      listeners.set(keyHash, set);
      set.add(listener);
      return () => {
        set.delete(listener);
      };
    },
  };
}

const unsubscribed = () => () => {};

export function useOptimisticWrite<V = void, R = unknown>({
  mutationKey,
  mutationFn,
  patch,
  revalidate: reads = [],
  pendingKey,
  onSuccess,
  onError,
}: OptimisticWrite<V, R>): OptimisticWriteResult<V, R> {
  const qc = useQueryClient();
  const mutation = useMutation<R, Error, V, WriteContext>({
    mutationKey,
    meta: pendingKey && { [PENDING_META]: pendingKey },
    // query-core passes a 2nd context argument; strip it so the write functions
    // are called with the variables alone.
    mutationFn: (vars) => mutationFn(vars),
    // Synchronous, both forms: the paint lands inside the click.
    onMutate: (vars) => {
      const painted = patch ? patch(vars, qc) : [];
      if (typeof painted === 'function') return { rollback: painted };
      const filters: QueryFilters[] = painted.map((p) => p.filters);
      return { hold: holdReads(qc, filters, painted) };
    },
    onSuccess: onSuccess && ((data, vars) => onSuccess(data, vars)),
    // Undefined unless the site owns the message — see the header.
    onError: onError && ((error, vars) => onError(error, vars)),
    onSettled: (_data, error, vars, ctx) => {
      if (error) (ctx?.hold?.rollback ?? ctx?.rollback)?.(); // before the re-read
      const keys = typeof reads === 'function' ? reads(vars, error != null, qc) : reads;
      // Not returned: the write is settled now, not when its re-read lands.
      void revalidate(qc, keys, { release: ctx?.hold });
    },
  });

  const keyed = pendingKey !== undefined;
  const index = pendingIndex(qc.getMutationCache());
  const keyHash = hashKey(mutationKey);
  /* The keys this instance has asked about — in render or in a handler. Only
   * those can change what it rendered, so only those wake it. */
  const [asked] = useState(() => new Set<PendingKey>());
  const subscribe = useCallback(
    (onChange: () => void) =>
      keyed
        ? index.subscribe(keyHash, (key) => {
            if (asked.has(key)) onChange();
          })
        : unsubscribed(),
    [index, keyHash, keyed, asked],
  );
  // Which asked keys are in flight: changes exactly when an answer would.
  const snapshot = useCallback(() => {
    let busy = '';
    for (const key of asked) if (index.isPending(keyHash, key)) busy += `${key}\u0000`;
    return busy;
  }, [index, keyHash, asked]);
  const busy = useSyncExternalStore(subscribe, snapshot);
  const pendingFor = useCallback(
    (key: PendingKey) => {
      if (!keyed) return false;
      asked.add(key);
      return index.isPending(keyHash, key);
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps -- busy is the change signal
    [index, keyHash, keyed, asked, busy],
  );
  return { ...mutation, pendingFor };
}
