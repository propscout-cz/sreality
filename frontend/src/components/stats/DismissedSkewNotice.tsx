import { czPlural, fmtCount } from '@/lib/format';
import type { ListingFilters } from '@/lib/filters';
import { useHiddenDismissed } from '@/lib/useHiddenDismissed';

/* While the sidebar says Hide dismissed, every chart's sample leaves out the
 * properties the caller dismissed (browse_stats_properties `hide_dismissed`): a
 * hole the caller curated, so the board says so above the charts, with the way
 * back. Silent when this filter hides none. */
export default function DismissedSkewNotice({
  filters,
  onInclude,
}: {
  filters: ListingFilters;
  onInclude?: () => void;
}) {
  const { hasAny, count } = useHiddenDismissed(filters);
  if (filters.showDismissed || !hasAny || count === 0) return null;
  const which =
    count == null
      ? 'nemovitosti, které jste skryli,'
      : `${fmtCount(count)} ${czPlural(count, 'nemovitost', 'nemovitosti', 'nemovitostí')} z tohoto filtru, ${count === 1 ? 'kterou' : 'které'} jste skryli,`;
  return (
    <div
      role="status"
      className="flex flex-wrap items-center gap-x-4 gap-y-2 rounded-[var(--radius-md)] border border-[var(--color-ochre)] bg-[var(--color-ochre-soft)] px-4 py-3 text-[0.8rem] text-[var(--color-ink)]"
    >
      <p className="min-w-0 flex-1">
        <strong className="font-semibold">Statistiky nezapočítávají skryté nemovitosti.</strong>{' '}
        Ve filtru je zvoleno „Hide dismissed“, takže {which} ve vzorku chybí a výsledky mohou být
        zkreslené.
      </p>
      {onInclude && (
        <button
          type="button"
          onClick={onInclude}
          className="shrink-0 rounded-[var(--radius-sm)] border border-[var(--color-ochre)] px-3 py-1.5 text-[0.75rem] font-medium hover:bg-[var(--color-paper-2)] transition-colors"
        >
          Započítat skryté
        </button>
      )}
    </div>
  );
}
