"""The gate's eval (#2452; spec #2441 Part 4 (1)–(4), Q2 c): own place search scored on the recorded picks.

Reads `place_picks` only through Part 1's reader (`location_data.place_picks.picks_between`) and asks only the own
route, `POST /places/suggest`, as the non-admin smoke account; it never calls Mapy. A question is a distinct (text,
level, code); a run scores one window of whole Prague days. Stdout carries tallies only, never a pick's text.
Goes at Stage B (#2455) with its test."""

from __future__ import annotations

import argparse
import contextlib
import math
import os
import secrets
import time
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
Z = 1.959964
TIMEOUT_S = 10.0
WHOLE_TEXT_GAP_S = 1.0
# A copy of own search's liveness (location_data/place_search.py) until Stage B: a unit is live when the current
# version draws it, a street when that version holds it open.
_LIVE_SQL = """
SELECT a.level, a.code,
       CASE WHEN a.level = 'ulice' THEN EXISTS (
              SELECT 1 FROM ruian_streets s
               WHERE s.code = a.code AND s.first_version_id <= %(cur)s AND s.last_version_id >= %(cur)s
                 AND (s.valid_to IS NULL OR s.last_version_id > %(cur)s))
            ELSE EXISTS (
              SELECT 1 FROM ruian_admin_units u
               WHERE u.level = a.level::ruian_level AND u.code = a.code
                 AND %(cur)s BETWEEN u.first_version_id AND u.last_version_id) END
  FROM unnest(%(levels)s::text[], %(codes)s::bigint[]) AS a(level, code)
"""
CREDENTIALS = ("SUPABASE_DB_URL", "SMOKE_TEST_NONADMIN_EMAIL", "SMOKE_TEST_NONADMIN_PASSWORD", "VITE_SUPABASE_URL",
               "VITE_SUPABASE_ANON_KEY")


@dataclass
class Boundaries:
    """The system edges, and nothing else: tests replace these."""
    http: Any
    open_conn: Callable[[], contextlib.AbstractContextManager[psycopg.Connection]]
    now: Callable[[], datetime]
    sleep: Callable[[float], None]


def _default_boundaries() -> Boundaries:
    return Boundaries(http=requests.Session(), open_conn=lambda: contextlib.closing(db.connect_read_only()),
                      now=lambda: datetime.now(UTC), sleep=time.sleep)


@dataclass(frozen=True)
class Question:
    number: int
    text: str
    level: str
    code: int | None
    best_rank: int


@dataclass(frozen=True)
class Scored:
    question: Question
    own_rank: int | None


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m scripts.place_search_gate_eval", description=__doc__)
    p.add_argument("--window-first", type=date.fromisoformat, required=True)
    p.add_argument("--window-last", type=date.fromisoformat, required=True)
    p.add_argument("--window-size", type=int, default=200)
    p.add_argument("--no-latency", action="store_true")
    p.add_argument("--strikes", type=Path, help="the operator's struck question numbers, one per line, '#' comments")
    p.add_argument("--api", default="https://api.propscout.cz")
    return p


def _inside_checkout(path: Path) -> bool:
    return any((d / ".git").exists() for d in path.resolve().parents)


def _strikes(path: Path | None) -> set[int] | str:
    """The struck numbers, or why the file is refused. Only numbers ever go in it, so nothing it says is printed."""
    if path is None:
        return set()
    if _inside_checkout(path):
        return "strikes: the file is inside a git checkout; keep it outside, and delete it after the run"
    numbers: set[int] = set()
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        value = line.split("#", 1)[0].strip()
        if not value:
            continue
        if not value.isdecimal():
            return f"strikes: line {n} is not a question number"
        numbers.add(int(value))
    return numbers


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
    return ([Question(n, text, level, code, best[(text, level, code)])
             for n, (text, level, code) in enumerate(ordered, 1)], len(first) - len(ordered))


def _share(part: int, whole: int) -> str:
    return f"{100 * part / whole:.1f} %" if whole else "—"


def _live(conn: psycopg.Connection, window: list[StoredPick]) -> dict[tuple[str, int], bool] | None:
    """Whether the current version still holds each place the window's picks name; None with no current version."""
    row = conn.execute("SELECT id FROM registry_versions WHERE is_current").fetchone()
    if row is None:
        return None
    places = sorted({(p.level, p.code) for p in window if p.code is not None})
    rows = conn.execute(_LIVE_SQL, {"cur": row[0], "levels": [lv for lv, _ in places],
                                    "codes": [code for _, code in places]}).fetchall()
    return {(level, int(code)): bool(live) for level, code, live in rows}


def _sign_in(io: Boundaries) -> str | None:
    res = io.http.post(f"{os.environ['VITE_SUPABASE_URL'].rstrip('/')}/auth/v1/token?grant_type=password",
                       json={"email": os.environ["SMOKE_TEST_NONADMIN_EMAIL"],
                             "password": os.environ["SMOKE_TEST_NONADMIN_PASSWORD"]},
                       headers={"apikey": os.environ["VITE_SUPABASE_ANON_KEY"]}, timeout=TIMEOUT_S)
    if res.status_code != 200:
        print(f"sign-in failed: HTTP {res.status_code}")
        return None
    return str(res.json()["access_token"])


def _ask(io: Boundaries, api: str, headers: dict[str, str], text: str) -> list[tuple[str, int]] | None:
    """Own search's rows for one text, as (level, id); None when the route errs or times out."""
    try:
        res = io.http.post(f"{api}/places/suggest", json={"query": text, "limit": 10}, headers=headers,
                           timeout=TIMEOUT_S)
    except requests.RequestException:
        return None
    if res.status_code != 200:
        return None
    return [(str(row["level"]), int(row["id"])) for row in res.json()["items"]]


def _needs(percent: int, n: int) -> int:
    return -(-percent * n // 100)


def _wilson(hits: int, n: int) -> str:
    p = hits / n
    d = 1 + Z * Z / n
    centre = (p + Z * Z / (2 * n)) / d
    half = (Z / d) * math.sqrt(p * (1 - p) / n + Z * Z / (4 * n * n))
    return f"{max(0.0, centre - half) * 100:.1f}–{min(1.0, centre + half) * 100:.1f} %"


def _block(label: str, scored: list[Scored]) -> tuple[bool, bool]:
    n = len(scored)
    top5 = sum(1 for s in scored if s.own_rank is not None and s.own_rank <= 5)
    top1 = sum(1 for s in scored if s.own_rank == 1)
    needs5, needs1 = _needs(97, n), _needs(90, n)
    print(f"{label} · top-5: {top5} of {n} (needs {needs5}) · Wilson {_wilson(top5, n)}")
    print(f"{label} · top-1: {top1} of {n} (needs {needs1}) · Wilson {_wilson(top1, n)}")
    mapy1 = sum(1 for s in scored if s.question.best_rank == 1)
    mapy5 = sum(1 for s in scored if s.question.best_rank <= 5)
    print(f"{label} · Mapy rank-based (biased upward): top-1 {mapy1} of {n}, top-5 {mapy5} of {n}")
    return top5 >= needs5, top1 >= needs1


def _run(args: argparse.Namespace, io: Boundaries) -> int:
    if not args.no_latency:
        print("latency: the prefix replay and the host-log read are not built yet; run with --no-latency")
        return 2
    struck = _strikes(args.strikes)
    if isinstance(struck, str):
        print(struck)
        return 2
    start, end = _midnight(args.window_first), _midnight(args.window_last + timedelta(days=1))
    with io.open_conn() as conn, conn.transaction():
        picks = picks_between(conn, EPOCH, end)
        window = [p for p in picks if p.created_at >= start]
        live = _live(conn, window)
    if live is None:
        print("no current registry version")
        return 2
    refused = sum(1 for p in window if p.code is None)
    null_text = sum(1 for p in window if p.text is None)
    not_live = sum(1 for p in window if p.code is not None and not live[(p.level, p.code)])
    print(f"rows {len(window)} · refused {refused} · NULL text {null_text} · not live {not_live}")
    print(f"digit share {null_text} of {len(window)} ({_share(null_text, len(window))})")
    # Ruling 2026-11-08: a question seen in an earlier window is never scored again.
    earlier = {(p.text, p.level, p.code) for p in picks if p.created_at < start and p.text is not None}
    questions, asked_before = _questions(window, earlier)
    print(f"asked in an earlier window: {asked_before}")
    scorable = [q for q in questions if q.code is not None and live[(q.level, q.code)]]
    print(f"questions N = {len(scorable)}")
    if not scorable:
        print("N = 0: no interval and no verdict")
        return 2

    token = _sign_in(io)
    if token is None:
        return 2
    headers = {"Authorization": f"Bearer {token}", "User-Agent": f"propscout-gate-eval-2452/run-{secrets.token_hex(4)}"}
    answers: dict[str, list[tuple[str, int]] | None] = {}
    for text in dict.fromkeys(q.text for q in questions):
        if answers:
            io.sleep(WHOLE_TEXT_GAP_S)
        answers[text] = _ask(io, args.api, headers, text)

    scored = []
    for q in scorable:
        rows = answers[q.text] or []
        place = (q.level, q.code)
        scored.append(Scored(q, rows.index(place) + 1 if place in rows else None))
    numbers = {q.number for q in questions}
    by_number = {s.question.number: s for s in scored}
    for n in sorted(struck):
        refusal = ("is not a question number" if n not in numbers else "is not scorable" if n not in by_number
                   else "is not a miss" if by_number[n].own_rank == 1 else None)
        if refusal:
            print(f"strike {n} {refusal}")
            return 2
    kept = [s for s in scored if s.question.number not in struck]
    print(f"struck {len(struck)}")
    print(f"N = {len(scored)} before strikes, {len(kept)} after")
    _block("before strikes", scored)
    if not kept:
        print("N = 0 after strikes: no interval and no verdict")
        return 2
    pass5, pass1 = _block("after strikes", kept)
    print("Wilson: questions cluster by about four people, so this interval is too narrow")
    print("Mapy: biased upward: every pick is a row Mapy listed; a repeat keeps its best rank")
    verdict = "pass" if pass5 and pass1 else "fail"
    print(f"questions: top-5 {'pass' if pass5 else 'fail'} · top-1 {'pass' if pass1 else 'fail'} → {verdict}")
    return 0 if verdict == "pass" else 1


def main(argv: list[str] | None = None, io: Boundaries | None = None) -> int:
    args = _parser().parse_args(argv)
    return _run(args, io or _default_boundaries())


if __name__ == "__main__":
    raise SystemExit(main())
