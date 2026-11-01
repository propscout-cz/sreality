/* lib/revalidate — the read scheduler. An owed read is paid only by a full read
 * that started after it was owed; holds park reads and keep any read from
 * landing over a pending write; the ledger composes overlapping writes' patches. */

import { afterEach, describe, expect, it, vi } from 'vitest';
import { act, renderHook, waitFor } from '@testing-library/react';
import {
  InfiniteQueryObserver,
  onlineManager,
  QueryClient,
  QueryClientProvider,
  QueryObserver,
  useQuery,
  type InfiniteData,
  type QueryKey,
} from '@tanstack/react-query';
import { createElement, type ReactNode } from 'react';

import { REQUEST_DEADLINE_MS } from './api';
import { createMutationCache } from './mutationCache';
import { useOptimisticWrite } from './useOptimisticWrite';
import {
  cachePatch,
  HOLD_TTL_MS,
  holdReads,
  isOwed,
  parkReads,
  repayOwed,
  resetReads,
  revalidate,
  useReadHold,
} from './revalidate';

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

const client = () => new QueryClient({ defaultOptions: { queries: { retry: false } } });
const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

/* A mounted reader of `key`: `fn` is its queryFn. Returns the unmount. */
function mount(qc: QueryClient, key: QueryKey, fn: () => Promise<unknown>, staleTime: number = Infinity) {
  const observer = new QueryObserver(qc, { queryKey: key, queryFn: fn, staleTime });
  return observer.subscribe(() => {});
}

/* A counting reader answering `value()` at once. */
const counter = (value: () => unknown = () => 1) => vi.fn(async () => value());

afterEach(() => {
  onlineManager.setOnline(true);
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe('owe and pay', () => {
  it('fetches an active query at once, and an inactive one at its next mount', async () => {
    const qc = client();
    qc.setQueryData(['a'], 0);
    qc.setQueryData(['b'], 0);
    const a = counter();
    const b = counter();
    mount(qc, ['a'], a);

    void revalidate(qc, [['a'], ['b']]);
    expect(a).toHaveBeenCalledTimes(1); // synchronously, inside the call
    expect(b).not.toHaveBeenCalled();
    expect(isOwed(qc, ['b'])).toBe(true);
    await flush();
    expect(isOwed(qc, ['a'])).toBe(false);

    mount(qc, ['b'], b);
    await flush();
    expect(b).toHaveBeenCalledTimes(1);
    expect(isOwed(qc, ['b'])).toBe(false);
  });

  it('owes nothing for a key no query holds, and honours exact', async () => {
    const qc = client();
    qc.setQueryData(['p'], 0);
    qc.setQueryData(['p', 'x'], 0);
    void revalidate(qc, [['nothing'], { queryKey: ['p'], exact: true }]);
    expect(isOwed(qc, ['p'])).toBe(true);
    expect(isOwed(qc, ['p', 'x'])).toBe(false);
    expect(isOwed(qc, ['nothing'])).toBe(false);
  });

  it('awaits an initial load from before the change, then reads again', async () => {
    const qc = client();
    const first = deferred<number>();
    const fn = vi.fn().mockReturnValueOnce(first.promise).mockResolvedValue(2);
    mount(qc, ['a'], fn);
    expect(fn).toHaveBeenCalledTimes(1);

    void revalidate(qc, [['a']]);
    // query-core joins a load with no data whatever cancelRefetch says: nothing new yet.
    expect(fn).toHaveBeenCalledTimes(1);
    first.resolve(1);
    await flush();
    expect(fn).toHaveBeenCalledTimes(2);
    await flush();
    expect(qc.getQueryData(['a'])).toBe(2);
    expect(isOwed(qc, ['a'])).toBe(false);
  });

  /* lib/curationSyncOwner's start: that load began after the window began following. */
  it('with skipInitialLoads, owes nothing on an initial load in flight, and still owes a read with data', async () => {
    const qc = client();
    const first = deferred<number>();
    const fn = vi.fn().mockReturnValueOnce(first.promise).mockResolvedValue(2);
    mount(qc, ['a'], fn);
    qc.setQueryData(['b'], 0);

    void revalidate(qc, [['a'], ['b']], { skipInitialLoads: true });
    expect(isOwed(qc, ['a'])).toBe(false);
    expect(isOwed(qc, ['b'])).toBe(true);
    first.resolve(1);
    await flush();
    expect(fn).toHaveBeenCalledTimes(1);
    expect(qc.getQueryData(['a'])).toBe(1);
  });

  it('restarts a refetch that started before the change', async () => {
    const qc = client();
    qc.setQueryData(['a'], 0);
    const old = deferred<number>();
    const fn = vi.fn().mockReturnValueOnce(old.promise).mockResolvedValue(2);
    mount(qc, ['a'], fn);
    void qc.refetchQueries({ queryKey: ['a'] });
    expect(fn).toHaveBeenCalledTimes(1);

    void revalidate(qc, [['a']]);
    expect(fn).toHaveBeenCalledTimes(2);
    old.resolve(1);
    await flush();
    expect(qc.getQueryData(['a'])).toBe(2);
    expect(isOwed(qc, ['a'])).toBe(false);
  });

  it('keeps an inactive read that lands from before the change owed, and pays it on remount', async () => {
    const qc = client();
    qc.setQueryData(['a'], 0);
    const old = deferred<number>();
    const fn = vi.fn().mockReturnValueOnce(old.promise).mockResolvedValue(2);
    const unmount = mount(qc, ['a'], fn);
    void qc.refetchQueries({ queryKey: ['a'] });
    unmount(); // the signal was never read: the read runs on, inactive

    void revalidate(qc, [['a']]);
    old.resolve(1);
    await flush();
    expect(qc.getQueryData(['a'])).toBe(1);
    expect(isOwed(qc, ['a'])).toBe(true);

    mount(qc, ['a'], fn);
    await flush();
    expect(fn).toHaveBeenCalledTimes(2);
    expect(qc.getQueryData(['a'])).toBe(2);
    expect(isOwed(qc, ['a'])).toBe(false);
  });

  /* The starvation guard: the mount's own read started after the owe, so the
   * scheduler joins it instead of restarting it. */
  it('joins a read that started after the change instead of restarting it', async () => {
    const qc = client();
    qc.setQueryData(['a'], 0);
    void revalidate(qc, [['a']]);
    let aborted = false;
    const fn = vi.fn(async ({ signal }: { signal: AbortSignal }) => {
      signal.addEventListener('abort', () => (aborted = true));
      return 1;
    });
    mount(qc, ['a'], fn as never, 0); // stale: the mount reads by itself
    await flush();
    expect(fn).toHaveBeenCalledTimes(1);
    expect(aborted).toBe(false);
    expect(isOwed(qc, ['a'])).toBe(false);
  });

  it('re-tries a failed read at most three times, then waits for a mount', async () => {
    const qc = client();
    qc.setQueryData(['e'], 0);
    const fn = vi.fn(async () => {
      throw new Error('boom');
    });
    const unmount = mount(qc, ['e'], fn);

    void revalidate(qc, [['e']]);
    await flush();
    expect(fn).toHaveBeenCalledTimes(1);
    repayOwed(qc);
    await flush();
    repayOwed(qc);
    await flush();
    expect(fn).toHaveBeenCalledTimes(3);
    repayOwed(qc); // three failures: no fourth try now
    await flush();
    expect(fn).toHaveBeenCalledTimes(3);
    expect(isOwed(qc, ['e'])).toBe(true);

    unmount();
    fn.mockResolvedValue(1 as never);
    mount(qc, ['e'], fn);
    await flush();
    expect(fn).toHaveBeenCalledTimes(4);
    expect(isOwed(qc, ['e'])).toBe(false);
  });

  it('keeps a cancelled read owed, and does not count it as a failure', async () => {
    const qc = client();
    qc.setQueryData(['a'], 0);
    const pending = deferred<number>();
    const fn = vi.fn().mockReturnValueOnce(pending.promise).mockResolvedValue(2);
    const unmount = mount(qc, ['a'], fn);
    void revalidate(qc, [['a']]);
    await qc.cancelQueries({ queryKey: ['a'] });
    expect(isOwed(qc, ['a'])).toBe(true);

    unmount();
    mount(qc, ['a'], fn);
    await flush();
    expect(fn).toHaveBeenCalledTimes(2);
    expect(isOwed(qc, ['a'])).toBe(false);
  });

  it('pays a read owed on a disabled query once an observer enables it', async () => {
    const qc = client();
    qc.setQueryData(['a'], 0);
    const fn = counter(() => 4);
    const observer = new QueryObserver(qc, { queryKey: ['a'], queryFn: fn, staleTime: Infinity, enabled: false });
    const unmount = observer.subscribe(() => {});

    void revalidate(qc, [['a']]);
    await flush();
    expect(fn).not.toHaveBeenCalled();
    expect(isOwed(qc, ['a'])).toBe(true);

    observer.setOptions({ queryKey: ['a'], queryFn: fn, staleTime: Infinity, enabled: true });
    await flush();
    expect(fn).toHaveBeenCalledTimes(1);
    expect(qc.getQueryData(['a'])).toBe(4);
    expect(isOwed(qc, ['a'])).toBe(false);
    unmount();
  });

  /* useInfiniteList's fetchNextPage takes query-core's default, cancelRefetch:
   * a scroll during a payment cancels it and fetches its page instead. */
  it('pays again once a scroll that cancelled the payment lands its page', async () => {
    const qc = client();
    const payment = deferred<string>();
    let calls = 0;
    const fn = vi.fn(async ({ pageParam }: { pageParam: number }) => {
      calls += 1;
      return calls === 2 ? payment.promise : `p${pageParam}`;
    });
    const observer = new InfiniteQueryObserver(qc, {
      queryKey: ['list'],
      queryFn: fn as never,
      initialPageParam: 0,
      getNextPageParam: (_last: string, all: string[]) => all.length,
      staleTime: Infinity,
    });
    observer.subscribe(() => {});
    await flush();
    expect(fn).toHaveBeenCalledTimes(1);

    void revalidate(qc, [['list']]);
    expect(fn).toHaveBeenCalledTimes(2); // the payment, in flight
    await observer.fetchNextPage(); // the scroll: cancels it, reads page 1
    payment.resolve('stale');
    await flush();
    await flush();

    // The load, the cancelled payment, the page — then one full read, every page.
    expect(fn.mock.calls.map(([ctx]) => ctx.pageParam)).toEqual([0, 0, 1, 0, 1]);
    expect(isOwed(qc, ['list'])).toBe(false);
    expect(qc.getQueryData<InfiniteData<string>>(['list'])?.pages).toEqual(['p0', 'p1']);
  });

  it('pays again when that page fails instead', async () => {
    const qc = client();
    const payment = deferred<string>();
    let calls = 0;
    const fn = vi.fn(async ({ pageParam }: { pageParam: number }) => {
      calls += 1;
      if (calls === 2) return payment.promise;
      if (calls === 3) throw new Error('page');
      return `p${pageParam}`;
    });
    const observer = new InfiniteQueryObserver(qc, {
      queryKey: ['list'],
      queryFn: fn as never,
      initialPageParam: 0,
      getNextPageParam: (_last: string, all: string[]) => all.length,
      staleTime: Infinity,
    });
    observer.subscribe(() => {});
    await flush();

    void revalidate(qc, [['list']]);
    await observer.fetchNextPage();
    await flush();
    await flush();
    expect(fn).toHaveBeenCalledTimes(4); // the full read again, on the one page held
    expect(isOwed(qc, ['list'])).toBe(false);
  });

  it('drops the debt of a query that leaves the cache', () => {
    const qc = client();
    qc.setQueryData(['a'], 0);
    void revalidate(qc, [['a']]);
    qc.removeQueries({ queryKey: ['a'] });
    qc.setQueryData(['a'], 0);
    expect(isOwed(qc, ['a'])).toBe(false);
  });

  it('settles when the reads it started have', async () => {
    const qc = client();
    qc.setQueryData(['a'], 0);
    const read = deferred<number>();
    mount(qc, ['a'], () => read.promise);
    let done = false;
    void revalidate(qc, [['a']]).then(() => (done = true));
    await flush();
    expect(done).toBe(false);
    read.resolve(1);
    await flush();
    expect(done).toBe(true);
  });
});

describe("'mount' debts", () => {
  it('never fetch while mounted, survive a manual write, a rollback and a landed page, and pay on remount', async () => {
    const qc = client();
    const fn = vi.fn(async (page: number) => `p${page}`);
    const observer = new InfiniteQueryObserver(qc, {
      queryKey: ['list'],
      queryFn: ({ pageParam }) => fn(pageParam as number),
      initialPageParam: 0,
      getNextPageParam: (_last: string, all: string[]) => all.length,
      staleTime: Infinity,
    });
    let unmount = observer.subscribe(() => {});
    await flush();
    expect(fn).toHaveBeenCalledTimes(1);

    void revalidate(qc, [{ queryKey: ['list'], mode: 'mount' }]);
    await flush();
    expect(fn).toHaveBeenCalledTimes(1);

    qc.setQueryData<InfiniteData<string>>(['list'], (d) => d && { ...d, pages: ['edited'] });
    const hold = holdReads(qc, [{ queryKey: ['list'] }], [
      cachePatch<InfiniteData<string>>({ queryKey: ['list'] }, (d) => d && { ...d, pages: ['patched'] }),
    ]);
    hold.rollback();
    hold.release();
    await observer.fetchNextPage();
    expect(fn).toHaveBeenCalledTimes(2); // the page, and only the page
    expect(isOwed(qc, ['list'])).toBe(true);

    unmount();
    unmount = observer.subscribe(() => {});
    await flush();
    await flush();
    expect(isOwed(qc, ['list'])).toBe(false);
    expect(fn.mock.calls.length).toBeGreaterThan(2); // the full re-read, every page
    unmount();
  });
});

describe("'mount' debts and a second reader", () => {
  it('a reader joining a mounted query pays nothing; the next real mount does', async () => {
    const qc = client();
    qc.setQueryData(['count'], 0);
    const fn = counter(() => 7);
    const first = mount(qc, ['count'], fn);
    void revalidate(qc, [{ queryKey: ['count'], mode: 'mount' }]);

    const second = mount(qc, ['count'], fn); // e.g. the phone filter sheet over Browse
    await flush();
    expect(fn).not.toHaveBeenCalled();
    expect(isOwed(qc, ['count'])).toBe(true);

    first();
    second();
    mount(qc, ['count'], fn);
    await flush();
    expect(fn).toHaveBeenCalledTimes(1);
    expect(isOwed(qc, ['count'])).toBe(false);
  });

  it('a first reader mounted disabled pays when it is enabled', async () => {
    const qc = client();
    qc.setQueryData(['count'], 0);
    const fn = counter(() => 7);
    void revalidate(qc, [{ queryKey: ['count'], mode: 'mount' }]);
    const observer = new QueryObserver(qc, { queryKey: ['count'], queryFn: fn, staleTime: Infinity, enabled: false });
    observer.subscribe(() => {});
    await flush();
    expect(fn).not.toHaveBeenCalled();

    observer.setOptions({ queryKey: ['count'], queryFn: fn, staleTime: Infinity, enabled: true });
    await flush();
    expect(fn).toHaveBeenCalledTimes(1);
    expect(isOwed(qc, ['count'])).toBe(false);
  });

  it('a first reader mounted while the key is held pays at the release', async () => {
    const qc = client();
    qc.setQueryData(['count'], 0);
    const fn = counter(() => 7);
    const hold = holdReads(qc, [{ queryKey: ['count'] }]);
    void revalidate(qc, [{ queryKey: ['count'], mode: 'mount' }]);
    mount(qc, ['count'], fn); // fresh: the mount itself reads nothing
    await flush();
    expect(fn).not.toHaveBeenCalled();

    hold.release();
    await flush();
    expect(fn).toHaveBeenCalledTimes(1);
    expect(qc.getQueryData(['count'])).toBe(7);
    expect(isOwed(qc, ['count'])).toBe(false);
  });
});

describe("'mount' debts and a scroll", () => {
  /* Browse's cards, cached and owed 'mount', get their first reader; `answers` serves the reads in turn. */
  function remount(staleTime: number, answers: Promise<string>[]) {
    const qc = client();
    qc.setQueryData<InfiniteData<string>>(['list'], { pages: ['p0'], pageParams: [0] });
    void revalidate(qc, [{ queryKey: ['list'], mode: 'mount' }]);
    const fn = vi.fn(async ({ pageParam }: { pageParam: number }) => answers.shift() ?? `p${pageParam}`);
    const observer = new InfiniteQueryObserver(qc, {
      queryKey: ['list'],
      queryFn: fn as never,
      initialPageParam: 0,
      getNextPageParam: (_last: string, all: string[]) => all.length,
      staleTime,
    });
    observer.subscribe(() => {});
    const params = () => fn.mock.calls.map(([ctx]) => ctx.pageParam);
    return { qc, observer, params };
  }

  it.each([
    ['the payment its mount started', Infinity],
    ['the mount read it joined', 0],
  ])('a page that cancels %s is followed by one full read', async (_name, staleTime) => {
    const payment = deferred<string>();
    const { qc, observer, params } = remount(staleTime, [payment.promise]);
    await flush();
    expect(params()).toEqual([0]); // the mount's payment, in flight

    await observer.fetchNextPage(); // the scroll: cancels it, reads page 1
    payment.resolve('stale');
    await flush();
    await flush();

    expect(params()).toEqual([0, 1, 0, 1]);
    expect(isOwed(qc, ['list'])).toBe(false);
    expect(qc.getQueryData<InfiniteData<string>>(['list'])?.pages).toEqual(['p0', 'p1']);
  });

  it('a hold that drops that page leaves the read to its release', async () => {
    const payment = deferred<string>();
    const page = deferred<string>();
    const { qc, observer, params } = remount(Infinity, [payment.promise, page.promise]);
    await flush();
    void observer.fetchNextPage(); // cancels the payment
    await flush();
    const hold = holdReads(qc, [{ queryKey: ['list'] }]); // a click while the page is in flight: drops it
    payment.resolve('stale');
    page.resolve('dropped');
    await flush();
    expect(params()).toEqual([0, 1]);

    hold.release();
    await flush();
    await flush();
    expect(params()).toEqual([0, 1, 0]);
    expect(isOwed(qc, ['list'])).toBe(false);
  });
});

describe('holds', () => {
  it('park an owed read until the last overlapping hold releases, then pay it once', async () => {
    const qc = client();
    qc.setQueryData(['a'], 0);
    const fn = counter();
    mount(qc, ['a'], fn);
    const one = holdReads(qc, [{ queryKey: ['a'] }]);
    const two = holdReads(qc, [{ queryKey: ['a'] }]);

    void revalidate(qc, [['a']]);
    void revalidate(qc, [['a']]);
    one.release();
    await flush();
    expect(fn).not.toHaveBeenCalled();
    two.release();
    two.release(); // idempotent
    await flush();
    expect(fn).toHaveBeenCalledTimes(1);
  });

  it('overlap by prefix both ways', async () => {
    const qc = client();
    for (const k of [['p'], ['p', 'x'], ['p', 'y']]) qc.setQueryData(k, 0);
    const x = counter();
    const y = counter();
    mount(qc, ['p', 'x'], x);
    mount(qc, ['p', 'y'], y);

    // A prefix hold parks an exact owe under it.
    const wide = holdReads(qc, [{ queryKey: ['p'] }]);
    void revalidate(qc, [{ queryKey: ['p', 'x'], exact: true }]);
    expect(x).not.toHaveBeenCalled();
    wide.release();
    expect(x).toHaveBeenCalledTimes(1);

    // An exact hold parks its own key out of a prefix owe, and nothing else.
    const narrow = holdReads(qc, [{ queryKey: ['p', 'x'], exact: true }]);
    void revalidate(qc, [['p']]);
    expect(y).toHaveBeenCalledTimes(1);
    expect(x).toHaveBeenCalledTimes(1);
    narrow.release();
    expect(x).toHaveBeenCalledTimes(2);
    await flush();
  });

  it('cancel a read in flight when they register, and owe it', async () => {
    const qc = client();
    qc.setQueryData(['a'], 0);
    const stale = deferred<number>();
    const fn = vi.fn().mockReturnValueOnce(stale.promise).mockResolvedValue(2);
    mount(qc, ['a'], fn);
    void qc.refetchQueries({ queryKey: ['a'] });

    const hold = holdReads(qc, [{ queryKey: ['a'] }], [cachePatch<number>({ queryKey: ['a'] }, () => 9)]);
    expect(qc.getQueryData(['a'])).toBe(9);
    stale.resolve(1);
    await flush();
    expect(qc.getQueryData(['a'])).toBe(9); // the cancelled read never lands
    expect(isOwed(qc, ['a'])).toBe(true);
    hold.release();
    await flush();
    expect(fn).toHaveBeenCalledTimes(2);
    expect(qc.getQueryData(['a'])).toBe(2);
  });

  it('cancel and owe any full read that starts on a held key; a page read passes', async () => {
    const qc = client();
    let aborts = 0;
    const fn = vi.fn(async ({ pageParam, signal }: { pageParam: number; signal: AbortSignal }) => {
      signal.addEventListener('abort', () => (aborts += 1));
      return `p${pageParam}`;
    });
    const observer = new InfiniteQueryObserver(qc, {
      queryKey: ['list'],
      queryFn: fn as never,
      initialPageParam: 0,
      getNextPageParam: (_last: string, all: string[]) => all.length,
      staleTime: Infinity,
    });
    observer.subscribe(() => {});
    await flush();
    const hold = holdReads(qc, [{ queryKey: ['list'] }]);

    void qc.invalidateQueries({ queryKey: ['list'] }); // a stray invalidate
    await flush();
    expect(aborts).toBe(1);
    expect(isOwed(qc, ['list'])).toBe(true);
    expect(qc.getQueryData<InfiniteData<string>>(['list'])?.pages).toEqual(['p0']);

    await observer.fetchNextPage();
    expect(qc.getQueryData<InfiniteData<string>>(['list'])?.pages).toEqual(['p0', 'p1']);

    hold.release();
    await flush();
    expect(isOwed(qc, ['list'])).toBe(false);
  });

  it('drop a page in flight when they register: it was built on the pages from before the patch', async () => {
    const qc = client();
    const server: Record<number, number[]> = { 0: [1, 42], 1: [3, 4] };
    const late = deferred<number[]>();
    let calls = 0;
    const fn = vi.fn(async ({ pageParam }: { pageParam: number }) => {
      calls += 1;
      return calls === 2 ? late.promise : server[pageParam];
    });
    const observer = new InfiniteQueryObserver(qc, {
      queryKey: ['list'],
      queryFn: fn as never,
      initialPageParam: 0,
      getNextPageParam: (_last: number[], all: number[][]) => (all.length < 2 ? all.length : undefined),
      staleTime: Infinity,
    });
    observer.subscribe(() => {});
    await flush();
    const scrolled = observer.fetchNextPage(); // in flight at the click

    const drop42 = cachePatch<InfiniteData<number[]>>({ queryKey: ['list'] }, (d) =>
      d && { ...d, pages: d.pages.map((rows) => rows.filter((id) => id !== 42)) },
    );
    const hold = holdReads(qc, [{ queryKey: ['list'] }], [drop42]);
    expect(isOwed(qc, ['list'])).toBe(false); // a page is not a full read: nothing owed
    hold.release(); // the settle: settleMemberWrite releases in the same call

    late.resolve([3, 4]);
    await scrolled;
    await flush();
    expect(qc.getQueryData<InfiniteData<number[]>>(['list'])?.pages).toEqual([[1]]); // 42 stays dropped
    await observer.fetchNextPage(); // the next scroll asks for the page again, on the patched pages
    expect(qc.getQueryData<InfiniteData<number[]>>(['list'])?.pages).toEqual([[1], [3, 4]]);
  });

  it('cancel a mount read on a held key', async () => {
    const qc = client();
    qc.setQueryData(['a'], 0);
    const hold = holdReads(qc, [{ queryKey: ['a'] }]);
    const fn = counter(() => 5);
    mount(qc, ['a'], fn, 0); // stale: it reads on mount
    await flush();
    expect(qc.getQueryData(['a'])).toBe(0);
    expect(isOwed(qc, ['a'])).toBe(true);
    hold.release();
    await flush();
    expect(qc.getQueryData(['a'])).toBe(5);
  });
});

describe('the ledger', () => {
  const add = (id: number) =>
    cachePatch<number[]>({ queryKey: ['members'], exact: true }, (ids) => ids && [...ids, id]);

  it('a write that fails while another is pending takes only its own patch back', () => {
    const qc = client();
    qc.setQueryData(['members'], [0]);
    const a = holdReads(qc, [{ queryKey: ['members'] }], [add(1)]);
    const b = holdReads(qc, [{ queryKey: ['members'] }], [add(2)]);
    expect(qc.getQueryData(['members'])).toEqual([0, 1, 2]);
    a.rollback();
    expect(qc.getQueryData(['members'])).toEqual([0, 2]);
    a.release();
    b.release();
  });

  it('two failures in either order resurrect nothing', () => {
    const qc = client();
    qc.setQueryData(['members'], [0]);
    const a = holdReads(qc, [{ queryKey: ['members'] }], [add(1)]);
    const b = holdReads(qc, [{ queryKey: ['members'] }], [add(2)]);
    b.rollback();
    expect(qc.getQueryData(['members'])).toEqual([0, 1]);
    a.rollback();
    expect(qc.getQueryData(['members'])).toEqual([0]);
    a.release();
    b.release();
  });

  it('a confirmed patch is kept, and applied once, when a later write fails', () => {
    const qc = client();
    qc.setQueryData(['count'], 10);
    const minus = cachePatch<number>({ queryKey: ['count'] }, (n) => (n ?? 0) - 1);
    const a = holdReads(qc, [{ queryKey: ['count'] }], [minus]);
    const b = holdReads(qc, [{ queryKey: ['count'] }], [minus]);
    expect(qc.getQueryData(['count'])).toBe(8);
    a.release();
    b.rollback();
    expect(qc.getQueryData(['count'])).toBe(9);
    b.release();
    expect(qc.getQueryData(['count'])).toBe(9);
  });

  it('keeps the time the base was read, through a paint and a rollback', () => {
    const qc = client();
    qc.setQueryData(['members'], [0], { updatedAt: 1_000 });
    const a = holdReads(qc, [{ queryKey: ['members'] }], [add(1)]);
    expect(qc.getQueryState(['members'])?.dataUpdatedAt).toBe(1_000);
    a.rollback();
    expect(qc.getQueryState(['members'])?.dataUpdatedAt).toBe(1_000);
    a.release();
  });

  it('a write it did not make becomes the base, with the patches in it, never applied twice', () => {
    const qc = client();
    qc.setQueryData(['count'], 10);
    const minus = cachePatch<number>({ queryKey: ['count'] }, (n) => (n ?? 0) - 1);
    const a = holdReads(qc, [{ queryKey: ['count'] }], [minus]);
    qc.setQueryData(['count'], 7); // the server's answer, set by a site
    const b = holdReads(qc, [{ queryKey: ['count'] }], [minus]);
    expect(qc.getQueryData(['count'])).toBe(6);
    b.rollback();
    expect(qc.getQueryData(['count'])).toBe(7);
    a.release();
    b.release();
  });
});

/* D1: two overlapping writes on one key. The first settle's re-read waits for
 * the second write (it would land without the second's patch and repaint the
 * old state), and the second settle's re-read and its release are ONE batch:
 * the key is read exactly once, after both. Built on the app's real
 * MutationCache, as useOptimisticWrite.test.tsx is. */
describe('release + settle', () => {
  it('reads a key two overlapping writes held exactly once, after the last settles', async () => {
    const qc = new QueryClient({
      mutationCache: createMutationCache(),
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });
    qc.setQueryData(['members'], [0]);
    const server = { members: [0] };
    const fn = vi.fn(async () => [...server.members]);
    const writes = new Map<number, ReturnType<typeof deferred<string>>>();
    const wrapper = ({ children }: { children: ReactNode }) => createElement(QueryClientProvider, { client: qc }, children);
    const { result } = renderHook(
      () => {
        useQuery({ queryKey: ['members'], queryFn: fn, staleTime: Infinity });
        return useOptimisticWrite({
          mutationKey: ['write', 'members'],
          mutationFn: (id: number) => {
            const d = deferred<string>();
            writes.set(id, d);
            return d.promise;
          },
          patch: (id) => [cachePatch<number[]>({ queryKey: ['members'], exact: true }, (ids) => ids && [...ids, id])],
          revalidate: [['members']],
        });
      },
      { wrapper },
    );
    act(() => result.current.mutate(1));
    act(() => result.current.mutate(2));
    expect(qc.getQueryData(['members'])).toEqual([0, 1, 2]);
    await waitFor(() => expect(writes.size).toBe(2));

    server.members = [0, 1];
    await act(async () => writes.get(1)!.resolve('ok'));
    expect(fn).not.toHaveBeenCalled(); // parked: write 2 still holds the key
    expect(qc.getQueryData(['members'])).toEqual([0, 1, 2]);

    server.members = [0, 1, 2];
    await act(async () => writes.get(2)!.resolve('ok'));
    await waitFor(() => expect(fn).toHaveBeenCalledTimes(1));
    await act(async () => {});
    expect(fn).toHaveBeenCalledTimes(1);
    expect(qc.getQueryData(['members'])).toEqual([0, 1, 2]);
  });
});

describe('useReadHold', () => {
  it('holds while active, and pays what it parked when it stops', async () => {
    const qc = client();
    qc.setQueryData(['board'], 0);
    const fn = counter();
    mount(qc, ['board'], fn);
    const wrapper = ({ children }: { children: ReactNode }) => createElement(QueryClientProvider, { client: qc }, children);
    const { rerender } = renderHook(({ active }) => useReadHold([['board']], active), {
      wrapper,
      initialProps: { active: true },
    });
    void revalidate(qc, [['board']]);
    expect(fn).not.toHaveBeenCalled();
    rerender({ active: false });
    expect(fn).toHaveBeenCalledTimes(1);
    await flush();
  });
});

/* A hand on a list (lib/useListHold): only the scheduler's own re-reads wait. */
describe('parkReads', () => {
  /* A mounted list of pages `p0`, `p1`, …; `fn` answers each page at once unless told otherwise. */
  async function list(qc: QueryClient, fn = vi.fn(async ({ pageParam }: { pageParam: number }) => `p${pageParam}`)) {
    const observer = new InfiniteQueryObserver(qc, {
      queryKey: ['list'],
      queryFn: fn as never,
      initialPageParam: 0,
      getNextPageParam: (_last: string, all: string[]) => all.length,
      staleTime: Infinity,
    });
    observer.subscribe(() => {});
    await flush();
    return { observer, fn };
  }
  const pagesOf = (qc: QueryClient) => qc.getQueryData<InfiniteData<string>>(['list'])?.pages;

  it('parks an owed read until the release, then pays it once', async () => {
    const qc = client();
    const { fn } = await list(qc);
    const hand = parkReads(qc, [{ queryKey: ['list'] }]);
    void revalidate(qc, [['list']]);
    void revalidate(qc, [['list']]);
    await flush();
    expect(fn).toHaveBeenCalledTimes(1); // the first load only
    expect(isOwed(qc, ['list'])).toBe(true);

    hand.release();
    await flush();
    expect(fn).toHaveBeenCalledTimes(2);
    expect(isOwed(qc, ['list'])).toBe(false);
  });

  it('cancels a payment in flight when the hand arrives; it stays owed and is paid at the release', async () => {
    const qc = client();
    const late = deferred<string>();
    const { fn } = await list(qc);
    fn.mockReturnValueOnce(late.promise);
    void revalidate(qc, [['list']]); // the payment, in flight
    expect(fn).toHaveBeenCalledTimes(2);

    const hand = parkReads(qc, [{ queryKey: ['list'] }]);
    expect(qc.getQueryState(['list'])?.fetchStatus).toBe('idle');
    late.resolve('stale');
    await flush();
    expect(pagesOf(qc)).toEqual(['p0']); // it never lands under the hand
    expect(isOwed(qc, ['list'])).toBe(true);

    hand.release();
    await flush();
    expect(fn).toHaveBeenCalledTimes(3);
    expect(isOwed(qc, ['list'])).toBe(false);
  });

  it("lets the list's own reads pass: a page, a refetch it asks for, and a first load", async () => {
    const qc = client();
    const { observer, fn } = await list(qc);
    const hand = parkReads(qc, [{ queryKey: ['list'] }, { queryKey: ['other'] }]);
    void revalidate(qc, [['list']]);

    await observer.fetchNextPage();
    expect(pagesOf(qc)).toEqual(['p0', 'p1']);
    await flush();
    expect(isOwed(qc, ['list'])).toBe(true); // a page pays nothing: still parked

    await observer.refetch(); // the user's refresh: two pages from the first
    expect(fn).toHaveBeenCalledTimes(4);
    expect(isOwed(qc, ['list'])).toBe(false); // and it paid what was owed

    const first = vi.fn(async () => 'loaded');
    mount(qc, ['other'], first); // a new key's first load under the hand (a filter or sort change)
    await flush();
    expect(qc.getQueryData(['other'])).toBe('loaded');
    hand.release();
  });

  it('cancels no first load in flight when the hand arrives', async () => {
    const qc = client();
    const load = deferred<number>();
    mount(qc, ['a'], () => load.promise);
    void revalidate(qc, [['a']]);
    const hand = parkReads(qc, [{ queryKey: ['a'] }]);
    expect(qc.getQueryState(['a'])?.fetchStatus).toBe('fetching');
    load.resolve(1);
    await flush();
    expect(qc.getQueryData(['a'])).toBe(1);
    hand.release();
  });

  it('has no TTL: a hand resting for minutes keeps the read parked, with no warning', async () => {
    vi.useFakeTimers();
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    const qc = client();
    qc.setQueryData(['a'], 0);
    const fn = counter();
    mount(qc, ['a'], fn);
    const hand = parkReads(qc, [{ queryKey: ['a'] }]);
    holdReads(qc, [{ queryKey: ['b'] }]).release(); // a write's hold: the online clock runs
    void revalidate(qc, [['a']]);
    vi.advanceTimersByTime(HOLD_TTL_MS * 3);
    expect(fn).not.toHaveBeenCalled();
    expect(warn).not.toHaveBeenCalled();
    hand.release();
    expect(fn).toHaveBeenCalledTimes(1);
  });

  it("composes with a write's hold: its patch paints, its settle frees only its own hold, one read at the hand's release", async () => {
    const qc = client();
    const { fn } = await list(qc);
    const hand = parkReads(qc, [{ queryKey: ['list'] }]);
    void revalidate(qc, [['list']]); // a change made elsewhere, parked

    const drop = cachePatch<InfiniteData<string>>({ queryKey: ['list'] }, (d) => d && { ...d, pages: [] });
    const write = holdReads(qc, [{ queryKey: ['list'] }], [drop]);
    expect(pagesOf(qc)).toEqual([]); // the click paints at once, under the hand
    await revalidate(qc, [['list']], { release: write }); // the settle
    expect(fn).toHaveBeenCalledTimes(1);
    expect(pagesOf(qc)).toEqual([]);

    hand.release();
    await flush();
    expect(fn).toHaveBeenCalledTimes(2);
    expect(pagesOf(qc)).toEqual(['p0']);
  });
});

describe('the epoch', () => {
  it('an older hold paints, parks and pays nothing after resetReads', async () => {
    const qc = client();
    qc.setQueryData(['a'], 1);
    const fn = counter(() => 3);
    mount(qc, ['a'], fn);
    const old = holdReads(qc, [{ queryKey: ['a'] }], [cachePatch<number>({ queryKey: ['a'] }, () => 2)]);
    void revalidate(qc, [['a']]);

    resetReads(qc);
    expect(isOwed(qc, ['a'])).toBe(false);
    old.rollback();
    expect(qc.getQueryData(['a'])).toBe(2); // no paint from the old epoch
    void revalidate(qc, [['a']]); // the new identity's read is not parked by it
    expect(fn).toHaveBeenCalledTimes(1);
    await flush();
    old.release();
    await flush();
    expect(fn).toHaveBeenCalledTimes(1);
  });

  it("a settle that releases an older hold owes the new identity's reads nothing", async () => {
    const qc = client();
    qc.setQueryData(['a'], 1);
    const old = holdReads(qc, [{ queryKey: ['a'] }]);
    resetReads(qc);
    const fn = counter(() => 3);
    mount(qc, ['a'], fn); // the new identity's reader of the same key

    await revalidate(qc, [['a']], { release: old });
    expect(isOwed(qc, ['a'])).toBe(false);
    expect(fn).not.toHaveBeenCalled();
  });
});

describe('the hold TTL', () => {
  it('is the request deadline plus ten seconds', () => {
    expect(HOLD_TTL_MS).toBe(REQUEST_DEADLINE_MS + 10_000);
  });

  it('counts online time only, then releases with a warning', async () => {
    vi.useFakeTimers();
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    const qc = client();
    qc.setQueryData(['a'], 0);
    const fn = counter();
    mount(qc, ['a'], fn);
    holdReads(qc, [{ queryKey: ['a'] }]);
    void revalidate(qc, [['a']]);

    vi.advanceTimersByTime(HOLD_TTL_MS - 1_000);
    onlineManager.setOnline(false);
    vi.advanceTimersByTime(10 * 60_000); // a paused write: offline time does not count
    onlineManager.setOnline(true);
    vi.advanceTimersByTime(999);
    expect(fn).not.toHaveBeenCalled();
    vi.advanceTimersByTime(2);
    expect(warn).toHaveBeenCalledTimes(1);
    expect(fn).toHaveBeenCalledTimes(1);
  });
});
