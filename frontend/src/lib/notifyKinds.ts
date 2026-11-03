/* The property events a watchdog can be set to tell (migration 720): which, in what order, the
 * default, and the one click rule. Only the watchdog editor and the create dialog read it, so it
 * rides in their chunks, never the first visit. */
import type { WatchdogKind } from './types';

/* In the order a watchdog stores them (api/notifications.WATCHDOG_KINDS), each with what it means
 * for a property the watchdog's filters match. */
export const WATCHDOG_KINDS: ReadonlyArray<{ kind: WatchdogKind; hint: string }> = [
  { kind: 'new', hint: 'A property that starts matching: a newly listed one.' },
  { kind: 'price_drop', hint: 'Its asking price went down.' },
  { kind: 'price_rise', hint: 'Its asking price went up.' },
  { kind: 'inactive', hint: 'Gone from every portal for a day.' },
  { kind: 'reactivated', hint: 'Back on a portal after a Delisted alert.' },
  { kind: 'new_source', hint: 'Listed on one more portal.' },
];

/* What a new watchdog tells: what every watchdog told before the setting existed (migration 720). */
export const DEFAULT_NOTIFY_KINDS: WatchdogKind[] = ['new', 'price_drop'];

/* One click on a kind. Relisted answers a Delisted the same watchdog told, so switching it on
 * switches Delisted on too, and switching Delisted off takes Relisted with it. */
export function toggleNotifyKind(kinds: ReadonlyArray<WatchdogKind>, kind: WatchdogKind): WatchdogKind[] {
  const next = new Set(kinds);
  if (next.has(kind)) {
    next.delete(kind);
    if (kind === 'inactive') next.delete('reactivated');
  } else {
    next.add(kind);
    if (kind === 'reactivated') next.add('inactive');
  }
  return WATCHDOG_KINDS.map((k) => k.kind).filter((k) => next.has(k));
}
