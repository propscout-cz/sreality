"""E976 — a build reads the scope's LIVE adverts first.

The operator, 2026-10-22: Praha's visible duplicates merged as fast as possible. The widening to
the whole of Praha left 37,910 adverts unread, 11,864 of them live, and the build's feed took them
in listing-id order after a cursor: the live ones (median id 18.7M against 0.5M overall) came
last, ~3 days in. During a build the feed now orders live before inactive, ids ascending within
each, under no id cursor (the `rt_fp` anti-join advances it); outside a build nothing changes.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any

from autodedup import incremental_sql as S
from autodedup.incremental import WorkItem, bootstrap_key
from autodedup.incremental_lane import CURSOR_ENTER, SqlWork, pass_rate_key, run_incremental
from autodedup.incremental_scope import Scope, ScopeBlock
from tests.autodedup.fake_pg import FakePg
from tests.autodedup.test_rt_widening import NEW_IDS, OTHER_TOWN, TOWN, _built_town, _seed

GEN = "rt"
BLOCK = Scope((ScopeBlock("obec", TOWN),))
# id -> is_active; None = no `listings` row at all.
WORLD: dict[int, bool | None] = {1: False, 2: True, 3: False, 4: True, 5: None, 6: True}


def _world(world: dict[int, bool | None] = WORLD) -> FakePg:
    db = FakePg()
    for listing_id, live in world.items():
        db.locations[listing_id] = {"obec_kod": TOWN, "cast_obce_kod": None,
                                    "resolved_at": db.now - timedelta(days=1)}
        if live is not None:
            db.listings[listing_id] = {"id": listing_id, "is_active": live,
                                       "first_seen_at": db.now - timedelta(days=30)}
    # The forward feed is past them: these are a widening's held history, not arrivals.
    db.cursors["rt_new"] = {"last_listing_id": max(world), "last_snapshot_id": None,
                            "watermark": None}
    return db


def _work(db: FakePg, **kw: Any) -> SqlWork:
    params: dict[str, Any] = {"straggler_window": 0, "revive_slice": 0, "drift_slice": 0,
                              "evidence_slice": 0, "lag": 0, "enter_slice": 1000}
    params.update(kw)
    return SqlWork(db, BLOCK, GEN, **params)


def _entered(work: SqlWork, limit: int = 50) -> list[int]:
    return [item.listing_id for item in work.claim(limit) if item.feed == "entered"]


def _decide(db: FakePg, work: SqlWork, ids: list[int]) -> dict[str, Any]:
    """What a committed pass leaves: a fingerprint row each, the feed's cursor written."""
    for listing_id in ids:
        db.rt_fp[(GEN, listing_id)] = {"is_active": True}
    return work.commit([WorkItem(listing_id, "entered", None, None) for listing_id in ids])


def test_a_build_reads_live_adverts_first_and_ids_ascending_within_each() -> None:
    db = _world()
    assert _entered(_work(db, bootstrap=True)) == [2, 4, 6, 1, 3, 5], (
        "live, then inactive, then the advert with no listings row")
    assert S.RT_SCOPE_BUILD_ENTRANTS_SQL in db.statements
    assert S.RT_SCOPE_ENTRANTS_SQL not in db.statements


def test_a_short_build_claim_takes_the_live_adverts_first() -> None:
    db = _world()
    assert _entered(_work(db, bootstrap=True), limit=2) == [2, 4]


def test_the_build_feed_is_advanced_by_the_fingerprints_not_by_an_id_cursor() -> None:
    """Two keys are no keyset: the id half of the cursor stays 0 and what a pass decided leaves
    the feed because it now has an `rt_fp` row, so the next pass takes the next live advert."""
    db = _world()
    first = _work(db, bootstrap=True)
    taken = _entered(first, limit=2)
    assert taken == [2, 4]
    out = _decide(db, first, taken)
    assert out[CURSOR_ENTER][0] == 0, "no id cursor during a build"
    assert db.cursors[CURSOR_ENTER]["last_listing_id"] == 0

    second = _work(db, bootstrap=True)
    assert _entered(second, limit=2) == [6, 1], "the last live advert, then the inactive"


def test_a_cursor_left_by_the_id_ordered_feed_skips_nothing_in_a_build() -> None:
    """A build that was running when E976 deployed holds a cursor deep in the id range: the build
    feed reads no cursor, so the live adverts below it are still read first."""
    db = _world()
    db.cursors[CURSOR_ENTER] = {"last_listing_id": 5, "last_snapshot_id": 0, "watermark": None}
    assert _entered(_work(db, bootstrap=True))[:3] == [2, 4, 6]


def test_the_build_feed_keeps_the_settle_lag() -> None:
    db = _world()
    db.locations[2]["resolved_at"] = db.now - timedelta(seconds=5)
    assert 2 not in _entered(_work(db, bootstrap=True, lag=300))
    db.now += timedelta(minutes=10)
    assert _entered(_work(db, bootstrap=True, lag=300))[:3] == [2, 4, 6]


def test_outside_a_build_the_feed_is_the_id_cursor_it_always_was() -> None:
    """Steady state: the same statement, the same id order after the cursor, the cursor moved
    to the last id taken (or wrapped on a short claim)."""
    db = _world()
    ordinary = _work(db)
    taken = _entered(ordinary, limit=10)
    assert taken == [1, 2], "a fifth of the claim, in id order"
    assert S.RT_SCOPE_ENTRANTS_SQL in db.statements
    assert S.RT_SCOPE_BUILD_ENTRANTS_SQL not in db.statements
    assert _decide(db, ordinary, taken)[CURSOR_ENTER][0] == 2

    later = _work(db)
    assert _entered(later, limit=10) == [3, 4], "after the cursor, by id, live or not"
    db.cursors[CURSOR_ENTER] = {"last_listing_id": 4, "last_snapshot_id": 0, "watermark": None}
    db.statements.clear()
    assert _entered(_work(db), limit=50) == [5, 6]
    assert S.RT_SCOPE_BUILD_ENTRANTS_SQL not in db.statements


def test_the_build_statement_reads_is_active_by_primary_key_and_takes_no_cursor() -> None:
    flat = " ".join(S.RT_SCOPE_BUILD_ENTRANTS_SQL.split())
    assert "%(after_id)s" not in flat
    assert ("order by (select l.is_active from public.listings l where l.id = s.listing_id) "
            "desc nulls last, s.listing_id limit %(limit)s") in flat
    assert ("not exists (select 1 from autodedup.rt_fp f where f.generation = "
            "%(generation)s::text and f.listing_id = s.listing_id)") in flat
    assert "make_interval(secs => %(lag)s)" in flat
    # The steady-state statement is untouched: no `public` table, the id keyset.
    steady = " ".join(S.RT_SCOPE_ENTRANTS_SQL.split())
    assert "public." not in steady
    assert "s.listing_id > %(after_id)s::bigint" in steady and "order by s.listing_id" in steady


def test_a_widening_build_reads_the_new_blocks_live_adverts_first(tmp_path: Path) -> None:
    """The live path end to end: a widening opens the build, the pass reads three adverts, and
    the three it reads are the new block's live ones although their ids are the highest."""
    conn = _built_town(tmp_path)
    for listing_id in list(NEW_IDS)[:3]:
        conn.listings[listing_id]["is_active"] = False
    _seed(conn, tmp_path, f"obec:{TOWN} obec:{OTHER_TOWN}")
    assert conn.settings[bootstrap_key(GEN)] is True
    conn.settings[pass_rate_key(GEN)] = 3.5 / 787.5          # three adverts a build pass

    out = run_incremental(lambda: conn)

    assert not out["aborted"] and out["feeds"].get("entered") == 3
    read = {i for (g, i) in conn.rt_fp if g == GEN} & set(NEW_IDS)
    live = set(list(NEW_IDS)[3:])
    assert live <= read, "the live adverts, before the lower inactive ids"
    # The straggler sweep (the last rows of `listings`, by id) is not the build's feed and keeps
    # its own fifth; nothing else reads an inactive advert of the new block.
    assert len(read - live) <= out["feeds"].get("straggler", 0)
    assert conn.cursors[CURSOR_ENTER]["last_listing_id"] == 0
