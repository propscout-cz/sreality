"""The gate's eval (#2452; spec #2441 Part 4 (1)–(4), Q2 c): own place search scored on the recorded picks.

Reads `place_picks` only through Part 1's reader (`location_data.place_picks.picks_between`) and asks only the own
route, `POST /places/suggest`, as the non-admin smoke account; it never calls Mapy. A question is a distinct (text,
level, code); a run scores one window of whole Prague days. Latency comes from the API host's HTTP log through the
pinned Railway CLI, which it only reads. Stdout carries tallies only, never a pick's text. Goes at Stage B (#2455)
with its test."""

from __future__ import annotations

import argparse
import contextlib
import json
import math
import os
import secrets
import subprocess
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from datetime import time as dt_time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import psycopg
import requests

from location_data.place_picks import StoredPick, picks_between
from scraper import db

PRAGUE = ZoneInfo("Europe/Prague")
EPOCH = datetime(2000, 1, 1, tzinfo=UTC)
# Window 1's first day: the recording's first full Prague day (#2473 live 2026-11-08 16:26Z; #2465).
FIRST_DAY = date(2026, 11, 9)
# The levels a pick's record names (api/maps.py PickedUnit): own search answers each as a row of that level and code.
LEVELS = frozenset({"kraj", "okres", "obec", "momc", "cast_obce", "ulice"})
DOOR = "adresni_misto"
Z = 1.959964
TIMEOUT_S = 10.0
WHOLE_TEXT_GAP_S = 1.0
# The place box asks from two trimmed characters (frontend/src/components/filter-controls/LocationTypeahead.tsx).
MIN_QUERY_LEN = 2
RENEW_BEFORE = timedelta(minutes=5)
RAILWAY = Path(__file__).resolve().parents[1] / ".railway" / "node_modules" / ".bin" / "railway"
# The API's project (.railway/railway.ts PROJECTS) and service (docs/architecture.md § Railway service configuration).
TARGET = ["-p", "78652c43-bb7f-401b-a3de-5f532f76f72c", "-s", "79e65a88-1629-439e-a961-a815f83539de",
          "-e", "production"]
# The most rows one `logs` call returns (5001 is refused); a range holding more comes back as its newest rows only.
LOG_LINES = 5000
# The deployment list gives no removal time. One that ran this long before the next was created surely came up, so
# every older one was gone by then; one replaced sooner may never have served, and the one before it went on serving.
SETTLE = timedelta(minutes=30)
NEVER_SERVED = frozenset({"FAILED", "SKIPPED"})
MAPY_SPAN = timedelta(days=7)
# This box's clock against the edge's stamps; the run's own agent keeps the read exact.
SKEW = timedelta(minutes=1)
POLL_S, POLL_TIMES = 30.0, 10
_CURRENT_SQL = """
SELECT v.id, v.label,
       (SELECT count(*) FROM ruian_admin_unit_relations rel
         WHERE rel.relation_type = 'momc_cast_obce' AND rel.registry_version_id = v.id)
  FROM registry_versions v
 WHERE v.is_current
"""
# A copy of own search's liveness (location_data/place_search.py) until Stage B: a unit is live when the current
# version draws it, a street when that version holds it open; a městská část answers only with quarter pairs there.
_LIVE_SQL = """
SELECT a.level, a.code,
       CASE WHEN a.level = 'ulice' THEN EXISTS (
              SELECT 1 FROM ruian_streets s
               WHERE s.code = a.code AND s.first_version_id <= %(cur)s AND s.last_version_id >= %(cur)s
                 AND (s.valid_to IS NULL OR s.last_version_id > %(cur)s))
            ELSE EXISTS (
              SELECT 1 FROM ruian_admin_units u
               WHERE u.level = a.level::ruian_level AND u.code = a.code
                 AND %(cur)s BETWEEN u.first_version_id AND u.last_version_id) END,
       a.level <> 'momc' OR EXISTS (
              SELECT 1 FROM ruian_admin_units u
                JOIN ruian_admin_unit_relations rel ON rel.from_id = u.id
               WHERE u.level = 'momc' AND u.code = a.code AND %(cur)s BETWEEN u.first_version_id AND u.last_version_id
                 AND rel.relation_type = 'momc_cast_obce' AND rel.registry_version_id = %(cur)s)
  FROM unnest(%(levels)s::text[], %(codes)s::bigint[]) AS a(level, code)
"""
CREDENTIALS = ("SUPABASE_DB_URL", "SMOKE_TEST_NONADMIN_EMAIL", "SMOKE_TEST_NONADMIN_PASSWORD", "VITE_SUPABASE_URL",
               "VITE_SUPABASE_ANON_KEY")
Row = tuple[str, int, str, str | None]


class Stop(Exception):
    """A refusal in the eval's own words, never a pick's text, so it is printed as it is."""


@dataclass
class Boundaries:
    """The system edges, and nothing else: tests replace these."""
    http: Any
    open_conn: Callable[[], contextlib.AbstractContextManager[psycopg.Connection]]
    run_cli: Callable[[list[str]], str]
    now: Callable[[], datetime]
    sleep: Callable[[float], None]


def _railway(argv: list[str]) -> str:
    done = subprocess.run([str(RAILWAY), *argv], capture_output=True, text=True, timeout=600, check=False)
    if done.returncode != 0:
        raise Stop(f"railway {argv[0]} exited {done.returncode}; check the CLI's login: railway whoami")
    return done.stdout


def _default_boundaries() -> Boundaries:
    return Boundaries(http=requests.Session(), open_conn=lambda: contextlib.closing(db.connect_read_only()),
                      run_cli=_railway, now=lambda: datetime.now(UTC), sleep=time.sleep)


@dataclass(frozen=True)
class Question:
    number: int
    text: str
    level: str
    code: int | None
    best_rank: int
    first_at: datetime


@dataclass(frozen=True)
class Scored:
    question: Question
    own_rank: int | None


def _positive(value: str) -> int:
    if not value.isdecimal() or int(value) < 1:
        raise argparse.ArgumentTypeError(f"{value} is not a positive whole number")
    return int(value)


def _aware(value: str) -> datetime:
    at = datetime.fromisoformat(value)
    if at.tzinfo is None:
        raise argparse.ArgumentTypeError(f"{value} names no time zone")
    return at


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m scripts.place_search_gate_eval", description=__doc__)
    p.add_argument("--window-first", type=date.fromisoformat, required=True)
    p.add_argument("--window-last", type=date.fromisoformat, required=True)
    p.add_argument("--window-size", type=_positive, default=200,
                   help="questions that fill a window (Q2 c: 200); a smaller size is for tests")
    p.add_argument("--no-latency", action="store_true", help="ask each text whole, once; read no log (the look's run)")
    p.add_argument("--strikes", type=Path, help="the operator's struck question numbers, one per line, '#' comments")
    p.add_argument("--addresses", type=Path,
                   help="the dictated address questions: kod_adm<TAB>the house as dictated, one per line, '#' comments")
    p.add_argument("--text-gap", type=float, default=30.0, help="seconds between texts in the prefix replay")
    p.add_argument("--keystroke-gap", type=float, default=0.2, help="seconds between a text's prefixes")
    p.add_argument("--mapy-from", type=_aware, help="start Mapy's 7 days no earlier than this (after its cache went)")
    p.add_argument("--api", default="https://api.propscout.cz")
    return p


def _inside_checkout(path: Path) -> bool:
    return any((d / ".git").exists() for d in path.resolve().parents)


def _strikes(path: Path | None) -> set[int]:
    """The struck numbers. Only numbers ever go in the file, so nothing it says is printed."""
    if path is None:
        return set()
    if _inside_checkout(path):
        raise Stop("strikes: the file is inside a git checkout; keep it outside, and delete it after the run")
    numbers: set[int] = set()
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        value = line.split("#", 1)[0].strip()
        if not value:
            continue
        if not value.isdecimal():
            raise Stop(f"strikes: line {n} is not a question number")
        numbers.add(int(value))
    return numbers


def _addresses(path: Path | None) -> list[tuple[int, str]]:
    """The dictated address questions: each house's kód ADM, which is the label (#2451: never the door's label), and
    the text as dictated."""
    if path is None:
        return []
    if _inside_checkout(path):
        raise Stop("addresses: the file is inside a git checkout; keep it outside, and delete it after the run")
    questions: list[tuple[int, str]] = []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        kod, tab, text = line.partition("\t")
        if not tab or not kod.strip().isdecimal() or not text.strip():
            raise Stop(f"addresses: line {n} is not kod_adm<TAB>text")
        questions.append((int(kod), text.strip()))
    if not questions:
        raise Stop("addresses: the file holds no question")
    return questions


def _midnight(day: date) -> datetime:
    return datetime.combine(day, dt_time.min, PRAGUE)


def _questions(window: list[StoredPick], earlier: set[tuple[str, str, int | None]]) -> tuple[list[Question], int]:
    """The window's distinct (text, level, code) with a text and not in `earlier`, numbered by first appearance (then
    level, code, text), an order stable across runs, so a strike is a number; a repeat keeps its best Mapy rank. Also
    how many were left out as earlier."""
    first: dict[tuple[str, str, int | None], datetime] = {}
    best: dict[tuple[str, str, int | None], int] = {}
    for pick in window:
        if pick.text is None:
            continue
        key = (pick.text, pick.level, pick.code)
        first.setdefault(key, pick.created_at)
        best[key] = min(best.get(key, pick.rank), pick.rank)
    ordered = sorted((k for k in first if k not in earlier),
                     key=lambda k: (first[k], k[1], k[2] is None, k[2] or 0, k[0]))
    return ([Question(n, text, level, code, best[(text, level, code)], first[(text, level, code)])
             for n, (text, level, code) in enumerate(ordered, 1)], len(first) - len(ordered))


def _share(part: int, whole: int) -> str:
    return f"{100 * part / whole:.1f} %" if whole else "—"


@dataclass(frozen=True)
class Registry:
    version: int
    label: str
    pairs: int
    live: dict[tuple[str, int], bool]
    answerable: dict[tuple[str, int], bool]


def _registry(conn: psycopg.Connection, window: list[StoredPick]) -> Registry | None:
    """The current version, its quarter pairs, and per place the window's picks name: whether that version still holds
    it, and whether own search can answer it there (a městská část only with a pair). None with no current version."""
    row = conn.execute(_CURRENT_SQL).fetchone()
    if row is None:
        return None
    version, label, pairs = row
    places = sorted({(p.level, p.code) for p in window if p.code is not None})
    rows = conn.execute(_LIVE_SQL, {"cur": version, "levels": [lv for lv, _ in places],
                                    "codes": [code for _, code in places]}).fetchall()
    return Registry(int(version), str(label), int(pairs),
                    {(level, int(code)): bool(live) for level, code, live, _ in rows},
                    {(level, int(code)): bool(answerable) for level, code, _, answerable in rows})


def _vintage(label: str) -> str:
    """The load's vintage from a registry label such as 'ruian:2026-10-31'."""
    return label.removeprefix("ruian:").replace("-", "") if label.startswith("ruian:") else "<the current vintage>"


class Route:
    """Own search as the smoke account, one request at a time: the token renewed before it lapses, a 401 answered by
    one fresh sign-in, and every request sent counted, since the host logs each."""

    def __init__(self, io: Boundaries, api: str, ua: str) -> None:
        self.io, self.api, self.ua = io, api, ua
        self.token, self.expires, self.sent = "", EPOCH, 0

    def sign_in(self) -> None:
        res = self.io.http.post(f"{os.environ['VITE_SUPABASE_URL'].rstrip('/')}/auth/v1/token?grant_type=password",
                                json={"email": os.environ["SMOKE_TEST_NONADMIN_EMAIL"],
                                      "password": os.environ["SMOKE_TEST_NONADMIN_PASSWORD"]},
                                headers={"apikey": os.environ["VITE_SUPABASE_ANON_KEY"]}, timeout=TIMEOUT_S)
        if res.status_code != 200:
            raise Stop(f"sign-in failed: HTTP {res.status_code}")
        body = res.json()
        self.token = str(body["access_token"])
        self.expires = self.io.now() + timedelta(seconds=int(body["expires_in"]))

    def ask(self, text: str) -> list[Row] | None:
        """Own search's rows for one text; None when the route errs or times out."""
        if self.io.now() >= self.expires - RENEW_BEFORE:
            self.sign_in()
        for fresh in (False, True):
            self.sent += 1
            try:
                res = self.io.http.post(f"{self.api}/places/suggest", json={"query": text, "limit": 10},
                                        headers={"Authorization": f"Bearer {self.token}", "User-Agent": self.ua},
                                        timeout=TIMEOUT_S)
            except requests.RequestException:
                return None
            if res.status_code != 401:
                break
            if fresh:
                raise Stop("the route refused the smoke account's token after a fresh sign-in")
            self.sign_in()
        if res.status_code != 200:
            return None
        return [(str(r["level"]), int(r["id"]), str(r["name"]), r.get("context")) for r in res.json()["items"]]


def _prefixes(text: str) -> list[str]:
    """What the place box asks while the text is typed: each new trimmed prefix of two characters or more, ending on
    the text as stored."""
    asked: list[str] = []
    for k in range(MIN_QUERY_LEN, len(text) + 1):
        prefix = text[:k].strip()
        if len(prefix) >= MIN_QUERY_LEN and prefix not in asked[-1:]:
            asked.append(prefix)
    return asked if asked[-1:] == [text] else [*asked, text]


def _whole(text: str) -> list[str]:
    return [text]


def _replay(route: Route, texts: list[str], gap: float, keystroke: float,
            typed: Callable[[str], list[str]]) -> dict[str, list[Row] | None]:
    """One text at a time, `gap` between texts and `keystroke` between a text's prefixes. The last prefix's answer is
    the text's; when it fails it is asked once more after the gap."""
    answers: dict[str, list[Row] | None] = {}
    for text in texts:
        if answers:
            route.io.sleep(gap)
        answer = None
        for n, prefix in enumerate(typed(text)):
            if n:
                route.io.sleep(keystroke)
            answer = route.ask(prefix)
        if answer is None:
            route.io.sleep(gap)
            answer = route.ask(text)
        answers[text] = answer
    return answers


def _instant(stamp: str) -> datetime:
    """A Railway stamp (UTC, `Z`, up to nanoseconds) as an aware datetime, cut to the microsecond."""
    head, _, fraction = stamp.removesuffix("Z").partition(".")
    return datetime.fromisoformat(f"{head}.{(fraction or '0')[:6]}+00:00")


@dataclass(frozen=True)
class Deployment:
    id: str
    status: str
    created: datetime


def _deployments(io: Boundaries) -> list[Deployment]:
    listed = json.loads(io.run_cli(["deployment", "list", *TARGET, "--limit", "1000", "--json"]))
    return sorted((Deployment(str(d["id"]), str(d["status"]), _instant(d["createdAt"])) for d in listed),
                  key=lambda d: d.created)


def _serving(deployments: list[Deployment], start: datetime, end: datetime) -> list[Deployment]:
    """Every deployment that can have served inside [start, end]: those created by `end`, back to the newest created
    before `start` that ran SETTLE unreplaced."""
    made = [d for d in deployments if d.created <= end]
    for i in range(len(made) - 1, -1, -1):
        d = made[i]
        replaced = made[i + 1].created if i + 1 < len(made) else end
        if d.created <= start and d.status not in NEVER_SERVED and min(replaced, start) - d.created >= SETTLE:
            return made[i:]
    return made


def _cli_time(at: datetime) -> str:
    return f"{at.astimezone(UTC):%Y-%m-%dT%H:%M:%SZ}"


def _page(io: Boundaries, deployment: str, method: str, path: str, since: datetime, until: datetime,
          ua: str | None) -> list[dict[str, Any]]:
    """One deployment's rows in [since, until] (whole seconds); a range that fills a page is read again in halves."""
    argv = ["logs", deployment, "--http", "--json", *TARGET, "--method", method, "--path", path,
            "--since", _cli_time(since), "--until", _cli_time(until), "--lines", str(LOG_LINES)]
    if ua is not None:
        argv += ["--filter", f"@clientUa:{ua}"]
    rows = [json.loads(line) for line in io.run_cli(argv).splitlines() if line.strip()]
    if len(rows) < LOG_LINES:
        return rows
    half = timedelta(seconds=int((until - since).total_seconds()) // 2)
    if half < timedelta(seconds=1):
        raise Stop(f"one second of the {method} {path} log holds {LOG_LINES} rows; read it by hand")
    return (_page(io, deployment, method, path, since, since + half, ua)
            + _page(io, deployment, method, path, since + half, until, ua))


def _log(io: Boundaries, serving: list[Deployment], method: str, path: str, start: datetime, end: datetime,
         ua: str | None = None) -> list[dict[str, Any]]:
    """The rows for one method and path stamped in [start, end), from each deployment in `serving`; a row read twice
    (on a split's edge) counts once."""
    since = start.replace(microsecond=0)
    until = end.replace(microsecond=0) + (timedelta(seconds=1) if end.microsecond else timedelta())
    rows: dict[str, dict[str, Any]] = {}
    for d in serving:
        for row in _page(io, d.id, method, path, since, until, ua):
            if (row["method"] == method and row["path"] == path and ua in (None, row["clientUa"])
                    and start <= _instant(row["timestamp"]) < end):
                rows[row["requestId"]] = row
    return sorted(rows.values(), key=lambda r: _instant(r["timestamp"]))


def _minute(at: datetime) -> str:
    return f"{at.astimezone(UTC):%Y-%m-%d %H:%M}Z"


class _Out:
    """Stdout, kept: the tally block the report repeats."""

    def __init__(self) -> None:
        self.lines: list[str] = []

    def __call__(self, line: str) -> None:
        print(line)
        self.lines.append(line)


def _figures(say: _Out, label: str, rows: list[dict[str, Any]], extra: str = "") -> int | None:
    """n, p50 and p95 (nearest rank) of the 200s and the rows by status; returns the p50, None with no 200."""
    ok = sorted(int(r["totalDuration"]) for r in rows if r["httpStatus"] == 200)
    statuses = Counter(int(r["httpStatus"]) for r in rows)
    shown = ", ".join(f"{status}: {statuses[status]}" for status in sorted(statuses)) or "—"
    if not ok:
        say(f"{label} · 200s n 0{extra} · statuses {shown}")
        return None
    p50, p95 = ok[_needs(50, len(ok)) - 1], ok[_needs(95, len(ok)) - 1]
    say(f"{label} · 200s n {len(ok)} · p50 {p50} ms · p95 {p95} ms{extra} · statuses {shown}")
    return p50


def _mapy(io: Boundaries, say: _Out, mapy_from: datetime | None) -> int | None:
    """Mapy's side, read before the replay: its suggest calls over the 7 days to now, every client's."""
    end = io.now()
    start = max(end - MAPY_SPAN, mapy_from) if mapy_from else end - MAPY_SPAN
    serving = _serving(_deployments(io), start, end)
    rows = _log(io, serving, "GET", "/maps/suggest", start, end)
    say(f"Mapy GET /maps/suggest {_minute(start)} to {_minute(end)} · deployments read {len(serving)} · rows"
        f" {len(rows)} · earliest row {rows[0]['timestamp'] if rows else '—'}")
    aborts = sum(1 for r in rows if r["httpStatus"] == 499)
    p50 = _figures(say, "Mapy", rows, f" · aborts {aborts} of {len(rows)} ({_share(aborts, len(rows))})")
    say("Mapy: its 300 s cache's hits are in these figures, unless --mapy-from starts after the cache went")
    return p50


def _own(io: Boundaries, say: _Out, ua: str, sent: int, run_start: datetime,
         run_end: datetime) -> tuple[int | None, bool]:
    """Own side: the replay's requests, found by the run's agent; the log is read again every 30 s, up to 5 minutes,
    until it holds every request sent. Returns the p50 and whether it held them all."""
    start, end = run_start - SKEW, run_end + SKEW
    deployments = _deployments(io)
    serving = _serving(deployments, start, end)
    for n in range(POLL_TIMES + 1):
        rows = _log(io, serving, "POST", "/places/suggest", start, end, ua)
        if len(rows) >= sent or n == POLL_TIMES:
            break
        io.sleep(POLL_S)
    say(f"own POST /places/suggest · sent {sent} · log holds {len(rows)} of {sent}")
    if any(run_start <= d.created <= run_end for d in deployments):
        say("own: a deployment was created during the replay; its cold start skews the p50: run again with no merge")
    return _figures(say, "own", rows), len(rows) >= sent


def _needs(percent: int, n: int) -> int:
    return -(-percent * n // 100)


def _wilson(hits: int, n: int) -> str:
    p = hits / n
    d = 1 + Z * Z / n
    centre = (p + Z * Z / (2 * n)) / d
    half = (Z / d) * math.sqrt(p * (1 - p) / n + Z * Z / (4 * n * n))
    return f"{max(0.0, centre - half) * 100:.1f}–{min(1.0, centre + half) * 100:.1f} %"


def _block(say: _Out, label: str, scored: list[Scored]) -> tuple[bool, bool]:
    n = len(scored)
    top5 = sum(1 for s in scored if s.own_rank is not None and s.own_rank <= 5)
    top1 = sum(1 for s in scored if s.own_rank == 1)
    needs5, needs1 = _needs(97, n), _needs(90, n)
    say(f"{label} · top-5: {top5} of {n} (needs {needs5}) · Wilson {_wilson(top5, n)}")
    say(f"{label} · top-1: {top1} of {n} (needs {needs1}) · Wilson {_wilson(top1, n)}")
    mapy1 = sum(1 for s in scored if s.question.best_rank == 1)
    mapy5 = sum(1 for s in scored if s.question.best_rank <= 5)
    say(f"{label} · Mapy rank-based (biased upward): top-1 {mapy1} of {n}, top-5 {mapy5} of {n}")
    return top5 >= needs5, top1 >= needs1


def _rank(rows: list[Row] | None, place: tuple[str, int]) -> int | None:
    found = [n for n, row in enumerate(rows or [], 1) if (row[0], row[1]) == place]
    return found[0] if found else None


def _score_addresses(say: _Out, addresses: list[tuple[int, str]], answers: dict[str, list[Row] | None]) -> str:
    """Q2 c's thresholds over the dictated questions: a hit is the house's door row; no strike, no Mapy figure."""
    ranks = [_rank(answers[text], (DOOR, kod)) for kod, text in addresses]
    n = len(ranks)
    top5 = sum(1 for r in ranks if r is not None and r <= 5)
    top1 = sum(1 for r in ranks if r == 1)
    needs5, needs1 = _needs(97, n), _needs(90, n)
    say(f"addresses N = {n} · route errors {sum(1 for _, text in addresses if answers[text] is None)}")
    say(f"addresses · top-5: {top5} of {n} (needs {needs5})")
    say(f"addresses · top-1: {top1} of {n} (needs {needs1})")
    verdict = "pass" if top5 >= needs5 and top1 >= needs1 else "fail"
    say(f"addresses: top-5 {'pass' if top5 >= needs5 else 'fail'} · top-1 {'pass' if top1 >= needs1 else 'fail'}"
        f" → {verdict}")
    return verdict


def _run(args: argparse.Namespace, io: Boundaries) -> int:
    say = _Out()
    missing = [name for name in CREDENTIALS if not os.environ.get(name)]
    if missing:
        raise Stop(f"missing in the environment: {', '.join(missing)}")
    start, end = _midnight(args.window_first), _midnight(args.window_last + timedelta(days=1))
    if args.window_first < FIRST_DAY:
        raise Stop(f"window 1 starts on {FIRST_DAY}, the recording's first full Prague day")
    if args.window_last < args.window_first:
        raise Stop(f"--window-last {args.window_last} is before --window-first {args.window_first}")
    if end > io.now():
        raise Stop(f"{args.window_last} is not over yet in Prague")
    struck = _strikes(args.strikes)
    addresses = _addresses(args.addresses)
    with io.open_conn() as conn, conn.transaction():
        picks = picks_between(conn, EPOCH, end)
        window = [p for p in picks if p.created_at >= start]
        registry = _registry(conn, window)
    if registry is None:
        raise Stop("no current registry version")
    live = registry.live
    say(f"window {args.window_first} to {args.window_last} · registry {registry.label} (version {registry.version})"
        f" · momc pairs {registry.pairs}")
    refused = sum(1 for p in window if p.code is None)
    null_text = sum(1 for p in window if p.text is None)
    not_live = sum(1 for p in window if p.code is not None and not live[(p.level, p.code)])
    say(f"rows {len(window)} · refused {refused} · NULL text {null_text} · not live {not_live}")
    say(f"digit share {null_text} of {len(window)} ({_share(null_text, len(window))})")
    others = sorted({p.level for p in window} - LEVELS)
    if others:
        raise Stop(f"picks at a level no question is scored at: {sum(1 for p in window if p.level not in LEVELS)}"
                   f" ({', '.join(others)}); look before scoring")
    # Ruling 2026-11-08: a question seen in an earlier window is never scored again.
    earlier = {(p.text, p.level, p.code) for p in picks if p.created_at < start and p.text is not None}
    questions, asked_before = _questions(window, earlier)
    say(f"asked in an earlier window: {asked_before}")
    scorable = [q for q in questions if q.code is not None and live[(q.level, q.code)]]
    say(f"questions N = {len(scorable)}")
    if not scorable:
        raise Stop("N = 0: no interval and no verdict")
    # A window runs to the day its last question lands, and no further: later days are the next window's.
    size = args.window_size
    if len(scorable) < size:
        raise Stop(f"the window is not full: {len(scorable)} of {size} questions by {args.window_last}; score it"
                   f" once question {size} lands")
    landed = scorable[size - 1].first_at.astimezone(PRAGUE).date()
    if landed != args.window_last:
        raise Stop(f"question {size} of the window landed on {landed}: score --window-last {landed}")
    unanswerable = [q for q in scorable if not registry.answerable[(q.level, q.code)]]
    if unanswerable:
        raise Stop(f"momc questions with no quarter pair at the current version: {len(unanswerable)} (pairs there:"
                   f" {registry.pairs}); first re-dispatch the load's finishing step, then confirm the pairs:"
                   f" gh workflow run location_registry_load.yml -f vintage={_vintage(registry.label)}")
    numbers, scorable_numbers = {q.number for q in questions}, {q.number for q in scorable}
    for n in sorted(struck):
        if n not in numbers or n not in scorable_numbers:
            raise Stop(f"strike {n} {'is not a question number' if n not in numbers else 'is not scorable'}")

    ua = f"propscout-gate-eval-2452/run-{secrets.token_hex(4)}"
    route = Route(io, args.api, ua)
    route.sign_in()
    texts = list(dict.fromkeys(q.text for q in questions))
    if args.no_latency:
        answers = _replay(route, texts, WHOLE_TEXT_GAP_S, 0.0, _whole)
    else:
        mapy_p50 = _mapy(io, say, args.mapy_from)
        run_start = io.now()
        answers = _replay(route, texts, args.text_gap, args.keystroke_gap, _prefixes)
        run_end, sent = io.now(), route.sent
    if addresses:
        route.ua = f"{ua}-addresses"
        doors = _replay(route, list(dict.fromkeys(text for _, text in addresses)), WHOLE_TEXT_GAP_S, 0.0, _whole)

    scored = [Scored(q, _rank(answers[q.text], (q.level, q.code))) for q in scorable]
    for s in scored:
        if s.question.number in struck and s.own_rank == 1:
            raise Stop(f"strike {s.question.number} is not a miss")
    kept = [s for s in scored if s.question.number not in struck]
    say(f"route errors {sum(1 for q in scorable if answers[q.text] is None)}")
    say(f"struck {len(struck)}")
    say(f"N = {len(scored)} before strikes, {len(kept)} after")
    _block(say, "before strikes", scored)
    if not kept:
        raise Stop("N = 0 after strikes: no interval and no verdict")
    pass5, pass1 = _block(say, "after strikes", kept)
    say("Wilson: questions cluster by about four people, so this interval is too narrow")
    say("Mapy: biased upward: every pick is a row Mapy listed; a repeat keeps its best rank")
    parts = {"questions": "pass" if pass5 and pass1 else "fail", "latency": "not measured",
             "addresses": "not asked"}
    say(f"questions: top-5 {'pass' if pass5 else 'fail'} · top-1 {'pass' if pass1 else 'fail'} → {parts['questions']}")

    if not args.no_latency:
        own_p50, complete = _own(io, say, ua, sent, run_start, run_end)
        if own_p50 is None or mapy_p50 is None or not complete:
            parts["latency"] = "incomplete"
            say("latency: incomplete: no verdict without both medians and every request sent in the log")
        else:
            parts["latency"] = "pass" if own_p50 <= mapy_p50 else "fail"
            say(f"latency: own p50 {own_p50} ms {'≤' if own_p50 <= mapy_p50 else '>'} Mapy p50 {mapy_p50} ms →"
                f" {'not slower' if own_p50 <= mapy_p50 else 'slower'} → {parts['latency']}")
    if addresses:
        parts["addresses"] = _score_addresses(say, addresses, doors)
    measured = [v for v in parts.values() if v not in ("not measured", "not asked")]
    overall = "fail" if "fail" in measured else "incomplete" if "incomplete" in measured else "pass"
    say(f"verdict: {' · '.join(f'{part} {value}' for part, value in parts.items())} → {overall}")
    return {"pass": 0, "fail": 1, "incomplete": 2}[overall]


def main(argv: list[str] | None = None, io: Boundaries | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        return _run(args, io or _default_boundaries())
    except Stop as stop:
        print(stop)
        return 2
    except Exception as exc:
        # The class alone: a driver's or an HTTP client's message can quote a pick's text or a credential.
        print(f"failed: {type(exc).__name__}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
