"""scripts/recompute_city_proximity over a fake connection; its SQL runs live in tests/test_674_city_proximity_pg_cron.py."""

from __future__ import annotations

from typing import Any

import psycopg

from scripts import recompute_city_proximity as rcp


class _Cursor:
    def __init__(self, conn: "_Conn") -> None:
        self._conn = conn
        self._rows: list[tuple[Any, ...]] = []

    def execute(self, sql: str, params: Any = None) -> None:
        self._conn.log[-1].append((" ".join(sql.split()), params))
        if "returning p.id" in sql:
            self._rows = [(i,) for i in self._conn.batches.pop(0)]
        elif "recompute_city_proximity(false)" in sql:
            if self._conn.deadlocks:
                self._conn.deadlocks -= 1
                raise psycopg.errors.DeadlockDetected("deadlock detected")
            self._rows = [(self._conn.written,)]

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self._rows

    def fetchone(self) -> tuple[Any, ...]:
        return self._rows[0]

    def __enter__(self) -> "_Cursor":
        return self

    def __exit__(self, *exc: Any) -> None:
        return None


class _Txn:
    def __init__(self, conn: "_Conn") -> None:
        self._conn = conn

    def __enter__(self) -> None:
        self._conn.log.append([])

    def __exit__(self, *exc: Any) -> None:
        return None


class _Conn:
    """Each `transaction()` opens a new entry in `log`; the restamp statement serves `batches` in order."""

    def __init__(self, batches: list[list[int]], written: int = 0) -> None:
        self.batches, self.written = batches, written
        self.deadlocks = 0  # how many recompute calls raise DeadlockDetected before one succeeds
        self.log: list[list[tuple[str, Any]]] = []

    def transaction(self) -> _Txn:
        return _Txn(self)

    def cursor(self) -> _Cursor:
        return _Cursor(self)


def test_the_full_rebuild_runs_one_transaction_per_batch_until_none_is_left() -> None:
    conn = _Conn([[3, 1], [7], []], written=2)
    assert rcp.full_rebuild(conn, batch_size=2) == 4
    assert len(conn.log) == 3
    for txn in conn.log:
        assert txn[0][0] == "set local statement_timeout = '20min'"
        # The function's lock BEFORE the restamp's row locks: taken after them, the hourly pg_cron run could
        # hold the lock while waiting on a restamped row, and Postgres would abort one side as a deadlock.
        assert txn[1][0] == "select pg_advisory_xact_lock(hashtext('recompute_city_proximity'))"
    (_, first), (_, second), (_, last) = (txn[2] for txn in conn.log)
    assert first == {"after": 0, "size": 2}
    assert second == {"after": 3, "size": 2}, "the keyset advances past the batch's highest id"
    assert last == {"after": 7, "size": 2}
    assert [sql for sql, _ in conn.log[0][3:]] == ["select recompute_city_proximity(false)"]
    assert conn.log[2][3:] == [], "an empty batch ends the walk without another recompute"


def test_a_batch_that_loses_a_deadlock_is_retried_and_the_walk_goes_on() -> None:
    # The property rollup can still hold a row the batch needs; the batch rolls back whole, so a retry is safe.
    conn = _Conn([[3, 1], [3, 1], []], written=2)
    conn.deadlocks = 1
    assert rcp.full_rebuild(conn, batch_size=2) == 2
    assert [txn[2][1] for txn in conn.log] == [{"after": 0, "size": 2}] * 2 + [{"after": 3, "size": 2}]


def test_a_batch_that_keeps_losing_deadlocks_fails_the_run() -> None:
    conn = _Conn([[3, 1]] * rcp.DEADLOCK_ATTEMPTS, written=2)
    conn.deadlocks = rcp.DEADLOCK_ATTEMPTS
    try:
        rcp.full_rebuild(conn, batch_size=2)
    except psycopg.errors.DeadlockDetected:
        assert len(conn.log) == rcp.DEADLOCK_ATTEMPTS
    else:
        raise AssertionError("a batch that never commits must fail the run, not end it green")


def test_the_restamp_reaches_only_properties_the_function_can_place() -> None:
    # Clearing the stamp of a property with no map point would leave it unset: the function skips it.
    sql = " ".join(rcp._RESTAMP_BATCH_SQL.split())
    assert "join listing_location ll on ll.listing_id = p.location_listing_ref_id" in sql
    assert "ll.geom is not null" in sql
    assert "order by p.id" in sql


def test_without_full_one_incremental_call_runs_in_one_transaction() -> None:
    conn = _Conn([], written=5)
    assert rcp.incremental(conn) == 5
    assert [sql for sql, _ in conn.log[0]] == [
        "set local statement_timeout = '20min'", "select recompute_city_proximity(false)"]
