"""`public.in_map_area` (migration 694) answers a TRUE circle and the readers' old box, executed.

What has to stay true, and what only executing the predicate shows:

  * **EXACT CIRCLE.** A point half a metre inside the radius is in and half a metre outside is out, in every
    direction, at a small, a mid and the largest radius Browse offers, at Prague's latitude and further north.
    The geodesic reference is PostGIS's own ST_Project. RED by: the band prefilter cutting the circle's
    diagonals (a band narrower than the circle), or a planar / spherical distance in place of the spheroid.
  * **THE BOX UNCHANGED.** Each edge is independently nullable, as the readers' inline band was.
  * **NO POINT, NO AREA MATCH.** A row without a point falls out of any area and stays in when there is none.
  * **ONE PREDICATE, EVERY READER.** browse_list_visible and browse_map_cells answer the same circle the same way.

`browse_list` is a real table, so the reader tests seed it inside a transaction that empties it first and always
rolls back. Skip posture: the migrations lane sets DB_RAILS_REQUIRED=1, so a lane that loses its
TEST_DATABASE_URL goes RED instead of reporting a green skip.
"""

from __future__ import annotations

import os

import pytest

_DB_URL = os.environ.get("TEST_DATABASE_URL")
_REQUIRED = os.environ.get("DB_RAILS_REQUIRED") == "1"
pytestmark = pytest.mark.skipif(
    not _DB_URL and not _REQUIRED,
    reason="TEST_DATABASE_URL not set — this rail runs in CI's migrations lane",
)

PRAGUE = (50.0755, 14.4378)
NORTH = (54.9, 9.0)
NO_BOX = (None, None, None, None)
_AREA = ("%s::float8, " * 9).rstrip(", ")


@pytest.fixture()
def conn():
    if not _DB_URL:
        pytest.fail(
            "DB_RAILS_REQUIRED=1 but TEST_DATABASE_URL is not set — the migrations lane "
            "is misconfigured and this rail would otherwise have skipped green."
        )
    import psycopg

    with psycopg.connect(_DB_URL, autocommit=False) as c:
        c.execute("delete from public.browse_list")
        yield c
        c.rollback()


def _in(cur, lat, lng, box=NO_BOX, circle=(None, None, None)) -> bool | None:
    cur.execute(f"select public.in_map_area({_AREA})", (lat, lng, *box, *circle))
    return cur.fetchone()[0]


@pytest.mark.parametrize("centre", [PRAGUE, NORTH])
@pytest.mark.parametrize("radius", [100.0, 1_000.0, 10_000.0])
def test_the_circle_is_exact_in_every_direction(conn, centre, radius):
    lat, lng = centre
    cur = conn.cursor()
    cur.execute(
        """
        with c as (select st_makepoint(%(lng)s::float8, %(lat)s::float8)::geography as g),
        p as (
          select az, d, st_project(c.g, d, radians(az))::geometry as pt
          from c, generate_series(0, 330, 30) az, unnest(array[%(r)s - 0.5, %(r)s + 0.5]) d
        )
        select az, d, public.in_map_area(st_y(pt), st_x(pt), null, null, null, null,
                                        %(lat)s::float8, %(lng)s::float8, %(r)s::float8)
        from p order by az, d
        """,
        {"lat": lat, "lng": lng, "r": radius},
    )
    rows = cur.fetchall()
    assert len(rows) == 24
    wrong = [(az, d, hit) for az, d, hit in rows if hit is not (d < radius)]
    assert wrong == []


def test_the_box_keeps_the_readers_per_edge_band(conn):
    cur = conn.cursor()
    box = (14.0, 50.0, 15.0, 51.0)
    assert _in(cur, 50.5, 14.5, box) is True
    assert _in(cur, 50.5, 15.5, box) is False
    assert _in(cur, 51.5, 14.5, box) is False
    assert _in(cur, 50.5, 15.5, (14.0, 50.0, None, 51.0)) is True  # a NULL edge is open
    assert _in(cur, 50.5, 14.5) is True  # no area at all


def test_a_row_without_a_point_falls_out_of_any_area_and_stays_with_none(conn):
    cur = conn.cursor()
    assert not _in(cur, None, None, (14.0, 50.0, 15.0, 51.0))
    assert not _in(cur, None, None, circle=(*PRAGUE, 1_000.0))
    assert _in(cur, None, None) is True


def test_the_list_source_and_the_map_cells_answer_the_same_circle(conn):
    """Twelve points 50 m inside and twelve 50 m outside a 1 km circle, plus the four corners of its square:
    the corners are what the old circumscribed square let in."""
    lat, lng = PRAGUE
    cur = conn.cursor()
    cur.execute(
        """
        with c as (select st_makepoint(%(lng)s::float8, %(lat)s::float8)::geography as g),
        p as (
          select st_project(c.g, d, radians(az))::geometry as pt
          from c, generate_series(0, 330, 30) az, unnest(array[950.0, 1050.0]) d
          union all
          select st_project(c.g, 1000.0 * sqrt(2) - 20, radians(az))::geometry
          from c, unnest(array[45, 135, 225, 315]) az
        )
        insert into browse_list (property_id, listing_id, lat, lng, category_main, category_type, is_active)
        select row_number() over (), row_number() over (), st_y(pt), st_x(pt), 'byt', 'pronajem', true from p
        """,
        {"lat": lat, "lng": lng},
    )
    circle = {"circle_lat": lat, "circle_lng": lng, "circle_radius_m": 1_000.0}
    cur.execute(
        "select count(*) from public.browse_list_visible(hide_dismissed => false, "
        "circle_lat => %(circle_lat)s::float8, circle_lng => %(circle_lng)s::float8, "
        "circle_radius_m => %(circle_radius_m)s::float8)",
        circle,
    )
    assert cur.fetchone()[0] == 12
    cur.execute(
        "select public.browse_map_cells(category_main_filter => array['byt']::text[], "
        "category_type_filter => 'pronajem'::text, point_budget => 2000::integer, "
        "circle_lat => %(circle_lat)s::float8, circle_lng => %(circle_lng)s::float8, "
        "circle_radius_m => %(circle_radius_m)s::float8)",
        circle,
    )
    assert cur.fetchone()[0]["total"] == 12
