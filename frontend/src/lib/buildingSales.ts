/* The rows of Prodeje v domě: the shared sales we hold and the cadastre deeds this account bought, in one list,
 * newest first, each naming its source. A deed has ONE price for everything it transfers and states no area. A
 * deed over one unit that the server matched to a record we hold is shown through it: matched to a held sale, it
 * is that sale's row (the same sale is not listed twice) with the deed's proceeding beside it; matched to an
 * advertisement, the deed row borrows the ad's area and names the ad's property. Every other deed row carries no
 * area and no per-m² figure, and one covering more than a single unit says so. */
import type { BuildingSale, CadastreDeed } from './api';

export interface SaleRow {
  key: string;
  date: string | null;
  /* The unit(s) in the cadastre's own form; null = no unit: the whole building, or what `scope` says. */
  unit: string | null;
  /* A deed without a unit that is not one whole building: it covers several, or none (land only). */
  scope: string | null;
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
  /* The cadastre proceeding: of a deed row, or of the deed that confirms a held sale's row. */
  proceeding: string | null;
  /* The property whose advertisement lent this deed row its area and disposition. */
  area_property_id: number | null;
  /* The one unit a deed row sold, by its cadastre id: the unit whose information can be asked for. */
  unit_iskn_id: number | null;
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

const MANY_UNITS = 3;

/* Whether the list shows anything this account bought: a deed row, or a held sale a bought deed confirms. */
export const showsCadastre = (rows: SaleRow[]): boolean => rows.some((r) => r.proceeding != null);

export function saleRows(sales: BuildingSale[], deeds: CadastreDeed[]): SaleRow[] {
  const heldIds = new Set(sales.map((s) => s.source_record_id));
  const confirmed = new Map<string, CadastreDeed>();
  for (const d of deeds) {
    const id = d.match?.source === 'reas' ? d.match.source_record_id : null;
    if (id != null && heldIds.has(id)) confirmed.set(id, d);
  }
  const held: SaleRow[] = sales.map((s) => ({
    key: `${s.source}:${s.source_record_id}`, date: s.sold_at, unit: s.unit_label, scope: null,
    also: confirmed.has(s.source_record_id) ? alsoOf(confirmed.get(s.source_record_id)!) : null,
    disposition: s.disposition, area_m2: s.area_m2, price_czk: s.price_czk, currency: 'CZK',
    price_per_m2: s.price_per_m2, price_per_m2_basis: s.price_per_m2_basis, source: 'reas',
    source_url: s.source_url, proceeding: confirmed.get(s.source_record_id)?.proceeding ?? null, area_property_id: null, unit_iskn_id: null,
  }));
  const shownAsHeld = (d: CadastreDeed) =>
    d.match?.source === 'reas' && d.match.source_record_id != null && heldIds.has(d.match.source_record_id);
  const bought: SaleRow[] = deeds.filter((d) => !shownAsHeld(d)).map((d, i) => {
    const units = d.properties.filter((p) => p.kind === 'jednotka').map((p) => p.label);
    const unitIds = d.properties.filter((p) => p.kind === 'jednotka').map((p) => p.iskn_id);
    const buildings = d.properties.filter((p) => p.kind === 'stavba').length;
    /* One price for everything the deed transfers: a deed over two buildings is not "the whole building". */
    const scope = units.length > MANY_UNITS ? `${units.length} ${plural(units.length, 'jednotka', 'jednotky', 'jednotek')} za jednu cenu`
      : units.length > 0 || buildings === 1 ? null : buildings > 1 ? `${buildings} stavby společně` : 'bez stavby';
    const ad = d.match?.source === 'ad' ? d.match : null;
    return {
      key: `katastr:${d.proceeding}:${i}`, date: d.effective_on ?? d.signed_on,
      unit: units.length > 0 && units.length <= MANY_UNITS ? units.join(' + ') : null, scope, also: alsoOf(d),
      disposition: ad?.disposition ?? null, area_m2: ad?.area_m2 ?? null,
      price_czk: d.price, currency: d.currency, price_per_m2: ad?.price_per_m2 ?? null,
      price_per_m2_basis: ad?.price_per_m2_basis ?? null, source: 'katastr',
      source_url: null, proceeding: d.proceeding, area_property_id: ad?.property_id ?? null,
      unit_iskn_id: unitIds.length === 1 && buildings === 0 ? unitIds[0] : null,
    };
  });
  return [...held, ...bought].sort((a, b) => (b.date ?? '').localeCompare(a.date ?? '') || a.key.localeCompare(b.key));
}
