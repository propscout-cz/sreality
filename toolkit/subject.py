"""The subject of an estimate or a tool when it is an ad we hold: its facts and its place (ruling R7)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from location_data.claims_common import SUBJECT_LOCATION_JOIN

if TYPE_CHECKING:
    import psycopg

_MATCH_BY_ID_SQL = (
    "SELECT l.id, l.source, l.source_id_native, l.sreality_id, "
    "ST_Y(ll.geom) AS lat, ST_X(ll.geom) AS lng, "
    "COALESCE(lp.area_m2, l.area_m2) AS area_m2, "
    "COALESCE(lp.disposition, l.disposition) AS disposition, "
    "l.floor, "
    "COALESCE(lp.current_price_czk, l.price_czk) AS price_czk, "
    "COALESCE(lp.category_type, l.category_type) AS category_type "
    f"FROM listings l {SUBJECT_LOCATION_JOIN} "
    "WHERE l.id = %(id)s"
)


def match_listing(conn: "psycopg.Connection", listing_id: int) -> dict[str, Any] | None:
    """The subject facts of an ad we hold (property-canonical, else its own row), or None."""
    with conn.cursor() as cur:
        cur.execute(_MATCH_BY_ID_SQL, {"id": int(listing_id)})
        row = cur.fetchone()
    if row is None:
        return None
    return {
        "listing_id": int(row[0]),
        "source": row[1],
        "source_id_native": row[2],
        "sreality_id": int(row[3]) if row[3] is not None else None,
        "spec": {
            "lat": float(row[4]) if row[4] is not None else None,
            "lng": float(row[5]) if row[5] is not None else None,
            "area_m2": float(row[6]) if row[6] is not None else None,
            "disposition": row[7], "floor": row[8],
        },
        "price_czk": int(row[9]) if row[9] is not None else None,
        "category_type": row[10],
    }


# The subject's MF facts when it is one of our adverts: its PROPERTY's golden record and
# its location advert's stored location codes (ruling R7) -- the inputs properties_public
# hands mf_reference(), so a run's reference equals the listing page's. COALESCE keeps the
# advert's own value for a pre-attach row.
_SUBJECT_MF_FACTS_SQL = f"""
    SELECT COALESCE(lp.category_main, l.category_main),
           COALESCE(lp.category_type, l.category_type),
           COALESCE(lp.current_price_czk, l.price_czk),
           COALESCE(lp.condition, l.condition),
           COALESCE(lp.has_balcony, l.has_balcony), COALESCE(lp.terrace, l.terrace),
           COALESCE(lp.furnished, l.furnished), COALESCE(lp.garage, l.garage),
           COALESCE(lp.has_lift, l.has_lift), COALESCE(lp.building_type, l.building_type),
           ll.obec_kod, ll.katastr_kod, ll.country_status::text
      FROM listings l
      {SUBJECT_LOCATION_JOIN}
     WHERE l.id = %(listing_id)s
"""
_MF_FACT_KEYS = (
    "category_main", "category_type", "price_czk", "condition", "has_balcony", "terrace",
    "furnished", "garage", "has_lift", "building_type", "obec_kod", "katastr_kod",
    "country_status",
)


def subject_mf_facts(conn: "psycopg.Connection", listing_id: int) -> dict[str, Any] | None:
    """What mf_reference() needs about an ad we hold besides its disposition and area, or None."""
    with conn.cursor() as cur:
        cur.execute(_SUBJECT_MF_FACTS_SQL, {"listing_id": int(listing_id)})
        row = cur.fetchone()
    return dict(zip(_MF_FACT_KEYS, row)) if row is not None else None
