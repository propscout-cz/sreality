"""The gate's eval (#2452; spec #2441 Part 4, Q2 c), EXECUTED against the replayed schema (CI's migrations lane,
`TEST_DATABASE_URL`; locally `scripts/local_pg.sh test`): picks written by `/maps/resolve` over a mini mirror seeded in
the transaction, read back by the eval through Part 1's reader, own search asked through the eval's HTTP boundary (the
real route on the test connection, or canned rows). Any other URL fails the test. Every test rolls back. Goes at Stage
B (#2455) with the eval."""

from __future__ import annotations

import contextlib
import json as jsonlib
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
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
BRNO_STRED, STARE_BRNO = 551082, 411744
BRNO_BOX = (16.50, 49.10, 16.70, 49.30)
PRAHA_BOX = (14.20, 49.90, 14.70, 50.20)
TOWN_BRNO = {"lat": 49.2, "lng": 16.6, "type": "regional.municipality", "name": "Brno"}
MOMC_PRAHA_6 = {"lat": 50.1, "lng": 14.4, "type": "regional.municipality", "name": "Praha 6"}
PART_BARRANDOV = {"lat": 50.03, "lng": 14.4, "type": "regional.municipality_part", "name": "Barrandov"}
MOMC_BRNO_STRED = {"lat": 49.19, "lng": 16.6, "type": "regional.municipality_part", "name": "Brno-střed"}

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


def _mirror(conn: psycopg.Connection) -> None:
    """Brno in its okres and kraj, Praha in its kraj: drawn (resolve places a point in them) and named in the index
    (own search finds them by name); the městské části Praha 6 and Brno-střed each stand in one quarter by one door,
    with no quarter pair yet (a test writes the pairs it needs)."""
    conn.execute("UPDATE registry_versions SET is_current = false WHERE is_current")
    version = conn.execute(
        "INSERT INTO registry_versions (label, kind, source_date, artifact_urls, proj_version, proj_pipeline,"
        " is_current) VALUES ('test:gate-eval', 'baseline', '2026-11-01', '{}', 'p', 'q', true) RETURNING id",
        ).fetchone()[0]
    ids: dict[str, int] = {}
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
    ids["brno_stred"] = _unit(conn, "momc", BRNO_STRED, "Brno-střed", ids["brno"], version)
    ids["stare_brno"] = _unit(conn, "cast_obce", STARE_BRNO, "Staré Brno", ids["brno"], version)
    _door(conn, 21000002, ids["brno"], BRNO, ids["brno_stred"], ids["stare_brno"], STARE_BRNO, (16.6, 49.19), version)


def _pair(conn: psycopg.Connection, momc: int, part: int) -> None:
    """A městská část's quarter pair at the current version, as the registry load's finish step writes it."""
    conn.execute(
        "INSERT INTO ruian_admin_unit_relations (from_id, to_id, relation_type, registry_version_id)"
        " SELECT m.id, q.id, 'momc_cast_obce', v.id FROM ruian_admin_units m, ruian_admin_units q, registry_versions v"
        " WHERE m.level = 'momc' AND m.code = %s AND q.level = 'cast_obce' AND q.code = %s AND v.is_current",
        (momc, part))


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


class _Breach(BaseException):
    """A step outside the eval's boundary contract. Not an Exception, so the eval's own catch-all cannot hide it."""


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
            return self.suggest(json, headers)
        raise _Breach(f"the eval called an unrouted URL: {url}")

    def suggest(self, body: dict[str, Any], headers: dict[str, str]) -> Any:
        return self.client.post("/places/suggest", json=body, headers=headers)


FILLER = [("kraj", 116), ("okres", 3702), ("obec", PRAHA), ("kraj", 19), ("momc", PRAHA_6)]


class _Canned(_Route):
    """Own search's rows, canned per text: Brno at the 1-based position `at[text]` (`others` for a text not in it),
    filler rows before it."""

    def __init__(self, at: dict[str, int], others: int | None = None) -> None:
        self.at, self.others = at, others

    def suggest(self, body: dict[str, Any], headers: dict[str, str]) -> Any:
        position = self.at.get(body["query"], self.others)
        if position is None:
            raise _Breach(f"no canned answer for {body['query']!r}")
        rows = [*FILLER[:position - 1], ("obec", BRNO), *FILLER[position - 1:]]
        return _Answer(200, {"items": [{"level": lv, "id": code, "name": "", "context": None} for lv, code in rows]})


class _Clock:
    """The eval's clock: a sleep moves it, and lands in `events` beside the requests the API host logs."""

    def __init__(self, start: datetime) -> None:
        self.at, self.events = start, []

    def now(self) -> datetime:
        return self.at

    def sleep(self, seconds: float) -> None:
        self.events.append(("sleep", seconds))
        self.at += timedelta(seconds=seconds)


def _stamp(at: datetime) -> str:
    """A log row's stamp as the CLI prints it: UTC, to the nanosecond."""
    return f"{at.astimezone(UTC):%Y-%m-%dT%H:%M:%S.%f}000Z"


class _Railway:
    """Railway's side of the boundary, as the pinned CLI answered on 2026-11-09: `deployment list --json` newest first,
    with no removal time; `logs <deployment> --http --json` one row per line, oldest first, at most `--lines` (more than
    5000 is refused), the newest kept when the range holds more, `--method`, `--path`, `--since`, `--until` and an
    `@clientUa:` filter applied on the server. A row is logged by the deployment serving then: the one that came up
    last (`up`; None for one that never served). Any other command breaches the contract: the eval only reads."""

    def __init__(self, deployments: list[tuple[str, datetime, datetime | None]]) -> None:
        self.deployments, self.rows = deployments, []

    def log(self, at: datetime, method: str, path: str, status: int, ms: int, ua: str = "Mozilla/5.0 (test)") -> None:
        serving = max((d for d in self.deployments if d[2] is not None and d[2] <= at), key=lambda d: d[2])
        # The fields of a row read on 2026-11-09; the address and the agent are invented.
        self.rows.append({
            "timestamp": _stamp(at), "method": method, "path": path, "httpStatus": status, "totalDuration": ms,
            "requestId": str(uuid.uuid4()), "host": "api.test", "clientUa": ua, "srcIp": "192.0.2.1",
            "edgeRegion": "europe-west4-drams3a", "txBytes": 512, "rxBytes": 64, "upstreamRqDuration": ms - 5,
            "upstreamAddress": "10.0.0.1:8080", "upstreamProto": "HTTP/1.1", "downstreamProto": "HTTP/2.0",
            "upstreamErrors": "", "responseDetails": "", "deploymentId": serving[0],
            "deploymentInstanceId": f"i-{serving[0]}"})

    def __call__(self, argv: list[str]) -> str:
        if argv[:2] == ["deployment", "list"]:
            return jsonlib.dumps([{"id": d, "status": "SUCCESS" if n == 0 else "REMOVED", "createdAt": _stamp(made),
                                   "meta": {}}
                                  for n, (d, made, _up) in enumerate(sorted(self.deployments, key=lambda d: d[1],
                                                                            reverse=True))])
        if argv[:1] != ["logs"] or "--http" not in argv or "--json" not in argv:
            raise _Breach(f"the eval ran railway {argv[:2]}")
        rest = [a for a in argv[2:] if a not in ("--http", "--json")]
        opts = dict(zip(rest[::2], rest[1::2], strict=True))
        if int(opts["--lines"]) > 5000:
            raise _Breach("Error in beforeLimit - Invalid input")
        since, until = (datetime.fromisoformat(opts[k]) for k in ("--since", "--until"))
        ua = opts["--filter"].removeprefix("@clientUa:") if "--filter" in opts else None
        found = [r for r in self.rows
                 if r["deploymentId"] == argv[1] and r["method"] == opts["--method"] and r["path"] == opts["--path"]
                 and since <= datetime.fromisoformat(r["timestamp"]) <= until and ua in (None, r["clientUa"])]
        return "".join(jsonlib.dumps(r) + "\n" for r in found[-int(opts["--lines"]):])


class _Logged:
    """The API host in front of a route: each request the eval sends is logged at the eval's clock with the next of
    `durations`, and lands in the clock's events."""

    def __init__(self, route: Any, railway: _Railway, clock: _Clock, durations: list[int]) -> None:
        self.route, self.railway, self.clock, self.durations = route, railway, clock, iter(durations)

    def post(self, url: str, *, json: dict[str, Any], headers: dict[str, str], timeout: float) -> Any:
        res = self.route.post(url, json=json, headers=headers, timeout=timeout)
        if url != AUTH:
            self.clock.events.append(("post", json["query"]))
            self.railway.log(self.clock.now(), "POST", "/places/suggest", res.status_code, next(self.durations),
                             headers["User-Agent"])
        return res


def _clock(conn: psycopg.Connection) -> _Clock:
    """Three days after the transaction, so its Prague day and the next are over, DST or not."""
    return _Clock(conn.execute("SELECT now()").fetchone()[0] + timedelta(days=3))


def _run(conn: psycopg.Connection, capsys: pytest.CaptureFixture[str], http: Any, *argv: str,
         clock: _Clock | None = None, railway: Any = None) -> tuple[int, list[str]]:
    """The eval over the window of the transaction's Prague day (a later `--window-last` in `argv` wins)."""
    day = conn.execute("SELECT (now() AT TIME ZONE 'Europe/Prague')::date").fetchone()[0]
    clock = clock or _clock(conn)

    @contextlib.contextmanager
    def open_conn() -> Iterator[psycopg.Connection]:
        yield conn

    def no_cli(argv: list[str]) -> str:
        raise _Breach(f"the eval ran railway {argv[:2]} on a run that reads no log")

    io = gate.Boundaries(http=http, open_conn=open_conn, run_cli=railway or no_cli, now=clock.now, sleep=clock.sleep)
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
    assert "questions: top-5 pass · top-1 pass → pass" in out
    assert out[-1] == "verdict: questions pass · latency not measured → pass"
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


# 200 distinct digit-free texts, every one picked as Brno.
TEXTS_200 = [f"Brno {a}{b}" for a in "abcdefghijklmnopqrst" for b in "abcdefghij"]


@REQUIRED_DB
@pytest.mark.parametrize(("top5", "top1", "lines", "exit_code"), [
    (194, 180, ["after strikes · top-5: 194 of 200 (needs 194) · Wilson 93.6–98.6 %",
                "after strikes · top-1: 180 of 200 (needs 180) · Wilson 85.1–93.4 %",
                "questions: top-5 pass · top-1 pass → pass"], 0),
    (193, 180, ["after strikes · top-5: 193 of 200 (needs 194) · Wilson 93.0–98.3 %",
                "questions: top-5 fail · top-1 pass → fail"], 1),
    (194, 179, ["after strikes · top-1: 179 of 200 (needs 180) · Wilson 84.5–93.0 %",
                "questions: top-5 pass · top-1 fail → fail"], 1),
], ids=["194 top-5 and 180 top-1 pass", "193 top-5 fail", "179 top-1 fail"])
def test_at_n_200_the_gate_needs_194_top5_and_180_top1_hits(
    conn: psycopg.Connection, client: TestClient, capsys: pytest.CaptureFixture[str],
    top5: int, top1: int, lines: list[str], exit_code: int,
) -> None:
    for text in TEXTS_200:
        _resolve(client, TOWN_BRNO, text, 1)
    at = {text: 1 if n < top1 else 3 if n < top5 else 6 for n, text in enumerate(TEXTS_200)}
    code, out = _run(conn, capsys, _Canned(at), "--no-latency")
    assert "questions N = 200" in out
    for line in lines:
        assert line in out
    assert code == exit_code


def _three_picks(client: TestClient, tmp_path: Path, strikes: str) -> tuple[_Canned, str]:
    """Questions #1 to #3 (one second, one level and code: numbered by text), Mapy ranks 1, 4 and 7; own search answers
    #1 and #2 first and #3 sixth. Returns the route and the strikes file."""
    for text, rank in (("Brno a", 1), ("Brno b", 4), ("Brno c", 7)):
        _resolve(client, TOWN_BRNO, text, rank)
    path = tmp_path / "strikes.txt"
    path.write_text(strikes)
    return _Canned({"Brno a": 1, "Brno b": 1, "Brno c": 6}), str(path)


@REQUIRED_DB
def test_a_strike_lowers_n_and_is_counted(conn: psycopg.Connection, client: TestClient,
                                          capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    route, strikes = _three_picks(client, tmp_path, "# the operator's look: #3 names its okres\n3\n")
    code, out = _run(conn, capsys, route, "--no-latency", "--window-size", "3", "--strikes", strikes)
    assert "struck 1" in out
    assert "N = 3 before strikes, 2 after" in out
    assert "before strikes · top-1: 2 of 3 (needs 3) · Wilson 20.8–93.9 %" in out
    assert "before strikes · Mapy rank-based (biased upward): top-1 1 of 3, top-5 2 of 3" in out
    assert "after strikes · top-1: 2 of 2 (needs 2) · Wilson 34.2–100.0 %" in out
    assert "after strikes · Mapy rank-based (biased upward): top-1 1 of 2, top-5 2 of 2" in out
    assert "questions: top-5 pass · top-1 pass → pass" in out
    assert out[-1] == "verdict: questions pass · latency not measured → pass"
    assert code == 0


@REQUIRED_DB
@pytest.mark.parametrize(("strike", "refusal"), [
    ("1", "strike 1 is not a miss"),
    ("4", "strike 4 is not a question number"),
], ids=["a top-1 hit", "no such question"])
def test_a_strike_that_is_no_miss_stops_the_run(conn: psycopg.Connection, client: TestClient,
                                                capsys: pytest.CaptureFixture[str], tmp_path: Path, strike: str,
                                                refusal: str) -> None:
    route, strikes = _three_picks(client, tmp_path, f"{strike}\n")
    code, out = _run(conn, capsys, route, "--no-latency", "--window-size", "3", "--strikes", strikes)
    assert out[-1] == refusal
    assert code == 2


@REQUIRED_DB
def test_a_shortcut_question_is_scored_as_its_mestska_cast(conn: psycopg.Connection, client: TestClient,
                                                           capsys: pytest.CaptureFixture[str]) -> None:
    """Resolve answers a městská část's quarters and records the městská část; own search's shortcut row is the
    městská část itself: first for its own name, second after the town for the town's name."""
    _pair(conn, PRAHA_6, VOKOVICE)
    _pair(conn, BRNO_STRED, STARE_BRNO)
    _resolve(client, MOMC_BRNO_STRED, "Brno-střed", 1)
    _resolve(client, MOMC_PRAHA_6, "Praha", 2)
    code, out = _run(conn, capsys, _Route(client), "--no-latency", "--window-size", "2")
    assert out[0].endswith(" · momc pairs 2")
    assert "after strikes · top-5: 2 of 2 (needs 2) · Wilson 34.2–100.0 %" in out
    assert "after strikes · top-1: 1 of 2 (needs 2) · Wilson 9.5–90.5 %" in out
    assert code == 1


@REQUIRED_DB
def test_a_shortcut_question_with_no_quarter_pair_stops_the_run_before_any_question(
    conn: psycopg.Connection, client: TestClient, capsys: pytest.CaptureFixture[str],
) -> None:
    """With no pair at the current version own search answers no shortcut, so the question would score a miss that
    says nothing about own search (the #2450 note on #2452)."""
    _pair(conn, BRNO_STRED, STARE_BRNO)
    _resolve(client, MOMC_BRNO_STRED, "Brno-střed", 1)
    _resolve(client, MOMC_PRAHA_6, "Praha", 2)
    code, out = _run(conn, capsys, _Route(client), "--no-latency", "--window-size", "2")
    assert out[-1].startswith("momc questions with no quarter pair at the current version: 1 (pairs there: 1);")
    assert "gh workflow run location_registry_load.yml" in out[-1]
    assert code == 2


def _today(conn: psycopg.Connection) -> Any:
    return conn.execute("SELECT (now() AT TIME ZONE 'Europe/Prague')::date").fetchone()[0]


@REQUIRED_DB
def test_a_window_short_of_its_size_gives_no_verdict(conn: psycopg.Connection, client: TestClient,
                                                     capsys: pytest.CaptureFixture[str]) -> None:
    _resolve(client, TOWN_BRNO, "Brno", 1)
    code, out = _run(conn, capsys, _Route(client), "--no-latency")
    assert out[-1] == f"the window is not full: 1 of 200 questions by {_today(conn)}; score it once question 200 lands"
    assert code == 2


@REQUIRED_DB
def test_a_window_scored_past_the_day_its_last_question_landed_is_refused(
    conn: psycopg.Connection, client: TestClient, capsys: pytest.CaptureFixture[str],
) -> None:
    """The days after it belong to the next window, whose questions a score would spend (ruling 2026-11-08)."""
    _resolve(client, TOWN_BRNO, "Brno", 1)
    today = _today(conn)
    code, out = _run(conn, capsys, _Route(client), "--no-latency", "--window-size", "1",
                     "--window-last", (today + timedelta(days=1)).isoformat())
    assert out[-1] == f"question 1 of the window landed on {today}: score --window-last {today}"
    assert code == 2


def _refused_before_any_read(capsys: pytest.CaptureFixture[str], now: datetime, *argv: str) -> tuple[int, list[str]]:
    def no_conn() -> Any:
        raise AssertionError("the eval read the database")

    io = gate.Boundaries(http=None, open_conn=no_conn, run_cli=None, now=lambda: now, sleep=lambda _s: None)
    code = gate.main(["--no-latency", *argv], io)
    return code, capsys.readouterr().out.splitlines()


@pytest.mark.parametrize(("first", "last", "refusal"), [
    ("2026-11-08", "2026-11-12", "window 1 starts on 2026-11-09, the recording's first full Prague day"),
    ("2026-11-12", "2026-11-11", "--window-last 2026-11-11 is before --window-first 2026-11-12"),
    ("2026-11-12", "2026-11-20", "2026-11-20 is not over yet in Prague"),
], ids=["before the recording", "inverted", "not over"])
def test_a_window_the_gate_cannot_score_is_refused_before_any_read(capsys: pytest.CaptureFixture[str], first: str,
                                                                   last: str, refusal: str) -> None:
    code, out = _refused_before_any_read(capsys, datetime(2026, 11, 20, 22, 59, tzinfo=UTC),
                                         "--window-first", first, "--window-last", last)
    assert out == [refusal]
    assert code == 2


class _Flaky(_Canned):
    """Canned rows behind a route that answers 503 the first `fails[text]` times it is asked a text."""

    def __init__(self, at: dict[str, int], fails: dict[str, int]) -> None:
        super().__init__(at)
        self.fails = dict(fails)

    def suggest(self, body: dict[str, Any], headers: dict[str, str]) -> Any:
        if self.fails.get(body["query"], 0) > 0:
            self.fails[body["query"]] -= 1
            return _Answer(503, {"detail": {"code": "place_search_unavailable"}})
        return super().suggest(body, headers)


@REQUIRED_DB
def test_a_route_error_that_persists_is_a_miss_and_is_counted(
    conn: psycopg.Connection, client: TestClient, capsys: pytest.CaptureFixture[str],
) -> None:
    """One retry rides out a blip; a question whose answer never comes is a miss own search earned."""
    for text in ("Brno a", "Brno b", "Brno c"):
        _resolve(client, TOWN_BRNO, text, 1)
    route = _Flaky({"Brno a": 1, "Brno b": 1, "Brno c": 1}, fails={"Brno b": 1, "Brno c": 2})
    code, out = _run(conn, capsys, route, "--no-latency", "--window-size", "3")
    assert "route errors 1" in out
    assert "after strikes · top-1: 2 of 3 (needs 3) · Wilson 20.8–93.9 %" in out
    assert code == 1


@REQUIRED_DB
def test_a_pick_at_a_level_no_question_is_scored_at_stops_the_run(
    conn: psycopg.Connection, client: TestClient, capsys: pytest.CaptureFixture[str],
) -> None:
    """Resolve records no door today (the address picker sends no pick); one would otherwise score a silent miss."""
    _resolve(client, TOWN_BRNO, "Brno", 1)
    conn.execute("INSERT INTO place_picks (text, rank, level, code) VALUES ('Nad Bořislavkou', 1, 'adresni_misto',"
                 " 21000001)")
    code, out = _run(conn, capsys, _Route(client), "--no-latency", "--window-size", "1")
    assert out[-1] == "picks at a level no question is scored at: 1 (adresni_misto); look before scoring"
    assert code == 2


def _host(clock: _Clock) -> _Railway:
    """One API deployment, up for a month, that logged three suggest calls in the 7 days before the run: two answered
    in 344 and 560 ms, one the client left (499)."""
    railway = _Railway([("dep-1", clock.at - timedelta(days=30), clock.at - timedelta(days=30))])
    railway.log(clock.at - timedelta(days=3), "GET", "/maps/suggest", 200, 560)
    railway.log(clock.at - timedelta(days=2), "GET", "/maps/suggest", 499, 1200)
    railway.log(clock.at - timedelta(days=1), "GET", "/maps/suggest", 200, 344)
    return railway


@REQUIRED_DB
@pytest.mark.parametrize(("durations", "own", "latency", "exit_code"), [
    ([230, 183, 200, 186], "own · 200s n 4 · p50 186 ms · p95 230 ms · statuses 200: 4",
     "latency: own p50 186 ms ≤ Mapy p50 344 ms → not slower → pass", 0),
    ([400, 380, 390, 410], "own · 200s n 4 · p50 390 ms · p95 410 ms · statuses 200: 4",
     "latency: own p50 390 ms > Mapy p50 344 ms → slower → fail", 1),
], ids=["not slower", "slower"])
def test_the_latency_run_reads_both_medians_from_the_host_log(
    conn: psycopg.Connection, client: TestClient, capsys: pytest.CaptureFixture[str], durations: list[int], own: str,
    latency: str, exit_code: int,
) -> None:
    """Own: the four prefixes the replay of "Brno a" sends ("Brno " trims to a repeat), as the host logged them."""
    _resolve(client, TOWN_BRNO, "Brno a", 1)
    clock = _clock(conn)
    railway = _host(clock)
    route = _Logged(_Canned({"Brno a": 1}, others=2), railway, clock, durations)
    code, out = _run(conn, capsys, route, "--window-size", "1", clock=clock, railway=railway)
    assert "Mapy · 200s n 2 · p50 344 ms · p95 560 ms · aborts 1 of 3 (33.3 %) · statuses 200: 2, 499: 1" in out
    assert "own POST /places/suggest · sent 4 · log holds 4 of 4" in out
    assert own in out
    assert latency in out
    assert "after strikes · top-1: 1 of 1 (needs 1) · Wilson 20.7–100.0 %" in out
    assert code == exit_code


@REQUIRED_DB
def test_the_replay_types_one_text_at_a_time_spaced_out(
    conn: psycopg.Connection, client: TestClient, capsys: pytest.CaptureFixture[str],
) -> None:
    """The place box's keystrokes, 0.2 s apart, and 30 s between texts, so the name index is not held warm."""
    _resolve(client, TOWN_BRNO, "Brno a", 1)
    _resolve(client, TOWN_BRNO, "Lom", 1)
    clock = _clock(conn)
    railway = _host(clock)
    route = _Logged(_Canned({"Brno a": 1, "Lom": 1}, others=2), railway, clock, [200] * 6)
    code, out = _run(conn, capsys, route, "--window-size", "2", clock=clock, railway=railway)
    assert clock.events == [("post", "Br"), ("sleep", 0.2), ("post", "Brn"), ("sleep", 0.2), ("post", "Brno"),
                            ("sleep", 0.2), ("post", "Brno a"), ("sleep", 30.0), ("post", "Lo"), ("sleep", 0.2),
                            ("post", "Lom")]
    assert "own POST /places/suggest · sent 6 · log holds 6 of 6" in out
    assert code == 0


@REQUIRED_DB
def test_mapys_side_reads_every_deployment_that_can_have_served_in_its_7_days(
    conn: psycopg.Connection, client: TestClient, capsys: pytest.CaptureFixture[str],
) -> None:
    """The list gives no removal time. Y came up 39 minutes before the 7 days began; X was created 8 minutes before
    them and replaced a second later by W, which came up a minute into them, so Y served that minute; V came up
    inside them. Z, replaced by Y long before, is not read."""
    _resolve(client, TOWN_BRNO, "Brno a", 1)
    clock = _clock(conn)
    begin = clock.at - timedelta(days=7)
    railway = _Railway([
        ("dep-z", begin - timedelta(hours=3), begin - timedelta(hours=3)),
        ("dep-y", begin - timedelta(minutes=40), begin - timedelta(minutes=39)),
        ("dep-x", begin - timedelta(minutes=8), None),
        ("dep-w", begin - timedelta(minutes=8) + timedelta(seconds=1), begin + timedelta(minutes=1)),
        ("dep-v", begin + timedelta(days=2), begin + timedelta(days=2)),
    ])
    railway.log(begin - timedelta(hours=1), "GET", "/maps/suggest", 200, 900)
    railway.log(begin + timedelta(seconds=30), "GET", "/maps/suggest", 200, 344)
    railway.log(begin + timedelta(days=1), "GET", "/maps/suggest", 200, 560)
    railway.log(begin + timedelta(days=3), "GET", "/maps/suggest", 499, 1200)
    route = _Logged(_Canned({"Brno a": 1}, others=2), railway, clock, [200] * 4)
    code, out = _run(conn, capsys, route, "--window-size", "1", clock=clock, railway=railway)
    header = next(line for line in out if line.startswith("Mapy GET /maps/suggest"))
    assert header.endswith(f" · deployments read 4 · rows 3 · earliest row {_stamp(begin + timedelta(seconds=30))}")
    assert "Mapy · 200s n 2 · p50 344 ms · p95 560 ms · aborts 1 of 3 (33.3 %) · statuses 200: 2, 499: 1" in out
    assert code == 0


@REQUIRED_DB
def test_a_log_range_that_fills_a_page_is_read_in_parts(
    conn: psycopg.Connection, client: TestClient, capsys: pytest.CaptureFixture[str],
) -> None:
    """A page holds 5000 rows, the newest; the client abort is the oldest of 5001 and would fall off it."""
    _resolve(client, TOWN_BRNO, "Brno a", 1)
    clock = _clock(conn)
    begin = clock.at - timedelta(days=7)
    railway = _Railway([("dep-1", clock.at - timedelta(days=30), clock.at - timedelta(days=30))])
    railway.log(begin + timedelta(hours=1), "GET", "/maps/suggest", 499, 1200)
    for n in range(5000):
        railway.log(begin + timedelta(hours=2, minutes=n), "GET", "/maps/suggest", 200, 300)
    route = _Logged(_Canned({"Brno a": 1}, others=2), railway, clock, [200] * 4)
    code, out = _run(conn, capsys, route, "--window-size", "1", clock=clock, railway=railway)
    assert ("Mapy · 200s n 5000 · p50 300 ms · p95 300 ms · aborts 1 of 5001 (0.0 %) · statuses 200: 5000, 499: 1"
            in out)
    assert code == 0


def test_missing_credentials_are_named_before_any_read(capsys: pytest.CaptureFixture[str],
                                                       monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SUPABASE_DB_URL")
    monkeypatch.delenv("SMOKE_TEST_NONADMIN_PASSWORD")
    code, out = _refused_before_any_read(capsys, datetime(2026, 11, 20, 12, 0, tzinfo=UTC),
                                         "--window-first", "2026-11-09", "--window-last", "2026-11-12")
    assert out == ["missing in the environment: SUPABASE_DB_URL, SMOKE_TEST_NONADMIN_PASSWORD"]
    assert code == 2


def test_a_failure_prints_its_class_alone(capsys: pytest.CaptureFixture[str]) -> None:
    """A driver's message can quote a statement's parameters or a connection string; neither reaches stdout."""
    def failing() -> Any:
        raise psycopg.OperationalError("connection to server at db.invalid failed for 'Brno a' with password x")

    io = gate.Boundaries(http=None, open_conn=failing, run_cli=None,
                         now=lambda: datetime(2026, 11, 20, 12, 0, tzinfo=UTC), sleep=lambda _s: None)
    code = gate.main(["--no-latency", "--window-first", "2026-11-09", "--window-last", "2026-11-12"], io)
    assert capsys.readouterr().out.splitlines() == ["failed: OperationalError"]
    assert code == 2


def test_a_window_size_below_1_is_a_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_:
        _refused_before_any_read(capsys, datetime(2026, 11, 20, 12, 0, tzinfo=UTC), "--window-first", "2026-11-09",
                                 "--window-last", "2026-11-12", "--window-size", "0")
    assert exit_.value.code == 2
    assert "--window-size: 0 is not a positive whole number" in capsys.readouterr().err
