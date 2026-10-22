"""The notification-events lock, executed on two connections against the replayed schema (notifications
W3 review): producers and merges serialize on one transaction-scoped advisory lock because the event
identity includes property_id, which a merge changes (toolkit/property_carriers.py states the protocol).

  - merge first: a producer statement started while the merge is uncommitted takes its snapshot only after
    the merge commits, so it inserts nothing for the retired property (merged_away by then) and conflicts
    on the survivor's re-pointed row;
  - producer first, the (S, I) interleaving: the producer holds the lock SHARED, the merge then asks for it
    EXCLUSIVE, and only then the producer INSERTs. The merge asks before it locks any row, so the INSERT's
    foreign key check waits on nothing, the producer commits, and the merge's REPOINT sees its row and marks
    the retired property's alert a duplicate. No deadlock.

Every seed is COMMITTED (two connections must see it) and deleted again in the test's `finally`; the
merges are ad-less properties, so they write no ledger, carry or Browse rows. Runs in CI's migrations job
(`TEST_DATABASE_URL`, DB_RAILS_REQUIRED=1)."""

from __future__ import annotations

import threading
import time
import uuid
from typing import Any, Callable

import pytest

from api import notifications as nf
from tests._live_property import OP, REQUIRED_DB, db_url
from toolkit.property_carriers import NOTIFICATION_EVENTS_SHARED_LOCK_SQL
from toolkit.property_identity import merge_property_set

pytestmark = REQUIRED_DB

# A watchdog 'new' over these properties, shaped as the producer's (properties_public is
# `status = 'active'`): what a snapshot taken before a merge commits would still see as active.
_PRODUCER = (
    "INSERT INTO notification_dispatches "
    "  (subscription_id, source_kind, property_id, listing_id, change_kind, trigger_price_czk, event_ref) "
    "SELECT %(subscription_id)s, 'watchdog', p.id, NULL, 'new', NULL, NULL "
    "FROM properties p WHERE p.id = ANY(%(ids)s) AND p.status = 'active' "
    + nf.ON_EVENT_CONFLICT
)


def _connect() -> Any:
    import psycopg

    return psycopg.connect(db_url(), autocommit=True,
                           options="-c statement_timeout=20000 -c lock_timeout=15000")


class _World:
    """Committed seeds: one account, a watchdog, two ad-less active properties (S, lower id, survives)
    and the watchdog's 'new' on R; `cleanup` deletes all of it."""

    def __init__(self, conn: Any) -> None:
        self.conn = conn
        self.account = conn.execute("INSERT INTO accounts (kind, name) VALUES ('personal', %s) "
                                    "RETURNING id", (f"lk-{uuid.uuid4().hex[:8]}",)).fetchone()[0]
        self.sub = conn.execute("INSERT INTO notification_subscriptions (account_id, name, filter_spec) "
                                "VALUES (%s, 'lk', '{}'::jsonb) RETURNING id",
                                (self.account,)).fetchone()[0]
        self.s = int(conn.execute("INSERT INTO properties DEFAULT VALUES RETURNING id").fetchone()[0])
        self.r = int(conn.execute("INSERT INTO properties DEFAULT VALUES RETURNING id").fetchone()[0])
        self.r_new = conn.execute(
            "INSERT INTO notification_dispatches (subscription_id, source_kind, property_id, change_kind, "
            "event_ref) VALUES (%s, 'watchdog', %s, 'new', NULL) RETURNING id",
            (self.sub, self.r)).fetchone()[0]

    def alerts(self) -> dict[Any, tuple[int, Any]]:
        rows = self.conn.execute("SELECT id, property_id, duplicate_of FROM notification_dispatches "
                                 "WHERE subscription_id = %s", (self.sub,)).fetchall()
        return {nid: (int(pid), dup) for nid, pid, dup in rows}

    def merge(self, conn: Any) -> dict[str, Any]:
        return merge_property_set(conn, [self.s, self.r], source="operator", reason="manual_subset",
                                  decided_by=OP)["data"]

    def cleanup(self) -> None:
        c = self.conn
        c.execute("DELETE FROM notification_dispatches WHERE subscription_id = %s", (self.sub,))
        c.execute("DELETE FROM notification_subscriptions WHERE id = %s", (self.sub,))
        c.execute("DELETE FROM property_merge_events WHERE survivor_property_id = ANY(%(p)s) "
                  "OR retired_property_id = ANY(%(p)s)", {"p": [self.s, self.r]})
        c.execute("DELETE FROM properties WHERE id = ANY(%s)", ([self.s, self.r],))
        c.execute("DELETE FROM accounts WHERE id = %s", (self.account,))


def _wait_until_blocked_on_the_lock(observer: Any, pid: int) -> None:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if observer.execute("SELECT count(*) FROM pg_locks WHERE pid = %s AND locktype = 'advisory' "
                            "AND NOT granted", (pid,)).fetchone()[0]:
            return
        time.sleep(0.05)
    pytest.fail(f"backend {pid} never waited on the notification-events lock")


def _in_thread(fn: Callable[[], Any]) -> tuple[threading.Thread, dict[str, Any]]:
    out: dict[str, Any] = {}

    def run() -> None:
        try:
            out["value"] = fn()
        except BaseException as exc:  # noqa: BLE001 — handed back to the test thread
            out["error"] = exc

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread, out


def _joined(thread: threading.Thread, out: dict[str, Any]) -> Any:
    thread.join(timeout=25)
    assert not thread.is_alive(), "the other connection never finished"
    if "error" in out:
        raise out["error"]
    return out.get("value")


def test_a_producer_whose_snapshot_predates_the_merge_commit_inserts_nothing_for_the_retired() -> None:
    seed, merger, producer, observer = _connect(), _connect(), _connect(), _connect()
    world = _World(seed)
    try:
        with merger.transaction():
            data = world.merge(merger)
            assert (data["survivor_id"], data["retired_ids"]) == (world.s, [world.r])
            # The producer statement starts while the merge is uncommitted, R still active in every
            # snapshot but the merge's own.
            thread, out = _in_thread(lambda: nf._commit_one(
                producer, _PRODUCER, {"subscription_id": world.sub, "ids": [world.s, world.r]}))
            _wait_until_blocked_on_the_lock(observer, producer.info.backend_pid)
        assert _joined(thread, out) == 0, "a 'new' was written after the merge for the retired or the held"
        assert world.alerts() == {world.r_new: (world.s, None)}
    finally:
        for conn in (merger, producer, observer):
            conn.close()
        world.cleanup()
        seed.close()


def test_a_merge_after_a_producer_marks_its_row_and_the_s_i_interleaving_never_deadlocks() -> None:
    seed, merger, producer, observer = _connect(), _connect(), _connect(), _connect()
    world = _World(seed)
    try:
        with producer.transaction(), producer.cursor() as cur:
            cur.execute(NOTIFICATION_EVENTS_SHARED_LOCK_SQL)                    # S
            thread, out = _in_thread(lambda: world.merge(merger))
            _wait_until_blocked_on_the_lock(observer, merger.info.backend_pid)  # I, asked
            cur.execute(_PRODUCER + " RETURNING id", {"subscription_id": world.sub, "ids": [world.s]})
            (s_new,) = cur.fetchone()                                           # S's FK check waited on nothing
        data = _joined(thread, out)
        assert (data["survivor_id"], data["retired_ids"]) == (world.s, [world.r])
        assert world.alerts() == {s_new: (world.s, None), world.r_new: (world.s, s_new)}
    finally:
        for conn in (merger, producer, observer):
            conn.close()
        world.cleanup()
        seed.close()
