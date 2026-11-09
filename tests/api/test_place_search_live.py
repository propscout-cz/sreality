"""Own place search (#2449; spec #2441 Part 4, Q2 c, Q6 a, Q9, Q12 a), EXECUTED against the replayed schema (CI's
migrations lane, `TEST_DATABASE_URL`; locally `scripts/local_pg.sh test`): `POST /places/suggest` over a mini RÚIAN
mirror seeded in the transaction, its gazetteer built by the loader's own `name_index.rebuild`, both read models
refreshed through `refresh_matview` before the first search. Nothing calls Mapy. Every test rolls back.

The mirror is RÚIAN-only, so no foreign place can exist in it: a foreign row has nowhere to come from.
"""

from __future__ import annotations

import contextlib
import itertools
import uuid
from collections.abc import Iterator
from typing import Any

import psycopg
import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from api import dependencies as deps  # noqa: E402
from api import main as api_main  # noqa: E402
from location_data import name_index  # noqa: E402
from location_data.name_index import normalize_name, normalize_street_name  # noqa: E402
from location_data.ruian_load import LABEL_PREFIX  # noqa: E402
from scraper import db  # noqa: E402
from tests._live_property import REQUIRED_DB, db_url  # noqa: E402
from tests.api._jwt import mint  # noqa: E402

pytestmark = REQUIRED_DB

SERVICE_ROLE_OPENER = deps.open_background_conn

JIHOMORAVSKY, BRNO_MESTO, VYSKOV_OKRES, BRNO, VYSKOV, BRNENSKA = 116, 3702, 3712, 582786, 592889, 437158
PRAHA_KRAJ, PRAHA_OKRES, PRAHA, ZIZKOV_PRAHA = 19, 3100, 554782, 490261
STREDOCESKY, KUTNA_HORA_OKRES, KUTNA_HORA, ZIZKOV_KH = 27, 3205, 533955, 77747


class _Mirror:
    """A current registry version (and an older one), units under real ltree paths, streets, doors, served properties
    and population; `publish()` runs the loader's gazetteer step and refreshes both read models."""

    def __init__(self, conn: psycopg.Connection) -> None:
        self.conn = conn
        self.old = self._version(current=False)
        self.v = self._version(current=True)
        self.paths: dict[int, str] = {}
        self.codes: dict[int, int] = {}
        self.properties = itertools.count(1)
        self.doors = itertools.count(31_000_001)

    def _version(self, *, current: bool) -> int:
        if current:
            self.conn.execute("UPDATE registry_versions SET is_current = false WHERE is_current")
        return int(self.conn.execute(
            "INSERT INTO registry_versions (label, kind, source_date, artifact_urls, proj_version, proj_pipeline,"
            " is_current) VALUES (%s, 'baseline', '2026-11-01', '{}', 'p', 'q', %s) RETURNING id",
            (f"test:place-search:{uuid.uuid4().hex}", current)).fetchone()[0])

    def next_version(self) -> int:
        """A later version a load is writing, not yet current."""
        return self._version(current=False)

    def unit(self, level: str, code: int, name: str, parent: int | None = None, *,
             point: tuple[float, float] | None = None, version: int | None = None,
             valid_from: str = "2020-01-01", valid_to: str | None = None) -> int:
        """Drawn in `version` alone (the current one by default); `valid_to` closes it."""
        version = self.v if version is None else version
        path = ".".join(p for p in (self.paths.get(parent) if parent else None, f"{LABEL_PREFIX[level]}{code}") if p)
        unit = int(self.conn.execute(
            "INSERT INTO ruian_admin_units (level, code, name, name_norm, parent_id, path, valid_from, valid_to,"
            " first_version_id, last_version_id) VALUES (%s::ruian_level, %s, %s, %s, %s, %s::ltree, %s,"
            " %s, %s, %s) RETURNING id",
            (level, code, name, normalize_name(name), parent, path, valid_from, valid_to, version,
             version)).fetchone()[0])
        self.paths[unit], self.codes[unit] = path, code
        if point is not None:
            lng, lat = point
            self.conn.execute(
                "INSERT INTO ruian_admin_unit_geometries (unit_id, registry_version_id, purpose,"
                " generalization_tolerance_m, geom, representative_point, inscribed_radius_m, centroid_point,"
                " containment_radius_m) SELECT %s, %s, 'authoritative', 0, ST_Multi(ST_Expand(p, 0.01)), p, 0, p, 0"
                "   FROM (SELECT ST_SetSRID(ST_MakePoint(%s, %s), 4326) AS p) s", (unit, version, lng, lat))
        return unit

    def touch(self, unit: int, version: int) -> None:
        """The unit survives into `version`, as the load's unit upsert carries it."""
        self.conn.execute("UPDATE ruian_admin_units SET last_version_id = %s WHERE id = %s", (version, unit))

    def close(self, unit: int, version: int) -> None:
        """`version` closes the unit, as the load's unit upsert does before it reopens a changed one."""
        self.conn.execute("UPDATE ruian_admin_units SET valid_to = '2026-11-30', last_version_id = %s WHERE id = %s",
                          (version, unit))

    def index_at(self, unit: int, level: str, name: str, version: int) -> None:
        """The unit's gazetteer row at `version`, as that version's load wrote it."""
        self.conn.execute(
            "INSERT INTO ruian_name_index (entity_kind, entity_id, registry_version_id, name, name_norm, name_kind)"
            " VALUES (%s::ruian_level, %s, %s, %s, %s, 'official')", (level, unit, version, name, normalize_name(name)))

    def street(self, code: int, name: str, obec: int, *, door: tuple[float, float] | None = None,
               version: int | None = None) -> int:
        """Open, drawn in `version` alone (the current one by default). Returns its row id."""
        version = self.v if version is None else version
        street = int(self.conn.execute(
            "INSERT INTO ruian_streets (code, name, name_norm, obec_unit_id, first_version_id, last_version_id,"
            " valid_from) VALUES (%s, %s, %s, %s, %s, %s, '2020-01-01') RETURNING id",
            (code, name, normalize_street_name(name), obec, version, version)).fetchone()[0])
        if door is not None:
            self.conn.execute(
                "INSERT INTO ruian_address_points (kod_adm, obec_unit_id, obec_kod, ulice_kod, psc, geom, plati_od,"
                " first_version_id, last_version_id) VALUES (%s, %s, %s, %s, '68201',"
                " ST_SetSRID(ST_MakePoint(%s, %s), 4326), '2020-01-01', %s, %s)",
                (next(self.doors), obec, self.codes[obec], code, *door, version, version))
        return street

    def house(self, kod: int, obec: int, *, cp: int, street: int | None = None, part: int | None = None,
              typ: str = "č.p.", co: int | None = None, znak: str | None = None,
              point: tuple[float, float] | None = None, version: int | None = None) -> None:
        """A numbered door on `street` (a street ROW, as the load points a door) or, streetless, in `part`; born in
        `version` (the current one by default)."""
        version = self.v if version is None else version
        lng, lat = point if point is not None else (None, None)
        self.conn.execute(
            "INSERT INTO ruian_address_points (kod_adm, obec_unit_id, obec_kod, street_id, ulice_kod,"
            " cast_obce_unit_id, cast_obce_kod, typ_so, cislo_domovni, cislo_orientacni, znak_orientacniho, psc,"
            " geom, plati_od, first_version_id, last_version_id)"
            " VALUES (%s, %s, %s, %s, (SELECT code FROM ruian_streets WHERE id = %s), %s, %s, %s, %s, %s, %s, '28002',"
            " ST_SetSRID(ST_MakePoint(%s::float8, %s::float8), 4326), '2020-01-01', %s, %s)",
            (kod, obec, self.codes[obec], street, street, part, self.codes[part] if part else None, typ, cp, co,
             znak, lng, lat, version, version))

    def quarters(self, momc: int, *parts: int, version: int | None = None) -> None:
        """A městská část's quarter pairs at `version` (the current one by default), as the load's finish step writes
        them: unit rows, not codes."""
        version = self.v if version is None else version
        for part in parts:
            self.conn.execute(
                "INSERT INTO ruian_admin_unit_relations (from_id, to_id, relation_type, registry_version_id)"
                " VALUES (%s, %s, 'momc_cast_obce', %s)", (momc, part, version))

    def door(self, obec: int, *, momc: int, part: int, point: tuple[float, float]) -> None:
        """An open door with no street, in `part` of `momc`."""
        self.conn.execute(
            "INSERT INTO ruian_address_points (kod_adm, obec_unit_id, obec_kod, momc_unit_id, cast_obce_unit_id,"
            " cast_obce_kod, psc, geom, plati_od, first_version_id, last_version_id) VALUES (%s, %s, %s, %s, %s, %s,"
            " '16000', ST_SetSRID(ST_MakePoint(%s, %s), 4326), '2020-01-01', %s, %s)",
            (next(self.doors), obec, self.codes[obec], momc, part, self.codes[part], *point, self.v, self.v))

    def served(self, *, region: int | None = None, okres: int | None = None, obec: int | None = None,
               part: int | None = None, street: int | None = None, active: bool = True,
               deal: str = "prodej") -> None:
        """One property as Browse serves it: a browse_list row naming its places by RÚIAN code."""
        self.conn.execute(
            "INSERT INTO browse_list (property_id, is_active, category_type, region_id, okres_id, obec_id,"
            " cast_obce_id, ulice_id) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (next(self.properties), active, deal, region, okres, obec, part, street))

    def population(self, obec_code: int, people: int, *, year: int = 2025) -> None:
        self.conn.execute("INSERT INTO obec_population (obec_kod, year, population) VALUES (%s, %s, %s)",
                          (obec_code, year, people))

    def publish(self) -> None:
        name_index.rebuild(self.conn, self.v)
        db.refresh_matview(self.conn, "place_facts")
        db.refresh_matview(self.conn, "place_served_counts")


@pytest.fixture()
def conn() -> Iterator[psycopg.Connection]:
    connection = psycopg.connect(db_url(), autocommit=True, options="-c statement_timeout=20000")
    with connection, connection.transaction(force_rollback=True):
        yield connection


@pytest.fixture()
def mirror(conn: psycopg.Connection) -> _Mirror:
    return _Mirror(conn)


@pytest.fixture()
def client(conn: psycopg.Connection, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """The real route on the test connection, a verified caller."""
    @contextlib.contextmanager
    def service() -> Iterator[psycopg.Connection]:
        yield conn

    monkeypatch.delenv("API_AUTH_OPTIONAL", raising=False)
    monkeypatch.setattr(deps, "open_background_conn", service)
    return TestClient(api_main.app, headers={"Authorization": f"Bearer {mint({'sub': str(uuid.uuid4())})}"})


def _suggest(client: TestClient, query: str) -> list[dict[str, Any]]:
    res = client.post("/places/suggest", json={"query": query})
    assert res.status_code == 200, res.text
    assert res.json().keys() == {"items"}
    return res.json()["items"]


def _chips(items: list[dict[str, Any]]) -> list[tuple[str, int, str, str | None]]:
    return [(i["level"], i["id"], i["name"], i["context"]) for i in items]


def _praha(m: _Mirror) -> int:
    """The town Praha under its okres and kraj. Returns its unit id."""
    return m.unit("obec", PRAHA, "Praha", m.unit("okres", PRAHA_OKRES, "území Hlavního města Prahy",
                                                 m.unit("kraj", PRAHA_KRAJ, "Hlavní město Praha")))


def _zizkov(m: _Mirror) -> dict[str, int]:
    """Two quarters named Žižkov: one in Praha, one in Kutná Hora. Returns their towns' unit ids."""
    praha = _praha(m)
    kutna_hora = m.unit("obec", KUTNA_HORA, "Kutná Hora", m.unit("okres", KUTNA_HORA_OKRES, "Kutná Hora",
                                                               m.unit("kraj", STREDOCESKY, "Středočeský kraj")))
    m.unit("cast_obce", ZIZKOV_PRAHA, "Žižkov", praha)
    m.unit("cast_obce", ZIZKOV_KH, "Žižkov", kutna_hora)
    return {"praha": praha, "kutna_hora": kutna_hora}


def _jihomoravsky(m: _Mirror) -> dict[str, int]:
    """Brno in okres Brno-město; the street Brněnská in Vyškov, whose okres starts with no 'brno'. Returns unit ids."""
    kraj = m.unit("kraj", JIHOMORAVSKY, "Jihomoravský kraj")
    brno_mesto = m.unit("okres", BRNO_MESTO, "Brno-město", kraj)
    brno = m.unit("obec", BRNO, "Brno", brno_mesto, point=(16.6068, 49.1951))
    vyskov_okres = m.unit("okres", VYSKOV_OKRES, "Vyškov", kraj)
    vyskov = m.unit("obec", VYSKOV, "Vyškov", vyskov_okres)
    m.street(BRNENSKA, "Brněnská", vyskov, door=(16.9978, 49.2786))
    return {"kraj": kraj, "brno_mesto": brno_mesto, "brno": brno, "vyskov_okres": vyskov_okres, "vyskov": vyskov}


def test_brno_answers_the_town_first_and_the_street_brnenska_later_as_chips_with_no_count(mirror, client):
    """Exact, then prefix, then fuzzy: the town Brno, its okres Brno-město, then Brněnská (word similarity 0.6)."""
    _jihomoravsky(mirror)
    mirror.publish()
    assert _suggest(client, "Brno") == [
        {"level": "obec", "id": BRNO, "name": "Brno", "context": "Brno-město",
         "point": {"lat": 49.1951, "lng": 16.6068}},
        {"level": "okres", "id": BRNO_MESTO, "name": "Brno-město", "context": "Jihomoravský kraj", "point": None},
        {"level": "ulice", "id": BRNENSKA, "name": "Brněnská", "context": "Vyškov",
         "point": {"lat": 49.2786, "lng": 16.9978}},
    ]


def test_zizkov_finds_both_quarters_named_zizkov_each_with_its_town_and_a_tie_breaks_by_code(mirror, client):
    """Typed without its accents; neither quarter has a served property (story 46: still findable), so the lower
    code answers first."""
    _zizkov(mirror)
    mirror.publish()
    assert _chips(_suggest(client, "zizkov")) == [
        ("cast_obce", ZIZKOV_KH, "Žižkov", "Kutná Hora"),
        ("cast_obce", ZIZKOV_PRAHA, "Žižkov", "Praha"),
    ]


def test_a_cadastral_area_a_cohesion_region_and_the_country_are_never_answered(mirror, client):
    """Q6 a, Q9. A městská část answers, as a shortcut (#2450): the shortcut tests below."""
    towns = _zizkov(mirror)
    mirror.unit("katastralni_uzemi", 727415, "Žižkov", towns["praha"])
    mirror.unit("region_soudrznosti", 11, "Praha")
    mirror.unit("stat", 1, "Česká republika")
    mirror.publish()
    assert _chips(_suggest(client, "zizkov")) == [
        ("cast_obce", ZIZKOV_KH, "Žižkov", "Kutná Hora"),
        ("cast_obce", ZIZKOV_PRAHA, "Žižkov", "Praha"),
    ]
    assert _suggest(client, "ceska") == []


PRAHA_1, PRAHA_6, PRAHA_7 = 500054, 500178, 500186
DEJVICE, VOKOVICE, BUBENEC, HRADCANY, JOSEFOV, HOLESOVICE = 400459, 400441, 490024, 490075, 127001, 490067


def test_praha_6_answers_one_shortcut_row_whose_places_are_its_quarters_chips(mirror, client):
    """#2380: a městská část is no served level, so its row is a shortcut whose places are its quarters' chips, the
    register's pairs, in name order; it flies to its doors. Then what the word "praha" reaches by similarity, the kraj
    and the town, the lower code first."""
    praha = _praha(mirror)
    praha_6 = mirror.unit("momc", PRAHA_6, "Praha 6", praha)
    dejvice = mirror.unit("cast_obce", DEJVICE, "Dejvice", praha)
    vokovice = mirror.unit("cast_obce", VOKOVICE, "Vokovice", praha)
    bubenec = mirror.unit("cast_obce", BUBENEC, "Bubeneč", praha)
    mirror.quarters(praha_6, dejvice, vokovice, bubenec)
    mirror.door(praha, momc=praha_6, part=dejvice, point=(14.3907, 50.1004))
    mirror.publish()
    assert _suggest(client, "Praha 6") == [
        {"level": "momc", "id": PRAHA_6, "name": "Praha 6", "context": "Praha",
         "point": {"lat": 50.1004, "lng": 14.3907},
         "places": [{"level": "cast_obce", "id": BUBENEC, "name": "Bubeneč", "context": "Praha"},
                    {"level": "cast_obce", "id": DEJVICE, "name": "Dejvice", "context": "Praha"},
                    {"level": "cast_obce", "id": VOKOVICE, "name": "Vokovice", "context": "Praha"}]},
        {"level": "kraj", "id": PRAHA_KRAJ, "name": "Hlavní město Praha", "context": None, "point": None},
        {"level": "obec", "id": PRAHA, "name": "Praha", "context": "území Hlavního města Prahy", "point": None},
    ]


def test_shortcuts_rank_by_the_sum_of_their_quarters_served_properties_a_shared_quarter_counting_for_each(
        mirror, client):
    """Hradčany is in Praha 1 and Praha 6 (#2380). Praha 6 serves 3 + 3 = 6 through Dejvice and Hradčany, Praha 1
    2 + 3 = 5 through Josefov and Hradčany, Praha 7 4 through Holešovice, the largest quarter. Without Hradčany, or
    by its largest quarter, Praha 7 would lead; by code, Praha 1. The town is exact, the shortcuts prefix; four rows
    fill the limit, so nothing fuzzy is read."""
    praha = _praha(mirror)
    praha_1 = mirror.unit("momc", PRAHA_1, "Praha 1", praha)
    praha_6 = mirror.unit("momc", PRAHA_6, "Praha 6", praha)
    praha_7 = mirror.unit("momc", PRAHA_7, "Praha 7", praha)
    hradcany = mirror.unit("cast_obce", HRADCANY, "Hradčany", praha)
    mirror.quarters(praha_1, mirror.unit("cast_obce", JOSEFOV, "Josefov", praha), hradcany)
    mirror.quarters(praha_6, mirror.unit("cast_obce", DEJVICE, "Dejvice", praha), hradcany)
    mirror.quarters(praha_7, mirror.unit("cast_obce", HOLESOVICE, "Holešovice", praha))
    for part, properties in ((JOSEFOV, 2), (DEJVICE, 3), (HRADCANY, 3), (HOLESOVICE, 4)):
        for _ in range(properties):
            mirror.served(obec=PRAHA, part=part)
    mirror.publish()
    res = client.post("/places/suggest", json={"query": "Praha", "limit": 4})
    assert res.status_code == 200, res.text
    assert res.json() == {"items": [
        {"level": "obec", "id": PRAHA, "name": "Praha", "context": "území Hlavního města Prahy", "point": None},
        {"level": "momc", "id": PRAHA_6, "name": "Praha 6", "context": "Praha", "point": None,
         "places": [{"level": "cast_obce", "id": DEJVICE, "name": "Dejvice", "context": "Praha"},
                    {"level": "cast_obce", "id": HRADCANY, "name": "Hradčany", "context": "Praha"}]},
        {"level": "momc", "id": PRAHA_1, "name": "Praha 1", "context": "Praha", "point": None,
         "places": [{"level": "cast_obce", "id": HRADCANY, "name": "Hradčany", "context": "Praha"},
                    {"level": "cast_obce", "id": JOSEFOV, "name": "Josefov", "context": "Praha"}]},
        {"level": "momc", "id": PRAHA_7, "name": "Praha 7", "context": "Praha", "point": None,
         "places": [{"level": "cast_obce", "id": HOLESOVICE, "name": "Holešovice", "context": "Praha"}]},
    ]}


def test_a_mestska_cast_with_no_pairs_at_the_index_s_version_answers_no_row_and_takes_no_row_s_place(mirror, client):
    """Between a load's pointer swap and its finish step the new version has no pairs yet, and an older version's do
    not stand in. Praha 6 answers nothing, and what "praha" reaches by similarity still fills the rows, even the one
    row a limit of one leaves."""
    praha = _praha(mirror)
    praha_6 = mirror.unit("momc", PRAHA_6, "Praha 6", praha)
    mirror.quarters(praha_6, mirror.unit("cast_obce", DEJVICE, "Dejvice", praha), version=mirror.old)
    mirror.publish()
    assert _chips(_suggest(client, "Praha 6")) == [
        ("kraj", PRAHA_KRAJ, "Hlavní město Praha", None),
        ("obec", PRAHA, "Praha", "území Hlavního města Prahy"),
    ]
    res = client.post("/places/suggest", json={"query": "Praha 6", "limit": 1})
    assert res.status_code == 200, res.text
    assert _chips(res.json()["items"]) == [("kraj", PRAHA_KRAJ, "Hlavní město Praha", None)]


def test_a_shortcut_reads_its_quarters_at_the_index_s_version_while_a_load_writes_the_next(mirror, client):
    """Ported from the re-parent tests: a load writes the next version for hours before its pointer swap. Praha 6
    and Vokovice survive into it on their rows, Dejvice closes and reopens renamed, Bubeneč is born, the next version's
    gazetteer is built and its pairs hang off the same Praha 6 row. The shortcut answers the index's version: Dejvice
    by the name it has there, Vokovice, each once, and no Bubeneč."""
    praha = _praha(mirror)
    praha_6 = mirror.unit("momc", PRAHA_6, "Praha 6", praha)
    dejvice = mirror.unit("cast_obce", DEJVICE, "Dejvice", praha)
    vokovice = mirror.unit("cast_obce", VOKOVICE, "Vokovice", praha)
    mirror.quarters(praha_6, dejvice, vokovice)
    mirror.publish()

    nxt = mirror.next_version()
    mirror.touch(praha_6, nxt)
    mirror.touch(vokovice, nxt)
    mirror.close(dejvice, nxt)
    new_dejvice = mirror.unit("cast_obce", DEJVICE, "Dejvice-Sever", praha, version=nxt, valid_from="2026-11-30")
    bubenec = mirror.unit("cast_obce", BUBENEC, "Bubeneč", praha, version=nxt, valid_from="2026-11-30")
    mirror.quarters(praha_6, new_dejvice, vokovice, bubenec, version=nxt)
    name_index.rebuild(mirror.conn, nxt)

    assert [i for i in _suggest(client, "Praha 6") if i["level"] == "momc"] == [
        {"level": "momc", "id": PRAHA_6, "name": "Praha 6", "context": "Praha", "point": None,
         "places": [{"level": "cast_obce", "id": DEJVICE, "name": "Dejvice", "context": "Praha"},
                    {"level": "cast_obce", "id": VOKOVICE, "name": "Vokovice", "context": "Praha"}]},
    ]


@pytest.mark.parametrize("query", ["110 00", "ulice .", "Starý Dvůr", "Zábrdí", "Zaniklá"],
                         ids=["a PSČ", "a bare street word", "a closed town", "an open town the latest vintage drops",
                              "an open street the latest vintage drops"])
def test_a_psc_a_bare_street_word_and_a_place_outside_the_current_version_answer_nothing(mirror, client, query):
    """Starý Dvůr was closed in the older version and named only by its index; Zábrdí and Zaniklá are still open but
    the current version no longer draws them (the gazetteer names every open unit, so Zábrdí is in it). 'ulice .' is
    the street word and punctuation alone: nothing of a name is left, so it must not reach every open street."""
    towns = _jihomoravsky(mirror)
    closed = mirror.unit("obec", 594172, "Starý Dvůr", towns["vyskov_okres"], version=mirror.old,
                         valid_to="2025-01-01")
    mirror.index_at(closed, "obec", "Starý Dvůr", mirror.old)
    mirror.unit("obec", 593338, "Zábrdí", towns["vyskov_okres"], version=mirror.old)
    mirror.street(437171, "Zaniklá", towns["vyskov"], version=mirror.old)
    mirror.publish()
    assert _suggest(client, query) == []


def test_brez_still_finds_breznice_by_the_start_of_its_name_typed_without_accents(mirror, client):
    okres = mirror.unit("okres", 3211, "Příbram", mirror.unit("kraj", STREDOCESKY, "Středočeský kraj"))
    mirror.unit("obec", 540153, "Březnice", okres)
    mirror.publish()
    assert _chips(_suggest(client, "brez")) == [("obec", 540153, "Březnice", "Příbram")]


def _lhoty(m: _Mirror) -> None:
    """Three towns named Lhota, each in its okres: one has a served property for rent, one has only inactive ones and
    the most people, one has the lowest code and fewer people now than in 2020."""
    stredocesky = m.unit("kraj", STREDOCESKY, "Středočeský kraj")
    m.unit("obec", 534986, "Lhota", m.unit("okres", 3209, "Praha-východ", stredocesky))
    m.unit("obec", 513041, "Lhota", m.unit("okres", 3203, "Kladno", stredocesky))
    m.unit("obec", 500101, "Lhota", m.unit("okres", 3808, "Přerov", m.unit("kraj", 124, "Olomoucký kraj")))
    m.served(region=STREDOCESKY, okres=3209, obec=534986, deal="pronajem")
    m.served(region=STREDOCESKY, okres=3203, obec=513041, active=False)
    m.served(region=STREDOCESKY, okres=3203, obec=513041, active=False)
    m.population(534986, 548)
    m.population(513041, 663)
    m.population(500101, 1000, year=2020)
    m.population(500101, 312)


def test_two_towns_called_lhota_answer_the_one_with_served_properties_first_each_with_its_okres(mirror, client):
    """Q12 a: served properties of any deal type, then the latest population, then code. The Lhota in Kladno has no
    served property (story 46: still findable) and comes before the Lhota in Přerov, whose code is lower."""
    _lhoty(mirror)
    mirror.publish()
    assert _chips(_suggest(client, "Lhota")) == [
        ("obec", 534986, "Lhota", "Praha-východ"),
        ("obec", 513041, "Lhota", "Kladno"),
        ("obec", 500101, "Lhota", "Přerov"),
    ]


def test_a_town_answers_before_its_quarter_of_the_same_name_when_both_serve_the_same_properties(mirror, client):
    """A quarter has no population, so on equal served properties the town goes first although its code is higher."""
    okres = mirror.unit("okres", 3203, "Kladno", mirror.unit("kraj", STREDOCESKY, "Středočeský kraj"))
    mirror.unit("cast_obce", 70971, "Lhota", mirror.unit("obec", 513041, "Lhota", okres))
    mirror.served(region=STREDOCESKY, okres=3203, obec=513041, part=70971)
    mirror.population(513041, 663)
    mirror.publish()
    assert _chips(_suggest(client, "Lhota")) == [
        ("obec", 513041, "Lhota", "Kladno"),
        ("cast_obce", 70971, "Lhota", "Lhota"),
    ]


def test_zizkov_answers_the_quarter_in_praha_first_once_it_serves_a_property(mirror, client):
    _zizkov(mirror)
    mirror.served(region=PRAHA_KRAJ, okres=PRAHA_OKRES, obec=PRAHA, part=ZIZKOV_PRAHA)
    mirror.publish()
    assert _chips(_suggest(client, "zizkov")) == [
        ("cast_obce", ZIZKOV_PRAHA, "Žižkov", "Praha"),
        ("cast_obce", ZIZKOV_KH, "Žižkov", "Kutná Hora"),
    ]


PLZEN_MESTO, PLZEN, BEROUN_OKRES, BEROUN, PLZENSKA = 3405, 554791, 3202, 531057, 437180


def test_the_exact_town_answers_before_a_prefix_street_that_serves_more_properties(mirror, client):
    """'plzen' starts 'plzenska': the town is exact, the street and the okres are prefix. The street serves two
    properties and the town one, yet the town answers first: the match tier ranks above served properties."""
    plzen_mesto = mirror.unit("okres", PLZEN_MESTO, "Plzeň-město")
    mirror.unit("obec", PLZEN, "Plzeň", plzen_mesto)
    beroun = mirror.unit("obec", BEROUN, "Beroun", mirror.unit("okres", BEROUN_OKRES, "Beroun"))
    mirror.street(PLZENSKA, "Plzeňská", beroun)
    mirror.served(okres=PLZEN_MESTO, obec=PLZEN)
    mirror.served(okres=BEROUN_OKRES, obec=BEROUN, street=PLZENSKA)
    mirror.served(okres=BEROUN_OKRES, obec=BEROUN, street=PLZENSKA)
    mirror.publish()
    assert _chips(_suggest(client, "Plzeň")) == [
        ("obec", PLZEN, "Plzeň", "Plzeň-město"),
        ("ulice", PLZENSKA, "Plzeňská", "Beroun"),
        ("okres", PLZEN_MESTO, "Plzeň-město", None),
    ]


def test_the_limit_bounds_the_rows(mirror, client):
    _lhoty(mirror)
    mirror.publish()
    res = client.post("/places/suggest", json={"query": "Lhota", "limit": 2})
    assert res.status_code == 200, res.text
    assert _chips(res.json()["items"]) == [
        ("obec", 534986, "Lhota", "Praha-východ"),
        ("obec", 513041, "Lhota", "Kladno"),
    ]


@pytest.mark.parametrize("body", [
    {"query": "Brno", "foo": 1},
    {"query": "Brno", "limit": 11},
    {"query": "Brno", "limit": 0},
    {"query": ""},
    {"query": "B" * 201},
], ids=["unknown field", "limit 11", "limit 0", "empty query", "201 characters"])
def test_a_malformed_body_is_a_422(client, body):
    assert client.post("/places/suggest", json=body).status_code == 422


def test_no_token_is_a_401_and_opens_no_connection(monkeypatch):
    opened: list[bool] = []

    @contextlib.contextmanager
    def service() -> Iterator[None]:
        opened.append(True)
        yield None

    monkeypatch.delenv("API_AUTH_OPTIONAL", raising=False)
    monkeypatch.setattr(deps, "open_background_conn", service)
    assert TestClient(api_main.app).post("/places/suggest", json={"query": "Brno"}).status_code == 401
    assert opened == []


UNAVAILABLE = {"code": "place_search_unavailable", "message": "Vyhledávání míst je teď nedostupné."}


def test_a_failed_read_is_a_503_and_no_log_line_carries_the_typed_text(mirror, client, conn, caplog):
    """The module logs the SQLSTATE alone: a psycopg message can quote the statement's parameters."""
    _jihomoravsky(mirror)
    mirror.publish()
    with caplog.at_level("INFO"):
        assert _suggest(client, "Brnoxq") == []
        conn.execute("ALTER MATERIALIZED VIEW place_facts RENAME TO place_facts_gone")
        res = client.post("/places/suggest", json={"query": "Brnoxq"})
    assert (res.status_code, res.json()) == (503, {"detail": UNAVAILABLE})
    lines = [r.getMessage() for r in caplog.records if r.name == "location_data.place_search"]
    assert lines == ["place_search: read failed (SQLSTATE 42P01)"]
    assert not any("brnoxq" in r.getMessage().lower() or r.exc_info for r in caplog.records)


def test_a_database_it_cannot_reach_is_the_same_503(client, monkeypatch, caplog):
    """The connection opens inside the route, not as a dependency, so a refused one is not the generic 500."""
    monkeypatch.setattr(deps, "open_background_conn", SERVICE_ROLE_OPENER)
    monkeypatch.setenv("SUPABASE_DB_URL", "postgresql://postgres:postgres@127.0.0.1:1/none?connect_timeout=2")
    with caplog.at_level("INFO"):
        res = client.post("/places/suggest", json={"query": "Brnoxq"})
    assert (res.status_code, res.json()) == (503, {"detail": UNAVAILABLE})
    assert "places/suggest: no database connection (OperationalError)" in [r.getMessage() for r in caplog.records]
    assert not any("brnoxq" in r.getMessage().lower() or r.exc_info for r in caplog.records)


def test_own_search_never_reads_the_picks_its_holdout(mirror, client, conn):
    _jihomoravsky(mirror)
    mirror.publish()
    conn.execute("ALTER TABLE place_picks RENAME TO place_picks_holdout")
    assert _chips(_suggest(client, "Brno"))[0] == ("obec", BRNO, "Brno", "Brno-město")


KOLIN_OKRES, KOLIN, NADRAZNI_KOLIN, NADRAZNI_KH = 3204, 533165, 401001, 401002


def _towns(m: _Mirror) -> dict[str, int]:
    """Kolín and Kutná Hora, each in its okres of the same name, in one kraj. Returns their unit ids."""
    kraj = m.unit("kraj", STREDOCESKY, "Středočeský kraj")
    return {"kolin": m.unit("obec", KOLIN, "Kolín", m.unit("okres", KOLIN_OKRES, "Kolín", kraj)),
            "kutna_hora": m.unit("obec", KUTNA_HORA, "Kutná Hora",
                                 m.unit("okres", KUTNA_HORA_OKRES, "Kutná Hora", kraj))}


def _doors(items: list[dict[str, Any]]) -> list[tuple[int, str]]:
    return [(i["id"], i["name"]) for i in items if i["level"] == "adresni_misto"]


def test_a_street_and_its_cp_answer_the_door_with_its_label_street_and_point_before_the_street(mirror, client):
    """Story 44: the door row is its level, kód ADM, the one door label, its town's okres, its point and the street's
    chip. The street itself answers by similarity ('nadrazni 12' to 'nadrazni' is 0.75), after the door."""
    towns = _towns(mirror)
    nadrazni = mirror.street(NADRAZNI_KOLIN, "Nádražní", towns["kolin"])
    mirror.house(21000101, towns["kolin"], street=nadrazni, cp=12, point=(15.2003, 50.0275))
    mirror.publish()
    assert _suggest(client, "Nádražní 12") == [
        {"level": "adresni_misto", "id": 21000101, "name": "Nádražní 12, Kolín", "context": "Kolín",
         "point": {"lat": 50.0275, "lng": 15.2003},
         "street": {"level": "ulice", "id": NADRAZNI_KOLIN, "name": "Nádražní", "context": "Kolín"}},
        {"level": "ulice", "id": NADRAZNI_KOLIN, "name": "Nádražní", "context": "Kolín",
         "point": {"lat": 50.0275, "lng": 15.2003}},
    ]


def test_a_street_and_its_co_answer_the_door_whose_orientation_number_it_is(mirror, client):
    """A bare number is tried as the č.o. too: Nádražní 345/12 is the house a user knows as Nádražní 12."""
    towns = _towns(mirror)
    nadrazni = mirror.street(NADRAZNI_KOLIN, "Nádražní", towns["kolin"])
    mirror.house(21000102, towns["kolin"], street=nadrazni, cp=345, co=12, point=(15.2011, 50.0281))
    mirror.publish()
    assert _suggest(client, "Nádražní 12") == [
        {"level": "adresni_misto", "id": 21000102, "name": "Nádražní 345/12, Kolín", "context": "Kolín",
         "point": {"lat": 50.0281, "lng": 15.2011},
         "street": {"level": "ulice", "id": NADRAZNI_KOLIN, "name": "Nádražní", "context": "Kolín"}},
        {"level": "ulice", "id": NADRAZNI_KOLIN, "name": "Nádražní", "context": "Kolín",
         "point": {"lat": 50.0281, "lng": 15.2011}},
    ]


def test_a_village_and_its_cp_answer_its_streetless_door_whose_one_label_has_no_number(mirror, client):
    """Story 53: "<village> <number>" is the Czech address of a house on no street, its village a část obce. The one
    door label (rule 25) names no number for a streetless door, so the row reads as the village; its context, the
    town's okres, tells it apart. The town and the část answer by similarity (0.67), the town first by its people."""
    okres = mirror.unit("okres", 3203, "Kladno", mirror.unit("kraj", STREDOCESKY, "Středočeský kraj"))
    lhota = mirror.unit("obec", 513041, "Lhota", okres)
    mirror.population(513041, 663)
    mirror.house(21000201, lhota, part=mirror.unit("cast_obce", 70971, "Lhota", lhota), cp=25,
                 point=(14.0021, 50.1703))
    mirror.publish()
    assert _suggest(client, "Lhota 25") == [
        {"level": "adresni_misto", "id": 21000201, "name": "Lhota", "context": "Kladno",
         "point": {"lat": 50.1703, "lng": 14.0021}, "street": None},
        {"level": "obec", "id": 513041, "name": "Lhota", "context": "Kladno", "point": None},
        {"level": "cast_obce", "id": 70971, "name": "Lhota", "context": "Lhota",
         "point": {"lat": 50.1703, "lng": 14.0021}},
    ]


def test_a_town_and_a_number_is_never_read_as_a_door_so_praha_6_stays_its_shortcut(mirror, client):
    """A village is a část obce, never the town: a č.p. is unique only within its část, and "Praha 6" is the městská
    část. Dejvice has a streetless č.p. 6, as Vysočany does in production; it is not answered."""
    praha = _praha(mirror)
    praha_6 = mirror.unit("momc", PRAHA_6, "Praha 6", praha)
    dejvice = mirror.unit("cast_obce", DEJVICE, "Dejvice", praha)
    mirror.quarters(praha_6, dejvice, mirror.unit("cast_obce", VOKOVICE, "Vokovice", praha))
    mirror.house(21000601, praha, part=dejvice, cp=6)
    mirror.publish()
    assert _chips(_suggest(client, "Praha 6")) == [
        ("momc", PRAHA_6, "Praha 6", "Praha"),
        ("kraj", PRAHA_KRAJ, "Hlavní město Praha", None),
        ("obec", PRAHA, "Praha", "území Hlavního města Prahy"),
    ]
