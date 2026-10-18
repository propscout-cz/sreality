"""scripts.verify_outbound_urls — the `outbound_url_conformance` check: the rotating sample,
the conformance rule, the concentration threshold and the check body (offline: a fake
connection draws the samples and a fake session answers the HEAD requests)."""

from __future__ import annotations

import ast
import datetime as _dt
import inspect
from typing import Any

import pytest
import requests

from scraper import sreality_url
from scripts import verify_outbound_urls as mod
from scripts.verify_pipeline import DEFAULT_THRESHOLDS

T = DEFAULT_THRESHOLDS
CANON = "https://www.sreality.cz/detail/prodej/dum/rodinny/praha-michle-pod-sychrovem-i/1915215948"


def _s(**kw: Any) -> mod.Sample:
    base = dict(id=1, source="sreality", category_main="dum", category_sub_cb=37, source_url=CANON)
    base.update(kw)
    return mod.Sample(**base)


class _SampleConn:
    """Answers the two sample queries: `per_cb` sreality ads per code, one ad per crawler."""

    def __init__(self, per_cb: int = 0, crawlers: bool = False) -> None:
        self.per_cb, self.crawlers = per_cb, crawlers
        self.executed: list[tuple[str, Any]] = []
        self._rows: list[tuple[Any, ...]] = []

    def cursor(self) -> "_SampleConn":
        return self

    def __enter__(self) -> "_SampleConn":
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def execute(self, sql: str, params: Any = None) -> None:
        self.executed.append((sql, params))
        if "category_sub_cb = %(cb)s" in sql:
            cb = params["cb"]
            self._rows = [(1000 * cb + i, "sreality", "byt", cb,
                           f"https://www.sreality.cz/detail/prodej/byt/x/{1000 * cb + i}")
                          for i in range(self.per_cb)]
        else:
            source = params["source"]
            self._rows = ([(9, source, "byt", None, f"https://{source}.example/9")]
                          if self.crawlers else [])

    def fetchall(self) -> Any:
        return self._rows


def _this_week() -> int:
    return _dt.datetime.now(_dt.timezone.utc).isocalendar().week


class _Clock:
    """The module's `time`, advanced by the fake portal and the probe's own sleeps."""

    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def _slow_portal(clock: _Clock, timeouts: list[float], answer_s: float = 20.0,
                 fast: str | None = None) -> type:
    """A portal that answers every HEAD 200 after `answer_s` (20 s: 44 URLs need ~15 minutes),
    or after 1 s for a URL containing `fast`; a request given less times out when its timeout
    runs out."""

    class _Resp:
        status_code, headers = 200, {}

    class _Session:
        def head(self, url: str, *, headers: Any, allow_redirects: bool,
                 timeout: float) -> _Resp:
            timeouts.append(timeout)
            needs = 1.0 if fast and fast in url else answer_s
            clock.now += min(timeout, needs)
            if timeout < needs:
                raise requests.ReadTimeout(f"{url}: read timed out")
            return _Resp()

    return _Session


# --- the sample ------------------------------------------------------------------


def test_the_rotation_covers_the_whole_codebook_every_four_weeks() -> None:
    seen: set[int] = set()
    for week in range(1, 5):
        seen |= set(mod.codes_for_week(week))
    assert seen == set(sreality_url.SUB_SLUG)
    assert 10 <= len(mod.codes_for_week(37)) <= 14
    assert mod.codes_for_week(37) == mod.codes_for_week(41)   # same quarter, four weeks on


def test_the_sample_is_three_ads_per_code_of_the_week_plus_one_per_crawler() -> None:
    conn = _SampleConn(per_cb=3, crawlers=True)
    samples = mod.draw_samples(conn, 37)
    codes = mod.codes_for_week(37)
    sreality = [p for sql, p in conn.executed if "category_sub_cb = %(cb)s" in sql]
    assert sreality == [{"cb": cb, "n": mod.ROWS_PER_CB} for cb in codes]
    crawlers = [p["source"] for sql, p in conn.executed if "%(source)s" in sql]
    assert crawlers == list(mod.CRAWLER_SOURCES)
    assert len(samples) == 3 * len(codes) + len(mod.CRAWLER_SOURCES)
    assert all(isinstance(s, mod.Sample) and s.source_url for s in samples)


def _ids(samples: list[mod.Sample]) -> list[tuple[str, int]]:
    return [(s.source, s.id) for s in samples]


def test_the_draw_is_shuffled_by_its_week_so_no_portal_queues_last() -> None:
    """In draw order the eight crawler portals queued behind every sreality code."""
    week37 = mod.draw_samples(_SampleConn(per_cb=3, crawlers=True), 37)
    assert _ids(week37) == _ids(mod.draw_samples(_SampleConn(per_cb=3, crawlers=True), 37))
    week41 = mod.draw_samples(_SampleConn(per_cb=3, crawlers=True), 41)  # the same quarter
    assert sorted(_ids(week41)) == sorted(_ids(week37)) and _ids(week41) != _ids(week37)
    first_half = week37[: len(week37) // 2]
    assert {s.source for s in first_half} - {"sreality"}, "a crawler portal is asked early"


def test_a_slow_sreality_no_longer_spends_the_budget_before_any_crawler_is_asked(
        monkeypatch: Any) -> None:
    clock, timeouts = _Clock(), []
    monkeypatch.setattr(mod, "time", clock)
    monkeypatch.setattr(mod.requests, "Session",
                        _slow_portal(clock, timeouts, fast=".example/"))
    # Week 37's order (fixed by its seed) asks a crawler third; in draw order, after 36 URLs.
    probed = mod.probe_all(mod.draw_samples(_SampleConn(per_cb=3, crawlers=True), 37),
                           mod.PROBE_BUDGET_S)
    assert [s.source for s, _ in probed if s.source != "sreality"]


# --- the conformance rule --------------------------------------------------------


def test_conformance_for_sreality_accepts_a_locality_only_redirect() -> None:
    assert mod.conforms("sreality", 200, None, CANON)
    drifted = CANON.replace("praha-michle-pod-sychrovem-i", "praha-michle-")
    assert mod.conforms("sreality", 301, drifted, CANON)
    assert not mod.conforms("sreality", 301, CANON.replace("rodinny", "vila"), CANON)
    assert not mod.conforms("sreality", 404, None, CANON)
    assert not mod.conforms("sreality", 302, "https://login.seznam.cz/x", CANON)


def test_conformance_for_a_crawler_accepts_its_own_same_host_canonicalisation() -> None:
    stored = "https://www.remax-czech.cz/reality/detail/440872/old-slug"
    assert mod.conforms("remax", 301, "https://www.remax-czech.cz/reality/detail/440872/new-slug", stored)
    assert not mod.conforms("remax", 301, "https://elsewhere.example/x", stored)
    assert not mod.conforms("remax", 410, None, stored)


def test_the_probe_heads_each_url_without_following_a_redirect(monkeypatch: Any) -> None:
    """HEAD only, no redirect followed; a network error is a non-conforming sample, never
    an exception that would end the check."""
    calls: list[tuple[str, bool]] = []
    timeouts: list[float] = []

    class _Resp:
        def __init__(self, status: int) -> None:
            self.status_code, self.headers = status, {}

    class _Session:
        def head(self, url: str, *, headers: Any, allow_redirects: bool,
                 timeout: float) -> _Resp:
            calls.append((url, allow_redirects))
            timeouts.append(timeout)
            if url.endswith("/down"):
                raise requests.ConnectionError("down")
            return _Resp(404 if url.endswith("/gone") else 200)

    monkeypatch.setattr(mod.requests, "Session", _Session)
    monkeypatch.setattr(mod, "_PROBE_INTERVAL_S", 0.0)
    base = "https://www.remax-czech.cz/reality/detail/1"
    samples = [_s(id=i, source="remax", source_url=f"{base}/{tail}")
               for i, tail in enumerate(("ok", "gone", "down"))]
    assert [ok for _, ok in mod.probe_all(samples, 60.0)] == [True, False, False]
    assert [follow for _, follow in calls] == [False, False, False]
    assert timeouts == [20.0, 20.0, 20.0]  # a budget with room left gives each the full cap


def test_the_probe_stops_at_its_deadline_and_leaves_the_rest_unprobed(
        monkeypatch: Any) -> None:
    """A portal answering each HEAD in 20 s needs 15 minutes for 44 URLs, and the probe used
    to wait all of it (20 s per request, no deadline): seven times its 120 s budget. It now
    stops at the deadline; the request the deadline cut short judged nothing and joins the
    unprobed rest instead of counting as a URL that failed."""
    clock, timeouts = _Clock(), []
    monkeypatch.setattr(mod, "time", clock)
    monkeypatch.setattr(mod.requests, "Session", _slow_portal(clock, timeouts))
    samples = [_s(id=i) for i in range(44)]
    probed = mod.probe_all(samples, 120.0)
    assert [(s.id, ok) for s, ok in probed] == [(i, True) for i in range(5)]
    assert clock.now <= 120.0
    assert timeouts[:5] == [20.0] * 5
    assert timeouts[5:] == [17.5]  # min(20 s, what was left); then nothing more was sent


# --- the concentration threshold -----------------------------------------------------


def test_a_whole_cell_failing_is_a_slug_defect_and_fails() -> None:
    samples = [(_s(id=i), False) for i in range(3)] + [(_s(id=i, category_sub_cb=39), True) for i in range(3, 6)]
    r = mod.classify(samples, T)
    assert r["status"] == "fail" and r["check_key"] == mod.CHECK_KEY
    assert r["details"]["failed_cells"] == [
        {"source": "sreality", "category_main": "dum", "category_sub_cb": 37, "sampled": 3, "failed": 3}
    ]
    # `cells` is the health ladder's per-cell verdict key; the sample strata live elsewhere
    assert "cells" not in r["details"] and len(r["details"]["sampled_cells"]) == 2


def test_scattered_failures_warn_by_share_and_never_fail() -> None:
    # one 404 in each of four cells of three: 33% per cell (< 67%), 33% overall (> 5%)
    samples = []
    for i, cb in enumerate((2, 3, 4, 5)):
        samples += [(_s(id=10 * i, category_main="byt", category_sub_cb=cb), False),
                    (_s(id=10 * i + 1, category_main="byt", category_sub_cb=cb), True),
                    (_s(id=10 * i + 2, category_main="byt", category_sub_cb=cb), True)]
    r = mod.classify(samples, T)
    assert r["status"] == "warn" and r["details"]["failed_cells"] == []
    assert r["value"] == 33.33


def test_a_single_bad_row_in_a_small_cell_is_not_a_cell_failure() -> None:
    r = mod.classify([(_s(), False), (_s(id=2, category_sub_cb=39), True)] + [(_s(id=i, category_sub_cb=33), True) for i in range(3, 40)], T)
    assert r["status"] == "ok"   # 1/39 = 2.6% < 5%, and the failing cell has < cell_min samples


def test_all_clean_is_ok_with_the_share_as_value() -> None:
    r = mod.classify([(_s(id=i), True) for i in range(5)], T)
    assert r["status"] == "ok" and r["value"] == 0.0 and r["details"]["sampled"] == 5


# --- the check ---------------------------------------------------------------------


def test_the_check_probes_this_iso_weeks_sample_and_judges_it(monkeypatch: Any) -> None:
    probed: list[mod.Sample] = []
    budgets: list[float] = []

    def _probe(samples: list[mod.Sample], budget_s: float) -> list[tuple[mod.Sample, bool]]:
        probed.extend(samples)
        budgets.append(budget_s)
        return [(s, True) for s in samples]

    monkeypatch.setattr(mod, "probe_all", _probe)
    before = _this_week()
    out = mod.check_outbound_url_conformance(_SampleConn(per_cb=3, crawlers=True), T)
    week = out["details"]["week"]
    assert week in {before, _this_week()}
    assert out["details"]["codes"] == mod.codes_for_week(week)
    assert out["check_key"] == mod.CHECK_KEY and out["status"] == "ok" and out["value"] == 0.0
    assert out["details"]["sampled"] == len(probed) == 3 * len(mod.codes_for_week(week)) + 8
    # the sampling queries spend from the same budget the HEAD loop is handed
    (budget,) = budgets
    assert 0 < budget <= mod.PROBE_BUDGET_S
    assert out["details"]["unprobed"] == 0
    assert out["details"]["budget_seconds"] == mod.PROBE_BUDGET_S
    assert "not probed" not in out["message"]


def test_the_check_judges_what_its_budget_reached_once_that_is_half_the_draw(
        monkeypatch: Any) -> None:
    clock, timeouts = _Clock(), []
    monkeypatch.setattr(mod, "time", clock)
    monkeypatch.setattr(mod.requests, "Session", _slow_portal(clock, timeouts, answer_s=3.0))
    out = mod.check_outbound_url_conformance(_SampleConn(per_cb=3, crawlers=True), T)
    drawn = 3 * len(out["details"]["codes"]) + len(mod.CRAWLER_SOURCES)
    assert clock.now <= mod.PROBE_BUDGET_S
    # 3 s a URL plus the 0.5 s pacing: 34 fit in 120 s, past half of the draw.
    assert out["status"] == "ok" and out["details"]["sampled"] == 34
    assert out["details"]["unprobed"] == drawn - 34
    assert out["details"]["budget_seconds"] == 120.0
    assert f"{drawn - 34} of {drawn} sampled URLs were not probed" in out["message"]


def test_a_check_that_probed_under_half_its_draw_is_not_measured(monkeypatch: Any) -> None:
    """Five of 44 URLs once judged the week OK: a verdict over a sliver of the draw cannot see
    a slug defect in the cells the budget never reached."""
    clock, timeouts = _Clock(), []
    monkeypatch.setattr(mod, "time", clock)
    monkeypatch.setattr(mod.requests, "Session", _slow_portal(clock, timeouts))
    out = mod.check_outbound_url_conformance(_SampleConn(per_cb=3, crawlers=True), T)
    drawn = 3 * len(out["details"]["codes"]) + len(mod.CRAWLER_SOURCES)
    assert drawn * 20.0 >= 14 * 60, "the whole draw would need about 15 minutes"
    assert clock.now <= mod.PROBE_BUDGET_S
    assert out["status"] == "unknown" and out["value"] is None
    assert out["details"]["skipped"] == (
        f"probed 5 of {drawn} sampled URLs before the 120 s budget ran out")
    assert out["details"]["unprobed"] == drawn - 5
    assert "Not measured" in out["message"]


@pytest.mark.parametrize(("short", "status"), [(0, "ok"), (1, "unknown")])
def test_half_the_draw_is_the_least_a_verdict_needs(
        monkeypatch: Any, short: int, status: str) -> None:
    def _half(samples: list[mod.Sample], budget_s: float) -> list[tuple[mod.Sample, bool]]:
        return [(s, True) for s in samples[: (len(samples) + 1) // 2 - short]]

    monkeypatch.setattr(mod, "probe_all", _half)
    out = mod.check_outbound_url_conformance(_SampleConn(per_cb=3, crawlers=True), T)
    assert out["status"] == status


def test_a_check_whose_budget_is_spent_before_any_probe_is_not_measured(
        monkeypatch: Any) -> None:
    """Zero URLs probed is not an ok over zero samples: nothing was verified."""

    class _NoSession:
        def head(self, *a: Any, **kw: Any) -> Any:
            raise AssertionError("the budget is spent, so nothing may be sent")

    monkeypatch.setattr(mod.requests, "Session", _NoSession)
    out = mod.check_outbound_url_conformance(_SampleConn(per_cb=3, crawlers=True), T,
                                             budget_s=0.0)
    drawn = 3 * len(out["details"]["codes"]) + len(mod.CRAWLER_SOURCES)
    assert out["status"] == "unknown" and out["value"] is None
    assert out["details"]["skipped"].startswith(f"probed 0 of {drawn} sampled URLs")
    assert out["details"]["unprobed"] == drawn
    assert "Not measured" in out["message"]


def test_the_check_is_unknown_when_no_sample_was_drawn(monkeypatch: Any) -> None:
    """Nothing to probe is Not measured, never an ok over zero samples."""

    def _never(samples: list[mod.Sample], budget_s: float) -> list[tuple[mod.Sample, bool]]:
        raise AssertionError("nothing was drawn, so nothing may be probed")

    monkeypatch.setattr(mod, "probe_all", _never)
    before = _this_week()
    out = mod.check_outbound_url_conformance(_SampleConn(), T)
    assert out["status"] == "unknown" and out["value"] is None
    assert "nothing was verified" in out["details"]["skipped"]
    assert out["details"]["week"] in {before, _this_week()}
    assert "verified NOTHING" in out["message"]


def test_the_check_is_registered_last_weekly_with_the_outbound_budget() -> None:
    from scripts.verify_pipeline import CHECKS

    last = CHECKS[-1]
    assert (last.key, last.fn) == (mod.CHECK_KEY, mod.check_outbound_url_conformance)
    assert (last.cls, last.cadence_s, last.budget_s) == ("alarm", 7 * 24 * 3600, 120.0)
    # the budget the harness accounts for is the one the HEAD loop stops at
    assert last.budget_s == mod.PROBE_BUDGET_S


def test_the_probe_module_never_imports_the_harness_back() -> None:
    """verify_pipeline imports this module to register the check; this module importing
    the harness back (it once borrowed insert_result and load_thresholds) is a cycle, and
    telling is the API's ladder, never a check's."""
    tree = ast.parse(inspect.getsource(mod))
    imported = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    imported |= {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert not imported & {"scripts.verify_pipeline", "toolkit.system_alerts", "scraper.db"}
    assert not hasattr(mod, "main")
