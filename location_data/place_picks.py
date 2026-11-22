"""Place picks (spec #2441 Part 1, ruling Q10): what a place box's pick named, kept as own place search's holdout.

`record` is `/maps/resolve`'s one background INSERT (migration 737); `picks_between` is the one reader, shared by the
DB-lane test and the gate's eval (#2452). Own place search never reads the picks. A text holding any digit
(`str.isdigit`, so "Praha 2" and "Praha ２" alike) is kept as NULL; any other is kept exactly as answered.
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

import psycopg

LOG = logging.getLogger("location_data.place_picks")

_INSERT_SQL = "INSERT INTO place_picks (text, rank, level, code) VALUES (%s, %s, %s::ruian_level, %s)"
_WINDOW_SQL = """
SELECT created_at, text, rank, level::text, code
  FROM place_picks
 WHERE created_at >= %s AND created_at < %s
 ORDER BY created_at, level, code, rank, text
"""


@dataclass(frozen=True)
class StoredPick:
    created_at: datetime
    text: str | None
    rank: int
    level: str
    code: int | None


def stored_text(text: str) -> str | None:
    return None if any(ch.isdigit() for ch in text) else text


def record(open_conn: Callable[[], contextlib.AbstractContextManager[psycopg.Connection]], *, text: str, rank: int,
           level: str, code: int | None) -> None:
    """One pick, on a connection of its own. A failure logs its SQLSTATE only: the error's message can quote the row."""
    try:
        with open_conn() as conn, conn.transaction(), conn.cursor() as cur:
            cur.execute(_INSERT_SQL, (stored_text(text), rank, level, code))
    except Exception as exc:
        LOG.warning("place_picks: write failed (SQLSTATE %s)", getattr(exc, "sqlstate", None))


def picks_between(conn: psycopg.Connection, start: datetime, end: datetime) -> list[StoredPick]:
    """Every pick made in [start, end), as stored."""
    with conn.cursor() as cur:
        cur.execute(_WINDOW_SQL, (start, end))
        return [StoredPick(*row) for row in cur.fetchall()]
