"""Hermetic tests for the wave-6 gauges (scripts.checks.quality: data_quality,
location_town_coverage, text_extraction) and the gauge trend every gauge cell carries
(scripts.checks.common.trend)."""

# No DB: the platform tests' fake connection answers each statement by the SQL constant it
# carries, through the per-check clock. At build time (2026-10-19) every new statement ran
# EXPLAIN ANALYZE read-only against production: the location read 3.1 s, the URL read 0.38 s,
# the text flow 16 ms and its stock 1.8 s, the trend source 0.35 ms and its lookback 1.3 ms.

from __future__ import annotations

import ast
import datetime as _dt
import inspect
import re
from typing import Any

import pytest

from location_data.claims_common import SERVED_LOCATION_PREDICATE, SOURCES
from location_data.resolver.version import RESOLVER_VERSION
from scripts.checks import clock, common
from scripts.checks import publishing as pub
from scripts.checks import quality as qc
from tests.scripts.test_checks_platform import _Conn
from tests.scripts.test_checks_platform_replay import _incidents, _told
from toolkit import description_extraction as text_lane

_UTC = _dt.timezone.utc
_NOW = _dt.datetime(2026, 10, 19, 14, 0, tzinfo=_UTC)
_TODAY = "2026-10-19"

# The gauges' keys exactly as the wave-6 contract and the harness spell them; a rail below pins
# the harness's values to these.
T: dict[str, Any] = {
    "data_quality_floor_warn": 0.05, "data_quality_floor_fail": 0.10,
    "data_quality_floor_min_n": 100, "data_quality_divergence_warn": 0.20,
    "data_quality_divergence_fail": 0.40, "data_quality_divergence_min_n": 100,
    "data_quality_estate_sum_warn": 0.05, "data_quality_estate_sum_fail": 0.10,
    "data_quality_estate_sum_min_n": 100, "data_quality_url_warn": 50,
    "data_quality_url_fail": 500, "data_quality_measure_gap_warn": 0.95,
    "data_quality_measure_gap_min_n": 200, "location_registry_max_age_days": 40,
    "location_registry_max_staged_hours": 24, "text_extraction_flow_warn": 0.05,
    "text_extraction_flow_fail": 0.25, "text_extraction_flow_min_n": 30,
    "text_extraction_eta_warn_d": 14,
}


def _cells(result: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {c["subject"]: c for c in result["details"]["cells"]}


def _in_pass(fn: Any, *args: Any) -> Any:
    """Run `fn` inside a health pass, as run_checks does: the pass memo on, then off."""
    clock.PASS.start()
    try:
        return fn(*args)
    finally:
        clock.PASS.reset()


# --- the gauge trend (common.trend, D11) -----------------------------------------------------


def _stored(subject: str, *points: list[Any], status: str = "ok") -> dict[str, Any]:
    """A cell of the gauge's newest measured result carrying its series."""
    return {"subject": subject, "status": status, "value": 1, "facts": {"trend": list(points)}}


def _trend(cells: list[Any] | None, subject: str = "url/bazos", value: Any = 3,
           status: str = "ok", at: _dt.datetime = _NOW,
           lookback: list[tuple[Any, ...]] | None = None) -> tuple[dict[str, Any], _Conn]:
    answers: dict[str, Any] = {common.TREND_LOOKBACK_SQL: lookback or []}
    if cells is not None:
        answers[common.TREND_SQL] = [(cells,)]
    conn = _Conn(answers, now=at)
    return common.trend(conn, "data_quality", subject, value, status, at), conn


def test_a_first_reading_starts_the_series_with_todays_point() -> None:
    out, conn = _trend(None)
    assert out == {"trend": [[_TODAY, 3, "ok"]], "worse_7d": None}
    assert [sql for sql, _, _ in conn.sent] == [common.TREND_SQL], "nothing older to look back on"


def test_the_series_rolls_on_from_the_newest_measured_result() -> None:
    held = _stored("url/bazos", ["2026-10-17", 1, "ok"], ["2026-10-18", 60, "warn"])
    out, conn = _trend([held, "junk", None], value=700, status="fail")
    assert out["trend"] == [["2026-10-17", 1, "ok"], ["2026-10-18", 60, "warn"],
                            [_TODAY, 700, "fail"]]
    assert conn.sent == [(common.TREND_SQL, {"key": "data_quality"}, True)]


def test_todays_point_replaces_the_days_earlier_one() -> None:
    held = _stored("url/bazos", ["2026-10-18", 2, "ok"], [_TODAY, 60, "warn"])
    out, _ = _trend([held], value=3, status="ok")
    assert out["trend"] == [["2026-10-18", 2, "ok"], [_TODAY, 3, "ok"]]


def test_an_unmeasured_cell_carries_its_series_unchanged() -> None:
    """Today's earlier point stays: an unknown reading never replaces a measured one."""
    held = _stored("url/bazos", ["2026-10-12", 2, "ok"], [_TODAY, 60, "warn"])
    out, _ = _trend([held], value=None, status="unknown")
    assert out == {"trend": [["2026-10-12", 2, "ok"], [_TODAY, 60, "warn"]], "worse_7d": None}


def test_the_series_keeps_thirty_prague_days_and_drops_what_is_not_a_point() -> None:
    held = _stored("url/bazos", ["2026-09-19", 1, "ok"], ["2026-09-20", 2, "ok"],
                   ["not a day", 3, "ok"], ["2026-10-01", 4, "unknown"], ["2026-10-02", 5],
                   ["2026-10-03", 6, ["ok"]], "2026-10-04", ["2026-10-25", 7, "ok"])
    out, _ = _trend([held])
    assert out["trend"] == [["2026-09-20", 2, "ok"], [_TODAY, 3, "ok"]]


def test_the_day_is_the_prague_day_not_the_utc_one() -> None:
    late = _dt.datetime(2026, 10, 19, 22, 30, tzinfo=_UTC)  # 00:30 in Prague (CEST)
    out, _ = _trend(None, at=late)
    assert out["trend"] == [["2026-10-20", 3, "ok"]]


@pytest.mark.parametrize("then, now_status, worse", [
    ("ok", "warn", True), ("warn", "fail", True), ("warn", "warn", False),
    ("fail", "ok", False), (None, "fail", None)])
def test_worse_7d_compares_todays_status_with_the_point_a_week_back(then: str | None,
                                                                   now_status: str,
                                                                   worse: bool | None) -> None:
    points = [["2026-10-11", 9, "fail"], ["2026-10-13", 9, "fail"]]
    if then:
        points.append(["2026-10-12", 1, then])
    out, _ = _trend([_stored("url/bazos", *points)], status=now_status)
    assert out["worse_7d"] is worse


def test_a_subject_the_newest_result_lacks_is_looked_up_once_for_every_such_subject() -> None:
    """An arm unread on the last run, or a unit back after a gap, keeps its series: the
    newest measured result that carries it. One lookback per check, on the first miss."""
    newest = [_stored("url/bazos", [_TODAY, 1, "ok"])]
    lookback = [("floor/bazos/byt-prodej", [["2026-10-18", 4.0, "ok"]]),
                ("floor/idnes/byt-prodej", "not a series")]
    conn = _Conn({common.TREND_SQL: [(newest,)], common.TREND_LOOKBACK_SQL: lookback}, now=_NOW)

    def read() -> list[dict[str, Any]]:
        return [common.trend(conn, "data_quality", s, 5.0, "ok", _NOW)
                for s in ("url/bazos", "floor/bazos/byt-prodej", "floor/idnes/byt-prodej")]

    url, floor, gone = _in_pass(read)
    assert url["trend"] == [[_TODAY, 5.0, "ok"]]
    assert floor["trend"] == [["2026-10-18", 4.0, "ok"], [_TODAY, 5.0, "ok"]]
    assert gone["trend"] == [[_TODAY, 5.0, "ok"]]
    assert [sql for sql, _, _ in conn.sent] == [common.TREND_SQL, common.TREND_LOOKBACK_SQL]
    (params,) = [p for sql, p, _ in conn.sent if sql == common.TREND_LOOKBACK_SQL]
    assert params == {"key": "data_quality", "since": _NOW - _dt.timedelta(days=30),
                      "known": ["url/bazos"]}


def test_only_measured_results_feed_a_series_so_an_unknown_run_never_resets_one() -> None:
    flat = " ".join(common.TREND_SQL.split())
    assert "status <> 'unknown'" in flat and "jsonb_typeof(details -> 'cells') = 'array'" in flat
    assert flat.endswith("order by run_at desc limit 1")
    back = " ".join(common.TREND_LOOKBACK_SQL.split())
    assert "r.status <> 'unknown'" in back and "r.run_at >= %(since)s" in back
    assert "jsonb_typeof(c -> 'facts' -> 'trend') = 'array'" in back


def test_a_pass_reads_a_gauges_series_once_and_a_second_pass_reads_it_again() -> None:
    """The source is memoised per check in clock.PASS: one read a pass, rebuilt every pass."""
    conn = _location_conn([_town("bazos", ads=100)])
    for _ in range(2):
        _in_pass(qc.check_location_town_coverage, conn, T)
    assert sum(sql == common.TREND_SQL for sql, _, _ in conn.sent) == 2
    qc.check_location_town_coverage(conn, T)
    assert sum(sql == common.TREND_SQL for sql, _, _ in conn.sent) == 2 + len(SOURCES) + 1


def test_alarms_carry_no_trend() -> None:
    """with_trends is a gauge's call: no alarm module calls it."""
    from scripts.checks import dedup, delisting, platform, scraping

    for module in (dedup, delisting, platform, pub):
        assert "with_trends" not in inspect.getsource(module)
    assert inspect.getsource(scraping).count("with_trends(") == 1, "coverage only"


# --- data_quality ----------------------------------------------------------------------------


def _row(source: str, main: str | None, kind: str | None, *,
         basis: str | None = "rent_monthly_czk_m2", **values: Any) -> tuple[Any, ...]:
    """A PLAUSIBILITY_SQL row: the named view columns, every other one empty."""
    named = {"source": source, "category_main": main, "category_type": kind,
             "price_per_m2_basis": basis, **values}
    return tuple(named.get(column) for column in qc.PLAUSIBILITY_COLUMNS)


def _floor_row(source: str, main: str, kind: str, n: int, share: float, n_7d: int = 0,
               share_7d: float | None = None) -> tuple[Any, ...]:
    return _row(source, main, kind, n_floor_eligible=n, floor_null_share=share,
                n_floor_eligible_7d=n_7d, floor_null_share_7d=share_7d)


def _url_counts(**overrides: tuple[int, int, int]) -> list[tuple[Any, ...]]:
    """_URL_SQL rows: 1,000 active ads with a link on every portal unless overridden."""
    return [(s, *overrides.get(s, (1_000, 0, 0))) for s in sorted(SOURCES)]


def _quality(rows: list[tuple[Any, ...]] | Exception, *,
             urls: list[tuple[Any, ...]] | None = None,
             trend: list[Any] | None = None) -> tuple[dict[str, Any], _Conn]:
    conn = _Conn({qc.PLAUSIBILITY_SQL: rows, qc._URL_SQL: _url_counts() if urls is None else urls,
                  common.TREND_SQL: [] if trend is None else [(trend,)]}, now=_NOW)
    return qc.check_data_quality(conn, T), conn


# Live 2026-10-19 13:14Z: the four floor cells over a Watch lower bound on the stock.
_LIVE_FLOOR = [
    _floor_row("realitymix", "komercni", "pronajem", 4_140, 0.114, 300, 0.02),
    _floor_row("realitymix", "pozemek", "pronajem", 131, 0.137),
    _floor_row("remax", "komercni", "pronajem", 281, 0.096),
    _floor_row("bazos", "komercni", "pronajem", 2_787, 0.063, 455, 0.073),
    _floor_row("idnes", "komercni", "pronajem", 8_444, 0.0054),
]


def test_data_quality_reads_problem_at_ship_on_realitymix_commercial_rentals() -> None:
    """D8: 11.4 % of 4,140 priced ads NULLed by the floor, lower bound 10.5 % > 10 %: a real
    standing condition, the expected ship-time red, not a line to re-size."""
    out, _ = _quality(_LIVE_FLOOR)
    cells = _cells(out)
    red = cells["floor/realitymix/komercni-pronajem"]
    assert (red["status"], red["value"], red["unit"], red["portal"]) == (
        "fail", 11.4, "%", "realitymix")
    assert red["facts"]["lower_bound"] == 0.1047 and red["facts"]["worst"] == "stock"
    assert red["message"].startswith("472 of 4,140 priced ads with an area lose their per-m² "
                                     "price to the floor (11.4%; lower bound 10.5%) (Problem "
                                     "above 10%) — a per-m² unit price is written as the price")
    assert cells["floor/realitymix/pozemek-pronajem"]["status"] == "warn"  # 13.7 %, LB 8.9 %
    assert cells["floor/remax/komercni-pronajem"]["status"] == "warn"  # 9.6 %, LB 6.7 %
    assert cells["floor/bazos/komercni-pronajem"]["status"] == "warn"  # both arms over 5 %
    assert cells["floor/idnes/komercni-pronajem"]["status"] == "ok"
    assert (out["check_key"], out["status"], out["value"]) == ("data_quality", "fail", 1)


@pytest.mark.parametrize("row, legacy, now_status", [
    (_floor_row("ceskereality", "komercni", "pronajem", 5_656, 0.2003, 169, 0.112), "fail", "fail"),
    (_floor_row("realitymix", "komercni", "pronajem", 4_500, 0.1896, 85, 0.094), "fail", "fail"),
    (_floor_row("realitymix", "pozemek", "pronajem", 115, 0.1565), "fail", "fail"),
    (_floor_row("remax", "komercni", "pronajem", 275, 0.1127), "fail", "warn"),
    (_floor_row("bazos", "komercni", "pronajem", 2_689, 0.0666, 436, 0.067), "warn", "warn"),
    (_floor_row("idnes", "komercni", "pronajem", 8_444, 0.0054), "ok", "ok")])
def test_the_floor_folds_the_legacy_cells_judged_on_the_lower_bound(row: tuple[Any, ...],
                                                                    legacy: str,
                                                                    now_status: str) -> None:
    """ppm2_basis_floor_share's 2026-08-25 fixture, cell for cell. Pinned separately: the raw
    share moved to its Wilson lower bound, so remax's 11.3 % of 275 (LB 8.1 %) reads Watch where
    the legacy check failed it; pozemek is scored, as it was."""
    (floor,) = [c for c in _quality([row])[0]["details"]["cells"]
                if c["subject"].startswith("floor/")]
    assert floor["status"] == now_status


def test_the_new_ads_arm_of_the_floor_decides_on_its_own_and_is_the_value() -> None:
    """A regression shipped this week is most of what arrived since, a sliver of the stock."""
    out, _ = _quality([_floor_row("maxima", "komercni", "pronajem", 4_000, 0.004, 300, 0.98)])
    cell = _cells(out)["floor/maxima/komercni-pronajem"]
    assert cell["status"] == "fail" and cell["value"] == 98.0 and cell["facts"]["worst"] == "7d"
    assert "first written in 7 d" in cell["message"]


def test_a_floor_cell_under_its_minimum_reads_not_measured_and_names_its_counts() -> None:
    out, _ = _quality([_floor_row("maxima", "komercni", "pronajem", 99, 0.5, 12, 1.0)])
    cell = _cells(out)["floor/maxima/komercni-pronajem"]
    assert cell["status"] == "unknown" and cell["value"] is None
    assert cell["message"] == ("Not measured: 99 priced ads with an area, 12 of them first "
                               "written in 7 d: under the 100 an arm needs")
    assert cell["facts"]["basis"] == "rent_monthly_czk_m2"


def test_a_category_with_no_decidable_basis_has_no_floor_and_no_measure_gap_cell() -> None:
    """Undecidable is the specified answer there (no measure by charter), not a gap."""
    out, _ = _quality([_row("realitymix", None, None, basis=None, n_active=189),
                       _floor_row("realitymix", None, "prodej", 150, 0.0)])
    subjects = set(_cells(out))
    assert "floor/realitymix/?-prodej" in subjects and "measure_gap/realitymix/?-prodej" in subjects
    assert not [s for s in subjects if s.endswith("/?-?")]


_DIVERGENT = _row("mmreality", "dum", "prodej", n_area_pairs=3_588, area_divergence_share=0.9967,
                  n_area_pairs_7d=113, area_divergence_share_7d=1.0)


def test_divergence_is_a_cell_per_portal_judged_per_category_and_land_is_skipped() -> None:
    """area_vs_usable_divergence's live 2026-08-25 dum cells: mmreality's plot as its headline
    is a Problem; land, where the headline IS the plot, never scores."""
    out, _ = _quality([
        _DIVERGENT,
        _row("sreality", "dum", "prodej", n_area_pairs=19_082, area_divergence_share=0.0,
             n_area_pairs_7d=420, area_divergence_share_7d=0.0),
        _row("sreality", "pozemek", "prodej", n_area_pairs=5_000, area_divergence_share=1.0),
        _row("bazos", "dum", "prodej", n_area_pairs=0, area_divergence_share=None)])
    cells = _cells(out)
    mm = cells["divergence/mmreality"]
    assert (mm["status"], mm["value"], mm["portal"]) == ("fail", 99.67, "mmreality")
    assert mm["facts"]["offenders"] == ["dum-prodej", "dum-prodej/7d"]
    assert "scraper/area.derive_headline_area" in mm["message"]
    assert cells["divergence/sreality"]["status"] == "ok"
    assert cells["divergence/sreality"]["facts"]["measured"] == 2, "land not scored"
    assert cells["divergence/bazos"]["message"] == (
        "Not measured: no category but land carries 100 ads with both areas")
    assert {f"divergence/{s}" for s in SOURCES} <= set(cells)


def test_the_estate_sum_is_its_own_cell_per_portal_with_its_own_lines() -> None:
    out, _ = _quality([_DIVERGENT, _row("mmreality", "dum", "prodej", n_estate_triples=1_123,
                                        estate_sum_share=0.026),
                       _row("remax", "dum", "prodej", n_estate_triples=200, estate_sum_share=0.15),
                       _row("remax", "pozemek", "prodej", n_estate_triples=900,
                            estate_sum_share=0.9)])
    cells = _cells(out)
    assert cells["estate_sum/mmreality"]["status"] == "ok"  # 2.6 % of 1,123, LB 1.8 %
    remax = cells["estate_sum/remax"]
    assert (remax["status"], remax["value"]) == ("fail", 15.0)  # LB 10.7 %; land skipped
    assert "their sum as the estate area" in remax["message"]


@pytest.mark.parametrize("no_url, status", [(3, "ok"), (50, "ok"), (51, "warn"), (120, "warn"),
                                            (500, "warn"), (501, "fail"), (5_000, "fail")])
def test_ads_with_no_portal_link_are_a_count_per_portal(no_url: int, status: str) -> None:
    """outbound_url_coverage's counts per portal. Pinned boundary change: graded's strict `>`,
    so 50 is OK and 500 Watch where the legacy `>=` warned and failed."""
    out, _ = _quality([], urls=_url_counts(sreality=(108_408, no_url, 0)))
    cell = _cells(out)["url/sreality"]
    assert (cell["status"], cell["value"], cell["unit"]) == (status, no_url, "ads")
    assert cell["message"].startswith(f"{no_url:,} of 108,408 active ads have no portal link")


def test_a_portal_with_no_active_ad_has_no_link_verdict_and_a_clean_one_says_so() -> None:
    out, _ = _quality([], urls=[r for r in _url_counts() if r[0] != "maxima"])
    cells = _cells(out)
    assert cells["url/maxima"]["status"] == "unknown"
    assert cells["url/bazos"]["message"] == "every one of 1,000 active ads has its portal link"


@pytest.mark.parametrize("n, share, status", [
    (27_174, 1.0, "warn"), (1_643, 0.995, "warn"), (1_000, 0.95, "ok"), (304, 0.865, "ok"),
    (199, 1.0, "unknown")])
def test_the_stocks_measure_gap_is_watch_only(n: int, share: float, status: str) -> None:
    """ppm2_measure_coverage's stock arm, moved here (D6): Watch on the lower bound, never a
    Problem. Pinned change: the legacy `>=` on the raw share warned at exactly 95 %."""
    out, _ = _quality([_row("sreality", "pozemek", "prodej", basis="sale_capital_czk_m2",
                            n_active=n, measure_input_gap_share=share)])
    cell = _cells(out)["measure_gap/sreality/pozemek-prodej"]
    assert cell["status"] == status
    if status == "warn":
        assert "(Watch above 95%) — a standing gap, never a Problem" in cell["message"]


def test_an_unreadable_measure_view_leaves_the_link_cells_measured() -> None:
    """The view's error is the read's result this pass: its per-portal cells read Not
    measured with the reason, its category cells cannot be named, the links are still read."""
    out, _ = _quality(RuntimeError("relation \"measure_plausibility_by_source\" does not exist"),
                      urls=_url_counts(sreality=(108_408, 120, 0)))
    cells = _cells(out)
    assert not [s for s in cells if s.startswith(("floor/", "measure_gap/"))]
    assert cells["divergence/bazos"]["status"] == "unknown"
    assert "measure_plausibility_by_source is unreadable" in cells["estate_sum/idnes"]["message"]
    assert out["status"] == "warn" and "measure view" in out["details"]["not_measured"]
    nothing, _ = _quality([], urls=[])
    assert nothing["status"] == "unknown" and nothing["details"]["cells"]


def test_data_quality_shares_the_measure_read_with_parsing_inside_a_pass() -> None:
    """One read a pass (clock.PASS): parsing reads it first, data_quality reuses it."""
    conn = _Conn({qc.PLAUSIBILITY_SQL: _LIVE_FLOOR, qc._URL_SQL: _url_counts()}, now=_NOW)

    def both() -> dict[str, Any]:
        qc._plausibility(conn)
        return qc.check_data_quality(conn, T)

    assert _in_pass(both)["status"] == "fail"
    assert sum(sql == qc.PLAUSIBILITY_SQL for sql, _, _ in conn.sent) == 1


def test_every_data_quality_cell_carries_its_trend() -> None:
    held = [_stored("url/sreality", ["2026-10-12", 0, "ok"])]
    out, _ = _quality(_LIVE_FLOOR, urls=_url_counts(sreality=(108_408, 120, 0)), trend=held)
    cells = _cells(out)
    assert all({"trend", "worse_7d"} <= set(c["facts"]) for c in cells.values())
    url = cells["url/sreality"]["facts"]
    assert (url["trend"], url["worse_7d"]) == ([["2026-10-12", 0, "ok"], [_TODAY, 120, "warn"]],
                                               True)


def test_the_ship_time_floor_red_opens_one_incident_and_watch_cells_none() -> None:
    """A gauge rings once when a cell first reads Problem; its Watch cells open nothing."""
    results = [(_NOW + _dt.timedelta(hours=6 * k), _quality(_LIVE_FLOOR)[0]) for k in range(3)]
    ladder = _told("data_quality", 21_600, results, cls="gauge")
    opened = _incidents(ladder, "data_quality")
    assert [i["subject"] for i in opened] == ["floor/realitymix/komercni-pronajem"]


# --- location_town_coverage ----------------------------------------------------------------


def _town(source: str, *, ads: int = 50_000, no_row: int = 0, cz_no_town: int = 0,
          hidden: int = 0, no_ku: int = 0) -> tuple[Any, ...]:
    """A _LOCATION_TOWN_COVERAGE_SQL row; `town` is every ad with a row and a town."""
    return (source, ads, no_row, cz_no_town, ads - no_row - cz_no_town, hidden, no_ku)


_REGISTRY = ("ruian:2026-09-30", 19, None, None)


def _location_conn(rows: list[tuple[Any, ...]],
                   registry: tuple[Any, ...] | None = _REGISTRY) -> _Conn:
    return _Conn({qc._LOCATION_TOWN_COVERAGE_SQL: rows,
                  qc._REGISTRY_LIVENESS_SQL: [] if registry is None else [registry]}, now=_NOW)


def _location(rows: list[tuple[Any, ...]],
              registry: tuple[Any, ...] | None = _REGISTRY) -> tuple[dict[str, Any], _Conn]:
    conn = _location_conn(rows, registry)
    return qc.check_location_town_coverage(conn, T), conn


def test_the_location_gauge_reads_problem_per_portal_until_zero() -> None:
    """Live 2026-10-19 13:09Z: 43 active ads past 24 h with no town, three portals red."""
    rows = [_town("ceskereality", ads=48_758, cz_no_town=39, hidden=15),
            _town("realitymix", ads=49_048, cz_no_town=3, hidden=1),
            _town("mmreality", ads=10_505, cz_no_town=1, hidden=1),
            _town("bazos", ads=52_213)]
    out, conn = _location(rows)
    cells = _cells(out)
    red = cells["ceskereality/town"]
    assert (red["status"], red["value"], red["unit"], red["portal"]) == (
        "fail", 39, "ads", "ceskereality")
    assert red["message"] == ("39 of 48,758 active ads first written more than 24 h ago have no "
                              "town: 0 with no location row, 39 not foreign with no town (rule 25: "
                              "red until zero)")
    assert red["facts"]["hidden"] == 15 and red["facts"]["town"] == 48_719
    assert cells["bazos/town"]["message"] == (
        "every one of 52,213 active ads first written more than 24 h ago has a town")
    assert cells["idnes/town"]["status"] == "unknown"  # no row read: nothing past the edge
    assert {f"{s}/town" for s in SOURCES} | {"registry"} == set(cells)
    assert (out["status"], out["value"]) == ("fail", 3)
    assert conn.sent and all(armed for _, _, armed in conn.sent)


def test_the_location_gauge_folds_the_legacy_per_portal_counts() -> None:
    """location_town_coverage's own fixture, as cells: no row and Czech-without-a-town add up,
    and the hidden ads are a fact that never moves a verdict."""
    out, _ = _location([_town("bazos", ads=40_000, cz_no_town=120, hidden=120),
                        _town("idnes", ads=30_000, no_row=900, cz_no_town=11_000, hidden=11_900),
                        _town("sreality", ads=200_000)])
    cells = _cells(out)
    assert (cells["idnes/town"]["status"], cells["idnes/town"]["value"]) == ("fail", 11_900)
    assert cells["bazos/town"]["value"] == 120 and cells["sreality/town"]["status"] == "ok"
    assert cells["sreality/town"]["facts"]["town_share"] == 1.0


def test_a_czech_address_row_with_no_ku_is_a_problem_that_stays_out_of_the_value() -> None:
    out, _ = _location([_town("bezrealitky", ads=20_000, no_ku=37)])
    cell = _cells(out)["bezrealitky/town"]
    assert (cell["status"], cell["value"]) == ("fail", 0)
    assert cell["message"] == (f"37 Czech ads at address grain have no cadastral area (KÚ) at "
                               f"{RESOLVER_VERSION}: the registry's KÚ geometry is missing at its "
                               "version")


def test_the_location_population_is_active_ads_past_the_edge_publishing_ends_at() -> None:
    """D9: the inactive stock is not read, and the 24 h edge with publishing's visible arm is
    one number, NEW_AD_H, imported by both."""
    _, conn = _location([_town("bazos")])
    (params,) = [p for sql, p, _ in conn.sent if sql == qc._LOCATION_TOWN_COVERAGE_SQL]
    assert params == {"resolver_version": RESOLVER_VERSION, "new_ad_h": common.NEW_AD_H}
    assert pub._WINDOW_H == common.NEW_AD_H == 24
    assert "_WINDOW_H = NEW_AD_H" in inspect.getsource(pub)
    flat = " ".join(qc._LOCATION_TOWN_COVERAGE_SQL.split())
    assert ("WHERE l.is_active AND l.first_seen_at < now() - make_interval(hours => "
            "%(new_ad_h)s::int)") in flat
    assert "first_seen_at >= now() - make_interval(hours => %(window_h)s::int)" in " ".join(
        pub._VISIBLE_SQL.split())


def test_the_location_counts_read_the_shared_predicates() -> None:
    """Undetermined and disputed count as Czech; hidden is the consumer rule negated; the KÚ
    arm reads the rank table, the current resolver and the current registry's excuse."""
    flat = " ".join(qc._LOCATION_TOWN_COVERAGE_SQL.split())
    assert f"NOT {SERVED_LOCATION_PREDICATE}" in qc._LOCATION_TOWN_COVERAGE_SQL
    assert "AS hidden_n" in qc._LOCATION_TOWN_COVERAGE_SQL
    assert "ll.country_status <> 'foreign' AND ll.obec_kod IS NULL" in flat
    assert ("gr.is_address_grain AND ll.resolver_version = %(resolver_version)s AND "
            "ll.katastr_kod IS NULL") in flat
    # A town-less row is the town arm's alone: NULL NOT IN an empty degenerate set is true.
    assert "ll.katastr_kod IS NULL AND ll.obec_kod IS NOT NULL AND ll.obec_kod NOT IN (" in flat
    assert "d.discrepancy = 'degenerate_boundary_geometry'" in flat


@pytest.mark.parametrize("registry, status, said", [
    (_REGISTRY, "ok", "the current registry ruian:2026-09-30 is 19 d old, nothing staged"),
    (("ruian:2026-09-30", 40, None, None), "ok", "is 40 d old, nothing staged"),
    (("ruian:2026-09-30", 41, None, None), "fail", "is 41 d old (Problem past 40 d)"),
    (("ruian:2026-09-30", 19, "ruian:2026-10-31", 24.0), "ok", "staged 24 h ago"),
    (("ruian:2026-09-30", 19, "ruian:2026-10-31", 25.0), "fail",
     "ruian:2026-10-31 has sat staged 25 h without becoming current (Problem past 24 h)"),
    (None, "fail", "no current RÚIAN registry version")])
def test_a_stalled_registry_is_a_problem(registry: tuple[Any, ...] | None, status: str,
                                         said: str) -> None:
    """D9's stated deviation: Problem, not the design's Watch, since a superseded load is
    cancelled, not failed, and no other check sees it. Strict `>` as before."""
    cell = _cells(_location([_town("bazos")], registry)[0])["registry"]
    assert cell["status"] == status and said in cell["message"]
    assert cell["portal"] is None


# --- text_extraction -----------------------------------------------------------------------


_MODEL = "gpt-5.6-luna"
_FLOW_LIVE = ("bazos", 876, 876, 0, 49.1)
_STOCK_LIVE = (55_236, 103, 12_745)


def _text(flow: list[tuple[Any, ...]] | None = None, stock: tuple[Any, ...] | None = _STOCK_LIVE,
          *, model: str | None = _MODEL,
          located: list[str] | None = None) -> tuple[dict[str, Any], _Conn]:
    conn = _Conn({text_lane.MODEL_SQL: [] if model is None else [(model,)],
                  text_lane.LLM_TEXT_SOURCES_SQL: [(s,) for s in (
                      ["bazos"] if located is None else located)],
                  text_lane.EXTRACTION_LATENCY_SQL: [_FLOW_LIVE] if flow is None else flow,
                  text_lane.ELIGIBLE_LAG_SQL: [] if stock is None else [stock]}, now=_NOW)
    return qc.check_text_extraction(conn, T), conn


def test_the_lane_reads_every_new_ad_and_drains_its_archive_in_days_live() -> None:
    """Live 2026-10-19 14:02Z: 876 of 876 read, p95 49 min; 55,236 waiting at 12,745 a day."""
    out, _ = _text()
    cells = _cells(out)
    flow, stock = cells["flow/bazos"], cells["stock"]
    assert (flow["status"], flow["value"], flow["unit"]) == ("ok", 0.0, "%")
    assert flow["message"] == ("0 of 876 ads with a description first written 1-24 h ago have no "
                               "reading yet (0.0%; lower bound 0.0%); p95 49 min to the first "
                               "reading (the lane's SLO is 20 min)")
    assert flow["facts"]["p95_minutes"] == 49.1 and flow["facts"]["slo_minutes"] == 20
    assert (stock["status"], stock["value"], stock["unit"]) == ("ok", 4.3, "d")
    assert stock["message"].startswith("55,236 ads wait for a description reading (103 active, "
                                       "55,133 inactive: the archive's read-once re-read); 12,745 "
                                       "read in 24 h, done in at least 4.3 d")
    assert set(cells) == {"flow/bazos", "stock"} and out["status"] == "ok"


@pytest.mark.parametrize("unread, status", [(0, "ok"), (100, "warn"), (300, "fail")])
def test_the_flow_is_judged_on_the_lower_bound_of_new_ads_unread(unread: int,
                                                                  status: str) -> None:
    out, _ = _text([("bazos", 700, 700 - unread, 0, 30.0)])
    cell = _cells(out)["flow/bazos"]
    assert cell["status"] == status
    assert cell["facts"]["lower_bound"] == round(common.wilson_lower(unread, 700), 4)


def test_given_up_ads_count_as_unread_and_are_named() -> None:
    """A per-row failure the lane gives up on must not read as read (the flow counts arrivals
    with no successful reading, never through the lane's own predicate)."""
    out, _ = _text([("bazos", 700, 450, 250, None)])
    cell = _cells(out)["flow/bazos"]
    assert cell["status"] == "fail" and cell["facts"]["given_up"] == 250
    assert ", 250 given up after 5 failed readings at this version (Problem above 25%)" in (
        cell["message"])


def test_a_portal_in_scope_with_no_arrival_reads_not_measured() -> None:
    out, _ = _text([], located=["bazos", "sreality"])
    cells = _cells(out)
    assert cells["flow/sreality"]["status"] == "unknown"
    assert cells["flow/sreality"]["message"] == (
        "Not measured: no active ad with a description first written 1-24 h ago")


@pytest.mark.parametrize("arrived, read, status", [
    (2, 0, "unknown"), (29, 0, "unknown"), (30, 0, "fail"), (30, 30, "ok")])
def test_the_flow_judges_from_thirty_arrivals(arrived: int, read: int, status: str) -> None:
    """A small portal's two new ads both refused have a lower bound of 34 % (wilson_lower(2, 2)), a
    Problem with its notice and daily reminders: under text_extraction_flow_min_n the flow is Not
    measured, as every other arm under its minimum is, and its counts stay facts."""
    out, _ = _text([("maxima", arrived, read, 0, None)], located=["maxima"])
    cell = _cells(out)["flow/maxima"]
    assert cell["status"] == status
    assert (cell["facts"]["arrived"], cell["facts"]["unread"]) == (arrived, arrived - read)
    if status == "unknown":
        assert cell["message"] == (f"Not measured: {arrived} active ads with a description first "
                                   "written 1-24 h ago, under the 30 this arm needs")
        assert "lower_bound" not in cell["facts"]
        low = common.wilson_lower(arrived, arrived)
        assert common.graded(low, T["text_extraction_flow_warn"],
                             T["text_extraction_flow_fail"]) == "fail", "what the gate spares"


def test_the_flow_counts_arrivals_and_a_version_change_is_not_a_stall() -> None:
    """Read = any reading at this SCHEMA_VERSION (a prompt or model change re-reads, it does not
    re-open the flow); arrivals are counted outside `_eligible_where`, so a predicate that
    selects nothing cannot hide them (2026-07)."""
    sql = text_lane.EXTRACTION_LATENCY_SQL
    assert text_lane._eligible_where(text_lane._OPEN) not in sql
    assert f"e.extractor_version LIKE '{text_lane.SCHEMA_VERSION}:%%'" in sql
    assert "g.extractor_version = %(version)s" in sql
    assert f">= {text_lane.GIVE_UP_AFTER}" in sql and "percentile_cont(0.95)" in sql
    _, conn = _text()
    (params,) = [p for s, p, _ in conn.sent if s == sql]
    assert params == {"version": text_lane.extractor_version(_MODEL), "window_h": 24,
                      "grace_h": 1}


@pytest.mark.parametrize("stock, status, said", [
    ((55_236, 103, 3_000), "warn", "done in at least 18.4 d (Watch past 14 d"),
    ((500, 20, 0), "fail", "nothing read in 24 h while active ads wait: the lane has stopped"),
    ((500, 0, 0), "warn", "(Watch past 14 d; nothing read in 24 h"),
    ((0, 0, 12_000), "ok", "no ad waits for a description reading; 12,000 read in 24 h"),
    ((14_000, 0, 1_000), "ok", "done in at least 14.0 d")])
def test_the_stock_is_judged_on_its_eta(stock: tuple[int, int, int], status: str,
                                        said: str) -> None:
    """D10: Problem when nothing was read in 24 h while active ads wait (told by llm as well,
    accepted); Watch past a 14-day ETA, strict `>`; 0 readings with only the archive waiting
    is Watch."""
    cell = _cells(_text(stock=stock)[0])["stock"]
    assert cell["status"] == status and said in cell["message"]


def test_the_lane_switched_off_reads_ok_with_its_state() -> None:
    out, _ = _text(model=None)
    cells = _cells(out)
    assert cells["flow/bazos"]["status"] == "ok" and cells["flow/bazos"]["facts"]["state"] == "off"
    assert cells["stock"]["message"] == ("the description lane is off: app_settings."
                                         "enrichment_model names no model; nothing is read and "
                                         "nothing waits")


def test_an_empty_scope_leaves_the_stock_cell_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(text_lane.contract, "extracted_cells", lambda: {})
    out, conn = _text(located=[])
    assert set(_cells(out)) == {"stock"} and out["status"] == "ok"
    assert text_lane.EXTRACTION_LATENCY_SQL not in {s for s, _, _ in conn.sent}


def test_the_model_and_the_scope_are_the_lanes_own_reads_on_the_clock() -> None:
    """The W5 rule: the same rows through the clock with the imported SQL, never a helper that
    runs its own."""
    _, conn = _text()
    sent = [s for s, _, _ in conn.sent]
    assert any(s is text_lane.MODEL_SQL for s in sent)
    assert any(s is text_lane.LLM_TEXT_SOURCES_SQL for s in sent)
    (model_params,) = [p for s, p, _ in conn.sent if s == text_lane.MODEL_SQL]
    assert model_params == (text_lane.MODEL_SETTING,)
    assert all(armed for _, _, armed in conn.sent)


# --- every gauge ---------------------------------------------------------------------------


def _every_result() -> list[dict[str, Any]]:
    return [
        _quality(_LIVE_FLOOR)[0], _quality([], urls=_url_counts(bazos=(1_000, 120, 0)))[0],
        _quality([])[0], _quality([], urls=[])[0],
        _location([_town("bazos")])[0], _location([_town("bazos", cz_no_town=1)])[0],
        _text()[0], _text(stock=(55_236, 0, 3_000))[0], _text(stock=(9, 9, 0))[0],
        _text(model=None)[0]]


def test_every_result_of_every_gauge_carries_its_cells_on_every_status() -> None:
    """D4: no measured result reaches the ladder without cells, and every cell has its trend."""
    results = _every_result()
    assert {r["status"] for r in results} == {"ok", "warn", "fail", "unknown"}
    for r in results:
        assert isinstance(r["details"]["cells"], list) and r["details"]["cells"]
        assert all({"trend", "worse_7d"} <= set(c["facts"]) for c in r["details"]["cells"])


def test_every_statement_runs_on_the_per_check_clock() -> None:
    _, conn = _quality(_LIVE_FLOOR)
    assert {s for s, _, _ in conn.sent} == {common.CLOCK_SQL, qc.PLAUSIBILITY_SQL, qc._URL_SQL,
                                           common.TREND_SQL}
    _, location = _location([_town("bazos")])
    _, text = _text()
    assert {s for s, _, _ in text.sent} == {common.CLOCK_SQL, text_lane.MODEL_SQL,
                                           text_lane.LLM_TEXT_SOURCES_SQL,
                                           text_lane.EXTRACTION_LATENCY_SQL,
                                           text_lane.ELIGIBLE_LAG_SQL, common.TREND_SQL}
    for sent in (conn.sent, location.sent, text.sent):
        assert all(armed for _, _, armed in sent)


def _gauge_source() -> str:
    """quality.py below the gauges' marker: the half these tests own."""
    return inspect.getsource(qc).partition("# --- gauges (Q2) ---")[2]


def test_the_gauges_read_exactly_the_thresholds_the_contract_names() -> None:
    read = set(re.findall(r"""thresholds\[["'](\w+)["']\]""", _gauge_source()))
    assert read == set(T)


def test_the_harness_declares_every_gauge_threshold_with_the_contracts_value() -> None:
    from scripts.verify_pipeline import THRESHOLDS

    assert {key: THRESHOLDS.get(key) for key in T} == T


def test_the_gauges_are_registered_as_worker_gauges_with_their_metadata() -> None:
    from scripts.verify_pipeline import CHECKS

    rows = {c.key: (c.fn, c.cls, c.host, c.cadence_s, c.unit) for c in CHECKS}
    assert rows["data_quality"] == (qc.check_data_quality, "gauge", "realtime-worker", 21_600,
                                    "cells")
    assert rows["location_town_coverage"] == (qc.check_location_town_coverage, "gauge",
                                              "realtime-worker", 21_600, "cells")
    assert rows["text_extraction"] == (qc.check_text_extraction, "gauge", "realtime-worker",
                                       3_600, "cells")


def test_the_module_never_imports_the_harness_the_worker_or_the_ladder() -> None:
    tree = ast.parse(inspect.getsource(qc))
    imported = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    imported |= {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert not imported & {"scripts.verify_pipeline", "scraper.realtime_worker",
                           "toolkit.system_alerts"}
