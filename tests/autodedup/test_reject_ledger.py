"""E943: the reject ledger — a pair decided below `store_floor` is not re-scored on every pass.

The gate is EQUIVALENCE: the same cohort, the same claims, two FakePg stores — today's engine
(a store that never reads or writes the ledger) and the ledger engine — and after every pass
the two databases hold the same rows. Between passes the cohort is disturbed in each way a
decision's inputs can move without `fp_digest` seeing it — and by a deploy of the decision code
and a CLIP vector arriving after its tags — and each disturbance must re-score exactly the pairs
it touches. The units below pin each rule on its own.
"""

from __future__ import annotations

import ast
import copy
import json
import random
import shutil
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any, Iterable

import pytest

from autodedup import harness, incremental, incremental_lane, incremental_store, rt_lease
from autodedup import incremental_sql as S
from autodedup.dataset import Dataset, Image, Location, Meta
from autodedup.decide import Decision
from autodedup.export import encode_clip
from autodedup.features import FEATURE_VERSION
from autodedup.incremental import (
    Calibration,
    Limits,
    PairRow,
    PassDeadline,
    PassResult,
    PassStopped,
    DECISION_MODULES,
    DECISION_SOURCE_DIGEST,
    engine_digest,
    run_pass,
    source_digest,
)
from autodedup.incremental_lane import LEDGER_CHUNK, SqlStore
from autodedup.incremental_store import CohortFacts, MemoryStore, Schedule
from autodedup.model import hand_initialised
from autodedup.store_score import storable
from tests.autodedup.fake_pg import FakePg
from tests.autodedup.test_incremental import (
    _calibration,
    _dataset,
    _listing,
    _settings,
    arrival_order,
)
from tests.autodedup.test_incremental_sqlstore import _twins_dataset

GEN = "rt"
FLOOR = 0.02
CLAIM = 4
MODEL = hand_initialised()
WORDS = ("balkon sklep parkovani vytah zahrada terasa metro tramvaj skola park kuchyn koupelna "
         "loznice satna komora podkrovi novostavba cihla panel lodzie").split()


def _mixed_dataset(n: int = 18, seed: int = 7) -> Dataset:
    """Eight near-duplicates, two syndicated twins and `n` flats of the same town, disposition
    and area band that share nothing else: the near-duplicates band, the twins merge, and the
    rest meet each other through the attribute probes and are rejected — most below the floor."""
    rnd = random.Random(seed)
    near, twins = _dataset(8), _twins_dataset(2)
    listings = {**near.listings, **twins.listings}
    images = {**near.images_by_listing, **twins.images_by_listing}
    for index in range(n):
        listing_id = 7000 + index * 11
        listings[listing_id] = _listing(
            listing_id, area_m2=55.0 + rnd.randint(0, 6),
            price=3_000_000.0 + rnd.randint(0, 40) * 100_000,
            description=" ".join(rnd.choice(WORDS) for _ in range(40)),
            broker_key=f"x{index}", floor=rnd.randint(1, 8),
            location=Location(obec_kod=554782, cast_obce_kod=490245, street_key=f"ul{index % 5}",
                              house_number_cp=str(index), lat=50.1 + index * 0.001, lon=14.4,
                              ruian_adm_kod=500 + index))
        images[listing_id] = [Image(listing_id=listing_id, image_id=listing_id * 10 + seq,
                                    seq=seq, phash=rnd.getrandbits(63), pop=1)
                              for seq in range(3)]
    return Dataset(meta=Meta(), listings=listings, images_by_listing=images)


class TodayStore(SqlStore):
    """Today's engine: the ledger is never read and never written."""

    def rejected(self, keys):
        return set()

    def _ledger(self, evicted, kept) -> None:
        return None


class _Engine:
    """One engine's database, work source and twin, driven a pass at a time. The store is new
    every pass with the pass's calibration and engine digests, as `run_incremental` builds it."""

    def __init__(self, store_cls: type[SqlStore], order: Iterable[int], twin: MemoryStore,
                 model: Any = MODEL) -> None:
        self.store_cls = store_cls
        self.model = model
        self.db = FakePg()
        self.work = Schedule(list(order))
        self.twin = twin
        self.twin_work = Schedule(list(order))

    def run(self, ds: Dataset, calibration: Calibration, work: Any = None,
            twin_work: Any = None, limits: Limits | None = None) -> PassResult:
        caps = limits or Limits(max_listings=CLAIM, max_pairs=10 ** 9)
        settings = _settings()
        key = {"calibration_digest": calibration.digest(),
               "engine_digest": engine_digest(settings, self.model)}
        store = self.store_cls(self.db, GEN, store_floor=settings.store_floor, **key)
        result = run_pass(store, CohortFacts(ds), work or self.work, settings, self.model,
                          calibration, limits=caps, generation=GEN)
        self.twin.calibration_digest = key["calibration_digest"]
        self.twin.engine_digest = key["engine_digest"]
        run_pass(self.twin, CohortFacts(ds), twin_work or self.twin_work, settings, self.model,
                 calibration, limits=caps, generation=GEN)
        return result


def _key(calibration: Calibration) -> dict[str, str]:
    """The ledger key `run_incremental` builds its store with."""
    return {"calibration_digest": calibration.digest(),
            "engine_digest": engine_digest(_settings(), MODEL)}


def _db_state(db: FakePg) -> dict[str, Any]:
    """Every table a pass writes, the ledger excepted. `pairs` holds every column but
    `decided_at`, which the fake does not keep."""
    return {
        "pairs": {key: dict(row) for key, row in db.pairs.items()},
        "rt_fp": {key: dict(row) for key, row in db.rt_fp.items()},
        "fp_key": set(db.fp_key),
        "clusters": {key: dict(row) for key, row in db.clusters.items()},
        "members": set(db.cluster_members),
        "conflicts": [dict(row) for row in db.cluster_conflicts],
        "cells": {key: dict(row) for key, row in db.cells.items()},
        "cursors": {key: dict(row) for key, row in db.cursors.items()},
    }


def _storable(store: MemoryStore, floor: float) -> dict[tuple[int, int], tuple]:
    return {key: (row.zone, row.score, row.reason, row.certificate, tuple(sorted(row.families)),
                  tuple(sorted(row.probes)), row.from_lo, row.from_hi)
            for key, row in store.pairs.items()
            if storable({"zone": row.zone, "score": row.score, "evidence": row.evidence}, floor)}


def _sql_storable(db: FakePg) -> dict[tuple[int, int], tuple]:
    from autodedup.score_lane import families_of_bitmask

    return {(lo, hi): ("veto" if row["guard_veto"] else row["zone"], row["score"],
                       row["decision"], row["certificate"],
                       tuple(sorted(families_of_bitmask(row["families"]))),
                       tuple(sorted(row["probes"])), row["from_lo"], row["from_hi"])
            for (_gen, lo, hi), row in db.pairs.items()}


@pytest.fixture
def scored(monkeypatch: pytest.MonkeyPatch) -> list[tuple[int, int]]:
    """Every pair `decide_pair` is asked about, in order."""
    seen: list[tuple[int, int]] = []
    real = incremental.decide_pair

    def spy(fa, fb, *args, **kwargs):
        seen.append(tuple(sorted((fa.listing_id, fb.listing_id))))
        return real(fa, fb, *args, **kwargs)

    monkeypatch.setattr(incremental, "decide_pair", spy)
    return seen


def _lockstep(today: _Engine, ledger: _Engine, ds: Dataset, calibration: Calibration,
              scored: list[tuple[int, int]], claims: list[int] | None = None,
              twin_is_sql: bool = True, limits: Limits | None = None
              ) -> tuple[PassResult, PassResult, set, set, set]:
    """One pass of each engine over the same claim (the drain's next, or `claims`); asserts
    the two databases are equal, and the twins' storable subsets with each other and with the
    store. A twin keeps every pair, so it never re-decides a reject whose digests stand: where
    an input `fp_digest` does not hash moves (a deploy, a vector), `twin_is_sql` is off and
    only the two twins are held equal. Returns both results, both scored sets and what was
    stored before the pass."""
    def works() -> dict[str, Any]:
        return {"limits": limits, **({} if claims is None else {
            "work": Schedule(claims), "twin_work": Schedule(claims)})}

    stored_before = {(lo, hi) for _g, lo, hi in ledger.db.pairs}
    scored.clear()
    ref = today.run(ds, calibration, **works())
    ref_scored = set(scored[:ref.pairs_scored])
    scored.clear()
    out = ledger.run(ds, calibration, **works())
    led_scored = set(scored[:out.pairs_scored])
    assert not ref.aborted and not out.aborted
    assert _db_state(ledger.db) == _db_state(today.db)
    floor = _settings().store_floor
    assert _storable(ledger.twin, floor) == _storable(today.twin, floor)
    assert not twin_is_sql or _storable(ledger.twin, floor) == _sql_storable(ledger.db)
    assert ledger.twin.clusters == today.twin.clusters
    assert ledger.work.cursor == today.work.cursor
    # The ledger only ever REMOVES a pair from the decided set, and says how many.
    assert led_scored <= ref_scored
    assert out.ledger_skipped == len(ref_scored - led_scored)
    assert out.pairs_to_decide == out.pairs_scored
    assert out.ledger_written == len(led_scored - {key[1:] for key in ledger.db.pairs})
    # Pairs and the ledger never hold the same pair.
    assert not {key[1:] for key in ledger.db.pairs} & {key[1:] for key in ledger.db.reject_ledger}
    return ref, out, ref_scored, led_scored, stored_before


def _touching(pairs: Iterable[tuple[int, int]], ids: set[int]) -> set[tuple[int, int]]:
    return {pair for pair in pairs if pair[0] in ids or pair[1] in ids}


# ----------------------------------------------------------------------- the gate


@pytest.mark.parametrize("order_name", ["arrival", "reversed"])
def test_the_ledger_engine_reaches_todays_state_after_every_pass(
        order_name: str, scored: list[tuple[int, int]], monkeypatch: pytest.MonkeyPatch) -> None:
    ds = _mixed_dataset()
    calibration = _calibration(ds, _settings())
    order = arrival_order(ds) if order_name == "arrival" else list(reversed(arrival_order(ds)))
    today = _Engine(TodayStore, order, MemoryStore())
    ledger = _Engine(SqlStore, order, MemoryStore(store_floor=FLOOR))

    skipped = 0
    while not ledger.work.exhausted():
        _ref, out, *_ = _lockstep(today, ledger, ds, calibration, scored)
        skipped += out.ledger_skipped
    assert ledger.db.reject_ledger, "the cohort must decide pairs below the floor"
    assert skipped > 0, "the drain itself re-wants rejects, and the ledger answers them"
    assert not today.db.reject_ledger

    randoms = sorted(i for i in ds.listings if i >= 7000)
    trigger = randoms[0]

    def disturb_and_pass(target: int | None, mutate,
                         twin_is_sql: bool = True) -> tuple[set, set, set, PassResult]:
        """Move `target`'s decision inputs with `mutate`, re-price `trigger` (a real change, so
        `target` is in its neighbourhood) and claim both — `target` is unchanged to the digest
        `fp_digest`, so only the ledger can tell its rejects moved."""
        if target is not None:
            mutate(target)
        listing = ds.listings[trigger]
        listing.price = (listing.price or 0) + 10_000
        claims = [trigger] if target is None else [trigger, target]
        _ref, out, ref_scored, led_scored, stored = _lockstep(
            today, ledger, ds, calibration, scored, claims, twin_is_sql)
        return ref_scored, led_scored, stored, out

    def assert_exactly_the_affected(target: int, ref_scored: set, led_scored: set,
                                    stored: set, out: PassResult) -> None:
        moved = {trigger, target}
        # Every reject touching the disturbed advert is re-scored (its digest moved) ...
        assert _touching(ref_scored - stored, {target}) <= led_scored
        assert _touching(ref_scored - stored, {target}), "the target's rejects must be wanted"
        # ... and nothing else is, but a pair with a refreshed side or a stored row.
        assert {pair for pair in led_scored
                if pair not in stored and not _touching([pair], moved)} == set()
        assert out.ledger_skipped > 0

    def body(i: int) -> None:
        listing = ds.listings[i]
        listing.description = listing.description.replace(" ", ", ", 1)

    def broker(i: int) -> None:
        ds.listings[i].broker_identity_id = 4242

    def tags(i: int) -> None:
        ds.images_by_listing[i] = [replace(image, tags=[("kitchen", 0.9)])
                                   for image in ds.images_by_listing[i]]

    for target, mutate in ((randoms[3], body), (randoms[5], broker), (randoms[7], tags)):
        assert_exactly_the_affected(target, *disturb_and_pass(target, mutate))

    # A calibration re-cut: the ledger's rows are inert, so every wanted reject is re-scored —
    # one full-price pass — and the next pass skips again under the new digest.
    calibration = replace(calibration, corpus_docs=calibration.corpus_docs + 1)
    ref_scored, led_scored, _stored, out = disturb_and_pass(None, None)
    assert (out.ledger_skipped, led_scored) == (0, ref_scored)
    assert disturb_and_pass(None, None)[3].ledger_skipped > 0

    # A feature-version bump, the same.
    for module in (incremental_lane, incremental_store):
        monkeypatch.setattr(module, "FEATURE_VERSION", FEATURE_VERSION + 1)
    ref_scored, led_scored, _stored, out = disturb_and_pass(None, None)
    assert (out.ledger_skipped, led_scored) == (0, ref_scored)
    assert {row["feature_version"] for row in ledger.db.reject_ledger.values()} >= {
        FEATURE_VERSION + 1}
    assert disturb_and_pass(None, None)[3].ledger_skipped > 0

    # A deploy of the decision code that bumps no version: every reject now scores 0.05, above
    # the floor. The sources moved, so the engine digest did, and every wanted reject is decided
    # under the new code — stored now — exactly as today's engine decides it.
    real = incremental.decide_pair

    def deployed(*args: Any, **kwargs: Any) -> Decision:
        decision = real(*args, **kwargs)
        if decision.zone != "reject" or decision.score >= 0.05:
            return decision
        return replace(decision, score=0.05)

    monkeypatch.setattr(incremental, "decide_pair", deployed)
    monkeypatch.setattr(incremental, "DECISION_SOURCE_DIGEST", "deployed")
    ref_scored, led_scored, _stored, out = disturb_and_pass(None, None, twin_is_sql=False)
    assert (out.ledger_skipped, led_scored) == (0, ref_scored)
    assert {key[1:] for key in ledger.db.pairs} >= _touching(ref_scored, set(randoms)), (
        "the deployed code stores what the old one rejected")


def _vector(seed: int) -> str:
    rnd = random.Random(seed)
    return encode_clip([rnd.uniform(-1, 1) for _ in range(512)])


@pytest.mark.parametrize("model_name", ["prior", "w6_gold"])
def test_a_vector_arriving_after_its_tags_reopens_the_rejects(
        model_name: str, scored: list[tuple[int, int]]) -> None:
    """The CLIP job writes the tags, then the vector, then `clip_tagged_at`, and a vector write
    that failed leaves the tags without a vector until its next run. Drained with tags and no
    vectors; then the vectors land with their stamp, the tags unchanged (one vector a frame
    position across the cohort, so the photographs now agree), and the evidence sweep claims
    every advert whose photographs moved (E92; a side is seen as the lane last read it, E960):
    every wanted reject is re-scored, as today's engine re-scores it."""
    ds = _mixed_dataset()
    for listing_id, images in ds.images_by_listing.items():
        ds.images_by_listing[listing_id] = [
            replace(image, tags=[("living_room", 0.8)], clip=None) for image in images]
    model = MODEL if model_name == "prior" else harness.named_model("w6_gold")
    calibration = _calibration(ds, _settings())
    order = arrival_order(ds)
    today = _Engine(TodayStore, order, MemoryStore(), model)
    ledger = _Engine(SqlStore, order, MemoryStore(store_floor=FLOOR), model)
    while not ledger.work.exhausted():
        _lockstep(today, ledger, ds, calibration, scored)
    assert ledger.db.reject_ledger
    for listing_id, images in ds.images_by_listing.items():
        ds.images_by_listing[listing_id] = [
            replace(image, clip=_vector(image.seq or 0),
                    clip_tagged_at="2026-10-12T10:00:00+00:00") for image in images]
    randoms = sorted(i for i in ds.listings if i >= 7000)
    ds.listings[randoms[0]].price = (ds.listings[randoms[0]].price or 0) + 10_000
    claims = [randoms[0], *sorted(set(ds.listings) - {randoms[0]})]
    _ref, out, ref_scored, led_scored, _stored = _lockstep(
        today, ledger, ds, calibration, scored, claims, twin_is_sql=False,
        limits=Limits(max_listings=len(claims), max_pairs=10 ** 9))
    assert (out.ledger_skipped, led_scored) == (0, ref_scored)


def test_an_unclaimed_advert_s_arriving_vector_reaches_its_rejects_through_the_sweep(
        tmp_path) -> None:
    """E943 (f)'s vector-after-tags case for an advert NO other feed claims (E960 reads such a
    side as its row stored it): its tags landed and its vector did not, so its row records an
    incomplete gallery (`ev_clip` < `ev_images`) and the evidence sweep keeps probing it; when
    the vector and its `clip_tagged_at` land, the sweep claims it (`redecide`), its decision
    digest moves and its ledgered rejects are re-decided under the new one."""
    world = _seeded_reject_world(tmp_path)
    assert not incremental_lane.run_incremental(lambda: world)["aborted"]
    target = 4006
    row = world.rt_fp[(GEN, target)]
    assert row["ev_clip"] < row["ev_images"] and row["ev_complete"] is False
    before = {key: (r["dd_lo"], r["dd_hi"]) for key, r in world.reject_ledger.items()
              if target in key[1:]}
    assert before, "the stranger's rejects are ledgered"
    for image in world.image_rows:
        if image["listing_id"] == target:
            rnd = random.Random(int(image["image_id"]))
            world.clip_vectors[int(image["image_id"])] = (
                "[" + ",".join(f"{rnd.uniform(-0.1, 0.1):.4f}" for _ in range(512)) + "]")
            image["clip_tagged_at"] = world.now
    out = incremental_lane.run_incremental(lambda: world)
    assert out["feeds"].get("evidence", 0) >= 1 and out["evidence"]["moved"] >= 1
    row = world.rt_fp[(GEN, target)]
    assert row["ev_clip"] == row["ev_images"] > 0, "the refresh recorded the vectors"
    after = {key: (r["dd_lo"], r["dd_hi"]) for key, r in world.reject_ledger.items()
             if key in before}
    side = {key: (0 if key[1] == target else 1) for key in before}
    assert after and all(after[key][side[key]] != before[key][side[key]] for key in after), (
        "every ledgered reject of the advert was re-decided under its new digest")
    assert all(after[key][side[key]] == row["dd"] for key in after)


def _churn_dataset() -> Dataset:
    """Three flats of one disposition and area band that share nothing else (A 20, B 30, D 40),
    and C 10, which arrives later in the same band and, being the lowest id, fills every one of
    their one-candidate retrievals ahead of the others."""
    listings, images = {}, {}
    rnd = random.Random(11)
    for listing_id in (10, 20, 30, 40):
        listings[listing_id] = _listing(
            listing_id, area_m2=50.0 + listing_id / 10,
            price=1_000_000.0 * (listing_id // 10) ** 2,
            description=" ".join(rnd.choice(WORDS) for _ in range(40)),
            broker_key=f"c{listing_id}", floor=2,
            first_seen_at=("2026-03-01T08:00:00+00:00" if listing_id == 10
                           else f"2026-01-{listing_id // 10:02d}T08:00:00+00:00"),
            location=Location(obec_kod=554782, cast_obce_kod=490245,
                              street_key=f"ulice{listing_id}", house_number_cp=str(listing_id),
                              lat=50.1 + listing_id * 0.001, lon=14.4,
                              ruian_adm_kod=900 + listing_id))
        images[listing_id] = [Image(listing_id=listing_id, image_id=listing_id * 10 + seq,
                                    seq=seq, phash=rnd.getrandbits(63), pop=1)
                              for seq in range(3)]
    return Dataset(meta=Meta(), listings=listings, images_by_listing=images)


def test_a_reject_dropped_by_cap_churn_and_wanted_again_is_answered_not_rescored(
        scored: list[tuple[int, int]], monkeypatch: pytest.MonkeyPatch) -> None:
    """One candidate a listing, and a floor of 0.1 so the four flats' pairs are all below it."""
    settings = replace(_settings(), max_candidates_per_listing=1, store_floor=0.1)
    monkeypatch.setattr(sys.modules[__name__], "_settings", lambda: settings)
    ds = _churn_dataset()
    calibration = _calibration(ds, settings)
    order = [20, 30, 40]
    today = _Engine(TodayStore, order, MemoryStore())
    ledger = _Engine(SqlStore, order, MemoryStore(store_floor=settings.store_floor))
    while not ledger.work.exhausted():
        _lockstep(today, ledger, ds, calibration, scored)
    churned = (20, 30)
    assert (GEN, *churned) in ledger.db.reject_ledger, "the pair must be a below-floor reject"

    # C arrives: it fills A's and B's one-candidate retrievals, so (A, B) is wanted by nobody.
    _ref, out, ref_scored, led_scored, _stored = _lockstep(
        today, ledger, ds, calibration, scored, [10])
    assert churned not in ref_scored and churned not in led_scored
    # C moves to another disposition: A and B retrieve each other again, neither changed.
    ds.listings[10].disposition = "4+1"
    _ref, out, ref_scored, led_scored, _stored = _lockstep(
        today, ledger, ds, calibration, scored, [10])
    assert churned in ref_scored, "today's engine re-scores it"
    assert churned not in led_scored and out.ledger_skipped >= 1, "the ledger answers it"
    assert churned not in ledger.twin.pairs and churned in today.twin.pairs, (
        "the twin keeps every pair it scores; an answered one it no longer re-stores")


# ------------------------------------------------------------------------- the units


def _row(lo: int, hi: int, zone: str, score: float, dd: tuple[str, str] = ("a", "b"),
         evidence: dict | None = None) -> PairRow:
    return PairRow(lo=lo, hi=hi, probes=["attr_dispo"], from_lo=True, from_hi=True, zone=zone,
                   score=score, families=[], certificate=None, veto=None, reason="model",
                   evidence=evidence or {}, context={"block": "o1"}, fp_lo="f", fp_hi="g",
                   dd_lo=dd[0], dd_hi=dd[1])


def _keyed(kind: str) -> Any:
    """A store of `kind` at a given ledger key, over ONE ledger: the key is the store's own."""
    db, twin = FakePg(), MemoryStore(store_floor=FLOOR)

    def at(calibration_digest: str = "cal", engine_digest: str = "eng") -> Any:
        if kind == "sql":
            return SqlStore(db, GEN, store_floor=FLOOR, calibration_digest=calibration_digest,
                            engine_digest=engine_digest)
        twin.calibration_digest, twin.engine_digest = calibration_digest, engine_digest
        return twin

    return at


@pytest.mark.parametrize("kind", ["sql", "twin"])
def test_a_pair_is_answered_only_when_all_five_keys_match(
        kind: str, monkeypatch: pytest.MonkeyPatch) -> None:
    at = _keyed(kind)
    assert at().upsert_pairs([_row(1, 2, "reject", 0.001)]) == 1
    assert at().rejected([(1, 2, "a", "b")]) == {(1, 2)}
    assert at().rejected([(1, 2, "x", "b")]) == set(), "dd_lo"
    assert at().rejected([(1, 2, "a", "x")]) == set(), "dd_hi"
    assert at().rejected([(1, 3, "a", "b")]) == set(), "another pair"
    assert at(calibration_digest="recut").rejected([(1, 2, "a", "b")]) == set(), "a re-cut"
    assert at(engine_digest="deployed").rejected([(1, 2, "a", "b")]) == set(), "a deploy"
    for module in (incremental_lane, incremental_store):
        monkeypatch.setattr(module, "FEATURE_VERSION", FEATURE_VERSION + 1)
    assert at().rejected([(1, 2, "a", "b")]) == set(), "a feature-version bump"


def test_the_lookup_statement_names_all_five_keys() -> None:
    sql = " ".join(S.RT_REJECT_LEDGER_LOOKUP_SQL.split())
    for clause in ("l.dd_lo = k.dd_lo", "l.dd_hi = k.dd_hi",
                   "l.calibration_digest = %(calibration_digest)s::text",
                   "l.feature_version = %(feature_version)s::smallint",
                   "l.engine_digest = %(engine_digest)s::text",
                   "l.generation = %(generation)s::text"):
        assert clause in sql, clause


def _imports(root: Path, name: str) -> set[str]:
    out: set[str] = set()
    for node in ast.walk(ast.parse((root / f"{name}.py").read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith(
                "autodedup."):
            out.add(node.module.split(".")[1])
        elif isinstance(node, ast.Import):
            out.update(alias.name.split(".")[1] for alias in node.names
                       if alias.name.startswith("autodedup."))
    return {module for module in out if (root / f"{module}.py").is_file()}


def test_the_engine_digest_reads_every_module_a_decision_is_computed_by() -> None:
    """`DECISION_MODULES` is the decide path's import closure inside `autodedup` plus
    `incremental` — exactly, so a new helper module cannot change a reject unseen."""
    root = Path(incremental.__file__).resolve().parent
    closure: set[str] = set()
    todo = ["decide", "features", "fingerprint", "guards", "model", "store_score", "dataset"]
    while todo:
        name = todo.pop()
        if name not in closure:
            closure.add(name)
            todo.extend(_imports(root, name) - closure)
    assert set(DECISION_MODULES) == closure | {"incremental"}
    assert DECISION_SOURCE_DIGEST == source_digest(root, DECISION_MODULES)


def test_the_engine_digest_moves_with_the_code_the_model_and_the_settings(tmp_path) -> None:
    root = Path(incremental.__file__).resolve().parent
    for name in DECISION_MODULES:
        shutil.copy(root / f"{name}.py", tmp_path / f"{name}.py")
    assert source_digest(tmp_path, DECISION_MODULES) == DECISION_SOURCE_DIGEST
    with (tmp_path / "decide.py").open("a", encoding="utf-8") as handle:
        handle.write("\n# a deploy\n")
    assert source_digest(tmp_path, DECISION_MODULES) != DECISION_SOURCE_DIGEST
    settings = _settings()
    base = engine_digest(settings, MODEL)
    assert engine_digest(_settings(), hand_initialised()) == base, "a function of content"
    assert engine_digest(replace(settings, t_lo=settings.t_lo / 2), MODEL) != base
    assert engine_digest(settings, harness.named_model("w6_gold")) != base


def test_a_database_without_596_is_store_absent(tmp_path) -> None:
    """The presence check names the ledger, so code merged before 596 was applied skips its
    passes (`store_absent` in the worker) instead of failing each one on a missing table."""
    from scraper import realtime_worker as rw

    assert "to_regclass('autodedup.rt_reject_ledger')" in S.RT_STORE_PRESENT_SQL
    world = _seeded_reject_world(tmp_path)
    world.absent_tables = {"rt_reject_ledger"}
    assert rw._autodedup_store_present(world) is False
    with pytest.raises(SystemExit, match="596"):
        incremental_lane.run_incremental(lambda: world)
    world.absent_tables = set()
    assert rw._autodedup_store_present(world) is True
    assert not incremental_lane.run_incremental(lambda: world)["aborted"]


def test_a_below_floor_deletion_issues_one_ledger_upsert_and_a_store_deletes_the_row() -> None:
    db = FakePg()
    store = SqlStore(db, GEN, store_floor=FLOOR, calibration_digest="cal", engine_digest="eng")
    before = len(db.statements)
    written = store.upsert_pairs([_row(1, 2, "reject", 0.001), _row(3, 4, "reject", 0.01),
                                  _row(5, 6, "band", 0.4), _row(7, 8, "reject", 0.5),
                                  _row(9, 10, "reject", 0.0, evidence={"unit_lo": "1"})])
    issued = db.statements[before:]
    assert written == 2
    assert issued.count(S.RT_REJECT_LEDGER_UPSERT_SQL) == 1
    assert issued.count(S.RT_REJECT_LEDGER_DELETE_SQL) == 1
    assert sorted(db.reject_ledger) == [(GEN, 1, 2), (GEN, 3, 4)]
    assert db.reject_ledger[(GEN, 1, 2)] | {"decided_at": None} == {
        "dd_lo": "a", "dd_hi": "b", "calibration_digest": "cal",
        "feature_version": FEATURE_VERSION, "engine_digest": "eng", "decided_at": None}
    assert sorted(key[1:] for key in db.pairs) == [(5, 6), (7, 8), (9, 10)]
    # Re-decided above the floor: stored, and its ledger row goes (never both).
    store.upsert_pairs([_row(1, 2, "band", 0.3)])
    assert (GEN, 1, 2) in db.pairs and (GEN, 1, 2) not in db.reject_ledger
    # Re-decided below it on new inputs: one row, overwritten.
    store.upsert_pairs([_row(3, 4, "reject", 0.001, dd=("c", "d"))])
    assert db.reject_ledger[(GEN, 3, 4)]["dd_lo"] == "c" and len(db.reject_ledger) == 1


def test_the_twin_ledgers_as_the_store_does_and_still_keeps_every_pair() -> None:
    twin = MemoryStore(store_floor=FLOOR, calibration_digest="cal", engine_digest="eng")
    assert twin.upsert_pairs([_row(1, 2, "reject", 0.001), _row(5, 6, "band", 0.4)]) == 1
    assert set(twin.pairs) == {(1, 2), (5, 6)} and set(twin.ledger) == {(1, 2)}
    twin.upsert_pairs([_row(1, 2, "band", 0.3)])
    assert not twin.ledger
    assert MemoryStore().upsert_pairs([_row(1, 2, "reject", 0.001)]) == 0, (
        "at the default floor of 0 the twin ledgers nothing")


def test_each_ledger_verb_costs_one_statement_a_chunk(monkeypatch: pytest.MonkeyPatch) -> None:
    assert LEDGER_CHUNK == 20_000
    monkeypatch.setattr(incremental_lane, "LEDGER_CHUNK", 2)
    db = FakePg()
    store = SqlStore(db, GEN, store_floor=FLOOR, calibration_digest="cal", engine_digest="eng")
    store.upsert_pairs([_row(i, i + 1, "reject", 0.001) for i in range(1, 10, 2)])
    assert db.statements.count(S.RT_REJECT_LEDGER_UPSERT_SQL) == 3
    keys = [(i, i + 1, "a", "b") for i in range(1, 10, 2)]
    assert len(store.rejected(keys)) == 5
    assert db.statements.count(S.RT_REJECT_LEDGER_LOOKUP_SQL) == 3
    assert store.rejected([]) == set()
    assert db.statements.count(S.RT_REJECT_LEDGER_LOOKUP_SQL) == 3, "no keys, no statement"


def _drained(ds: Dataset) -> tuple[FakePg, Calibration]:
    calibration = _calibration(ds, _settings())
    db = FakePg()
    work = Schedule(arrival_order(ds))
    while not work.exhausted():
        run_pass(SqlStore(db, GEN, store_floor=FLOOR, **_key(calibration)),
                 CohortFacts(ds), work, _settings(), MODEL, calibration,
                 limits=Limits(max_listings=CLAIM, max_pairs=10 ** 9), generation=GEN)
    return db, calibration


def test_a_refreshed_side_is_never_answered(scored: list[tuple[int, int]],
                                            monkeypatch: pytest.MonkeyPatch) -> None:
    """The evidence sweep re-claims an advert whose photographs moved (E92) with nothing a
    digest sees changed: every pair of it is re-decided, the ledger notwithstanding."""
    ds = _mixed_dataset()
    db, calibration = _drained(ds)
    target = next(lo for _g, lo, _hi in sorted(db.reject_ledger) if lo >= 7000)
    asked: list[tuple[int, int, str, str]] = []
    real = SqlStore.rejected

    def spy(self, keys):
        asked.extend(keys)
        return real(self, keys)

    monkeypatch.setattr(SqlStore, "rejected", spy)
    ledgered = {key[1:] for key in db.reject_ledger if target in key[1:]}
    scored.clear()
    out = run_pass(SqlStore(db, GEN, store_floor=FLOOR, **_key(calibration)),
                   CohortFacts(ds), Schedule([target], redecide=True), _settings(), MODEL,
                   calibration, limits=Limits(max_listings=CLAIM, max_pairs=10 ** 9),
                   generation=GEN)
    assert asked and not _touching([key[:2] for key in asked], {target})
    assert _touching(scored, {target}) and ledgered <= set(scored)
    assert out.ledger_skipped > 0, "the neighbours' rejects are still answered"


def test_the_budget_counts_the_pairs_a_pass_decides() -> None:
    """E943's restatement of E75/E952: a pass whose WANTED set is over `max_pairs` commits when
    the pairs it must decide fit, and is refused one pair below that."""
    ds = _mixed_dataset()
    db, calibration = _drained(ds)
    trigger = sorted(i for i in ds.listings if i >= 7000)[0]
    ds.listings[trigger].price = (ds.listings[trigger].price or 0) + 10_000
    state = db.snapshot()

    def attempt(max_pairs: int) -> PassResult:
        db.restore(copy.deepcopy(state))
        return run_pass(
            SqlStore(db, GEN, store_floor=FLOOR, **_key(calibration)),
            CohortFacts(ds), Schedule([trigger]), _settings(), MODEL, calibration,
            limits=Limits(max_listings=CLAIM, max_pairs=max_pairs), generation=GEN)

    measured = attempt(10 ** 9)
    assert measured.wanted_pairs > measured.pairs_to_decide > 0
    assert measured.ledger_skipped > 0
    fits = attempt(measured.pairs_to_decide)
    assert not fits.aborted and fits.pairs_scored == measured.pairs_to_decide
    refused = attempt(measured.pairs_to_decide - 1)
    assert refused.aborted == "pair_budget" and refused.pairs_to_decide == fits.pairs_to_decide
    assert refused.to_json()["counts"]["pairs_to_decide"] == fits.pairs_to_decide


# --------------------------------------------- the ledger rides the pass's one transaction


def _reject_world():
    """The lane world with four of its nine adverts made strangers to the rest (their own
    address, body, price, photographs and broker), so a pass decides pairs below the floor."""
    from tests.autodedup import lane_world

    world = lane_world.world()
    rnd = random.Random(3)
    for step, listing_id in enumerate((4005, 4006, 4007, 4008), start=1):
        row = world.listings[listing_id]
        row["description"] = " ".join(rnd.choice(WORDS) for _ in range(40))
        row["price_czk"] = 1_000_000 * step ** 2
        row["broker_identity_id"] = listing_id
        row["broker_phone"], row["broker_email"] = None, None
        world.locations[listing_id].update(
            street_name=f"Ulice{listing_id}", house_number_cp=str(step),
            ruian_adm_kod=listing_id, lat=50.70 + step * 0.01, lon=15.0)
        for image in world.image_rows:
            if image["listing_id"] == listing_id:
                image["phash"] = rnd.getrandbits(63)
    return world


def _seeded_reject_world(tmp_path) -> FakePg:
    """The reject world seeded, its generation's `store_floor` raised to 0.1 (the prior model
    scores the strangers' pairs 0.03-0.2) so its rejects fall below the floor."""
    from tests.autodedup import lane_world

    world = _reject_world()
    lane_world.seed_lane(world, tmp_path)
    row = world.calibration["rt"]
    row["settings"] = json.dumps({**json.loads(row["settings"]), "store_floor": 0.1})
    return world


def test_a_committed_pass_ledgers_and_reports_it(tmp_path) -> None:
    world = _seeded_reject_world(tmp_path)
    out = incremental_lane.run_incremental(lambda: world)
    assert not out["aborted"]
    assert out["counts"]["ledger_written"] == len(world.reject_ledger) > 0
    assert out["counts"]["ledger_skipped"] == 0 and out["counts"]["pairs_to_decide"] > 0
    assert {row["calibration_digest"] for row in world.reject_ledger.values()} == {
        world.calibration["rt"]["digest"]}
    assert {row["engine_digest"] for row in world.reject_ledger.values()} == {
        out["engine_digest"]}


@pytest.mark.parametrize("how", ["refused", "deadline", "stopped", "lease_lost"])
def test_a_pass_that_does_not_commit_ledgers_nothing(how: str, tmp_path,
                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    world = _seeded_reject_world(tmp_path)
    original = incremental_lane.run_pass_bounded
    if how == "refused":
        monkeypatch.setattr(incremental_lane, "PASS_LIMITS",
                            replace(incremental_lane.PASS_LIMITS, max_pairs=-1))

    def past_the_write(*args, **kwargs):
        result = original(*args, **kwargs)
        assert world.reject_ledger, "the pass wrote ledger rows before it was stopped"
        if how == "deadline":
            raise PassDeadline("the pass ran past its deadline")
        if how == "stopped":
            raise PassStopped("the worker is shutting down")
        rt_lease.release(world.other_session(), "worker:1:1")
        return result

    if how != "refused":
        monkeypatch.setattr(incremental_lane, "run_pass_bounded", past_the_write)
    out = incremental_lane.run_incremental(lambda: world, holder="worker:1:1",
                                           stopping=lambda: False)
    assert out["aborted"] in ("pair_budget", "deadline", "stopping", "lease_lost")
    assert not world.reject_ledger and not world.pairs


def test_the_statement_bound_holds_with_the_ledger() -> None:
    """E74 with ledger statements in: a whole mixed cohort in one pass, then a pass that
    re-wants its rejects, each well under the bound `test_a_pass_is_bounded_in_statements`
    holds the near-duplicate cohort to."""
    ds = _mixed_dataset()
    calibration = _calibration(ds, _settings())
    db = FakePg()
    store = SqlStore(db, GEN, store_floor=FLOOR, **_key(calibration))
    run_pass(store, CohortFacts(ds), Schedule(arrival_order(ds)), _settings(), MODEL,
             calibration, limits=Limits(max_listings=len(ds.listings), max_pairs=10 ** 9),
             generation=GEN)
    assert db.reject_ledger and store.statements < 120, store.statements
    trigger = sorted(i for i in ds.listings if i >= 7000)[0]
    ds.listings[trigger].price = (ds.listings[trigger].price or 0) + 10_000
    again = SqlStore(db, GEN, store_floor=FLOOR, **_key(calibration))
    out = run_pass(again, CohortFacts(ds), Schedule([trigger]), _settings(), MODEL,
                   calibration, limits=Limits(max_listings=CLAIM, max_pairs=10 ** 9),
                   generation=GEN)
    assert out.ledger_skipped > 0 and again.statements < 120, again.statements
