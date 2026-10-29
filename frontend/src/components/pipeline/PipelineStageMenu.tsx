/* THE deal-pipeline card menu (rule #22): move this property to another stage,
 * or take it off the board.
 *
 * One menu for every surface that carries the funnel — the Browse cards and
 * the listing-detail header both open this exact component
 * from their own `<PipelineMark>`. Before it, each surface had its own answer
 * to "the property is already in the pipeline, now what": Browse REMOVED it on
 * a single unconfirmed click (a stray click in a 60-card grid silently dropped
 * a deal, and there was no way to advance one without leaving the page), while
 * the listing header offered a native <select> plus an unconfirmed ✕.
 *
 * Removal is the only destructive action in the pipeline and it has no undo:
 * `remove_card` DELETEs the row, so the card's `added_at` / `entered_stage_at`
 * / board position are gone and re-adding stamps a fresh `added_at` — quietly
 * resetting "in pipeline since" and every time-in-stage figure the board sorts
 * on. (The stage-transition trail survives in `property_pipeline_events`, and
 * operator notes are property-grain — rule #18 — so those are never at risk.)
 * Hence the two-step confirm, matching the kanban trash, and the nudge toward a
 * terminal stage: closing a deal into "Passed / Bought / Lost" keeps the record.
 *
 * TWO CONTAINERS, ONE MENU. The desktop surfaces hang it off their control in an
 * AnchoredPopover; the phone feed (#1766) opens the same body in a bottom sheet
 * (`size="sheet"`: thumb-sized rows, the sheet's own heading). The rows, the
 * writes and the two-step remove are this one component either way.
 */

import { useEffect, useMemo, useRef, useState } from 'react';
import AnchoredPopover from '@/components/AnchoredPopover';
import { CheckIcon } from '@/components/phone/phoneIcons';
import { usePipelineStages } from '@/lib/pipelineReads';
import { REMOVE_QUESTION, removeWarning, stageAccent, stageBadge } from '@/lib/pipelineStage';
import { usePipelineCard } from '@/lib/usePipelineCard';
import type { PipelineStage } from '@/lib/types';

export interface PipelineStageMenuProps {
  property_id: number;
  /* The stage the card sits at now — checked in the list, and never a move target. */
  stageId: number;
  /* The control the menu hangs off (the funnel button / the header pill). */
  anchorRef: React.RefObject<HTMLElement | null>;
  onClose: () => void;
}

export default function PipelineStageMenu({
  property_id,
  stageId,
  anchorRef,
  onClose,
}: PipelineStageMenuProps) {
  return (
    <AnchoredPopover
      anchorRef={anchorRef}
      onClose={onClose}
      ariaLabel="Fáze v pipeline"
      className="w-[15.5rem] py-1"
    >
      <PipelineStageMenuBody
        property_id={property_id}
        stageId={stageId}
        onClose={onClose}
      />
    </AnchoredPopover>
  );
}

/* Class sets per container. `popover` is the desktop menu, unchanged; `sheet`
 * is the phone's 48 px rows (#1762 prototype `.srow` / `.sremove` / `.sconfirm`). */
const SIZES = {
  popover: {
    heading: 'px-2.5 pb-1 pt-1 text-[0.62rem] uppercase tracking-[0.18em] text-[var(--color-ink-4)]',
    loading: 'px-2.5 py-1.5 text-[0.75rem] text-[var(--color-ink-4)]',
    divider:
      'mt-1 border-t border-[var(--color-rule-soft)] px-2.5 pb-1 pt-1.5 text-[0.62rem] uppercase tracking-[0.18em] text-[var(--color-ink-4)]',
    row: 'flex w-full items-center gap-2 px-2.5 py-1.5 text-left text-[0.8rem] transition-colors',
    rowIdle: 'text-[var(--color-ink-2)] hover:bg-[var(--color-paper-2)]',
    badge:
      'inline-flex h-4 min-w-4 shrink-0 items-center justify-center rounded-[var(--radius-xs)] border px-1 font-mono text-[0.62rem] tabular-nums',
    foot: 'mt-1 border-t border-[var(--color-rule-soft)] pt-1',
    confirm: 'px-2.5 py-1.5',
    question: 'text-[0.75rem] font-medium text-[var(--color-ink)]',
    why: 'mt-1 text-[0.7rem] leading-snug text-[var(--color-ink-3)]',
    buttons: 'mt-2 flex items-center gap-1.5',
    danger:
      'rounded-[var(--radius-sm)] border border-[var(--color-brick)] px-2 py-0.5 text-[0.72rem] text-[var(--color-brick)] transition-colors hover:bg-[var(--color-brick)]/10 disabled:opacity-50',
    cancel:
      'rounded-[var(--radius-sm)] border border-[var(--color-rule)] px-2 py-0.5 text-[0.72rem] text-[var(--color-ink-2)] transition-colors hover:border-[var(--color-rule-strong)] hover:bg-[var(--color-rule-soft)]',
    remove:
      'flex w-full items-center px-2.5 py-1.5 text-left text-[0.78rem] text-[var(--color-ink-3)] transition-colors hover:bg-[var(--color-brick-soft)] hover:text-[var(--color-brick)] disabled:opacity-50',
  },
  sheet: {
    heading: '',
    loading: 'px-4 py-3 text-[14px] text-[var(--color-ink-3)]',
    divider:
      'mt-1 border-t border-[var(--color-rule-soft)] px-4 pb-1 pt-3 text-[11px] font-medium uppercase tracking-[0.14em] text-[var(--color-ink-3)]',
    row: 'flex h-12 w-full items-center gap-3 px-4 text-left text-[15px]',
    rowIdle: 'text-[var(--color-ink-2)] active:bg-[var(--color-paper-2)]',
    badge:
      'inline-flex h-5 min-w-8 shrink-0 items-center justify-center rounded-[var(--radius-xs)] border px-1 font-mono text-[11px] font-medium leading-none tabular-nums',
    foot: 'mt-1 border-t border-[var(--color-rule-soft)]',
    confirm: 'flex flex-col gap-1 px-4 pb-1 pt-3',
    question: 'text-[14px] font-medium text-[var(--color-ink)]',
    why: 'text-[12px] leading-[1.45] text-[var(--color-ink-3)]',
    buttons: 'mt-2 flex items-center gap-2',
    danger:
      'inline-flex h-9 items-center rounded-[var(--radius-sm)] border border-[var(--color-brick)] bg-[var(--color-paper)] px-3 text-[13px] text-[var(--color-brick)] active:bg-[var(--color-brick-soft)] disabled:opacity-50',
    cancel:
      'inline-flex h-9 items-center rounded-[var(--radius-sm)] border border-[var(--color-rule-strong)] bg-[var(--color-paper)] px-3 text-[13px] text-[var(--color-ink-2)] active:bg-[var(--color-inset)]',
    remove:
      'flex h-12 w-full items-center px-4 text-left text-[14px] text-[var(--color-ink-3)] active:bg-[var(--color-brick-soft)] active:text-[var(--color-brick)] disabled:opacity-50',
  },
} as const;

export interface PipelineStageMenuBodyProps {
  property_id: number;
  stageId: number;
  onClose: () => void;
  size?: keyof typeof SIZES;
}

export function PipelineStageMenuBody({
  property_id,
  stageId,
  onClose,
  size = 'popover',
}: PipelineStageMenuBodyProps) {
  const cls = SIZES[size];
  const sheet = size === 'sheet';
  const stagesQ = usePipelineStages();
  const stages = useMemo(() => stagesQ.data ?? [], [stagesQ.data]);
  const { move, remove, pending: pendingFor } = usePipelineCard();
  const pending = pendingFor(property_id);
  const [confirming, setConfirming] = useState(false);
  const listRef = useRef<HTMLDivElement>(null);

  /* Open on the current stage, the WAI-ARIA menu default for a radio group —
   * the operator's next move is almost always one step from where they are. */
  useEffect(() => {
    if (confirming || stages.length === 0) return;
    const el = listRef.current?.querySelector<HTMLElement>('[aria-checked="true"]');
    (el ?? listRef.current?.querySelector<HTMLElement>('[role^="menuitem"]'))?.focus();
  }, [confirming, stages.length]);

  /* Roving focus across the menu items (Up/Down/Home/End), so the menu is
   * operable without a mouse once it is open. */
  const onKeyDown = (e: React.KeyboardEvent) => {
    const keys = ['ArrowDown', 'ArrowUp', 'Home', 'End'];
    if (!keys.includes(e.key)) return;
    const items = [
      ...(listRef.current?.querySelectorAll<HTMLElement>(
        '[role^="menuitem"]:not([disabled])',
      ) ?? []),
    ];
    if (items.length === 0) return;
    e.preventDefault();
    const at = items.indexOf(document.activeElement as HTMLElement);
    const next =
      e.key === 'Home'
        ? 0
        : e.key === 'End'
          ? items.length - 1
          : e.key === 'ArrowDown'
            ? (at + 1) % items.length
            : (at - 1 + items.length) % items.length;
    items[next]?.focus();
  };

  const moveTo = (stage: PipelineStage) => {
    if (stage.id === stageId || pending) return;
    move.mutate({ propertyId: property_id, stageId: stage.id });
    onClose();
  };

  /* Index of the first closed stage, so the terminal block reads as the
   * outcomes it is rather than as three more steps. -1 when the operator's
   * board has none (or only terminal stages) and the divider is skipped. */
  const firstTerminal = stages.findIndex((s) => s.is_terminal);
  const hasTerminalSplit = firstTerminal > 0;

  return (
    <div ref={listRef} role="menu" aria-label="Fáze v pipeline" onKeyDown={onKeyDown}>
      {!sheet && <p className={cls.heading}>Přesunout do fáze</p>}

      {stagesQ.isLoading && <p className={cls.loading}>Načítám…</p>}

      {stages.map((stage, i) => {
        const current = stage.id === stageId;
        const accent = stageAccent(stage);
        const badge = stageBadge(stage, stages);
        return (
          <div key={stage.id}>
            {hasTerminalSplit && i === firstTerminal && <p className={cls.divider}>Uzavřené</p>}
            <button
              type="button"
              role="menuitemradio"
              aria-checked={current}
              tabIndex={-1}
              /* NOT disabled when it is the current stage — a disabled button
               * can't hold focus, and this is the item the menu opens on.
               * `moveTo` no-ops instead. */
              disabled={pending}
              onClick={() => moveTo(stage)}
              className={[
                cls.row,
                'disabled:cursor-default disabled:opacity-50',
                current ? 'cursor-default font-medium text-[var(--color-ink)]' : cls.rowIdle,
              ].join(' ')}
              style={current ? { background: accent.soft } : undefined}
            >
              <span
                aria-hidden
                className={cls.badge}
                style={{ color: accent.fg, borderColor: accent.fg }}
              >
                {badge ?? '·'}
              </span>
              <span className="min-w-0 flex-1 truncate">{stage.label}</span>
              {current &&
                (sheet ? (
                  <span className="ml-auto shrink-0" style={{ color: accent.fg }} aria-hidden>
                    <CheckIcon className="h-[18px] w-[18px]" />
                  </span>
                ) : (
                  <span
                    className="shrink-0 text-[0.7rem]"
                    style={{ color: accent.fg }}
                    aria-hidden
                  >
                    ✓
                  </span>
                ))}
            </button>
          </div>
        );
      })}

      <div className={cls.foot}>
        {confirming ? (
          <div className={cls.confirm}>
            <p className={cls.question}>{REMOVE_QUESTION}</p>
            <p className={cls.why}>{removeWarning(stages)}</p>
            <div className={cls.buttons}>
              <button
                type="button"
                role="menuitem"
                tabIndex={-1}
                autoFocus
                disabled={pending}
                onClick={() => {
                  remove.mutate(property_id);
                  onClose();
                }}
                className={cls.danger}
              >
                Odebrat
              </button>
              <button
                type="button"
                role="menuitem"
                tabIndex={-1}
                onClick={() => setConfirming(false)}
                className={cls.cancel}
              >
                Zrušit
              </button>
            </div>
          </div>
        ) : (
          <button
            type="button"
            role="menuitem"
            tabIndex={-1}
            disabled={pending}
            onClick={() => setConfirming(true)}
            className={cls.remove}
          >
            Odebrat z pipeline
          </button>
        )}
      </div>
    </div>
  );
}
