"""The Stats dashboard for an agent (#2064): every parameter of the dashboard's function is either sent the
dashboard's way (`stats_pivot.PARAMS`, bound as its declared type) or named unsent with a reason
(`stats_pivot.UNMAPPED`), and the tool sends exactly the keys the dashboard sends, with the dashboard's values.

The census reads the latest definition from the migrations, as tests/test_626_stats_dashboard.py does; a parameter a
later migration adds fails it until it is mapped or reasoned away. The rows it selects are pinned against the
function's own answer in tests/test_capabilities_live.py.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

import pytest

from api import stats_pivot as sp
from api.capabilities import registry
from api.capabilities.entry import Caller
from api.capabilities.errors import ToolError, ToolErrorCode
from api.capabilities.truncate import RESULT_BUDGET, encode
from tests.api._script_conn import ScriptConn
from tests.migration_defs import latest_definition
from tests.test_browse_map_read_contract import _builder_keys
from tests.test_portal_exclusion_migration import _params
from toolkit import filter_registry as fr
from toolkit.filter_compiler import RETIRED_FILTERS
from toolkit.measures import BASIS_MIXED, BASIS_UNKNOWN, SALE_CAPITAL_CZK_M2

FN = "browse_stats_properties"


def census(signature: list[tuple[str, str]], params: Mapping[str, str], unmapped: Mapping[str, str]) -> list[str]:
    """Every way the two tables and the function's signature disagree."""
    declared = dict(signature)
    return [
        *(f"{p} is neither mapped nor unmapped" for p in declared if p not in params and p not in unmapped),
        *(f"{p} is both mapped and unmapped" for p in params if p in unmapped),
        *(f"{p} is no parameter" for p in (*params, *unmapped) if p not in declared),
        *(f"{p} is bound as {t}, declared {declared[p]}" for p, t in params.items() if declared.get(p, t) != t),
        *(f"{p} gives no reason" for p, why in unmapped.items() if len(why.split()) < 8 or not why.endswith(".")),
    ]


def _signature() -> list[tuple[str, str]]:
    return _params(latest_definition(FN).read_text(encoding="utf-8"), FN)


def test_every_dashboard_parameter_is_mapped_or_unmapped_with_its_reason() -> None:
    assert len(_signature()) >= 86, "the signature parse broke"
    assert census(_signature(), sp.PARAMS, sp.UNMAPPED) == []
    assert list(sp.PARAMS) == [p for p, _ in _signature() if p not in sp.UNMAPPED], "PARAMS reads in signature order"


def test_the_census_fails_on_a_parameter_no_table_names() -> None:
    planted = [*_signature(), ("planted_filter", "integer")]
    assert census(planted, sp.PARAMS, sp.UNMAPPED) == ["planted_filter is neither mapped nor unmapped"]


def test_the_census_fails_on_a_type_drift_a_vanished_parameter_an_overlap_and_a_missing_reason() -> None:
    signature = [(p, "text" if p == "tom_days_min" else t) for p, t in _signature() if p != "spec"]
    unmapped = {**sp.UNMAPPED, "hide_dismissed": "no"}
    assert census(signature, sp.PARAMS, unmapped) == [
        "hide_dismissed is both mapped and unmapped", "spec is no parameter",
        "tom_days_min is bound as integer, declared text", "hide_dismissed gives no reason"]


def test_the_tool_sends_exactly_the_keys_the_dashboard_sends_plus_its_spec() -> None:
    assert set(sp.PARAMS) - {"spec"} == _builder_keys()


def test_only_the_parked_condition_levels_go_unsent() -> None:
    """Rule 14: the registry retires their bounds, so neither the dashboard nor an agent can send one."""
    assert set(sp.UNMAPPED) == {f for f in RETIRED_FILTERS if "condition_level" in f}


# --- the dashboard's values ---------------------------------------------------------------------------------------

def _args(**over: Any) -> sp.AggregateArgs:
    return registry.validate("aggregate_properties", {"category_type": "prodej", **over})[1]


def test_an_unfiltered_call_sends_the_dashboards_defaults_and_reads_nothing_first() -> None:
    conn = ScriptConn([])
    values = sp.pivot_args(conn, _args())
    assert set(values) == set(sp.PARAMS) and conn.executed == []
    # queries.ts buildBrowseStatsArgs over a fresh filter: Browse hides the dismissed, the function's default does not.
    assert {k: v for k, v in values.items() if v is not None} == {
        "category_type_filter": "prodej", "status_filter": "any", "include_no_price": False, "hide_dismissed": True,
        "portal_excluded_active_only": False, "tom_observed_only": False,
        "spec": json.dumps({"rows": [], "cols": [], "measure": "ppm2"})}


def test_an_empty_list_is_sent_as_null_as_the_dashboard_sends_it() -> None:
    """`[]` would match nothing in the function (`= any('{}')`); the dashboard sends null."""
    values = sp.pivot_args(ScriptConn([]), _args(dispositions=[], category_main_in=[], portals=[], furnished=[],
                                                 condition_match=[], building_material=[], districts=[]))
    assert {k: v for k, v in values.items() if isinstance(v, list)} == {}


def test_place_chips_travel_as_four_parallel_arrays_and_the_map_area_as_its_seven_numbers() -> None:
    chips = [{"name": "Brno", "level": "obec", "id": 582786},
             {"name": "Veveří", "context": "Brno", "level": "cast_obce", "id": 411001, "excluded": True}]
    values = sp.pivot_args(ScriptConn([]), _args(districts=chips, circle={"lat": 49.2, "lng": 16.6, "radius_m": 900}))
    assert {k: values[k] for k in ("districts_filter", "districts_excluded_filter", "districts_levels",
                                   "districts_ids")} == {
        "districts_filter": ["Brno", "Veveří"], "districts_excluded_filter": [False, True],
        "districts_levels": ["obec", "cast_obce"], "districts_ids": [582786, 411001]}
    assert {k: values[k] for k in ("bbox_west", "bbox_north", "circle_lat", "circle_lng", "circle_radius_m")} == {
        "bbox_west": None, "bbox_north": None, "circle_lat": 49.2, "circle_lng": 16.6, "circle_radius_m": 900}
    boxed = sp.pivot_args(ScriptConn([]), _args(bounds={"west": 14.2, "south": 49.9, "east": 14.7, "north": 50.2}))
    assert [boxed[k] for k in ("bbox_west", "bbox_south", "bbox_east", "bbox_north", "circle_radius_m")] == [
        14.2, 49.9, 14.7, 50.2, None]


def test_the_irregular_filters_are_sent_as_the_dashboard_sends_them() -> None:
    values = sp.pivot_args(ScriptConn([]), _args(
        building_material=["panel", "ostatni"], show_dismissed=True, min_area_m2=40, max_area_m2=50, has_lift=False,
        tags={"in": [4, 5]}, pipeline={"set": "out"}, min_parking_lots=1, total_price_change_pct=-5))
    assert values["building_type_filter"] == fr.building_material_values(["panel", "ostatni"])
    assert (values["hide_dismissed"], values["area_min_filter"], values["area_max_filter"]) == (False, 40, 50)
    assert (values["has_lift_filter"], values["parking_lots_min_filter"], values["total_price_change_pct_filter"]) == (
        False, 1, -5)
    # setScope.scopeArgs: only a set side.
    assert {k: v for k, v in values.items() if k.startswith(("tags_", "collections_", "pipeline_"))} == {
        "tags_set": None, "tags_in": [4, 5], "tags_out": None, "collections_set": None, "collections_in": None,
        "collections_out": None, "pipeline_set": "out", "pipeline_in": None, "pipeline_out": None}


def test_the_prefilters_resolve_under_the_login_and_an_empty_match_travels_as_empty() -> None:
    """resolveBrowsePrefilters: the city rules to towns through curated_cities_matching, with_estimates to the user's
    estimated properties; [] when nothing matched, so the function answers `empty`."""
    rules = [{"index_name": "bezpecnost", "op": ">=", "value": 6}]
    conn = ScriptConn([(lambda sql: "curated_cities_matching" in sql, [(582786,), (554782,)]),
                       (lambda sql: "property_estimates_public" in sql, [(7,), (8,), (9,)])])
    values = sp.pivot_args(conn, _args(city_index_rules=rules, with_estimates=True), property_ids=[9, 3, 7])
    assert (values["obec_ids_filter"], values["property_ids_filter"]) == ([582786, 554782], [7, 9])
    wire = json.dumps([{**rules[0], "value": 6.0}])   # the rule as its model dumps it
    assert conn.executed[0] == ("SELECT curated_cities_matching(%s::jsonb)", (wire,))
    assert sp.pivot_args(ScriptConn([]), _args(city_index_rules=rules, with_estimates=True))[
        "obec_ids_filter"] == []
    assert sp.pivot_args(ScriptConn([]), _args(), property_ids=[])["property_ids_filter"] == []


@pytest.mark.parametrize("args, loc", [
    ({"min_area_m2": 40.5}, ["min_area_m2"]),
    ({"rows": ["obec"], "cols": ["obec"]}, []),
    ({"rows": ["disposition"] * 2}, []),
    ({"rows": ["kraj", "okres", "obec", "cast_obce", "ulice", "disposition", "condition"]}, ["rows"]),
    ({"cols": ["kraj", "obec"]}, ["cols"]),
    ({"measure": "price_per_m2"}, ["measure"]),
    ({"rows": ["town"]}, ["rows", 0]),
])
def test_a_spec_the_dashboard_refuses_and_a_fractional_area_are_invalid_arguments(args: dict[str, Any],
                                                                                    loc: list[Any]) -> None:
    with pytest.raises(ToolError) as info:
        _args(**args)
    assert info.value.code is ToolErrorCode.INVALID_ARGUMENTS
    assert loc in [d["loc"] for d in info.value.details]


# --- the envelope ------------------------------------------------------------------------------------------------

def _payload(**over: Any) -> dict[str, Any]:
    return {"spec": {"rows": [], "cols": [], "measure": "ppm2"}, "filtered": 6, "sample": 5, "excluded": 1,
            "category_main": "byt", "category_type": "prodej", "status": "active", "basis": SALE_CAPITAL_CZK_M2,
            "min_n": 5, "refused": None, "places": [], "cells": [], **over}


def _call(args: dict[str, Any], payload: dict[str, Any]) -> tuple[dict[str, Any], ScriptConn]:
    import contextlib
    conn = ScriptConn([(lambda sql: sql.startswith("SELECT public.browse_stats_properties("), [(payload,)])])

    @contextlib.contextmanager
    def own(claims: dict[str, Any], *, act_as: Any, method: str) -> Any:
        assert (act_as, method) == (None, "GET")
        yield conn
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(registry.tenant_pool, "tenant_transaction", own)
        return registry.call(Caller(market=ScriptConn([]), claims={"sub": "a"}), "aggregate_properties",
                             {"category_type": "prodej", **args}), conn


def test_the_data_is_the_functions_answer_and_the_metadata_names_its_unit() -> None:
    out, conn = _call({"rows": ["disposition"], "min_area_m2": 40}, _payload())
    assert out["data"] == _payload()
    (sql, params), = conn.executed
    assert sql == sp._CALL and params["spec"] == json.dumps({"rows": ["disposition"], "cols": [], "measure": "ppm2"})
    meta = out["metadata"]
    assert (meta["tool"], meta["result_count"], meta["price_per_m2_basis"]) == (
        "aggregate_properties", 5, SALE_CAPITAL_CZK_M2)
    assert meta["filters_used"] == {"category_type": "prodej", "min_area_m2": 40, "rows": ["disposition"]}


@pytest.mark.parametrize("refused, basis", [("mixed_category", BASIS_MIXED), ("empty", BASIS_UNKNOWN)])
def test_a_refused_cohort_has_no_unit(refused: str, basis: str) -> None:
    out, _ = _call({}, _payload(refused=refused, basis=None))
    assert out["metadata"]["price_per_m2_basis"] == basis


def test_a_payload_over_budget_cuts_cells_from_the_tail_and_keeps_the_names_they_use() -> None:
    """truncate.fit would keep the shorter list whole: every town named, few statistics."""
    towns = [str(500_000 + i) for i in range(600)]
    cell = {"col": None, "n": 9, "mean": 1.5, "median": 1.5, "p10": 1.0, "p25": 1.2, "p75": 1.8, "p90": 2.0,
            "small": None}
    cells = [{"keys": [], **cell}] + [{"keys": [t], **cell} for t in towns]
    places = [{"dim": "obec", "key": t, "name": f"Obec {t}"} for t in towns]
    out, _ = _call({"rows": ["obec"]}, _payload(cells=cells, places=places))
    kept = out["data"]["cells"]
    assert len(encode(out)) <= RESULT_BUDGET and kept == cells[:len(kept)] and len(kept) > 100
    assert out["data"]["places"] == places[:len(kept) - 1]
    assert out["metadata"]["truncated"] == {"data.cells": {"kept": len(kept), "total": 601},
                                            "data.places": {"kept": len(kept) - 1, "total": 600}}
