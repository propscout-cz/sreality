"""E977 — during a build, a block whose walk is complete holds only its own unread adverts.

E964's rail held every group with an advert in a block whose snapshot still held an advert with
no fingerprint, so the Praha build (one block, 37,910 unread adverts) merged nothing until its
last advert was read. A block walked to its end has a whole snapshot: what it still owes are
adverts the build has not read, not adverts the walk has not seen. During a build such a block
now holds a group only while one of the group's own adverts (a member, or a rider it carries,
R2) has no fingerprint. A block never walked or mid-walk keeps the block-level hold, an advert
the snapshot does not place keeps F3's rule, and outside a build nothing changes.
"""

from __future__ import annotations

from typing import Any

from autodedup import apply as AP
from autodedup import reconcile
from autodedup.incremental_lane import PAGES_SETTING
from autodedup.incremental_sql import RT_KNOWN_SQL
from tests.autodedup.fake_pg import FakePg
from tests.autodedup.test_apply import _pair_group
from tests.autodedup.test_one_lane import BLOCK, RT, LaneDb

UNREAD = 99                      # an advert of the block the build has not read yet


class ReadDb(LaneDb):
    """LaneDb whose fingerprints are `read` alone: a group member is not fingerprinted for
    being a member, so a member the build has not read can be stated."""

    def dispatch(self, sql: str, p: dict) -> list[tuple]:
        if sql == RT_KNOWN_SQL:
            return [(int(i),) for i in p["ids"] if int(i) in self.read]
        return super().dispatch(sql, p)


def _world(*, read: tuple[int, ...] = (10, 11)) -> ReadDb:
    """Group 10 = adverts 10 and 11 on properties 100 and 200, both placed in BLOCK by the
    snapshot; the block also holds UNREAD, which no pass has fingerprinted."""
    db = ReadDb()
    db.live_scope()
    _pair_group(db, 10, [10, 11], [100, 200], gen=RT)
    for lid in (10, 11, UNREAD):
        db.snapshot[lid] = BLOCK
    db.read.update(read)
    return db


def _run(db: LaneDb, *, building: bool) -> dict[str, Any]:
    return reconcile.run(db, RT, [], run_id="rt:test", deadline=10 ** 9, blocks=[BLOCK],
                         merge=db.merge([]), clock=lambda: 0.0, now=lambda: db.now,
                         building=building)


def test_a_walked_block_still_reading_merges_a_group_whose_adverts_are_all_read() -> None:
    db = _world()
    assert db.walked == {BLOCK}
    out = _run(db, building=True)
    assert out["counts"]["applied"] == 1 and reconcile.WAITING not in out["counts"]
    assert db.listings[11]["property_id"] == 100


def test_a_group_with_one_unread_member_still_waits() -> None:
    db = _world(read=(10,))
    out = _run(db, building=True)
    assert out["counts"][reconcile.WAITING] == 1 and out["counts"]["applied"] == 0
    db.read.add(11)
    assert _run(db, building=True)["counts"]["applied"] == 1, "read, it merges at once"


def test_a_group_whose_carried_rider_is_unread_still_waits() -> None:
    """R2: property 200 also holds advert 12, which a merge moves along and no pass has read."""
    db = _world()
    db.listing(12, 200)
    db.snapshot[12] = BLOCK
    out = _run(db, building=True)
    assert out["counts"][reconcile.WAITING] == 1 and out["counts"]["applied"] == 0
    db.read.add(12)
    counts = _run(db, building=True)["counts"]
    assert reconcile.WAITING not in counts, "read, the rail lets it through"
    assert counts["skipped_by_reason"] == {"carries_ungrouped_listings": 1}, (
        "to the plan's own rule for an advert no group holds")


def test_a_never_walked_block_still_holds_every_group() -> None:
    db = _world()
    db.walked = set()
    out = _run(db, building=True)
    assert out["counts"][reconcile.WAITING] == 1 and out["counts"]["applied"] == 0


def test_an_advert_the_snapshot_does_not_place_keeps_the_block_rule() -> None:
    """F3: advert 11 is in BLOCK by its live location only — a walk has not listed it, so its
    block's completeness says nothing about it, and the block still owes UNREAD."""
    db = _world()
    del db.snapshot[11]
    out = _run(db, building=True)
    assert out["counts"][reconcile.WAITING] == 1 and out["counts"]["applied"] == 0


def test_steady_state_keeps_the_block_rule() -> None:
    """Outside a build the block still holds every group while it owes an advert: unchanged."""
    db = _world()
    out = _run(db, building=False)
    assert out["counts"][reconcile.WAITING] == 1 and out["counts"]["applied"] == 0
    db.read.add(UNREAD)
    assert _run(db, building=False)["counts"]["applied"] == 1


def test_the_retire_step_keeps_the_block_rule() -> None:
    """W8's gate reads both reasons together: a block that owes an advert is refused."""
    db = _world()
    assert AP.rt_unread_blocks(db, RT, [BLOCK]) == {BLOCK}
    assert AP.rt_block_reads(db, RT, [BLOCK]) == (set(), {BLOCK})


# ------------------------------------------------- the two reasons, over the lane's own tables


def _lane(pages_of: int | None, *, scanned: bool = True) -> FakePg:
    """BLOCK's snapshot holds 1, 2 (fingerprinted) and 3 (not); `pages_of` is the block's page
    row (`of` 0: inside its first cycle of pages), None for no page row (read whole at once)."""
    db = FakePg()
    for lid in (1, 2, 3):
        db.scope_ids[(RT, BLOCK, lid)] = {"resolved_at": None, "refreshed_at": db.now}
    for lid in (1, 2):
        db.rt_fp[(RT, lid)] = {"is_active": True}
    if scanned:
        db.scope_scans.append({"generation": RT, "block_key": BLOCK, "scanned_at": db.now,
                               "rows_found": 3, "elapsed_ms": 1.0})
    if pages_of is not None:
        db.settings[f"{PAGES_SETTING}:{RT}"] = {BLOCK: {"after": 2, "of": pages_of}}
    return db


def _adverts(*ids: int) -> list[AP.Member]:
    return [AP.Member(lid, None, None, None, 563510, None, None) for lid in ids]


def test_rt_block_reads_names_the_two_reasons_apart() -> None:
    assert AP.rt_block_reads(_lane(None), RT, [BLOCK]) == (set(), {BLOCK})
    assert AP.rt_block_reads(_lane(1), RT, [BLOCK]) == (set(), {BLOCK}), "a later cycle"
    assert AP.rt_block_reads(_lane(0), RT, [BLOCK]) == ({BLOCK}, {BLOCK}), "mid first cycle"
    assert AP.rt_block_reads(_lane(None, scanned=False), RT, [BLOCK]) == ({BLOCK}, {BLOCK})


def test_a_complete_block_holds_only_its_unread_adverts_during_a_build() -> None:
    db = _lane(None)
    assert reconcile._unread(db, RT, _adverts(1, 2, 3), [BLOCK], building=True) == {3}
    assert reconcile._unread(db, RT, _adverts(1, 2, 3), [BLOCK], building=False) == {1, 2, 3}


def test_a_mid_walk_or_never_walked_block_holds_every_advert_during_a_build() -> None:
    for db in (_lane(0), _lane(None, scanned=False)):
        assert reconcile._unread(db, RT, _adverts(1, 2, 3), [BLOCK], building=True) == {1, 2, 3}
