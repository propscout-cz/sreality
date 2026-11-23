"""Who brings a given-up queue row back (#1851), executed against the replayed schema.

The walk's re-arm exists so an ACTIVE listing whose page erred five times keeps a path to its
page check. Reset source-wide, it also looped an ad we hold nothing for: sreality 615387212 cost
1,035 failed fetches in 11 days. Which rows an UPDATE reaches, through a join to `listings`,
is what these execute; the status a row failed with plays no part, so no test here names one as
a verdict.

Gated on TEST_DATABASE_URL like the other live suites; rows sit under a per-test uuid source.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from typing import Any

import psycopg
import pytest
from psycopg.rows import dict_row

from scraper import db, delist_policy, portal_runner

_DB_URL = os.environ.get("TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not _DB_URL,
    reason="TEST_DATABASE_URL not set — the live re-arm runs in the CI DB job",
)

_ERR = "fetch: 409 Client Error: Conflict for url: https://example.test/1"


@pytest.fixture()
def conn() -> Iterator[psycopg.Connection]:
    with psycopg.connect(_DB_URL, autocommit=True) as c:
        yield c


@pytest.fixture()
def source(conn: psycopg.Connection) -> Iterator[str]:
    name = f"test-{uuid.uuid4().hex[:12]}"
    yield name
    conn.execute("DELETE FROM listing_detail_queue WHERE source = %s", (name,))
    conn.execute("DELETE FROM detail_queue_completions WHERE source = %s", (name,))
    conn.execute("DELETE FROM listings WHERE source = %s", (name,))


def _give_up(conn: psycopg.Connection, source: str, native: str, *, aged: bool = False) -> None:
    """Queue an ad and fail it to the give-up threshold; `aged` = it last came back long ago."""
    db.enqueue_detail(conn, source, [(native, None, None, db.QUEUE_PRIORITY_NEW)])
    for _ in range(db.FAILURE_GIVE_UP_THRESHOLD):
        db.fail_detail(conn, source, [native], _ERR)
    if aged:
        conn.execute(
            "UPDATE listing_detail_queue SET enqueued_at = now() - %s "
            "WHERE source = %s AND native_id = %s",
            (delist_policy.RECHECK_THROTTLE * 2, source, native),
        )


def _listing(conn: psycopg.Connection, source: str, native: str, *, active: bool) -> None:
    conn.execute(
        "INSERT INTO listings (source, source_id_native, raw_json, is_active, last_seen_at) "
        "VALUES (%s, %s, '{}'::jsonb, %s, now())",
        (source, native, active),
    )


def _row(conn: psycopg.Connection, source: str, native: str) -> dict[str, Any]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT given_up, attempts, last_error, priority, "
            "       enqueued_at > now() - interval '1 minute' AS fresh "
            "FROM listing_detail_queue WHERE source = %s AND native_id = %s",
            (source, native),
        )
        row = cur.fetchone()
    assert row is not None, "the failed row must stay recorded (rule #5)"
    return row


def _walk_rearm(conn: psycopg.Connection, source: str) -> None:
    """One nominating walk: a candidate of its own, so the re-arm runs."""
    db.enqueue_presence_checks(
        conn, source, "byt", "prodej", [("candidate", None, None)], active_rows=10)


def test_the_walk_leaves_a_given_up_ad_we_hold_no_listing_for(
    conn: psycopg.Connection, source: str,
) -> None:
    _give_up(conn, source, "poison", aged=True)
    for _ in range(3):
        _walk_rearm(conn, source)
    row = _row(conn, source, "poison")
    assert row["given_up"] is True
    assert row["attempts"] == db.FAILURE_GIVE_UP_THRESHOLD
    assert row["last_error"] == _ERR


def test_the_walk_still_brings_an_active_listing_back_for_its_page_check(
    conn: psycopg.Connection, source: str,
) -> None:
    """Whatever the page answered: an active ad without this path would stay active forever."""
    _listing(conn, source, "held", active=True)
    _give_up(conn, source, "held", aged=True)
    _walk_rearm(conn, source)
    row = _row(conn, source, "held")
    assert row["given_up"] is False
    assert row["attempts"] == 0
    assert row["fresh"] is True
    assert row["priority"] == db.QUEUE_PRIORITY_VERIFY


def test_the_walk_leaves_an_inactive_listing_it_has_nothing_to_verify_on(
    conn: psycopg.Connection, source: str,
) -> None:
    _listing(conn, source, "closed", active=False)
    _give_up(conn, source, "closed", aged=True)
    _walk_rearm(conn, source)
    assert _row(conn, source, "closed")["given_up"] is True


def test_a_full_batch_of_unheld_rows_does_not_starve_an_active_listing(
    conn: psycopg.Connection, source: str,
) -> None:
    """The batch is LIMITed oldest first, so the filter must sit inside it."""
    for i in range(db.PRESENCE_REARM_PER_WALK + 1):
        _give_up(conn, source, f"unheld-{i}", aged=True)
    _listing(conn, source, "held", active=True)
    _give_up(conn, source, "held")
    _walk_rearm(conn, source)
    assert _row(conn, source, "held")["given_up"] is False


def test_a_sighting_brings_an_unheld_given_up_ad_back_for_the_drain(
    conn: psycopg.Connection, source: str,
) -> None:
    _give_up(conn, source, "still-listed", aged=True)
    assert db.claimable_counts(conn, source).get(source, 0) == 0
    portal_runner.reconcile_sightings(
        conn, source, {"still-listed": (None, None)}, min_change_pct=0.0)
    row = _row(conn, source, "still-listed")
    assert row["given_up"] is False
    assert row["attempts"] == 0
    assert row["fresh"] is True
    assert row["priority"] == db.QUEUE_PRIORITY_NEW
    assert db.claimable_counts(conn, source).get(source, 0) == 1


def test_an_ad_the_index_keeps_listing_comes_back_at_most_once_per_throttle(
    conn: psycopg.Connection, source: str,
) -> None:
    """Sighted every walk and probe: without the bound the loop would only change its clock."""
    _give_up(conn, source, "keeps-failing", aged=True)
    sighting = {"keeps-failing": (None, None)}
    portal_runner.reconcile_sightings(conn, source, sighting, min_change_pct=0.0)
    for _ in range(db.FAILURE_GIVE_UP_THRESHOLD):
        db.fail_detail(conn, source, ["keeps-failing"], _ERR)
    portal_runner.reconcile_sightings(conn, source, sighting, min_change_pct=0.0)
    assert _row(conn, source, "keeps-failing")["given_up"] is True


def test_rearm_sighted_touches_only_given_up_rows(conn: psycopg.Connection, source: str) -> None:
    db.enqueue_detail(conn, source, [("waiting", None, None, db.QUEUE_PRIORITY_NEW)])
    db.fail_detail(conn, source, ["waiting"], _ERR)
    conn.execute(
        "UPDATE listing_detail_queue SET enqueued_at = now() - %s WHERE source = %s",
        (delist_policy.RECHECK_THROTTLE * 2, source),
    )
    assert db.rearm_sighted_given_up(conn, source, ["waiting", "unknown"]) == 0
    row = _row(conn, source, "waiting")
    assert row["attempts"] == 1
    assert row["fresh"] is False
