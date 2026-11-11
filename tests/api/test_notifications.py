"""Hermetic tests for the notifications backend (Phase U2.7).

Covers WatchdogFilterSpec validation, the SQL-clause generator and the
three producers over fake connections; the worker's notifications lane, their
one caller, is tested in tests/scraper/test_realtime_worker.py.
"""

from __future__ import annotations

import re

import pytest

pytest.importorskip("pydantic")

from pydantic import ValidationError

from api.notifications import (
    ON_EVENT_CONFLICT,
    WatchdogFilterSpec,
    _build_match_clauses,
)


def test_filter_spec_defaults() -> None:
    """category_main is multiselect now: a blank-save watchdog carries no
    category_main constraint (matches every category). Deal type still
    defaults to rent."""
    spec = WatchdogFilterSpec()
    assert spec.category_main_in is None
    assert spec.category_type == "pronajem"


def test_filter_spec_bounds_must_be_an_ordered_box() -> None:
    """`bounds` is the SPA's MapBounds: west < east and south < north, or a 422 —
    an inverted box would match nothing and mute the watchdog without a word."""
    with pytest.raises(ValidationError):
        WatchdogFilterSpec(bounds={"west": 14.5, "south": 50.0, "east": 14.3, "north": 50.2})
    with pytest.raises(ValidationError):
        WatchdogFilterSpec(bounds={"west": 14.3, "south": 50.2, "east": 14.5, "north": 50.0})
    with pytest.raises(ValidationError):
        WatchdogFilterSpec(bounds={"west": 14.3, "south": 50.0, "east": 14.5})
    s = WatchdogFilterSpec(bounds={"west": 14.3, "south": 50.0, "east": 14.5, "north": 50.2})
    assert s.bounds is not None
    assert (s.bounds.west, s.bounds.north) == (14.3, 50.2)


def test_build_clauses_default_category_main_unconstrained_type_rent() -> None:
    # category_main is multiselect now; a blank spec carries no category_main
    # constraint (matches every category). Deal type still defaults to rent.
    spec = WatchdogFilterSpec()
    where, params = _build_match_clauses(spec)
    assert not any("l.category_main" in w for w in where)
    assert "category_main_in" not in params
    assert "l.category_type = %(category_type)s" in where
    assert params["category_type"] == "pronajem"


def test_build_clauses_emits_category_main_in_membership() -> None:
    spec = WatchdogFilterSpec(category_main_in=["byt", "dum"])
    where, params = _build_match_clauses(spec)
    assert "l.category_main = ANY(%(category_main_in)s)" in where
    assert params["category_main_in"] == ["byt", "dum"]


def test_build_clauses_migrates_legacy_scalar_category_main() -> None:
    # Specs saved before the multiselect split carry a scalar "category_main"
    # key; the before-validator lifts it to category_main_in so the saved
    # watchdog keeps matching instead of silently widening to all categories.
    spec = WatchdogFilterSpec(**{"category_main": "dum"})
    assert spec.category_main_in == ["dum"]
    where, params = _build_match_clauses(spec)
    # A one-element list is an equality (the planner rule Browse follows).
    assert "l.category_main = %(category_main_in)s" in where
    assert params["category_main_in"] == "dum"


def test_build_clauses_skips_unset_box() -> None:
    spec = WatchdogFilterSpec()
    where, params = _build_match_clauses(spec)
    assert not any("l.lat" in w or "l.lng" in w for w in where)
    assert not any(k.startswith("bbox_") for k in params)


_AREA_CALL = (
    "public.in_map_area(l.lat, l.lng, %(bbox_west)s::float8, %(bbox_south)s::float8, %(bbox_east)s::float8, "
    "%(bbox_north)s::float8, %(circle_lat)s::float8, %(circle_lng)s::float8, %(circle_radius_m)s::float8)"
)


def test_build_clauses_emits_the_map_box_when_set() -> None:
    """Rule 16: the box is tested by the one predicate Browse's list source and both
    stats RPCs test (`in_map_area`, migration 694), never a shape of its own."""
    spec = WatchdogFilterSpec(bounds={"west": 14.40, "south": 50.06, "east": 14.44, "north": 50.10})
    where, params = _build_match_clauses(spec)
    assert _AREA_CALL in where
    assert not any("ST_DWithin" in w or "l.lng >=" in w for w in where)
    # Anchored: `sl.geom` is the W5 consumer rule's own probe into listing_location,
    # which is not `properties_public.geom` and must not be flagged as one.
    assert not any(re.search(r"(?<![a-z_])l\.geom", w) for w in where)
    assert (params["bbox_west"], params["bbox_south"], params["bbox_east"], params["bbox_north"]) == (
        14.40, 50.06, 14.44, 50.10)
    assert (params["circle_lat"], params["circle_lng"], params["circle_radius_m"]) == (None, None, None)


def test_build_clauses_emits_the_map_circle_when_set() -> None:
    """A circle saved from Browse is the same predicate with the circle's arguments: a true circle, not the
    square around it."""
    spec = WatchdogFilterSpec(circle={"lat": 50.08, "lng": 14.42, "radius_m": 1500})
    where, params = _build_match_clauses(spec)
    assert _AREA_CALL in where
    assert (params["circle_lat"], params["circle_lng"], params["circle_radius_m"]) == (50.08, 14.42, 1500)
    assert params["bbox_west"] is None


def test_filter_spec_map_area_is_a_box_or_a_circle_never_both() -> None:
    with pytest.raises(ValidationError):
        WatchdogFilterSpec(bounds={"west": 14.3, "south": 50.0, "east": 14.5, "north": 50.2},
                           circle={"lat": 50.08, "lng": 14.42, "radius_m": 1500})
    for radius in (50, 20_000):  # outside Browse's slider, the registry's radius_m domain
        with pytest.raises(ValidationError):
            WatchdogFilterSpec(circle={"lat": 50.08, "lng": 14.42, "radius_m": radius})


def test_build_clauses_city_quality_is_one_obec_predicate() -> None:
    """W5 (migration 436): the matcher renders ONE `obec_id = ANY(...)` predicate.

    Before W5 this branch built a nested EXISTS tree against `curated_cities_public`
    joined on the precomputed `home_city_id`, and the same rule was implemented three
    times over (here, in `browse_stats_properties`, and in the SPA prefilter RPC).
    Migration 374's own header records two divergences found between those copies,
    including an operator-chosen op silently re-interpreted as `>=`.

    Now one SQL function owns rule evaluation and every consumer reduces to the same
    form — a form with nothing left to diverge on (rule 16).

    `ARRAY(SELECT ...)` and not `IN (SELECT ...)`: the former forces a
    once-per-statement InitPlan, the latter can degrade into the per-row correlated
    SubPlan that made this predicate cost 1,778,259 blocks.
    """
    spec = WatchdogFilterSpec(
        city_index_rules=[{"index_name": "safety", "value": 60, "op": ">="}],
    )
    where, params = _build_match_clauses(spec)
    city = [w for w in where if "curated_cities_matching" in w]
    assert len(city) == 1, f"expected exactly one city-quality predicate, got {city}"
    clause = city[0]
    assert "l.obec_id = ANY (ARRAY(SELECT curated_cities_matching(" in clause
    # The whole point: no nested EXISTS tree, no per-rule subquery, no interpolated op.
    assert "EXISTS" not in clause
    assert "curated_cities_public" not in clause
    assert "home_city_id" not in clause
    # The rules travel as ONE bound jsonb parameter, nothing string-interpolated.
    assert params["city_index_rules"].obj == [
        {"index_name": "safety", "value": 60, "op": ">="}
    ]


def test_build_clauses_near_city_proximity_is_retired_loudly() -> None:
    """A retired filter that silently returns everything is worse than one that errors.

    `near_city_proximity` had zero UI widgets, 0 of 7 filter_presets and 0 of 2
    notification_subscriptions, and was never load-tested. Dropping the branch without
    a raise would WIDEN every cohort that still carried the value.
    """
    spec = WatchdogFilterSpec(near_city_proximity={"radius_km": 10, "index_rules": []})
    with pytest.raises(ValueError, match="near_city_proximity is retired"):
        _build_match_clauses(spec)


def test_build_clauses_property_grain_derived_predicates() -> None:
    """The merged price-change filters (migration 173) render against the
    property-grain columns properties_public exposes; the window picks the
    precomputed count column."""
    spec = WatchdogFilterSpec(price_change_count_min=2)
    where, params = _build_match_clauses(spec)
    assert "l.price_change_count >= %(price_change_count_min)s" in where
    assert params["price_change_count_min"] == 2

    spec = WatchdogFilterSpec(price_change_count_min=3, price_change_window_days=90)
    where, params = _build_match_clauses(spec)
    assert "l.price_change_count_90d >= %(price_change_count_min)s" in where
    assert params["price_change_count_min"] == 3


def test_build_clauses_total_price_change_sign_flips_direction() -> None:
    """Negative threshold = 'dropped at least', positive = 'rose at least';
    zero is treated as unset."""
    spec = WatchdogFilterSpec(total_price_change_pct=-10.0)
    where, params = _build_match_clauses(spec)
    assert "l.total_price_change_pct <= %(total_price_change_pct)s" in where
    assert params["total_price_change_pct"] == -10.0

    spec = WatchdogFilterSpec(total_price_change_pct=5.0)
    where, params = _build_match_clauses(spec)
    assert "l.total_price_change_pct >= %(total_price_change_pct)s" in where

    spec = WatchdogFilterSpec(total_price_change_pct=0.0)
    where, params = _build_match_clauses(spec)
    assert not any("total_price_change_pct" in w for w in where)


def test_spec_ignores_retired_price_history_keys() -> None:
    """Stored specs predating migration 173 carry the per-direction keys;
    pydantic's extra='ignore' default must drop them without raising."""
    spec = WatchdogFilterSpec(**{
        "distinct_site_count_min": 2,
        "price_drop_count_min": 3,
        "price_rise_count_min": 1,
        "max_price_drop_pct_min": 10.0,
    })
    where, params = _build_match_clauses(spec)
    assert not any("distinct_site_count" in w for w in where)
    assert not any("price_drop_count" in w for w in where)
    assert "distinct_site_count_min" not in params


def test_spec_model_dump_keeps_building_material_and_garden() -> None:
    """create/update_subscription persist `model_dump()`. Merge f2d7b359 dropped these
    fields from the model, so extra='ignore' discarded them on every save and the
    watchdog matched wider than the Browse view it was made from."""
    dumped = WatchdogFilterSpec(min_garden_area=100, building_material=["cihla"]).model_dump()
    assert dumped["min_garden_area"] == 100
    assert dumped["building_material"] == ["cihla"]


def test_build_clauses_building_material_ostatni_expands_to_the_rest_of_the_canon() -> None:
    from toolkit.filter_registry import COLUMN_CANONICAL_VALUES

    where, params = _build_match_clauses(WatchdogFilterSpec(building_material=["ostatni"]))
    assert "l.building_type = ANY(%(building_material)s)" in where
    assert params["building_material"] == [
        v for v in COLUMN_CANONICAL_VALUES["building_type"]
        if v not in {"cihla", "panel", "smisena"}
    ]
    # A blob saved before merge f2d7b359 holds one scalar bucket.
    assert WatchdogFilterSpec(building_material="cihla").building_material == ["cihla"]
    where, params = _build_match_clauses(WatchdogFilterSpec(building_material=[]))
    assert not any("building_type" in w for w in where)


def test_build_clauses_garden_area_bounds() -> None:
    spec = WatchdogFilterSpec(min_garden_area=100.0, max_garden_area=800.0)
    where, params = _build_match_clauses(spec)
    assert "l.garden_area >= %(min_garden_area)s" in where
    assert "l.garden_area <= %(max_garden_area)s" in where
    assert params["min_garden_area"] == 100.0
    assert params["max_garden_area"] == 800.0


def test_build_clauses_handles_price_and_area_bounds() -> None:
    spec = WatchdogFilterSpec(
        min_price_czk=15_000,
        max_price_czk=30_000,
        min_area_m2=40.0,
        max_area_m2=80.0,
    )
    where, params = _build_match_clauses(spec)
    assert "l.price_czk >= %(min_price_czk)s" in where
    assert "l.price_czk <= %(max_price_czk)s" in where
    assert "l.area_m2 >= %(min_area_m2)s" in where
    assert "l.area_m2 <= %(max_area_m2)s" in where
    assert params["min_price_czk"] == 15_000
    assert params["max_price_czk"] == 30_000


def test_build_clauses_tri_state_amenities() -> None:
    spec = WatchdogFilterSpec(
        has_balcony=True,
        terrace=False,
        garage=None,  # explicit "any" — no clause
    )
    where, params = _build_match_clauses(spec)
    assert "l.has_balcony = %(has_balcony)s" in where
    assert "l.terrace = %(terrace)s" in where
    assert not any("l.garage = " in w for w in where)
    assert params["has_balcony"] is True
    assert params["terrace"] is False


def test_build_clauses_dispositions_use_any() -> None:
    """Dispositions are a multi-select; SQL uses ANY() for index-friendly
    lookups against `l.disposition`."""
    spec = WatchdogFilterSpec(dispositions=["2+kk", "2+1", "3+kk"])
    where, params = _build_match_clauses(spec)
    assert "l.disposition = ANY(%(dispositions)s)" in where
    assert params["dispositions"] == ["2+kk", "2+1", "3+kk"]


def test_build_clauses_subtype_use_any() -> None:
    """Subtype is a portal-agnostic multi-select; ANY() against l.subtype."""
    spec = WatchdogFilterSpec(subtype=["rodinny_dum", "vila"])
    where, params = _build_match_clauses(spec)
    assert "l.subtype = ANY(%(subtype)s)" in where
    assert params["subtype"] == ["rodinny_dum", "vila"]


def test_build_clauses_no_subtype_by_default() -> None:
    """Default spec leaves subtype unset → no subtype clause emitted."""
    where, params = _build_match_clauses(WatchdogFilterSpec())
    assert "subtype" not in params
    assert not any("l.subtype" in c for c in where)


def test_build_clauses_place_chips_are_one_code_predicate() -> None:
    """Rule 16, in its W3 form: the matcher tests a chip exactly the way Browse,
    Stats and the map do — `<level>_id = any(codes)`, one array per level.

    RED by: reintroducing a name ILIKE, a `place_search_text` arm or a `context`
    narrow here. The six copies of the old five-predicate tree are what let this
    matcher and Browse answer the same saved filter differently."""
    spec = WatchdogFilterSpec(
        districts=[
            {"name": "Jihlava", "level": "obec", "id": 586846},
            {"name": "okres Jihlava", "level": "okres", "id": 3707},
            {"name": "Kraj Vysočina", "level": "kraj", "id": 108},
            {"name": "Žižkov", "level": "cast_obce", "id": 490067},
            {"name": "Na Rybníčkách", "context": "Pardubice", "level": "ulice", "id": 666742},
        ],
    )
    where, params = _build_match_clauses(spec)
    clause = next(w for w in where if "district_codes" in w)
    assert clause == (
        "(l.region_id = ANY(%(district_codes_kraj)s) "
        "OR l.okres_id = ANY(%(district_codes_okres)s) "
        "OR l.obec_id = ANY(%(district_codes_obec)s) "
        "OR l.cast_obce_id = ANY(%(district_codes_cast_obce)s) "
        "OR l.ulice_id = ANY(%(district_codes_ulice)s))"
    )
    assert params["district_codes_obec"] == [586846]
    assert params["district_codes_okres"] == [3707]
    assert params["district_codes_kraj"] == [108]
    assert params["district_codes_cast_obce"] == [490067]
    assert params["district_codes_ulice"] == [666742]


def test_build_clauses_place_chips_read_no_text_column() -> None:
    spec = WatchdogFilterSpec(
        districts=[
            {"name": "Na Rybníčkách", "context": "Pardubice", "level": "ulice", "id": 666742},
            {"name": "Brno", "context": None, "level": "obec", "id": 582786},
        ],
    )
    where, _params = _build_match_clauses(spec)
    for clause in [w for w in where if "district_codes" in w]:
        assert "ILIKE" not in clause.upper()
        assert "place_search_text" not in clause
        assert "l.district" not in clause
        assert "'%'" not in clause


def test_build_clauses_a_street_chip_filters_at_its_street_never_its_town() -> None:
    spec = WatchdogFilterSpec(
        districts=[{"name": "Na Rybníčkách", "context": "Pardubice", "level": "ulice", "id": 666742}],
    )
    where, params = _build_match_clauses(spec)
    clause = next(w for w in where if "district_codes" in w)
    assert clause == "(l.ulice_id = ANY(%(district_codes_ulice)s))"
    assert params["district_codes_ulice"] == [666742]
    assert "district_codes_obec" not in params


def test_build_clauses_excluded_chip_is_negated() -> None:
    spec = WatchdogFilterSpec(
        districts=[{"name": "Praha", "level": "obec", "id": 554782, "excluded": True}],
    )
    where, params = _build_match_clauses(spec)
    clauses = [w for w in where if "district_codes" in w]
    # COALESCE: an exclude subtracts only what it MATCHES, so a listing with no
    # obec code survives it (the same answer the RPC and the board already give).
    assert clauses == [
        "NOT COALESCE(l.obec_id = ANY(%(district_codes_excl_obec)s), false)"
    ]
    assert params["district_codes_excl_obec"] == [554782]


def test_build_clauses_mixed_include_exclude() -> None:
    spec = WatchdogFilterSpec(
        districts=[
            {"name": "Praha", "level": "obec", "id": 554782},
            {"name": "Modřany", "level": "cast_obce", "id": 490017, "excluded": True},
        ],
    )
    where, params = _build_match_clauses(spec)
    inc = next(w for w in where if "district_codes_obec" in w)
    exc = next(w for w in where if "district_codes_excl_cast_obce" in w)
    assert not inc.startswith("NOT ")
    assert exc.startswith("NOT COALESCE(")
    assert params["district_codes_obec"] == [554782]
    assert params["district_codes_excl_cast_obce"] == [490017]


@pytest.mark.parametrize("chips", [
    [{"name": "Brno", "context": None}],
    ["Praha"],
    [{"name": "Lipová", "context": "Praha", "level": "locality", "id": 554782}],
    [{"name": "Praha", "level": "obec", "id": -1}],
    [{"name": "Praha", "level": "obec", "id": 554782}, {"name": "Jihlava"}],
], ids=["name only", "legacy string", "stale locality", "the old sentinel", "one bad of two"])
def test_a_spec_with_a_chip_without_a_code_is_refused(chips) -> None:
    """A chip is its (level, code) or nothing: a watchdog that silently matched
    nothing (the old sentinel) or the whole country (a widened guess) is no
    longer expressible, so it never reaches the matcher."""
    with pytest.raises(ValidationError):
        WatchdogFilterSpec(districts=chips)


@pytest.mark.parametrize("method,path", [
    ("post", "/notifications/subscriptions"),
    ("put", "/notifications/subscriptions/46a6005f-dc32-4d94-9ced-4d262b57ef6b"),
])
def test_the_routes_answer_a_chip_without_a_code_with_a_422(method, path, monkeypatch) -> None:
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from api import main as api_main
    from api import notifications as nf
    from api import tenant_pool

    def never(*_a: Any, **_kw: Any) -> None:
        raise AssertionError("a refused spec must not be written")

    monkeypatch.setattr(nf, "create_subscription", never)
    monkeypatch.setattr(nf, "update_subscription", never)
    api_main.app.dependency_overrides[tenant_pool.tenant_conn] = lambda: object()
    api_main.app.dependency_overrides[tenant_pool.require_account_id] = (
        lambda: UUID("11111111-1111-1111-1111-111111111111")
    )
    try:
        body = {"name": "x", "filter_spec": {"districts": [{"name": "Brno", "context": None}]}}
        res = getattr(TestClient(api_main.app), method)(path, json=body)
    finally:
        api_main.app.dependency_overrides.clear()
    assert res.status_code == 422, res.text
    assert "districts" in res.text


@pytest.mark.parametrize("method,path", [
    ("post", "/notifications/subscriptions"),
    ("put", "/notifications/subscriptions/46a6005f-dc32-4d94-9ced-4d262b57ef6b"),
])
def test_the_routes_answer_a_spec_the_compiler_refuses_with_a_422(method, path, monkeypatch) -> None:
    """A spec the model accepts but the matcher's compiler refuses (here a retired filter) is
    refused at write, one layer further in than the chip-without-code 422: stored, it would
    mute its watchdog every pass. A PUT that leaves the spec alone is not re-judged."""
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from api import main as api_main
    from api import notifications as nf
    from api import tenant_pool

    written: list[str] = []

    # The refusal lives in the module, so every caller gets it; this connection would fail any statement reaching it.
    api_main.app.dependency_overrides[tenant_pool.tenant_conn] = lambda: object()
    api_main.app.dependency_overrides[tenant_pool.require_account_id] = (
        lambda: UUID("11111111-1111-1111-1111-111111111111")
    )
    try:
        client = TestClient(api_main.app)
        body = {"name": "x", "filter_spec": {"near_city_proximity": {"radius_km": 10,
                                                                     "index_rules": []}}}
        res = getattr(client, method)(path, json=body)
        assert res.status_code == 422, res.text
        assert res.json()["detail"].startswith("filter_spec: near_city_proximity is retired")
        if method == "put":
            monkeypatch.setattr(nf, "update_subscription",
                                lambda *_a, **kw: written.append(kw["name"]) or {"id": "s"})
            assert client.put(path, json={"name": "renamed"}).status_code == 200
            assert written == ["renamed"]
    finally:
        api_main.app.dependency_overrides.clear()


def test_the_write_check_is_the_matchers_own_compile() -> None:
    """One definition of what a spec may hold: the route's check is the matcher's compile, and
    what it raises is what the producers count as invalid."""
    from api import notifications as nf

    spec = WatchdogFilterSpec(near_city_proximity={"radius_km": 10, "index_rules": []})
    with pytest.raises(nf.SPEC_REJECTIONS):
        nf.check_filter_spec(spec)
    with pytest.raises(nf.SPEC_REJECTIONS):
        _build_match_clauses(spec)
    nf.check_filter_spec(WatchdogFilterSpec())


def test_build_clauses_enumerated_columns() -> None:
    # A bare string still coerces to a single-element list (backward-compat).
    spec = WatchdogFilterSpec(furnished="ano", ownership=["osobni", "druzstevni"])
    assert spec.furnished == ["ano"]
    where, params = _build_match_clauses(spec)
    clauses = " ".join(where)
    assert "l.furnished = %(furnished)s" in clauses
    assert "l.ownership = ANY(%(ownership)s)" in clauses
    assert params["furnished"] == "ano"
    assert params["ownership"] == ["osobni", "druzstevni"]


def test_build_clauses_furnished_unknown_sentinel() -> None:
    spec = WatchdogFilterSpec(furnished=["__unknown__"])
    where, params = _build_match_clauses(spec)
    clauses = " ".join(where)
    assert "l.furnished IS NULL OR NOT (l.furnished = ANY(%(furnished_canon)s))" in clauses
    assert params["furnished_canon"] == ["ano", "ne", "castecne"]


def test_build_clauses_condition_match() -> None:
    """condition_match feeds ANY() against l.condition — the same
    pattern toolkit/comparables uses, so Browse / Watchdog stay
    aligned with the analytical surfaces."""
    spec = WatchdogFilterSpec(
        condition_match=["novostavba", "po_rekonstrukci"],
    )
    where, params = _build_match_clauses(spec)
    assert "(l.condition = ANY(%(condition_match)s))" in where
    assert params["condition_match"] == ["novostavba", "po_rekonstrukci"]
    assert "condition_match_canon" not in params


def test_build_clauses_condition_unknown_sentinel() -> None:
    """The "Neuvedeno" chip: NULL or any value outside the canon, as on furnished."""
    from toolkit.filter_registry import COLUMN_CANONICAL_VALUES

    spec = WatchdogFilterSpec(condition_match=["novostavba", "__unknown__"])
    where, params = _build_match_clauses(spec)
    clauses = " ".join(where)
    assert ("l.condition = %(condition_match)s OR "
            "(l.condition IS NULL OR NOT (l.condition = ANY(%(condition_match_canon)s)))") in clauses
    assert params["condition_match"] == "novostavba"
    assert params["condition_match_canon"] == list(COLUMN_CANONICAL_VALUES["condition"])


def test_build_clauses_condition_match_empty_list_drops_clause() -> None:
    """An empty list behaves like None — no WHERE clause emitted.
    Matches how `dispositions` already behaves."""
    spec = WatchdogFilterSpec(condition_match=[])
    where, params = _build_match_clauses(spec)
    assert not any("l.condition" in w for w in where)
    assert "condition_match" not in params


def test_build_clauses_categoryless_spec() -> None:
    """An operator who explicitly clears the category filters gets a
    spec with no category WHERE clauses — the watchdog matches every
    category. Defaults narrow; explicit None widens."""
    spec = WatchdogFilterSpec(category_main_in=None, category_type=None)
    where, params = _build_match_clauses(spec)
    assert not any("l.category_main" in w for w in where)
    assert not any("l.category_type" in w for w in where)
    assert "category_main_in" not in params
    assert "category_type" not in params


@pytest.mark.parametrize("fid", [
    "building_condition_level_min", "building_condition_level_max",
    "apartment_condition_level_min", "apartment_condition_level_max",
])
def test_build_clauses_parked_condition_levels_are_retired_loudly(fid: str) -> None:
    """Condition scoring is parked (rule 14, operator ruling 2026-10-20). A stored spec still
    deserialises, but a level bound raises (the matcher skips that subscription) rather than
    silently widening to every property; an unset one renders nothing."""
    with pytest.raises(ValueError, match=f"{fid} is retired"):
        _build_match_clauses(WatchdogFilterSpec(**{fid: 3}))
    where, params = _build_match_clauses(WatchdogFilterSpec())
    assert not any("condition_level" in w for w in where)
    assert not any("condition_level" in k for k in params)


# --- match_once with a fake psycopg connection ---------------------------


from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from api.notifications import (
    COLLECTION_KINDS,
    WATCHDOG_KINDS,
    get_unread_count,
    mark_all_seen,
    match_lifecycle_once,
    match_once,
)

# What a watchdog tells unless its owner changed it (migration 735's column default).
_DEFAULT_KINDS = ["new", "price_drop"]


def _slicing(started_at: datetime, sliced_at: datetime | None = None,
             told: int = 0) -> list[tuple[Any, list[tuple[Any, ...]], int]]:
    """A fake's lines for a watchdog's slice pass, ahead of any broader INSERT line: its row held, taken for
    `started_at` and last passed at `sliced_at` (None: a first pass), and the New matches its statement tells."""
    return [
        (lambda s: s.startswith("SELECT started_at, sliced_at FROM watchdog_slices"), [(started_at, sliced_at)], 1),
        (lambda s: s.startswith("WITH slice AS"), [(told,)], 1),
    ]


def _slice_statements(conn: "_FakeConn") -> list[tuple[str, Any]]:
    return [(s, p) for s, p in conn.executed if s.startswith("WITH slice AS")]


class _FakeCursor:
    """Minimal cursor that returns canned rows in sequence.

    `_FakeConn.results` is a list of (predicate, rows) pairs; the cursor
    finds the first predicate that matches the executed SQL and returns
    its `rows`. Insert/UPDATE SQL is captured in `_FakeConn.executed`
    so the test can assert on what the matcher emitted.
    """

    def __init__(self, conn: "_FakeConn"):
        self._conn = conn
        self._last_rows: list[tuple[Any, ...]] = []
        self.rowcount = 0
        self.description: list[tuple[str]] | None = None

    def execute(self, sql: str, params: Any = None) -> None:
        sql_norm = " ".join(sql.split())
        self._conn.executed.append((sql_norm, params))
        for predicate, rows, rowcount in self._conn.script:
            if predicate(sql_norm):
                self._last_rows = rows
                self.rowcount = rowcount
                return
        self._last_rows = []
        self.rowcount = 0

    def fetchone(self) -> tuple[Any, ...] | None:
        return self._last_rows[0] if self._last_rows else None

    def fetchall(self) -> list[tuple[Any, ...]]:
        return list(self._last_rows)

    def __enter__(self) -> "_FakeCursor":
        return self

    def __exit__(self, *_: Any) -> None:
        return None


class _FakeConn:
    def __init__(
        self,
        script: list[tuple[Any, list[tuple[Any, ...]], int]],
    ):
        self.script = script
        self.executed: list[tuple[str, Any]] = []
        self.transactions = 0

    def cursor(self) -> _FakeCursor:
        return _FakeCursor(self)

    def transaction(self) -> Any:
        # Each watchdog's INSERT is its own transaction (a savepoint inside the lane's step).
        import contextlib

        self.transactions += 1
        return contextlib.nullcontext()


def test_create_subscription_stamps_account_id() -> None:
    """create_subscription must write account_id into the INSERT — it is NOT NULL
    since migration 364. The route resolves it from the caller's JWT via
    tenant_pool.resolve_account_id and passes it here; None would be a
    NotNullViolation by design (the route 400s first)."""
    from api.notifications import create_subscription

    acct = UUID("11111111-1111-1111-1111-111111111111")
    script: list[tuple[Any, list[tuple[Any, ...]], int]] = [
        (
            lambda s: "INSERT INTO notification_subscriptions" in s,
            [(UUID("22222222-2222-2222-2222-222222222222"),)],
            1,
        ),
    ]
    conn = _FakeConn(script)
    create_subscription(
        conn, name="Test", filter_spec=WatchdogFilterSpec(), account_id=acct
    )
    insert_sql, insert_params = conn.executed[0]
    assert "INSERT INTO notification_subscriptions" in insert_sql
    assert "account_id" in insert_sql
    assert acct in insert_params


def test_deleting_a_watchdog_retires_it_and_keeps_its_alerts() -> None:
    """F21: a delete is an UPDATE that stamps retired_at and switches it off — no DELETE, so the
    FK's cascade never takes the watchdog's alerts with it; a missing or already retired one
    changes nothing (the route's 404). List, get and update see live watchdogs only, and the
    producers read none that is retired."""
    from api import notifications as nf

    conn = _FakeConn([(lambda s: s.startswith("UPDATE notification_subscriptions"), [], 1)])
    assert nf.delete_subscription(conn, "s-1") is True  # type: ignore[arg-type]
    [(sql, params)] = conn.executed
    assert sql == ("UPDATE notification_subscriptions SET retired_at = now(), is_active = false "
                   "WHERE id = %s AND retired_at IS NULL") and params == ("s-1",)
    assert nf.delete_subscription(_FakeConn([]), "gone") is False  # type: ignore[arg-type]

    conn = _FakeConn([])
    nf.list_subscriptions(conn)  # type: ignore[arg-type]
    nf.list_subscriptions(conn, include_inactive=False)  # type: ignore[arg-type]
    nf.get_subscription(conn, "s-1")  # type: ignore[arg-type]
    nf.update_subscription(conn, "s-1", name="x")  # type: ignore[arg-type]
    listed, active, got, updated = (s for s, _ in conn.executed)
    assert listed.endswith("FROM notification_subscriptions WHERE retired_at IS NULL ORDER BY created_at DESC")
    assert "WHERE retired_at IS NULL AND is_active = true ORDER BY" in active
    assert got.endswith("FROM notification_subscriptions WHERE id = %s AND retired_at IS NULL")
    assert updated.endswith("WHERE id = %s AND retired_at IS NULL")
    assert nf._LIVE_WATCHDOGS_SQL.endswith(
        "WHERE is_active = true AND retired_at IS NULL AND notify_kinds && %s::text[]")


def test_the_delete_route_keeps_its_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi.testclient import TestClient

    from api import notifications as nf
    from api import tenant_pool
    from api.main import app

    retired: list[str] = []
    monkeypatch.setattr(nf, "delete_subscription", lambda _c, sid: retired.append(sid) or sid == "s-1")
    app.dependency_overrides[tenant_pool.tenant_conn] = lambda: object()
    try:
        client = TestClient(app)
        assert client.delete("/notifications/subscriptions/s-1").json() == {"deleted": True}
        assert client.delete("/notifications/subscriptions/s-2").status_code == 404
    finally:
        app.dependency_overrides.pop(tenant_pool.tenant_conn, None)
    assert retired == ["s-1", "s-2"]


def test_a_stored_spec_with_a_chip_without_a_code_mutes_only_its_watchdog() -> None:
    """Fail CLOSED: a stored row the model now refuses is counted invalid and
    dispatches nothing — never a cohort widened to the whole country — while the
    healthy watchdog in the same pass still matches. Migration 606 deleted the
    last such rows; this is the rail if one ever reappears."""
    dead = UUID("46a6005f-dc32-4d94-9ced-4d262b57ef6b")
    healthy = UUID("5b1e4c8e-0d1f-4a77-9c3e-2a6f1d0b9e10")
    since = datetime(2026, 10, 16, 12, 0, 0, tzinfo=timezone.utc)
    conn = _FakeConn([
        (
            lambda s: "FROM notification_subscriptions WHERE is_active" in s,
            [
                (dead, {"districts": [{"name": "Jihlava"}]}, since, _DEFAULT_KINDS),
                (healthy, {"districts": [{"name": "Na Rybníčkách", "context": "Pardubice",
                                          "level": "ulice", "id": 666742}]},
                 since, _DEFAULT_KINDS),
            ],
            2,
        ),
        *_slicing(since, told=2),
    ])
    stats = match_once(conn)  # type: ignore[arg-type]
    assert stats == {"subscriptions_evaluated": 2, "subscriptions_invalid": 1,
                     "subscriptions_failed": 0, "matches_inserted": 2}
    inserts = [p for _, p in _slice_statements(conn)]
    assert len(inserts) == 1 and inserts[0]["subscription_id"] == str(healthy)
    assert inserts[0]["district_codes_ulice"] == [666742]


def test_a_start_takes_the_slice_and_tells_only_what_was_published_after_it() -> None:
    """A watchdog's first pass, or its first after a restart (`started_at` is not its active_since), takes
    its slice afresh (ruling Q14): the old members go, the slice (the spec over properties_public, on the
    market) becomes the members, and only a property first seen inside NEW_WINDOW that became visible
    after the start is told (R6 (a), F1: one first seen before the start and published after it still is),
    as the matcher before the slice told, minus a 'new' it already told since the start. One transaction
    per watchdog, under the bound, the events lock SHARED first; the watchdog row is never written."""
    from api import notifications as nf
    from toolkit.property_carriers import NOTIFICATION_EVENTS_SHARED_LOCK_SQL
    from toolkit.publication import visible_since_sql

    sub_id = UUID("46a6005f-dc32-4d94-9ced-4d262b57ef6b")
    since = datetime(2026, 10, 21, 9, 0, 0, tzinfo=timezone.utc)
    conn = _FakeConn([
        (
            lambda s: "FROM notification_subscriptions WHERE is_active" in s,
            [(sub_id, {"category_main": "byt", "category_type": "pronajem",
                       "districts": [{"name": "Praha", "level": "obec", "id": 554782}]},
              since, _DEFAULT_KINDS)],
            1,
        ),
        *_slicing(datetime(2026, 9, 1, tzinfo=timezone.utc), datetime(2026, 10, 1, tzinfo=timezone.utc), told=3),
    ])

    stats = match_once(conn)  # type: ignore[arg-type]

    assert stats == {"subscriptions_evaluated": 1, "subscriptions_invalid": 0,
                     "subscriptions_failed": 0, "matches_inserted": 3}
    subs_sql, subs_params = next((s, p) for s, p in conn.executed if "FROM notification_subscriptions" in s
                                 and s.startswith("SELECT id"))
    statements = [s for s, _ in conn.executed]
    assert statements.index(" ".join(nf._SLICE_FORGET_SQL.split())) < statements.index(subs_sql), \
        "the slices no live watchdog keeps go first"
    assert subs_sql == ("SELECT id, filter_spec, active_since, notify_kinds FROM notification_subscriptions "
                        "WHERE is_active = true AND retired_at IS NULL AND notify_kinds && %s::text[]")
    assert subs_params == (["new"],), "only the watchdogs that tell 'new'"
    at = statements.index(nf._SLICE_ENSURE_SQL)
    assert statements[at - 1] == "SET LOCAL statement_timeout = '120s'"
    # The events lock, SHARED, just before the one statement that writes alerts: a merge waits one statement.
    assert statements[at + 1:at + 4] == [nf._SLICE_LOCK_SQL, nf._SLICE_RESTART_SQL, NOTIFICATION_EVENTS_SHARED_LOCK_SQL]
    [(sql, params)] = _slice_statements(conn)
    assert statements[at + 4] == sql and sql.startswith(" ".join(nf._SLICE_SEED_SQL.split())[:40])
    assert "FROM properties_public l WHERE l.is_active AND " in sql
    assert "WHERE p.first_seen_at > now() - %(new_window)s::interval " in sql
    assert f"AND {visible_since_sql('p')} > %(active_since)s::timestamptz " in sql
    assert ("AND d.change_kind = 'new' AND d.duplicate_of IS NULL "
            "AND d.dispatched_at >= %(active_since)s::timestamptz)") in sql
    assert "UPDATE watchdog_slices SET started_at = %(active_since)s, sliced_at = now()" in sql
    assert params["new_window"] == "24 hours" and params["active_since"] == since
    assert params["subscription_id"] == str(sub_id)
    # The spec made it through the one compiler (rule 16).
    assert params["category_main_in"] == "byt"
    assert params["category_type"] == "pronajem"
    assert params["district_codes_obec"] == [554782]
    assert "l.obec_id = ANY(%(district_codes_obec)s)" in sql
    assert "'watchdog'" in sql and f"{ON_EVENT_CONFLICT} RETURNING 1" in sql
    assert conn.transactions == 2, "the forget, then one transaction per watchdog"
    executed = " ".join(statements)
    for gone in ("last_matched_first_seen_at", "max(first_seen_at)", "UPDATE notification_subscriptions",
                 "image_lookback_minutes", "window_size", "app_settings", "storage_path"):
        assert gone not in executed, gone


def test_a_later_pass_diffs_the_slice_and_names_why_each_entry_entered() -> None:
    """A pass after the start keeps the members and diffs them in ONE statement: a leaver is stamped, one
    gone past SLICE_BLINK forgotten, an entrant upserted as entering now (so nothing that changed while a
    blinking property was out is told later), and an entry is told unless it blinked, is a member's merge
    survivor or split off a member, or nothing about it changed since the last pass (less SLICE_SETTLE)
    while its spec reads our own data (`_always_told`)."""
    from api import notifications as nf

    sub_id = UUID("46a6005f-dc32-4d94-9ced-4d262b57ef6b")
    since = datetime(2026, 10, 21, 9, 0, 0, tzinfo=timezone.utc)
    last = datetime(2026, 11, 7, 8, 0, 0, tzinfo=timezone.utc)
    conn = _FakeConn([
        (lambda s: "FROM notification_subscriptions WHERE is_active" in s,
         [(sub_id, {"category_type": "prodej", "min_city_population": 50_000}, since, _DEFAULT_KINDS)], 1),
        *_slicing(since, last, told=1),
    ])
    stats = match_once(conn)  # type: ignore[arg-type]
    assert stats["matches_inserted"] == 1
    assert nf._SLICE_RESTART_SQL not in [s for s, _ in conn.executed], "the members are kept"
    [(sql, params)] = _slice_statements(conn)
    assert ("members AS ( SELECT w.property_id, w.entered_at, w.left_at FROM watchdog_slice_members w "
            "WHERE w.subscription_id = %(subscription_id)s::uuid), ") in sql
    assert params["since"] == last - nf.SLICE_SETTLE and params["blink"] == "24 hours"
    assert params["always_told"] is False, "a city-population bound reads our own data"
    for part in (
        "UPDATE watchdog_slice_members w SET left_at = now() WHERE w.subscription_id = %(subscription_id)s::uuid "
        "AND w.left_at IS NULL AND NOT EXISTS (SELECT 1 FROM slice s WHERE s.property_id = w.property_id)",
        "DELETE FROM watchdog_slice_members w WHERE w.subscription_id = %(subscription_id)s::uuid "
        "AND w.left_at <= now() - %(blink)s::interval",
        "coalesce(w.left_at > now() - %(blink)s::interval, false) AS blinked",
        "SELECT %(subscription_id)s, e.property_id, now() FROM entering e",
        "JOIN property_merge_events pe ON pe.listing_ref_id = c.id AND pe.undone_at > now() - %(blink)s::interval "
        "JOIN members w ON w.property_id = pe.survivor_property_id",
        "DO UPDATE SET entered_at = excluded.entered_at, left_at = NULL",
        "WHERE NOT e.blinked AND NOT EXISTS (SELECT 1 FROM members w JOIN properties x ON x.id = w.property_id "
        "WHERE x.merged_into = e.property_id",
        "AND (sig.reason IS NOT NULL OR %(always_told)s)",
        f"{nf._ENTRY_REF}, coalesce(sig.reason, 'other')",
        "UPDATE watchdog_slices SET sliced_at = now()",
    ):
        assert part in sql, part
    # The reasons, in MATCH_REASONS order, each of a change after `since`.
    signal = " ".join(nf._ENTRY_SIGNAL.split())
    order = [signal.index(f"THEN '{reason}'") for reason in nf.MATCH_REASONS[:-1]]
    assert order == sorted(order)
    assert "p.back_on_market_at > %(since)s::timestamptz THEN 'back'" in signal
    assert "WHEN p.first_seen_at > now() - %(new_window)s::interval OR " in signal, "new, whatever let it in late"
    assert "x.merged_at > %(since)s::timestamptz" in signal and "ll.resolved_at > %(since)s::timestamptz" in signal


def test_every_entry_is_told_unless_the_spec_reads_our_own_data_alone() -> None:
    """Ruling Q11: an entry nothing about the property explains is still a New match unless the spec uses a
    filter on our own data (a city index or population, proximity, the rent map, the parked condition
    levels); a Velocity filter makes every entry told again, since time passing brings a property in."""
    from api import notifications as nf
    from toolkit.filter_registry import CATEGORY_CITY_QUALITY, CATEGORY_VELOCITY, REGISTRY

    assert nf._always_told(WatchdogFilterSpec(category_type="prodej", max_price_czk=5_000_000))
    assert not nf._always_told(WatchdogFilterSpec(min_mf_gross_yield_pct=4.0))
    assert not nf._always_told(WatchdogFilterSpec(near_pop_5km_min=10_000))
    assert nf._always_told(WatchdogFilterSpec(min_mf_gross_yield_pct=4.0, price_change_count_min=2))
    spec_fields = set(WatchdogFilterSpec.model_fields)
    assert {k for k, f in REGISTRY.items() if f.category == CATEGORY_CITY_QUALITY} & spec_fields \
        <= nf._OUTSIDE_DATA_FIELDS
    assert nf._VELOCITY_FIELDS == {k for k, f in REGISTRY.items() if f.category == CATEGORY_VELOCITY} & spec_fields
    assert nf._OUTSIDE_DATA_FIELDS <= spec_fields, "every name is a spec field"


def test_a_pass_another_worker_holds_is_skipped() -> None:
    """A deploy overlap's two workers: the second finds the watchdog's row locked (SKIP LOCKED) and tells
    nothing, so one entry is never told twice."""
    sub_id = UUID("46a6005f-dc32-4d94-9ced-4d262b57ef6b")
    conn = _FakeConn([
        (lambda s: "FROM notification_subscriptions WHERE is_active" in s,
         [(sub_id, {}, datetime(2026, 10, 21, tzinfo=timezone.utc), _DEFAULT_KINDS)], 1),
    ])
    stats = match_once(conn)  # type: ignore[arg-type]
    assert stats == {"subscriptions_evaluated": 1, "subscriptions_invalid": 0,
                     "subscriptions_failed": 0, "matches_inserted": 0}
    assert _slice_statements(conn) == []


def test_a_quiet_pass_writes_nothing_and_never_touches_the_watchdog() -> None:
    """No matching property is the steady state: one INSERT that writes 0 rows, and the
    subscription row is never written (there is no cursor to move)."""
    conn = _FakeConn([
        (lambda s: "FROM notification_subscriptions WHERE is_active" in s,
         [(UUID("00000000-0000-0000-0000-000000000001"), {},
           datetime(2030, 1, 1, tzinfo=timezone.utc), _DEFAULT_KINDS)], 1),
        (lambda s: "INSERT INTO notification_dispatches" in s, [], 0),
    ])
    stats = match_once(conn)  # type: ignore[arg-type]
    assert stats["matches_inserted"] == 0
    assert not any("UPDATE notification_subscriptions" in s for s, _ in conn.executed)


def test_lifecycle_noop_when_nothing_monitored_and_no_watchdog_tells_it() -> None:
    """No monitored collection → no collection INSERT; no watchdog telling a lifecycle kind → none."""
    script: list[tuple[Any, list[tuple[Any, ...]], int]] = [
        (lambda s: "FROM collections WHERE monitoring_enabled = true" in s, [(0,)], 0),
    ]
    conn = _FakeConn(script)
    stats = match_lifecycle_once(conn)  # type: ignore[arg-type]
    assert stats == {"subscriptions_evaluated": 0, "subscriptions_invalid": 0,
                     "subscriptions_failed": 0, "events_inserted": 0,
                     "monitored_collections": 0, "collection_events_inserted": 0}
    assert not any(
        "INSERT INTO notification_dispatches" in s for s, _ in conn.executed
    )
    subs_params = next(p for s, p in conn.executed if "FROM notification_subscriptions" in s)
    assert subs_params == (["inactive", "new_source"],), "a watchdog tells a return as a New match"


def test_collection_monitor_emits_the_five_clean_kinds() -> None:
    """One set-based INSERT per detector across all monitored collections — inactive,
    reactivated and new_source in step lifecycle, price_drop / price_rise (one CASE) in
    step price; every dispatch is source_kind='collection_monitor', idempotent on the one
    identity. broker_change is reserved, not emitted."""
    script: list[tuple[Any, list[tuple[Any, ...]], int]] = [
        (lambda s: "FROM collections WHERE monitoring_enabled = true" in s, [(2,)], 0),
        (lambda s: "INSERT INTO notification_dispatches" in s, [], 2),
    ]
    conn = _FakeConn(script)
    stats = match_lifecycle_once(conn)  # type: ignore[arg-type]
    assert stats["monitored_collections"] == 2 and stats["collection_events_inserted"] == 6
    assert conn.transactions == 3, "each detector commits before the next reads it"
    match_changes_once(conn)  # type: ignore[arg-type]

    inserts = [
        (sql, params) for sql, params in conn.executed
        if "INSERT INTO notification_dispatches" in sql
    ]
    assert len(inserts) == 4
    joined = " ".join(sql for sql, _ in inserts)
    assert "'collection_monitor'" in joined and "'watchdog'" not in joined
    for kind in ("'price_drop'", "'price_rise'", "'inactive'", "'reactivated'", "'new_source'"):
        assert kind in joined
    assert "'cm:'" not in joined                   # no key grammar since W3
    assert joined.count(ON_EVENT_CONFLICT) == 4
    assert "'broker_change'" not in joined          # reserved, no clean signal yet
    # Each collection tells the kinds it is set to (migration 735), read off its own row: no
    # statement passes the monitor a kind list.
    assert all("kinds" not in params for _, params in inserts)
    assert all("c.notify_kinds AS kinds" in sql for sql, _ in inserts)


def test_new_source_names_one_introducing_ad_on_every_pass() -> None:
    """`new_source`'s event_ref is the introducing ad's id, so the ad the window picks must not
    change between passes or the identity would, and the same portal arrival would be told twice.
    The legacy handle breaks a first-seen tie as the pre-W3 key did; the surrogate breaks the
    rest (a non-sreality ad has no handle). Its portal's first ad that is not the property's first
    ad: another portal was there before it."""
    from api import notifications as nf

    for sql in (nf._MONITOR_NEW_SOURCE_SQL, nf._WATCHDOG_NEW_SOURCE_SQL):
        new_source = " ".join(sql.split())
        assert ("row_number() OVER (PARTITION BY l.property_id, l.source "
                "ORDER BY l.first_seen_at, l.sreality_id, l.id) AS rn") in new_source
        assert "WHERE x.rn = 1 AND x.first_seen_at > x.prop_first" in new_source
        assert "e.listing_id::text" in new_source
        # count(DISTINCT ...) OVER (...) is 0A000 in Postgres; it threw on every run once.
        assert "count(DISTINCT" not in new_source


# Each source, and the detectors rendered for it: a watchdog tells a return as a New match, so it has no
# Back on the market; one telling 'new' reads its slice for the price and new-portal kinds, and its Left the
# market stays on the spec (a property leaving the market leaves the slice with it).
_SOURCE_DETECTORS = {
    "MONITOR": ("PRICE", "INACTIVE", "REACTIVATED", "NEW_SOURCE"),
    "WATCHDOG": ("PRICE", "INACTIVE", "NEW_SOURCE"),
    "WATCHDOG_SLICE": ("PRICE", "NEW_SOURCE"),
}


@pytest.mark.parametrize("source", list(_SOURCE_DETECTORS))
def test_every_detector_is_one_definition_gated_on_its_sources_since(source: str) -> None:
    """Each kind is ONE detector rendered for every source: the statements differ only in the
    source's own text (its CTE, its join, its column and kind). Each fires only for changes
    AFTER the source began watching (`m.since`: a collection's greatest(added_at,
    monitoring_enabled_at), a watchdog's active_since, a slice member's entry), reactivated
    through the inactive dispatch it answers, which must itself postdate it. 'inactive' also
    waits out the delist dwell (F14): a blink sends neither Left the market nor Back on the market."""
    from api import notifications as nf

    renders = {"PRICE": nf._price_sql, "INACTIVE": nf._inactive_sql, "REACTIVATED": nf._reactivated_sql,
               "NEW_SOURCE": nf._new_source_sql}
    sql = {k: " ".join(getattr(nf, f"_{source}_{k}_SQL").split()) for k in _SOURCE_DETECTORS[source]}
    assert "WHERE e.scraped_at > m.since AND " in sql["PRICE"]
    assert "WHERE e.first_seen_at > m.since" in sql["NEW_SOURCE"]
    if "INACTIVE" in sql:
        assert "WHERE e.inactive_at > m.since" in sql["INACTIVE"]
        assert "max(g.inactive_at) < now() - %(inactive_dwell)s::interval" in sql["INACTIVE"]
        assert "g.inactive_at > now() - %(monitor_window)s::interval" in sql["INACTIVE"]
    if "REACTIVATED" in sql:
        assert "WHERE m.source_id = e.told_source AND e.told_at > m.since" in sql["REACTIVATED"]
    # Each fires only for a kind its source tells (migration 735), from the one `m.kinds`.
    assert "CASE WHEN e.price_czk < e.prev_price_czk THEN 'price_drop' ELSE 'price_rise' END = ANY(m.kinds)" \
        in sql["PRICE"]
    for name, kind in (("INACTIVE", "inactive"), ("REACTIVATED", "reactivated"), ("NEW_SOURCE", "new_source")):
        if name in sql:
            assert f"AND '{kind}' = ANY(m.kinds)" in sql[name], name
    src = getattr(nf, f"_{source}")
    for name in sql:
        assert getattr(nf, f"_{source}_{name}_SQL") == renders[name](src), name
    if source == "MONITOR":
        anchor = "greatest(cp.added_at, coalesce(c.monitoring_enabled_at, cp.added_at)) AS since, " \
                 "c.notify_kinds AS kinds"
        assert all(anchor in s for s in sql.values())
    elif source == "WATCHDOG":
        # The spec joins each event directly (`{where}` on properties_public), never behind a CTE of
        # matching properties: that plan scanned a broad watchdog's cohort once per price step.
        for s in sql.values():
            assert "JOIN properties_public l ON l.property_id = e.property_id AND {where} " in s
            assert "%(active_since)s::timestamptz AS since, %(kinds)s::text[] AS kinds" in s and "members" not in s
    else:
        # A member of the slice taken for this start, still in it, of a change after it entered: the change
        # that brought it in is its New match (ruling Q13). No spec: the pass already applied it.
        for s in sql.values():
            assert ("FROM watchdog_slice_members w JOIN watchdog_slices ws ON ws.subscription_id = w.subscription_id "
                    "AND ws.started_at = %(active_since)s::timestamptz") in s
            assert "w.entered_at AS since, %(kinds)s::text[] AS kinds" in s
            assert "AND w.left_at IS NULL" in s and "{where}" not in s


def test_a_watchdog_tells_only_the_kinds_it_is_set_to() -> None:
    """notify_kinds (migration 735) picks each watchdog's statements: step price passes its kinds
    to the one CASE, step lifecycle runs only its detectors, inactive first, and a watchdog that
    tells none of a step's kinds is not read by it at all. One telling 'new' reads its slice for
    the price and new-portal kinds; its Left the market stays on the spec."""
    from api import notifications as nf

    lister, slicer = UUID("13131313-1313-1313-1313-131313131313"), UUID("14141414-1414-1414-1414-141414141414")
    since = datetime(2026, 11, 3, tzinfo=timezone.utc)
    rows = [(lister, {}, since, ["price_rise", "inactive"]),
            (slicer, {}, since, ["new", "price_drop", "inactive", "new_source"])]
    conn = _FakeConn([
        (lambda s: "FROM notification_subscriptions WHERE is_active" in s, rows, 2),
        (lambda s: "FROM collections WHERE monitoring_enabled = true" in s, [(0,)], 0),
        (lambda s: "INSERT INTO notification_dispatches" in s and "'watchdog'" in s, [], 1),
    ])
    nf.match_changes_once(conn)  # type: ignore[arg-type]
    stats = nf.match_lifecycle_once(conn)  # type: ignore[arg-type]
    assert stats["events_inserted"] == 3 and stats["subscriptions_evaluated"] == 2
    watchdog = [(s, p) for s, p in conn.executed
                if "INSERT INTO notification_dispatches" in s and "'watchdog'" in s]
    mine = {sub: [(s, p) for s, p in watchdog if p["subscription_id"] == str(sub)] for sub in (lister, slicer)}
    assert [p["kinds"] for s, p in mine[lister] if "canonical_steps" in s] == [["price_rise"]]
    lifecycle = [s for s, _ in mine[lister] if "canonical_steps" not in s]
    assert len(lifecycle) == 1 and "'inactive'" in lifecycle[0] and "'new_source'" not in lifecycle[0]
    assert all(p["active_since"] == since for _, p in watchdog)
    assert not any("watchdog_slice_members" in s for s, _ in mine[lister]), "no 'new', no slice"
    price, gone, portal = (s for s, _ in mine[slicer])
    assert "canonical_steps" in price and "watchdog_slice_members" in price
    assert "'inactive'" in gone and "watchdog_slice_members" not in gone
    assert "'new_source'" in portal and "watchdog_slice_members" in portal
    assert not any("'reactivated'" in s for s, _ in watchdog)


def test_the_kinds_a_watchdog_stores_are_one_ordered_set() -> None:
    """Once each in KIND_ORDER, so re-saving the same set is no change (no restamp of active_since);
    never empty; a watchdog never chooses Back on the market (it re-enters as a New match), and a
    collection's Back on the market only beside its Left the market, whose dispatch it answers."""
    from api.notifications import KIND_ORDER, normalize_notify_kinds

    assert WATCHDOG_KINDS == ("new", "price_drop", "price_rise", "inactive", "new_source")
    assert COLLECTION_KINDS == KIND_ORDER[1:]
    assert normalize_notify_kinds(["new_source", "new", "new"]) == ["new", "new_source"]
    with pytest.raises(ValueError, match="at least one"):
        normalize_notify_kinds([])
    with pytest.raises(ValueError, match="needs 'inactive'"):
        normalize_notify_kinds(["reactivated"])
    assert normalize_notify_kinds(["reactivated", "inactive"]) == ["inactive", "reactivated"]


def test_migration_735_checks_what_the_api_refuses() -> None:
    """The column's CHECK holds the rules normalize_notify_kinds and the kinds' Literal hold, its default is
    what every watchdog told before it, and the active_since stamp restarts a watchdog on a kinds change."""
    from api.notifications import MATCH_REASONS
    from tests.migration_defs import MIGRATIONS

    sql = " ".join((MIGRATIONS / "735_watchdog_notify_kinds.sql").read_text().split())
    watchdog, collection = sql.split("alter table public.collections", 1)
    assert "add column if not exists notify_kinds text[] not null default array['new', 'price_drop']" in watchdog
    allowed = watchdog.split("notify_kinds <@ array[", 1)[1].split("]", 1)[0]
    assert tuple(re.findall(r"'(\w+)'", allowed)) == WATCHDOG_KINDS
    assert "cardinality(notify_kinds) > 0" in watchdog
    assert "or new.notify_kinds is distinct from old.notify_kinds then new.active_since := now();" in watchdog
    assert "before insert or update of is_active, filter_spec, notify_kinds" in watchdog
    # A collection: every kind but 'new', all five by default (what each told before), Back on the market
    # only beside Left the market, and a kinds change while watching restarts it as switching it on does.
    assert ("add column if not exists notify_kinds text[] not null default array['price_drop', 'price_rise', "
            "'inactive', 'reactivated', 'new_source']") in collection
    allowed = collection.split("notify_kinds <@ array[", 1)[1].split("]", 1)[0]
    assert tuple(re.findall(r"'(\w+)'", allowed)) == COLLECTION_KINDS
    assert "cardinality(notify_kinds) > 0" in collection
    assert "('inactive' = any(notify_kinds) or not 'reactivated' = any(notify_kinds))" in collection
    assert ("if NEW.monitoring_enabled and (TG_OP = 'INSERT' or OLD.monitoring_enabled is distinct from true "
            "or NEW.notify_kinds is distinct from OLD.notify_kinds) then NEW.monitoring_enabled_at := now();") \
        in collection
    # The slice: producer state, service role only; a return stamped by every writer; a reason only on 'new'.
    for table in ("watchdog_slices", "watchdog_slice_members"):
        assert f"alter table public.{table} enable row level security;" in sql
        assert f"revoke all on public.{table} from public, anon, authenticated;" in sql
    assert ("when (old.is_active = false and new.is_active and old.status = 'active' and new.status = 'active') "
            "execute function public.properties_stamp_back_on_market_at();") in sql
    reasons = sql.split("match_reason in (", 1)[1].split(")", 1)[0]
    assert set(re.findall(r"'(\w+)'", reasons)) == set(MATCH_REASONS)
    assert "match_reason is null or (change_kind = 'new' and match_reason in" in sql


@pytest.mark.parametrize(("body", "detail"), [
    ({"notify_kinds": []}, "at least one"),
    ({"notify_kinds": ["reactivated"]}, "literal_error"),
    ({"notify_kinds": ["sold"]}, "literal_error"),
])
def test_the_routes_refuse_a_kind_set_the_check_would(
    body: dict[str, Any], detail: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastapi.testclient import TestClient

    from api import notifications as nf
    from api import tenant_pool
    from api.main import app

    written: list[Any] = []
    monkeypatch.setattr(nf, "update_subscription", lambda _c, sid, **kw: written.append(kw) or {"id": sid})
    app.dependency_overrides[tenant_pool.tenant_conn] = lambda: object()
    try:
        client = TestClient(app)
        bad = client.put("/notifications/subscriptions/s-1", json=body)
        good = client.put("/notifications/subscriptions/s-1",
                          json={"notify_kinds": ["new_source", "price_drop", "price_drop"]})
    finally:
        app.dependency_overrides.pop(tenant_pool.tenant_conn, None)
    assert bad.status_code == 422 and detail in bad.text
    assert good.status_code == 200
    assert [kw["notify_kinds"] for kw in written] == [["price_drop", "new_source"]]


def test_create_and_update_write_the_kinds_or_leave_the_default() -> None:
    from api.notifications import create_subscription, update_subscription

    acct = UUID("11111111-1111-1111-1111-111111111111")
    script: list[tuple[Any, list[tuple[Any, ...]], int]] = [
        (lambda s: "INSERT INTO notification_subscriptions" in s,
         [(UUID("22222222-2222-2222-2222-222222222222"),)], 1),
        (lambda s: s.startswith("UPDATE notification_subscriptions"), [], 1),
    ]
    conn = _FakeConn(script)
    create_subscription(conn, name="a", filter_spec=WatchdogFilterSpec(), account_id=acct)
    create_subscription(conn, name="b", filter_spec=WatchdogFilterSpec(), account_id=acct,
                        notify_kinds=["inactive"])
    update_subscription(conn, "s-1", notify_kinds=["new_source"])  # type: ignore[arg-type]
    default, chosen, updated = [(s, p) for s, p in conn.executed
                                if s.startswith(("INSERT INTO notification_subscriptions",
                                                 "UPDATE notification_subscriptions"))]
    assert "notify_kinds) VALUES (%s, %s::jsonb, %s, %s, DEFAULT)" in default[0]
    assert "notify_kinds) VALUES (%s, %s::jsonb, %s, %s, %s)" in chosen[0]
    assert chosen[1][-1] == ["inactive"] and len(default[1]) == 4
    assert updated[0].startswith("UPDATE notification_subscriptions SET notify_kinds = %s ")
    assert updated[1] == [["new_source"], "s-1"]


# notification_dispatches' columns: the 15 migration 656 kept (its $post$ pins them) and 735's match_reason.
_DISPATCH_COLUMNS = frozenset({
    "id", "subscription_id", "collection_id", "source_kind", "property_id", "listing_id", "change_kind",
    "trigger_price_czk", "prev_price_czk", "dispatched_at", "seen_at", "account_id", "event_ref",
    "actor_account_id", "duplicate_of", "match_reason"})


def test_every_producer_writes_only_the_columns_the_migrations_keep() -> None:
    """Producers write facts (notifications program, north star 1), never routing, a key, text or
    the legacy id: every INSERT, the share grant's included, names only the 15 columns migration 656
    keeps (it dropped channel, status, error_message, target_channels, dedupe_key,
    trigger_snapshot_id, sreality_id, estimation_run_id and message) and why a New match entered (735)."""
    from api import shares
    from api.notifications import match_changes_once

    since = datetime(2026, 5, 14, tzinfo=timezone.utc)
    sub_row = (UUID("46a6005f-dc32-4d94-9ced-4d262b57ef6b"), {}, since, list(WATCHDOG_KINDS))
    conn = _FakeConn([
        (lambda s: "FROM notification_subscriptions WHERE is_active" in s, [sub_row], 1),
        (lambda s: "FROM collections WHERE monitoring_enabled = true" in s, [(1,)], 0),
        *_slicing(since),
    ])
    match_once(conn)  # type: ignore[arg-type]
    match_changes_once(conn)  # type: ignore[arg-type]
    match_lifecycle_once(conn)  # type: ignore[arg-type]
    statements = [s for s, _ in conn.executed if "INSERT INTO notification_dispatches" in s]
    statements.append(" ".join(shares._INSERT_SHARE_DISPATCH_SQL.split()))
    columns = [re.search(r"INSERT INTO notification_dispatches \(([^)]*)\)", s) for s in statements]
    # The slice's 1, price's 2 (watchdog, collections), lifecycle's 2 + 3, the share's 1.
    assert len(columns) == 9 and all(columns), statements
    for match in columns:
        named = {c.strip() for c in match.group(1).split(",")}
        assert named <= _DISPATCH_COLUMNS, named - _DISPATCH_COLUMNS
    from tests.migration_defs import MIGRATIONS
    post = " ".join((MIGRATIONS / "656_notifications_w6_prune.sql").read_text().split())
    kept = re.search(r"v_cols is distinct from array\[([^\]]*)\]", post)
    assert kept and set(re.findall(r"'(\w+)'", kept.group(1))) == _DISPATCH_COLUMNS - {"match_reason"}
    added = " ".join((MIGRATIONS / "735_watchdog_notify_kinds.sql").read_text().split())
    assert "alter table public.notification_dispatches add column if not exists match_reason text;" in added


def test_the_unread_count_is_one_number() -> None:
    """Bell and badge count one visible set (program § 3.6): no per-source breakdown, no scope."""
    conn = _FakeConn([(lambda s: s.startswith("SELECT count(*) FROM notification_dispatches d"),
                       [(7,)], 0)])
    assert get_unread_count(conn) == {"unread_count": 7}  # type: ignore[arg-type]
    [(sql, params)] = conn.executed
    assert "GROUP BY" not in sql and "source_kind" not in sql and params is None


def test_dispatch_select_labels_a_share_from_its_actor_and_live_name_never_stored_text() -> None:
    """A share stores no text (F17, R11; migration 656 dropped `message`): the feed hands the SPA the
    sharer's email through share_party_emails(), spelled as api/shares.party_label spells it (NULL
    once no share joins the two accounts), the shared thing's live name and a pipeline's mode."""
    from api import shares
    from api.notifications import _DISPATCH_FROM, _DISPATCH_SELECT

    sql = " ".join(_DISPATCH_SELECT.split())
    assert "message" not in sql and "message" not in _DISPATCH_FROM
    assert (f"CASE WHEN d.actor_account_id IS NOT NULL THEN {shares.party_label('d.actor_account_id')} "
            "END AS actor_email") in sql
    assert ("CASE d.change_kind WHEN 'shared_collection' THEN c.name WHEN 'shared_dashboard' THEN "
            "(SELECT sd.name FROM stats_dashboards sd WHERE sd.id::text = d.event_ref) END AS share_name") in sql
    assert "CASE WHEN d.change_kind = 'shared_pipeline' THEN d.event_ref END AS share_mode" in sql


def test_dispatch_select_names_what_a_share_opens() -> None:
    """A share's bell row opens its collection, the owner's pipeline or the dashboard: the kind is
    its change_kind's suffix (`shared_<collection|pipeline|dashboard>`, migration 652) and its id
    is the row's own column, the one api/shares.py writes it to (F24: no key is parsed): the
    collection id, the owner (the actor), the dashboard id (the event_ref). Every other producer
    reads NULL for both. No row is left labelled 'shared' to fall back for: migration 653
    relabels any 652's re-run missed before it builds the identity."""
    import inspect

    from api import notifications as nf

    assert ("CASE WHEN d.source_kind = 'share' THEN split_part(d.change_kind, '_', 2) END AS share_kind, "
            "CASE d.change_kind WHEN 'shared_collection' THEN d.collection_id::text "
            "  WHEN 'shared_pipeline' THEN d.actor_account_id::text "
            "  WHEN 'shared_dashboard' THEN d.event_ref END AS share_ref") in nf._DISPATCH_SELECT
    assert "dedupe_key" not in nf._DISPATCH_SELECT + nf._DISPATCH_FROM
    assert "split_part(d.dedupe_key" not in inspect.getsource(nf)


def test_dispatch_feed_reads_listings_through_the_public_view() -> None:
    """Every listing column on the feed comes from `listings_public`, never from
    `listings`.

    This feed runs on the tenant pool — `SET LOCAL ROLE authenticated` — and
    `listings` has carried RLS with NO POLICY since migration 001, so the bare
    table is empty under that role and the whole projection arrived NULL. The
    owner-rights view returns the same rows with no WHERE of its own. It is also
    the only relation in reach that can publish `display_label`: migration 501
    revokes `listing_location` from `authenticated` outright, so a direct join
    would be a hard permission error rather than a silent blank.
    """
    from api.notifications import _DISPATCH_FROM, _LISTING_PROJECTION

    assert "LEFT JOIN listings_public lp ON lp.id = d.listing_id" in _DISPATCH_FROM
    assert "JOIN listings l " not in _DISPATCH_FROM
    # The place is the PROPERTY's location ad's (ruling R5), never the advert's own.
    assert "LEFT JOIN properties_public pp ON pp.property_id = d.property_id" in _DISPATCH_FROM
    assert "pp.display_label" in _LISTING_PROJECTION
    assert "lp.display_label" not in _LISTING_PROJECTION
    # No column may sneak back onto the RLS-dark table alias.
    assert " l." not in f" {_LISTING_PROJECTION}"


def test_feed_mf_yield_is_the_number_the_matcher_filtered_on() -> None:
    """The feed shows the MF yield of the dispatch's PROPERTY, read off the same
    `properties_public` row, by the same key, that the matcher's yield bound was
    evaluated against — so a row that fired on `yield >= 4` can never show a
    different yield next to it (the listing-grain column it replaced could)."""
    from api import notifications as nf

    where, _ = _build_match_clauses(WatchdogFilterSpec(min_mf_gross_yield_pct=4.0))
    assert "l.mf_gross_yield_pct >= %(min_mf_gross_yield_pct)s" in where
    for statement in (nf._SLICE_SEED_SQL, nf._SLICE_PASS_SQL):
        assert nf._SLICE_CTE in statement
    assert "FROM properties_public l " in nf._SLICE_CTE and "{where}" in nf._SLICE_CTE

    assert "LEFT JOIN properties_public pp ON pp.property_id = d.property_id" in nf._DISPATCH_FROM
    assert "pp.mf_gross_yield_pct" in nf._LISTING_PROJECTION
    assert "lp.mf_" not in nf._LISTING_PROJECTION


def test_mark_all_seen_scoped_filters_by_source() -> None:
    script: list[tuple[Any, list[tuple[Any, ...]], int]] = [
        (lambda s: "UPDATE notification_dispatches d SET seen_at" in s, [], 5),
    ]
    conn = _FakeConn(script)
    assert mark_all_seen(conn) == 5  # type: ignore[arg-type]
    assert mark_all_seen(conn, source_kind="watchdog") == 5  # type: ignore[arg-type]
    scoped_sql = [
        s for s, _ in conn.executed if "UPDATE notification_dispatches" in s
    ][-1]
    assert "AND d.source_kind = %s" in scoped_sql


# --- VISIBLE: one fragment for every in-app read, count and update (program § 3.6) ---


def test_visible_is_reachable_and_not_retracted_on_every_role() -> None:
    """REACHABLE: not a marked repeat, not dismissed by the event's OWN account, not from a retired
    watchdog; VISIBLE adds: not a retracted 'new'. The account is named, never left to the caller's
    policy: W4's fan-out reads on the service role, which bypasses RLS, so
    `property_dismissals_public` (no account column) would there hide every account's alert once
    anyone dismissed the property; the retraction reads `properties`, which every signed-in caller
    reads whole (migration 349), so it too answers the same on both. The push ledger embeds the
    one fragment by reference."""
    from api import push_delivery as pd
    from api.notifications import REACHABLE_SQL, RETRACTED_SQL, VISIBLE_SQL

    arms = [a.strip() for a in REACHABLE_SQL.split(" AND ", 1)]
    assert arms[0] == "d.duplicate_of IS NULL"
    assert ("NOT EXISTS (SELECT 1 FROM property_dismissals pd WHERE pd.property_id = d.property_id "
            "AND pd.account_id = d.account_id AND pd.lifted_at IS NULL)") in REACHABLE_SQL
    assert REACHABLE_SQL.endswith(
        "AND (d.subscription_id IS NULL OR EXISTS (SELECT 1 FROM notification_subscriptions ws "
        "WHERE ws.id = d.subscription_id AND ws.retired_at IS NULL))")
    assert "property_dismissals_public" not in VISIBLE_SQL
    assert VISIBLE_SQL == f"{REACHABLE_SQL} AND NOT {RETRACTED_SQL}"
    assert pd.VISIBLE_SQL is VISIBLE_SQL
    for sql in (pd.FANOUT_SQL, pd.CLAIM_SQL, pd.UNREAD_SQL, pd.RECALL_SQL):
        assert VISIBLE_SQL in sql


def test_every_in_app_read_count_and_update_carries_visible() -> None:
    """The badge, the feed, its total, mark-all-seen and each watchdog's dispatch count must agree
    on what is visible — a count that includes rows the list hides is the badge-vs-list divergence
    migrations 351/378 exist to close, and a bell that counts a merge's marked repeat or a retired
    watchdog's alert would count an alert the feed never shows. The tap path (mark-seen and the
    row it returns) reads REACHABLE: a retracted alert leaves attention, never a direct tap on a
    push already sent, which opens the property the alert now names."""
    from api.notifications import (
        _RESOLVE_DISPATCH_SQL, REACHABLE_SQL, VISIBLE_SQL, get_subscription, list_dispatches,
        list_subscriptions, mark_dispatch_seen,
    )

    resolve = " ".join(_RESOLVE_DISPATCH_SQL.split())
    conn = _FakeConn([(lambda s: s == resolve, [("d-1",)], 1)])
    get_unread_count(conn)  # type: ignore[arg-type]
    mark_all_seen(conn)  # type: ignore[arg-type]
    mark_all_seen(conn, source_kind="watchdog")  # type: ignore[arg-type]
    list_dispatches(conn)  # type: ignore[arg-type]
    list_dispatches(conn, subscription_id="s-1", seen="unseen")  # type: ignore[arg-type]
    get_subscription(conn, "s-1")  # type: ignore[arg-type]
    list_subscriptions(conn)  # type: ignore[arg-type]
    shown = [s for s, _ in conn.executed if "notification_dispatches" in s]
    mark_dispatch_seen(conn, "d-1")  # type: ignore[arg-type]
    # mark-seen's first statement only resolves the id it was handed (to the row a merge marked it
    # a duplicate of); the row it stamps and returns is REACHABLE-checked.
    tapped = [s for s, _ in conn.executed[len(shown):]
              if "notification_dispatches" in s and s != resolve]
    assert len(shown) == 9 and len(tapped) == 2
    for sql in shown:
        assert VISIBLE_SQL in sql, sql
    for sql in tapped:
        assert REACHABLE_SQL in sql and VISIBLE_SQL not in sql, sql
    totals = [s for s in shown if s.startswith("SELECT count(*) FROM notification_dispatches d")]
    assert len(totals) == 3  # the badge, and the two feed reads' first-page totals
    updates = [s for s in shown + tapped
               if s.startswith("UPDATE notification_dispatches d SET seen_at")]
    assert len(updates) == 3  # mark-all-seen twice, mark-seen once


def test_a_hidden_dispatch_is_a_404_on_mark_seen(monkeypatch: pytest.MonkeyPatch) -> None:
    """A repeat, a dismissed property's or a retired watchdog's dispatch is not reachable, so
    marking it seen changes nothing and finds nothing: the route answers 404 (program § 3.5:
    the tap then lands on /notifications)."""
    from fastapi.testclient import TestClient

    from api import tenant_pool
    from api.main import app
    from api.notifications import REACHABLE_SQL, mark_dispatch_seen

    conn = _FakeConn([
        (lambda s: s.startswith("SELECT coalesce(d.duplicate_of, d.id)"), [("d-1",)], 1),
        (lambda s: s.startswith("UPDATE notification_dispatches d"), [], 0),
    ])
    assert mark_dispatch_seen(conn, "d-1") is None  # type: ignore[arg-type]
    _resolve, update, fetch = (s for s, _ in conn.executed)
    assert update == ("UPDATE notification_dispatches d SET seen_at = now() "
                      f"WHERE d.id = %s AND d.seen_at IS NULL AND {REACHABLE_SQL}")
    assert fetch.endswith(f"WHERE d.id = %s AND {REACHABLE_SQL}")

    app.dependency_overrides[tenant_pool.tenant_conn] = lambda: _FakeConn([])
    try:
        res = TestClient(app).post("/notifications/dispatches/d-1/mark-seen")
    finally:
        app.dependency_overrides.pop(tenant_pool.tenant_conn, None)
    assert res.status_code == 404 and res.json() == {"detail": "dispatch not found"}


def test_mark_seen_follows_a_duplicate_to_the_row_it_duplicates() -> None:
    """A push tapped for an alert a merge later marked a duplicate lands on the survivor's alert:
    the id resolves to coalesce(duplicate_of, id) first, and THAT row is stamped and returned; an
    id that resolves to nothing (another account's, under RLS) is the route's 404."""
    from api.notifications import _RESOLVE_DISPATCH_SQL, mark_dispatch_seen

    kept = "22222222-2222-2222-2222-222222222222"
    conn = _FakeConn([
        (lambda s: s.startswith("SELECT coalesce(d.duplicate_of, d.id)"), [(UUID(kept),)], 1),
        (lambda s: s.startswith("UPDATE notification_dispatches d"), [], 1),
    ])
    mark_dispatch_seen(conn, "11111111-1111-1111-1111-111111111111")  # type: ignore[arg-type]
    (resolve, asked), (_update, stamped), (_fetch, read) = conn.executed
    assert resolve == ("SELECT coalesce(d.duplicate_of, d.id) FROM notification_dispatches d "
                       "WHERE d.id = %s") == " ".join(_RESOLVE_DISPATCH_SQL.split())
    assert asked == ("11111111-1111-1111-1111-111111111111",)
    assert stamped == read == (kept,)

    nothing = _FakeConn([])
    assert mark_dispatch_seen(nothing, "foreign") is None  # type: ignore[arg-type]
    assert len(nothing.executed) == 1, "nothing is stamped for an id that resolves to nothing"


def test_reactivated_is_anchored_on_events_never_on_a_merges_duplicates() -> None:
    """A merge re-points the retired property's 'inactive' (and 'reactivated') rows and marks the
    ones the survivor already holds; such a duplicate must neither anchor a 'reactivated' (the
    latest 'inactive' it told) nor stand in for one already told (the NOT EXISTS)."""
    from api import notifications as nf

    for source in (nf._MONITOR, nf._WATCHDOG):
        sql = " ".join(nf._reactivated_sql(source).split())
        told = sql.split("told AS (", 1)[1].split(") INSERT", 1)[0]
        assert (f"WHERE {source.scope} AND change_kind = 'inactive' AND duplicate_of IS NULL "
                f"ORDER BY {source.column}, property_id, dispatched_at DESC") in told
        again = sql.split("AND NOT EXISTS (", 1)[1]
        assert ("AND r.change_kind = 'reactivated' AND r.duplicate_of IS NULL "
                "AND r.dispatched_at > e.told_at") in again
    assert nf._WATCHDOG.scope.endswith("subscription_id = %(subscription_id)s::uuid"), "its own alerts only"


def test_detection_never_consults_dismissals() -> None:
    """Suppressing an INSERT would lose the event for good on undo: a slice takes a property in
    whatever its owner did with it, and the `reactivated` detector keys off a prior `inactive`
    dispatch, so no producer statement reads a dismissal."""
    import inspect

    from api import notifications as nf

    for fn in (nf.match_once, nf._slice_pass, nf.match_changes_once, nf.match_lifecycle_once):
        assert "dismiss" not in inspect.getsource(fn), fn.__name__
    for sql in (nf._CANONICAL_PRICE_STEPS, nf._SLICE_SEED_SQL, nf._SLICE_PASS_SQL,
                nf._WATCHDOG_PRICE_SQL, nf._WATCHDOG_SLICE_PRICE_SQL, nf._MONITOR_PRICE_SQL,
                nf._MONITOR_INACTIVE_SQL, nf._MONITOR_REACTIVATED_SQL,
                nf._MONITOR_NEW_SOURCE_SQL, nf._WATCHDOG_INACTIVE_SQL,
                nf._WATCHDOG_NEW_SOURCE_SQL, nf._WATCHDOG_SLICE_NEW_SOURCE_SQL):
        assert "dismiss" not in sql


def test_match_once_skips_invalid_filter_spec() -> None:
    """A subscription whose filter_spec doesn't validate (e.g. corrupt
    legacy row) must NOT crash the whole matcher pass — log + skip."""
    good_id = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    bad_id = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
    since = datetime(2026, 5, 1, tzinfo=timezone.utc)

    script: list[tuple[Any, list[tuple[Any, ...]], int]] = [
        (
            lambda s: "FROM notification_subscriptions WHERE is_active" in s,
            [
                # Invalid: a map box with west >= east.
                (bad_id, {"bounds": {"west": 14.5, "south": 50.0, "east": 14.0, "north": 50.2}}, since,
                 _DEFAULT_KINDS),
                # Valid spec
                (good_id, {}, since, _DEFAULT_KINDS),
            ],
            2,
        ),
        *_slicing(since, told=1),
    ]
    conn = _FakeConn(script)

    stats = match_once(conn)  # type: ignore[arg-type]

    # Both rows counted as "evaluated" — the broken one was inspected
    # and skipped as invalid, and the surviving one fired one dispatch.
    assert stats == {"subscriptions_evaluated": 2, "subscriptions_invalid": 1,
                     "subscriptions_failed": 0, "matches_inserted": 1}


def _raise_for_poison(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the compile raise for any spec carrying the poison price bound."""
    import api.notifications as nf

    real = nf._build_match_clauses

    def fake(spec: WatchdogFilterSpec) -> Any:
        if spec.min_price_czk == 666:
            raise ValueError("a stored spec the compile refuses")
        return real(spec)

    monkeypatch.setattr(nf, "_build_match_clauses", fake)


def test_a_spec_the_compiler_refuses_is_invalid_never_failed(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """A spec the compile refuses (ValueError / LookupError) fails the same way every pass: it
    is counted invalid, which never reddens the lane's step, and the other subscription still
    matches."""
    _raise_for_poison(monkeypatch)
    since = datetime(2026, 5, 1, tzinfo=timezone.utc)
    script: list[tuple[Any, list[tuple[Any, ...]], int]] = [
        (
            lambda s: "FROM notification_subscriptions WHERE is_active" in s,
            [
                (UUID("cccccccc-cccc-cccc-cccc-cccccccccccc"), {"min_price_czk": 666}, since, _DEFAULT_KINDS),
                (UUID("dddddddd-dddd-dddd-dddd-dddddddddddd"), {}, since, _DEFAULT_KINDS),
            ],
            2,
        ),
        *_slicing(since, told=1),
    ]
    stats = match_once(_FakeConn(script))  # type: ignore[arg-type]
    assert stats == {"subscriptions_evaluated": 2, "subscriptions_invalid": 1,
                     "subscriptions_failed": 0, "matches_inserted": 1}


def test_a_watchdog_whose_statement_fails_rolls_back_alone() -> None:
    """Each watchdog's pass is its own transaction: a statement that raises (a timeout,
    a future spec field's bad SQL) is counted failed (the lane reddens its step) and the
    next watchdog still matches."""
    import psycopg

    bad, good = UUID("eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"), UUID("ffffffff-ffff-ffff-ffff-ffffffffffff")
    since = datetime(2026, 5, 1, tzinfo=timezone.utc)

    class _Raising(_FakeCursor):
        def execute(self, sql: str, params: Any = None) -> None:
            super().execute(sql, params)
            if sql.startswith("WITH slice AS") and params["subscription_id"] == str(bad):
                raise psycopg.errors.QueryCanceled("canceling statement due to statement timeout")

    conn = _FakeConn([
        (lambda s: "FROM notification_subscriptions WHERE is_active" in s,
         [(bad, {}, since, _DEFAULT_KINDS), (good, {}, since, _DEFAULT_KINDS)], 2),
        *_slicing(since, told=4),
    ])
    conn.cursor = lambda: _Raising(conn)  # type: ignore[method-assign]
    stats = match_once(conn)  # type: ignore[arg-type]
    assert stats == {"subscriptions_evaluated": 2, "subscriptions_invalid": 0,
                     "subscriptions_failed": 1, "matches_inserted": 4}
    assert conn.transactions == 3, "the forget, then one per watchdog"


def test_match_changes_once_counts_a_refused_spec_as_invalid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.notifications import match_changes_once

    _raise_for_poison(monkeypatch)
    since = datetime(2026, 5, 1, tzinfo=timezone.utc)
    script: list[tuple[Any, list[tuple[Any, ...]], int]] = [
        (
            lambda s: "FROM notification_subscriptions WHERE is_active" in s,
            [
                (UUID("cccccccc-cccc-cccc-cccc-cccccccccccc"), {"min_price_czk": 666}, since, _DEFAULT_KINDS),
                (UUID("dddddddd-dddd-dddd-dddd-dddddddddddd"), {}, since, _DEFAULT_KINDS),
            ],
            2,
        ),
        (lambda s: "INSERT INTO notification_dispatches" in s and "'watchdog'" in s, [], 1),
        (lambda s: "INSERT INTO notification_dispatches" in s, [], 2),
    ]
    stats = match_changes_once(_FakeConn(script))  # type: ignore[arg-type]
    assert stats == {"subscriptions_evaluated": 2, "subscriptions_invalid": 1,
                     "subscriptions_failed": 0, "changes_inserted": 3,
                     "collection_changes_inserted": 2}


# --- the publication rule (rule 16, migrations 668/669, toolkit/publication.py) ---


def test_the_published_rule_is_the_second_unconditional_clause() -> None:
    """Browse serves a property only once it is published (browse_projection, migration 669);
    the matcher reads properties_public, which does not gate, so it renders the one rule itself,
    right after the served rule, for every spec — a blank one and a full one alike."""
    from location_data.claims_common import served_location_predicate
    from toolkit.publication import published_property_predicate

    rule = published_property_predicate("l.property_id")
    box = {"west": 14.3, "south": 50.0, "east": 14.5, "north": 50.2}
    for spec in (WatchdogFilterSpec(),
                 WatchdogFilterSpec(category_main_in=["byt"], min_price_czk=1, bounds=box)):
        where, _ = _build_match_clauses(spec)
        assert where[:2] == [served_location_predicate("l.location_listing_ref_id"), rule]
        assert where.count(rule) == 1


def test_both_watchdog_producers_tell_only_a_published_property() -> None:
    """The slice compiles the spec through `_build_match_clauses`, so it carries the rule, and a
    watchdog's price_drop either carries it too or reads that slice; the collection monitor does
    not (a change on a curated property is not its first appearance)."""
    from api.notifications import match_changes_once
    from toolkit.publication import published_property_predicate

    rule = published_property_predicate("l.property_id")
    since = datetime(2026, 10, 23, tzinfo=timezone.utc)
    rows = [(UUID("46a6005f-dc32-4d94-9ced-4d262b57ef6b"), {}, since, list(WATCHDOG_KINDS)),
            (UUID("47a6005f-dc32-4d94-9ced-4d262b57ef6b"), {}, since, ["price_drop"])]
    conn = _FakeConn([
        (lambda s: "FROM notification_subscriptions WHERE is_active" in s, rows, 2),
        *_slicing(since),
    ])
    match_once(conn)  # type: ignore[arg-type]
    match_changes_once(conn)  # type: ignore[arg-type]
    inserts = [s for s, _ in conn.executed if "INSERT INTO notification_dispatches" in s]
    watchdog = [s for s in inserts if "'watchdog'" in s]
    sliced = [s for s in watchdog if "watchdog_slice_members" in s and not s.startswith("WITH slice AS")]
    # The fake serves both rows to every step: two slices, then each one's price step.
    assert len(watchdog) == 4 and len(sliced) == 1
    assert all(rule in s for s in watchdog if s not in sliced)
    monitor = [s for s in inserts if s not in watchdog]
    assert monitor and not any(rule in s for s in monitor)


def test_the_retraction_arm_hides_a_new_its_property_now_predates() -> None:
    """Ruling 5: a 'new' told as newly listed (or written before migration 735, when every one was)
    is hidden exactly when the property it now names was first seen more than NEW_WINDOW before the
    alert was told; a New match for any other reason names an older property by design. And a 'new'
    its watchdog already told, unretracted, about the same property inside NEW_WINDOW: merge twins
    (the executed cases: tests/test_735_watchdog_notify_kinds_live.py). Derived, no placeholder
    (every statement embedding VISIBLE keeps its own parameters)."""
    from api.notifications import NEW_WINDOW, RETRACTED_SQL, VISIBLE_SQL

    assert NEW_WINDOW == "24 hours"
    stale = ("(coalesce({a}.match_reason, 'listed') = 'listed' AND EXISTS (SELECT 1 FROM properties rp "
             "WHERE rp.id = {a}.property_id AND rp.first_seen_at <= {a}.dispatched_at - interval '24 hours'))")
    assert RETRACTED_SQL == (
        f"(d.change_kind = 'new' AND ({stale.format(a='d')}"
        " OR EXISTS (SELECT 1 FROM notification_dispatches tw WHERE tw.account_id = d.account_id"
        " AND tw.subscription_id = d.subscription_id AND tw.collection_id IS NULL"
        " AND tw.change_kind = 'new' AND tw.property_id = d.property_id AND tw.duplicate_of IS NULL"
        " AND tw.dispatched_at < d.dispatched_at AND tw.dispatched_at > d.dispatched_at - interval '24 hours'"
        f" AND NOT {stale.format(a='tw')})))")
    assert "%" not in VISIBLE_SQL


# --- match_changes_once (the property-change matcher, Slice 2b) ----------


from api.notifications import match_changes_once


def test_match_changes_once_emits_price_drop_for_matching_subs() -> None:
    """Step price: ONE canonical-ad step fragment feeds a `price_drop` per (matching
    watchdog x drop step after its active_since), keyed per snapshot, and then the
    collection monitor's price_drop / price_rise over the same fragment. (A watchdog
    telling 'new' reads its slice instead: test_a_watchdog_tells_only_the_kinds_it_is_set_to.)"""
    from api import notifications as nf

    sub_id = UUID("12121212-1212-1212-1212-121212121212")
    since = datetime(2026, 10, 1, tzinfo=timezone.utc)
    script: list[tuple[Any, list[tuple[Any, ...]], int]] = [
        (
            lambda s: "FROM notification_subscriptions WHERE is_active" in s,
            [(sub_id, {"category_main": "byt", "category_type": "pronajem"}, since, ["price_drop"])],
            1,
        ),
        (lambda s: "INSERT INTO notification_dispatches" in s and "'watchdog'" in s, [], 2),
        (lambda s: "INSERT INTO notification_dispatches" in s, [], 1),
    ]
    conn = _FakeConn(script)

    stats = match_changes_once(conn)  # type: ignore[arg-type]

    assert stats == {"subscriptions_evaluated": 1, "subscriptions_invalid": 0,
                     "subscriptions_failed": 0, "changes_inserted": 3,
                     "collection_changes_inserted": 1}
    assert conn.transactions == 2, "the watchdog's INSERT and the collections' each commit"
    watchdog, collection = [(s, p) for s, p in conn.executed
                            if "INSERT INTO notification_dispatches" in s]
    fragment = " ".join(nf._CANONICAL_PRICE_STEPS.split())
    kind = "CASE WHEN e.price_czk < e.prev_price_czk THEN 'price_drop' ELSE 'price_rise' END"
    for sql, params in (watchdog, collection):
        assert f"{fragment} INSERT" in sql
        assert f"WHERE e.scraped_at > m.since AND {kind} = ANY(m.kinds)" in sql
        assert params["price_window"] == "7 days"
        assert sql.endswith(ON_EVENT_CONFLICT)
    sql, params = watchdog
    assert params["kinds"] == ["price_drop"], "it tells drops only"
    assert params["active_since"] == since and params["category_type"] == "pronajem"
    assert "FROM canonical_steps e JOIN properties_public l ON l.property_id = e.property_id AND " in sql
    assert "%(kinds)s::text[] AS kinds) m" in sql
    sql, params = collection
    assert "kinds" not in params, "a collection's kinds are its own column"
    assert "FROM canonical_steps e JOIN members m ON m.property_id = e.property_id" in sql


def test_match_changes_once_with_no_live_watchdog_runs_only_the_collection_half() -> None:
    conn = _FakeConn([(lambda s: "INSERT INTO notification_dispatches" in s, [], 0)])
    stats = match_changes_once(conn)  # type: ignore[arg-type]
    assert stats == {"subscriptions_evaluated": 0, "subscriptions_invalid": 0,
                     "subscriptions_failed": 0, "changes_inserted": 0,
                     "collection_changes_inserted": 0}
    inserts = [s for s, _ in conn.executed if "INSERT INTO notification_dispatches" in s]
    assert len(inserts) == 1 and "'collection_monitor'" in inserts[0]




# --- one event identity, continuous with history (notifications W3) ----------------------
#
# From W3 the identity index (EVENT_IDENTITY, migration 653) is the one "same event?", and
# nothing writes dedupe_key. A row written before W3 carries the identity migration 652
# backfilled from its key; a producer re-running over the same event must render the SAME
# identity, or the event would be told twice. So each producer statement's columns are
# RENDERED against fixture rows by the small evaluator below and compared with what 652 reads
# off the pre-W3 key grammar, spelled out in Python.

import math as _math

_TOKEN = re.compile(r"\s*('(?:[^']|'')*'|%\(\w+\)s|\|\||::|[(),<]|[A-Za-z_][\w.]*)")


def _tokens(sql: str) -> list[str]:
    out, i = [], 0
    while i < len(sql):
        m = _TOKEN.match(sql, i)
        if not m:
            if sql[i:].strip() == "":
                break
            raise AssertionError(f"cannot tokenize at {sql[i:i + 40]!r}")
        out.append(m.group(1))
        i = m.end()
    return out


class _Expr:
    """Evaluates the producers' SELECT-list subset: literals, params, column refs, `||`, `::text`
    / `::bigint`, floor(), extract(epoch FROM …), coalesce(a, b), CASE WHEN a < b THEN … ELSE … END."""

    def __init__(self, toks: list[str], row: dict[str, Any], params: dict[str, Any]) -> None:
        self.toks, self.i, self.row, self.params = toks, 0, row, params

    def peek(self) -> str | None:
        return self.toks[self.i] if self.i < len(self.toks) else None

    def take(self, want: str | None = None) -> str:
        tok = self.toks[self.i]
        assert want is None or tok.upper() == want.upper(), (want, tok)
        self.i += 1
        return tok

    def concat(self) -> Any:
        parts = [self.cast()]
        while self.peek() == "||":
            self.take()
            parts.append(self.cast())
        if len(parts) == 1:
            return parts[0]
        return None if any(p is None for p in parts) else "".join(str(p) for p in parts)

    def cast(self) -> Any:
        value = self.atom()
        while self.peek() == "::":
            self.take()
            kind = self.take().lower()
            if value is not None:
                value = {"text": str, "bigint": int}[kind](value)
        return value

    def atom(self) -> Any:
        tok = self.take()
        if tok.startswith("'"):
            return tok[1:-1].replace("''", "'")
        if tok.startswith("%("):
            return self.params[tok[2:-2]]
        upper = tok.upper()
        if upper == "NULL":
            return None
        if upper == "CASE":
            self.take("WHEN")
            left = self.cast()
            self.take("<")
            right = self.cast()
            self.take("THEN")
            yes = self.concat()
            self.take("ELSE")
            no = self.concat()
            self.take("END")
            return yes if left < right else no
        if self.peek() == "(":
            self.take("(")
            if upper == "EXTRACT":
                self.take("epoch")
                self.take("FROM")
                value = self.cast().timestamp()
            elif upper == "FLOOR":
                value = _math.floor(self.concat())
            elif upper == "COALESCE":
                first = self.concat()
                self.take(",")
                second = self.concat()
                value = first if first is not None else second
            else:
                raise AssertionError(f"unknown function {tok}")
            self.take(")")
            return value
        return self.row[tok.split(".")[-1]]


def _select_list(select: str) -> str:
    """The SELECT list: everything before the first FROM outside parentheses and literals."""
    depth, quoted = 0, False
    for i, ch in enumerate(select):
        if ch == "'":
            quoted = not quoted
        elif not quoted and ch in "()":
            depth += 1 if ch == "(" else -1
        elif not quoted and depth == 0 and select.startswith(" FROM ", i):
            return select[:i]
    raise AssertionError("no FROM")


def _rendered(sql: str, row: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    """column -> value the INSERT's SELECT list renders for one fixture row."""
    sql = " ".join(sql.split())
    head, select = sql.split("INSERT INTO notification_dispatches (", 1)[1].split(") SELECT ", 1)
    columns = [c.strip() for c in head.split(",")]
    expr = _Expr([*_tokens(_select_list(select)), "FROM"], row, params)
    values = []
    for n, _ in enumerate(columns):
        values.append(expr.concat())
        expr.take("," if n < len(columns) - 1 else "FROM")
    return dict(zip(columns, values))


_SUB = "46a6005f-dc32-4d94-9ced-4d262b57ef6b"
_GONE_AT = datetime(2026, 10, 19, 8, 30, 15, 999_000, tzinfo=timezone.utc)
_FLAGGED_AT = datetime(2026, 10, 20, 9, 0, 0, tzinfo=timezone.utc)


def _pre_w3(kind: str, r: dict[str, Any]) -> str:
    """The dedupe_key grammar every row written before W3 carries (migration 206, Sprint C)."""
    if kind == "new":
        return f"wd:{_SUB}:new:{r['property_id']}"
    if kind == "wd_price":
        return f"wd:{_SUB}:price_drop:{r['snapshot_id']}"
    if kind == "cm_price":
        step = "price_drop" if r["price_czk"] < r["prev_price_czk"] else "price_rise"
        return f"cm:{r['collection_id']}:{step}:{r['snapshot_id']}"
    if kind in ("inactive", "reactivated"):
        at = r["inactive_at"] if kind == "inactive" else r["told_at"]
        return f"cm:{r['collection_id']}:{kind}:{r['property_id']}:{int(_math.floor(at.timestamp()))}"
    if kind == "new_source":
        handle = r["sreality_id"] if r["sreality_id"] is not None else f"l{r['listing_id']}"
        return f"cm:{r['collection_id']}:new_source:{handle}"
    raise AssertionError(kind)


_PRICE_ROW = {"property_id": 101, "listing_id": 9001, "price_czk": 4_900_000,
              "prev_price_czk": 5_000_000, "snapshot_id": 5001, "collection_id": 7, "source_id": 7}


@pytest.mark.parametrize(("statement", "kind", "row", "event_ref"), [
    ("_WATCHDOG_PRICE_SQL", "wd_price", {**_PRICE_ROW, "source_id": _SUB}, "5001"),
    ("_MONITOR_PRICE_SQL", "cm_price", _PRICE_ROW, "5001"),
    ("_MONITOR_PRICE_SQL", "cm_price",
     {**_PRICE_ROW, "price_czk": 5_100_000, "snapshot_id": 5002}, "5002"),
    ("_MONITOR_INACTIVE_SQL", "inactive",
     {"collection_id": 7, "source_id": 7, "property_id": 101, "listing_id": 9001,
      "inactive_at": _GONE_AT}, str(int(_GONE_AT.timestamp()))),
    ("_MONITOR_REACTIVATED_SQL", "reactivated",
     {"collection_id": 7, "source_id": 7, "property_id": 101, "listing_id": 9001,
      "told_at": _FLAGGED_AT}, str(int(_FLAGGED_AT.timestamp()))),
    ("_MONITOR_NEW_SOURCE_SQL", "new_source",
     {"collection_id": 7, "source_id": 7, "property_id": 101, "listing_id": 402678, "sreality_id": 3141592},
     "402678"),
    ("_MONITOR_NEW_SOURCE_SQL", "new_source",
     {"collection_id": 7, "source_id": 7, "property_id": 101, "listing_id": 402678, "sreality_id": None},
     "402678"),
    # A legacy handle is signed (2026-10-21: the one live new_source row keys on -248427).
    ("_MONITOR_NEW_SOURCE_SQL", "new_source",
     {"collection_id": 7, "source_id": 7, "property_id": 101, "listing_id": 402678, "sreality_id": -248427},
     "402678"),
], ids=["wd-price", "cm-drop", "cm-rise", "inactive", "reactivated", "new-source",
        "new-source-no-sreality", "new-source-legacy-handle"])
def _backfilled_652(kind: str, key: str, row: dict[str, Any]) -> str | None:
    """event_ref as migration 652 (and 653's re-run of it) fills it from a pre-W3 key."""
    seg = key.split(":")
    if kind in ("wd_price", "cm_price"):
        return seg[3]  # 652 reads trigger_snapshot_id, which every price key ends in
    if kind in ("inactive", "reactivated"):
        return seg[4]
    if kind == "new_source":
        # 'l<id>' is the listing id; a legacy handle maps once through listings.sreality_id.
        return seg[3][1:] if seg[3].startswith("l") else str(row["listing_id"])
    return None


@pytest.mark.parametrize(("statement", "kind", "row", "event_ref"), [
    ("_WATCHDOG_PRICE_SQL", "wd_price", {**_PRICE_ROW, "source_id": _SUB}, "5001"),
    ("_MONITOR_PRICE_SQL", "cm_price", _PRICE_ROW, "5001"),
    ("_MONITOR_PRICE_SQL", "cm_price",
     {**_PRICE_ROW, "price_czk": 5_100_000, "snapshot_id": 5002}, "5002"),
    ("_MONITOR_INACTIVE_SQL", "inactive",
     {"collection_id": 7, "source_id": 7, "property_id": 101, "listing_id": 9001,
      "inactive_at": _GONE_AT}, str(int(_GONE_AT.timestamp()))),
    # event_ref = the epoch of the inactive dispatch it answers, as the key always was (it
    # supersedes the program's "the id of the inactive dispatch": production's grammar wins).
    ("_MONITOR_REACTIVATED_SQL", "reactivated",
     {"collection_id": 7, "source_id": 7, "property_id": 101, "listing_id": 9001,
      "told_at": _FLAGGED_AT}, str(int(_FLAGGED_AT.timestamp()))),
    ("_MONITOR_NEW_SOURCE_SQL", "new_source",
     {"collection_id": 7, "source_id": 7, "property_id": 101, "listing_id": 402678, "sreality_id": 3141592},
     "402678"),
    ("_MONITOR_NEW_SOURCE_SQL", "new_source",
     {"collection_id": 7, "source_id": 7, "property_id": 101, "listing_id": 402678, "sreality_id": None},
     "402678"),
    # A legacy handle is signed (2026-10-21: the one live new_source row keys on -248427).
    ("_MONITOR_NEW_SOURCE_SQL", "new_source",
     {"collection_id": 7, "source_id": 7, "property_id": 101, "listing_id": 402678, "sreality_id": -248427},
     "402678"),
], ids=["wd-price", "cm-drop", "cm-rise", "inactive", "reactivated", "new-source",
        "new-source-no-sreality", "new-source-legacy-handle"])
def test_every_producer_renders_the_identity_its_pre_w3_row_carries(
    statement: str, kind: str, row: dict[str, Any], event_ref: str | None,
) -> None:
    from api import notifications as nf

    out = _rendered(getattr(nf, statement), row, {"subscription_id": _SUB})
    key = _pre_w3(kind, row)
    seg = key.split(":")
    assert out["event_ref"] == event_ref == _backfilled_652(kind, key, row)
    if seg[0] == "wd":
        assert (out["source_kind"], out["subscription_id"]) == ("watchdog", seg[1])
    else:
        assert (out["source_kind"], str(out["collection_id"])) == ("collection_monitor", seg[1])
    assert out["change_kind"] == seg[2]
    assert out["property_id"] == row["property_id"]
    if kind in ("new", "inactive", "reactivated"):
        assert str(out["property_id"]) == seg[3], "the key's property is the row's"
    # The rest of the identity is the trigger's (account_id) or NULL: a detector has no actor,
    # and a row has one source, so the other source column is NULL (source_ck).
    other = "collection_id" if seg[0] == "wd" else "subscription_id"
    assert {c for c in nf.EVENT_IDENTITY if c not in out} == {"account_id", "actor_account_id", other}


def _conflict_target(sql: str) -> tuple[str, ...]:
    m = re.search(r"ON CONFLICT \(([^)]*)\) WHERE duplicate_of IS NULL DO NOTHING$", sql)
    assert m, sql[-200:]
    return tuple(c.strip() for c in m.group(1).split(","))


def test_every_insert_conflicts_on_the_one_identity_and_writes_no_key() -> None:
    """Each producer statement and the share grant end in ON CONFLICT (EVENT_IDENTITY) WHERE
    duplicate_of IS NULL DO NOTHING, the partial unique index migration 653 builds, and none
    writes the retired key or the snapshot column (a price row's snapshot is its event_ref)."""
    from api import notifications as nf
    from api import shares

    statements = [nf._WATCHDOG_PRICE_SQL, nf._WATCHDOG_SLICE_PRICE_SQL, nf._MONITOR_PRICE_SQL,
                  nf._MONITOR_INACTIVE_SQL, nf._MONITOR_REACTIVATED_SQL, nf._WATCHDOG_INACTIVE_SQL,
                  nf._MONITOR_NEW_SOURCE_SQL, nf._WATCHDOG_NEW_SOURCE_SQL, nf._WATCHDOG_SLICE_NEW_SOURCE_SQL,
                  shares._INSERT_SHARE_DISPATCH_SQL]
    # A slice pass's New matches are one CTE of a longer statement: the same conflict, then RETURNING.
    for sql in (nf._SLICE_SEED_SQL, nf._SLICE_PASS_SQL):
        told = " ".join(sql.split()).split("told AS ( ", 1)[1].split(" RETURNING 1", 1)[0]
        assert told.startswith("INSERT INTO notification_dispatches (")
        assert _conflict_target(told) == nf.EVENT_IDENTITY and told.count("ON CONFLICT") == 1
        assert "event_ref" in told.split(")", 1)[0] and "dedupe_key" not in sql
    for sql in statements:
        sql = " ".join(sql.split())
        head = sql.split("INSERT INTO notification_dispatches (", 1)[1].split(")", 1)[0]
        columns = {c.strip() for c in head.split(",")}
        assert "event_ref" in columns, sql
        assert not columns & {"dedupe_key", "trigger_snapshot_id"}, sql
        assert "dedupe_key" not in sql and "trigger_snapshot_id" not in sql, sql
        assert _conflict_target(sql) == nf.EVENT_IDENTITY
        assert sql.count("ON CONFLICT") == 1
    assert "actor_account_id" in " ".join(shares._INSERT_SHARE_DISPATCH_SQL.split())


def test_event_identity_is_the_index_migration_653_builds() -> None:
    """EVENT_IDENTITY, in index order, is notification_dispatches_identity_uq: NULLS NOT DISTINCT
    (a NULL source, property or event_ref is one value) and partial on the unmarked rows."""
    from api import notifications as nf
    from tests.migration_defs import MIGRATIONS

    sql = " ".join((MIGRATIONS / "653_notifications_w3_event_identity.sql").read_text().split())
    m = re.search(r"create unique index concurrently if not exists notification_dispatches_identity_uq "
                  r"on public\.notification_dispatches \(([^)]*)\) nulls not distinct "
                  r"where duplicate_of is null;", sql)
    assert m, "653 no longer builds the identity index this test reads"
    assert tuple(c.strip() for c in m.group(1).split(",")) == nf.EVENT_IDENTITY
    # The repeat marking (a) partitions on the same eight columns, so it marks exactly what the
    # index would refuse.
    part = re.search(r"partition by ([^)]*?) order by dispatched_at, id\)", sql)
    assert part and tuple(c.strip() for c in part.group(1).split(",")) == nf.EVENT_IDENTITY
    # And the $post$ check names them in the same order.
    post = re.search(r"v_cols is distinct from array\[([^\]]*)\]", sql)
    assert post and tuple(c.strip(" '") for c in post.group(1).split(",")) == nf.EVENT_IDENTITY


def test_the_change_kinds_the_producers_write_are_the_check_652_allows() -> None:
    """Every change_kind a producer renders or a share writes is in migration 652's CHECK, and 652
    keeps every value it found."""
    import inspect

    from api import shares
    from tests.migration_defs import MIGRATIONS

    sql = " ".join((MIGRATIONS / "652_notifications_w2a_event_columns.sql").read_text().split())
    check = sql.split("add constraint notification_dispatches_change_kind_ck check (", 1)[1]
    allowed = set(re.findall(r"'(\w+)'", check.split(");", 1)[0]))
    share_kinds = set(re.findall(r'"(shared_\w+)"', inspect.getsource(shares.grant)))
    assert share_kinds == {"shared_collection", "shared_dashboard", "shared_pipeline"}
    produced = {"new", "price_drop", "price_rise", "inactive", "reactivated", "new_source"}
    assert produced | share_kinds <= allowed
    assert {"shared", "system_alert", "broker_change"} <= allowed, "652 drops nothing"
