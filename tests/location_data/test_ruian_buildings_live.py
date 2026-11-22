"""The building load's writes, against a real schema: a changed building gets a new row, a
vanished one is closed, and an address point's building code follows the file. Gated on
TEST_DATABASE_URL (the schema-replay job); everything rolls back.
"""

from __future__ import annotations

import dataclasses
import datetime
import os

import pytest

_DB_URL = os.environ.get("TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(not _DB_URL, reason="TEST_DATABASE_URL not set")

from location_data import ruian_buildings as rb  # noqa: E402
from location_data import ruian_vfr  # noqa: E402

DAY = datetime.date(2026, 10, 31)
OBEC, CAST, ADM, OTHER_ADM = 990001, 990002, 99000001, 99000002

BUILDING = ruian_vfr.BuildingRow(
    code=77000001, plati_od=datetime.date(2023, 12, 18), typ_so=1, usage_code=6, iskn_building_id=896183101,
    identifying_parcel_id=2099603101, cast_obce_kod=CAST, momc_kod=None, house_numbers=(727, 728),
    completed_on=datetime.date(1945, 12, 31), construction_code=7, floors=8, dwelling_count=75,
    built_up_area_m2=2270, floor_area_m2=None, enclosed_volume_m3=None, lift_code=1, water_code=1,
    sewer_code=1, gas_code=1, heating_code=2, protection_codes=(16, 18))


@pytest.fixture
def conn():
    import psycopg

    with psycopg.connect(_DB_URL) as c:
        try:
            yield c
        finally:
            c.rollback()


@pytest.fixture
def world(conn):
    with conn.cursor() as cur:
        cur.execute(
            "insert into registry_versions (label, kind, source_date, artifact_urls, proj_version, proj_pipeline) "
            "values ('test:buildings', 'baseline', %s, '{}', 'p', 'q') returning id", (DAY,))
        version = rb.Version(int(cur.fetchone()[0]), DAY)
        units = {}
        for level, code in (("obec", OBEC), ("cast_obce", CAST)):
            cur.execute(
                "insert into ruian_admin_units (level, code, name, name_norm, path, valid_from, "
                "first_version_id, last_version_id) values (%s::ruian_level, %s, %s, %s, %s::ltree, %s, %s, %s) "
                "returning id", (level, code, str(code), str(code), f"t{code}", DAY, version.id, version.id))
            units[code] = int(cur.fetchone()[0])
        for kod in (ADM, OTHER_ADM):
            cur.execute(
                "insert into ruian_address_points (kod_adm, obec_unit_id, obec_kod, psc, plati_od, "
                "first_version_id, last_version_id) values (%s, %s, %s, '11000', %s, %s, %s)",
                (kod, units[OBEC], OBEC, DAY, version.id, version.id))
    return version, units


def _current(conn, code: int) -> list[tuple]:
    with conn.cursor() as cur:
        cur.execute(
            "select valid_from, floors, typ_so, house_numbers, protection_codes, cast_obce_unit_id, "
            "definition_point, valid_to from ruian_building_objects "
            "where code = %s order by valid_from", (code,))
        return cur.fetchall()


def _link(conn, kod: int) -> int | None:
    with conn.cursor() as cur:
        cur.execute("select stavebni_objekt_code from ruian_address_points where kod_adm = %s", (kod,))
        return cur.fetchone()[0]


def _load(conn, world, rows, obec_kod=OBEC, sha="a"):
    version, units = world
    with conn.cursor() as cur:
        cur.execute("delete from ruian_building_loads where version_id = %s", (version.id,))
    return rb.write_obec(conn, version, units[OBEC], obec_kod, rows, sha)


def test_a_building_lands_with_what_the_register_states(conn, world):
    assert _load(conn, world, [BUILDING, ruian_vfr.AddressLink(ADM, BUILDING.code)]) == (1, 1)
    assert _current(conn, BUILDING.code) == [
        (BUILDING.plati_od, 8, "č.p.", [727, 728], [16, 18], world[1][CAST], None, None)]
    assert (_link(conn, ADM), _link(conn, OTHER_ADM)) == (BUILDING.code, None)


def test_a_changed_building_gets_a_new_row_and_the_old_one_is_closed(conn, world):
    _load(conn, world, [BUILDING])
    changed = dataclasses.replace(BUILDING, plati_od=datetime.date(2026, 10, 1), floors=9)
    _load(conn, world, [changed])
    rows = _current(conn, BUILDING.code)
    assert [(r[0], r[1], r[7]) for r in rows] == [
        (BUILDING.plati_od, 8, changed.plati_od), (changed.plati_od, 9, None)]


def test_a_building_gone_from_the_file_is_closed_and_its_link_cleared(conn, world):
    _load(conn, world, [BUILDING, ruian_vfr.AddressLink(ADM, BUILDING.code)])
    _load(conn, world, [])
    assert _current(conn, BUILDING.code)[0][7] == DAY
    assert _link(conn, ADM) is None


def test_reading_the_same_file_again_changes_nothing(conn, world):
    rows = [BUILDING, ruian_vfr.AddressLink(ADM, BUILDING.code)]
    _load(conn, world, rows)
    before = _current(conn, BUILDING.code)
    _load(conn, world, rows)
    assert _current(conn, BUILDING.code) == before


def test_an_obec_already_read_is_not_pending(conn, world):
    version, _ = world
    assert OBEC in [code for _, code in rb.pending_obce(conn, version)]
    rb.write_obec(conn, version, world[1][OBEC], OBEC, [], "a")
    assert OBEC not in [code for _, code in rb.pending_obce(conn, version)]


def test_the_completion_period_reads_a_placeholder_as_a_period(conn):
    with conn.cursor() as cur:
        cur.execute(
            "select ruian_completion_period('1945-12-31')::text, ruian_completion_period('1919-12-31')::text, "
            "ruian_completion_period('2012-06-30')::text, ruian_completion_period('2012-12-31')::text, "
            "ruian_completion_period(null)")
        assert cur.fetchone() == ("[1920,1946)", "(,1920)", "[2012,2013)", "[2012,2013)", None)
