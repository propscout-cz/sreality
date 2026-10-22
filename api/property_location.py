"""Where a property is: its location ad's point (`properties.location_listing_ref_id`, the place the page, Browse
and the map show) from `listing_location`, rule 25's one store, with its granularity, the page's place label and the
zoom the phone opens at. A detail-by-id read: no served-location predicate (rule 25 keeps those reachable)."""

from __future__ import annotations

from typing import Any

from psycopg.rows import dict_row

NOTE_OBEC = "Poloha jen na úrovni obce"
NOTE_QUARTER = "Poloha jen na úrovni části obce"
NOTE_COARSE = "Poloha jen přibližná, hrubší než obec"

# How finely a point is known, finest first, and the zoom + note each opens with. A point coarser
# than a town (okres, kraj, country) is never labelled as one.
PRECISIONS: dict[str, tuple[int, str | None]] = {
    "street": (16, None),
    "quarter": (14, NOTE_QUARTER),
    "town": (12, NOTE_OBEC),
    "coarse": (10, NOTE_COARSE),
}

# Granularity is compared through location_granularity_rank.rank, never enum order: the row
# carries its own rank and the ranks of the three thresholds the precision steps at.
_LOCATION_SQL = """
select st_y(ll.geom) as lat,
       st_x(ll.geom) as lon,
       ll.granularity::text as granularity,
       r.rank as granularity_rank,
       (select t.rank from location_granularity_rank t
         where t.granularity = 'street') as street_rank,
       (select t.rank from location_granularity_rank t
         where t.granularity = 'cast_obce_or_quarter') as quarter_rank,
       (select t.rank from location_granularity_rank t
         where t.granularity = 'obec') as obec_rank,
       ll.country_status::text as country_status,
       ll.uncertainty_radius_m,
       location_display_label(ll.street_name, ll.house_number_cp, ll.house_number_co,
                              ll.obec_name, ll.cast_obce_name, ll.country_code,
                              ll.country_status) as label
  from properties p
  left join listing_location ll on ll.listing_id = p.location_listing_ref_id
  left join location_granularity_rank r on r.granularity = ll.granularity
 where p.id = %s and p.status = 'active'
"""


class NoLocation(LookupError):
    """No point to answer: `args` = (reason, its Czech sentence), the property unknown or without a point."""


def precision(rank: int | None, street_rank: int, quarter_rank: int, obec_rank: int) -> str:
    """The PRECISIONS key a point's granularity rank falls in."""
    r = rank if rank is not None else 0
    if r >= street_rank:
        return "street"
    if r >= quarter_rank:
        return "quarter"
    return "town" if r >= obec_rank else "coarse"


def load_location(conn: Any, property_id: int) -> dict[str, Any]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(_LOCATION_SQL, (property_id,))
        row = cur.fetchone()
    if row is None:
        raise NoLocation("not_found", "Nemovitost nenalezena")
    if row["lat"] is None or row["lon"] is None:
        raise NoLocation("no_location", "Bez polohy")
    level = precision(row["granularity_rank"], row["street_rank"], row["quarter_rank"], row["obec_rank"])
    zoom, note = PRECISIONS[level]
    radius = row["uncertainty_radius_m"]
    return {
        "lat": float(row["lat"]),
        "lon": float(row["lon"]),
        "granularity": row["granularity"],
        "granularity_rank": row["granularity_rank"],
        "precision": level,
        "country_status": row["country_status"],
        "uncertainty_radius_m": None if radius is None else float(radius),
        "label": row["label"],
        "zoom": zoom,
        "note": note,
    }
