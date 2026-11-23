"""The broker re-point's dirty step on two connections against the replayed schema: a re-point of
`broker_identities.broker_id` and a write that MOVES one of the identity's ads (a birth, a property merge) take turns on
the ad's row, so the book follows the re-point whichever commits first (without the lock each side reads the other's
old state and the book keeps the old broker until the daily sweep). Every seed is COMMITTED (two connections must see
it) and deleted again in the test's `finally`."""

from __future__ import annotations

import contextlib
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from datetime import timedelta
from typing import Any

import psycopg

from api.broker_review import merge_brokers
from scraper import db
from scripts.recompute_property_stats import properties_changed
from scripts.resolve_brokers import _attach_singletons
from tests._live_property import OP, REQUIRED_DB, _advert, _property, db_url
from toolkit.property_identity import merge_property_set

pytestmark = REQUIRED_DB


def _connect(autocommit: bool = True) -> Any:
    return psycopg.connect(db_url(), autocommit=autocommit,
                           options="-c statement_timeout=20000 -c lock_timeout=15000")


class _World:
    """Committed seeds, every id recorded so `cleanup` deletes all of it (and what the writers wrote about it)."""

    def __init__(self, conn: Any) -> None:
        self.conn = conn
        self.props: list[int] = []
        self.ads: list[int] = []
        self.idents: list[int] = []
        self.brokers: list[int] = []
        self.now = conn.execute("SELECT now()").fetchone()[0]

    def _add(self, ids: list[int], sql: str, params: tuple[Any, ...] = ()) -> int:
        ids.append(int(self.conn.execute(sql, params).fetchone()[0]))
        return ids[-1]

    def prop(self) -> int:
        with self.conn.cursor() as cur:
            self.props.append(_property(cur))
        return self.props[-1]

    def broker(self) -> int:
        return self._add(self.brokers, "INSERT INTO brokers (display_name) VALUES (%s) RETURNING id",
                         (f"lk-{uuid.uuid4().hex[:8]}",))

    def identity(self, broker: int | None) -> int:
        return self._add(self.idents, "INSERT INTO broker_identities (source, source_broker_id_native, broker_id) "
                         "VALUES ('sreality', %s, %s) RETURNING id", (f"lk-{uuid.uuid4()}", broker))

    def ad(self, pid: int | None, identity: int) -> int:
        with self.conn.cursor() as cur:
            self.ads.append(_advert(cur, pid, source="sreality", broker_identity_id=identity,
                                    first_seen_at=self.now - timedelta(days=5),
                                    last_seen_at=self.now - timedelta(hours=1)))
        return self.ads[-1]

    def book(self, pid: int, relation: str = "broker_book") -> set[int]:
        rows = self.conn.execute(f"SELECT broker_id FROM {relation} WHERE property_id = %s", (pid,)).fetchall()
        return {int(r[0]) for r in rows}

    def dirty(self) -> set[int]:
        rows = self.conn.execute("SELECT property_id FROM dirty_properties WHERE property_id = ANY(%s)",
                                 (self.props,)).fetchall()
        return {int(r[0]) for r in rows}

    def recompute(self, ids: set[int]) -> None:
        """The dirty drain's slice, over these properties only (the shared lane may hold others' rows)."""
        with self.conn.transaction():
            properties_changed(self.conn, sorted(ids))

    def cleanup(self) -> None:
        c = self.conn
        for ids, sql, of in (
                (self.props, "SELECT property_id FROM listings WHERE id = ANY(%s)", self.ads),
                (self.idents, "SELECT broker_identity_id FROM listings WHERE id = ANY(%s)", self.ads),
                (self.brokers, "SELECT broker_id FROM broker_identities WHERE id = ANY(%s)", self.idents)):
            ids += [int(r[0]) for r in c.execute(sql, (of,)).fetchall() if r[0] is not None and r[0] not in ids]
        p, l, i, b = self.props, self.ads, self.idents, self.brokers
        c.execute("DELETE FROM broker_book WHERE property_id = ANY(%s) OR broker_id = ANY(%s)", (p, b))
        c.execute("DELETE FROM dirty_properties WHERE property_id = ANY(%s)", (p,))
        c.execute("DELETE FROM browse_list WHERE property_id = ANY(%s)", (p,))
        c.execute("DELETE FROM property_merge_carries WHERE from_property_id = ANY(%s) OR to_property_id = ANY(%s)",
                  (p, p))
        c.execute("DELETE FROM property_merge_events WHERE survivor_property_id = ANY(%s) "
                  "OR retired_property_id = ANY(%s)", (p, p))
        c.execute("DELETE FROM broker_merge_events WHERE survivor_broker_id = ANY(%s) OR retired_broker_id = ANY(%s) "
                  "OR identity_id = ANY(%s)", (b, b, i))
        c.execute("DELETE FROM listings WHERE id = ANY(%s)", (l,))
        c.execute("DELETE FROM properties WHERE id = ANY(%s)", (p,))
        c.execute("DELETE FROM brokers WHERE id = ANY(%s)", (b,))
        c.execute("DELETE FROM broker_identities WHERE id = ANY(%s)", (i,))
        c.close()


@contextlib.contextmanager
def _two_writers() -> Iterator[tuple[_World, Any, Any]]:
    """The world, a FIRST writer inside an open transaction (a writer's own `conn.transaction()` becomes a savepoint in
    it, uncommitted until the test commits) and a SECOND writer."""
    world, first, second = _World(_connect()), _connect(autocommit=False), _connect()
    first.execute("SELECT 1")
    try:
        yield world, first, second
    finally:
        first.rollback()
        first.close()
        second.close()
        world.cleanup()


def _waits_then_runs(world: _World, first: Any, second: Any, fn: Callable[[], Any]) -> Any:
    """Run `fn` on `second` in a thread, see it wait on a lock, commit `first`, and return what `fn` returned."""
    box: dict[str, Any] = {}

    def run() -> None:
        try:
            box["out"] = fn()
        except BaseException as exc:  # noqa: BLE001 - surfaced below
            box["err"] = exc

    th = threading.Thread(target=run, daemon=True)
    th.start()
    deadline, waited = time.monotonic() + 10, False
    while not waited and th.is_alive() and time.monotonic() < deadline:
        row = world.conn.execute("SELECT wait_event_type FROM pg_stat_activity WHERE pid = %s",
                                 (second.info.backend_pid,)).fetchone()
        waited = bool(row and row[0] == "Lock")
        time.sleep(0.05)
    assert waited, "the second writer never waited on the first"
    first.commit()
    th.join(20)
    assert not th.is_alive(), "the second writer never finished"
    if "err" in box:
        raise box["err"]
    return box.get("out")


def test_a_singleton_attach_waits_for_an_open_birth_and_dirties_the_born_property() -> None:
    """The birth's own book read saw an identity with no broker; the attach waits, then dirties the born property."""
    with _two_writers() as (world, birth, attach):
        who = world.identity(None)
        lid = world.ad(None, who)
        (born,) = db.create_singleton_properties(birth, [lid])
        world.props.append(born)
        properties_changed(birth, [born])
        assert _waits_then_runs(world, birth, attach, lambda: _attach_singletons(attach)) >= 1

        broker = world.conn.execute("SELECT broker_id FROM broker_identities WHERE id = %s", (who,)).fetchone()[0]
        assert broker is not None and world.book(born) == set() and world.dirty() == {born}
        world.recompute({born})
        assert world.book(born) == world.book(born, "broker_book_projection") == {broker}


def test_an_operator_broker_merge_waits_for_an_open_property_merge_and_dirties_the_survivor() -> None:
    """The ad moves from the retired property to the survivor, whose book read took the loser broker's row: the broker
    merge waits for the move, so it dirties the survivor, not the retired property."""
    with _two_writers() as (world, move, op):
        keep, loser, third = world.broker(), world.broker(), world.broker()
        survivor_p, retired_p = world.prop(), world.prop()
        world.ad(retired_p, world.identity(loser))
        world.ad(survivor_p, world.identity(third))
        world.recompute({survivor_p, retired_p})
        world.conn.execute("DELETE FROM dirty_properties WHERE property_id = ANY(%s)", (world.props,))
        out = merge_property_set(move, [survivor_p, retired_p], source="operator", reason="manual_subset",
                                 decided_by=OP)["data"]
        assert (out["survivor_id"], out["retired_ids"]) == (survivor_p, [retired_p])
        merged = _waits_then_runs(world, move, op, lambda: merge_brokers(op, [keep, loser], created_by=OP))
        assert merged["survivor_broker_id"] == keep

        assert world.book(survivor_p) == {loser, third} and world.dirty() == {survivor_p}
        world.recompute(world.dirty())
        assert world.book(survivor_p) == world.book(survivor_p, "broker_book_projection") == {keep, third}


def test_a_birth_after_an_open_re_point_waits_for_it_and_stores_the_new_broker() -> None:
    """The re-point locked the unborn ad first (nothing to dirty): the birth waits for its commit and its own book read
    stores the survivor broker, with nothing left for the drain."""
    with _two_writers() as (world, repoint, birth):
        keep, loser = world.broker(), world.broker()
        lid = world.ad(None, world.identity(loser))
        merge_brokers(repoint, [keep, loser], created_by=OP)

        def born() -> list[int]:
            with birth.transaction():
                ids = db.create_singleton_properties(birth, [lid])
                properties_changed(birth, ids)
                return ids

        (pid,) = _waits_then_runs(world, repoint, birth, born)
        world.props.append(pid)
        assert world.book(pid) == world.book(pid, "broker_book_projection") == {keep}
        assert world.dirty() == set()
