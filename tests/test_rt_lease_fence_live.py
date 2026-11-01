"""E941, EXECUTED: the autodedup lane's lease fence reads the CLOCK.

Session A takes the lane's lease and opens a transaction; session B — the worker's shutdown
path, or a `release_lease=` dispatch — releases A's lease and commits; A's fence
(`rt_lease.hold`, `job_lease.fence`) must then find no live row and raise. Against `now()` —
A's transaction START, earlier than B's release — the row would still read live and A would
commit beside the next holder: the fake has one clock and cannot tell the two apart, so this
runs on Postgres. And a release that comes AFTER A's fence waits for A's commit (the row lock).

E948, EXECUTED too: `job_lease.end_predecessor` ends a dead predecessor's live lease (this
hostname and pid, a second before this boot) and nothing else, its read-only twin
`job_lease.predecessor` names the same row and ends nothing, and the cast of either never
reaches a holder of another shape, which the fake can only model.

#2109, EXECUTED: the lease is a `scheduled_jobs` row, an ended one keeps who held it (the Health
cell's clock), and the job runner's pause reaches it.

Both run in CI's migrations job (`TEST_DATABASE_URL`), each test on a lease name of its own,
deleted after.
"""

from __future__ import annotations

import os
import threading
import time
import uuid
from typing import Any, Iterator

import pytest

from autodedup import rt_lease

_DB_URL = os.environ.get("TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not _DB_URL,
    reason="TEST_DATABASE_URL not set — schema-replay test runs only in the CI DB job",
)


def _session() -> Any:
    import psycopg

    return psycopg.connect(_DB_URL, autocommit=True,
                           options="-c statement_timeout=20000 -c lock_timeout=5000")


@pytest.fixture()
def sessions(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[Any, Any]]:
    """Two sessions over a lease row of this test's own, so the lane's row is never touched."""
    name = f"e941-fence-test:{uuid.uuid4()}"
    monkeypatch.setattr(rt_lease, "NAME", name)
    a, b = _session(), _session()
    try:
        yield a, b
    finally:
        a.close()
        b.close()
        with _session() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM scheduled_jobs WHERE job_name = %s", (name,))


def test_a_lease_released_mid_transaction_fails_the_fence(sessions: tuple[Any, Any]) -> None:
    a, b = sessions
    assert rt_lease.take(a, "A", 600)
    with a.transaction():
        with a.cursor() as cur:
            cur.execute("SELECT now()")          # A's transaction has started: now() is fixed
        time.sleep(0.05)
        rt_lease.release(b, "A")                 # committed by B while A's transaction is open
        time.sleep(0.05)
        with pytest.raises(rt_lease.LeaseLost):
            rt_lease.hold(a, "A")
    assert rt_lease.current(a)["live"] is False


def test_a_live_lease_passes_the_fence_and_holds_a_later_release(
        sessions: tuple[Any, Any]) -> None:
    a, b = sessions
    assert rt_lease.take(a, "A", 600)
    released = threading.Event()

    def release() -> None:
        rt_lease.release(b, "A")
        released.set()

    with a.transaction():
        rt_lease.hold(a, "A")                    # live: passes, and locks the row until commit
        thread = threading.Thread(target=release)
        thread.start()
        assert not released.wait(0.5), "the release waits for A's commit"
    thread.join(5)
    assert released.is_set()
    assert rt_lease.current(a)["live"] is False


def test_a_dead_predecessors_live_lease_is_released_once(sessions: tuple[Any, Any]) -> None:
    a, _b = sessions
    assert rt_lease.take(a, "host-a:1:100", 600)
    assert rt_lease.predecessor(a, "host-a", 1, 200) == "host-a:1:100"
    assert rt_lease.current(a)["live"] is True, "the read twin ends nothing"
    assert rt_lease.release_predecessor(a, "host-a", 1, 200) == "host-a:1:100"
    assert rt_lease.current(a)["live"] is False
    assert rt_lease.release_predecessor(a, "host-a", 1, 200) is None, "an ended lease is no death"
    assert rt_lease.predecessor(a, "host-a", 1, 200) is None
    assert rt_lease.take(a, "host-a:1:250", 600), "the lane is free at once"


@pytest.mark.parametrize("holder", [
    "host-a:1:200", "host-a:1:300", "host-a:7:100", "host-b:1:100", "rt_seed:host-a:1:100",
    "dispatch:gh-123", "host-a:1:abc", "host-a:1:100:7",
    "host-a:123456789012345678901:100",
])
def test_no_other_lease_is_named_or_released_and_no_cast_fails(sessions: tuple[Any, Any],
                                                               holder: str) -> None:
    a, _b = sessions
    assert rt_lease.take(a, holder, 600)
    assert rt_lease.predecessor(a, "host-a", 1, 200) is None
    assert rt_lease.release_predecessor(a, "host-a", 1, 200) is None
    row = rt_lease.current(a)
    assert (row["holder"], row["live"]) == (holder, True)


def _row(conn: Any) -> tuple:
    with conn.cursor() as cur:
        cur.execute("SELECT lease_holder, lease_expires_at > now(), last_outcome, due_at, registry_at"
                    "  FROM scheduled_jobs WHERE job_name = %s", (rt_lease.NAME,))
        return cur.fetchone()


def test_the_lease_is_an_unscheduled_row_that_keeps_its_holder_after_the_end(
        sessions: tuple[Any, Any]) -> None:
    a, b = sessions
    assert rt_lease.take(a, "host-a:1:100", 600)
    assert _row(a) == ("host-a:1:100", True, "running", None, None)
    assert not rt_lease.take(b, "host-b:1:100", 600), "one holder at a time"
    rt_lease.release(b, "host-a:1:100")
    assert _row(a) == ("host-a:1:100", False, None, None, None)
    assert rt_lease.current(a)["holder"] == "host-a:1:100"
    assert rt_lease.take(b, "host-b:1:100", 600), "the lane is free at once"
    rt_lease.release(a, "host-a:1:100")
    assert _row(a)[:2] == ("host-b:1:100", True), "a release names its holder"


def test_a_row_never_taken_reads_as_no_lease(sessions: tuple[Any, Any]) -> None:
    a, _b = sessions
    with a.cursor() as cur:
        cur.execute("INSERT INTO scheduled_jobs (job_name, cadence, runner) VALUES (%s, '10 minutes', 'test')",
                    (rt_lease.NAME,))
    assert rt_lease.current(a) is None
    assert rt_lease.take(a, "host-a:1:100", 600)


def test_a_paused_row_refuses_the_take_and_says_it_may_be_why(sessions: tuple[Any, Any]) -> None:
    from scraper import job_lease
    from datetime import datetime, timedelta, timezone

    a, _b = sessions
    assert rt_lease.take(a, "host-a:1:100", 600)
    rt_lease.release(a, "host-a:1:100")
    job_lease.pause(a, rt_lease.NAME, until=datetime.now(timezone.utc) + timedelta(hours=1),
                    note="test", by="test")
    assert not rt_lease.take(a, "host-a:1:200", 600)
    assert "paused" in rt_lease.describe(a)
