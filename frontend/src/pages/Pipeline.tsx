import { Suspense, useCallback, useEffect, useId, useMemo, useRef, useState, type ReactNode } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import {
  DndContext,
  DragOverlay,
  KeyboardSensor,
  PointerSensor,
  pointerWithin,
  rectIntersection,
  useDroppable,
  useSensor,
  useSensors,
  type CollisionDetection,
  type DragEndEvent,
  type DragStartEvent,
} from '@dnd-kit/core';
import {
  archivePipelineStage,
  createPipelineStage,
  reorderPipelineStages,
  sharePipeline,
  unsharePipeline,
  updatePipelineStage,
} from '@/lib/api';
import {
  ActingAs,
  actingKey,
  actOpts,
  resetActiveBoard,
  SHARE_MODE_LABEL,
  shareOwnerName,
  useActAs,
  useActingReadOnly,
  type ActiveBoard,
} from '@/lib/acting';
import { useBoardSelection, useSharedBoardGuard } from '@/lib/pipelineShares';
import { revalidateTables } from '@/lib/curationSync';
import { trimDraft, useServerDraft } from '@/lib/useServerDraft';
import { useCollectionMembers, useCollections } from '@/lib/collectionCache';
import { useTagReads } from '@/lib/useCardTags';
import { BOARD_GESTURE_HELD, matchesDistricts, pipelineKeys, pipelineShareKeys } from '@/lib/queries';
import { holdReads, type Hold } from '@/lib/revalidate';
import Panel, { PANEL_ACTION } from '@/components/Panel';
import ShareBlock from '@/components/ShareBlock';
import { TwoStep } from '@/components/TwoStep';
import BoardSwitcher from '@/components/pipeline/BoardSwitcher';
import { useNewPropertyDialog } from '@/components/NewPropertyProvider';
import DelegatedStamp from '@/components/DelegatedStamp';
import { CURATED_SETS, matchesScope, normalizeScope, type SetScope } from '@/lib/setScope';
import { ActiveFilters, FilterMenu, type ActiveFilter } from '@/components/pipeline/FilterMenu';
import { offerCuratedSet } from '@/lib/curatedSetOffer';
import {
  CuratedSetFilter,
  collectionElement,
  joinReads,
  tagElement,
} from '@/components/filter-controls/CuratedSetFilter';
import { usePipelineBoard, usePipelineStages } from '@/lib/pipelineReads';
import { usePhoneShell } from '@/lib/phoneShell';
import PhonePipelineSkeleton from '@/components/phone/PhonePipelineSkeleton';
import { lazyChunk } from '@/lib/lazyChunk';
import { usePipelineCard } from '@/lib/usePipelineCard';
import { CardHydrationProvider } from '@/lib/hydration';
import { LocationTypeahead } from '@/components/filter-controls/LocationTypeahead';
import { MultiselectChips } from '@/components/filter-controls/MultiselectChips';
import { type EnumOptionLite } from '@/components/filter-controls/types';
import { Segmented } from '@/components/controls';
import { type ListingStatus } from '@/lib/filters';
import { filterById } from '@/lib/filterRegistry.generated';
import TagColorPicker from '@/components/TagColorPicker';
import ErrorBanner from '@/components/ErrorBanner';
import { readFailed } from '@/components/ReadFailedMark';
import { FunnelIcon, InfoIcon } from '@/components/icons';
import SizeToggle, {
  LargeImageGlyph,
  MediumImageGlyph,
  SmallImageGlyph,
} from '@/components/SizeToggle';
import {
  PIPELINE_CARD_GEOMETRY,
  PIPELINE_CARD_SIZE_LABELS,
  PIPELINE_CARD_SIZES,
  PIPELINE_CLOSED_LANE_WIDTH,
  usePipelineCardSize,
  type PipelineCardSize,
} from '@/lib/pipelineCardSize';
import BoardCard, {
  CardFace,
  CARD_PREFIX,
  STAGE_PREFIX,
} from '@/components/pipeline/BoardCard';
import { sortParamOf } from '@/lib/cardSort';
import { PIPELINE_SORT_OPTIONS, sortPipelineCards } from '@/lib/pipelineSort';
import { usePipelineViewState } from '@/lib/pipelineState';
import {
  type PipelineBoardCard,
  type PipelineShare,
  type PipelineShares,
  type PipelineStage,
  type TagColor,
} from '@/lib/types';

/* The bar's vocabulary comes off the generated filter registry, so the
 * Byty/Domy/… and Vše/Aktivní/Neaktivní labels never drift from Browse's. */
const enumOptions = (id: string): EnumOptionLite[] =>
  (filterById(id)?.enum_values ?? []).map((o) => ({
    value: String(o.value),
    label: o.label_cs,
  }));

/* `status` reads against the property-grain `is_active` rollup (bool_or over
 * child listings, rule #15/#20) that fetchPipelineBoard already selects. */
const STATUS_OPTIONS = enumOptions('status').map((o) => ({
  value: o.value as ListingStatus,
  label: o.label,
}));
const CATEGORY_MAIN_OPTIONS = enumOptions('category_main');

/* One stable empty board, so every memo below can depend on `cards` itself. */
const NO_CARDS: PipelineBoardCard[] = [];

/* A chunk of its own: the desktop never downloads it, and the first-visit
 * budget (scripts/bundle-budget.mjs) does not pay for it. */
const PhonePipeline = lazyChunk(() => import('@/components/phone/PhonePipeline'));

/* One route, two faces: under the phone shell the stages are swiped pages
 * (#1768 — no drag, no stage management, #1760 Q8/Q13), and the kanban below,
 * with its drag sensors and stage editor, is never mounted. Both read the same
 * two cache entries (lib/pipelineReads) and write through usePipelineCard. */
export default function Pipeline() {
  return usePhoneShell() ? (
    <Suspense fallback={<PhonePipelineSkeleton />}>
      <PhonePipeline />
    </Suspense>
  ) : (
    <PipelineBoardPage />
  );
}

/* The active board (lib/acting, design §3.5): "Můj pipeline", or a board
 * shared with me rendered inside <ActingAs> — every read and write below is
 * then the owner's. Keyed by the board, so a switch starts the page afresh (no
 * stage editor left open over someone else's board). */
function PipelineBoardPage() {
  const { active, sharesQ } = useBoardSelection();
  /* A collection / tag scope names one account's set ids: on another board
   * they would match the owner's sets by accident, or empty every column. Any
   * change of board (the switcher, a revoke, a leave) drops both. */
  const { clearScopes } = usePipelineViewState();
  const board = active?.account_id ?? null;
  const shownBoard = useRef(board);
  useEffect(() => {
    if (shownBoard.current === board) return;
    shownBoard.current = board;
    clearScopes();
  }, [board, clearScopes]);
  return (
    <ActingAs share={active}>
      <BoardPage key={active?.account_id ?? 'own'} active={active} shares={sharesQ.data ?? null} />
    </ActingAs>
  );
}

function BoardPage({ active, shares }: { active: ActiveBoard; shares: PipelineShares | null }) {
  /* A read share sees the board only (ruling 2): no drag, stage menu, trash or
   * stage editor, and no tag / collection decorations — the owner's curation is
   * not readable under it, so those reads are not even made. */
  const readOnly = useActingReadOnly();
  const newProperty = useNewPropertyDialog();
  const [manage, setManage] = useState(false);
  const [sharing, setSharing] = useState(false);
  /* Filters AND sort live in the URL (lib/pipelineState) using Browse's own
   * param vocabulary, so a filtered/sorted board is linkable and survives a
   * reload. `manage` stays local — it's a transient editor toggle, not a view. */
  const {
    status,
    types,
    districts,
    scopes,
    sort,
    setStatus,
    setTypes,
    setDistricts,
    setScope,
    setSort,
    reset,
  } = usePipelineViewState();
  /* Card size is a workspace preference, NOT part of the URL view state above:
   * a shared link carries which deals to look at, not how this browser likes
   * its photos. Same split Browse draws for its own image-size switch. */
  const cardSize = usePipelineCardSize();
  const stagesQ = usePipelineStages();
  const boardQ = usePipelineBoard();

  /* The two curation lenses (rule #18), on the keys the rest of the app
   * already shares — the board pays for them only on a cold visit. */
  const { collections, query: collectionsQ } = useCollections({ enabled: !readOnly });
  const membersQ = useCollectionMembers({ enabled: !readOnly });
  const { tagsQ, membersQ: tagMembersQ } = useTagReads({ enabled: !readOnly });

  useSharedBoardGuard(active, stagesQ.data);
  /* Undefined while loading or errored — NOT an empty map (the fail-open
   * contract, docs/architecture.md rule #22). */
  const members = membersQ.data;
  const tagMembers = tagMembersQ.data;
  const collectionsRead = joinReads(membersQ, collectionsQ);
  const tagsRead = joinReads(tagMembersQ, tagsQ);

  const stages = stagesQ.data ?? [];
  const cards = boardQ.data ?? NO_CARDS;
  /* P22: the board draws the live stages as columns and folds the terminal ones
   * into the Uzavřené lane, each half in the server's position order. The stage
   * manager keeps the full list, so ticking "konec" there moves a stage between
   * the two. */
  const lanes = useMemo(() => {
    const all = stagesQ.data ?? [];
    return { live: all.filter((s) => !s.is_terminal), closed: all.filter((s) => s.is_terminal) };
  }, [stagesQ.data]);

  // Client-side (rule #22) as ONE list the board, the count and Reset share.
  // A curation clause is skipped while its member map is unresolved.
  const predicates = useMemo(() => {
    const out: Array<(c: PipelineBoardCard) => boolean> = [];
    if (types.size > 0) {
      out.push((c) => c.category_main != null && types.has(c.category_main));
    }
    if (districts.length > 0) out.push((c) => matchesDistricts(c, districts));
    if (status !== 'any') out.push((c) => c.is_active === (status === 'active'));
    const sets = [
      ['collections', members],
      ['tags', tagMembers],
    ] as const;
    for (const [name, map] of sets) {
      const scope = scopes[name];
      if (map && scope) {
        out.push((c) => matchesScope(map.get(c.property_id), scope, CURATED_SETS[name].inRule));
      }
    }
    return out;
  }, [types, districts, status, members, tagMembers, scopes]);

  const filtersActive = predicates.length > 0;
  const filteredCards = useMemo(
    () => cards.filter((c) => predicates.every((p) => p(c))),
    [cards, predicates],
  );

  /* One pass over the board for everything the bar offers: the types on it,
   * whether a delisted card is on it, and which collections / tags hold one
   * (lib/curatedSetOffer — one rule for both). */
  const bar = useMemo(() => {
    const present = new Set<string>();
    let inactive = 0;
    for (const c of cards) {
      if (c.category_main) present.add(c.category_main);
      if (!c.is_active) inactive += 1;
    }
    const offered = offerCuratedSet(cards, members, collections, scopes.collections);
    const tags = offerCuratedSet(cards, tagMembers, tagsQ.data?.data ?? [], scopes.tags);
    return {
      types: CATEGORY_MAIN_OPTIONS.filter((o) => present.has(o.value)),
      hasInactive: inactive > 0,
      collections: offered.options.map(collectionElement),
      showCollections: offered.show,
      tags: tags.options.map(tagElement),
      showTags: tags.show,
    };
  }, [cards, members, collections, tagMembers, tagsQ.data, scopes]);

  /* The decoration cohort: the representative listing of every card ON the
   * board (not just the filtered view — filtering is client-side and instant,
   * so hydrating the full board once keeps a filter toggle free instead of
   * re-keying the enrichment query on every chip click). */
  const visibleListingIds = useMemo(
    () => cards.map((c) => c.listing_id).filter((id): id is number => id != null),
    [cards],
  );

  const byStage = useMemo(() => {
    const m = new Map<number, PipelineBoardCard[]>();
    for (const s of stagesQ.data ?? []) m.set(s.id, []);
    for (const c of filteredCards) {
      const bucket = m.get(c.stage_id);
      if (bucket) bucket.push(c);
    }
    /* Sort WITHIN each column, not across the board — a kanban's vertical axis
     * is per-column. Every comparator tiebreaks on property_id, so equal keys
     * (and colliding board_positions, which live data has) hold a stable order
     * across refetches instead of reshuffling. */
    for (const [id, bucket] of m) m.set(id, sortPipelineCards(bucket, sort));
    return m;
  }, [stagesQ.data, filteredCards, sort]);

  /* Every choice that is on, by name, each with its own × (the menus only count them). */
  const activeFilters: ActiveFilter[] = [];
  if (status !== 'any') {
    activeFilters.push({
      key: 'status',
      label: STATUS_OPTIONS.find((o) => o.value === status)?.label ?? status,
      onRemove: () => setStatus('any'),
    });
  }
  for (const t of types) {
    activeFilters.push({
      key: `type:${t}`,
      label: CATEGORY_MAIN_OPTIONS.find((o) => o.value === t)?.label ?? t,
      onRemove: () => setTypes([...types].filter((x) => x !== t)),
    });
  }
  for (const [name, word, elements] of [
    ['collections', 'Kolekce', bar.collections],
    ['tags', 'Štítek', bar.tags],
  ] as const) {
    const scope = scopes[name];
    if (!scope) continue;
    const labelOf = (id: number) => elements.find((e) => e.id === id)?.label ?? `#${id}`;
    if (scope.set === 'out') {
      activeFilters.push({ key: `${name}:none`, label: CURATED_SETS[name].notInLabel, out: true, onRemove: () => setScope(name, null) });
    }
    for (const id of scope.in) {
      activeFilters.push({
        key: `${name}:in:${id}`,
        label: `${word}: ${labelOf(id)}`,
        onRemove: () => setScope(name, normalizeScope({ ...scope, in: scope.in.filter((x) => x !== id) })),
      });
    }
    for (const id of scope.out) {
      activeFilters.push({
        key: `${name}:out:${id}`,
        label: `${word}: − ${labelOf(id)}`,
        out: true,
        onRemove: () => setScope(name, normalizeScope({ ...scope, out: scope.out.filter((x) => x !== id) })),
      });
    }
  }

  return (
    <div className="px-6 py-8">
      {/* The bar over the columns (operator's variant A, 2026-10-22). One title
          row (name, board, count, actions) and ONE filter row: the long sets —
          types, collections, tags — open from menus instead of laying every chip
          out, so the bar keeps its height however many sets the account has.
          Measured on 1440 × 900 with the test account's board: the column heads
          moved from 330 px to 207 px (more with the operator's sets). */}
      <header className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <h1
          className="text-[1.5rem] leading-tight whitespace-nowrap"
          style={{ fontFamily: 'var(--font-display)', fontWeight: 600 }}
        >
          Pipeline obchodů
        </h1>
        <BoardSwitcher boards={shares?.shared_with_me ?? []} active={active} />
        <p className="text-[0.75rem] tracking-wide text-[var(--color-ink-3)] font-mono tabular-nums whitespace-nowrap">
          {/* An em dash while the count is genuinely unknown. Rendering 0
              during the load states "your pipeline is empty", which is a
              claim, not a placeholder — and it was briefly true on every
              visit before the board resolved. */}
          {boardQ.data === undefined
            ? '—'
            : filtersActive
              ? `${filteredCards.length} z ${cards.length}`
              : cards.length}{' '}
          nemovitostí
        </p>
        <div className="ml-auto flex items-center gap-2">
          {/* Sharing is always my own board's (never delegated): not on a shared one. */}
          {active == null && (
            <button
              type="button"
              onClick={() => setSharing((v) => !v)}
              aria-pressed={sharing}
              className={HEADER_BUTTON}
            >
              {sharing
                ? 'Hotovo'
                : shares && shares.shared_with.length > 0
                  ? `Sdílení · ${shares.shared_with.length}`
                  : 'Sdílení'}
            </button>
          )}
          {/* An edit share manages the owner's stages as the owner (ruling 3). */}
          {!readOnly && (
            <button
              type="button"
              onClick={() => setManage((v) => !v)}
              aria-pressed={manage}
              className={HEADER_BUTTON}
            >
              {manage ? 'Hotovo' : 'Spravovat fáze'}
            </button>
          )}
          {/* The card lands on the board shown: acting through its share. The
              page's one primary action. */}
          {!readOnly && newProperty && (
            <button
              type="button"
              onClick={() => newProperty.open({ stay: true, acting: active })}
              className={PRIMARY_BUTTON}
            >
              <span aria-hidden>+</span>
              Nová nemovitost
            </button>
          )}
        </div>
      </header>
      {active != null && <ViewerLine share={active} />}

      {sharing && active == null && (
        <div className="mt-5">
          <PipelineSharing shares={shares} />
        </div>
      )}

      {manage && !readOnly && stages.length > 0 && <StageManager stages={stages} />}

      <div className="mt-4 flex flex-wrap items-center gap-2">
        <div role="group" aria-label="Lokalita" className="w-[18rem] max-w-full">
          <LocationTypeahead
            label="Lokalita"
            value={districts}
            onChange={(n) => setDistricts(n ?? [])}
          />
        </div>
        {bar.hasInactive && (
          <div role="group" aria-label="Stav">
            <Segmented
              variant="solid"
              options={STATUS_OPTIONS}
              value={status}
              onChange={setStatus}
            />
          </div>
        )}
        {bar.types.length >= 2 && (
          <FilterMenu label="Typ" count={types.size}>
            <MultiselectChips value={[...types]} options={bar.types} onChange={setTypes} />
          </FilterMenu>
        )}
        {/* The curation lenses fail open (rule #22) and say so (MS16): the
            control's read is the member map the offer stands on AND the
            elements it names, so a failed list is a retry, not "#id" chips. */}
        {(bar.showCollections || readFailed(collectionsRead)) && (
          <FilterMenu label="Kolekce" count={scopeCount(scopes.collections)} failed={readFailed(collectionsRead)}>
            <CuratedSetFilter
              set="collections"
              elements={bar.collections}
              value={scopes.collections}
              onChange={(next) => setScope('collections', next)}
              query={collectionsRead}
            />
          </FilterMenu>
        )}
        {(bar.showTags || readFailed(tagsRead)) && (
          <FilterMenu label="Štítky" count={scopeCount(scopes.tags)} failed={readFailed(tagsRead)}>
            <CuratedSetFilter
              set="tags"
              elements={bar.tags}
              value={scopes.tags}
              onChange={(next) => setScope('tags', next)}
              query={tagsRead}
            />
          </FilterMenu>
        )}
        {filtersActive && (
          <button
            type="button"
            onClick={reset}
            className="px-1 text-[0.8rem] text-[var(--color-copper)] hover:text-[var(--color-copper-2)] whitespace-nowrap"
          >
            Zrušit filtry
          </button>
        )}
        {/* Řazení and Karty are how the board PRESENTS the cohort, not which
            deals are in it — the bar's far end, and Zrušit filtry leaves them alone. */}
        <div className="ml-auto flex items-center gap-3">
          <label className="inline-flex items-center gap-1.5 text-[0.78rem] text-[var(--color-ink-3)] whitespace-nowrap">
            Řazení
            <select
              aria-label="Řazení karet ve fázi"
              value={
                PIPELINE_SORT_OPTIONS.find(
                  (o) => o.field === sort.field && o.direction === sort.direction,
                )?.value ?? sortParamOf(sort)
              }
              onChange={(e) => {
                const picked = PIPELINE_SORT_OPTIONS.find(
                  (o) => o.value === e.target.value,
                );
                if (picked) setSort({ field: picked.field, direction: picked.direction });
              }}
              className="h-8 rounded-[var(--radius-sm)] border border-[var(--color-rule)] bg-[var(--color-paper-2)] px-2 text-[0.78rem] text-[var(--color-ink-2)] transition-colors hover:border-[var(--color-rule-strong)]"
            >
              {PIPELINE_SORT_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
          </label>
          <div role="group" aria-label="Karty">
            <SizeToggle
              label="Velikost karet"
              value={cardSize.value}
              onChange={cardSize.set}
              steps={PIPELINE_CARD_SIZES.map((v) => ({
                value: v,
                label: PIPELINE_CARD_SIZE_LABELS[v].label,
                title: PIPELINE_CARD_SIZE_LABELS[v].title,
                glyph: CARD_SIZE_GLYPH[v],
              }))}
            />
          </div>
        </div>
      </div>
      <ActiveFilters filters={activeFilters} />


      {readFailed(stagesQ) || readFailed(boardQ) ? (
        <ErrorBanner
          title="Pipeline"
          message="se nepodařilo načíst."
          onRetry={() => {
            void stagesQ.refetch();
            void boardQ.refetch();
          }}
        />
      ) : stagesQ.data === undefined || boardQ.data === undefined ? (
        /* Until BOTH reads land: a frame drawn from one alone is a board with no
           columns, or columns that read as empty. The stages are usually warm,
           so this is the real columns with the cards still in flight. */
        <BoardSkeleton live={lanes.live} closed={lanes.closed} size={cardSize.value} />
      ) : (
        <CardHydrationProvider
          listingIds={visibleListingIds}
          /* The board's card face: one 48px thumbnail and a broker line. No
             carousel — asking the multi-image read for one photo per card is
             exactly what W4's listing_cover_public replaced. */
          renders={{ covers: true, brokers: cards }}
        >
          <Board
            live={lanes.live}
            closed={lanes.closed}
            cards={filteredCards}
            byStage={byStage}
            size={cardSize.value}
          />
        </CardHydrationProvider>
      )}
    </div>
  );
}

const HEADER_BUTTON =
  'inline-flex items-center h-8 px-3 whitespace-nowrap rounded-[var(--radius-sm)] border border-[var(--color-rule)] bg-[var(--color-paper-2)] text-[0.8rem] text-[var(--color-ink-2)] hover:border-[var(--color-rule-strong)] hover:text-[var(--color-ink)] aria-pressed:border-[var(--color-copper)] aria-pressed:text-[var(--color-copper)]';
const PRIMARY_BUTTON =
  'inline-flex items-center gap-1.5 h-8 px-3.5 whitespace-nowrap rounded-[var(--radius-sm)] bg-[var(--color-copper)] text-[0.8rem] font-medium text-[var(--color-on-copper)] hover:bg-[var(--color-copper-2)] transition-colors';

/* How many choices a curated-set scope holds, for its menu's label. */
const scopeCount = (s: SetScope | null | undefined): number =>
  s ? (s.set ? 1 : 0) + s.in.length + s.out.length : 0;

/* A board shared with me: whose it is, in which mode, and the one write a
 * recipient has on it — leaving (its own grant), which also puts this device
 * back on my own board. Never acts: the leave is the caller's own. */
function ViewerLine({ share }: { share: PipelineShare }) {
  const qc = useQueryClient();
  const [confirming, setConfirming] = useState(false);
  const leave = useMutation({
    mutationFn: () => unsharePipeline(share.account_id, 'shared_with_me'),
    onSuccess: () => {
      void revalidateTables(qc, ['pipeline_shares']);
      resetActiveBoard();
    },
    /* Its failure shows under the line; an onError keeps the global toast off it. */
    onError: () => {},
  });
  return (
    <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-2">
      <p className="text-[0.8rem] text-[var(--color-ink-2)]">
        sdílí <span className="text-[var(--color-ink)]">{shareOwnerName(share)}</span>
        <span className="text-[var(--color-ink-4)]"> · {SHARE_MODE_LABEL[share.mode]}</span>
      </p>
      <TwoStep
        idle="Odebrat z mého seznamu"
        confirm={leave.isPending ? 'Odebírám…' : 'Odebrat'}
        cancel="Zrušit"
        confirming={confirming}
        setConfirming={setConfirming}
        pending={leave.isPending}
        onConfirm={() => leave.mutate()}
      />
      {leave.error && (
        <p className="w-full text-[0.75rem] text-[var(--color-brick)]">{leave.error.message}</p>
      )}
    </div>
  );
}

/* My board's Sharing block: the one ShareBlock, with a mode per share. A POST
 * answers both lists, so it lands in the cache as is. */
function PipelineSharing({ shares }: { shares: PipelineShares | null }) {
  const qc = useQueryClient();
  return (
    <ShareBlock
      title="Sdílení"
      lang="cs"
      modes
      description="Komu pipeline nasdílíte, uvidí jeho fáze a karty. S přístupem Úpravy může karty přesouvat a fáze spravovat, a na stránce nemovitosti upravovat jako vy štítky, kolekce, poznámky a odkaz. Odhady, hlídače, předvolby a vyúčtování zůstávají vaše."
      recipients={shares?.shared_with ?? []}
      onShare={(email, mode) =>
        sharePipeline(email, mode ?? 'read').then((next) => qc.setQueryData(pipelineShareKeys.all, next))
      }
      onRevoke={(account_id) =>
        unsharePipeline(account_id, 'shared_with').then(() => revalidateTables(qc, ['pipeline_shares']))
      }
    />
  );
}

/* One glyph per card size — the switch's vocabulary, shared with the boolean
 * ImageSizeToggle the other grids use. */
const CARD_SIZE_GLYPH: Record<PipelineCardSize, JSX.Element> = {
  sm: <SmallImageGlyph />,
  md: <MediumImageGlyph />,
  lg: <LargeImageGlyph />,
};

/* The board's shape, drawn from the stage list alone.
 *
 * Not a generic shimmer: it is the real column layout with the real labels and
 * colours, so the transition to the loaded board is the cards appearing inside
 * columns that were already there — no reflow, no jump. Falls back to three
 * neutral columns on the rare path where even the stages are cold, which keeps
 * the page from collapsing to a single line of text. */
function BoardSkeleton({
  live,
  closed,
  size,
}: {
  live: PipelineStage[];
  closed: PipelineStage[];
  size: PipelineCardSize;
}) {
  const columns: Array<PipelineStage | null> =
    live.length > 0 ? live : [null, null, null];
  const geo = PIPELINE_CARD_GEOMETRY[size];
  return (
    <BoardFrame
      busy
      header={
        <>
          {columns.map((s, i) =>
            s ? (
              <StageHeader
                key={s.id}
                label={s.label}
                labelColor={stageColor(s)}
                ruleColor={stageColor(s)}
                width={geo.column}
              />
            ) : (
              <StageHeader
                key={`skeleton-${i}`}
                label={'\u00a0'}
                labelColor="var(--color-ink-4)"
                ruleColor="var(--color-rule)"
                width={geo.column}
              />
            ),
          )}
          {closed.length > 0 && <ClosedLaneHeader width={PIPELINE_CLOSED_LANE_WIDTH} />}
        </>
      }
    >
      {columns.map((s, i) => (
        <ul
          key={s?.id ?? `skeleton-${i}`}
          className={`shrink-0 space-y-2 p-1 ${geo.column} ${geo.dropZoneMin}`}
        >
          {[0, 1].map((n) => (
            <li
              key={n}
              className={`${geo.skeletonRow} rounded-[var(--radius-md)] border border-[var(--color-rule)] bg-[var(--color-paper-2)] opacity-60`}
            />
          ))}
        </ul>
      ))}
      {/* The lane as it will land, folded: its rows without their counts. */}
      {closed.length > 0 && (
        <div className={`shrink-0 p-1 ${PIPELINE_CLOSED_LANE_WIDTH}`}>
          <ul className="space-y-1">
            {closed.map((s) => (
              <li key={s.id} className={`${CLOSED_ROW_BOX} ${CLOSED_ROW_IDLE}`}>
                <ClosedRowFace stage={s} />
              </li>
            ))}
          </ul>
        </div>
      )}
      <span className="sr-only">Načítání pipeline…</span>
    </BoardFrame>
  );
}

/* The board's two rows: the stage headers, pinned under the top bar while the
 * page scrolls, above the columns, which scroll sideways.
 *
 * The headers cannot simply be `sticky` inside their columns. `overflow-x:
 * auto` forces `overflow-y` to auto as well, so the column row is its own
 * scroll container, and a sticky header inside it pins against that row —
 * which never scrolls vertically — instead of against the page. So the headers
 * live in a row OUTSIDE the scroller and follow it by copying its scrollLeft.
 * Both rows lay out the same column widths and gaps, so their scroll ranges
 * are identical and any clamp (a smaller card size, a narrower window) lands
 * on both alike: a scroll of the columns is the one thing to mirror.
 *
 * `top-14` is the Shell's top-bar height (Shell.tsx lists every site pinned to
 * it). `z-10` keeps the row above the cards but below the Lokalita dropdown
 * (z-20), which can hang down over the board. */
function BoardFrame({
  header,
  busy,
  children,
}: {
  header: ReactNode;
  busy?: boolean;
  children: ReactNode;
}) {
  const headerRow = useRef<HTMLDivElement>(null);
  return (
    <div className="mt-3" aria-busy={busy || undefined}>
      <div
        ref={headerRow}
        className="sticky top-14 z-10 flex gap-4 overflow-hidden bg-[var(--color-paper)] pt-3"
      >
        {header}
      </div>
      {/* items-stretch: every column is as tall as the tallest, so each stage's
          drop zone spans the whole board height instead of ending at its last
          card. */}
      <div
        onScroll={(e) => {
          if (headerRow.current) headerRow.current.scrollLeft = e.currentTarget.scrollLeft;
        }}
        className="mt-3 flex items-stretch gap-4 overflow-x-auto pb-4"
      >
        {children}
      </div>
    </div>
  );
}

/* A stage's label and rule in its colour, plus its card count once the board
 * has loaded. The skeleton's stand-in columns (stages still cold) pass a
 * neutral rule and a no-break space, which holds the label's line height so
 * the row doesn't collapse; the Uzavřené lane passes its own neutral pair. */
function StageHeader({
  label,
  labelColor,
  ruleColor,
  count,
  width,
}: {
  label: string;
  labelColor: string;
  ruleColor: string;
  count?: number;
  width: string;
}) {
  return (
    <div
      className={`flex shrink-0 items-baseline justify-between px-1 pb-2 border-b-2 ${width}`}
      style={{ borderColor: ruleColor }}
    >
      <span
        className="text-[0.72rem] tracking-[0.14em] uppercase font-medium"
        style={{ color: labelColor }}
      >
        {label}
      </span>
      {count != null && (
        <span className="font-mono tabular-nums text-[0.7rem] text-[var(--color-ink-4)]">
          {count}
        </span>
      )}
    </div>
  );
}

function stageColor(stage: PipelineStage): string {
  return stage.color
    ? `var(--color-tag-${stage.color})`
    : 'var(--color-rule-strong)';
}

/* Pure: resolve a drag-end (active card, over column) into a stage move, or
 * null for a no-op (same column / dropped outside a column / unknown card).
 * Exported so the move-resolution logic is unit-tested without simulating DnD. */
export function planMove(
  activeId: string,
  overId: string | null,
  cards: PipelineBoardCard[],
): { propertyId: number; stageId: number } | null {
  if (!overId || !overId.startsWith(STAGE_PREFIX)) return null;
  const propertyId = Number(activeId.slice(CARD_PREFIX.length));
  const stageId = Number(overId.slice(STAGE_PREFIX.length));
  if (!Number.isFinite(propertyId) || !Number.isFinite(stageId)) return null;
  const card = cards.find((c) => c.property_id === propertyId);
  if (!card || card.stage_id === stageId) return null;
  return { propertyId, stageId };
}

/* A closed-stage row registers this as its droppable data; the columns none. */
const CLOSED_ROW = { closed: true };

/* Which droppable a drag is over (P22). A closed-stage row is ~2rem tall and
 * the drag ghost 4.5rem (Malé) to ~22rem (Velké), so dnd-kit's overlap rule
 * would close a deal into whichever row the ghost happened to cover most. A
 * row is hit by a POINT instead: the pointer, or on a keyboard drag (which has
 * no pointer) the ghost's centre. The columns keep the overlap rule they have
 * always had. Exported for its test. */
export const boardCollision: CollisionDetection = (args) => {
  const rows = args.droppableContainers.filter((c) => c.data.current?.closed);
  const columns = args.droppableContainers.filter((c) => !c.data.current?.closed);
  const { left, top, width, height } = args.collisionRect;
  const point = args.pointerCoordinates ?? { x: left + width / 2, y: top + height / 2 };
  const onRow = pointerWithin({ ...args, droppableContainers: rows, pointerCoordinates: point });
  return onRow.length > 0 ? onRow : rectIntersection({ ...args, droppableContainers: columns });
};

/* No sensor, no drag: a read-only board's DndContext never starts one. */
const NO_SENSORS: ReturnType<typeof useSensors> = [];

/* What a drag holds: a gesture's board reads, and the stages — they are the
 * droppable columns (docs/design/curation-sync.md Decision 10). */
const DRAG_HELD = [...BOARD_GESTURE_HELD, pipelineKeys.stages] as const;

/* A drag holds its board's reads from pick-up to drop, so a re-read — a change
 * made elsewhere, a mount — never repaints the columns under the card in hand:
 * one in flight at pick-up is cancelled and owed, one that starts meanwhile too
 * (lib/revalidate `holdReads`). The drop calls `move.mutate` FIRST, whose own
 * hold (lib/useOptimisticWrite) registers synchronously over the board and the
 * members map, and only then lets the drag's go. A read is paid when the LAST
 * hold over it goes, so what the drag parked on the board and members waits for
 * the move's settle, which reads each once; the stages, held by the drag alone,
 * are read at the drop. A drop with no move, a cancel and an unmount pay
 * everything parked at once. */
function useDragHold(): { grab: () => void; letGo: () => void } {
  const qc = useQueryClient();
  const actAs = useActAs();
  const held = useRef<Hold | null>(null);
  const letGo = useCallback(() => {
    held.current?.release();
    held.current = null;
  }, []);
  useEffect(() => letGo, [letGo]);
  const grab = () => {
    letGo();
    held.current = holdReads(
      qc,
      DRAG_HELD.map((key) => ({ queryKey: actingKey(actAs, key) })),
    );
  };
  return { grab, letGo };
}

function Board({
  live,
  closed,
  cards,
  byStage,
  size,
}: {
  live: PipelineStage[];
  closed: PipelineStage[];
  cards: PipelineBoardCard[];
  byStage: Map<number, PipelineBoardCard[]>;
  size: PipelineCardSize;
}) {
  const readOnly = useActingReadOnly();
  const [activeId, setActiveId] = useState<string | null>(null);
  /* One closed stage open at a time, local like Spravovat fáze. Looked up in
   * the list, so a stage un-ticked from "konec" simply closes. */
  const [openClosedId, setOpenClosedId] = useState<number | null>(null);
  const openClosed = closed.find((s) => s.id === openClosedId) ?? null;
  const geo = PIPELINE_CARD_GEOMETRY[size];
  /* One value for the lane's header and body, so the pinned header row keeps
   * the columns' scroll range. */
  const laneWidth = openClosed ? geo.column : PIPELINE_CLOSED_LANE_WIDTH;
  const sensors = useSensors(
    // distance:6 so a click on the card's link/select doesn't start a drag.
    useSensor(PointerSensor, { activationConstraint: { distance: 6 } }),
    useSensor(KeyboardSensor),
  );

  /* The drag (move) and the trash (remove) are the same writes every funnel
   * makes — one hook, so a drag here repaints the Browse funnels and the
   * listing header exactly like a click there repaints the board, and a failed
   * one snaps back AND says why (rule #22). */
  const { move, remove } = usePipelineCard();
  const onRemove = (propertyId: number) => remove.mutate(propertyId);
  const drag = useDragHold();

  const activeCard = activeId
    ? cards.find((c) => `${CARD_PREFIX}${c.property_id}` === activeId) ?? null
    : null;

  return (
    <DndContext
      sensors={readOnly ? NO_SENSORS : sensors}
      collisionDetection={boardCollision}
      onDragStart={(e: DragStartEvent) => {
        drag.grab();
        setActiveId(String(e.active.id));
      }}
      onDragCancel={() => {
        setActiveId(null);
        drag.letGo();
      }}
      onDragEnd={(e: DragEndEvent) => {
        setActiveId(null);
        const plan = planMove(
          String(e.active.id),
          e.over ? String(e.over.id) : null,
          cards,
        );
        // The move's hold first, then the drag lets go (useDragHold).
        if (plan) move.mutate(plan);
        drag.letGo();
      }}
    >
      <BoardFrame
        header={
          <>
            {live.map((s) => (
              <StageHeader
                key={s.id}
                label={s.label}
                labelColor={stageColor(s)}
                ruleColor={stageColor(s)}
                count={byStage.get(s.id)?.length ?? 0}
                width={geo.column}
              />
            ))}
            {closed.length > 0 && <ClosedLaneHeader width={laneWidth} />}
          </>
        }
      >
        {live.map((s) => (
          <StageColumn
            key={s.id}
            stage={s}
            cards={byStage.get(s.id) ?? []}
            size={size}
            onRemove={onRemove}
          />
        ))}
        {closed.length > 0 && (
          <ClosedLane
            stages={closed}
            byStage={byStage}
            open={openClosed}
            onToggle={(id) => setOpenClosedId((cur) => (cur === id ? null : id))}
            width={laneWidth}
            size={size}
            onRemove={onRemove}
          />
        )}
      </BoardFrame>
      {/* dropAnimation={null}: the optimistic move already places the card in
          the target column on release, so the default "fly back to origin"
          drop animation would show the ghost sliding home before the card
          reappears — a visible jump back. Vanish the overlay instantly. */}
      <DragOverlay dropAnimation={null}>
        {activeCard ? (
          <div
            className={`${geo.overlay} rounded-[var(--radius-md)] border border-[var(--color-rule-strong)] bg-[var(--color-paper-2)] p-2.5 shadow-lg`}
          >
            <CardFace card={activeCard} size={size} />
          </div>
        ) : null}
      </DragOverlay>
    </DndContext>
  );
}

function StageManager({ stages }: { stages: PipelineStage[] }) {
  const qc = useQueryClient();
  /* On an edit share the owner's stages, managed as the owner (ruling 3). */
  const actAs = useActAs();
  const [err, setErr] = useState<string | null>(null);
  const [newLabel, setNewLabel] = useState('');

  /* What a stage edit moves is lib/curationSync's `pipeline_stages` row: the
   * stage list, the board (its columns) and the members map, whose stage label,
   * colour and code every funnel badge renders from (property_pipeline_public
   * joins the stages). The hand list it replaces omitted the members. */
  const invalidate = () => {
    setErr(null);
    void revalidateTables(qc, ['pipeline_stages'], actAs);
  };
  const onError = (e: unknown) =>
    setErr(e instanceof Error ? e.message : 'Akce selhala.');

  const reorder = useMutation({
    mutationFn: (ids: number[]) => reorderPipelineStages(ids, ...actOpts(actAs)),
    onSuccess: invalidate,
    onError,
  });
  const create = useMutation({
    mutationFn: (label: string) => createPipelineStage({ label }, ...actOpts(actAs)),
    onSuccess: () => {
      setNewLabel('');
      invalidate();
    },
    onError,
  });

  const move = (idx: number, dir: -1 | 1) => {
    const ids = stages.map((s) => s.id);
    const j = idx + dir;
    if (j < 0 || j >= ids.length) return;
    [ids[idx], ids[j]] = [ids[j], ids[idx]];
    reorder.mutate(ids);
  };

  const submitNew = () => {
    const label = newLabel.trim();
    if (label) create.mutate(label);
  };

  /* A section card like Sdílení (C1–C6): the stages its count. Adding a stage stays the body's last row:
     a title bar holds buttons and links only (C6), never a field. */
  return (
    <Panel
      className="mt-5"
      title="Fáze pipeline"
      count={stages.length}
    >
      {err && <p className="text-xs text-[var(--color-brick)]">{err}</p>}
      <ul className="space-y-2">
        {stages.map((s, i) => (
          <StageEditorRow
            key={s.id}
            stage={s}
            ordinal={i + 1}
            isFirst={i === 0}
            isLast={i === stages.length - 1}
            onMove={(dir) => move(i, dir)}
            onError={onError}
            invalidate={invalidate}
          />
        ))}
      </ul>
      <div className="flex items-center gap-2 border-t border-[var(--color-rule-soft)] pt-3">
        <input
          aria-label="Nová fáze"
          value={newLabel}
          onChange={(e) => setNewLabel(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') submitNew();
          }}
          placeholder="Nová fáze…"
          maxLength={80}
          className="flex-1 px-2 py-1 text-sm rounded-[var(--radius-sm)] bg-[var(--color-inset)] border border-[var(--color-rule)] text-[var(--color-ink)]"
        />
        <button
          type="button"
          onClick={submitNew}
          disabled={!newLabel.trim() || create.isPending}
          className={PANEL_ACTION}
        >
          Přidat
        </button>
      </div>
    </Panel>
  );
}

function StageEditorRow({
  stage,
  ordinal,
  isFirst,
  isLast,
  onMove,
  onError,
  invalidate,
}: {
  stage: PipelineStage;
  /* Ordinal among the live stages — the placeholder shown in the code box when
     the operator hasn't set one, i.e. exactly what the funnels will render. */
  ordinal: number;
  isFirst: boolean;
  isLast: boolean;
  onMove: (dir: -1 | 1) => void;
  onError: (e: unknown) => void;
  invalidate: () => void;
}) {
  /* Both fields follow the stage while untouched, so a rename made elsewhere shows
   * here and a focus and blur never writes it back (D3, lib/useServerDraft). */
  const label = useServerDraft(stage.label, trimDraft);
  const code = useServerDraft(stage.code ?? '', trimDraft);
  const actAs = useActAs();

  const update = useMutation({
    mutationFn: (patch: {
      label?: string;
      color?: TagColor | null;
      is_terminal?: boolean;
      is_entry?: boolean;
      code?: string | null;
    }) => updatePipelineStage(stage.id, patch, ...actOpts(actAs)),
    onSuccess: invalidate,
    onError,
  });
  const archive = useMutation({
    mutationFn: () => archivePipelineStage(stage.id, ...actOpts(actAs)),
    onSuccess: invalidate,
    onError,
  });

  /* Only what the user typed is saved; an emptied name is refused, back to the stage's.
   * The row stays open after a save, so the saved draft is marked: the next value
   * the server reads back ends it, even a change made elsewhere meanwhile. */
  const saveLabel = () => {
    label.onBlur();
    const next = label.edited();
    if (next === '') label.reset();
    else if (next != null) {
      update.mutate({ label: next });
      label.saved();
    }
  };

  /* Empty box = no code: the badge falls back to the ordinal rather than
   * freezing a guessed number into the row (migration 377). */
  const saveCode = () => {
    code.onBlur();
    const next = code.edited();
    if (next == null) return;
    update.mutate({ code: next === '' ? null : next });
    code.saved();
  };

  return (
    <li className="space-y-2 py-1">
      <div className="flex items-center gap-2">
        <span
          className="h-4 w-1 shrink-0 rounded-full"
          style={{ background: stageColor(stage) }}
          aria-hidden
        />
        <div className="flex shrink-0 flex-col leading-none">
          <button
            type="button"
            onClick={() => onMove(-1)}
            disabled={isFirst}
            aria-label="Posunout nahoru"
            className="text-[0.6rem] text-[var(--color-ink-3)] hover:text-[var(--color-ink)] disabled:opacity-25"
          >
            ▲
          </button>
          <button
            type="button"
            onClick={() => onMove(1)}
            disabled={isLast}
            aria-label="Posunout dolů"
            className="text-[0.6rem] text-[var(--color-ink-3)] hover:text-[var(--color-ink)] disabled:opacity-25"
          >
            ▼
          </button>
        </div>
        <input
          {...code.bind}
          onBlur={saveCode}
          onKeyDown={(e) => {
            if (e.key === 'Enter') (e.target as HTMLInputElement).blur();
          }}
          maxLength={4}
          placeholder={String(ordinal)}
          aria-label="Značka fáze"
          title={'Značka ve trychtýři (např. „1“, „9“). Prázdné = pořadí fáze.'}
          className="w-10 shrink-0 rounded-[var(--radius-sm)] border border-transparent bg-[var(--color-inset)] px-1 py-1 text-center font-mono text-sm tabular-nums text-[var(--color-ink)] hover:border-[var(--color-rule)]"
        />
        <input
          {...label.bind}
          onBlur={saveLabel}
          onKeyDown={(e) => {
            if (e.key === 'Enter') (e.target as HTMLInputElement).blur();
          }}
          maxLength={80}
          aria-label="Název fáze"
          className="flex-1 min-w-0 px-2 py-1 text-sm rounded-[var(--radius-sm)] bg-[var(--color-inset)] border border-transparent hover:border-[var(--color-rule)] text-[var(--color-ink)]"
        />
        <span className="flex shrink-0 items-center gap-0.5">
          <button
            type="button"
            onClick={() => !stage.is_entry && update.mutate({ is_entry: true })}
            disabled={stage.is_entry}
            title={stage.is_entry ? 'Vstupní fáze' : 'Nastavit jako vstupní'}
            aria-label="Vstupní fáze"
            className="flex w-5 justify-center disabled:cursor-default"
            style={{ color: stage.is_entry ? 'var(--color-copper)' : 'var(--color-ink-4)' }}
          >
            <FunnelIcon filled={stage.is_entry} className="h-4 w-4" />
          </button>
          <Hint text={'Vstupní fáze: sem se nemovitost přidá jako záložka („Přidat do pipeline“). Právě jedna fáze může být vstupní.'} />
        </span>
        <span className="flex shrink-0 items-center gap-0.5 text-[0.68rem] text-[var(--color-ink-3)]">
          <label className="flex items-center gap-1">
            <input
              type="checkbox"
              checked={stage.is_terminal}
              onChange={(e) => update.mutate({ is_terminal: e.target.checked })}
              disabled={stage.is_entry}
            />
            konec
          </label>
          <Hint text={'Koncová fáze: uzavřený obchod (např. Koupeno / Zamítnuto). Při slučování duplicit nepřebije živý (otevřený) obchod.'} />
        </span>
        <button
          type="button"
          onClick={() => archive.mutate()}
          disabled={stage.is_entry || archive.isPending}
          title={
            stage.is_entry
              ? 'Vstupní fázi nelze archivovat'
              : 'Archivovat fázi (musí být prázdná)'
          }
          aria-label="Archivovat fázi"
          className="shrink-0 w-6 text-center text-[var(--color-ink-4)] hover:text-[var(--color-brick)] disabled:opacity-25"
        >
          ✕
        </button>
      </div>
      <div className="flex flex-wrap items-center gap-1 pl-[1.4rem]">
        <TagColorPicker
          value={stage.color ?? null}
          onChange={(c) => update.mutate({ color: c })}
          showNull
          size="sm"
        />
      </div>
      {/* Who last changed it acting as the board's owner (ruling 5). */}
      <DelegatedStamp stamp={stage} className="pl-[1.4rem]" />
    </li>
  );
}

/* Small (i) help glyph — native title hover box (the codebase's tooltip
 * convention) + aria-label so it reads to assistive tech. */
function Hint({ text }: { text: string }) {
  return (
    <span
      role="img"
      aria-label={text}
      title={text}
      className="cursor-help text-[var(--color-ink-3)] hover:text-[var(--color-ink)]"
    >
      <InfoIcon className="h-3.5 w-3.5" />
    </span>
  );
}

function StageColumn({
  stage,
  cards,
  size,
  onRemove,
}: {
  stage: PipelineStage;
  cards: PipelineBoardCard[];
  size: PipelineCardSize;
  onRemove: (propertyId: number) => void;
}) {
  const { setNodeRef, isOver } = useDroppable({ id: `${STAGE_PREFIX}${stage.id}` });
  const geo = PIPELINE_CARD_GEOMETRY[size];
  /* The stage's header lives in BoardFrame's pinned row, not above this list,
     so the list names its stage itself — otherwise a screen reader meets every
     header first and then a run of unlabelled lists. */
  return (
    <ul
      ref={setNodeRef}
      aria-label={stage.label}
      className={`shrink-0 space-y-2 rounded-[var(--radius-md)] p-1 transition-colors ${geo.column} ${geo.dropZoneMin} ${
        isOver
          ? 'bg-[var(--color-inset)] outline outline-1 outline-[var(--color-rule-strong)]'
          : ''
      }`}
    >
      <StageCards cards={cards} size={size} onRemove={onRemove} />
    </ul>
  );
}

/* A stage's cards, or the dash of an empty stage: a live column's and an
 * opened closed row's alike, so a closed deal is the same BoardCard. */
function StageCards({
  cards,
  size,
  onRemove,
}: {
  cards: PipelineBoardCard[];
  size: PipelineCardSize;
  onRemove: (propertyId: number) => void;
}) {
  if (cards.length === 0) {
    return <li className="px-1 py-2 text-sm text-[var(--color-ink-4)]">—</li>;
  }
  return (
    <>
      {cards.map((c) => (
        <li key={c.property_id}>
          <BoardCard card={c} size={size} onRemove={onRemove} />
        </li>
      ))}
    </>
  );
}

const CLOSED_LANE_LABEL = 'Uzavřené';

/* The lane's header in the pinned row: neutral, and no total, since the
 * counts are on its rows. */
function ClosedLaneHeader({ width }: { width: string }) {
  return (
    <StageHeader
      label={CLOSED_LANE_LABEL}
      labelColor="var(--color-ink-3)"
      ruleColor="var(--color-rule-strong)"
      width={width}
    />
  );
}

/* P22: the terminal stages folded into one lane after the live columns, a row
 * per closed stage with its count. Each row is a drop target that closes a deal
 * into its stage (rule #22: a move, through the same planMove → move as a
 * column drop). A row opens that stage's cards UNDER the rows, so no drop
 * target moves when one opens. */
function ClosedLane({
  stages,
  byStage,
  open,
  onToggle,
  width,
  size,
  onRemove,
}: {
  stages: PipelineStage[];
  byStage: Map<number, PipelineBoardCard[]>;
  open: PipelineStage | null;
  onToggle: (stageId: number) => void;
  width: string;
  size: PipelineCardSize;
  onRemove: (propertyId: number) => void;
}) {
  const listId = useId();
  return (
    <div className={`shrink-0 p-1 ${width}`}>
      <ul aria-label={CLOSED_LANE_LABEL} className="space-y-1">
        {stages.map((s) => (
          <ClosedStageRow
            key={s.id}
            stage={s}
            count={byStage.get(s.id)?.length ?? 0}
            open={open?.id === s.id}
            controls={listId}
            onToggle={() => onToggle(s.id)}
          />
        ))}
      </ul>
      {open && (
        <ul id={listId} aria-label={open.label} className="mt-2 space-y-2">
          <StageCards cards={byStage.get(open.id) ?? []} size={size} onRemove={onRemove} />
        </ul>
      )}
    </div>
  );
}

const CLOSED_ROW_BOX = 'flex items-center gap-2 rounded-[var(--radius-sm)] border px-2 py-1.5';
const CLOSED_ROW_IDLE = 'border-[var(--color-rule)] bg-[var(--color-paper-2)]';

function ClosedStageRow({
  stage,
  count,
  open,
  controls,
  onToggle,
}: {
  stage: PipelineStage;
  count: number;
  open: boolean;
  controls: string;
  onToggle: () => void;
}) {
  const { setNodeRef, isOver } = useDroppable({
    id: `${STAGE_PREFIX}${stage.id}`,
    data: CLOSED_ROW,
  });
  /* The drag-over treatment sits on the button, which fills the row: on the
     <li> the button's own paper would hide it. Copper = the open row. */
  return (
    <li ref={setNodeRef}>
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={open}
        aria-controls={open ? controls : undefined}
        aria-label={`${stage.label} · ${count}`}
        className={`${CLOSED_ROW_BOX} w-full text-left transition-colors ${
          isOver
            ? 'border-[var(--color-rule-strong)] bg-[var(--color-inset)] outline outline-1 outline-[var(--color-rule-strong)]'
            : `${CLOSED_ROW_IDLE} hover:border-[var(--color-rule-strong)] aria-expanded:border-[var(--color-copper)]`
        }`}
      >
        <ClosedRowFace stage={stage} count={count} />
      </button>
    </li>
  );
}

/* A closed row's contents, also drawn by the skeleton (no count yet): the
 * stage's colour as a bar, as in the stage editor, then its label and count. */
function ClosedRowFace({ stage, count }: { stage: PipelineStage; count?: number }) {
  return (
    <>
      <span
        className="h-4 w-1 shrink-0 rounded-full"
        style={{ background: stageColor(stage) }}
        aria-hidden
      />
      <span
        className="min-w-0 flex-1 truncate text-[0.72rem] font-medium uppercase tracking-[0.14em] text-[var(--color-ink-2)]"
        title={stage.label}
      >
        {stage.label}
      </span>
      {count != null && (
        <span className="font-mono tabular-nums text-[0.7rem] text-[var(--color-ink-3)]">
          {count}
        </span>
      )}
    </>
  );
}
