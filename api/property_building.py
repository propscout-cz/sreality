"""What the RÚIAN register states about a property's building: the building of the address point its location ad
stands at (`listing_location.ruian_adm_kod` -> `ruian_address_points.stavebni_objekt_code`). Register values only,
as stated, each code with its label and whether it means "not known"; nothing here is merged with what an ad says.
The completion period is DERIVED from the register's date (`ruian_completion_period`), so the date rides with it.
Protection is the register's own list of zones and regimes the building stands in (heritage, nature, water
source and others), each code with its label; an empty list means the register states none.
A property with no door, or whose door names no building, has none."""

from __future__ import annotations

from typing import Any

from psycopg.rows import dict_row

_CODED = ("usage", "construction", "lift", "heating", "water", "sewer", "gas")

_BUILDING_SQL = """
select b.code,
       b.iskn_building_id,
       b.house_numbers,
       b.typ_so,
       b.completed_on,
       lower(ruian_completion_period(b.completed_on)) as completed_from,
       upper(ruian_completion_period(b.completed_on)) - 1 as completed_to,
       b.floors,
       b.dwelling_count,
       b.built_up_area_m2,
       b.floor_area_m2,
       b.enclosed_volume_m3,
       b.valid_from,
       (select max(rv.source_date) from ruian_building_loads bl
          join registry_versions rv on rv.id = bl.version_id
         where bl.obec_kod = ap.obec_kod and bl.error is null) as source_date,
       (select count(*) from ruian_address_points a
         where a.stavebni_objekt_code = b.code and a.valid_to is null) as entrances,
       -- A code the seeded list does not hold keeps its number, as the coded facts below do.
       (select coalesce(jsonb_agg(jsonb_build_object('code', c.code, 'label', l.label) order by c.code), '[]'::jsonb)
          from unnest(b.protection_codes) as c(code)
          left join ruian_code_labels l on l.code_list = 'protection' and l.code = c.code) as protection,
       {coded}
  from properties p
  left join listing_location ll on ll.listing_id = p.location_listing_ref_id
  left join ruian_address_points ap on ap.kod_adm = ll.ruian_adm_kod
  left join ruian_building_objects b on b.code = ap.stavebni_objekt_code and b.valid_to is null
 where p.id = %s and p.status = 'active'
""".format(coded=",\n       ".join(
    # A code the seeded list does not hold keeps its number: it is a value, not a gap.
    f"case when b.{name}_code is not null then coalesce("
    f"(select jsonb_build_object('code', l.code, 'label', l.label, 'unknown', l.unknown) from ruian_code_labels l"
    f" where l.code_list = '{name}' and l.code = b.{name}_code),"
    f" jsonb_build_object('code', b.{name}_code, 'label', null, 'unknown', false)) end as {name}"
    for name in _CODED))


class NoProperty(LookupError):
    """The property is unknown."""


def load_building(conn: Any, property_id: int) -> dict[str, Any] | None:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(_BUILDING_SQL, (property_id,))
        row = cur.fetchone()
    if row is None:
        raise NoProperty(property_id)
    if row["code"] is None:
        return None
    completed = None
    if row["completed_on"] is not None:
        completed = {"on": row["completed_on"].isoformat(), "from": row["completed_from"],
                     "to": row["completed_to"]}
    out: dict[str, Any] = {
        "code": row["code"],
        "iskn_building_id": row["iskn_building_id"],
        "house_numbers": row["house_numbers"] or [],
        "house_number_kind": row["typ_so"],
        "completed": completed,
        "floors": row["floors"],
        "dwellings": row["dwelling_count"],
        "entrances": row["entrances"],
        "built_up_area_m2": row["built_up_area_m2"],
        "floor_area_m2": row["floor_area_m2"],
        "enclosed_volume_m3": row["enclosed_volume_m3"],
        "valid_from": row["valid_from"].isoformat(),
        # The newest clean load of the door's obec; None when there is none on record.
        "source_date": row["source_date"].isoformat() if row["source_date"] is not None else None,
    }
    out["protection"] = row["protection"]
    out.update({name: row[name] for name in _CODED})
    return out
