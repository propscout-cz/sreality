"""browse_map_cells returns a BOUNDED, COMPLETE answer in WORLD TILES (migration 602), executed.

439 replaced an unordered `.limit(50000)` — which hid 52% of the default cohort — with a 20 x 13
grid laid over the caller's bbox; 602 replaces that grid with world tiles
(docs/design/map-counts.md). What has to stay true, and what only executing the function shows:

  * **BOUNDED, and as fine as the bound allows.** At most 1,024 cells with no LIMIT anywhere: the
    function coarsens level by level from level 18 and stops at the first level that fits, so one
    level finer would not have fitted. RED by: a `>=`/`>` slip in the loop's exit, or stopping one
    level late.
  * **COMPLETE.** `total` is the whole mappable cohort and every row is counted exactly once, in
    one tile: the cells sum to the total and there is no off-grid bucket (the latitude is clamped
    inside the tile expression, so the live data's lng -118 / +125 rows, a row south of the old CZ
    box and a polar row are tiles like any other).
  * **ONE FORMULA.** A cell is a tile CENTRE. For a cohort that answers at level 18 the centres are
    the ones tests/fixtures/map_tile_lattice.json pins — the file frontend/src/lib/mapCounts.ts is
    pinned to as well — so the server and the browser cannot disagree on which tile a coordinate
    is in.
  * **STABLE UNDER A PAN.** A tile wholly inside two overlapping bboxes carries the same count in
    both answers: the bbox filters rows, it never moves the grid's origin.
  * **NESTED.** Two level-18 tiles sharing a parent answer as ONE level-17 cell holding both, at
    the parent's centre, once the bound forces the coarser level.

THE OBSTACLE, AND THE TECHNIQUE — copied from tests/test_broker_leaderboard_live.py.
`properties_map_mv` is a MATERIALIZED VIEW: you cannot INSERT into it, and CI's replayed copy is
empty. So each test PARKS it (renames it) and creates a real table of the same shape in its place.
Verified safe: nothing in the catalog depends on properties_map_mv (checked live 2026-08-26 — zero
rewrite dependencies), and `browse_map_cells` is plpgsql, whose body is a string that re-resolves
names at plan time, so it picks up the substitute. The conn fixture is FUNCTION-scoped and parks
before the function is ever called in that session, so no cached plan can be holding the
matview's OID. Everything runs inside a transaction that always rolls back.

Skip posture: the migrations lane sets DB_RAILS_REQUIRED=1, so a lane that loses its
TEST_DATABASE_URL goes RED instead of reporting a green skip.
"""

from __future__ import annotations

import json
import math
import os
from pathlib import Path

import pytest

_DB_URL = os.environ.get("TEST_DATABASE_URL")
_REQUIRED = os.environ.get("DB_RAILS_REQUIRED") == "1"
pytestmark = pytest.mark.skipif(
    not _DB_URL and not _REQUIRED,
    reason="TEST_DATABASE_URL not set — this rail runs in CI's migrations lane",
)

_PARK = """
alter materialized view public.properties_map_mv rename to pmm_parked_for_rail;
create table public.properties_map_mv (like public.pmm_parked_for_rail);
"""

_LATTICE = json.loads(
    (Path(__file__).parent / "fixtures" / "map_tile_lattice.json").read_text(encoding="utf-8")
)
_MAX_CELLS = 1024
_MAX_LAT = 85.05112878
_KEYS = {"total", "clustered", "level", "cells"}


def _tile(lng: float, lat: float, level: int) -> tuple[int, int]:
    """The fixture's formula (its `_doc`), restated so a test can reason about parents."""
    n = 2 ** level
    s = math.sin(math.radians(max(-_MAX_LAT, min(_MAX_LAT, lat))))
    x = math.floor((lng + 180) / 360 * n)
    y = math.floor((1 - math.log((1 + s) / (1 - s)) / (2 * math.pi)) / 2 * n)
    return min(max(x, 0), n - 1), min(max(y, 0), n - 1)


def _centre(x: int, y: int, level: int) -> tuple[float, float]:
    n = 2 ** level
    lng = (x + 0.5) / n * 360 - 180
    lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * (y + 0.5) / n))))
    return round(lng, 5), round(lat, 5)


def _extent(x: int, y: int, level: int) -> tuple[float, float, float, float]:
    """(west, south, east, north) of a tile."""
    n = 2 ** level
    lat = lambda yy: math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * yy / n))))  # noqa: E731
    return x / n * 360 - 180, lat(y + 1), (x + 1) / n * 360 - 180, lat(y)


@pytest.fixture()
def conn():
    if not _DB_URL:
        pytest.fail(
            "DB_RAILS_REQUIRED=1 but TEST_DATABASE_URL is not set — the migrations lane "
            "is misconfigured and this rail would otherwise have skipped green."
        )
    import psycopg

    with psycopg.connect(_DB_URL, autocommit=False) as c:
        yield c
        c.rollback()


def _park(cur) -> None:
    cur.execute(_PARK)


def _seed(cur, points) -> None:
    """One matview row per (lat, lng), with the three id spaces all set to the 1-based
    row ordinal so a filter on any of them selects the same row. Only the columns the
    function reads are set; every other predicate is guarded by a NULL parameter, so
    NULLs elsewhere are inert."""
    cur.executemany(
        "insert into properties_map_mv "
        "(property_id, listing_id, obec_id, lat, lng, category_main, category_type, "
        " is_active) values (%s, %s, %s, %s, %s, 'byt', 'pronajem', true)",
        [(i + 1, i + 1, i + 1, lat, lng) for i, (lat, lng) in enumerate(points)],
    )


# Every argument is cast explicitly. Named-argument resolution is by TYPE as well as by
# name, and an int8 offered to an `integer` parameter is not implicitly castable — the call
# would fail with "function ... does not exist", which reads like a missing migration.
_CASTS = {
    "category_main_filter": "text[]",
    "category_type_filter": "text",
    "bbox_west": "double precision",
    "bbox_east": "double precision",
    "bbox_south": "double precision",
    "bbox_north": "double precision",
    "point_budget": "integer",
    "obec_ids_filter": "bigint[]",
    "property_ids_filter": "bigint[]",
}


def _call(cur, **kw):
    args = {"category_main_filter": ["byt"], "category_type_filter": "pronajem", **kw}
    named = ", ".join(f"{k} => %({k})s::{_CASTS[k]}" for k in args)
    cur.execute(f"select public.browse_map_cells({named})", args)
    return cur.fetchone()[0]


def _cells(r) -> dict[tuple[float, float], int]:
    """{(lng, lat): n} — a cell's centre is its identity."""
    return {(c["lng"], c["lat"]): c["n"] for c in r["cells"]}


def test_the_cells_are_the_fixtures_tile_centres(conn):
    """The twelve fixture points (two polar, three far-flung, seven Czech) sit in twelve
    distinct level-18 tiles, so level 18 fits and every cell is that tile's centre — the
    fixture's x/y through the centre formula, and for the six round-trip entries the
    fixture's own centre. RED by: any drift between the SQL and the fixture's formula."""
    cases = [c for c in _LATTICE["cases"] if c["level"] == 18]
    with conn.cursor() as cur:
        _park(cur)
        _seed(cur, [(c["lat"], c["lng"]) for c in cases])
        r = _call(cur, point_budget=0)
    assert set(r) == _KEYS and r["clustered"] is True and r["level"] == 18
    assert r["total"] == len(cases) == 12
    assert _cells(r) == {_centre(c["x"], c["y"], 18): 1 for c in cases}
    pinned = {(t["centre_lng"], t["centre_lat"]) for t in _LATTICE["centre_round_trip"]
              if t["level"] == 18}
    assert pinned and pinned <= set(_cells(r))


def test_bounded_as_fine_as_the_bound_allows_and_children_sum_to_their_parent(conn):
    """1,100 points in 1,100 distinct level-18 tiles, pairwise sharing a level-17 parent:
    level 18 would not fit the bound, level 17 does with 550 cells, each the parent's centre
    holding both children. RED by: an off-by-one in the loop's exit (1,100 cells at level 18,
    or 275 at level 16), or a centre that is not the parent's."""
    py = _tile(14.4, 50.08, 18)[1]
    parents = [(100_000 + k, py) for k in range(550)]
    points = []
    for px, _ in parents:
        for child in (2 * px, 2 * px + 1):
            lng, lat = _centre(child, 2 * py, 18)
            points.append((lat, lng))
    with conn.cursor() as cur:
        _park(cur)
        _seed(cur, points)
        r = _call(cur, point_budget=0)
    assert r["level"] == 17
    assert len(r["cells"]) == 550 <= _MAX_CELLS
    assert r["total"] == 1100 == sum(c["n"] for c in r["cells"])
    assert _cells(r) == {_centre(px, py, 17): 2 for px, py in parents}


def test_every_row_is_in_exactly_one_tile(conn):
    """No off-grid bucket: the far-flung rows the live data holds (lng -118 / +125), a row
    south of the old CZ box and a polar row are tiles like any other, and the cells sum to
    the total. RED by: dropping the latitude clamp (a polar row raises) or re-adding an
    extent guard (the sum falls short of the total)."""
    inside = [(50.08 + 0.001 * k, 14.42 + 0.001 * k) for k in range(10)]
    far = [(-20.5, -118.2), (59.3, 125.7), (10.0, 100.0), (0.0, 0.0), (48.4, 11.9), (89.0, 14.4)]
    with conn.cursor() as cur:
        _park(cur)
        _seed(cur, inside + far)
        r = _call(cur, point_budget=0)
    assert set(r) == _KEYS
    assert r["total"] == 16 == sum(c["n"] for c in r["cells"])
    assert {_tile(lng, lat, 18) for lat, lng in far} <= {
        _tile(lng, lat, 18) for (lng, lat) in _cells(r)
    }


def test_interior_tiles_are_stable_under_a_pan(conn):
    """A 20 x 20 lattice of points; two overlapping bboxes. Every tile wholly inside both
    carries the same count in both answers. RED by: deriving the grid from the bbox (439's
    shape), which moves every count when the window moves."""
    points = [(50.0 + 0.01 * i, 14.0 + 0.01 * j) for i in range(20) for j in range(20)]
    a = {"bbox_west": 14.0, "bbox_east": 14.2, "bbox_south": 50.0, "bbox_north": 50.2}
    b = {"bbox_west": 14.05, "bbox_east": 14.25, "bbox_south": 50.0, "bbox_north": 50.2}
    with conn.cursor() as cur:
        _park(cur)
        _seed(cur, points)
        ra = _call(cur, point_budget=0, **a)
        rb = _call(cur, point_budget=0, **b)
    assert ra["level"] == rb["level"] == 18

    def inside(centre: tuple[float, float], box: dict[str, float]) -> bool:
        w, s, e, n = _extent(*_tile(*centre, 18), 18)
        return (box["bbox_west"] <= w and e <= box["bbox_east"]
                and box["bbox_south"] <= s and n <= box["bbox_north"])

    common = {k: v for k, v in _cells(ra).items() if inside(k, a) and inside(k, b)}
    assert len(common) > 100
    cells_b = _cells(rb)
    assert all(cells_b[k] == v for k, v in common.items())


def test_the_point_budget_is_a_strict_threshold(conn):
    """At the budget the caller gets points; one row over it, cells.

    The boundary matters because the SPA branches on `clustered` and issues a SECOND read
    for the pins when it is false. RED by: writing `>=` instead of `>` — the map would
    then count a cohort it could have plotted, and (at budget 0, an empty cohort) would
    count nothing at all.
    """
    with conn.cursor() as cur:
        _park(cur)
        _seed(cur, [(50.0 + 0.01 * k, 14.0 + 0.01 * k) for k in range(5)])
        at_budget = _call(cur, point_budget=5)
        assert at_budget["total"] == 5
        assert at_budget["clustered"] is False
        assert at_budget["level"] is None
        assert at_budget["cells"] is None, (
            "below the budget the cells must be WITHHELD, not shipped and ignored — the "
            "caller distinguishes the two lanes on `clustered`, and a populated array "
            "under it is a payload nothing renders."
        )
        over_budget = _call(cur, point_budget=4)
        assert over_budget["clustered"] is True
        assert over_budget["cells"] and sum(c["n"] for c in over_budget["cells"]) == 5


def test_both_prefilter_id_spaces_are_applied(conn):
    """obec_id AND property_id each narrow the cohort: the two spaces the SPA's
    applyPrefilters emits `.in()` on (the listing_id space went with W5's legacy
    city-quality path; migration 597 dropped its parameter).

    RED by: deleting either `= any(...)` predicate — the call below then returns 3, not 1:
    the map would show the unfiltered market while Cards/Table/Count show the filtered one.
    """
    with conn.cursor() as cur:
        _park(cur)
        _seed(cur, [(50.0, 14.0), (50.5, 14.5), (51.0, 15.0)])
        for param in ("obec_ids_filter", "property_ids_filter"):
            r = _call(cur, point_budget=0, **{param: [2]})
            assert r["total"] == 1, (
                f"{param} did not narrow the cohort (total={r['total']}) — the map is "
                "ignoring an allowlist the read it replaces applies."
            )


def test_an_empty_cohort_is_not_a_small_one(conn):
    """Zero rows must report clustered=false with cells null, not an empty answer.

    RED by: writing `clustered := v_total >= point_budget` — at budget 0 an EMPTY cohort
    then reports itself clustered, and the SPA renders "counted by tile, zoom in for pins"
    over a map that has nothing to zoom into. The `>` / `>=` distinction is invisible
    everywhere else because both are correct for every non-degenerate cohort.
    """
    with conn.cursor() as cur:
        _park(cur)
        r = _call(cur, point_budget=0)
    assert r["total"] == 0
    assert r["clustered"] is False
    assert r["level"] is None
    assert r["cells"] is None
