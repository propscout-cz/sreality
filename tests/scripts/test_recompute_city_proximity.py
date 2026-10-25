"""scripts/recompute_city_proximity: the full rebuild walks the placeable properties in id batches, one short
transaction each (restamp the batch, let the incremental call refill it); without --full, one incremental call.
Offline, over a fake connection; the SQL itself runs live in tests/test_674_city_proximity_pg_cron.py."""

from __future__ import annotations

from typing import Any

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
    (_, first), (_, second), (_, last) = (txn[1] for txn in conn.log)
    assert first == {"after": 0, "size": 2}
    assert second == {"after": 3, "size": 2}, "the keyset advances past the batch's highest id"
    assert last == {"after": 7, "size": 2}
    assert [sql for sql, _ in conn.log[0][2:]] == ["select recompute_city_proximity(false)"]
    assert conn.log[2][2:] == [], "an empty batch ends the walk without another recompute"


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
