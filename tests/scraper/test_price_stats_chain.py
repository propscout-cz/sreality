"""The price-stats runaway (#2013): one stalest-first queue across active datasets, and a chain only on progress."""

from __future__ import annotations

import datetime as dt
import pathlib
from typing import Any

import pytest
import yaml

from scraper import price_stats_main as pm

ROOT = pathlib.Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "scrape_price_stats.yml"
UTC = dt.timezone.utc


def _loc(name: str, last: dt.datetime | None) -> dict[str, Any]:
    return {"entity_type": "muni", "entity_id": hash(name) % 10_000, "name": name, "obec_id": None,
            "last_fetched_at": last}


def _plan(dataset_id: int, *locs: dict[str, Any]) -> pm.Plan:
    return pm.Plan({"id": dataset_id}, list(locs), (2015, 1), (2026, 10))


def test_the_queue_is_stalest_first_across_datasets_never_fetched_first() -> None:
    big = _plan(1, _loc("Brno", dt.datetime(2026, 10, 1, tzinfo=UTC)), _loc("Aš", None))
    small = _plan(2, _loc("Cheb", dt.datetime(2026, 9, 1, tzinfo=UTC)), _loc("Děčín", None))
    order = [(plan.dataset["id"], loc["name"]) for plan, loc in pm.work_queue([big, small])]
    assert order == [(1, "Aš"), (2, "Děčín"), (2, "Cheb"), (1, "Brno")]


class _Db:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []
        self._run_ids = iter(range(100, 200))

    def start_run(self, _conn: Any, dataset_id: int, *, cities_total: int) -> int:
        run_id = next(self._run_ids)
        self.calls.append(("start", (dataset_id, cities_total, run_id)))
        return run_id

    def update_run_progress(self, _conn: Any, run_id: int, *, cities_done: int, observations: int) -> None:
        self.calls.append(("progress", (run_id, cities_done)))

    def recompute_metrics(self, _conn: Any, dataset_id: int, *, window_years: int) -> int:
        self.calls.append(("metrics", dataset_id))
        return 0

    def finish_run(self, _conn: Any, run_id: int, *, status: str, **_kw: Any) -> None:
        self.calls.append(("finish", (run_id, status)))

    def upsert_observations(self, _conn: Any, **_kw: Any) -> int:
        return 1

    def clear_no_data(self, *_a: Any) -> None:
        return None

    def record_no_data(self, *_a: Any) -> None:
        return None


class _Client:
    def fetch_series(self, _dataset: Any, **_kw: Any) -> dict[str, Any]:
        return {"months": [{"ym": "2026-09"}]}


def test_the_run_stops_at_the_budget_and_opens_a_run_row_only_for_datasets_it_reached(monkeypatch) -> None:
    fake = _Db()
    for name in ("start_run", "update_run_progress", "recompute_metrics", "finish_run",
                 "upsert_observations", "clear_no_data", "record_no_data"):
        monkeypatch.setattr(pm.db, name, getattr(fake, name))
    ticks = iter([False, False, True])
    monkeypatch.setattr(pm, "deadline_reached", lambda _d: next(ticks))
    a = _plan(1, _loc("Aš", None), _loc("Brno", dt.datetime(2026, 10, 1, tzinfo=UTC)))
    b = _plan(2, _loc("Děčín", None))
    c = _plan(3, _loc("Cheb", dt.datetime(2026, 10, 2, tzinfo=UTC)))
    queue = pm.work_queue([a, b, c])

    assert pm.run_queue(object(), _Client(), queue, window_years=5, chunk_months=24, dry_run=False,
                        deadline=1.0) is True

    started = {args[0]: args[2] for kind, args in fake.calls if kind == "start"}
    assert started == {1: 100, 2: 101}, "dataset 3 was never reached, so it opens no run"
    assert ("start", (1, 2, 100)) in fake.calls, "a run counts its dataset's whole queue"
    assert sorted(d for kind, d in fake.calls if kind == "metrics") == [1, 2]
    assert sorted(args for kind, args in fake.calls if kind == "finish") == [(100, "success"), (101, "success")]


@pytest.mark.parametrize(("before", "after", "chain"), [
    (500, 120, True),   # progress, more remains: one more run
    (500, 500, False),  # no progress: never re-trigger
    (500, 0, False),    # complete: the weekly schedule refreshes
    (0, 0, False),
])
def test_main_chains_only_on_progress_with_more_remaining(monkeypatch, tmp_path, before, after, chain) -> None:
    out = tmp_path / "out"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    remaining = iter([before, after])
    monkeypatch.setattr(pm.db, "connect", lambda: object())
    monkeypatch.setattr(pm.db, "sweep_stale_runs", lambda _c: 0)
    monkeypatch.setattr(pm, "get_session_cookies", lambda: {})
    monkeypatch.setattr(pm, "PriceStatsClient", lambda **_kw: _Client())
    monkeypatch.setattr(pm, "_load_city_names", lambda _p: [])
    monkeypatch.setattr(pm, "resolve_localities", lambda *_a, **_k: 0)
    monkeypatch.setattr(pm.db, "load_active_datasets", lambda _c: [{"id": 1, "obec_ids": None}])
    monkeypatch.setattr(pm, "_dataset_localities", lambda *_a, **_k: [_loc("Aš", None)])
    monkeypatch.setattr(pm, "run_queue", lambda *_a, **_k: True)
    monkeypatch.setattr(pm.db, "refresh_choropleth", lambda _c: None)
    monkeypatch.setattr(pm.db, "coverage_remaining", lambda *_a: next(remaining))

    assert pm.main([]) == 0
    assert out.read_text() == f"chain={'1' if chain else '0'}\n"


def test_the_workflow_chains_on_its_own_token_and_keeps_run_now() -> None:
    text = WORKFLOW.read_text()
    assert "SCRAPE_CHAIN_TOKEN" not in text
    doc = yaml.safe_load(text)
    (job,) = doc["jobs"].values()
    assert job["permissions"] == {"contents": "read", "actions": "write"}
    (chain,) = [s for s in job["steps"] if s.get("name", "").startswith("Chain")]
    assert "steps.ingest.outputs.chain == '1'" in chain["if"]
    assert chain["env"] == {"CHAIN_TOKEN": "${{ github.token }}"}
    # api/price_stats.run_dataset_now ("Run now") dispatches mode + dataset_id.
    inputs = doc[True]["workflow_dispatch"]["inputs"]
    assert {"mode", "dataset_id"} <= set(inputs) and "full" in inputs["mode"]["options"]
    assert doc[True]["schedule"] == [{"cron": "0 4 * * 1"}], "one scheduled run a week"
