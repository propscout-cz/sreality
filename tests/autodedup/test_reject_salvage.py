"""E997: a decided reject survives its pass.

Under w34 every neighbour of a refreshed advert retrieves candidates w31 vetoed before a decision
(C4c's window), so after the switch one claim of 8 adverts wanted 74k-150k pairs no ledger row
could answer; the pass decided 74,000 of them in its 1,050 s, hit the deadline and rolled all of
them back, and the next pass met them again (2026-11-26). A pass its deadline stops now ledgers,
after the rollback, the rejects it decided between two adverts it did not refresh: nothing else.
"""

from __future__ import annotations

import ast
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from autodedup import incremental, incremental_lane
from autodedup import incremental_sql as S
from autodedup.incremental import Limits, PassDeadline, run_pass
from autodedup.incremental_lane import SqlStore
from autodedup.incremental_store import CohortFacts, Schedule
from tests.autodedup.fake_pg import FakePg
from tests.autodedup.test_incremental import _settings
from tests.autodedup.test_reject_ledger import (
    FLOOR,
    GEN,
    MODEL,
    _drained,
    _key,
    _mixed_dataset,
    _row,
    _seeded_reject_world,
)

TABLES = ("pairs", "rt_fp", "fp_key", "clusters", "cluster_members", "cluster_conflicts", "cells",
          "cursors")


def _state(db: FakePg, *names: str) -> dict[str, Any]:
    snap = db.snapshot()
    return {name: snap[name] for name in names or TABLES}


def _neighbourhood_without_rows() -> tuple[Any, FakePg, Any, int]:
    """A drained store whose ledger was emptied: every reject of a neighbourhood has to be
    decided again, as the pairs only w34 retrieves had to be."""
    ds = _mixed_dataset()
    db, calibration = _drained(ds)
    assert db.reject_ledger
    db.reject_ledger.clear()
    trigger = sorted(i for i in ds.listings if i >= 7000)[0]
    ds.listings[trigger].price = (ds.listings[trigger].price or 0) + 10_000
    return ds, db, calibration, trigger


def _pass(ds: Any, db: FakePg, calibration: Any, trigger: int, *, stop_after: int | None,
          monkeypatch: pytest.MonkeyPatch) -> tuple[Any, SqlStore, bool]:
    """One pass claiming `trigger`, scored 10 pairs a chunk; with `stop_after` the deadline
    falls once that many chunks are written, and the transaction rolls back."""
    db.now += timedelta(seconds=60)
    store = SqlStore(db, GEN, store_floor=FLOOR, **_key(calibration))
    written = {"chunks": 0}
    real = SqlStore.upsert_pairs

    def counted(self: SqlStore, rows: Any) -> int:
        out = real(self, rows)
        written["chunks"] += 1
        if stop_after is not None and written["chunks"] >= stop_after:
            raise PassDeadline("the pass ran past its deadline")
        return out

    before = db.snapshot()
    with monkeypatch.context() as patch:
        patch.setattr(SqlStore, "upsert_pairs", counted)
        try:
            result = run_pass(store, CohortFacts(ds), Schedule([trigger]), _settings(), MODEL,
                              calibration, limits=Limits(max_listings=4, max_pairs=10 ** 9,
                                                         score_chunk=10), generation=GEN)
        except PassDeadline:
            db.restore(before)
            return None, store, True
    return result, store, False


def test_a_pass_stopped_twice_converges_on_the_third_to_the_unstopped_state(
        monkeypatch: pytest.MonkeyPatch) -> None:
    ds, db, calibration, trigger = _neighbourhood_without_rows()
    ref_ds, ref_db, ref_calibration, ref_trigger = _neighbourhood_without_rows()
    reference, _store, _ = _pass(ref_ds, ref_db, ref_calibration, ref_trigger, stop_after=None,
                                 monkeypatch=monkeypatch)
    assert reference.pairs_scored > 40 and reference.ledger_skipped == 0

    kept: list[int] = []
    for stop_after in (2, 2):
        rolled_back = _state(db)
        result, store, stopped = _pass(ds, db, calibration, trigger, stop_after=stop_after,
                                       monkeypatch=monkeypatch)
        assert stopped and result is None
        ledger_before = dict(db.reject_ledger)
        salvaged = store.salvage()
        assert salvaged > 0, "two chunks of rejects between unrefreshed adverts"
        assert _state(db) == rolled_back, "the salvage writes ledger rows and nothing else"
        assert len(db.reject_ledger) > len(ledger_before)
        assert not {key[1:] for key in db.reject_ledger} & {key[1:] for key in db.pairs}
        assert not [key for key in db.reject_ledger if trigger in key[1:]], (
            "no pair of the advert whose refresh rolled back")
        kept.append(len(db.reject_ledger))
    assert kept[1] > kept[0], "the second stop adds to what the first one kept"

    result, _store, stopped = _pass(ds, db, calibration, trigger, stop_after=None,
                                    monkeypatch=monkeypatch)
    assert not stopped and not result.aborted
    assert result.ledger_skipped == kept[1], "every salvaged reject is answered, not decided again"
    assert result.pairs_scored == reference.pairs_scored - kept[1]
    for name in ("pairs", "fp_key", "clusters", "cluster_members", "cluster_conflicts"):
        assert _state(db, name) == _state(ref_db, name), name
    assert set(db.reject_ledger) == set(ref_db.reject_ledger)
    assert {k: (v["dd_lo"], v["dd_hi"]) for k, v in db.reject_ledger.items()} == {
        k: (v["dd_lo"], v["dd_hi"]) for k, v in ref_db.reject_ledger.items()}


def test_the_salvage_leaves_out_a_refreshed_side_a_stored_pair_and_a_pair_stored_since() -> None:
    db = FakePg()
    store = SqlStore(db, GEN, store_floor=FLOOR, calibration_digest="cal")
    db.pairs[(GEN, 5, 6)] = {"zone": "band"}
    store._touched.add(1)
    store._ledger([_row(1, 2, "reject", 0.001), _row(3, 4, "reject", 0.001),
                   _row(5, 6, "reject", 0.001), _row(7, 8, "reject", 0.001)], [])
    store._ledger([], [_row(7, 8, "band", 0.4)])
    db.reject_ledger.clear()
    before = len(db.statements)
    assert store.salvage() == 2
    assert db.statements[before:] == [S.RT_REJECT_LEDGER_SALVAGE_SQL]
    assert sorted(db.reject_ledger) == [(GEN, 3, 4)], (
        "1-2 has a refreshed side, 5-6 holds a stored row, 7-8 was stored later in the pass")
    assert SqlStore(db, GEN, store_floor=FLOOR, calibration_digest="cal").salvage() == 0


def test_the_salvage_statement_can_write_nothing_but_ledger_rows() -> None:
    """The rail: one INSERT into the ledger, guarded by the stored pairs, and `salvage` runs no
    other statement."""
    sql = " ".join(S.RT_REJECT_LEDGER_SALVAGE_SQL.lower().split())
    assert sql.startswith("insert into autodedup.rt_reject_ledger (")
    assert sql.count("insert into") == 1 and "update autodedup" not in sql
    assert "delete" not in sql and ";" not in sql
    assert "where not exists ( select 1 from autodedup.pairs p" in sql
    tree = ast.parse(Path(incremental_lane.__file__).read_text(encoding="utf-8"))
    method = next(node for cls in ast.walk(tree) if isinstance(cls, ast.ClassDef)
                  and cls.name == "SqlStore" for node in cls.body
                  if isinstance(node, ast.FunctionDef) and node.name == "salvage")
    names = {node.id for node in ast.walk(method) if isinstance(node, ast.Name)
             and node.id.startswith("RT_")}
    assert names == {"RT_REJECT_LEDGER_SALVAGE_SQL"}
    assert "incremental_lane" not in incremental.DECISION_MODULES


@pytest.mark.parametrize("how", ["deadline", "refused", "stopped"])
def test_only_a_deadline_stop_salvages_and_the_summary_says_how_many(
        how: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    world = _seeded_reject_world(tmp_path)
    calls: list[int] = []
    monkeypatch.setattr(SqlStore, "salvage", lambda self: calls.append(1) or 7)
    original = incremental_lane.run_pass_bounded

    def stop(*args: Any, **kwargs: Any) -> Any:
        result = original(*args, **kwargs)
        if how == "deadline":
            raise PassDeadline("the pass ran past its deadline")
        if how == "stopped":
            raise incremental.PassStopped("the worker is shutting down")
        return result

    if how == "refused":
        monkeypatch.setattr(incremental_lane, "PASS_LIMITS",
                            incremental_lane.PASS_LIMITS.__class__(max_pairs=-1))
    else:
        monkeypatch.setattr(incremental_lane, "run_pass_bounded", stop)
    out = incremental_lane.run_incremental(lambda: world, holder="worker:1:1",
                                           stopping=lambda: False)
    assert len(calls) == (1 if how == "deadline" else 0)
    if how == "deadline":
        assert out["aborted"] == "deadline" and out["ledger_salvaged"] == 7


def test_a_salvage_that_fails_writes_nothing_and_the_pass_still_reports(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    world = _seeded_reject_world(tmp_path)

    def broken(self: SqlStore) -> int:
        self.conn.reject_ledger[(GEN, 1, 2)] = {"dd_lo": "a", "dd_hi": "b", "decided_at": None}
        raise RuntimeError("canceling statement due to statement timeout")

    monkeypatch.setattr(SqlStore, "salvage", broken)
    original = incremental_lane.run_pass_bounded

    def stop(*args: Any, **kwargs: Any) -> Any:
        original(*args, **kwargs)
        raise PassDeadline("the pass ran past its deadline")

    monkeypatch.setattr(incremental_lane, "run_pass_bounded", stop)
    out = incremental_lane.run_incremental(lambda: world, holder="worker:1:1",
                                           stopping=lambda: False)
    assert out["aborted"] == "deadline" and out["ledger_salvaged"] == 0
    assert not world.reject_ledger, "its transaction rolled back"
