/* The arrangement of a saved-set bar with GROUPS (migration 672): the loose row
 * and one box per group, each an ordered list of the account's own item ids.
 * The bar drags inside this shape; the API stores it as ONE order over every
 * item (loose first, then each box in box order) plus each item's group, so a
 * surface that ignores groups (the phone's scope row) still reads the desktop's
 * order. Pure, so the drag rules are tested without a pointer. */

import { arrayMove } from '@dnd-kit/sortable';

/* The loose row's container id; a group's is its uuid, so the two never collide. */
export const LOOSE = 'loose';

/* Container id → the item ids it holds, in order. */
export type Containers = Record<string, string[]>;

/* What PUT /filter-presets/layout takes: the boxes in order, every item in order with its box. */
export interface SavedSetLayout {
  groups: string[];
  items: { id: string; group_id: string | null }[];
}

type Grouped = { id: string; group_id?: string | null };

/* Each own item in its box (list order), loose when it names no box this list holds. */
export function containersOf(items: readonly Grouped[], groupIds: readonly string[]): Containers {
  const out: Containers = { [LOOSE]: [] };
  for (const g of groupIds) out[g] = [];
  for (const item of items) {
    const box = item.group_id != null && item.group_id in out ? item.group_id : LOOSE;
    out[box].push(item.id);
  }
  return out;
}

/* The container an id names (a container id answers itself), or undefined. */
export function containerOf(c: Containers, id: string): string | undefined {
  if (id in c) return id;
  return Object.keys(c).find((k) => c[k].includes(id));
}

/* A drag crossing into another container: the item leaves its own and lands at
 * the hovered item's place (the end, over the container itself). Null when the
 * drag has not changed container. */
export function moveAcross(c: Containers, activeId: string, overId: string): Containers | null {
  const from = containerOf(c, activeId);
  const to = containerOf(c, overId);
  if (from == null || to == null || from === to || activeId in c) return null;
  const target = c[to].filter((id) => id !== activeId);
  const at = overId in c ? target.length : Math.max(0, target.indexOf(overId));
  target.splice(at, 0, activeId);
  return { ...c, [from]: c[from].filter((id) => id !== activeId), [to]: target };
}

/* The drop: inside one container the item takes the hovered item's place. */
export function moveWithin(c: Containers, activeId: string, overId: string): Containers {
  const box = containerOf(c, activeId);
  if (box == null || containerOf(c, overId) !== box || overId in c) return c;
  const from = c[box].indexOf(activeId);
  const to = c[box].indexOf(overId);
  return from === to ? c : { ...c, [box]: arrayMove(c[box], from, to) };
}

/* A menu move: the item goes to the end of `target` (LOOSE or a group id). */
export function moveTo(c: Containers, id: string, target: string): Containers {
  const from = containerOf(c, id);
  if (from == null || !(target in c) || from === target) return c;
  return { ...c, [from]: c[from].filter((x) => x !== id), [target]: [...c[target], id] };
}

/* The write: the loose row first, then each box in `groupOrder`. */
export function layoutOf(c: Containers, groupOrder: readonly string[]): SavedSetLayout {
  return {
    groups: [...groupOrder],
    items: [
      ...c[LOOSE].map((id) => ({ id, group_id: null })),
      ...groupOrder.flatMap((g) => (c[g] ?? []).map((id) => ({ id, group_id: g }))),
    ],
  };
}

export function sameLayout(a: SavedSetLayout, b: SavedSetLayout): boolean {
  return JSON.stringify(a) === JSON.stringify(b);
}

/* The optimistic cache patch: the listed items in the layout's order with their
 * new group, unlisted ones (shared) after; the boxes in the layout's order. */
export function applyLayout<T extends Grouped, G extends { id: string }>(
  prev: { data: T[]; total: number; groups?: G[] },
  layout: SavedSetLayout,
): { data: T[]; total: number; groups?: G[] } {
  const byId = new Map(prev.data.map((i) => [i.id, i]));
  const listed = new Set(layout.items.map((i) => i.id));
  const moved = layout.items.flatMap(({ id, group_id }) => {
    const item = byId.get(id);
    return item ? [{ ...item, group_id }] : [];
  });
  const groups = prev.groups ?? [];
  const gById = new Map(groups.map((g) => [g.id, g]));
  return {
    ...prev,
    data: [...moved, ...prev.data.filter((i) => !listed.has(i.id))],
    groups: [
      ...layout.groups.flatMap((id) => gById.get(id) ?? []),
      ...groups.filter((g) => !layout.groups.includes(g.id)),
    ],
  };
}
