/* THE curation sync owner (docs/design/curation-sync.md § 3.5, Decisions 11-15):
 * one per signed-in window, mounted by lib/auth (AuthProvider). It reads the
 * curation clock (lib/queries `fetchCurationClock`, never acting) and owes,
 * through lib/revalidate, every read a moved family feeds (lib/curationSync
 * `familyTargets`). A plain module: no React state, no query-cache entry, no
 * QueryObserver — every behaviour TanStack would bring is overridden here (R26),
 * and the global focusManager is left alone (R12).
 *
 * WHEN it reads, only while the document is visible (Decision 12):
 * - every SYNC_INTERVAL_MS[frame] from the last read's start (the phone frame
 *   15 s, ruling Q2), never sooner than SYNC_MIN_GAP_MS after an answer;
 * - at once when the page is shown (visibilitychange → visible, pageshow),
 *   aborting a read in flight from before: one across an iOS suspension could
 *   otherwise hang until PG_DEADLINE_MS. A read in flight that started under
 *   SYNC_MIN_GAP_MS ago is already the shown page's (a bfcache restore fires
 *   both events: one read); with none, a show under that gap after the last
 *   start reads once the gap has passed (the hide before it aborted that read,
 *   or disarmed the next);
 * - on focus, online and the nudge (another tab of this browser committed a
 *   write; coalesced over SYNC_NUDGE_DEBOUNCE_MS). Each asks for a read that
 *   starts after it — focus also takes one that started under SYNC_MIN_GAP_MS
 *   before it (the window was already reading); online and a nudge do not (a
 *   read from before cannot have reached the server, or seen that commit). A
 *   read in flight that cannot answer a trigger gets ONE follow-up, and no two
 *   reads start under SYNC_MIN_GAP_MS apart.
 * Hidden: no timer and no request; a read in flight is aborted, a nudge dropped.
 * A transient failure (network, deadline, 5xx, 408, 429, and a lapsed session's
 * 401, which passes once supabase-js refreshes the session) backs off 5 s × 2ⁿ
 * up to SYNC_BACKOFF_MAX_MS, reset by an answer. A permanent one (any other
 * 4xx: the reader kill switch's 403, a renamed view's 404 or 400) stops the
 * interval: a trigger then reads at most once per SYNC_BACKOFF_MAX_MS, until an
 * answer. Never a toast, never a retry storm; an answer whose processing throws
 * is logged and the loop goes on.
 *
 * WHAT moved (Decision 14b). Before its first read the owner owes every synced
 * read already cached, bar an initial load in flight: that one began in the very
 * commit that mounted the owner (RequireAuth renders no page before the session
 * is read; an account switch has just cleared the cache). Every read left
 * unowed thus started after t0, give or take that commit, and a change it
 * missed committed no earlier than t0 minus the bump-to-visible gap, which the
 * margin covers. So the first answer counts a family as changed iff
 * age_ms <= (now - t0) + SYNC_START_MARGIN_MS (both durations: no clock skew),
 * however late it lands. now - t0 is the larger of the monotonic and the
 * wall-clock time since start: performance.now() stands still while the device
 * sleeps on some platforms (w3c/hr-time#115), and a page opened hidden reads
 * first when it is shown. Every later answer is diffed against the last good
 * one (`clockChanges`; a vanished row, a revoked share, is a change of that
 * family and owner) and owes every moved family's targets in ONE `revalidate`;
 * then `repayOwed` retries a debt whose read failed (at most 3 times,
 * lib/revalidate). The self-echo is kept (Decision 15).
 *
 * The nudge rides ONE MutationCache subscription: every mutation success (the
 * API commits before it answers, W0) posts {v:1} on BroadcastChannel
 * 'curation-sync', where the browser has one. Each read goes through
 * supabase-js, which reads the session (getSession) before every request, so an
 * hourly token refresh needs nothing here. */

import { useEffect } from 'react';
import { useQueryClient, type QueryClient } from '@tanstack/react-query';

import { ApiError } from '@/lib/api';
import { clockChanges, clockKey, CURATION_FAMILIES, familyTargets, type ClockRow } from '@/lib/curationSync';
import { isPhoneFrame } from '@/lib/phoneShell';
import { fetchCurationClock } from '@/lib/queries';
import { repayOwed, revalidate, type Revalidation } from '@/lib/revalidate';
import { isSupabaseConfigured } from '@/lib/supabase';

/** Ruling Q2: a visible window reads every 5 s, 15 s in the phone frame. */
export const SYNC_INTERVAL_MS = { desktop: 5_000, phone: 15_000 } as const;
/** No two reads start less than this apart. */
export const SYNC_MIN_GAP_MS = 1_000;
export const SYNC_NUDGE_DEBOUNCE_MS = 100;
/** The longest transient back-off, and the spacing of reads after a permanent failure. */
export const SYNC_BACKOFF_MAX_MS = 60_000;
/** The first answer's margin over the time since start (Decision 14b). */
export const SYNC_START_MARGIN_MS = 10_000;

const BACKOFF_BASE_MS = 5_000;
const NUDGE_CHANNEL = 'curation-sync';
const NUDGE = { v: 1 } as const;
/* The acting root (lib/acting): an owner's reads, which no family names before an answer does. */
const ACTING_ROOT = ['acting'] as const;

const isVisible = (): boolean => document.visibilityState === 'visible';
const clock = (): number => performance.now();

/* Network, deadline, 5xx, 408, 429 and 401 (a session not yet refreshed) may pass; any other 4xx will not. */
const isPermanent = (err: unknown): boolean =>
  err instanceof ApiError && err.kind === 'http' && err.status >= 400 && err.status < 500
  && err.status !== 401 && err.status !== 408 && err.status !== 429;

/* Every synced read cached before the first read could have read before t0, so
 * it is owed, in the mode its family owes it; an initial load in flight is not
 * (the header says why). */
function cachedReads(qc: QueryClient): Revalidation[] {
  const cache = qc.getQueryCache();
  const items: Revalidation[] = [
    ...CURATION_FAMILIES.flatMap((family) => familyTargets(qc, family, null)),
    { queryKey: ACTING_ROOT },
  ];
  const out: Revalidation[] = [];
  for (const item of items) {
    for (const q of cache.findAll({ queryKey: item.queryKey, exact: item.exact })) {
      if (q.state.data === undefined && q.state.fetchStatus === 'fetching') continue;
      out.push({ queryKey: q.queryKey, exact: true, mode: item.mode });
    }
  }
  return out;
}

/** Start following the curation clock for the signed-in user; `stop()` ends it. */
export function startCurationSync(qc: QueryClient): { stop(): void } {
  if (!isSupabaseConfigured()) return { stop: () => {} };
  const t0 = clock();
  const t0Wall = Date.now();
  let stopped = false;
  let last: Map<string, ClockRow> | null = null;
  let inFlight: AbortController | null = null;
  let lastStart = -Infinity;
  let followUp = false;
  let failures = 0;
  let broken = false;
  let due: { at: number; timer: ReturnType<typeof setTimeout> } | null = null;
  /* The newest nudge received, and the timer that coalesces it with the ones before. */
  let nudgedAt = -Infinity;
  let nudgeTimer: ReturnType<typeof setTimeout> | null = null;

  const gap = (): number => (broken ? SYNC_BACKOFF_MAX_MS : SYNC_MIN_GAP_MS);

  /* The one pending read: the earliest asked for wins. */
  function arm(at: number): void {
    if (stopped || !isVisible() || (due && due.at <= at)) return;
    if (due) clearTimeout(due.timer);
    due = { at, timer: setTimeout(read, Math.max(0, at - clock())) };
  }

  /* A trigger's read: now if it is due, else at its time. */
  function readAt(at: number): void {
    if (at <= clock()) read();
    else arm(at);
  }

  function disarm(): void {
    if (due) clearTimeout(due.timer);
    due = null;
  }

  function abort(): void {
    inFlight?.abort();
    inFlight = null;
  }

  /* A trigger at `at` is answered by a read that started at or after `at - slack`. */
  function request(at: number, slack: number): void {
    if (stopped || !isVisible() || lastStart >= at - slack) return;
    if (inFlight) followUp = true;
    else readAt(lastStart + gap());
  }

  const onFocus = (): void => request(clock(), SYNC_MIN_GAP_MS);
  const onOnline = (): void => request(clock(), 0);

  function onShown(): void {
    if (stopped || !isVisible()) return;
    if (clock() - lastStart >= SYNC_MIN_GAP_MS) return broken ? request(clock(), SYNC_MIN_GAP_MS) : read();
    // Under the gap: a read in flight is the shown page's, an armed one stands; with neither, a
    // hide in between aborted that read or disarmed the next, so read again once the gap has passed.
    if (!inFlight && !due) arm(lastStart + gap());
  }

  function onHidden(): void {
    abort();
    disarm();
    if (nudgeTimer != null) clearTimeout(nudgeTimer);
    nudgeTimer = null;
    followUp = false;
  }

  function onNudge(event: MessageEvent): void {
    if (stopped || !isVisible() || (event.data as { v?: unknown } | null)?.v !== NUDGE.v) return;
    nudgedAt = clock();
    nudgeTimer ??= setTimeout(() => {
      nudgeTimer = null;
      request(nudgedAt, 0);
    }, SYNC_NUDGE_DEBOUNCE_MS);
  }

  function read(): void {
    disarm();
    if (stopped || !isVisible()) return;
    abort();
    const ctrl = new AbortController();
    inFlight = ctrl;
    lastStart = clock();
    followUp = false;
    fetchCurationClock({ signal: ctrl.signal }).then(
      (rows) => settle(ctrl, () => answered(rows)),
      (err: unknown) => settle(ctrl, () => failed(err)),
    );
  }

  /* An answer to an aborted, superseded or stopped read is dropped; one whose processing throws is logged. */
  function settle(ctrl: AbortController, then: () => void): void {
    if (stopped || inFlight !== ctrl) return;
    inFlight = null;
    try {
      then();
    } catch (err) {
      console.error('[curation sync] a clock answer could not be processed', err);
    } finally {
      if (followUp) {
        followUp = false;
        readAt(lastStart + gap());
      }
    }
  }

  function answered(rows: ClockRow[]): void {
    failures = 0;
    broken = false;
    const interval = SYNC_INTERVAL_MS[isPhoneFrame() ? 'phone' : 'desktop'];
    arm(Math.max(lastStart + interval, clock() + SYNC_MIN_GAP_MS));
    const elapsed = Math.max(clock() - t0, Date.now() - t0Wall);
    const changed = clockChanges(last, rows, elapsed + SYNC_START_MARGIN_MS);
    last = new Map(rows.map((row) => [clockKey(row), row]));
    const targets = changed.flatMap((row) => familyTargets(qc, row.family, row.mine ? null : row.account_id));
    if (targets.length > 0) void revalidate(qc, targets);
    repayOwed(qc);
  }

  function failed(err: unknown): void {
    if (isPermanent(err)) {
      broken = true;
      return;
    }
    if (broken) return;
    failures += 1;
    arm(clock() + Math.min(BACKOFF_BASE_MS * 2 ** (failures - 1), SYNC_BACKOFF_MAX_MS));
  }

  const onVisibility = (): void => (isVisible() ? onShown() : onHidden());
  document.addEventListener('visibilitychange', onVisibility);
  window.addEventListener('pageshow', onShown);
  window.addEventListener('focus', onFocus);
  window.addEventListener('online', onOnline);

  const channel = typeof BroadcastChannel === 'undefined' ? null : new BroadcastChannel(NUDGE_CHANNEL);
  let unsubscribe = (): void => {};
  if (channel) {
    channel.onmessage = onNudge;
    unsubscribe = qc.getMutationCache().subscribe((event) => {
      if (event.type === 'updated' && event.action.type === 'success') channel.postMessage(NUDGE);
    });
  }

  void revalidate(qc, cachedReads(qc));
  // The first read a task later: a StrictMode remount's stop() cancels it before it is sent.
  arm(clock());

  return {
    stop() {
      if (stopped) return;
      stopped = true;
      onHidden();
      document.removeEventListener('visibilitychange', onVisibility);
      window.removeEventListener('pageshow', onShown);
      window.removeEventListener('focus', onFocus);
      window.removeEventListener('online', onOnline);
      unsubscribe();
      channel?.close();
    },
  };
}

/** Follow the curation clock while `userId` is signed in (null: nobody, or not known yet). */
export function useCurationSync(userId: string | null): void {
  const qc = useQueryClient();
  useEffect(() => {
    if (userId == null) return;
    const owner = startCurationSync(qc);
    return () => owner.stop();
  }, [qc, userId]);
}
