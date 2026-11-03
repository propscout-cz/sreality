/* The property events a watchdog or a watched collection can be set to tell (migration 720): which,
 * in what order, the defaults, and the one click rule. Only the watchdog editor, the create dialog
 * and the collection page read it, so it rides in their chunks, never the first visit. */
import type { CollectionKind, WatchdogKind } from './types';

/* In the order a watchdog or a collection stores them (api/notifications.WATCHDOG_KINDS), each with
 * what it means for a property the source follows. */
export const WATCHDOG_KINDS: ReadonlyArray<{ kind: WatchdogKind; hint: string }> = [
  { kind: 'new', hint: 'A property that starts matching: a newly listed one.' },
  { kind: 'price_drop', hint: 'Its asking price went down.' },
  { kind: 'price_rise', hint: 'Its asking price went up.' },
  { kind: 'inactive', hint: 'Gone from every portal for a day: sold or withdrawn.' },
  { kind: 'reactivated', hint: 'On a portal again after it was told as Left the market.' },
  { kind: 'new_source', hint: 'Listed on one more portal.' },
];

/* A collection's: every kind but New match, as a property it holds is never new to it. */
export const COLLECTION_KINDS = WATCHDOG_KINDS.filter(
  (k): k is { kind: CollectionKind; hint: string } => k.kind !== 'new',
);

/* What a new watchdog tells: what every watchdog told before the setting existed (migration 720). */
export const DEFAULT_NOTIFY_KINDS: WatchdogKind[] = ['new', 'price_drop'];

/* One click on a kind. Back on the market answers a Left the market the same source told, so
 * switching it on switches Left the market on too, and switching that off takes it with it. */
export function toggleNotifyKind<K extends WatchdogKind>(kinds: ReadonlyArray<K>, kind: K): K[] {
  const next = new Set<WatchdogKind>(kinds);
  if (next.has(kind)) {
    next.delete(kind);
    if (kind === 'inactive') next.delete('reactivated');
  } else {
    next.add(kind);
    if (kind === 'reactivated') next.add('inactive');
  }
  return WATCHDOG_KINDS.map((k) => k.kind).filter((k): k is K => next.has(k));
}
