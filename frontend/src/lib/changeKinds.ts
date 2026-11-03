/* What each notification's change_kind is called, and which of them a watchdog can be set to tell
 * (migration 720). The feed's chip and the watchdog editor's picker read the same label, so the
 * word a user turned on is the word on the alert it sends. */
import type { DispatchChangeKind, WatchdogKind } from './types';

/* change_kind → label + the colour of its leading dot in the feed (only design tokens that are
 * known to exist; the chip itself stays neutral). */
export const CHANGE_META: Record<DispatchChangeKind, { label: string; dot: string }> = {
  new:               { label: 'New match',         dot: 'var(--color-copper)' },
  price_drop:        { label: 'Price drop',        dot: 'var(--color-sage)' },
  price_rise:        { label: 'Price rise',        dot: 'var(--color-brick)' },
  inactive:          { label: 'Delisted',          dot: 'var(--color-ink-4)' },
  reactivated:       { label: 'Relisted',          dot: 'var(--color-copper)' },
  new_source:        { label: 'New source',        dot: 'var(--color-copper)' },
  shared_collection: { label: 'Shared collection', dot: 'var(--color-ink-3)' },
  shared_dashboard:  { label: 'Shared dashboard',  dot: 'var(--color-ink-3)' },
  shared_pipeline:   { label: 'Shared pipeline',   dot: 'var(--color-ink-3)' },
};

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
