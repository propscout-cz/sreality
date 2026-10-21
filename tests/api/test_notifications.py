"""Hermetic tests for the notifications backend (Phase U2.7).

Covers WatchdogFilterSpec validation + the SQL-clause generator. The
matcher loop and FastAPI routes are integration-heavy (DB + asyncio
lifespan) and exercised via the live deployment, not here.
"""

from __future__ import annotations

import re

import pytest

pytest.importorskip("pydantic")

from pydantic import ValidationError

from api.notifications import (
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
    assert "l.category_main = ANY(%(category_main_in)s)" in where
    assert params["category_main_in"] == ["dum"]


def test_build_clauses_skips_unset_box() -> None:
    spec = WatchdogFilterSpec()
    where, params = _build_match_clauses(spec)
    assert not any("l.lat" in w or "l.lng" in w for w in where)
    assert not any(k.startswith("bbox_") for k in params)


def test_build_clauses_emits_the_map_box_when_set() -> None:
    """Rule 16: the box is the lat/lng band Browse's list (`queries.ts applyFilters`)
    and both stats RPCs (`bbox_*`) apply — no circle, no ST_DWithin, no own shape."""
    spec = WatchdogFilterSpec(bounds={"west": 14.40, "south": 50.06, "east": 14.44, "north": 50.10})
    where, params = _build_match_clauses(spec)
    assert "l.lng >= %(bbox_west)s" in where
    assert "l.lng <= %(bbox_east)s" in where
    assert "l.lat >= %(bbox_south)s" in where
    assert "l.lat <= %(bbox_north)s" in where
    assert not any("ST_DWithin" in w for w in where)
    # Anchored: `sl.geom` is the W5 consumer rule's own probe into listing_location,
    # which is not `properties_public.geom` and must not be flagged as one.
    assert not any(re.search(r"(?<![a-z_])l\.geom", w) for w in where)
    assert params["bbox_west"] == 14.40
    assert params["bbox_east"] == 14.44
    assert params["bbox_south"] == 50.06
    assert params["bbox_north"] == 50.10


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


def test_build_clauses_enumerated_columns() -> None:
    # A bare string still coerces to a single-element list (backward-compat).
    spec = WatchdogFilterSpec(furnished="ano", ownership=["osobni", "druzstevni"])
    assert spec.furnished == ["ano"]
    where, params = _build_match_clauses(spec)
    clauses = " ".join(where)
    assert "l.furnished = ANY(%(furnished)s)" in clauses
    assert "l.ownership = ANY(%(ownership)s)" in clauses
    assert params["furnished"] == ["ano"]
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
    assert ("l.condition = ANY(%(condition_match)s) OR "
            "(l.condition IS NULL OR NOT (l.condition = ANY(%(condition_match_canon)s)))") in clauses
    assert params["condition_match"] == ["novostavba"]
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
    get_unread_count,
    mark_all_seen,
    match_monitored_collections_once,
    match_once,
)


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

    def cursor(self) -> _FakeCursor:
        return _FakeCursor(self)


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


def test_matcher_lease_cas_acquires_and_yields() -> None:
    """The single-runner lease (migration 366) is a mig-279-shape CAS
    UPDATE ... RETURNING: a returned row means we hold it, no row means another
    runner does. Guards against N replicas fanning out N× market-wide scans."""
    from api.notifications import _try_matcher_lease

    acquired = _FakeConn(
        [(lambda s: "UPDATE notification_matcher_lease" in s, [(1,)], 1)]
    )
    assert _try_matcher_lease(acquired, "matcher:me") is True
    sql, params = acquired.executed[0]
    assert "notification_matcher_lease" in sql
    assert "RETURNING" in sql.upper()
    assert params["holder"] == "matcher:me"

    held = _FakeConn(
        [(lambda s: "UPDATE notification_matcher_lease" in s, [], 0)]
    )
    assert _try_matcher_lease(held, "matcher:me") is False


def test_a_stored_spec_with_a_chip_without_a_code_mutes_only_its_watchdog() -> None:
    """Fail CLOSED: a stored row the model now refuses is counted as failed and
    dispatches nothing — never a cohort widened to the whole country — while the
    healthy watchdog in the same pass still matches. Migration 606 deleted the
    last such rows; this is the rail if one ever reappears."""
    dead = UUID("46a6005f-dc32-4d94-9ced-4d262b57ef6b")
    healthy = UUID("5b1e4c8e-0d1f-4a77-9c3e-2a6f1d0b9e10")
    cursor_ts = datetime(2026, 10, 16, 12, 0, 0, tzinfo=timezone.utc)
    conn = _FakeConn([
        (lambda s: "FROM app_settings" in s, [], 0),
        (
            lambda s: "FROM notification_subscriptions WHERE is_active" in s,
            [
                (dead, {"districts": [{"name": "Jihlava"}]}, cursor_ts, ["in_app"]),
                (healthy, {"districts": [{"name": "Na Rybníčkách", "context": "Pardubice",
                                          "level": "ulice", "id": 666742}]},
                 cursor_ts, ["in_app"]),
            ],
            2,
        ),
        (lambda s: "SELECT max(first_seen_at), count(*) FROM" in s,
         [(datetime(2026, 10, 16, 13, 0, 0, tzinfo=timezone.utc), 4)], 0),
        (lambda s: "INSERT INTO notification_dispatches" in s
         and "image_lookback_minutes" in s, [], 0),
        (lambda s: "INSERT INTO notification_dispatches" in s, [], 2),
        (lambda s: "UPDATE notification_subscriptions SET last_matched_first_seen_at" in s,
         [], 1),
    ])
    stats = match_once(conn)  # type: ignore[arg-type]
    assert stats["subscriptions_evaluated"] == 2
    assert stats["subscriptions_failed"] == 1
    assert stats["matches_inserted"] == 2
    inserts = [p for sql, p in conn.executed
               if "INSERT INTO notification_dispatches" in sql and isinstance(p, dict)]
    assert inserts and {p.get("subscription_id") for p in inserts} == {str(healthy)}
    assert all(p["district_codes_ulice"] == [666742] for p in inserts)


def test_match_once_uses_per_subscription_cursor() -> None:
    """The matcher reads `last_matched_first_seen_at` per subscription
    and injects it as the `cursor` clause — proves the per-subscription
    cursor column drives the SQL, not the old global watermark."""
    sub_id = UUID("46a6005f-dc32-4d94-9ced-4d262b57ef6b")
    cursor_ts = datetime(2026, 5, 14, 13, 57, 48, tzinfo=timezone.utc)
    upper_ts = datetime(2026, 5, 15, 21, 0, 0, tzinfo=timezone.utc)

    script: list[tuple[Any, list[tuple[Any, ...]], int]] = [
        # app_settings reads for interval / window / image gate — default all.
        (lambda s: "FROM app_settings" in s, [], 0),
        # Active subscriptions query
        (
            lambda s: "FROM notification_subscriptions WHERE is_active" in s,
            [(sub_id, {"category_main": "byt", "category_type": "pronajem",
                       "districts": [{"name": "Praha", "level": "obec", "id": 554782}]},
              cursor_ts, ["email", "in_app"])],
            1,
        ),
        # Window upper-bound query (first_seen_at-keyed; the publication gate is gone)
        (lambda s: "SELECT max(first_seen_at), count(*) FROM" in s, [(upper_ts, 5)], 0),
        # Gate-pending lookback INSERT (default-on gate) — nothing released.
        (
            lambda s: "INSERT INTO notification_dispatches" in s
            and "image_lookback_minutes" in s,
            [],
            0,
        ),
        # Window INSERT dispatches — return rowcount 3 (idempotent dedup)
        (lambda s: "INSERT INTO notification_dispatches" in s, [], 3),
        # Cursor advance UPDATE — rowcount 1
        (lambda s: "UPDATE notification_subscriptions SET last_matched_first_seen_at" in s, [], 1),
    ]
    conn = _FakeConn(script)

    stats = match_once(conn)  # type: ignore[arg-type]

    assert stats == {
        "subscriptions_evaluated": 1,
        "subscriptions_failed": 0,
        "matches_inserted": 3,
        "gate_lookback_inserted": 0,
        "listings_in_window": 5,
        "cursors_advanced": 1,
    }

    # Verify the WHERE clause references the per-subscription cursor
    # parameter, not a global watermark.
    window_query = next(
        (sql, p) for sql, p in conn.executed if "max(first_seen_at), count(*)" in sql
    )
    sql, params = window_query
    # New-dispatch detection keys on arrival (first_seen_at) — the migration-273
    # published_at anchor died with the publication gate (NEW DEDUP cutoff §3).
    assert "l.first_seen_at > %(cursor)s" in sql
    assert isinstance(params, dict) and params["cursor"] == cursor_ts

    # And that the filter spec made it through too. The stored spec carries a
    # legacy scalar category_main; the before-validator lifts it to
    # category_main_in and the matcher emits an = ANY membership.
    assert params["category_main_in"] == ["byt"]
    assert params["category_type"] == "pronajem"
    assert params["district_codes_obec"] == [554782]
    assert "l.obec_id = ANY(%(district_codes_obec)s)" in sql

    # The 'new' dispatch INSERT writes the unified event shape: source_kind,
    # a per-property dedupe_key, ON CONFLICT on that key (migration 206), and the
    # producer-stamped target_channels (migration 208) — the subscription's
    # channels minus the always-implicit 'in_app'.
    insert_sql, insert_params = next(
        (sql, p) for sql, p in conn.executed
        if "INSERT INTO notification_dispatches" in sql
    )
    assert "'watchdog'" in insert_sql
    assert ":new:' || l.property_id::text" in insert_sql
    assert "ON CONFLICT (dedupe_key)" in insert_sql
    assert "%(target_channels)s::text[]" in insert_sql
    assert insert_params["target_channels"] == ["email"]


def test_collection_monitor_noop_when_nothing_monitored() -> None:
    """No monitored collection → cheap early-out, no INSERT attempted."""
    script: list[tuple[Any, list[tuple[Any, ...]], int]] = [
        (lambda s: "FROM collections WHERE monitoring_enabled = true" in s, [(0,)], 0),
    ]
    conn = _FakeConn(script)
    stats = match_monitored_collections_once(conn)  # type: ignore[arg-type]
    assert stats == {"monitored_collections": 0, "events_inserted": 0}
    assert not any(
        "INSERT INTO notification_dispatches" in s for s, _ in conn.executed
    )


def test_collection_monitor_emits_the_five_clean_kinds() -> None:
    """One set-based INSERT per detector across all monitored collections;
    every dispatch is source_kind='collection_monitor', collection-scoped
    dedupe, ON CONFLICT idempotent. broker_change is reserved, not emitted."""
    script: list[tuple[Any, list[tuple[Any, ...]], int]] = [
        (lambda s: "FROM collections WHERE monitoring_enabled = true" in s, [(2,)], 0),
        (lambda s: "FROM app_settings" in s, [], 0),  # window default
        (lambda s: "INSERT INTO notification_dispatches" in s, [], 2),
    ]
    conn = _FakeConn(script)
    stats = match_monitored_collections_once(conn)  # type: ignore[arg-type]
    assert stats["monitored_collections"] == 2
    # 4 INSERT statements (price_drop + price_rise share one CASE insert) x rowcount 2.
    assert stats["events_inserted"] == 8

    inserts = [
        sql for sql, _ in conn.executed
        if "INSERT INTO notification_dispatches" in sql
    ]
    assert len(inserts) == 4
    joined = " ".join(inserts)
    assert "'collection_monitor'" in joined
    for kind in ("'price_drop'", "'price_rise'", "'inactive'", "'reactivated'", "'new_source'"):
        assert kind in joined
    assert "'cm:'" in joined                       # collection-scoped dedupe prefix
    assert joined.count("ON CONFLICT (dedupe_key)") == 4
    assert "'broker_change'" not in joined          # reserved, no clean signal yet


def test_collection_monitor_dedupe_keys_survive_a_null_sreality_id() -> None:
    """No dedupe_key may concatenate a bare `sreality_id` (R2/Gate 2).

    `dedupe_key` is NOT NULL and `||` yields NULL if ANY operand is NULL, so a
    post-Gate-2 listing (sreality_id NULL) would make the whole key NULL and abort
    the ENTIRE collection-monitor pass with a not-null violation — every collection
    silently stops notifying, not just the one row. The `new_source` detector was
    the one keyed this way; it now COALESCEs onto the surrogate. This test pins the
    invariant for every detector so a future one can't reintroduce it."""
    script: list[tuple[Any, list[tuple[Any, ...]], int]] = [
        (lambda s: "FROM collections WHERE monitoring_enabled = true" in s, [(1,)], 0),
        (lambda s: "FROM app_settings" in s, [], 0),
        (lambda s: "INSERT INTO notification_dispatches" in s, [], 0),
    ]
    conn = _FakeConn(script)
    match_monitored_collections_once(conn)  # type: ignore[arg-type]

    for sql, _ in conn.executed:
        if "INSERT INTO notification_dispatches" not in sql:
            continue
        # The dedupe_key expression is the tail of the SELECT list; any bare
        # sreality_id concatenated into it is the bug.
        assert "|| src.sreality_id::text" not in sql
        assert "|| st.sreality_id::text" not in sql
        assert "|| l.sreality_id::text" not in sql
        if "new_source" in sql:
            # Positive form: the surrogate is the NULL-safe fallback.
            assert "coalesce(src.sreality_id::text, 'l' || src.listing_id::text)" in sql


def test_collection_monitor_gates_every_detector_on_monitor_since() -> None:
    """Each detector fires only for changes AFTER monitoring began for the
    (collection, property) pair. The shared `monitored` CTE computes the anchor
    `monitor_since = greatest(added_at, monitoring_enabled_at)`, and price /
    inactive / new_source gate their change-time on it (reactivated is gated
    transitively via the inactive dispatch). This is the false-positive the fix
    closes: a price drop that predates membership must not notify."""
    script: list[tuple[Any, list[tuple[Any, ...]], int]] = [
        (lambda s: "FROM collections WHERE monitoring_enabled = true" in s, [(1,)], 0),
        (lambda s: "FROM app_settings" in s, [], 0),
        (lambda s: "INSERT INTO notification_dispatches" in s, [], 0),
    ]
    conn = _FakeConn(script)
    match_monitored_collections_once(conn)  # type: ignore[arg-type]

    inserts = [
        sql for sql, _ in conn.executed
        if "INSERT INTO notification_dispatches" in sql
    ]
    assert len(inserts) == 4
    anchor = (
        "greatest(cp.added_at, coalesce(c.monitoring_enabled_at, cp.added_at)) "
        "AS monitor_since"
    )
    for sql in inserts:
        assert anchor in sql  # every detector shares the anchored CTE

    price_sql = next(s for s in inserts if "'price_drop'" in s)
    assert "st.scraped_at > m.monitor_since" in price_sql

    # The inactive insert references 'inactive' but not 'reactivated'.
    inactive_sql = next(
        s for s in inserts if "'inactive'" in s and "'reactivated'" not in s
    )
    assert "max(l.inactive_at) > m.monitor_since" in inactive_sql

    new_source_sql = next(s for s in inserts if "'new_source'" in s)
    assert "src.first_seen_at > src.monitor_since" in new_source_sql
    # new_source must NOT use count(DISTINCT ...) OVER (...): Postgres rejects it
    # (0A000), which threw on every producer run pre-fix. The distinct-source
    # count is a filtered window count over the rn=1 rows instead.
    assert "count(DISTINCT" not in new_source_sql
    assert "count(*) FILTER (WHERE rn = 1)" in new_source_sql

    # reactivated is hardened: the prior 'inactive' dispatch it keys on must
    # itself postdate monitor_since (no leak off a pre-fix ungated dispatch).
    reactivated_sql = next(s for s in inserts if "'reactivated'" in s)
    assert "nd.dispatched_at > m.monitor_since" in reactivated_sql


def test_get_unread_count_breaks_down_by_source() -> None:
    script: list[tuple[Any, list[tuple[Any, ...]], int]] = [
        (
            lambda s: "GROUP BY d.source_kind" in s,
            [("watchdog", 4), ("collection_monitor", 3)],
            0,
        ),
    ]
    conn = _FakeConn(script)
    out = get_unread_count(conn)  # type: ignore[arg-type]
    assert out == {
        "watchdog": 4, "collection_monitor": 3, "share": 0, "system_health": 0,
        "total": 7, "unread_count": 7,
    }
    scoped = get_unread_count(conn, source_kind="collection_monitor")  # type: ignore[arg-type]
    assert scoped["unread_count"] == 3


def test_get_unread_count_sums_system_health_into_total() -> None:
    # total sums EVERY kind, not a hardcoded watchdog+collection_monitor — the bug
    # that previously dropped system_health alerts from the nav badge.
    script: list[tuple[Any, list[tuple[Any, ...]], int]] = [
        (
            lambda s: "GROUP BY d.source_kind" in s,
            [("watchdog", 4), ("collection_monitor", 3), ("system_health", 2)],
            0,
        ),
    ]
    conn = _FakeConn(script)
    out = get_unread_count(conn)  # type: ignore[arg-type]
    assert out["system_health"] == 2
    assert out["total"] == 9
    assert out["unread_count"] == 9
    scoped = get_unread_count(conn, source_kind="system_health")  # type: ignore[arg-type]
    assert scoped["unread_count"] == 2


def test_get_unread_count_names_the_share_kind() -> None:
    # Every SourceKind gets its own count from the ONE alias; both share kinds are one 'share'
    # producer (migrations 610 + 641).
    script: list[tuple[Any, list[tuple[Any, ...]], int]] = [
        (lambda s: "GROUP BY d.source_kind" in s, [("share", 2)], 0),
    ]
    out = get_unread_count(_FakeConn(script))  # type: ignore[arg-type]
    assert out["share"] == 2 and out["total"] == 2
    assert "collection_share" not in out


def test_dispatch_select_projects_message() -> None:
    # The feed row carries d.message so system_health alerts render their verbatim text.
    from api.notifications import _DISPATCH_SELECT

    assert "d.message" in _DISPATCH_SELECT


def test_dispatch_select_names_the_check_a_system_alert_is_about() -> None:
    """The bell's "Open Health" opens Health at the alert's check, as the outbox's links do. Every system dedupe
    key opens `sys:<key>:` (emit_system_alert refuses any other), so the key is its second segment; every other
    producer's key (`wd:…`, `cm:…`) names no check and reads NULL. Checked live on 2026-10-19: all 882 system rows
    agree with the outbox's `^sys:([a-z0-9_]+):` and no other row carries a key."""
    from api.notifications import _DISPATCH_SELECT

    assert (
        "CASE WHEN d.source_kind = 'system_health' "
        "THEN split_part(d.dedupe_key, ':', 2) END AS check_key"
    ) in _DISPATCH_SELECT


def test_dispatch_select_names_what_a_share_opens() -> None:
    """A share's bell row opens its collection, the owner's pipeline or the dashboard (migration 647):
    api/shares.py keys every share `share:<kind>:<ref>:<recipient>…`, so the kind is the second segment and
    the collection id / owner / dashboard id the third; every other producer reads NULL for both."""
    from api.notifications import _DISPATCH_SELECT

    assert ("CASE WHEN d.source_kind = 'share' THEN split_part(d.dedupe_key, ':', 2) END AS share_kind, "
            "CASE WHEN d.source_kind = 'share' THEN split_part(d.dedupe_key, ':', 3) END AS share_ref"
            ) in _DISPATCH_SELECT
    for key, kind, ref in (("share:collection:7:r", "collection", "7"),
                           ("share:pipeline:o:r:edit", "pipeline", "o"),
                           ("share:dashboard:d:r", "dashboard", "d")):
        assert key.split(":")[1:3] == [kind, ref]


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
    import inspect

    from api import notifications as nf

    where, _ = _build_match_clauses(WatchdogFilterSpec(min_mf_gross_yield_pct=4.0))
    assert "l.mf_gross_yield_pct >= %(min_mf_gross_yield_pct)s" in where
    insert = inspect.getsource(nf._insert_new_dispatches)
    assert "FROM properties_public l " in insert
    assert "SELECT %(subscription_id)s, 'watchdog', l.property_id," in insert

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


# --- dismissed properties are invisible, never undetected (migration 536) ---

_HIDDEN = (
    "NOT EXISTS (SELECT 1 FROM property_dismissals_public pd "
    "WHERE pd.property_id = d.property_id)"
)


def test_every_in_app_read_and_count_hides_dismissed_properties() -> None:
    """The badge, the feed, its total, mark-all-seen and each watchdog's dispatch
    count must agree on what is visible — a count that includes rows the list
    hides is the badge-vs-list divergence migrations 351/378 exist to close."""
    from api.notifications import get_subscription, list_dispatches, list_subscriptions

    conn = _FakeConn([])
    get_unread_count(conn)  # type: ignore[arg-type]
    mark_all_seen(conn)  # type: ignore[arg-type]
    mark_all_seen(conn, source_kind="watchdog")  # type: ignore[arg-type]
    list_dispatches(conn)  # type: ignore[arg-type]
    list_dispatches(conn, subscription_id="s-1", seen="unseen")  # type: ignore[arg-type]
    get_subscription(conn, "s-1")  # type: ignore[arg-type]
    list_subscriptions(conn)  # type: ignore[arg-type]
    reads = [s for s, _ in conn.executed if "notification_dispatches" in s]
    assert len(reads) == 9
    for sql in reads:
        assert _HIDDEN in sql, sql
    totals = [s for s in reads if s.startswith("SELECT count(*) FROM notification_dispatches d")]
    assert len(totals) == 2


def test_a_dispatch_is_still_reachable_by_id() -> None:
    """Detail-by-id stays reachable (a deep link from an already-sent message)."""
    from api.notifications import mark_dispatch_seen

    conn = _FakeConn([])
    mark_dispatch_seen(conn, "d-1")  # type: ignore[arg-type]
    assert all("property_dismissals" not in s for s, _ in conn.executed)


def test_detection_never_consults_dismissals() -> None:
    """Suppressing an INSERT would lose the event for good on undo: the matcher's
    cursor advances regardless, `:new:` dedupe keys are once-ever, and the
    `reactivated` detector keys off a prior `inactive` dispatch."""
    import inspect

    from api import notifications as nf

    for fn in (nf.match_once, nf.match_changes_once, nf.match_monitored_collections_once):
        assert "dismiss" not in inspect.getsource(fn), fn.__name__
    assert "dismiss" not in nf._MONITORED_CTE


def test_match_once_skips_subscription_with_no_listings() -> None:
    """An empty window (no listings past the cursor) is the steady
    state. The matcher should record zero dispatches and zero cursor
    advances rather than blow up trying to UPDATE with a NULL value.
    (Image gate scripted OFF — the gate's empty-window lookback re-scan
    has its own test below.)"""
    sub_id = UUID("00000000-0000-0000-0000-000000000001")
    cursor_ts = datetime(2030, 1, 1, tzinfo=timezone.utc)

    script: list[tuple[Any, list[tuple[Any, ...]], int]] = [
        # Every app_settings read returns false-y: interval/window fall back to
        # their floors and the image-gate flag reads False (gate off).
        (lambda s: "FROM app_settings" in s, [(False,)], 0),
        (
            lambda s: "FROM notification_subscriptions WHERE is_active" in s,
            [(sub_id, {}, cursor_ts, [])],
            1,
        ),
        # Window query returns (NULL, 0) — no fresh listings.
        (lambda s: "SELECT max(first_seen_at), count(*) FROM" in s, [(None, 0)], 0),
    ]
    conn = _FakeConn(script)

    stats = match_once(conn)  # type: ignore[arg-type]

    assert stats["subscriptions_evaluated"] == 1
    assert stats["matches_inserted"] == 0
    assert stats["cursors_advanced"] == 0
    # Critically: no INSERT or UPDATE was attempted.
    assert not any("INSERT INTO notification_dispatches" in sql for sql, _ in conn.executed)
    assert not any(
        "UPDATE notification_subscriptions" in sql for sql, _ in conn.executed
    )


def test_match_once_skips_invalid_filter_spec() -> None:
    """A subscription whose filter_spec doesn't validate (e.g. corrupt
    legacy row) must NOT crash the whole matcher pass — log + skip."""
    good_id = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    bad_id = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
    cursor_ts = datetime(2026, 5, 1, tzinfo=timezone.utc)
    upper_ts = datetime(2026, 5, 15, tzinfo=timezone.utc)

    script: list[tuple[Any, list[tuple[Any, ...]], int]] = [
        (lambda s: "FROM app_settings" in s, [], 0),
        (
            lambda s: "FROM notification_subscriptions WHERE is_active" in s,
            [
                # Invalid: a map box with west >= east.
                (bad_id, {"bounds": {"west": 14.5, "south": 50.0, "east": 14.0, "north": 50.2}}, cursor_ts, []),
                # Valid spec
                (good_id, {}, cursor_ts, []),
            ],
            2,
        ),
        (lambda s: "SELECT max(first_seen_at), count(*) FROM" in s, [(upper_ts, 1)], 0),
        (
            lambda s: "INSERT INTO notification_dispatches" in s
            and "image_lookback_minutes" in s,
            [],
            0,
        ),
        (lambda s: "INSERT INTO notification_dispatches" in s, [], 1),
        (lambda s: "UPDATE notification_subscriptions SET last_matched_first_seen_at" in s, [], 1),
    ]
    conn = _FakeConn(script)

    stats = match_once(conn)  # type: ignore[arg-type]

    # Both rows counted as "evaluated" — the broken one was inspected
    # and skipped, but the surviving one fired one dispatch.
    assert stats["subscriptions_evaluated"] == 2
    assert stats["matches_inserted"] == 1


def _raise_for_poison(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the compile raise for any spec carrying the poison price bound."""
    import api.notifications as nf

    real = nf._build_match_clauses

    def fake(spec: WatchdogFilterSpec) -> Any:
        if spec.min_price_czk == 666:
            raise ValueError("a stored spec the compile refuses")
        return real(spec)

    monkeypatch.setattr(nf, "_build_match_clauses", fake)


def test_match_once_counts_failed_subscriptions(monkeypatch: pytest.MonkeyPatch) -> None:
    """A spec that raises inside the compile mutes its watchdog every pass. It is counted
    (the loop logs the stats at INFO when the count is above 0) and the other
    subscription still matches."""
    _raise_for_poison(monkeypatch)
    cursor_ts = datetime(2026, 5, 1, tzinfo=timezone.utc)
    script: list[tuple[Any, list[tuple[Any, ...]], int]] = [
        (lambda s: "FROM app_settings" in s, [], 0),
        (
            lambda s: "FROM notification_subscriptions WHERE is_active" in s,
            [
                (UUID("cccccccc-cccc-cccc-cccc-cccccccccccc"), {"min_price_czk": 666},
                 cursor_ts, []),
                (UUID("dddddddd-dddd-dddd-dddd-dddddddddddd"), {}, cursor_ts, []),
            ],
            2,
        ),
        (lambda s: "SELECT max(first_seen_at), count(*) FROM" in s,
         [(datetime(2026, 5, 15, tzinfo=timezone.utc), 1)], 0),
        (
            lambda s: "INSERT INTO notification_dispatches" in s
            and "image_lookback_minutes" in s,
            [],
            0,
        ),
        (lambda s: "INSERT INTO notification_dispatches" in s, [], 1),
        (lambda s: "UPDATE notification_subscriptions SET last_matched_first_seen_at" in s,
         [], 1),
    ]
    stats = match_once(_FakeConn(script))  # type: ignore[arg-type]
    assert stats["subscriptions_evaluated"] == 2
    assert stats["subscriptions_failed"] == 1
    assert stats["matches_inserted"] == 1


def test_match_changes_once_counts_failed_subscriptions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.notifications import match_changes_once

    _raise_for_poison(monkeypatch)
    script: list[tuple[Any, list[tuple[Any, ...]], int]] = [
        (lambda s: "FROM app_settings" in s, [], 0),
        (lambda s: "FROM listing_price_steps" in s, [(101, 5001, 4_900_000, 5_000_000)], 0),
        (
            lambda s: "FROM notification_subscriptions WHERE is_active" in s,
            [
                (UUID("cccccccc-cccc-cccc-cccc-cccccccccccc"), {"min_price_czk": 666}, []),
                (UUID("dddddddd-dddd-dddd-dddd-dddddddddddd"), {}, []),
            ],
            2,
        ),
        (lambda s: "INSERT INTO notification_dispatches" in s, [], 1),
    ]
    stats = match_changes_once(_FakeConn(script))  # type: ignore[arg-type]
    assert stats["subscriptions_evaluated"] == 2
    assert stats["subscriptions_failed"] == 1
    assert stats["changes_inserted"] == 1


# --- images-first publication gate (Wave C-4) ----------------------------


from api.notifications import (
    IMAGE_GATE_ZERO_ROWS_FLOOR_MINUTES,
    ImageGateSettings,
    _read_bool_setting,
)


def _gate_script(
    sub_id: UUID, cursor_ts: datetime, upper: tuple[Any, Any],
) -> list[tuple[Any, list[tuple[Any, ...]], int]]:
    return [
        # app_settings all default → gate ON (default true), timeout 30.
        (lambda s: "FROM app_settings" in s, [], 0),
        (
            lambda s: "FROM notification_subscriptions WHERE is_active" in s,
            [(sub_id, {}, cursor_ts, [])],
            1,
        ),
        (lambda s: "SELECT max(first_seen_at), count(*) FROM" in s, [upper], 0),
        (
            lambda s: "INSERT INTO notification_dispatches" in s
            and "image_lookback_minutes" in s,
            [],
            1,
        ),
        (lambda s: "INSERT INTO notification_dispatches" in s, [], 0),
        (lambda s: "UPDATE notification_subscriptions SET last_matched_first_seen_at" in s, [], 1),
    ]


def test_match_once_image_gate_holds_new_properties_without_losing_them() -> None:
    """Default-on gate: the window INSERT requires a stored image (or a release
    arm), the CURSOR still advances past held properties (the window/upper query
    carries NO gate — one photoless CDN can't stall the feed), and the
    gate-pending lookback re-scan re-considers already-passed properties so a
    held property is dispatched once released — never lost."""
    sub_id = UUID("00000000-0000-0000-0000-00000000000a")
    cursor_ts = datetime(2026, 7, 1, tzinfo=timezone.utc)
    upper_ts = datetime(2026, 7, 2, tzinfo=timezone.utc)

    conn = _FakeConn(_gate_script(sub_id, cursor_ts, (upper_ts, 2)))
    stats = match_once(conn)  # type: ignore[arg-type]

    # Phase 1 (window upper / cursor advance) is gate-free by design.
    window_sql, _ = next(
        (sql, p) for sql, p in conn.executed
        if "max(first_seen_at), count(*)" in sql
    )
    assert "storage_path" not in window_sql

    inserts = [
        (sql, p) for sql, p in conn.executed
        if "INSERT INTO notification_dispatches" in sql
    ]
    assert len(inserts) == 2  # window insert + lookback re-scan

    window_insert_sql, window_params = inserts[0]
    assert "image_lookback_minutes" not in window_insert_sql
    # The three release arms, set-based inside the INSERT's WHERE:
    assert "gi.storage_path IS NOT NULL" in window_insert_sql            # (a)
    assert (
        "make_interval(mins => %(image_timeout_minutes)s)"
        in window_insert_sql
    )                                                                     # (b)
    assert "make_interval(mins => %(image_zero_rows_floor_minutes)s)" in window_insert_sql
    assert "AND NOT EXISTS" in window_insert_sql                          # (c)
    assert window_params["image_timeout_minutes"] == 30
    assert (
        window_params["image_zero_rows_floor_minutes"]
        == IMAGE_GATE_ZERO_ROWS_FLOOR_MINUTES
    )

    lookback_sql, lookback_params = inserts[1]
    # Re-scan targets properties the cursor already passed, bounded by the
    # lookback window, under the same gate; dedupe_key keeps it idempotent.
    assert "l.first_seen_at <= %(cursor)s" in lookback_sql
    assert "make_interval(mins => %(image_lookback_minutes)s)" in lookback_sql
    assert "gi.storage_path IS NOT NULL" in lookback_sql
    assert "ON CONFLICT (dedupe_key) DO NOTHING" in lookback_sql
    assert lookback_params["image_lookback_minutes"] == 60  # max(60, 2×30)

    # Cursor advanced past the (possibly held) window — the lookback re-scan is
    # what keeps held properties reachable, not a stalled cursor.
    assert any(
        "UPDATE notification_subscriptions SET last_matched_first_seen_at" in sql
        for sql, _ in conn.executed
    )
    assert stats["cursors_advanced"] == 1
    assert stats["matches_inserted"] == 1       # the released lookback property
    assert stats["gate_lookback_inserted"] == 1


def test_match_once_gate_lookback_runs_even_when_window_is_empty() -> None:
    """A held property must not be stranded waiting for unrelated new
    inventory: with NO fresh listings past the cursor (upper NULL) the
    gate-pending lookback INSERT still runs — that's how a gated property gets
    dispatched after its images land even on a quiet market."""
    sub_id = UUID("00000000-0000-0000-0000-00000000000b")
    cursor_ts = datetime(2026, 7, 1, tzinfo=timezone.utc)

    conn = _FakeConn(_gate_script(sub_id, cursor_ts, (None, 0)))
    stats = match_once(conn)  # type: ignore[arg-type]

    inserts = [
        sql for sql, _ in conn.executed
        if "INSERT INTO notification_dispatches" in sql
    ]
    assert len(inserts) == 1
    assert "image_lookback_minutes" in inserts[0]
    # No window → no cursor advance (unchanged), but the release still fired.
    assert not any(
        "UPDATE notification_subscriptions" in sql for sql, _ in conn.executed
    )
    assert stats["cursors_advanced"] == 0
    assert stats["matches_inserted"] == 1
    assert stats["gate_lookback_inserted"] == 1


def test_match_once_gate_flag_off_restores_old_behavior() -> None:
    """`notifications_new_requires_image` = false → exactly one ungated window
    INSERT, no lookback re-scan, no gate SQL anywhere."""
    sub_id = UUID("00000000-0000-0000-0000-00000000000c")
    cursor_ts = datetime(2026, 7, 1, tzinfo=timezone.utc)
    upper_ts = datetime(2026, 7, 2, tzinfo=timezone.utc)

    script: list[tuple[Any, list[tuple[Any, ...]], int]] = [
        # Every app_settings read returns false-y → the gate flag reads False.
        (lambda s: "FROM app_settings" in s, [(False,)], 0),
        (
            lambda s: "FROM notification_subscriptions WHERE is_active" in s,
            [(sub_id, {}, cursor_ts, [])],
            1,
        ),
        (lambda s: "SELECT max(first_seen_at), count(*) FROM" in s, [(upper_ts, 2)], 0),
        (lambda s: "INSERT INTO notification_dispatches" in s, [], 2),
        (lambda s: "UPDATE notification_subscriptions SET last_matched_first_seen_at" in s, [], 1),
    ]
    conn = _FakeConn(script)
    stats = match_once(conn)  # type: ignore[arg-type]

    inserts = [
        sql for sql, _ in conn.executed
        if "INSERT INTO notification_dispatches" in sql
    ]
    assert len(inserts) == 1
    assert "storage_path" not in inserts[0]
    assert "image_lookback_minutes" not in inserts[0]
    assert stats["matches_inserted"] == 2
    assert stats["gate_lookback_inserted"] == 0
    assert stats["cursors_advanced"] == 1


def test_image_gate_lookback_always_covers_the_timeout() -> None:
    """The re-scan window must exceed the longest possible hold, or a property
    gated the whole way would leave the lookback before its release."""
    assert ImageGateSettings(True, 30).lookback_minutes == 60
    assert ImageGateSettings(True, 45).lookback_minutes == 90
    assert ImageGateSettings(True, 120).lookback_minutes == 240
    assert ImageGateSettings(True, 5).lookback_minutes == 60  # floor


def test_read_bool_setting_parses_jsonb_shapes() -> None:
    def read(rows: list[tuple[Any, ...]], default: bool = True) -> bool:
        conn = _FakeConn([(lambda s: "FROM app_settings" in s, rows, 0)])
        return _read_bool_setting(conn, "k", default=default)  # type: ignore[arg-type]

    assert read([], default=True) is True         # absent → default
    assert read([], default=False) is False
    assert read([(True,)]) is True
    assert read([(False,)]) is False
    assert read([("false",)]) is False
    assert read([("TRUE",)]) is True
    assert read([(0,)]) is False
    assert read([(1,)]) is True


# --- match_changes_once (the property-change matcher, Slice 2b) ----------


from api.notifications import match_changes_once


def test_match_changes_once_emits_price_drop_for_matching_subs() -> None:
    """The change matcher resolves recent per-snapshot price drops once, then
    fires a `price_drop` dispatch per (matching subscription x drop snapshot),
    deduped by the per-snapshot dedupe_key so each genuine cut is its own
    event. Provenance (snapshot id + new/prev price) rides the unnest join."""
    sub_id = UUID("12121212-1212-1212-1212-121212121212")
    script: list[tuple[Any, list[tuple[Any, ...]], int]] = [
        # window-days app_settings read → default
        (lambda s: "FROM app_settings" in s, [], 0),
        # recent price-drop steps: (property_id, snapshot_id, price, prev)
        (
            lambda s: "FROM listing_price_steps" in s,
            [(101, 5001, 4_900_000, 5_000_000), (102, 5002, 2_400_000, 2_500_000)],
            0,
        ),
        # active subscriptions
        (
            lambda s: "FROM notification_subscriptions WHERE is_active" in s,
            [(sub_id, {"category_main": "byt", "category_type": "pronajem"}, ["email"])],
            1,
        ),
        # INSERT price_drop dispatches — 2 inserted
        (lambda s: "INSERT INTO notification_dispatches" in s, [], 2),
    ]
    conn = _FakeConn(script)

    stats = match_changes_once(conn)  # type: ignore[arg-type]

    assert stats["subscriptions_evaluated"] == 1
    assert stats["price_drops_in_window"] == 2
    assert stats["changes_inserted"] == 2

    insert_sql, insert_params = next(
        (sql, p) for sql, p in conn.executed
        if "INSERT INTO notification_dispatches" in sql
    )
    assert "'price_drop'" in insert_sql
    # Per-snapshot dedup + the unnest provenance join replace the old
    # ANY(drop_ids) + composite ON CONFLICT.
    assert "ON CONFLICT (dedupe_key)" in insert_sql
    assert ":price_drop:' || d.snapshot_id::text" in insert_sql
    assert "JOIN unnest(" in insert_sql
    assert insert_params["drop_pids"] == [101, 102]
    assert insert_params["drop_sids"] == [5001, 5002]
    assert insert_params["drop_prices"] == [4_900_000, 2_400_000]
    assert insert_params["drop_prevs"] == [5_000_000, 2_500_000]
    # Producer-stamped delivery routing (migration 208) — subscription channels
    # minus the always-implicit 'in_app'.
    assert "%(target_channels)s::text[]" in insert_sql
    assert insert_params["target_channels"] == ["email"]


def test_match_changes_once_noops_when_no_recent_drops() -> None:
    """No recent price drops → no subscription scan, no inserts."""
    script: list[tuple[Any, list[tuple[Any, ...]], int]] = [
        (lambda s: "FROM app_settings" in s, [], 0),
        (lambda s: "FROM listing_price_steps" in s, [], 0),
    ]
    conn = _FakeConn(script)

    stats = match_changes_once(conn)  # type: ignore[arg-type]

    assert stats == {
        "subscriptions_evaluated": 0,
        "subscriptions_failed": 0,
        "price_drops_in_window": 0,
        "changes_inserted": 0,
    }
    assert not any(
        "INSERT INTO notification_dispatches" in sql for sql, _ in conn.executed
    )

