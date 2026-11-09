"""Own place search (#2449; spec #2441 Part 4, Q2 c, Q6 a, Q9, Q12 a), EXECUTED against the replayed schema (CI's
migrations lane, `TEST_DATABASE_URL`; locally `scripts/local_pg.sh test`): `POST /places/suggest` over a mini RÚIAN
mirror seeded in the transaction, its gazetteer built by the loader's own `name_index.rebuild`, both read models
refreshed through `refresh_matview` before the first search. Nothing calls Mapy. Every test rolls back. Door rows
(#2451; stories 44, 53) are asserted on numbered doors the mirror seeds; one door per street wherever a street's point
is asserted, as its point is its doors' mean.

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
               version: int | None = None, valid_from: str = "2020-01-01") -> int:
        """Open, drawn in `version` alone (the current one by default). Returns its row id."""
        version = self.v if version is None else version
        street = int(self.conn.execute(
            "INSERT INTO ruian_streets (code, name, name_norm, obec_unit_id, first_version_id, last_version_id,"
            " valid_from) VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
            (code, name, normalize_street_name(name), obec, version, version, valid_from)).fetchone()[0])
        if door is not None:
            self.conn.execute(
                "INSERT INTO ruian_address_points (kod_adm, obec_unit_id, obec_kod, ulice_kod, psc, geom, plati_od,"
                " first_version_id, last_version_id) VALUES (%s, %s, %s, %s, '68201',"
                " ST_SetSRID(ST_MakePoint(%s, %s), 4326), '2020-01-01', %s, %s)",
                (next(self.doors), obec, self.codes[obec], code, *door, version, version))
        return street

    def house(self, kod: int, obec: int, *, cp: int, street: int | None = None, part: int | None = None,
              typ: str = "č.p.", co: int | None = None, znak: str | None = None,
              point: tuple[float, float] | None = None, version: int | None = None,
              valid_to: str | None = None, momc: int | None = None) -> None:
        """A numbered door on `street` (a street ROW, as the load points a door) or, streetless, in `part`; born in
        `version` (the current one by default); `valid_to` retires it; `momc` is the městská část row it lies in."""
        version = self.v if version is None else version
        lng, lat = point if point is not None else (None, None)
        self.conn.execute(
            "INSERT INTO ruian_address_points (kod_adm, obec_unit_id, obec_kod, street_id, ulice_kod,"
            " cast_obce_unit_id, cast_obce_kod, typ_so, cislo_domovni, cislo_orientacni, znak_orientacniho, psc,"
            " geom, plati_od, valid_to, first_version_id, last_version_id, momc_unit_id)"
            " VALUES (%s, %s, %s, %s, (SELECT code FROM ruian_streets WHERE id = %s), %s, %s, %s, %s, %s, %s, '28002',"
            " ST_SetSRID(ST_MakePoint(%s::float8, %s::float8), 4326), '2020-01-01', %s, %s, %s, %s)",
            (kod, obec, self.codes[obec], street, street, part, self.codes[part] if part else None, typ, cp, co,
             znak, lng, lat, valid_to, version, version, momc))

    def close_street(self, street: int, version: int) -> None:
        """`version` closes the street row, as the load's street upsert does before it reopens a changed one."""
        self.conn.execute("UPDATE ruian_streets SET valid_to = '2026-11-30', last_version_id = %s WHERE id = %s",
                          (version, street))

    def repoint(self, kod: int, street: int, version: int) -> None:
        """The load updates the door in place onto `street`, the row it now belongs to (doors are not versioned)."""
        self.conn.execute("UPDATE ruian_address_points SET street_id = %s, last_version_id = %s WHERE kod_adm = %s",
                          (street, version, kod))

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
    """Kolín and Kutná Hora, each in its okres of the same name, in one kraj. Returns their and the kraj's unit ids."""
    kraj = m.unit("kraj", STREDOCESKY, "Středočeský kraj")
    return {"kraj": kraj, "kolin": m.unit("obec", KOLIN, "Kolín", m.unit("okres", KOLIN_OKRES, "Kolín", kraj)),
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


def test_one_read_answers_the_doors_of_same_named_streets_in_two_towns_the_served_street_s_first(mirror, client):
    """About 77 % of street names repeat in another town, so the read takes every exact namesake at once. Q12 a: the
    street in Kutná Hora serves a property and the one in Kolín none, so its door answers first though its code is
    higher; the streets themselves follow by similarity, in the same order."""
    towns = _towns(mirror)
    kolin = mirror.street(NADRAZNI_KOLIN, "Nádražní", towns["kolin"])
    kutna_hora = mirror.street(NADRAZNI_KH, "Nádražní", towns["kutna_hora"])
    mirror.house(21000101, towns["kolin"], street=kolin, cp=12, point=(15.2003, 50.0275))
    mirror.house(21000102, towns["kutna_hora"], street=kutna_hora, cp=12, point=(15.2681, 49.9481))
    mirror.served(region=STREDOCESKY, okres=KUTNA_HORA_OKRES, obec=KUTNA_HORA, street=NADRAZNI_KH)
    mirror.publish()
    assert _suggest(client, "Nádražní 12") == [
        {"level": "adresni_misto", "id": 21000102, "name": "Nádražní 12, Kutná Hora", "context": "Kutná Hora",
         "point": {"lat": 49.9481, "lng": 15.2681},
         "street": {"level": "ulice", "id": NADRAZNI_KH, "name": "Nádražní", "context": "Kutná Hora"}},
        {"level": "adresni_misto", "id": 21000101, "name": "Nádražní 12, Kolín", "context": "Kolín",
         "point": {"lat": 50.0275, "lng": 15.2003},
         "street": {"level": "ulice", "id": NADRAZNI_KOLIN, "name": "Nádražní", "context": "Kolín"}},
        {"level": "ulice", "id": NADRAZNI_KH, "name": "Nádražní", "context": "Kutná Hora",
         "point": {"lat": 49.9481, "lng": 15.2681}},
        {"level": "ulice", "id": NADRAZNI_KOLIN, "name": "Nádražní", "context": "Kolín",
         "point": {"lat": 50.0275, "lng": 15.2003}},
    ]


def test_a_bare_number_answers_its_cp_door_and_its_co_door_on_one_street_ties_by_code(mirror, client):
    """Both readings at once, neither ranked above the other: 345/12 has the lower code, so it goes first. 13/2 carries
    no 12."""
    towns = _towns(mirror)
    nadrazni = mirror.street(NADRAZNI_KOLIN, "Nádražní", towns["kolin"])
    mirror.house(21000101, towns["kolin"], street=nadrazni, cp=345, co=12)
    mirror.house(21000102, towns["kolin"], street=nadrazni, cp=12)
    mirror.house(21000103, towns["kolin"], street=nadrazni, cp=13, co=2)
    mirror.publish()
    street = {"level": "ulice", "id": NADRAZNI_KOLIN, "name": "Nádražní", "context": "Kolín"}
    assert _suggest(client, "Nádražní 12") == [
        {"level": "adresni_misto", "id": 21000101, "name": "Nádražní 345/12, Kolín", "context": "Kolín",
         "point": None, "street": street},
        {"level": "adresni_misto", "id": 21000102, "name": "Nádražní 12, Kolín", "context": "Kolín",
         "point": None, "street": street},
        {**street, "point": None},
    ]


def test_a_typed_cp_and_co_answer_only_that_door_a_typed_letter_must_match_an_untyped_one_matches_any(mirror, client):
    """12/3a is the č.p. 12 with the č.o. 3a: never 12/3b, the č.p. 3, or 99/12 (a typed č.o. ends the bare reading).
    Typed without its letter, 12/3 answers both letters, by code. Doors only: 'nadrazni 12 3a' reaches the street at
    exactly 0.6, which this test does not pin."""
    towns = _towns(mirror)
    nadrazni = mirror.street(NADRAZNI_KOLIN, "Nádražní", towns["kolin"])
    mirror.house(21000101, towns["kolin"], street=nadrazni, cp=12, co=3, znak="a")
    mirror.house(21000102, towns["kolin"], street=nadrazni, cp=12, co=3, znak="b")
    mirror.house(21000103, towns["kolin"], street=nadrazni, cp=3)
    mirror.house(21000104, towns["kolin"], street=nadrazni, cp=99, co=12)
    mirror.publish()
    assert _doors(_suggest(client, "Nádražní 12/3a")) == [(21000101, "Nádražní 12/3a, Kolín")]
    assert _doors(_suggest(client, "Nádražní 12/3")) == [
        (21000101, "Nádražní 12/3a, Kolín"),
        (21000102, "Nádražní 12/3b, Kolín"),
    ]


def test_a_village_and_a_typed_co_answer_no_streetless_door(mirror, client):
    """A house on no street carries no č.o., so "Lhota 25/3" is no streetless č.p. 25."""
    okres = mirror.unit("okres", 3203, "Kladno", mirror.unit("kraj", STREDOCESKY, "Středočeský kraj"))
    lhota = mirror.unit("obec", 513041, "Lhota", okres)
    mirror.house(21000201, lhota, part=mirror.unit("cast_obce", 70971, "Lhota", lhota), cp=25)
    mirror.publish()
    assert _doors(_suggest(client, "Lhota 25")) == [(21000201, "Lhota")]
    assert _doors(_suggest(client, "Lhota 25/3")) == []


def test_a_whole_text_that_is_exactly_a_name_answers_before_the_doors_and_they_before_what_is_only_similar(
        mirror, client):
    """The register has a street named "Na Hroudách 1": the whole text is exactly its name, so it answers first. The
    door Na Hroudách 1 comes next, as its street part is an exact name too, and the street Na Hroudách, only similar
    to the whole text, after it."""
    nymburk = mirror.unit("obec", 537004, "Nymburk",
                          mirror.unit("okres", 3208, "Nymburk", mirror.unit("kraj", STREDOCESKY, "Středočeský kraj")))
    na_hroudach = mirror.street(402001, "Na Hroudách", nymburk)
    mirror.street(402002, "Na Hroudách 1", nymburk)
    mirror.house(21000801, nymburk, street=na_hroudach, cp=1)
    mirror.publish()
    assert _suggest(client, "Na Hroudách 1") == [
        {"level": "ulice", "id": 402002, "name": "Na Hroudách 1", "context": "Nymburk", "point": None},
        {"level": "adresni_misto", "id": 21000801, "name": "Na Hroudách 1, Nymburk", "context": "Nymburk",
         "point": None, "street": {"level": "ulice", "id": 402001, "name": "Na Hroudách", "context": "Nymburk"}},
        {"level": "ulice", "id": 402001, "name": "Na Hroudách", "context": "Nymburk", "point": None},
    ]


def test_a_number_no_door_carries_answers_the_whole_text_s_rows_as_before(mirror, client):
    towns = _towns(mirror)
    nadrazni = mirror.street(NADRAZNI_KOLIN, "Nádražní", towns["kolin"])
    mirror.house(21000101, towns["kolin"], street=nadrazni, cp=12, point=(15.2003, 50.0275))
    mirror.publish()
    assert _suggest(client, "Nádražní 99") == [
        {"level": "ulice", "id": NADRAZNI_KOLIN, "name": "Nádražní", "context": "Kolín",
         "point": {"lat": 50.0275, "lng": 15.2003}},
    ]


def test_an_evidence_number_answers_only_when_marked_and_its_one_label_has_no_number(mirror, client):
    """A bare number is a č.p. or a č.o., never a č.ev.: the č.ev. 12 answers "Nádražní č.ev. 12" alone. The one door
    label names no č.ev. Only the door: the whole text is 0.53 similar to the street."""
    towns = _towns(mirror)
    nadrazni = mirror.street(NADRAZNI_KOLIN, "Nádražní", towns["kolin"])
    mirror.house(21000101, towns["kolin"], street=nadrazni, cp=12, typ="č.ev.")
    mirror.publish()
    assert _doors(_suggest(client, "Nádražní 12")) == []
    assert _suggest(client, "Nádražní č.ev. 12") == [
        {"level": "adresni_misto", "id": 21000101, "name": "Nádražní, Kolín", "context": "Kolín", "point": None,
         "street": {"level": "ulice", "id": NADRAZNI_KOLIN, "name": "Nádražní", "context": "Kolín"}},
    ]


def test_an_evidence_door_s_co_answers_a_bare_number_like_any_co_and_its_label_names_the_co_alone(mirror, client):
    """A bare number is never read as a č.ev. number, but a č.ev. door's own č.o. is a č.o., as the resolver's lookup
    reads it: production has 1,822 current č.ev. doors on a street with a č.o. (kód 14647397, č.ev. 2085 and č.o. 15,
    reads "5. května 15, Brandýs nad Labem-Stará Boleslav"). The one label names the č.o., never the č.ev."""
    towns = _towns(mirror)
    nadrazni = mirror.street(NADRAZNI_KOLIN, "Nádražní", towns["kolin"])
    mirror.house(21000102, towns["kolin"], street=nadrazni, cp=2085, typ="č.ev.", co=15)
    mirror.publish()
    street = {"level": "ulice", "id": NADRAZNI_KOLIN, "name": "Nádražní", "context": "Kolín"}
    assert _suggest(client, "Nádražní 15") == [
        {"level": "adresni_misto", "id": 21000102, "name": "Nádražní 15, Kolín", "context": "Kolín", "point": None,
         "street": street},
        {**street, "point": None},
    ]
    assert _doors(_suggest(client, "Nádražní 2085")) == []
    assert _doors(_suggest(client, "Nádražní č.ev. 2085")) == [(21000102, "Nádražní 15, Kolín")]


def test_a_village_s_evidence_number_answers_only_when_marked(mirror, client):
    """Production has 296,432 streetless č.ev. doors, a cottage's number; "Lhota 25" is the č.p. 25."""
    okres = mirror.unit("okres", 3203, "Kladno", mirror.unit("kraj", STREDOCESKY, "Středočeský kraj"))
    lhota = mirror.unit("obec", 513041, "Lhota", okres)
    mirror.house(21000201, lhota, part=mirror.unit("cast_obce", 70971, "Lhota", lhota), cp=25, typ="č.ev.")
    mirror.publish()
    assert _doors(_suggest(client, "Lhota 25")) == []
    assert _doors(_suggest(client, "Lhota č.ev. 25")) == [(21000201, "Lhota")]


def test_doors_answer_at_the_index_s_version_while_a_load_writes_the_next(mirror, client):
    """Ported from the shortcut's version test. A load writes the next version for hours before its pointer swap and
    updates doors in place. Nádražní closes and reopens: its door 12, re-pointed to the new row, is still found by the
    street's stable code, once, with the street read at the index's version. Not answered: a door the load retired,
    a door on a street only the next version draws, and the doors the load inserts, born in a version not yet
    published, on a street or in a village."""
    towns = _towns(mirror)
    nadrazni = mirror.street(NADRAZNI_KOLIN, "Nádražní", towns["kolin"])
    mirror.house(21000101, towns["kolin"], street=nadrazni, cp=12)
    mirror.house(21000102, towns["kolin"], street=nadrazni, cp=12, valid_to="2026-11-30")
    lhota = mirror.unit("obec", 513041, "Lhota", mirror.unit("okres", 3203, "Kladno", towns["kraj"]))
    village = mirror.unit("cast_obce", 70971, "Lhota", lhota)
    mirror.publish()

    nxt = mirror.next_version()
    mirror.close_street(nadrazni, nxt)
    reopened = mirror.street(NADRAZNI_KOLIN, "Nádražní", towns["kolin"], version=nxt, valid_from="2026-11-30")
    mirror.repoint(21000101, reopened, nxt)
    newer = mirror.street(NADRAZNI_KH, "Nádražní", towns["kutna_hora"], version=nxt, valid_from="2026-11-30")
    mirror.house(21000103, towns["kutna_hora"], street=newer, cp=12)
    mirror.house(21000104, towns["kolin"], street=reopened, cp=12, version=nxt)
    mirror.house(21000105, towns["kolin"], street=reopened, cp=345, co=12, version=nxt)
    mirror.house(21000202, lhota, part=village, cp=25, version=nxt)

    assert [i for i in _suggest(client, "Nádražní 12") if i["level"] == "adresni_misto"] == [
        {"level": "adresni_misto", "id": 21000101, "name": "Nádražní 12, Kolín", "context": "Kolín", "point": None,
         "street": {"level": "ulice", "id": NADRAZNI_KOLIN, "name": "Nádražní", "context": "Kolín"}},
    ]
    assert _doors(_suggest(client, "Lhota 25")) == []


def test_a_village_named_exactly_answers_before_one_whose_name_only_starts_so_before_its_qualifier(mirror, client):
    """As the name read ranks a qualifier-stripped name below an exact one: Lhota u Příbramě's č.p. 25 has the lower
    code, yet the Lhota's answers first. Production answers "Lhota 25" with 19 doors in official Lhotas and 14 in
    qualified ones."""
    kraj = mirror.unit("kraj", STREDOCESKY, "Středočeský kraj")
    lhota = mirror.unit("obec", 513041, "Lhota", mirror.unit("okres", 3203, "Kladno", kraj))
    breznice = mirror.unit("obec", 540153, "Březnice", mirror.unit("okres", 3211, "Příbram", kraj))
    mirror.house(21000201, breznice, part=mirror.unit("cast_obce", 70960, "Lhota u Příbramě", breznice), cp=25)
    mirror.house(21000202, lhota, part=mirror.unit("cast_obce", 70971, "Lhota", lhota), cp=25)
    mirror.publish()
    assert _doors(_suggest(client, "Lhota 25")) == [(21000202, "Lhota"), (21000201, "Lhota u Příbramě, Březnice")]


def test_a_street_typed_short_answers_the_register_s_long_name_and_one_the_register_spells_short(mirror, client):
    """The resolver's own abbreviation folds: "nám. Míru" is the register's náměstí Míru. The register spells some
    streets short itself, so the key as typed stays: "Nám. 5. května" is one."""
    towns = _towns(mirror)
    mirror.house(21000301, towns["kolin"], street=mirror.street(401005, "náměstí Míru", towns["kolin"]), cp=5)
    mirror.house(21000302, towns["kolin"], street=mirror.street(401006, "Nám. 5. května", towns["kolin"]), cp=3)
    mirror.publish()
    assert _doors(_suggest(client, "nám. Míru 5")) == [(21000301, "náměstí Míru 5, Kolín")]
    assert _doors(_suggest(client, "Nám. 5. května 3")) == [(21000302, "Nám. 5. května 3, Kolín")]


def test_a_street_typed_long_answers_the_register_s_short_spelling_and_csa_reaches_every_spelling(mirror, client):
    """The folds work on both sides, as the resolver folds them: "náměstí Svobody" reaches the register's "Nám.
    Svobody" (production spells 1,001 current streets short: 38 "nam svobody" beside 56 "namesti svobody"), and "ČSA"
    and "Čs. armády" each reach "Československé armády"."""
    towns = _towns(mirror)
    mirror.house(21000303, towns["kolin"], street=mirror.street(401007, "Nám. Svobody", towns["kolin"]), cp=1)
    mirror.house(21000304, towns["kolin"], street=mirror.street(401008, "Československé armády", towns["kolin"]),
                 cp=3)
    mirror.publish()
    assert _doors(_suggest(client, "náměstí Svobody 1")) == [(21000303, "Nám. Svobody 1, Kolín")]
    assert _doors(_suggest(client, "ČSA 3")) == [(21000304, "Československé armády 3, Kolín")]
    assert _doors(_suggest(client, "Čs. armády 3")) == [(21000304, "Československé armády 3, Kolín")]


def test_a_street_typed_with_the_word_ulice_after_it_answers_its_door(mirror, client):
    """The resolver's generic-word strip: "Nádražní ulice 12" is Nádražní 12."""
    towns = _towns(mirror)
    mirror.house(21000101, towns["kolin"], street=mirror.street(NADRAZNI_KOLIN, "Nádražní", towns["kolin"]), cp=12)
    mirror.publish()
    assert _doors(_suggest(client, "Nádražní ulice 12")) == [(21000101, "Nádražní 12, Kolín")]


def test_doors_fill_the_whole_list_when_there_are_as_many_as_the_limit(mirror, client):
    """Builder decision for the operator's ruling (PR notes): doors rank above what is only similar, so a common street
    shows doors alone (production has 67 doors for "Nádražní 12", 10 shown, no street row) and a common village
    shows no town ("Lhota 25", 33 doors)."""
    towns = _towns(mirror)
    nadrazni = mirror.street(NADRAZNI_KOLIN, "Nádražní", towns["kolin"])
    mirror.house(21000101, towns["kolin"], street=nadrazni, cp=345, co=12)
    mirror.house(21000102, towns["kolin"], street=nadrazni, cp=12)
    lhota = mirror.unit("obec", 513041, "Lhota", mirror.unit("okres", 3203, "Kladno", towns["kraj"]))
    mirror.house(21000201, lhota, part=mirror.unit("cast_obce", 70971, "Lhota", lhota), cp=25)
    mirror.publish()
    street = {"level": "ulice", "id": NADRAZNI_KOLIN, "name": "Nádražní", "context": "Kolín"}
    res = client.post("/places/suggest", json={"query": "Nádražní 12", "limit": 2})
    assert res.status_code == 200, res.text
    assert res.json() == {"items": [
        {"level": "adresni_misto", "id": 21000101, "name": "Nádražní 345/12, Kolín", "context": "Kolín",
         "point": None, "street": street},
        {"level": "adresni_misto", "id": 21000102, "name": "Nádražní 12, Kolín", "context": "Kolín",
         "point": None, "street": street},
    ]}
    res = client.post("/places/suggest", json={"query": "Lhota 25", "limit": 1})
    assert res.status_code == 200, res.text
    assert res.json() == {"items": [
        {"level": "adresni_misto", "id": 21000201, "name": "Lhota", "context": "Kladno", "point": None,
         "street": None},
    ]}


def test_a_failed_door_read_is_the_same_503_and_no_log_line_carries_the_typed_text(mirror, client, conn, caplog):
    """The door read runs in the name read's transaction, under its timeout and its SQLSTATE-only log line. A text
    with no number never reads the doors."""
    towns = _towns(mirror)
    mirror.house(21000101, towns["kolin"], street=mirror.street(NADRAZNI_KOLIN, "Nádražní", towns["kolin"]), cp=12)
    mirror.publish()
    with caplog.at_level("INFO"):
        assert _doors(_suggest(client, "Nádražní 12")) == [(21000101, "Nádražní 12, Kolín")]
        conn.execute("ALTER TABLE ruian_address_points RENAME TO ruian_address_points_gone")
        assert _chips(_suggest(client, "Nádražní")) == [("ulice", NADRAZNI_KOLIN, "Nádražní", "Kolín")]
        res = client.post("/places/suggest", json={"query": "Nádražní 12"})
    assert (res.status_code, res.json()) == (503, {"detail": UNAVAILABLE})
    lines = [r.getMessage() for r in caplog.records if r.name == "location_data.place_search"]
    assert lines == ["place_search: read failed (SQLSTATE 42P01)"]
    assert not any("nádražní" in r.getMessage().lower() or "nadrazni" in r.getMessage().lower() or r.exc_info
                   for r in caplog.records)


DOOR_KOLIN = {"level": "adresni_misto", "id": 21000101, "name": "Nádražní 12, Kolín", "context": "Kolín",
              "point": {"lat": 50.0275, "lng": 15.2003},
              "street": {"level": "ulice", "id": NADRAZNI_KOLIN, "name": "Nádražní", "context": "Kolín"}}


@pytest.mark.parametrize("query", ["Nádražní 12, Kolín", "Kolín, Nádražní 12", "Nádražní 12 Kolín"])
def test_a_street_and_number_typed_with_its_town_answer_its_door_first_the_row_the_street_and_number_give(
        mirror, client, query):
    """#2485 (story 53): the town is split off before the door read. The whole text names no place by similarity (0.50
    to the street at best), so the door is the only row, the same row "Nádražní 12" answers."""
    towns = _towns(mirror)
    nadrazni = mirror.street(NADRAZNI_KOLIN, "Nádražní", towns["kolin"])
    mirror.house(21000101, towns["kolin"], street=nadrazni, cp=12, point=(15.2003, 50.0275))
    mirror.publish()
    assert _suggest(client, query) == [DOOR_KOLIN]
    assert _suggest(client, "Nádražní 12") == [
        DOOR_KOLIN,
        {"level": "ulice", "id": NADRAZNI_KOLIN, "name": "Nádražní", "context": "Kolín",
         "point": {"lat": 50.0275, "lng": 15.2003}},
    ]


def test_a_street_whose_name_holds_a_number_still_answers_its_door_as_the_whole_text_is_read_last(mirror, client):
    """The town split reads "Pod 5 Lipami 12" as "Pod 5" in a town "Lipami 12", which names nothing, so the whole text
    answers as #2451 read it; the street follows by similarity (0.81)."""
    towns = _towns(mirror)
    mirror.house(21000105, towns["kolin"], street=mirror.street(401009, "Pod 5 Lipami", towns["kolin"]), cp=12)
    mirror.publish()
    street = {"level": "ulice", "id": 401009, "name": "Pod 5 Lipami", "context": "Kolín"}
    assert _suggest(client, "Pod 5 Lipami 12") == [
        {"level": "adresni_misto", "id": 21000105, "name": "Pod 5 Lipami 12, Kolín", "context": "Kolín",
         "point": None, "street": street},
        {**street, "point": None},
    ]


def test_the_town_typed_decides_which_of_two_same_named_streets_answers_whatever_their_served_counts(mirror, client):
    """Kutná Hora's Nádražní serves a property and Kolín's none: typed with Kolín, Kolín's door 12 answers alone, and
    typed with Kutná Hora, Kutná Hora's. With no town, both answer as #2451 ranks them, the served street's first."""
    towns = _towns(mirror)
    kolin = mirror.street(NADRAZNI_KOLIN, "Nádražní", towns["kolin"])
    kutna_hora = mirror.street(NADRAZNI_KH, "Nádražní", towns["kutna_hora"])
    mirror.house(21000101, towns["kolin"], street=kolin, cp=12, point=(15.2003, 50.0275))
    mirror.house(21000102, towns["kutna_hora"], street=kutna_hora, cp=12, point=(15.2681, 49.9481))
    mirror.served(region=STREDOCESKY, okres=KUTNA_HORA_OKRES, obec=KUTNA_HORA, street=NADRAZNI_KH)
    mirror.publish()
    assert _suggest(client, "Nádražní 12, Kolín") == [DOOR_KOLIN]
    assert _suggest(client, "Kutná Hora, Nádražní 12") == [
        {"level": "adresni_misto", "id": 21000102, "name": "Nádražní 12, Kutná Hora", "context": "Kutná Hora",
         "point": {"lat": 49.9481, "lng": 15.2681},
         "street": {"level": "ulice", "id": NADRAZNI_KH, "name": "Nádražní", "context": "Kutná Hora"}},
    ]
    assert _suggest(client, "Nádražní 12") == [
        {"level": "adresni_misto", "id": 21000102, "name": "Nádražní 12, Kutná Hora", "context": "Kutná Hora",
         "point": {"lat": 49.9481, "lng": 15.2681},
         "street": {"level": "ulice", "id": NADRAZNI_KH, "name": "Nádražní", "context": "Kutná Hora"}},
        {"level": "adresni_misto", "id": 21000101, "name": "Nádražní 12, Kolín", "context": "Kolín",
         "point": {"lat": 50.0275, "lng": 15.2003},
         "street": {"level": "ulice", "id": NADRAZNI_KOLIN, "name": "Nádražní", "context": "Kolín"}},
        {"level": "ulice", "id": NADRAZNI_KH, "name": "Nádražní", "context": "Kutná Hora",
         "point": {"lat": 49.9481, "lng": 15.2681}},
        {"level": "ulice", "id": NADRAZNI_KOLIN, "name": "Nádražní", "context": "Kolín",
         "point": {"lat": 50.0275, "lng": 15.2003}},
    ]


KLADNO_OKRES, KLADNO, LHOTA, LHOTA_LHOTA, LHOTA_KLADNO = 3203, 532053, 513041, 70971, 70972


def test_a_village_and_its_cp_typed_with_its_town_answer_the_streetless_door_of_that_town_s_village(mirror, client):
    """Two villages called Lhota: one is its own obec in okres Kladno and serves a property, the other a část of the
    town Kladno (its code invented). "Lhota 25, Kladno" answers the town's: an okres is a region, never the town."""
    okres = mirror.unit("okres", KLADNO_OKRES, "Kladno", mirror.unit("kraj", STREDOCESKY, "Středočeský kraj"))
    kladno, lhota = mirror.unit("obec", KLADNO, "Kladno", okres), mirror.unit("obec", LHOTA, "Lhota", okres)
    mirror.house(21000201, lhota, part=mirror.unit("cast_obce", LHOTA_LHOTA, "Lhota", lhota), cp=25,
                 point=(14.0021, 50.1703))
    mirror.house(21000203, kladno, part=mirror.unit("cast_obce", LHOTA_KLADNO, "Lhota", kladno), cp=25)
    mirror.served(region=STREDOCESKY, okres=KLADNO_OKRES, obec=LHOTA, part=LHOTA_LHOTA)
    mirror.publish()
    assert _suggest(client, "Lhota 25, Kladno") == [
        {"level": "adresni_misto", "id": 21000203, "name": "Lhota, Kladno", "context": "Kladno", "point": None,
         "street": None},
    ]
    assert _doors(_suggest(client, "Lhota 25")) == [(21000201, "Lhota"), (21000203, "Lhota, Kladno")]


@pytest.mark.parametrize("query", ["Nádražní 12, Pečky", "Nádražní 12 Kutná Hora"])
def test_a_typed_town_that_names_nothing_or_has_no_such_street_answers_no_door_of_another_town(mirror, client, query):
    """Builder decision for the operator's ruling (PR notes), as the ticket recommends: the mirror has no Pečky, and
    Kutná Hora has no Nádražní, so Kolín's door 12 is not offered in their place. The whole text names nothing either
    (0.50 to the street at best)."""
    towns = _towns(mirror)
    nadrazni = mirror.street(NADRAZNI_KOLIN, "Nádražní", towns["kolin"])
    mirror.house(21000101, towns["kolin"], street=nadrazni, cp=12, point=(15.2003, 50.0275))
    mirror.publish()
    assert _suggest(client, query) == []


PARDUBICKY, CHRUDIM_OKRES, KLADNO_CHRUDIM = 94, 3603, 571580


def test_a_town_name_two_obce_share_answers_the_doors_in_both_the_served_first(mirror, client):
    """Builder decision for the operator's ruling (PR notes): every town of the typed name is kept, as production has
    two obce Kladno (okresy Kladno and Chrudim). Their doors rank as any doors do: the served street's first though its
    code is higher. Kolín's door 12 serves the most and is not in either."""
    towns = _towns(mirror)
    kladno = mirror.unit("obec", KLADNO, "Kladno", mirror.unit("okres", KLADNO_OKRES, "Kladno", towns["kraj"]))
    pardubicky = mirror.unit("kraj", PARDUBICKY, "Pardubický kraj")
    chrudim = mirror.unit("obec", KLADNO_CHRUDIM, "Kladno", mirror.unit("okres", CHRUDIM_OKRES, "Chrudim", pardubicky))
    mirror.house(21000101, towns["kolin"], street=mirror.street(NADRAZNI_KOLIN, "Nádražní", towns["kolin"]), cp=12)
    mirror.house(21000141, kladno, street=mirror.street(401011, "Nádražní", kladno), cp=12)
    mirror.house(21000142, chrudim, street=mirror.street(401012, "Nádražní", chrudim), cp=12)
    mirror.served(region=PARDUBICKY, okres=CHRUDIM_OKRES, obec=KLADNO_CHRUDIM, street=401012)
    for _ in range(2):
        mirror.served(region=STREDOCESKY, okres=KOLIN_OKRES, obec=KOLIN, street=NADRAZNI_KOLIN)
    mirror.publish()
    assert _suggest(client, "Nádražní 12, Kladno") == [
        {"level": "adresni_misto", "id": 21000142, "name": "Nádražní 12, Kladno", "context": "Chrudim", "point": None,
         "street": {"level": "ulice", "id": 401012, "name": "Nádražní", "context": "Kladno"}},
        {"level": "adresni_misto", "id": 21000141, "name": "Nádražní 12, Kladno", "context": "Kladno", "point": None,
         "street": {"level": "ulice", "id": 401011, "name": "Nádražní", "context": "Kladno"}},
    ]


def test_a_letter_on_a_bare_number_is_the_co_s_letter_with_or_without_its_town(mirror, client):
    """Builder decision for the operator's ruling (PR notes), as the ticket recommends: RÚIAN carries a letter only on
    the č.o., so "12a" is the č.o. 12 with the letter a, never the č.p. 12 or the č.o. 12b. With no town, the street
    follows by similarity (0.69); with one, nothing else is similar enough."""
    towns = _towns(mirror)
    nadrazni = mirror.street(NADRAZNI_KOLIN, "Nádražní", towns["kolin"])
    mirror.house(21000101, towns["kolin"], street=nadrazni, cp=345, co=12, znak="a")
    mirror.house(21000102, towns["kolin"], street=nadrazni, cp=346, co=12, znak="b")
    mirror.house(21000103, towns["kolin"], street=nadrazni, cp=12)
    mirror.publish()
    street = {"level": "ulice", "id": NADRAZNI_KOLIN, "name": "Nádražní", "context": "Kolín"}
    door = {"level": "adresni_misto", "id": 21000101, "name": "Nádražní 345/12a, Kolín", "context": "Kolín",
            "point": None, "street": street}
    assert _suggest(client, "Nádražní 12a") == [door, {**street, "point": None}]
    assert _suggest(client, "Nádražní 12a, Kolín") == [door]
    assert _suggest(client, "Nádražní 12a Kolín") == [door]


NADRAZNI_PRAHA = 401003


@pytest.mark.parametrize(("query", "kod"), [("Nádražní 12, Praha 6", 21000611), ("Praha 7, Nádražní 12", 21000612),
                                           ("Nádražní 12 Praha 6", 21000611)])
def test_a_mestska_cast_typed_as_the_town_answers_the_door_that_lies_in_it(mirror, client, query, kod):
    """A street runs through several městské části of its town, and its doors carry the one each lies in: Nádražní 12
    is in Praha 6, Nádražní 345/12 in Praha 7. Both pieces of "Praha 7, Nádražní 12" carry a number; "Praha 7" in a town
    "Nádražní 12" names nothing, so the other reading answers."""
    praha = _praha(mirror)
    praha_6, praha_7 = mirror.unit("momc", PRAHA_6, "Praha 6", praha), mirror.unit("momc", PRAHA_7, "Praha 7", praha)
    nadrazni = mirror.street(NADRAZNI_PRAHA, "Nádražní", praha)
    mirror.house(21000611, praha, street=nadrazni, cp=12, momc=praha_6)
    mirror.house(21000612, praha, street=nadrazni, cp=345, co=12, momc=praha_7)
    mirror.publish()
    street = {"level": "ulice", "id": NADRAZNI_PRAHA, "name": "Nádražní", "context": "Praha"}
    door = {21000611: {"level": "adresni_misto", "id": 21000611, "name": "Nádražní 12, Praha",
                       "context": "území Hlavního města Prahy", "point": None, "street": street},
            21000612: {"level": "adresni_misto", "id": 21000612, "name": "Nádražní 345/12, Praha",
                       "context": "území Hlavního města Prahy", "point": None, "street": street}}
    assert _suggest(client, query) == [door[kod]]


KOLIN_II, KOLIN_III = 402095, 402109


def test_a_cast_obce_typed_as_the_town_answers_the_door_that_lies_in_it(mirror, client):
    """A č.p. is unique only within its část, so one street can carry two doors 12, here in Kolín II and Kolín III
    (production's Kolín has 16 such pairs). The one label names the town alone, so the two rows read alike; typed with
    Kolín III, only its door answers."""
    towns = _towns(mirror)
    nadrazni = mirror.street(NADRAZNI_KOLIN, "Nádražní", towns["kolin"])
    kolin_ii = mirror.unit("cast_obce", KOLIN_II, "Kolín II", towns["kolin"])
    kolin_iii = mirror.unit("cast_obce", KOLIN_III, "Kolín III", towns["kolin"])
    mirror.house(21000121, towns["kolin"], street=nadrazni, part=kolin_ii, cp=12)
    mirror.house(21000122, towns["kolin"], street=nadrazni, part=kolin_iii, cp=12)
    mirror.publish()
    assert _suggest(client, "Nádražní 12, Kolín III") == [
        {"level": "adresni_misto", "id": 21000122, "name": "Nádražní 12, Kolín", "context": "Kolín", "point": None,
         "street": {"level": "ulice", "id": NADRAZNI_KOLIN, "name": "Nádražní", "context": "Kolín"}},
    ]


def test_a_village_typed_with_a_mestska_cast_answers_its_streetless_door_only_if_it_lies_in_it(mirror, client):
    """Dejvice has a streetless č.p. 6, as Vysočany does in production, and it lies in Praha 6: a village narrows to the
    town's obec, Praha, and its door to the městská část typed."""
    praha = _praha(mirror)
    praha_6 = mirror.unit("momc", PRAHA_6, "Praha 6", praha)
    mirror.unit("momc", PRAHA_7, "Praha 7", praha)
    mirror.house(21000601, praha, part=mirror.unit("cast_obce", DEJVICE, "Dejvice", praha), cp=6, momc=praha_6)
    mirror.publish()
    assert _suggest(client, "Dejvice 6, Praha 6") == [
        {"level": "adresni_misto", "id": 21000601, "name": "Dejvice, Praha", "context": "území Hlavního města Prahy",
         "point": None, "street": None},
    ]
    assert _suggest(client, "Dejvice 6, Praha 7") == []


@pytest.mark.parametrize("query", ["28. října 5, Kolín", "28. října 5 Kolín"])
def test_a_street_named_with_an_ordinal_typed_with_its_town_answers_its_door(mirror, client, query):
    """The ordinal is part of the street's name, never a number the town follows. The whole text names nothing (0.53
    to the street)."""
    towns = _towns(mirror)
    mirror.house(21000131, towns["kolin"], street=mirror.street(401010, "28. října", towns["kolin"]), cp=5)
    mirror.publish()
    assert _suggest(client, query) == [
        {"level": "adresni_misto", "id": 21000131, "name": "28. října 5, Kolín", "context": "Kolín", "point": None,
         "street": {"level": "ulice", "id": 401010, "name": "28. října", "context": "Kolín"}},
    ]


def test_a_failed_read_of_a_text_with_a_town_is_the_same_503_with_one_line_naming_no_town(mirror, client, conn, caplog):
    """A text with a town reads the doors once per reading, in the name read's transaction: the first statement that
    fails ends it, with the one SQLSTATE line."""
    towns = _towns(mirror)
    mirror.house(21000101, towns["kolin"], street=mirror.street(NADRAZNI_KOLIN, "Nádražní", towns["kolin"]), cp=12)
    mirror.publish()
    with caplog.at_level("INFO"):
        assert _suggest(client, "Nádražní 12, Kolín") == [
            {"level": "adresni_misto", "id": 21000101, "name": "Nádražní 12, Kolín", "context": "Kolín", "point": None,
             "street": {"level": "ulice", "id": NADRAZNI_KOLIN, "name": "Nádražní", "context": "Kolín"}},
        ]
        conn.execute("ALTER TABLE ruian_address_points RENAME TO ruian_address_points_gone")
        res = client.post("/places/suggest", json={"query": "Nádražní 12, Kolín"})
    assert (res.status_code, res.json()) == (503, {"detail": UNAVAILABLE})
    lines = [r.getMessage() for r in caplog.records if r.name == "location_data.place_search"]
    assert lines == ["place_search: read failed (SQLSTATE 42P01)"]
    assert not any(word in r.getMessage().lower() for word in ("kolín", "kolin", "nádražní", "nadrazni")
                   for r in caplog.records)
    assert not any(r.exc_info for r in caplog.records)
