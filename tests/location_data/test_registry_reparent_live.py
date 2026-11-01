"""W7's first production SCD-2 close, against a real schema: Praha re-parents under okres 9999.

A mirror loaded in the old shape (obec under its kraj, no okres) is staged again by the W7 loader
with the okres the pack publishes. The whole subtree closes and reopens, the streets follow, the
address points update in place, and the readers still answer once per unit.

Gated on TEST_DATABASE_URL: runs in the schema-replay job (.github/workflows/migrations.yml), skips in
the offline suite. Everything rolls back.
"""

from __future__ import annotations

import datetime
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

_DB_URL = os.environ.get("TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not _DB_URL,
    reason="TEST_DATABASE_URL not set — the registry re-parent live checks run only in CI's DB job",
)

from autodedup.incremental_sql import RT_SCOPE_OKRES_OBCE_SQL  # noqa: E402
from location_data import name_index, ruian_load  # noqa: E402
from location_data.resolver import resolve_db  # noqa: E402

V1_DATE = datetime.date(2026, 9, 30)
V2_DATE = datetime.date(2026, 10, 31)

STAT, REGION, KRAJ, OTHER_KRAJ = 9_910_001, 9_910_002, 9_910_019, 9_910_027
PRAHA_OKRES, OTHER_OKRES = 9999, 9_910_205
PRAHA, TOWN = 9_910_782, 9_910_011
PART, MOMC, KU = 9_910_067, 9_910_178, 9_910_664
TOWN_PART, TOWN_KU = 9_910_901, 9_910_902
STREET, TOWN_STREET = 9_910_536, 9_910_456
DOOR, TOWN_DOOR = 9_910_278, 9_910_279
RETIRED = (("spravni_obvod", 9_910_051), ("zsj", 9_910_052), ("orp", 9_910_053), ("pou", 9_910_054))


@pytest.fixture
def conn():
    import psycopg

    with psycopg.connect(_DB_URL) as c:
        try:
            yield c
        finally:
            c.rollback()


def _version(conn: Any, n: int, source_date: datetime.date) -> int:
    return int(conn.execute(
        "INSERT INTO registry_versions (label, kind, source_date, artifact_urls, proj_version, "
        "proj_pipeline) VALUES (%s, 'baseline', %s, '{}', 'p', 'q') RETURNING id",
        (f"test:reparent:{n}", source_date),
    ).fetchone()[0])


def _stage_sources(conn: Any, version: int) -> ruian_load.Staging:
    """The CSV products of one vintage: Praha with no okres, as the chain has it since 2022."""
    stage = ruian_load.Staging.for_version(version)
    ruian_load.create_staging(conn, stage)
    with conn.cursor() as cur:
        cur.executemany(
            f"INSERT INTO {stage.adr} (kod_adm, obec_kod, obec_nazev, momc_kod, momc_nazev, "
            "cobce_kod, cobce_nazev, ulice_kod, ulice_nazev, typ_so, cislo_domovni, psc, "
            "krovak_y, krovak_x, plati_od, lat, lon) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, "
            "'č.p.', 1, '16000', 744384.54, 1042569.73, '2020-01-01', 50.08, 14.39)",
            [(DOOR, PRAHA, "Praha", MOMC, "Praha 6", PART, "Vokovice", STREET, "Evropská"),
             (TOWN_DOOR, TOWN, "Kolín", None, None, TOWN_PART, "Kolín I", TOWN_STREET, "Husova")],
        )
        cur.executemany(
            f"INSERT INTO {stage.cobce} (cobce_kod, obec_kod, okres_kod, vusc_kod, regsoudr_kod, "
            "stat_kod) VALUES (%s, %s, %s, %s, %s, %s)",
            [(PART, PRAHA, None, KRAJ, REGION, STAT),
             (TOWN_PART, TOWN, OTHER_OKRES, OTHER_KRAJ, REGION, STAT)],
        )
        cur.executemany(f"INSERT INTO {stage.katuz} (katuz_kod, obec_kod) VALUES (%s, %s)",
                        [(KU, PRAHA), (TOWN_KU, TOWN)])
        cur.execute(f"INSERT INTO {stage.momc} (momc_kod, obec_kod) VALUES (%s, %s)", (MOMC, PRAHA))
        cur.executemany(f"INSERT INTO {stage.ulice} (ulice_kod, obec_kod) VALUES (%s, %s)",
                        [(STREET, PRAHA), (TOWN_STREET, TOWN)])
    return stage


def _old_shape_units(conn: Any, stage: ruian_load.Staging) -> None:
    """The unit rows the pre-W7 loader staged: Praha hanging off its kraj, its KÚ placeholder."""
    rows = [
        ("stat", STAT, "1", None, None), ("region_soudrznosti", REGION, "2", "stat", STAT),
        ("kraj", KRAJ, "19", "region_soudrznosti", REGION),
        ("kraj", OTHER_KRAJ, "27", "region_soudrznosti", REGION),
        ("okres", OTHER_OKRES, "205", "kraj", OTHER_KRAJ),
        ("obec", PRAHA, "Praha", "kraj", KRAJ), ("obec", TOWN, "Kolín", "okres", OTHER_OKRES),
        ("momc", MOMC, "Praha 6", "obec", PRAHA), ("cast_obce", PART, "Vokovice", "obec", PRAHA),
        ("cast_obce", TOWN_PART, "Kolín I", "obec", TOWN),
        ("katastralni_uzemi", KU, str(KU), "obec", PRAHA),
        ("katastralni_uzemi", TOWN_KU, str(TOWN_KU), "obec", TOWN),
    ]
    with conn.cursor() as cur:
        cur.executemany(
            f"INSERT INTO {stage.units} (level, code, name, name_norm, parent_level, parent_code, "
            "is_placeholder) VALUES (%s, %s, %s, %s, %s, %s, %s)",
            [(lvl, code, name, name_index.normalize_name(name), plvl, pcode, name == str(code))
             for lvl, code, name, plvl, pcode in rows],
        )


def _unit(conn: Any, level: str, code: int) -> list[tuple]:
    """Every row of a unit, oldest first: (id, parent_id, path, name, valid_to, last_version_id)."""
    return conn.execute(
        "SELECT id, parent_id, path::text, name, valid_to, last_version_id FROM ruian_admin_units "
        "WHERE level = %s::ruian_level AND code = %s ORDER BY id", (level, code),
    ).fetchall()


@dataclass
class _Mirror:
    v1: int
    v2: int
    before: dict[str, list[tuple]]


def _load_w7_over_old_shape(conn: Any) -> _Mirror:
    v1 = _version(conn, 1, V1_DATE)
    stage = _stage_sources(conn, v1)
    _old_shape_units(conn, stage)
    ruian_load.upsert_units(conn, stage, v1, V1_DATE)
    ruian_load.upsert_streets(conn, stage, v1, V1_DATE)
    ruian_load.load_address_points(conn, stage, v1, V1_DATE)
    conn.execute("UPDATE ruian_admin_units SET name = 'Vokovice', name_norm = 'vokovice' "
                 "WHERE level = 'katastralni_uzemi' AND code = %s", (KU,))
    for level, code in RETIRED:  # as migration 718 leaves them, closed before the W7 load
        conn.execute(
            "INSERT INTO ruian_admin_units (level, code, name, name_norm, path, valid_from, "
            "valid_to, retired_at, first_version_id, last_version_id) "
            "VALUES (%s::ruian_level, %s, %s, %s, %s::ltree, %s, %s, now(), %s, %s)",
            (level, code, str(code), str(code), f"x{code}", V1_DATE, V2_DATE, v1, v1),
        )
    before = {"praha": _unit(conn, "obec", PRAHA), "town": _unit(conn, "obec", TOWN),
              "part": _unit(conn, "cast_obce", PART), "town_part": _unit(conn, "cast_obce", TOWN_PART)}

    v2 = _version(conn, 2, V2_DATE)
    stage2 = _stage_sources(conn, v2)
    pack = {PRAHA: PRAHA_OKRES, TOWN: OTHER_OKRES}
    assert ruian_load.staged_obec_okres(conn, stage2) == {PRAHA: None, TOWN: OTHER_OKRES}
    ruian_load.build_unit_rows(conn, stage2, pack)
    ruian_load.upsert_units(conn, stage2, v2, V2_DATE)
    ruian_load.upsert_relations(conn, stage2, v2)
    ruian_load.upsert_streets(conn, stage2, v2, V2_DATE)
    ruian_load.load_address_points(conn, stage2, v2, V2_DATE)
    return _Mirror(v1=v1, v2=v2, before=before)


def test_build_unit_rows_stages_no_retired_level_and_praha_under_the_packs_okres(conn):
    version = _version(conn, 1, V1_DATE)
    stage = _stage_sources(conn, version)
    ruian_load.build_unit_rows(conn, stage, {PRAHA: PRAHA_OKRES, TOWN: OTHER_OKRES})
    rows = {(lvl, code): (plvl, pcode) for lvl, code, plvl, pcode in conn.execute(
        f"SELECT level, code, parent_level, parent_code FROM {stage.units}").fetchall()}
    assert {lvl for lvl, _ in rows} == set(ruian_load.LEVEL_ORDER)
    assert rows[("okres", PRAHA_OKRES)] == ("kraj", KRAJ)
    assert rows[("obec", PRAHA)] == ("okres", PRAHA_OKRES)
    assert rows[("momc", MOMC)] == ("obec", PRAHA)
    assert rows[("katastralni_uzemi", KU)] == ("obec", PRAHA)


def test_okres_9999_opens_and_praha_closes_and_reopens_under_it(conn):
    m = _load_w7_over_old_shape(conn)
    [okres] = _unit(conn, "okres", PRAHA_OKRES)
    assert okres[2].endswith(f"o{PRAHA_OKRES}") and okres[3] == str(PRAHA_OKRES) and okres[4] is None

    old, new = _unit(conn, "obec", PRAHA)
    assert old[0] == m.before["praha"][0][0]
    assert (old[4], old[5]) == (V2_DATE, m.v2)
    assert new[1] == okres[0] and new[4] is None and new[5] == m.v2
    assert new[2] == f"{okres[2]}.b{PRAHA}"


def test_the_whole_subtree_closes_and_reopens_and_keeps_its_names(conn):
    m = _load_w7_over_old_shape(conn)
    _, praha = _unit(conn, "obec", PRAHA)
    for level, code, name in (("cast_obce", PART, "Vokovice"), ("momc", MOMC, "Praha 6"),
                              ("katastralni_uzemi", KU, "Vokovice")):
        old, new = _unit(conn, level, code)
        assert (old[4], old[5]) == (V2_DATE, m.v2)
        assert new[1] == praha[0] and new[4] is None and new[3] == name
    assert _unit(conn, "cast_obce", PART)[1][2] == f"{praha[2]}.c{PART}"


def test_an_untouched_town_is_not_closed(conn):
    m = _load_w7_over_old_shape(conn)
    for key, level, code in (("town", "obec", TOWN), ("town_part", "cast_obce", TOWN_PART)):
        [row] = _unit(conn, level, code)
        assert row[0] == m.before[key][0][0] and row[4] is None and row[5] == m.v2
    [street] = conn.execute("SELECT valid_to FROM ruian_streets WHERE code = %s",
                            (TOWN_STREET,)).fetchall()
    assert street == (None,)


def test_a_retired_level_stays_retired(conn):
    m = _load_w7_over_old_shape(conn)
    for level, code in RETIRED:
        [row] = _unit(conn, level, code)
        assert row[4] == V2_DATE and row[5] == m.v1


def test_the_streets_close_and_reopen_on_the_new_town(conn):
    _load_w7_over_old_shape(conn)
    _, praha = _unit(conn, "obec", PRAHA)
    old, new = conn.execute(
        "SELECT obec_unit_id, valid_to FROM ruian_streets WHERE code = %s ORDER BY id", (STREET,),
    ).fetchall()
    assert old[1] == V2_DATE
    assert new == (praha[0], None)


def test_the_address_point_updates_in_place_with_one_change_row(conn):
    m = _load_w7_over_old_shape(conn)
    _, praha = _unit(conn, "obec", PRAHA)
    _, part = _unit(conn, "cast_obce", PART)
    street = conn.execute("SELECT id FROM ruian_streets WHERE code = %s AND valid_to IS NULL",
                          (STREET,)).fetchone()[0]
    assert conn.execute(
        "SELECT obec_unit_id, cast_obce_unit_id, street_id, last_version_id, valid_to "
        "FROM ruian_address_points WHERE kod_adm = %s", (DOOR,),
    ).fetchone() == (praha[0], part[0], street, m.v2, None)
    [(kind, changed)] = conn.execute(
        "SELECT change_kind, changed_fields FROM ruian_address_point_changes "
        "WHERE kod_adm = %s AND registry_version_id = %s", (DOOR, m.v2),
    ).fetchall()
    assert kind == "update"
    assert {"obec_unit_id", "cast_obce_unit_id", "street_id"} <= set(changed)
    assert conn.execute(
        "SELECT count(*) FROM ruian_address_point_changes WHERE kod_adm = %s "
        "AND registry_version_id = %s", (TOWN_DOOR, m.v2),
    ).fetchone() == (0,)


def test_the_readers_answer_once_after_the_first_close(conn):
    m = _load_w7_over_old_shape(conn)
    # What a boundary phase does for a placeholder: okres 9999 takes ČÚZK's name.
    okres_name = "území Hlavního města Prahy"
    conn.execute("UPDATE ruian_admin_units SET name = %s, name_norm = %s "
                 "WHERE level = 'okres' AND code = %s",
                 (okres_name, name_index.normalize_name(okres_name), PRAHA_OKRES))
    name_index.rebuild(conn, m.v2)
    view = resolve_db.SqlRegistryView(conn, m.v2)

    _, praha = _unit(conn, "obec", PRAHA)
    [town] = view.admin_units_by_name("praha", levels=("obec",))
    assert town.unit_id == praha[0] and town.okres_kod == PRAHA_OKRES

    chain = view.admin_chain_by_code("obec", PRAHA)
    assert [(u.level, u.code) for u in chain][:2] == [("obec", PRAHA), ("okres", PRAHA_OKRES)]

    assert [tuple(r) for r in conn.execute(
        RT_SCOPE_OKRES_OBCE_SQL, {"codes": [PRAHA_OKRES]})] == [(PRAHA_OKRES, PRAHA)]

    assert conn.execute(
        "SELECT name FROM ruian_place_names_public WHERE level = 'okres' AND code = %s",
        (PRAHA_OKRES,),
    ).fetchall() == [(okres_name,)]
    assert conn.execute(
        "SELECT count(*) FROM ruian_place_names_public WHERE level = 'obec' AND code = %s",
        (PRAHA,),
    ).fetchone() == (1,)


def test_the_migration_retires_by_scd2_and_deletes_only_the_unread_relations(conn):
    """The statements as shipped, over rows the replay never saw: 718 is idempotent DDL."""
    version = _version(conn, 1, V1_DATE)
    units = {}
    for level, code in (*RETIRED, ("obec", PRAHA), ("katastralni_uzemi", KU)):
        units[level] = conn.execute(
            "INSERT INTO ruian_admin_units (level, code, name, name_norm, path, valid_from, "
            "first_version_id, last_version_id) VALUES (%s::ruian_level, %s, %s, %s, %s::ltree, "
            "%s, %s, %s) RETURNING id",
            (level, code, str(code), str(code), f"x{code}", V1_DATE, version, version),
        ).fetchone()[0]
    for relation, a, b in (("orp_of", "obec", "orp"), ("pou_of", "obec", "pou"),
                           ("momc_in_so", "obec", "spravni_obvod"),
                           ("katastr_in_obec", "katastralni_uzemi", "obec")):
        conn.execute("INSERT INTO ruian_admin_unit_relations (from_id, to_id, relation_type, "
                     "registry_version_id) VALUES (%s, %s, %s, %s)",
                     (units[a], units[b], relation, version))

    for level, _ in RETIRED:
        conn.execute("INSERT INTO ruian_name_index (entity_kind, entity_id, registry_version_id, "
                     "name, name_norm, name_kind) VALUES (%s::ruian_level, %s, %s, 'Praha 5', "
                     "'praha 5', 'official')", (level, units[level], version))
    view = resolve_db.SqlRegistryView(conn, version)
    assert len(view.admin_units_by_name("praha 5")) == len(RETIRED)

    sql = next(Path(__file__).resolve().parents[2].glob(
        "migrations/*_location_w7_registry_read_levels.sql")).read_text()
    body = sql.split("begin;", 1)[1].rsplit("commit;", 1)[0]
    conn.execute(body)

    assert view.admin_units_by_name("praha 5") == []  # the old version's gazetteer rows stay
    for level, _ in RETIRED:
        row = conn.execute("SELECT valid_to IS NOT NULL, retired_at IS NOT NULL, last_version_id "
                           "FROM ruian_admin_units WHERE id = %s", (units[level],)).fetchone()
        assert row == (True, True, version), level
    assert conn.execute("SELECT valid_to FROM ruian_admin_units WHERE id = %s",
                        (units["obec"],)).fetchone() == (None,)
    assert conn.execute(
        "SELECT relation_type FROM ruian_admin_unit_relations WHERE from_id = ANY(%s)",
        ([units["obec"], units["katastralni_uzemi"]],),
    ).fetchall() == [("katastr_in_obec",)]


def test_the_dropped_columns_are_gone_and_their_index_with_them(conn):
    assert conn.execute(
        "SELECT count(*) FROM information_schema.columns WHERE table_name = ANY(%s) "
        "AND column_name = ANY(%s)",
        (["ruian_admin_units", "ruian_address_points"],
         ["nuts_lau", "definition_point", "has_polygon", "display_path", "praha_obvod_unit_id",
          "momc_unit_id"]),
    ).fetchone() == (0,)
    assert conn.execute("SELECT to_regclass('ruian_admin_units_defpoint')").fetchone() == (None,)
