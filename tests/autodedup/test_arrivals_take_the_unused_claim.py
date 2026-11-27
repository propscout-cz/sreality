"""Arrivals take what the forward feeds leave of a claim (2026-11-27).

Every forward feed is held to a fifth of the limit, and the room they left went to the rescore
sweep: with 1,584 in-scope arrivals unread (the oldest a day), a pass of 51 adverts read 10 of
them and gave 10 to a re-read of adverts it already held. The publication gate holds a new
property on the engine's read, so the unused room is the arrivals' first.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from autodedup.incremental_lane import CURSOR_NEW, FEED_WINDOW, SqlWork, rescore_key
from autodedup.incremental_scope import Scope, ScopeBlock
from tests.autodedup.fake_pg import FakePg

GEN = "rt"
TOWN = 563510
SCOPE = Scope((ScopeBlock("obec", TOWN),))


def _arrivals(db: FakePg, ids: range, town: int = TOWN) -> None:
    for listing_id in ids:
        db.listings[listing_id] = {"id": listing_id, "is_active": True,
                                   "first_seen_at": db.now - timedelta(hours=1)}
        db.locations[listing_id] = {"obec_kod": town}


def _work(db: FakePg, **kwargs: Any) -> SqlWork:
    return SqlWork(db, SCOPE, GEN, straggler_window=0, revive_slice=0, drift_slice=0,
                   enter_slice=0, evidence_slice=0, **kwargs)


def _feeds(items: list[Any]) -> dict[str, int]:
    out: dict[str, int] = {}
    for item in items:
        out[item.feed] = out.get(item.feed, 0) + 1
    return out


def test_with_the_other_feeds_quiet_the_arrivals_take_the_whole_claim() -> None:
    db = FakePg()
    _arrivals(db, range(101, 171))
    work = _work(db)
    items = work.claim(50)
    assert _feeds(items) == {"new": 50}, "a fifth, then the forty the other feeds left"
    assert [item.listing_id for item in items] == list(range(101, 151)), "in id order"
    assert work.waiting == {"new": 20, "changed": 0, "flipped": 0} and work.claim_bound["cut"] is True
    work.commit(items)
    assert db.cursors[CURSOR_NEW]["last_listing_id"] == 150, "the cursor stops at the last taken"
    again = _work(db)
    rest = again.claim(50)
    assert [item.listing_id for item in rest] == list(range(151, 171))
    assert again.waiting["new"] == 0 and again.claim_bound["cut"] is False
    again.commit(rest)
    assert db.cursors[CURSOR_NEW]["last_listing_id"] == 170


def test_a_claim_the_arrivals_do_not_fill_is_theirs_whole_and_the_cursor_ends_the_window() -> None:
    db = FakePg()
    _arrivals(db, range(101, 113))
    _arrivals(db, range(113, 116), town=577626)
    work = _work(db)
    items = work.claim(50)
    assert [item.listing_id for item in items] == list(range(101, 113)), "in scope only"
    assert work.waiting == {"new": 0, "changed": 0, "flipped": 0}
    work.commit(items)
    assert db.cursors[CURSOR_NEW]["last_listing_id"] == 115, "past the window, out of scope too"


def test_the_sweep_takes_what_the_arrivals_leave_never_less_than_its_fifth() -> None:
    db = FakePg()
    _arrivals(db, range(101, 171))
    before = db.now - timedelta(hours=2)
    for listing_id in range(1, 41):
        db.locations[listing_id] = {"obec_kod": TOWN}
        db.rt_fp[(GEN, listing_id)] = {"is_active": True, "first_decided_at": before,
                                       "ev_complete": True, "block_key": None,
                                       "updated_at": before}
    record = {"since": db.now.isoformat(), "settings": "w31"}
    db.settings[rescore_key(GEN)] = dict(record)
    # Arrivals waiting: they fill the limit and the sweep keeps the fifth it is never denied.
    work = _work(db, rescore=record)
    assert _feeds(work.claim(50)) == {"new": 50, "rescore": 10}
    # No arrival waiting: the sweep has the whole claim, as before.
    quiet = FakePg()
    quiet.rt_fp, quiet.locations = dict(db.rt_fp), dict(db.locations)
    quiet.settings[rescore_key(GEN)] = dict(record)
    assert _feeds(_work(quiet, rescore=record).claim(50)) == {"rescore": 40}


def _held(db: FakePg, ids: range) -> None:
    before = db.now - timedelta(hours=2)
    for listing_id in ids:
        db.locations[listing_id] = {"obec_kod": TOWN}
        db.rt_fp[(GEN, listing_id)] = {"is_active": True, "first_decided_at": before,
                                       "ev_complete": True, "block_key": None,
                                       "updated_at": before}


def test_the_re_claims_riding_a_pass_keep_their_slots() -> None:
    """E971: the stale re-claims ride on top of the claim, so the arrivals' top-up leaves them
    their room: a pass holds at most the limit plus the sweep's room, as before the top-up."""
    db = FakePg()
    _arrivals(db, range(101, 301))
    _held(db, range(1, 61))
    stale = list(range(1, 31))
    # A steady pass with re-claims and arrivals waiting, no sweep: the limit and no more.
    work = _work(db, stale_claims=stale, stale_waiting=30)
    feeds = _feeds(work.claim(50))
    assert feeds == {"new": 20, "stale": 30} and sum(feeds.values()) == 50
    # With a sweep owed: the sweep's room on top and nothing else.
    record = {"since": db.now.isoformat(), "settings": "w31"}
    work = _work(db, stale_claims=stale, stale_waiting=30, rescore=record)
    feeds = _feeds(work.claim(50))
    assert feeds["stale"] == 30 and feeds["new"] == 20
    assert sum(feeds.values()) <= 50 + 10, feeds
    # A retry of the claim carries no re-claims (E960: first attempt only): arrivals take it.
    assert _feeds(work.claim(50))["new"] == 50


def test_more_re_claims_than_the_limit_leave_the_arrivals_their_fifth_only() -> None:
    db = FakePg()
    _arrivals(db, range(101, 301))
    _held(db, range(1, 51))
    work = _work(db, stale_claims=list(range(1, 51)), stale_waiting=50)
    feeds = _feeds(work.claim(50))
    assert feeds == {"new": 10, "stale": 50}, "main's 60, not the 100 a blind top-up would hold"


def test_a_builds_first_attempt_with_re_claims_holds_what_it_held_before() -> None:
    """E964 x E971: in a build the re-claims sit INSIDE the limit (the entrant feed shrinks by
    them); the arrivals' top-up must not fill the slots that shrink left open."""
    db = FakePg()
    _arrivals(db, range(1001, 1401))
    _held(db, range(1, 201))
    work = _work(db, bootstrap=True, stale_claims=list(range(1, 201)), stale_waiting=200)
    feeds = _feeds(work.claim(200))
    limit = work.claim_bound["limit"]
    assert feeds == {"new": max(1, limit // 5), "stale": 200}, (
        "the arrivals' fifth and the re-claims, never the limit + re-claims + arrivals")


def test_the_waiting_count_is_bounded_by_the_window() -> None:
    db = FakePg()
    _arrivals(db, range(1, FEED_WINDOW + 500))
    work = _work(db)
    items = work.claim(50)
    assert len(items) == 50 and work.waiting["new"] == FEED_WINDOW - 50, (
        "at least: the window holds no more")


def test_the_heartbeat_carries_what_each_feed_left_waiting() -> None:
    from scraper import realtime_worker as rw

    last = rw._autodedup_outcome(0.0, summary={
        "counts": {"claimed": 51}, "aborted": "", "reconcile": {"counts": {}},
        "waiting": {"new": 950, "changed": 40, "flipped": 0}})
    assert last["waiting"] == {"new": 950, "changed": 40, "flipped": 0}
