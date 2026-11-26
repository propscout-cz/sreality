"""Registered sales in a property's building: the sales we hold whose own key names the building of the
property's door (`sold_transactions.building_code` = the door's `stavebni_objekt_code`). Facts as stored, newest
first; the per-m² figure and its basis come from the one measure (rule 23). Nothing here says which sale, if
any, is this property's own flat. A property that holds a plot ad has none (`api/building_scope.py`)."""

from __future__ import annotations

from typing import Any

from psycopg.rows import dict_row

from api.building_scope import holds_no_building_ad

_SALES_SQL = """
select s.source,
       s.source_record_id,
       s.sold_at,
       s.price_czk,
       s.asking_last_czk,
       s.category_main,
       s.disposition,
       s.area_m2,
       s.unit_label,
       s.source_url,
       measure_price_per_m2(s.price_czk::numeric, s.area_m2, s.category_main, s.category_type) as price_per_m2,
       measure_price_per_m2_basis(s.category_main, s.category_type) as price_per_m2_basis
  from properties p
  join listing_location ll on ll.listing_id = p.location_listing_ref_id
  join ruian_address_points ap on ap.kod_adm = ll.ruian_adm_kod
  join sold_transactions s on s.building_code = ap.stavebni_objekt_code
 where p.id = %s and p.status = 'active'
   and not {out_of_scope}
 order by s.sold_at desc, s.source_record_id
""".format(out_of_scope=holds_no_building_ad("p"))


def load_building_sales(conn: Any, property_id: int) -> list[dict[str, Any]]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(_SALES_SQL, (property_id,))
        rows = cur.fetchall()
    return [
        {**row, "sold_at": row["sold_at"].isoformat(),
         "area_m2": float(row["area_m2"]) if row["area_m2"] is not None else None,
         "price_per_m2": float(row["price_per_m2"]) if row["price_per_m2"] is not None else None}
        for row in rows
    ]
