/* Browse's sort: the field union, its URL token, the parser that falls back to
 * the default, and the one sort menu. A leaf module, no imports: filters.ts
 * reads a stored preset's sort through parseSort without importing queries.ts
 * (which imports filters.ts, and re-exports everything here).
 * tests/test_browse_read_path_guardrail.py reads SORTABLE_FIELDS below by naive
 * quote matching, so keep comments out of that array. */

export type SortField =
  | 'first_seen_at' | 'last_seen_at'
  | 'price_czk' | 'price_per_m2'
  | 'total_price_change_pct'
  | 'area_m2' | 'plot_area_m2'
  | 'street_name'
  | 'mf_gross_yield_pct'
  /* One portal's "Newest first" (MS19), never user-selectable and never in a URL —
   * derived by effectiveSort() in queries.ts. */
  | `newest_ad_at_${string}`;

export type SortDirection = 'asc' | 'desc';

export interface SortSpec {
  field: SortField;
  direction: SortDirection;
}

/* first_seen_at DESC = "newest listings first" (operator decision 2026-07-07,
 * with the browse_list read model): meaningful (genuinely new listings, not
 * "whichever portal the scraper touched last" — touch_listings bumps
 * last_seen_at market-wide every cycle) and IMMUTABLE, so keyset cursors stay
 * valid across snapshot rebuilds. last_seen_at remains a selectable option. */
export const DEFAULT_SORT: SortSpec = { field: 'first_seen_at', direction: 'desc' };

/* Exactly the fields CARD_SORTS orders by: a URL or preset naming a sort field without a control reads as DEFAULT_SORT. */
const SORTABLE_FIELDS: ReadonlyArray<SortField> = [
  'first_seen_at', 'last_seen_at',
  'price_czk', 'price_per_m2',
  'total_price_change_pct',
  'area_m2', 'plot_area_m2',
  'street_name',
  'mf_gross_yield_pct',
];

export const parseSort = (raw: string | null): SortSpec => {
  if (!raw) return DEFAULT_SORT;
  const desc = raw.startsWith('-');
  const field = (desc ? raw.slice(1) : raw) as SortField;
  if (!(SORTABLE_FIELDS as ReadonlyArray<string>).includes(field)) return DEFAULT_SORT;
  /* The menu offers last seen most recent first only, so an ascending link reads that way, never as an unlabelled order. */
  return { field, direction: desc || field === 'last_seen_at' ? 'desc' : 'asc' };
};

export const sortToParam = (s: SortSpec): string =>
  `${s.direction === 'desc' ? '-' : ''}${s.field}`;

/* The cards lane's sort menu — Browse's card view and the phone feed's Řazení
 * row offer exactly these. Two bookend the file by date (first_seen_at — when
 * the listing entered our archive), one by when a portal last showed it, two by
 * price, two by price/m², two by price change (total_price_change_pct: the
 * signed percent from the first observed price of the shown price's lineage to
 * the shown price -- the card's price-move badge and the Price change filter
 * read the same column, NULL below two price points), two by area (area_m2:
 * floor area, or the PLOT for pozemek — rule 23), two by lot (plot_area_m2: THE plot measure the Lot area
 * filter reads, never the bare estate_area, which four portals leave empty on
 * land), two by street name (street_name: the location ad's street under the
 * Czech collation, migration 665 -- Č after C, Ch after H), two by MF gross
 * yield. The price change, the areas, the street and the yield are nullable (an
 * ad seen at one price has no change, a flat has no lot, a village house often no
 * street, the yield only for sale apartments); the query orders nullsFirst:false
 * so a row without one sorts to the end either way. */
export const CARD_SORTS: ReadonlyArray<{ label: string; spec: SortSpec }> = [
  { label: 'Newest first',      spec: { field: 'first_seen_at', direction: 'desc' } },
  { label: 'Oldest first',      spec: { field: 'first_seen_at', direction: 'asc'  } },
  /* last_seen_at is bumped market-wide every cycle, so a keyset scroll crossing a browse_list rebuild can skip a row (DEFAULT_SORT's note says why first_seen_at is stable). */
  { label: 'Naposledy viděno',  spec: { field: 'last_seen_at',  direction: 'desc' } },
  { label: 'Price: low → high', spec: { field: 'price_czk',     direction: 'asc'  } },
  { label: 'Price: high → low', spec: { field: 'price_czk',     direction: 'desc' } },
  { label: 'Price/m²: low → high', spec: { field: 'price_per_m2', direction: 'asc'  } },
  { label: 'Price/m²: high → low', spec: { field: 'price_per_m2', direction: 'desc' } },
  { label: 'Změna ceny: největší pokles', spec: { field: 'total_price_change_pct', direction: 'asc'  } },
  { label: 'Změna ceny: největší nárůst', spec: { field: 'total_price_change_pct', direction: 'desc' } },
  { label: 'Plocha: od nejmenší', spec: { field: 'area_m2',     direction: 'asc'  } },
  { label: 'Plocha: od největší', spec: { field: 'area_m2',     direction: 'desc' } },
  { label: 'Pozemek: od nejmenšího', spec: { field: 'plot_area_m2', direction: 'asc'  } },
  { label: 'Pozemek: od největšího', spec: { field: 'plot_area_m2', direction: 'desc' } },
  { label: 'Ulice: A → Z',      spec: { field: 'street_name',   direction: 'asc'  } },
  { label: 'Ulice: Z → A',      spec: { field: 'street_name',   direction: 'desc' } },
  { label: 'Výnos MF: high → low', spec: { field: 'mf_gross_yield_pct', direction: 'desc' } },
  { label: 'Výnos MF: low → high', spec: { field: 'mf_gross_yield_pct', direction: 'asc'  } },
];
