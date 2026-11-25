/* What each notification's change_kind is called. The feed's chip and the watchdog editor's picker
 * read the same label, so the word a user turned on is the word on the alert it sends. */
import type { DispatchChangeKind, MatchReason, WatchdogDispatch } from './types';

/* change_kind → label + the colour of its leading dot in the feed (only design tokens that are
 * known to exist; the chip itself stays neutral). */
export const CHANGE_META: Record<DispatchChangeKind, { label: string; dot: string }> = {
  new:               { label: 'New match',         dot: 'var(--color-copper)' },
  price_drop:        { label: 'Price drop',        dot: 'var(--color-sage)' },
  price_rise:        { label: 'Price rise',        dot: 'var(--color-brick)' },
  inactive:          { label: 'Left the market',   dot: 'var(--color-ink-4)' },
  reactivated:       { label: 'Back on the market', dot: 'var(--color-copper)' },
  new_source:        { label: 'New portal',        dot: 'var(--color-copper)' },
  shared_collection: { label: 'Shared collection', dot: 'var(--color-ink-3)' },
  shared_dashboard:  { label: 'Shared dashboard',  dot: 'var(--color-ink-3)' },
  shared_pipeline:   { label: 'Shared pipeline',   dot: 'var(--color-ink-3)' },
};

/* Why a New match entered its watchdog's slice (api/notifications.MATCH_REASONS): the line under its
 * chip. A price change is named by its direction, read off the alert's two frozen prices. */
const MATCH_REASON_LABELS: Record<Exclude<MatchReason, 'price'>, string> = {
  listed: 'Newly listed',
  back: 'Back on the market',
  portal: 'On a new portal',
  changed: 'Ad updated',
  other: 'Now matches',
};

export function matchReasonLabel(
  d: Pick<WatchdogDispatch, 'match_reason' | 'trigger_price_czk' | 'prev_price_czk'>,
): string {
  const reason = d.match_reason ?? 'listed';
  if (reason !== 'price') return MATCH_REASON_LABELS[reason];
  return d.prev_price_czk != null && d.trigger_price_czk != null && d.trigger_price_czk > d.prev_price_czk
    ? 'Price raised'
    : 'Price cut';
}
