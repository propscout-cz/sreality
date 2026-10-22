"""Hermetic tests for scripts.checks.delisting: outflow, stuck, backlog, deferrals, precision."""

# No DB: the wave-3 fake connection answers each statement by the SQL constant it carries, through
# the per-check clock. At build time the SQL was EXPLAINed and PREPAREd ($n form, as CI's PREPARE
# sweep sends it) read-only against production.

from __future__ import annotations

import ast
import datetime as _dt
import inspect
import re
from typing import Any

import pytest

from location_data.claims_common import SOURCES
from scraper import delist_policy
from scraper.db import QUEUE_PRIORITY_VERIFY
from scripts.checks import delisting as dl
from tests.scripts.test_checks_platform import _Conn

_UTC = _dt.timezone.utc
_NOW = _dt.datetime(2026, 10, 19, 6, 0, tzinfo=_UTC)
_H = 3600.0

# The delisting keys exactly as the wave-4 contract spells them (§ 2.12), and the outflow's
# alignment the review of draft #1875 added.
T: dict[str, Any] = {
    "delisting_outflow_fail_share": 0.2, "delisting_outflow_warn_share": 0.5,
    "delisting_outflow_spike_share": 3.0, "delisting_outflow_spike_min": 500,
    "delisting_outflow_min_baseline": 50, "delisting_outflow_align_h": 3,
    "delisting_stuck_warn": 25, "delisting_stuck_fail": 250, "delisting_stuck_slack_h": 1,
    "delisting_verify_warn_h": 6, "delisting_verify_fail_h": 24, "delisting_deferral_walks": 2,
    "delisting_precision_warn": 0.10, "delisting_precision_fail": 0.15,
    "delisting_precision_min_n": 50,
}


def _walks(source: str = "sreality", *, every_h: float = 6.0, n: int = 50, reached: bool = True,
           deferred: list[int] | None = None) -> list[tuple[Any, ...]]:
    """_WALKS_SQL's rows for `n` finished walks `every_h` apart, the newest an hour ago."""
    deferred = deferred or []
    rows = []
    for i in range(n):
        back = n - 1 - i
        rows.append((source, _NOW - _dt.timedelta(hours=1 + back * every_h), reached,
                     deferred[i - n + len(deferred)] if i >= n - len(deferred) else 0))
    return rows


_OFFSETS = list(range(-T["delisting_outflow_align_h"], T["delisting_outflow_align_h"] + 1))


def _outflow(source: str, current: int, *back: int | tuple[int, ...]) -> list[tuple[Any, ...]]:
    """_OUTFLOW_SQL's rows: the trailing 24 h, then each week back's 24 h at -3..+3 h."""
    # A week given as one number holds it at every offset; a tuple gives its seven windows.
    rows = [(source, 0, 0, current)] if current else []
    for lag, week in zip(dl._OUTFLOW_LAGS_D, back):
        sums = week if isinstance(week, tuple) else (week,) * len(_OFFSETS)
        rows += [(source, lag, off, ads) for off, ads in zip(_OFFSETS, sums) if ads]
    return rows


def _check(*, walks: Any = None, outflow: Any = (), verify: Any = (), precision: Any = (),
           stuck: Any = ()) -> tuple[dict[str, Any], _Conn]:
    conn = _Conn({dl._WALKS_SQL: _walks() if walks is None else walks,
                  dl._OUTFLOW_SQL: list(outflow), dl._VERIFY_SQL: list(verify),
                  dl._PRECISION_SQL: list(precision), dl._STUCK_SQL: stuck}, now=_NOW)
    return dl.check_delisting(conn, T), conn


def _cells(result: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {c["subject"]: c for c in result["details"]["cells"]}


def _params(conn: _Conn, sql: str) -> Any:
    return next(params for sent, params, _ in conn.sent if sent == sql)


# --- outflow ------------------------------------------------------------------------------------


def test_outflow_at_its_usual_volume_is_ok_against_the_median_of_four_weeks_back() -> None:
    out, conn = _check(outflow=_outflow("sreality", 3_400, 2_883, 2_951, 2_960, 3_944))
    c = _cells(out)["sreality/outflow"]
    assert c["status"] == "ok" and c["portal"] == "sreality"
    assert c["value"] == 3_400 and c["unit"] == "ads"
    assert c["facts"]["baseline"] == c["facts"]["baseline_busy"] == 2_955.5
    assert c["facts"]["align_h"] == 3 and c["facts"]["share"] == 1.15
    assert c["facts"]["weeks_back"] == {"7 d": 2_883, "14 d": 2_951, "21 d": 2_960, "28 d": 3_944}
    assert "of the quietest 24 h within ±3 h of this hour, one to four weeks back" in c["message"]
    assert _params(conn, dl._OUTFLOW_SQL) == {"lags": [0, 7, 14, 21, 28], "align_h": 3}


def test_a_walk_run_two_hours_late_moves_no_burst_out_of_the_baseline() -> None:
    """realitymix 10-05 15:00: its midday walk ran 1 h 53 min late (the replay's rows)."""
    # On fixed windows the weeks back read 128, 476, 590 and 556 at offset 0: 87 ads were 17 % of
    # their median 516, a Problem. Each week's quietest window ends before its burst: 43, 103,
    # 431 and 130, median 116.5, so 87 ads are 75 % of it.
    out, _ = _check(outflow=_outflow("realitymix", 87, (91, 45, 43, 128, 128, 128, 108),
                                     (137, 103, 174, 476, 476, 458, 444),
                                     (462, 431, 514, 590, 859, 829, 810),
                                     (130, 147, 556, 556, 556, 556, 556)))
    c = _cells(out)["realitymix/outflow"]
    assert c["status"] == "ok" and c["facts"]["baseline"] == 116.5
    assert c["facts"]["baseline_busy"] == 516
    assert c["facts"]["weeks_back"] == {"7 d": 43, "14 d": 103, "21 d": 431, "28 d": 130}
    assert c["message"].startswith("87 ads made inactive in 24 h: 75% of the median 116.5 ")


def test_the_zero_flip_day_is_a_problem_on_every_portal_with_a_baseline() -> None:
    """10-10: no ad was made inactive for 32 h, against medians of 400-1,900 a day."""
    out, _ = _check(outflow=[*_outflow("sreality", 0, 2_883, 2_951, 2_960, 3_944),
                             *_outflow("bazos", 0, 1_400, 1_620, 1_380, 1_510)])
    cells = _cells(out)
    for portal in ("sreality", "bazos"):
        assert cells[f"{portal}/outflow"]["status"] == "fail"
        message = cells[f"{portal}/outflow"]["message"]
        assert "0 ads made inactive in 24 h: 0% of the median" in message
    assert out["status"] == "fail"


@pytest.mark.parametrize(("current", "status"), [
    (199, "fail"), (200, "warn"), (499, "warn"), (500, "ok"), (3_000, "ok")])
def test_outflow_reads_problem_under_a_fifth_and_watch_under_half(current: int,
                                                                     status: str) -> None:
    out, _ = _check(outflow=_outflow("idnes", current, 1_000, 1_000, 1_000, 1_000))
    assert _cells(out)["idnes/outflow"]["status"] == status


def test_a_spike_is_watch_only_past_three_times_the_baseline_and_500_ads() -> None:
    big, _ = _check(outflow=_outflow("idnes", 3_001, 1_000, 1_000, 1_000, 1_000))
    assert _cells(big)["idnes/outflow"]["status"] == "warn"
    assert ("; 300% of the median 1,000 of the busiest such 24 h: a spike (Watch over 300% and "
            "500 ads)") in _cells(big)["idnes/outflow"]["message"]
    small, _ = _check(outflow=_outflow("remax", 400, 100, 100, 100, 100))
    assert _cells(small)["remax/outflow"]["status"] == "ok"


def test_a_spike_is_judged_on_the_busiest_windows_around_this_hour() -> None:
    """idnes 10-19 05:00: 1,405 ads, 430 % of the fixed windows' median, 208 % of the busiest."""
    # The replay's rows: a later walk's burst sits 1-3 h after this hour in the 28 d week.
    out, _ = _check(outflow=_outflow("idnes", 1_405, (1_113,) * 6 + (1_795,),
                                     (292, 292, 292, 292, 300, 346, 512),
                                     (347, 347, 347, 347, 346, 309, 313),
                                     (306, 306, 306, 306, 717, 839, 813)))
    c = _cells(out)["idnes/outflow"]
    assert c["status"] == "ok" and c["facts"]["baseline_busy"] == 675.5
    assert c["facts"]["baseline"] == 307.5 and c["facts"]["share"] == 4.569


def test_one_empty_week_back_does_not_sink_the_baseline() -> None:
    """The zero-flip day sits in the windows a week later: the median of four keeps the level."""
    out, _ = _check(outflow=_outflow("sreality", 3_100, 0, 2_951, 2_960, 3_944))
    c = _cells(out)["sreality/outflow"]
    assert c["status"] == "ok" and c["facts"]["baseline"] == 2_955.5


def test_a_portal_under_fifty_a_day_is_not_measured_by_its_volume() -> None:
    out, _ = _check(outflow=_outflow("maxima", 0, 3, 2, 4, 1))
    c = _cells(out)["maxima/outflow"]
    assert c["status"] == "unknown" and c["message"].startswith("Not measured: ")
    assert "under the 50 this arm needs" in c["message"]


def test_the_floor_judges_the_quietest_windows_not_the_busiest() -> None:
    """A portal whose bursts reach 80 a day but whose quietest windows hold 40 is not measured."""
    week = (40, 40, 40, 80, 80, 80, 80)
    out, _ = _check(outflow=_outflow("mmreality", 0, week, week, week, week))
    c = _cells(out)["mmreality/outflow"]
    assert c["status"] == "unknown" and c["facts"]["baseline"] == 40
    assert c["facts"]["baseline_busy"] == 80


# --- stuck confirmations -----------------------------------------------------------------------


def test_the_effective_dwell_is_the_longer_of_the_two_policy_constants() -> None:
    assert dl._EFFECTIVE_DWELL_S == max(delist_policy.GONE_DWELL,
                                        delist_policy.RECHECK_THROTTLE).total_seconds()
    assert dl._EFFECTIVE_DWELL_S == 24 * _H


def test_the_module_imports_both_dwell_constants_and_spells_no_duration() -> None:
    """GONE_DWELL and RECHECK_THROTTLE are imported, never re-spelled."""
    tree = ast.parse(inspect.getsource(dl))
    names = {(n.module, a.name) for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)
             for a in n.names}
    assert {("scraper.delist_policy", "GONE_DWELL"),
            ("scraper.delist_policy", "RECHECK_THROTTLE")} <= names
    calls = {getattr(n.func, "attr", getattr(n.func, "id", None)) for n in ast.walk(tree)
             if isinstance(n, ast.Call)}
    assert "timedelta" not in calls


def test_the_stuck_limit_is_the_dwell_plus_two_p90_walk_gaps_plus_the_slack() -> None:
    """Walks 6 h apart: 24 h + 2 x 6 h + 1 h = 37 h, sent to the SQL per portal."""
    out, conn = _check(stuck=[("sreality", 1_460, 0, 22.4 * _H)])
    params = _params(conn, dl._STUCK_SQL)
    assert params["sources"] == ["sreality"] and params["limits_s"] == [37 * _H]
    c = _cells(out)["sreality/stuck"]
    assert c["status"] == "ok" and c["value"] == 0
    assert c["facts"]["limit_s"] == round(37 * _H) and c["facts"]["walk_gap_own"] is True
    assert "1,460 waiting, the oldest for 22.4 h" in c["message"]
    assert "24.0 h effective dwell + 2 × its p90 walk gap of 6.0 h + 1 h" in c["message"]


def test_the_p90_gap_reads_the_slow_walks_of_an_uneven_cadence() -> None:
    starts = [_NOW - _dt.timedelta(hours=h) for h in (40, 34, 28, 22, 16, 10, 4, 1)]
    walks = [("idnes", at, True, 0) for at in starts]
    _, conn = _check(walks=walks, stuck=[])
    gaps = [6.0] * 6 + [3.0]
    assert _params(conn, dl._STUCK_SQL)["limits_s"] == [(24 + 2 * dl._p90(gaps) + 1) * _H]
    assert dl._p90(gaps) == pytest.approx(6.0)


@pytest.mark.parametrize(("stuck", "status"), [(24, "ok"), (25, "warn"), (249, "warn"),
                                                (250, "fail")])
def test_stuck_reads_watch_at_25_and_problem_at_250(stuck: int, status: str) -> None:
    out, _ = _check(stuck=[("sreality", 3_000, stuck, 52.0 * _H)])
    c = _cells(out)["sreality/stuck"]
    assert c["status"] == status and c["value"] == stuck
    if status != "ok":
        assert f"{stuck:,} active ads have waited past 37.0 h" in c["message"]


def test_a_portal_without_two_walks_borrows_the_slowest_portals_gap() -> None:
    walks = [*_walks("sreality", every_h=4.0), *_walks("bazos", every_h=9.0),
             ("remax", _NOW - _dt.timedelta(hours=3), True, 0)]
    out, conn = _check(walks=walks, stuck=[("remax", 40, 0, 20 * _H)])
    params = _params(conn, dl._STUCK_SQL)
    assert params["sources"] == ["bazos", "sreality"]
    assert params["fallback_s"] == (24 + 2 * 9 + 1) * _H
    c = _cells(out)["remax/stuck"]
    assert c["facts"]["walk_gap_own"] is False and c["facts"]["limit_s"] == round(43 * _H)
    assert "the slowest portal's (it has no two finished walks in 14 d)" in c["message"]
    young = dict(zip(*(_params(conn, dl._PRECISION_SQL)[k] for k in ("sources", "young_s"))))
    assert (young["remax"], young["bazos"], young["sreality"]) == (19 * _H, 19 * _H, 9 * _H)


def test_with_no_walk_gap_anywhere_neither_stuck_nor_precision_is_read() -> None:
    out, conn = _check(walks=[], outflow=_outflow("bazos", 1_300, 1_400, 1_620, 1_380, 1_510))
    assert not {dl._STUCK_SQL, dl._PRECISION_SQL} & {sql for sql, _, _ in conn.sent}
    assert {"stuck", "precision"} <= set(out["details"]["not_measured"])
    assert not {"bazos/stuck", "bazos/precision"} & set(_cells(out))


# --- verify backlog -----------------------------------------------------------------------------


@pytest.mark.parametrize(("hours", "status"), [(3.4, "ok"), (6.0, "ok"), (6.1, "warn"),
                                               (24.0, "warn"), (24.1, "fail")])
def test_the_oldest_presence_check_reads_watch_past_6_h_and_problem_past_24(hours: float,
                                                                             status: str) -> None:
    out, conn = _check(verify=[("sreality", 66, 65, hours * _H)])
    c = _cells(out)["sreality/verify_backlog"]
    assert c["status"] == status and c["value"] == round(hours * 60)
    assert "(66 waiting, 65 of them claimed)" in c["message"]
    assert _params(conn, dl._VERIFY_SQL) == {"priority": QUEUE_PRIORITY_VERIFY}


def test_an_empty_verify_queue_is_a_measured_ok() -> None:
    out, _ = _check()
    c = _cells(out)["sreality/verify_backlog"]
    assert c["status"] == "ok" and c["value"] == 0
    assert c["message"] == "no presence check is waiting"


# --- deferrals -----------------------------------------------------------------------------------


def test_two_end_reached_walks_in_a_row_with_deferrals_read_watch_never_problem() -> None:
    """The 10-13..15 catch-up deferred on two walks in a row, then three: Watch, no notice."""
    for streak in (2, 5, 30):
        out, _ = _check(walks=_walks("ceskereality", n=40, deferred=[2_648] * streak))
        c = _cells(out)["ceskereality/deferrals"]
        assert c["status"] == "warn" and c["value"] == streak, streak
        assert f"the last {streak} walks in which a unit reached the end" in c["message"]
        assert c["facts"]["walks"] == c["facts"]["walks_a_unit_reached_end"] == 40


def test_one_deferring_walk_is_ok_and_says_so() -> None:
    out, _ = _check(walks=_walks("bazos", n=40, deferred=[0, 79]))
    c = _cells(out)["bazos/deferrals"]
    assert c["status"] == "ok" and c["value"] == 1
    assert "had 79 nominations deferred by the flip cap" in c["message"]


def test_a_walk_that_did_not_reach_the_end_neither_breaks_nor_extends_a_streak() -> None:
    walks = _walks("ceskereality", n=10, deferred=[369, 141])
    walks.append(("ceskereality", _NOW - _dt.timedelta(minutes=10), False, 0))
    out, _ = _check(walks=walks)
    c = _cells(out)["ceskereality/deferrals"]
    assert c["value"] == 2 and (c["facts"]["walks"], c["facts"]["walks_a_unit_reached_end"]) == (
        11, 10)


def test_the_newest_end_reached_walk_without_deferrals_clears_the_streak() -> None:
    out, _ = _check(walks=_walks("ceskereality", n=10, deferred=[2_648, 1_274, 0]))
    c = _cells(out)["ceskereality/deferrals"]
    assert c["status"] == "ok" and c["value"] == 0
    assert c["message"] == ("the newest walk in which a unit reached the end had no nomination "
                            "deferred")


def test_no_end_reached_walk_in_two_weeks_leaves_deferrals_not_measured() -> None:
    out, _ = _check(walks=_walks("ceskereality", reached=False))
    c = _cells(out)["ceskereality/deferrals"]
    assert c["status"] == "unknown" and c["message"] == (
        "Not measured: no unit reached the end in any walk of the last 14 d")
    assert c["facts"] == {"walks": 50, "walks_a_unit_reached_end": 0}


# --- precision -----------------------------------------------------------------------------------


def test_the_cohort_starts_where_the_stuck_arm_says_the_walks_have_sighted_a_listed_ad() -> None:
    """idnes walks every 9.2 h, sreality 6.6 h: 19.4-43.4 h and 14.2-38.2 h, both short of 50 h."""
    # One bound, two arms: the stuck limit is the dwell plus the age the cohort starts at. An ad
    # first sighted again 50 h after its flip was absent from end-reached walks after it: a
    # re-list, and its flip has left the window before it comes back (the live test seeds it).
    walks = [*_walks("idnes", every_h=9.2), *_walks("sreality", every_h=6.6)]
    _, conn = _check(walks=walks)
    params, limits = _params(conn, dl._PRECISION_SQL), _params(conn, dl._STUCK_SQL)
    young = dict(zip(params["sources"], params["young_s"]))
    assert params["sources"] == sorted(SOURCES) and params["span_s"] == 24 * _H
    assert young["idnes"] == pytest.approx(19.4 * _H) and young["sreality"] == pytest.approx(
        14.2 * _H)
    for source, limit in zip(limits["sources"], limits["limits_s"]):
        assert young[source] + dl._EFFECTIVE_DWELL_S == limit
    assert max(young.values()) + params["span_s"] < 50 * _H


def test_the_steady_state_reads_ok_on_the_cohorts_lower_bound() -> None:
    """10-22 16:32Z: sreality 144 of 1,684 flipped in its window are active again (LB 7.3 %)."""
    out, _ = _check(precision=[("sreality", 1_684, 144)])
    c = _cells(out)["sreality/precision"]
    assert c["status"] == "ok" and c["value"] == 8.6 and c["unit"] == "%"
    assert c["facts"]["window"] == "13-37 h" and c["facts"]["lower_bound"] == pytest.approx(0.073,
                                                                                         abs=1e-3)
    assert c["message"] == ("144 of 1,684 ads made inactive 13-37 h ago (from the age a "
                            "still-listed ad is sighted again by) are active again (8.6%, lower "
                            "bound 7.3%)")


def test_a_thirty_percent_revived_cohort_is_a_problem() -> None:
    """Replay (h): the blink cohorts read 26-49 % active again within hours of their flip."""
    out, _ = _check(precision=[("sreality", 1_000, 300)])
    c = _cells(out)["sreality/precision"]
    assert c["status"] == "fail" and "300 of 1,000 ads made inactive 13-37 h ago" in c["message"]


def test_the_10_13_tail_reads_watch_on_its_lower_bound() -> None:
    """284 of 1,718 (16.5 %): its Wilson lower bound, ~14.9 %, sits under the Problem line."""
    out, _ = _check(precision=[("sreality", 1_718, 284)])
    assert _cells(out)["sreality/precision"]["status"] == "warn"


def test_a_portal_under_fifty_flips_in_its_window_is_not_measured_never_pooled() -> None:
    out, _ = _check(precision=[("remax", 49, 11)])
    cells = _cells(out)
    assert cells["remax/precision"]["status"] == "unknown"
    assert cells["remax/precision"]["message"] == (
        "Not measured: 49 ads made inactive 13-37 h ago (from the age a still-listed ad is "
        "sighted again by), under the 50 this arm needs")
    assert cells["maxima/precision"]["message"].startswith("Not measured: 0 ads made inactive ")


# --- the result -----------------------------------------------------------------------------------


def test_every_portal_of_the_fleet_gets_every_arm_whatever_its_ledgers_hold() -> None:
    """The portal list is SOURCES, as the other three checks': a portal with nothing in any
    ledger keeps its five cells (Not measured or OK) instead of dropping out of the check."""
    out, _ = _check(walks=_walks("sreality"), outflow=_outflow("bazos", 1_300, 1_400, 1_620,
                                                              1_380, 1_510),
                    verify=[("idnes", 3, 0, 60.0)], precision=[("realitymix", 691, 3)])
    cells = _cells(out)
    for portal in SOURCES:
        for arm in ("outflow", "stuck", "verify_backlog", "deferrals", "precision"):
            assert cells[f"{portal}/{arm}"]["portal"] == portal, (portal, arm)
    assert len(cells) == 5 * len(SOURCES)
    assert cells["maxima/outflow"]["status"] == "unknown"
    assert cells["maxima/verify_backlog"]["status"] == "ok"


def test_the_result_is_the_worst_measured_cell_and_counts_the_cells_at_it() -> None:
    out, _ = _check(outflow=[*_outflow("sreality", 0, 2_883, 2_951, 2_960, 3_944),
                             *_outflow("bazos", 0, 1_400, 1_620, 1_380, 1_510)],
                    verify=[("idnes", 3, 0, 7 * _H)])
    assert out["check_key"] == "delisting" and out["status"] == "fail" and out["value"] == 2
    assert out["message"].startswith("2 Problem — ")


def test_every_statement_runs_on_the_per_check_clock() -> None:
    _, conn = _check(stuck=[("sreality", 1, 0, 60.0)])
    assert {sql for sql, _, _ in conn.sent} == {dl._WALKS_SQL, dl._OUTFLOW_SQL, dl._VERIFY_SQL,
                                                 dl._PRECISION_SQL, dl._STUCK_SQL}
    assert all(armed for _, _, armed in conn.sent)


# --- rails -------------------------------------------------------------------------------------


def test_the_module_never_imports_the_harness_or_the_worker() -> None:
    """The harness imports this check, so importing it back is a cycle; the worker is a lane."""
    tree = ast.parse(inspect.getsource(dl))
    imported = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    imported |= {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert not imported & {"scripts.verify_pipeline", "scraper.realtime_worker",
                           "toolkit.system_alerts"}


def test_the_check_reads_exactly_the_thresholds_the_contract_names() -> None:
    read = set(re.findall(r"""thresholds\[["'](\w+)["']\]""", inspect.getsource(dl)))
    assert read == set(T)


def test_the_harness_declares_every_threshold_this_check_reads_with_the_contracts_value() -> None:
    from scripts.verify_pipeline import THRESHOLDS

    assert {key: THRESHOLDS.get(key) for key in T} == T
