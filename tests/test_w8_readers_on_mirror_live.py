"""Location W8-3 (#2362), executed: every reader of the legacy boundary table reads the RÚIAN mirror instead
(migrations 725-729). One polygon read (`ruian_unit_render`, the current version's render geometry by (level, code)),
one population store (`obec_population`, by RÚIAN town code and year), and the curated cities keyed by the town code
they already held. Runs in CI's migrations job (`TEST_DATABASE_URL`); every test rolls back.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest

from scraper import db as scraper_db
from scraper import price_stats_db
from tests._live_property import REQUIRED_DB, _mirror_unit

pytestmark = REQUIRED_DB

KRAJ, OKRES, OBEC, OTHER, REGION = 990_019, 990_999, 990_782, 990_783, 990_001
BOX = (14.40, 50.00, 14.50, 50.10)


def _population(cur: Any, code: int, year: int, people: int) -> None:
    cur.execute("INSERT INTO obec_population (obec_kod, year, population) VALUES (%s, %s, %s)", (code, year, people))


def _city(cur: Any, name: str, obec: int) -> int:
    cur.execute("INSERT INTO curated_cities (name, kraj_name, obec_kod) VALUES (%s, 'w8', %s) RETURNING id",
                (name, obec))
    return cur.fetchone()[0]


def _new_version(cur: Any, *, current: bool) -> int:
    cur.execute("INSERT INTO registry_versions (label, kind, source_date, artifact_urls, proj_version, proj_pipeline, "
                "  is_current) VALUES (%s, 'baseline', '2026-10-31', '{}', 'test', 'test', %s) RETURNING id",
                (f"w8-{uuid.uuid4().hex}", current))
    return int(cur.fetchone()[0])


def _dataset(cur: Any) -> int:
    cur.execute("INSERT INTO price_stat_datasets (slug, name) VALUES (%s, 'w8') RETURNING id",
                (f"w8-{uuid.uuid4().hex[:8]}",))
    return int(cur.fetchone()[0])


def _drawn(cur: Any, code: int) -> list[tuple[Any, ...]]:
    cur.execute("SELECT level::text, code, name, ST_AsText(ST_Envelope(geom)) FROM ruian_unit_render WHERE code = %s",
                (code,))
    return cur.fetchall()


def test_the_render_view_draws_one_polygon_per_unit_through_a_close_and_reopen(cur: Any) -> None:
    """W7's first SCD-2 close: the closed row keeps the current version's geometry, the reopened one has only the
    staged version's. The view joins by version, so the code reads one row before the flip and one after."""
    cur.execute("UPDATE registry_versions SET is_current = false WHERE is_current")
    old = _new_version(cur, current=True)
    closed = _mirror_unit(cur, "obec", OBEC, "Starov", BOX, version=old)
    staged = _new_version(cur, current=False)
    cur.execute("UPDATE ruian_admin_units SET valid_to = '2026-10-31' WHERE id = %s", (closed,))
    cur.execute("INSERT INTO ruian_admin_units (level, code, name, name_norm, path, valid_from, first_version_id, "
                "  last_version_id) VALUES ('obec', %s, 'Novov', 'novov', 'x2', '2026-11-01', %s, %s) RETURNING id",
                (OBEC, staged, staged))
    reopened = cur.fetchone()[0]
    cur.execute("INSERT INTO ruian_admin_unit_geometries (unit_id, registry_version_id, purpose, "
                "  generalization_tolerance_m, geom, representative_point, inscribed_radius_m, centroid_point, "
                "  containment_radius_m) "
                "SELECT %s, %s, 'render', 0, g, ST_PointOnSurface(g), 0, ST_Centroid(g), 0 "
                "  FROM (SELECT ST_Multi(ST_MakeEnvelope(14.4, 50.0, 14.6, 50.1, 4326)) AS g) s", (reopened, staged))

    assert _drawn(cur, OBEC) == [("obec", OBEC, "Starov", "POLYGON((14.4 50,14.4 50.1,14.5 50.1,14.5 50,14.4 50))")]
    cur.execute("UPDATE registry_versions SET is_current = (id = %s) WHERE id IN (%s, %s)", (staged, old, staged))
    assert _drawn(cur, OBEC) == [("obec", OBEC, "Novov", "POLYGON((14.4 50,14.4 50.1,14.6 50.1,14.6 50,14.4 50))")]


def test_the_render_view_and_the_population_are_the_service_roles_alone(cur: Any) -> None:
    """The mirror carries no browser grant; the SPA reads it through owner views (design R7)."""
    for relation in ("ruian_unit_render", "obec_population"):
        cur.execute("SELECT has_table_privilege('anon', %s, 'SELECT'), has_table_privilege('authenticated', %s, "
                    "'SELECT'), has_table_privilege('service_role', %s, 'SELECT')", (relation, relation, relation))
        assert cur.fetchone() == (False, False, True), relation
    cur.execute("SELECT relrowsecurity FROM pg_class WHERE oid = 'public.obec_population'::regclass")
    assert cur.fetchone() == (True,)


def test_the_picker_is_the_registers_kraj_okres_obec_tree_with_the_latest_population(cur: Any) -> None:
    region = _mirror_unit(cur, "region_soudrznosti", REGION, "Region", BOX)
    kraj = _mirror_unit(cur, "kraj", KRAJ, "Krajov", BOX, parent=region)
    okres = _mirror_unit(cur, "okres", OKRES, "Okresov", BOX, parent=kraj)
    _mirror_unit(cur, "obec", OBEC, "Obcov", BOX, parent=okres)
    gone = _mirror_unit(cur, "obec", OTHER, "Zaniklov", BOX, parent=okres, version=_new_version(cur, current=False))
    cur.execute("UPDATE ruian_admin_units SET valid_to = '2026-10-31' WHERE id = %s", (gone,))  # drawn at an old one
    _population(cur, OBEC, 2025, 900)
    _population(cur, OBEC, 2026, 1_000)
    cur.execute("SELECT * FROM price_stat_obce_picker_public WHERE id = ANY(%s) ORDER BY id",
                ([KRAJ, OKRES, OBEC, OTHER, REGION],))
    assert cur.fetchall() == [
        (KRAJ, "kraj", "Krajov", None, None),
        (OBEC, "obec", "Obcov", OKRES, 1_000),
        (OKRES, "okres", "Okresov", KRAJ, None),
    ]


def test_the_picker_reads_the_current_version_through_a_load_that_closes_before_it_publishes(cur: Any) -> None:
    """#2378's shape: a load closes a unit and stages its reopened row hours before the publish. The picker keeps the
    published row until the version flips, as ruian_unit_render does, and never shows both or neither."""
    cur.execute("UPDATE registry_versions SET is_current = false WHERE is_current")
    old = _new_version(cur, current=True)
    closed = _mirror_unit(cur, "obec", OBEC, "Starov", BOX, version=old)
    staged = _new_version(cur, current=False)
    cur.execute("UPDATE ruian_admin_units SET valid_to = '2026-10-31' WHERE id = %s", (closed,))
    _mirror_unit(cur, "obec", OBEC, "Novov", BOX, version=staged, valid_from="2026-11-01")
    cur.execute("SELECT name FROM price_stat_obce_picker_public WHERE id = %s", (OBEC,))
    assert cur.fetchall() == [("Starov",)]
    cur.execute("UPDATE registry_versions SET is_current = (id = %s) WHERE id IN (%s, %s)", (staged, old, staged))
    cur.execute("SELECT name FROM price_stat_obce_picker_public WHERE id = %s", (OBEC,))
    assert cur.fetchall() == [("Novov",)]


def test_the_choropleth_draws_each_metric_row_with_its_towns_mirror_polygon_and_name(cur: Any) -> None:
    _mirror_unit(cur, "obec", OBEC, "Obcov", BOX)
    dataset = _dataset(cur)
    cur.execute("INSERT INTO price_stat_city_metrics (dataset_id, obec_id, window_years, sale_cagr_pct) VALUES "
                "(%s, %s, 5, 4.5), (%s, %s, 5, 1.0)", (dataset, OBEC, dataset, OTHER))
    scraper_db.refresh_matview(cur.connection, "price_stat_choropleth")
    cur.execute("SELECT obec_id, obec_name, geojson, sale_cagr_pct FROM price_stat_choropleth_public "
                "WHERE dataset_id = %s", (dataset,))
    (row,) = cur.fetchall()
    assert row[:2] == (OBEC, "Obcov") and row[3] == 4.5, "a town the register cannot draw is not drawn"
    assert json.loads(row[2])["type"] == "MultiPolygon"


def test_price_stat_growth_reads_its_towns_without_a_boundary_table(cur: Any) -> None:
    """The obec id is a register code by construction (723), so the growth RPC checks it against nothing."""
    dataset = _dataset(cur)
    months = [{"year": 2020 + y, "month": 1, "price": 50_000 + 1_000 * y, "active_count": 30} for y in range(3)]
    price_stats_db.upsert_observations(cur.connection, dataset_id=dataset, obec_id=OBEC, category_type_cb=1,
                                       run_id=None, months=months)
    cur.execute("SELECT obec_id, sale_latest_price FROM price_stat_growth(%s)", (dataset,))
    assert cur.fetchall() == [(OBEC, 52_000)]


def test_sold_coverage_names_the_town_whose_pip_polygon_holds_the_point(cur: Any) -> None:
    _mirror_unit(cur, "obec", OBEC, "Obcov", BOX)
    cur.execute("SELECT obec_kod, obec_name, fetched_at, last_attempt_status FROM sold_coverage(50.05, 14.45)")
    assert cur.fetchall() == [(OBEC, "Obcov", None, None)]
    cur.execute("SELECT count(*) FROM sold_coverage(49.0, 17.0)")
    assert cur.fetchone() == (0,), "a point in no town has no cell"


def test_a_curated_city_reads_its_population_polygon_and_rules_by_its_register_code(cur: Any) -> None:
    _mirror_unit(cur, "obec", OBEC, "Obcov", BOX)
    _population(cur, OBEC, 2026, 23_344)
    index = f"w8_{uuid.uuid4().hex[:8]}"
    city = _city(cur, index, OBEC)
    cur.execute("INSERT INTO city_index_revisions (source_filename, row_count, raw_rows) VALUES ('w8.csv', 1, "
                "'[]'::jsonb) RETURNING source_revision")
    cur.execute("INSERT INTO city_index_values (city_id, source_revision, index_name, value) VALUES (%s, %s, %s, 8)",
                (city, cur.fetchone()[0], index))

    cur.execute("SELECT obec_kod, population, population_as_of_year FROM curated_cities_public WHERE city_id = %s",
                (city,))
    assert cur.fetchone() == (OBEC, 23_344, 2026)
    cur.execute("SELECT geojson FROM curated_city_polygons_public WHERE city_id = %s", (city,))
    assert json.loads(cur.fetchone()[0])["type"] in ("Polygon", "MultiPolygon")
    cur.execute("SELECT * FROM curated_cities_matching(%s::jsonb)",
                (json.dumps([{"index_name": index, "op": ">=", "value": 6}]),))
    assert cur.fetchall() == [(OBEC,)]


def test_the_population_loader_upserts_by_code_and_year_and_keeps_every_year(cur: Any) -> None:
    from scripts.load_obec_population import upsert_population

    _population(cur, OBEC, 2025, 900)
    assert upsert_population(cur.connection, [(OBEC, 2026, 1_000), (OTHER, 2026, 50)]) == 2
    assert upsert_population(cur.connection, [(OBEC, 2026, 1_000)]) == 0, "an unchanged file writes nothing"
    assert upsert_population(cur.connection, [(OBEC, 2026, 1_010)]) == 1
    cur.execute("SELECT year, population FROM obec_population WHERE obec_kod = %s ORDER BY year", (OBEC,))
    assert cur.fetchall() == [(2025, 900), (2026, 1_010)]


def _revisions(cur: Any) -> int:
    cur.execute("SELECT count(*) FROM city_index_revisions")
    return cur.fetchone()[0]


def test_the_seed_writes_a_revision_for_an_existing_city_read_back_with_its_towns_population(cur: Any) -> None:
    """#2454: the seed writes index revisions for cities already in curated_cities, matched by name and kraj."""
    from scripts.seed_curated_cities import CsvRow, seed

    _mirror_unit(cur, "obec", OBEC, "Obcov", BOX)
    _population(cur, OBEC, 2026, 23_344)
    name = f"w8-{uuid.uuid4().hex[:8]}"
    city = _city(cur, name, OBEC)

    seed(cur.connection, [CsvRow(name=name, kraj_name="w8", values={"celkove_hodnoceni": 7.5, "exekuce": 3.0})],
         source_filename="obce_v_datech_2026.csv", uploaded_by=None)
    cur.execute("SELECT c.population, v.index_name, v.value FROM curated_cities_public c "
                "JOIN city_index_values_public v ON v.city_id = c.city_id WHERE c.city_id = %s ORDER BY 2", (city,))
    assert cur.fetchall() == [(23_344, "celkove_hodnoceni", 7.5), (23_344, "exekuce", 3.0)]


def test_a_row_naming_no_existing_city_refuses_the_run_by_name_and_writes_nothing(cur: Any) -> None:
    """Jinov is a town in the register but no curated city, so its row is refused, never bound by name: a new city
    arrives as a one-line data migration checked against the register (Hodonín is two obce in one kraj)."""
    from scripts.seed_curated_cities import CsvRow, SeedRefused, seed

    _mirror_unit(cur, "obec", OBEC, "Obcov", BOX)
    _mirror_unit(cur, "obec", OTHER, "Jinov", (14.50, 50.00, 14.60, 50.10))
    known = f"w8-{uuid.uuid4().hex[:8]}"
    _city(cur, known, OBEC)
    before = _revisions(cur)
    cur.execute("DELETE FROM city_index_definitions WHERE index_name = 'exekuce'")
    cur.execute("SELECT count(*) FROM city_index_definitions")
    (definitions,) = cur.fetchone()

    with pytest.raises(SeedRefused, match="Jinov, w8"):
        seed(cur.connection, [CsvRow(name=known, kraj_name="w8", values={"exekuce": 3.0}),
                              CsvRow(name="Jinov", kraj_name="w8", values={"exekuce": 4.0})],
             source_filename="obce_v_datech_2026.csv", uploaded_by=None)
    assert _revisions(cur) == before
    cur.execute("SELECT count(*) FROM city_index_definitions")
    assert cur.fetchone() == (definitions,), "the refused run upserted no definition either"


def test_an_estimation_of_a_typed_point_takes_the_town_under_it_for_the_mf_reference(cur: Any) -> None:
    """#2454: a raw-spec subject has no stored location, so the MF hand-off asks the resolver's registry view at the
    current version for the town covering its point; a point in no town has none."""
    from api.estimation_runs import _typed_point_obec_kod
    from toolkit import TargetSpec

    _mirror_unit(cur, "obec", OBEC, "Obcov", BOX)
    assert _typed_point_obec_kod(cur.connection, TargetSpec(lat=50.05, lng=14.45)) == OBEC
    assert _typed_point_obec_kod(cur.connection, TargetSpec(lat=49.0, lng=17.0)) is None
