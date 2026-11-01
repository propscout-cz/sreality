/* Nové — the Phone app's start screen (#1766, design #1762): what came on the
 * market, newest first, as one continuous roll of photo cards cut into days.
 *
 * THE READ IS BROWSE'S, NOT A NEW ONE. `fetchListingsForCards` on `browse_list`
 * (the dismissal-aware twin by default, so hidden properties never appear),
 * keyset-paged 24 at a time, `CARD_COLS`, under Browse's own query key — so a
 * dismissal, a merge or a pipeline write sweeps this roll exactly as it sweeps
 * the Browse cards. Newest first is Browse's default sort; with one portal chip
 * on, `effectiveSort` orders by that portal's newest ad (MS19) and the days
 * follow that timestamp. The photos come through the shared hydration layer, the
 * cover chosen as the Browse card chooses it.
 *
 * The chips: "Filtry" (Browse's whole filter set in a sheet, its own lazy chunk),
 * "Vše" + the account's saved presets (#1761, per account since #1775); the
 * portals; "Skryté" switches the roll to the hidden properties only
 * (fetchDismissedCards — Browse has no hidden-only lens).
 *
 * THE VIEW LIVES IN THE URL, AS BROWSE'S DOES (lib/feed `feedSearch`): the
 * filters, the order and the chosen preset, so a Browse link opens the same
 * scope here and back. A bare /feed reopens what this device last showed. */

import { Suspense, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { useIsFetching, useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigationType, useSearchParams } from 'react-router-dom';

import FeedCard from '@/components/phone/FeedCard';
import BottomSheet from '@/components/phone/BottomSheet';
import DayRule from '@/components/phone/DayRule';
import { CHIP, CHIP_DIVIDER, CHIP_MD, CHIP_ROW, CHIP_SM, chipState } from '@/components/phone/chips';
import { PHOTOS_PER_CARD } from '@/components/phone/CardRoll';
import { filtersBeyond } from '@/components/phone/filterRows';
import { RollEmpty, RollSkeleton } from '@/components/phone/RollParts';
import { usePhoneHeaderAside } from '@/components/phone/PhoneShell';
import { SlidersIcon } from '@/components/phone/phoneIcons';
import { AlertIcon, EyeOffIcon } from '@/components/icons';
import InfiniteSentinel from '@/components/InfiniteSentinel';
import { listFilterPresets } from '@/lib/api';
import { browseKeys } from '@/lib/browseKeys';
import { useCardCoverTag } from '@/lib/browseLayout';
import {
  FEED_ALL_FILTERS,
  FEED_ALL_SCOPE,
  dayRuleLabel,
  feedFiltersForPreset,
  feedHiddenKey,
  feedSearch,
  feedTimestamp,
  feedViewOf,
  groupByDay,
  isNewestFirst,
  pragueClock,
  pragueDayKey,
  readFeedQuery,
  readFeedScope,
  sortLabel,
  writeFeedQuery,
  writeFeedScope,
  type FeedView,
} from '@/lib/feed';
import { filterById } from '@/lib/filterRegistry.generated';
import { filtersEqualForPreset } from '@/lib/filters';
import { czPlural } from '@/lib/format';
import { CardHydrationProvider } from '@/lib/hydration';
import type { KeysetCursor } from '@/lib/keyset';
import { lazyChunk } from '@/lib/lazyChunk';
import { cyclePortal, portalShort, type PortalChoice } from '@/lib/portals';
import {
  CARD_PAGE_SIZE,
  DEFAULT_SORT,
  dismissalKeys,
  fetchBrowseCount,
  fetchDismissedCards,
  fetchDismissedCount,
  fetchListingsForCards,
  filterPresetKeys,
  orderPortal as orderPortalOf,
  type CardRow,
  type DismissedCardRow,
} from '@/lib/queries';
import { useInfiniteList } from '@/lib/useInfiniteList';
import { useListHold } from '@/lib/useListHold';

/* Browse's whole filter set is large; the sheet loads on its first open. */
const FilterSheet = lazyChunk(() => import('@/components/phone/FilterSheet'));

/* Resume after this long away and the roll re-reads (#1763: refetch on foreground). */
const STALE_ON_RESUME_MS = 60_000;

const PORTALS: ReadonlyArray<string> = (filterById('portals')?.enum_values ?? []).map((o) => String(o.value));

/* Window scroll per roll, so "open a card → back" lands where it left. */
const scrollMemory = new Map<string, number>();

const PLACEHOLDER_VIEW: FeedView = { filters: FEED_ALL_FILTERS, sort: DEFAULT_SORT, presetId: null };

export default function Feed() {
  const qc = useQueryClient();
  const navType = useNavigationType();
  const coverTag = useCardCoverTag().value;
  const [searchParams, setSearchParams] = useSearchParams();
  const [hidden, setHidden] = useState(false);
  const [sheetOpen, setSheetOpen] = useState(false);

  const presetsQ = useQuery({ queryKey: filterPresetKeys.all, queryFn: listFilterPresets });
  const presets = useMemo(() => presetsQ.data?.data ?? [], [presetsQ.data]);

  /* A bare /feed (the tab bar, a cold start) reopens what this device last
   * showed: the whole view if it was modified, else its scope as that scope
   * reads NOW. A remembered preset deleted on the desktop falls back to Vše once
   * the list is in; until then nothing is read, so the roll never flashes the
   * wrong scope. */
  const bare = searchParams.toString() === '';
  const search = useMemo(() => {
    if (!bare) return searchParams.toString();
    const remembered = readFeedQuery();
    if (remembered != null) return remembered;
    const scope = readFeedScope();
    if (scope !== FEED_ALL_SCOPE && presetsQ.isPending) return null;
    const p = presets.find((x) => x.id === scope) ?? null;
    return feedSearch({ filters: feedFiltersForPreset(p), sort: DEFAULT_SORT, presetId: p?.id ?? null }).toString();
  }, [bare, searchParams, presets, presetsQ.isPending]);
  const waitingForPreset = search == null;
  useEffect(() => {
    if (bare && search != null) setSearchParams(new URLSearchParams(search), { replace: true });
  }, [bare, search, setSearchParams]);

  const view = useMemo(() => (search == null ? PLACEHOLDER_VIEW : feedViewOf(new URLSearchParams(search))), [search]);
  const { filters, sort } = view;
  /* A filter is not a destination: every write replaces (useUrlFilters' rule). */
  const setView = (next: FeedView) => setSearchParams(feedSearch(next), { replace: true });

  /* The chosen chip and what it reads by itself — the baseline every "beyond the
   * scope" mark is measured from. Unknown while the preset list loads. */
  const preset = view.presetId ? (presets.find((p) => p.id === view.presetId) ?? null) : null;
  const baseKnown = !view.presetId || !presetsQ.isPending;
  const activeScope = preset ? preset.id : FEED_ALL_SCOPE;
  const base = useMemo(() => feedFiltersForPreset(preset), [preset]);
  const beyond = baseKnown && !waitingForPreset ? filtersBeyond(filters, base) : 0;
  const modified = baseKnown && !waitingForPreset && !filtersEqualForPreset(filters, base);
  const newestFirst = isNewestFirst(sort);

  /* Remember the view: its scope always, the whole query only while it differs
   * from that scope's own (lib/feed FEED_QUERY_KEY). */
  useEffect(() => {
    if (bare || search == null || !baseKnown) return;
    writeFeedScope(activeScope);
    writeFeedQuery(beyond > 0 || !newestFirst ? search : null);
  }, [bare, search, baseKnown, activeScope, beyond, newestFirst]);

  const orderPortal = orderPortalOf(filters, sort);

  const feedKey = browseKeys.cards(filters, sort);
  const hiddenKey = useMemo(() => feedHiddenKey(sort), [sort]);

  const feed = useInfiniteList<CardRow>({
    queryKey: feedKey,
    queryFn: (cursor, signal) =>
      fetchListingsForCards(filters, sort, cursor as KeysetCursor | null, { signal }),
    pageSize: CARD_PAGE_SIZE,
    getRowId: (r) => r.property_id,
    enabled: !hidden && !waitingForPreset,
    gcTime: 10 * 60_000,
  });
  const hiddenRoll = useInfiniteList<DismissedCardRow>({
    queryKey: hiddenKey,
    queryFn: (cursor, signal) => fetchDismissedCards(cursor as KeysetCursor | null, { signal }),
    /* A page can come back short (a hidden property Browse no longer serves), so
     * the end is decided by the page's cursor, not its length. */
    pageSize: 1,
    getRowId: (r) => r.property_id,
    enabled: hidden,
  });
  const list = hidden ? hiddenRoll : feed;
  const activeKey = hidden ? hiddenKey : feedKey;
  const rows: CardRow[] = list.rows;
  /* A change made elsewhere moves the roll at once, but never under a finger
   * (ruling Q3(c)); the count in the header re-reads regardless. */
  const hand = useListHold([activeKey]);

  const hiddenCountQ = useQuery({
    queryKey: dismissalKeys.count,
    queryFn: fetchDismissedCount,
    staleTime: 60_000,
  });

  const fetching = useIsFetching({ queryKey: activeKey, exact: true }) > 0;
  const refreshing = fetching && !list.isLoading && !list.isFetchingNextPage;
  const updatedAt = qc.getQueryState(activeKey)?.dataUpdatedAt || Date.now();

  const refetchRef = useRef(list.refetch);
  refetchRef.current = list.refetch;
  const activeKeyRef = useRef(activeKey);
  activeKeyRef.current = activeKey;
  useEffect(() => {
    const onVisible = () => {
      if (document.visibilityState !== 'visible') return;
      const at = qc.getQueryState(activeKeyRef.current)?.dataUpdatedAt ?? 0;
      if (Date.now() - at > STALE_ON_RESUME_MS) refetchRef.current();
    };
    document.addEventListener('visibilitychange', onVisible);
    return () => document.removeEventListener('visibilitychange', onVisible);
  }, [qc]);

  const todayKey = pragueDayKey(Date.now());
  const groups = useMemo(
    () =>
      groupByDay(rows, (r) =>
        hidden ? (r as DismissedCardRow).dismissed_at : feedTimestamp(r, orderPortal),
      ),
    [rows, hidden, orderPortal],
  );

  /* "5 nových dnes" — only once the whole of today is loaded; past the scope's
   * own filters, or in another order, the scope's total instead. */
  const showTotal = beyond > 0 || !newestFirst;
  const totalQ = useQuery({
    queryKey: browseKeys.count(filters),
    queryFn: ({ signal }) => fetchBrowseCount(filters, { signal }),
    enabled: showTotal && !hidden && !waitingForPreset,
    staleTime: 60_000,
  });
  const todayCount = groups[0]?.dayKey === todayKey ? groups[0].rows.length : 0;
  const todayComplete = !list.isLoading && (!list.hasNextPage || groups.length > 1 || todayCount === 0);
  const total = totalQ.data;
  usePhoneHeaderAside(
    hidden || list.isError
      ? null
      : showTotal
        ? total == null
          ? null
          : `${total.toLocaleString('cs-CZ')} ${czPlural(total, 'nemovitost', 'nemovitosti', 'nemovitostí')}`
        : todayComplete && rows.length > 0
          ? `${todayCount} ${czPlural(todayCount, 'nový', 'nové', 'nových')} dnes`
          : null,
  );

  /* Scroll memory, per roll. */
  const scrollKey = hidden ? 'hidden' : (search ?? '');
  const restored = useRef(false);
  const ready = rows.length > 0;
  useEffect(() => {
    if (!ready || restored.current) return;
    restored.current = true;
    const y = navType === 'POP' ? scrollMemory.get(scrollKey) : undefined;
    if (y) requestAnimationFrame(() => window.scrollTo(0, y));
  }, [ready, navType, scrollKey]);
  useEffect(() => () => void scrollMemory.set(scrollKey, window.scrollY), [scrollKey]);

  const listingIds = useMemo(() => rows.map((r) => r.listing_id), [rows]);

  const show = (next: FeedView) => {
    setView(next);
    setHidden(false);
    window.scrollTo(0, 0);
  };
  /* A scope chip loads that scope as it stands, newest first (Q17); like a
   * Browse preset click it applies every filter the preset stores, the lenses
   * included (ruling 2026-10-22). */
  const chooseScope = (next: string) => {
    const p = next === FEED_ALL_SCOPE ? null : (presets.find((x) => x.id === next) ?? null);
    show({ filters: feedFiltersForPreset(p), sort: DEFAULT_SORT, presetId: p?.id ?? null });
  };
  const choosePortals = (next: PortalChoice) => show({ ...view, filters: { ...filters, ...next } });
  const clearBeyond = () => show({ filters: base, sort: DEFAULT_SORT, presetId: view.presetId });
  const shown: PortalChoice = hidden
    ? { portals: [], portalsExcluded: [], portalsExcludedActiveOnly: false }
    : {
        portals: filters.portals,
        portalsExcluded: filters.portalsExcluded,
        portalsExcludedActiveOnly: filters.portalsExcludedActiveOnly,
      };
  /* The Filtry chip counts what the view adds to its scope (not under Skryté). */
  const shownBeyond = hidden ? 0 : beyond;
  /* The chosen chip carries a trailing "·" once the view has left its filters. */
  const scopeChip = (id: string, name: string) => {
    const on = !hidden && activeScope === id;
    const marked = on && modified;
    return (
      <button
        key={id}
        type="button"
        aria-pressed={on}
        onClick={() => chooseScope(id)}
        className={`${CHIP} ${CHIP_MD} ${chipState(on)}`}
      >
        {name}
        {marked && (
          <>
            <span aria-hidden className="-ml-1 text-[var(--color-ink)]">
              ·
            </span>
            <span className="sr-only"> (upraveno)</span>
          </>
        )}
      </button>
    );
  };
  const toggleHidden = () => {
    if (!hidden) void qc.invalidateQueries({ queryKey: hiddenKey, exact: true });
    setHidden((h) => !h);
    window.scrollTo(0, 0);
  };

  const refresh = { onRefresh: () => list.refetch(), refreshing };
  const hiddenCount = hiddenCountQ.data ?? null;

  return (
    <div className="pt-3">
      <div className={CHIP_ROW} role="group" aria-label="Předvolby">
        <button
          type="button"
          onClick={() => setSheetOpen(true)}
          aria-haspopup="dialog"
          aria-label={shownBeyond > 0 ? `Filtry (${shownBeyond} navíc)` : 'Filtry'}
          className={`${CHIP} ${CHIP_MD} ${chipState(shownBeyond > 0)}`}
        >
          <SlidersIcon className="h-3.5 w-3.5" />
          Filtry
          {shownBeyond > 0 && (
            <span className="font-mono text-[11px] tabular-nums text-[var(--color-ink)]">{shownBeyond}</span>
          )}
        </button>
        <span aria-hidden className={CHIP_DIVIDER} />
        {scopeChip(FEED_ALL_SCOPE, 'Vše')}
        {presets.map((p) => scopeChip(p.id, p.name))}
        {presetsQ.isError && (
          <button
            type="button"
            onClick={() => void presetsQ.refetch()}
            className={`${CHIP} ${CHIP_MD} border-[var(--color-rule-strong)] text-[var(--color-brick)]`}
          >
            <AlertIcon className="h-3.5 w-3.5" />
            Předvolby se nenačetly
          </button>
        )}
      </div>
      <div className={`${CHIP_ROW} mt-2`} role="group" aria-label="Portály">
        {PORTALS.map((p) => {
          /* Off → on it → not on it ("−"); pressed either way, never colour (chips.ts). */
          const out = shown.portalsExcluded.includes(p);
          const on = out || shown.portals.includes(p);
          return (
            <button
              key={p}
              type="button"
              aria-pressed={on}
              aria-label={out ? `${portalShort(p)} (vyloučeno)` : undefined}
              onClick={() => choosePortals(cyclePortal(shown, p))}
              className={`${CHIP} ${CHIP_SM} ${chipState(on)}`}
            >
              {out ? `−${portalShort(p)}` : portalShort(p)}
            </button>
          );
        })}
        {shown.portalsExcluded.length > 0 && (
          <button
            type="button"
            aria-pressed={shown.portalsExcludedActiveOnly}
            onClick={() =>
              choosePortals({ ...shown, portalsExcludedActiveOnly: !shown.portalsExcludedActiveOnly })
            }
            className={`${CHIP} ${CHIP_SM} ${chipState(shown.portalsExcludedActiveOnly)}`}
          >
            jen aktivní
          </button>
        )}
        <span aria-hidden className={CHIP_DIVIDER} />
        <button
          type="button"
          aria-pressed={hidden}
          onClick={toggleHidden}
          className={`${CHIP} ${CHIP_SM} ${chipState(hidden)}`}
        >
          <EyeOffIcon className="h-3.5 w-3.5" />
          Skryté
          {hiddenCount != null && (
            <span
              className={`font-mono text-[11px] tabular-nums ${hidden ? 'text-[var(--color-ink-2)]' : 'text-[var(--color-ink-3)]'}`}
            >
              {hiddenCount.toLocaleString('cs-CZ')}
            </span>
          )}
        </button>
      </div>

      <CardHydrationProvider listingIds={listingIds} renders={{ photos: PHOTOS_PER_CARD }}>
        <div {...hand}>
          <Roll
            hidden={hidden}
            loading={list.isLoading || waitingForPreset}
            error={list.isError}
            onRetry={() => list.refetch()}
            rows={rows}
            groups={groups}
            todayKey={todayKey}
            clock={pragueClock(updatedAt)}
            order={newestFirst ? null : sortLabel(sort)}
            refresh={refresh}
            coverTag={coverTag}
            onClearFilters={beyond > 0 || !newestFirst ? clearBeyond : undefined}
          />
          {rows.length > 0 && (
            <InfiniteSentinel
              onReach={list.fetchNextPage}
              hasNextPage={list.hasNextPage}
              isFetchingNextPage={list.isFetchingNextPage}
              loadedCount={rows.length}
              total={null}
              loadingLabel="Načítám…"
              endLabel={hidden ? 'Víc skrytých není.' : 'Víc inzerátů v tomto výběru není.'}
            />
          )}
        </div>
      </CardHydrationProvider>

      {sheetOpen && (
        <Suspense
          fallback={
            <BottomSheet label="Filtry" closeLabel="Zavřít" onClose={() => setSheetOpen(false)} footer={<div className="h-11" />}>
              <p role="status" className="px-4 py-3 text-[14px] text-[var(--color-ink-3)]">
                Načítám filtry…
              </p>
            </BottomSheet>
          }
        >
          <FilterSheet
            filters={filters}
            sort={sort}
            base={base}
            preset={preset}
            onApply={show}
            onClose={() => setSheetOpen(false)}
          />
        </Suspense>
      )}
    </div>
  );
}

function Roll({
  hidden,
  loading,
  error,
  onRetry,
  rows,
  groups,
  todayKey,
  clock,
  order,
  refresh,
  coverTag,
  onClearFilters,
}: {
  hidden: boolean;
  loading: boolean;
  error: boolean;
  onRetry: () => void;
  rows: CardRow[];
  groups: ReturnType<typeof groupByDay<CardRow>>;
  todayKey: string;
  clock: string;
  /* Another order than newest first: one continuous roll under its name. */
  order: string | null;
  refresh: { onRefresh: () => void; refreshing: boolean };
  coverTag: ReturnType<typeof useCardCoverTag>['value'];
  /* Present while the view is past its scope's own filters or order. */
  onClearFilters?: () => void;
}) {
  const topLabel = hidden ? 'Skryté' : order ? `${order} · ${clock}` : `Dnes ${clock}`;

  if (loading) {
    return (
      <>
        <DayRule label={hidden ? 'Skryté' : 'Dnes'} />
        <RollSkeleton />
      </>
    );
  }

  if (error) {
    return (
      <>
        <DayRule label={topLabel} {...refresh} />
        <RollEmpty
          title={hidden ? 'Skryté se nepodařilo načíst.' : 'Nové se nepodařilo načíst.'}
          detail="Spojení nebo databáze teď neodpověděly. Zkuste to znovu."
          action={{ label: 'Zkusit znovu', onClick: onRetry }}
        />
      </>
    );
  }

  if (rows.length === 0) {
    return (
      <>
        <DayRule label={topLabel} {...refresh} />
        {hidden ? (
          <RollEmpty
            title="Nic skrytého."
            detail="Nemovitost skryjete okem na kartě; odtud ji zase vrátíte."
          />
        ) : (
          <RollEmpty
            title="V tomhle výběru nic není."
            detail="Předvolba ani filtry teď nenacházejí žádnou nemovitost."
            action={onClearFilters ? { label: 'Vymazat filtry', onClick: onClearFilters } : undefined}
          />
        )}
      </>
    );
  }

  const items: ReactNode[] = [];
  const card = (r: CardRow) => <FeedCard key={r.property_id} r={r} coverTag={coverTag} />;
  if (hidden) {
    groups.forEach((g, i) => {
      const label = `Skryto ${dayRuleLabel(g.dayKey, todayKey).toLowerCase()}`;
      items.push(<DayRule key={`rule-${g.dayKey}-${i}`} label={label} {...(i === 0 ? refresh : {})} />);
      g.rows.forEach((r) => items.push(card(r)));
    });
  } else if (order) {
    items.push(<DayRule key="rule-top" label={topLabel} {...refresh} />);
    rows.forEach((r) => items.push(card(r)));
  } else {
    items.push(<DayRule key="rule-top" label={topLabel} {...refresh} />);
    groups.forEach((g, i) => {
      if (!(i === 0 && g.dayKey === todayKey)) {
        if (i === 0) {
          items.push(
            <p key="none-today" className="px-4 pb-3 text-[13px] text-[var(--color-ink-3)]">
              Dnes zatím nic nového.
            </p>,
          );
        }
        items.push(<DayRule key={`rule-${g.dayKey}-${i}`} label={dayRuleLabel(g.dayKey, todayKey)} />);
      }
      g.rows.forEach((r) => items.push(card(r)));
    });
  }
  return <div>{items}</div>;
}
