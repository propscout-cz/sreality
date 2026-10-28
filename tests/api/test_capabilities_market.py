"""The market tools (#2062): each places its subject, hands the toolkit its filters, returns the toolkit's envelope."""

from __future__ import annotations

from typing import Any

import pytest

from api.capabilities import market, registry, subject
from api.capabilities.entry import Caller
from api.capabilities.errors import ToolError, ToolErrorCode
from toolkit import ComparableFilters, TargetSpec
from toolkit.measures import RENT_MONTHLY_CZK_M2

CONN = object()
CATS = {"category_main": "byt", "category_type": "prodej"}
POINT = {"lat": 50.08, "lng": 14.42, "area_m2": 60.0, "disposition": "2+kk", "floor": 3}
SPEC = {"lat": 49.2, "lng": 16.6, "area_m2": 75.0, "disposition": "3+1", "floor": 2}
FACTS = {"category_main": "dum", "category_type": "pronajem", "price_czk": 25_000, "condition": "dobry",
         "has_balcony": True, "terrace": None, "furnished": None, "garage": None, "has_lift": None,
         "building_type": "cihla", "obec_kod": 582786, "katastr_kod": None, "country_status": "cz"}


@pytest.fixture()
def held(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Property 7's canonical ad is 70, located at SPEC with FACTS; nothing else is held."""
    monkeypatch.setattr(subject, "canonical_advert", lambda conn, pid: 70 if pid == 7 else None)
    monkeypatch.setattr(subject, "match_listing",
                        lambda conn, lid: {"listing_id": lid, "spec": dict(SPEC)} if lid == 70 else None)
    lookups: dict[str, Any] = {"facts": 0}

    def facts(conn: Any, lid: int) -> dict[str, Any] | None:
        lookups["facts"] += 1
        return dict(FACTS) if lid == 70 else None
    monkeypatch.setattr(subject, "subject_mf_facts", facts)
    return lookups


def _call(name: str, args: dict[str, Any]) -> dict[str, Any]:
    return registry.call(Caller(market=CONN), name, args)


def _error(name: str, args: dict[str, Any]) -> ToolError:
    with pytest.raises(ToolError) as info:
        _call(name, args)
    return info.value


def _capture(monkeypatch: pytest.MonkeyPatch, fn: str, envelope: dict[str, Any] | None = None) -> dict[str, Any]:
    seen: dict[str, Any] = {"envelope": envelope or {"data": {"listings": []}, "metadata": {"tool": fn}}}

    def fake(*args: Any, **kwargs: Any) -> dict[str, Any]:
        seen["args"], seen["kwargs"] = args, kwargs
        return seen["envelope"]
    monkeypatch.setattr(market, fn, fake)
    return seen


# --- the subject ------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("args", [{}, {"property_id": 7, "listing_id": 70}, {"listing_id": 70, "point": POINT}])
def test_the_subject_is_exactly_one_of_property_ad_or_point(held: Any, args: dict[str, Any]) -> None:
    assert _error("find_comparables_relaxed", args).code is ToolErrorCode.INVALID_ARGUMENTS


def test_a_point_must_state_its_categories_because_it_has_none(held: Any) -> None:
    err = _error("find_comparables_relaxed", {"point": POINT, "category_main": "byt"})
    assert err.code is ToolErrorCode.INVALID_ARGUMENTS and "category_type" in err.message


@pytest.mark.parametrize("bad", [{"point": {**POINT, "bogus": 1}}, {"point": {**POINT, "disposition": "2kk"}},
                                 {"point": {**POINT, "lat": 91}}, {"point": {**POINT, "exclude_listing_ids": [1]}},
                                 {"sreality_id": 1}, {"price_min": 1}])
def test_what_a_point_or_a_filter_cannot_carry_is_refused(held: Any, bad: dict[str, Any]) -> None:
    assert _error("find_comparables_relaxed", {"point": POINT, **CATS, **bad}).code is ToolErrorCode.INVALID_ARGUMENTS


def test_a_held_subject_stands_at_its_location_and_its_own_property_is_left_out(held: Any, monkeypatch) -> None:
    seen = _capture(monkeypatch, "find_comparables_relaxed")
    _call("find_comparables_relaxed", {"property_id": 7, "category_type": "prodej"})
    conn, target, filters = seen["args"]
    assert target == TargetSpec(**SPEC, exclude_listing_ids=[70])
    # Unset category_main takes the property's own; the stated category_type stands.
    assert (filters.category_main, filters.category_type) == ("dum", "prodej")


def test_an_explicit_null_category_still_means_every_category(held: Any, monkeypatch) -> None:
    seen = _capture(monkeypatch, "find_comparables_relaxed")
    _call("find_comparables_relaxed", {"listing_id": 70, "category_main": None, "category_type": None})
    assert (seen["args"][2].category_main, seen["args"][2].category_type) == (None, None)
    assert held["facts"] == 0


@pytest.mark.parametrize("args, said", [({"property_id": 8}, "property 8"), ({"listing_id": 71}, "ad 71")])
def test_a_subject_we_do_not_hold_is_not_found_in_the_callers_own_words(held: Any, args: dict[str, Any],
                                                                        said: str) -> None:
    err = _error("find_comparables_relaxed", args)
    assert err.code is ToolErrorCode.NOT_FOUND and said in err.message


@pytest.mark.parametrize("args, said", [({"listing_id": 70}, "ad 70 has no location"),
                                        ({"property_id": 7}, "property 7 (its canonical ad 70) has no location")])
def test_a_held_subject_with_no_location_is_refused(held: Any, monkeypatch, args: dict[str, Any], said: str) -> None:
    monkeypatch.setattr(subject, "match_listing", lambda conn, lid: {"spec": {**SPEC, "lat": None, "lng": None}})
    err = _error("find_comparables_relaxed", args)
    assert err.code is ToolErrorCode.REFUSED and said in err.message


@pytest.mark.parametrize("name", ["find_comparables_relaxed", "analyze_distribution", "find_distribution_outliers",
                                  "estimate_yield"])
def test_recency_without_the_active_gate_is_refused_not_dropped(name: str) -> None:
    args = {"point": POINT, "category_main": "byt", "max_age_days": 30}
    if "category_type" in registry.ENTRIES[name].args.model_fields:
        args["category_type"] = "prodej"
    err = _error(name, args)
    assert err.code is ToolErrorCode.INVALID_ARGUMENTS and "lifecycle='active'" in err.message


# --- each tool returns its toolkit function's own envelope ------------------------------------------------------

def test_find_comparables_relaxed(monkeypatch) -> None:
    seen = _capture(monkeypatch, "find_comparables_relaxed")
    out = _call("find_comparables_relaxed", {"point": POINT, **CATS, "radius_m": 1500, "min_results": 8,
                                             "last_seen_max_days": 3, "max_garden_area": 300.0})
    assert out is seen["envelope"]
    conn, target, filters = seen["args"]
    assert conn is CONN and target == TargetSpec(**POINT) and seen["kwargs"] == {"min_results": 8}
    assert filters == ComparableFilters(**CATS, radius_m=1500, last_seen_max_days=3, max_garden_area=300.0)


@pytest.mark.parametrize("name, fn", [("analyze_distribution", "analyze_distribution"),
                                      ("find_distribution_outliers", "find_distribution_outliers")])
def test_distribution_and_outliers_read_the_strict_cohort_their_arguments_select(monkeypatch, name, fn) -> None:
    rows = [{"listing_id": 1, "price_per_m2": 90_000.0}]
    cohort = _capture(monkeypatch, "find_comparables", {"data": {"listings": rows}, "metadata": {}})
    seen = _capture(monkeypatch, fn)
    assert _call(name, {"point": POINT, **CATS, "field": "price_czk", "has_lift": True}) is seen["envelope"]
    assert cohort["args"][2] == ComparableFilters(**CATS, has_lift=True)
    assert rows in seen["args"] and seen["kwargs"]["field"] == "price_czk"


def test_outliers_hand_on_their_fence_and_history_switch(monkeypatch) -> None:
    _capture(monkeypatch, "find_comparables", {"data": {"listings": []}, "metadata": {}})
    seen = _capture(monkeypatch, "find_distribution_outliers")
    _call("find_distribution_outliers", {"point": POINT, **CATS, "iqr_multiplier": 3, "investigate_history": False})
    assert seen["kwargs"] == {"field": "price_per_m2", "iqr_multiplier": 3.0, "investigate_history": False}


def test_market_velocity_takes_lifecycle_as_its_own_argument_defaulting_to_every_ad(monkeypatch) -> None:
    seen = _capture(monkeypatch, "compute_market_velocity")
    assert _call("compute_market_velocity", {"point": POINT, **CATS}) is seen["envelope"]
    assert seen["kwargs"] == {"lifecycle": "all", "trend_split_days": 7}
    assert seen["args"][2] == ComparableFilters(**CATS)
    _call("compute_market_velocity", {"point": POINT, **CATS, "lifecycle": "active", "trend_split_days": 14})
    assert seen["kwargs"] == {"lifecycle": "active", "trend_split_days": 14}


@pytest.mark.parametrize("dropped", ["max_age_days", "tom_days_min", "last_seen_min_days"])
def test_market_velocity_offers_no_filter_it_would_drop(dropped: str) -> None:
    assert _error("compute_market_velocity", {"point": POINT, **CATS, dropped: 3}).code is ToolErrorCode.INVALID_ARGUMENTS


def test_listing_velocity_is_one_ads(monkeypatch) -> None:
    seen = _capture(monkeypatch, "compute_listing_velocity")
    assert _call("compute_listing_velocity", {"listing_id": 70, "radius_m": 2000}) is seen["envelope"]
    assert seen["kwargs"] == {"listing_id": 70, "radius_m": 2000, "disposition_match": "exact", "lifecycle": "all"}
    assert _error("compute_listing_velocity", {"property_id": 7}).code is ToolErrorCode.INVALID_ARGUMENTS


@pytest.mark.parametrize("kind, deal", [("rent", "pronajem"), ("sale", "prodej")])
def test_yield_takes_its_deal_type_from_its_kind(monkeypatch, kind: str, deal: str) -> None:
    seen = _capture(monkeypatch, "estimate_yield")
    out = _call("estimate_yield", {"point": POINT, "category_main": "byt", "estimate_kind": kind,
                                   "purchase_price_czk": 5_000_000})
    assert out is seen["envelope"]
    assert seen["args"][2] == ComparableFilters(category_main="byt", category_type=deal)
    assert seen["args"][3] == 5_000_000 and seen["kwargs"]["estimate_kind"] == kind
    assert _error("estimate_yield", {"point": POINT, "category_main": "byt",
                                     "category_type": "prodej"}).code is ToolErrorCode.INVALID_ARGUMENTS


def test_a_yield_over_a_cohort_with_no_single_unit_is_refused(monkeypatch) -> None:
    from toolkit.measures import MeasureBasisError

    def mixed(*a: Any, **k: Any) -> dict[str, Any]:
        raise MeasureBasisError("the cohort has no single per-m² basis")
    monkeypatch.setattr(market, "estimate_yield", mixed)
    err = _error("estimate_yield", {"point": POINT, "category_main": None})
    assert err.code is ToolErrorCode.REFUSED and "basis" in err.message


def test_reference_rent_reads_the_held_ads_own_facts_and_names_its_unit(held: Any, monkeypatch) -> None:
    handed: dict[str, Any] = {}

    def fake(conn: Any, **facts: Any) -> dict[str, Any]:
        handed.update(facts)
        return {"status": "ok", "base_per_m2": 310.0, "engine": "mf_sql_v1"}
    monkeypatch.setattr(market, "compute_reference_rent", fake)
    out = _call("compute_reference_rent", {"property_id": 7})
    assert handed == {**FACTS, "disposition": "3+1", "area_m2": 75.0}
    assert out["data"]["base_per_m2"] == 310.0
    assert out["metadata"]["price_per_m2_basis"] == RENT_MONTHLY_CZK_M2
    assert (out["metadata"]["result_count"], out["metadata"]["filters_used"]["listing_id"]) == (1, 70)
    assert _error("compute_reference_rent", {"point": POINT}).code is ToolErrorCode.INVALID_ARGUMENTS


def test_reference_rent_says_so_when_the_map_has_none(held: Any, monkeypatch) -> None:
    monkeypatch.setattr(market, "compute_reference_rent", lambda conn, **facts: None)
    out = _call("compute_reference_rent", {"listing_id": 70})
    assert out["data"] is None and out["metadata"]["result_count"] == 0


@pytest.mark.parametrize("band, missing", [({"floor_band": 1}, "floor"), ({"area_band_pct": 0.3}, "area_m2"),
                                           ({"disposition_match": "loose"}, "disposition")])
def test_a_band_asked_for_without_the_subjects_own_value_is_refused_not_dropped(
        held: Any, monkeypatch, band: dict[str, Any], missing: str) -> None:
    bare = {k: v for k, v in POINT.items() if k != missing}
    err = _error("find_comparables_relaxed", {"point": bare, **CATS, **band})
    assert err.code is ToolErrorCode.REFUSED and missing in err.message
    monkeypatch.setattr(subject, "match_listing", lambda conn, lid: {"spec": {**SPEC, missing: None}})
    err = _error("compute_market_velocity", {"property_id": 7, **band})
    assert err.code is ToolErrorCode.REFUSED and "property 7" in err.message and missing in err.message


def test_a_band_left_at_its_default_or_any_never_refuses(held: Any, monkeypatch) -> None:
    _capture(monkeypatch, "find_comparables_relaxed")
    _call("find_comparables_relaxed", {"point": {"lat": 50.0, "lng": 14.0}, **CATS, "disposition_match": "any"})


@pytest.mark.parametrize("extra", [{"area_m2": 60.0}, {"disposition": "2+kk"}, {"floor": 3}])
def test_describe_neighborhood_refuses_point_attributes_it_cannot_apply(extra: dict[str, Any]) -> None:
    args = {"point": {"lat": 50.08, "lng": 14.42, **extra}, **CATS}
    assert _error("describe_neighborhood", args).code is ToolErrorCode.INVALID_ARGUMENTS


def test_describe_neighborhood_says_its_recency_window_needs_no_lifecycle() -> None:
    schema = registry.ENTRIES["describe_neighborhood"].input_schema["properties"]["max_age_days"]
    assert "lifecycle" not in schema["description"]


def test_each_description_states_the_categories_its_point_must_carry_and_the_unit_key_it_returns() -> None:
    for name, entry in registry.ENTRIES.items():
        fields = entry.args.model_fields
        if "point" in fields:
            for c in ("category_main", "category_type"):
                assert (c in entry.description) is (c in fields), (name, c)
    assert "estimate_kind" in registry.ENTRIES["estimate_yield"].description
    for name in ("analyze_distribution", "find_distribution_outliers"):   # their envelopes carry `basis`
        assert "price_per_m2_basis" not in registry.ENTRIES[name].description, name
    for name in ("find_comparables_relaxed", "estimate_yield"):
        assert "price_per_m2_basis" in registry.ENTRIES[name].description, name
    assert "sreality" in registry.ENTRIES["find_distribution_outliers"].description


def test_describe_neighborhood_takes_the_subjects_place_and_its_four_filters(held: Any, monkeypatch) -> None:
    seen = _capture(monkeypatch, "describe_neighborhood")
    assert _call("describe_neighborhood", {"listing_id": 70, "radius_m": 800}) is seen["envelope"]
    assert seen["kwargs"] == {"lat": 49.2, "lng": 16.6, "radius_m": 800, "max_age_days": None,
                              "category_main": "dum", "category_type": "pronajem"}
    assert _error("describe_neighborhood", {"listing_id": 70, "has_lift": True}).code is ToolErrorCode.INVALID_ARGUMENTS
