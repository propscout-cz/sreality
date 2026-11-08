"""Place picks (spec #2441 Part 1, ruling Q10), EXECUTED against the replayed schema (CI's migrations lane,
`TEST_DATABASE_URL`; locally `scripts/local_pg.sh test`): `/maps/resolve` with a pick over a mini mirror seeded in
the transaction, the background connection pointed at the test one, every record read back through the reader. Every
test rolls back."""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import Iterator
from datetime import datetime
from typing import Any

import psycopg
import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from api import dependencies as deps  # noqa: E402
from api import main as api_main  # noqa: E402
from location_data import place_picks  # noqa: E402
from location_data.name_index import normalize_name  # noqa: E402
from tests._live_property import REQUIRED_DB, _mirror_unit, db_url  # noqa: E402
from tests.api._jwt import mint  # noqa: E402

pytestmark = REQUIRED_DB

BRNO, PRAHA, PRAHA_6, VOKOVICE = 582786, 554782, 500178, 490067
BRNO_BOX = (16.50, 49.10, 16.70, 49.30)
PRAHA_BOX = (14.20, 49.90, 14.70, 50.20)
TOWN_BRNO = {"lat": 49.2, "lng": 16.6, "type": "regional.municipality", "name": "Brno"}
TOWN_PRAHA = {"lat": 50.1, "lng": 14.4, "type": "regional.municipality", "name": "Praha"}
MOMC_PRAHA_6 = {"lat": 50.1, "lng": 14.4, "type": "regional.municipality", "name": "Praha 6"}
PART_BARRANDOV = {"lat": 50.03, "lng": 14.4, "type": "regional.municipality_part", "name": "Barrandov"}


@pytest.fixture()
def conn() -> Iterator[psycopg.Connection]:
    connection = psycopg.connect(db_url(), autocommit=True, options="-c statement_timeout=20000")
    with connection, connection.transaction(force_rollback=True):
        _mirror(connection)
        yield connection


def _unit(conn: psycopg.Connection, level: str, code: int, name: str, parent: int, version: int) -> int:
    """A unit RÚIAN draws no polygon for (a městská část, a část obce), named in the index inside its obec."""
    unit = conn.execute(
        "INSERT INTO ruian_admin_units (level, code, name, name_norm, parent_id, path, valid_from, first_version_id,"
        " last_version_id) VALUES (%s::ruian_level, %s, %s, %s, %s, %s::ltree, '2020-01-01', %s, %s) RETURNING id",
        (level, code, name, normalize_name(name), parent, f"x{code}", version, version)).fetchone()[0]
    conn.execute(
        "INSERT INTO ruian_name_index (entity_kind, entity_id, registry_version_id, name, name_norm, name_kind,"
        " parent_obec_unit_id) VALUES (%s::ruian_level, %s, %s, %s, %s, 'official', %s)",
        (level, unit, version, name, normalize_name(name), parent))
    return int(unit)


def _mirror(conn: psycopg.Connection) -> None:
    """Brno in its okres and kraj; Praha in its kraj, with the městská část Praha 6 standing in Vokovice by one door."""
    conn.execute("UPDATE registry_versions SET is_current = false WHERE is_current")
    version = conn.execute(
        "INSERT INTO registry_versions (label, kind, source_date, artifact_urls, proj_version, proj_pipeline,"
        " is_current) VALUES ('test:place-picks', 'baseline', '2026-11-01', '{}', 'p', 'q', true) RETURNING id",
        ).fetchone()[0]
    with conn.cursor() as cur:
        kraj = _mirror_unit(cur, "kraj", 116, "Jihomoravský kraj", BRNO_BOX, version=version)
        okres = _mirror_unit(cur, "okres", 3702, "Brno-město", BRNO_BOX, parent=kraj, version=version)
        _mirror_unit(cur, "obec", BRNO, "Brno", BRNO_BOX, parent=okres, version=version)
        kraj = _mirror_unit(cur, "kraj", 19, "Hlavní město Praha", PRAHA_BOX, version=version)
        praha = _mirror_unit(cur, "obec", PRAHA, "Praha", PRAHA_BOX, parent=kraj, version=version)
    momc = _unit(conn, "momc", PRAHA_6, "Praha 6", praha, version)
    part = _unit(conn, "cast_obce", VOKOVICE, "Vokovice", praha, version)
    conn.execute(
        "INSERT INTO ruian_address_points (kod_adm, obec_unit_id, obec_kod, cast_obce_unit_id, cast_obce_kod,"
        " momc_unit_id, typ_so, cislo_domovni, psc, geom, plati_od, first_version_id, last_version_id)"
        " VALUES (21000001, %s, %s, %s, %s, %s, 'č.p.', 487, '16000', ST_SetSRID(ST_MakePoint(14.348, 50.101), 4326),"
        " '2020-01-01', %s, %s)", (praha, PRAHA, part, VOKOVICE, momc, version, version))


@pytest.fixture()
def client(conn: psycopg.Connection, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """The real route on the test connection, its background write on that connection too, a verified caller."""
    @contextlib.contextmanager
    def background() -> Iterator[psycopg.Connection]:
        yield conn

    monkeypatch.delenv("API_AUTH_OPTIONAL", raising=False)
    monkeypatch.setattr(deps, "open_background_conn", background)
    api_main.app.dependency_overrides[deps.get_db_conn] = lambda: conn
    try:
        yield TestClient(api_main.app, headers={"Authorization": f"Bearer {mint({'sub': str(uuid.uuid4())})}"})
    finally:
        api_main.app.dependency_overrides.pop(deps.get_db_conn, None)


def _picks(conn: psycopg.Connection) -> list[place_picks.PlacePick]:
    start, end = conn.execute("SELECT now() - interval '1 minute', now() + interval '1 minute'").fetchone()
    return place_picks.picks_between(conn, start, end)


def _resolve(client: TestClient, body: dict[str, Any], text: str, rank: int) -> dict[str, Any]:
    res = client.post("/maps/resolve", json={**body, "pick": {"text": text, "rank": rank}})
    assert res.status_code == 200, res.text
    return res.json()


def test_a_town_pick_is_recorded_with_its_text_rank_level_and_code(conn: psycopg.Connection,
                                                                   client: TestClient) -> None:
    answer = _resolve(client, TOWN_BRNO, "Brno", 2)
    assert answer == {"level": "obec", "id": BRNO, "name": "Brno", "context": None}
    (pick,) = _picks(conn)
    assert (pick.text, pick.rank, pick.level, pick.code) == ("Brno", 2, "obec", BRNO)
    assert isinstance(pick.created_at, datetime)
