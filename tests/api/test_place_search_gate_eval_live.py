"""The gate's eval (#2452; spec #2441 Part 4, Q2 c), EXECUTED against the replayed schema (CI's migrations lane,
`TEST_DATABASE_URL`; locally `scripts/local_pg.sh test`): picks written by `/maps/resolve` over a mini mirror seeded in
the transaction, read back by the eval through Part 1's reader, own search asked through the eval's HTTP boundary (the
real route on the test connection, or canned rows). Any other URL fails the test. Every test rolls back. Goes at Stage
B (#2455) with the eval."""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import Iterator
from datetime import timedelta
from typing import Any

import psycopg
import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from api import dependencies as deps  # noqa: E402
from api import main as api_main  # noqa: E402
from location_data.name_index import normalize_name  # noqa: E402
from scripts import place_search_gate_eval as gate  # noqa: E402
from tests._live_property import REQUIRED_DB, _mirror_unit, db_url  # noqa: E402
from tests.api._jwt import mint  # noqa: E402

BRNO, PRAHA, PRAHA_6, VOKOVICE, LOMNICE = 582786, 554782, 500178, 490067, 581917
BRNO_BOX = (16.50, 49.10, 16.70, 49.30)
PRAHA_BOX = (14.20, 49.90, 14.70, 50.20)
TOWN_BRNO = {"lat": 49.2, "lng": 16.6, "type": "regional.municipality", "name": "Brno"}
MOMC_PRAHA_6 = {"lat": 50.1, "lng": 14.4, "type": "regional.municipality", "name": "Praha 6"}
PART_BARRANDOV = {"lat": 50.03, "lng": 14.4, "type": "regional.municipality_part", "name": "Barrandov"}

AUTH = "https://auth.test/auth/v1/token?grant_type=password"
API = "http://testserver"


@pytest.fixture()
def conn() -> Iterator[psycopg.Connection]:
    connection = psycopg.connect(db_url(), autocommit=True, options="-c statement_timeout=20000")
    with connection, connection.transaction(force_rollback=True):
        _mirror(connection)
        yield connection


def _index(conn: psycopg.Connection, level: str, unit: int, name: str, version: int, obec: int | None) -> None:
    conn.execute(
        "INSERT INTO ruian_name_index (entity_kind, entity_id, registry_version_id, name, name_norm, name_kind,"
        " parent_obec_unit_id) VALUES (%s::ruian_level, %s, %s, %s, %s, 'official', %s)",
        (level, unit, version, name, normalize_name(name), obec))


def _unit(conn: psycopg.Connection, level: str, code: int, name: str, parent: int | None, version: int) -> int:
    """A unit drawn with no polygon (a městská část, a část obce), named in the index inside its obec."""
    unit = int(conn.execute(
        "INSERT INTO ruian_admin_units (level, code, name, name_norm, parent_id, path, valid_from, first_version_id,"
        " last_version_id) VALUES (%s::ruian_level, %s, %s, %s, %s, %s::ltree, '2020-01-01', %s, %s) RETURNING id",
        (level, code, name, normalize_name(name), parent, f"x{code}", version, version)).fetchone()[0])
    _index(conn, level, unit, name, version, parent)
    return unit


def _door(conn: psycopg.Connection, kod: int, obec: int, obec_code: int, momc: int, part: int, part_code: int,
          point: tuple[float, float], version: int) -> None:
    """A door standing in `part` of `momc`: what makes resolve bind a městská část."""
    conn.execute(
        "INSERT INTO ruian_address_points (kod_adm, obec_unit_id, obec_kod, cast_obce_unit_id, cast_obce_kod,"
        " momc_unit_id, typ_so, cislo_domovni, psc, geom, plati_od, first_version_id, last_version_id)"
        " VALUES (%s, %s, %s, %s, %s, %s, 'č.p.', 487, '16000', ST_SetSRID(ST_MakePoint(%s, %s), 4326),"
        " '2020-01-01', %s, %s)", (kod, obec, obec_code, part, part_code, momc, *point, version, version))


def _mirror(conn: psycopg.Connection) -> dict[str, int]:
    """Brno in its okres and kraj, Praha in its kraj: drawn (resolve places a point in them) and named in the index
    (own search finds them by name); the městská část Praha 6 stands in Vokovice by one door."""
    conn.execute("UPDATE registry_versions SET is_current = false WHERE is_current")
    version = conn.execute(
        "INSERT INTO registry_versions (label, kind, source_date, artifact_urls, proj_version, proj_pipeline,"
        " is_current) VALUES ('test:gate-eval', 'baseline', '2026-11-01', '{}', 'p', 'q', true) RETURNING id",
        ).fetchone()[0]
    ids: dict[str, int] = {"version": version}
    with conn.cursor() as cur:
        ids["jmk"] = _mirror_unit(cur, "kraj", 116, "Jihomoravský kraj", BRNO_BOX, version=version)
        ids["brno_mesto"] = _mirror_unit(cur, "okres", 3702, "Brno-město", BRNO_BOX, parent=ids["jmk"],
                                         version=version)
        ids["brno"] = _mirror_unit(cur, "obec", BRNO, "Brno", BRNO_BOX, parent=ids["brno_mesto"], version=version)
        ids["praha_kraj"] = _mirror_unit(cur, "kraj", 19, "Hlavní město Praha", PRAHA_BOX, version=version)
        ids["praha"] = _mirror_unit(cur, "obec", PRAHA, "Praha", PRAHA_BOX, parent=ids["praha_kraj"], version=version)
    for key, level, name in (("jmk", "kraj", "Jihomoravský kraj"), ("brno_mesto", "okres", "Brno-město"),
                             ("brno", "obec", "Brno"), ("praha_kraj", "kraj", "Hlavní město Praha"),
                             ("praha", "obec", "Praha")):
        _index(conn, level, ids[key], name, version, None)
    ids["praha_6"] = _unit(conn, "momc", PRAHA_6, "Praha 6", ids["praha"], version)
    ids["vokovice"] = _unit(conn, "cast_obce", VOKOVICE, "Vokovice", ids["praha"], version)
    _door(conn, 21000001, ids["praha"], PRAHA, ids["praha_6"], ids["vokovice"], VOKOVICE, (14.348, 50.101), version)
    return ids


@pytest.fixture()
def client(conn: psycopg.Connection, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """The real routes on the test connection, resolve's pick written on it too, a verified caller."""
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


@pytest.fixture(autouse=True)
def credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """Dummy values: the eval only checks that they are there; its connection and sign-in are the test's."""
    for name, value in (("SUPABASE_DB_URL", "postgresql://unused.invalid/db"),
                        ("SMOKE_TEST_NONADMIN_EMAIL", "eval@test.invalid"), ("SMOKE_TEST_NONADMIN_PASSWORD", "x"),
                        ("VITE_SUPABASE_URL", "https://auth.test"), ("VITE_SUPABASE_ANON_KEY", "anon")):
        monkeypatch.setenv(name, value)


def _resolve(client: TestClient, body: dict[str, Any], text: str, rank: int) -> None:
    res = client.post("/maps/resolve", json={**body, "pick": {"text": text, "rank": rank}})
    assert res.status_code == 200, res.text


class _Answer:
    def __init__(self, status_code: int, body: dict[str, Any]) -> None:
        self.status_code, self._body = status_code, body

    def json(self) -> dict[str, Any]:
        return self._body


class _Route:
    """The eval's HTTP boundary: the auth host signs the smoke account in (an automation account, as production's
    is), the API host is the real route on the test connection; any other URL fails the test."""

    def __init__(self, client: TestClient) -> None:
        self.client = client

    def post(self, url: str, *, json: dict[str, Any], headers: dict[str, str], timeout: float) -> Any:
        if url == AUTH:
            token = mint({"sub": str(uuid.uuid4()), "app_metadata": {"automation": True}})
            return _Answer(200, {"access_token": token, "expires_in": 3600})
        if url == f"{API}/places/suggest":
            return self.client.post("/places/suggest", json=json, headers=headers)
        raise AssertionError(f"the eval called an unrouted URL: {url}")


def _run(conn: psycopg.Connection, capsys: pytest.CaptureFixture[str], http: Any,
         *argv: str) -> tuple[int, list[str]]:
    """The eval over the window of the transaction's Prague day, two days later (a completed day, DST or not)."""
    now, day = conn.execute("SELECT now(), (now() AT TIME ZONE 'Europe/Prague')::date").fetchone()

    @contextlib.contextmanager
    def open_conn() -> Iterator[psycopg.Connection]:
        yield conn

    io = gate.Boundaries(http=http, open_conn=open_conn, now=lambda: now + timedelta(days=2), sleep=lambda _s: None)
    code = gate.main(["--window-first", day.isoformat(), "--window-last", day.isoformat(), "--api", API, *argv], io)
    return code, capsys.readouterr().out.splitlines()


@REQUIRED_DB
def test_a_repeated_question_counts_once_and_keeps_its_best_mapy_rank(
    conn: psycopg.Connection, client: TestClient, capsys: pytest.CaptureFixture[str],
) -> None:
    _resolve(client, TOWN_BRNO, "Brno", 2)
    _resolve(client, TOWN_BRNO, "Brno", 1)
    code, out = _run(conn, capsys, _Route(client), "--no-latency", "--window-size", "1")
    assert "questions N = 1" in out
    assert "after strikes · top-1: 1 of 1 (needs 1) · Wilson 20.7–100.0 %" in out
    assert "after strikes · top-5: 1 of 1 (needs 1) · Wilson 20.7–100.0 %" in out
    assert "after strikes · Mapy rank-based (biased upward): top-1 1 of 1, top-5 1 of 1" in out
    assert out[-1] == "questions: top-5 pass · top-1 pass → pass"
    assert code == 0


@REQUIRED_DB
def test_null_text_refused_and_not_live_rows_are_counted_and_left_out(
    conn: psycopg.Connection, client: TestClient, capsys: pytest.CaptureFixture[str],
) -> None:
    _resolve(client, TOWN_BRNO, "Brno", 1)
    _resolve(client, MOMC_PRAHA_6, "Praha 6", 1)
    _resolve(client, PART_BARRANDOV, "Barrandov", 3)
    older = conn.execute(
        "INSERT INTO registry_versions (label, kind, source_date, artifact_urls, proj_version, proj_pipeline,"
        " is_current) VALUES ('test:gate-eval-older', 'baseline', '2026-10-01', '{}', 'p', 'q', false) RETURNING id",
        ).fetchone()[0]
    _unit(conn, "obec", LOMNICE, "Lomnice", None, older)
    # Resolve cannot record a unit the current version no longer draws, so this one row is written as it once was.
    conn.execute("INSERT INTO place_picks (text, rank, level, code) VALUES ('Lomnice', 1, 'obec', %s)", (LOMNICE,))
    code, out = _run(conn, capsys, _Route(client), "--no-latency", "--window-size", "1")
    assert "rows 4 · refused 1 · NULL text 1 · not live 1" in out
    assert "digit share 1 of 4 (25.0 %)" in out
    assert "questions N = 1" in out
    assert "after strikes · top-1: 1 of 1 (needs 1) · Wilson 20.7–100.0 %" in out
    assert code == 0


@REQUIRED_DB
def test_a_question_asked_in_an_earlier_window_is_left_out_and_n_0_gives_no_verdict(
    conn: psycopg.Connection, client: TestClient, capsys: pytest.CaptureFixture[str],
) -> None:
    """Ruling 2026-11-08: every window scores own search on questions it was never scored against."""
    # Resolve writes now(); the earlier pick is written as it was two days ago.
    conn.execute("INSERT INTO place_picks (created_at, text, rank, level, code)"
                 " VALUES (now() - interval '2 days', 'Brno', 1, 'obec', %s)", (BRNO,))
    _resolve(client, TOWN_BRNO, "Brno", 1)
    code, out = _run(conn, capsys, _Route(client), "--no-latency", "--window-size", "1")
    assert "asked in an earlier window: 1" in out
    assert "questions N = 0" in out
    assert out[-1] == "N = 0: no interval and no verdict"
    assert code == 2
