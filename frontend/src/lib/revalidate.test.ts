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
import { cachePatch, useOptimisticWrite } from './useOptimisticWrite';
import {
  HOLD_TTL_MS,
  holdReads,
  isOwed,
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
