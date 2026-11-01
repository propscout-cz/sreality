/* lib/curationSyncOwner — THE curation sync owner (docs/design/curation-sync.md
 * § 3.5, Decisions 11-15; § 9's list). Fake timers as in useBuildSkew.test.tsx
 * (performance.now too: the first answer's window is a duration), a fake
 * BroadcastChannel, the app's MutationCache, and a deferred `fetchCurationClock`:
 * each read waits for the test to answer it. What an answer re-reads is listed
 * by hand below, never asked of familyTargets. */

import { createElement, StrictMode } from 'react';
import { render } from '@testing-library/react';
import {
  hashKey,
  MutationObserver,
  QueryClient,
  QueryClientProvider,
  QueryObserver,
  type QueryKey,
} from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ApiError } from '@/lib/api';
import { CURATION_FAMILIES, familyTargets, type ClockRow, type CurationFamily } from '@/lib/curationSync';
import { createMutationCache } from '@/lib/mutationCache';
import { curationKeys, fetchCurationClock, pipelineKeys } from '@/lib/queries';
import { isOwed, revalidate, type Revalidation } from '@/lib/revalidate';
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
  return { ...actual, revalidate: vi.fn(actual.revalidate) };
});
/* Passed through; one test makes it throw once. */
vi.mock('@/lib/curationSync', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/curationSync')>();
  return { ...actual, familyTargets: vi.fn(actual.familyTargets) };
});
vi.mock('@/lib/toast', () => ({ pushToast: vi.fn() }));

/* BroadcastChannel as the browser runs it, synchronously: a message reaches
 * every OTHER open channel of the name, never the one that posted it. */
class FakeChannel {
  static open = new Set<FakeChannel>();
  onmessage: ((event: MessageEvent) => void) | null = null;
  posted: unknown[] = [];
  constructor(readonly name: string) {
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

/* What each family re-reads with no Browse cohort cached, by hand (lib/curationSync
 * SYNC_TABLES): every read of its tables, an open collection page among them
 * (LENS_REREAD.lists, 'now' since ruling Q3(c)). */
const now = (...keys: QueryKey[]): Revalidation[] => keys.map((queryKey) => ({ queryKey }));
const BOARD = now(['pipeline', 'board'], ['pipeline', 'members'], ['pipeline', 'stages']);
const TAGS = now(['curation', 'tags'], ['curation', 'property-tag-members']);
const NOTES = now(['curation', 'property-notes'], ['curation', 'note-counts']);
const SHARES = [
  ...now(['pipeline-shares'], ['curation', 'collections'], ['curation', 'property-collection-members']),
  ...now(['curation', 'property-notes'], ['curation', 'note-counts'], ['curation', 'collection']),
];
const ownerBoard = (owner: string) =>
  now(['acting', owner, 'pipeline', 'board'], ['acting', owner, 'pipeline', 'members'],
    ['acting', owner, 'pipeline', 'stages']);

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
  vi.stubGlobal('BroadcastChannel', FakeChannel);
  qc = new QueryClient({
    mutationCache: createMutationCache(),
    defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
  });
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
    expect(shape(revalidations().at(-1)!)).toEqual(shape(BOARD));
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

  /* performance.now() stands still while the device sleeps on macOS and Linux (w3c/hr-time#115);
   * vi.setSystemTime moves the wall clock alone, as a sleep does there. */
  it('a page opened hidden and first shown after the device slept counts what changed during the sleep', async () => {
    visibility = 'hidden';
    await start();
    qc.setQueryData(pipelineKeys.board, []);
    qc.setQueryData(curationKeys.tags, []);
    vi.setSystemTime(Date.now() + 8 * 3_600_000);
    await vi.advanceTimersByTimeAsync(60_000);
    expect(performance.now()).toBeLessThan(3_600_000);
    setVisibility('visible');
    await answer([
      ...mine().filter((r) => r.family !== 'board' && r.family !== 'tags'),
      row('board', 7, { age: 2 * 3_600_000 }),
      row('tags', 7, { age: 9 * 3_600_000 }),
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
    expect(shape(revalidations().at(-1)!)).toEqual(shape(BOARD));

    await vi.advanceTimersByTimeAsync(SYNC_INTERVAL_MS.desktop);
    await answer(mine({ board: 3, tags: 2, notes: 2 }));
    expect(revalidations()).toHaveLength(before + 2);
    expect(shape(revalidations().at(-1)!)).toEqual(shape([...BOARD, ...TAGS, ...NOTES]));

    await vi.advanceTimersByTimeAsync(SYNC_INTERVAL_MS.desktop);
    await answer(mine({ board: 3, tags: 2, notes: 2 }));
    expect(revalidations()).toHaveLength(before + 2);
  });

  it("an owner's row maps to that owner's acting reads", async () => {
    await start();
    await answer([...mine(), row('board', 1, { account: OWNER })]);
    await vi.advanceTimersByTimeAsync(SYNC_INTERVAL_MS.desktop);
    await answer([...mine(), row('board', 2, { account: OWNER })]);
    expect(shape(revalidations().at(-1)!)).toEqual(shape(ownerBoard(OWNER)));
  });

  /* A revoke: the owner's rows vanish, and both parties' shares rows move. */
  it("a vanished owner row re-reads that family's acting reads, with the own shares that moved", async () => {
    await start();
    await answer([...mine(), row('board', 4, { account: OWNER })]);
    await vi.advanceTimersByTimeAsync(SYNC_INTERVAL_MS.desktop);
    await answer(mine({ shares: 2 }));
    expect(shape(revalidations().at(-1)!)).toEqual(shape([...SHARES, ...ownerBoard(OWNER)]));
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
    sub();
  });

  /* The baseline moves only once the answer's re-reads are owed: a change whose
     processing threw is still a change to the next answer. */
  it('an answer whose targets cannot be computed leaves its change for the next answer', async () => {
    const logged = vi.spyOn(console, 'error').mockImplementation(() => {});
    qc.setQueryData(pipelineKeys.board, []);
    await start();
    await answer(mine());
    await vi.advanceTimersByTimeAsync(SYNC_INTERVAL_MS.desktop);
    await qc.fetchQuery({ queryKey: pipelineKeys.board, queryFn: async () => [] });
    expect(isOwed(qc, pipelineKeys.board)).toBe(false);
    vi.mocked(familyTargets).mockImplementationOnce(() => {
      throw new Error('a family the map does not know');
    });
    await answer(mine({ board: 2 }));
    expect(logged).toHaveBeenCalledTimes(1);
    expect(isOwed(qc, pipelineKeys.board)).toBe(false);
    await vi.advanceTimersByTimeAsync(SYNC_INTERVAL_MS.desktop);
    await answer(mine({ board: 2 }));
    expect(isOwed(qc, pipelineKeys.board)).toBe(true);
    logged.mockRestore();
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

  /* A desktop tab flick: visibilitychange, then focus, within a second of the last start. */
  it('a hide and show within 1 s of a read the hide aborted read again once the gap has passed; the interval goes on', async () => {
    await start();
    await answer(mine());
    await vi.advanceTimersByTimeAsync(SYNC_INTERVAL_MS.desktop + 300);
    expect(h.reads).toHaveLength(2);
    setVisibility('hidden');
    expect(h.reads[1].signal.aborted).toBe(true);
    await vi.advanceTimersByTimeAsync(300);
    setVisibility('visible');
    fire('focus');
    expect(h.reads).toHaveLength(2);
    expect(vi.getTimerCount()).toBe(1);
    await vi.advanceTimersByTimeAsync(SYNC_MIN_GAP_MS - 600 - 1);
    expect(h.reads).toHaveLength(2);
    await vi.advanceTimersByTimeAsync(1);
    expect(h.reads).toHaveLength(3);
    await answer(mine());
    await vi.advanceTimersByTimeAsync(SYNC_INTERVAL_MS.desktop);
    expect(h.reads).toHaveLength(4);
  });

  it('a hide and show within 1 s of a read that had answered read again once the gap has passed; the interval goes on', async () => {
    await start();
    await answer(mine());
    await vi.advanceTimersByTimeAsync(SYNC_INTERVAL_MS.desktop + 100);
    await answer(mine());
    await vi.advanceTimersByTimeAsync(200);
    setVisibility('hidden');
    await vi.advanceTimersByTimeAsync(300);
    setVisibility('visible');
    fire('pageshow');
    fire('focus');
    expect(vi.getTimerCount()).toBe(1);
    await vi.advanceTimersByTimeAsync(SYNC_MIN_GAP_MS - 600);
    expect(h.reads).toHaveLength(3);
    await answer(mine());
    await vi.advanceTimersByTimeAsync(SYNC_INTERVAL_MS.desktop);
    expect(h.reads).toHaveLength(4);
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

  /* supabase-js falls back to the anon key while a refresh fails; the next refresh ends it. */
  it("a lapsed session's 401 backs off like a blip and resumes by itself once the session is refreshed", async () => {
    await start();
    await fail(http(401));
    await vi.advanceTimersByTimeAsync(5_000);
    expect(h.reads).toHaveLength(2);
    await fail(http(401));
    await vi.advanceTimersByTimeAsync(10_000);
    expect(h.reads).toHaveLength(3);
    await answer(mine());
    await vi.advanceTimersByTimeAsync(SYNC_INTERVAL_MS.desktop);
    expect(h.reads).toHaveLength(4);
    expect(pushToast).not.toHaveBeenCalled();
  });

  it('an answer that cannot be processed is logged, and its follow-up and the interval go on', async () => {
    const logged = vi.spyOn(console, 'error').mockImplementation(() => {});
    await start();
    await answer(mine());
    await vi.advanceTimersByTimeAsync(SYNC_INTERVAL_MS.desktop + SYNC_MIN_GAP_MS);
    expect(h.reads).toHaveLength(2);
    fire('online');
    await answer(null as unknown as ClockRow[]);
    expect(logged).toHaveBeenCalledTimes(1);
    expect(h.reads).toHaveLength(3);
    await answer(mine({ board: 2 }));
    expect(shape(revalidations().at(-1)!)).toEqual(shape(BOARD));
    await vi.advanceTimersByTimeAsync(SYNC_INTERVAL_MS.desktop);
    expect(h.reads).toHaveLength(4);
    logged.mockRestore();
  });

  it.each([400, 403, 404])(
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

  /* query-core settles a write whose own onSuccess throws as an error; it committed all the same. */
  it('a committed write whose onSuccess throws still posts its nudge', async () => {
    await start();
    const heard = vi.fn();
    otherTab().onmessage = heard;
    await new MutationObserver(qc, {
      mutationFn: async () => 'ok',
      onSuccess: () => {
        throw new Error('the screen it updates is gone');
      },
      onError: () => {},
    })
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

  it('a StrictMode double mount runs one owner: one read, one nudge per write', async () => {
    function Probe() {
      useCurationSync('user-1');
      return null;
    }
    const view = render(
      createElement(StrictMode, null, createElement(QueryClientProvider, { client: qc }, createElement(Probe))),
    );
    await flush();
    expect(h.reads).toHaveLength(1);
    const tab = otherTab();
    const heard = vi.fn();
    tab.onmessage = heard;
    await new MutationObserver(qc, { mutationFn: async () => 'ok' }).mutate();
    expect(heard).toHaveBeenCalledTimes(1);
    tab.close();
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
