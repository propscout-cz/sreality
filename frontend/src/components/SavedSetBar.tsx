/* THE saved-set bar (docs/design/saved-dashboards.md): named chips an account
 * owns, loads, updates, reorders and shares. Its two instances — Browse's filter
 * presets (FilterPresetBar) and the Stats dashboards (stats/DashboardBar) — read
 * their list and persist a drag through `useSavedSet`, and bring the other writes
 * and the words; the bar decides nothing about a set's blob.
 *
 * A chip click loads (`onLoad`); the active chip, once `dirty`, carries a dot and
 * the copper update button appears. Own chips drag by a grip (dnd-kit, keyboard
 * too); a chip SHARED with the account (`isOwn` false) follows them, names its
 * owner (`suffix`), and never drags, renames, deletes or updates. */

import { useEffect, useId, useRef, useState, type CSSProperties, type ReactNode } from 'react';
import { useQuery, useQueryClient, type QueryKey } from '@tanstack/react-query';
import {
  DndContext, KeyboardSensor, PointerSensor, closestCenter, useSensor, useSensors, type DragEndEvent,
} from '@dnd-kit/core';
import {
  SortableContext, arrayMove, rectSortingStrategy, sortableKeyboardCoordinates, useSortable,
} from '@dnd-kit/sortable';
import { CSS } from '@dnd-kit/utilities';

import { isApiConfigured } from '@/lib/api';
import { usePersistedFlag } from '@/lib/persistedFlag';
import type { TagColor } from '@/lib/types';
import { cachePatch, useOptimisticWrite } from '@/lib/useOptimisticWrite';
import SavedSetSaveModal, { type SaveModalWords } from '@/components/SavedSetSaveModal';

export type SavedSetItem = { id: string; name: string; color: TagColor | null };

/* A chip menu entry. A `confirm` one is two-step (the first click arms it); a
 * promise keeps the menu open and busy, closing it on success. `danger` hovers brick. */
export interface SavedSetMenuItem {
  label: string;
  onSelect: () => void | Promise<unknown>;
  confirm?: { label: string; busy: string };
  danger?: boolean;
}

/* Every string the bar draws: the presets' English, the dashboards' Czech. `load`,
 * `reorder` and `options` name a chip as "<word>: <name>"; the update button
 * reads as the update dialog's submit. `shared` words a chip shared WITH the
 * account: the owner's lead-in for a screen reader, and its dirty dot's hint. */
export type SavedSetCopy = SaveModalWords &
  Record<
    'empty' | 'save' | 'saveHint' | 'updateHint' | 'edit' | 'load' | 'reorder' | 'dragHint' | 'options' | 'edited' | 'editedHint',
    string
  > & {
    delete: Required<Pick<SavedSetMenuItem, 'label' | 'confirm'>>;
    shared?: { by: string; editedHint: string };
  };

export interface SavedSetBarProps<T extends SavedSetItem> {
  items: readonly T[];
  /* The list is still being read: no empty-state sentence yet. */
  loading?: boolean;
  activeId: string | null;
  dirty: boolean;
  copy: SavedSetCopy;
  onLoad: (item: T) => void;
  /* The dialog's writes: it stays open and busy until the promise settles. */
  onSaveNew: (name: string, checked: boolean, color: TagColor | null) => Promise<unknown>;
  onUpdate: (item: T, name: string, checked: boolean, color: TagColor | null) => Promise<unknown>;
  onRename: (item: T, name: string, color: TagColor | null) => Promise<unknown>;
  onDelete: (item: T) => Promise<unknown>;
  /* The account's own chips in their new order. */
  onReorder: (next: T[]) => void;
  /* An item the account owns: it drags, renames, deletes and updates. Default: all. */
  isOwn?: (item: T) => boolean;
  /* A small ink-3 suffix after the chip's name (a shared chip's owner; an own chip's role). */
  suffix?: (item: T) => string | null;
  /* Menu entries the instance adds (between Edit… and Delete on an own chip). */
  chipMenu?: (item: T) => SavedSetMenuItem[];
  /* The one checkbox the save and update dialogs offer; `initial(null)` on a save. */
  checkbox?: { label: string; initial: (item: T | null) => boolean };
  /* Chips that lead the row and are not saved sets (Browse's Pipeline scope). */
  leading?: ReactNode;
  /* Fold the chips behind a "<label> <count>" toggle: folded, only the active
   * chip shows. The choice is remembered per browser under `storageKey`. */
  collapse?: { label: string; storageKey: string; show: string; fold: string };
}

type SavedSetList<T> = { data: T[]; total: number };

/* An instance's list (`{data, total}` under `key`) and its drag write: the chips
 * move on the drop, the PUT's canonical list is adopted, a failure rolls back and
 * re-reads (`onReorderError` replaces the global toast). Shared items stay behind
 * the own ones. `refetchOnMount: 'always'` re-reads a fresh cache on every mount.
 * `bar` spreads onto the bar. */
export function useSavedSet<T extends SavedSetItem>(
  key: QueryKey,
  list: () => Promise<SavedSetList<T>>,
  reorder: (ids: string[]) => Promise<SavedSetList<T>>,
  {
    isOwn = () => true,
    refetchOnMount,
    onReorderError,
  }: {
    isOwn?: (item: T) => boolean;
    refetchOnMount?: 'always';
    onReorderError?: (error: Error) => void;
  } = {},
) {
  const qc = useQueryClient();
  const enabled = isApiConfigured();
  const query = useQuery({ queryKey: key, queryFn: list, enabled, refetchOnMount });
  const write = useOptimisticWrite<T[], SavedSetList<T>>({
    mutationKey: ['write', ...key, 'reorder'],
    mutationFn: (own) => reorder(own.map((i) => i.id)),
    patch: (own) => [
      cachePatch<SavedSetList<T>>({ queryKey: key, exact: true }, (prev) =>
        prev && { ...prev, data: [...own, ...prev.data.filter((i) => !isOwn(i))] },
      ),
    ],
    // The PUT answers with the canonical list, so only a failure needs a re-read.
    revalidate: (_own, failed) => (failed ? [key] : []),
    onSuccess: (res) => qc.setQueryData(key, res),
    onError: onReorderError,
  });
  const items = query.data?.data ?? [];
  return { enabled, query, bar: { items, loading: query.isLoading, isOwn, onReorder: write.mutate } };
}

type ModalState<T> = { mode: 'save'; item: null } | { mode: 'update' | 'edit'; item: T };

export const chipBase =
  'inline-flex items-center rounded-[var(--radius-sm)] border text-[0.8rem] transition-colors';

export default function SavedSetBar<T extends SavedSetItem>({
  items, loading = false, activeId, dirty, copy, onLoad, onSaveNew, onUpdate, onRename, onDelete, onReorder,
  isOwn = () => true, suffix, chipMenu, checkbox, leading, collapse,
}: SavedSetBarProps<T>) {
  const barRef = useRef<HTMLDivElement>(null);
  const dirtyId = useId();
  const [modal, setModal] = useState<ModalState<T> | null>(null);
  /* The open chip menu, and its armed two-step entry. */
  const [menu, setMenu] = useState<{ id: string; armed?: number } | null>(null);
  const [running, setRunning] = useState(false);

  const expanded = usePersistedFlag(collapse?.storageKey ?? '', false);
  const collapsed = collapse != null && !expanded.value;
  const shown = collapsed ? items.filter((i) => i.id === activeId) : items;
  const own = shown.filter(isOwn);
  const shared = shown.filter((i) => !isOwn(i));
  const active = items.find((i) => i.id === activeId);

  /* Click-away closes the per-chip kebab menu. */
  const menuOpen = menu != null;
  useEffect(() => {
    if (!menuOpen) return;
    const onDown = (e: MouseEvent) => {
      if (barRef.current && !barRef.current.contains(e.target as Node)) setMenu(null);
    };
    document.addEventListener('mousedown', onDown);
    return () => document.removeEventListener('mousedown', onDown);
  }, [menuOpen]);

  const sensors = useSensors(
    /* A small activation distance keeps a click on the grip from registering
     * as a drag; the keyboard sensor makes reordering accessible. */
    useSensor(PointerSensor, { activationConstraint: { distance: 6 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  );

  const onDragEnd = ({ active: dragged, over }: DragEndEvent) => {
    const from = own.findIndex((i) => i.id === dragged.id);
    const to = own.findIndex((i) => i.id === over?.id);
    if (from >= 0 && to >= 0 && from !== to) onReorder(arrayMove(own, from, to));
  };

  const menuFor = (item: T): SavedSetMenuItem[] => {
    const added = chipMenu?.(item) ?? [];
    if (!isOwn(item)) return added;
    return [
      { label: copy.edit, onSelect: () => setModal({ mode: 'edit', item }) },
      ...added,
      { ...copy.delete, danger: true, onSelect: () => onDelete(item) },
    ];
  };

  const runEntry = async (item: T, index: number, entry: SavedSetMenuItem) => {
    if (running) return;
    if (entry.confirm && menu?.armed !== index) {
      setMenu({ id: item.id, armed: index });
      return;
    }
    const out = entry.onSelect();
    if (!(out instanceof Promise)) {
      setMenu(null);
      return;
    }
    setRunning(true);
    try {
      await out;
      setMenu(null);
    } catch {
      /* The write's own toast says why; the entry stays armed for a retry. */
    } finally {
      setRunning(false);
    }
  };

  const reorderable = own.length > 1;
  const chip = (item: T) => {
    const draggable = reorderable && isOwn(item);
    const marked = item.id === activeId && dirty;
    const entries = menu?.id === item.id ? menuFor(item) : null;
    const owner = suffix?.(item);
    /* "<name> — <by> <owner>": two shared chips of one name stay two for a screen reader;
     * an own chip's suffix is its role, read without "by". */
    const ownerLead = ['—', isOwn(item) ? null : copy.shared?.by].filter(Boolean).join(' ');
    const named = owner ? `${item.name} ${ownerLead} ${owner}` : item.name;
    const editedHint = isOwn(item) ? copy.editedHint : (copy.shared?.editedHint ?? copy.editedHint);
    /* The accent is the chip's colour (shared tag palette) or copper when
     * uncolored, as CSS vars so active/hover states reuse one source. */
    const tone = item.color ? `--color-tag-${item.color}` : '--color-copper';
    const accent = { '--chip-accent': `var(${tone})`, '--chip-accent-soft': `var(${tone}-soft)` } as CSSProperties;
    return (
      <Sortable key={item.id} id={item.id} disabled={!draggable}>
        {({ attributes, listeners, setActivatorNodeRef, isDragging }) => (
          <>
            <div
              style={accent}
              className={[
                chipBase,
                isDragging ? 'shadow-md' : '',
                item.id === activeId
                  ? 'border-[var(--chip-accent)] bg-[var(--chip-accent-soft)] text-[var(--chip-accent)]'
                  : item.color
                    ? 'border-[var(--chip-accent)] text-[var(--chip-accent)] hover:bg-[var(--chip-accent-soft)]'
                    : 'border-[var(--color-rule)] text-[var(--color-ink-2)] hover:border-[var(--color-rule-strong)] hover:text-[var(--color-ink)]',
              ].join(' ')}
            >
              {draggable ? (
                <button
                  type="button"
                  ref={setActivatorNodeRef}
                  {...attributes}
                  {...listeners}
                  className="flex items-center self-stretch px-1.5 cursor-grab touch-none text-[var(--color-ink-4)] hover:text-[var(--color-ink-2)] active:cursor-grabbing"
                  aria-label={`${copy.reorder}: ${item.name}`}
                  title={copy.dragHint}
                >
                  {GRIP}
                </button>
              ) : null}
              <button
                type="button"
                onClick={() => onLoad(item)}
                className={`${draggable ? 'pl-0.5' : 'pl-2.5'} pr-2.5 py-1 max-w-[16rem] truncate`}
                aria-describedby={marked ? dirtyId : undefined}
                title={`${copy.load}: ${item.name}`}
              >
                {item.name}
                {owner ? (
                  <>
                    <span className="sr-only"> {ownerLead} </span>
                    <span className="ml-1.5 text-[0.7rem] text-[var(--color-ink-3)]">{owner}</span>
                  </>
                ) : null}
                {marked ? (
                  <>
                    <span className="ml-1 text-[var(--chip-accent)]" title={editedHint} aria-hidden>
                      •
                    </span>
                    <span id={dirtyId} hidden>
                      {copy.edited}
                    </span>
                  </>
                ) : null}
              </button>
              <button
                type="button"
                onClick={() => setMenu((cur) => (cur?.id === item.id ? null : { id: item.id }))}
                className="px-1.5 py-1 border-l border-[var(--color-rule)] opacity-60 hover:opacity-100"
                aria-haspopup="menu"
                aria-expanded={entries != null}
                aria-label={`${copy.options}: ${named}`}
                title={copy.options}
              >
                ⋯
              </button>
            </div>

            {entries ? (
              <div
                role="menu"
                className="absolute left-0 top-[calc(100%+4px)] z-30 min-w-[10rem] rounded-[var(--radius-sm)] border border-[var(--color-rule)] bg-[var(--color-paper)] py-1 shadow-lg"
              >
                {entries.map((entry, i) => {
                  const armed = menu?.armed === i ? entry.confirm : undefined;
                  return (
                    <button
                      key={i}
                      type="button"
                      role="menuitem"
                      onClick={() => void runEntry(item, i, entry)}
                      disabled={running}
                      className={`block w-full px-3 py-1.5 text-left text-[0.8rem] disabled:opacity-50 ${
                        armed
                          ? 'text-[var(--color-brick)] hover:bg-[var(--color-brick-soft)]'
                          : `text-[var(--color-ink-2)] hover:bg-[var(--color-paper-2)] ${entry.danger ? 'hover:text-[var(--color-brick)]' : 'hover:text-[var(--color-ink)]'}`
                      }`}
                    >
                      {armed ? (running ? armed.busy : armed.label) : entry.label}
                    </button>
                  );
                })}
              </div>
            ) : null}
          </>
        )}
      </Sortable>
    );
  };

  return (
    <div ref={barRef} className="flex items-center gap-2 flex-wrap">
      {leading}

      {collapse && items.length > 0 ? (
        <button
          type="button"
          onClick={expanded.toggle}
          aria-expanded={!collapsed}
          title={collapsed ? collapse.show : collapse.fold}
          className={`${chipBase} gap-1.5 px-2.5 py-1 font-medium bg-[var(--color-paper-2)] border-[var(--color-rule)] text-[var(--color-ink)] hover:border-[var(--color-rule-strong)]`}
        >
          <svg width="10" height="10" viewBox="0 0 10 10" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden
            className={`transition-transform ${collapsed ? '' : 'rotate-90'}`}>
            <path d="M3.5 2 6.5 5l-3 3" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
          <span>{collapse.label}</span>
          <span className="font-mono tabular-nums text-[0.7rem] text-[var(--color-ink-3)]">{items.length}</span>
        </button>
      ) : null}

      {items.length === 0 && !loading ? (
        <span className="text-[0.75rem] text-[var(--color-ink-4)]">{copy.empty}</span>
      ) : null}

      <DndContext
        sensors={sensors}
        collisionDetection={closestCenter}
        onDragStart={() => setMenu(null)}
        onDragEnd={onDragEnd}
      >
        <SortableContext items={own.map((i) => i.id)} strategy={rectSortingStrategy}>
          {own.map(chip)}
          {shared.map(chip)}
        </SortableContext>
      </DndContext>

      {active && dirty && isOwn(active) ? (
        <button
          type="button"
          onClick={() => setModal({ mode: 'update', item: active })}
          className="px-2.5 py-1 text-[0.8rem] rounded-[var(--radius-sm)] bg-[var(--color-copper)] text-white hover:bg-[var(--color-copper-2)] transition-colors"
          title={copy.updateHint}
        >
          {copy.submit.update}
        </button>
      ) : null}

      <button
        type="button"
        onClick={() => setModal({ mode: 'save', item: null })}
        className="px-2.5 py-1 text-[0.8rem] rounded-[var(--radius-sm)] border border-dashed border-[var(--color-rule-strong)] text-[var(--color-ink-3)] hover:text-[var(--color-ink)] hover:border-[var(--color-copper)] transition-colors"
        title={copy.saveHint}
      >
        {copy.save}
      </button>

      {modal ? (
        <SavedSetSaveModal
          words={copy}
          mode={modal.mode}
          initial={modal.item}
          checkbox={checkbox && modal.mode !== 'edit' ? { label: checkbox.label, initial: checkbox.initial(modal.item) } : null}
          onSubmit={(name, checked, color) =>
            (modal.mode === 'save'
              ? onSaveNew(name, checked, color)
              : modal.mode === 'update'
                ? onUpdate(modal.item, name, checked, color)
                : onRename(modal.item, name, color)
            ).then(() => setModal(null))
          }
          onClose={() => setModal(null)}
        />
      ) : null}
    </div>
  );
}

/* One sortable node. A shared chip is disabled and not among the context's
 * items, so it neither drags nor moves for a drag. */
function Sortable({
  id,
  disabled,
  children,
}: {
  id: string;
  disabled: boolean;
  children: (sortable: ReturnType<typeof useSortable>) => ReactNode;
}) {
  const sortable = useSortable({ id, disabled });
  const { transform, transition, isDragging } = sortable;
  return (
    <div
      ref={sortable.setNodeRef}
      style={{ transform: CSS.Transform.toString(transform), transition, zIndex: isDragging ? 30 : undefined }}
      className={`relative ${isDragging ? 'opacity-90' : ''}`}
    >
      {children(sortable)}
    </div>
  );
}

/* Six-dot grip — the standard "drag me" affordance, dim at rest and
 * brightening on hover (mirrors the ResizeHandle idiom). */
const GRIP = (
  <svg width="9" height="14" viewBox="0 0 9 14" fill="currentColor" aria-hidden>
    {[3, 7, 11].flatMap((cy) => [2, 7].map((cx) => <circle key={`${cx}-${cy}`} cx={cx} cy={cy} r="1.1" />))}
  </svg>
);
