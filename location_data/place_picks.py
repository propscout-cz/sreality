"""Place picks (spec #2441 Part 1, ruling Q10): what a place box's pick named while the box was on Mapy, kept as own
place search's holdout. Nothing records one since the switch-over (#2453), whose deploy empties the table; Stage B
(#2455) drops it. `picks_between` is the one reader, shared by the DB-lane test and the gate's eval (#2452). Own place
search never reads the picks.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import psycopg

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


def picks_between(conn: psycopg.Connection, start: datetime, end: datetime) -> list[StoredPick]:
    """Every pick made in [start, end), as stored."""
    with conn.cursor() as cur:
        cur.execute(_WINDOW_SQL, (start, end))
        return [StoredPick(*row) for row in cur.fetchall()]
