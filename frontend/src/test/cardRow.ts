import type { CardRow } from '@/lib/queries';

/* One Browse card row for a roll fixture: a 2+kk sale with a basis-labelled
 * per-m² figure; override what the test is about. */
export const cardRow = (id: number, extra: Partial<CardRow> = {}): CardRow => ({
  property_id: id,
  listing_id: id * 10,
  sreality_id: null,
  display_label: `Místo ${id}`,
  disposition: '2+kk',
  subtype: null,
  area_m2: 50,
  price_czk: 5_000_000 + id,
  price_per_m2: 100_000,
  price_per_m2_basis: 'sale_capital_czk_m2',
  first_seen_at: '2026-10-10T08:00:00Z',
  last_seen_at: '2026-10-12T08:00:00Z',
  is_active: true,
  tom_days: 2,
  category_main: 'byt',
  category_type: 'prodej',
  source: 'sreality',
  source_id_native: String(id),
  mf_gross_yield_pct: null,
  total_price_change_pct: null,
  price_change_count: 0,
  all_sources: ['sreality'],
  active_sources: ['sreality'],
  source_count: 1,
  ...extra,
});
