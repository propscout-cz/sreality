"""Hermetic tests for scripts.checks.scraping: the scraping alarm's arms and the coverage gauge."""

# No DB: the platform tests' fake connection answers each statement by the SQL constant it
# carries, through the per-check clock. The SQL itself ran read-only against production at build
# time (2026-10-19) and PREPAREs against CI's replayed schema.

from __future__ import annotations

import ast
import datetime as _dt
import inspect
import re
from typing import Any

import pytest

from location_data.claims_common import SOURCES
from scraper.db import QUEUE_PRIORITY_NEW
from scraper.portal_runner import DECLARED_TOTAL_KEY
from scripts.checks import common
from scripts.checks import scraping as sc
from scripts.checks.common import wilson_lower
from tests.scripts.test_checks_platform import _Conn
from tests.scripts.test_checks_platform_replay import _incidents, _told

_UTC = _dt.timezone.utc
_NOW = _dt.datetime(2026, 10, 19, 6, 0, tzinfo=_UTC)

# The scraping and coverage keys exactly as the wave-4 contract spells them (§ 2.12).
T: dict[str, Any] = {
    "scraping_walk_watch_gaps": 3, "scraping_walk_fail_gaps": 6,
    "scraping_walk_fallback_min_gaps": 10, "scraping_walk_stale_days": 14,
    "scraping_walk_recent_days": 30, "scraping_inflow_fail_share": 0.2,
    "scraping_inflow_warn_share": 0.5, "scraping_inflow_spike_share": 3.0,
    "scraping_inflow_min_baseline": 20, "scraping_fetch_err_warn": 0.02,
    "scraping_fetch_err_fail": 0.10, "scraping_fetch_err_min_ids": 200,
    "scraping_missing_warn": 0.02, "scraping_missing_fail": 0.05,
    "scraping_missing_min_declared": 500, "coverage_residual_warn": 0.02,
    "coverage_residual_fail": 0.05, "coverage_residual_min_declared": 500,
    "coverage_unnominated_warn": 25, "coverage_unnominated_fail": 100,
}


def _ago(**delta: float) -> _dt.datetime:
    return _NOW - _dt.timedelta(**delta)


def _unit(source: str = "bazos", main: str | None = "byt", kind: str | None = "prodej", *,
          end_h: float | None = 2.0, walked_h: float | None = None, gap_h: float | None = 7.2,
          gaps: int = 40, declared: int | None = 10_000, collected: int | None = 10_000,
          entries: int = 1, recorded: int | None = None, counted: bool = False,
          upper: bool = False, undeclared: int = 0, deferred: int = 0,
          all_reached_h: float | None = 2.0) -> tuple[Any, ...]:
    """A _WALKS_SQL row: one unit of one portal, its newest end-reached walk `end_h` hours ago."""
    end = None if end_h is None else _ago(hours=end_h)
    walked = _ago(hours=walked_h if walked_h is not None else (end_h or 1.0))
    return (source, walked, walked, None if gap_h is None else gap_h * 3600, gaps,
            None if all_reached_h is None else _ago(hours=all_reached_h), main, kind, walked, end,
            declared, undeclared, collected, entries, entries if recorded is None else recorded,
            counted, upper, deferred)


def _fleet(*rows: tuple[Any, ...]) -> list[tuple[Any, ...]]:
    """`rows`, and one healthy unit for every other portal, so a verdict reads `rows` alone."""
    named = {row[0] for row in rows}
    return [*rows, *(_unit(source) for source in sorted(SOURCES) if source not in named)]


def _bare(source: str, *, started_h: float = 1.0) -> tuple[Any, ...]:
    """A _WALKS_SQL row for a portal whose index runs in the window never finished."""
    return (source, _ago(hours=started_h), None, None, 0, None, None, None, None, None, None, 0,
            None, 0, 0, False, False, 0)


def _inflow(source: str = "bazos", *, today: int = 1_500, back: int = 1_500,
            queued: int = 0, daily: bool = True) -> list[tuple[Any, ...]]:
    """_INFLOW_SQL rows: `today` written in 24 h, `back` in each baseline window."""
    rows = [("written", source, 0, today)]
    rows += [("written", source, 7 * k, back) for k in (1, 2, 3, 4)]
    if not daily:  # spread a week's worth over every day back, the 7-day windows' shape
        rows = [("written", source, d, today // 7 if d < 7 else back // 7) for d in range(35)]
    if queued:
        rows.append(("queued", source, 0, queued))
    return rows


def _scraping(walks: list[tuple[Any, ...]], *, inflow: list[tuple[Any, ...]] | None = None,
              fetch: list[tuple[Any, ...]] | None = None,
              thresholds: dict[str, Any] = T) -> tuple[dict[str, Any], _Conn]:
    conn = _Conn({sc._WALKS_SQL: walks, sc._INFLOW_SQL: inflow or [],
                  sc._FETCH_ERRORS_SQL: fetch or []}, now=_NOW)
    return sc.check_scraping(conn, thresholds), conn


def _cells(result: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {c["subject"]: c for c in result["details"]["cells"]}


# --- walks ---------------------------------------------------------------------------------


@pytest.mark.parametrize("age_h, status", [(21.0, "ok"), (21.7, "warn"), (43.0, "warn"),
                                           (43.3, "fail")])
def test_a_unit_watches_past_3_walk_gaps_and_fails_past_6(age_h: float, status: str) -> None:
    cell = _cells(_scraping([_unit(end_h=age_h)])[0])["bazos/walks/byt-prodej"]
    assert (cell["status"], cell["portal"], cell["unit"]) == (status, "bazos", "h")
    assert cell["value"] == round(age_h, 1) and cell["facts"]["gap_basis"] == "its own"


def test_a_unit_with_no_end_reached_walk_in_14_days_fails_whatever_its_gap() -> None:
    cell = _cells(_scraping([_unit(end_h=14 * 24 + 1, gap_h=200.0)])[0])["bazos/walks/byt-prodej"]
    assert cell["status"] == "fail" and cell["message"].startswith("no end-reached walk in 14 d")


def test_a_unit_no_walk_reached_the_end_of_in_30_days_fails() -> None:
    cell = _cells(_scraping([_unit(end_h=None, walked_h=3.0)])[0])["bazos/walks/byt-prodej"]
    assert cell["status"] == "fail" and cell["value"] is None
    assert "reached the portal's end" in cell["message"]


def test_a_portal_with_too_few_gaps_is_scored_on_the_slowest_portals_gap() -> None:
    """Fewer than 10 finished gaps in 14 d: never dropped, judged on the slowest G (7.2 h here)."""
    walks = [_unit("bazos", gap_h=7.2), _unit("sreality", gap_h=4.4),
             _unit("remax", end_h=30.0, gap_h=1.0, gaps=3)]
    cell = _cells(_scraping(walks)[0])["remax/walks/byt-prodej"]
    assert cell["status"] == "warn" and cell["facts"]["gap_basis"] == "the slowest portal's"
    assert cell["facts"]["gap_h"] == 7.2


def test_with_no_portal_able_to_set_a_gap_a_fresh_unit_is_not_measured() -> None:
    walks = [_unit(end_h=3.0, gaps=4), _unit("sreality", end_h=15 * 24, gaps=2)]
    cells = _cells(_scraping(walks)[0])
    assert cells["bazos/walks/byt-prodej"]["status"] == "unknown"
    assert cells["bazos/walks/byt-prodej"]["message"].startswith("Not measured")
    assert cells["sreality/walks/byt-prodej"]["status"] == "fail", "stale needs no gap"


def test_a_portal_whose_runs_never_finished_reads_problem_rather_than_dropping_out() -> None:
    cells = _cells(_scraping([_unit(), _bare("idnes", started_h=2.0)])[0])
    assert cells["idnes/walks"]["status"] == "fail" and cells["idnes/walks"]["portal"] == "idnes"
    assert cells["idnes/walks"]["message"] == (
        "no finished walk in 30 d: the newest walk started 2.0 h ago and recorded no unit")


def test_a_portal_with_no_walk_in_the_window_reads_problem_and_keeps_its_other_arms() -> None:
    """Never dropped: every portal in SOURCES gets its cells, whether or not it walked."""
    out, _ = _scraping([_unit()], inflow=_inflow("idnes"), fetch=[("idnes", 300, 1, 900, 1, None)])
    cells = _cells(out)
    arms = ("walks", "inflow", "fetch_errors")
    assert {f"{s}/{arm}" for s in SOURCES for arm in arms} <= set(cells)
    assert (cells["idnes/walks"]["status"], cells["idnes/walks"]["message"]) == (
        "fail", "no walk started in 30 d")
    assert (cells["idnes/inflow"]["status"], cells["idnes/fetch_errors"]["status"]) == ("ok", "ok")
    nothing, _ = _scraping([])
    assert (nothing["status"], nothing["value"]) == ("fail", len(SOURCES))
    assert "not_measured" not in nothing["details"]


def test_a_walked_portal_reads_ok_on_its_newest_finished_walk() -> None:
    """The `<portal>/walks` subject is measured on every pass, so a Problem there recovers."""
    out, _ = _scraping([_unit(end_h=5.0), _unit(kind="pronajem", end_h=5.0)])
    cell = _cells(out)["bazos/walks"]
    assert (cell["status"], cell["value"], cell["unit"]) == ("ok", 5.0, "h")
    assert cell["message"] == "2 units walked in 30 d; the newest finished walk started 5.0 h ago"
    assert cell["facts"] == {"finished_at": _ago(hours=5.0).isoformat(), "units": 2}


def test_a_portals_first_finished_walk_recovers_its_walks_incident_with_one_notice() -> None:
    """An unfinished first run reads Problem; the finished walk reads OK on the same subject, and
    the ladder closes the incident on a second OK measured across its 30-minute hold."""
    def at(minutes: int, walks: list[tuple[Any, ...]]) -> tuple[_dt.datetime, dict[str, Any]]:
        when = _NOW + _dt.timedelta(minutes=minutes)
        conn = _Conn({sc._WALKS_SQL: walks, sc._INFLOW_SQL: [], sc._FETCH_ERRORS_SQL: []},
                     now=when)
        return when, sc.check_scraping(conn, T)

    unfinished, finished = (_fleet(_bare("idnes", started_h=2.0)),
                            _fleet(_unit("idnes", end_h=2.0, gap_h=None, gaps=0)))
    results = [at(0, unfinished), *(at(m, finished) for m in (10, 20, 40))]
    assert [_cells(r)["idnes/walks"]["status"] for _, r in results] == ["fail", "ok", "ok", "ok"]
    ladder = _told("scraping", 600, results)
    (inc,) = _incidents(ladder, "scraping")
    assert (inc["subject"], inc["first_seen_at"]) == ("idnes/walks", _NOW)
    assert (inc["resolved_at"], inc["resolve_reason"]) == (results[-1][0], "measured ok")
    told = [(key.split(":")[2], message) for message, key, _ in ladder.dispatches]
    assert [kind for kind, _ in told] == ["onset", "recovery"]
    assert told[1][1] == "✓ scraping: idnes/walks recovered after 0 h"


# --- missing -------------------------------------------------------------------------------


@pytest.mark.parametrize("collected, status", [(9_800, "ok"), (9_790, "warn"), (9_499, "fail"),
                                               (10_150, "ok")])
def test_missing_watches_past_2_and_fails_past_5_percent(collected: int, status: str) -> None:
    cell = _cells(_scraping([_unit(collected=collected)])[0])["bazos/missing/byt-prodej"]
    assert (cell["status"], cell["unit"]) == (status, "%")
    assert cell["value"] == round((10_000 - collected) / 100, 2)


@pytest.mark.parametrize("over, why", [
    ({"recorded": 0}, "not yet recorded by this walk's adapter"),
    ({"entries": 2, "recorded": 1}, "not yet recorded by this walk's adapter"),
    ({"counted": True}, "declared is a count of what was seen"),
    ({"declared": 499, "collected": 400}, "under the 500 floor"),
    ({"undeclared": 1}, "declared no total"),
])
def test_a_declared_total_that_cannot_be_judged_is_not_measured(over: dict[str, Any],
                                                                 why: str) -> None:
    """Never 0 %: a self-certified or unrecorded total reads Not measured with its reason."""
    cell = _cells(_scraping([_unit(**over)])[0])["bazos/missing/byt-prodej"]
    assert cell["status"] == "unknown" and why in cell["message"]


def test_an_upper_bound_declared_total_is_carried_as_a_fact() -> None:
    cell = _cells(_scraping([_unit(upper=True, collected=9_900)])[0])["bazos/missing/byt-prodej"]
    assert cell["status"] == "ok" and cell["facts"]["declared_is_upper_bound"] is True


# --- inflow --------------------------------------------------------------------------------


@pytest.mark.parametrize("today, status", [(299, "fail"), (300, "warn"), (749, "warn"),
                                           (750, "ok"), (4_500, "ok"), (4_501, "warn")])
def test_inflow_against_the_median_of_the_same_window_one_to_four_weeks_back(
        today: int, status: str) -> None:
    cell = _cells(_scraping([_unit()], inflow=_inflow(today=today))[0])["bazos/inflow"]
    assert (cell["status"], cell["portal"]) == (status, "bazos")
    assert cell["facts"]["window"] == "24 h" and cell["facts"]["baseline"] == 1_500


def test_a_stopped_drain_is_not_lost_discovery() -> None:
    """Ads the walks queued but the drain has not written yet are discovered all the same."""
    cell = _cells(_scraping([_unit()], inflow=_inflow(today=0, queued=1_480))[0])["bazos/inflow"]
    assert cell["status"] == "ok" and cell["facts"]["queued"] == 1_480


def test_a_thin_daily_baseline_compares_seven_day_windows_and_a_thin_week_is_not_measured() -> None:
    weekly = _cells(_scraping([_unit()], inflow=_inflow(today=140, back=133, daily=False))[0])
    assert weekly["bazos/inflow"]["status"] == "ok"
    assert weekly["bazos/inflow"]["facts"]["window"] == "7 d"
    thin = _cells(_scraping([_unit()], inflow=_inflow(today=0, back=7, daily=False))[0])
    assert thin["bazos/inflow"]["status"] == "unknown"


def test_inflow_reads_new_priority_queue_rows_over_five_weeks_of_listings() -> None:
    _, conn = _scraping([_unit()], inflow=_inflow())
    (params,) = [p for sql, p, _ in conn.sent if sql == sc._INFLOW_SQL]
    assert params == {"sources": sorted(SOURCES), "days": 35, "new": QUEUE_PRIORITY_NEW}


# --- fetch errors --------------------------------------------------------------------------


@pytest.mark.parametrize("failed, status", [(3, "ok"), (10, "warn"), (30, "fail")])
def test_fetch_errors_are_judged_on_the_wilson_lower_bound(failed: int, status: str) -> None:
    """10 of 200 is 5 % with a lower bound of 2.7 %; 30 of 200 bounds at 10.7 %. The value is
    the share (what happened); the bound it is judged on sits in the facts and the message."""
    out, _ = _scraping([_unit()], fetch=[("bazos", 200, failed, 900, failed, None)])
    cell, lower = _cells(out)["bazos/fetch_errors"], wilson_lower(failed, 200)
    assert (cell["status"], cell["facts"]["window"]) == (status, "24 h")
    assert cell["value"] == round(100 * failed / 200, 2)
    assert cell["facts"]["lower_bound"] == round(lower, 5)
    assert f"({failed / 200:.2%}; lower bound {lower:.2%})" in cell["message"]


def test_a_thin_day_widens_to_seven_days_and_a_thin_week_is_not_measured() -> None:
    week = _cells(_scraping([_unit()], fetch=[("bazos", 150, 10, 1_200, 10, None)])[0])
    assert week["bazos/fetch_errors"]["facts"]["window"] == "7 d"
    assert week["bazos/fetch_errors"]["status"] == "ok"
    thin = _cells(_scraping([_unit()], fetch=[("bazos", 20, 9, 57, 9, None)])[0])
    assert thin["bazos/fetch_errors"]["status"] == "unknown"


def test_an_id_given_up_on_a_loop_is_named_not_excluded() -> None:
    """The old 25 % false FAIL was one poison id: 1 of ~6,060 ids here, and named."""
    cell = _cells(_scraping([_unit("sreality")], fetch=[
        ("sreality", 6_060, 1, 31_035, 1, [["615387212", 143]])])[0])["sreality/fetch_errors"]
    assert cell["status"] == "ok" and "615387212 (143×)" in cell["message"]
    assert cell["facts"]["given_up_repeatedly"] == ["615387212"]


def test_a_portal_absent_from_the_ledger_is_not_measured() -> None:
    cell = _cells(_scraping([_unit()])[0])["bazos/fetch_errors"]
    assert cell["status"] == "unknown" and cell["facts"]["ids_7d"] == 0


# --- the scraping verdict ------------------------------------------------------------------


def test_the_verdict_is_the_worst_cell_and_every_statement_runs_on_the_clock() -> None:
    walks = _fleet(_unit(end_h=50.0), _unit("sreality", gap_h=4.4, end_h=2.0))
    out, conn = _scraping(walks, inflow=_inflow(), fetch=[("bazos", 300, 0, 900, 0, None)])
    assert (out["check_key"], out["status"], out["value"]) == ("scraping", "fail", 1)
    assert conn.sent and all(armed for _, _, armed in conn.sent)
    assert [c["subject"] for c in out["details"]["cells"][:2]] == [
        "bazos/walks", "bazos/walks/byt-prodej"]


def test_the_declared_total_is_read_by_its_shared_key() -> None:
    assert f"'{DECLARED_TOTAL_KEY}'" in sc._WALKS_SQL
    assert "DECLARED_TOTAL_KEY" in inspect.getsource(sc)


# --- coverage ------------------------------------------------------------------------------


def _coverage(walks: list[tuple[Any, ...]], *, held: list[tuple[Any, ...]] | None = None,
              unnominated: list[tuple[Any, ...]] | None = None,
              trend: list[dict[str, Any]] | None = None) -> tuple[dict[str, Any], _Conn]:
    """check_coverage over these rows; `trend` is its newest measured result's cells."""
    conn = _Conn({sc._WALKS_SQL: walks, sc._HELD_SQL: held or [],
                  sc._UNNOMINATED_SQL: unnominated or [],
                  common.TREND_SQL: [] if trend is None else [(trend,)]}, now=_NOW)
    return sc.check_coverage(conn, T), conn


@pytest.mark.parametrize("held, pending, status", [
    (10_150, 0, "ok"), (10_400, 150, "warn"), (10_600, 0, "fail"), (9_700, 0, "warn"),
    (9_400, 0, "fail"), (10_600, 450, "ok")])
def test_the_residual_sets_ads_in_dwell_aside_and_reads_both_signs(held: int, pending: int,
                                                                     status: str) -> None:
    out, _ = _coverage([_unit()], held=[("bazos", "byt", "prodej", held, pending)])
    cell = _cells(out)["bazos/residual/byt-prodej"]
    assert (cell["status"], cell["unit"]) == (status, "%")
    assert cell["value"] == round((held - pending - 10_000) / 100, 2)
    assert (cell["facts"]["held"], cell["facts"]["in_dwell"]) == (held, pending)


def test_a_self_certified_or_unrecorded_total_leaves_the_residual_not_measured() -> None:
    out, _ = _coverage([_unit(counted=True), _unit("idnes", recorded=0)],
                       held=[("bazos", "byt", "prodej", 10_000, 0),
                             ("idnes", "byt", "prodej", 12_000, 0)])
    cells = _cells(out)
    assert cells["bazos/residual/byt-prodej"]["message"] == (
        "Not measured: declared is a count of what was seen")
    assert cells["idnes/residual/byt-prodej"]["message"] == (
        "Not measured: not yet recorded by this walk's adapter")


@pytest.mark.parametrize("ads, deferred, status", [(24, 0, "ok"), (25, 0, "warn"),
                                                    (99, 0, "warn"), (100, 0, "fail"),
                                                    (130, 40, "warn")])
def test_unnominated_ads_less_the_walks_deferrals(ads: int, deferred: int, status: str) -> None:
    out, _ = _coverage([_unit(deferred=deferred)], unnominated=[("bazos", ads, 0, _ago(days=3))])
    cell = _cells(out)["bazos/unnominated"]
    assert (cell["status"], cell["value"], cell["unit"]) == (status, ads - deferred, "ads")
    assert cell["facts"]["deferred"] == deferred


def test_ads_in_categories_no_walk_covers_are_counted_by_the_portals_walk() -> None:
    """NULL-category ads: no unit matches them, so the portal's all-reached walk dates them."""
    walks = [_unit("realitymix", all_reached_h=5.0), _unit("realitymix", "dum", end_h=None)]
    out, conn = _coverage(walks, unnominated=[("realitymix", 151, 151, _ago(days=112))])
    cell = _cells(out)["realitymix/unnominated"]
    assert cell["status"] == "fail" and cell["facts"]["no_walked_category"] == 151
    assert "151 in a category no end-reached walk covers" in cell["message"]
    (params,) = [p for sql, p, _ in conn.sent if sql == sc._UNNOMINATED_SQL]
    assert params["mains"] == ["byt"], "a unit with no end-reached walk dates nothing"
    assert dict(zip(params["portal_sources"], params["portal_starts"])) == {
        s: _ago(hours=5.0) if s == "realitymix" else None for s in SOURCES}
    assert "coalesce(u.start_at, p.start_at)" in sc._UNNOMINATED_SQL


def test_a_portal_no_walk_reached_the_end_of_has_no_unnominated_verdict() -> None:
    out, _ = _coverage([_unit(end_h=None, all_reached_h=None)])
    assert _cells(out)["bazos/unnominated"]["status"] == "unknown"


def test_a_portal_with_no_walk_in_the_window_keeps_its_unnominated_cell() -> None:
    """The coverage twin: every portal in SOURCES gets its unnominated cell, walked or not."""
    out, conn = _coverage([_unit()], unnominated=[("bazos", 3, 0, None)])
    cells = _cells(out)
    assert {f"{s}/unnominated" for s in SOURCES} <= set(cells)
    assert cells["idnes/unnominated"]["status"] == "unknown"
    assert cells["idnes/unnominated"]["message"] == (
        "Not measured: no walk in 30 d reached the portal's end")
    (params,) = [p for sql, p, _ in conn.sent if sql == sc._HELD_SQL]
    assert params == {"sources": sorted(SOURCES)}
    nothing, _ = _coverage([])
    assert nothing["status"] == "unknown" and "not_measured" not in nothing["details"]


def test_each_cell_carries_its_rolling_trend_and_whether_it_is_worse_than_a_week_ago() -> None:
    """Wave 6 (D11): coverage's cells carry the gauges' one trend (common.trend), rolled on from
    this check's newest measured result; W4's trend_30d / worse_than_7d are gone."""
    trend = [{"subject": "bazos/unnominated", "facts": {"trend": [["2026-10-12", 10, "ok"]]}},
             {"subject": "bazos/residual/byt-prodej",
              "facts": {"trend": [["2026-10-12", -3.0, "warn"], ["2026-10-18", 1.0, "ok"]]}}]
    out, conn = _coverage([_unit()], held=[("bazos", "byt", "prodej", 10_150, 0)],
                          unnominated=[("bazos", 30, 0, _ago(days=1))], trend=trend)
    cells = _cells(out)
    unnominated = cells["bazos/unnominated"]["facts"]
    assert unnominated["trend"] == [["2026-10-12", 10, "ok"], ["2026-10-19", 30, "warn"]]
    assert unnominated["worse_7d"] is True
    residual = cells["bazos/residual/byt-prodej"]["facts"]
    assert residual["trend"] == [["2026-10-12", -3.0, "warn"], ["2026-10-18", 1.0, "ok"],
                                 ["2026-10-19", 1.5, "ok"]]
    assert residual["worse_7d"] is False
    assert not {"trend_30d", "worse_than_7d"} & set(unnominated)
    assert {str(p) for sql, p, _ in conn.sent if sql == common.TREND_SQL} == {
        str({"key": "coverage"})}


def test_a_cell_with_no_reading_a_week_ago_claims_no_trend() -> None:
    out, _ = _coverage([_unit()], unnominated=[("bazos", 3, 0, None)])
    facts = _cells(out)["bazos/unnominated"]["facts"]
    assert (facts["trend"], facts["worse_7d"]) == ([["2026-10-19", 3, "ok"]], None)


def test_the_coverage_verdict_names_its_key() -> None:
    out, conn = _coverage([_unit()], held=[("bazos", "byt", "prodej", 10_600, 0)])
    assert (out["check_key"], out["status"]) == ("coverage", "fail")
    assert conn.sent and all(armed for _, _, armed in conn.sent)


# --- the module's own rails ------------------------------------------------------------------


def test_the_module_never_imports_the_harness_the_worker_or_the_ladder() -> None:
    """The harness imports these checks, so importing it back is a cycle; the worker is a lane."""
    tree = ast.parse(inspect.getsource(sc))
    imported = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    imported |= {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert not imported & {"scripts.verify_pipeline", "scraper.realtime_worker",
                           "toolkit.system_alerts"}


def test_the_checks_read_exactly_the_thresholds_the_contract_names() -> None:
    read = set(re.findall(r"""thresholds\[["'](\w+)["']\]""", inspect.getsource(sc)))
    assert read == set(T)


def test_the_harness_declares_every_threshold_these_checks_read_with_the_contracts_value() -> None:
    from scripts.verify_pipeline import THRESHOLDS

    assert {key: THRESHOLDS.get(key) for key in T} == T
