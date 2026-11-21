/* The property events a watchdog or a watched collection can be set to tell (migration 735): which,
 * in what order, the defaults, and the one click rule. Only the watchdog editor, the create dialog
 * and the collection page read it, so it rides in their chunks, never the first visit. */
import type { CollectionKind, NotifyKind, WatchdogKind } from './types';

/* What each kind means for a property the source follows. */
const HINTS: Record<NotifyKind, string> = {
  new: 'A property that enters the slice: newly listed, or one that now matches (a price cut, a new portal…).',
  price_drop: 'Its asking price went down.',
  price_rise: 'Its asking price went up.',
  inactive: 'Gone from every portal for a day: sold or withdrawn.',
  reactivated: 'On a portal again after a day or more off the market.',
  new_source: 'Listed on one more portal.',
};

/* In the order a watchdog or a collection stores them (api/notifications.KIND_ORDER). */
export const WATCHDOG_KINDS: ReadonlyArray<{ kind: WatchdogKind; hint: string }> = (
  ['new', 'price_drop', 'price_rise', 'inactive', 'reactivated', 'new_source'] as const
).map((kind) => ({ kind, hint: HINTS[kind] }));

/* A collection's: every kind but New match, as a property it holds is never new to it. */
export const COLLECTION_KINDS: ReadonlyArray<{ kind: CollectionKind; hint: string }> = (
  ['price_drop', 'price_rise', 'inactive', 'reactivated', 'new_source'] as const
).map((kind) => ({ kind, hint: HINTS[kind] }));

const ORDER: ReadonlyArray<NotifyKind> = ['new', 'price_drop', 'price_rise', 'inactive', 'reactivated', 'new_source'];

/* What a new watchdog tells: what every watchdog told before the setting existed (migrations 735, 754:
 * a return was a New match until Back on the market became a watchdog's own kind). */
export const DEFAULT_NOTIFY_KINDS: WatchdogKind[] = ['new', 'price_drop', 'reactivated'];

/* One click on a kind: on or off, kept in the stored order. */
export function toggleNotifyKind<K extends NotifyKind>(kinds: ReadonlyArray<K>, kind: K): K[] {
  const next = new Set<NotifyKind>(kinds);
  if (!next.delete(kind)) next.add(kind);
  return ORDER.filter((k): k is K => next.has(k));
}
