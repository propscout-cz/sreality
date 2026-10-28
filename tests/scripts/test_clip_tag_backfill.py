"""The runner's clip job (#2032): its selection order, and the run's behaviour with fakes.

_select_pending: SET LOCAL is a literal (not a bound param), then the fresh arm, the priority
kraje and the global newest-first drain. Regression guard for the SyntaxError that stalled
every clip_tag shard: `SET` is a PostgreSQL utility statement and cannot take a bound
parameter ($1), so the timeout must be interpolated, never passed as %s.

run(): fakes for the connection (statements recorded, pending rows re-served, a campaign),
R2 (GETs that fail, block or take fake seconds), the tagger (8-d vectors, a load counter, an
embed that can raise per image), the decoder (live images counted) and a fake clock.
"""

from __future__ import annotations

import contextlib
import io
import logging
import sys
import threading
import time
from collections.abc import Callable, Iterable, Iterator
from typing import Any

import pytest

from scraper import image_storage, stop
from scraper.clip_tagger import TagResult
from scripts import clip_tag_backfill as ctb
from scripts import retag_from_embeddings as rte


class _Cur:
    def __init__(self, conn: "_Conn") -> None:
        self._conn = conn
        self._rows: list[tuple[Any, ...]] = []

    def __enter__(self) -> "_Cur":
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False

    def execute(self, sql: str, params: Any = None) -> None:
        s = " ".join(sql.split())
        self._conn.executed.append((s, params))
        if s.startswith("SET LOCAL"):
            self._rows = []
        elif "last_download_attempt_at" in s:  # the fresh arm
            self._rows = list(self._conn.fresh_rows)
        elif isinstance(params, dict) and "region" in params:
            self._rows = list(self._conn.region_rows.get(params["region"], []))
        elif "FROM images i" in s:  # the global fallback select
            self._rows = list(self._conn.global_rows)
        else:
            self._rows = []

    def fetchall(self) -> list[tuple[Any, ...]]:
        return list(self._rows)


class _Txn:
    def __enter__(self) -> "_Txn":
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False


class _Conn:
    def __init__(self, region_rows: dict | None = None,
                 global_rows: list | None = None, fresh_rows: list | None = None) -> None:
        self.executed: list[tuple[str, Any]] = []
        self.region_rows = region_rows or {}
        self.global_rows = global_rows or []
        self.fresh_rows = fresh_rows or []

    def transaction(self) -> "_Txn":
        return _Txn()

    def cursor(self) -> "_Cur":
        return _Cur(self)


def test_region_and_global_selects_join_images_on_listing_id() -> None:
    # Gate-2: images.listing_id is fully populated + NOT-NULL-enforced (migration 350),
    # but images.sreality_id/listings.sreality_id both go NULL for every non-sreality row
    # once the flip lands. A sreality_id-keyed join (INNER, in the region arm's old form)
    # would then match NULL = NULL -> no rows -> those images silently never surface for
    # region-priority tagging, forever. Both arms must key on the surrogate instead.
    assert "i.listing_id = l.id" in ctb._SELECT_REGION
    assert "sreality_id" not in ctb._SELECT_REGION
    assert "l.id = i.listing_id" in ctb._SELECT_GLOBAL
    assert "sreality_id" not in ctb._SELECT_GLOBAL


def test_set_local_statement_timeout_is_literal_not_parameterized() -> None:
    conn = _Conn(global_rows=[(1, "k", True)])
    ctb._select_pending(conn, limit=10, priority_regions=[])
    set_stmts = [(s, p) for s, p in conn.executed if s.startswith("SET LOCAL")]
    # The fresh, region and global arms share one selection transaction.
    assert len(set_stmts) == 1
    for sql, params in set_stmts:
        assert params is None  # SET can't bind a param
        assert "%s" not in sql and "$1" not in sql
        assert str(ctb.SELECT_TIMEOUT_MS) in sql


def test_drains_priority_region_then_global() -> None:
    conn = _Conn(
        region_rows={19: [(1, "a", True), (2, "b", False)]},
        global_rows=[(3, "c", True)],
    )
    rows, phase = ctb._select_pending(
        conn, limit=10, priority_regions=[19])
    assert [r[0] for r in rows] == [1, 2, 3]  # region 19 first, then global
    assert "r19:2" in phase and "global:1" in phase


def test_priority_filling_budget_skips_global() -> None:
    conn = _Conn(
        region_rows={19: [(1, "a", True), (2, "b", True)]},
        global_rows=[(9, "x", True)],
    )
    rows, phase = ctb._select_pending(
        conn, limit=2, priority_regions=[19])
    assert [r[0] for r in rows] == [1, 2]
    assert "global" not in phase  # budget filled by the priority region


def test_region_and_global_overlap_is_deduped() -> None:
    # The global fallback has no region exclusion, so it can re-emit a priority-region
    # image — _select_pending must de-dup so it isn't processed twice in one run.
    conn = _Conn(
        region_rows={19: [(1, "a", True), (2, "b", True)]},
        global_rows=[(2, "b", True), (3, "c", True)],
    )
    rows, _ = ctb._select_pending(
        conn, limit=10, priority_regions=[19])
    assert [r[0] for r in rows] == [1, 2, 3]  # image 2 appears once


def test_the_runners_clip_chunk_decodes_inside_its_memory_declaration() -> None:
    """A chunk stays decoded until it is written, and Pillow holds RGB at 4 B a pixel (12.3.0: 4.01 MB a megapixel).
    Prod's stored photos above id 308.2M (2026-10-27): sreality 1.68 MP on average, p90 2.43 MP. At the p90, a chunk
    beside the fp32 weights (605,247,071 B), one encode batch's full-size uint8 arrays (the slow processor's, 32 at
    3 B a pixel), the prefetched chunk's bytes (~0.4 MB a photo) and the interpreter with torch (~512 MiB) fits
    memory_mb, the figure the co-tenancy census counts. The model undercounts: at 64 a chunk it reads ~1.9 GiB, and
    the first runner run (job_runs 6, 2026-10-28, 20,922 photos) peaked at 2,386 MB, so memory_mb also keeps a
    quarter over that measured peak (#2155)."""
    from scraper import jobs

    (clip,) = [j for j in jobs.runner_jobs() if j.name == "clip"]
    args = ctb._parser().parse_args(list(clip.argv[1:]))
    p90_px, mib = 2.43e6, 2**20
    peak = (args.chunk * p90_px * 4 + 605_247_071 + args.batch_size * p90_px * 3 + args.chunk * 0.4e6) / mib + 512
    assert peak <= clip.memory_mb, f"a p90 chunk of {args.chunk} peaks near {peak:.0f} MiB, past {clip.memory_mb}"
    measured_peak_mb = 2386
    assert clip.memory_mb >= 1.25 * measured_peak_mb, f"{clip.memory_mb} leaves under a quarter over {measured_peak_mb}"


def test_fresh_photos_come_before_the_priority_kraje() -> None:
    conn = _Conn(
        fresh_rows=[(50, "f", True), (40, "x", True)],
        region_rows={19: [(40, "x", True), (30, "r", True)], 27: [(20, "q", True)]},
        global_rows=[(10, "g", True)],
    )
    rows, phase = ctb._select_pending(conn, limit=10, priority_regions=[19, 27])
    assert [r[0] for r in rows] == [50, 40, 30, 20, 10]  # 40 once, at its fresh place
    assert phase == "fresh:2+r19:2+r27:1+global:1"
    arms = [s for s, _ in conn.executed if not s.startswith("SET LOCAL")]
    assert arms == [" ".join(sql.split()) for sql in
                    (ctb._SELECT_FRESH, ctb._SELECT_REGION, ctb._SELECT_REGION, ctb._SELECT_GLOBAL)]
    assert "interval '45 minutes'" in ctb._SELECT_FRESH and "ORDER BY i.id DESC" in ctb._SELECT_FRESH


@pytest.mark.parametrize("argv", [["--shard", "0"], ["--shards", "4"], ["--categories", "dum"]])
def test_no_shard_flag_or_shard_clause_remains(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as refused, contextlib.redirect_stderr(io.StringIO()):
        ctb._parser().parse_args(argv)
    assert refused.value.code == 2
    for sql in (ctb._SELECT_FRESH, ctb._SELECT_REGION, ctb._SELECT_GLOBAL):
        assert "shard" not in sql


def test_the_clip_job_bounds_each_r2_get(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}
    monkeypatch.setattr(image_storage.R2Client, "from_env", lambda **kwargs: seen.update(kwargs) or "r2")
    assert ctb._r2_client(16) == "r2"
    assert seen == {"max_pool_connections": 20, "connect_timeout_s": 5, "read_timeout_s": 20, "max_attempts": 2}


def test_missing_r2_refuses_the_run(monkeypatch: pytest.MonkeyPatch) -> None:
    import psycopg

    monkeypatch.setenv("SUPABASE_DB_URL", "postgresql://unused")
    for name in image_storage.R2_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(sys, "argv", ["clip_tag_backfill"])
    monkeypatch.setattr(psycopg, "connect", lambda *a, **k: pytest.fail("a refused run never connects"))
    assert ctb.main() == 2


# ── run() with fakes ──────────────────────────────────────────────────────────────────────────

class _Clock:
    """Fake monotonic seconds: `advance` moves it; `step` moves it on every read too."""

    def __init__(self, step: float = 0.0) -> None:
        self.t = 0.0
        self.step = step
        self._lock = threading.Lock()

    def __call__(self) -> float:
        with self._lock:
            now = self.t
            self.t += self.step
            return now

    def advance(self, seconds: float) -> None:
        with self._lock:
            self.t += seconds


class _StoreCur:
    def __init__(self, db: "_Store") -> None:
        self._db = db
        self._rows: list[tuple[Any, ...]] = []

    def __enter__(self) -> "_StoreCur":
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False

    def execute(self, sql: str, params: Any = None) -> None:
        db = self._db
        rows: list[tuple[Any, ...]] = []
        if sql.startswith("SET LOCAL"):
            db.selects.append([])
            db.pending |= set(db.arrivals.get(len(db.selects), ()))
            if db.on_select:
                db.on_select(len(db.selects))
        elif "clip_tagging_priority_region_ids" in sql:
            rows = [([],)]
        elif sql in (ctb._SELECT_FRESH, ctb._SELECT_REGION, ctb._SELECT_GLOBAL):
            pool = db.pending & db.fresh if sql == ctb._SELECT_FRESH else (
                db.pending if sql == ctb._SELECT_GLOBAL else set())
            got = sorted(pool, reverse=True)[:params["limit"]]
            db.selects[-1] += got
            rows = [(i, f"img/{i}.jpg", True) for i in got]
        elif sql == ctb._MARK_SQL:
            db.writes.append("stamp")
            db.marked += [i for i in params[0] if i in db.pending]
            db.pending -= set(params[0])
        elif sql == rte._CUTOFF_SQL:
            rows = [("2026-06-26T16:45:00Z",)]
        elif sql == rte._PENDING_SQL:
            rows = [(False, db.campaign_left())]
        elif sql == rte._SELECT:
            db.writes.append("retag_select")
            rows = [(i, "m", "[0.5,0.5]") for i in db.campaign[db.retagged:db.retagged + params["limit"]]]
        else:
            raise AssertionError(f"unexpected SQL: {sql}")
        self._rows = rows

    def executemany(self, sql: str, params: list[tuple[Any, ...]]) -> None:
        db = self._db
        if sql == ctb._UPSERT_SQL:
            db.writes.append("tags")
            db.tagged += [p[0] for p in params]
        elif sql == ctb._UPSERT_EMB_SQL:
            db.writes.append("vectors")
            db.vectored += [p[0] for p in params]
        elif sql == rte._UPDATE:
            db.writes.append("retag_update")
            db.retagged += len(params)
            if db.clock is not None:
                db.clock.advance(db.retag_secs)
        else:
            raise AssertionError(f"unexpected executemany: {sql}")

    def fetchone(self) -> tuple[Any, ...] | None:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[tuple[Any, ...]]:
        return list(self._rows)


class _Store:
    """The job's database: pending images served newest first (arrivals join at the n-th select), the writes in
    order, and a campaign of stored vectors."""

    def __init__(self, ids: Iterable[int] = (), *, fresh: Iterable[int] = (),
                 arrivals: dict[int, Iterable[int]] | None = None, campaign: int = 0,
                 on_select: Callable[[int], None] | None = None) -> None:
        self.pending = set(ids)
        self.on_select = on_select
        self.fresh = set(fresh)
        self.arrivals = {n: list(v) for n, v in (arrivals or {}).items()}
        self.campaign = list(range(10**9, 10**9 + campaign))
        self.retagged = 0
        self.retag_secs = 0.0
        self.clock: _Clock | None = None
        self.marked: list[int] = []
        self.tagged: list[int] = []
        self.vectored: list[int] = []
        self.writes: list[str] = []
        self.selects: list[list[int]] = []

    def campaign_left(self) -> bool:
        return self.retagged < len(self.campaign)

    def transaction(self) -> contextlib.AbstractContextManager[None]:
        return contextlib.nullcontext()

    def cursor(self) -> _StoreCur:
        return _StoreCur(self)


class _Bytes(bytes):
    """A GET's body: counts how many are alive at once."""

    made = 0
    live = 0

    def __new__(cls, value: bytes) -> "_Bytes":
        with _Live.lock:
            _Bytes.made += 1
            _Bytes.live += 1
        return super().__new__(cls, value)

    def __del__(self) -> None:
        with _Live.lock:
            _Bytes.live -= 1


class _R2:
    """GETs return the image id as bytes (`junk` ids: bytes that decode to no image); `fail(id, n)` (n: the GET's
    number) raises after `secs` fake seconds; an id in `block` waits (its first GET only) until `release` or 3 s,
    counted in `blocking` meanwhile."""

    def __init__(self, clock: _Clock, *, fail: Callable[[int, int], bool] = lambda i, n: False, secs: float = 0.0,
                 delay: float = 0.0, block: Iterable[int] = (), junk: Iterable[int] = (),
                 on_get: Callable[[int, int], None] | None = None) -> None:
        self.clock, self.fail, self.secs, self.delay, self.on_get = clock, fail, secs, delay, on_get
        self.block = set(block)
        self.junk = set(junk)
        self.release = threading.Event()
        self.blocking = 0
        self.calls: list[int] = []
        self._lock = threading.Lock()

    def download_bytes(self, key: str) -> bytes:
        image_id = int(key.split("/")[1].split(".")[0])
        with self._lock:
            self.calls.append(image_id)
            n = len(self.calls)
            blocked = image_id in self.block
            self.block.discard(image_id)
        if self.on_get:
            self.on_get(image_id, n)
        if self.delay:
            time.sleep(self.delay)
        if blocked:
            with self._lock:
                self.blocking += 1
            self.release.wait(3)
            with self._lock:
                self.blocking -= 1
        self.clock.advance(self.secs)
        if self.fail(image_id, n):
            raise RuntimeError("R2 blip")
        return _Bytes(b"not-an-image" if image_id in self.junk else str(image_id).encode())


class _Live:
    """A decoded image: counts how many are alive at once."""

    lock = threading.RLock()
    live = 0
    peak = 0

    def __init__(self, image_id: int) -> None:
        self.image_id = image_id
        with _Live.lock:
            _Live.live += 1
            _Live.peak = max(_Live.peak, _Live.live)

    def __del__(self) -> None:
        with _Live.lock:
            _Live.live -= 1


class _Vec:
    def __init__(self, image_id: int) -> None:
        self.image_id = image_id

    def tolist(self) -> list[float]:
        return [((self.image_id * (k + 1)) % 7) / 7 for k in range(8)]


class _Tagger:
    model_id = "openai/clip-vit-base-patch32"
    revision = "3d74acf9a28c67741b2f4f2ea7635f0aaf6f0268"

    def __init__(self, clock: _Clock, *, secs: float = 0.0,
                 raises: Callable[[list[int]], BaseException | None] = lambda ids: None,
                 on_embed: Callable[[list[int]], None] | None = None) -> None:
        self.clock, self.secs, self.raises, self.on_embed = clock, secs, raises, on_embed
        self.batches: list[list[int]] = []

    def embed(self, images: list[_Live], batch_size: int = 32) -> list[_Vec]:
        ids = [im.image_id for im in images]
        self.batches.append(ids)
        if self.on_embed:
            self.on_embed(ids)
        self.clock.advance(self.secs)
        exc = self.raises(ids)
        if exc is not None:
            raise exc
        return [_Vec(i) for i in ids]

    def tags_from_emb(self, emb: list[Any]) -> list[TagResult]:
        return [TagResult("kitchen", "kitchen", 0.9) for _ in emb]


class _Job:
    """One harness: a store, a clock, R2 and a tagger, and the factory calls the run made."""

    def __init__(self, store: _Store, *, clock: _Clock | None = None, r2: _R2 | None = None,
                 tagger: _Tagger | None = None) -> None:
        self.store = store
        self.clock = clock or _Clock()
        store.clock = self.clock
        self.r2 = r2 or _R2(self.clock)
        self.tagger = tagger or _Tagger(self.clock)
        self.loads: list[int] = []
        self.r2_builds = 0

    def _load(self, threads: int) -> _Tagger:
        self.loads.append(threads)
        return self.tagger

    def _r2(self) -> _R2:
        self.r2_builds += 1
        return self.r2

    def run(self, *argv: str) -> int:
        """The fakes move the clock without real time passing, so a chunk waits for its GETs unless a test sets the
        download deadline."""
        args = ctb._parser().parse_args(["--workers", "16", "--download-deadline", "1e9", *argv])
        return ctb.run(self.store, args, r2_factory=self._r2, tagger_factory=self._load, clock=self.clock)


@pytest.fixture(autouse=True)
def _fakes(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(ctb, "_POLL_S", 0.005)
    real = ctb._decode
    monkeypatch.setattr(ctb, "_decode", lambda data: _Live(int(data)) if data.isdigit() else real(data))
    monkeypatch.setattr(rte, "_as_tensor", lambda vectors: vectors)
    _Live.live = _Live.peak = 0
    _Bytes.made = _Bytes.live = 0
    yield


def _lines(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.name in (ctb.LOG.name, rte.LOG.name)]


def _newest(n: int, top: int) -> set[int]:
    return set(range(top, top - n, -1))


def test_nothing_pending_and_no_campaign_never_loads_the_model(caplog: pytest.LogCaptureFixture) -> None:
    job = _Job(_Store())
    with caplog.at_level(logging.INFO):
        assert job.run("--max-seconds", "1500") == 0
    assert job.loads == [] and job.r2_builds == 0
    assert _lines(caplog)[-1] == "CLIP_JOB exit=0 why=nothing"


def test_a_run_cut_by_the_budget_with_progress_exits_more_remains(caplog: pytest.LogCaptureFixture) -> None:
    clock = _Clock()
    job = _Job(_Store(range(1, 3001)), clock=clock, tagger=_Tagger(clock, secs=200))
    with caplog.at_level(logging.INFO):
        assert job.run("--max-seconds", "100") == 75
    assert set(job.store.marked) == _newest(128, 3000) and len(job.store.marked) == 128, "chunk 2 never marked"
    assert _lines(caplog)[-1] == "CLIP_JOB exit=75 why=budget"


def test_the_run_reselects_and_ends_on_a_reselect_under_one_chunk(caplog: pytest.LogCaptureFixture) -> None:
    store = _Store(range(1, 301), arrivals={2: range(1001, 1201), 3: range(2001, 2041)})
    job = _Job(store)
    with caplog.at_level(logging.INFO):
        assert job.run("--max-seconds", "1500") == 0
    assert len(store.marked) == len(set(store.marked)) == 540
    assert len(job.loads) == 1 and len(store.selects) == 3
    assert _lines(caplog)[-1] == "CLIP_JOB exit=0 why=done"


def test_a_reselect_after_its_interval_puts_a_newer_arrival_first() -> None:
    """Chunk 2 was prefetched before the 300 s passed; the first chunk selected after them leads with the arrival."""
    clock = _Clock()
    store = _Store(range(1, 5001), arrivals={2: [9999]})
    job = _Job(store, clock=clock, tagger=_Tagger(clock, secs=310))
    assert job.run("--max-seconds", "1000") == 75
    assert job.tagger.batches[2][0] == 9999 and 9999 in store.marked


@pytest.mark.parametrize(("case", "pending", "argv", "fail", "secs", "embed_secs", "code"), [
    ("a: every GET fails fast", 5000, [], lambda i, n: True, 0.0, 0.0, 1),
    ("b: the same at --limit 20000", 20000, ["--limit", "20000"], lambda i, n: True, 0.0, 0.0, 1),
    ("c: every GET fails after 52 s, past the deadline", 5000, ["--download-deadline", "45"], lambda i, n: True,
     52.0, 0.0, 1),
    ("d: 60 % of GETs fail and the deadline cuts", 5000, [], lambda i, n: n % 5 < 3, 0.0, 100.0, 0),
    ("e: 10 % of GETs fail and the deadline cuts", 5000, [], lambda i, n: n % 10 == 0, 0.0, 100.0, 75),
], ids=lambda v: v if isinstance(v, str) else "")
def test_transient_download_errors_never_chain_the_job(case: str, pending: int, argv: list[str],
                                                       fail: Callable[[int, int], bool], secs: float,
                                                       embed_secs: float,
                                                       code: int) -> None:
    clock = _Clock()
    job = _Job(_Store(range(1, pending + 1)), clock=clock, r2=_R2(clock, fail=fail, secs=secs),
               tagger=_Tagger(clock, secs=embed_secs))
    assert job.run("--max-seconds", "1500", *argv) == code, case
    if code == 1:
        assert job.store.marked == [] and len(job.r2.calls) <= 2 * 128, "the all-failed chunk ends the phase"
    else:
        assert job.store.marked


def test_the_next_chunks_downloads_overlap_the_current_encode() -> None:
    clock = _Clock()
    chunk2_fetched = threading.Event()
    overlapped: list[bool] = []

    def on_get(image_id: int, _n: int) -> None:
        if image_id <= 128:
            chunk2_fetched.set()

    def on_embed(ids: list[int]) -> None:
        if ids[0] == 256:
            overlapped.append(chunk2_fetched.wait(2))

    job = _Job(_Store(range(1, 257)), clock=clock, r2=_R2(clock, on_get=on_get),
               tagger=_Tagger(clock, on_embed=on_embed))
    assert job.run() == 0
    assert overlapped == [True], "chunk 2's GETs ran while chunk 1 encoded"
    assert len(job.store.marked) == 256
    assert 0 < _Live.peak <= 128, "one chunk decoded at a time"


def test_a_straggler_past_the_download_deadline_stays_unmarked() -> None:
    clock = _Clock(step=1.0)
    r2 = _R2(clock, block=[64])
    store = _Store(range(1, 129), arrivals={2: range(1001, 1129)})
    job = _Job(store, clock=clock, r2=r2)
    try:
        assert job.run("--download-deadline", "45") == 0
        first = set(range(1, 129)) - {64}
        assert set(job.tagger.batches[0]) == first and set(store.marked[:127]) == first, "the other 127 are tagged"
        assert len(store.selects) == 3 and store.selects[2][-1] == 64, "a later re-select still sees the straggler"
        assert 64 not in store.marked and 64 in store.pending, "this run skips it; the next slot retries it"
        assert len(store.marked) == 255 and r2.calls.count(64) == 1
    finally:
        r2.release.set()


def test_images_whose_get_always_fails_cost_one_get_a_run_and_never_end_the_drain(
        caplog: pytest.LogCaptureFixture) -> None:
    """A dead object (deleted from R2, say) is newer than the backlog behind it, so every re-select would offer it
    first again: 200 of them would fill an all-failed chunk and end the run. Each costs one GET a run instead."""
    clock = _Clock()
    dead = set(range(25, 5001, 25))
    store = _Store(range(1, 5001))
    r2 = _R2(clock, fail=lambda i, n: i in dead)
    with caplog.at_level(logging.INFO):
        assert _Job(store, clock=clock, r2=r2).run("--max-seconds", "1500") == 0
    assert _lines(caplog)[-1] == "CLIP_JOB exit=0 why=done"
    assert set(store.marked) == set(range(1, 5001)) - dead and dead <= store.pending
    assert {i: r2.calls.count(i) for i in dead} == dict.fromkeys(dead, 1)


def test_a_few_failing_gets_alone_are_no_r2_outage(caplog: pytest.LogCaptureFixture) -> None:
    """Nothing else waits (a quiet hour, three dead objects): under a chunk of failed GETs is no outage and starts no
    failure streak; a whole chunk failing is one (test_transient_download_errors_never_chain_the_job's cases a-c)."""
    clock = _Clock()
    with caplog.at_level(logging.INFO):
        assert _Job(_Store(range(1, 4)), clock=clock, r2=_R2(clock, fail=lambda i, n: True)).run() == 0
    assert _lines(caplog)[-1] == "CLIP_JOB exit=0 why=errors"


def test_a_chunks_bytes_are_dropped_once_it_is_decoded() -> None:
    """While chunk 1 encodes, the only GET bodies alive are the prefetched chunk's (decision 5's memory budget)."""
    clock = _Clock()
    alive: list[int] = []

    def on_embed(ids: list[int]) -> None:
        if ids[0] == 384:
            until = time.monotonic() + 2
            while _Bytes.made < 256 and time.monotonic() < until:
                time.sleep(0.001)
            alive.append(_Bytes.live)

    assert _Job(_Store(range(1, 385)), clock=clock, tagger=_Tagger(clock, on_embed=on_embed)).run() == 0
    assert alive == [128], "chunk 2's bodies, never chunk 1's"


def test_a_prefetched_chunk_whose_deadline_passed_during_the_encode_still_gets_a_wait() -> None:
    """Its GETs queued behind four slow ones while chunk 1 encoded for 50 s, past its 45 s from submission: once
    current it still waits a moment, so the GETs that were about to land are tagged, not dropped."""
    clock = _Clock()
    r2 = _R2(clock, block=[128, 127, 126, 125])

    def on_embed(ids: list[int]) -> None:
        if ids[0] == 256:
            threading.Timer(0.2, r2.release.set).start()

    job = _Job(_Store(range(1, 257)), clock=clock, r2=r2, tagger=_Tagger(clock, secs=50, on_embed=on_embed))
    try:
        assert job.run("--workers", "4", "--download-deadline", "45") == 0
        assert len(job.store.marked) == 256
    finally:
        r2.release.set()


def test_a_get_cancelled_before_it_started_is_no_attempt(caplog: pytest.LogCaptureFixture) -> None:
    """One stalled GET holds the only worker past the deadline: one attempt, failed; the 127 queued behind it were
    never tried."""
    clock = _Clock(step=1.0)
    r2 = _R2(clock, block=[128])
    try:
        with caplog.at_level(logging.INFO):
            assert _Job(_Store(range(1, 129)), clock=clock, r2=r2).run("--workers", "1", "--download-deadline",
                                                                        "45") == 1
    finally:
        r2.release.set()
    done = next(line for line in _lines(caplog) if line.startswith("CLIP_TAG done "))
    assert " errors=1 attempts=1 " in done, done
    assert r2.calls == [128]


def test_stalled_gets_never_hold_up_the_decode() -> None:
    """Both download workers are stuck on GETs abandoned at the deadline; the chunk still decodes and encodes."""
    clock = _Clock(step=1.0)
    r2 = _R2(clock, block=[2, 1])
    held: list[int] = []
    tagger = _Tagger(clock, on_embed=lambda ids: held.append(r2.blocking))
    try:
        assert _Job(_Store(range(1, 129)), clock=clock, r2=r2, tagger=tagger).run(
            "--workers", "2", "--download-deadline", "45") == 0
    finally:
        r2.release.set()
    assert held[:1] == [2], "chunk 1 encoded while both stalled GETs still held the download workers"


def test_tag_runs_before_retag_with_one_model_load_and_one_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    deadlines: list[float] = []
    real = rte.retag

    def spy(*args: Any, **kwargs: Any) -> tuple[int, bool]:
        deadlines.append(kwargs["deadline"])
        return real(*args, **kwargs)

    monkeypatch.setattr(rte, "retag", spy)
    store = _Store(range(1, 301), campaign=1000)
    job = _Job(store)
    assert job.run("--max-seconds", "1500") == 0
    assert len(job.loads) == 1
    assert max(i for i, w in enumerate(store.writes) if w == "stamp") < store.writes.index("retag_select")
    assert deadlines == [300.0] and not store.campaign_left(), "a --reselect-seconds slice inside the run's deadline"


def test_a_retag_slice_ends_at_the_reselect_interval_so_new_photos_go_first(caplog: pytest.LogCaptureFixture) -> None:
    """Photos stored while a campaign re-tags wait for the next run: 300 s of retag a run, never the whole budget."""
    store = _Store(campaign=20 * 512)
    store.retag_secs = 100.0
    job = _Job(store)
    with caplog.at_level(logging.INFO):
        assert job.run("--max-seconds", "1500") == 75
    assert store.retagged == 3 * 512
    assert _lines(caplog)[-1] == "CLIP_JOB exit=75 why=retag"


@pytest.mark.parametrize(("campaign", "code", "why"), [(2000, 75, "retag"), (100, 0, "done")])
def test_a_retag_cut_by_the_budget_exits_75_and_a_drained_one_exits_0(
        caplog: pytest.LogCaptureFixture, campaign: int, code: int, why: str) -> None:
    store = _Store(campaign=campaign)
    store.retag_secs = 100.0
    job = _Job(store)
    with caplog.at_level(logging.INFO):
        assert job.run("--max-seconds", "1500") == code
    assert _lines(caplog)[-1] == f"CLIP_JOB exit={code} why={why}"
    assert len(job.loads) == 1 and job.r2_builds == 0


def test_each_chunk_writes_tags_then_vectors_then_the_stamp() -> None:
    """autodedup reads clip_tagged_at as written last, after the tags and the vector (autodedup/incremental.py's
    stamp contract, autodedup/incremental_sql.py's CLIP arm)."""
    store = _Store(range(1, 301))
    assert _Job(store).run() == 0
    assert store.writes == ["tags", "vectors", "stamp"] * 3


def test_a_batch_that_fails_to_embed_isolates_one_bad_image(caplog: pytest.LogCaptureFixture) -> None:
    clock = _Clock()
    tagger = _Tagger(clock, raises=lambda ids: RuntimeError("shape") if 7 in ids else None)
    store = _Store(range(1, 51))
    with caplog.at_level(logging.INFO):
        assert _Job(store, clock=clock, tagger=tagger).run() == 0
    assert sorted(store.marked) == list(range(1, 51))
    assert sorted(store.tagged) == sorted(store.vectored) == sorted(set(range(1, 51)) - {7})
    failed = [line for line in _lines(caplog) if line.startswith("CLIP_TAG embed failed image=")]
    assert failed == ["CLIP_TAG embed failed image=7: RuntimeError"]
    assert " terminal=1 " in next(line for line in _lines(caplog) if line.startswith("CLIP_TAG done "))


@pytest.mark.parametrize("raises", [
    lambda ids: RuntimeError("every image"),
    lambda ids: RuntimeError("three bad") if {5, 7, 9} & set(ids) else None,
    lambda ids: MemoryError() if 7 in ids else None,
    lambda ids: MemoryError() if ids == [7] else RuntimeError("batch") if len(ids) > 1 else None,
], ids=["no-image-embeds-alone", "over-the-cap", "memory-error", "memory-error-alone"])
def test_an_embed_failure_that_is_not_one_images_marks_nothing_and_exits_1(
        caplog: pytest.LogCaptureFixture, raises: Callable[[list[int]], BaseException | None]) -> None:
    clock = _Clock()
    store = _Store(range(1, 51))
    with caplog.at_level(logging.INFO):
        assert _Job(store, clock=clock, tagger=_Tagger(clock, raises=raises)).run() == 1
    assert store.marked == [] and store.writes == []
    assert _lines(caplog)[-1] == "CLIP_JOB exit=1 why=embed"


@pytest.mark.parametrize("kind", ["embed", "decode"])
def test_the_run_stamps_at_most_five_photos_terminal(caplog: pytest.LogCaptureFixture, kind: str) -> None:
    """Two failing photos in each of four chunks: chunks 1 and 2 mark theirs; chunk 3 would make six, so it stamps
    none of its own (an environmental regression never stamps a whole run's photos): an embed failure exits 1 there, a
    decode failure on the next chunk that fails too."""
    clock = _Clock()
    bad = {500, 499, 372, 371, 244, 243, 116, 115}
    store = _Store(range(1, 513))
    if kind == "embed":
        job = _Job(store, clock=clock,
                   tagger=_Tagger(clock, raises=lambda ids: RuntimeError("shape") if bad & set(ids) else None))
    else:
        job = _Job(store, clock=clock, r2=_R2(clock, junk=bad))
    with caplog.at_level(logging.INFO):
        assert job.run() == 1
    assert _lines(caplog)[-1] == f"CLIP_JOB exit=1 why={kind}"
    assert bad & set(store.marked) == {500, 499, 372, 371}
    if kind == "embed":
        assert len(store.marked) == 256, "chunk 3 marks nothing"
    else:  # one chunk past a cap is skipped, so its photos that decode are tagged; the second in a row ends the run
        assert len(store.marked) == 256 + 126, "chunk 3 stamps neither bad photo, chunk 4 marks nothing"


def test_an_undecodable_photo_beside_siblings_that_decode_is_marked_terminal(
        caplog: pytest.LogCaptureFixture) -> None:
    clock = _Clock()
    store = _Store(range(1, 51))
    with caplog.at_level(logging.INFO):
        assert _Job(store, clock=clock, r2=_R2(clock, junk=[7, 9])).run() == 0
    assert sorted(store.marked) == list(range(1, 51))
    assert sorted(store.tagged) == sorted(store.vectored) == sorted(set(range(1, 51)) - {7, 9})
    assert [line for line in _lines(caplog) if line.startswith("CLIP_TAG undecodable ")] == [
        "CLIP_TAG undecodable image=9: UnidentifiedImageError", "CLIP_TAG undecodable image=7: UnidentifiedImageError"]
    assert " terminal=2 " in next(line for line in _lines(caplog) if line.startswith("CLIP_TAG done "))


def _oom(data: bytes) -> Any:
    raise MemoryError


@pytest.mark.parametrize(("ids", "junk", "oom", "tagged"), [
    (range(1, 257), range(1, 257), (), set()),
    (range(1, 257), (250, 249, 248, 120, 119, 118), (), set(range(129, 257)) - {250, 249, 248}),
    (range(1, 51), (), (7,), set())], ids=["nothing-decodes", "over-the-cap", "memory-error"])
def test_a_decode_failure_that_is_not_one_photos_marks_nothing_and_exits_1(
        caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch, ids: Iterable[int], junk: Iterable[int],
        oom: tuple[int, ...], tagged: set[int]) -> None:
    """A Pillow regression or memory pressure, not a corrupt file: stamping would lose the photos' CLIP evidence. One
    chunk past a cap is not yet the environment (a listing's photos sit side by side): its photos that decode are
    tagged and the rest wait; a second in a row is, and the run ends there with that chunk unmarked. A MemoryError ends
    it at once."""
    fake = ctb._decode
    monkeypatch.setattr(ctb, "_decode", lambda data: _oom(data) if data.isdigit() and int(data) in oom else fake(data))
    clock = _Clock()
    store = _Store(ids)
    with caplog.at_level(logging.INFO):
        assert _Job(store, clock=clock, r2=_R2(clock, junk=junk)).run() == 1
    assert set(store.marked) == set(store.tagged) == tagged, "no undecodable photo is stamped, chunk 2 marks nothing"
    assert _lines(caplog)[-1] == "CLIP_JOB exit=1 why=decode"


def test_one_listings_undecodable_photos_past_the_chunk_cap_never_wedge_the_queue(
        caplog: pytest.LogCaptureFixture) -> None:
    """Three corrupt photos of one listing land in one chunk, past its cap of 2. Every run reaches them newest first,
    so ending the run there would stall every photo behind them and keep job/clip a Problem for ever: the chunk's
    other photos are tagged, the three wait unmarked (offered again by the next run), and the run goes on."""
    clock = _Clock()
    store = _Store(range(1, 513))
    r2 = _R2(clock, junk=(500, 499, 498))
    with caplog.at_level(logging.INFO):
        assert _Job(store, clock=clock, r2=r2).run() == 0
    assert sorted(store.marked) == sorted(store.tagged) == sorted(set(range(1, 513)) - {500, 499, 498})
    lines = _lines(caplog)
    assert "CLIP_TAG decode failed chunk n=128 undecodable=3: not one image's" in lines
    assert " terminal=0 undecoded=3 " in next(line for line in lines if line.startswith("CLIP_TAG done "))
    assert lines[-1] == "CLIP_JOB exit=0 why=done"
    r2.calls.clear()
    assert _Job(store, clock=clock, r2=r2).run() == 0
    assert sorted(r2.calls) == [498, 499, 500] and not {498, 499, 500} & set(store.marked)


def test_a_stop_during_a_reselect_submits_no_more_gets() -> None:
    clock = _Clock()
    store = _Store(range(1, 301), arrivals={2: range(1001, 1129)},
                   on_select=lambda n: stop.EVENT.set() if n == 2 else None)
    # The encode takes a moment, so GETs submitted before it would have started.
    job = _Job(store, clock=clock, tagger=_Tagger(clock, on_embed=lambda ids: time.sleep(0.05)))
    try:
        assert job.run() == stop.EXIT_CODE
        assert len(store.selects) == 2 and not [i for i in job.r2.calls if i > 1000], "no GET after the stop"
        assert len(store.marked) == 300, "the chunk in hand still commits"
    finally:
        stop.reset()


def test_a_stop_during_the_model_load_submits_no_get() -> None:
    clock = _Clock()
    store = _Store(range(1, 301))
    job = _Job(store, clock=clock)

    def load(threads: int) -> _Tagger:
        stop.EVENT.set()
        return job.tagger

    try:
        assert ctb.run(store, ctb._parser().parse_args([]), r2_factory=job._r2, tagger_factory=load,
                       clock=clock) == stop.EXIT_CODE
        assert job.r2.calls == [] and store.marked == []
    finally:
        stop.reset()


def test_a_stop_signal_finishes_the_chunk_in_hand_and_exits_143() -> None:
    clock = _Clock()

    def on_get(_image_id: int, n: int) -> None:
        if n == 10:
            stop.EVENT.set()

    r2 = _R2(clock, block=[256, 255], delay=0.01, on_get=on_get)
    job = _Job(_Store(range(1, 257)), clock=clock, r2=r2)
    try:
        assert job.run("--workers", "4") == stop.EXIT_CODE
        assert job.store.marked, "the chunk in hand commits what decoded"
        assert not {256, 255} & set(job.store.marked)
        assert set(job.store.marked) <= set(r2.calls)
        assert not [i for i in r2.calls if i <= 128], "no GET of chunk 2 after the stop"
        assert len(r2.calls) < 128, "the queued GETs were cancelled"
    finally:
        r2.release.set()
        stop.reset()


def test_a_full_budget_run_fits_the_log_tail(caplog: pytest.LogCaptureFixture) -> None:
    clock = _Clock()
    store = _Store(range(1, 90 * 128 + 1), campaign=100 * 512)
    store.retag_secs = 0.5
    job = _Job(store, clock=clock, tagger=_Tagger(clock, secs=15))
    with caplog.at_level(logging.INFO):
        assert job.run("--max-seconds", "1500") == 0
    lines = _lines(caplog)
    assert len(store.marked) == 90 * 128 and not store.campaign_left()
    assert len(lines) <= 40, len(lines)
    assert [line.split(" ", 2)[:2] for line in lines[-3:]] == [["CLIP_TAG", "done"], ["RETAG", "done"],
                                                                ["CLIP_JOB", "exit=0"]]
