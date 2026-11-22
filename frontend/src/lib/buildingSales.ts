/* The rows of Prodeje v domě: the shared sales we hold and the cadastre deeds this account bought, in one list,
 * newest first, each naming its source. A deed has ONE price for everything it transfers and states no area, so a
 * deed row never carries a per-m² figure, and one covering more than a single unit says what else it covers. */
import type { BuildingSale, CadastreDeed } from './api';

export interface SaleRow {
  key: string;
  date: string | null;
  /* The unit(s) in the cadastre's own form; null = the whole building. */
  unit: string | null;
  /* What a deed transfers besides its unit(s): "+ pozemek", "+ 2 pozemky". */
  also: string | null;
  disposition: string | null;
  area_m2: number | null;
  price_czk: number | null;
  currency: string | null;
  price_per_m2: number | null;
  price_per_m2_basis: string | null;
  source: 'reas' | 'katastr';
  source_url: string | null;
  proceeding: string | null;
}

const plural = (n: number, one: string, few: string, many: string) => (n === 1 ? one : n < 5 ? few : many);

function alsoOf(deed: CadastreDeed): string | null {
  const parcels = deed.properties.filter((p) => p.kind === 'parcela').length;
  const buildings = deed.properties.filter((p) => p.kind === 'stavba').length;
  const units = deed.properties.filter((p) => p.kind === 'jednotka').length;
  const parts: string[] = [];
  if (parcels > 0) parts.push(parcels === 1 ? 'pozemek' : `${parcels} ${plural(parcels, 'pozemek', 'pozemky', 'pozemků')}`);
  if (buildings > 0 && units > 0) parts.push(buildings === 1 ? 'stavba' : `${buildings} stavby`);
  return parts.length > 0 ? `+ ${parts.join(', ')}` : null;
}

export function saleRows(sales: BuildingSale[], deeds: CadastreDeed[]): SaleRow[] {
  const held: SaleRow[] = sales.map((s) => ({
    key: `${s.source}:${s.source_record_id}`, date: s.sold_at, unit: s.unit_label, also: null,
    disposition: s.disposition, area_m2: s.area_m2, price_czk: s.price_czk, currency: 'CZK',
    price_per_m2: s.price_per_m2, price_per_m2_basis: s.price_per_m2_basis, source: 'reas',
    source_url: s.source_url, proceeding: null,
  }));
  const bought: SaleRow[] = deeds.map((d, i) => {
    const units = d.properties.filter((p) => p.kind === 'jednotka').map((p) => p.label);
    return {
      key: `katastr:${d.proceeding}:${i}`, date: d.effective_on ?? d.signed_on,
      unit: units.length > 0 ? units.join(' + ') : null, also: alsoOf(d), disposition: null, area_m2: null,
      price_czk: d.price, currency: d.currency, price_per_m2: null, price_per_m2_basis: null, source: 'katastr',
      source_url: null, proceeding: d.proceeding,
    };
  });
  return [...held, ...bought].sort((a, b) => (b.date ?? '').localeCompare(a.date ?? '') || a.key.localeCompare(b.key));
}
