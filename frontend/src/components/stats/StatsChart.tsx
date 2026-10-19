import { Suspense, useEffect, useId, useMemo, useRef, useState, type ReactNode } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import type { ListingFilters } from '@/lib/filters';
import { lazyChunk } from '@/lib/lazyChunk';
import Skeleton from '@/components/Skeleton';
import {
  LINE_NEEDS_ROW,
  MAX_SERIES,
  chartProblem,
  chartTitle,
  dimBlock,
  dimLabel,
  fetchStatsPivot,
  refusalText,
  seriesCount,
  statsPivotKey,
  tooManySeriesText,
  viewBlock,
  type ChartSpec,
  type StatsPivot,
  type StatsSpec,
} from '@/lib/stats';
import ChartBuilder from './ChartBuilder';
import SampleLedger from './SampleLedger';
import PivotTable from './views/PivotTable';
import BoxTable from './views/BoxTable';

/* recharts stays out of the Browse chunk until a line or bar chart is on screen. */
const PivotLines = lazyChunk(() => import('./views/PivotLines'));
const PivotBars = lazyChunk(() => import('./views/PivotBars'));

/* A transiently unavailable action (a save in flight) is aria-disabled, not
 * disabled, so it keeps keyboard focus; a structurally unavailable one is disabled. */
const ACTION =
  'rounded-[var(--radius-xs)] px-1.5 py-1 text-[0.75rem] text-[var(--color-ink-3)] hover:bg-[var(--color-paper)] hover:text-[var(--color-ink)] disabled:opacity-35 disabled:hover:bg-transparent disabled:cursor-not-allowed aria-disabled:opacity-35 aria-disabled:hover:bg-transparent aria-disabled:cursor-not-allowed transition-colors';

type FocusTarget = 'heading' | 'edit' | 'remove' | 'keep' | 'earlier' | 'later' | 'builder';

/* Focus the board asks this card to take after it moved the card or removed its
 * neighbour (the pressed control was re-inserted or is gone). `seq` re-fires an
 * identical request. */
export interface CardFocus {
  target: Extract<FocusTarget, 'heading' | 'edit' | 'earlier' | 'later'>;
  seq: number;
}

/* One card of the board: the derived title, the sample ledger, the view. Upravit
 * flips THIS card (same box, same width) into the builder over a live preview;
 * Uložit hands the chart back and the card leaves edit mode once the board has
 * saved it (a failed save keeps the draft); Zrušit drops the draft. Odebrat asks
 * once. Every flip moves keyboard focus to where the next action is, because
 * the control just pressed is gone. */
export default function StatsChart({
  chart,
  filters,
  onSave,
  onRemove,
  onMove,
  startEditing = false,
  onCancelNew,
  saving = false,
  focus = null,
}: {
  chart: ChartSpec;
  filters: ListingFilters;
  /* Settles when the board's save does; a rejection keeps the card in edit mode. */
  onSave: (next: ChartSpec) => Promise<unknown>;
  onRemove: () => void;
  /* −1 / +1 within the board; absent where the card cannot move that way. */
  onMove?: { earlier?: () => void; later?: () => void };
  startEditing?: boolean;
  /* A card that was never saved: Zrušit discards it instead of reverting. */
  onCancelNew?: () => void;
  saving?: boolean;
  focus?: CardFocus | null;
}) {
  const client = useQueryClient();
  const headingId = useId();
  const [editing, setEditing] = useState(startEditing);
  const [draft, setDraft] = useState<ChartSpec>(chart);
  const [confirming, setConfirming] = useState(false);
  const shown = editing ? draft : chart;
  const spec: StatsSpec = useMemo(
    () => ({ rows: shown.rows, cols: shown.cols, measure: shown.measure }),
    [shown.rows, shown.cols, shown.measure],
  );

  const pivot = useQuery<StatsPivot, Error>({
    queryKey: statsPivotKey(filters, spec),
    queryFn: ({ signal }) => fetchStatsPivot(filters, spec, { signal, client }),
    placeholderData: (prev) => prev,
  });

  const data = pivot.data ?? null;
  const title = chartTitle(shown, data?.basis ?? null);
  const problem = editing ? chartProblem(draft) : null;

  const headingRef = useRef<HTMLHeadingElement>(null);
  const editRef = useRef<HTMLButtonElement>(null);
  const removeRef = useRef<HTMLButtonElement>(null);
  const keepRef = useRef<HTMLButtonElement>(null);
  const earlierRef = useRef<HTMLButtonElement>(null);
  const laterRef = useRef<HTMLButtonElement>(null);
  const builderRef = useRef<HTMLDivElement>(null);
  const focusTo = (t: FocusTarget) => {
    const el = {
      heading: headingRef, edit: editRef, remove: removeRef, keep: keepRef,
      earlier: earlierRef, later: laterRef, builder: builderRef,
    }[t].current;
    if (el && !(el instanceof HTMLButtonElement && el.disabled)) el.focus();
    else headingRef.current?.focus();
  };

  /* A flip of this card queues its focus target; the effect lands it once the
   * flipped markup is in the DOM. A new card opens in the builder. */
  const focusNext = useRef<FocusTarget | null>(startEditing ? 'builder' : null);
  useEffect(() => {
    const t = focusNext.current;
    if (t == null) return;
    focusNext.current = null;
    focusTo(t);
  });
  /* The board's request (a move, a removed neighbour, a first save): the
   * request object is the trigger; focusTo reads refs only. */
  useEffect(() => {
    if (focus) focusTo(focus.target);
  }, [focus]);

  const begin = () => {
    setDraft(chart);
    setConfirming(false);
    setEditing(true);
    focusNext.current = 'builder';
  };
  const cancel = () => {
    if (onCancelNew) return onCancelNew();
    setEditing(false);
    focusNext.current = 'edit';
  };
  const save = async () => {
    if (problem || saving) return;
    try {
      await onSave(draft);
    } catch {
      return; // the board rolled back and said why; the draft stays to retry
    }
    setEditing(false);
    focusNext.current = 'edit';
  };
  const askRemove = () => {
    if (saving) return;
    setConfirming(true);
    focusNext.current = 'keep';
  };
  const keep = () => {
    setConfirming(false);
    focusNext.current = 'remove';
  };

  return (
    <article
      aria-labelledby={headingId}
      className="@container min-w-0 rounded-[var(--radius-md)] border border-[var(--color-rule)] bg-[var(--color-paper-2)]"
    >
      <header className="flex items-start gap-3 px-5 pt-4">
        <h3 id={headingId} ref={headingRef} tabIndex={-1} className="min-w-0 text-[1.02rem] leading-snug">
          {title}
        </h3>
        {!editing && (
          <div className="ml-auto flex shrink-0 items-center gap-0.5">
            {confirming ? (
              <>
                <span className="mr-1 text-[0.75rem] text-[var(--color-ink-2)]">Odebrat graf?</span>
                <button
                  type="button"
                  className="rounded-[var(--radius-xs)] px-1.5 py-1 text-[0.75rem] font-medium text-[var(--color-brick)] hover:bg-[var(--color-brick-soft)] aria-disabled:opacity-35 aria-disabled:cursor-not-allowed transition-colors"
                  onClick={saving ? undefined : onRemove}
                  aria-disabled={saving || undefined}
                  aria-label={`Ano, odebrat graf ${title}`}
                >
                  Odebrat
                </button>
                <button ref={keepRef} type="button" className={ACTION} onClick={keep} aria-label="Ponechat graf">
                  Ponechat
                </button>
              </>
            ) : (
              <>
                {onMove && (
                  <>
                    <button
                      ref={earlierRef}
                      type="button"
                      className={ACTION}
                      onClick={saving ? undefined : onMove.earlier}
                      disabled={!onMove.earlier}
                      aria-disabled={saving || undefined}
                      aria-label={`Posunout graf ${title} dopředu`}
                    >
                      ↑
                    </button>
                    <button
                      ref={laterRef}
                      type="button"
                      className={ACTION}
                      onClick={saving ? undefined : onMove.later}
                      disabled={!onMove.later}
                      aria-disabled={saving || undefined}
                      aria-label={`Posunout graf ${title} dozadu`}
                    >
                      ↓
                    </button>
                  </>
                )}
                <button ref={editRef} type="button" className={ACTION} onClick={begin} aria-label={`Upravit graf ${title}`}>
                  Upravit
                </button>
                <button
                  ref={removeRef}
                  type="button"
                  className={ACTION}
                  onClick={askRemove}
                  aria-disabled={saving || undefined}
                  aria-label={`Odebrat graf ${title}`}
                >
                  Odebrat
                </button>
              </>
            )}
          </div>
        )}
      </header>

      {editing && (
        <div
          ref={builderRef}
          tabIndex={-1}
          role="group"
          aria-label={`Úprava grafu ${title}`}
          className="mx-5 mt-3 border-b border-[var(--color-rule-soft)] pb-4"
        >
          <ChartBuilder draft={draft} onChange={setDraft} categoryMain={data?.category_main ?? null} />
        </div>
      )}

      <div className="px-5 pt-3">
        {data ? (
          <SampleLedger pivot={data} />
        ) : pivot.isLoading ? (
          /* The ledger's footprint while the first answer is on its way, so the
           * view does not jump when the counts land. */
          <div aria-hidden="true" className="space-y-1.5">
            <div className="h-3.5 w-2/3 rounded-[var(--radius-xs)] bg-[var(--color-paper)]" />
            <div className="h-[3px] w-full rounded-full bg-[var(--color-rule)]" />
          </div>
        ) : null}
      </div>

      <div
        className={`px-5 pt-4 pb-5 transition-opacity ${pivot.isPlaceholderData ? 'opacity-60' : ''}`}
        aria-busy={pivot.isFetching}
      >
        <Body chart={shown} pivot={data} loading={pivot.isLoading} error={pivot.error} onRetry={() => void pivot.refetch()} />
      </div>

      {editing && (
        <footer className="flex flex-wrap items-center justify-end gap-2 border-t border-[var(--color-rule-soft)] px-5 py-3">
          {problem && <p className="mr-auto text-[0.75rem] text-[var(--color-ink-3)]">{problem}</p>}
          <button
            type="button"
            onClick={cancel}
            className="rounded-[var(--radius-sm)] px-2.5 py-1 text-[0.8rem] text-[var(--color-ink-2)] hover:text-[var(--color-ink)] transition-colors"
            aria-label="Zrušit úpravy grafu"
          >
            Zrušit
          </button>
          <button
            type="button"
            onClick={() => void save()}
            disabled={problem != null || saving}
            className="rounded-[var(--radius-sm)] bg-[var(--color-copper)] px-2.5 py-1 text-[0.8rem] text-white hover:bg-[var(--color-copper-2)] disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
            aria-label="Uložit graf"
          >
            Uložit
          </button>
        </footer>
      )}
    </article>
  );
}

function Body({
  chart,
  pivot,
  loading,
  error,
  onRetry,
}: {
  chart: ChartSpec;
  pivot: StatsPivot | null;
  loading: boolean;
  error: Error | null;
  onRetry: () => void;
}) {
  if (error && !pivot) {
    return (
      <p className="text-[0.8rem] text-[var(--color-brick)]">
        Graf se nepodařilo načíst: {error.message}{' '}
        <button
          type="button"
          onClick={onRetry}
          aria-label="Načíst graf znovu"
          className="font-medium underline underline-offset-2 hover:no-underline"
        >
          Zkusit znovu
        </button>
      </p>
    );
  }
  if (loading || !pivot) return <Skeleton height={96} />;
  if (pivot.refused) return <Refusal>{refusalText(pivot.refused)}</Refusal>;
  /* The payload answered a different spec (a placeholder while the preview's
   * new one loads) that this view cannot draw: hold the old picture back. */
  if (viewBlock(pivot.spec, chart.view) != null) return null;
  if (pivot.sample === 0) {
    const off = pivot.spec.rows.concat(pivot.spec.cols).filter((d) => dimBlock(d, pivot.category_main) != null);
    return (
      <Refusal>
        Žádná nemovitost ve filtru nemá všechny potřebné údaje.
        {off.length > 0 ? ` ${off.map(dimLabel).join(', ')}: ${dimBlock(off[0], pivot.category_main)}.` : ''}
      </Refusal>
    );
  }
  switch (chart.view) {
    case 'table':
      return <PivotTable pivot={pivot} stat={chart.stat} />;
    case 'box':
      return <BoxTable pivot={pivot} />;
    case 'line':
    case 'bar': {
      if (pivot.spec.rows.length !== 1) return <Refusal>{LINE_NEEDS_ROW}</Refusal>;
      if (seriesCount(pivot) > MAX_SERIES) return <Refusal>{tooManySeriesText()}</Refusal>;
      const View = chart.view === 'line' ? PivotLines : PivotBars;
      return (
        <Suspense fallback={<Skeleton height={240} />}>
          <View pivot={pivot} stat={chart.stat} />
        </Suspense>
      );
    }
  }
}

/* A refusal is one sentence in the card body — on the same dashed vocabulary as
 * a cell under the minimum: a number the chart will not draw. */
function Refusal({ children }: { children: ReactNode }) {
  return (
    <p className="rounded-[var(--radius-sm)] border border-dashed border-[var(--color-rule-strong)] px-4 py-3 text-[0.8rem] leading-relaxed text-[var(--color-ink-2)]">
      {children}
    </p>
  );
}
