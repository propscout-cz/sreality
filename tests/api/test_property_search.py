"""search_properties (#2063): Browse's list for an agent, over Browse's own source and the one filter compiler.

The rows it selects are pinned against the Watchdog's in tests/test_capabilities_live.py; here, the arguments it takes,
the statements it runs and the page it returns.
"""

from __future__ import annotations

import contextlib
import re
from typing import Any

import pytest

from api.capabilities import registry
from api.capabilities.entry import Caller
from api.capabilities.errors import ToolError, ToolErrorCode
from api.capabilities.truncate import RESULT_BUDGET, encode
from api.filter_args import agenda_ids
from api.property_search import PAGE_MAX, SEARCH_COLUMNS, SORTS, SearchArgs, SearchFilters
from tests.api._script_conn import ScriptConn
from tests.test_browse_read_path_guardrail import BROWSE_SORT_TS, _projection_columns, _sortable_fields
from toolkit import filter_registry as fr
from toolkit.measures import BASIS_MIXED, RENT_MONTHLY_CZK_M2, SALE_CAPITAL_CZK_M2

BROWSE = agenda_ids(fr.Agenda.BROWSE)
_OVERRIDDEN = {"category_type", "status", "show_dismissed"}


def _validate(args: dict[str, Any]) -> SearchArgs:
    return registry.validate("search_properties", args)[1]


def _refused(args: dict[str, Any]) -> ToolError:
    with pytest.raises(ToolError) as info:
        _validate(args)
    assert info.value.code is ToolErrorCode.INVALID_ARGUMENTS
    return info.value


# --- the arguments ----------------------------------------------------------------------------------------------

def test_every_browse_filter_is_an_argument_but_the_place_composite_which_travels_as_bounds_and_districts() -> None:
    assert tuple(SearchFilters.model_fields) == tuple(i for i in BROWSE if i != "location")
    assert set(SearchArgs.model_fields) - set(SearchFilters.model_fields) == {"bounds", "sort", "limit", "offset"}


def test_each_description_is_the_registrys_with_its_unit_and_basis_or_a_stated_override() -> None:
    for fid, field in SearchFilters.model_fields.items():
        f = fr.REGISTRY[fid]
        if fid in _OVERRIDDEN:
            assert field.description != f.description, fid
            continue
        assert field.description.startswith(f.description), fid
        if f.basis:
            assert field.description.endswith(f"basis: {f.basis}."), fid
    assert "Watchdog" not in SearchFilters.model_fields["status"].description


def test_the_deal_type_must_be_stated_and_null_means_every_one() -> None:
    err = _refused({})
    assert [d["loc"] for d in err.details] == [["category_type"]]
    assert _validate({"category_type": None}).category_type is None


@pytest.mark.parametrize("args, loc", [
    ({"price_change_window_days": 45}, ["price_change_window_days"]),
    ({"recently_added_days": 2}, ["recently_added_days"]),
    ({"status": "live"}, ["status"]),
    ({"districts": [{"name": "Brno", "level": "obec", "id": 582786, "kind": "town"}], }, ["districts", 0, "kind"]),
    ({"districts": [{"name": "Brno", "level": "obec", "id": 0}]}, ["districts", 0, "id"]),
    ({"city_index_rules": [{"index_name": "bezpecnost", "op": "=>", "value": 6}]}, ["city_index_rules", 0, "op"]),
    ({"tags": {"set": "in", "every": [1]}}, ["tags", "every"]),
    ({"bounds": {"west": 14, "south": 50, "east": 15, "north": 51, "zoom": 9}}, ["bounds", "zoom"]),
    ({"sort": "-street_name_cz"}, ["sort"]),
    ({"limit": PAGE_MAX + 1}, ["limit"]),
])
def test_an_off_vocabulary_or_unknown_value_is_refused_by_name_never_reached(args: dict[str, Any],
                                                                               loc: list[Any]) -> None:
    err = _refused({"category_type": "prodej", **args})
    assert loc in [d["loc"] for d in err.details]


def test_the_sorts_are_browses_menu() -> None:
    menu = re.findall(r"field: '(\w+)',\s*direction: '(asc|desc)'", BROWSE_SORT_TS.read_text())
    assert set(SORTS) == {f"{'-' if way == 'desc' else ''}{field}" for field, way in menu}
    assert {s.lstrip("-") for s in SORTS} == _sortable_fields()


def test_the_columns_are_browses_published_ones() -> None:
    assert set(SEARCH_COLUMNS) <= _projection_columns()
    assert {"price_per_m2", "price_per_m2_basis", "category_type"} <= set(SEARCH_COLUMNS)


# --- the statements and the page -------------------------------------------------------------------------------

_ROW = (7, 70, "idnes", "Brno", "byt", "prodej", None, "2+kk", 60, None, 6_000_000, 100_000, SALE_CAPITAL_CZK_M2,
        4.1, -5.0, 2, None, None, None, True, 3, True, ["idnes"], ["idnes"], 49.2, 16.6)


def _run(args: dict[str, Any], rows: list[tuple[Any, ...]] | None = None,
         total: int = 1) -> tuple[dict[str, Any], list[tuple[str, Any]]]:
    conn = ScriptConn([(lambda sql: sql.startswith("SELECT count(*)"), [(total,)]),
                       (lambda sql: sql.startswith("SELECT l.property_id"), rows if rows is not None else [_ROW])])

    @contextlib.contextmanager
    def own(claims: dict[str, Any], *, act_as: Any, method: str) -> Any:
        assert (act_as, method) == (None, "GET")
        yield conn
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(registry.tenant_pool, "tenant_transaction", own)
        out = registry.call(Caller(market=ScriptConn([]), claims={"sub": "a"}), "search_properties", args)
    return out, conn.executed


def test_a_page_and_its_count_read_browses_source_with_the_same_where() -> None:
    _, executed = _run({"category_type": "prodej", "dispositions": ["2+kk"], "show_dismissed": True,
                        "tags": {"set": None, "in": [4, 5], "out": []}, "pipeline": {"set": "out"},
                        "limit": 3, "offset": 6})
    (page, page_params), (count, count_params) = executed
    source = page.split(" FROM ", 1)[1].split(" ORDER BY ")[0]
    assert count == f"SELECT count(*) FROM {source}"
    assert source.startswith("browse_list_visible(hide_dismissed => %(hide_dismissed)s, tags_set => ")
    assert "l.category_type = %(category_type)s" in source and "l.disposition = %(dispositions)s" in source
    assert page.startswith("SELECT " + ", ".join(f"l.{c}" for c in SEARCH_COLUMNS) + " FROM ")
    assert page.endswith("ORDER BY l.first_seen_at DESC, l.property_id DESC LIMIT %(limit)s OFFSET %(offset)s")
    assert {k: v for k, v in page_params.items() if k not in count_params} == {"limit": 3, "offset": 6}
    # setScope.scopeArgs: only a side that is set reaches the source.
    assert {k: v for k, v in count_params.items() if k.startswith(("hide_", "tags_", "collections_", "pipeline_"))} == {
        "hide_dismissed": False, "tags_set": None, "tags_in": [4, 5], "tags_out": None,
        "collections_set": None, "collections_in": None, "collections_out": None,
        "pipeline_set": "out", "pipeline_in": None, "pipeline_out": None}


@pytest.mark.parametrize("sort, order", [
    ("-first_seen_at", "l.first_seen_at DESC, l.property_id DESC"),
    ("first_seen_at", "l.first_seen_at ASC, l.property_id ASC"),
    ("-last_seen_at", "l.last_seen_at DESC, l.property_id DESC"),
    ("price_per_m2", "l.price_per_m2 ASC NULLS LAST, l.property_id ASC"),
    ("-mf_gross_yield_pct", "l.mf_gross_yield_pct DESC NULLS LAST, l.property_id DESC"),
])
def test_the_order_is_keysets_a_missing_value_last_and_property_id_breaks_ties(sort: str, order: str) -> None:
    _, ((page, _), _) = _run({"category_type": None, "sort": sort})
    assert f"ORDER BY {order} LIMIT" in page


def test_the_place_and_the_box_are_the_watchdogs_renderers() -> None:
    _, ((page, params), _) = _run({
        "category_type": "prodej", "bounds": {"west": 16.5, "south": 49.1, "east": 16.7, "north": 49.3},
        "districts": [{"name": "Brno", "level": "obec", "id": 582786},
                      {"name": "Žabovřesky", "level": "cast_obce", "id": 411, "excluded": True}]})
    assert "(l.obec_id = ANY(%(district_codes_obec)s))" in page
    assert "NOT COALESCE(l.cast_obce_id = ANY(%(district_codes_excl_cast_obce)s), false)" in page
    assert "l.lng >= %(bbox_west)s" in page and params["bbox_north"] == 49.3


def test_the_envelope_is_the_toolkits_with_the_pages_one_unit() -> None:
    sale, rent = _ROW, (*_ROW[:5], "pronajem", *_ROW[6:12], RENT_MONTHLY_CZK_M2, *_ROW[13:])
    out, _ = _run({"category_type": None, "status": "active", "limit": 2}, rows=[sale, rent], total=41)
    meta = out["metadata"]
    assert meta["tool"] == "search_properties" and meta["result_count"] == 41
    assert meta["filters_used"] == {"category_type": None, "status": "active", "limit": 2}
    assert meta["price_per_m2_basis"] == BASIS_MIXED
    assert [r["property_id"] for r in out["data"]] == [7, 7] and set(out["data"][0]) == set(SEARCH_COLUMNS)
    one, _ = _run({"category_type": "prodej"})
    assert one["metadata"]["price_per_m2_basis"] == SALE_CAPITAL_CZK_M2


def test_the_widest_page_fits_the_budget_whole() -> None:
    """A page cut by the budget would make an offset skip rows; the widest card is measured against production
    (2026-10-29: the longest place label 58 chars, at most 7 portals on one property) with headroom."""
    portals = [o.value for o in fr.PORTAL_OPTIONS]
    num, ts = -1234567.8901234567, "2026-10-29T12:34:56.123456+00:00"
    widest = {c: num for c in SEARCH_COLUMNS} | {
        "property_id": 10**12, "listing_id": 10**12, "source": "ceskereality", "display_label": "x" * 96,
        "category_main": "komercni", "category_type": "pronajem", "subtype": "x" * 24, "disposition": "x" * 8,
        "price_czk": 2**31 - 1, "price_per_m2_basis": "x" * 24, "price_change_count": 2**31 - 1,
        "first_seen_at": ts, "last_seen_at": ts, "last_change_at": ts, "is_active": False, "tom_days": 2**31 - 1,
        "tom_observed": False, "active_sources": portals, "all_sources": portals}
    chips = [{"name": "x" * 40, "context": "x" * 40, "excluded": False, "level": "cast_obce", "id": 10**7}] * 10
    args = _validate({"category_type": "prodej", "districts": chips, "portals": portals, "dispositions": ["2+kk"],
                      "sort": "-price_per_m2", "limit": PAGE_MAX, "offset": 490})
    page = {"data": [widest] * PAGE_MAX, "metadata": {
        "tool": "search_properties", "filters_used": args.model_dump(mode="json", by_alias=True, exclude_defaults=True),
        "result_count": 10**6, "queried_at": ts, "data_freshness": ts, "price_per_m2_basis": BASIS_MIXED}}
    assert len(encode(page)) <= RESULT_BUDGET
