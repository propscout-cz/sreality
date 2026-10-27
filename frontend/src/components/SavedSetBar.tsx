/* THE saved-set bar (docs/design/saved-dashboards.md): named chips an account
 * owns, loads, updates, reorders and shares. Its two instances — Browse's filter
 * presets (FilterPresetBar) and the Stats dashboards (stats/DashboardBar) — read
 * their list and persist a drag through `useSavedSet`, and bring the other writes
 * and the words; the bar decides nothing about a set's blob.
 *
 * A chip click loads (`onLoad`); the active chip, once `dirty`, carries a dot, and
 * its menu leads with Save changes (own chips: one click, no dialog) and Revert
 * to saved (a reload). The bar draws no update button (operator 2026-10-27). Own
 * chips drag by a grip (dnd-kit, keyboard too); a chip SHARED with the account
 * (`isOwn` false) follows them, names its owner (`suffix`), and never drags,
 * renames, deletes or saves.
 *
 * An instance with GROUPS (presets, migration 672) draws each group as a box
 * after the loose chips: a chip drags into a box, between boxes and back out (a
 * "drop here" target appears while a boxed chip is dragged), a box drags by its
 * own grip, and the chip menu moves a chip without a drag. A drop writes the
 * whole arrangement once (`groups.onLayout`, lib/savedSetLayout). */

import { useEffect, useId, useRef, useState, type CSSProperties, type ReactNode } from 'react';
import { useQuery, useQueryClient, type QueryKey } from '@tanstack/react-query';
import {
  DndContext, KeyboardSensor, MeasuringStrategy, PointerSensor, closestCenter, getFirstCollision, pointerWithin,
  rectIntersection, useDroppable, useSensor, useSensors, type CollisionDetection, type DragEndEvent,
  type DragOverEvent, type DragStartEvent, type UniqueIdentifier,
} from '@dnd-kit/core';
import {
  SortableContext, arrayMove, rectSortingStrategy, sortableKeyboardCoordinates, useSortable,
} from '@dnd-kit/sortable';
import { CSS } from '@dnd-kit/utilities';

import { isApiConfigured } from '@/lib/api';
import { usePersistedFlag } from '@/lib/persistedFlag';
import {
  LOOSE, applyLayout, containerOf, containersOf, layoutOf, moveAcross, moveTo, moveWithin, sameLayout,
  type Containers, type SavedSetLayout,
} from '@/lib/savedSetLayout';
import type { TagColor } from '@/lib/types';
import { cachePatch, useOptimisticWrite } from '@/lib/useOptimisticWrite';
import SavedSetSaveModal, { type SaveModalWords } from '@/components/SavedSetSaveModal';

export type SavedSetItem = { id: string; name: string; color: TagColor | null; group_id?: string | null };
export type SavedSetGroup = { id: string; name: string; color: TagColor | null };

/* A chip menu entry. A `confirm` one is two-step (the first click arms it); a
 * promise keeps the menu open and busy, closing it on success. `danger` hovers
 * brick; `accent` is the menu's one copper line (Save changes). */
export interface SavedSetMenuItem {
  label: string;
  onSelect: () => void | Promise<unknown>;
  confirm?: { label: string; busy: string };
  danger?: boolean;
  accent?: boolean;
}

export interface SavedSetBadge {
  label: string;
  /* Its tooltip. */
  title: string;
  icon?: ReactNode;
}

/* Every string the bar draws: the presets' English, the dashboards' Czech. `load`,
 * `reorder` and `options` name a chip as "<word>: <name>"; `saveChanges` and
 * `revert` lead a dirty chip's menu. `shared` words a chip shared WITH the
 * account: the owner's lead-in for a screen reader, and its dirty dot's hint. */
export type SavedSetCopy = SaveModalWords &
  Record<
    | 'empty' | 'save' | 'saveHint' | 'saveChanges' | 'revert' | 'edit' | 'load' | 'reorder' | 'dragHint' | 'options'
    | 'edited' | 'editedHint',
    string
  > & {
    delete: Required<Pick<SavedSetMenuItem, 'label' | 'confirm'>>;
    shared?: { by: string; editedHint: string };
  };

/* A group's words: its dialog (the save dialog's `save` / `edit` flows), the add
 * button, an empty box, the ungroup target, its grip and menu, and the chip
 * menu's moves ("<moveTo> <group>", `ungroup`). */
export type SavedSetGroupCopy = SaveModalWords &
  Record<'add' | 'addHint' | 'empty' | 'loose' | 'move' | 'dragHint' | 'options' | 'edit' | 'moveTo' | 'ungroup', string> & {
    delete: Required<Pick<SavedSetMenuItem, 'label' | 'confirm'>>;
  };

export interface SavedSetGroups {
  items: readonly SavedSetGroup[];
  copy: SavedSetGroupCopy;
  /* The arrangement after a drop or a menu move: boxes in order, every own chip with its box. */
  onLayout: (layout: SavedSetLayout) => void;
  onCreate: (name: string, color: TagColor | null) => Promise<unknown>;
  onEdit: (group: SavedSetGroup, name: string, color: TagColor | null) => Promise<unknown>;
  /* A deleted group's chips fall loose; none is deleted. */
  onDelete: (group: SavedSetGroup) => Promise<unknown>;
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
  onSaveNew: (name: string, checked: boolean, color: TagColor | null) => Promise<unknown>;
  /* Save changes: the item's own name, colour and checkbox choice, no dialog. */
  onUpdate: (item: T, name: string, checked: boolean, color: TagColor | null) => Promise<unknown>;
  onRename: (item: T, name: string, color: TagColor | null) => Promise<unknown>;
  onDelete: (item: T) => Promise<unknown>;
  /* The account's own chips in their new order (a bar without groups). */
  onReorder: (next: T[]) => void;
  /* An item the account owns: it drags, renames, deletes and updates. Default: all. */
  isOwn?: (item: T) => boolean;
  /* A small ink-3 suffix after the chip's name (a shared chip's owner; an own chip's role). */
  suffix?: (item: T) => string | null;
  /* A small tracked-caps badge after the name, in the chip's own colour (the Okolí
   * dashboard's). Where a chip has one, its suffix is read but not shown, and the
   * name truncates alone so a long one never hides the badge. */
  badge?: (item: T) => SavedSetBadge | null;
  /* Menu entries the instance adds (between Edit… and Delete on an own chip). */
  chipMenu?: (item: T) => SavedSetMenuItem[];
  /* The one checkbox the save dialog offers (`initial(null)`); Save changes keeps an item's
   * own answer, `initial(item)`. `disabled` answers why it is greyed out for an item, or
   * null where it is not (Save changes then sends it unticked). */
  checkbox?: {
    label: string;
    initial: (item: T | null) => boolean;
    disabled?: (item: T | null) => string | null;
  };
  /* Chips that lead the row and are not saved sets (Browse's Pipeline scope). */
  leading?: ReactNode;
  /* Fold the chips behind a "<label> <count>" toggle: folded, only the active
   * chip shows (no boxes). The choice is remembered per browser under `storageKey`. */
  collapse?: { label: string; storageKey: string; show: string; fold: string };
  /* Boxes the own chips sort into; without it the bar is one flat row. */
  groups?: SavedSetGroups;
}

type SavedSetList<T> = { data: T[]; total: number; groups?: SavedSetGroup[] };

/* An instance's list (`{data, total}` under `key`, plus `groups` where it has
 * them) and its drag writes: the chips move on the drop, the PUT's canonical list
 * is adopted, a failure rolls back and re-reads (`onReorderError` replaces the
 * global toast). Shared items stay behind the own ones. `refetchOnMount: 'always'`
 * re-reads a fresh cache on every mount. `bar` spreads onto the bar; `groups`
 * feeds its `groups` prop where the instance passes `layout`. */
export function useSavedSet<T extends SavedSetItem>(
  key: QueryKey,
  list: () => Promise<SavedSetList<T>>,
  reorder: (ids: string[]) => Promise<SavedSetList<T>>,
  {
    isOwn = () => true,
    refetchOnMount,
    onReorderError,
    layout,
  }: {
    isOwn?: (item: T) => boolean;
    refetchOnMount?: 'always';
    onReorderError?: (error: Error) => void;
    layout?: (layout: SavedSetLayout) => Promise<SavedSetList<T>>;
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
  const arrange = useOptimisticWrite<SavedSetLayout, SavedSetList<T>>({
    mutationKey: ['write', ...key, 'layout'],
    mutationFn: (next) => (layout ? layout(next) : Promise.reject(new Error('this saved set has no groups'))),
    patch: (next) => [
      cachePatch<SavedSetList<T>>({ queryKey: key, exact: true }, (prev) => prev && applyLayout(prev, next)),
    ],
    revalidate: (_next, failed) => (failed ? [key] : []),
    onSuccess: (res) => qc.setQueryData(key, res),
    onError: onReorderError,
  });
  const items = query.data?.data ?? [];
  return {
    enabled,
    query,
    bar: { items, loading: query.isLoading, isOwn, onReorder: write.mutate },
    groups: { items: query.data?.groups ?? [], onLayout: arrange.mutate },
  };
}

type ModalState<T> =
  | { mode: 'save'; item: null }
  | { mode: 'edit'; item: T }
  | { mode: 'group'; item: SavedSetGroup | null };

/* The drag in flight: what it moves, the box it left, and the arrangement as it stands. */
type Drag = { id: string; from: string; containers: Containers };

export const chipBase =
  'inline-flex items-center rounded-[var(--radius-sm)] border text-[0.8rem] transition-colors';

export default function SavedSetBar<T extends SavedSetItem>({
  items, loading = false, activeId, dirty, copy, onLoad, onSaveNew, onUpdate, onRename, onDelete, onReorder,
  isOwn = () => true, suffix, badge, chipMenu, checkbox, leading, collapse, groups,
}: SavedSetBarProps<T>) {
  const barRef = useRef<HTMLDivElement>(null);
  const dirtyId = useId();
  const [modal, setModal] = useState<ModalState<T> | null>(null);
  /* The open chip or box menu, and its armed two-step entry. */
  const [menu, setMenu] = useState<{ id: string; armed?: number } | null>(null);
  const [running, setRunning] = useState(false);
  const [drag, setDrag] = useState<Drag | null>(null);

  const expanded = usePersistedFlag(collapse?.storageKey ?? '', false);
  const collapsed = collapse != null && !expanded.value;
  const shown = collapsed ? items.filter((i) => i.id === activeId) : items;
  const own = shown.filter(isOwn);
  const shared = shown.filter((i) => !isOwn(i));
  const byId = new Map(own.map((i) => [i.id, i]));

  /* Folded, the bar is the active chip alone: no boxes, nothing to drag. */
  const boxes = groups && !collapsed ? groups.items : [];
  const groupIds = boxes.map((g) => g.id);
  const base = containersOf(own, groupIds);
  const containers = drag?.containers ?? base;

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

  /* A box drags among boxes only. A chip over a box lands beside the nearest chip
   * in it (an empty box takes it whole); right after a chip changes box, the
   * last target holds a frame so the re-laid-out row does not bounce it back
   * (dnd-kit's multiple-containers recipe). */
  const lastOver = useRef<UniqueIdentifier | null>(null);
  const justMoved = useRef(false);
  useEffect(() => {
    const frame = requestAnimationFrame(() => {
      justMoved.current = false;
    });
    return () => cancelAnimationFrame(frame);
  }, [drag]);
  const collision: CollisionDetection = (args) => {
    if (boxes.length === 0) return closestCenter(args);
    if (groupIds.includes(String(args.active.id))) {
      return closestCenter({
        ...args,
        droppableContainers: args.droppableContainers.filter((d) => groupIds.includes(String(d.id))),
      });
    }
    const hits = pointerWithin(args);
    let overId = getFirstCollision(hits.length > 0 ? hits : rectIntersection(args), 'id');
    if (overId != null) {
      const inside = groupIds.includes(String(overId)) ? containers[String(overId)] : [];
      if (inside.length > 0) {
        overId =
          closestCenter({
            ...args,
            droppableContainers: args.droppableContainers.filter((d) => inside.includes(String(d.id))),
          })[0]?.id ?? overId;
      }
      lastOver.current = overId;
      return [{ id: overId }];
    }
    if (justMoved.current) lastOver.current = args.active.id;
    return lastOver.current != null ? [{ id: lastOver.current }] : [];
  };

  /* One write per drop: the layout where the bar has groups, else the flat order. */
  const commit = (next: Containers, order: readonly string[]) => {
    if (groups && !collapsed) {
      const layout = layoutOf(next, order);
      if (!sameLayout(layout, layoutOf(base, groupIds))) groups.onLayout(layout);
      return;
    }
    const ids = next[LOOSE];
    if (ids.join() !== own.map((i) => i.id).join()) onReorder(ids.flatMap((id) => byId.get(id) ?? []));
  };

  const onDragStart = ({ active: dragged }: DragStartEvent) => {
    setMenu(null);
    const id = String(dragged.id);
    setDrag({ id, from: containerOf(base, id) ?? LOOSE, containers: base });
  };

  const onDragOver = ({ active: dragged, over }: DragOverEvent) => {
    if (over == null) return;
    setDrag((d) => {
      const next = d && moveAcross(d.containers, String(dragged.id), String(over.id));
      if (d == null || next == null) return d;
      justMoved.current = true;
      return { ...d, containers: next };
    });
  };

  const onDragEnd = ({ active: dragged, over }: DragEndEvent) => {
    const d = drag;
    setDrag(null);
    if (d == null || over == null) return;
    const id = String(dragged.id);
    if (groupIds.includes(id)) {
      const from = groupIds.indexOf(id);
      const to = groupIds.indexOf(String(over.id));
      if (from >= 0 && to >= 0 && from !== to) commit(d.containers, arrayMove(groupIds, from, to));
      return;
    }
    commit(moveWithin(d.containers, id, String(over.id)), groupIds);
  };

  /* A dirty chip's menu leads with the save (own only) and the revert: a reload of
   * the stored set. The save keeps the item's name, colour and checkbox answer. */
  const dirtyEntries = (item: T): SavedSetMenuItem[] =>
    item.id === activeId && dirty
      ? [
          ...(isOwn(item)
            ? [{
                label: copy.saveChanges,
                accent: true,
                onSelect: () =>
                  onUpdate(item, item.name, checkbox != null && checkbox.disabled?.(item) == null && checkbox.initial(item), item.color),
              }]
            : []),
          { label: copy.revert, onSelect: () => onLoad(item) },
        ]
      : [];

  const menuFor = (item: T): SavedSetMenuItem[] => {
    const added = chipMenu?.(item) ?? [];
    if (!isOwn(item)) return [...dirtyEntries(item), ...added];
    const at = containerOf(base, item.id) ?? LOOSE;
    const moves: SavedSetMenuItem[] = groups
      ? [
          ...boxes
            .filter((g) => g.id !== at)
            .map((g) => ({ label: `${groups.copy.moveTo} ${g.name}`, onSelect: () => commit(moveTo(base, item.id, g.id), groupIds) })),
          ...(at !== LOOSE ? [{ label: groups.copy.ungroup, onSelect: () => commit(moveTo(base, item.id, LOOSE), groupIds) }] : []),
        ]
      : [];
    return [
      ...dirtyEntries(item),
      { label: copy.edit, onSelect: () => setModal({ mode: 'edit', item }) },
      ...moves,
      ...added,
      { ...copy.delete, danger: true, onSelect: () => onDelete(item) },
    ];
  };

  const boxMenu = (group: SavedSetGroup, words: SavedSetGroupCopy, remove: SavedSetGroups['onDelete']): SavedSetMenuItem[] => [
    { label: words.edit, onSelect: () => setModal({ mode: 'group', item: group }) },
    { ...words.delete, danger: true, onSelect: () => remove(group) },
  ];

  const runEntry = async (id: string, index: number, entry: SavedSetMenuItem) => {
    if (running) return;
    if (entry.confirm && menu?.armed !== index) {
      setMenu({ id, armed: index });
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

  const menuList = (id: string, entries: SavedSetMenuItem[]) => (
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
            onClick={() => void runEntry(id, i, entry)}
            disabled={running}
            className={`block w-full px-3 py-1.5 text-left text-[0.8rem] disabled:opacity-50 ${
              armed
                ? 'text-[var(--color-brick)] hover:bg-[var(--color-brick-soft)]'
                : entry.accent
                  ? 'font-medium text-[var(--color-copper)] hover:bg-[var(--color-paper-2)]'
                  : `text-[var(--color-ink-2)] hover:bg-[var(--color-paper-2)] ${entry.danger ? 'hover:text-[var(--color-brick)]' : 'hover:text-[var(--color-ink)]'}`
            }`}
          >
            {armed ? (running ? armed.busy : armed.label) : entry.label}
          </button>
        );
      })}
    </div>
  );

  const reorderable = own.length > 1 || boxes.length > 0;
  const chip = (item: T) => {
    const draggable = reorderable && isOwn(item);
    const marked = item.id === activeId && dirty;
    const entries = menu?.id === item.id ? menuFor(item) : null;
    const owner = suffix?.(item);
    const tag = badge?.(item) ?? null;
    /* "<name> — <by> <owner>": two shared chips of one name stay two for a screen reader;
     * an own chip's suffix is its role, read without "by". */
    const ownerLead = ['—', isOwn(item) ? null : copy.shared?.by].filter(Boolean).join(' ');
    const named = owner ? `${item.name} ${ownerLead} ${owner}` : item.name;
    const editedHint = isOwn(item) ? copy.editedHint : (copy.shared?.editedHint ?? copy.editedHint);
    const accent = accentOf(item.color);
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
                className={`${draggable ? 'pl-0.5' : 'pl-2.5'} pr-2.5 py-1 max-w-[16rem] ${tag ? 'inline-flex items-center' : 'truncate'}`}
                aria-describedby={marked ? dirtyId : undefined}
                title={`${copy.load}: ${item.name}`}
              >
                {tag ? <span className="min-w-0 truncate">{item.name}</span> : item.name}
                {owner ? (
                  <>
                    <span className="sr-only"> {ownerLead} </span>
                    <span className={tag ? 'sr-only' : 'ml-1.5 text-[0.7rem] text-[var(--color-ink-3)]'}>{owner}</span>
                  </>
                ) : null}
                {tag ? (
                  <span
                    title={tag.title}
                    aria-hidden
                    className="ml-1.5 inline-flex shrink-0 items-center gap-0.5 text-[0.58rem] font-medium uppercase tracking-[0.16em]"
                  >
                    {tag.icon}
                    {tag.label}
                  </span>
                ) : null}
                {marked ? (
                  <>
                    <span className={`ml-1 text-[1.1em] leading-none text-[var(--chip-accent)]${tag ? ' shrink-0' : ''}`} title={editedHint} aria-hidden>
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

            {entries ? menuList(item.id, entries) : null}
          </>
        )}
      </Sortable>
    );
  };

  /* A group's box: its name (grip + menu) and its chips, or a "drag here" line.
   * The box holding the dragged chip lights up. */
  const box = (group: SavedSetGroup, g: SavedSetGroups) => {
    const ids = containers[group.id] ?? [];
    const entries = menu?.id === group.id ? boxMenu(group, g.copy, g.onDelete) : null;
    const target = drag != null && !groupIds.includes(drag.id) && containerOf(containers, drag.id) === group.id;
    const movable = boxes.length > 1;
    return (
      <Sortable key={group.id} id={group.id} disabled={!movable}>
        {({ attributes, listeners, setActivatorNodeRef, isDragging }) => (
          <div
            role="group"
            aria-label={group.name}
            style={accentOf(group.color)}
            className={[
              'inline-flex flex-wrap items-center gap-1.5 rounded-[var(--radius-md)] border px-1.5 py-1 transition-colors',
              group.color ? 'border-[var(--chip-accent)]' : 'border-[var(--color-rule-strong)]',
              target ? 'border-dashed bg-[var(--chip-accent-soft)]' : 'bg-[var(--color-paper-2)]',
              isDragging ? 'shadow-md' : '',
            ].join(' ')}
          >
            <span className="relative inline-flex items-center self-stretch">
              {movable ? (
                <button
                  type="button"
                  ref={setActivatorNodeRef}
                  {...attributes}
                  {...listeners}
                  className="flex items-center self-stretch px-1 cursor-grab touch-none text-[var(--color-ink-4)] hover:text-[var(--color-ink-2)] active:cursor-grabbing"
                  aria-label={`${g.copy.move}: ${group.name}`}
                  title={g.copy.dragHint}
                >
                  {GRIP}
                </button>
              ) : null}
              <span
                className={`${movable ? '' : 'pl-1'} max-w-[12rem] truncate text-[0.65rem] font-medium uppercase tracking-[0.12em] ${
                  group.color ? 'text-[var(--chip-accent)]' : 'text-[var(--color-ink-3)]'
                }`}
              >
                {group.name}
              </span>
              <button
                type="button"
                onClick={() => setMenu((cur) => (cur?.id === group.id ? null : { id: group.id }))}
                className="px-1.5 text-[0.8rem] text-[var(--color-ink-3)] opacity-60 hover:opacity-100"
                aria-haspopup="menu"
                aria-expanded={entries != null}
                aria-label={`${g.copy.options}: ${group.name}`}
                title={g.copy.options}
              >
                ⋯
              </button>
              {entries ? menuList(group.id, entries) : null}
            </span>
            <SortableContext items={ids} strategy={rectSortingStrategy}>
              {ids.length > 0 ? (
                ids.flatMap((id) => {
                  const item = byId.get(id);
                  return item ? [chip(item)] : [];
                })
              ) : (
                <span className="px-1 py-1 text-[0.75rem] text-[var(--color-ink-4)]">{g.copy.empty}</span>
              )}
            </SortableContext>
          </div>
        )}
      </Sortable>
    );
  };

  /* While a chip from a box is dragged (or the loose row is empty), the row
   * ends in a target that takes it out of its box. */
  const ungroupTarget =
    groups && drag != null && !groupIds.includes(drag.id) && (drag.from !== LOOSE || containers[LOOSE].length === 0)
      ? groups.copy.loose
      : null;

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
        collisionDetection={collision}
        measuring={{ droppable: { strategy: MeasuringStrategy.Always } }}
        onDragStart={onDragStart}
        onDragOver={onDragOver}
        onDragEnd={onDragEnd}
        onDragCancel={() => setDrag(null)}
      >
        <SortableContext items={containers[LOOSE]} strategy={rectSortingStrategy}>
          {containers[LOOSE].flatMap((id) => {
            const item = byId.get(id);
            return item ? [chip(item)] : [];
          })}
          {shared.map(chip)}
        </SortableContext>
        {ungroupTarget != null ? <DropTarget id={LOOSE} label={ungroupTarget} /> : null}
        {groups && boxes.length > 0 ? (
          <SortableContext items={groupIds} strategy={rectSortingStrategy}>
            {boxes.map((g) => box(g, groups))}
          </SortableContext>
        ) : null}
      </DndContext>

      <button
        type="button"
        onClick={() => setModal({ mode: 'save', item: null })}
        className={ADD_BUTTON}
        title={copy.saveHint}
      >
        {copy.save}
      </button>

      {groups && !collapsed ? (
        <button
          type="button"
          onClick={() => setModal({ mode: 'group', item: null })}
          className={ADD_BUTTON}
          title={groups.copy.addHint}
        >
          {groups.copy.add}
        </button>
      ) : null}

      {modal?.mode === 'group' && groups ? (
        <SavedSetSaveModal
          words={groups.copy}
          mode={modal.item ? 'edit' : 'save'}
          initial={modal.item}
          checkbox={null}
          onSubmit={(name, _checked, color) =>
            (modal.item ? groups.onEdit(modal.item, name, color) : groups.onCreate(name, color)).then(() =>
              setModal(null),
            )
          }
          onClose={() => setModal(null)}
        />
      ) : modal && modal.mode !== 'group' ? (
        <SavedSetSaveModal
          words={copy}
          mode={modal.mode}
          initial={modal.item}
          checkbox={
            checkbox && modal.mode === 'save'
              ? { label: checkbox.label, initial: checkbox.initial(null), disabledHint: checkbox.disabled?.(null) ?? null }
              : null
          }
          onSubmit={(name, checked, color) =>
            (modal.mode === 'save' ? onSaveNew(name, checked, color) : onRename(modal.item, name, color)).then(() =>
              setModal(null),
            )
          }
          onClose={() => setModal(null)}
        />
      ) : null}
    </div>
  );
}

const ADD_BUTTON =
  'px-2.5 py-1 text-[0.8rem] rounded-[var(--radius-sm)] border border-dashed border-[var(--color-rule-strong)] text-[var(--color-ink-3)] hover:text-[var(--color-ink)] hover:border-[var(--color-copper)] transition-colors';

/* The accent is the set's colour (shared tag palette) or copper when uncolored,
 * as CSS vars so active/hover states reuse one source. */
function accentOf(color: TagColor | null): CSSProperties {
  const tone = color ? `--color-tag-${color}` : '--color-copper';
  return { '--chip-accent': `var(${tone})`, '--chip-accent-soft': `var(${tone}-soft)` } as CSSProperties;
}

/* One sortable node: a chip, or a group's box. A shared chip is disabled and not
 * among any context's items, so it neither drags nor moves for a drag. */
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

/* The loose row's drop target while a boxed chip is dragged. */
function DropTarget({ id, label }: { id: string; label: string }) {
  const { setNodeRef, isOver } = useDroppable({ id });
  return (
    <span
      ref={setNodeRef}
      className={`${chipBase} border-dashed px-2.5 py-1 ${
        isOver
          ? 'border-[var(--color-copper)] bg-[var(--color-copper-soft)] text-[var(--color-copper)]'
          : 'border-[var(--color-rule-strong)] text-[var(--color-ink-4)]'
      }`}
    >
      {label}
    </span>
  );
}

/* Six-dot grip — the standard "drag me" affordance, dim at rest and
 * brightening on hover (mirrors the ResizeHandle idiom). */
const GRIP = (
  <svg width="9" height="14" viewBox="0 0 9 14" fill="currentColor" aria-hidden>
    {[3, 7, 11].flatMap((cy) => [2, 7].map((cx) => <circle key={`${cx}-${cy}`} cx={cx} cy={cy} r="1.1" />))}
  </svg>
);
