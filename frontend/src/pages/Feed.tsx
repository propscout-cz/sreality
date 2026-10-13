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
 * The chips: "Vše" + the account's saved presets (#1761, per account since
 * #1775), remembered per device; the portals; "Skryté" switches the roll to the
 * hidden properties only (fetchDismissedCards — Browse has no hidden-only lens). */

import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { useIsFetching, useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigationType } from 'react-router-dom';

import FeedCard from '@/components/phone/FeedCard';
import DayRule from '@/components/phone/DayRule';
import { CHIP, CHIP_DIVIDER, CHIP_MD, CHIP_ROW, CHIP_SM, chipState } from '@/components/phone/chips';
import { PHOTOS_PER_CARD } from '@/components/phone/CardRoll';
import { RollEmpty, RollSkeleton } from '@/components/phone/RollParts';
import { usePhoneHeaderAside } from '@/components/phone/PhoneShell';
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
  feedTimestamp,
  groupByDay,
  pragueClock,
  pragueDayKey,
  readFeedScope,
  withPortals,
  writeFeedScope,
} from '@/lib/feed';
import { filterById } from '@/lib/filterRegistry.generated';
import { czPlural } from '@/lib/format';
import { CardHydrationProvider } from '@/lib/hydration';
import type { KeysetCursor } from '@/lib/keyset';
import { cyclePortal, NO_PORTALS, portalShort, type PortalChoice } from '@/lib/portals';
import {
  CARD_PAGE_SIZE,
  DEFAULT_SORT,
  dismissalKeys,
  fetchDismissedCards,
  fetchDismissedCount,
  fetchListingsForCards,
  filterPresetKeys,
  orderPortal as orderPortalOf,
  type CardRow,
  type DismissedCardRow,
} from '@/lib/queries';
import { useInfiniteList } from '@/lib/useInfiniteList';

/* The hidden roll is keyed under Browse's `cards` root so a restore — which
 * re-reads every Browse list — re-reads it too; `showDismissed: true` keeps a
 * dismissal from patching rows out of it. */
const HIDDEN_KEY_FILTERS = { ...FEED_ALL_FILTERS, showDismissed: true };

/* Resume after this long away and the roll re-reads (#1763: refetch on foreground). */
const STALE_ON_RESUME_MS = 60_000;

const PORTALS: ReadonlyArray<string> = (filterById('portals')?.enum_values ?? []).map((o) => String(o.value));

/* Window scroll per roll, so "open a card → back" lands where it left. */
const scrollMemory = new Map<string, number>();

export default function Feed() {
  const qc = useQueryClient();
  const navType = useNavigationType();
  const coverTag = useCardCoverTag().value;
  const [scope, setScope] = useState(readFeedScope);
  const [portals, setPortals] = useState<PortalChoice | null>(null);
  const [hidden, setHidden] = useState(false);

  const presetsQ = useQuery({ queryKey: filterPresetKeys.all, queryFn: listFilterPresets });
  const presets = useMemo(() => presetsQ.data?.data ?? [], [presetsQ.data]);
  const preset = scope === FEED_ALL_SCOPE ? null : (presets.find((p) => p.id === scope) ?? null);
  /* A remembered preset deleted on the desktop falls back to Vše once the list
   * is in; until then nothing is read, so the roll never flashes the wrong scope. */
  const waitingForPreset = scope !== FEED_ALL_SCOPE && presetsQ.isPending;
  const activeScope = preset ? scope : FEED_ALL_SCOPE;

  const filters = useMemo(() => withPortals(feedFiltersForPreset(preset), portals), [preset, portals]);
  const sort = DEFAULT_SORT;
  const orderPortal = orderPortalOf(filters, sort);

  const feedKey = browseKeys.cards(filters, sort);
  const hiddenKey = useMemo(() => [...browseKeys.cards(HIDDEN_KEY_FILTERS, sort), 'dismissed'], [sort]);

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

  /* "5 nových dnes" — only once the whole of today is loaded. */
  const todayCount = groups[0]?.dayKey === todayKey ? groups[0].rows.length : 0;
  const todayComplete = !list.isLoading && (!list.hasNextPage || groups.length > 1 || todayCount === 0);
  usePhoneHeaderAside(
    !hidden && !list.isError && todayComplete && rows.length > 0
      ? `${todayCount} ${czPlural(todayCount, 'nový', 'nové', 'nových')} dnes`
      : null,
  );

  /* Scroll memory, per roll. */
  const scrollKey = hidden
    ? 'hidden'
    : [activeScope, filters.portals, filters.portalsExcluded, filters.portalsExcludedActiveOnly].join('|');
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

  const chooseScope = (next: string) => {
    setScope(next);
    writeFeedScope(next);
    setPortals(null);
    setHidden(false);
    window.scrollTo(0, 0);
  };
  const choosePortals = (next: PortalChoice) => {
    setPortals(next);
    setHidden(false);
    window.scrollTo(0, 0);
  };
  const shown: PortalChoice = hidden ? NO_PORTALS : {
    portals: filters.portals,
    portalsExcluded: filters.portalsExcluded,
    portalsExcludedActiveOnly: filters.portalsExcludedActiveOnly,
  };
  const portalsOn = shown.portals.length > 0 || shown.portalsExcluded.length > 0;
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
          aria-pressed={!hidden && activeScope === FEED_ALL_SCOPE}
          onClick={() => chooseScope(FEED_ALL_SCOPE)}
          className={`${CHIP} ${CHIP_MD} ${chipState(!hidden && activeScope === FEED_ALL_SCOPE)}`}
        >
          Vše
        </button>
        {presets.map((p) => (
          <button
            key={p.id}
            type="button"
            aria-pressed={!hidden && activeScope === p.id}
            onClick={() => chooseScope(p.id)}
            className={`${CHIP} ${CHIP_MD} ${chipState(!hidden && activeScope === p.id)}`}
          >
            {p.name}
          </button>
        ))}
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
        <Roll
          hidden={hidden}
          loading={list.isLoading || waitingForPreset}
          error={list.isError}
          onRetry={() => list.refetch()}
          rows={rows}
          groups={groups}
          todayKey={todayKey}
          clock={pragueClock(updatedAt)}
          refresh={refresh}
          coverTag={coverTag}
          portalsOn={portalsOn}
          onClearPortals={() => setPortals(NO_PORTALS)}
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
      </CardHydrationProvider>
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
  refresh,
  coverTag,
  portalsOn,
  onClearPortals,
}: {
  hidden: boolean;
  loading: boolean;
  error: boolean;
  onRetry: () => void;
  rows: CardRow[];
  groups: ReturnType<typeof groupByDay<CardRow>>;
  todayKey: string;
  clock: string;
  refresh: { onRefresh: () => void; refreshing: boolean };
  coverTag: ReturnType<typeof useCardCoverTag>['value'];
  portalsOn: boolean;
  onClearPortals: () => void;
}) {
  const topLabel = hidden ? 'Skryté' : `Dnes ${clock}`;

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
            detail="Předvolba ani portály teď nenacházejí žádnou nemovitost."
            action={portalsOn ? { label: 'Zrušit filtr portálů', onClick: onClearPortals } : undefined}
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
