/* Browse's applied filters, as removable pills under the header's count (ruling
 * P09, 2026-10-27): one per filter set beyond the defaults, in the sidebar's order.
 * The list is the one the phone counts (phone/filterRows appliedFilters) and the
 * words are the ones its sheet says (phone/filterSummary rowWords), so a pill can
 * never disagree with either. × sets that one filter back to its default. The
 * type and deal are the header's own words, so never a pill. */

import { sidebarUnits } from '@/components/Filters';
import { appliedFilters, withoutApplied, type AppliedFilter } from '@/components/phone/filterRows';
import { rowWords, rowWordsAreNumbers } from '@/components/phone/filterSummary';
import {
  DEFAULT_FILTERS,
  applySidebarUpdates,
  listingFiltersToRegistryView,
  type ListingFilters,
} from '@/lib/filters';

const HEADLINE = new Set<keyof ListingFilters>(['categoryMain', 'categoryType']);

/* The two scopes that had a chip of their own before the pills keep its words. */
const CHIP_WORDS: Readonly<Record<string, { text: string; title: string }>> = {
  bounds: { text: 'Map area applied', title: 'Clear the map area filter and widen back to the full cohort' },
  /* The scope's way out in the header: a /browse?broker=<id> view (reached via
   * "Go to Browse") opens with Advanced folded, so the sidebar's Broker control
   * is out of sight (see ListingFilters.brokerId). */
  broker: { text: 'Broker scope applied', title: 'Clear the broker scope and widen back to the full cohort' },
};

export default function AppliedFilterPills({
  filters,
  onChange,
  onClearBounds,
}: {
  filters: ListingFilters;
  onChange: (next: ListingFilters) => void;
  /* The map area goes through its own setter, which replaces the history entry. */
  onClearBounds: () => void;
}) {
  const applied = appliedFilters(filters, DEFAULT_FILTERS).filter((a) => a.fields.some((k) => !HEADLINE.has(k)));
  if (applied.length === 0) return null;
  const view = listingFiltersToRegistryView(filters);
  const units = sidebarUnits(filters);
  const clear = (a: AppliedFilter) =>
    a.key === 'bounds'
      ? onClearBounds()
      : onChange(applySidebarUpdates(withoutApplied(filters, DEFAULT_FILTERS, a), []));

  return (
    <ul aria-label="Applied filters" className="flex flex-wrap items-center gap-1.5">
      {applied.map((a) => {
        const chip = CHIP_WORDS[a.key];
        const words = !chip && a.row ? rowWords(a.row, filters, view, units, { lang: 'en' }) : null;
        const full = words && a.row ? rowWords(a.row, filters, view, units, { lang: 'en', full: true }) : null;
        return (
          <li key={a.key}>
            <button
              type="button"
              onClick={() => clear(a)}
              className="group inline-flex items-center gap-1 px-2 py-0.5 text-[0.7rem] tracking-wide rounded-[var(--radius-sm)] bg-[var(--color-copper-soft)] text-[var(--color-copper)] hover:bg-[var(--color-copper)]/15 transition-colors"
              title={chip?.title ?? `Clear ${a.label}${full ? ` (${full})` : ''}`}
            >
              <span>
                {chip ? chip.text : words ? `${a.label}: ` : a.label}
                {words && (
                  <span className={a.row && rowWordsAreNumbers(a.row) ? 'font-mono tabular-nums' : undefined}>
                    {words}
                  </span>
                )}
              </span>
              <span aria-hidden className="opacity-60 group-hover:opacity-100">
                ×
              </span>
            </button>
          </li>
        );
      })}
    </ul>
  );
}
