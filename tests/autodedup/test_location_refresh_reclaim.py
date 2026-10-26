"""A: the drift sweep re-claims an in-scope advert whose stored block key is not today's.

An inactive advert appends no snapshot, so when the resolver later moved its location no feed
saw it and its postings kept the block of its first claim (research experiments/
absent_attribution.md M3 and V3: 3,105 rows). The sweep compares the stored KEY with
`listing_location`'s, never a resolver timestamp, and rides the drift sweep's share.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from autodedup import incremental_sql as S
from autodedup.dataset import Location
from autodedup.fingerprint import block_key_of
from autodedup.incremental import Limits, run_pass
from autodedup.incremental_lane import CURSOR_SCOPE, SqlStore, SqlWork
from autodedup.incremental_scope import Scope, ScopeBlock
from autodedup.incremental_store import CohortFacts, Schedule
from tests.autodedup.fake_pg import FakePg
from tests.autodedup.test_incremental import _calibration, _dataset, _settings, arrival_order
from tests.autodedup.test_reject_ledger import CLAIM, FLOOR, GEN, MODEL, _key

PRAHA, VYSOCANY, TURNOV, LIBEREC = 554782, 490245, 577626, 563889
SCOPE = Scope((ScopeBlock("obec", TURNOV), ScopeBlock("obec", LIBEREC),
               ScopeBlock("cast_obce", VYSOCANY)))
_SETTLED = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)


def _row(block_key: str | None, is_active: bool = False) -> dict[str, Any]:
    return {"category_main": "byt", "category_type": "prodej", "area_m2": 60.0,
            "disposition": "2+kk", "floor": 3, "fp_digest": "d", "cell_key": "o1",
            "cell_group": "byt|prodej", "is_active": is_active, "ev_images": 0, "ev_phash": 0,
            "ev_clip": 0, "ev_tags": 0, "ev_complete": True, "first_decided_at": _SETTLED,
            "updated_at": _SETTLED, "block_key": block_key}


def _place(db: FakePg, listing_id: int, obec: int | None, cast: int | None = None,
           resolved_at: datetime | None = None) -> None:
    db.locations[listing_id] = {"obec_kod": obec, "cast_obce_kod": cast,
                                "resolved_at": resolved_at or _SETTLED}


def _sweep(db: FakePg, **kwargs: Any) -> SqlWork:
    return SqlWork(db, SCOPE, GEN, revive_slice=0, enter_slice=0, evidence_slice=0, **kwargs)


def _relocated(items: list) -> list[int]:
    return [item.listing_id for item in items if item.feed == "relocated"]


def test_the_sweep_compares_the_block_key_and_never_a_resolver_stamp() -> None:
    assert "resolved_at" not in S.RT_SCOPE_DRIFT_SQL
    assert "f.block_key is not null" in S.RT_SCOPE_DRIFT_SQL
    assert "then 'c' || ll.cast_obce_kod::text" in S.RT_SCOPE_DRIFT_SQL
    assert "then 'o' || ll.obec_kod::text" in S.RT_SCOPE_DRIFT_SQL
    assert block_key_of(PRAHA, VYSOCANY) == f"c{VYSOCANY}"
    assert block_key_of(TURNOV, None) == f"o{TURNOV}" and block_key_of(None, None) == ""


def test_an_ad_whose_location_moved_to_another_town_is_reclaimed() -> None:
    db = FakePg()
    db.rt_fp[(GEN, 51)] = _row(f"o{TURNOV}")
    db.rt_fp[(GEN, 52)] = _row(f"o{TURNOV}")
    db.rt_fp[(GEN, 53)] = _row("")
    _place(db, 51, LIBEREC)                    # moved inside the scope
    _place(db, 52, TURNOV)                     # where it was
    _place(db, 53, LIBEREC)                    # a town-less advert resolved since
    items = _sweep(db).claim(50)
    assert _relocated(items) == [51, 53]
    assert not any(item.retire or item.redecide for item in items)


def test_a_re_resolution_onto_the_same_block_is_not_reclaimed() -> None:
    db = FakePg()
    later = _SETTLED + timedelta(days=30)
    db.rt_fp[(GEN, 61)] = _row(f"c{VYSOCANY}")
    db.rt_fp[(GEN, 62)] = _row(None)
    _place(db, 61, PRAHA, VYSOCANY, resolved_at=later)   # re-resolved, same quarter
    _place(db, 62, LIBEREC, resolved_at=later)           # moved, but a row from before 677
    work = _sweep(db)
    assert _relocated(work.claim(50)) == []
    assert work.windows["relocated"] == 0


def test_an_ad_that_left_the_scope_is_retired_not_relocated() -> None:
    db = FakePg()
    db.rt_fp[(GEN, 71)] = _row(f"o{TURNOV}")
    _place(db, 71, 999999)
    items = _sweep(db).claim(50)
    assert [(item.listing_id, item.feed, item.retire) for item in items] == [
        (71, "drifted", True)]


def test_the_cap_is_the_drift_share_retirements_first() -> None:
    db = FakePg()
    db.rt_fp[(GEN, 100)] = _row(f"o{TURNOV}")
    _place(db, 100, 999999)                              # departed
    for listing_id in range(101, 111):
        db.rt_fp[(GEN, listing_id)] = _row(f"o{TURNOV}")
        _place(db, listing_id, LIBEREC)
    work = _sweep(db, max_retire_fraction=1.0)
    items = work.claim(10)                               # five feeds, a share of two
    assert [i.listing_id for i in items if i.retire] == [100]
    assert _relocated(items) == [101]
    assert work.windows["relocated"] == 10
    assert work.commit(items)[CURSOR_SCOPE] == 101, "the cursor stops at the last one taken"
    db.rt_fp.pop((GEN, 100))                             # what the pass does with each
    db.rt_fp[(GEN, 101)]["block_key"] = f"o{LIBEREC}"
    taken = []
    for _pass in range(6):
        work = _sweep(db)
        items = work.claim(10)
        assert len(_relocated(items)) <= 2
        taken += _relocated(items)
        work.commit(items)
        for listing_id in _relocated(items):
            db.rt_fp[(GEN, listing_id)]["block_key"] = f"o{LIBEREC}"
    assert taken == list(range(102, 111)), "two a pass, each once, none skipped"
    assert _relocated(_sweep(db).claim(10)) == []


def test_a_cut_with_nothing_taken_holds_the_cursor() -> None:
    db = FakePg()
    for listing_id in (200, 201):
        db.rt_fp[(GEN, listing_id)] = _row(f"o{TURNOV}")
        _place(db, listing_id, 999999)                   # departed: the whole share
    db.rt_fp[(GEN, 202)] = _row(f"o{TURNOV}")
    _place(db, 202, LIBEREC)
    db.cursors[CURSOR_SCOPE] = {"last_listing_id": 150}
    work = _sweep(db, max_retire_fraction=1.0)
    items = work.claim(10)
    assert _relocated(items) == [] and len([i for i in items if i.retire]) == 2
    assert work.commit(items)[CURSOR_SCOPE] == 150


def test_a_reclaimed_ad_is_refreshed_and_then_left_alone() -> None:
    """End to end over the SQL store: the moved advert's row and postings take today's block,
    and the next sweep finds nothing to do."""
    ds = _dataset(8)
    settings = _settings()
    calibration = _calibration(ds, settings)
    db = FakePg()
    for listing_id in ds.listings:
        _place(db, listing_id, PRAHA, VYSOCANY)
    work = Schedule(arrival_order(ds))
    while not work.exhausted():
        run_pass(SqlStore(db, GEN, store_floor=FLOOR, **_key(calibration)), CohortFacts(ds),
                 work, settings, MODEL, calibration,
                 limits=Limits(max_listings=CLAIM, max_pairs=10 ** 9), generation=GEN)
    assert {row["block_key"] for row in db.rt_fp.values()} == {f"c{VYSOCANY}"}
    moved = sorted(ds.listings)[3]
    ds.listings[moved].location = Location(obec_kod=TURNOV)
    _place(db, moved, TURNOV)
    before = {token for g, probe, token, i in db.fp_key if i == moved and probe == "attr_dispo"}

    sweep = _sweep(db)
    out = run_pass(SqlStore(db, GEN, store_floor=FLOOR, **_key(calibration)), CohortFacts(ds),
                   sweep, settings, MODEL, calibration,
                   limits=Limits(max_listings=50, max_pairs=10 ** 9), generation=GEN)
    assert out.feeds == {"relocated": 1} and out.claimed == [moved]
    assert db.rt_fp[(GEN, moved)]["block_key"] == f"o{TURNOV}"
    after = {token for g, probe, token, i in db.fp_key if i == moved and probe == "attr_dispo"}
    assert before and after and before != after
    assert all(token.startswith(f"o{TURNOV}") for token in after)
    assert _relocated(_sweep(db).claim(50)) == []
