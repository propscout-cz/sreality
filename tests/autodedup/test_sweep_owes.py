"""E998: while a rescore sweep runs, a pair nobody has decided and neither side of which the pass
refreshed is left to the sweep, which still owes one of its sides a re-read.

The w34 switch widened retrieval (C4c's window), so every neighbour of a claimed advert wanted
pairs w31 had vetoed before a decision: one pass of 8 adverts was asked for 74k-150k of them, hit
its deadline and committed nothing, and the lane read about a dozen arrivals an hour (2026-11-26).
The sweep re-claims every in-scope advert once and decides ALL the pairs of an advert it
refreshes, so those pairs have an owner with a cursor; an arrival's pass need not pay for them.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from autodedup import incremental, incremental_lane
from autodedup import incremental_sql as S
from autodedup.incremental import Limits, run_pass
from autodedup.incremental_lane import SqlStore, rescore_key, run_incremental
from autodedup.incremental_store import CohortFacts, MemoryStore, Schedule
from tests.autodedup import lane_world
from tests.autodedup.fake_pg import FakePg
from tests.autodedup.test_incremental import _settings
from tests.autodedup.test_reject_ledger import (
    FLOOR,
    GEN,
    MODEL,
    _drained,
    _key,
    _mixed_dataset,
    _touching,
)
from tests.autodedup.test_rt_rescore import (
    C4C,
    _advert,
    _built_w31,
    _fingerprints,
    _groups,
    _pairs,
    _quiesce,
    _seed,
    _vector,
)

EVERYWHERE: dict[str, Any] = {"all_scope": True, "obec": [], "cast_obce": [], "okres": []}


def _sweeping(db: FakePg, calibration: Any) -> SqlStore:
    """A store under a sweep ordered a moment ago: every row it holds is owed a re-read."""
    return SqlStore(db, GEN, store_floor=FLOOR, **_key(calibration),
                    sweep={"since": (db.now - timedelta(seconds=1)).isoformat(), **EVERYWHERE})


def _unanswered() -> tuple[Any, FakePg, Any]:
    """A drained store with no ledger row: every reject is a pair nobody has an answer for, as
    the pairs only the new settings retrieve are."""
    ds = _mixed_dataset()
    db, calibration = _drained(ds)
    assert db.reject_ledger
    db.reject_ledger.clear()
    db.now += timedelta(minutes=10)
    return ds, db, calibration


def test_a_refreshed_side_is_never_left_to_the_sweep(monkeypatch: pytest.MonkeyPatch) -> None:
    scored: list[tuple[int, int]] = []
    real = incremental.decide_pair

    def spy(fa: Any, fb: Any, *args: Any, **kwargs: Any) -> Any:
        scored.append(tuple(sorted((fa.listing_id, fb.listing_id))))
        return real(fa, fb, *args, **kwargs)

    monkeypatch.setattr(incremental, "decide_pair", spy)
    ds, db, calibration = _unanswered()
    target = sorted(i for i in ds.listings if i >= 7000)[3]
    db.now += timedelta(seconds=60)
    scored.clear()
    out = run_pass(_sweeping(db, calibration), CohortFacts(ds),
                   Schedule([target], redecide=True), _settings(), MODEL, calibration,
                   limits=Limits(max_listings=4, max_pairs=10 ** 9), generation=GEN)
    decided = set(scored[:out.pairs_scored])
    assert out.sweep_owed > 0 and out.ledger_skipped == 0
    assert decided and decided == _touching(decided, {target}), (
        "only the refreshed advert's pairs are decided; its neighbours' wait for their re-read")
    assert out.pairs_to_decide == len(decided) == out.pairs_scored


def test_without_a_sweep_nothing_is_left_and_no_statement_is_spent() -> None:
    ds, db, calibration = _unanswered()
    target = sorted(i for i in ds.listings if i >= 7000)[3]
    db.now += timedelta(seconds=60)
    out = run_pass(SqlStore(db, GEN, store_floor=FLOOR, **_key(calibration)), CohortFacts(ds),
                   Schedule([target], redecide=True), _settings(), MODEL, calibration,
                   limits=Limits(max_listings=4, max_pairs=10 ** 9), generation=GEN)
    assert out.sweep_owed == 0 and S.RT_SWEEP_OWED_SQL not in db.statements
    assert MemoryStore().owed([(1, 2)]) == set()


def test_the_sweep_decides_what_was_left_and_ends_where_a_sweep_that_left_nothing_does(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """The whole sweep, 4 adverts a pass, with and without the rule: the same pairs, clusters
    and ledger at its end, and far fewer decisions in its first pass."""
    ends: dict[bool, Any] = {}
    first: dict[bool, int] = {}
    for rule in (True, False):
        ds, db, calibration = _unanswered()
        since = (db.now - timedelta(seconds=1)).isoformat()
        work = Schedule(sorted(ds.listings), redecide=True)
        while not work.exhausted():
            db.now += timedelta(seconds=60)
            store = SqlStore(db, GEN, store_floor=FLOOR, **_key(calibration),
                             sweep={"since": since, **EVERYWHERE} if rule else None)
            out = run_pass(store, CohortFacts(ds), work, _settings(), MODEL, calibration,
                           limits=Limits(max_listings=4, max_pairs=10 ** 9), generation=GEN)
            first.setdefault(rule, out.pairs_scored)
        snap = db.snapshot()
        ends[rule] = ({k: v for k, v in snap["pairs"].items()}, snap["cluster_members"],
                      snap["clusters"], {k: (v["dd_lo"], v["dd_hi"])
                                         for k, v in snap["reject_ledger"].items()})
        assert not store.owed([key[1:] for key in db.reject_ledger]), "every advert was re-read"
    assert ends[True] == ends[False]
    assert first[True] < first[False], first


def test_owed_reads_the_sweeps_own_test_refreshed_since_and_in_scope() -> None:
    conn = lane_world.world()
    stamp = conn.now
    for listing_id, at in ((4000, stamp - timedelta(hours=2)), (4001, stamp - timedelta(hours=2)),
                           (4002, stamp), (4003, stamp)):
        conn.rt_fp[(GEN, listing_id)] = {"updated_at": at, "is_active": True}
    since = (stamp - timedelta(hours=1)).isoformat()
    scope = incremental_lane.pass_scope(lane_world.SCOPE).params()
    store = SqlStore(conn, GEN, calibration_digest="c", sweep={"since": since, **scope})
    assert store.owed([(4000, 4001), (4000, 4002), (4002, 4003), (4002, 9999)]) == {
        (4000, 4001), (4000, 4002)}, "one side not re-read since the switch is enough"
    # A LATER switch (w34 then w35) moves `since`: what the first sweep re-read is owed again.
    later = SqlStore(conn, GEN, calibration_digest="c",
                     sweep={"since": (stamp + timedelta(hours=1)).isoformat(), **scope})
    assert later.owed([(4002, 4003)]) == {(4002, 4003)}
    # An advert the sweep will never read (outside the scope) is owed to nobody: its pairs are
    # decided at once, as without a sweep.
    nowhere = SqlStore(conn, GEN, calibration_digest="c", sweep={
        "since": since, "all_scope": False, "obec": [], "cast_obce": [], "okres": []})
    assert nowhere.owed([(4000, 4001)]) == set()


def _switched(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, rule: bool
              ) -> tuple[FakePg, list[dict[str, Any]], list[Any]]:
    """A w31 store switched to w34 at 4 adverts a pass, with an ARRIVAL beside the window pair
    after the sweep's first pass and a re-priced advert after its second."""
    monkeypatch.setattr(incremental_lane, "PASS_LIMITS",
                        Limits(max_listings=4, max_component=400))
    sweeps: list[Any] = []
    real = SqlStore.__init__

    def built(self: SqlStore, *args: Any, **kwargs: Any) -> None:
        sweeps.append(kwargs.get("sweep"))
        real(self, *args, **({**kwargs, "sweep": None} if not rule else kwargs))

    monkeypatch.setattr(SqlStore, "__init__", built)
    conn = _built_w31(tmp_path)
    _seed(conn, tmp_path, "w34", recut="true", rescore="true")
    passes: list[dict[str, Any]] = []
    conn.now += timedelta(minutes=10)
    passes.append(run_incremental(lambda: conn))
    _advert(conn, 4300, "remax", [0x7777_0000_1111_0001 + 13 * k for k in range(3)],
            disposition="3+kk", area_m2=64.0, usable_area=64.0, price_czk=7_100_000,
            description="Byt 3+kk s lodzii, puvodni stav, klidna ulice u parku. " * 3)
    for row in conn.image_rows:
        if row["listing_id"] == 4300:
            conn.clip_vectors[int(row["image_id"])] = _vector(int(row["phash"]) % 97)
            row["clip_tagged_at"] = lane_world.EXPORTED_AT
    conn.now += timedelta(minutes=10)
    passes.append(run_incremental(lambda: conn))
    conn.listings[4002]["price_czk"] += 50_000
    conn.snapshots.append({"id": max(row["id"] for row in conn.snapshots) + 1,
                           "listing_id": 4002, "scraped_at": conn.now - timedelta(minutes=5),
                           "price_czk": conn.listings[4002]["price_czk"]})
    conn.now += timedelta(minutes=10)
    passes.extend(_quiesce(conn))
    assert not [p for p in passes if p["aborted"]]
    return conn, passes, sweeps


def test_arrivals_between_the_sweeps_passes_end_where_they_end_without_the_rule(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with monkeypatch.context() as patch:
        ruled, passes, sweeps = _switched(tmp_path, patch, rule=True)
    with monkeypatch.context() as patch:
        plain, plain_passes, _ = _switched(tmp_path, patch, rule=False)
    assert sum(p["counts"]["sweep_owed"] for p in passes) > 0, "the rule left pairs to the sweep"
    assert sum(p["counts"]["sweep_owed"] for p in plain_passes) == 0
    assert ruled.settings[rescore_key(GEN)]["done"] is True
    # The lane hands the store the sweep while one runs, and nothing once it is done.
    record = ruled.settings[rescore_key(GEN)]
    during = [sweep for sweep in sweeps if sweep]
    assert sweeps[0] is None, "the w31 build runs no sweep"
    assert during and {sweep["since"] for sweep in during} == {record["since"]}
    assert sweeps[-1] is None
    assert _pairs(ruled) == _pairs(plain) and C4C in _pairs(ruled)
    assert _groups(ruled) == _groups(plain)
    assert _fingerprints(ruled) == _fingerprints(plain)
    assert {row for row in ruled.fp_key if row[0] == GEN} == {
        row for row in plain.fp_key if row[0] == GEN}
    assert set(ruled.reject_ledger) == set(plain.reject_ledger)
    assert 4300 in {i for (_g, i) in ruled.rt_fp}
