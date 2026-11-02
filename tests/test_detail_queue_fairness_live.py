"""The starvation invariant, executed against the replayed schema.

tests/test_detail_queue.py proves the SQL SHAPE with a fake connection: which
CTEs exist, what limits are passed. A fake connection structurally cannot prove
the only thing that matters here — that a refresh backlog larger than the batch
no longer stops new listings being claimed. That is a property of how Postgres
composes two `FOR UPDATE SKIP LOCKED` CTEs under a shared limit, so it needs
real SQL against the real table.

The incident this guards (2026-08-17): `ORDER BY priority DESC, enqueued_at`
over one queue, with a refresh class that a walk re-enqueues on every pass. Over
seven days sreality completed 598,797 refresh fetches and ZERO new-listing
fetches; 15,064 listings sat discovered-but-never-ingested. The guard is
deliberately written as "flood refresh, assert acquisition still flows" rather
than as an assertion about ordering, because ordering is the implementation and
throughput-under-load is the requirement.

Gated on TEST_DATABASE_URL like the other live suites, so a normal local
`pytest` skips it. Rows are keyed on a per-test uuid source in a throwaway
container; nothing here touches production.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator

import psycopg
import pytest

from scraper import db

_DB_URL = os.environ.get("TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not _DB_URL,
    reason="TEST_DATABASE_URL not set — the live claim fairness runs in the CI DB job",
)


@pytest.fixture()
def conn() -> Iterator[psycopg.Connection]:
    with psycopg.connect(_DB_URL, autocommit=True) as c:
        yield c


@pytest.fixture()
def source(conn: psycopg.Connection) -> Iterator[str]:
    """A queue namespace unique to one test — `source` is part of the queue key."""
    name = f"test-{uuid.uuid4().hex[:12]}"
    yield name
    conn.execute("DELETE FROM listing_detail_queue WHERE source = %s", (name,))


def _seed(conn: psycopg.Connection, source: str, priority: int, count: int) -> None:
    conn.execute(
        """
        INSERT INTO listing_detail_queue (source, native_id, detail_ref, priority)
        SELECT %(source)s, %(source)s || '-' || %(priority)s || '-' || g,
               '/d/' || g, %(priority)s
        FROM generate_series(1, %(count)s) AS g
        """,
        {"source": source, "priority": priority, "count": count},
    )


def _claimed_priorities(conn: psycopg.Connection, source: str) -> dict[int, int]:
    rows = conn.execute(
        """
        SELECT priority, count(*) FROM listing_detail_queue
        WHERE source = %s AND claimed_at IS NOT NULL GROUP BY priority
        """,
        (source,),
    ).fetchall()
    return {int(p): int(n) for p, n in rows}


def test_refresh_flood_cannot_starve_new_listings(conn, source):
    """The regression itself: refresh work an order of magnitude over the batch."""
    _seed(conn, source, db.QUEUE_PRIORITY_CHANGED, 2_000)
    _seed(conn, source, db.QUEUE_PRIORITY_NEW, 500)

    claimed = db.claim_detail_batch(conn, source, 200)

    assert len(claimed) == 200
    # (native_id, detail_ref, index_price_czk, enqueued_at)
    assert all(len(row) == 4 for row in claimed)
    by_priority = _claimed_priorities(conn, source)
    assert by_priority[db.QUEUE_PRIORITY_NEW] == 100
    assert by_priority[db.QUEUE_PRIORITY_CHANGED] == 100


def test_repeated_claims_drain_the_new_backlog_not_just_one_batch(conn, source):
    """One fair batch is not enough — the backlog has to actually clear.

    Under the old ordering this loop claimed 1,000 refresh rows and zero new
    ones, indefinitely, because the walk refills refresh faster than it drains.
    """
    _seed(conn, source, db.QUEUE_PRIORITY_CHANGED, 5_000)
    _seed(conn, source, db.QUEUE_PRIORITY_NEW, 300)

    for _ in range(5):
        db.claim_detail_batch(conn, source, 200)

    assert _claimed_priorities(conn, source)[db.QUEUE_PRIORITY_NEW] == 300


def test_unused_acquisition_reserve_backfills_to_refresh(conn, source):
    """A quiet market must cost refresh nothing — no idle half-batches."""
    _seed(conn, source, db.QUEUE_PRIORITY_CHANGED, 500)
    _seed(conn, source, db.QUEUE_PRIORITY_NEW, 10)

    claimed = db.claim_detail_batch(conn, source, 200)

    assert len(claimed) == 200
    by_priority = _claimed_priorities(conn, source)
    assert by_priority[db.QUEUE_PRIORITY_NEW] == 10
    assert by_priority[db.QUEUE_PRIORITY_CHANGED] == 190


def test_refresh_keeps_its_internal_ranking(conn, source):
    """Failure-retry still outranks price-changed, and the location refetch lane
    (priority -1, migration 384) still sorts below both — the change is about
    acquisition vs refresh, not about reshuffling refresh."""
    _seed(conn, source, db.QUEUE_PRIORITY_CHANGED, 100)
    _seed(conn, source, db.QUEUE_PRIORITY_FAILURE, 60)
    _seed(conn, source, -1, 100)

    db.claim_detail_batch(conn, source, 100, acquisition_reserve=0)

    by_priority = _claimed_priorities(conn, source)
    assert by_priority[db.QUEUE_PRIORITY_FAILURE] == 60
    assert by_priority[db.QUEUE_PRIORITY_CHANGED] == 40
    assert -1 not in by_priority


def test_acquisition_is_claimed_oldest_first(conn, source):
    """Within acquisition the queue is FIFO, so a listing cannot be overtaken
    indefinitely by fresher discoveries."""
    _seed(conn, source, db.QUEUE_PRIORITY_NEW, 50)
    conn.execute(
        """
        UPDATE listing_detail_queue SET enqueued_at = now() - interval '9 days'
        WHERE source = %s AND native_id = %s
        """,
        (source, f"{source}-0-7"),
    )

    claimed = db.claim_detail_batch(conn, source, 2)

    assert f"{source}-0-7" in {nid for nid, _ref, _price, _enq in claimed}


def test_claimed_rows_are_not_reclaimed_by_a_concurrent_drain(conn, source):
    """SKIP LOCKED across two CTEs must not hand the same row to two drains."""
    _seed(conn, source, db.QUEUE_PRIORITY_NEW, 100)
    _seed(conn, source, db.QUEUE_PRIORITY_CHANGED, 100)

    first = {nid for nid, _r, _p, _e in db.claim_detail_batch(conn, source, 100)}
    second = {nid for nid, _r, _p, _e in db.claim_detail_batch(conn, source, 100)}

    assert len(first) == 100
    assert len(second) == 100
    assert not (first & second)


# --- the location-refetch re-arm (#2375), on the real ledger and the real drain writers --------


_MIN_AGE = 21600
_CAP = 500


def _seed_ad(conn: psycopg.Connection, source: str, nid: str) -> None:
    conn.execute(
        "INSERT INTO listings (source, source_id_native, source_url, raw_json, is_active) "
        "VALUES (%s, %s, %s, '{}'::jsonb, true)",
        (source, nid, f"https://x/{nid}"),
    )
    conn.execute(
        "INSERT INTO portal_raw_pages (source, source_id_native, source_url, page_kind, html, http_status, fetched_at) "
        "VALUES (%s, %s, %s, 'detail', '<html></html>', 200, now() - interval '2 days')",
        (source, nid, f"https://x/{nid}"),
    )


@pytest.fixture()
def townless_ad(conn: psycopg.Connection, source: str) -> Iterator[str]:
    """An active ad with no location row and a page two days old: what the lane nominates."""
    nid = f"{source}-ad"
    _seed_ad(conn, source, nid)
    yield nid
    conn.execute("DELETE FROM portal_raw_pages WHERE source = %s", (source,))
    conn.execute("DELETE FROM detail_queue_completions WHERE source = %s", (source,))
    conn.execute("DELETE FROM listings WHERE source = %s", (source,))


def _candidates(conn: psycopg.Connection, source: str, *, min_age: int = _MIN_AGE, cap: int = _CAP) -> list[str]:
    rows = db.location_refetch_candidates(conn, min_age_seconds=min_age, cap_per_source=cap)
    return [r["native_id"] for r in rows if r["source"] == source]


def _tick(conn: psycopg.Connection, source: str) -> int:
    """One pass of the lane: the candidates, then the enqueue."""
    rows = db.location_refetch_candidates(conn, min_age_seconds=_MIN_AGE, cap_per_source=_CAP)
    mine = [(r["native_id"], r["detail_ref"], r["price_czk"]) for r in rows if r["source"] == source]
    return db.enqueue_location_refetch(conn, source, mine)


def _fail_once(conn: psycopg.Connection, source: str, nid: str) -> None:
    claimed = db.claim_detail_batch(conn, source, 10)
    assert nid in [c[0] for c in claimed]
    db.fail_detail(conn, source, [nid], "content-free page")


def _queue_row(conn: psycopg.Connection, source: str, nid: str) -> tuple[int, bool, str | None]:
    return conn.execute(
        "SELECT attempts, given_up, last_error FROM listing_detail_queue WHERE source = %s AND native_id = %s",
        (source, nid),
    ).fetchone()


def _give_up(conn: psycopg.Connection, source: str, nid: str) -> None:
    assert _tick(conn, source) == 1
    for _ in range(db.FAILURE_GIVE_UP_THRESHOLD):
        _fail_once(conn, source, nid)


def test_a_given_up_ad_rests_a_day_through_every_tick_and_stays_recorded(conn, source, townless_ad):
    _give_up(conn, source, townless_ad)
    for _ in range(3):
        assert _tick(conn, source) == 0
    # Rule 5: tracked, not dropped. The row, its count and its error all stand.
    assert _queue_row(conn, source, townless_ad) == (db.FAILURE_GIVE_UP_THRESHOLD, True, "content-free page")
    assert db.claim_detail_batch(conn, source, 10) == []


def test_after_the_throttle_it_gets_one_more_cycle(conn, source, townless_ad):
    _give_up(conn, source, townless_ad)
    conn.execute(
        "UPDATE detail_queue_completions SET completed_at = now() - interval '24 hours 1 minute' WHERE source = %s",
        (source,),
    )
    assert _tick(conn, source) == 1
    assert _queue_row(conn, source, townless_ad)[:2] == (0, False)


def test_a_page_landing_after_the_give_up_is_new_evidence(conn, source, townless_ad):
    _give_up(conn, source, townless_ad)
    assert _candidates(conn, source, min_age=0) == []
    conn.execute(
        "UPDATE portal_raw_pages SET fetched_at = now() WHERE source = %s AND source_id_native = %s",
        (source, townless_ad),
    )
    assert _candidates(conn, source, min_age=0) == [townless_ad]


def test_a_row_still_in_its_tries_keeps_its_count_so_it_can_give_up(conn, source, townless_ad):
    assert _tick(conn, source) == 1
    _fail_once(conn, source, townless_ad)
    _fail_once(conn, source, townless_ad)
    assert _tick(conn, source) == 1
    assert _queue_row(conn, source, townless_ad)[:2] == (2, False)


def test_an_ad_at_rest_does_not_hold_the_cap_from_one_that_can_be_read(conn, source, townless_ad):
    _give_up(conn, source, townless_ad)
    other = f"{source}-other"
    _seed_ad(conn, source, other)
    # Younger than the ad at rest, which sorts first by its frozen clock without the throttle.
    conn.execute(
        "UPDATE portal_raw_pages SET fetched_at = now() - interval '1 day' WHERE source = %s AND source_id_native = %s",
        (source, other),
    )
    assert _candidates(conn, source, cap=1) == [other]

