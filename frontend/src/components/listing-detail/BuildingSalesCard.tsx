import { useQuery } from '@tanstack/react-query';
import Panel from '@/components/Panel';
import { Th } from '@/components/table';
import { fetchPropertyBuildingSales, type BuildingSale } from '@/lib/api';
import { fmtArea, fmtCzk, fmtMeasuredPricePerM2, fmtShortDate } from '@/lib/format';
import { ppm2BasisFromToken } from '@/lib/measure';
import { advertLink } from '@/lib/portals';

/* Prodeje v domě: the registered sales we hold in the building at the property's door, each with its unit and its
 * source. Building-level facts: the card never says which sale, if any, is this property's own flat. A property
 * whose building has no sale we hold shows no card (most have none: the source covers 24 months). */

const SOURCES: Record<string, string> = { reas: 'Reas' };

export default function BuildingSalesCard({ propertyId }: { propertyId: number }) {
  const { data } = useQuery({
    queryKey: ['property-building-sales', propertyId],
    queryFn: () => fetchPropertyBuildingSales(propertyId),
    staleTime: 10 * 60_000,
  });
  const sales = data?.sales ?? [];
  if (sales.length === 0) return null;
  return (
    <Panel title="Prodeje v domě" count={sales.length} note="registrované prodeje ve stejné budově">
      {/* A table that scrolls keeps its own box (system.md § Section cards). */}
      <div className="overflow-x-auto rounded-[var(--radius-md)] border border-[var(--color-rule)]">
        <table className="w-full text-sm">
          <thead className="border-b border-[var(--color-rule)] bg-[var(--color-paper-2)]">
            <tr>
              <Th size="xs">Prodáno</Th>
              <Th size="xs">Jednotka</Th>
              <Th size="xs">Dispozice</Th>
              <Th size="xs" align="right">Plocha</Th>
              <Th size="xs" align="right">Cena</Th>
              <Th size="xs" align="right">Za m²</Th>
              <Th size="xs">Zdroj</Th>
            </tr>
          </thead>
          <tbody>
            {sales.map((s) => <SaleRow key={`${s.source}:${s.source_record_id}`} sale={s} />)}
          </tbody>
        </table>
      </div>
    </Panel>
  );
}

function SaleRow({ sale }: { sale: BuildingSale }) {
  const source = SOURCES[sale.source] ?? sale.source;
  const link = advertLink(sale);
  return (
    <tr className="border-b border-[var(--color-rule-soft)] last:border-b-0">
      <td className="px-2.5 py-2 font-mono text-[13px] tabular-nums whitespace-nowrap">{fmtShortDate(sale.sold_at)}</td>
      <td className="px-2.5 py-2 font-mono text-[13px] whitespace-nowrap">
        {sale.unit_label ?? <span className="font-sans text-[var(--color-ink-3)]">celá budova</span>}
      </td>
      <td className="px-2.5 py-2 whitespace-nowrap">{sale.disposition ?? '—'}</td>
      <td className="px-2.5 py-2 text-right font-mono text-[13px] tabular-nums whitespace-nowrap">{fmtArea(sale.area_m2)}</td>
      <td className="px-2.5 py-2 text-right font-mono text-[13px] tabular-nums whitespace-nowrap">{fmtCzk(sale.price_czk)}</td>
      <td className="px-2.5 py-2 text-right font-mono text-[13px] tabular-nums whitespace-nowrap">
        {fmtMeasuredPricePerM2(sale.price_per_m2, ppm2BasisFromToken(sale.price_per_m2_basis))}
      </td>
      <td className="px-2.5 py-2 whitespace-nowrap text-[var(--color-ink-2)]">
        {link ? (
          <a href={link} target="_blank" rel="noreferrer"
            className="underline decoration-[var(--color-rule-strong)] underline-offset-2 hover:text-[var(--color-copper)]">
            {source}
          </a>
        ) : source}
      </td>
    </tr>
  );
}
