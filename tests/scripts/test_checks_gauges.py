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
from decimal import Decimal
from typing import Any

import pytest

from location_data.claims_common import SOURCES
from location_data.resolver.version import RESOLVER_VERSION
from scripts.checks import clock, common
from scripts.checks import publishing as pub
from scripts.checks import quality as qc
from tests.scripts.test_checks_platform import _Conn
from tests.scripts.test_checks_platform_replay import _incidents, _told
from toolkit import description_extraction as text_lane
from toolkit.measures import wilson_lower, wilson_upper

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
    "location_registry_max_staged_hours": 24,
    "location_street_floor": {
        "bezrealitky": 0.62, "sreality": 0.57, "realitymix": 0.52, "idnes": 0.45, "maxima": 0.45,
        "ceskereality": 0.38, "mmreality": 0.35, "bazos": 0.28, "remax": 0.04},
    "location_street_floor_min_n": 200, "location_served_street_floor": 0.44,
    "location_radius_cover_watch": 0.90, "location_accuracy_min_n": 30,
    "location_evidence_backlog_max_min": 60, "text_extraction_flow_warn": 0.05,
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
    conn = _location_conn()
    for _ in range(2):
        _in_pass(qc.check_location_town_coverage, conn, T)
    assert sum(sql == common.TREND_SQL for sql, _, _ in conn.sent) == 2
    qc.check_location_town_coverage(conn, T)
    assert sum(sql == common.TREND_SQL for sql, _, _ in conn.sent) == 2 + len(_SUBJECTS)


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


# --- location_town_coverage: the ONE location scorecard (W1, migration 698) -----------------


def _portal(source: str, *, ads: int = 50_000, foreign: int = 0, no_row: int = 0,
            unresolved: int = 0, withheld: int = 0, hidden: int = 0, street_share: float = 0.65,
            ku_missing: int = 0, outside: int = 0, disputed: tuple[int, int, int, int] = (0, 0, 0, 0),
            versions: dict[str, tuple[int, int]] | None = None, ku_gap: int = 0,
            cohort: str = "listing") -> list[tuple[Any, ...]]:
    """One source's view rows of a cohort; the Czech ads with a town split 40/25/10/25 by grain
    unless `street_share` says otherwise. `versions` is {resolver_version: (rows, stale)}."""
    czech = ads - foreign
    towned = czech - unresolved - withheld
    street = round(towned * street_share)
    grains = {"address": street // 2, "street": street - street // 2,
              "quarter": (towned - street) // 3}
    grains["obec"] = towned - sum(grains.values())

    def row(metric: str, cls: str, n: int, of_n: int | None) -> tuple[Any, ...]:
        return (cohort, source, metric, cls, n, of_n, None, None, None, None)

    rows = [row("town", "ads", ads, ads), row("town", "foreign", foreign, ads),
            row("town", "has_town", towned, czech), row("town", "no_row", no_row, ads),
            row("town", "unresolved", unresolved, czech), row("town", "withheld", withheld, czech),
            row("town", "hidden", hidden, ads), row("outside", "position", outside, towned)]
    rows += [row("ku", g, n - (ku_missing if g == "obec" else 0), n) for g, n in grains.items()]
    rows += [row("disputed", c, n, ads) for c, n in zip(qc._DISPUTED, disputed)]
    rows += [row("grain", g, n, towned) for g, n in grains.items()]
    if cohort == "listing":
        held = versions or {RESOLVER_VERSION: (ads - no_row, 0)}
        total = sum(n for n, _ in held.values())
        for version, (n, stale) in held.items():
            rows += [row("version", version, n, total), row("registry_stale", version, stale, n),
                     row("ku_gap", version, ku_gap if version == RESOLVER_VERSION else 0,
                         grains["address"])]
    return rows


def _sample(source: str, *, hits: int = 290, n: int = 300,
            pins: dict[str, tuple[int, float]] | None = None) -> list[tuple[Any, ...]]:
    """One source's sample rows: radius cover hits of n, and pin error per label (n, q95 m)."""
    rows = [("sample", source, "radius_cover", "below_address", hits, n, None, None, None, None)]
    for label, (count, q95) in (pins or {"undeclared": (n, 40.0)}).items():
        rows.append(("sample", source, "pin_error_m", label, count, None, Decimal("5.5"),
                     Decimal(str(q95 / 2)), Decimal(str(q95)), Decimal(str(q95 * 3))))
    return rows


def _queue(evidence: tuple[int, int, float, float] | None = None,
           backfill: tuple[int, int, float, float] | None = None) -> list[tuple[Any, ...]]:
    """The resolve queue's rows: (count, retrying, median s, oldest s) per class."""
    return [("queue", None, "backlog_s", cls, n, retrying, Decimal(str(q50)), None, None,
             Decimal(str(oldest)))
            for cls, values in (("evidence", evidence), ("backfill", backfill)) if values
            for n, retrying, q50, oldest in [values]]


_REGISTRY = ("ruian:2026-09-30", 19, None, None)
_LIVE = ("bezrealitky", "sreality", "realitymix", "idnes", "maxima", "ceskereality", "mmreality",
         "bazos", "remax")


def _stock(*rows: list[tuple[Any, ...]], served: bool = True) -> list[tuple[Any, ...]]:
    """Every live portal OK at the listing grain (and served, unless off), then the given rows
    replacing a portal's cohort wholesale."""
    given = {(r[0], r[1]) for rs in rows for r in rs}
    base = [r for p in _LIVE for cohort in (("listing", "served") if served else ("listing",))
            if (cohort, p) not in given for r in _portal(p, cohort=cohort)]
    return base + [r for rs in rows for r in rs]


def _location_conn(stock: list[tuple[Any, ...]] | None = None,
                   sample: list[tuple[Any, ...]] | BaseException | None = None,
                   registry: tuple[Any, ...] | None = _REGISTRY,
                   previous: list[dict[str, Any]] | None = None) -> _Conn:
    return _Conn({qc._LOCATION_STOCK_SQL: _stock() if stock is None else stock,
                  qc._LOCATION_SAMPLE_SQL: ([r for p in _LIVE for r in _sample(p)]
                                           if sample is None else sample),
                  qc._REGISTRY_LIVENESS_SQL: [] if registry is None else [registry],
                  common.PREVIOUS_CELLS_SQL: [] if previous is None else [
                      (_NOW - _dt.timedelta(hours=6), previous)]}, now=_NOW)


def _location(stock: list[tuple[Any, ...]] | None = None,
              sample: list[tuple[Any, ...]] | BaseException | None = None,
              registry: tuple[Any, ...] | None = _REGISTRY,
              previous: list[dict[str, Any]] | None = None) -> tuple[dict[str, Any], _Conn]:
    conn = _location_conn(stock, sample, registry, previous)
    return qc.check_location_town_coverage(conn, T), conn


_SUBJECTS = ([f"{p}/{name}" for p in sorted(SOURCES) for name in qc.LOCATION_PORTAL_CELLS]
             + ["served/town", "served/town_withheld", "served/outside", "served/street",
                "registry", "freshness/version", "freshness/evidence"])


def test_the_scorecard_emits_one_stable_subject_list_and_web_reads_not_measured() -> None:
    """67 cells on every pass, whatever the rows hold: six per portal, four served, the registry,
    two freshness. web has no ad past the edge, so its six read Not measured."""
    out, conn = _location()
    cells = _cells(out)
    assert [c["subject"] for c in out["details"]["cells"]] == _SUBJECTS and len(_SUBJECTS) == 67
    assert {cells[f"web/{n}"]["status"] for n in qc.LOCATION_PORTAL_CELLS} == {"unknown"}
    assert cells["web/town"]["message"] == ("Not measured: no active ads first written more than "
                                            "24 h ago")
    assert {c["status"] for s, c in cells.items() if not s.startswith("web/")} == {"ok"}
    assert out["status"] == "ok" and not out["details"].get("not_measured")
    empty, _ = _location(stock=[], sample=[])
    assert [c["subject"] for c in empty["details"]["cells"]] == _SUBJECTS
    assert conn.sent and all(armed for _, _, armed in conn.sent)


def test_the_gauge_reads_the_view_in_two_statements_split_on_the_literal_cohort() -> None:
    _, conn = _location()
    sent = [s for s, _, _ in conn.sent]
    assert sent.index(qc._LOCATION_STOCK_SQL) < sent.index(qc._LOCATION_SAMPLE_SQL)
    assert qc._LOCATION_STOCK_SQL.endswith("from location_quality_by_source where cohort <> 'sample'")
    assert qc._LOCATION_SAMPLE_SQL.endswith("from location_quality_by_source where cohort = 'sample'")
    assert {s for s in sent} == {common.CLOCK_SQL, qc._LOCATION_STOCK_SQL, qc._REGISTRY_LIVENESS_SQL,
                                 common.PREVIOUS_CELLS_SQL, qc._LOCATION_SAMPLE_SQL, common.TREND_SQL}


def test_every_count_cell_reads_the_count_that_colours_it() -> None:
    """The value IS the rows that colour the cell: Problem above zero for the invariants, Watch
    above zero for the page-withheld, each counted in its own cell and never in another's."""
    stock = _stock(_portal("ceskereality", ads=50_026, unresolved=3, withheld=26, no_row=2,
                           hidden=5, outside=139, disputed=(157, 141, 6, 4)),
                   _portal("sreality", ads=106_936, foreign=1, ku_gap=1),
                   _portal("ceskereality", cohort="served", ads=40_000, withheld=20, outside=113),
                   _portal("bazos", cohort="served", ads=45_000, unresolved=2, outside=10_545))
    cells = _cells(_location(stock)[0])
    expect = {"ceskereality/town": ("fail", 3), "ceskereality/town_withheld": ("warn", 26),
              "ceskereality/outside": ("fail", 139), "ceskereality/ku": ("ok", 0),
              "sreality/ku": ("fail", 1), "sreality/town": ("ok", 0),
              "sreality/town_withheld": ("ok", 0), "served/town": ("fail", 2),
              "served/town_withheld": ("warn", 20), "served/outside": ("fail", 10_658),
              "freshness/version": ("ok", 0)}
    assert {s: (cells[s]["status"], cells[s]["value"]) for s in expect} == expect
    town = cells["ceskereality/town"]
    assert town["unit"] == "ads" and town["portal"] == "ceskereality"
    assert town["message"] == ("3 of 50,026 active ads first written more than 24 h ago not "
                               "determined foreign hold town-bearing evidence and no town (rule 25: "
                               "red until zero)")
    assert {k: town["facts"][k] for k in ("ads", "foreign", "has_town", "no_row", "hidden",
                                          "town_share")} == {
        "ads": 50_026, "foreign": 0, "has_town": 49_997, "no_row": 2, "hidden": 5,
        "town_share": round(49_997 / 50_026, 4)}
    assert "withheld" not in town["facts"], "measured once, in its own cell"
    assert cells["ceskereality/outside"]["facts"]["disputed"] == {
        "pin_outside_obec": 157, "pin_off_street": 141, "country_conflict": 6, "pin_outside_cz": 4}
    assert cells["served/outside"]["facts"]["by_portal"] == {"bazos": 10_545, "ceskereality": 113}
    assert cells["served/town_withheld"]["facts"]["by_portal"] == {"ceskereality": 20}
    assert cells["sreality/ku"]["message"] == (
        f"1 Czech ads at address grain have no cadastral area (KÚ) at {RESOLVER_VERSION}: the "
        "registry's KÚ geometry is missing at its version")


def test_the_town_share_leaves_foreign_ads_out_of_its_denominator() -> None:
    """idnes on 10-31: 17,668 of 108,726 ads are abroad, and every Czech one has a town: 100 %."""
    cells = _cells(_location(_stock(_portal("idnes", ads=108_726, foreign=17_668)))[0])
    assert cells["idnes/town"]["facts"]["town_share"] == 1.0
    assert cells["idnes/town"]["status"] == "ok"


@pytest.mark.parametrize("withheld", [1, 26, 40_000])
def test_page_withheld_is_watch_and_never_problem(withheld: int) -> None:
    out, _ = _location(_stock(_portal("mmreality", ads=50_000, withheld=withheld)))
    cell = _cells(out)["mmreality/town_withheld"]
    assert (cell["status"], cell["value"]) == ("warn", withheld)
    assert _cells(out)["mmreality/town"]["status"] == "ok" and out["status"] == "warn"


@pytest.mark.parametrize("hits, n, status", [
    (290, 300, "ok"), (102, 116, "warn"), (0, 500, "warn"), (213, 373, "warn"), (7, 7, "unknown"),
    (29, 29, "unknown")])
def test_accuracy_is_watch_on_its_lower_bound_and_never_problem(hits: int, n: int,
                                                               status: str) -> None:
    sample = [r for p in _LIVE if p != "sreality" for r in _sample(p)]
    sample += _sample("sreality", hits=hits, n=n, pins={"gps": (40, 108.0), "street": (30, 713.0)})
    cell = _cells(_location(sample=sample)[0])["sreality/accuracy"]
    assert cell["status"] == status
    assert cell["facts"]["pin_error_m"] == {
        "gps": {"n": 40, "q50": 5.5, "q90": 54.0, "q95": 108.0, "max": 324.0},
        "street": {"n": 30, "q50": 5.5, "q90": 356.5, "q95": 713.0, "max": 2139.0}}
    if status != "unknown":
        assert cell["value"] == round(100 * hits / n, 2) and cell["unit"] == "%"
        assert cell["facts"]["lower_bound"] == round(wilson_lower(hits, n), 4)
        assert "gps q95 108 m (n 40); street q95 713 m (n 30)" in cell["message"]


@pytest.mark.parametrize("share, n, status", [
    (0.641, 106_000, "ok"),      # sreality on 10-31, floor 0.57
    (0.565, 106_000, "fail"),    # under the floor, upper bound too
    (0.56, 210, "ok"),           # under the floor, but its upper bound is not
    (0.40, 199, "unknown")])     # under min_n: no verdict
def test_the_street_floor_is_judged_on_the_upper_bound_over_min_n(share: float, n: int,
                                                                  status: str) -> None:
    cell = _cells(_location(_stock(_portal("sreality", ads=n, street_share=share)))[0])[
        "sreality/street"]
    hits = round(n * share)
    assert cell["status"] == status and cell["facts"]["floor"] == 0.57
    assert cell["facts"]["upper_bound"] == round(wilson_upper(hits, n), 4)
    assert cell["facts"]["share"] == round(hits / n, 4)
    if status != "unknown":
        assert cell["value"] == round(100 * hits / n, 2)
    assert set(cell["facts"]["grains"]) == {"address", "street", "quarter", "obec"}


def test_served_street_is_the_fleet_share_against_its_own_floor() -> None:
    stock = _stock(*[_portal(p, cohort="served", ads=10_000, street_share=0.40) for p in _LIVE])
    cell = _cells(_location(stock)[0])["served/street"]
    assert (cell["status"], cell["value"], cell["facts"]["floor"]) == ("fail", 40.0, 0.44)
    assert cell["facts"]["by_portal"]["bazos"] == 0.4


@pytest.mark.parametrize("previous, status", [
    (None, "warn"), ([{"subject": "freshness/version", "value": 0}], "warn"),
    ([{"subject": "freshness/version", "value": 400}], "fail"),
    ([{"subject": "registry", "value": 31}], "warn")])
def test_a_resolve_lag_is_watch_once_and_problem_on_the_second_reading(
        previous: list[dict[str, Any]] | None, status: str) -> None:
    """Rows behind the code's version count once whatever their registry; rows at it count when
    their registry label is stale."""
    stock = _stock(_portal("bazos", ads=1_000, versions={"resolver:v5.0": (300, 300),
                                                         RESOLVER_VERSION: (700, 20)}))
    cell = _cells(_location(stock, previous=previous)[0])["freshness/version"]
    assert (cell["status"], cell["value"]) == (status, 320)
    assert {k: cell["facts"][k] for k in ("rows", "behind", "stale_registry")} == {
        "rows": 50_000 * 8 + 1_000, "behind": 300, "stale_registry": 320}


@pytest.mark.parametrize("oldest_s, status", [(59 * 60, "ok"), (60 * 60, "ok"), (61 * 60, "fail")])
def test_evidence_waiting_past_an_hour_is_a_problem_and_backfill_a_fact(oldest_s: float,
                                                                        status: str) -> None:
    stock = _stock() + _queue(evidence=(12, 1, 300.0, oldest_s), backfill=(9_000, 4, 3600.0, 7200.0))
    cell = _cells(_location(stock)[0])["freshness/evidence"]
    assert (cell["status"], cell["value"], cell["unit"]) == (status, round(oldest_s / 60, 1), "min")
    assert {k: cell["facts"][k] for k in ("evidence", "evidence_retrying", "backfill",
                                          "backfill_oldest_min")} == {
        "evidence": 12, "evidence_retrying": 1, "backfill": 9_000, "backfill_oldest_min": 120.0}
    quiet = _cells(_location(_stock() + _queue(backfill=(9_000, 0, 60.0, 7200.0)))[0])
    assert (quiet["freshness/evidence"]["status"], quiet["freshness/evidence"]["value"]) == ("ok", 0.0)


def test_a_sample_that_times_out_leaves_only_the_accuracy_cells_not_measured() -> None:
    """The pin sample is the second statement: its timeout costs the accuracy cells, never a red,
    and every other cell reads exactly what it reads with the sample."""
    read, _ = _location()
    timed_out, conn = _location(sample=RuntimeError("canceling statement due to statement timeout"))
    cells, before = _cells(timed_out), _cells(read)
    assert {s for s, c in cells.items() if c["status"] != before[s]["status"]} == {
        f"{p}/accuracy" for p in _LIVE}
    assert cells["bazos/accuracy"]["message"] == (
        "Not measured: the pin sample was not read (canceling statement due to statement timeout)")
    assert timed_out["details"]["not_measured"] == {
        "sample": "canceling statement due to statement timeout"}
    assert timed_out["status"] == "ok"
    assert common.TREND_SQL in [s for s, _, _ in conn.sent], "the trends still read"


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
    cell = _cells(_location(registry=registry)[0])["registry"]
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
    assert cell["facts"]["lower_bound"] == round(wilson_lower(unread, 700), 4)


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
        low = wilson_lower(arrived, arrived)
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
        _location()[0], _location(_stock(_portal("bazos", unresolved=1)))[0],
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
    _, location = _location()
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
