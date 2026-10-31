/* lib/curationSyncOwner — THE curation sync owner (docs/design/curation-sync.md
 * § 3.5, Decisions 11-15; § 9's list). Fake timers as in useBuildSkew.test.tsx
 * (performance.now too: the first answer's window is a duration), a fake
 * BroadcastChannel, and a deferred `fetchCurationClock`: each read waits for
 * the test to answer it. */

import { createElement, StrictMode } from 'react';
import { render } from '@testing-library/react';
import { hashKey, MutationObserver, QueryClient, QueryClientProvider, QueryObserver } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ApiError } from '@/lib/api';
import { CURATION_FAMILIES, familyTargets, type ClockRow, type CurationFamily } from '@/lib/curationSync';
import { curationKeys, fetchCurationClock, pipelineKeys } from '@/lib/queries';
import { isOwed, repayOwed, revalidate, type Revalidation } from '@/lib/revalidate';
import { pushToast } from '@/lib/toast';
import {
  startCurationSync,
  SYNC_BACKOFF_MAX_MS,
  SYNC_INTERVAL_MS,
  SYNC_MIN_GAP_MS,
  SYNC_NUDGE_DEBOUNCE_MS,
  SYNC_START_MARGIN_MS,
  useCurationSync,
} from './curationSyncOwner';

interface Read {
  signal: AbortSignal;
  resolve: (rows: unknown) => void;
  reject: (err: unknown) => void;
}

const h = vi.hoisted(() => ({ configured: true, reads: [] as Read[] }));

vi.mock('@/lib/supabase', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/supabase')>()),
  isSupabaseConfigured: () => h.configured,
}));
vi.mock('@/lib/queries', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/queries')>()),
  fetchCurationClock: vi.fn(
    ({ signal }: { signal: AbortSignal }) =>
      new Promise((resolve, reject) => {
        h.reads.push({ signal, resolve, reject });
      }),
  ),
}));
vi.mock('@/lib/revalidate', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/revalidate')>();
  return { ...actual, revalidate: vi.fn(actual.revalidate), repayOwed: vi.fn(actual.repayOwed) };
});
vi.mock('@/lib/toast', () => ({ pushToast: vi.fn() }));

/* BroadcastChannel as the browser runs it, synchronously: a message reaches
 * every OTHER open channel of the name, never the one that posted it. */
class FakeChannel {
  static open = new Set<FakeChannel>();
  static created = 0;
  onmessage: ((event: MessageEvent) => void) | null = null;
  posted: unknown[] = [];
  constructor(readonly name: string) {
    FakeChannel.created += 1;
    FakeChannel.open.add(this);
  }
  postMessage(data: unknown): void {
    this.posted.push(data);
    for (const c of FakeChannel.open) if (c !== this && c.name === this.name) c.onmessage?.({ data } as MessageEvent);
  }
  close(): void {
    FakeChannel.open.delete(this);
  }
}

const ME = 'acct-me';
const OWNER = 'acct-owner';
const OLD = 3_600_000;

const row = (
  family: CurationFamily,
  counter: number,
  { account = ME, mine = account === ME, age = OLD }: { account?: string; mine?: boolean; age?: number } = {},
): ClockRow => ({ account_id: account, family, counter, mine, age_ms: age });
const mine = (counters: Partial<Record<CurationFamily, number>> = {}): ClockRow[] =>
  CURATION_FAMILIES.map((f) => row(f, counters[f] ?? 1));

let visibility: DocumentVisibilityState = 'visible';
const setVisibility = (state: DocumentVisibilityState) => {
  visibility = state;
  document.dispatchEvent(new Event('visibilitychange'));
};
const fire = (type: 'focus' | 'online' | 'pageshow') => window.dispatchEvent(new Event(type));
const flush = () => vi.advanceTimersByTimeAsync(0);
const answer = async (rows: ClockRow[], i = h.reads.length - 1) => {
  h.reads[i].resolve(rows);
  await flush();
};
const fail = async (err: unknown, i = h.reads.length - 1) => {
  h.reads[i].reject(err);
  await flush();
};
const http = (status: number) => new ApiError(`HTTP ${status}`, status, null, 'http');
/* Another tab of this browser whose write just committed. */
const otherTab = () => new FakeChannel('curation-sync');

const shape = (items: readonly Revalidation[]) =>
  new Set(items.map((t) => `${hashKey(t.queryKey)}|${t.exact ?? false}|${t.mode ?? 'now'}`));
const revalidations = () => vi.mocked(revalidate).mock.calls.map(([, items]) => items as Revalidation[]);

let qc: QueryClient;
let owner: { stop(): void } | null;

beforeEach(() => {
  vi.useFakeTimers({
    toFake: ['setTimeout', 'clearTimeout', 'setInterval', 'clearInterval', 'Date', 'performance'],
  });
  h.configured = true;
  h.reads = [];
  visibility = 'visible';
  Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => visibility });
  FakeChannel.open.clear();
  FakeChannel.created = 0;
  vi.stubGlobal('BroadcastChannel', FakeChannel);
  qc = new QueryClient({ defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } } });
  owner = null;
});

afterEach(() => {
  owner?.stop();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

const start = async () => {
  owner = startCurationSync(qc);
  await flush();
};

describe('the interval, and only while visible', () => {
  it('reads at start, then every 5 s from the last start', async () => {
    await start();
    expect(h.reads).toHaveLength(1);
    expect(fetchCurationClock).toHaveBeenCalledWith({ signal: expect.any(AbortSignal) });
    await answer(mine());
    await vi.advanceTimersByTimeAsync(SYNC_INTERVAL_MS.desktop - 1);
    expect(h.reads).toHaveLength(1);
    await vi.advanceTimersByTimeAsync(1);
    expect(h.reads).toHaveLength(2);
  });

  it('hidden: no timer, no request — a nudge received then sends nothing; shown: one read at once', async () => {
    await start();
    await answer(mine());
    setVisibility('hidden');
    otherTab().postMessage({ v: 1 });
    fire('focus');
    fire('online');
    await vi.advanceTimersByTimeAsync(10 * 60_000);
    expect(h.reads).toHaveLength(1);
    expect(vi.getTimerCount()).toBe(0);
    setVisibility('visible');
    expect(h.reads).toHaveLength(2);
  });

  it('a page that starts hidden reads nothing until it is shown', async () => {
    visibility = 'hidden';
    await start();
    await vi.advanceTimersByTimeAsync(60_000);
    expect(h.reads).toHaveLength(0);
    setVisibility('visible');
    expect(h.reads).toHaveLength(1);
  });

  it('hiding aborts a read in flight, and its late answer moves nothing', async () => {
    await start();
    await answer(mine());
    await vi.advanceTimersByTimeAsync(SYNC_INTERVAL_MS.desktop);
    setVisibility('hidden');
    expect(h.reads[1].signal.aborted).toBe(true);
    const calls = vi.mocked(revalidate).mock.calls.length;
    await answer(mine({ board: 2 }), 1);
    expect(vi.mocked(revalidate).mock.calls.length).toBe(calls);
  });

  it('the phone frame reads every 15 s', async () => {
    vi.stubGlobal('matchMedia', (query: string) => ({ matches: query === '(max-width: 767px)' }));
    await start();
    await answer(mine());
    await vi.advanceTimersByTimeAsync(SYNC_INTERVAL_MS.phone - 1);
    expect(h.reads).toHaveLength(1);
    await vi.advanceTimersByTimeAsync(1);
    expect(h.reads).toHaveLength(2);
  });
});

describe('what an answer re-reads', () => {
  it('the first answer counts a family iff age_ms <= elapsed + 10 s', async () => {
    await start();
    qc.setQueryData(pipelineKeys.board, []);
    qc.setQueryData(curationKeys.tags, []);
    await answer([
      ...mine().filter((r) => r.family !== 'board' && r.family !== 'tags'),
      row('board', 7, { age: SYNC_START_MARGIN_MS }),
      row('tags', 7, { age: SYNC_START_MARGIN_MS + 1 }),
    ]);
    expect(shape(revalidations().at(-1)!)).toEqual(shape(familyTargets(qc, 'board', null)));
    expect(isOwed(qc, pipelineKeys.board)).toBe(true);
    expect(isOwed(qc, curationKeys.tags)).toBe(false);
  });

  /* § 9 (v): the window is the time since start, however late the first good answer lands. */
  it('a first good answer 40 s after start (after an outage) counts what changed within 50 s', async () => {
    await start();
    qc.setQueryData(pipelineKeys.board, []);
    qc.setQueryData(curationKeys.tags, []);
    await fail(http(503));
    await vi.advanceTimersByTimeAsync(5_000);
    await fail(http(503));
    await vi.advanceTimersByTimeAsync(10_000);
    await fail(http(503));
    await vi.advanceTimersByTimeAsync(20_000);
    expect(h.reads).toHaveLength(4);
    await vi.advanceTimersByTimeAsync(5_000);
    expect(performance.now()).toBeGreaterThanOrEqual(40_000);
    await answer([
      ...mine().filter((r) => r.family !== 'board' && r.family !== 'tags'),
      row('board', 7, { age: 40_000 + SYNC_START_MARGIN_MS - 1 }),
      row('tags', 7, { age: 40_000 + SYNC_START_MARGIN_MS + 100 }),
    ]);
    expect(isOwed(qc, pipelineKeys.board)).toBe(true);
    expect(isOwed(qc, curationKeys.tags)).toBe(false);
  });

  it('a board counter change re-reads exactly its targets, in ONE revalidate per answer', async () => {
    await start();
    await answer(mine());
    const before = revalidations().length;
    await vi.advanceTimersByTimeAsync(SYNC_INTERVAL_MS.desktop);
    await answer(mine({ board: 2 }));
    expect(revalidations()).toHaveLength(before + 1);
    expect(shape(revalidations().at(-1)!)).toEqual(shape(familyTargets(qc, 'board', null)));

    await vi.advanceTimersByTimeAsync(SYNC_INTERVAL_MS.desktop);
    await answer(mine({ board: 3, tags: 2, notes: 2 }));
    expect(revalidations()).toHaveLength(before + 2);
    expect(shape(revalidations().at(-1)!)).toEqual(
      shape(['board', 'tags', 'notes'].flatMap((f) => familyTargets(qc, f as CurationFamily, null))),
    );

    await vi.advanceTimersByTimeAsync(SYNC_INTERVAL_MS.desktop);
    await answer(mine({ board: 3, tags: 2, notes: 2 }));
    expect(revalidations()).toHaveLength(before + 2);
  });

  it("an owner's row maps to that owner's acting reads", async () => {
    await start();
    await answer([...mine(), row('board', 1, { account: OWNER })]);
    await vi.advanceTimersByTimeAsync(SYNC_INTERVAL_MS.desktop);
    await answer([...mine(), row('board', 2, { account: OWNER })]);
    const targets = revalidations().at(-1)!;
    expect(shape(targets)).toEqual(shape(familyTargets(qc, 'board', OWNER)));
    for (const t of targets) expect(t.queryKey.slice(0, 2)).toEqual(['acting', OWNER]);
  });

  /* A revoke: the owner's rows vanish, and both parties' shares rows move. */
  it("a vanished owner row re-reads that family's acting reads, with the own shares that moved", async () => {
    await start();
    await answer([...mine(), row('board', 4, { account: OWNER })]);
    await vi.advanceTimersByTimeAsync(SYNC_INTERVAL_MS.desktop);
    await answer(mine({ shares: 2 }));
    expect(shape(revalidations().at(-1)!)).toEqual(
      shape([...familyTargets(qc, 'shares', null), ...familyTargets(qc, 'board', OWNER)]),
    );
  });

  it('start owes every synced read already cached, bar an initial load in flight', async () => {
    const acting = ['acting', OWNER, ...pipelineKeys.board];
    qc.setQueryData(pipelineKeys.board, []);
    qc.setQueryData(acting, []);
    void qc.prefetchQuery({ queryKey: pipelineKeys.members, queryFn: () => new Promise(() => {}) });
    await start();
    expect(isOwed(qc, pipelineKeys.board)).toBe(true);
    expect(isOwed(qc, acting)).toBe(true);
    expect(isOwed(qc, pipelineKeys.members)).toBe(false);
  });

  it('every answer re-pays an errored debt: at most 3 attempts, then it waits for a mount', async () => {
    const queryFn = vi.fn(async () => {
      throw new Error('down');
    });
    await start();
    const sub = new QueryObserver(qc, { queryKey: pipelineKeys.board, queryFn }).subscribe(() => {});
    await flush();
    expect(queryFn).toHaveBeenCalledTimes(1);
    await answer(mine());
    await vi.advanceTimersByTimeAsync(SYNC_INTERVAL_MS.desktop);
    await answer(mine({ board: 2 }));
    expect(queryFn).toHaveBeenCalledTimes(2);
    for (let i = 0; i < 4; i += 1) {
      await vi.advanceTimersByTimeAsync(SYNC_INTERVAL_MS.desktop);
      await answer(mine({ board: 2 }));
    }
    expect(queryFn).toHaveBeenCalledTimes(4);
    expect(repayOwed).toHaveBeenCalledTimes(6);
    sub();
  });
});

describe('triggers', () => {
  it('pageshow and visibilitychange abort a read in flight and read ONCE; the late answer is dropped', async () => {
    await start();
    await answer(mine());
    await vi.advanceTimersByTimeAsync(SYNC_INTERVAL_MS.desktop + 2_000);
    expect(h.reads).toHaveLength(2);
    fire('pageshow');
    setVisibility('visible');
    expect(h.reads[1].signal.aborted).toBe(true);
    expect(h.reads).toHaveLength(3);
    const calls = revalidations().length;
    await answer(mine({ board: 2 }), 1);
    expect(revalidations()).toHaveLength(calls);
    await answer(mine({ board: 2 }), 2);
    expect(revalidations()).toHaveLength(calls + 1);
  });

  it('focus right after a read takes that read; online and a nudge get one starting after them, >= 1 s apart', async () => {
    await start();
    await answer(mine());
    await vi.advanceTimersByTimeAsync(300);
    fire('focus');
    await vi.advanceTimersByTimeAsync(SYNC_MIN_GAP_MS);
    expect(h.reads).toHaveLength(1);

    // Reconnected: a read from before cannot have reached the server.
    fire('online');
    expect(h.reads).toHaveLength(2);
    await answer(mine());

    await vi.advanceTimersByTimeAsync(500);
    const tab = otherTab();
    tab.postMessage({ v: 1 });
    tab.postMessage({ v: 1 });
    await vi.advanceTimersByTimeAsync(SYNC_MIN_GAP_MS - 500 - 1);
    expect(h.reads).toHaveLength(2);
    await vi.advanceTimersByTimeAsync(1);
    expect(h.reads).toHaveLength(3);
    await answer(mine());
    tab.postMessage({ v: 2 });
    tab.postMessage('read now');
    await vi.advanceTimersByTimeAsync(SYNC_INTERVAL_MS.desktop - 1);
    expect(h.reads).toHaveLength(3);
  });

  it('a nudge within 1 s of the last start waits for the gap', async () => {
    await start();
    await answer(mine());
    await vi.advanceTimersByTimeAsync(200);
    otherTab().postMessage({ v: 1 });
    await vi.advanceTimersByTimeAsync(SYNC_MIN_GAP_MS - 200 - 1);
    expect(h.reads).toHaveLength(1);
    await vi.advanceTimersByTimeAsync(1);
    expect(h.reads).toHaveLength(2);
  });

  it('triggers during a read in flight make ONE follow-up once it lands', async () => {
    await start();
    await answer(mine());
    await vi.advanceTimersByTimeAsync(SYNC_INTERVAL_MS.desktop);
    expect(h.reads).toHaveLength(2);
    await vi.advanceTimersByTimeAsync(SYNC_MIN_GAP_MS + 200);
    const tab = otherTab();
    tab.postMessage({ v: 1 });
    fire('focus');
    fire('online');
    await vi.advanceTimersByTimeAsync(SYNC_NUDGE_DEBOUNCE_MS);
    tab.postMessage({ v: 1 });
    await vi.advanceTimersByTimeAsync(SYNC_NUDGE_DEBOUNCE_MS);
    expect(h.reads).toHaveLength(2);
    await answer(mine());
    expect(h.reads).toHaveLength(3);
    await answer(mine());
    await vi.advanceTimersByTimeAsync(SYNC_INTERVAL_MS.desktop - 1);
    expect(h.reads).toHaveLength(3);
  });
});

describe('failures', () => {
  it('a transient failure backs off 5 s × 2ⁿ up to 60 s; an answer resets it; never a toast', async () => {
    await start();
    const failures = [http(503), new ApiError('Failed to fetch', 0, null, 'network'), http(408), http(429),
      new ApiError('slow', 0, null, 'timeout'), http(500)];
    const waits = [5_000, 10_000, 20_000, 40_000, 60_000, 60_000];
    for (const [i, err] of failures.entries()) {
      await fail(err);
      await vi.advanceTimersByTimeAsync(waits[i] - 1);
      expect(h.reads).toHaveLength(i + 1);
      await vi.advanceTimersByTimeAsync(1);
      expect(h.reads).toHaveLength(i + 2);
    }
    await answer(mine());
    await vi.advanceTimersByTimeAsync(SYNC_INTERVAL_MS.desktop);
    expect(h.reads).toHaveLength(failures.length + 2);
    await fail(http(503));
    await vi.advanceTimersByTimeAsync(5_000);
    expect(h.reads).toHaveLength(failures.length + 3);
    expect(pushToast).not.toHaveBeenCalled();
  });

  it.each([400, 401, 403, 404])(
    'a permanent %i (the reader kill switch) stops the interval: at most one read per 60 s, never a toast',
    async (status) => {
      await start();
      await fail(http(status));
      await vi.advanceTimersByTimeAsync(5 * 60_000);
      expect(h.reads).toHaveLength(1);
      fire('focus');
      await flush();
      expect(h.reads).toHaveLength(2);
      await fail(http(status));
      await vi.advanceTimersByTimeAsync(10_000);
      fire('focus');
      fire('online');
      otherTab().postMessage({ v: 1 });
      setVisibility('visible');
      fire('pageshow');
      await vi.advanceTimersByTimeAsync(SYNC_BACKOFF_MAX_MS - 10_000 - 1);
      expect(h.reads).toHaveLength(2);
      await vi.advanceTimersByTimeAsync(1);
      expect(h.reads).toHaveLength(3);
      // A blip while broken keeps it broken: still no interval, no back-off.
      await fail(new ApiError('Failed to fetch', 0, null, 'network'));
      await vi.advanceTimersByTimeAsync(5 * 60_000);
      expect(h.reads).toHaveLength(3);
      // The grant is back: the next triggered read answers, and the interval resumes.
      fire('focus');
      await flush();
      await answer(mine());
      await vi.advanceTimersByTimeAsync(SYNC_INTERVAL_MS.desktop);
      expect(h.reads).toHaveLength(5);
      expect(pushToast).not.toHaveBeenCalled();
    },
  );
});

describe('the nudge', () => {
  it('every mutation success posts ONE {v:1} to the other tabs; a failed write posts none', async () => {
    await start();
    const tab = otherTab();
    const heard = vi.fn();
    tab.onmessage = heard;
    await new MutationObserver(qc, { mutationFn: async () => 'ok' }).mutate();
    expect(heard).toHaveBeenCalledTimes(1);
    expect(heard.mock.calls[0][0].data).toEqual({ v: 1 });
    await new MutationObserver(qc, { mutationFn: async () => Promise.reject(new Error('409')) })
      .mutate()
      .catch(() => {});
    expect(heard).toHaveBeenCalledTimes(1);
  });

  it('a browser without BroadcastChannel still follows the clock', async () => {
    vi.stubGlobal('BroadcastChannel', undefined);
    await start();
    await new MutationObserver(qc, { mutationFn: async () => 'ok' }).mutate();
    await answer(mine());
    await vi.advanceTimersByTimeAsync(SYNC_INTERVAL_MS.desktop);
    expect(h.reads).toHaveLength(2);
  });
});

describe('lifecycle', () => {
  it('stop() aborts the read, clears the timers, closes the channel, unsubscribes and drops a late answer', async () => {
    await start();
    const [mineChannel] = FakeChannel.open;
    expect(h.reads).toHaveLength(1);
    const calls = revalidations().length;
    owner!.stop();
    expect(h.reads[0].signal.aborted).toBe(true);
    expect(FakeChannel.open.size).toBe(0);
    expect(vi.getTimerCount()).toBe(0);
    await answer(mine({ board: 9 }), 0);
    expect(revalidations()).toHaveLength(calls);
    for (const type of ['focus', 'online', 'pageshow'] as const) fire(type);
    setVisibility('visible');
    otherTab().postMessage({ v: 1 });
    await new MutationObserver(qc, { mutationFn: async () => 'ok' }).mutate();
    await vi.advanceTimersByTimeAsync(10 * 60_000);
    expect(h.reads).toHaveLength(1);
    expect(mineChannel.posted).toEqual([]);
  });

  it('a StrictMode double mount runs one owner: one read, one channel', async () => {
    function Probe() {
      useCurationSync('user-1');
      return null;
    }
    const view = render(
      createElement(StrictMode, null, createElement(QueryClientProvider, { client: qc }, createElement(Probe))),
    );
    await flush();
    expect(FakeChannel.created).toBe(2);
    expect(h.reads).toHaveLength(1);
    expect(FakeChannel.open.size).toBe(1);
    await answer(mine());
    await vi.advanceTimersByTimeAsync(2_000);
    fire('focus');
    await flush();
    expect(h.reads).toHaveLength(2);
    view.unmount();
    expect(FakeChannel.open.size).toBe(0);
  });

  it('a signed-out window (null) runs no owner', async () => {
    function Probe() {
      useCurationSync(null);
      return null;
    }
    render(createElement(QueryClientProvider, { client: qc }, createElement(Probe)));
    await vi.advanceTimersByTimeAsync(60_000);
    expect(h.reads).toHaveLength(0);
  });

  it('isSupabaseConfigured() false: no owner — no read, no channel, no listener', async () => {
    h.configured = false;
    await start();
    fire('focus');
    setVisibility('visible');
    await vi.advanceTimersByTimeAsync(60_000);
    expect(h.reads).toHaveLength(0);
    expect(FakeChannel.open.size).toBe(0);
  });
});

/* The read itself, through the real supabase-js client against a stubbed fetch
 * (as lib/pgRead.test.ts does): the ClockRow columns of the view, and never the
 * acting header — the audience is the view's to state (§ 3.4). */
describe('fetchCurationClock', () => {
  it('reads exactly the ClockRow columns of curation_clock_public and never sends x-act-as', async () => {
    vi.useRealTimers();
    const rows = [row('board', 3)];
    const fetchMock = vi.fn(async (_url: RequestInfo | URL, _init?: RequestInit) =>
      ({ ok: true, status: 200, statusText: '', headers: new Headers(), text: async () => JSON.stringify(rows) }) as
        unknown as Response,
    );
    vi.stubGlobal('fetch', fetchMock);
    const real = await vi.importActual<typeof import('@/lib/queries')>('@/lib/queries');
    await expect(real.fetchCurationClock({ signal: new AbortController().signal })).resolves.toEqual(rows);
    const [url, init] = fetchMock.mock.calls[0];
    const u = new URL(String(url));
    expect(u.pathname).toBe('/rest/v1/curation_clock_public');
    expect(u.searchParams.get('select')).toBe('account_id,family,counter,mine,age_ms');
    expect(new Headers(init?.headers).has('x-act-as')).toBe(false);
  });
});
