/* What each notification's change_kind is called. The feed's chip and the watchdog editor's picker
 * read the same label, so the word a user turned on is the word on the alert it sends. */
import type { DispatchChangeKind } from './types';

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
