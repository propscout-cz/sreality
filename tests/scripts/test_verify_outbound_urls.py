"""scripts.verify_outbound_urls — the `outbound_url_conformance` check: the rotating sample,
the conformance rule, one cell per portal and the `sample` cell, and the check body (offline: a
fake connection draws the samples and a fake session answers the HEAD requests)."""

from __future__ import annotations

import ast
import contextlib
import datetime as _dt
import inspect
import re
from typing import Any

import pytest
import requests

from location_data.claims_common import SOURCES
from scraper import sreality_url
from scripts import verify_outbound_urls as mod

# The keys the probe reads, with the values scripts/verify_pipeline.THRESHOLDS declares (pinned below).
T: dict[str, Any] = {"outbound_url_conformance_warn_share": 0.05,
                     "outbound_url_conformance_cell_min": 3,
                     "outbound_url_conformance_cell_fail_share": 0.67}
CANON = "https://www.sreality.cz/detail/prodej/dum/rodinny/praha-michle-pod-sychrovem-i/1915215948"
SUBJECTS = [*SOURCES, mod.SAMPLE_SUBJECT]


def _s(**kw: Any) -> mod.Sample:
    base = dict(id=1, source="sreality", category_main="dum", category_sub_cb=37, source_url=CANON)
    base.update(kw)
    return mod.Sample(**base)


def _cells(result: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {c["subject"]: c for c in result["details"]["cells"]}


class _SampleConn:
    """Answers the two sample queries: `per_cb` sreality ads per code, one ad per crawler."""

    def __init__(self, per_cb: int = 0, crawlers: bool = False) -> None:
        self.per_cb, self.crawlers = per_cb, crawlers
        self.executed: list[tuple[str, Any]] = []
        self._rows: list[tuple[Any, ...]] = []

    @contextlib.contextmanager
    def transaction(self) -> Any:
        yield

    def cursor(self) -> "_SampleConn":
        return self

    def __enter__(self) -> "_SampleConn":
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def execute(self, sql: str, params: Any = None) -> None:
        if sql.startswith("SET LOCAL statement_timeout"):
            return
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


class _Answer:
    """A HEAD's response as the egress reads it (a context manager, no body)."""

    def __init__(self, status: int, headers: dict[str, str] | None = None) -> None:
        self.status_code, self.headers = status, headers or {}

    def __enter__(self) -> "_Answer":
        return self

    def __exit__(self, *exc: Any) -> None:
        return None


class _HeadSession:
    """A fake session the probe drives through `scraper.egress.fetch`: its one request per
    URL is a non-following, streamed HEAD, answered by the subclass's `head` with the
    request's read timeout."""

    def request(self, method: str, url: str, *, headers: Any, timeout: tuple[float, float],
                stream: bool, allow_redirects: bool) -> Any:
        assert (method, stream) == ("HEAD", True)
        return self.head(url, headers=headers, allow_redirects=allow_redirects, timeout=timeout[1])

    def head(self, url: str, *, headers: Any, allow_redirects: bool, timeout: float) -> Any:
        raise NotImplementedError


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

    class _Session(_HeadSession):
        def head(self, url: str, *, headers: Any, allow_redirects: bool,
                 timeout: float) -> _Answer:
            timeout = round(timeout, 2)  # the egress hands what is left of ITS deadline
            timeouts.append(timeout)
            needs = 1.0 if fast and fast in url else answer_s
            clock.now += min(timeout, needs)
            if timeout < needs:
                raise requests.ReadTimeout(f"{url}: read timed out")
            return _Answer(200)

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


def test_every_portal_that_gets_a_cell_is_drawn() -> None:
    """The crawler list is the fleet's minus sreality, so no portal's cell reads 'no draw' for good."""
    assert ("sreality", *mod.CRAWLER_SOURCES) == SOURCES


def test_the_draw_runs_on_the_check_clock_like_every_other_statement() -> None:
    """Wave 2: the sample's reads were raw-cursor statements outside the per-check budget."""
    from tests.scripts.test_verify_pipeline import _BoundConn

    conn = _BoundConn()
    assert mod.draw_samples(conn, 37) == []
    assert len(conn.sent) == len(mod.codes_for_week(37)) + len(mod.CRAWLER_SOURCES)
    assert all(armed for _, armed in conn.sent)


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
    monkeypatch.setattr(mod.egress, "safe_session",
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


def test_a_relative_redirect_is_read_against_the_url_it_answered() -> None:
    """10-18: two of the six non-conforming links were sreality 301s to a bare path that kept the
    page, read as broken because a path has no host to match."""
    assert mod.conforms("sreality", 301, "/detail/prodej/dum/rodinny/praha-michle-/1915215948", CANON)
    assert not mod.conforms("sreality", 301, "/detail/prodej/dum/vila/praha-michle-/1915215948", CANON)
    stored = "https://www.remax-czech.cz/reality/detail/440872/old-slug"
    assert mod.conforms("remax", 301, "/reality/detail/440872/new-slug", stored)
    assert mod.conforms("remax", 308, "//www.remax-czech.cz/reality/detail/440872/new-slug", stored)


def test_a_malformed_stored_sreality_link_never_conforms_by_redirect() -> None:
    """Two links with no readable segments once compared equal, so any redirect read as conforming."""
    assert not mod.conforms("sreality", 302, "https://login.seznam.cz/x", "https://www.sreality.cz/x")


@pytest.mark.parametrize(("status", "location", "said"), [
    (200, None, "opens"), (301, "/detail/prodej/dum/rodinny/praha-michle-/1915215948", "opens"),
    (404, None, "broken"), (410, None, "broken"), (302, "https://login.seznam.cz/x", "broken"),
    (403, None, "refused"), (405, None, "refused")])
def test_a_bot_wall_or_a_server_without_head_refuses_and_judges_nothing(
        status: int, location: str | None, said: str) -> None:
    assert mod.outcome("sreality", status, location, CANON) == said


def test_the_probe_heads_each_url_without_following_a_redirect(monkeypatch: Any) -> None:
    """HEAD only, no redirect followed; a network error is a non-conforming sample, never
    an exception that would end the check."""
    calls: list[tuple[str, bool]] = []
    timeouts: list[float] = []

    class _Session(_HeadSession):
        def head(self, url: str, *, headers: Any, allow_redirects: bool,
                 timeout: float) -> _Answer:
            calls.append((url, allow_redirects))
            timeouts.append(round(timeout, 2))
            if url.endswith("/down"):
                raise requests.ConnectionError("down")
            return _Answer({"gone": 404, "walled": 403}.get(url.rsplit("/", 1)[1], 200))

    monkeypatch.setattr(mod.egress, "safe_session", _Session)
    monkeypatch.setattr(mod, "_PROBE_INTERVAL_S", 0.0)
    base = "https://www.remax-czech.cz/reality/detail/1"
    samples = [_s(id=i, source="remax", source_url=f"{base}/{tail}")
               for i, tail in enumerate(("ok", "gone", "down", "walled"))]
    assert [said for _, said in mod.probe_all(samples, 60.0)] == [
        mod.OPENS, mod.BROKEN, mod.BROKEN, mod.REFUSED]
    assert [follow for _, follow in calls] == [False, False, False, False]
    assert timeouts == [20.0] * 4  # a budget with room left gives each the full cap


def test_the_probe_stops_at_its_deadline_and_leaves_the_rest_unprobed(
        monkeypatch: Any) -> None:
    """A portal answering each HEAD in 20 s needs 15 minutes for 44 URLs, and the probe used
    to wait all of it (20 s per request, no deadline): seven times its 120 s budget. It now
    stops at the deadline; the request the deadline cut short judged nothing and joins the
    unprobed rest instead of counting as a URL that failed."""
    clock, timeouts = _Clock(), []
    monkeypatch.setattr(mod, "time", clock)
    monkeypatch.setattr(mod.egress, "safe_session", _slow_portal(clock, timeouts))
    samples = [_s(id=i) for i in range(44)]
    probed = mod.probe_all(samples, 120.0)
    assert [(s.id, said) for s, said in probed] == [(i, mod.OPENS) for i in range(5)]
    assert clock.now <= 120.0
    assert timeouts[:5] == [20.0] * 5
    assert timeouts[5:] == [17.5]  # min(20 s, what was left); then nothing more was sent


# --- one cell per portal and the `sample` cell -----------------------------------------------


def _crawlers(said: str = mod.OPENS, *, fleet: tuple[str, ...] | None = None,
              **by_portal: str) -> list[tuple[mod.Sample, str]]:
    """One answered link of each crawler portal, as a week's draw holds them."""
    return [(_s(id=900 + i, source=p, category_main="byt", category_sub_cb=None,
                source_url=f"https://{p}.example/{i}"), by_portal.get(p, said))
            for i, p in enumerate(fleet if fleet is not None else mod.CRAWLER_SOURCES)]


def test_a_sub_category_failing_together_is_a_slug_defect_and_a_problem_on_its_portal() -> None:
    samples = ([(_s(id=i), mod.BROKEN) for i in range(3)]
               + [(_s(id=i, category_sub_cb=39), mod.OPENS) for i in range(3, 6)] + _crawlers())
    r = mod.classify(samples, T)
    assert r["status"] == "fail" and r["check_key"] == mod.CHECK_KEY and r["value"] == 1
    cells = _cells(r)
    assert list(cells) == SUBJECTS, "every portal and the sample, in every state"
    sreality = cells["sreality"]
    assert sreality["status"] == "fail" and sreality["portal"] == "sreality"
    assert "dum/37 3/3" in sreality["message"] and "slug defect" in sreality["message"]
    assert sreality["facts"]["strata"] == [
        {"category_main": "dum", "category_sub_cb": 37, "sampled": 3, "broken": 3},
        {"category_main": "dum", "category_sub_cb": 39, "sampled": 3, "broken": 0}]
    assert {cells[p]["status"] for p in mod.CRAWLER_SOURCES} == {"ok"}
    # 3 of 14 broken: a lower bound of 7.6 %, so the whole sample is Watch beside the Problem
    assert cells["sample"]["status"] == "warn"


def test_the_concentration_line_is_strict_and_its_sample_floor_inclusive() -> None:
    """The fold boundary: the retired rule failed a sub-category AT 67 %, common.graded's strict `>`
    does not; three links of one sub-category, all broken, are still a Problem, two are not."""
    at_line = [(_s(id=i), mod.BROKEN if i < 67 else mod.OPENS) for i in range(100)]
    assert _cells(mod.classify(at_line, T))["sreality"]["status"] == "ok"
    over = [(_s(id=i), mod.BROKEN if i < 68 else mod.OPENS) for i in range(100)]
    assert _cells(mod.classify(over, T))["sreality"]["status"] == "fail"
    floor = [(_s(id=i), mod.BROKEN) for i in range(3)]
    assert _cells(mod.classify(floor, T))["sreality"]["status"] == "fail"
    assert _cells(mod.classify(floor[:2], T))["sreality"]["status"] == "ok"


def test_scattered_failures_are_watch_on_the_sample_never_a_problem() -> None:
    # one 404 in each of four sub-categories of three: 33 % each (under 67 %); 4 of 12 overall,
    # a lower bound of 13.8 % (over 5 %)
    samples = []
    for i, cb in enumerate((2, 3, 4, 5)):
        samples += [(_s(id=10 * i, category_main="byt", category_sub_cb=cb), mod.BROKEN),
                    (_s(id=10 * i + 1, category_main="byt", category_sub_cb=cb), mod.OPENS),
                    (_s(id=10 * i + 2, category_main="byt", category_sub_cb=cb), mod.OPENS)]
    r = mod.classify(samples, T)
    cells = _cells(r)
    assert r["status"] == "warn" and r["value"] == 1
    assert cells["sreality"]["status"] == "ok"
    assert "4 did not, scattered" in cells["sreality"]["message"]
    assert cells["sample"]["status"] == "warn" and cells["sample"]["value"] == 33.33
    assert cells["sample"]["facts"]["lower_bound"] == pytest.approx(0.138, abs=0.001)


def test_the_design_figure_reads_ok_on_the_lower_bound_where_the_raw_share_watched() -> None:
    """4 of 44 is 9.1 %, over the 5 % line; its lower bound is 3.6 %."""
    samples = [(_s(id=i, category_sub_cb=i % 12), mod.BROKEN if i < 4 else mod.OPENS)
               for i in range(44)]
    r = mod.classify(samples, T)
    sample = _cells(r)["sample"]
    assert r["status"] == "ok" and sample["status"] == "ok" and sample["value"] == 9.09
    assert sample["facts"]["lower_bound"] == pytest.approx(0.036, abs=0.001)


def test_the_10_18_run_reads_ok_once_the_probe_stops_misreading_its_answers() -> None:
    """10-18 20:24Z: 6 of 44 links non-conforming, a lower bound of 6.4 %: Watch. Two were sreality
    301s to a bare path that kept the page, two were bot walls (mmreality and ceskereality 403);
    the two 404s left are scattered delistings."""

    def week(fixed: bool) -> list[tuple[mod.Sample, str]]:
        links = [(_s(id=i, category_sub_cb=i // 3), mod.OPENS) for i in range(36)]
        for i in (0, 3):
            links[i] = (links[i][0], mod.BROKEN)
        for i in (6, 9):
            links[i] = (links[i][0], mod.OPENS if fixed else mod.BROKEN)
        walled = mod.REFUSED if fixed else mod.BROKEN
        # That run's eight crawler portals: `web` joined the fleet on 2026-10-21.
        then_fleet = tuple(p for p in mod.CRAWLER_SOURCES if p != "web")
        return links + _crawlers(fleet=then_fleet, mmreality=walled, ceskereality=walled)

    then = _cells(mod.classify(week(fixed=False), T))["sample"]
    assert then["status"] == "warn" and then["facts"]["lower_bound"] == pytest.approx(0.064, abs=0.001)
    now = mod.classify(week(fixed=True), T)
    cells = _cells(now)
    assert now["status"] == "ok"
    assert cells["sample"]["facts"]["answered"] == 42 and cells["sample"]["facts"]["broken"] == 2
    assert cells["sample"]["facts"]["lower_bound"] == pytest.approx(0.0132, abs=0.0005)
    for walled in ("mmreality", "ceskereality"):
        assert cells[walled]["status"] == "unknown" and cells[walled]["message"].startswith("Refused")


def test_a_refused_link_judges_nothing_and_the_sample_leaves_it_out() -> None:
    samples = [(_s(id=i, category_sub_cb=i), mod.OPENS) for i in range(12)]
    cells = _cells(mod.classify(samples + _crawlers(remax=mod.REFUSED), T))
    assert cells["remax"]["status"] == "unknown" and cells["remax"]["value"] is None
    assert "403 or 405" in cells["remax"]["message"]
    assert cells["sample"]["facts"]["answered"] == 12 + len(mod.CRAWLER_SOURCES) - 1
    assert cells["sample"]["facts"]["refused"] == 1
    mixed = samples + [(_s(id=99, category_sub_cb=0), mod.REFUSED)]
    assert "1 refused" in _cells(mod.classify(mixed, T))["sreality"]["message"]


def test_a_sample_of_nothing_but_refusals_is_not_measured() -> None:
    r = mod.classify(_crawlers(said=mod.REFUSED), T)
    assert r["status"] == "unknown" and r["value"] is None
    assert {c["status"] for c in r["details"]["cells"]} == {"unknown"}
    assert _cells(r)["sreality"]["message"].startswith("No draw")


def test_a_portal_drawn_but_never_reached_is_not_probed_while_the_rest_still_judge() -> None:
    drawn = [_s(id=i, category_sub_cb=i) for i in range(12)] + [s for s, _ in _crawlers()]
    probed = [(s, mod.OPENS) for s in drawn if s.source != "maxima"]
    r = mod.classify(probed, T, drawn=drawn, facts={"budget_seconds": 120.0})
    assert r["status"] == "ok"
    assert _cells(r)["maxima"]["status"] == "unknown"
    assert _cells(r)["maxima"]["message"].startswith("Not probed")
    drawn_n = 12 + len(mod.CRAWLER_SOURCES)
    assert r["details"]["not_measured"] == {
        "links": f"1 of {drawn_n} sampled links were not probed: the check's 120 s budget ran out"}


def test_a_portal_with_nothing_drawn_still_has_its_cell() -> None:
    r = mod.classify([(_s(id=i, category_sub_cb=i), mod.OPENS) for i in range(12)], T)
    cells = _cells(r)
    assert list(cells) == SUBJECTS and r["status"] == "ok"
    assert all(cells[p]["status"] == "unknown" and cells[p]["message"].startswith("No draw")
               for p in mod.CRAWLER_SOURCES)


def test_a_portal_outside_the_fleet_list_still_gets_a_cell() -> None:
    r = mod.classify([(_s(source="w6-portal", source_url="https://w6.example/1"), mod.OPENS)], T)
    assert list(_cells(r)) == [*SOURCES, "w6-portal", mod.SAMPLE_SUBJECT]


def test_all_clean_is_ok_with_every_measured_cell_ok() -> None:
    r = mod.classify([(_s(id=i), mod.OPENS) for i in range(5)] + _crawlers(), T)
    assert r["status"] == "ok" and r["value"] == 0
    assert {c["status"] for c in r["details"]["cells"]} == {"ok"}
    assert _cells(r)["sample"]["value"] == 0.0
    assert _cells(r)["sample"]["facts"]["answered"] == 5 + len(mod.CRAWLER_SOURCES)


def test_every_verdict_carries_a_cell_for_every_portal_and_the_sample() -> None:
    """Wave 6: a measured result without cells no longer reaches the ladder."""
    verdicts = {}
    for samples in ([(_s(id=i), mod.OPENS) for i in range(5)],
                    [(_s(id=i, category_sub_cb=i), mod.BROKEN if i < 4 else mod.OPENS)
                     for i in range(12)],
                    [(_s(id=i), mod.BROKEN) for i in range(3)]):
        r = mod.classify(samples, T)
        verdicts[r["status"]] = list(_cells(r))
    assert verdicts == {status: SUBJECTS for status in ("ok", "warn", "fail")}


# --- the check ---------------------------------------------------------------------


def test_the_check_probes_this_iso_weeks_sample_and_judges_it(monkeypatch: Any) -> None:
    probed: list[mod.Sample] = []
    budgets: list[float] = []

    def _probe(samples: list[mod.Sample], budget_s: float) -> list[tuple[mod.Sample, str]]:
        probed.extend(samples)
        budgets.append(budget_s)
        return [(s, mod.OPENS) for s in samples]

    monkeypatch.setattr(mod, "probe_all", _probe)
    before = _this_week()
    out = mod.check_outbound_url_conformance(_SampleConn(per_cb=3, crawlers=True), T)
    assert list(_cells(out)) == SUBJECTS
    run = _cells(out)["sample"]["facts"]
    week = run["week"]
    assert week in {before, _this_week()}
    assert run["codes"] == mod.codes_for_week(week)
    assert out["check_key"] == mod.CHECK_KEY and out["status"] == "ok" and out["value"] == 0
    assert run["drawn"] == run["probed"] == len(probed) == (
        3 * len(mod.codes_for_week(week)) + len(mod.CRAWLER_SOURCES))
    # the sampling queries spend from the same budget the HEAD loop is handed
    (budget,) = budgets
    assert 0 < budget <= mod.PROBE_BUDGET_S
    assert run["unprobed"] == 0
    assert run["budget_seconds"] == mod.PROBE_BUDGET_S
    assert "not probed" not in out["message"] and "not_measured" not in out["details"]


def test_the_check_judges_what_its_budget_reached_once_that_is_half_the_draw(
        monkeypatch: Any) -> None:
    clock, timeouts = _Clock(), []
    monkeypatch.setattr(mod, "time", clock)
    monkeypatch.setattr(mod.egress, "safe_session", _slow_portal(clock, timeouts, answer_s=3.0))
    out = mod.check_outbound_url_conformance(_SampleConn(per_cb=3, crawlers=True), T)
    run = _cells(out)["sample"]["facts"]
    drawn = 3 * len(run["codes"]) + len(mod.CRAWLER_SOURCES)
    assert clock.now <= mod.PROBE_BUDGET_S
    # 3 s a URL plus the 0.5 s pacing: 34 fit in 120 s, past half of the draw.
    assert out["status"] == "ok" and run["probed"] == 34
    assert run["unprobed"] == drawn - 34
    assert run["budget_seconds"] == 120.0
    assert f"{drawn - 34} of {drawn} sampled links were not probed" in out["message"]
    unreached = {c["subject"] for c in out["details"]["cells"]
                 if c["message"].startswith("Not probed")}
    assert unreached, "a portal the budget never reached reads Not probed, never OK"


def test_a_check_that_probed_under_half_its_draw_is_not_measured(monkeypatch: Any) -> None:
    """Five of 44 URLs once judged the week OK: a verdict over a sliver of the draw cannot see
    a slug defect in the cells the budget never reached."""
    clock, timeouts = _Clock(), []
    monkeypatch.setattr(mod, "time", clock)
    monkeypatch.setattr(mod.egress, "safe_session", _slow_portal(clock, timeouts))
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
    def _half(samples: list[mod.Sample], budget_s: float) -> list[tuple[mod.Sample, str]]:
        return [(s, mod.OPENS) for s in samples[: (len(samples) + 1) // 2 - short]]

    monkeypatch.setattr(mod, "probe_all", _half)
    out = mod.check_outbound_url_conformance(_SampleConn(per_cb=3, crawlers=True), T)
    assert out["status"] == status


def test_a_check_whose_budget_is_spent_before_any_probe_is_not_measured(
        monkeypatch: Any) -> None:
    """Zero URLs probed is not an ok over zero samples: nothing was verified."""

    class _NoSession(_HeadSession):
        def head(self, *a: Any, **kw: Any) -> Any:
            raise AssertionError("the budget is spent, so nothing may be sent")

    monkeypatch.setattr(mod.egress, "safe_session", _NoSession)
    out = mod.check_outbound_url_conformance(_SampleConn(per_cb=3, crawlers=True), T,
                                             budget_s=0.0)
    drawn = 3 * len(out["details"]["codes"]) + len(mod.CRAWLER_SOURCES)
    assert out["status"] == "unknown" and out["value"] is None
    assert out["details"]["skipped"].startswith(f"probed 0 of {drawn} sampled URLs")
    assert out["details"]["unprobed"] == drawn
    assert "Not measured" in out["message"]


def test_the_check_is_unknown_when_no_sample_was_drawn(monkeypatch: Any) -> None:
    """Nothing to probe is Not measured, never an ok over zero samples."""

    def _never(samples: list[mod.Sample], budget_s: float) -> list[tuple[mod.Sample, str]]:
        raise AssertionError("nothing was drawn, so nothing may be probed")

    monkeypatch.setattr(mod, "probe_all", _never)
    before = _this_week()
    out = mod.check_outbound_url_conformance(_SampleConn(), T)
    assert out["status"] == "unknown" and out["value"] is None
    assert "nothing was verified" in out["details"]["skipped"]
    assert out["details"]["week"] in {before, _this_week()}
    assert "verified NOTHING" in out["message"]


def test_the_check_is_registered_last_weekly_with_the_outbound_budget() -> None:
    """On GitHub, never the worker: the probe needs an address other than the scrapers'."""
    from scripts.verify_pipeline import CHECKS

    last = CHECKS[-1]
    assert (last.key, last.fn) == (mod.CHECK_KEY, mod.check_outbound_url_conformance)
    assert (last.cls, last.cadence_s, last.budget_s) == ("alarm", 7 * 24 * 3600, 120.0)
    assert last.host == "github-actions"
    # the budget the harness accounts for is the one the HEAD loop stops at
    assert last.budget_s == mod.PROBE_BUDGET_S


def test_the_probe_reads_exactly_the_thresholds_named_here() -> None:
    read = set(re.findall(r"""thresholds\[["'](\w+)["']\]""", inspect.getsource(mod)))
    assert read == set(T)


def test_the_harness_declares_the_probes_thresholds_with_these_values() -> None:
    from scripts.verify_pipeline import THRESHOLDS

    assert {key: THRESHOLDS.get(key) for key in T} == T


def test_the_probe_module_never_imports_the_harness_back() -> None:
    """verify_pipeline imports this module to register the check; this module importing
    the harness back (it once borrowed insert_result and load_thresholds) is a cycle, and
    telling is the API's ladder, never a check's."""
    tree = ast.parse(inspect.getsource(mod))
    imported = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    imported |= {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert not imported & {"scripts.verify_pipeline", "toolkit.system_alerts", "scraper.db"}
    assert not hasattr(mod, "main")


def test_the_probe_goes_out_on_the_egress_session_and_a_private_link_judges_broken(
        monkeypatch: Any) -> None:
    """A stored URL is page-supplied: the HEAD never reaches a private address, and one that
    resolves there is a link that does not open, not an exception that ends the check."""
    from scraper import egress

    assert isinstance(mod.egress.safe_session().get_adapter("https://rk.example/"),
                      egress._PinnedAdapter)

    class _Session(_HeadSession):
        def head(self, url: str, *, headers: Any, allow_redirects: bool, timeout: float) -> Any:
            raise egress.EgressRefused("refused address 10.0.0.7")

    monkeypatch.setattr(mod.egress, "safe_session", _Session)
    monkeypatch.setattr(mod, "_PROBE_INTERVAL_S", 0.0)
    probed = mod.probe_all([_s(id=1, source="remax", source_url="https://rk.example/1")], 60.0)
    assert [said for _, said in probed] == [mod.BROKEN]
