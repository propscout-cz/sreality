"""Place picks (spec #2441 Part 1, ruling Q10) after the switch-over (#2453): nothing records a pick any more, and the
table stays, closed to the browser, until Stage B (#2455) drops it. EXECUTED against the replayed schema (CI's
migrations lane, `TEST_DATABASE_URL`; locally `scripts/local_pg.sh test`). Every test rolls back."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import psycopg
import pytest

from location_data import place_picks
from tests._live_property import REQUIRED_DB, db_url

pytestmark = REQUIRED_DB

BRNO, PRAHA_6 = 582786, 500178


@pytest.fixture()
def conn() -> Iterator[psycopg.Connection]:
    connection = psycopg.connect(db_url(), autocommit=True, options="-c statement_timeout=20000")
    with connection, connection.transaction(force_rollback=True):
        yield connection


@pytest.mark.parametrize("role", ["anon", "authenticated"])
def test_no_browser_role_reaches_the_table(conn: psycopg.Connection, role: str) -> None:
    assert conn.execute("SELECT has_table_privilege(%s, 'public.place_picks', 'SELECT, INSERT, UPDATE, DELETE')",
                        (role,)).fetchone() == (False,)


def test_rls_is_on(conn: psycopg.Connection) -> None:
    assert conn.execute("SELECT relrowsecurity FROM pg_class WHERE oid = 'public.place_picks'::regclass"
                        ).fetchone() == (True,)


@pytest.mark.parametrize("text", ["Praha 2", "Praha ２"], ids=["ascii digit", "full-width digit"])
def test_the_table_refuses_a_text_with_a_digit(conn: psycopg.Connection, text: str) -> None:
    """The CHECK pins the ICU collation, so the full-width digit is refused on a libc-default image too."""
    with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
        conn.execute("INSERT INTO place_picks (text, rank, level, code) VALUES (%s, 1, 'momc', %s)", (text, PRAHA_6))


def test_the_reader_answers_the_picks_of_its_window_as_stored(conn: psycopg.Connection) -> None:
    """The gate's eval (#2452) reads the picks recorded before the switch-over through this reader alone."""
    conn.execute("DELETE FROM place_picks")
    now = datetime.now(UTC)
    conn.execute("INSERT INTO place_picks (created_at, text, rank, level, code) VALUES (%s, 'Brno', 2, 'obec', %s),"
                 " (%s, NULL, 1, 'momc', %s)", (now - timedelta(minutes=10), BRNO, now - timedelta(minutes=5), PRAHA_6))
    picks = place_picks.picks_between(conn, now - timedelta(hours=1), now + timedelta(hours=1))
    assert [(p.text, p.rank, p.level, p.code) for p in picks] == [("Brno", 2, "obec", BRNO), (None, 1, "momc", PRAHA_6)]
    assert place_picks.picks_between(conn, now + timedelta(hours=1), now + timedelta(hours=2)) == []
