"""Own place search's read models (migration 738, #2449), EXECUTED against the replayed schema (CI's migrations lane,
`TEST_DATABASE_URL`; locally `scripts/local_pg.sh test`). Every test rolls back.

  * THE REBUILD STILL RUNS. rebuild_browse_list() drops browse_list and renames its successor into place every 15
    minutes (658). A matview that named browse_list in its FROM would depend on it and refuse every rebuild, and Browse
    would freeze. place_served_counts reads it through place_served_rows(), so the rebuild swaps the table under it
    and the next refresh counts the NEW table. RED by: the matview reading browse_list directly (the DROP refuses), or
    an unqualified table in the function's body (PG17 runs a matview's refresh under search_path pg_catalog, pg_temp).
  * THE COUNTS. One row per level and code that an active served property names, every deal type; an inactive
    property and a property that names no place count nowhere.
  * THE POINTS. kraj, okres and obec copy the authoritative representative point of the current registry version;
    městská část, část obce and street take the mean of their open doors (FILL's centroid). A closed unit, a point
    drawn at an older version, a closed door and a door with no point are not read.
  * THE NAME INDEX. The text_pattern_ops btree serves the resolver's `=` and the search's `LIKE 'p%'`, and the btree it
    replaced is gone.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Any

import psycopg
import pytest

from location_data.name_index import normalize_name
from tests._live_property import REQUIRED_DB, db_url

pytestmark = REQUIRED_DB

KRAJ, OKRES, OKRES_OLD, BRNO, CLOSED, MOMC, PART, STREET = 116, 3702, 3703, 582786, 599999, 551007, 411582, 1001


@pytest.fixture()
def conn() -> Iterator[psycopg.Connection]:
    connection = psycopg.connect(db_url(), autocommit=True, options="-c statement_timeout=20000 -c lock_timeout=5000")
    with connection, connection.transaction(force_rollback=True):
        yield connection


def _version(conn: psycopg.Connection, *, current: bool) -> int:
    if current:
        conn.execute("UPDATE registry_versions SET is_current = false WHERE is_current")
    return int(conn.execute(
        "INSERT INTO registry_versions (label, kind, source_date, artifact_urls, proj_version, proj_pipeline,"
        " is_current) VALUES (%s, 'baseline', '2026-11-01', '{}', 'p', 'q', %s) RETURNING id",
        (f"test:place-read-models:{uuid.uuid4().hex}", current)).fetchone()[0])


def _geometry(conn: psycopg.Connection, unit: int, version: int, point: tuple[float, float]) -> None:
    """The unit's authoritative polygon at `version`, a small box whose stored representative point is `point`."""
    lng, lat = point
    conn.execute(
        "INSERT INTO ruian_admin_unit_geometries (unit_id, registry_version_id, purpose, generalization_tolerance_m,"
        " geom, representative_point, inscribed_radius_m, centroid_point, containment_radius_m)"
        " SELECT %s, %s, 'authoritative', 0, ST_Multi(ST_Expand(p, 0.01)), p, 0, p, 0"
        "   FROM (SELECT ST_SetSRID(ST_MakePoint(%s, %s), 4326) AS p) s", (unit, version, lng, lat))


def _unit(conn: psycopg.Connection, level: str, code: int, name: str, parent: int | None, version: int, *,
          point: tuple[float, float] | None = None, valid_to: str | None = None) -> int:
    unit = int(conn.execute(
        "INSERT INTO ruian_admin_units (level, code, name, name_norm, parent_id, path, valid_from, valid_to,"
        " first_version_id, last_version_id) VALUES (%s::ruian_level, %s, %s, %s, %s, %s::ltree, '2020-01-01', %s,"
        " %s, %s) RETURNING id",
        (level, code, name, normalize_name(name), parent, f"x{code}", valid_to, version, version)).fetchone()[0])
    if point is not None:
        _geometry(conn, unit, version, point)
    return unit


def _door(conn: psycopg.Connection, kod: int, obec: int, part: int, momc: int, version: int,
          point: tuple[float, float] | None, *, valid_to: str | None = None) -> None:
    conn.execute(
        "INSERT INTO ruian_address_points (kod_adm, obec_unit_id, obec_kod, momc_unit_id, cast_obce_unit_id,"
        " cast_obce_kod, ulice_kod, psc, geom, plati_od, valid_to, first_version_id, last_version_id)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, '60200', ST_SetSRID(ST_MakePoint(%s, %s), 4326), '2020-01-01', %s,"
        " %s, %s)",
        (kod, obec, BRNO, momc, part, PART, STREET, *(point or (None, None)), valid_to, version, version))


def _nodes(plan: dict[str, Any]) -> Iterator[dict[str, Any]]:
    yield plan
    for child in plan.get("Plans", []):
        yield from _nodes(child)


def test_the_rebuild_swaps_browse_list_under_the_counts_and_the_refresh_counts_the_new_table(conn):
    before = conn.execute("SELECT 'public.browse_list'::regclass::oid").fetchone()[0]
    conn.execute("SELECT public.rebuild_browse_list()")
    after = conn.execute("SELECT 'public.browse_list'::regclass::oid").fetchone()[0]
    assert after != before, "the rebuild skipped its tick, so it proved nothing about the DROP"

    conn.execute("DELETE FROM public.browse_list")
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO public.browse_list (property_id, is_active, category_type, region_id, okres_id, obec_id,"
            " cast_obce_id, ulice_id) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            [(1, True, "prodej", KRAJ, OKRES, BRNO, PART, STREET),
             (2, True, "pronajem", KRAJ, OKRES, BRNO, PART, None),
             (3, True, "prodej", KRAJ, OKRES_OLD, 583251, None, None),
             (4, False, "prodej", KRAJ, OKRES, BRNO, PART, STREET),
             (5, True, "prodej", None, None, None, None, None)])

    assert conn.execute("SELECT public.refresh_matview('place_served_counts')").fetchone()[0] == 7
    assert conn.execute(
        "SELECT c.level::text, c.code, c.n FROM public.place_served_counts c ORDER BY c.level, c.code").fetchall() == [
        ("kraj", KRAJ, 3), ("okres", OKRES, 2), ("okres", OKRES_OLD, 1), ("obec", BRNO, 2), ("obec", 583251, 1),
        ("cast_obce", PART, 2), ("ulice", STREET, 1)]


def test_place_facts_copies_the_current_polygon_points_and_averages_the_open_doors(conn):
    old = _version(conn, current=False)
    v = _version(conn, current=True)
    kraj = _unit(conn, "kraj", KRAJ, "Jihomoravský kraj", None, v, point=(16.5, 49.25))
    okres = _unit(conn, "okres", OKRES, "Brno-město", kraj, v, point=(16.625, 49.1875))
    _unit(conn, "okres", OKRES_OLD, "Brno-venkov", kraj, old, point=(16.25, 49.375))
    brno = _unit(conn, "obec", BRNO, "Brno", okres, v, point=(16.6068, 49.1951))
    _unit(conn, "obec", CLOSED, "Starý Dvůr", okres, v, point=(16.0, 49.0), valid_to="2025-01-01")
    momc = _unit(conn, "momc", MOMC, "Brno-střed", brno, v)
    part = _unit(conn, "cast_obce", PART, "Veveří", brno, v)
    _door(conn, 31000001, brno, part, momc, v, (16.5, 49.25))
    _door(conn, 31000002, brno, part, momc, v, (16.75, 49.75))
    _door(conn, 31000003, brno, part, momc, v, (10.0, 50.0), valid_to="2025-01-01")
    _door(conn, 31000004, brno, part, momc, v, None)

    conn.execute("SELECT public.refresh_matview('place_facts')")
    assert conn.execute(
        "SELECT f.level::text, f.code, f.lat, f.lng FROM public.place_facts f WHERE f.code = ANY(%s)"
        " ORDER BY f.level, f.code",
        ([KRAJ, OKRES, OKRES_OLD, BRNO, CLOSED, MOMC, PART, STREET],)).fetchall() == [
        ("kraj", KRAJ, 49.25, 16.5), ("okres", OKRES, 49.1875, 16.625), ("obec", BRNO, 49.1951, 16.6068),
        ("momc", MOMC, 49.5, 16.625), ("cast_obce", PART, 49.5, 16.625), ("ulice", STREET, 49.5, 16.625)]


@pytest.mark.parametrize(("predicate", "index_cond"), [
    ("n.name_norm = 'brno'", "((name_norm = 'brno'::text) AND (registry_version_id = 1))"),
    ("n.name_norm LIKE 'brno%'",
     "((name_norm ~>=~ 'brno'::text) AND (name_norm ~<~ 'brnp'::text) AND (registry_version_id = 1))"),
])
def test_the_text_pattern_ops_name_index_serves_equality_and_a_prefix(conn, predicate, index_cond):
    """No seq scan and no bitmap scan (the trigram GIN serves only bitmaps), so a btree must answer, and the name has
    to bound its scan: the old (name_norm, registry_version_id) btree walks the whole version and filters a LIKE."""
    conn.execute("SET LOCAL enable_seqscan = off")
    conn.execute("SET LOCAL enable_bitmapscan = off")
    plan = conn.execute(
        f"EXPLAIN (FORMAT JSON) SELECT n.id FROM ruian_name_index n WHERE {predicate} AND n.registry_version_id = 1"
    ).fetchone()[0]
    scans = [(node["Index Name"], node.get("Index Cond")) for node in _nodes(plan[0]["Plan"]) if "Index Name" in node]
    assert scans == [("ruian_name_index_norm_prefix", index_cond)]
