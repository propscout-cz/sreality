/* THE read scheduler: every curation re-read, local or remote, and every
 * optimistic write's re-read is OWED here and PAID here
 * (docs/design/curation-sync.md § 3.5, Decisions 9 and 14). One per QueryClient.
 *
 * OWE. `revalidate(items)` records, per cached query an item matches, an owed
 * read stamped with a tick from this scheduler's own counter. It is paid only by
 * a full (non-page) fetch that STARTED after that tick and landed. A fetch that
 * started earlier may have read the server before the change: it pays nothing.
 * `isInvalidated` is never the carrier, so a manual `setQueryData`, a rollback
 * or a landed `fetchNextPage` cannot erase what is owed.
 *
 * PAY. An active, unheld query owed 'now' is fetched at once (`query.fetch` —
 * the call `refetchQueries` makes, disabled and static queries skipped alike);
 * an in-flight fetch started after the owe is joined, never restarted (the
 * starvation guard); an initial load from before it cannot be restarted
 * (query-core dedupes a query with no data onto its load), so it is awaited and
 * paid anew when it lands. Anything else — inactive, disabled — is paid once an
 * observer makes it active, a microtask later, joining the mount's own fetch.
 * A scroll's page cancels a payment in flight (fetchNextPage's cancelRefetch
 * default): the debt is paid again once that page settles.
 * A 'mount' debt is paid only by a real mount (the query's first observer, or a
 * first observer's enable), never while it is on screen: a second reader joining
 * a mounted query pays nothing; a mount that came while the key was held is
 * paid by the release. A failed payment stays owed; `repayOwed`
 * re-tries it at most MAX_ATTEMPTS times, then it falls to 'mount', so an error
 * never loops.
 *
 * HOLD. `holdReads` registers synchronously inside the click: it cancels every
 * matching in-flight fetch with revert (Query.cancel reverts at once,
 * query-core retryer.js) — a full one is owed; a page is only dropped, because
 * it was built on the pages from before the patches (query-core
 * infiniteQueryBehavior takes them when it starts) and the list's next scroll
 * asks for it again — then paints the patches through a per-query LEDGER
 * {base, patches[]}. While a key is held, any later non-page fetch on it — a
 * mount, a reconnect, a stray invalidate, a remote target — is cancelled and
 * owed. That cancel waits one microtask because query-core dispatches its
 * 'fetch' action before it creates the retryer it would cancel. A page fetch
 * that STARTS while held (`fetchMeta.fetchMore`, useInfiniteList's
 * fetchNextPage) passes: it builds on the patched pages, so scrolling during a
 * pending dismissal never turns into a list refetch. `rollback()` drops this
 * hold's patches and repaints base + the rest in order, so overlapping writes
 * roll back in any order with nothing erased and nothing resurrected. A write
 * onto a held query that is not the ledger's own (such a page, a site's
 * setQueryData over the patched value) becomes the new base, the patches
 * already in it, never re-applied on top (a rollback then leaves it to the
 * re-read that follows). Owed reads on a held query park until its last
 * overlapping hold releases; the ledger then drops.
 *
 * EPOCH. `resetReads` (the identity change, lib/auth.tsx) drops every owed
 * read, hold and ledger; a hold of the previous account then paints, parks and
 * pays nothing, and a settle that releases it owes nothing.
 *
 * Built on @tanstack/query-core 5.100.9 internals (Query.isStatic, the 'fetch'
 * dispatch before the retryer, cancel with revert, fetchMeta.fetchMore, a
 * manual setData): re-run revalidate.test.ts on any query-core bump. */

import { useEffect } from 'react';
import {
  CancelledError,
  hashKey,
  matchQuery,
  onlineManager,
  useQueryClient,
  type Query,
  type QueryClient,
  type QueryFilters,
  type QueryKey,
} from '@tanstack/react-query';

export type ReadMode = 'now' | 'mount';

/** One optimistic cache write: which queries (prefix unless `exact`) and the value each holds while held. */
export interface CachePatch {
  filters: QueryFilters;
  update: (old: unknown) => unknown;
}

export function cachePatch<T>(
  filters: QueryFilters,
  update: (old: T | undefined) => T | undefined,
): CachePatch {
  return { filters, update: update as (old: unknown) => unknown };
}

/** One read to owe: every cached query under `queryKey` (exactly it with `exact`). Default mode 'now'. */
export interface Revalidation {
  queryKey: QueryKey;
  exact?: boolean;
  mode?: ReadMode;
}

/** A registered hold. Both calls are idempotent, and no-ops once the epoch moved on. */
export interface Hold {
  release(): void;
  rollback(): void;
}

/** lib/api REQUEST_DEADLINE_MS (130 s) + 10 s, counted in ONLINE time only
 * (pinned equal by revalidate.test.ts; not imported, so a test mocking lib/api
 * whole still loads every writer). A paused write cannot trip it. */
export const HOLD_TTL_MS = 140_000;

/* A payment that failed this many times falls to 'mount'. */
const MAX_ATTEMPTS = 3;

interface Debt {
  since: number;
  mode: ReadMode;
  attempts: number;
  /* Due but not payable yet — found inactive or disabled, or its mount came
   * while held: the next payment that can run pays it, 'mount' or not. */
  waiting: boolean;
}

interface Start {
  tick: number;
  page: boolean;
}

interface LedgerPatch {
  hold: number;
  update: (old: unknown) => unknown;
}

interface Ledger {
  base: unknown;
  patches: LedgerPatch[];
}

interface HoldRecord {
  id: number;
  epoch: number;
  filters: readonly QueryFilters[];
  done: boolean;
  /* Online ms already spent; `onlineSince` starts the current online stretch, null while offline. */
  spent: number;
  onlineSince: number | null;
  timer: ReturnType<typeof setTimeout> | null;
}

interface Scheduler {
  qc: QueryClient;
  tick: number;
  epoch: number;
  debts: Map<string, Debt>;
  starts: Map<string, Start>;
  ledgers: Map<string, Ledger>;
  holds: Map<number, HoldRecord>;
  holdSeq: number;
  /* The query hash the ledger is writing right now: its own write is not external. */
  writing: string | null;
  unsubscribeOnline: (() => void) | null;
}

const schedulers = new WeakMap<QueryClient, Scheduler>();
const records = new WeakMap<Hold, { s: Scheduler; rec: HoldRecord }>();
const noop = () => {};

function scheduler(qc: QueryClient): Scheduler {
  let s = schedulers.get(qc);
  if (!s) {
    s = {
      qc,
      tick: 0,
      epoch: 0,
      debts: new Map(),
      starts: new Map(),
      ledgers: new Map(),
      holds: new Map(),
      holdSeq: 0,
      writing: null,
      unsubscribeOnline: null,
    };
    schedulers.set(qc, s);
    listen(s);
  }
  return s;
}

const isHeld = (s: Scheduler, q: Query): boolean => {
  for (const rec of s.holds.values()) if (rec.filters.some((f) => matchQuery(f, q))) return true;
  return false;
};

function owe(s: Scheduler, q: Query, mode: ReadMode): void {
  const since = ++s.tick;
  const debt = s.debts.get(q.queryHash);
  if (debt) {
    debt.since = since;
    debt.attempts = 0;
    if (mode === 'now') debt.mode = 'now';
  } else {
    s.debts.set(q.queryHash, { since, mode, attempts: 0, waiting: false });
  }
}

/* Pay what `q` owes if it can be paid now; the promise settles when the read it
 * started or joined does. `mount`: the query just got its first observer, so a
 * 'mount' debt is due too. */
function pay(s: Scheduler, q: Query, mount = false): Promise<unknown> | undefined {
  const debt = s.debts.get(q.queryHash);
  if (!debt) return undefined;
  // A mount makes a 'mount' debt due; one that came while held is paid by the release.
  if (mount) debt.waiting = true;
  if (isHeld(s, q)) return undefined;
  if (debt.mode === 'mount' && !debt.waiting) return undefined;
  if (!q.isActive() || q.isStatic()) {
    // Due, but no enabled observer yet: the observer that enables it pays.
    debt.waiting = true;
    return undefined;
  }
  debt.waiting = false;
  const inFlight = q.state.fetchStatus !== 'idle';
  const start = s.starts.get(q.queryHash);
  if (inFlight && start && !start.page && start.tick > debt.since) return q.promise?.then(noop, noop);
  // An initial load cannot be restarted: its landing pays anew (the 'success' arm of `listen`).
  if (inFlight && q.state.data === undefined) return q.promise?.then(noop, noop);
  // The fetch (a restart included: its meta differs) dispatches 'fetch', which `listen` stamps.
  return q.fetch(undefined, { cancelRefetch: true }).catch(noop);
}

/* The ledger writes through Query.setData (what setQueryData calls), flagged so
 * `listen` does not take its own write for an external one. A patch keeps the
 * time its base was read: lib/curationSettle judges which side is stale by it. */
function write(s: Scheduler, q: Query, data: unknown): void {
  if (data === undefined) return;
  s.writing = q.queryHash;
  try {
    q.setData(data, { manual: true, updatedAt: q.state.dataUpdatedAt });
  } finally {
    s.writing = null;
  }
}

const replay = (l: Ledger): unknown => l.patches.reduce((data, p) => p.update(data), l.base);

function listen(s: Scheduler): void {
  s.qc.getQueryCache().subscribe((event) => {
    const q = event.query;
    const hash = q.queryHash;
    switch (event.type) {
      case 'removed':
        s.debts.delete(hash);
        s.starts.delete(hash);
        s.ledgers.delete(hash);
        return;
      case 'observerAdded':
        if (s.debts.has(hash)) {
          const first = q.getObserversCount() === 1;
          queueMicrotask(() => pay(s, q, first));
        }
        return;
      case 'observerOptionsUpdated':
        // Fires on every render of every observer: one map lookup, nothing more.
        if (s.debts.get(hash)?.waiting) queueMicrotask(() => pay(s, q));
        return;
      case 'updated':
        break;
      default:
        return;
    }
    const action = event.action;
    if (action.type === 'fetch') {
      const tick = ++s.tick;
      const page = action.meta?.fetchMore != null;
      s.starts.set(hash, { tick, page });
      if (!page && s.holds.size > 0 && isHeld(s, q)) queueMicrotask(() => cancelHeld(s, q, tick));
      return;
    }
    if (action.type === 'success') {
      if (s.writing === hash) return;
      const ledger = s.ledgers.get(hash);
      if (ledger) {
        // Not the ledger's own write: the new base. It was built on the patched value
        // (a page from before the hold was cancelled when the hold registered).
        ledger.base = q.state.data;
        ledger.patches = [];
      }
      if (action.manual) return;
      const debt = s.debts.get(hash);
      const start = s.starts.get(hash);
      if (!debt) return;
      if (start?.page) return repayAfterPage(s, q, debt);
      if (start && start.tick > debt.since) s.debts.delete(hash);
      else queueMicrotask(() => pay(s, q));
      return;
    }
    if (action.type === 'error') {
      const debt = s.debts.get(hash);
      const start = s.starts.get(hash);
      // A cancel is no failed attempt: what it cancelled stays owed as it was.
      if (!debt || action.error instanceof CancelledError) return;
      if (start?.page) return repayAfterPage(s, q, debt);
      if (start && start.tick > debt.since) {
        debt.attempts += 1;
        if (debt.attempts >= MAX_ATTEMPTS) debt.mode = 'mount';
      } else {
        queueMicrotask(() => pay(s, q));
      }
    }
  });
}

/* A page pays nothing, but a scroll's page may have cancelled a payment
 * (useInfiniteList's fetchNextPage takes query-core's cancelRefetch default):
 * a debt still due at once is paid again once the page settles. A failed one
 * waits for `repayOwed`, a 'mount' one for its mount. */
function repayAfterPage(s: Scheduler, q: Query, debt: Debt): void {
  if (debt.mode === 'now' && debt.attempts === 0) queueMicrotask(() => pay(s, q));
}

function cancelHeld(s: Scheduler, q: Query, tick: number): void {
  if (s.starts.get(q.queryHash)?.tick !== tick) return;
  if (q.state.fetchStatus === 'idle' || !isHeld(s, q)) return;
  owe(s, q, 'now');
  void q.cancel({ revert: true });
}

/* ── Online-time TTL ───────────────────────────────────────────────────── */

function arm(s: Scheduler, rec: HoldRecord): void {
  if (rec.timer != null) clearTimeout(rec.timer);
  rec.timer = null;
  if (rec.onlineSince == null) return;
  rec.timer = setTimeout(
    () => {
      if (rec.done || rec.epoch !== s.epoch) return;
      console.warn(`[revalidate] a read hold outlived ${HOLD_TTL_MS / 1000} s online; released`, rec.filters);
      for (const q of releaseRecord(s, rec)) pay(s, q);
    },
    Math.max(0, HOLD_TTL_MS - rec.spent),
  );
}

function watchOnline(s: Scheduler): void {
  if (s.unsubscribeOnline) return;
  s.unsubscribeOnline = onlineManager.subscribe((online) => {
    const now = Date.now();
    for (const rec of s.holds.values()) {
      if (online && rec.onlineSince == null) rec.onlineSince = now;
      if (!online && rec.onlineSince != null) {
        rec.spent += now - rec.onlineSince;
        rec.onlineSince = null;
      }
      arm(s, rec);
    }
  });
}

/* Release without paying; answers the queries whose last hold this was, owed or not. */
function releaseRecord(s: Scheduler, rec: HoldRecord): Query[] {
  if (rec.done || rec.epoch !== s.epoch) return [];
  rec.done = true;
  if (rec.timer != null) clearTimeout(rec.timer);
  s.holds.delete(rec.id);
  if (s.holds.size === 0 && s.unsubscribeOnline) {
    s.unsubscribeOnline();
    s.unsubscribeOnline = null;
  }
  const live = new Set(s.holds.keys());
  for (const [hash, ledger] of s.ledgers) {
    if (!ledger.patches.some((p) => live.has(p.hold))) s.ledgers.delete(hash);
  }
  const freed = new Map<string, Query>();
  const cache = s.qc.getQueryCache();
  for (const f of rec.filters) {
    for (const q of cache.findAll(f)) if (!isHeld(s, q)) freed.set(q.queryHash, q);
  }
  return [...freed.values()];
}

function rollbackRecord(s: Scheduler, rec: HoldRecord): void {
  if (rec.done || rec.epoch !== s.epoch) return;
  const cache = s.qc.getQueryCache();
  for (const [hash, ledger] of s.ledgers) {
    const kept = ledger.patches.filter((p) => p.hold !== rec.id);
    if (kept.length === ledger.patches.length) continue;
    ledger.patches = kept;
    const q = cache.get(hash);
    if (q) write(s, q, replay(ledger));
  }
}

/* ── The API ───────────────────────────────────────────────────────────── */

const toItem = (item: Revalidation | QueryKey): Revalidation =>
  Array.isArray(item) ? { queryKey: item as QueryKey } : (item as Revalidation);

/** Owe `items` and pay what can be paid now. With `release`, that hold is let go
 * in the same batch: a query both parked by it and named here is fetched once.
 * Settles when every read it started or joined has (parked and mount reads are
 * not waited for). */
export function revalidate(
  qc: QueryClient,
  items: readonly (Revalidation | QueryKey)[],
  opts: { release?: Hold } = {},
): Promise<void> {
  const s = scheduler(qc);
  const held = opts.release ? records.get(opts.release) : undefined;
  // A write of the previous identity settling after resetReads owes the new one nothing.
  if (held && held.s === s && held.rec.epoch !== s.epoch) return Promise.resolve();
  const cache = qc.getQueryCache();
  const touched = new Map<string, Query>();
  for (const item of items.map(toItem)) {
    for (const q of cache.findAll({ queryKey: item.queryKey, exact: item.exact })) {
      owe(s, q, item.mode ?? 'now');
      touched.set(q.queryHash, q);
    }
  }
  if (held && held.s === s) for (const q of releaseRecord(s, held.rec)) touched.set(q.queryHash, q);
  const waits: Promise<unknown>[] = [];
  for (const q of touched.values()) {
    const paid = pay(s, q);
    if (paid) waits.push(paid);
  }
  return Promise.all(waits).then(noop);
}

/** Hold every query `filters` match — now and any created while held — and paint
 * `patches` through the ledger. Synchronous: no await between this and the paint. */
export function holdReads(
  qc: QueryClient,
  filters: readonly QueryFilters[],
  patches: readonly CachePatch[] = [],
): Hold {
  const s = scheduler(qc);
  const cache = qc.getQueryCache();
  const online = onlineManager.isOnline();
  const rec: HoldRecord = {
    id: ++s.holdSeq,
    epoch: s.epoch,
    filters: [...filters],
    done: false,
    spent: 0,
    onlineSince: online ? Date.now() : null,
    timer: null,
  };
  s.holds.set(rec.id, rec);
  for (const f of rec.filters) {
    for (const q of cache.findAll(f)) {
      if (q.state.fetchStatus === 'idle') continue;
      if (q.state.fetchMeta?.fetchMore == null) owe(s, q, 'now');
      void q.cancel({ revert: true });
    }
  }
  for (const p of patches) {
    for (const q of cache.findAll(p.filters)) {
      let ledger = s.ledgers.get(q.queryHash);
      if (!ledger) {
        ledger = { base: q.state.data, patches: [] };
        s.ledgers.set(q.queryHash, ledger);
      }
      ledger.patches.push({ hold: rec.id, update: p.update });
      write(s, q, p.update(q.state.data));
    }
  }
  watchOnline(s);
  arm(s, rec);
  const hold: Hold = {
    release: () => {
      for (const q of releaseRecord(s, rec)) pay(s, q);
    },
    rollback: () => rollbackRecord(s, rec),
  };
  records.set(hold, { s, rec });
  return hold;
}

/** Hold `keys` (by prefix) while `active` — a gesture in progress. */
export function useReadHold(keys: readonly QueryKey[], active: boolean): void {
  const qc = useQueryClient();
  const hash = hashKey(keys as unknown as QueryKey);
  useEffect(() => {
    if (!active) return undefined;
    const hold = holdReads(
      qc,
      keys.map((queryKey) => ({ queryKey })),
    );
    return () => hold.release();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- `hash` is the identity of `keys`
  }, [qc, hash, active]);
}

/** Does the query under exactly `key` still owe a read? */
export function isOwed(qc: QueryClient, key: QueryKey): boolean {
  return schedulers.get(qc)?.debts.has(hashKey(key)) ?? false;
}

/** Re-try owed 'now' reads whose payment failed (< MAX_ATTEMPTS so far): the
 * curation clock's answer calls it, so a failed read is retried on the next one. */
export function repayOwed(qc: QueryClient): void {
  const s = schedulers.get(qc);
  if (!s) return;
  const cache = qc.getQueryCache();
  for (const [hash, debt] of s.debts) {
    if (debt.mode !== 'now' || debt.attempts === 0) continue;
    const q = cache.get(hash);
    if (q && q.state.fetchStatus === 'idle') pay(s, q);
  }
}

/** A new identity: drop every owed read, hold and ledger; an older hold is inert from here on. */
export function resetReads(qc: QueryClient): void {
  const s = schedulers.get(qc);
  if (!s) return;
  s.epoch += 1;
  for (const rec of s.holds.values()) if (rec.timer != null) clearTimeout(rec.timer);
  s.holds.clear();
  s.debts.clear();
  s.ledgers.clear();
  s.unsubscribeOnline?.();
  s.unsubscribeOnline = null;
}
