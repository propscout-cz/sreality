"""Browse's list for an agent (#2063): the registry's BROWSE filters over Browse's own source, under the reader's own
login, compiled by the one filter compiler (rule 16), so Browse, the Watchdog and an agent's search match one way."""

from __future__ import annotations

from typing import Any, Literal

import psycopg
from pydantic import Field

from api.filter_args import Args, MapAreaArgs, agenda_ids, filter_model, filter_values
from api.location_filter import area_where, district_where
from api.property_read import jsonable
from toolkit import _max_last_seen, _now_iso
from toolkit.filter_compiler import BROWSE_GRAIN, compile_filter_where
from toolkit.filter_registry import Agenda
from toolkit.measures import cohort_basis, measure_backed

# A Browse card's facts (frontend/src/lib/queries.ts CARD_COLS), each published by browse_projection. Never a parked
# condition level (rule 14), a city-quality column (rule 17), a broker or an internal (sreality_id, newest_ad_at_*).
SEARCH_COLUMNS = (
    "property_id", "listing_id", "source", "display_label", "category_main", "category_type", "subtype",
    "disposition", "area_m2", "plot_area_m2", "price_czk", "price_per_m2", "price_per_m2_basis", "mf_gross_yield_pct",
    "total_price_change_pct", "price_change_count", "first_seen_at", "last_seen_at", "last_change_at", "is_active",
    "tom_days", "tom_observed", "active_sources", "all_sources", "lat", "lng",
)
# Browse's sort menu (frontend/src/lib/browseSort.ts CARD_SORTS); a leading '-' is descending.
SORTS = (
    "-first_seen_at", "first_seen_at", "-last_seen_at", "price_czk", "-price_czk", "price_per_m2", "-price_per_m2",
    "total_price_change_pct", "-total_price_change_pct", "area_m2", "-area_m2", "plot_area_m2", "-plot_area_m2",
    "street_name", "-street_name", "-mf_gross_yield_pct", "mf_gross_yield_pct",
)
# Never NULL, so ordered with the btree's own NULL placement, which lets their (column, property_id) index serve the
# order (frontend/src/lib/keyset.ts NON_NULL_SORT_FIELDS); every other field puts its NULLs last either way.
_NOT_NULL_SORTS = frozenset({"first_seen_at", "last_seen_at"})
# The widest page still fits truncate.RESULT_BUDGET whole (tests/api/test_property_search.py).
PAGE_MAX = 10
REACH = 500
_SETS = ("tags", "collections", "pipeline")
# Browse's one source (migration 629): its dismissals and curated-set scope are judged under the reader's RLS.
_SOURCE = "browse_list_visible(hide_dismissed => %(hide_dismissed)s, " + ", ".join(
    f"{s}_set => %({s}_set)s::text, {s}_in => %({s}_in)s::bigint[], {s}_out => %({s}_out)s::bigint[]" for s in _SETS
) + ")"

SearchFilters = filter_model(
    "SearchFilters", tuple(i for i in agenda_ids(Agenda.BROWSE) if i != "location"), None,
    required={"category_type"},
    # Where the registry's text speaks to Browse's UI or to another surface.
    descriptions={
        "category_type": "Deal type, always stated: pronajem = for rent, prodej = for sale, drazba = auction, podil "
        "= fractional ownership, null = every deal type. It decides the unit of every per-m² figure (a rent's is "
        "per month).",
        "status": "The property's own status: any = live and delisted properties; active = a live property (with a "
        "portal filter: with an active ad there); inactive = a delisted property, every ad ended.",
        "show_dismissed": "When false, the properties the user dismissed are left out, as Browse leaves them out; "
        "true includes them.",
    },
)


class SearchArgs(SearchFilters, MapAreaArgs):
    sort: Literal[SORTS] = Field(
        "-first_seen_at", description="The order Browse's sort menu offers: a field, descending with a leading '-'; "
        "a missing value sorts last. Ties break on property_id. With exactly one portal, newest / oldest first "
        "orders by when the property's newest ad on that portal was first seen, as Browse does.")
    limit: int = Field(PAGE_MAX, ge=1, le=PAGE_MAX, description="How many properties one page holds.")
    offset: int = Field(
        0, ge=0, le=REACH - PAGE_MAX, description=f"How many matches to skip. Only the first {REACH} are reachable; "
        "narrow the filters to reach others.")


def _sole_portal(args: Args) -> str | None:
    """queries.ts solePortal: the one included portal, when exactly one is."""
    portals = set(args.portals or ())
    return next(iter(portals)) if len(portals) == 1 else None


def cohort_where(args: Args) -> tuple[list[str], dict[str, Any]]:
    """Browse's cohort over a `browse_list`-shaped `l`, without the reader's lenses: its conjuncts and their
    parameters (the broker board counts the same cohort, api/broker_board.py)."""
    where, params = compile_filter_where(filter_values(args), BROWSE_GRAIN)
    for fragments, more in (district_where(args.districts, "l"), area_where(args.bounds, args.circle, "l")):
        where += fragments
        params.update(more)
    if portal := _sole_portal(args):
        # queries.ts applyPortalRule: redundant by the stored invariant (an ad on P <=> a date for P), it keeps the
        # one-portal order's column non-NULL and lets P's partial index (migration 590) serve page and count alike.
        # P is a PORTAL_OPTIONS literal.
        where.append(f"l.newest_ad_at_{portal} IS NOT NULL")
    return where, params


def cohort(args: SearchArgs) -> tuple[str, dict[str, Any]]:
    """The FROM and WHERE a page and its count share, with their parameters."""
    where, params = cohort_where(args)
    params["hide_dismissed"] = not args.show_dismissed
    for name in _SETS:
        # setScope.scopeArgs over a scope already in normalizeScope's form (SetScope): an empty side is no side.
        scope = getattr(args, name)
        params.update({f"{name}_set": scope.set if scope else None, f"{name}_in": (scope.in_ if scope else None) or None,
                       f"{name}_out": (scope.out if scope else None) or None})
    return f"FROM {_SOURCE} l WHERE {' AND '.join(where) or 'true'}", params


def _order(args: SearchArgs) -> str:
    field, way = args.sort.lstrip("-"), "DESC" if args.sort.startswith("-") else "ASC"
    nulls = "" if field in _NOT_NULL_SORTS else " NULLS LAST"
    # queries.ts effectiveSort (MS19, Q49 b): with one portal P, newest / oldest first is when the property's
    # newest ad on P was first seen, so a property advertised there again rises; never NULL (cohort's conjunct).
    if field == "first_seen_at" and (portal := _sole_portal(args)):
        field = f"newest_ad_at_{portal}"
    return f"l.{field} {way}{nulls}, l.property_id {way}"


def _card(row: tuple[Any, ...]) -> dict[str, Any]:
    return jsonable(dict(zip(SEARCH_COLUMNS, row, strict=True)))


def search_properties(conn: psycopg.Connection, args: SearchArgs) -> dict[str, Any]:
    """One page of the matching properties in `args.sort` order, and how many match in all."""
    source, params = cohort(args)
    columns = ", ".join(f"l.{c}" for c in SEARCH_COLUMNS)
    with conn.cursor() as cur:
        cur.execute(f"SELECT {columns} {source} ORDER BY {_order(args)} LIMIT %(limit)s OFFSET %(offset)s",
                    {**params, "limit": args.limit, "offset": args.offset})
        rows = [_card(r) for r in cur.fetchall()]
        cur.execute(f"SELECT count(*) {source}", params)
        total = int(cur.fetchone()[0])
    return {
        "data": rows,
        "metadata": {
            "tool": "search_properties",
            "filters_used": args.model_dump(mode="json", by_alias=True, exclude_defaults=True),
            "result_count": total,
            "queried_at": _now_iso(),
            "data_freshness": _max_last_seen(rows),
            # The page's one per-m² unit (rule 23); 'mixed' or 'unknown' means it has none.
            "price_per_m2_basis": cohort_basis(measure_backed(rows)),
        },
    }
