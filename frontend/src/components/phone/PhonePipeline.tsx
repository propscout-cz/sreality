/* Pipeline on the phone (#1768, Phone app map #1759 W3; design #1762): the
 * desktop board's one read as one page per stage, swiped (#1760 Q8), under a
 * strip of stage tabs with their counts.
 *
 * THE READS AND WRITES ARE THE BOARD'S. Stages and cards come from
 * lib/pipelineReads (the kanban's own cache entries), covers from the shared
 * hydration layer on the board's cohort, and a card moves or leaves only
 * through its stage badge: the stage sheet is `PipelineStageMenuBody` over
 * `usePipelineCard` (rule 22 — the two-step remove, never a toggle). No drag
 * anywhere, and no stage create / rename / reorder / archive (#1760 Q13: the
 * desktop's job).
 *
 * The pages fill the space between the header and the tab bar and each scrolls
 * on its own, so swiping away from a long stage and back finds it where it was
 * and a short stage has no empty tail. Swipe and strip stay in step: the pages'
 * scroll picks the active tab (one rAF per frame), a tab tap scrolls the pages,
 * and the active tab is kept in view. The stage last looked at is remembered
 * per device, per board.
 *
 * SHARED BOARDS (migration 641, ruling 6): the same per-device active board as
 * the desktop (lib/acting), switched in a strip above the stages; a shared
 * board renders inside <ActingAs>. A read share is read-only — no stage sheet,
 * no tag chips, no artifact link; an edit share moves cards through the stage
 * sheet as the owner. Sharing itself, and leaving, stay on the desktop.
 *
 * LOKALITA is the desktop bar's place filter, nothing of its own: the same
 * `districts` params (lib/pipelineState, so one link is one view on either
 * device), the same typeahead in the brokers' place sheet, and the same
 * in-memory rule over the board's five codes (`matchesDistricts`). The stage
 * pages keep every stage and count only the cards in the place. */

import {
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type KeyboardEvent,
  type ReactNode,
} from 'react';
import { Link, useNavigationType } from 'react-router-dom';

import { readFailed } from '@/components/ReadFailedMark';
import { LocationTypeahead } from '@/components/filter-controls/LocationTypeahead';
import { matchesDistricts } from '@/lib/districtCodes';
import { fmtArea } from '@/lib/format';
import { CardHydrationProvider, useCardHydration } from '@/lib/hydration';
import { propertyPath } from '@/lib/listingUrl';
import { areaKindOf } from '@/lib/measure';
import {
  cardsWord,
  daysInStage,
  fmtDaysInStage,
  initialStageIndex,
  readLastStage,
  stagePages,
  writeLastStage,
  type StagePage,
} from '@/lib/phonePipeline';
import { TAB_BAR_SPACE } from '@/lib/phoneShell';
import { usePipelineBoard, usePipelineStages } from '@/lib/pipelineReads';
import { usePipelineViewState } from '@/lib/pipelineState';
import { ActingAs, useActAs, useActingReadOnly, type ActiveBoard } from '@/lib/acting';
import { useBoardSelection, useSharedBoardGuard } from '@/lib/pipelineShares';
import BoardSwitcher from '@/components/pipeline/BoardSwitcher';
import { useCardTags } from '@/lib/useCardTags';
import TagChip from '@/components/curation/TagChip';
import ArtifactLinkButton from '@/components/curation/ArtifactLinkButton';
import DelegatedStamp from '@/components/DelegatedStamp';
import { stageAccent, stageBadge } from '@/lib/pipelineStage';
import type { DistrictChip } from '@/lib/filters';
import type { PipelineBoardCard, PipelineStage } from '@/lib/types';

import BottomSheet from './BottomSheet';
import { PHOTO_DARK } from './CardPhotoStrip';
import { feedDelta, feedLayout, feedMfYield, feedPrice } from './FeedCard';
import { StageSheet, TagSheet, type SheetSubject } from './FeedSheets';
import PhonePipelineSkeleton from './PhonePipelineSkeleton';
import { usePhoneHeaderAside } from './PhoneShell';
import StageWash from './StageWash';
import { CHIP, CHIP_MD, CHIP_ROW, NO_FOCUS_ZOOM, chipState } from './chips';
import { ChevronDownIcon, PinIcon } from './phoneIcons';
import { placeLabelOf } from './placeLabel';

/* What is left between the phone header (PhoneShell's `h-14` plus its hairline)
 * and the tab bar's space: the strip and the pages fill exactly that. */
const FRAME_HEIGHT = `calc(100dvh - 57px - ${TAB_BAR_SPACE})`;

/* The stage code in a box, in the stage's colour (#1762 `.sb`). */
const BADGE =
  'inline-flex h-[18px] min-w-5 shrink-0 items-center justify-center rounded-[var(--radius-xs)] border border-[var(--st)] px-1 font-mono text-[10.5px] font-medium leading-none tabular-nums text-[var(--st)]';

const NO_STAGES: PipelineStage[] = [];
const NO_CARDS: PipelineBoardCard[] = [];

/* Each page's vertical scroll, by stage id, so "open a card → back" lands where it left. */
const pageScroll = new Map<number, number>();

const stageVars = (stage: PipelineStage): CSSProperties => {
  const accent = stageAccent(stage);
  return { '--st': accent.fg, '--st-soft': accent.soft } as CSSProperties;
};

const reducedMotion = (): boolean =>
  typeof window.matchMedia === 'function' && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

/* "2+kk · 54 m²", "Dům 4+1 · 132 m²". */
function pipelineLayoutLine(c: PipelineBoardCard): string {
  const parts = [feedLayout(c)].filter(Boolean);
  if (c.area_m2 != null) parts.push(fmtArea(c.area_m2, areaKindOf(c.category_main)));
  return parts.join(' · ');
}

export default function PhonePipeline() {
  const { active, sharesQ } = useBoardSelection();
  const switcher = <BoardSwitcher boards={sharesQ.data?.shared_with_me ?? []} active={active} size="phone" />;
  return (
    <ActingAs share={active}>
      <PhoneBoard key={active?.account_id ?? 'own'} active={active} switcher={switcher} />
    </ActingAs>
  );
}

function PhoneBoard({ active, switcher }: { active: ActiveBoard; switcher: ReactNode }) {
  const stagesQ = usePipelineStages();
  const boardQ = usePipelineBoard();
  const { districts, setDistricts } = usePipelineViewState();
  const placed = districts.length > 0;
  const stages = stagesQ.data ?? NO_STAGES;
  const cards = boardQ.data ?? NO_CARDS;
  const shown = useMemo(
    () => (placed ? cards.filter((c) => matchesDistricts(c, districts)) : cards),
    [cards, districts, placed],
  );
  const pages = useMemo(() => stagePages(stages, shown), [stages, shown]);
  /* The badge's ordinal fallback counts along the pages' own order. */
  const ordered = useMemo(() => pages.map((p) => p.stage), [pages]);
  const loaded = stagesQ.data !== undefined && boardQ.data !== undefined;
  const failed = readFailed(stagesQ) || readFailed(boardQ);
  const total = pages.reduce((n, p) => n + p.cards.length, 0);

  usePhoneHeaderAside(
    loaded && !failed ? (placed ? `${total} z ${cards.length} karet` : cardsWord(total)) : null,
  );

  useSharedBoardGuard(active, stagesQ.data);

  const listingIds = useMemo(
    () => cards.map((c) => c.listing_id).filter((id): id is number => id != null),
    [cards],
  );

  if (failed) {
    return (
      <>
        {switcher}
        <Notice
          title="Pipeline se nepodařilo načíst."
          detail="Spojení nebo databáze teď neodpověděly. Zkuste to znovu."
          action={{
            label: 'Zkusit znovu',
            onClick: () => {
              void stagesQ.refetch();
              void boardQ.refetch();
            },
          }}
        />
      </>
    );
  }
  if (!loaded) return <PhonePipelineSkeleton />;

  return (
    <CardHydrationProvider listingIds={listingIds} renders={{ covers: true }}>
      <StagePager
        pages={pages}
        stages={ordered}
        switcher={switcher}
        place={<PlaceRow districts={districts} onChange={setDistricts} />}
        placed={placed}
      />
    </CardHydrationProvider>
  );
}

/* The place chip over the stage tabs, and its sheet: the typeahead with its
 * field at the top, so the suggestions open above the keyboard. */
function PlaceRow({
  districts,
  onChange,
}: {
  districts: DistrictChip[];
  onChange: (next: DistrictChip[]) => void;
}) {
  const [open, setOpen] = useState(false);
  const on = districts.length > 0;
  const label = on ? placeLabelOf(districts) : 'Lokalita';
  return (
    <>
      <div className={`${CHIP_ROW} shrink-0 py-2`}>
        <button
          type="button"
          onClick={() => setOpen(true)}
          aria-haspopup="dialog"
          aria-label={on ? `Lokalita: ${label}` : 'Lokalita: všechny'}
          className={`${CHIP} ${CHIP_MD} ${chipState(on)} max-w-[70vw]`}
        >
          <PinIcon className="h-3.5 w-3.5 shrink-0" />
          <span className="truncate">{label}</span>
        </button>
      </div>
      {open && (
        <BottomSheet label="Lokalita" onClose={() => setOpen(false)}>
          <div className={`min-h-[70dvh] px-4 pt-3 ${NO_FOCUS_ZOOM}`}>
            <LocationTypeahead label="Lokalita" value={districts} onChange={(next) => onChange(next ?? [])} />
            <p className="mt-3 text-[12px] text-[var(--color-ink-3)]">
              Kraj, okres, obec, část obce nebo ulice; bez lokality jsou vidět všechny karty.
            </p>
          </div>
        </BottomSheet>
      )}
    </>
  );
}

function StagePager({
  pages,
  stages,
  switcher,
  place,
  placed,
}: {
  pages: StagePage[];
  stages: PipelineStage[];
  switcher: ReactNode;
  place: ReactNode;
  placed: boolean;
}) {
  const stripRef = useRef<HTMLDivElement>(null);
  const pagerRef = useRef<HTMLDivElement>(null);
  /* The stage last looked at is remembered per board (lib/phonePipeline). */
  const board = useActAs();
  const remembered = useRef(readLastStage(board));
  const [activeId, setActiveId] = useState<number | null>(null);
  const [sheetFor, setSheetFor] = useState<{
    kind: 'stage' | 'tags';
    card: PipelineBoardCard;
    subject: SheetSubject;
  } | null>(null);

  const found = activeId == null ? -1 : pages.findIndex((p) => p.stage.id === activeId);
  const active = found >= 0 ? found : initialStageIndex(pages, remembered.current);
  const activeRef = useRef(active);
  activeRef.current = active;

  /* A tab tap's destination while the smooth scroll is on its way: the pages
   * it passes over must not flick the strip through every stage between. */
  const target = useRef<number | null>(null);
  const targetTimer = useRef<number | undefined>(undefined);
  const frame = useRef(0);

  const settle = (i: number) => {
    const page = pages[i];
    if (!page) return;
    setActiveId(page.stage.id);
    writeLastStage(page.stage.id, board);
  };

  /* Land on the active page without animation: on first paint, when the stage
   * order changes under it, and when the width does. */
  const order = pages.map((p) => p.stage.id).join(',');
  useLayoutEffect(() => {
    const el = pagerRef.current;
    if (el && el.clientWidth) el.scrollLeft = activeRef.current * el.clientWidth;
  }, [order]);
  useEffect(() => {
    const onResize = () => {
      const el = pagerRef.current;
      if (el && el.clientWidth) el.scrollLeft = activeRef.current * el.clientWidth;
    };
    window.addEventListener('resize', onResize);
    return () => {
      window.removeEventListener('resize', onResize);
      window.clearTimeout(targetTimer.current);
      cancelAnimationFrame(frame.current);
    };
  }, []);

  /* Keep the active tab inside the strip, 8 px clear of either edge. */
  useEffect(() => {
    const strip = stripRef.current;
    const tab = strip?.querySelector<HTMLElement>(`[data-stage-index="${active}"]`);
    if (!strip || !tab || !strip.clientWidth) return;
    const left = tab.offsetLeft;
    const right = left + tab.offsetWidth;
    if (left < strip.scrollLeft + 8) strip.scrollLeft = left - 8;
    else if (right > strip.scrollLeft + strip.clientWidth - 8) strip.scrollLeft = right - strip.clientWidth + 8;
  }, [active]);

  const onPagerScroll = () => {
    if (frame.current) return;
    frame.current = requestAnimationFrame(() => {
      frame.current = 0;
      const el = pagerRef.current;
      if (!el || !el.clientWidth) return;
      const i = Math.min(pages.length - 1, Math.max(0, Math.round(el.scrollLeft / el.clientWidth)));
      if (target.current != null) {
        if (i !== target.current) return;
        target.current = null;
      }
      if (i !== activeRef.current) settle(i);
    });
  };

  const goTo = (i: number) => {
    const el = pagerRef.current;
    settle(i);
    if (!el || !el.clientWidth || Math.round(el.scrollLeft / el.clientWidth) === i) return;
    target.current = i;
    window.clearTimeout(targetTimer.current);
    targetTimer.current = window.setTimeout(() => {
      target.current = null;
    }, 900);
    const left = i * el.clientWidth;
    if (typeof el.scrollTo === 'function') el.scrollTo({ left, behavior: reducedMotion() ? 'auto' : 'smooth' });
    else el.scrollLeft = left;
  };

  /* The tablist's keyboard contract: arrows, Home and End move and select. */
  const onTabKey = (e: KeyboardEvent<HTMLButtonElement>) => {
    const last = pages.length - 1;
    const next =
      e.key === 'ArrowRight'
        ? Math.min(last, active + 1)
        : e.key === 'ArrowLeft'
          ? Math.max(0, active - 1)
          : e.key === 'Home'
            ? 0
            : e.key === 'End'
              ? last
              : null;
    if (next == null) return;
    e.preventDefault();
    goTo(next);
    stripRef.current?.querySelector<HTMLElement>(`[data-stage-index="${next}"]`)?.focus();
  };

  /* The sheet acts on the card as the board holds it NOW (a refetch may have
   * moved it); when the card is gone the sheet has nothing left to act on. */
  const sheetCard = sheetFor
    ? (pages.flatMap((p) => p.cards).find((c) => c.property_id === sheetFor.card.property_id) ?? null)
    : null;

  return (
    <div className="flex flex-col" style={{ height: FRAME_HEIGHT }}>
      {switcher && <div className="shrink-0 border-b border-[var(--color-rule-soft)]">{switcher}</div>}
      {place}
      <div
        ref={stripRef}
        role="tablist"
        aria-label="Fáze"
        className="flex shrink-0 overflow-x-auto border-b border-[var(--color-rule)] px-2 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
      >
        {pages.map((p, i) => {
          const on = i === active;
          return (
            <span key={p.stage.id} className="flex shrink-0">
              {p.separatorBefore && (
                <span aria-hidden className="mx-1 my-3 w-px shrink-0 bg-[var(--color-rule-strong)]" />
              )}
              <button
                type="button"
                role="tab"
                id={`pipeline-tab-${p.stage.id}`}
                aria-label={`${p.stage.label}, ${cardsWord(p.cards.length)}`}
                aria-selected={on}
                aria-controls={`pipeline-stage-${p.stage.id}`}
                tabIndex={on ? 0 : -1}
                data-stage-index={i}
                onClick={() => goTo(i)}
                onKeyDown={onTabKey}
                style={stageVars(p.stage)}
                className={[
                  'relative inline-flex h-12 shrink-0 items-center gap-1.5 whitespace-nowrap px-2 text-[13px]',
                  on ? 'font-medium text-[var(--color-ink)]' : 'text-[var(--color-ink-2)]',
                ].join(' ')}
              >
                <span aria-hidden className={BADGE}>
                  {stageBadge(p.stage, stages) ?? '·'}
                </span>
                {p.stage.label}
                <span className="font-mono text-[11px] font-normal tabular-nums text-[var(--color-ink-3)]">
                  {p.cards.length}
                </span>
                {on && <span aria-hidden className="absolute inset-x-2 -bottom-px h-0.5 bg-[var(--st)]" />}
              </button>
            </span>
          );
        })}
      </div>

      <div
        ref={pagerRef}
        onScroll={onPagerScroll}
        className="flex min-h-0 flex-1 snap-x snap-mandatory overflow-x-auto overscroll-x-contain [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
      >
        {pages.map((p, i) => (
          <StagePageView
            key={p.stage.id}
            page={p}
            index={i}
            count={pages.length}
            stages={stages}
            placed={placed}
            subjectId={sheetCard?.property_id ?? null}
            onStage={(card, subject) => setSheetFor({ kind: 'stage', card, subject })}
            onTags={(card, subject) => setSheetFor({ kind: 'tags', card, subject })}
          />
        ))}
      </div>

      {sheetFor?.kind === 'stage' && sheetCard && (
        <StageSheet
          subject={sheetFor.subject}
          stageId={sheetCard.stage_id}
          onClose={() => setSheetFor(null)}
        />
      )}
      {sheetFor?.kind === 'tags' && sheetCard && (
        <CardTagSheet subject={sheetFor.subject} onClose={() => setSheetFor(null)} />
      )}
    </div>
  );
}

function StagePageView({
  page,
  index,
  count,
  stages,
  placed,
  subjectId,
  onStage,
  onTags,
}: {
  page: StagePage;
  index: number;
  count: number;
  stages: PipelineStage[];
  placed: boolean;
  subjectId: number | null;
  onStage: (card: PipelineBoardCard, subject: SheetSubject) => void;
  onTags: (card: PipelineBoardCard, subject: SheetSubject) => void;
}) {
  const { stage, cards } = page;
  const ref = useRef<HTMLElement>(null);
  const navType = useNavigationType();

  useLayoutEffect(() => {
    const y = navType === 'POP' ? pageScroll.get(stage.id) : undefined;
    if (y && ref.current) ref.current.scrollTop = y;
  }, [navType, stage.id]);

  const badge = stageBadge(stage, stages) ?? '·';
  const readOnly = useActingReadOnly();
  return (
    <section
      ref={ref}
      id={`pipeline-stage-${stage.id}`}
      role="tabpanel"
      aria-labelledby={`pipeline-tab-${stage.id}`}
      onScroll={(e) => pageScroll.set(stage.id, e.currentTarget.scrollTop)}
      style={stageVars(stage)}
      className="h-full w-full min-w-0 shrink-0 snap-start snap-always overflow-y-auto overscroll-y-contain pb-4"
    >
      <div className="flex items-baseline gap-2 px-4 pb-3 pt-4">
        <h2 className="m-0 min-w-0 truncate font-display text-[19px] font-medium leading-[1.2] text-[var(--color-ink)]">
          {stage.label}
        </h2>
        <span className="shrink-0 text-[12px] text-[var(--color-ink-3)]">
          {cardsWord(cards.length)}
          {stage.is_terminal ? ' · uzavřené' : ''}
        </span>
        <span className="ml-auto shrink-0 font-mono text-[11px] tabular-nums text-[var(--color-ink-4)]">
          {index + 1} / {count}
        </span>
      </div>
      {cards.length > 0 ? (
        <div>
          {cards.map((c) => (
            <PipelinePhoneCard
              key={c.property_id}
              card={c}
              stage={stage}
              badge={badge}
              subject={c.property_id === subjectId}
              onStage={onStage}
              onTags={onTags}
            />
          ))}
        </div>
      ) : (
        <p className="mx-4 my-1 rounded-[var(--radius-md)] border border-dashed border-[var(--color-rule-strong)] px-4 py-6 text-[13px] text-[var(--color-ink-3)]">
          {placed
            ? 'V této fázi v té lokalitě nic není.'
            : readOnly
              ? 'V této fázi teď nic není.'
              : 'V této fázi teď nic není. Kartu sem přesunete z jejího odznaku fáze.'}
        </p>
      )}
    </section>
  );
}

function PipelinePhoneCard({
  card,
  stage,
  badge,
  subject,
  onStage,
  onTags,
}: {
  card: PipelineBoardCard;
  stage: PipelineStage;
  badge: string;
  subject: boolean;
  onStage: (card: PipelineBoardCard, subject: SheetSubject) => void;
  onTags: (card: PipelineBoardCard, subject: SheetSubject) => void;
}) {
  const { coverFor } = useCardHydration();
  const src = coverFor(card.listing_id);
  /* A read share's board (ruling 2): no stage sheet, no tags, no artifact link. */
  const readOnly = useActingReadOnly();
  const [broken, setBroken] = useState<string | null>(null);

  const price = feedPrice(card);
  const priceLabel = price.text ? `${price.text}${price.perMonth ? ' /měs' : ''}` : 'Cena na vyžádání';
  const layout = pipelineLayoutLine(card);
  const place = card.display_label?.trim() || null;
  const delta = feedDelta(card.total_price_change_pct);
  const days = fmtDaysInStage(daysInStage(card.entered_stage_at));
  const mf = feedMfYield(card);
  const text = [layout, place].filter(Boolean).join(' · ');
  const sheetSubject: SheetSubject = { property_id: card.property_id, text, figure: price.text ? priceLabel : null };

  return (
    <article
      className={[
        'relative flex items-start gap-3 border-t border-[var(--color-rule)] py-3 pl-4 pr-1 last:border-b',
        subject ? 'bg-[var(--st-soft)]' : '',
      ].join(' ')}
    >
      <div className="relative isolate aspect-[4/3] w-[104px] shrink-0 overflow-hidden rounded-[var(--radius-sm)] border border-[var(--color-rule)] bg-[var(--color-inset)]">
        {src && broken !== src && (
          <img
            src={src}
            alt=""
            loading="lazy"
            decoding="async"
            onError={() => setBroken(src)}
            className={`block h-full w-full object-cover ${PHOTO_DARK}`}
          />
        )}
        <StageWash color={stageAccent(stage).fg} badge={badge} label={null} size="sm" />
      </div>
      <div className="flex min-w-0 flex-1 flex-col gap-0.5">
        <div className="flex items-baseline gap-2">
          <Link
            to={propertyPath(card.property_id)}
            aria-label={[priceLabel, text].filter(Boolean).join(', ')}
            className="stretched-link whitespace-nowrap font-display text-[18px] font-medium leading-[1.2] tracking-[-0.01em] text-[var(--color-ink)] [font-variant-numeric:lining-nums_tabular-nums]"
          >
            {price.text ?? (
              <span className="font-sans text-[14px] text-[var(--color-ink-3)]">Cena na vyžádání</span>
            )}
            {price.perMonth && (
              <span className="ml-0.5 font-sans text-[13px] font-normal tracking-normal text-[var(--color-ink-3)]">
                /měs
              </span>
            )}
          </Link>
          {delta && (
            <span
              title="Změna oproti první zaznamenané ceně"
              className="whitespace-nowrap font-mono text-[11px] tabular-nums text-[var(--color-ink-2)]"
            >
              {delta}
            </span>
          )}
        </div>
        {layout && <p className="truncate text-[13px] text-[var(--color-ink-2)]">{layout}</p>}
        <p className={`truncate text-[13px] ${place ? 'text-[var(--color-ink-2)]' : 'text-[var(--color-ink-4)]'}`}>
          {place ?? 'Lokalita neurčena'}
        </p>
        <p className="mt-0.5 flex flex-wrap gap-x-2 font-mono text-[11px] tabular-nums text-[var(--color-ink-3)]">
          {days && <span>{days}</span>}
          {mf && <span title="Hrubý výnos dle cenové mapy nájemného MF (nájem ÷ cena)">{mf}</span>}
          {!card.is_active && <span>neaktivní</span>}
        </p>
        {!readOnly && (
          <>
            <PhoneCardTags property_id={card.property_id} onOpen={() => onTags(card, sheetSubject)} />
            <ArtifactLinkButton
              property_id={card.property_id}
              className="card-action mt-1 h-7 self-start px-2 text-[12px]"
            />
          </>
        )}
        <DelegatedStamp stamp={card} className="truncate" />
      </div>
      {readOnly ? (
        <span className="flex h-12 w-[52px] shrink-0 items-center justify-center self-center text-[var(--st)]">
          <span aria-hidden className={BADGE}>
            {badge}
          </span>
          <span className="sr-only">Fáze {stage.label}</span>
        </span>
      ) : (
        <button
          type="button"
          aria-haspopup="dialog"
          aria-label={`Fáze ${stage.label}, změnit`}
          onClick={() => onStage(card, sheetSubject)}
          className="card-action group flex h-12 w-[52px] shrink-0 items-center justify-center gap-0.5 self-center text-[var(--st)]"
        >
          <span aria-hidden className={`${BADGE} group-active:bg-[var(--st-soft)]`}>
            {badge}
          </span>
          <ChevronDownIcon className="h-3.5 w-3.5" />
        </button>
      )}
    </article>
  );
}

/* The card's tags as chips — the board card's row (useCardTags), in the
 * phone's type size — and the tap that opens the tag sheet. */
function PhoneCardTags({ property_id, onOpen }: { property_id: number; onOpen: () => void }) {
  const { membersQ, mine } = useCardTags(property_id);
  if (readFailed(membersQ)) {
    return (
      <button
        type="button"
        onClick={() => void membersQ.refetch()}
        className="card-action mt-1 self-start text-[12px] text-[var(--color-brick)]"
      >
        Štítky se nepodařilo načíst — zkusit znovu
      </button>
    );
  }
  return (
    <button
      type="button"
      aria-haspopup="dialog"
      aria-label={mine.length ? `Štítky: ${mine.map((t) => t.name).join(', ')}, upravit` : 'Přidat štítek'}
      onClick={onOpen}
      className="card-action mt-1 flex flex-wrap items-center gap-1 self-start text-left text-[12px] leading-none"
    >
      {mine.length ? (
        mine.map((t) => <TagChip key={t.id} tag={t} />)
      ) : (
        <span className="rounded-[var(--radius-xs)] border border-dashed border-[var(--color-rule)] px-1.5 leading-[1.5] text-[var(--color-ink-4)]">
          + štítek
        </span>
      )}
    </button>
  );
}

/* The sheet reads the membership itself: it acts on the property as the map
 * holds it NOW, not as the card read it when tapped. */
function CardTagSheet({ subject, onClose }: { subject: SheetSubject; onClose: () => void }) {
  const { memberIds } = useCardTags(subject.property_id);
  return <TagSheet subject={subject} memberIds={memberIds} onClose={onClose} />;
}

function Notice({
  title,
  detail,
  action,
}: {
  title: string;
  detail: string;
  action: { label: string; onClick: () => void };
}) {
  return (
    <div className="mx-4 mt-4 flex flex-col items-start gap-2 rounded-[var(--radius-md)] border border-dashed border-[var(--color-rule-strong)] px-4 py-6">
      <p className="font-display text-[17px] text-[var(--color-ink)]">{title}</p>
      <p className="text-[13px] text-[var(--color-ink-3)]">{detail}</p>
      <button
        type="button"
        onClick={action.onClick}
        className="inline-flex h-9 items-center rounded-[var(--radius-sm)] border border-[var(--color-rule-strong)] bg-[var(--color-paper)] px-3 text-[13px] text-[var(--color-ink-2)] active:bg-[var(--color-inset)]"
      >
        {action.label}
      </button>
    </div>
  );
}
