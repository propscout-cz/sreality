"""The Stats dashboard for an agent (#2064): `public.browse_stats_properties` (its latest definition, migration 694)
called with exactly the arguments the dashboard sends for the same Browse filters (frontend/src/lib/queries.ts
buildBrowseStatsArgs over resolveBrowsePrefilters, setScope.ts scopeArgs, stats.ts fetchStatsPivot), under the
reader's own login, so the dismissals and the curated-set scope are theirs; its payload comes back as it answers."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from types import MappingProxyType
from typing import Any, Literal, Self

import psycopg
from pydantic import Field, model_validator

from api.capabilities.truncate import RESULT_BUDGET, encode, heads
from api.filter_args import INT4_MAX, Args, MapAreaArgs, filter_values
from api.location_filter import area_where
from api.property_search import SearchFilters, lens_args
from toolkit import _now_iso
from toolkit import filter_registry as fr
from toolkit import stats_registry as sr
from toolkit.measures import BASIS_MIXED, BASIS_UNKNOWN

# Every parameter of browse_stats_properties the dashboard sends, by the SQL type it is bound as, in signature order.
# With UNMAPPED it is the whole signature (tests/api/test_stats_pivot_census.py).
PARAMS: Mapping[str, str] = MappingProxyType({
    "districts_filter": "text[]", "dispositions_filter": "text[]", "price_min_filter": "integer",
    "price_max_filter": "integer", "area_min_filter": "integer", "area_max_filter": "integer",
    "last_seen_min_days": "integer", "last_seen_max_days": "integer", "first_seen_min_days": "integer",
    "first_seen_max_days": "integer", "tom_days_min": "integer", "tom_days_max": "integer",
    "has_balcony_filter": "boolean", "has_lift_filter": "boolean", "has_parking_filter": "boolean",
    "furnished_filter": "text[]", "terrace_filter": "boolean", "cellar_filter": "boolean", "garage_filter": "boolean",
    "category_sub_cb_filter": "integer", "building_type_filter": "text[]", "category_main_filter": "text[]",
    "category_type_filter": "text", "bbox_west": "double precision", "bbox_south": "double precision",
    "bbox_east": "double precision", "bbox_north": "double precision", "ownership_filter": "text[]",
    "estate_area_min_filter": "double precision", "estate_area_max_filter": "double precision",
    "usable_area_min_filter": "double precision", "usable_area_max_filter": "double precision",
    "parking_lots_min_filter": "integer", "garden_area_min_filter": "double precision",
    "garden_area_max_filter": "double precision", "condition_match_filter": "text[]", "city_pop_min": "integer",
    "city_pop_max": "integer", "price_per_m2_min": "double precision", "price_per_m2_max": "double precision",
    "portal_filter": "text[]", "mf_gross_yield_pct_min": "double precision",
    "mf_gross_yield_pct_max": "double precision", "near_pop_5km_min": "integer", "near_pop_15km_min": "integer",
    "near_jobs_5km_min": "double precision", "near_jobs_15km_min": "double precision",
    "near_youth_5km_min": "double precision", "near_youth_15km_min": "double precision",
    "near_overall_5km_min": "double precision", "near_overall_15km_min": "double precision",
    "districts_excluded_filter": "boolean[]", "subtype_filter": "text[]", "recently_added_days": "integer",
    "recently_changed_days": "integer", "obec_ids_filter": "bigint[]", "districts_levels": "text[]",
    "districts_ids": "bigint[]", "price_change_count_min": "integer", "price_change_window_days": "integer",
    "total_price_change_pct_filter": "double precision", "include_no_price": "boolean",
    "property_ids_filter": "bigint[]", "hide_dismissed": "boolean", "portal_excluded_filter": "text[]",
    "portal_excluded_active_only": "boolean", "tom_observed_only": "boolean", "spec": "jsonb", "tags_set": "text",
    "tags_in": "bigint[]", "tags_out": "bigint[]", "collections_set": "text", "collections_in": "bigint[]",
    "collections_out": "bigint[]", "pipeline_set": "text", "pipeline_in": "bigint[]", "pipeline_out": "bigint[]",
    "energy_rating_filter": "text[]", "status_filter": "text", "circle_lat": "double precision",
    "circle_lng": "double precision", "circle_radius_m": "double precision",
})
_PARKED = ("Rule 14: condition scoring is parked and no surface filters on its levels; the registry retires this "
           "bound (filter_compiler.RETIRED_FILTERS) and the dashboard never sends it.")
# The parameters never sent, each with why: the function's default (null) applies.
UNMAPPED: Mapping[str, str] = MappingProxyType({p: _PARKED for p in (
    "building_condition_level_min", "building_condition_level_max",
    "apartment_condition_level_min", "apartment_condition_level_max")})

# Registry id -> the parameter the dashboard sends its value as, unchanged but an empty list, which it sends as null
# (`[]` would match nothing).
_AS_IS: Mapping[str, str] = MappingProxyType({
    "status": "status_filter", "category_main_in": "category_main_filter", "category_type": "category_type_filter",
    "category_sub_cb": "category_sub_cb_filter", "dispositions": "dispositions_filter", "subtype": "subtype_filter",
    # queries.ts adPortals: the portals as set, a broker scope (which no agent has) aside.
    "portals": "portal_filter", "portals_excluded": "portal_excluded_filter",
    "portals_excluded_active_only": "portal_excluded_active_only", "condition_match": "condition_match_filter",
    "energy_rating_match": "energy_rating_filter", "furnished": "furnished_filter", "ownership": "ownership_filter",
    "has_balcony": "has_balcony_filter", "has_lift": "has_lift_filter", "has_parking": "has_parking_filter",
    "terrace": "terrace_filter", "cellar": "cellar_filter", "garage": "garage_filter",
    "min_parking_lots": "parking_lots_min_filter", "min_price_czk": "price_min_filter",
    "max_price_czk": "price_max_filter", "include_no_price": "include_no_price",
    "min_price_per_m2": "price_per_m2_min", "max_price_per_m2": "price_per_m2_max",
    "min_mf_gross_yield_pct": "mf_gross_yield_pct_min", "max_mf_gross_yield_pct": "mf_gross_yield_pct_max",
    "min_area_m2": "area_min_filter", "max_area_m2": "area_max_filter",
    "min_estate_area": "estate_area_min_filter", "max_estate_area": "estate_area_max_filter",
    "min_usable_area": "usable_area_min_filter", "max_usable_area": "usable_area_max_filter",
    "min_garden_area": "garden_area_min_filter", "max_garden_area": "garden_area_max_filter",
    "min_city_population": "city_pop_min", "max_city_population": "city_pop_max",
    "total_price_change_pct": "total_price_change_pct_filter",
    **{fid: fid for fid in (
        "tom_days_min", "tom_days_max", "tom_observed_only", "last_seen_min_days", "last_seen_max_days",
        "first_seen_min_days", "first_seen_max_days", "recently_added_days", "recently_changed_days",
        "near_pop_5km_min", "near_pop_15km_min", "near_jobs_5km_min", "near_jobs_15km_min", "near_youth_5km_min",
        "near_youth_15km_min", "near_overall_5km_min", "near_overall_15km_min", "price_change_count_min",
        "price_change_window_days")},
})
_AREA = ("bbox_west", "bbox_south", "bbox_east", "bbox_north", "circle_lat", "circle_lng", "circle_radius_m")
_CALL = "SELECT public.browse_stats_properties({})".format(
    ", ".join(f"{p} => %({p})s::{t}" for p, t in PARAMS.items()))


def whole_m2(model: type[Args], fid: str) -> Any:
    """`model`'s area bound `fid` as the dashboard takes it: its parameter is an integer, so a fraction is refused as
    an invalid argument, never rounded (40.5 and 41 select different properties)."""
    return Field(None, ge=0, le=INT4_MAX,
                 description=f"{model.model_fields[fid].description} Whole m²: the dashboard's bound is an integer.")


def _dimension(d: sr.Dimension) -> str:
    only = f", {' and '.join(d.categories)} only" if d.categories else ""
    return f"{d.id} ({d.label_en}{only})"


_DIMENSION_IDS = tuple(d.id for d in sr.DIMENSIONS)
_MEASURE_IDS = tuple(m.id for m in sr.MEASURES)
_ONE_STATUS = " and ".join(m.id for m in sr.MEASURES if m.single_status)


class AggregateArgs(SearchFilters, MapAreaArgs):
    min_area_m2: int | None = whole_m2(SearchFilters, "min_area_m2")
    max_area_m2: int | None = whole_m2(SearchFilters, "max_area_m2")
    rows: list[Literal[_DIMENSION_IDS]] = Field(
        [], max_length=sr.MAX_ROWS, description=f"Row dimensions, outermost first, at most {sr.MAX_ROWS}; none = the "
        f"cohort total alone. {'; '.join(_dimension(d) for d in sr.DIMENSIONS)}. A place keys on its RÚIAN code as "
        "text, a band on its lower bound. E.g. disposition, obec, area_band_10; new build and renovated are "
        "condition_match values, a panel building is building_material.")
    cols: list[Literal[_DIMENSION_IDS]] = Field(
        [], max_length=sr.MAX_COLS, description=f"At most {sr.MAX_COLS} column dimension (a series across every row "
        "level), any dimension rows takes and not among them.")
    measure: Literal[_MEASURE_IDS] = Field(
        "ppm2", description=f"The value described: {'; '.join(f'{m.id} = {m.label_en}' for m in sr.MEASURES)}. ppm2 is "
        f"THE per-m² measure, in the unit basis names; {_ONE_STATUS} needs one status in the sample.")

    @model_validator(mode="after")
    def _one_spec(self) -> Self:
        sr.validate_spec(self.spec)   # a SpecError is a ValueError: an invalid argument, never a refused pivot
        return self

    @property
    def spec(self) -> dict[str, Any]:
        return {"rows": list(self.rows), "cols": list(self.cols), "measure": self.measure}


def _sent(value: Any) -> Any:
    return None if isinstance(value, list) and not value else value


def _intersect(a: list[int] | None, b: list[int] | None) -> list[int] | None:
    """queries.ts intersectPrefilters: null is no constraint."""
    if a is None:
        return b
    if b is None:
        return a
    keep = set(b)
    return [i for i in a if i in keep]


def _ids(conn: psycopg.Connection, sql: str, params: Sequence[Any] = ()) -> list[int]:
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return [int(r[0]) for r in cur.fetchall()]


def pivot_args(conn: psycopg.Connection, args: AggregateArgs, *,
               property_ids: Sequence[int] | None = None) -> dict[str, Any]:
    """buildBrowseStatsArgs for `args` over its resolved prefilters plus its spec: exactly PARAMS' keys. `property_ids`
    narrows the cohort as a further id allowlist."""
    values = filter_values(args)
    out = {param: _sent(values[fid]) for fid, param in _AS_IS.items()}
    chips = args.districts or []
    # The four parallel arrays the function unnests; all null with no chip.
    out.update({"districts_filter": [c.name for c in chips] or None,
                "districts_excluded_filter": [c.excluded for c in chips] or None,
                "districts_levels": [c.level for c in chips] or None, "districts_ids": [c.id for c in chips] or None})
    material = args.building_material
    out["building_type_filter"] = fr.building_material_values(material) if material else None
    out.update(lens_args(args))
    out.update({**dict.fromkeys(_AREA), **area_where(args.bounds, args.circle, "l")[1]})
    # resolveBrowsePrefilters: the city rules to their towns, with_estimates to the user's estimated properties (under
    # their RLS); a rule or allowlist that matched nothing travels as [] and the function answers `empty`.
    rules = values["city_index_rules"]
    towns = _ids(conn, "SELECT curated_cities_matching(%s::jsonb)", (json.dumps(rules),)) if rules else None
    out["obec_ids_filter"] = towns
    estimated = _ids(conn, "SELECT property_id FROM property_estimates_public") if args.with_estimates else None
    out["property_ids_filter"] = _intersect(estimated, None if property_ids is None else list(property_ids))
    out["spec"] = json.dumps(args.spec)
    return out


def run_pivot(conn: psycopg.Connection, values: Mapping[str, Any]) -> dict[str, Any]:
    """The dashboard function's answer for `values` (every PARAMS key), verbatim."""
    with conn.cursor() as cur:
        cur.execute(_CALL, {p: values[p] for p in PARAMS})
        return cur.fetchone()[0]


def payload_basis(payload: Mapping[str, Any]) -> str:
    """The pivot's one per-m² unit (rule 23): its basis, else 'mixed' for a mixed-category cohort, else 'unknown'."""
    return payload.get("basis") or (BASIS_MIXED if payload.get("refused") == "mixed_category" else BASIS_UNKNOWN)


def _within_budget(result: dict[str, Any], args: AggregateArgs) -> dict[str, Any]:
    """`result` cut to fit by its cells, never its names first: truncate.fit keeps the shorter list whole, so a place
    spec would keep every name and lose the statistics. The cells keep their head (the totals lead, the function's
    order) and places the names a kept cell uses; metadata.truncated records each cut as fit records it."""
    if len(encode(result)) <= RESULT_BUDGET:
        return result
    data = result["data"]
    cells, places = data["cells"], data["places"]

    def cut(n: int) -> dict[str, Any]:
        used = {(dim, key) for c in cells[:n] for dim, key in zip(args.rows, c["keys"])}
        used |= {(args.cols[0], c["col"]) for c in cells[:n] if args.cols}
        kept = {"cells": cells[:n], "places": [p for p in places if (p["dim"], p["key"]) in used]}
        truncated = {f"data.{k}": {"kept": len(v), "total": len(data[k])} for k, v in kept.items()
                     if len(v) < len(data[k])}
        return {**result, "data": {**data, **kept}, "metadata": {**result["metadata"], "truncated": truncated}}
    head = heads({"data.cells": cells}, lambda h: len(encode(cut(h["data.cells"]))) <= RESULT_BUDGET)
    return result if head is None else cut(head["data.cells"])   # None: nothing fits, which fit refuses


def aggregate_properties(conn: psycopg.Connection, args: AggregateArgs) -> dict[str, Any]:
    """The dashboard's pivot for these filters and spec, as the function answers it."""
    payload = run_pivot(conn, pivot_args(conn, args))
    return _within_budget({
        "data": payload,
        "metadata": {
            "tool": "aggregate_properties",
            "filters_used": args.model_dump(mode="json", by_alias=True, exclude_defaults=True),
            "result_count": payload.get("sample", 0),
            "queried_at": _now_iso(),
            "data_freshness": None,
            "price_per_m2_basis": payload_basis(payload),
        },
    }, args)
