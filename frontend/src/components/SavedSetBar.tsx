/* THE saved-set bar (docs/design/saved-dashboards.md): named chips an account
 * owns, loads, updates, reorders and shares. Its two instances — Browse's filter
 * presets (FilterPresetBar) and the Stats dashboards (stats/DashboardBar) — bring
 * the reads, the writes and the words; the bar decides nothing about a set's blob.
 *
 * A chip click loads (`onLoad`); the active chip, once `dirty`, carries a dot and
 * the copper update button appears. Own chips drag by a grip (dnd-kit, keyboard
 * too); a chip SHARED with the account (`isOwn` false) follows them, names its
 * owner (`suffix`), and never drags, renames, deletes or updates. */

import { useEffect, useId, useRef, useState, type CSSProperties, type ReactNode } from 'react';
import {
  DndContext,
  KeyboardSensor,
  PointerSensor,
  closestCenter,
  useSensor,
  useSensors,
  type DragEndEvent,
} from '@dnd-kit/core';
import {
  SortableContext,
  arrayMove,
  rectSortingStrategy,
  sortableKeyboardCoordinates,
  useSortable,
} from '@dnd-kit/sortable';
import { CSS } from '@dnd-kit/utilities';

import { ApiError } from '@/lib/api';
import type { TagColor } from '@/lib/types';
import SavedSetSaveModal, { type SaveModalWords } from '@/components/SavedSetSaveModal';

export interface SavedSetItem {
  id: string;
  name: string;
  color: TagColor | null;
}

/* A chip menu entry. A `confirm` one is two-step (the first click arms it); a
 * promise keeps the menu open and busy, closing it on success. */
export interface SavedSetMenuItem {
  label: string;
  onSelect: () => void | Promise<unknown>;
  confirm?: { label: string; busy: string };
  /* Hovers brick: the entry removes something. */
  danger?: boolean;
}

export type SavedSetModalMode = 'save' | 'update' | 'edit';

/* Every string the bar draws: the presets' English, the dashboards' Czech. */
export interface SavedSetCopy extends SaveModalWords {
  empty: string;
  save: string;
  saveHint: string;
  update: string;
  updateHint: string;
  /* The dialog's title and submit label per flow. */
  dialog: Record<SavedSetModalMode, string>;
  submit: Record<SavedSetModalMode, string>;
  edit: string;
  delete: string;
  confirmDelete: string;
  deleting: string;
  load: (name: string) => string;
  reorder: (name: string) => string;
  dragHint: string;
  options: (name: string) => string;
  optionsHint: string;
  edited: string;
  editedHint: string;
  failed: string;
}

export interface SavedSetBarProps<T extends SavedSetItem> {
  items: readonly T[];
  /* The list is still being read: no empty-state sentence yet. */
  loading?: boolean;
  activeId: string | null;
  dirty: boolean;
  copy: SavedSetCopy;
  onLoad: (item: T) => void;
  /* The dialog's writes: it stays open and busy until the promise settles. */
  onSaveNew: (name: string, extra: boolean, color: TagColor | null) => Promise<unknown>;
  onUpdate: (item: T, name: string, extra: boolean, color: TagColor | null) => Promise<unknown>;
  onRename: (item: T, name: string, color: TagColor | null) => Promise<unknown>;
  onDelete: (item: T) => Promise<unknown>;
  /* The account's own chips in their new order. */
  onReorder: (next: T[]) => void;
  /* An item the account owns: it drags, renames, deletes and updates. Default: all. */
  isOwn?: (item: T) => boolean;
  /* A small ink-3 suffix after the chip's name (a shared chip's owner). */
  suffix?: (item: T) => string | null;
  /* Menu entries the instance adds (between Edit… and Delete on an own chip). */
  chipMenu?: (item: T) => SavedSetMenuItem[];
  /* The one checkbox the save and update dialogs offer; `initial(null)` on a save. */
  extra?: { label: string; initial: (item: T | null) => boolean };
  /* Chips that lead the row and are not saved sets (Browse's Pipeline scope). */
  leading?: ReactNode;
}

type ModalState<T> = { mode: 'save' } | { mode: 'update' | 'edit'; item: T };

export const chipBase =
  'inline-flex items-center rounded-[var(--radius-sm)] border text-[0.8rem] transition-colors';

const everyItemIsOwn = () => true;

export default function SavedSetBar<T extends SavedSetItem>({
  items,
  loading = false,
  activeId,
  dirty,
  copy,
  onLoad,
  onSaveNew,
  onUpdate,
  onRename,
  onDelete,
  onReorder,
  isOwn = everyItemIsOwn,
  suffix,
  chipMenu,
  extra,
  leading,
}: SavedSetBarProps<T>) {
  const barRef = useRef<HTMLDivElement>(null);

  const own = items.filter(isOwn);
  const shared = items.filter((i) => !isOwn(i));
  const active = activeId != null ? items.find((i) => i.id === activeId) ?? null : null;

  const [modal, setModal] = useState<ModalState<T> | null>(null);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [menuId, setMenuId] = useState<string | null>(null);
  /* The armed two-step entry, as `${chip id}:${entry index}`. */
  const [armed, setArmed] = useState<string | null>(null);
  const [running, setRunning] = useState(false);

  const closeMenu = () => {
    setMenuId(null);
    setArmed(null);
  };

  /* Click-away closes the per-chip kebab menu. */
  useEffect(() => {
    if (menuId == null) return;
    const onDown = (e: MouseEvent) => {
      if (barRef.current && !barRef.current.contains(e.target as Node)) {
        setMenuId(null);
        setArmed(null);
      }
    };
    document.addEventListener('mousedown', onDown);
    return () => document.removeEventListener('mousedown', onDown);
  }, [menuId]);

  const openModal = (next: ModalState<T>) => {
    setSaveError(null);
    setModal(next);
  };

  const sensors = useSensors(
    /* A small activation distance keeps a click on the grip from registering
     * as a drag; the keyboard sensor makes reordering accessible. */
    useSensor(PointerSensor, { activationConstraint: { distance: 6 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  );

  const handleDragEnd = (event: DragEndEvent) => {
    const { active: dragged, over } = event;
    if (!over || dragged.id === over.id) return;
    const oldIndex = own.findIndex((i) => i.id === dragged.id);
    const newIndex = own.findIndex((i) => i.id === over.id);
    if (oldIndex < 0 || newIndex < 0) return;
    onReorder(arrayMove(own, oldIndex, newIndex));
  };

  const handleSubmit = async (name: string, checked: boolean, color: TagColor | null) => {
    if (modal == null) return;
    setSaving(true);
    setSaveError(null);
    try {
      if (modal.mode === 'save') await onSaveNew(name, checked, color);
      else if (modal.mode === 'update') await onUpdate(modal.item, name, checked, color);
      else await onRename(modal.item, name, color);
      setModal(null);
    } catch (e) {
      setSaveError(e instanceof ApiError ? e.message : copy.failed);
    } finally {
      setSaving(false);
    }
  };

  const menuFor = (item: T): SavedSetMenuItem[] => {
    const added = chipMenu?.(item) ?? [];
    if (!isOwn(item)) return added;
    return [
      { label: copy.edit, onSelect: () => openModal({ mode: 'edit', item }) },
      ...added,
      {
        label: copy.delete,
        danger: true,
        confirm: { label: copy.confirmDelete, busy: copy.deleting },
        onSelect: () => onDelete(item),
      },
    ];
  };

  const runEntry = async (item: T, index: number, entry: SavedSetMenuItem) => {
    const key = `${item.id}:${index}`;
    if (entry.confirm && armed !== key) {
      setArmed(key);
      return;
    }
    const out = entry.onSelect();
    if (!(out instanceof Promise)) {
      closeMenu();
      return;
    }
    setRunning(true);
    try {
      await out;
      closeMenu();
    } catch {
      /* The write's own toast says why; the entry stays armed for a retry. */
    } finally {
      setRunning(false);
    }
  };

  const reorderable = own.length > 1;
  const chip = (item: T) => <Chip key={item.id} {...chipProps(item)} />;
  const chipProps = (item: T) => ({
    item,
    draggable: reorderable && isOwn(item),
    isActive: item.id === activeId,
    dirty,
    copy,
    suffix: suffix?.(item) ?? null,
    menuOpen: menuId === item.id,
    menu: menuId === item.id ? menuFor(item) : [],
    armed,
    running,
    onLoad,
    onToggleMenu: (id: string) => {
      setArmed(null);
      setMenuId((cur) => (cur === id ? null : id));
    },
    onEntry: runEntry,
  });

  return (
    <div ref={barRef} className="flex items-center gap-2 flex-wrap">
      {leading}

      {items.length === 0 && !loading ? (
        <span className="text-[0.75rem] text-[var(--color-ink-4)]">
          {copy.empty}
        </span>
      ) : null}

      <DndContext
        sensors={sensors}
        collisionDetection={closestCenter}
        onDragStart={closeMenu}
        onDragEnd={handleDragEnd}
      >
        <SortableContext
          items={own.map((i) => i.id)}
          strategy={rectSortingStrategy}
        >
          {own.map(chip)}
          {shared.map(chip)}
        </SortableContext>
      </DndContext>

      {active && dirty && isOwn(active) ? (
        <button
          type="button"
          onClick={() => openModal({ mode: 'update', item: active })}
          className="px-2.5 py-1 text-[0.8rem] rounded-[var(--radius-sm)] bg-[var(--color-copper)] text-white hover:bg-[var(--color-copper-2)] transition-colors"
          title={copy.updateHint}
        >
          {copy.update}
        </button>
      ) : null}

      <button
        type="button"
        onClick={() => openModal({ mode: 'save' })}
        className="px-2.5 py-1 text-[0.8rem] rounded-[var(--radius-sm)] border border-dashed border-[var(--color-rule-strong)] text-[var(--color-ink-3)] hover:text-[var(--color-ink)] hover:border-[var(--color-copper)] transition-colors"
        title={copy.saveHint}
      >
        {copy.save}
      </button>

      {modal ? (
        <SavedSetSaveModal
          words={copy}
          title={copy.dialog[modal.mode]}
          initialName={modal.mode === 'save' ? '' : modal.item.name}
          initialColor={modal.mode === 'save' ? null : modal.item.color}
          submitLabel={copy.submit[modal.mode]}
          extra={
            extra && modal.mode !== 'edit'
              ? { label: extra.label, initial: extra.initial(modal.mode === 'save' ? null : modal.item) }
              : null
          }
          busy={saving}
          error={saveError}
          onSubmit={(name, checked, color) => void handleSubmit(name, checked, color)}
          onClose={() => setModal(null)}
        />
      ) : null}
    </div>
  );
}

interface ChipProps<T extends SavedSetItem> {
  item: T;
  /* An own chip among several: it carries the grip and drags. */
  draggable: boolean;
  isActive: boolean;
  dirty: boolean;
  copy: SavedSetCopy;
  suffix: string | null;
  menuOpen: boolean;
  menu: SavedSetMenuItem[];
  armed: string | null;
  running: boolean;
  onLoad: (item: T) => void;
  onToggleMenu: (id: string) => void;
  onEntry: (item: T, index: number, entry: SavedSetMenuItem) => void;
}

/* Every chip is a sortable node; a shared one is not among the context's
 * items and is disabled, so it neither drags nor moves for a drag. */
function Chip<T extends SavedSetItem>({
  item,
  draggable,
  isActive,
  dirty,
  copy,
  suffix,
  menuOpen,
  menu,
  armed,
  running,
  onLoad,
  onToggleMenu,
  onEntry,
}: ChipProps<T>) {
  const {
    attributes,
    listeners,
    setNodeRef,
    setActivatorNodeRef,
    transform,
    transition,
    isDragging,
  } = useSortable({ id: item.id, disabled: !draggable });
  const dirtyId = useId();

  /* The chip's accent comes from its colour (shared tag palette) or copper when
   * uncolored — exposed as CSS vars so active/hover states reuse one source.
   * Uncolored chips render copper when active, neutral when not. */
  const accent = item.color ? `var(--color-tag-${item.color})` : 'var(--color-copper)';
  const accentSoft = item.color
    ? `var(--color-tag-${item.color}-soft)`
    : 'var(--color-copper-soft)';

  const style: CSSProperties = {
    transform: CSS.Transform.toString(transform),
    transition,
    zIndex: isDragging ? 30 : undefined,
    ['--chip-accent' as string]: accent,
    ['--chip-accent-soft' as string]: accentSoft,
  };

  return (
    <div
      ref={setNodeRef}
      style={style}
      className={`relative ${isDragging ? 'opacity-90' : ''}`}
    >
      <div
        className={[
          chipBase,
          isDragging ? 'shadow-md' : '',
          isActive
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
            aria-label={copy.reorder(item.name)}
            title={copy.dragHint}
          >
            <GripIcon />
          </button>
        ) : null}
        <button
          type="button"
          onClick={() => onLoad(item)}
          className={`${draggable ? 'pl-0.5' : 'pl-2.5'} pr-2.5 py-1 max-w-[16rem] truncate`}
          aria-describedby={isActive && dirty ? dirtyId : undefined}
          title={copy.load(item.name)}
        >
          {item.name}
          {suffix ? (
            <>
              {' '}
              <span className="ml-0.5 text-[0.7rem] text-[var(--color-ink-3)]">{suffix}</span>
            </>
          ) : null}
          {isActive && dirty ? (
            <>
              <span
                className="ml-1 text-[var(--chip-accent)]"
                title={copy.editedHint}
                aria-hidden
              >
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
          onClick={() => onToggleMenu(item.id)}
          className="px-1.5 py-1 border-l border-[var(--color-rule)] opacity-60 hover:opacity-100"
          aria-haspopup="menu"
          aria-expanded={menuOpen}
          aria-label={copy.options(item.name)}
          title={copy.optionsHint}
        >
          ⋯
        </button>
      </div>

      {menuOpen ? (
        <div
          role="menu"
          className="absolute left-0 top-[calc(100%+4px)] z-30 min-w-[10rem] rounded-[var(--radius-sm)] border border-[var(--color-rule)] bg-[var(--color-paper)] py-1 shadow-lg"
        >
          {menu.map((entry, i) =>
            entry.confirm && armed === `${item.id}:${i}` ? (
              <button
                key={i}
                type="button"
                role="menuitem"
                onClick={() => onEntry(item, i, entry)}
                disabled={running}
                className="block w-full px-3 py-1.5 text-left text-[0.8rem] text-[var(--color-brick)] hover:bg-[var(--color-brick-soft)] disabled:opacity-50"
              >
                {running ? entry.confirm.busy : entry.confirm.label}
              </button>
            ) : (
              <button
                key={i}
                type="button"
                role="menuitem"
                onClick={() => onEntry(item, i, entry)}
                className={`block w-full px-3 py-1.5 text-left text-[0.8rem] text-[var(--color-ink-2)] hover:bg-[var(--color-paper-2)] ${
                  entry.danger ? 'hover:text-[var(--color-brick)]' : 'hover:text-[var(--color-ink)]'
                }`}
              >
                {entry.label}
              </button>
            ),
          )}
        </div>
      ) : null}
    </div>
  );
}

/* Six-dot grip — the standard "drag me" affordance, dim at rest and
 * brightening on hover (mirrors the ResizeHandle idiom). */
function GripIcon() {
  return (
    <svg width="9" height="14" viewBox="0 0 9 14" fill="currentColor" aria-hidden>
      <circle cx="2" cy="3" r="1.1" />
      <circle cx="7" cy="3" r="1.1" />
      <circle cx="2" cy="7" r="1.1" />
      <circle cx="7" cy="7" r="1.1" />
      <circle cx="2" cy="11" r="1.1" />
      <circle cx="7" cy="11" r="1.1" />
    </svg>
  );
}
