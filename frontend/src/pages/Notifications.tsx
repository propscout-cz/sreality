/* The one Notifications feed: watchdog matches, collection-monitor change
 * events and things shared with me, from one endpoint. A read/triage surface —
 * filter by source / seen, mark one or all read, open what a row is about
 * (lib/dispatchTarget; on the desktop in a new tab, so the feed stays where it
 * was — lib/linkGestures `useNewTabProps`). The deal pipeline, the watchdog list and this feed stay
 * distinct; this is where "what changed about things I care about" lives, and
 * its unseen rows are the count the nav badge, the phone bell and the app icon
 * show (lib/unreadCount). Older rows load as the end of the list comes into
 * view (components/InfiniteSentinel, as on Browse; keyset, newest first).
 *
 * ONE PAGE FOR BOTH FRAMES (W5a): below 768 px the same markup lays out to the
 * phone's 16 px gutters (`max-md:`). The frame's header already names the page
 * and its bell carries the count, so the explanation steps aside (the empty
 * state still explains); the rows' hairlines run to the screen edge as the
 * roll's do, and a row turns into two lines beside its photo — kind and time,
 * then what it is about — whose prices never split.
 *
 * A listing row leads with the cover of the ad it fired on (lib/hydration: one
 * cover read per loaded page, never a gate on the row). The photo's box is fixed
 * before the photo lands, so a row never reflows; a share has no ad and no box.
 *
 * A coalesced push opens this page as `/notifications?d={delivery}`: the open is
 * reported once and the parameter dropped (lib/push). */

import { useId, useMemo, useState, type MouseEvent } from 'react';
import { Link } from 'react-router-dom';
import { useMutation, useQueryClient, type InfiniteData } from '@tanstack/react-query';
import {
  listNotifications,
  markAllNotificationsSeen,
  markWatchdogDispatchSeen,
} from '@/lib/api';
import { revalidateTables, tableReads } from '@/lib/curationSync';
import { notificationKeys } from '@/lib/queries';
import { cachePatch } from '@/lib/revalidate';
import { useListHold } from '@/lib/useListHold';
import { useOptimisticWrite } from '@/lib/useOptimisticWrite';
import { dispatchTarget } from '@/lib/dispatchTarget';
import { useNewTabProps } from '@/lib/linkGestures';
import { ROUTES } from '@/lib/routes';
import { usePageTitle } from '@/lib/pageTitle';
import { usePhoneShell } from '@/lib/phoneShell';
import { usePushOpenedParam } from '@/lib/push';
import { useUnreadCount } from '@/lib/unreadCount';
import { useInfiniteList, type InfiniteListPage } from '@/lib/useInfiniteList';
import { fmtAbsolute, fmtCount, fmtCzk, fmtRelative } from '@/lib/format';
import { listingKindLabel } from '@/lib/enums';
import { CHANGE_META, matchReasonLabel } from '@/lib/changeKinds';
import { CardHydrationProvider, useCardHydration } from '@/lib/hydration';
import ErrorBanner from '@/components/ErrorBanner';
import InfiniteSentinel from '@/components/InfiniteSentinel';
import { PHOTO_DARK } from '@/components/phone/CardPhotoStrip';
import { SegmentedBar, type SegmentOption } from '@/components/controls';
import type {
  NotificationSourceKind,
  WatchdogDispatch,
  WatchdogSeenFilter,
} from '@/lib/types';

type SourceFilter = NotificationSourceKind | 'all';

const PAGE_SIZE = 50;
const noop = () => {};
/* The one decoration a row renders; every other is off (lib/hydration). */
const COVERS = { covers: true } as const;

/* dispatchTarget's answer for a row with nothing to open: the feed itself. */
const FEED = ROUTES.notifications.build();

/* The two filters each pick one: the one joined bar (SegmentedBar), in the phone
 * tone inside the phone frame (chosen = ink on the inset well, never copper). */
const SOURCE_OPTS: ReadonlyArray<SegmentOption<SourceFilter>> = [
  { value: 'all', label: 'All' },
  { value: 'collection_monitor', label: 'Collections' },
  { value: 'watchdog', label: 'Watchdogs' },
  { value: 'share', label: 'Shared' },
];

const SEEN_OPTS: ReadonlyArray<SegmentOption<WatchdogSeenFilter>> = [
  { value: 'all', label: 'All' },
  { value: 'unseen', label: 'Unread' },
];

export default function Notifications() {
  const qc = useQueryClient();
  const [source, setSource] = useState<SourceFilter>('all');
  const [seen, setSeen] = useState<WatchdogSeenFilter>('all');
  usePushOpenedParam();

  const feedKey = notificationKeys.feed({ source, seen });
  const feed = useInfiniteList<WatchdogDispatch>({
    queryKey: feedKey,
    queryFn: async (cursor) => {
      const resp = await listNotifications({
        source_kind: source === 'all' ? undefined : source,
        seen,
        limit: PAGE_SIZE,
        cursor: (cursor as string | null) ?? undefined,
      });
      return { rows: resp.data, nextCursor: resp.next_cursor ?? undefined };
    },
    pageSize: PAGE_SIZE,
    getRowId: (d) => d.id,
  });
  /* The feed follows the curation clock (W7, lib/curationSync): a mark-seen, a new
   * event or a retired watchdog anywhere re-reads it within one clock read, but
   * never under the hand (ruling Q3(c)): the rows park while the pointer rests on
   * them or a finger is down, the bell's count follows at once. No poll. */
  const hand = useListHold([feedKey]);

  const markAll = useMutation({
    mutationFn: () => markAllNotificationsSeen(),
    onSuccess: () => revalidateTables(qc, ['notification_dispatches']),
  });

  const rows = feed.rows;
  const pages = useMemo(() => {
    const out: WatchdogDispatch[][] = [];
    for (let i = 0; i < rows.length; i += PAGE_SIZE) out.push(rows.slice(i, i + PAGE_SIZE));
    return out;
  }, [rows]);
  /* No poll comes back for a failed read, so it offers its own retry; a failed next
   * page or re-read keeps the rows already drawn, the banner under them. */
  const failed = feed.isError ? (
    <ErrorBanner
      flush={rows.length === 0}
      title="Failed to load:"
      message={feed.error?.message ?? 'The notifications did not load.'}
      retryLabel="Retry"
      onRetry={feed.isFetchNextPageError ? feed.fetchNextPage : feed.refetch}
    />
  ) : null;
  /* Shell's one unread observer (lib/unreadCount), the number the bells show. */
  const { count: unread } = useUnreadCount();

  // Tab title carries the unread count, capped like the nav badge so the two
  // never disagree. Zero → the plain "Notifications" route handle. Not in the
  // phone frame: the title is its header's, beside the bell that has the count.
  const phone = usePhoneShell();
  usePageTitle(!phone && unread > 0 ? `Notifications (${unread > 99 ? '99+' : unread})` : null);
  const tone = phone ? 'phone' : 'desktop';

  return (
    <div className="px-6 py-8 max-w-5xl mx-auto max-md:px-4 max-md:pt-5 max-md:pb-6">
      <Header
        unread={unread}
        onMarkAll={() => markAll.mutate()}
        markAllPending={markAll.isPending}
      />

      <div className="mt-6 flex flex-wrap items-center gap-x-6 gap-y-3 max-md:mt-4 max-md:gap-y-2">
        <SegmentedBar hug tone={tone} label="Source" options={SOURCE_OPTS} value={source} onChange={setSource} />
        <SegmentedBar hug tone={tone} label="Read state" options={SEEN_OPTS} value={seen} onChange={setSeen} />
      </div>

      <div {...hand} className="mt-7 max-md:mt-4">
        {feed.isLoading ? (
          <div className="text-sm text-[var(--color-ink-3)]">Loading…</div>
        ) : failed && rows.length === 0 ? (
          failed
        ) : rows.length === 0 ? (
          <EmptyState seen={seen} />
        ) : (
          <>
            <ul className="divide-y divide-[var(--color-rule-soft)] border-t border-b border-[var(--color-rule-soft)] max-md:-mx-4">
              {pages.map((page, i) => (
                <RowsPage key={i} rows={page} />
              ))}
            </ul>
            {/* Only while there is more: the list ends silently, as it did under the button. */}
            {feed.hasNextPage && (
              <InfiniteSentinel
                /* A failed next page waits for the banner's retry: it would ask again in a loop. */
                onReach={feed.isFetchNextPageError ? noop : feed.fetchNextPage}
                hasNextPage
                isFetchingNextPage={feed.isFetchingNextPage}
                loadedCount={rows.length}
                total={null}
              />
            )}
            {failed}
          </>
        )}
      </div>
    </div>
  );
}

function Header({
  unread,
  onMarkAll,
  markAllPending,
}: {
  unread: number;
  onMarkAll: () => void;
  markAllPending: boolean;
}) {
  return (
    <header className="flex items-end justify-between gap-4 max-md:items-center">
      <div className="min-w-0">
        <h1
          className="text-[2.1rem] leading-tight max-md:text-[1.5rem]"
          style={{ fontFamily: 'var(--font-display)', fontWeight: 600 }}
        >
          What changed
        </h1>
        <p className="mt-2 text-sm text-[var(--color-ink-2)] max-md:hidden">
          Watchdog matches and changes to properties in your monitored
          collections — price moves, delistings, relistings, new sources — and
          collections shared with you.{' '}
          {unread > 0 && (
            <span className="text-[var(--color-ink-3)]">
              {fmtCount(unread)} unread.
            </span>
          )}
        </p>
      </div>
      <button
        type="button"
        onClick={onMarkAll}
        disabled={markAllPending || unread === 0}
        className="shrink-0 px-3 py-1.5 text-[0.78rem] rounded-[var(--radius-sm)] border border-[var(--color-rule)] text-[var(--color-ink-2)] hover:border-[var(--color-rule-strong)] hover:text-[var(--color-ink)] disabled:opacity-40 disabled:cursor-not-allowed transition-colors max-md:h-9 max-md:py-0 max-md:text-[13px]"
      >
        {markAllPending ? 'Marking…' : 'Mark all read'}
      </button>
    </header>
  );
}

/* A page of rows under its own cover read: the read is keyed on its cohort, so one
 * cohort that grew with every page would re-read all earlier covers as each next
 * page lands (Browse buckets its photos the same way, lib/hydration). The page is
 * the key's index, never its first row: a new event at the top would otherwise
 * remount every page. */
function RowsPage({ rows }: { rows: WatchdogDispatch[] }) {
  const listingIds = useMemo(
    () => rows.flatMap((d) => (d.listing_id == null ? [] : [d.listing_id])),
    [rows],
  );
  return (
    <CardHydrationProvider listingIds={listingIds} renders={COVERS}>
      {rows.map((d) => (
        <li key={d.id}>
          <NotificationRow dispatch={d} />
        </li>
      ))}
    </CardHydrationProvider>
  );
}

/* One feed row. A share has no listing subject, so it renders as a text row;
 * watchdog + collection_monitor rows render the listing card. One component per
 * shape so each calls its hooks unconditionally (rules-of-hooks) — the
 * dispatcher itself uses none. */
function NotificationRow({ dispatch: d }: { dispatch: WatchdogDispatch }) {
  if (d.source_kind === 'share') return <ShareNotificationRow dispatch={d} />;
  return <StandardNotificationRow dispatch={d} />;
}

type FeedPages = InfiniteData<InfiniteListPage<WatchdogDispatch>>;

/* The one optimistic write of a row (lib/useOptimisticWrite): the row reads as read
 * and the bell drops one inside the click, so neither waits for a re-read a hand on
 * the list has parked (the pointer is on the row it just clicked). The re-read
 * follows: under the Unread filter the row leaves when the hand does. */
function useMarkSeen(d: WatchdogDispatch): () => void {
  const markSeen = useOptimisticWrite({
    mutationKey: ['write', 'notifications', 'mark-seen'],
    mutationFn: () => markWatchdogDispatchSeen(d.id),
    patch: () => [
      cachePatch<FeedPages>({ queryKey: notificationKeys.anyFeed }, (old) => old && {
        ...old,
        pages: old.pages.map((p) => ({
          ...p,
          rows: p.rows.map((r) => (r.id === d.id ? { ...r, seen_at: new Date().toISOString() } : r)),
        })),
      }),
      cachePatch<{ unread_count: number }>({ queryKey: notificationKeys.unreadCount, exact: true }, (old) =>
        old && { ...old, unread_count: Math.max(0, old.unread_count - 1) }),
    ],
    revalidate: tableReads(['notification_dispatches'], null),
  });
  return () => {
    if (d.seen_at == null) markSeen.mutate();
  };
}

/* A middle-click opens a row's link too, but fires `auxclick`, never `click`;
 * an opened row is a read row either way. */
const onMiddleClick = (fn: () => void) => (e: MouseEvent) => {
  if (e.button === 1) fn();
};

/* The unread dot is decorative; the state rides on the control's description
 * (`id`), so marking a row seen never renames it. */
function UnreadDot({ unread, id, color }: { unread: boolean; id: string; color: string }) {
  return (
    <span className="mt-1.5 w-1.5 shrink-0 max-md:mt-2.5">
      {unread && (
        <>
          <span className="block w-1.5 h-1.5 rounded-full" style={{ background: color }} aria-hidden />
          <span id={id} hidden>
            unread
          </span>
        </>
      )}
    </span>
  );
}

/* The ad's cover in a fixed 4:3 box that is there before the photo is (nothing
 * reflows when it lands), and stays there, empty, for an ad with no photo or one
 * that will not load (dashed, as the Valuo card's comparables show it). It names
 * nothing: the row's own text is its name. A delisted ad's photo is muted, as
 * Browse and the board mute it. */
function RowThumb({ listingId, inactive }: { listingId: number | null; inactive: boolean }) {
  const { coverFor } = useCardHydration();
  const src = coverFor(listingId);
  const [broken, setBroken] = useState<string | null>(null);
  const failed = src != null && broken === src;
  return (
    <span
      className={`relative isolate block aspect-[4/3] w-[4.5rem] overflow-hidden rounded-[var(--radius-sm)] border bg-[var(--color-inset)] max-md:row-span-2 ${
        failed ? 'border-dashed border-[var(--color-rule-strong)]' : 'border-[var(--color-rule)]'
      }`}
    >
      {src && !failed && (
        <img
          src={src}
          alt=""
          loading="lazy"
          decoding="async"
          onError={() => setBroken(src)}
          className={`block h-full w-full object-cover ${PHOTO_DARK} ${inactive ? 'saturate-[0.4] brightness-[0.97]' : ''}`}
        />
      )}
    </span>
  );
}

/* A wrapped line ends on " ·", never starts on one. */
const NBSP = '\u00a0';
/* A row line on the phone: text and figures as items that wrap whole. */
const PHONE_FIGURES = 'max-md:flex max-md:flex-wrap max-md:items-baseline max-md:gap-x-2 max-md:whitespace-normal';

function StandardNotificationRow({ dispatch: d }: { dispatch: WatchdogDispatch }) {
  const markSeen = useMarkSeen(d);
  const unread = d.seen_at == null;
  const unreadId = useId();
  const meta = CHANGE_META[d.change_kind] ?? {
    label: d.change_kind,
    dot: 'var(--color-ink-3)',
  };
  const place = d.display_label ?? '—';
  /* The kind of source first, so a watchdog's name never reads as the ad's title. */
  const [sourceKind, sourceName] =
    d.source_kind === 'collection_monitor'
      ? ['Collection', d.collection_name]
      : ['Watchdog', d.subscription_name];
  const sourceLabel = sourceName ? `${sourceKind} · ${sourceName}` : sourceKind;
  const to = dispatchTarget(d);
  const newTab = useNewTabProps();
  const control = {
    onClick: markSeen,
    'aria-describedby': unread ? unreadId : undefined,
    /* A grid so the photo can lead on both frames: one line of dot, photo, kind,
     * what it is about, time on the desktop; below 768 px the photo spans two
     * lines, kind and time on the first, what it is about on the second. */
    className:
      'grid grid-cols-[0.375rem_4.5rem_auto_minmax(0,1fr)_auto] items-start gap-x-3 py-3.5 group w-full text-left max-md:grid-cols-[0.375rem_4.5rem_minmax(0,1fr)_auto] max-md:gap-x-2.5 max-md:gap-y-1.5 max-md:px-4',
  };

  const body = (
    <>
      <UnreadDot unread={unread} id={unreadId} color="var(--color-copper)" />

      <RowThumb listingId={d.listing_id} inactive={d.is_active === false} />

      {/* change-kind chip */}
      <span className="mt-0.5 inline-flex items-center gap-1.5 justify-self-start px-2 py-0.5 text-[0.7rem] rounded-[var(--radius-sm)] border border-[var(--color-rule)] bg-[var(--color-paper-2)] text-[var(--color-ink-2)]">
        <span
          aria-hidden
          className="w-1.5 h-1.5 rounded-full"
          style={{ background: meta.dot }}
        />
        {meta.label}
      </span>

      {/* On the phone this is the row's second line, under the chip. Its
        * figures are flex items there: one that does not fit starts the next
        * line whole, without the " · " (a dot opening a line reads as noise). */}
      <div className="min-w-0 max-md:col-span-2 max-md:col-start-3 max-md:row-start-2">
        <p className={`text-sm text-[var(--color-ink)] group-hover:text-[var(--color-copper)] transition-colors truncate ${PHONE_FIGURES}`}>
          <span>
            <span className="font-medium">{place}</span>
            {listingKindLabel(d) && (
              <span className="text-[var(--color-ink-3)]">{NBSP}· {listingKindLabel(d)}</span>
            )}
          </span>
          {d.price_czk != null && (
            <span className="text-[var(--color-ink-2)] font-mono tabular-nums whitespace-nowrap">
              <span className="max-md:hidden">{' '}· </span>
              {fmtCzk(d.price_czk)}
            </span>
          )}
        </p>
        <p className={`mt-0.5 text-[0.78rem] text-[var(--color-ink-3)] truncate ${PHONE_FIGURES}`}>
          {sourceLabel}
          {/* A New match names why it entered the watchdog's slice (migration 735). */}
          {d.change_kind === 'new' && (
            <span className="whitespace-nowrap">
              <span className="max-md:hidden">{' '}· </span>
              {matchReasonLabel(d)}
            </span>
          )}
          {d.prev_price_czk != null && d.trigger_price_czk != null && (
            <span className="font-mono tabular-nums text-[var(--color-ink-4)] whitespace-nowrap">
              <span className="max-md:hidden">{' '}· </span>
              {fmtCzk(d.prev_price_czk)} → {fmtCzk(d.trigger_price_czk)}
            </span>
          )}
        </p>
      </div>

      <RowTime iso={d.dispatched_at} />
    </>
  );
  return to === FEED ? (
    <button type="button" {...control}>{body}</button>
  ) : (
    <Link to={to} {...newTab} {...control} onAuxClick={onMiddleClick(markSeen)}>{body}</Link>
  );
}

/* When the row was dispatched; the exact time on hover. On the phone it closes
 * the first line, in the roll's mono. */
function RowTime({ iso }: { iso: string }) {
  return (
    <span
      className="shrink-0 text-[0.7rem] tracking-wide text-[var(--color-ink-4)] cursor-help mt-0.5 max-md:col-start-4 max-md:row-start-1 max-md:ml-auto max-md:mt-[5px] max-md:font-mono max-md:text-[11px] max-md:tracking-normal max-md:tabular-nums"
      title={fmtAbsolute(iso)}
    >
      {fmtRelative(iso)}
    </span>
  );
}

/* What each share kind's link says. */
const SHARE_META = {
  collection: { open: 'Open collection' },
  pipeline: { open: 'Open pipeline' },
  dashboard: { open: 'Open dashboard' },
} as const;

/* A share's sentence, from the sharer and the live name (migration 656: no text
 * is stored). Each part falls back on its own: the email once no share joins the
 * two accounts, the name once the thing is no longer readable. */
function shareSentence(d: WatchdogDispatch): string {
  const who = d.actor_email ?? 'Someone';
  switch (d.share_kind) {
    case 'collection':
      return d.share_name
        ? `${who} shared the collection "${d.share_name}" with you`
        : `${who} shared a collection with you`;
    case 'dashboard':
      return d.share_name
        ? `${who} shared the dashboard "${d.share_name}" with you`
        : `${who} shared a dashboard with you`;
    case 'pipeline':
      return `${who} shared their pipeline with you${
        d.share_mode ? ` (${d.share_mode === 'edit' ? 'edit' : 'view only'})` : ''
      }`;
    default:
      return 'Something was shared with you.';
  }
}

/* share: someone shared a collection, their pipeline or a Stats dashboard with
 * me (migrations 610, 641, 647: one kind, one renderer). A text row (shareSentence
 * names the owner and what was shared); pressing the text marks it read, the link
 * marks it read and opens what dispatchTarget names. Two sibling controls, never a
 * link inside a button; no link when there is nothing to open. */
function ShareNotificationRow({ dispatch: d }: { dispatch: WatchdogDispatch }) {
  const markSeen = useMarkSeen(d);
  const unread = d.seen_at == null;
  const unreadId = useId();
  const meta = CHANGE_META[d.change_kind];
  const kind = d.share_kind ? SHARE_META[d.share_kind] : null;
  const to = dispatchTarget(d);
  const newTab = useNewTabProps();

  return (
    <div className="flex items-start gap-3 py-3.5 max-md:flex-wrap max-md:gap-x-2.5 max-md:gap-y-1 max-md:px-4">
      <UnreadDot unread={unread} id={unreadId} color="var(--color-copper)" />
      <button
        type="button"
        onClick={markSeen}
        aria-describedby={unread ? unreadId : undefined}
        className="min-w-0 flex-1 text-left flex items-start gap-3 max-md:flex-col max-md:gap-1.5"
      >
        <span className="mt-0.5 inline-flex items-center gap-1.5 shrink-0 px-2 py-0.5 text-[0.7rem] rounded-[var(--radius-sm)] border border-[var(--color-rule)] bg-[var(--color-paper-2)] text-[var(--color-ink-2)]">
          <span aria-hidden className="w-1.5 h-1.5 rounded-full" style={{ background: meta.dot }} />
          {meta.label}
        </span>
        <span className="min-w-0 flex-1 text-sm text-[var(--color-ink)] break-words max-md:flex-none">
          {shareSentence(d)}
        </span>
      </button>
      {kind && to !== FEED && (
        <Link
          to={to}
          {...newTab}
          onClick={markSeen}
          onAuxClick={onMiddleClick(markSeen)}
          className="shrink-0 mt-0.5 text-[0.78rem] text-[var(--color-ink-2)] underline decoration-[var(--color-rule-strong)] underline-offset-2 hover:text-[var(--color-ink)] hover:decoration-[var(--color-ink-3)] transition-colors max-md:order-last max-md:-my-1.5 max-md:inline-flex max-md:min-h-11 max-md:basis-full max-md:items-center max-md:pl-4 max-md:text-[13px]"
        >
          {kind.open}
        </Link>
      )}
      <RowTime iso={d.dispatched_at} />
    </div>
  );
}

function EmptyState({ seen }: { seen: WatchdogSeenFilter }) {
  return (
    <div className="px-6 py-12 text-center border border-dashed border-[var(--color-rule)] rounded-[var(--radius-md)] max-md:px-4 max-md:py-8">
      <p className="text-[0.7rem] tracking-[0.18em] uppercase text-[var(--color-ink-3)]">
        {seen === 'unseen' ? 'Nothing unread' : 'No notifications yet'}
      </p>
      <p className="mt-2 text-sm text-[var(--color-ink-2)]">
        Turn on monitoring for a collection, or save a watchdog, and changes show
        up here. A collection, a pipeline or a dashboard someone shares with you
        is announced here too.
      </p>
    </div>
  );
}
