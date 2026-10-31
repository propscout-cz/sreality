"""market_evidence (#2064): prices achieved round a subject, the proxy arm (the Stats tab's sold-market lens over Browse's
cohort) and the registered arm (the property page's registered sales), never mixed, every row and summary labelled.

What each arm selects under a user's login is pinned against the replayed schema in tests/test_capabilities_live.py;
here, the arguments (the window needs its reason), the statements, the labels and bases, the refusal, the filters an
arm announces it cannot apply, the port of the page's registered-sales summary and the screens' twins.
"""

from __future__ import annotations

import contextlib
import re
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from api import market_evidence as me
from api import property_read as pr
from api.capabilities import registry
from api.capabilities.entry import Caller
from api.capabilities.errors import ToolError, ToolErrorCode
from tests.api._script_conn import ScriptConn
from tests.api.test_capabilities_census import _dicts
from tests.api.test_stats_pivot_census import registry_key_map
from tests.test_browse_map_read_contract import _balanced_block, _strip_ts_comments
from toolkit import stats_registry as sr
from toolkit.measures import (
    BASIS_MIXED,
    BASIS_UNKNOWN,
    PPM2_BASES,
    PPM2_BASIS_KEY,
    PPM2_VALUE_KEY,
    RENT_MONTHLY_CZK_M2,
    SALE_CAPITAL_CZK_M2,
)

LIB = Path(__file__).resolve().parents[2] / "frontend" / "src" / "lib"
POINT = {"point": {"lat": 50.08, "lng": 14.42}, "category_main": "byt", "category_type": "prodej"}
SEEN = datetime(2026, 10, 20, 8, 0, tzinfo=timezone.utc)


def _const(file: str, name: str) -> str:
    return re.search(rf"export const {name} = (.+?);?\n", (LIB / file).read_text(encoding="utf-8"))[1]


def test_the_defaults_are_twins_of_the_screens() -> None:
    assert _const("stats.ts", "SOLD_MARKET_DAYS") == (
        f"{{ min: {sr.SOLD_MARKET_DAYS[0]}, max: {sr.SOLD_MARKET_DAYS[1]} }} as const")
    assert int(_const("queries.ts", "SOLD_COMPS_LIMIT")) == me.SOLD_COMPS_LIMIT
    assert int(_const("soldComps.ts", "SUMMARY_MIN_ROWS")) == me.SUMMARY_MIN_ROWS
    assert int(_const("soldComps.ts", "SMALL_UNIT_M2")) == me.SMALL_UNIT_M2
    assert _const("soldComps.ts", "COVERED_CATEGORIES") == (
        f"new Set([{', '.join(repr(c) for c in me.COVERED_CATEGORIES)}])")


def test_the_sold_lens_is_the_stats_tabs_facet_for_facet() -> None:
    """stats.ts soldMarketFilters' literal, keyed by registry id (filters.ts REGISTRY_KEY_MAP), is sold_lens's own:
    a facet the Stats tab adds, drops or changes turns this red."""
    src = (LIB / "stats.ts").read_text(encoding="utf-8")
    body = _strip_ts_comments(_balanced_block(src, src.index("({", src.index("export const soldMarketFilters")) + 1))
    assert re.search(r"^\s*\.\.\.f,$", body, flags=re.MULTILINE), "the lens no longer keeps the other filters"
    ts = dict(re.findall(r"^\s*(\w+):\s*(.+?),?$", body, flags=re.MULTILINE))
    by_field = {field: fid for fid, field in registry_key_map().items()}
    lo, hi = sr.SOLD_MARKET_DAYS   # their twin is pinned above
    value = {"true": True, "false": False, "SOLD_MARKET_DAYS.min": lo, "SOLD_MARKET_DAYS.max": hi}
    assert {by_field[k]: v[1:-1] if v.startswith("'") else value[v] for k, v in ts.items()} == me.sold_lens()


# --- the arguments ----------------------------------------------------------------------------------------------

def _validate(args: dict[str, Any]) -> me.EvidenceArgs:
    return registry.validate("market_evidence", args)[1]


def _refused(args: dict[str, Any]) -> list[list[Any]]:
    with pytest.raises(ToolError) as info:
        _validate(args)
    assert info.value.code is ToolErrorCode.INVALID_ARGUMENTS
    return [d["loc"] for d in info.value.details]


def test_the_window_defaults_to_the_stats_tabs_and_any_other_needs_a_reason() -> None:
    lo, hi = sr.SOLD_MARKET_DAYS
    args = _validate(POINT)
    assert (args.tom_days_min, args.tom_days_max, args.window_reason) == (lo, hi, None)
    for window in ({"tom_days_max": 120}, {"tom_days_min": 10}, {"tom_days_min": 0, "tom_days_max": 90}):
        assert _refused({**POINT, **window}) == [[]]
        assert _refused({**POINT, **window, "window_reason": "  "}) == [[]]
        assert _validate({**POINT, **window, "window_reason": "luxury villas sell slowly"}).window_reason
    assert _refused({**POINT, "tom_days_min": 70, "tom_days_max": 60, "window_reason": "x"}) == [[]]
    assert _refused({**POINT, "window_reason": "x" * 501}) == [["window_reason"]]


@pytest.mark.parametrize("args, loc", [
    ({"min_area_m2": 40.5}, ["min_area_m2"]),
    ({"radius_m": 10_001}, []),
    ({"radius_m": 99}, ["radius_m"]),
    ({"limit": me.ROWS_MAX + 1}, ["limit"]),
    ({"max_sold_age_days": 30}, ["max_sold_age_days"]),
    ({"status": "inactive"}, ["status"]),
    ({"point": {"lat": 50.08, "lng": 14.42, "area_m2": 60}}, ["point", "area_m2"]),
])
def test_an_argument_the_arms_cannot_take_is_refused_by_name(args: dict[str, Any], loc: list[Any]) -> None:
    assert loc in _refused({**POINT, **args})


def test_a_point_states_both_categories_and_null_means_every_one() -> None:
    assert _refused({"point": {"lat": 50.08, "lng": 14.42}, "category_main": "byt"}) == [[]]
    args = _validate({"point": {"lat": 50.08, "lng": 14.42}, "category_main": None, "category_type": None})
    assert (args.category_main, args.category_type) == (None, None)


# --- the statements and the envelope ----------------------------------------------------------------------------

def _proxy_row(pid: int, ppm2: float | None = 100_000.0, basis: str | None = SALE_CAPITAL_CZK_M2) -> tuple[Any, ...]:
    facts = {"property_id": pid, "category_main": "byt", "category_type": "prodej", "price_czk": 6_000_000,
             "area_m2": Decimal("60"), "price_per_m2": None if ppm2 is None else Decimal(str(ppm2)),
             "price_per_m2_basis": basis, "tom_days": 30, "last_seen_at": SEEN, "disposition": "2+kk",
             "condition": "dobry", "building_type": "cihla"}
    return (*(facts[c] for c in me.PROXY_COLUMNS), 120.5)


def _sale(n: int, area: float | None = 60.0, ppm2: float | None = 90_000.0, kind: str = "byt",
          basis: str | None = SALE_CAPITAL_CZK_M2) -> tuple[Any, ...]:
    facts = {"source": "reas", "source_record_id": f"r{n}", "sold_at": date(2026, 8, 1), "category_main": kind,
             "price_czk": 5_000_000, "area_m2": None if area is None else Decimal(str(area)),
             "price_per_m2": None if ppm2 is None else Decimal(str(ppm2)), "price_per_m2_basis": basis,
             "disposition": "2+kk", "address_text": "Nusle", "distance_m": 10.0 * n, "sold_age_days": 90,
             "fetched_at": SEEN}
    return tuple(facts[c] for c in me.REGISTERED_COLUMNS)


TOTAL = {"keys": [], "col": None, "n": 6, "mean": 98_000.0, "median": 97_000.0, "p10": 80_000.0, "p25": 90_000.0,
         "p75": 105_000.0, "p90": 110_000.0, "small": None}
PAYLOAD = {"spec": {"rows": [], "cols": [], "measure": "ppm2"}, "filtered": 7, "sample": 6, "excluded": 1,
           "category_main": "byt", "category_type": "prodej", "status": "inactive", "basis": SALE_CAPITAL_CZK_M2,
           "min_n": 5, "refused": None, "places": [], "cells": [TOTAL]}
COVERAGE = (554782, "Praha", SEEN, 120, 400, False, SEEN, "ok")
PROPERTY = tuple({"property_id": 7, "lat": 50.07, "lng": 14.43, "category_main": "dum",
                  "category_type": "prodej"}.get(c) for c in pr.PROPERTY_COLS)


def _script(proxy: list[tuple[Any, ...]] | None = None, cohort: list[int] = (11, 12, 13, 14, 15, 16, 17),
            sales: list[tuple[Any, ...]] | None = None, payload: dict[str, Any] = PAYLOAD,
            prop: tuple[Any, ...] = PROPERTY) -> ScriptConn:
    return ScriptConn([
        (lambda sql: sql.startswith("SELECT property_id FROM listings_public"), [(7,)]),
        (lambda sql: sql.startswith("WITH RECURSIVE chain"), [(7, 7)]),
        (lambda sql: "from properties_public" in sql, [prop]),
        (lambda sql: sql.startswith("SELECT l.property_id, l.category_main"),
         [_proxy_row(i) for i in (11, 12)] if proxy is None else proxy),
        (lambda sql: sql.startswith("SELECT l.property_id FROM browse_list"), [(i,) for i in cohort]),
        (lambda sql: sql.startswith("SELECT public.browse_stats_properties("), [(payload,)]),
        (lambda sql: "FROM public.sold_comparables(" in sql, [_sale(i) for i in range(6)] if sales is None else sales),
        (lambda sql: "FROM public.sold_coverage(" in sql, [COVERAGE]),
    ])


def _run(args: dict[str, Any], conn: ScriptConn | None = None) -> tuple[dict[str, Any], list[tuple[str, Any]]]:
    conn = conn or _script()

    @contextlib.contextmanager
    def own(claims: dict[str, Any], *, act_as: Any, method: str) -> Any:
        assert (act_as, method) == (None, "GET")
        yield conn
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(registry.tenant_pool, "tenant_transaction", own)
        out = registry.call(Caller(market=ScriptConn([]), claims={"sub": "a"}), "market_evidence", args)
    return out, conn.executed


def _statement(executed: list[tuple[str, Any]], start: str) -> tuple[str, Any]:
    return next((sql, params) for sql, params in executed if sql.startswith(start) or start in sql)


def test_every_row_and_summary_is_labelled_and_every_per_m2_figure_has_its_basis() -> None:
    out, _ = _run(POINT)
    data = out["data"]
    assert [r["evidence"] for r in data["proxy"]] == ["proxy"] * 2
    assert [r["evidence"] for r in data["registered"]] == ["registered"] * 6
    assert (data["proxy_summary"]["evidence"], data["registered_summary"]["evidence"]) == ("proxy", "registered")
    figures = [d for d in _dicts(data) if any(k.endswith(PPM2_VALUE_KEY) or k in ("median", "mean") for k in d)]
    assert len(figures) == 2 + 6 + 2 and all(d[PPM2_BASIS_KEY] in PPM2_BASES for d in figures)
    assert out["metadata"]["price_per_m2_basis"] == SALE_CAPITAL_CZK_M2
    assert data["registered"][0]["sold_at"] == "2026-08-01" and data["proxy"][0]["distance_m"] == 120.5


def test_the_proxy_arm_is_the_sold_lens_over_browses_cohort_round_the_subject() -> None:
    out, executed = _run({**POINT, "radius_m": 800, "dispositions": ["2+kk"], "min_area_m2": 40,
                          "condition_match": ["novostavba"], "building_material": ["panel"],
                          "last_seen_max_days": 30, "limit": 3})
    rows, params = _statement(executed, "SELECT l.property_id, l.category_main")
    source = rows.split(" FROM ", 1)[1].split(" ORDER BY ")[0]
    assert source.startswith("browse_list l WHERE ")
    assert _statement(executed, "SELECT l.property_id FROM browse_list") == (
        f"SELECT l.property_id FROM {source}", {k: v for k, v in params.items() if not k.startswith(("subject_",
                                                                                                    "limit"))})
    for clause in ("l.tom_days >= %(tom_days_min)s", "l.tom_days <= %(tom_days_max)s", "l.tom_observed",
                   "l.category_main = %(category_main_in)s", "l.category_type = %(category_type)s",
                   "l.building_type = ANY(%(building_material)s)", "public.in_map_area(l.lat, l.lng"):
        assert clause in source, clause
    assert "property_id <>" not in source   # a point has no property of its own
    assert rows.endswith("ORDER BY distance_m, l.last_seen_at DESC NULLS LAST, l.property_id LIMIT %(limit)s")
    assert {k: params[k] for k in ("status", "tom_days_min", "tom_days_max", "circle_lat", "circle_lng",
                                   "circle_radius_m", "limit")} == {
        "status": "inactive", "tom_days_min": 2, "tom_days_max": 60, "circle_lat": 50.08, "circle_lng": 14.42,
        "circle_radius_m": 800, "limit": 3}
    # Its summary is the dashboard's statistic over exactly that cohort: the empty spec's total cell.
    _, pivot = _statement(executed, "SELECT public.browse_stats_properties(")
    assert (pivot["property_ids_filter"], pivot["spec"], pivot["status_filter"], pivot["hide_dismissed"],
            pivot["tom_observed_only"], pivot["area_min_filter"]) == (
        [11, 12, 13, 14, 15, 16, 17], '{"rows": [], "cols": [], "measure": "ppm2"}', "inactive", False, True, 40)
    summary = out["data"]["proxy_summary"]
    assert summary == {"evidence": "proxy", "filtered": 7, "sample": 6, "excluded": 1, "min_n": 5, "refused": None,
                       **{k: TOTAL[k] for k in (*sr.STATS, "small")}, PPM2_BASIS_KEY: SALE_CAPITAL_CZK_M2}


def test_a_held_subject_stands_at_its_property_and_leaves_its_own_property_out() -> None:
    """Decision 13; an unset category takes the property's own (a house), an explicit null stays every category."""
    out, executed = _run({"listing_id": 70})
    rows, params = _statement(executed, "SELECT l.property_id, l.category_main")
    assert "l.property_id <> %(subject_property_id)s" in rows and params["subject_property_id"] == 7
    assert (params["subject_lat"], params["subject_lng"], params["category_main_in"]) == (50.07, 14.43, "dum")
    assert out["metadata"]["subject"] == {"property_id": 7, "lat": 50.07, "lng": 14.43}
    _, wide = _run({"property_id": 7, "category_main": None})
    assert "category_main_in" not in _statement(wide, "SELECT l.property_id, l.category_main")[1]


@pytest.mark.parametrize("prop, code", [
    (tuple({"property_id": 7, "category_main": "byt"}.get(c) for c in pr.PROPERTY_COLS), ToolErrorCode.REFUSED),
    (None, ToolErrorCode.NOT_FOUND),
])
def test_a_held_subject_with_no_place_is_refused_and_an_unknown_one_not_found(prop: Any, code: ToolErrorCode) -> None:
    conn = _script(prop=prop) if prop else ScriptConn([])
    with pytest.raises(ToolError) as info:
        _run({"property_id": 7}, conn)
    assert info.value.code is code


@pytest.mark.parametrize("categories", [{"category_type": "pronajem"}, {"category_main": "komercni"},
                                        {"category_main": "pozemek", "category_type": None}])
def test_registered_sales_are_flats_and_houses_for_sale_and_any_other_ask_empties_the_arm(
        categories: dict[str, Any]) -> None:
    out, executed = _run({**POINT, **categories})
    assert not [sql for sql, _ in executed if "sold_" in sql]
    assert out["data"]["registered"] == [] and out["metadata"]["registered_refused"]
    assert out["metadata"]["registered_radius_m"] is None
    assert out["data"]["registered_summary"]["sales"] == 0


def test_every_category_reads_both_kinds_of_registered_sale() -> None:
    _, executed = _run({**POINT, "category_main": None, "category_type": None})
    sql, params = _statement(executed, "FROM public.sold_comparables(")
    assert "category_main" not in params and "WHERE true ORDER BY" in sql


def test_the_registered_arm_reads_the_pages_sales_nearest_first_with_its_own_filters() -> None:
    out, executed = _run({**POINT, "radius_m": 3000, "dispositions": ["2+kk", "3+kk"], "max_area_m2": 80,
                          "max_sold_age_days": 365})
    sql, params = _statement(executed, "FROM public.sold_comparables(")
    assert sql.startswith("SELECT " + ", ".join(f"l.{c}" for c in me.REGISTERED_COLUMNS) + " FROM public."
                          "sold_comparables(%(subject_lat)s::float8, %(subject_lng)s::float8, %(radius_m)s::float8) l")
    for clause in ("l.category_main = %(category_main_in)s", "l.disposition = ANY(%(dispositions)s)",
                   "l.area_m2 <= %(max_area_m2)s", "l.sold_age_days <= %(max_sold_age_days)s"):
        assert clause in sql, clause
    assert sql.endswith("ORDER BY l.distance_m, l.sold_at DESC, l.source_record_id LIMIT %(cap)s")
    assert (params["radius_m"], params["cap"], out["metadata"]["registered_radius_m"]) == (
        3000, me.SOLD_COMPS_LIMIT + 1, 3000)
    assert out["metadata"]["registered_coverage"] == {
        "obec_kod": 554782, "obec_name": "Praha", "fetched_at": SEEN.isoformat(), "record_count": 120,
        "source_total": 400, "truncated": False, "last_attempt_at": SEEN.isoformat(), "last_attempt_status": "ok"}


def test_the_registered_arm_reads_no_further_than_its_sales_are_fetched_and_says_so() -> None:
    """scraper/sold_db.MAX_READ_RADIUS_M: the margin the fetcher covers round every town; the proxy reads the circle."""
    out, executed = _run({**POINT, "radius_m": 8000})
    assert _statement(executed, "FROM public.sold_comparables(")[1]["radius_m"] == 5000
    assert _statement(executed, "SELECT l.property_id, l.category_main")[1]["circle_radius_m"] == 8000
    assert out["metadata"]["registered_radius_m"] == 5000


def test_more_than_the_pages_cap_is_said_and_the_rows_are_the_nearest_few() -> None:
    out, _ = _run({**POINT, "limit": 4}, _script(sales=[_sale(i) for i in range(me.SOLD_COMPS_LIMIT + 1)]))
    summary = out["data"]["registered_summary"]
    assert (summary["sales"], summary["more"], summary["n"]) == (me.SOLD_COMPS_LIMIT, True, me.SOLD_COMPS_LIMIT)
    assert [r["source_record_id"] for r in out["data"]["registered"]] == ["r0", "r1", "r2", "r3"]
    assert out["metadata"]["result_count"] == 7 + me.SOLD_COMPS_LIMIT


def test_a_filter_an_arm_cannot_apply_is_announced_never_dropped() -> None:
    out, _ = _run({**POINT, "condition_match": ["novostavba"], "building_material": ["panel"],
                   "last_seen_max_days": 30, "max_sold_age_days": 365, "dispositions": ["2+kk"]})
    assert out["metadata"]["unapplied"] == {"registered": ["condition_match", "building_material", "last_seen_max_days"],
                                            "proxy": ["max_sold_age_days"]}
    assert _run(POINT)[0]["metadata"]["unapplied"] == {"registered": [], "proxy": []}


def test_the_window_and_its_reason_are_echoed() -> None:
    out, executed = _run({**POINT, "tom_days_max": 120, "window_reason": "luxury villas sell slowly"})
    assert out["metadata"]["window"] == {"tom_days_min": 2, "tom_days_max": 120, "default": False,
                                         "reason": "luxury villas sell slowly"}
    assert _statement(executed, "SELECT l.property_id, l.category_main")[1]["tom_days_max"] == 120
    assert _run(POINT)[0]["metadata"]["window"]["default"] is True


def test_an_empty_proxy_cohort_is_the_dashboards_empty_answer() -> None:
    empty = {**PAYLOAD, "filtered": 0, "sample": 0, "excluded": 0, "status": None, "basis": None,
             "refused": "empty", "cells": []}
    out, executed = _run(POINT, _script(proxy=[], cohort=[], payload=empty))
    assert _statement(executed, "SELECT public.browse_stats_properties(")[1]["property_ids_filter"] == []
    summary = out["data"]["proxy_summary"]
    assert (summary["refused"], summary["median"], summary[PPM2_BASIS_KEY]) == ("empty", None, BASIS_UNKNOWN)


# --- the page's registered-sales summary, ported -----------------------------------------------------------------

def _sold(area: float | None, ppm2: float | None, kind: str = "byt",
          basis: str | None = SALE_CAPITAL_CZK_M2) -> dict[str, Any]:
    return {"area_m2": area, PPM2_VALUE_KEY: ppm2, PPM2_BASIS_KEY: basis, "category_main": kind}


def test_the_summary_holds_small_units_out_of_its_median_and_counts_them() -> None:
    rows = [_sold(60, v) for v in (100, 300, 200, 400, 500)] + [_sold(29.9, 900), _sold(None, 600)]
    assert me.summarize_sold(rows) == {"n": 6, "held_out": 1, PPM2_BASIS_KEY: SALE_CAPITAL_CZK_M2,
                                       "mixed_kinds": False, "median_price_per_m2": 350.0}
    assert me.summarize_sold(rows[:5])["median_price_per_m2"] == 300
    assert me.summarize_sold([*rows[:5], _sold(30, 700)])["held_out"] == 0   # 30 m² is not under 30


def test_the_summary_needs_five_kept_sales_and_counts_only_priced_ones_in_its_median() -> None:
    assert me.summarize_sold([_sold(60, 1)] * 4 + [_sold(20, 1)] * 3) is None
    unpriced = me.summarize_sold([_sold(60, None)] * 5)
    assert (unpriced["n"], unpriced["median_price_per_m2"]) == (5, None)
    assert me.summarize_sold([_sold(60, 100)] * 2 + [_sold(60, None)] * 3)["n"] == 2


def test_the_summary_flags_flats_and_houses_together_and_reads_the_basis_from_its_rows() -> None:
    mixed = me.summarize_sold([_sold(60, 100)] * 4 + [_sold(120, 50, kind="dum")])
    assert (mixed["mixed_kinds"], mixed[PPM2_BASIS_KEY]) == (True, SALE_CAPITAL_CZK_M2)
    two = me.summarize_sold([_sold(60, 100)] * 4 + [_sold(60, 1, basis=RENT_MONTHLY_CZK_M2)])
    assert two[PPM2_BASIS_KEY] == BASIS_MIXED
    assert me.summarize_sold([_sold(60, 100, basis=None)] * 5)[PPM2_BASIS_KEY] == BASIS_UNKNOWN
    # A held-out house still marks the mix: the TS reads every row's kind.
    assert me.summarize_sold([_sold(60, 100)] * 5 + [_sold(20, 1, kind="dum")])["mixed_kinds"] is True
