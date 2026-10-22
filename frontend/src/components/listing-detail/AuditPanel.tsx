/* The property page's Audit tab: how fresh the data is, and where it came from.
 * Its own chunk (the tab is opened rarely; scripts/bundle-budget.mjs). */
import { fmtShortDate } from '@/lib/format';
import { portalLabel } from '@/lib/portals';
import type { PropertyPublic } from '@/lib/queries';
import type { ListingFreshnessCheckPublic, PropertySource } from '@/lib/types';
import { SectionLabel } from '@/components/section';
import FreshnessBlock from './FreshnessBlock';

/* Freshness checks are stored against the legacy sreality_id: empty by
   construction for a non-sreality advert. Then every advert's portal id and the
   span we saw it over — the handles to look it up by. */
export default function AuditPanel({
  property,
  sources,
  checks,
}: {
  property: PropertyPublic;
  sources: PropertySource[];
  checks: ListingFreshnessCheckPublic[];
}) {
  return (
    <div className="space-y-10">
      {property.sreality_id != null && (
        <FreshnessBlock sreality_id={property.sreality_id} checks={checks} label="Kontroly aktuálnosti" />
      )}
      <div>
        <SectionLabel>Identifikátory</SectionLabel>
        <table className="mt-3 w-full max-w-2xl text-sm">
          <tbody>
            <tr className="border-b border-[var(--color-rule-soft)]">
              <th scope="row" className="py-2 pr-4 text-left font-normal text-[var(--color-ink-2)]">Nemovitost</th>
              <td className="py-2 font-mono tabular-nums">{property.property_id}</td>
              <td />
            </tr>
            {sources.map((s) => (
              <tr key={s.id} className="border-b border-[var(--color-rule-soft)]">
                <th scope="row" className="py-2 pr-4 text-left font-normal text-[var(--color-ink-2)]">
                  {portalLabel(s.source) ?? s.source}
                </th>
                <td className="py-2 pr-4 font-mono tabular-nums break-all">{s.source_id_native ?? '—'}</td>
                <td className="py-2 font-mono tabular-nums text-[var(--color-ink-3)] whitespace-nowrap">
                  {fmtShortDate(s.first_seen_at)} – {s.is_active ? 'dnes' : fmtShortDate(s.last_seen_at)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
