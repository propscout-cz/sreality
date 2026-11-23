"""What we already hold about the one unit a bought deed sold. A deed states a price and no area (ČÚZK's report
has none), so the area is borrowed, named by its source, from a record we hold of the same sale:

  reas  a registered sale of the building with the deed's unit label and the deed's price: the same sale, held twice.
  ad    an advertisement at a door of the building that asked exactly the deed's price at some point and was first
        seen before the deed was entered. Kept only when it is unambiguous: one property among the ads at that
        price, one area among them, and no other single-unit deed of the purchase at that price.

Only a deed over exactly one unit and no building is matched (one price over several units is no unit's price);
reas wins over an ad. Nothing is stored: a read over the account's deeds, recomputed as ads and sales arrive. The
per-m² figure comes from the one measure (rule 23), on the deed's price and the borrowed area."""

from __future__ import annotations

from typing import Any

import psycopg
from psycopg.rows import dict_row

_MATCH_SQL = """
with deed as (
  select d.id, d.price, coalesce(d.entered_on, d.effective_on, d.signed_on) as dated, u.label
    from cadastre_deeds d
   cross join lateral (
          select min(e->>'label') filter (where e->>'kind' = 'jednotka') as label,
                 count(*) filter (where e->>'kind' = 'jednotka') as units,
                 count(*) filter (where e->>'kind' = 'stavba') as buildings
            from jsonb_array_elements(d.properties) e) u
   where d.account_id = %(account)s and d.purchase_id = %(purchase)s
     and d.currency = 'CZK' and d.price is not null and u.units = 1 and u.buildings = 0
), sale as (
  select d.id, min(s.source_record_id) as source_record_id
    from deed d
    join sold_transactions s on s.building_code = %(building)s and s.unit_label = d.label and s.price_czk = d.price
   group by d.id
  having count(*) = 1
), ad as (
  select d.id, min(l.property_id) as property_id, min(l.area_m2) as area_m2,
         case when count(distinct l.disposition) = 1 then min(l.disposition) end as disposition,
         min(l.category_main) as category_main
    from deed d
    join ruian_address_points ap on ap.stavebni_objekt_code = %(building)s
    join listing_location ll on ll.ruian_adm_kod = ap.kod_adm
    join listings l on l.id = ll.listing_id and l.category_type = 'prodej' and l.area_m2 is not null
                   and l.category_main not in ('pozemek', 'ostatni')
                   and l.first_seen_at::date <= d.dated
    join properties p on p.id = l.property_id and p.status = 'active'
   where (l.price_czk = d.price
          or exists (select 1 from listing_snapshots s where s.listing_id = l.id and s.price_czk = d.price))
     and (select count(*) from deed other where other.price = d.price) = 1
   group by d.id
  having count(distinct l.property_id) = 1 and count(distinct l.area_m2) = 1 and count(distinct l.category_main) = 1
)
select d.id, 'reas' as source, sale.source_record_id, null::bigint as property_id, null::numeric as area_m2,
       null::text as disposition, null::numeric as price_per_m2, null::text as price_per_m2_basis
  from deed d join sale on sale.id = d.id
union all
select d.id, 'ad', null, ad.property_id, ad.area_m2, ad.disposition,
       measure_price_per_m2(d.price, ad.area_m2, ad.category_main, 'prodej'),
       measure_price_per_m2_basis(ad.category_main, 'prodej')
  from deed d join ad on ad.id = d.id
 where not exists (select 1 from sale where sale.id = d.id)
"""


def matches(conn: psycopg.Connection, account_id: Any, purchase_id: int, building_code: int) -> dict[int, dict[str, Any]]:
    """Deed id -> its match, for the deeds of one purchase of the account."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(_MATCH_SQL, {"account": account_id, "purchase": purchase_id, "building": building_code})
        rows = cur.fetchall()
    return {
        row.pop("id"): {**row, **{k: float(row[k]) if row[k] is not None else None
                                  for k in ("area_m2", "price_per_m2")}}
        for row in rows
    }
