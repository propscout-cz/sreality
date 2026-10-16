"""E960: a pass reads full facts only for its claim and for the sides of the pairs it decides.

The probe-key neighbourhood (E71) is re-retrieved from the keys the store posts (`fp_key`) and
the guard columns its rows keep (`rt_fp`); whether a wanted pair is decided, and whether the
reject ledger answers it (E943), reads each side's row: its digest and its decision digest. A
side is read once the pair budget agreed, and only when a pair touching it is decided.

The gate is EQUIVALENCE with rows equal to facts: two FakePg stores over the same claims, one
pass holding every listing's facts, the other as shipped, equal after every pass in every table
a pass writes, the reject ledger included, while every row's digest and decision digest are its
listing's (asserted). Where a fact moves with no feed claiming its advert the rows are NOT the
facts: the pass then decides on the row, and the freshness rule (the review's probe, below) is
what keeps such a stored merge from being applied.
"""

from __future__ import annotations

import random
from dataclasses import replace
from datetime import timedelta
from typing import Any, Iterable

import pytest

from autodedup import harness, incremental
from autodedup import incremental_sql as S
from autodedup.dataset import Dataset, Image, Location, Meta
from autodedup.fingerprint import build_fingerprint
from autodedup.incremental import (
    Calibration,
    Keyer,
    Limits,
    PassResult,
    _fill,
    _Working,
    decision_digest,
    engine_digest,
    fp_digest,
    guard_row,
    retrieve,
    retrieve_posted,
    run_pass,
)
from autodedup.incremental_lane import SqlStore
from autodedup.incremental_store import CohortFacts, MemoryStore, Schedule
from autodedup.settings import Settings
from tests.autodedup.fake_pg import FakePg
from tests.autodedup.test_incremental import (
    QUARTER_GRAIN,
    _calibration,
    _dataset,
    _listing,
    _settings,
    arrival_order,
    quarter_grain_dataset,
)
from tests.autodedup.test_reject_ledger import (
    CLAIM,
    GEN,
    MODEL,
    WORDS,
    _db_state,
    _mixed_dataset,
)

READS = {"claim", "order", "digest", "decided", "cluster", "rail"}


def _everything(ids: Iterable[int]) -> type[_Working]:
    """The read before E960: every listing's facts in hand before the pass looks at one."""
    every = sorted(ids)

    class _HeldEverything(_Working):
        def ensure(self, ids: Iterable[int], *, clip: bool = False,
                   reason: str = "other") -> None:
            if not self.fps:
                super().ensure(every, reason="everything")
            super().ensure(ids, clip=clip, reason=reason)

    return _HeldEverything


class _Recording(CohortFacts):
    """The cohort's facts, keeping the ids each read asked for."""

    def __init__(self, ds: Dataset) -> None:
        super().__init__(ds)
        self.asked: set[int] = set()

    def facts(self, ids, *, clip=True):
        ids = list(ids)
        self.asked.update(ids)
        return super().facts(ids, clip=clip)


class _Engine:
    def __init__(self, order: Iterable[int], everything: bool) -> None:
        self.db = FakePg()
        self.work = Schedule(list(order))
        self.everything = everything

    def run(self, ds: Dataset, settings: Settings, calibration: Calibration,
            monkeypatch: pytest.MonkeyPatch, claims: list[int] | None = None,
            limits: Limits | None = None, redecide: bool = False
            ) -> tuple[PassResult, set[int]]:
        store = SqlStore(self.db, GEN, store_floor=settings.store_floor,
                         calibration_digest=calibration.digest(),
                         engine_digest=engine_digest(settings, MODEL))
        facts = _Recording(ds)
        with monkeypatch.context() as patch:
            if self.everything:
                patch.setattr(incremental, "_Working", _everything(ds.listings))
            work = self.work if claims is None else Schedule(claims, redecide=redecide)
            result = run_pass(store, facts, work, settings, MODEL, calibration,
                              limits=limits or Limits(max_listings=CLAIM, max_pairs=10 ** 9),
                              generation=GEN)
        return result, facts.asked


def _state(db: FakePg) -> dict[str, Any]:
    return {**_db_state(db), "ledger": {key: dict(row) for key, row in db.reject_ledger.items()}}


def _rows_are_the_facts(db: FakePg, ds: Dataset, settings: Settings) -> None:
    facts = CohortFacts(ds).facts(sorted(ds.listings), clip=False)
    for (_gen, listing_id), row in db.rt_fp.items():
        listing, images = facts[listing_id]
        assert row["fp_digest"] == fp_digest(build_fingerprint(listing, images, settings),
                                             images), listing_id
        assert row["dd"] == decision_digest(listing, images), listing_id


def _lockstep(full: _Engine, narrow: _Engine, ds: Dataset, settings: Settings,
              calibration: Calibration, monkeypatch: pytest.MonkeyPatch,
              claims: list[int] | None = None) -> tuple[PassResult, PassResult, set[int]]:
    ref, _everything_asked = full.run(ds, settings, calibration, monkeypatch, claims)
    out, asked = narrow.run(ds, settings, calibration, monkeypatch, claims)
    assert not ref.aborted and not out.aborted
    assert _state(narrow.db) == _state(full.db)
    _rows_are_the_facts(narrow.db, ds, settings)
    assert set(out.reads) <= READS, out.reads
    assert sum(out.reads.values()) == len(asked)
    assert (out.wanted_pairs, out.pairs_scored, out.ledger_skipped) == (
        ref.wanted_pairs, ref.pairs_scored, ref.ledger_skipped)
    return ref, out, asked


# ------------------------------------------------------------------------------- the gate


@pytest.mark.parametrize("world", ["mixed", "quarter"])
@pytest.mark.parametrize("order_name", ["arrival", "reversed"])
def test_the_narrow_read_reaches_the_full_reads_state_after_every_pass(
        world: str, order_name: str, monkeypatch: pytest.MonkeyPatch) -> None:
    ds, settings = ((_mixed_dataset(), _settings()) if world == "mixed"
                    else (quarter_grain_dataset(), QUARTER_GRAIN))
    calibration = _calibration(ds, settings)
    order = arrival_order(ds) if order_name == "arrival" else list(reversed(arrival_order(ds)))
    full, narrow = _Engine(order, True), _Engine(order, False)
    unread = 0
    while not narrow.work.exhausted():
        _ref, out, asked = _lockstep(full, narrow, ds, settings, calibration, monkeypatch)
        unread += out.dirty - len(asked)
    if world == "quarter":
        return
    assert unread > 0, "the neighbourhood is re-retrieved without being read"

    # The disturbances E943 pins, each claimed as its feed claims it: a re-price (`trigger`),
    # a body edit that keeps the SimHash, a broker identity, tags arriving; then a re-cut.
    randoms = sorted(i for i in ds.listings if i >= 7000)
    trigger = randoms[0]

    def disturb(target: int | None, mutate) -> tuple[PassResult, set[int]]:
        if target is not None:
            mutate(ds, target)
        ds.listings[trigger].price = (ds.listings[trigger].price or 0) + 10_000
        claims = [trigger] if target is None else [trigger, target]
        _ref, out, asked = _lockstep(full, narrow, ds, settings, calibration, monkeypatch,
                                     claims)
        return out, asked

    def body(ds: Dataset, i: int) -> None:
        ds.listings[i].description = ds.listings[i].description.replace(" ", ", ", 1)

    def broker(ds: Dataset, i: int) -> None:
        ds.listings[i].broker_identity_id = 4242

    def tags(ds: Dataset, i: int) -> None:
        ds.images_by_listing[i] = [replace(image, tags=[("kitchen", 0.9)])
                                   for image in ds.images_by_listing[i]]

    for target, mutate in ((randoms[3], body), (randoms[5], broker), (randoms[7], tags)):
        out, asked = disturb(target, mutate)
        assert out.ledger_skipped > 0 and out.dirty > len(asked)
    calibration = replace(calibration, corpus_docs=calibration.corpus_docs + 1)
    out, _asked = disturb(None, None)
    assert out.ledger_skipped == 0, "a re-cut re-decides every wanted reject, as before"
    assert disturb(None, None)[0].ledger_skipped > 0


def test_the_neighbourhood_read_in_slices_retrieves_what_one_read_does(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """`KEY_CHUNK` bounds how many posted key sets a pass holds at once; slices of three decide
    exactly what the full read decides."""
    monkeypatch.setattr(incremental, "KEY_CHUNK", 3)
    ds, settings = _mixed_dataset(), _settings()
    calibration = _calibration(ds, settings)
    order = arrival_order(ds)
    full, narrow = _Engine(order, True), _Engine(order, False)
    while not narrow.work.exhausted():
        _ref, out, _asked = _lockstep(full, narrow, ds, settings, calibration, monkeypatch)
    assert out.dirty > 3 * incremental.KEY_CHUNK


def test_a_pass_reads_its_claim_and_the_sides_it_decides(monkeypatch: pytest.MonkeyPatch,
                                                         ) -> None:
    """Over a built store, a re-priced advert's pass reads the advert and the sides of the pairs
    it decides; the rest of its neighbourhood is retrieved from the store and never read."""
    ds = _mixed_dataset()
    settings = _settings()
    calibration = _calibration(ds, settings)
    order = arrival_order(ds)
    full, narrow = _Engine(order, True), _Engine(order, False)
    while not narrow.work.exhausted():
        _lockstep(full, narrow, ds, settings, calibration, monkeypatch)
    trigger = sorted(i for i in ds.listings if i >= 7000)[0]
    ds.listings[trigger].price = (ds.listings[trigger].price or 0) + 10_000
    seen: list[tuple[int, int]] = []
    real = incremental.decide_pair

    def spy(fa, fb, *args, **kwargs):
        seen.append((fa.listing_id, fb.listing_id))
        return real(fa, fb, *args, **kwargs)

    monkeypatch.setattr(incremental, "decide_pair", spy)
    ref, out, asked = _lockstep(full, narrow, ds, settings, calibration, monkeypatch, [trigger])
    sides = {i for pair in seen[len(seen) // 2:] for i in pair}
    assert asked == {trigger} | sides | (asked - {trigger} - sides)
    assert len(asked - {trigger} - sides) <= out.reads.get("cluster", 0)
    assert out.reads["claim"] == 1 and out.reads.get("order", 0) == 0
    assert out.dirty > len(asked) and sum(ref.reads.values()) == len(ds.listings)
    assert out.to_json()["counts"]["reads"] == out.reads


# ------------------------------------------------------------------- the keys a row stands in for


def _worlds() -> list[tuple[Dataset, Settings]]:
    edges = _dataset(12)
    for step, listing_id in enumerate(sorted(edges.listings)):
        # Deciles at both ends and none at all: the broker probe's ±1 is clipped to 0..9.
        edges.listings[listing_id].price = (None if step % 4 == 0
                                            else 1_000_000.0 * (1 + step) ** 3)
    return [(_mixed_dataset(), _settings()), (quarter_grain_dataset(), QUARTER_GRAIN),
            (edges, _settings()), (_dataset(), replace(_settings(), anchor_images=1))]


@pytest.mark.parametrize("index", range(4))
def test_a_listing_s_posted_keys_give_back_its_probe_keys(index: int) -> None:
    """Every probe in `probe_keys`' own order, `phash` too when one anchor posted it; over two
    anchors or more the same bands, in another order."""
    ds, settings = _worlds()[index]
    fps, calibration = harness.calibrated(ds, settings)
    keyer = Keyer(settings, calibration)
    shapes: set[str] = set()
    for fp in fps.values():
        truth = keyer.probe_keys(fp)
        got = keyer.probe_keys_of(keyer.index_keys(fp))
        for probe in keyer.index.probes:
            want = list(dict.fromkeys(token for name, token in truth if name == probe))
            have = [token for name, token in got if name == probe]
            bands = {token.partition("\x1f")[0] for token in have}
            if probe != "phash" or len(bands) == len(have):
                assert have == want, (fp.listing_id, probe)
            else:
                assert sorted(have) == sorted(want), (fp.listing_id, probe)
        shapes.update(probe for probe, _token in truth)
    assert shapes >= {"phash", "attr_dispo"}


def _order_world() -> tuple[Dataset, Settings, int, int]:
    """D (100) posts two anchors, A and B. A's bands also sit on 201 and 202, B's on 301 and
    302, and T (400) arrives later with A's photograph. One candidate a listing: in D's true
    order (A first) it retrieves 201; read in its posted order (B's band 0 sorts first) it
    would retrieve 301."""
    settings = replace(_settings(), max_candidates_per_listing=1)
    a = 9 | (1 << 16) | (2 << 32) | (3 << 48)
    b = 10 | (5 << 16) | (6 << 32) | (7 << 48)
    gallery = {100: [a, b], 201: [a], 202: [a], 301: [b], 302: [b], 400: [a]}
    rnd = random.Random(5)
    listings, images = {}, {}
    for step, (listing_id, hashes) in enumerate(sorted(gallery.items())):
        listings[listing_id] = _listing(
            listing_id, description=" ".join(rnd.choice(WORDS) for _ in range(40)),
            broker_key=f"o{listing_id}", price=2_000_000.0 + step * 300_000,
            first_seen_at=f"2026-0{1 + (listing_id == 400)}-{step + 1:02d}T08:00:00+00:00",
            location=Location(obec_kod=554782, cast_obce_kod=490245,
                              street_key=f"u{listing_id}", house_number_cp=str(listing_id),
                              lat=50.1, lon=14.4, ruian_adm_kod=listing_id))
        images[listing_id] = [Image(listing_id=listing_id, image_id=listing_id * 10 + seq,
                                    seq=seq, phash=phash, pop=1)
                              for seq, phash in enumerate(hashes)]
    return Dataset(meta=Meta(), listings=listings, images_by_listing=images), settings, 100, 400


def test_a_cap_that_binds_inside_a_loose_phash_probe_reads_the_listing(
        monkeypatch: pytest.MonkeyPatch) -> None:
    ds, settings, d, trigger = _order_world()
    fps, calibration = harness.calibrated(ds, settings)
    keyer = Keyer(settings, calibration)
    store = MemoryStore()
    for listing_id, fp in fps.items():
        store.put_listing(listing_id, incremental.FpRow(guard_row(fp), "", "", "", True),
                          sorted(set(keyer.index_keys(fp))))
    guards = {i: guard_row(fp) for i, fp in fps.items()}
    truth = retrieve(fps[d], keyer, store, guards, settings, {})
    posted = keyer.index_keys(fps[d])
    assert set(truth) == {201}
    assert set(_fill(d, guards[d], keyer.probe_keys_of(posted), keyer, store, guards, settings,
                     {}) or {}) == {301}, "the posted order would retrieve another listing"
    assert retrieve_posted(guards[d], posted, keyer, store, guards, settings, {}) is None
    for listing_id in set(fps) - {d}:
        assert retrieve_posted(guards[listing_id], keyer.index_keys(fps[listing_id]), keyer,
                               store, guards, settings, {}) == retrieve(
            fps[listing_id], keyer, store, guards, settings, {}), "one anchor is in order"

    order = [i for i in arrival_order(ds) if i != trigger]
    full, narrow = _Engine(order, True), _Engine(order, False)
    while not narrow.work.exhausted():
        _lockstep(full, narrow, ds, settings, calibration, monkeypatch)
    _ref, out, asked = _lockstep(full, narrow, ds, settings, calibration, monkeypatch,
                                 [trigger])
    assert out.reads.get("order") == 1 and d in asked


# ------------------------------------------------------------- the freshness rule (E960, #1818)


def _groups(db: FakePg) -> list[set[int]]:
    groups: dict[int, set[int]] = {}
    for gen, key, member in db.cluster_members:
        if gen == GEN:
            groups.setdefault(key, set()).add(member)
    return list(groups.values())


def test_a_healed_storey_s_merge_waits_for_its_re_claim_and_is_dropped(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """The review's probe: twins 3000/3001 merge on K-R; 3001's storey is healed 3 -> 7 with no
    feed claiming it, and an unrelated flat arrives in the same quarter. The pass reads 3001 as
    its row stored it, so the merge stands — on facts the store no longer holds. The reconcile
    therefore applies no merge whose moving members were refreshed more than
    FRESH_DECISION_HOURS ago: both twins are named stale, the next pass re-claims them
    (`redecide`), the floor gap of four vetoes the pair and the group is gone."""
    from autodedup import reconcile

    ds, settings = _mixed_dataset(), _settings()
    calibration = _calibration(ds, settings)
    narrow = _Engine(arrival_order(ds), False)
    while not narrow.work.exhausted():
        narrow.run(ds, settings, calibration, monkeypatch)
    lo, hi = 3000, 3001
    assert narrow.db.pairs[(GEN, lo, hi)]["zone"] == "merge"
    assert any({lo, hi} <= members for members in _groups(narrow.db))
    ds.listings[hi].floor = (ds.listings[lo].floor or 3) + 4
    arrival = 3500
    ds.listings[arrival] = _listing(arrival, area_m2=ds.listings[lo].area_m2,
                                    description="Uplne jiny popis bytu " * 12,
                                    first_seen_at="2026-09-01T08:00:00+00:00")
    ds.images_by_listing[arrival] = [Image(listing_id=arrival, image_id=arrival * 10 + seq,
                                           seq=seq, phash=123_456_789 + seq * 7_919, pop=1)
                                     for seq in range(3)]
    narrow.run(ds, settings, calibration, monkeypatch, [arrival])
    assert narrow.db.pairs[(GEN, lo, hi)]["zone"] == "merge", "read as its row stored it"

    narrow.db.now += timedelta(hours=reconcile.FRESH_DECISION_HOURS + 1)
    stale = reconcile.stale_members(narrow.db, GEN, [lo, hi],
                                    fresh_hours=reconcile.FRESH_DECISION_HOURS,
                                    engine_since=None)
    assert stale == {lo, hi}, "the reconcile holds the group and hands both twins on"
    narrow.run(ds, settings, calibration, monkeypatch, sorted(stale), redecide=True)
    row = narrow.db.pairs.get((GEN, lo, hi))
    assert row is None or row["zone"] != "merge", "re-decided on the healed storey"
    assert not any({lo, hi} <= members for members in _groups(narrow.db))
    assert not reconcile.stale_members(narrow.db, GEN, [lo, hi],
                                       fresh_hours=reconcile.FRESH_DECISION_HOURS,
                                       engine_since=None), "both refreshed now"


def test_the_next_pass_re_claims_what_the_reconcile_handed_it(tmp_path) -> None:
    """The members a waiting group names (`rt_redecide:<generation>`) are claimed by the next
    pass as `stale` with `redecide`, only while they are still stale, and refreshed."""
    from autodedup import incremental_lane, reconcile
    from tests.autodedup.test_reject_ledger import _seeded_reject_world

    world = _seeded_reject_world(tmp_path)
    assert not incremental_lane.run_incremental(lambda: world)["aborted"]
    target, fresh = 4006, 4007
    world.rt_fp[(GEN, target)]["updated_at"] = world.now - timedelta(
        hours=reconcile.FRESH_DECISION_HOURS + 1)
    world.settings[reconcile.redecide_key(GEN)] = [[target, fresh]]
    engine = world.settings[incremental_lane.engine_key(GEN)]
    assert engine["digest"] and engine["since"], "the lane records its engine and since when"
    # The fake's clock is a fixed day in September; the engine has run since before it.
    engine["since"] = (world.now - timedelta(days=1)).isoformat()
    out = incremental_lane.run_incremental(lambda: world)
    assert out["feeds"].get("stale") == 1, "the stale one only"
    assert out["evidence"]["stale_claimed"] == 1
    assert world.rt_fp[(GEN, target)]["updated_at"] == world.now, "refreshed"
    assert out["counts"]["redecided"] >= 1


def test_the_re_claim_has_its_own_floor_whatever_the_share(tmp_path) -> None:
    """A pass the clock or the claim cap cut to one advert (a share of 1) still re-claims up to
    `STALE_FLOOR` adverts of the groups handed on, whole groups, oldest wait first."""
    from autodedup import incremental_lane, reconcile

    world = _seeded_reject_world_for_floor(tmp_path)
    ids = sorted(i for _g, i in world.rt_fp)
    for hours, listing_id in enumerate(ids, start=13):
        world.rt_fp[(GEN, listing_id)]["updated_at"] = world.now - timedelta(hours=hours)
    world.settings[reconcile.redecide_key(GEN)] = [[i] for i in ids]
    world.settings[incremental_lane.claim_cap_key(GEN)] = {"cap": 1, "reason": "test"}
    out = incremental_lane.run_incremental(lambda: world)
    share = max(1, out["claim_bound"]["limit"] // 5)
    assert share < len(ids) <= reconcile.STALE_FLOOR
    assert out["feeds"].get("stale") == len(ids) == out["evidence"]["stale_claimed"]
    assert reconcile.claims([[4007], [4005], [4006]], {
        4005: (1, True), 4006: (2, True), 4007: (3, True)}, 2) == [4005, 4006], (
        "oldest wait first, whole groups, up to the budget")


def _seeded_reject_world_for_floor(tmp_path):
    from autodedup import incremental_lane
    from tests.autodedup.test_reject_ledger import _seeded_reject_world

    world = _seeded_reject_world(tmp_path)
    assert not incremental_lane.run_incremental(lambda: world)["aborted"]
    engine = world.settings[incremental_lane.engine_key(GEN)]
    engine["since"] = (world.now - timedelta(days=2)).isoformat()
    return world


# ------------------------------------------------------------- rows written before migration 604


def test_a_row_without_its_digest_is_read_once_for_it(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every row of a store built before 604 carries no decision digest: the first pass whose
    ledger lookup needs one reads that side, writes its digest and answers what the full read
    answers; the next pass reads none for it."""
    ds = _mixed_dataset()
    settings = _settings()
    calibration = _calibration(ds, settings)
    order = arrival_order(ds)
    full, narrow = _Engine(order, True), _Engine(order, False)
    while not narrow.work.exhausted():
        _lockstep(full, narrow, ds, settings, calibration, monkeypatch)
    for row in narrow.db.rt_fp.values():
        row["dd"] = None
    randoms = sorted(i for i in ds.listings if i >= 7000)
    for step, trigger in enumerate((randoms[0], randoms[0], randoms[1])):
        ds.listings[trigger].price = (ds.listings[trigger].price or 0) + 10_000
        ref, _ = full.run(ds, settings, calibration, monkeypatch, [trigger])
        out, asked = narrow.run(ds, settings, calibration, monkeypatch, [trigger])
        state, expected = _state(narrow.db), _state(full.db)
        assert {k: v for k, v in state.items() if k != "rt_fp"} == {
            k: v for k, v in expected.items() if k != "rt_fp"}
        assert out.ledger_skipped == ref.ledger_skipped > 0
        stamped = {i for (_g, i), row in narrow.db.rt_fp.items() if row["dd"]}
        for listing_id in stamped:
            assert narrow.db.rt_fp[(GEN, listing_id)]["dd"] == full.db.rt_fp[
                (GEN, listing_id)]["dd"]
        if step == 0:
            assert out.reads["digest"] > 0 and stamped
        if step == 1:
            assert out.reads.get("digest", 0) == 0, "what the first pass read is written"


def test_a_database_without_604_is_store_absent(tmp_path) -> None:
    """The presence check names the column, so code merged before 604 was applied skips its
    passes (`store_absent` in the worker) instead of failing each one on it."""
    from autodedup import incremental_lane
    from scraper import realtime_worker as rw
    from tests.autodedup.test_reject_ledger import _seeded_reject_world

    assert "attname = 'dd'" in S.RT_STORE_PRESENT_SQL
    world = _seeded_reject_world(tmp_path)
    world.absent_columns = {"rt_fp.dd"}
    assert rw._autodedup_store_present(world) is False
    with pytest.raises(SystemExit, match="604"):
        incremental_lane.run_incremental(lambda: world)
    world.absent_columns = set()
    assert rw._autodedup_store_present(world) is True
    out = incremental_lane.run_incremental(lambda: world)
    assert not out["aborted"] and set(out["counts"]["reads"]) <= READS
    assert all(row["dd"] for row in world.rt_fp.values())


def test_a_digest_written_without_a_refresh_moves_nothing_else() -> None:
    db = FakePg()
    sql = SqlStore(db, GEN, calibration_digest="c", engine_digest="e")
    twin = MemoryStore()
    for store in (sql, twin):
        store.put_listing(1, incremental.FpRow(incremental.GuardRow(1, "byt", "prodej", 50.0,
                                                                    "2+kk", 2),
                                               "f", "k", "g", True, dd="old"), [("attr", "x")])
        store.flush()
    before = dict(db.rt_fp[(GEN, 1)])
    statements = len(db.statements)
    for store in (sql, twin):
        store.put_decision_digests({1: "new", 2: "absent"})
    assert db.statements[statements:] == [S.RT_FP_DD_SQL]
    assert db.rt_fp[(GEN, 1)] == {**before, "dd": "new"} and (GEN, 2) not in db.rt_fp
    assert sql.rows([1])[1] == twin.rows([1])[1] == replace(twin.rows([1])[1], dd="new")
    assert SqlStore(db, GEN, calibration_digest="c", engine_digest="e").rows([1])[1].dd == "new"


# ------------------------------------------------------------------------- what is named


def test_a_change_no_feed_claims_is_seen_when_its_advert_is_claimed(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """A neighbour is seen as its row stored it (E960): a broker identity attributed without a
    claim leaves its rejects answered from the ledger while other adverts' passes want them,
    and they are re-decided when a feed claims the advert."""
    ds = _mixed_dataset()
    settings = _settings()
    calibration = _calibration(ds, settings)
    narrow = _Engine(arrival_order(ds), False)
    while not narrow.work.exhausted():
        narrow.run(ds, settings, calibration, monkeypatch)
    randoms = sorted(i for i in ds.listings if i >= 7000)
    trigger, target = randoms[0], randoms[5]
    ledgered = {key[1:] for key in narrow.db.reject_ledger if target in key[1:]}
    assert ledgered
    seen: list[tuple[int, int]] = []
    real = incremental.decide_pair

    def spy(fa, fb, *args, **kwargs):
        seen.append(tuple(sorted((fa.listing_id, fb.listing_id))))
        return real(fa, fb, *args, **kwargs)

    monkeypatch.setattr(incremental, "decide_pair", spy)
    ds.listings[target].broker_identity_id = 4242
    ds.listings[trigger].price = (ds.listings[trigger].price or 0) + 10_000
    out, _asked = narrow.run(ds, settings, calibration, monkeypatch, [trigger])
    assert out.ledger_skipped > 0
    assert not {pair for pair in seen if target in pair} - {
        pair for pair in seen if trigger in pair}, "only the claim's pairs are decided"
    assert narrow.db.rt_fp[(GEN, target)]["dd"] != decision_digest(
        ds.listings[target], ds.images(target)), "its row is as the lane last read it"
    seen.clear()
    ds.listings[trigger].price = (ds.listings[trigger].price or 0) + 10_000
    narrow.run(ds, settings, calibration, monkeypatch, [trigger, target])
    redecided = {pair for pair in seen if target in pair and trigger not in pair}
    assert redecided & ledgered, "claimed, its rejects are re-decided"
    assert narrow.db.rt_fp[(GEN, target)]["dd"] == decision_digest(
        ds.listings[target], ds.images(target))
