import { useId } from 'react';
import {
  useMutation,
  useQuery,
  useQueryClient,
  type InfiniteData,
} from '@tanstack/react-query';
import { Link, useSearchParams } from 'react-router-dom';
import { ROUTES } from '@/lib/routes';

import {
  listWatchdogDispatches,
  listWatchdogSubscriptions,
  markWatchdogDispatchSeen,
} from '@/lib/api';
import { watchdogKeys } from '@/lib/queries';
import { useInfiniteList } from '@/lib/useInfiniteList';
import InfiniteSentinel from '@/components/InfiniteSentinel';
import {
  fmtAbsolute,
  fmtCount,
  fmtCzk,
  fmtRelative,
} from '@/lib/format';
import { portalShort } from '@/lib/portals';
import { listingKindLabel } from '@/lib/enums';
import { listingPath } from '@/lib/listingUrl';
import type {
  WatchdogDispatch,
  WatchdogSeenFilter,
  WatchdogSubscription,
} from '@/lib/types';
import { Th } from '@/components/table';

const PAGE_SIZE = 50;

interface WatchdogPage {
  rows: WatchdogDispatch[];
  nextCursor?: string;
  total: number | null;
}
/* The matcher ticks every 5 min, so 30 s gives plenty of resolution. */
const POLL_INTERVAL_FEED_MS = 30_000;

const SEEN_OPTIONS: ReadonlyArray<WatchdogSeenFilter> = ['all', 'unseen', 'seen'];

export default function Watchdog() {
  const qc = useQueryClient();
  const [params, setParams] = useSearchParams();
  const subscriptionId = params.get('subscription') ?? null;
  const seen = (params.get('seen') as WatchdogSeenFilter | null) ?? 'all';

  /* Keyset infinite feed over GET /notifications/dispatches (newest-first,
   * cursor on (dispatched_at, id) — dup/skip-free as the background matcher
   * prepends new dispatches). Keyed on the (subscription, seen) partition. */
  const dispatches = useInfiniteList<WatchdogDispatch, WatchdogPage>({
    queryKey: watchdogKeys.dispatches({
      subscription_id: subscriptionId ?? undefined,
      source_kind: 'watchdog',
      seen,
    }),
    queryFn: async (cursor) => {
      const resp = await listWatchdogDispatches({
        subscription_id: subscriptionId ?? undefined,
        // This page is watchdog-only; the unified /notifications feed shows
        // collection_monitor events (which carry no subscription_name).
        source_kind: 'watchdog',
        seen,
        limit: PAGE_SIZE,
        cursor: (cursor as string | null) ?? undefined,
      });
      return {
        rows: resp.data,
        nextCursor: resp.next_cursor ?? undefined,
        total: resp.total,
      };
    },
    pageSize: PAGE_SIZE,
    getRowId: (r) => r.id,
    refetchInterval: () => POLL_INTERVAL_FEED_MS,
  });
  const total = dispatches.firstPage?.total ?? null;

  const subscriptionsQ = useQuery<
    { data: WatchdogSubscription[]; total: number },
    Error
  >({
    queryKey: watchdogKeys.subscriptions,
    queryFn: () => listWatchdogSubscriptions(),
    staleTime: 30_000,
  });

  /* Surgical update of one dispatch across every loaded page of every
   * dispatches infinite query — so mark-seen doesn't blow the cache
   * and reset the scroll position (the old invalidate(all) did). The mutation
   * responses already carry the updated row. The callback is partition-aware
   * (it sees each cached query's `seen` filter) so marking a row seen DROPS
   * it from the 'unseen' view instead of leaving a now-read row lingering. */
  const updateDispatchCaches = (
    fn: (
      rows: WatchdogDispatch[],
      seenFilter: WatchdogSeenFilter | undefined,
    ) => WatchdogDispatch[],
  ) => {
    const entries = qc.getQueriesData<InfiniteData<WatchdogPage>>({
      queryKey: ['watchdog', 'dispatches'],
    });
    for (const [key, data] of entries) {
      if (!data) continue;
      const params = key[2] as { seen?: WatchdogSeenFilter } | undefined;
      qc.setQueryData<InfiniteData<WatchdogPage>>(key, {
        ...data,
        pages: data.pages.map((pg) => ({
          ...pg,
          rows: fn(pg.rows, params?.seen),
        })),
      });
    }
  };

  const markSeenMut = useMutation({
    mutationFn: (dispatchId: string) => markWatchdogDispatchSeen(dispatchId),
    onSuccess: (updated) =>
      updateDispatchCaches((rows, seenFilter) =>
        seenFilter === 'unseen'
          ? rows.filter((r) => r.id !== updated.id)
          : rows.map((r) => (r.id === updated.id ? updated : r)),
      ),
  });

  const setFilter = (key: 'subscription' | 'seen', value: string | null) => {
    const sp = new URLSearchParams(params);
    if (value == null || value === '' || value === 'all') sp.delete(key);
    else sp.set(key, value);
    setParams(sp, { replace: false });
  };

  const subscriptions = subscriptionsQ.data?.data ?? [];

  return (
    <div className="px-6 py-8 max-w-6xl mx-auto">
      <Header />

      <div className="mt-6 flex flex-wrap items-center gap-x-6 gap-y-3">
        <SubscriptionFilter
          value={subscriptionId}
          subscriptions={subscriptions}
          onChange={(v) => setFilter('subscription', v)}
        />
        <SeenFilter value={seen} onChange={(v) => setFilter('seen', v)} />
      </div>

      <div className="mt-6">
        {dispatches.isLoading ? (
          <div className="text-sm text-[var(--color-ink-3)]">Loading…</div>
        ) : dispatches.isError ? (
          <div className="text-sm text-[var(--color-brick)]">
            Failed to load: {dispatches.error?.message}
          </div>
        ) : dispatches.rows.length === 0 ? (
          <EmptyState
            filtered={subscriptionId != null || seen !== 'all'}
            hasAnyWatchdog={subscriptions.length > 0}
          />
        ) : (
          <DispatchesTable
            rows={dispatches.rows}
            total={total}
            isFetchingNextPage={dispatches.isFetchingNextPage}
            hasNextPage={dispatches.hasNextPage}
            onReachEnd={dispatches.fetchNextPage}
            onMarkSeen={(id) => markSeenMut.mutate(id)}
          />
        )}
      </div>
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Header                                                                     */
/* -------------------------------------------------------------------------- */

function Header() {
  return (
    <header className="flex flex-wrap items-end justify-between gap-4">
      <div>
        <p className="text-[0.7rem] tracking-[0.18em] uppercase text-[var(--color-ink-3)]">
          Watchdog
        </p>
        <h1
          className="mt-1.5 text-[2.1rem] leading-tight"
          style={{ fontFamily: 'var(--font-display)', fontWeight: 600 }}
        >
          New-listing notifications
        </h1>
        <p className="mt-2 text-sm text-[var(--color-ink-2)]">
          Saved filters fire a notification the moment a freshly scraped
          listing matches. The <em>Portal</em> column links to the listing on the portal it was
          last seen on; the <em>MF yield</em> column shows the property's
          Ministry-of-Finance reference gross yield, the one the filter matched.
        </p>
      </div>
      <div className="flex items-center gap-3">
        <Link
          to={ROUTES.watchdogManage.build()}
          className="px-3 py-1.5 text-sm rounded-[var(--radius-sm)] bg-[var(--color-copper)] text-white hover:bg-[var(--color-copper-2)] transition-colors"
        >
          Manage watchdogs
        </Link>
      </div>
    </header>
  );
}

/* -------------------------------------------------------------------------- */
/* Filter row                                                                 */
/* -------------------------------------------------------------------------- */

function SubscriptionFilter({
  value,
  subscriptions,
  onChange,
}: {
  value: string | null;
  subscriptions: WatchdogSubscription[];
  onChange: (v: string | null) => void;
}) {
  const captionId = useId();
  return (
    <div className="flex items-center gap-2">
      <span
        id={captionId}
        className="text-[0.65rem] tracking-[0.14em] uppercase text-[var(--color-ink-4)]"
      >
        Watchdog
      </span>
      <select
        aria-labelledby={captionId}
        value={value ?? ''}
        onChange={(e) => onChange(e.target.value || null)}
        className="px-2 py-1 text-sm rounded-[var(--radius-sm)] border border-[var(--color-rule)] bg-[var(--color-paper-2)] text-[var(--color-ink)]"
      >
        <option value="">All watchdogs</option>
        {subscriptions.map((s) => (
          <option key={s.id} value={s.id}>
            {s.name} {s.is_active ? '' : '(paused)'}
          </option>
        ))}
      </select>
    </div>
  );
}

function SeenFilter({
  value,
  onChange,
}: {
  value: WatchdogSeenFilter;
  onChange: (v: string | null) => void;
}) {
  return (
    <div className="flex items-center gap-2">
      <span className="text-[0.65rem] tracking-[0.14em] uppercase text-[var(--color-ink-4)]">
        Read
      </span>
      <div className="inline-flex items-center gap-0.5 p-0.5 rounded-[var(--radius-sm)] bg-[var(--color-paper-2)] border border-[var(--color-rule)]">
        {SEEN_OPTIONS.map((opt) => (
          <button
            key={opt}
            type="button"
            onClick={() => onChange(opt === 'all' ? null : opt)}
            className={[
              'px-2.5 py-0.5 text-[0.7rem] tracking-wide rounded-[var(--radius-xs)] transition-colors',
              value === opt
                ? 'bg-[var(--color-copper)] text-white'
                : 'text-[var(--color-ink-3)] hover:text-[var(--color-ink-2)]',
            ].join(' ')}
            aria-pressed={value === opt}
          >
            {opt}
          </button>
        ))}
      </div>
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Dispatches table                                                           */
/* -------------------------------------------------------------------------- */

function DispatchesTable({
  rows,
  total,
  isFetchingNextPage,
  hasNextPage,
  onReachEnd,
  onMarkSeen,
}: {
  rows: WatchdogDispatch[];
  total: number | null;
  isFetchingNextPage: boolean;
  hasNextPage: boolean;
  onReachEnd: () => void;
  onMarkSeen: (dispatchId: string) => void;
}) {
  return (
    <div className="rounded-[var(--radius-md)] border border-[var(--color-rule)] bg-[var(--color-paper-2)] overflow-hidden">
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="bg-[var(--color-paper-2)] border-b border-[var(--color-rule)]">
            <tr>
              <Th align="left">Listing</Th>
              <Th align="left">Disposition</Th>
              <Th align="right">Price</Th>
              <Th align="left">When</Th>
              <Th align="left">Watchdog</Th>
              <Th align="left">Portal</Th>
              <Th align="right">MF yield</Th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <Row
                key={r.id}
                dispatch={r}
                onMarkSeen={onMarkSeen}
              />
            ))}
          </tbody>
        </table>
      </div>
      <InfiniteSentinel
        onReach={onReachEnd}
        hasNextPage={hasNextPage}
        isFetchingNextPage={isFetchingNextPage}
        loadedCount={rows.length}
        total={total}
      />
      <div className="flex items-center justify-between gap-4 px-4 py-2.5 border-t border-[var(--color-rule)] bg-[var(--color-paper)]">
        <p className="text-[0.75rem] text-[var(--color-ink-3)] tabular-nums">
          Showing{' '}
          <span className="text-[var(--color-ink-2)]">{fmtCount(rows.length)}</span>
          {total != null && (
            <> of <span className="text-[var(--color-ink-2)]">{fmtCount(total)}</span></>
          )}
        </p>
      </div>
    </div>
  );
}

function Row({
  dispatch,
  onMarkSeen,
}: {
  dispatch: WatchdogDispatch;
  onMarkSeen: (dispatchId: string) => void;
}) {
  const unread = dispatch.seen_at == null;
  const handleListingClick = () => {
    if (unread) onMarkSeen(dispatch.id);
  };
  return (
    <tr
      className={[
        'border-b border-[var(--color-rule-soft)] last:border-b-0 transition-colors',
        unread
          ? 'bg-[var(--color-copper-soft)]/30 hover:bg-[var(--color-copper-soft)]/50'
          : 'hover:bg-[var(--color-paper)]',
      ].join(' ')}
    >
      <td className="px-4 py-2.5 align-middle max-w-[280px]">
        <Link
          to={listingPath(dispatch.sreality_id)}
          onClick={handleListingClick}
          className="block hover:text-[var(--color-copper)] hover:underline underline-offset-2"
        >
          <span className="block text-[var(--color-ink)] truncate">
            {dispatch.display_label ?? '—'}
          </span>
        </Link>
      </td>
      <td className="px-4 py-2.5 align-middle text-[var(--color-ink-2)] tabular-nums">
        {listingKindLabel(dispatch) ?? <span className="text-[var(--color-ink-4)]">—</span>}
      </td>
      <td className="px-4 py-2.5 align-middle text-right font-mono tabular-nums text-[var(--color-ink)]">
        <PriceCell dispatch={dispatch} />
      </td>
      <td
        className="px-4 py-2.5 align-middle text-[var(--color-ink-2)] tabular-nums"
        title={fmtAbsolute(dispatch.dispatched_at)}
      >
        {fmtRelative(dispatch.dispatched_at)}
      </td>
      <td className="px-4 py-2.5 align-middle">
        <span className="inline-block px-2 py-0.5 text-[0.65rem] tracking-wide rounded-[var(--radius-xs)] bg-[var(--color-paper)] border border-[var(--color-rule)] text-[var(--color-ink-2)]">
          {dispatch.subscription_name}
        </span>
      </td>
      <td className="px-4 py-2.5 align-middle">
        <PortalCell dispatch={dispatch} onMarkSeen={onMarkSeen} unread={unread} />
      </td>
      <td className="px-4 py-2.5 align-middle text-right">
        <MfYieldCell dispatch={dispatch} />
      </td>
    </tr>
  );
}

function PriceCell({ dispatch }: { dispatch: WatchdogDispatch }) {
  if (dispatch.price_czk == null) {
    return <span className="text-[var(--color-ink-4)]">—</span>;
  }
  const isRental = dispatch.category_type === 'pronajem';
  return (
    <>
      {fmtCzk(dispatch.price_czk)}
      {isRental ? (
        <span className="ml-1 text-[var(--color-ink-3)] text-[0.7rem]">/mo</span>
      ) : null}
    </>
  );
}

/* The portal the property was last seen on, as a clickable chip. Links to the
 * portal's own listing page — the stored source_url (every portal, sreality
 * included); when it is null it falls back to our internal listing detail.
 * Clicking marks the dispatch read, like the listing-name link. */
function PortalCell({
  dispatch,
  onMarkSeen,
  unread,
}: {
  dispatch: WatchdogDispatch;
  onMarkSeen: (dispatchId: string) => void;
  unread: boolean;
}) {
  if (!dispatch.source) {
    return <span className="text-[var(--color-ink-4)]">—</span>;
  }
  const label = portalShort(dispatch.source);
  const onClick = () => {
    if (unread) onMarkSeen(dispatch.id);
  };
  const chip =
    'inline-flex items-center gap-1 px-2 py-0.5 text-[0.65rem] tracking-wide '
    + 'rounded-[var(--radius-xs)] bg-[var(--color-paper)] border '
    + 'border-[var(--color-rule)] text-[var(--color-ink-2)] '
    + 'hover:text-[var(--color-copper)] hover:border-[var(--color-rule-strong)] transition-colors';
  const externalUrl = dispatch.source_url;
  if (externalUrl) {
    return (
      <a
        href={externalUrl}
        target="_blank"
        rel="noopener noreferrer"
        onClick={onClick}
        className={chip}
        title={`Open on ${label}`}
      >
        {label} ↗
      </a>
    );
  }
  return (
    <Link
      to={listingPath(dispatch.sreality_id)}
      onClick={onClick}
      className={chip}
      title={`Last seen on ${label}`}
    >
      {label}
    </Link>
  );
}

/* MF (Ministry of Finance) reference gross yield carried on the listing —
 * the deterministic Cenová-mapa figure. Sale apartments only; '—' otherwise. */
function MfYieldCell({ dispatch }: { dispatch: WatchdogDispatch }) {
  if (dispatch.mf_gross_yield_pct == null) {
    return <span className="text-[var(--color-ink-4)]">—</span>;
  }
  return (
    <span
      className="font-mono tabular-nums text-[var(--color-ink)]"
      title="Ministry-of-Finance reference gross rental yield (Cenová mapa nájemného)"
    >
      {dispatch.mf_gross_yield_pct.toFixed(1)}%
    </span>
  );
}

/* -------------------------------------------------------------------------- */
/* Empty state                                                                */
/* -------------------------------------------------------------------------- */

function EmptyState({
  filtered,
  hasAnyWatchdog,
}: {
  filtered: boolean;
  hasAnyWatchdog: boolean;
}) {
  return (
    <div className="px-6 py-16 text-center border border-dashed border-[var(--color-rule)] rounded-[var(--radius-md)]">
      <p className="text-[0.7rem] tracking-[0.18em] uppercase text-[var(--color-ink-3)]">
        {filtered ? 'No matches' : 'Nothing fired yet'}
      </p>
      <p className="mt-2 text-sm text-[var(--color-ink-2)]">
        {filtered
          ? 'No notifications match these filters.'
          : hasAnyWatchdog
            ? 'No new listings have matched your watchdogs since the last scrape. The matcher wakes up automatically.'
            : 'Define a watchdog (a saved filter) and you’ll get a notification when a freshly scraped listing matches.'}
      </p>
      {!hasAnyWatchdog ? (
        <Link
          to={ROUTES.browse.build()}
          className="mt-4 inline-block text-sm text-[var(--color-copper)] hover:underline underline-offset-2"
        >
          Create a watchdog in Browse →
        </Link>
      ) : null}
    </div>
  );
}
