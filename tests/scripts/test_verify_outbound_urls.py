"""scripts.verify_outbound_urls — the `outbound_url_conformance` check: the rotating sample,
the conformance rule, the concentration threshold and the check body (offline: a fake
connection draws the samples and a fake session answers the HEAD requests)."""

from __future__ import annotations

import ast
import datetime as _dt
import inspect
from typing import Any

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

    class _Resp:
        def __init__(self, status: int) -> None:
            self.status_code, self.headers = status, {}

    class _Session:
        def head(self, url: str, *, headers: Any, allow_redirects: bool,
                 timeout: float) -> _Resp:
            calls.append((url, allow_redirects))
            if url.endswith("/down"):
                raise requests.ConnectionError("down")
            return _Resp(404 if url.endswith("/gone") else 200)

    monkeypatch.setattr(mod.requests, "Session", _Session)
    monkeypatch.setattr(mod, "_PROBE_INTERVAL_S", 0.0)
    base = "https://www.remax-czech.cz/reality/detail/1"
    samples = [_s(id=i, source="remax", source_url=f"{base}/{tail}")
               for i, tail in enumerate(("ok", "gone", "down"))]
    assert [ok for _, ok in mod.probe_all(samples)] == [True, False, False]
    assert [follow for _, follow in calls] == [False, False, False]


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

    def _probe(samples: list[mod.Sample]) -> list[tuple[mod.Sample, bool]]:
        probed.extend(samples)
        return [(s, True) for s in samples]

    monkeypatch.setattr(mod, "probe_all", _probe)
    before = _this_week()
    out = mod.check_outbound_url_conformance(_SampleConn(per_cb=3, crawlers=True), T)
    week = out["details"]["week"]
    assert week in {before, _this_week()}
    assert out["details"]["codes"] == mod.codes_for_week(week)
    assert out["check_key"] == mod.CHECK_KEY and out["status"] == "ok" and out["value"] == 0.0
    assert out["details"]["sampled"] == len(probed) == 3 * len(mod.codes_for_week(week)) + 8


def test_the_check_is_unknown_when_no_sample_was_drawn(monkeypatch: Any) -> None:
    """Nothing to probe is Not measured, never an ok over zero samples."""

    def _never(samples: list[mod.Sample]) -> list[tuple[mod.Sample, bool]]:
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


def test_the_probe_module_never_imports_the_harness_back() -> None:
    """verify_pipeline imports this module to register the check; this module importing
    the harness back (it once borrowed insert_result and load_thresholds) is a cycle, and
    telling is the API's ladder, never a check's."""
    tree = ast.parse(inspect.getsource(mod))
    imported = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    imported |= {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert not imported & {"scripts.verify_pipeline", "toolkit.system_alerts", "scraper.db"}
    assert not hasattr(mod, "main")
