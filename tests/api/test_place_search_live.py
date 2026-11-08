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

    def unit(self, level: str, code: int, name: str, parent: int | None = None, *,
             point: tuple[float, float] | None = None) -> int:
        path = ".".join(p for p in (self.paths.get(parent) if parent else None, f"{LABEL_PREFIX[level]}{code}") if p)
        unit = int(self.conn.execute(
            "INSERT INTO ruian_admin_units (level, code, name, name_norm, parent_id, path, valid_from,"
            " first_version_id, last_version_id) VALUES (%s::ruian_level, %s, %s, %s, %s, %s::ltree, '2020-01-01',"
            " %s, %s) RETURNING id",
            (level, code, name, normalize_name(name), parent, path, self.v, self.v)).fetchone()[0])
        self.paths[unit], self.codes[unit] = path, code
        if point is not None:
            lng, lat = point
            self.conn.execute(
                "INSERT INTO ruian_admin_unit_geometries (unit_id, registry_version_id, purpose,"
                " generalization_tolerance_m, geom, representative_point, inscribed_radius_m, centroid_point,"
                " containment_radius_m) SELECT %s, %s, 'authoritative', 0, ST_Multi(ST_Expand(p, 0.01)), p, 0, p, 0"
                "   FROM (SELECT ST_SetSRID(ST_MakePoint(%s, %s), 4326) AS p) s", (unit, self.v, lng, lat))
        return unit

    def street(self, code: int, name: str, obec: int, *, door: tuple[float, float] | None = None) -> None:
        self.conn.execute(
            "INSERT INTO ruian_streets (code, name, name_norm, obec_unit_id, first_version_id, last_version_id,"
            " valid_from) VALUES (%s, %s, %s, %s, %s, %s, '2020-01-01')",
            (code, name, normalize_street_name(name), obec, self.v, self.v))
        if door is not None:
            self.conn.execute(
                "INSERT INTO ruian_address_points (kod_adm, obec_unit_id, obec_kod, ulice_kod, psc, geom, plati_od,"
                " first_version_id, last_version_id) VALUES (%s, %s, %s, %s, '68201',"
                " ST_SetSRID(ST_MakePoint(%s, %s), 4326), '2020-01-01', %s, %s)",
                (next(self.doors), obec, self.codes[obec], code, *door, self.v, self.v))

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


def _zizkov(m: _Mirror) -> dict[str, int]:
    """Two quarters named Žižkov: one in Praha, one in Kutná Hora. Returns their towns' unit ids."""
    praha = m.unit("obec", PRAHA, "Praha", m.unit("okres", PRAHA_OKRES, "území Hlavního města Prahy",
                                                  m.unit("kraj", PRAHA_KRAJ, "Hlavní město Praha")))
    kutna_hora = m.unit("obec", KUTNA_HORA, "Kutná Hora", m.unit("okres", KUTNA_HORA_OKRES, "Kutná Hora",
                                                               m.unit("kraj", STREDOCESKY, "Středočeský kraj")))
    m.unit("cast_obce", ZIZKOV_PRAHA, "Žižkov", praha)
    m.unit("cast_obce", ZIZKOV_KH, "Žižkov", kutna_hora)
    return {"praha": praha, "kutna_hora": kutna_hora}


def _jihomoravsky(m: _Mirror) -> None:
    """Brno in okres Brno-město; the street Brněnská in Vyškov, whose okres starts with no 'brno'."""
    kraj = m.unit("kraj", JIHOMORAVSKY, "Jihomoravský kraj")
    brno_mesto = m.unit("okres", BRNO_MESTO, "Brno-město", kraj)
    m.unit("obec", BRNO, "Brno", brno_mesto, point=(16.6068, 49.1951))
    vyskov = m.unit("obec", VYSKOV, "Vyškov", m.unit("okres", VYSKOV_OKRES, "Vyškov", kraj))
    m.street(BRNENSKA, "Brněnská", vyskov, door=(16.9978, 49.2786))


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
