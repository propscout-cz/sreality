"""E980: the evidence sweep probes the newest ACTIVE rows still marked incomplete on every pass,
ahead of its round-robin. On 2026-10-26 a widening had filled the round-robin with 90,984
candidates (400 a pass, ~50 h a cycle, longer than the 48 h horizon), so an active arrival
decided before its CLIP vector waited out the cycle: four ads of one Hradec Králové flat stayed
four properties for 16 h."""
from __future__ import annotations

from datetime import timedelta
from typing import Any

from autodedup.incremental_lane import (
    EVIDENCE_ACTIVE_RECHECK_MINUTES,
    EVIDENCE_ACTIVE_REDECIDE_MAX,
    EVIDENCE_ACTIVE_SLICE,
    SqlWork,
)
from autodedup.incremental_scope import Scope, ScopeBlock
from autodedup.incremental_sql import RT_EVIDENCE_ACTIVE_SQL
from tests.autodedup.fake_pg import FakePg

TOWN = Scope((ScopeBlock("obec", 569810),))


def _row(db: FakePg, listing_id: int, *, active: bool, complete: bool, hours_ago: float,
         images: int = 3, clip: int = 0, written_minutes_ago: float | None = None) -> None:
    db.rt_fp[("rt", listing_id)] = {
        "category_main": None, "category_type": None, "area_m2": None, "disposition": None,
        "floor": None, "fp_digest": "d", "cell_key": "o1", "cell_group": "byt",
        "is_active": active, "ev_images": images, "ev_phash": images, "ev_clip": clip,
        "ev_tags": clip, "ev_complete": complete,
        "first_decided_at": db.now - timedelta(hours=hours_ago),
        "updated_at": db.now - (timedelta(hours=hours_ago) if written_minutes_ago is None
                                else timedelta(minutes=written_minutes_ago)),
    }


def _gallery(db: FakePg, listing_id: int, *, images: int = 3, tagged: bool) -> None:
    for seq in range(images):
        db.image_rows.append({
            "image_id": listing_id * 100 + seq, "listing_id": listing_id, "sequence": seq,
            "storage_path": f"p{seq}", "phash": 1000 + seq,
            "clip_tagged_at": db.now if tagged else None})


def _active_ids(db: FakePg, limit: int) -> list[int]:
    cur = db.cursor()
    cur.execute(RT_EVIDENCE_ACTIVE_SQL, {"generation": "rt", "limit": limit,
                                         "recheck_minutes": EVIDENCE_ACTIVE_RECHECK_MINUTES})
    return [int(row[0]) for row in cur.fetchall()]


def _work(db: FakePg, **kw: Any) -> SqlWork:
    return SqlWork(db, TOWN, "rt", straggler_window=0, revive_slice=0, drift_slice=0,
                   enter_slice=0, **kw)


def _young_and_settled(db: FakePg, ids: range) -> None:
    """The widening's shape: rows read inside the horizon whose probe agrees with them."""
    for listing_id in ids:
        _row(db, listing_id, active=False, complete=True, hours_ago=2, clip=3)
        _gallery(db, listing_id, tagged=True)


def test_an_active_arrival_beyond_the_round_robin_is_re_claimed_in_the_next_pass() -> None:
    """The defect: the round-robin's slice ends at id 3, the arrival is id 1,000. Its CLIP
    vectors arrived after its first decision, so its probe disagrees with `rt_fp`."""
    db = FakePg()
    _young_and_settled(db, range(1, 11))
    _row(db, 1000, active=True, complete=False, hours_ago=16)
    _gallery(db, 1000, tagged=True)
    work = _work(db, evidence_slice=3)

    items = work.claim(50)

    assert [(item.listing_id, item.feed, item.redecide) for item in items] == [
        (1000, "evidence", True)]
    assert work.evidence["active_candidates"] == 1 and work.evidence["candidates"] == 3

    without = _work(db, evidence_slice=3, evidence_active_slice=0)
    assert without.claim(50) == [], "without the arm the round-robin never reaches it"


def test_the_arm_takes_only_active_rows_still_marked_incomplete() -> None:
    db = FakePg()
    _row(db, 1, active=False, complete=False, hours_ago=2)
    _row(db, 2, active=True, complete=True, hours_ago=2, clip=3)
    _row(db, 3, active=True, complete=False, hours_ago=2)
    _row(db, 4, active=True, complete=None, hours_ago=2)  # type: ignore[arg-type]

    assert _active_ids(db, 10) == [3]


def test_the_newest_first_decision_comes_first_and_the_slice_bounds_it() -> None:
    db = FakePg()
    _row(db, 5, active=True, complete=False, hours_ago=30)
    _row(db, 6, active=True, complete=False, hours_ago=2)
    _row(db, 7, active=True, complete=False, hours_ago=10)

    assert _active_ids(db, 2) == [6, 7]


def test_the_arm_s_moved_rows_take_the_share_before_the_round_robin_s() -> None:
    """`share` is a fifth of the claim: with room for one evidence item, the active arrival
    wins over a moved inactive row the round-robin found."""
    db = FakePg()
    _row(db, 2, active=False, complete=False, hours_ago=2)
    _gallery(db, 2, tagged=True)
    _row(db, 1000, active=True, complete=False, hours_ago=2)
    _gallery(db, 1000, tagged=True)

    items = _work(db).claim(5)

    assert [item.listing_id for item in items if item.feed == "evidence"] == [1000]


def test_the_cursor_follows_the_round_robin_rows_alone() -> None:
    """A full round-robin slice moves the cursor to ITS last id, never to the active arm's
    higher one — else every id in between would be skipped for a whole cycle."""
    db = FakePg()
    _young_and_settled(db, range(1, 6))
    _row(db, 1000, active=True, complete=False, hours_ago=2)
    _gallery(db, 1000, tagged=False)
    work = _work(db, evidence_slice=3)

    work.claim(50)

    assert work._pending["rt_evidence"] == 3


def test_the_cursor_stops_before_a_round_robin_mover_the_share_cut() -> None:
    """With room for one evidence item the arm's arrival takes it; the round-robin's moved row
    2 is cut, so the cursor stops at 1 and offers it again, never stepping past it."""
    db = FakePg()
    _row(db, 1, active=False, complete=True, hours_ago=2, clip=3)
    _gallery(db, 1, tagged=True)
    _row(db, 2, active=False, complete=False, hours_ago=2)
    _gallery(db, 2, tagged=True)
    _row(db, 3, active=False, complete=True, hours_ago=2, clip=3)
    _gallery(db, 3, tagged=True)
    _row(db, 4, active=False, complete=True, hours_ago=2, clip=3)
    _row(db, 1000, active=True, complete=False, hours_ago=2)
    _gallery(db, 1000, tagged=True)
    work = _work(db, evidence_slice=3)

    items = work.claim(5)

    assert [item.listing_id for item in items if item.feed == "evidence"] == [1000]
    assert work._pending["rt_evidence"] == 1 and work.evidence["cut"] == 1


def test_a_row_the_lane_just_wrote_waits_out_the_recheck_gate() -> None:
    """An undecodable image is stamped `clip_tagged_at` with no vector: the probe says 3, the
    decision 2, for ever. The arm re-decides such a row at most once an hour, not every pass."""
    db = FakePg()
    _row(db, 1000, active=True, complete=False, hours_ago=5, clip=2, written_minutes_ago=10)
    _gallery(db, 1000, tagged=True)

    assert _active_ids(db, 10) == []

    db.rt_fp[("rt", 1000)]["updated_at"] = db.now - timedelta(minutes=90)
    assert _active_ids(db, 10) == [1000]


def test_the_arm_re_decides_at_most_its_own_count_a_pass() -> None:
    """E980b: a share of 100 would take 40 moved arrivals; the arm hands on 15 of them, the
    newest first decisions first, and the rest wait for the next pass."""
    db = FakePg()
    for n in range(40):
        _row(db, 1000 + n, active=True, complete=False, hours_ago=2 + n)
        _gallery(db, 1000 + n, tagged=True)

    items = _work(db, evidence_slice=1, rate_per_s=100.0).claim(500)

    taken = [item.listing_id for item in items if item.feed == "evidence"]
    assert len(taken) == EVIDENCE_ACTIVE_REDECIDE_MAX == 15
    assert taken == list(range(1000, 1015))


def test_the_arm_is_off_in_a_build() -> None:
    """E980b: a build pass already runs near its deadline, so there the feed is E92's
    round-robin alone and an arrival beyond its slice waits, as before E980."""
    db = FakePg()
    _young_and_settled(db, range(1, 11))
    _row(db, 1000, active=True, complete=False, hours_ago=16)
    _gallery(db, 1000, tagged=True)
    work = _work(db, evidence_slice=3, bootstrap=True)

    items = work.claim(50)

    assert [item.listing_id for item in items if item.feed == "evidence"] == []
    assert work.evidence["active_candidates"] == 0


def test_evidence_slice_zero_still_turns_the_whole_feed_off() -> None:
    db = FakePg()
    _row(db, 1000, active=True, complete=False, hours_ago=2)
    _gallery(db, 1000, tagged=True)

    assert _work(db, evidence_slice=0).claim(50) == []


def test_the_statement_reads_the_partial_index_and_the_default_slice_is_bounded() -> None:
    """`ev_complete = false` is `autodedup_rt_fp_evidence_idx`'s predicate (an `is not true`
    would also take the NULL rows and the table with them)."""
    assert "f.ev_complete = false" in RT_EVIDENCE_ACTIVE_SQL
    assert "and f.is_active" in RT_EVIDENCE_ACTIVE_SQL
    assert "order by f.first_decided_at desc nulls last, f.listing_id" in RT_EVIDENCE_ACTIVE_SQL
    assert "f.updated_at < now() - make_interval(mins => %(recheck_minutes)s::int)" in (
        RT_EVIDENCE_ACTIVE_SQL)
    assert "limit %(limit)s" in RT_EVIDENCE_ACTIVE_SQL
    assert 0 < EVIDENCE_ACTIVE_SLICE <= 2000
