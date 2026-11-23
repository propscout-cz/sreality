"""What we already hold about the one unit a bought deed sold. A deed states a price and no area (ČÚZK's report
has none), so the area is borrowed, named by its source, from a record we hold of the same sale:

  reas  a registered sale of the building with the deed's unit label and the deed's price, dated within the deed's
        own span (signing to entry; the held sales seen carry the entry date): the same sale, held twice. A resale
        of the unit at the same price in another year is another sale, and a sale two deeds could claim is no
        deed's.
  ad    an advertisement at a door of the building that was asking exactly the deed's price in a snapshot taken
        no later than the deed was signed (else took effect, else was entered): an ad repriced to that sum
        afterwards is not evidence. Kept only when it is unambiguous: one property among the ads at that price,
        one area among them, and no other single-unit deed of the purchase at that price.

Only a deed over exactly one unit and no building is matched (one price over several units is no unit's price);
reas wins over an ad. Nothing is stored: a read over the account's deeds, recomputed as ads and sales arrive. The
per-m² figure comes from the one measure (rule 23), on the deed's price and the borrowed area."""

from __future__ import annotations

from typing import Any

import psycopg
from psycopg.rows import dict_row

_MATCH_SQL = """
with deed as (
  select d.id, d.price, u.label,
         coalesce(d.signed_on, d.effective_on, d.entered_on) as agreed_on,
         least(d.signed_on, d.effective_on, d.entered_on) as first_on,
         greatest(d.signed_on, d.effective_on, d.entered_on) as last_on,
         count(*) over (partition by d.price) as at_this_price
    from cadastre_deeds d
   cross join lateral (
          select min(e->>'label') filter (where e->>'kind' = 'jednotka') as label,
                 count(*) filter (where e->>'kind' = 'jednotka') as units,
                 count(*) filter (where e->>'kind' = 'stavba') as buildings
            from jsonb_array_elements(d.properties) e) u
   where d.account_id = %(account)s and d.purchase_id = %(purchase)s
     and d.currency = 'CZK' and d.price is not null and u.units = 1 and u.buildings = 0
), sale as (
  select id, source_record_id
    from (select d.id, s.source_record_id, count(*) over (partition by d.id) as sales_of_deed,
                 count(*) over (partition by s.source, s.source_record_id) as deeds_of_sale
            from deed d
            join sold_transactions s on s.building_code = %(building)s and s.unit_label = d.label
                                    and s.price_czk = d.price and s.sold_at between d.first_on and d.last_on) m
   where sales_of_deed = 1 and deeds_of_sale = 1
), ad as (
  select d.id, min(l.property_id) as property_id, min(l.area_m2) as area_m2,
         case when count(distinct l.disposition) = 1 then min(l.disposition) end as disposition,
         min(l.category_main) as category_main
    from deed d
    join ruian_address_points ap on ap.stavebni_objekt_code = %(building)s
    join listing_location ll on ll.ruian_adm_kod = ap.kod_adm
    join listings l on l.id = ll.listing_id and l.category_type = 'prodej' and l.area_m2 is not null
                   and l.category_main not in ('pozemek', 'ostatni')
    join properties p on p.id = l.property_id and p.status = 'active'
   where d.at_this_price = 1
     and exists (select 1 from listing_snapshots s
                  where s.listing_id = l.id and s.price_czk = d.price and s.scraped_at::date <= d.agreed_on)
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
