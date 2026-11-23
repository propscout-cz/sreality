"""The one place a registry FilterDef becomes a WHERE predicate (rule 16), per relation.

A column-backed filter compiles from its derived `filter_registry.sql_kind` (the same value
codegen hands Browse's TS auto-dispatch); an irregular one from ONE hook: the grain's own
(`FilterGrain.hooks`, a filter that relation spells in its own shape: the portal rule on a
property relation, MS19) or else the shared one in `_HOOKS`. A `FilterGrain` names the agenda
that grants filters on a relation, the spellings that relation needs where it lacks a column
(rule 23), its own hooks, and the keys its adapter renders itself. Every relation is aliased
`l`. The three adapters keep only what is not a filter: `toolkit/comparables._shared_filter_where`
(`listings`: the target-relative clauses and the cohort invariants),
`api/notifications._build_match_clauses` (`properties_public`: the served and published
predicates, the map box and the place chips) and `api/property_search` (Browse's own source,
`browse_list_visible`: the map box, the place chips and the caller's own lenses; its `cohort_where`, without the
lenses, is the broker board's cohort over `browse_list`). `SOLD_GRAIN` is the registered sales' relation
(`api/market_evidence`).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from psycopg.types.json import Jsonb

from toolkit.filter_registry import (
    CATEGORY_CITY_QUALITY,
    COLUMN_CANONICAL_VALUES,
    PRICE_CHANGE_COUNT_COLUMNS,
    REGISTRY,
    UNKNOWN_FILTER_VALUE,
    Agenda,
    all_filters,
    building_material_values,
    sql_kind,
)
from toolkit.measures import per_m2_sql, plot_area_sql, tom_days_sql, tom_observed_sql

Hook = Callable[[str, Any, Mapping[str, Any], dict[str, Any]], list[str]]
# (filter id, its value — may be None, the whole values mapping, params to write) -> fragments


@dataclass(frozen=True)
class FilterGrain:
    """One relation the compiler renders against, always aliased `l`."""
    agenda: Agenda                  # the single grant: a set filter it does not declare raises
    exprs: Mapping[str, str]        # pg_column -> expression where the relation lacks the column (rule 23)
    caller_renders: frozenset[str]  # keys the adapter renders itself; the compiler skips them
    hooks: Mapping[str, Hook] = MappingProxyType({})  # this relation's own hooks, before `_HOOKS`

# Kept on the models so an old saved blob still deserialises; any value raises, because a
# retired filter silently ignored WIDENS what a watchdog or a cohort matches.
RETIRED_FILTERS: Mapping[str, str] = MappingProxyType({
    "near_city_proximity": (
        "near_city_proximity is retired (W5, migration 436): no UI widget ever set "
        "it, 0 of 7 filter_presets and 0 of 2 notification_subscriptions carry a "
        "value, and it was never load-tested (migration 375's own header flags it as "
        "~33 s EXTRAPOLATED, CPU-bound ST_DWithin on scalars no index can serve). "
        "Use the migration-142 near_*_min columns."
    ),
    **{
        fid: (
            f"{fid} is retired (operator ruling 2026-10-20, rule 14): condition scoring is "
            "parked, its 1-5 levels were last written 2026-06-18 for ~4% of properties, and no "
            "surface filters on them. Use condition_match."
        )
        for fid in (
            "building_condition_level_min", "building_condition_level_max",
            "apartment_condition_level_min", "apartment_condition_level_max",
        )
    },
})

# One template per sql_kind; `enum_or_unknown` is `_enum_or_unknown_clause`. Pinned to
# tests/fixtures/filter_sql_kinds.json, the table Browse's TS dispatch is tested against. A
# one-element list is an `eq`: a ScalarArrayOp in a btree's equality prefix costs the index
# its order (the planner rule in frontend/src/lib/registryQueryBuilder.ts).
_KIND_SQL: Mapping[str, str | None] = MappingProxyType({
    "eq": "{expr} = %({id})s",
    "any": "{expr} = ANY(%({id})s)",
    "gte": "{expr} >= %({id})s",
    "lte": "{expr} <= %({id})s",
    "enum_or_unknown": None,
})

# Days-ago bounds read INVERTED against their affix: `*_max_days` is the oldest allowed, so
# the timestamp must be at least that recent; `*_min_days` is the most recent allowed.
_SEEN_DAYS: Mapping[str, tuple[str, str]] = MappingProxyType({
    "last_seen_max_days": ("last_seen_at", ">="),
    "last_seen_min_days": ("last_seen_at", "<="),
    "first_seen_max_days": ("first_seen_at", ">="),
    "first_seen_min_days": ("first_seen_at", "<="),
    # Browse's two recency presets: within the last N days (queries.ts applyFilters).
    "recently_added_days": ("first_seen_at", ">="),
    "recently_changed_days": ("last_change_at", ">="),
})


def _is_set(value: Any) -> bool:
    """Not None and not an empty list, tuple or dict; False, 0 and "" are set."""
    return value is not None and not (isinstance(value, (list, tuple, dict)) and not value)


def _enum_or_unknown_clause(
    values: list[str],
    col: str,
    pname: str,
    canonical: tuple[str, ...],
    params: dict[str, Any],
) -> str | None:
    """WHERE fragment for a multi-select enum that may carry the `__unknown__`
    sentinel. Real values match by `= ANY(...)` (one by `=`); `__unknown__` matches NULL or
    any value outside the canonical set. Returns None when nothing to filter."""
    reals = [v for v in values if v != UNKNOWN_FILTER_VALUE]
    parts: list[str] = []
    if reals:
        parts.append(f"{col} = %({pname})s" if len(reals) == 1 else f"{col} = ANY(%({pname})s)")
        params[pname] = reals[0] if len(reals) == 1 else reals
    if UNKNOWN_FILTER_VALUE in values:
        parts.append(f"({col} IS NULL OR NOT ({col} = ANY(%({pname}_canon)s)))")
        params[f"{pname}_canon"] = list(canonical)
    if not parts:
        return None
    return "(" + " OR ".join(parts) + ")"


# --- the hooks: every irregular filter, one hook per grain ------------------------------


def _price_bound(fid: str, v: Any, values: Mapping[str, Any], params: dict[str, Any]) -> list[str]:
    """A price_czk bound; with include_no_price a NULL-price row survives it (scope is
    price_czk only — the per-m² and yield bounds still drop NULL rows)."""
    if v is None:
        return []
    op = ">=" if fid.startswith("min_") else "<="
    params[fid] = v
    if values.get("include_no_price"):
        return [f"(l.price_czk is null or l.price_czk {op} %({fid})s)"]
    return [f"l.price_czk {op} %({fid})s"]


def _only(expr: str) -> Hook:
    """A BOOL "only" filter over `expr`: true narrows to it, false or None is no constraint."""
    def hook(fid: str, v: Any, values: Mapping[str, Any], params: dict[str, Any]) -> list[str]:
        return [expr] if v else []
    return hook


def _modifier(fid: str, v: Any, values: Mapping[str, Any], params: dict[str, Any]) -> list[str]:
    """include_no_price / price_change_window_days / the portal rule's modifiers: read by the
    hook they modify."""
    return []


def _seen_days(fid: str, v: Any, values: Mapping[str, Any], params: dict[str, Any]) -> list[str]:
    if v is None:
        return []
    col, op = _SEEN_DAYS[fid]
    params[fid] = v
    return [f"l.{col} {op} now() - make_interval(days => %({fid})s)"]


def _price_change_count(
    fid: str, v: Any, values: Mapping[str, Any], params: dict[str, Any],
) -> list[str]:
    """The window picks the precomputed count column; the column name comes from the
    registry's canonical dict, never from the stored value."""
    if v is None:
        return []
    col = PRICE_CHANGE_COUNT_COLUMNS[values.get("price_change_window_days")]
    params[fid] = v
    return [f"l.{col} >= %({fid})s"]


def _total_price_change(
    fid: str, v: Any, values: Mapping[str, Any], params: dict[str, Any],
) -> list[str]:
    """Signed: negative = a total drop of at least that much, positive = a rise; 0 = none."""
    if v is None or v == 0:
        return []
    params[fid] = v
    return [f"l.total_price_change_pct {'<=' if v < 0 else '>='} %({fid})s"]


def _city_index_rules(
    fid: str, v: Any, values: Mapping[str, Any], params: dict[str, Any],
) -> list[str]:
    if not v:
        return []
    # ONE SQL function owns rule evaluation (migration 436). All three consumers --
    # Browse and Stats (a client-resolved obec array, browse_stats_properties' obec_ids_filter;
    # api/stats_pivot resolves it the same way) and this matcher -- reduce to `obec_id = ANY(...)`,
    # a form with nothing left to diverge on (rule 16). Migration 374's own header
    # records two divergences found between the three hand-maintained copies this
    # replaces, including an operator-chosen op silently re-interpreted as >=.
    #
    # ARRAY(SELECT ...) forces a once-per-statement InitPlan; `IN (SELECT ...)` can
    # degrade into a per-row correlated SubPlan -- the exact shape that made this
    # predicate cost 1,778,259 blocks.
    #
    # Nothing is string-interpolated any more: the operator whitelist lives in the
    # function's CASE, whose else-arm is `>=`. That is strictly safer than the old
    # inline-the-operator-token approach.
    params[fid] = Jsonb(v)
    return [f"l.obec_id = ANY (ARRAY(SELECT curated_cities_matching(%({fid})s::jsonb)))"]


def _portal_rule(
    fid: str, v: Any, values: Mapping[str, Any], params: dict[str, Any],
) -> list[str]:
    """THE portal rule on a property relation (MS19; migrations 590, 597, 657): the status is
    the property's own, a portal asks for an ad there in that status, and no ad on an excluded
    portal. One SQL function owns it (inlined: sql, immutable); the Watchdog sets no status,
    its `any` arm."""
    status, excluded = values.get("status"), values.get("portals_excluded")
    if status in (None, "any") and not _is_set(v) and not _is_set(excluded):
        return []
    params.update({
        fid: list(v) if v else None, "status": status,
        "portals_excluded": list(excluded) if excluded else None,
        "portals_excluded_active_only": bool(values.get("portals_excluded_active_only")),
    })
    return [f"portal_status_matches(l.is_active, l.all_sources, l.active_sources, %({fid})s::text[], "
            "%(status)s::text, %(portals_excluded)s::text[], %(portals_excluded_active_only)s)"]


def _with_estimates(
    fid: str, v: Any, values: Mapping[str, Any], params: dict[str, Any],
) -> list[str]:
    """Browse's prefilter relation (queries.ts resolveEstimatesPrefilter), under the reader's own login."""
    return ["EXISTS (SELECT 1 FROM property_estimates_public e WHERE e.property_id = l.property_id)"] if v else []


def _building_material(
    fid: str, v: Any, values: Mapping[str, Any], params: dict[str, Any],
) -> list[str]:
    if not v:
        return []
    params[fid] = building_material_values(v)
    return [f"l.building_type = ANY(%({fid})s)"]


_HOOKS: Mapping[str, Hook] = MappingProxyType({
    "min_price_czk": _price_bound,
    "max_price_czk": _price_bound,
    "include_no_price": _modifier,
    "price_change_window_days": _modifier,
    **{fid: _seen_days for fid in _SEEN_DAYS},
    "price_change_count_min": _price_change_count,
    "total_price_change_pct": _total_price_change,
    "city_index_rules": _city_index_rules,
    "building_material": _building_material,
    "with_estimates": _with_estimates,
})


# --- the grains ------------------------------------------------------------------------


# `listings` has no price_per_m2 or tom_days column, so each measure is its function call there;
# the read models PUBLISH them, so `l.price_per_m2` is the measure on `properties_public` and
# `browse_list`. `properties_public` publishes no plot column, so it calls plot_area_m2()
# (migration 534); `browse_list` publishes it. A portal is the canonical ad's own on `listings`
# (estimation comparables) and the portal rule on a property relation; observed TOM is the
# measure over an ad on `listings` and the property's published flag on `browse_list`
# (migration 625: its EARLIEST ad with a category).
_PORTAL_RULE: Mapping[str, Hook] = MappingProxyType({
    "portals": _portal_rule,
    "portals_excluded": _modifier,
    "portals_excluded_active_only": _modifier,
})
LISTINGS_GRAIN = FilterGrain(
    agenda=Agenda.COMPARABLES,
    exprs=MappingProxyType({
        "price_per_m2": per_m2_sql("l"), "plot_area_m2": plot_area_sql("l"),
        "tom_days": tom_days_sql("l"),
    }),
    caller_renders=frozenset({"radius_m", "area_band_pct", "disposition_match", "floor_band",
                              "lifecycle", "max_age_days", "include_unreliable"}),
    hooks=MappingProxyType({"tom_observed_only": _only(tom_observed_sql("l"))}),
)
PROPERTIES_GRAIN = FilterGrain(
    agenda=Agenda.WATCHDOG,
    exprs=MappingProxyType({"plot_area_m2": plot_area_sql("l")}),
    caller_renders=frozenset({"location", "bounds", "circle", "districts"}),
    hooks=_PORTAL_RULE,
)
BROWSE_GRAIN = FilterGrain(
    agenda=Agenda.BROWSE,
    exprs=MappingProxyType({}),
    caller_renders=frozenset({"location", "bounds", "circle", "districts", "tags", "collections", "pipeline",
                              "show_dismissed"}),
    hooks=MappingProxyType({**_PORTAL_RULE, "status": _modifier,
                            "tom_observed_only": _only("l.tom_observed")}),
)
# The registered sales: `public.sold_comparables` (migration 545) returns every column its agenda filters
# (tests/test_sold_comps_read_surface.py).
SOLD_GRAIN = FilterGrain(agenda=Agenda.SOLD, exprs=MappingProxyType({}), caller_renders=frozenset())


# --- the compile -----------------------------------------------------------------------


def _gate(values: Mapping[str, Any], grain: FilterGrain) -> None:
    """Rules 16 + 17: a set filter the grain's agenda does not declare never renders."""
    denied = [
        f for f in all_filters()
        if f.id not in grain.caller_renders
        and _is_set(values.get(f.id))
        and grain.agenda not in f.agendas
    ]
    if not denied:
        return
    ids = ", ".join(f.id for f in denied)
    if any(f.category == CATEGORY_CITY_QUALITY for f in denied):
        raise ValueError(
            f"rule 17 violation: city-quality filters ({ids}) are not declared for the "
            f"{grain.agenda} agenda (toolkit/filter_registry.py); an estimate must never "
            "depend on a city_index_* revision"
        )
    raise ValueError(f"agenda violation: {ids} are not declared for the {grain.agenda} agenda")


def compile_filter_where(
    values: Mapping[str, Any], grain: FilterGrain,
) -> tuple[list[str], dict[str, Any]]:
    """Render every set registry filter in `values` against `l`; raise on one `grain.agenda` does not declare."""
    for fid, message in RETIRED_FILTERS.items():
        if values.get(fid) is not None:
            raise ValueError(message)
    unknown = sorted(
        k for k, v in values.items()
        if _is_set(v) and k not in REGISTRY and k not in grain.caller_renders
        and k not in RETIRED_FILTERS
    )
    if unknown:
        raise LookupError(f"{unknown}: not registered filters")
    _gate(values, grain)

    where: list[str] = []
    params: dict[str, Any] = {}
    for f in all_filters():
        if f.id in grain.caller_renders:
            continue
        value = values.get(f.id)
        # A hook runs unset too: the rule one id owns may be set through its modifiers alone.
        hook = grain.hooks.get(f.id, _HOOKS.get(f.id))
        if hook is not None:
            where.extend(hook(f.id, value, values, params))
            continue
        if not _is_set(value):
            continue
        kind = sql_kind(f)
        if kind is None:
            raise LookupError(f"{f.id}: no sql_kind and no hook")
        expr = grain.exprs.get(f.pg_column, f"l.{f.pg_column}")
        template = _KIND_SQL[kind]
        if template is None:
            clause = _enum_or_unknown_clause(
                list(value), expr, f.id, COLUMN_CANONICAL_VALUES[f.pg_column], params,
            )
            if clause:
                where.append(clause)
            continue
        if kind == "any":
            template, value = (_KIND_SQL["eq"], value[0]) if len(value) == 1 else (template, list(value))
        where.append(template.format(expr=expr, id=f.id))
        params[f.id] = value
    return where, params


__all__ = [
    "BROWSE_GRAIN", "FilterGrain", "LISTINGS_GRAIN", "PROPERTIES_GRAIN", "RETIRED_FILTERS",
    "compile_filter_where",
]
