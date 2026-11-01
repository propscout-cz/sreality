"""The maintenance lane and the daily sweep on one `scheduled_jobs` lease row (#2107), EXECUTED on the replayed schema
through their real entry points and on connections of their own, so each lease commits where the other can see it.

Runs in CI's migrations job (`TEST_DATABASE_URL`, `DB_RAILS_REQUIRED=1`). The sweep's data steps are patched off: the
lease is under test, and a stamp or a recompute would leak into the lanes that share this database.
"""

from __future__ import annotations

import sys
import threading
from collections.abc import Iterator
from typing import Any

import psycopg
import pytest

import scripts.recompute_property_stats as rps
from scraper import job_lease
from tests._live_property import REQUIRED_DB, db_url

pytestmark = REQUIRED_DB


def _connect() -> psycopg.Connection:
    return psycopg.connect(db_url(), autocommit=True, prepare_threshold=None)


def _clear(conn: psycopg.Connection) -> None:
    conn.execute("DELETE FROM scheduled_jobs WHERE job_name = %s", (rps.MAINTENANCE_LEASE,))
    conn.execute("UPDATE property_maintenance_lease SET holder = NULL, expires_at = NULL")


@pytest.fixture
def conn() -> Iterator[psycopg.Connection]:
    with _connect() as c:
        _clear(c)
        yield c
        _clear(c)


def _holders(conn: psycopg.Connection) -> tuple[str | None, str | None]:
    """(the job_lease row's holder, the cutover row's holder)."""
    row = conn.execute("SELECT lease_holder FROM scheduled_jobs WHERE job_name = %s",
                       (rps.MAINTENANCE_LEASE,)).fetchone()
    old = conn.execute("SELECT holder FROM property_maintenance_lease").fetchone()
    return (row[0] if row else None), old[0]


def _sweep_main(monkeypatch: pytest.MonkeyPatch, *, during: Any = None) -> int:
    """`main()`'s real lease path over no data; `during` runs while the sweep holds the lease."""
    monkeypatch.setenv("SUPABASE_DB_URL", db_url())
    monkeypatch.setattr(sys, "argv", ["recompute_property_stats"])
    monkeypatch.setattr(rps.signal, "signal", lambda *a: None)
    monkeypatch.setattr(rps, "_max_property_id", lambda c: 0)
    monkeypatch.setattr(rps, "_reconcile_childless", lambda c: 0)
    monkeypatch.setattr(rps.db, "stamp_derived_artifact", lambda *a, **k: None)

    def attach(c: Any, **_: Any) -> int:
        if during is not None:
            during()
        return 0

    monkeypatch.setattr(rps, "_attach_stragglers", attach)
    return rps.main()


def test_the_lane_skips_while_the_sweep_holds_the_lease(conn: psycopg.Connection, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    def during() -> None:
        seen["holders"] = _holders(conn)
        seen["pass"] = rps.run_incremental_pass(conn)

    assert _sweep_main(monkeypatch, during=during) == 0
    new, old = seen["holders"]
    assert new.startswith("full:"), "the mode lives in the holder prefix"
    assert old == new, "the cutover row names the same holder"
    assert seen["pass"] == {"skipped": True, "attached": 0, "recomputed": 0, "settled": 0}
    assert _holders(conn) == (None, None)
    last = conn.execute("SELECT last_outcome, registry_at FROM scheduled_jobs WHERE job_name = %s",
                        (rps.MAINTENANCE_LEASE,)).fetchone()
    assert last == ("ok", None), "a lease-only row: an outcome, and never on the runner's registry"


def test_the_sweep_goes_red_while_the_lane_holds_the_lease(conn: psycopg.Connection, monkeypatch: pytest.MonkeyPatch) -> None:
    """The lane's real pass holds the lease across its attach; a sweep starting inside it waits out its budget."""
    monkeypatch.setattr(rps, "_MAX_LEASE_WAIT_SECONDS", 0.0)
    errors: list[BaseException] = []
    holders: list[Any] = []

    def attach(c: Any, **_: Any) -> int:
        holders.append(_holders(conn))
        with monkeypatch.context() as inner:
            try:
                _sweep_main(inner)
            except RuntimeError as exc:
                errors.append(exc)
        return 0

    with _connect() as lane:
        monkeypatch.setattr(rps, "_attach_stragglers", attach)
        monkeypatch.setattr(rps, "_drain_dirty", lambda *a, **k: 0)
        monkeypatch.setattr(rps, "_settle_fresh", lambda *a, **k: 0)
        stats = rps.run_incremental_pass(lane)

    assert stats["skipped"] is False
    assert holders[0][0].startswith("incremental:")
    assert len(errors) == 1
    assert "failing RED" in str(errors[0])
    assert _holders(conn) == (None, None)


@pytest.mark.parametrize("kind", ["incremental", "full"])
def test_the_scheduled_jobs_row_excludes_on_its_own(
    conn: psycopg.Connection, monkeypatch: pytest.MonkeyPatch, kind: str,
) -> None:
    """The state after the gate: only the new row is contended. A writer that loses it gives the cutover row back."""
    monkeypatch.setattr(job_lease.time, "sleep", lambda s: None)
    with job_lease.held(conn, rps.MAINTENANCE_LEASE, cadence="30 seconds", holder="rival") as rival:
        assert rival is not None
        with _connect() as other:
            if kind == "incremental":
                assert rps.run_incremental_pass(other)["skipped"] is True
            else:
                monkeypatch.setattr(rps, "_MAX_LEASE_WAIT_SECONDS", 1.0)
                with pytest.raises(RuntimeError, match="failing RED"):
                    _sweep_main(monkeypatch)
        assert _holders(conn) == ("rival", None)


@pytest.mark.parametrize("kind", ["incremental", "full"])
def test_an_older_worker_on_migration_279s_row_alone_still_excludes(
    conn: psycopg.Connection, monkeypatch: pytest.MonkeyPatch, kind: str,
) -> None:
    """The deploy overlap: older code holds only the old row, so the new row is free and must not be enough."""
    monkeypatch.setattr(rps.time, "sleep", lambda s: None)
    conn.execute("UPDATE property_maintenance_lease SET holder = 'older-worker', expires_at = now() + interval '1 hour'")
    with _connect() as other:
        if kind == "incremental":
            assert rps.run_incremental_pass(other)["skipped"] is True
        else:
            monkeypatch.setattr(rps, "_MAX_LEASE_WAIT_SECONDS", 0.001)
            with pytest.raises(RuntimeError, match="failing RED"):
                _sweep_main(monkeypatch)
    assert _holders(conn) == (None, "older-worker"), "the new row was never taken, and the old holder keeps its row"


def test_a_lapsed_lease_stops_the_sweep_before_it_stamps(conn: psycopg.Connection, monkeypatch: pytest.MonkeyPatch) -> None:
    """A rival took the row after it lapsed: the sweep's next renewal refuses, and its release leaves the rival's lease."""
    stamps: list[Any] = []

    def during() -> None:
        conn.execute("UPDATE scheduled_jobs SET lease_holder = 'rival', lease_expires_at = now() + interval '1 hour' "
                     "WHERE job_name = %s", (rps.MAINTENANCE_LEASE,))

    with pytest.raises(job_lease.LeaseLost):
        _sweep_main(monkeypatch, during=during)
        stamps.append("finished")
    assert stamps == []
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
                    with rps._maintenance_lease(own, "incremental") as hold:
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
