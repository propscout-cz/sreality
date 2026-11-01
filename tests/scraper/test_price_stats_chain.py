"""The price-stats runaway (#2013): one stalest-first queue across active datasets, and a chain only on progress."""

from __future__ import annotations

import datetime as dt
import os
import pathlib
import subprocess
from typing import Any

import pytest
import yaml

from scraper import price_stats_db
from scraper import price_stats_main as pm

ROOT = pathlib.Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "scrape_price_stats.yml"
UTC = dt.timezone.utc


def _loc(name: str, last: dt.datetime | None, entity_id: int = 1) -> dict[str, Any]:
    return {"entity_type": "muni", "entity_id": entity_id, "name": name, "obec_id": None, "last_fetched_at": last}


def _run(dataset_id: int, *locs: dict[str, Any]) -> pm.DatasetRun:
    return pm.DatasetRun({"id": dataset_id}, list(locs), (2015, 1), (2026, 10))


def test_the_queue_is_stalest_first_across_datasets_never_fetched_first() -> None:
    big = _run(1, _loc("Brno", dt.datetime(2026, 10, 1, tzinfo=UTC)), _loc("Aš", None))
    small = _run(2, _loc("Cheb", dt.datetime(2026, 9, 1, tzinfo=UTC)), _loc("Děčín", None))
    order = [(run.dataset["id"], loc["name"]) for run, loc in pm.work_queue([big, small])]
    assert order == [(1, "Aš"), (2, "Děčín"), (2, "Cheb"), (1, "Brno")]


def test_a_locality_dates_from_its_last_fetch_or_its_last_empty_check() -> None:
    # An expired no-data marker re-enters the queue dated by that check, never as never-scraped: only a
    # locality with neither data nor a marker is coverage the run can make progress on.
    source = " ".join(pathlib.Path(price_stats_db.__file__).read_text().split())
    assert "coalesce(o.mf, nd.checked_at) AS last_fetched_at" in source
    assert "ORDER BY coalesce(o.mf, nd.checked_at) ASC NULLS FIRST" in source


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

    def finish_run(self, _conn: Any, run_id: int, *, status: str, error: str | None = None, **_kw: Any) -> None:
        self.calls.append(("finish", (run_id, status, error)))

    def upsert_observations(self, _conn: Any, **_kw: Any) -> int:
        return 1

    def clear_no_data(self, *_a: Any) -> None:
        return None

    def record_no_data(self, *_a: Any) -> None:
        return None


class _Client:
    def __init__(self, fail_on: str | None = None) -> None:
        self._fail_on = fail_on

    def fetch_series(self, _dataset: Any, **kw: Any) -> dict[str, Any]:
        if kw.get("entity_id") == 99 and self._fail_on:
            raise RuntimeError(self._fail_on)
        return {"months": [{"ym": "2026-09"}]}


def _fake_db(monkeypatch: pytest.MonkeyPatch) -> _Db:
    fake = _Db()
    for name in ("start_run", "update_run_progress", "recompute_metrics", "finish_run",
                 "upsert_observations", "clear_no_data", "record_no_data"):
        monkeypatch.setattr(pm.db, name, getattr(fake, name))
    return fake


def test_the_run_stops_at_the_budget_and_opens_a_run_row_only_for_datasets_it_reached(
        monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _fake_db(monkeypatch)
    ticks = iter([False, False, True])
    monkeypatch.setattr(pm, "deadline_reached", lambda _d: next(ticks))
    a = _run(1, _loc("Aš", None), _loc("Brno", dt.datetime(2026, 10, 1, tzinfo=UTC)))
    b = _run(2, _loc("Děčín", None))
    c = _run(3, _loc("Cheb", dt.datetime(2026, 10, 2, tzinfo=UTC)))

    assert pm.run_queue(object(), _Client(), pm.work_queue([a, b, c]), window_years=5, chunk_months=24,
                        dry_run=False, deadline=1.0) is True

    started = {args[0]: args[2] for kind, args in fake.calls if kind == "start"}
    assert started == {1: 100, 2: 101}, "dataset 3 was never reached, so it opens no run"
    assert ("start", (1, 2, 100)) in fake.calls, "a run counts its dataset's whole queue"
    assert sorted(d for kind, d in fake.calls if kind == "metrics") == [1, 2]
    assert sorted(args for kind, args in fake.calls if kind == "finish") == [(100, "success", None),
                                                                              (101, "success", None)]


def test_a_failing_locality_fails_its_own_dataset_and_names_it_on_the_others(
        monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _fake_db(monkeypatch)
    monkeypatch.setattr(pm, "deadline_reached", lambda _d: False)
    a = _run(1, _loc("Aš", None))
    b = _run(2, _loc("Brno", None, entity_id=99))
    with pytest.raises(RuntimeError):
        pm.run_queue(object(), _Client(fail_on="HTTP 500"), pm.work_queue([a, b]), window_years=5,
                     chunk_months=24, dry_run=False)
    finished = {args[0]: (args[1], args[2]) for kind, args in fake.calls if kind == "finish"}
    assert finished == {100: ("failed", "stopped by dataset 2: HTTP 500"), 101: ("failed", "HTTP 500")}


def _patch_main(monkeypatch: pytest.MonkeyPatch, remaining: list[int], events: list[str]) -> None:
    counts = iter(remaining)
    monkeypatch.setattr(pm.db, "connect", lambda: object())
    monkeypatch.setattr(pm.db, "sweep_stale_runs", lambda _c: 0)
    monkeypatch.setattr(pm, "get_session_cookies", lambda: {})
    monkeypatch.setattr(pm, "PriceStatsClient", lambda **_kw: _Client())
    monkeypatch.setattr(pm, "_load_city_names", lambda _p: [])
    monkeypatch.setattr(pm, "resolve_localities", lambda *_a, **_k: 0)
    monkeypatch.setattr(pm.db, "load_active_datasets", lambda _c: [{"id": 1, "obec_ids": None},
                                                                    {"id": 2, "obec_ids": [5]}])

    def localities(*_a: Any, **_k: Any) -> list[dict[str, Any]]:
        events.append("resolve")
        return [_loc("Aš", None)]

    def coverage(*_a: Any) -> int:
        events.append("count")
        return next(counts)

    monkeypatch.setattr(pm, "_dataset_localities", localities)
    monkeypatch.setattr(pm, "run_queue", lambda *_a, **_k: events.append("run") or True)
    monkeypatch.setattr(pm.db, "refresh_choropleth", lambda _c: None)
    monkeypatch.setattr(pm.db, "coverage_remaining", coverage)


@pytest.mark.parametrize(("before", "after", "chain"), [
    (500, 120, True),   # progress, more remains: one more run
    (500, 500, False),  # no progress: never re-trigger
    (500, 0, False),    # complete: the weekly schedule refreshes
    (0, 0, False),
])
def test_main_chains_only_on_progress_with_more_remaining(
        monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, before: int, after: int, chain: bool) -> None:
    out = tmp_path / "out"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    events: list[str] = []
    _patch_main(monkeypatch, [before, 0, after, 0], events)  # two datasets, counted before and after
    assert pm.main([]) == 0
    assert out.read_text() == f"chain={'1' if chain else '0'}\n"
    # Counted after every dataset's localities resolve: a newly resolved locality is not progress.
    assert events == ["resolve", "resolve", "count", "count", "run", "count", "count"]


def _chain_step() -> dict[str, Any]:
    (job,) = yaml.safe_load(WORKFLOW.read_text())["jobs"].values()
    (step,) = [s for s in job["steps"] if s.get("name", "").startswith("Chain")]
    return step


def test_the_workflow_chains_on_its_own_token_and_keeps_run_now() -> None:
    doc = yaml.safe_load(WORKFLOW.read_text())
    (job,) = doc["jobs"].values()
    assert job["permissions"] == {"contents": "read", "actions": "write"}
    step = _chain_step()
    assert "steps.ingest.outputs.chain == '1'" in step["if"]
    assert step["env"]["GH_TOKEN"] == "${{ github.token }}"
    assert "${{" not in step["run"], "inputs reach the script as env vars, never as shell text"
    # api/price_stats.run_dataset_now ("Run now") dispatches mode + dataset_id.
    inputs = doc[True]["workflow_dispatch"]["inputs"]
    assert {"mode", "dataset_id"} <= set(inputs) and "full" in inputs["mode"]["options"]
    assert doc[True]["schedule"] == [{"cron": "0 4 * * 1"}], "one scheduled run a week"


def _run_chain_step(tmp_path: pathlib.Path, waiting_id: str) -> tuple[subprocess.CompletedProcess[str], str]:
    """Execute the step's exact script with stub `gh` (lists runs) and `curl` (records the dispatch)."""
    bin_dir, log = tmp_path / "bin", tmp_path / "curl.log"
    bin_dir.mkdir()
    (bin_dir / "gh").write_text(f"#!/bin/sh\necho '{waiting_id}'\n")
    (bin_dir / "curl").write_text(f"#!/bin/sh\necho \"$@\" >> {log}\nprintf 204\n")
    for stub in bin_dir.iterdir():
        stub.chmod(0o755)
    step = _chain_step()
    env = {"PATH": f"{bin_dir}:{os.environ['PATH']}", "GH_TOKEN": "t", "GH_REPO": "o/r", "REF": "main",
           "MODE": "", "DATASET_ID": "9", "START_YEAR": "", "WINDOW_YEARS": ""}
    done = subprocess.run(["bash", "-e", "-c", step["run"]], env=env, capture_output=True, text=True,
                          cwd=tmp_path)
    return done, log.read_text() if log.exists() else ""


def test_the_chain_yields_to_a_run_already_waiting(tmp_path: pathlib.Path) -> None:
    # One pending slot per concurrency group: a dispatch now would cancel the waiting run (a Run now click).
    done, dispatched = _run_chain_step(tmp_path, waiting_id="4242")
    assert done.returncode == 0, done.stderr
    assert "yields" in done.stdout and dispatched == ""


def test_the_chain_dispatches_the_same_inputs_when_nothing_waits(tmp_path: pathlib.Path) -> None:
    done, dispatched = _run_chain_step(tmp_path, waiting_id="")
    assert done.returncode == 0, done.stderr
    assert "repos/o/r/actions/workflows/scrape_price_stats.yml/dispatches" in dispatched
    assert '"ref":"main"' in dispatched and '"mode":"full"' in dispatched and '"dataset_id":"9"' in dispatched
