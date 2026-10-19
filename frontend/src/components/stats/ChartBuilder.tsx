import { useId, useMemo, useState, type CSSProperties, type MouseEventHandler, type ReactNode, type TouchEventHandler } from 'react';
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
import { Label, PickButton } from '@/components/controls';
import { STATS_REGISTRY } from '@/lib/statsRegistry.generated';
import {
  DIMENSION_GROUPS,
  STAT_LABEL,
  VIEW_LABEL,
  categoryBlockNote,
  chipTarget,
  dimBlock,
  dimLabel,
  dropOutcome,
  maxColsFor,
  maxRowsFor,
  measureLabel,
  moveRow,
  noColumnsText,
  placeDim,
  removeDim,
  statUsed,
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

/* What dropping `active` on `over` does to the draft; null when it is no drop. */
const outcomeOf = (draft: ChartSpec, active: UniqueIdentifier, over: UniqueIdentifier | undefined) => {
  const dim = dimOf(active);
  const target = over != null ? toTarget(over) : null;
  return dim && target ? dropOutcome(draft, dim, target) : null;
};

/* Screen-reader narration of a drag, in Czech. The drop says whether the chart
 * took it: a move the view's limits refuse leaves the chart unchanged. */
const announcementsFor = (draft: ChartSpec): Announcements => ({
  onDragStart: ({ active }) => `Zvednuto: ${dragLabel(active.id)}.`,
  onDragOver: ({ active, over }) =>
    over
      ? `${dragLabel(active.id)} nad polem ${shelfName(over.id)}.`
      : `${dragLabel(active.id)} není nad žádným polem.`,
  onDragEnd: ({ active, over }) => {
    const result = outcomeOf(draft, active.id, over?.id);
    if (!over || !result) return `Vráceno: ${dragLabel(active.id)}.`;
    return result.refused
      ? `${dragLabel(active.id)} nelze položit do ${shelfName(over.id)}.`
      : `Položeno: ${dragLabel(active.id)} do ${shelfName(over.id)}.`;
  },
  onDragCancel: ({ active }) => `Vráceno: ${dragLabel(active.id)}.`,
});

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

const PALETTE_DIMS = DIMENSION_GROUPS.flatMap((g) => g.dims);

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
  /* Why each dimension is ruled out for the cohort's category (null = it is not):
   * read by the chips and the slots alike, with the one sentence that names them. */
  const { why, note } = useMemo(
    () => ({
      why: new Map(PALETTE_DIMS.map((d) => [d, dimBlock(d, categoryMain)])),
      note: categoryBlockNote(PALETTE_DIMS, categoryMain),
    }),
    [categoryMain],
  );
  const announcements = useMemo(() => announcementsFor(draft), [draft]);

  const onDragEnd = ({ active, over }: DragEndEvent) => {
    setDragging(null);
    const next = outcomeOf(draft, active.id, over?.id)?.next;
    if (next && next !== draft) onChange(next);
  };

  return (
    <DndContext
      sensors={sensors}
      collisionDetection={closestCenter}
      accessibility={{ announcements, screenReaderInstructions: INSTRUCTIONS }}
      onDragStart={({ active }: DragStartEvent) => setDragging(active.id)}
      onDragCancel={() => setDragging(null)}
      onDragEnd={onDragEnd}
    >
      <div className="grid gap-5 @3xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        <div role="group" aria-label="Dimenze" className="space-y-3">
          {DIMENSION_GROUPS.map((g) => (
            <Picker key={g.label} label={g.label}>
              {g.dims.map((d) => {
                const on = chosen.has(d);
                const off = why.get(d) ?? null;
                const full = !on && target == null;
                return (
                  <PaletteChip
                    key={d}
                    dim={d}
                    on={on}
                    // A chosen chip always stays clickable: its click takes it back out.
                    disabled={!on && (off != null || full)}
                    ariaLabel={
                      on
                        ? `Odebrat ${dimLabel(d)} z grafu`
                        : off
                          ? `${dimLabel(d)} — ${off}`
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
            </Picker>
          ))}
          {note && <p className="text-[0.7rem] text-[var(--color-ink-3)]">{note}</p>}
          {target == null && (
            <p className="text-[0.7rem] text-[var(--color-ink-3)]">
              Řádky i Sloupce jsou plné — dimenzi nejdřív odeberte.
            </p>
          )}
        </div>

        <div className="space-y-4">
          <RowsShelf draft={draft} onChange={onChange} why={why} />
          <ColsShelf draft={draft} onChange={onChange} why={why} />
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
      <Label id={id}>{children}</Label>
      {aside ? <span className="font-mono tabular-nums text-[0.65rem] text-[var(--color-ink-3)]">{aside}</span> : null}
    </div>
  );
}

/* A labelled row of pick buttons: a palette group, the value, the view, the statistic. */
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

type Why = ReadonlyMap<string, string | null>;

const SLOT_BTN =
  'inline-flex h-6 min-w-6 items-center justify-center rounded-[var(--radius-xs)] px-1 text-[0.72rem] text-[var(--color-ink-3)] hover:bg-[var(--color-paper-2)] hover:text-[var(--color-ink)] disabled:opacity-35 disabled:hover:bg-transparent disabled:cursor-not-allowed transition-colors';

/* A filled slot, in Řádky or Sloupce. */
const SLOT =
  'flex h-8 items-center gap-1 rounded-[var(--radius-xs)] border border-[var(--color-rule)] bg-[var(--color-paper-3)] pr-1';

/* An empty slot: the dashed mark of something absent. */
const PLACEHOLDER =
  'flex h-8 items-center rounded-[var(--radius-xs)] border border-dashed border-[var(--color-rule-strong)] px-2 text-[0.72rem] text-[var(--color-ink-2)]';

/* A shelf: its label (with the fill count), and the well a drag drops into. */
function ShelfFrame({
  label,
  aside,
  list,
  dropRef,
  over,
  children,
}: {
  label: string;
  aside?: string;
  /* Řádky are an ordered list; Sloupce one slot. */
  list: boolean;
  dropRef: (el: HTMLElement | null) => void;
  over: boolean;
  children: ReactNode;
}) {
  const id = useId();
  const well = `rounded-[var(--radius-sm)] border bg-[var(--color-inset)] p-1.5 transition-colors ${
    over ? 'border-[var(--color-copper)]' : 'border-[var(--color-rule)]'
  }`;
  return (
    <div role="group" aria-labelledby={id}>
      <ShelfLabel id={id} aside={aside}>
        {label}
      </ShelfLabel>
      {list ? (
        <ol ref={dropRef} className={`space-y-1 ${well}`}>
          {children}
        </ol>
      ) : (
        <div ref={dropRef} className={well}>
          {children}
        </div>
      )}
    </div>
  );
}

function RowsShelf({ draft, onChange, why }: { draft: ChartSpec; onChange: (next: ChartSpec) => void; why: Why }) {
  const max = maxRowsFor(draft.view);
  const { setNodeRef, isOver } = useDroppable({ id: ROWS_SHELF });
  return (
    <SortableContext items={draft.rows.map((d) => `r:${d}`)} strategy={verticalListSortingStrategy}>
      <ShelfFrame label="Řádky" aside={`${draft.rows.length} z ${max}`} list dropRef={setNodeRef} over={isOver}>
        {draft.rows.map((d, i) => {
          const toCols = placeDim(draft, d, 'cols');
          return (
            <RowSlot
              key={d}
              dim={d}
              index={i}
              last={i === draft.rows.length - 1}
              note={why.get(d) ?? null}
              onUp={() => onChange(moveRow(draft, i, i - 1))}
              onDown={() => onChange(moveRow(draft, i, i + 1))}
              onToCols={toCols !== draft ? () => onChange(toCols) : undefined}
              onRemove={() => onChange(removeDim(draft, d))}
            />
          );
        })}
        {draft.rows.length < max && (
          <li className={PLACEHOLDER} style={{ marginLeft: `${draft.rows.length * 0.75}rem` }}>
            {draft.rows.length === 0 ? 'Bez řádků: jeden řádek za celý vzorek' : 'Další úroveň — klikněte na dimenzi nebo ji sem přetáhněte'}
          </li>
        )}
      </ShelfFrame>
    </SortableContext>
  );
}

/* What a row slot and the column slot share: the label, the category note, and
 * the slot's buttons ending in its ×. */
function SlotBody({
  label,
  note,
  onRemove,
  children,
}: {
  label: string;
  note: string | null;
  onRemove: () => void;
  children: ReactNode;
}) {
  return (
    <>
      <span className="min-w-0 truncate text-[0.8rem] text-[var(--color-ink)]">{label}</span>
      {note ? <span className="text-[0.65rem] text-[var(--color-brick)] whitespace-nowrap">{note}</span> : null}
      <span className="ml-auto flex items-center">
        {children}
        <button type="button" className={SLOT_BTN} onClick={onRemove} aria-label={`Odebrat ${label}`}>
          ×
        </button>
      </span>
    </>
  );
}

function RowSlot({
  dim,
  index,
  last,
  note,
  onUp,
  onDown,
  onToCols,
  onRemove,
}: {
  dim: string;
  index: number;
  last: boolean;
  note: string | null;
  onUp: () => void;
  onDown: () => void;
  /* Absent where the view takes no column. */
  onToCols?: () => void;
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
    <li ref={setNodeRef} style={style} className={`${SLOT}${isDragging ? ' opacity-60' : ''}`}>
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
      <SlotBody label={label} note={note} onRemove={onRemove}>
        <button type="button" className={SLOT_BTN} onClick={onUp} disabled={index === 0} aria-label={`Posunout ${label} výš`}>
          ↑
        </button>
        <button type="button" className={SLOT_BTN} onClick={onDown} disabled={last} aria-label={`Posunout ${label} níž`}>
          ↓
        </button>
        {onToCols && (
          <button type="button" className={SLOT_BTN} onClick={onToCols} aria-label={`Přesunout ${label} do Sloupců`}>
            do Sloupců
          </button>
        )}
      </SlotBody>
    </li>
  );
}

function ColsShelf({ draft, onChange, why }: { draft: ChartSpec; onChange: (next: ChartSpec) => void; why: Why }) {
  const max = maxColsFor(draft.view);
  const { setNodeRef, isOver } = useDroppable({ id: COLS_SHELF, disabled: max === 0 });
  const dim = draft.cols[0];
  const toRows = dim ? placeDim(draft, dim, 'rows') : draft;
  return (
    <ShelfFrame
      label="Sloupce"
      aside={max === 0 ? undefined : `${draft.cols.length} z ${max}`}
      list={false}
      dropRef={setNodeRef}
      over={isOver}
    >
      {max === 0 ? (
        <p className="flex h-8 items-center px-2 text-[0.72rem] text-[var(--color-ink-2)]">{noColumnsText(draft.view)}</p>
      ) : dim ? (
        <ColSlot
          dim={dim}
          note={why.get(dim) ?? null}
          onToRows={toRows !== draft ? () => onChange(toRows) : undefined}
          onRemove={() => onChange(removeDim(draft, dim))}
        />
      ) : (
        <p className={PLACEHOLDER}>Bez sloupců — přetáhněte sem dimenzi pro řady nebo sloupce tabulky</p>
      )}
    </ShelfFrame>
  );
}

function ColSlot({
  dim,
  note,
  onToRows,
  onRemove,
}: {
  dim: string;
  note: string | null;
  /* Absent while Řádky are full. */
  onToRows?: () => void;
  onRemove: () => void;
}) {
  const label = dimLabel(dim);
  const { setNodeRef, listeners, isDragging } = useDraggable({ id: `c:${dim}` });
  return (
    <div ref={setNodeRef} className={`${SLOT}${isDragging ? ' opacity-60' : ''}`}>
      <span
        {...pointerStarts(listeners)}
        aria-hidden="true"
        className="flex h-full cursor-grab touch-none items-center px-1.5 text-[var(--color-ink-3)]"
      >
        <Grip />
      </span>
      <SlotBody label={label} note={note} onRemove={onRemove}>
        {onToRows && (
          <button type="button" className={SLOT_BTN} onClick={onToRows} aria-label={`Přesunout ${label} do Řádků`}>
            do Řádků
          </button>
        )}
      </SlotBody>
    </div>
  );
}

/* -------------------------------------------------------------------------- */

function ValueWell({ draft, onChange }: { draft: ChartSpec; onChange: (next: ChartSpec) => void }) {
  /* Why each view cannot take the draft's shelves (null = it can). */
  const blocks = STATS_REGISTRY.views.map((v) => ({ v, why: viewBlock(draft, v) }));
  const notes = blocks.filter((x) => x.why != null && x.v !== draft.view);
  /* The well holds three pickers; only the measure group is named "Hodnota". */
  return (
    <div className="space-y-3 rounded-[var(--radius-sm)] border border-[var(--color-rule)] bg-[var(--color-inset)] p-3">
      <Picker label="Hodnota">
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
      </Picker>
      <Picker label="Zobrazení">
        {blocks.map(({ v, why }) => (
          <PickButton
            key={v}
            on={draft.view === v}
            disabled={draft.view !== v && why != null}
            onClick={() => onChange({ ...draft, view: v })}
            ariaLabel={`Zobrazení: ${VIEW_LABEL[v]}`}
          >
            {VIEW_LABEL[v]}
          </PickButton>
        ))}
      </Picker>
      {notes.length > 0 && (
        <p className="-mt-1.5 text-[0.7rem] text-[var(--color-ink-3)]">
          {notes.map((x) => `${VIEW_LABEL[x.v]}: ${x.why}`).join(' · ')}
        </p>
      )}
      {statUsed(draft) && (
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
