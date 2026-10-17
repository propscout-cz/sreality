"""E950: a pass scores the pairs it decides a chunk at a time, and gives each chunk's vectors back.

The gate is EQUIVALENCE: the same cohort and the same claims through two engines, each on its own
FakePg and its own twin — one scoring every decided pair at once, one a chunk of 1, 2 or 5 at a
time — and after every pass the two databases hold the same rows (every column the fake keeps,
the features and the reject ledger included), the two twins the same pairs (every field of every
row, the feature vectors too), groups and conflicts, and the two passes report the same counts.
Only WHEN a row reaches the store moves. Beside it, what the chunks are for: a side's vectors are
read once a pass, in the chunk that first decides it, and given back after the last.
"""

from __future__ import annotations

import math
import random
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from autodedup import harness, incremental, incremental_lane
from autodedup.dataset import Dataset
from autodedup.export import encode_clip
from autodedup.incremental import (
    Calibration,
    EvidenceHold,
    Limits,
    PassResult,
    _score_chunks,
    _Working,
    context_for,
    engine_digest,
    run_pass,
)
from autodedup.incremental_lane import SqlFacts, SqlStore
from autodedup.incremental_store import CohortFacts, MemoryStore, Schedule
from autodedup.export_sql import COHORT_CLIP_SQL
from autodedup.model import hand_initialised
from autodedup.settings import Settings
from tests.autodedup.fake_pg import LEDGER_KEY, FakePg
from tests.autodedup.test_incremental import _calibration, _settings, arrival_order
from tests.autodedup.test_narrower_fetch import (
    ORDER,
    _dataset as _world_dataset,
    _vector as _pg_vector,
    _sql_evidence,
    _sql_groups,
    _sql_rows,
    _world,
)
from tests.autodedup.test_reject_ledger import _db_state, _mixed_dataset, _reject_world

GEN = "rt"
FLOOR = 0.02
CLAIM = 4
MODEL = hand_initialised()
WHOLE = 10 ** 9
NOW = FakePg().now.timestamp()


def _vectored(ds: Dataset) -> Dataset:
    """Every photograph with a CLIP vector, one of five a frame position across the cohort, so
    some galleries agree and the CLIP features fire."""
    for listing_id, images in ds.images_by_listing.items():
        ds.images_by_listing[listing_id] = [
            replace(image, clip=_vector((image.seq or 0) % 5), tags=[("living_room", 0.8)],
                    clip_tagged_at="2026-10-12T10:00:00+00:00")
            for image in images]
    return ds


def _vector(seed: int) -> str:
    rnd = random.Random(seed)
    return encode_clip([rnd.uniform(-1, 1) for _ in range(512)])


class _Counting(CohortFacts):
    """The cohort's facts, keeping every image id a vector read asked for."""

    def __init__(self, ds: Dataset, asked: list[int]) -> None:
        super().__init__(ds)
        self.asked = asked

    def vectors(self, image_ids):
        wanted = list(image_ids)
        self.asked.extend(wanted)
        return super().vectors(wanted)


class _Engine:
    """One engine's database and twin, driven a pass at a time at one chunk size."""

    def __init__(self, chunk: int, order: list[int]) -> None:
        self.chunk = chunk
        self.db = FakePg()
        self.twin = MemoryStore(now=NOW, store_floor=FLOOR)
        self.work = Schedule(order)
        self.twin_work = Schedule(order)

    def run(self, ds: Dataset, calibration: Calibration, held: dict[str, list[int]],
            work: Any = None, twin_work: Any = None, claim: int = CLAIM
            ) -> tuple[PassResult, list[int], int, list[int]]:
        settings = _settings()
        key = {"calibration_digest": calibration.digest(),
               "engine_digest": engine_digest(settings, MODEL)}
        limits = Limits(max_listings=claim, max_pairs=10 ** 9, score_chunk=self.chunk)
        hold = EvidenceHold(now=NOW, horizon_s=48 * 3600.0)
        asked: list[int] = []
        held["vectored"] = []
        store = SqlStore(self.db, GEN, store_floor=FLOOR, **key)
        writes: list[int] = []
        upsert = store.upsert_pairs
        store.upsert_pairs = lambda rows: writes.append(len(rows)) or upsert(rows)  # type: ignore[method-assign]
        result = run_pass(store, _Counting(ds, asked), work or self.work, settings, MODEL,
                          calibration, limits=limits, generation=GEN, hold=hold)
        peak = max(held["vectored"], default=0)
        self.twin.calibration_digest = key["calibration_digest"]
        self.twin.engine_digest = key["engine_digest"]
        run_pass(self.twin, CohortFacts(ds), twin_work or self.twin_work, settings, MODEL,
                 calibration, limits=limits, generation=GEN, hold=hold)
        return result, asked, peak, writes


@pytest.fixture
def held(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[int]]:
    """How many listings carry their CLIP vectors after each read of them."""
    seen: dict[str, list[int]] = {"vectored": []}
    real = _Working.ensure

    def ensure(self: _Working, ids, **kwargs: Any) -> None:
        real(self, ids, **kwargs)
        seen["vectored"].append(len(self.vectored))

    monkeypatch.setattr(_Working, "ensure", ensure)
    return seen


COUNTS = ("pairs_scored", "pairs_written", "pairs_deleted", "ledger_skipped",
          "pairs_to_decide", "ledger_written", "components", "clusters_written",
          "clusters_dropped", "held", "released", "redecided", "zones", "certificates",
          "cursors")


def _lockstep(whole: _Engine, chunked: _Engine, ds: Dataset, calibration: Calibration,
              held: dict[str, list[int]], sweep: list[int] | None = None
              ) -> tuple[PassResult, int, int]:
    """One pass of each engine over the same claim (the drain's next, or the evidence sweep of
    `sweep` in one claim); asserts the two are one engine. Returns the chunked pass and both
    engines' peak count of listings holding their vectors."""
    def claims() -> dict[str, Any]:
        return {} if sweep is None else {"work": Schedule(sweep, redecide=True),
                                         "twin_work": Schedule(sweep, redecide=True),
                                         "claim": len(sweep)}

    ref, ref_asked, ref_peak, ref_writes = whole.run(ds, calibration, held, **claims())
    out, asked, peak, writes = chunked.run(ds, calibration, held, **claims())
    assert not ref.aborted and not out.aborted
    assert _db_state(chunked.db) == _db_state(whole.db)
    assert chunked.db.reject_ledger == whole.db.reject_ledger
    assert chunked.twin.pairs == whole.twin.pairs, "every field, the feature vectors too"
    assert chunked.twin.clusters == whole.twin.clusters
    assert chunked.twin.conflicts == whole.twin.conflicts
    assert chunked.twin.fp == whole.twin.fp
    assert chunked.twin.ledger == whole.twin.ledger
    for name in COUNTS:
        assert getattr(out, name) == getattr(ref, name), name
    assert len(out.chunk_rss_mb) == math.ceil(out.pairs_scored / chunked.chunk)
    assert len(ref.chunk_rss_mb) == min(1, ref.pairs_scored)
    assert out.to_json()["counts"]["chunks"] == len(out.chunk_rss_mb) == len(out.chunk_vectored)
    # One write as each chunk closes, none of them empty, and one after the last chunk for the
    # rows restated behind it: the rows a pass holds are one chunk's.
    for result, written in ((out, writes), (ref, ref_writes)):
        assert len(written) == len(result.chunk_rss_mb) + 1 and all(written[:-1])
        assert sum(written) == result.pairs_written
    # A side's vectors are read once a pass, in the chunk that first decides it: the same
    # photographs as the whole pass reads, and none twice.
    assert len(asked) == len(set(asked)) and set(asked) == set(ref_asked)
    assert peak <= ref_peak
    return out, ref_peak, peak


# ----------------------------------------------------------------------- the gate


@pytest.mark.parametrize("chunk", [1, 2, 5])
@pytest.mark.parametrize("order_name", ["arrival", "reversed"])
def test_a_chunked_pass_writes_what_the_whole_pass_writes(
        chunk: int, order_name: str, held: dict[str, list[int]]) -> None:
    ds = _vectored(_mixed_dataset())
    calibration = _calibration(ds, _settings())
    order = arrival_order(ds) if order_name == "arrival" else list(reversed(arrival_order(ds)))
    whole, chunked = _Engine(WHOLE, order), _Engine(chunk, order)

    chunks, fewer = 0, 0
    while not chunked.work.exhausted():
        out, ref_peak, peak = _lockstep(whole, chunked, ds, calibration, held)
        chunks = max(chunks, len(out.chunk_rss_mb))
        fewer += peak < ref_peak
    assert chunks > 1, "a pass must have scored more than one chunk"
    if chunk == 1:
        assert fewer, "the chunks held fewer listings' vectors at once than the whole pass"
    assert chunked.db.reject_ledger, "the cohort must decide pairs below the floor"
    assert any(row["zone"] == "merge" for row in chunked.db.pairs.values()), "and merge"

    # The evidence sweep re-decides the whole cohort in one claim, in many chunks (the claim's
    # vectors are all read before the first, for the refresh's evidence, E92).
    out, _ref_peak, _peak = _lockstep(whole, chunked, ds, calibration, held, sweep=order)
    assert len(out.chunk_rss_mb) == math.ceil(out.pairs_scored / chunk) > 1
    assert all(reading is None or reading > 0 for reading in out.chunk_rss_mb)


def test_the_lane_facts_read_each_side_s_vectors_once_a_pass() -> None:
    """The production fact source and store (`SqlFacts`, `SqlStore` over the FakePg) with E93's
    hold, a chunk a pair against the whole pass: the pairs, every feature, the groups and the
    refresh's evidence are equal, and per pass `COHORT_CLIP_SQL` names the same photographs,
    none of them twice."""
    settings = Settings()
    calibration = harness.calibrated(_world_dataset(_world()), settings)[1]
    hold = EvidenceHold(now=_world().now.timestamp(), horizon_s=48 * 3600.0)

    def drain(chunk: int) -> tuple[FakePg, list[list[int]], list[PassResult]]:
        conn = _world()
        store = SqlStore(conn, GEN, store_floor=0.0, **LEDGER_KEY)
        work = Schedule(ORDER)
        reads, passes = [], []
        while not work.exhausted():
            mark = len(conn.issued)
            passes.append(run_pass(store, SqlFacts(conn), work, settings, MODEL, calibration,
                                   limits=Limits(max_listings=2, max_pairs=10 ** 9,
                                                 score_chunk=chunk), hold=hold))
            reads.append([int(i) for text, params in conn.issued[mark:]
                          if text == COHORT_CLIP_SQL for i in params["ids"]])
        return conn, reads, passes

    whole, whole_reads, whole_passes = drain(WHOLE)
    one, one_reads, one_passes = drain(1)
    assert _sql_rows(one) == _sql_rows(whole)
    assert {key: row["features"] for key, row in one.pairs.items()} == {
        key: row["features"] for key, row in whole.pairs.items()}
    assert _sql_groups(one) == _sql_groups(whole)
    assert _sql_evidence(one) == _sql_evidence(whole)
    for read, whole_read in zip(one_reads, whole_reads):
        assert len(read) == len(set(read)) and set(read) == set(whole_read)
    assert max(len(p.chunk_rss_mb) for p in one_passes) > 1
    assert sum(p.zones.get("merge", 0) for p in one_passes) > 0, "the world must merge"
    assert [p.held for p in one_passes] == [p.held for p in whole_passes]


# ------------------------------------------------------------------------- the units


def test_the_chunks_open_on_what_they_read_and_close_on_what_no_later_chunk_reads() -> None:
    ordered = [(1, 2), (1, 3), (2, 3), (3, 4), (4, 9), (5, 6)]
    decide = {(1, 2), (2, 3), (3, 4), (4, 9), (5, 6)}
    known = {1, 2, 3, 4, 5, 6}
    opens, closes = _score_chunks(ordered, decide, known, 2)
    # (1, 3) stands and (4, 9) has a side the pass does not hold: neither is scored.
    assert opens == {0: {1, 2, 3}, 3: {3, 4, 5, 6}}
    assert closes == {2: {1, 2}, 5: {3, 4, 5, 6}}
    assert _score_chunks(ordered, decide, known, 10 ** 9) == (
        {0: {1, 2, 3, 4, 5, 6}}, {5: {1, 2, 3, 4, 5, 6}})
    assert _score_chunks(ordered, set(), known, 2) == ({}, {})


def test_forget_drops_everything_the_context_derived_of_a_listing_and_nothing_else() -> None:
    ds = _vectored(_mixed_dataset())
    settings = _settings()
    calibration = _calibration(ds, settings)
    working = _Working(CohortFacts(ds), settings)
    first, second = sorted(ds.listings)[:2]
    working.ensure([first, second], clip=True)
    ctx = context_for(calibration, settings, working.fps, working.listings)
    for listing_id in (first, second):
        images = working.images[listing_id]
        ctx.clip_gallery(working.fps[listing_id], images)
        ctx.tag_gallery(listing_id, images)
        ctx.phash_gallery(listing_id, images)
        ctx.non_catalog_count(listing_id, images)
        ctx.events(working.listings[listing_id])

    def held_of(listing_id: int) -> list[str]:
        return [name for name in ("tfidf", "tfidf_corpus", "rare", "_events")
                if listing_id in getattr(ctx, name)] + [
            name for name in ("_clip", "_phash", "_live", "_tags")
            if any(key[0] == listing_id for key in getattr(ctx, name))]

    assert held_of(first) == held_of(second) and len(held_of(first)) == 8
    ctx.forget([first])
    assert held_of(first) == [] and len(held_of(second)) == 8

    working.release([first])
    assert first not in working.vectored and second in working.vectored
    assert all(image.clip is None for image in working.images[first])
    assert all(image.clip is not None for image in working.images[second])
    working.ensure([first], clip=True)
    assert all(image.clip is not None for image in working.images[first]), "read again"



def test_a_row_restated_after_the_last_chunk_is_written(held: dict[str, list[int]]) -> None:
    """A stored decision that stands but whose probes moved is re-written without a score; when
    it sorts after the pass's last decided pair, the write after the chunks carries it."""
    ds = _vectored(_mixed_dataset())
    calibration = _calibration(ds, _settings())
    order = arrival_order(ds)
    engine = _Engine(1, order)
    while not engine.work.exhausted():
        engine.run(ds, calibration, held)
    last = max(engine.db.pairs)
    kept = list(engine.db.pairs[last]["probes"])
    engine.db.pairs[last]["probes"] = ["stale"]
    first = min(ds.listings)
    assert first < last[1]
    ds.listings[first].price = (ds.listings[first].price or 0) + 10_000
    out, _asked, _peak, _writes = engine.run(ds, calibration, held, work=Schedule([first]),
                                             twin_work=Schedule([first]))
    assert out.pairs_scored and engine.db.pairs[last]["probes"] == kept


def test_a_pair_never_scores_without_its_sides_vectors(monkeypatch: pytest.MonkeyPatch) -> None:
    """A side given back too early would score with no CLIP gallery, no tfidf and no vectors in
    its evidence, which E93's hold covers only while the side is young. So the pass refuses to
    score it: here no chunk reads its sides' vectors, and the first pair whose side the claim
    did not already carry stops the pass instead of being decided on less."""
    real = incremental._score_chunks
    monkeypatch.setattr(incremental, "_score_chunks", lambda *args: ({}, real(*args)[1]))
    ds = _vectored(_mixed_dataset())
    settings = _settings()
    calibration = _calibration(ds, settings)
    store, work = MemoryStore(), Schedule(arrival_order(ds))
    limits = Limits(max_listings=CLAIM, max_pairs=10 ** 9, score_chunk=1)
    with pytest.raises(RuntimeError, match="without its sides' CLIP vectors"):
        while not work.exhausted():
            run_pass(store, CohortFacts(ds), work, settings, MODEL, calibration, limits=limits)


@pytest.mark.parametrize("size", [0, -3])
def test_a_chunk_size_below_one_scores_a_pair_a_chunk(size: int) -> None:
    ds = _vectored(_mixed_dataset())
    settings = _settings()
    calibration = _calibration(ds, settings)
    stores = []
    for chunk in (size, WHOLE):
        store, work = MemoryStore(), Schedule(arrival_order(ds))
        scored, chunks = 0, 0
        while not work.exhausted():
            out = run_pass(store, CohortFacts(ds), work, settings, MODEL, calibration,
                           limits=Limits(max_listings=CLAIM, max_pairs=10 ** 9, score_chunk=chunk))
            scored, chunks = scored + out.pairs_scored, chunks + len(out.chunk_rss_mb)
        stores.append(store)
        if chunk == size:
            assert chunks == scored > 0
    assert stores[0].pairs == stores[1].pairs


def _lane_world(tmp_path: Path) -> FakePg:
    """`test_reject_ledger`'s lane world (nine adverts, four of them strangers) with a CLIP
    vector on every photograph, seeded, drained by one pass, then one advert re-priced, so the
    next pass claims it and decides its pairs with eight sides it did not claim: each side's
    vectors are read as its chunk opens, through `SqlFacts` and its stop checkpoint."""
    from tests.autodedup import lane_world

    world = _reject_world()
    for row in world.image_rows:
        world.clip_vectors[int(row["image_id"])] = _pg_vector(int(row["phash"]) % 7)
        row["clip_tagged_at"] = lane_world.EXPORTED_AT
    lane_world.seed_lane(world, tmp_path)
    for _ in range(10):
        out = incremental_lane.run_incremental(lambda: world)
        assert not out["aborted"]
        if not out["counts"]["claimed"]:
            break
    world.listings[4003]["price_czk"] += 10_000
    world.snapshots.append({"id": 10_000, "listing_id": 4003, "scraped_at": lane_world.EXPORTED_AT,
                            "price_czk": world.listings[4003]["price_czk"]})
    return world


def _pass_tables(db: FakePg) -> dict[str, Any]:
    return {**_db_state(db), "reject_ledger": {k: dict(v) for k, v in db.reject_ledger.items()}}


def test_a_pass_stopped_between_chunks_leaves_the_database_as_it_found_it(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A chunk a pair, and the worker shutting down once the first chunk is written: the next
    chunk's vector read is the checkpoint that stops the pass (`SqlFacts._slices`), the
    transaction rolls back to the database the pass found, and the next pass writes exactly
    what a pass that was never stopped writes."""
    monkeypatch.setattr(incremental_lane, "PASS_LIMITS",
                        replace(incremental_lane.PASS_LIMITS, score_chunk=1))
    stopped, whole = _lane_world(tmp_path / "stopped"), _lane_world(tmp_path / "whole")
    writes: list[int] = []
    flag = {"stop": False}
    real = SqlStore.upsert_pairs

    def upsert(self: SqlStore, rows):
        written = real(self, rows)
        if rows:
            writes.append(len(rows))
            flag["stop"] = True
        return written

    monkeypatch.setattr(SqlStore, "upsert_pairs", upsert)
    before = _pass_tables(stopped)
    out = incremental_lane.run_incremental(lambda: stopped, holder="worker:1:1",
                                           stopping=lambda: flag["stop"])
    assert out["aborted"] == incremental_lane.STOPPING
    assert writes == [1], "stopped at the second chunk's vector read, after the first write"
    assert _pass_tables(stopped) == before

    flag["stop"] = False
    again = incremental_lane.run_incremental(lambda: stopped, holder="worker:1:2")
    ref = incremental_lane.run_incremental(lambda: whole, holder="worker:1:2")
    assert not again["aborted"] and not ref["aborted"]
    assert again["counts"]["chunks"] == ref["counts"]["chunks"] == ref["counts"]["pairs_scored"] > 1
    assert _pass_tables(stopped) == _pass_tables(whole)
