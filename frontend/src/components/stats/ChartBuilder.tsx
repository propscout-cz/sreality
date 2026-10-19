import { useId, useState, type CSSProperties, type MouseEventHandler, type ReactNode, type TouchEventHandler } from 'react';
import {
  DndContext,
  DragOverlay,
  KeyboardSensor,
  MouseSensor,
  TouchSensor,
  closestCenter,
  useDraggable,
  useDroppable,
  useSensor,
  useSensors,
  type Announcements,
  type DragEndEvent,
  type DragStartEvent,
  type DraggableSyntheticListeners,
  type UniqueIdentifier,
} from '@dnd-kit/core';
import {
  SortableContext,
  sortableKeyboardCoordinates,
  useSortable,
  verticalListSortingStrategy,
} from '@dnd-kit/sortable';
import { CSS } from '@dnd-kit/utilities';
import { PickButton } from '@/components/controls';
import { STATS_REGISTRY, type ViewId } from '@/lib/statsRegistry.generated';
import {
  DIMENSION_GROUPS,
  STAT_LABEL,
  VIEW_LABEL,
  chipTarget,
  dimBlock,
  dimLabel,
  dropOutcome,
  maxColsFor,
  maxRowsFor,
  measureLabel,
  moveRow,
  placeDim,
  removeDim,
  viewBlock,
  type ChartSpec,
  type DropTarget,
} from '@/lib/stats';

/* The builder a card flips into: the dimension palette (the filter sidebar's own
 * chips, copper = chosen), the shelves Řádky (nested, in order) and Sloupce (one),
 * and the Hodnota well with the view and statistic pickers. Every rule is the
 * registry's (lib/stats: placeDim / chipTarget / viewBlock / dimBlock).
 *
 * Every drag has a click path, so the keyboard builds any chart: a chip click
 * puts the dimension on the next free shelf (Řádky first), a slot's ↑ ↓ reorder,
 * its "do Sloupců" / "do Řádků" moves it across, its × removes it. The sortable
 * grip also reorders from the keyboard (Space, arrows, Space). */

const ROWS_SHELF = 'shelf:rows';
const COLS_SHELF = 'shelf:cols';

/* A drag source's dimension: a palette chip `p:`, a row slot `r:`, the column slot `c:`. */
const dimOf = (id: UniqueIdentifier): string | null => {
  const s = String(id);
  return /^[prc]:/.test(s) ? s.slice(2) : null;
};

/* The droppables are the two shelves and the row slots (sortable items); the
 * column slot is only ever a drag source. */
const toTarget = (id: UniqueIdentifier): DropTarget | null => {
  const s = String(id);
  if (s === COLS_SHELF) return { shelf: 'cols' };
  if (s === ROWS_SHELF) return { shelf: 'rows' };
  if (s.startsWith('r:')) return { shelf: 'rows', at: s.slice(2) };
  return null;
};

const shelfName = (id: UniqueIdentifier): string => (String(id) === COLS_SHELF ? 'Sloupců' : 'Řádků');

const dragLabel = (id: UniqueIdentifier): string => {
  const dim = dimOf(id);
  return dim ? dimLabel(dim) : String(id);
};

/* Screen-reader narration of a drag, in Czech. The drop says whether the chart
 * took it: a move the view's limits refuse leaves the chart unchanged. */
const announcementsFor = (draft: ChartSpec): Announcements => {
  const outcome = (active: UniqueIdentifier, over: UniqueIdentifier) => {
    const dim = dimOf(active);
    const target = toTarget(over);
    return dim && target ? dropOutcome(draft, dim, target) : null;
  };
  return {
    onDragStart: ({ active }) => `Zvednuto: ${dragLabel(active.id)}.`,
    onDragOver: ({ active, over }) =>
      over
        ? `${dragLabel(active.id)} nad polem ${shelfName(over.id)}.`
        : `${dragLabel(active.id)} není nad žádným polem.`,
    onDragEnd: ({ active, over }) => {
      const result = over ? outcome(active.id, over.id) : null;
      if (!over || !result) return `Vráceno: ${dragLabel(active.id)}.`;
      return result.refused
        ? `${dragLabel(active.id)} nelze položit do ${shelfName(over.id)}.`
        : `Položeno: ${dragLabel(active.id)} do ${shelfName(over.id)}.`;
    },
    onDragCancel: ({ active }) => `Vráceno: ${dragLabel(active.id)}.`,
  };
};

/* Pointer drags bind the mouse and touch starts only — never the keyboard
 * listener — so a chip's Enter / Space stays its click. */
const pointerStarts = (listeners: DraggableSyntheticListeners) => ({
  onMouseDown: listeners?.onMouseDown as MouseEventHandler | undefined,
  onTouchStart: listeners?.onTouchStart as TouchEventHandler | undefined,
});

const INSTRUCTIONS = {
  draggable:
    'Mezerníkem dimenzi zvednete, šipkami ji posunete, mezerníkem položíte, klávesou Escape přesun zrušíte.',
};

export default function ChartBuilder({
  draft,
  onChange,
  categoryMain,
}: {
  draft: ChartSpec;
  onChange: (next: ChartSpec) => void;
  /* The cohort's single category from the preview payload; null when unknown. */
  categoryMain: string | null;
}) {
  const [dragging, setDragging] = useState<UniqueIdentifier | null>(null);
  const sensors = useSensors(
    // distance: a click on a chip or a slot button must stay a click.
    useSensor(MouseSensor, { activationConstraint: { distance: 6 } }),
    // A touch drag is a long press, so a swipe over the palette still scrolls the page.
    useSensor(TouchSensor, { activationConstraint: { delay: 250, tolerance: 5 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  );
  const chosen = new Set([...draft.rows, ...draft.cols]);
  const target = chipTarget(draft);

  const onDragEnd = ({ active, over }: DragEndEvent) => {
    setDragging(null);
    const dim = dimOf(active.id);
    const to = over ? toTarget(over.id) : null;
    if (!dim || !to) return;
    const { next, refused } = dropOutcome(draft, dim, to);
    if (!refused && next !== draft) onChange(next);
  };

  const blockedByCategory = DIMENSION_GROUPS.flatMap((g) => g.dims)
    .map((d) => ({ d, why: dimBlock(d, categoryMain) }))
    .filter((x): x is { d: string; why: string } => x.why != null);

  return (
    <DndContext
      sensors={sensors}
      collisionDetection={closestCenter}
      accessibility={{ announcements: announcementsFor(draft), screenReaderInstructions: INSTRUCTIONS }}
      onDragStart={({ active }: DragStartEvent) => setDragging(active.id)}
      onDragCancel={() => setDragging(null)}
      onDragEnd={onDragEnd}
    >
      <div className="grid gap-5 @3xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        <div role="group" aria-label="Dimenze" className="space-y-3">
          {DIMENSION_GROUPS.map((g) => (
            <PaletteGroup key={g.label} label={g.label}>
              {g.dims.map((d) => {
                const on = chosen.has(d);
                const why = dimBlock(d, categoryMain);
                const full = !on && target == null;
                return (
                  <PaletteChip
                    key={d}
                    dim={d}
                    on={on}
                    // A chosen chip always stays clickable: its click takes it back out.
                    disabled={!on && (why != null || full)}
                    ariaLabel={
                      on
                        ? `Odebrat ${dimLabel(d)} z grafu`
                        : why
                          ? `${dimLabel(d)} — ${why}`
                          : full
                            ? `${dimLabel(d)} — Řádky i Sloupce jsou plné`
                            : `Přidat ${dimLabel(d)} do ${target === 'cols' ? 'Sloupců' : 'Řádků'}`
                    }
                    onClick={() =>
                      onChange(on ? removeDim(draft, d) : target ? placeDim(draft, d, target) : draft)
                    }
                  />
                );
              })}
            </PaletteGroup>
          ))}
          {blockedByCategory.length > 0 && (
            <p className="text-[0.7rem] text-[var(--color-ink-3)]">
              {blockedByCategory.map((x) => dimLabel(x.d)).join(', ')}: {blockedByCategory[0].why}.
            </p>
          )}
          {target == null && (
            <p className="text-[0.7rem] text-[var(--color-ink-3)]">
              Řádky i Sloupce jsou plné — dimenzi nejdřív odeberte.
            </p>
          )}
        </div>

        <div className="space-y-4">
          <RowsShelf draft={draft} onChange={onChange} categoryMain={categoryMain} />
          <ColsShelf draft={draft} onChange={onChange} categoryMain={categoryMain} />
          <ValueWell draft={draft} onChange={onChange} />
        </div>
      </div>

      <DragOverlay dropAnimation={null}>
        {dragging != null ? (
          <span className="inline-flex rounded-[var(--radius-sm)] border border-[var(--color-copper)] bg-[var(--color-paper-3)] px-2 py-1.5 text-xs text-[var(--color-copper)]">
            {dragLabel(dragging)}
          </span>
        ) : null}
      </DragOverlay>
    </DndContext>
  );
}

/* -------------------------------------------------------------------------- */

function ShelfLabel({ id, children, aside }: { id: string; children: ReactNode; aside?: ReactNode }) {
  return (
    <div className="mb-1.5 flex items-baseline justify-between gap-2">
      <p id={id} className="text-[0.62rem] tracking-[0.18em] uppercase text-[var(--color-ink-3)] font-medium">
        {children}
      </p>
      {aside ? <span className="font-mono tabular-nums text-[0.65rem] text-[var(--color-ink-3)]">{aside}</span> : null}
    </div>
  );
}

function PaletteGroup({ label, children }: { label: string; children: ReactNode }) {
  const id = useId();
  return (
    <div role="group" aria-labelledby={id}>
      <ShelfLabel id={id}>{label}</ShelfLabel>
      <div className="flex flex-wrap gap-1">{children}</div>
    </div>
  );
}

function PaletteChip({
  dim,
  on,
  disabled,
  ariaLabel,
  onClick,
}: {
  dim: string;
  on: boolean;
  disabled: boolean;
  ariaLabel: string;
  onClick: () => void;
}) {
  const { setNodeRef, listeners, isDragging } = useDraggable({ id: `p:${dim}`, disabled });
  /* Mouse and touch drags only: the keyboard path is the click, so the chip's
   * Enter / Space never doubles as a keyboard pick-up. No touch-none: the
   * palette fills most of the builder on a phone and must still scroll. */
  return (
    <span ref={setNodeRef} {...pointerStarts(listeners)} className={`inline-flex ${isDragging ? 'opacity-40' : ''}`}>
      <PickButton on={on} disabled={disabled} onClick={onClick} ariaLabel={ariaLabel}>
        {dimLabel(dim)}
      </PickButton>
    </span>
  );
}

/* -------------------------------------------------------------------------- */

const SLOT_BTN =
  'inline-flex h-6 min-w-6 items-center justify-center rounded-[var(--radius-xs)] px-1 text-[0.72rem] text-[var(--color-ink-3)] hover:bg-[var(--color-paper-2)] hover:text-[var(--color-ink)] disabled:opacity-35 disabled:hover:bg-transparent disabled:cursor-not-allowed transition-colors';

function RowsShelf({
  draft,
  onChange,
  categoryMain,
}: {
  draft: ChartSpec;
  onChange: (next: ChartSpec) => void;
  categoryMain: string | null;
}) {
  const id = useId();
  const max = maxRowsFor(draft.view);
  const { setNodeRef, isOver } = useDroppable({ id: ROWS_SHELF });
  const colsFree = maxColsFor(draft.view) > 0;
  return (
    <div role="group" aria-labelledby={id}>
      <ShelfLabel id={id} aside={`${draft.rows.length} z ${max}`}>
        Řádky
      </ShelfLabel>
      <SortableContext items={draft.rows.map((d) => `r:${d}`)} strategy={verticalListSortingStrategy}>
        <ol
          ref={setNodeRef}
          className={[
            'space-y-1 rounded-[var(--radius-sm)] border bg-[var(--color-inset)] p-1.5 transition-colors',
            isOver ? 'border-[var(--color-copper)]' : 'border-[var(--color-rule)]',
          ].join(' ')}
        >
          {draft.rows.map((d, i) => (
            <RowSlot
              key={d}
              dim={d}
              index={i}
              last={i === draft.rows.length - 1}
              note={dimBlock(d, categoryMain)}
              canToCols={colsFree}
              onUp={() => onChange(moveRow(draft, i, i - 1))}
              onDown={() => onChange(moveRow(draft, i, i + 1))}
              onToCols={() => onChange(placeDim(draft, d, 'cols'))}
              onRemove={() => onChange(removeDim(draft, d))}
            />
          ))}
          {draft.rows.length < max && (
            <li
              className="flex h-8 items-center rounded-[var(--radius-xs)] border border-dashed border-[var(--color-rule-strong)] px-2 text-[0.72rem] text-[var(--color-ink-2)]"
              style={{ marginLeft: `${draft.rows.length * 0.75}rem` }}
            >
              {draft.rows.length === 0 ? 'Bez řádků: jeden řádek za celý vzorek' : 'Další úroveň — klikněte na dimenzi nebo ji sem přetáhněte'}
            </li>
          )}
        </ol>
      </SortableContext>
    </div>
  );
}

function RowSlot({
  dim,
  index,
  last,
  note,
  canToCols,
  onUp,
  onDown,
  onToCols,
  onRemove,
}: {
  dim: string;
  index: number;
  last: boolean;
  note: string | null;
  canToCols: boolean;
  onUp: () => void;
  onDown: () => void;
  onToCols: () => void;
  onRemove: () => void;
}) {
  const label = dimLabel(dim);
  const { attributes, listeners, setNodeRef, setActivatorNodeRef, transform, transition, isDragging } =
    useSortable({ id: `r:${dim}`, attributes: { roleDescription: 'řaditelná dimenze' } });
  const style: CSSProperties = {
    transform: CSS.Transform.toString(transform),
    transition,
    marginLeft: `${index * 0.75}rem`,
  };
  return (
    <li
      ref={setNodeRef}
      style={style}
      className={[
        'flex h-8 items-center gap-1 rounded-[var(--radius-xs)] border border-[var(--color-rule)] bg-[var(--color-paper-3)] pr-1',
        isDragging ? 'opacity-60' : '',
      ].join(' ')}
    >
      <button
        type="button"
        ref={setActivatorNodeRef}
        {...attributes}
        {...listeners}
        aria-label={`Přesunout ${label} (úroveň ${index + 1})`}
        className="flex h-full cursor-grab touch-none items-center px-1.5 text-[var(--color-ink-3)] hover:text-[var(--color-ink)] active:cursor-grabbing"
      >
        <Grip />
      </button>
      <span className="font-mono tabular-nums text-[0.65rem] text-[var(--color-ink-3)]">{index + 1}</span>
      <span className="min-w-0 truncate text-[0.8rem] text-[var(--color-ink)]">{label}</span>
      {note ? <span className="text-[0.65rem] text-[var(--color-brick)] whitespace-nowrap">{note}</span> : null}
      <span className="ml-auto flex items-center">
        <button type="button" className={SLOT_BTN} onClick={onUp} disabled={index === 0} aria-label={`Posunout ${label} výš`}>
          ↑
        </button>
        <button type="button" className={SLOT_BTN} onClick={onDown} disabled={last} aria-label={`Posunout ${label} níž`}>
          ↓
        </button>
        {canToCols && (
          <button type="button" className={SLOT_BTN} onClick={onToCols} aria-label={`Přesunout ${label} do Sloupců`}>
            do Sloupců
          </button>
        )}
        <button type="button" className={SLOT_BTN} onClick={onRemove} aria-label={`Odebrat ${label}`}>
          ×
        </button>
      </span>
    </li>
  );
}

function ColsShelf({
  draft,
  onChange,
  categoryMain,
}: {
  draft: ChartSpec;
  onChange: (next: ChartSpec) => void;
  categoryMain: string | null;
}) {
  const id = useId();
  const max = maxColsFor(draft.view);
  const { setNodeRef, isOver } = useDroppable({ id: COLS_SHELF, disabled: max === 0 });
  const dim = draft.cols[0];
  const rowsFree = draft.rows.length < maxRowsFor(draft.view);
  return (
    <div role="group" aria-labelledby={id}>
      <ShelfLabel id={id} aside={max === 0 ? undefined : `${draft.cols.length} z ${max}`}>
        Sloupce
      </ShelfLabel>
      <div
        ref={setNodeRef}
        className={[
          'rounded-[var(--radius-sm)] border bg-[var(--color-inset)] p-1.5 transition-colors',
          isOver ? 'border-[var(--color-copper)]' : 'border-[var(--color-rule)]',
        ].join(' ')}
      >
        {max === 0 ? (
          <p className="flex h-8 items-center px-2 text-[0.72rem] text-[var(--color-ink-2)]">
            Krabice nemá Sloupce — dimenze patří do Řádků.
          </p>
        ) : dim ? (
          <ColSlot
            dim={dim}
            note={dimBlock(dim, categoryMain)}
            canToRows={rowsFree}
            onToRows={() => onChange(placeDim(draft, dim, 'rows'))}
            onRemove={() => onChange(removeDim(draft, dim))}
          />
        ) : (
          <p className="flex h-8 items-center rounded-[var(--radius-xs)] border border-dashed border-[var(--color-rule-strong)] px-2 text-[0.72rem] text-[var(--color-ink-2)]">
            Bez sloupců — přetáhněte sem dimenzi pro řady nebo sloupce tabulky
          </p>
        )}
      </div>
    </div>
  );
}

function ColSlot({
  dim,
  note,
  canToRows,
  onToRows,
  onRemove,
}: {
  dim: string;
  note: string | null;
  canToRows: boolean;
  onToRows: () => void;
  onRemove: () => void;
}) {
  const label = dimLabel(dim);
  const { setNodeRef, listeners, isDragging } = useDraggable({ id: `c:${dim}` });
  return (
    <div
      ref={setNodeRef}
      className={[
        'flex h-8 items-center gap-1 rounded-[var(--radius-xs)] border border-[var(--color-rule)] bg-[var(--color-paper-3)] pr-1',
        isDragging ? 'opacity-60' : '',
      ].join(' ')}
    >
      <span
        {...pointerStarts(listeners)}
        aria-hidden="true"
        className="flex h-full cursor-grab touch-none items-center px-1.5 text-[var(--color-ink-3)]"
      >
        <Grip />
      </span>
      <span className="min-w-0 truncate text-[0.8rem] text-[var(--color-ink)]">{label}</span>
      {note ? <span className="text-[0.65rem] text-[var(--color-brick)] whitespace-nowrap">{note}</span> : null}
      <span className="ml-auto flex items-center">
        {canToRows && (
          <button type="button" className={SLOT_BTN} onClick={onToRows} aria-label={`Přesunout ${label} do Řádků`}>
            do Řádků
          </button>
        )}
        <button type="button" className={SLOT_BTN} onClick={onRemove} aria-label={`Odebrat ${label}`}>
          ×
        </button>
      </span>
    </div>
  );
}

/* -------------------------------------------------------------------------- */

function ValueWell({ draft, onChange }: { draft: ChartSpec; onChange: (next: ChartSpec) => void }) {
  const id = useId();
  const viewNotes = STATS_REGISTRY.views
    .map((v) => ({ v, why: viewBlock(draft, v) }))
    .filter((x): x is { v: ViewId; why: string } => x.why != null && x.v !== draft.view);
  const statUsed = draft.view === 'line' || draft.view === 'bar' || (draft.view === 'table' && draft.cols.length > 0);
  /* The well holds three pickers; only the measure group is named "Hodnota". */
  return (
    <div className="space-y-3 rounded-[var(--radius-sm)] border border-[var(--color-rule)] bg-[var(--color-inset)] p-3">
      <div>
        <ShelfLabel id={id}>Hodnota</ShelfLabel>
        <div role="group" aria-labelledby={id} className="flex flex-wrap gap-1">
          {STATS_REGISTRY.measures.map((m) => (
            <PickButton
              key={m.id}
              on={draft.measure === m.id}
              onClick={() => onChange({ ...draft, measure: m.id })}
              ariaLabel={`Hodnota: ${measureLabel(m.id)}`}
            >
              {m.label_cs}
            </PickButton>
          ))}
        </div>
      </div>
      <Picker label="Zobrazení">
        {STATS_REGISTRY.views.map((v) => (
          <PickButton
            key={v}
            on={draft.view === v}
            disabled={draft.view !== v && viewBlock(draft, v) != null}
            onClick={() => onChange({ ...draft, view: v })}
            ariaLabel={`Zobrazení: ${VIEW_LABEL[v]}`}
          >
            {VIEW_LABEL[v]}
          </PickButton>
        ))}
      </Picker>
      {viewNotes.length > 0 && (
        <p className="-mt-1.5 text-[0.7rem] text-[var(--color-ink-3)]">
          {viewNotes.map((x) => `${VIEW_LABEL[x.v]}: ${x.why}`).join(' · ')}
        </p>
      )}
      {statUsed && (
        <Picker label="Statistika">
          {STATS_REGISTRY.chart_stats.map((s) => (
            <PickButton
              key={s}
              on={draft.stat === s}
              onClick={() => onChange({ ...draft, stat: s })}
              ariaLabel={`Statistika: ${STAT_LABEL[s]}`}
            >
              {STAT_LABEL[s]}
            </PickButton>
          ))}
        </Picker>
      )}
    </div>
  );
}

function Picker({ label, children }: { label: string; children: ReactNode }) {
  const id = useId();
  return (
    <div>
      <ShelfLabel id={id}>{label}</ShelfLabel>
      <div role="group" aria-labelledby={id} className="flex flex-wrap gap-1">
        {children}
      </div>
    </div>
  );
}

function Grip() {
  return (
    <svg width="8" height="12" viewBox="0 0 8 12" aria-hidden="true" fill="currentColor">
      <circle cx="2" cy="2" r="1" />
      <circle cx="6" cy="2" r="1" />
      <circle cx="2" cy="6" r="1" />
      <circle cx="6" cy="6" r="1" />
      <circle cx="2" cy="10" r="1" />
      <circle cx="6" cy="10" r="1" />
    </svg>
  );
}
