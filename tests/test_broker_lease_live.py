"""The broker lane and the daily full resolve on one `scheduled_jobs` lease row (#2108), EXECUTED on the replayed schema
through their real entry points and on connections of their own, so each lease commits where the other can see it.

Runs in CI's migrations job (`TEST_DATABASE_URL`, `DB_RAILS_REQUIRED=1`). The resolve's phases are patched off: the
lease is under test, and attribution or a merge would leak into the lanes that share this database.
"""

from __future__ import annotations

import sys
import threading
from collections.abc import Iterator
from typing import Any

import psycopg
import pytest

import scripts.resolve_brokers as rb
from scraper import job_lease
from tests._live_property import REQUIRED_DB, db_url

pytestmark = REQUIRED_DB

_FULL_STATS = {"attached": 0, "auto_merges": 0, "queued": 0, "suppressed": 0}


def _connect() -> psycopg.Connection:
    return psycopg.connect(db_url(), autocommit=True, prepare_threshold=None)


def _clear(conn: psycopg.Connection) -> None:
    conn.execute("DELETE FROM scheduled_jobs WHERE job_name = %s", (rb.BROKER_LEASE,))
    conn.execute("UPDATE broker_resolution_lock SET holder = NULL, mode = NULL")


@pytest.fixture
def conn() -> Iterator[psycopg.Connection]:
    with _connect() as c:
        _clear(c)
        yield c
        _clear(c)


def _holders(conn: psycopg.Connection) -> tuple[str | None, str | None]:
    """(the job_lease row's holder, the cutover row's holder)."""
    row = conn.execute("SELECT lease_holder FROM scheduled_jobs WHERE job_name = %s", (rb.BROKER_LEASE,)).fetchone()
    old = conn.execute("SELECT holder FROM broker_resolution_lock").fetchone()
    return (row[0] if row else None), old[0]


def _full_main(monkeypatch: pytest.MonkeyPatch, *, during: Any = None) -> int:
    """`main()`'s real lease path with the sweep's phases off; `during` runs while it holds the lease."""
    monkeypatch.setenv("SUPABASE_DB_URL", db_url())
    monkeypatch.setattr(sys, "argv", ["resolve_brokers"])

    def run_full(c: Any, *a: Any, **k: Any) -> tuple[dict[str, int], Any]:
        if during is not None:
            during(c, a[5])
        return _FULL_STATS, c

    monkeypatch.setattr(rb, "_run_full", run_full)
    return rb.main()


def test_the_lane_skips_while_the_full_resolve_holds_the_lease(
    conn: psycopg.Connection, monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, Any] = {}

    def during(c: Any, hold: Any) -> None:
        seen["holders"] = _holders(conn)
        seen["pass"] = rb.run_incremental_pass(conn)

    assert _full_main(monkeypatch, during=during) == 0
    new, old = seen["holders"]
    assert new.startswith("full:"), "the mode lives in the holder prefix"
    assert old == new, "the cutover row names the same holder"
    assert seen["pass"] == {"skipped": True, "attributed": 0, "brokers": 0}
    assert _holders(conn) == (None, None)
    last = conn.execute("SELECT last_outcome, registry_at FROM scheduled_jobs WHERE job_name = %s",
                        (rb.BROKER_LEASE,)).fetchone()
    assert last == ("ok", None), "a lease-only row: an outcome, and never on the runner's registry"


def test_the_full_resolve_aborts_while_the_lane_holds_the_lease(
    conn: psycopg.Connection, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The lane's real pass holds the lease across a drain; a full resolve starting inside it waits out its budget."""
    monkeypatch.setattr(rb, "_LOCK_WAIT_MAX_SECONDS", 0)
    seen: dict[str, Any] = {}

    def drain(c: Any, *a: Any, **k: Any) -> tuple[dict[str, int], Any]:
        seen["holders"] = _holders(conn)
        with monkeypatch.context() as inner:
            seen["full"] = _full_main(inner)
        return {"attributed": 0, "brokers": 0}, c

    with _connect() as lane:
        monkeypatch.setattr(rb, "_run_incremental", drain)
        stats = rb.run_incremental_pass(lane)

    assert stats["skipped"] is False
    assert seen["holders"][0].startswith("incremental:")
    assert seen["full"] == 1
    assert _holders(conn) == (None, None)


@pytest.mark.parametrize("kind", ["incremental", "full"])
def test_the_scheduled_jobs_row_excludes_on_its_own(
    conn: psycopg.Connection, monkeypatch: pytest.MonkeyPatch, kind: str,
) -> None:
    """The state after the gate: only the new row is contended. A writer that loses it gives the cutover row back."""
    monkeypatch.setattr(job_lease.time, "sleep", lambda s: None)
    with job_lease.held(conn, rb.BROKER_LEASE, cadence="30 seconds", holder="rival") as rival:
        assert rival is not None
        with _connect() as other:
            if kind == "incremental":
                assert rb.run_incremental_pass(other)["skipped"] is True
            else:
                monkeypatch.setattr(rb, "_LOCK_WAIT_MAX_SECONDS", 1)
                assert _full_main(monkeypatch) == 1
        assert _holders(conn) == ("rival", None)


@pytest.mark.parametrize("kind", ["incremental", "full"])
def test_an_older_worker_on_migration_192s_row_alone_still_excludes(
    conn: psycopg.Connection, monkeypatch: pytest.MonkeyPatch, kind: str,
) -> None:
    """The deploy overlap: older code holds only the old row, so the new row is free and must not be enough."""
    monkeypatch.setattr(rb.time, "sleep", lambda s: None)
    conn.execute("UPDATE broker_resolution_lock SET holder = 'older-worker', mode = 'incremental', heartbeat_at = now()")
    with _connect() as other:
        if kind == "incremental":
            assert rb.run_incremental_pass(other)["skipped"] is True
        else:
            monkeypatch.setattr(rb, "_LOCK_WAIT_MAX_SECONDS", 0.001)
            assert _full_main(monkeypatch) == 1
    assert _holders(conn) == (None, "older-worker"), "the new row was never taken, and the old holder keeps its row"


def test_a_lease_a_rival_took_stops_the_full_resolve(conn: psycopg.Connection, monkeypatch: pytest.MonkeyPatch) -> None:
    """A rival took the row after it lapsed: the next renewal refuses, and the release leaves the rival's lease."""

    def during(c: Any, hold: Any) -> None:
        conn.execute("UPDATE scheduled_jobs SET lease_holder = 'rival', lease_expires_at = now() + interval '1 hour' "
                     "WHERE job_name = %s", (rb.BROKER_LEASE,))
        rb._renew_lock(c, hold)

    with pytest.raises(job_lease.LeaseLost):
        _full_main(monkeypatch, during=during)
    assert _holders(conn) == ("rival", None)


def test_many_writers_never_overlap(conn: psycopg.Connection) -> None:
    """Racing writers on committed sessions: at most one inside at any moment, and every one releases."""
    lock = threading.Lock()
    inside = {"now": 0, "most": 0, "entered": 0}
    failures: list[BaseException] = []

    def writer() -> None:
        try:
            with _connect() as own:
                for _ in range(25):
                    with rb._resolution_lease(own, "incremental") as hold:
                        if hold is None:
                            continue
                        with lock:
                            inside["now"] += 1
                            inside["entered"] += 1
                            inside["most"] = max(inside["most"], inside["now"])
                        job_lease.renew(own, hold)
                        with lock:
                            inside["now"] -= 1
        except BaseException as exc:  # noqa: BLE001 - reported on the test's thread
            failures.append(exc)

    threads = [threading.Thread(target=writer) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert failures == []
    assert inside["entered"] > 0
    assert inside["most"] == 1
    assert _holders(conn) == (None, None)
