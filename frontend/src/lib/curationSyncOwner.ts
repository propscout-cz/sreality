/* THE curation sync owner: one per signed-in window, mounted by lib/auth
 * (AuthProvider). It reads the curation clock and owes, through lib/revalidate,
 * every read a moved family feeds (lib/curationSync `familyTargets`). The what
 * and when are docs/design/curation-sync.md § 3.5, Decisions 11-15 and 14b; here
 * is why the loop is shaped as it is.
 *
 * - A plain module, no React state, no cache entry, no QueryObserver (Decision
 *   11, R26): every behaviour TanStack would bring (focus, reconnect, retry) is
 *   one this loop must override, and a cache entry would need its own root and
 *   an exemption from `clear()`.
 * - Every trigger is visibility-gated, and a show aborts a read in flight: a
 *   hidden tab costs nothing, and a read in flight across an iOS suspension could
 *   otherwise hang until PG_DEADLINE_MS (Decision 12).
 * - A trigger asks for a read that starts after it (focus also takes one that
 *   started just before it), never two under SYNC_MIN_GAP_MS apart: a read from
 *   before a reconnect or another tab's commit cannot have seen it.
 * - Failures back off and never toast; a permanent one (the reader kill switch)
 *   stops the interval, so a switched-off reader sheds its load (§ 3.9).
 * - The first answer counts a family by its age against the time since start,
 *   both durations, so no clock skew; the larger of the monotonic and the wall
 *   duration, because performance.now() stands still in a device sleep on some
 *   platforms (w3c/hr-time#115). Exact because start owes every synced read
 *   already cached bar an initial load in flight (Decision 14b says why).
 * - The baseline moves only once an answer's re-reads are owed, so an answer
 *   whose processing throws leaves its changes owed to the next answer.
 * - The nudge rides lib/mutationCache's committed-write signal (W0: success is
 *   committed), on BroadcastChannel where the browser has one; supabase-js reads
 *   the session before every request, so a token refresh needs nothing here. */

import { useEffect } from 'react';
import { useQueryClient, type QueryClient } from '@tanstack/react-query';

import { ACTING_SEGMENT } from '@/lib/acting';
import { ApiError } from '@/lib/api';
import { clockChanges, clockKey, CURATION_FAMILIES, familyTargets, type ClockRow } from '@/lib/curationSync';
import { onCommittedWrite } from '@/lib/mutationCache';
import { isPhoneFrame } from '@/lib/phoneShell';
import { fetchCurationClock } from '@/lib/queries';
import { repayOwed, revalidate } from '@/lib/revalidate';
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

const isVisible = (): boolean => document.visibilityState === 'visible';
const monotonicNow = (): number => performance.now();

/* Transient: back off and read again. Every failure but a 4xx: a network
 * failure, a deadline, any 5xx, an error that is no API answer at all; and of
 * the 4xx, 401 (a session supabase-js has not refreshed yet, which passes by
 * itself: Risk 4; the reader kill switch answers 403), 408 and 429 (the server
 * asks to be asked later). Wider than lib/api's isTransientApiError (a network
 * failure, 502/503/504/520), which asks whether ONE request is worth a second
 * attempt now; the owner asks whether a read seconds later may pass, so a
 * deadline and a 500 wait too, and everything that rule retries is transient
 * here. Permanent, stopping the interval: every other 4xx (the kill switch's
 * 403, a renamed view's 404 or 400). */
const WAIT_STATUSES = new Set([401, 408, 429]);
const isTransient = (err: unknown): boolean =>
  !(err instanceof ApiError && err.kind === 'http' && err.status >= 400 && err.status < 500)
  || WAIT_STATUSES.has(err.status);

/** Start following the curation clock for the signed-in user; `stop()` ends it. */
export function startCurationSync(qc: QueryClient): { stop(): void } {
  if (!isSupabaseConfigured()) return { stop: () => {} };
  const t0 = monotonicNow();
  const t0Wall = Date.now();
  let stopped = false;
  /* The last good answer: what the next one is diffed against. */
  let last: Map<string, ClockRow> | null = null;
  /* The loop: the read in flight, when the last one started, whether a trigger it
   * cannot answer asked for one more, and the one pending read. */
  const reads = {
    inFlight: null as AbortController | null,
    lastStart: -Infinity,
    followUp: false,
    due: null as { at: number; timer: ReturnType<typeof setTimeout> } | null,
  };
  /* Transient failures in a row, and whether a permanent one stopped the interval. */
  const errors = { transient: 0, permanentMode: false };
  /* The newest nudge received, and the timer that coalesces it with the ones before. */
  const nudge = { at: -Infinity, timer: null as ReturnType<typeof setTimeout> | null };

  const gap = (): number => (errors.permanentMode ? SYNC_BACKOFF_MAX_MS : SYNC_MIN_GAP_MS);

  /* The one pending read: the earliest asked for wins. */
  function arm(at: number): void {
    if (stopped || !isVisible() || (reads.due && reads.due.at <= at)) return;
    if (reads.due) clearTimeout(reads.due.timer);
    reads.due = { at, timer: setTimeout(read, Math.max(0, at - monotonicNow())) };
  }

  /* A trigger's read: now if it is due, else at its time. */
  function readAt(at: number): void {
    if (at <= monotonicNow()) read();
    else arm(at);
  }

  function disarm(): void {
    if (reads.due) clearTimeout(reads.due.timer);
    reads.due = null;
  }

  function abort(): void {
    reads.inFlight?.abort();
    reads.inFlight = null;
  }

  /* A trigger at `at` is answered by a read that started at or after `at - slack`. */
  function request(at: number, slack: number): void {
    if (stopped || !isVisible() || reads.lastStart >= at - slack) return;
    if (reads.inFlight) reads.followUp = true;
    else readAt(reads.lastStart + gap());
  }

  const onFocus = (): void => request(monotonicNow(), SYNC_MIN_GAP_MS);
  const onOnline = (): void => request(monotonicNow(), 0);

  function onShown(): void {
    if (stopped || !isVisible()) return;
    if (monotonicNow() - reads.lastStart >= SYNC_MIN_GAP_MS) {
      if (errors.permanentMode) request(monotonicNow(), SYNC_MIN_GAP_MS);
      else read();
      return;
    }
    // Under the gap: a read in flight is the shown page's, an armed one stands; with neither, a
    // hide in between aborted that read or disarmed the next, so read again once the gap has passed.
    if (!reads.inFlight && !reads.due) arm(reads.lastStart + gap());
  }

  function onHidden(): void {
    abort();
    disarm();
    if (nudge.timer != null) clearTimeout(nudge.timer);
    nudge.timer = null;
    reads.followUp = false;
  }

  function onNudge(event: MessageEvent): void {
    if (stopped || !isVisible() || (event.data as { v?: unknown } | null)?.v !== NUDGE.v) return;
    nudge.at = monotonicNow();
    nudge.timer ??= setTimeout(() => {
      nudge.timer = null;
      request(nudge.at, 0);
    }, SYNC_NUDGE_DEBOUNCE_MS);
  }

  function read(): void {
    disarm();
    if (stopped || !isVisible()) return;
    abort();
    const ctrl = new AbortController();
    reads.inFlight = ctrl;
    reads.lastStart = monotonicNow();
    reads.followUp = false;
    fetchCurationClock({ signal: ctrl.signal }).then(
      (rows) => takeAnswer(ctrl, () => answered(rows)),
      (err: unknown) => takeAnswer(ctrl, () => failed(err)),
    );
  }

  /* An answer to an aborted, superseded or stopped read is dropped; one whose processing throws is logged. */
  function takeAnswer(ctrl: AbortController, then: () => void): void {
    if (stopped || reads.inFlight !== ctrl) return;
    reads.inFlight = null;
    try {
      then();
    } catch (err) {
      console.error('[curation sync] a clock answer could not be processed', err);
    } finally {
      if (reads.followUp) {
        reads.followUp = false;
        readAt(reads.lastStart + gap());
      }
    }
  }

  function answered(rows: ClockRow[]): void {
    errors.transient = 0;
    errors.permanentMode = false;
    const interval = SYNC_INTERVAL_MS[isPhoneFrame() ? 'phone' : 'desktop'];
    arm(Math.max(reads.lastStart + interval, monotonicNow() + SYNC_MIN_GAP_MS));
    const elapsed = Math.max(monotonicNow() - t0, Date.now() - t0Wall);
    const changed = clockChanges(last, rows, elapsed + SYNC_START_MARGIN_MS);
    const targets = changed.flatMap((row) => familyTargets(qc, row.family, row.mine ? null : row.account_id));
    if (targets.length > 0) void revalidate(qc, targets);
    last = new Map(rows.map((row) => [clockKey(row), row]));
    repayOwed(qc);
  }

  function failed(err: unknown): void {
    if (!isTransient(err)) {
      errors.permanentMode = true;
      return;
    }
    if (errors.permanentMode) return;
    errors.transient += 1;
    arm(monotonicNow() + Math.min(BACKOFF_BASE_MS * 2 ** (errors.transient - 1), SYNC_BACKOFF_MAX_MS));
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
    unsubscribe = onCommittedWrite(qc.getMutationCache(), () => channel.postMessage(NUDGE));
  }

  void revalidate(
    qc,
    [...CURATION_FAMILIES.flatMap((family) => familyTargets(qc, family, null)), { queryKey: [ACTING_SEGMENT] }],
    { skipInitialLoads: true },
  );
  // The first read a task later: a StrictMode remount's stop() cancels it before it is sent.
  arm(monotonicNow());

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
