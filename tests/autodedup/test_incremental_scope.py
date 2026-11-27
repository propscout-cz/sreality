"""E79 — the real-time lane's SCOPE and its storage budget, exercised.

W9 built a lane whose four feeds claimed the arrivals of the WHOLE corpus: ~178,000 new
listings in 30 days, a fingerprint, 17.3 postings and a pair fan-out for each, into a store the
operator pays for by the megabyte. Everything below is a rail on the two rules that close it —
a pass holds only what `rt_scope` holds, and no pass writes anything while the schema is over
`rt_max_schema_mb` — plus the two things scoping OWES: a feed has to cross a corpus that is
99.4% out of scope without stalling, and a listing that LEAVES the scope has to be retired
rather than left half-indexed.
"""

from __future__ import annotations

from dataclasses import replace as dc_replace
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from autodedup import cohort
from autodedup.incremental import Limits, WorkItem, run_pass
from autodedup.incremental_lane import (
    CURSOR_CHANGED,
    CURSOR_ENTER,
    CURSOR_EVIDENCE,
    CURSOR_NEW,
    CURSOR_SCOPE,
    MAX_SCHEMA_MB,
    SCOPE_SETTING,
    STORAGE_WATERMARK,
    RetireRefusal,
    SqlStore,
    SqlWork,
    resolve_scope_parents,
    run_incremental,
    scope_setting_key,
    storage_guard,
)
from autodedup.incremental_sql import (
    RT_GENERATION_BYTES_SQL,
    RT_IDLE_GUARD_SQL,
    RT_LOCK_GUARD_SQL,
    RT_STATEMENT_GUARD_SQL,
)
from autodedup.incremental_scope import (
    CORPUS_PROJECTION_MB,
    DEFAULT_SCOPE,
    Scope,
    ScopeBlock,
    ScopeError,
    parse_scope,
    resolve_scope,
)
from autodedup.incremental_store import MemoryStore
from autodedup.model import hand_initialised
from autodedup.incremental_store import CohortFacts
from autodedup.store_score import storable
from tests.autodedup.fake_pg import EMPTY_DIGEST, EMPTY_PAYLOAD, FakePg
from tests.autodedup.test_incremental import _calibration, _dataset, _drain, _settings, arrival_order

GEN = "rt"
SCOPE = Scope((ScopeBlock("obec", 563510), ScopeBlock("cast_obce", 490245)))


def _place(db: FakePg, listing_id: int, *, obec: int | None = None,
           cast_obce: int | None = None) -> None:
    db.locations[int(listing_id)] = {"obec_kod": obec, "cast_obce_kod": cast_obce}


def _calibrated(db: FakePg, generation: str = GEN) -> FakePg:
    """A SEEDED generation. A pass over an unseeded one is a green skip (W9e/R1), so every
    test about what a pass REFUSES has to seed it first."""
    db.calibration[generation] = {"digest": EMPTY_DIGEST, "n_listings": 3,
                                  "payload_zlib": EMPTY_PAYLOAD, "settings": {},
                                  "model_version": "hand_v1"}
    return db


# A SETTLED store row: its photographs were counted when the generation decided it, long
# enough ago that the evidence sweep (E92) has no business in it. A hand-written row that left
# these NULL would be claimed by the sweep on every pass — unmeasured is not the same as
# nothing to measure — which is what `test_shipped_w9h.py` proves it does.
_SETTLED = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)


def _fp_row(is_active: bool = True) -> dict:
    return {"category_main": None, "category_type": None, "area_m2": None,
            "disposition": None, "floor": None, "fp_digest": "d",
            "cell_key": "o1", "cell_group": "byt", "is_active": is_active,
            "ev_images": 0, "ev_phash": 0, "ev_clip": 0, "ev_tags": 0,
            "ev_complete": True, "first_decided_at": _SETTLED}


# ------------------------------------------------------------------- the scope as data


def test_the_default_scope_is_the_trial_blocks_and_never_the_negative_control() -> None:
    """The negative control is a RULE over Praha address groups evaluated at export time. It
    has no arrival feed, so nothing in the corpus could tell the lane a new listing belongs to
    it — it is out of every real-time scope by construction, not by omission."""
    assert [(b.grain, b.code) for b in DEFAULT_SCOPE.blocks] == [
        ("obec", 563510), ("obec", 577626), ("cast_obce", 490245)]
    assert not DEFAULT_SCOPE.whole_corpus
    negctl = cohort.block_by_key("negctl")
    assert negctl.grain == "assembled"
    assert all(block.code != negctl.code for block in DEFAULT_SCOPE.blocks)


def test_a_scope_reads_from_a_token_string_a_json_list_or_all() -> None:
    assert parse_scope("obec:563510 cast_obce:490245").blocks == SCOPE.blocks
    assert parse_scope([{"grain": "obec", "code": 563510},
                        {"grain": "cast_obce", "code": "490245"}]).blocks == SCOPE.blocks
    assert parse_scope("all").whole_corpus is True
    assert parse_scope("obec:1 obec:1").blocks == (ScopeBlock("obec", 1),)
    with pytest.raises(ScopeError):
        parse_scope("kraj:27")                       # E961 added `okres`; there is no kraj grain
    with pytest.raises(ScopeError):
        parse_scope("obec:not-a-number")


def test_an_empty_or_missing_scope_is_a_hard_error_never_the_whole_corpus() -> None:
    for empty in ("", "   ", [], {}):
        with pytest.raises(ScopeError):
            parse_scope(empty)
    with pytest.raises(ScopeError):
        parse_scope(None)
    # A PRESENT but empty setting is the dangerous one: it must not fall back to the default.
    with pytest.raises(ScopeError):
        resolve_scope(None, [])
    with pytest.raises(ScopeError):
        resolve_scope("", [])
    assert resolve_scope(None, None) == DEFAULT_SCOPE
    assert resolve_scope("obec:563510 cast_obce:490245", None).blocks == SCOPE.blocks
    # The argument wins over the row, and both win over the default.
    assert resolve_scope("all", [{"grain": "obec", "code": 1}]).whole_corpus is True


def test_the_lane_refuses_an_empty_scope_setting() -> None:
    # SEEDED (W9e/R1): an UNseeded generation is a green skip, and only a seeded one can have a
    # scope row that is wrong.
    conn = _calibrated(FakePg())
    conn.settings[scope_setting_key(GEN)] = []
    with pytest.raises(SystemExit) as raised:
        run_incremental(lambda: conn)
    assert SCOPE_SETTING in str(raised.value)
    assert not conn.cursors and not conn.rt_fp


# ------------------------------------------------------------------ the storage budget


def test_the_guard_refuses_a_pass_when_the_schema_is_over_budget() -> None:
    conn = _calibrated(FakePg())
    conn.schema_bytes = int((MAX_SCHEMA_MB + 100) * 1_048_576)
    conn.settings[scope_setting_key(GEN)] = [{"grain": "obec", "code": 563510}]
    with pytest.raises(SystemExit) as raised:
        run_incremental(lambda: conn)
    assert f"over the {MAX_SCHEMA_MB:.0f} MB" in str(raised.value)
    # Loud, non-zero, and nothing moved: no lease taken, no cursor written, no row stored.
    assert not conn.lease and not conn.cursors and not conn.rt_fp and not conn.fp_key


def test_a_pass_is_budgeted_on_the_schema_as_it_stands_never_a_projection() -> None:
    """E916's projection is the fresh seed's alone: a pass deletes nothing first, so what the
    generation holds is never subtracted from what it is about to add to."""
    conn = _calibrated(FakePg())
    conn.schema_bytes = int((MAX_SCHEMA_MB + 1) * 1_048_576)
    conn.table_bytes = {"rt_fp": 1_000 * 1_048_576}
    conn.rt_fp[(GEN, 1)] = _fp_row()
    conn.settings[scope_setting_key(GEN)] = [{"grain": "obec", "code": 563510}]
    with pytest.raises(SystemExit, match="over the"):
        run_incremental(lambda: conn)
    assert RT_GENERATION_BYTES_SQL not in conn.statements
    assert not conn.lease and not conn.cursors


def test_the_guard_reports_size_rows_and_growth_since_the_last_pass() -> None:
    conn = FakePg()
    conn.schema_bytes = 100 * 1_048_576
    conn.settings[STORAGE_WATERMARK] = {"bytes": 90 * 1_048_576}
    conn.rt_fp[(GEN, 1)] = _fp_row()
    conn.fp_key.add((GEN, "img", "t1", 1))
    report = storage_guard(conn, GEN, SCOPE, 400)
    assert report["schema_mb"] == 100.0
    assert report["growth_mb"] == 10.0
    assert report["rows"]["rt_fp"] == 1 and report["rows"]["fp_key"] == 1
    assert report["scope"] == SCOPE.as_json()


def test_whole_corpus_needs_the_budget_to_agree_as_well_as_the_setting() -> None:
    conn = FakePg()
    conn.schema_bytes = 10 * 1_048_576
    every = Scope((), whole_corpus=True)
    with pytest.raises(Exception) as raised:
        storage_guard(conn, GEN, every, 400)
    assert str(CORPUS_PROJECTION_MB) in str(raised.value)
    # With a budget that could hold it, `all` is allowed — the two halves are ONE decision.
    assert storage_guard(conn, GEN, every, CORPUS_PROJECTION_MB)["schema_mb"] == 10.0


def test_the_trial_budget_never_enables_the_whole_corpus() -> None:
    """E916 raised MAX_SCHEMA_MB for the TRIAL's working set; `all` stays a separate budget
    decision, so the lane's own default must still refuse it."""
    assert MAX_SCHEMA_MB < CORPUS_PROJECTION_MB
    conn = FakePg()
    conn.schema_bytes = 10 * 1_048_576
    with pytest.raises(Exception) as raised:
        storage_guard(conn, GEN, Scope((), whole_corpus=True))
    assert str(CORPUS_PROJECTION_MB) in str(raised.value)
    assert storage_guard(conn, GEN, SCOPE)["max_schema_mb"] == MAX_SCHEMA_MB


# ------------------------------------------------------------------------- every feed


def _feeds_db() -> FakePg:
    """One in-scope and one out-of-scope listing on each of the four public feeds."""
    db = FakePg()
    old = db.now - timedelta(hours=2)
    for listing_id, obec in ((21, 563510), (22, 999999)):          # changed
        db.listings[listing_id] = {"first_seen_at": db.now - timedelta(days=9),
                                   "inactive_at": None, "is_active": True}
        _place(db, listing_id, obec=obec)
        db.rt_fp[(GEN, listing_id)] = _fp_row()
        db.snapshots.append({"id": listing_id, "listing_id": listing_id,
                             "scraped_at": db.now - timedelta(days=9)})
        db.snapshots.append({"id": 100 + listing_id, "listing_id": listing_id,
                             "scraped_at": old})
    for listing_id, obec in ((31, 563510), (32, 999999)):          # flipped
        db.listings[listing_id] = {"first_seen_at": db.now - timedelta(days=9),
                                   "inactive_at": old, "is_active": False}
        _place(db, listing_id, obec=obec)
        db.rt_fp[(GEN, listing_id)] = _fp_row()
    for listing_id, obec in ((41, 563510), (42, 999999)):          # revived
        db.listings[listing_id] = {"first_seen_at": db.now - timedelta(days=9),
                                   "inactive_at": None, "is_active": True}
        _place(db, listing_id, obec=obec)
        db.rt_fp[(GEN, listing_id)] = _fp_row(is_active=False)
    db.cursors[CURSOR_NEW] = {"last_listing_id": 50}
    for listing_id, obec in ((111, 563510), (112, 999999)):        # new
        db.listings[listing_id] = {"first_seen_at": old, "inactive_at": None,
                                   "is_active": True}
        _place(db, listing_id, obec=obec)
    return db


def test_every_feed_claims_only_what_the_scope_holds() -> None:
    db = _feeds_db()
    # The straggler feed too: an id behind the `new` cursor with no fingerprint row.
    db.listings[9] = {"first_seen_at": db.now - timedelta(hours=2), "inactive_at": None,
                      "is_active": True}
    db.listings[8] = {"first_seen_at": db.now - timedelta(hours=2), "inactive_at": None,
                      "is_active": True}
    _place(db, 9, obec=563510)
    _place(db, 8, obec=999999)

    # Half this fixture's store is out of scope on purpose, so W9d-1's rail is opened for it:
    # what is under test here is WHICH feed may name an out-of-scope listing.
    items = SqlWork(db, SCOPE, GEN, max_retire_fraction=1.0).claim(50)
    by_feed = {feed: sorted(item.listing_id for item in items if item.feed == feed)
               for feed in {item.feed for item in items}}
    assert by_feed["straggler"] == [9]
    assert by_feed["new"] == [111]
    assert by_feed["changed"] == [21]
    assert by_feed["flipped"] == [31]
    assert by_feed["revived"] == [41]
    # The only feed that may NAME an out-of-scope listing is the drift sweep, and it names it
    # to retire it (22/32/42 are in this generation's store and have left the scope).
    assert all(item.listing_id not in (8, 112) for item in items)
    assert all(item.retire for item in items
               if item.listing_id in (22, 32, 42))
    assert sorted(item.listing_id for item in items if item.retire) == [22, 32, 42]


def test_a_quarter_code_is_a_scope_of_its_own() -> None:
    """`cast_obce_kod` carries no index of its own, which is exactly why the predicate is
    resolved per window id rather than scanned — the grain still has to WORK."""
    db = FakePg()
    db.listings[71] = {"first_seen_at": db.now - timedelta(hours=2), "inactive_at": None,
                       "is_active": True}
    db.listings[72] = {"first_seen_at": db.now - timedelta(hours=2), "inactive_at": None,
                       "is_active": True}
    _place(db, 71, obec=554782, cast_obce=490245)
    _place(db, 72, obec=554782, cast_obce=490246)
    items = SqlWork(db, SCOPE, GEN).claim(50)
    assert [item.listing_id for item in items if item.feed == "new"] == [71]


def test_a_window_with_nothing_in_scope_still_advances_its_cursor() -> None:
    """The progress rail. The scope holds 0.6% of the corpus, so a feed that advanced only
    over survivors would need as many passes as the corpus has arrivals to reach the handful
    that matter — and would never finish."""
    db = FakePg()
    old = db.now - timedelta(hours=2)
    for listing_id in range(200, 260):
        db.listings[listing_id] = {"first_seen_at": old, "inactive_at": None,
                                   "is_active": True}
        _place(db, listing_id, obec=999999)
        db.snapshots.append({"id": listing_id, "listing_id": listing_id, "scraped_at": old})
    work = SqlWork(db, SCOPE, GEN, window=25, revive_slice=0, drift_slice=0)
    items = work.claim(50)
    assert items == []
    cursors = work.commit(items)
    assert cursors[CURSOR_NEW] == 224 and cursors[CURSOR_CHANGED] == 224
    # And it keeps going: the next pass crosses the next window rather than the same one.
    work.claim(50)
    assert work.commit([])[CURSOR_NEW] == 249


def test_a_window_with_more_in_scope_than_the_share_stops_at_what_it_took() -> None:
    db = FakePg()
    old = db.now - timedelta(hours=2)
    for listing_id in range(300, 315):
        db.listings[listing_id] = {"first_seen_at": old, "inactive_at": None,
                                   "is_active": True}
        _place(db, listing_id, obec=563510)
    work = SqlWork(db, SCOPE, GEN, window=25, revive_slice=0, drift_slice=0, enter_slice=0)
    # Five feeds, so a share of two; the other feeds are quiet, so the arrivals take the rest
    # of the claim, and no more than the claim.
    items = work.claim(10)
    assert [item.listing_id for item in items] == list(range(300, 310))
    assert work.commit(items)[CURSOR_NEW] == 309, "the window end would skip 310-314"


def test_a_refused_pass_advances_no_cursor_even_across_an_empty_window() -> None:
    db = _feeds_db()
    work = SqlWork(db, SCOPE, GEN, max_retire_fraction=1.0)
    work.claim(50)
    work.commit([])  # E75: the pair budget refused this claim
    # The evidence sweep's pointer is a ROUND-ROBIN position over a read-only probe, not a
    # watermark over work: it handed nothing over, so nothing it could have skipped was
    # skipped, and a short slice wraps it to 0. Every real watermark is where it was.
    assert db.cursors == {CURSOR_NEW: {"last_listing_id": 50},
                          CURSOR_EVIDENCE: {"last_listing_id": 0}}


# --------------------------------------------------------------- leaving the scope


def test_the_drift_sweep_claims_a_listing_the_scope_stopped_holding() -> None:
    db = FakePg()
    db.rt_fp[(GEN, 51)] = _fp_row()
    db.rt_fp[(GEN, 52)] = _fp_row()
    _place(db, 51, obec=563510)
    _place(db, 52, obec=999999)      # the geocoder moved it out
    items = SqlWork(db, SCOPE, GEN).claim(50)
    drifted = [item for item in items if item.feed == "drifted"]
    assert [item.listing_id for item in drifted] == [52]
    assert all(item.retire for item in drifted)
    assert SqlWork(db, SCOPE, GEN).claim(50) and db.cursors.get(CURSOR_SCOPE) is None


def test_a_listing_that_leaves_the_scope_is_retired_not_left_half_indexed() -> None:
    """Half-indexed is worse than unindexed: the postings stay, so every neighbour goes on
    retrieving a listing nothing maintains, and the component keeps an edge to it."""
    ds = _dataset(12)
    settings = _settings()
    calibration = _calibration(ds, settings)
    store, _passes = _drain(ds, settings, calibration, arrival_order(ds))
    victim = sorted(ds.listings)[0]
    assert store.keys.get(victim) and store.known([victim]) == {victim}
    before = [key for key in store.pairs if victim in key]
    assert before, "the fixture must give the retired listing at least one pair"

    class _Retire:
        def claim(self, limit: int) -> list[WorkItem]:
            return [WorkItem(victim, "drifted", None, None, retire=True)]

        def commit(self, done) -> dict:
            return {}

    result = run_pass(store, CohortFacts(ds), _Retire(), settings, hand_initialised(),
                      calibration, limits=Limits(max_listings=50, max_pairs=10 ** 9))
    assert result.retired == 1
    assert not store.keys.get(victim), "postings survived the retirement"
    assert store.known([victim]) == set()
    assert [key for key in store.pairs if victim in key] == []
    assert all(victim not in members for members in store.clusters.values())


def test_a_retired_listing_is_never_also_refreshed() -> None:
    ds = _dataset(6)
    settings = _settings()
    calibration = _calibration(ds, settings)
    store = MemoryStore()
    victim = sorted(ds.listings)[0]

    class _Both:
        def claim(self, limit: int) -> list[WorkItem]:
            return [WorkItem(victim, "new", None, None),
                    WorkItem(victim, "drifted", None, None, retire=True)]

        def commit(self, done) -> dict:
            return {}

    result = run_pass(store, CohortFacts(ds), _Both(), settings, hand_initialised(),
                      calibration, limits=Limits(max_listings=50, max_pairs=10 ** 9))
    assert result.claimed == []
    assert store.known([victim]) == set()


# ------------------------------------------------------------------ the retention rule


def test_the_store_keeps_exactly_what_the_batch_lane_keeps() -> None:
    """`score_lane.persist` writes `storable(row, store_floor)` — the whole merge and band
    zones, plus the reject tail at or above the floor. The real-time store mirrors it, so the
    two generations hold the same rows and the store grows with duplicates, not comparisons."""
    from autodedup.incremental import PairRow

    conn = FakePg()
    store = SqlStore(conn, GEN, store_floor=0.02, calibration_digest="c0ffee")

    def _row(lo: int, hi: int, zone: str, score: float) -> PairRow:
        return PairRow(lo=lo, hi=hi, probes=["img"], from_lo=True, from_hi=False,
                       zone=zone, score=score, families=["IMG"], certificate=None,
                       veto="", reason="r", evidence={}, context={"block": "o1"},
                       fp_lo="a", fp_hi="b")

    rows = [_row(1, 2, "merge", 0.99), _row(3, 4, "band", 0.4),
            _row(5, 6, "reject", 0.5), _row(7, 8, "reject", 0.001),
            _row(9, 10, "veto", 0.0001)]
    store.upsert_pairs(rows)
    store.flush()
    kept = {(lo, hi) for _gen, lo, hi in conn.pairs}
    assert kept == {(1, 2), (3, 4), (5, 6)}
    assert store.pairs_retained == 3 and store.pairs_evicted == 2
    for row in rows:
        assert (storable({"zone": row.zone, "score": row.score}, 0.02)
                == ((row.lo, row.hi) in kept)), "the two lanes disagree about a row"


def test_a_pair_that_falls_below_the_floor_on_a_re_score_is_deleted() -> None:
    from autodedup.incremental import PairRow

    conn = FakePg()
    store = SqlStore(conn, GEN, store_floor=0.02, calibration_digest="c0ffee")
    kept = PairRow(lo=1, hi=2, probes=["img"], from_lo=True, from_hi=True, zone="band",
                   score=0.4, families=["IMG"], certificate=None, veto="", reason="r",
                   evidence={}, context={"block": "o1"}, fp_lo="a", fp_hi="b")
    store.upsert_pairs([kept])
    store.flush()
    assert (GEN, 1, 2) in conn.pairs
    store.upsert_pairs([dc_replace(kept, zone="reject", score=0.001)])
    store.flush()
    assert (GEN, 1, 2) not in conn.pairs, "a demoted pair must not linger in the store"


# ------------------------------------------------------------ the summary the operator reads


def test_the_pass_summary_carries_the_scope_the_budget_and_the_growth() -> None:
    conn = FakePg()
    conn.schema_bytes = 64 * 1_048_576
    conn.calibration[GEN] = {"digest": EMPTY_DIGEST, "n_listings": 3,
                             "payload_zlib": EMPTY_PAYLOAD, "settings": {},
                             "model_version": "hand_v1"}
    conn.settings[scope_setting_key(GEN)] = [{"grain": "obec", "code": 563510}]
    out = run_incremental(lambda: conn)
    assert out["scope"] == [{"grain": "obec", "code": 563510}]
    assert out["storage"]["schema_mb"] == 64.0
    assert out["storage"]["max_schema_mb"] == MAX_SCHEMA_MB
    assert "pass_growth_mb" in out["storage"] and "rows" in out["storage"]
    assert out["retention"]["store_floor"] > 0
    # The growth watermark is written, so the NEXT pass can report growth against this one.
    assert conn.settings[STORAGE_WATERMARK]["bytes"] == conn.schema_bytes


# ------------------------------------------------- W9d: the four defects the verification found
#
# W9d-1 a separator-only scope parsed to an EMPTY, non-whole-corpus scope and the drift sweep
# then reported every row of the store as departed; W9d-2 a dispatch-arg scope retired everything
# the seeded scope holds without ever being persisted; W9d-3 nothing claimed a listing whose
# location resolved INTO the scope after the cursor had passed it, and the straggler look-back
# was an id RANGE rather than a row count; W9d-4 the session guards were set outside the pass's
# transaction on a transaction-pooled connection. (They are the VERIFICATION's numbering: the
# program's own D1/D4 are the cohort-block and shadow-posture rulings.)


def _empty_scope() -> Scope:
    """An empty scope the constructor now refuses, built around it — the drift sweep's own
    refusal has to stand on its own feet (W9d-1)."""
    scope = object.__new__(Scope)
    object.__setattr__(scope, "blocks", ())
    object.__setattr__(scope, "whole_corpus", False)
    return scope


def test_a_separator_only_scope_is_a_hard_error_not_a_null_scope() -> None:
    for raw in (",", " , , ", ",,", ";", " ; "):
        with pytest.raises(ScopeError):
            parse_scope(raw)
    with pytest.raises(ScopeError):
        Scope(())
    with pytest.raises(ScopeError):
        parse_scope([[]])


def test_the_drift_sweep_refuses_to_retire_more_than_the_safety_fraction() -> None:
    """The W9d-1 scenario end to end: a scope that holds nothing would report the WHOLE store as
    departed. The sweep refuses rather than retiring it, and no cursor moves."""
    db = FakePg()
    for listing_id in range(1, 101):
        db.rt_fp[(GEN, listing_id)] = _fp_row()
        _place(db, listing_id, obec=999999)
    work = SqlWork(db, SCOPE, GEN)
    with pytest.raises(RetireRefusal) as raised:
        work.claim(50)
    assert "retire" in str(raised.value).lower()
    assert not db.cursors and len(db.rt_fp) == 100


def test_the_drift_sweep_refuses_outright_on_an_empty_scope() -> None:
    db = FakePg()
    db.rt_fp[(GEN, 1)] = _fp_row()
    _place(db, 1, obec=563510)
    with pytest.raises(RetireRefusal):
        SqlWork(db, _empty_scope(), GEN).claim(50)


def test_a_handful_of_departures_is_still_retired() -> None:
    """The guard is a rail against a store-wide wipe, not against the drift it exists for."""
    db = FakePg()
    for listing_id in range(1, 101):
        db.rt_fp[(GEN, listing_id)] = _fp_row()
        _place(db, listing_id, obec=563510)
    _place(db, 7, obec=999999)
    items = SqlWork(db, SCOPE, GEN).claim(50)
    assert [item.listing_id for item in items if item.retire] == [7]


def test_the_lane_stops_loudly_when_the_drift_sweep_refuses() -> None:
    conn = FakePg()
    conn.calibration[GEN] = {"digest": EMPTY_DIGEST, "n_listings": 3,
                             "payload_zlib": EMPTY_PAYLOAD, "settings": {},
                             "model_version": "hand_v1"}
    conn.settings[scope_setting_key(GEN)] = [{"grain": "obec", "code": 563510}]
    for listing_id in range(1, 101):
        conn.rt_fp[(GEN, listing_id)] = _fp_row()
        _place(conn, listing_id, obec=999999)
    with pytest.raises(SystemExit) as raised:
        run_incremental(lambda: conn)
    assert "retire" in str(raised.value).lower()
    assert not conn.cursors and len(conn.rt_fp) == 100


# -------------------------------------------------------- W9d-2: the seeded scope is the truth


def _seeded(scope_json: Any) -> FakePg:
    conn = FakePg()
    conn.calibration[GEN] = {"digest": EMPTY_DIGEST, "n_listings": 3,
                             "payload_zlib": EMPTY_PAYLOAD, "settings": {},
                             "model_version": "hand_v1"}
    conn.settings[scope_setting_key(GEN)] = scope_json
    return conn


def test_a_seeded_generation_with_no_scope_row_is_a_hard_error() -> None:
    """Never a silent fall back to the default: the row IS the generation's scope."""
    conn = FakePg()
    conn.calibration[GEN] = {"digest": EMPTY_DIGEST, "n_listings": 3,
                             "payload_zlib": EMPTY_PAYLOAD, "settings": {},
                             "model_version": "hand_v1"}
    with pytest.raises(SystemExit) as raised:
        run_incremental(lambda: conn)
    assert SCOPE_SETTING in str(raised.value)
    assert not conn.cursors and not conn.rt_fp


# ----------------------------------------------------------- W9d-3: the drift-IN feed


def test_the_straggler_sweep_looks_back_a_row_count_not_an_id_range() -> None:
    """Ids are sparse (21.7 id units a row on the live corpus), so an id RANGE of 5,000 is a
    few hundred rows, not 5,000."""
    db = FakePg()
    for listing_id in (1_000, 40_000, 80_000):
        db.listings[listing_id] = {"first_seen_at": db.now - timedelta(hours=2),
                                   "inactive_at": None, "is_active": True}
        _place(db, listing_id, obec=563510)
    db.cursors[CURSOR_NEW] = {"last_listing_id": 100_000}
    work = SqlWork(db, SCOPE, GEN, straggler_window=2, revive_slice=0, drift_slice=0,
                   enter_slice=0)
    stragglers = [item.listing_id for item in work.claim(50) if item.feed == "straggler"]
    assert stragglers == [40_000, 80_000]


# ------------------------------------------- E987: outside a build the entrant feed completes blocks

TOWN, QUARTER = "obec:563510", "cast_obce:490245"


def _post_brake_world(town_unread: int, praha_unread: int) -> FakePg:
    """Production's shape at 2026-11-17 15:15Z: every block's snapshot holds unread adverts above
    BOTH cursors, the entrant's id cursor ahead of `rt_new`, no walk due. The town's adverts sit
    at ids the new feed reaches last; Praha's quarter holds the rest."""
    db = FakePg()
    db.admin_parents[490245] = 554782
    resolved = db.now - timedelta(hours=8)
    town = list(range(5000, 5000 + town_unread))
    praha = list(range(1001, 1001 + praha_unread))
    for listing_id in town + praha:
        db.listings[listing_id] = {"first_seen_at": resolved, "inactive_at": None,
                                   "is_active": True}
    for listing_id in town:
        _place(db, listing_id, obec=563510)
        db.scope_ids[(GEN, TOWN, listing_id)] = {"resolved_at": resolved}
    for listing_id in praha:
        _place(db, listing_id, obec=554782, cast_obce=490245)
        db.scope_ids[(GEN, QUARTER, listing_id)] = {"resolved_at": resolved}
    for block in (TOWN, QUARTER):
        db.scope_scans.append({"generation": GEN, "block_key": block, "scanned_at": db.now,
                               "rows_found": 0})
    db.settings[f"rt_scope_pages:{GEN}"] = {block: {"of": 1, "page": 1, "after": 0}
                                            for block in (TOWN, QUARTER)}
    db.cursors[CURSOR_NEW] = {"last_listing_id": 1000}
    db.cursors[CURSOR_ENTER] = {"last_listing_id": 1100, "last_snapshot_id": 0}
    return db


def _entrant_pass(db: FakePg, limit: int) -> SqlWork:
    """One steady pass: claim, commit, fingerprint what it claimed (as the pass does)."""
    work = SqlWork(db, SCOPE, GEN, straggler_window=0, revive_slice=0, drift_slice=0,
                   evidence_slice=0, parents={490245: 554782})
    items = work.claim(limit)
    work.commit(items)
    for item in items:
        db.rt_fp[(GEN, item.listing_id)] = _fp_row()
    return work


def test_after_a_brake_the_smallest_block_completes_before_the_backlog_is_read() -> None:
    """2026-11-17: 4,120 unread in 32 of 32 blocks, both cursors below them, no merge from 12:58Z;
    the entrant feed read in id order behind its own cursor, so no block completed before all did.
    At a limit of 15 (a fifth: 3) the town's 15 complete in five passes, judged by the reconcile's
    own predicate, while Praha's 300 still hold their block."""
    from autodedup.apply import rt_block_reads

    db = _post_brake_world(town_unread=15, praha_unread=300)
    assert {TOWN, QUARTER} <= rt_block_reads(db, GEN, [TOWN, QUARTER])[1]
    for _ in range(5):
        work = _entrant_pass(db, 15)
    unwalked, unfingerprinted = rt_block_reads(db, GEN, [TOWN, QUARTER])
    assert TOWN not in unwalked | unfingerprinted, "the town's groups may merge"
    assert QUARTER in unfingerprinted
    assert work.unread_blocks_after() == unfingerprinted, "the pass's one scan, handed on"
    assert db.cursors[CURSOR_ENTER]["last_listing_id"] == 0, "no id cursor outside a build"


@pytest.mark.parametrize(("added", "completes"), [(2, True), (3, False), (4, False)])
def test_a_small_block_the_walks_keep_feeding_completes_only_while_they_add_less_than_a_fifth(
        added: int, completes: bool) -> None:
    """Walks add newer unread adverts to the town before every pass. Fewest first, the town takes
    the entrant's whole fifth (3 at a limit of 15) each pass: fed 2 a pass it is read out and stays
    complete; fed 3 or more it never completes. The new feed never reaches these ids (they sit
    below its cursor), so this is the entrant feed's arithmetic alone."""
    from autodedup.apply import rt_block_reads

    db = _post_brake_world(town_unread=0, praha_unread=300)
    next_id = iter(range(100, 1000))
    complete_at: list[int] = []
    for number in range(40):
        for _ in range(added):
            listing_id = next(next_id)
            db.listings[listing_id] = {"first_seen_at": db.now - timedelta(hours=1),
                                       "inactive_at": None, "is_active": True}
            _place(db, listing_id, obec=563510)
            db.scope_ids[(GEN, TOWN, listing_id)] = {"resolved_at": db.now - timedelta(hours=1)}
        if number == 0:
            for listing_id in range(5000, 5015):    # and a backlog of 15 to begin with
                db.listings[listing_id] = {"first_seen_at": db.now - timedelta(hours=8),
                                           "inactive_at": None, "is_active": True}
                _place(db, listing_id, obec=563510)
                db.scope_ids[(GEN, TOWN, listing_id)] = {
                    "resolved_at": db.now - timedelta(hours=8)}
        _entrant_pass(db, 15)
        if TOWN not in rt_block_reads(db, GEN, [TOWN, QUARTER])[1]:
            complete_at.append(number)
    assert bool(complete_at) is completes
    if completes:
        # 15 + 2 before the first pass, then 2 in and 3 out a pass: read out after the 15th.
        assert complete_at == list(range(14, 40)), "and stays complete"


def test_no_block_waits_for_ever_behind_a_supply_of_smaller_ones() -> None:
    """E987's fairness rule: an unread advert resolved `ENTRANT_MAX_WAIT_S` (72 h) ago or more is
    read before any block order, oldest first. Praha's 300 hold the largest block; small blocks
    arrive every pass; the Praha adverts past 72 h are still read, three a pass, oldest first. One
    resolved 30 h ago (11-17's backlog was 16 h old at deploy) is not overdue: it keeps its block's
    place, so a brake's backlog does not turn the feed into the new feed's id order."""
    db = _post_brake_world(town_unread=0, praha_unread=300)
    for listing_id, hours in ((1290, 80), (1291, 76), (1292, 74), (1293, 30)):
        db.scope_ids[(GEN, QUARTER, listing_id)] = {"resolved_at": db.now - timedelta(hours=hours)}
    town_ids = iter(range(100, 1000))
    for listing_id in [next(town_ids) for _ in range(5)]:
        db.listings[listing_id] = {"first_seen_at": db.now, "inactive_at": None, "is_active": True}
        _place(db, listing_id, obec=563510)
        db.scope_ids[(GEN, TOWN, listing_id)] = {"resolved_at": db.now - timedelta(hours=1)}
    work = SqlWork(db, SCOPE, GEN, straggler_window=0, revive_slice=0, drift_slice=0,
                   evidence_slice=0, parents={490245: 554782})
    entered = [item.listing_id for item in work.claim(15) if item.feed == "entered"]
    assert entered == [1290, 1291, 1292], "the overdue first, oldest first, before the town's 5"


def test_the_entrant_order_reads_fewest_unread_blocks_first() -> None:
    from autodedup.incremental_lane import ENTRANT_MAX_WAIT_S, entrant_order

    hour = 3600.0
    scan = [("okres:2", 7, "t", True, hour), ("okres:1", 9, "t", True, hour),
            ("okres:2", 5, "t", True, hour), ("okres:3", 1, "t", True, hour),
            ("okres:3", 2, "t", True, hour), ("okres:3", 3, "t", True, hour),
            ("okres:1", 8, "t", False, 10.0),                       # inside the settle lag
            ("okres:3", 9, "t", True, hour),                        # in two blocks, taken once
            ("okres:4", 4, "t", True, ENTRANT_MAX_WAIT_S + hour),   # overdue
            ("okres:4", 6, "t", True, None)]                        # never resolved
    assert [lid for lid, _at in entrant_order(scan, 10)] == [6, 4, 9, 5, 7, 1, 2, 3]
    assert [lid for lid, _at in entrant_order(scan, 3)] == [6, 4, 9]


def test_a_listing_whose_location_resolves_into_the_scope_later_is_claimed() -> None:
    """`listing_location` is written asynchronously, so an in-scope listing can carry no obec
    when its window passes. The sixth feed is the only thing that ever claims it."""
    db = FakePg()
    db.listings[10] = {"first_seen_at": db.now - timedelta(days=9), "inactive_at": None,
                       "is_active": True}
    _place(db, 10, obec=563510)                       # resolved AFTER the cursor passed it
    db.cursors[CURSOR_NEW] = {"last_listing_id": 5_000_000}
    work = SqlWork(db, SCOPE, GEN, straggler_window=0, revive_slice=0, drift_slice=0)
    entered = [item.listing_id for item in work.claim(50) if item.feed == "entered"]
    assert entered == [10]
    # Once it holds a fingerprint row the sweep stops offering it.
    db.rt_fp[(GEN, 10)] = _fp_row()
    assert not [item for item in SqlWork(db, SCOPE, GEN, straggler_window=0, revive_slice=0,
                                         drift_slice=0).claim(50) if item.feed == "entered"]


def test_the_entrant_sweep_refreshes_one_block_and_claims_from_the_snapshot() -> None:
    """W9e/R3 moved the cost, not the coverage: the wide walk of a block on `public` fills
    `autodedup.rt_scope_ids` on a cadence, one block at a time, and the pass claims what the
    snapshot holds — so both blocks' entrants are reached without either walk being per-pass."""
    db = FakePg()
    db.admin_parents[490245] = 554782
    for listing_id, place in ((1, {"obec": 563510}), (2, {"cast_obce": 490245})):
        db.listings[listing_id] = {"first_seen_at": db.now - timedelta(days=9),
                                   "inactive_at": None, "is_active": True}
        _place(db, listing_id, obec=place.get("obec", 554782),
               cast_obce=place.get("cast_obce"))
    db.cursors[CURSOR_NEW] = {"last_listing_id": 5_000_000}
    work = SqlWork(db, SCOPE, GEN, straggler_window=0, revive_slice=0, drift_slice=0,
                   parents={490245: 554782})
    first = [item.listing_id for item in work.claim(50) if item.feed == "entered"]
    work.commit([WorkItem(listing_id, "entered", None, None) for listing_id in first])
    for listing_id in first:                      # the pass fingerprints what it decided
        db.rt_fp[(GEN, listing_id)] = _fp_row()
    assert len(db.scope_scans) == 1, "one block's walk a pass, never the scope's"
    db.now += timedelta(hours=12)
    second_work = SqlWork(db, SCOPE, GEN, straggler_window=0, revive_slice=0, drift_slice=0,
                          parents={490245: 554782})
    second = [item.listing_id for item in second_work.claim(50) if item.feed == "entered"]
    assert sorted(first + second) == [1, 2], "both blocks are reached, one walk at a time"


def test_a_quarter_block_needs_its_parent_obec_resolved() -> None:
    db = FakePg()
    db.admin_parents[490245] = 554782
    assert resolve_scope_parents(db, SCOPE) == {490245: 554782}
    with pytest.raises(ScopeError):
        resolve_scope_parents(FakePg(), SCOPE)


def test_a_quarter_beside_its_own_town_is_refused() -> None:
    """E941: `obec:554782` already holds every advert of Praha-Vysočany, so beside it the
    quarter would put them in the snapshot twice and tie Vysočany's merges to the whole town's
    walk. Refused by name; the grammar is unchanged and a quarter of ANOTHER town resolves."""
    db = FakePg()
    db.admin_parents.update({490245: 554782, 490156: 554782})
    beside = parse_scope("obec:563510 obec:554782 cast_obce:490245")
    with pytest.raises(ScopeError,
                       match=r"cast_obce:490245 beside its own town obec:554782"):
        resolve_scope_parents(db, beside)
    quarters = parse_scope("obec:563510 cast_obce:490245 cast_obce:490156")
    assert resolve_scope_parents(db, quarters) == {490245: 554782, 490156: 554782}
    assert resolve_scope_parents(db, parse_scope("obec:554782 obec:563510")) == {}


def test_a_pass_over_a_quarter_beside_its_town_refuses_before_the_lease() -> None:
    conn = _seeded([{"grain": "obec", "code": 554782}, {"grain": "cast_obce", "code": 490245}])
    conn.admin_parents[490245] = 554782
    with pytest.raises(SystemExit, match="beside its own town"):
        run_incremental(lambda: conn)
    assert not conn.lease and not conn.cursors and not conn.rt_fp


# --------------------------- W9d-4: the session guards belong INSIDE the transaction


def test_the_session_guards_are_set_local_inside_the_pass_transaction() -> None:
    """`db.connect` speaks to the transaction-mode pooler, which rebinds a connection between
    queries: a guard set on the session is a guard the pass's own transaction may never see."""
    conn = _seeded([{"grain": "obec", "code": 563510}])
    run_incremental(lambda: conn)
    for guard in (RT_STATEMENT_GUARD_SQL, RT_LOCK_GUARD_SQL, RT_IDLE_GUARD_SQL):
        assert guard in conn.statements_in_tx, guard
        assert ", true)" in guard, "set_config must be LOCAL to the transaction"
