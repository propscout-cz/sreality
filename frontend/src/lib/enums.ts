/* Czech labels for enum-typed listing columns. */

import { FILTER_REGISTRY, PRICE_UNIT_LABELS, filterById } from './filterRegistry.generated';

/* One column's slug -> Czech label table, read off the generated filter
 * registry: every filter that compiles straight onto the column contributes
 * its options. A hook-compiled filter (sql_kind null, e.g. building_material's
 * buckets) holds no column values, so it is skipped. */
const ENUM_LABELS = new Map<string, Map<string, string>>();
for (const f of FILTER_REGISTRY.filters) {
  if (!f.pg_column || f.sql_kind == null || !f.enum_values) continue;
  const labels = ENUM_LABELS.get(f.pg_column) ?? new Map<string, string>();
  for (const o of f.enum_values) {
    if (!labels.has(String(o.value))) labels.set(String(o.value), o.label_cs);
  }
  ENUM_LABELS.set(f.pg_column, labels);
}

/* The registry's Czech label for a stored value; the value itself when the
 * registry has none; null when there is no value. */
export function enumLabel(column: string, slug: string | null | undefined): string | null {
  return slug ? (ENUM_LABELS.get(column)?.get(slug) ?? slug) : null;
}

/* The word rendered after a price ("3 000 000 Kč / celkem"). The stored slug is
 * the eight text-priced portals' spelling and is not display Czech; an unhealed
 * sreality row still carries its own 'celkem' / 'měsíc', which falls through as
 * itself, so the two spellings read identically while the heal runs. */
export function priceUnitLabel(unit: string | null | undefined): string | null {
  return unit ? PRICE_UNIT_LABELS[unit] ?? unit : null;
}

/* What an area figure is the area OF — `scraper/area.py`'s vocabulary, which is
 * stamped on every row it derives a headline area for. `unknown` gets no label
 * on purpose: the source carried neither of the two areas, so the honest render
 * is nothing at all rather than the word "unknown", which reads as if the
 * measurement were missing. */
const AREA_BASIS_LABELS: Record<string, string> = {
  usable: 'užitná',
  floor: 'podlahová',
  total: 'celková',
  plot: 'pozemek',
};

export function areaBasisLabel(basis: string | null | undefined): string | null {
  return (basis && AREA_BASIS_LABELS[basis]) || null;
}

/* Czech singular label for a listing's `category_main` (the property type).
 * The single source for the type word shown on cards + the listing tab title. */
export const CATEGORY_MAIN_LABELS: Record<string, string> = {
  byt: 'Byt',
  dum: 'Dům',
  komercni: 'Komerční prostor',
  pozemek: 'Pozemek',
  ostatni: 'Ostatní',
};

export function categoryMainLabel(cm: string | null | undefined): string {
  return (cm && CATEGORY_MAIN_LABELS[cm]) || 'Nemovitost';
}

/* Plural Czech label for category_main aggregate headings ("Byty", "Domy") and
 * the deal-type word ("prodej", "pronájem") — both derived from the filter
 * registry (the registry's category_main labels are plural, its category_type
 * labels are the canonical Czech words), so the stats and location-audit
 * rollups share one source with the Browse filters instead of a private
 * hardcoded copy. Distinct from categoryMainLabel's singular title noun. */
export function categoryMainLabelPlural(cm: string | null | undefined): string {
  return enumLabel('category_main', cm) ?? cm ?? '—';
}

export function categoryTypeLabel(ct: string | null | undefined): string {
  return enumLabel('category_type', ct) ?? ct ?? '—';
}

/* Portal-agnostic `subtype` slugs (migration 152), grouped by the
 * category_main they belong to. Single source: the generated filter registry
 * (toolkit/filter_registry.SUBTYPE_OPTIONS), whose `group` field carries the
 * dum/komercni partition the Browse sidebar renders by selected category_main.
 * Derived here rather than hand-typed so a slug/label added in the Python
 * registry can never drift from the SPA (CI's codegen --check is the guard). */
export interface SubtypeOption {
  slug: string;
  label: string;
}

export const SUBTYPE_LABELS_BY_MAIN: Record<'dum' | 'komercni', SubtypeOption[]> = {
  dum: (filterById('subtype')?.enum_values ?? [])
    .filter((o) => o.group === 'dum')
    .map((o) => ({ slug: String(o.value), label: o.label_cs })),
  komercni: (filterById('subtype')?.enum_values ?? [])
    .filter((o) => o.group === 'komercni')
    .map((o) => ({ slug: String(o.value), label: o.label_cs })),
};

export function subtypeLabel(slug: string): string {
  return enumLabel('subtype', slug) ?? slug;
}

/* --- listing identity: the "what kind" descriptor --------------------------
 * `disposition` (2+kk, apartments) and `subtype` (Kancelář / Rodinný dům,
 * commercial + houses) are two complementary faces of the same descriptor,
 * partitioned by category_main: apartments carry disposition (subtype NULL),
 * houses/commercial carry subtype (disposition usually NULL), land carries
 * neither. These helpers are THE single place that resolves them so every
 * surface (card title, detail header, table, map, watchdog, …) agrees. */
export interface ListingKindFields {
  subtype?: string | null;
  disposition?: string | null;
}

/* Ordered identity tokens, most-specific first: the subtype label (when the
 * portal resolved one) then disposition. Both appear for the rare house listed
 * by room count (e.g. "Rodinný dům", "5+kk"); empty for land. */
export function listingKindParts(row: ListingKindFields): string[] {
  const parts: string[] = [];
  if (row.subtype) parts.push(subtypeLabel(row.subtype));
  if (row.disposition) parts.push(row.disposition);
  return parts;
}

/* The single most-specific identity token, or null. Apartments → "2+kk",
 * commercial/houses → "Ubytování"/"Rodinný dům", land → null. Replaces the
 * bare `disposition ?? '—'` that rendered a dash for every commercial row. */
export function listingKindLabel(row: ListingKindFields): string | null {
  return listingKindParts(row)[0] ?? null;
}

/* The noun for a listing TITLE ("Ubytování na prodej"): the specific subtype
 * when present, else the generic category_main word ("Byt"/"Komerční prostor").
 * Never null — always yields a usable title noun. */
export function listingTypeLabel(
  row: ListingKindFields & { category_main?: string | null },
): string {
  return row.subtype ? subtypeLabel(row.subtype) : categoryMainLabel(row.category_main);
}
