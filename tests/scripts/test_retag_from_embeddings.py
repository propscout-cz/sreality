"""The retag library the runner's clip job calls after tagging (#2032): a campaign's cutoff only while a row waits,
batches until none is left or the deadline, and a log line a minute, never one a batch."""

from __future__ import annotations

import logging
from typing import Any

import pytest

from scraper import stop
from scraper.clip_tagger import TagResult
from scripts import retag_from_embeddings as rte


class _Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


class _Cur:
    def __init__(self, db: "_DB") -> None:
        self._db = db
        self._rows: list[tuple[Any, ...]] = []

    def __enter__(self) -> "_Cur":
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False

    def execute(self, sql: str, params: Any = None) -> None:
        self._db.executed.append(sql)
        if sql == rte._CUTOFF_SQL:
            self._rows = [] if self._db.cutoff is _UNSET else [(self._db.cutoff,)]
        elif sql == rte._PENDING_SQL:
            self._rows = [(self._db.future, bool(self._db.waiting))]
        elif sql == rte._SELECT:
            self._db.selects += 1
            self._rows = [(i, "m", "[0.5,0.5]") for i in self._db.waiting[:params["limit"]]]
        else:
            raise AssertionError(f"unexpected SQL: {sql}")

    def executemany(self, sql: str, params: list[tuple[Any, ...]]) -> None:
        assert sql == rte._UPDATE
        done = {p[-2] for p in params}  # (…, image_id, model)
        self._db.waiting = [i for i in self._db.waiting if i not in done]
        self._db.clock.t += self._db.secs_per_batch

    def fetchone(self) -> tuple[Any, ...] | None:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[tuple[Any, ...]]:
        return list(self._rows)


_UNSET = object()


class _DB:
    def __init__(self, *, cutoff: Any = "2026-06-26T16:45:00Z", waiting: int = 0, secs_per_batch: float = 0.0,
                 future: bool = False) -> None:
        self.cutoff = cutoff
        self.future = future
        self.waiting = list(range(1, waiting + 1))
        self.secs_per_batch = secs_per_batch
        self.executed: list[str] = []
        self.selects = 0
        self.clock = _Clock()

    def cursor(self) -> _Cur:
        return _Cur(self)


class _Tagger:
    def tags_from_emb(self, emb: Any) -> list[TagResult]:
        return [TagResult("kitchen", "kitchen", 0.9) for _ in emb]


@pytest.fixture(autouse=True)
def _no_torch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rte, "_as_tensor", lambda vectors: vectors)


@pytest.mark.parametrize("cutoff", [_UNSET, None, ""])
def test_no_cutoff_is_no_campaign(cutoff: Any) -> None:
    db = _DB(cutoff=cutoff, waiting=3)
    assert rte.campaign_cutoff(db) is None
    assert rte._PENDING_SQL not in db.executed


def test_a_drained_campaign_is_no_campaign() -> None:
    assert rte.campaign_cutoff(_DB(waiting=0)) is None
    assert rte.campaign_cutoff(_DB(waiting=1)) == "2026-06-26T16:45:00Z"


def test_a_cutoff_later_than_the_databases_now_is_no_campaign(caplog: pytest.LogCaptureFixture) -> None:
    """Re-tagged rows are stamped now(), still before such a cutoff: the campaign would never drain, and every run
    would re-tag for its whole slice and exit 75, green. It waits, with one warning a run, until the cutoff passes."""
    db = _DB(waiting=3, future=True)
    with caplog.at_level(logging.WARNING, logger=rte.LOG.name):
        assert rte.campaign_cutoff(db) is None
    assert [r.getMessage() for r in caplog.records] == [
        "RETAG cutoff=2026-06-26T16:45:00Z is later than the database's now(): no campaign until it passes"]
    assert db.selects == 0


def test_retag_loops_until_no_row_is_left() -> None:
    db = _DB(waiting=1300)
    assert rte.retag(db, _Tagger(), "c", deadline=1e9, batch_size=512, clock=db.clock) == (1300, True)
    assert db.waiting == [] and db.selects == 4


def test_retag_stops_at_the_deadline_undrained() -> None:
    db = _DB(waiting=5000, secs_per_batch=10.0)
    written, drained = rte.retag(db, _Tagger(), "c", deadline=25.0, batch_size=512, clock=db.clock)
    assert (written, drained) == (3 * 512, False)
    assert db.waiting


def test_retag_stops_at_a_stop_signal_undrained() -> None:
    db = _DB(waiting=5000)
    stop.EVENT.set()
    assert rte.retag(db, _Tagger(), "c", deadline=1e9, batch_size=512, clock=db.clock) == (0, False)


def test_no_shard_text_remains_in_the_sql() -> None:
    for sql in (rte._CUTOFF_SQL, rte._PENDING_SQL, rte._SELECT, rte._UPDATE):
        assert "shard" not in sql and "%%" not in sql
    assert not hasattr(rte, "main"), "the CLI went with the workflow: the clip job is the one caller"


def test_retag_logs_at_most_one_progress_line_a_minute(caplog: pytest.LogCaptureFixture) -> None:
    db = _DB(waiting=100 * 10, secs_per_batch=5.0)
    with caplog.at_level(logging.INFO, logger=rte.LOG.name):
        assert rte.retag(db, _Tagger(), "c", deadline=1e9, batch_size=10, clock=db.clock) == (1000, True)
    lines = [r.getMessage() for r in caplog.records]
    assert db.selects == 101 and db.clock.t == 500.0
    assert all(line.startswith("RETAG progress ") for line in lines)
    assert 1 <= len(lines) <= 500 // 60 + 1, lines
