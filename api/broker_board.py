"""The broker board (docs/design/broker-board.md): Browse's cohort counted by broker through `broker_book`.

A market read on the service connection (the broker relations are the API's alone, A6), so the reader's own lenses,
which only their login can judge (migration 629), are refused rather than dropped. Read by `POST /brokers/board`, the
agents' `rank_brokers` and Outreach.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

import psycopg
from psycopg.rows import dict_row
from pydantic import Field

from api import tenant_pool
from api.filter_args import Box, agenda_ids, filter_model
from api.property_read import jsonable
from api.property_search import cohort_where
from scraper.portal_factory import indexless_sources
from toolkit import _now_iso
from toolkit import filter_registry as fr
from toolkit.broker_sources import BROKER_SOURCE_NAMES
from toolkit.brokers import book_rule_sql
from toolkit.measures import EXIT_CLEAN_SINCE, EXIT_MARGIN_DAYS, exit_within_sql, wilson_lower
from toolkit.stats_registry import MIN_N

LENSES = frozenset(f.id for f in fr.all_filters() if f.category == fr.CATEGORY_CURATION)
FILTERS = tuple(i for i in agenda_ids(fr.Agenda.BROWSE) if i != "location" and i not in LENSES)
BOARD_MAX = 200

# Every board filter by how it bears on speed (a test holds the two sets to FILTERS, so a new Browse filter is
# classified before the board takes it). An OUTCOME filter selects on how the stay went, so the share over what it
# leaves is chosen by the answer: speed is refused. The rest select on when or how the stay began, or on what the
# property is and where.
OUTCOME = frozenset({
    "status", "recently_changed_days", "tom_days_min", "tom_days_max", "last_seen_min_days", "last_seen_max_days",
    "portals_excluded_active_only", "price_change_count_min", "price_change_window_days", "total_price_change_pct"})
NOT_OUTCOME = frozenset("""
    districts recently_added_days tom_observed_only first_seen_min_days first_seen_max_days category_main_in
    category_type category_sub_cb dispositions subtype portals portals_excluded condition_match building_material
    energy_rating_match furnished ownership has_balcony has_lift has_parking terrace cellar garage min_parking_lots
    min_price_czk max_price_czk include_no_price min_price_per_m2 max_price_per_m2 min_mf_gross_yield_pct
    max_mf_gross_yield_pct min_area_m2 max_area_m2 min_estate_area max_estate_area min_usable_area max_usable_area
    min_garden_area max_garden_area city_index_rules min_city_population max_city_population near_pop_5km_min
    near_pop_15km_min near_jobs_5km_min near_jobs_15km_min near_youth_5km_min near_youth_15km_min
    near_overall_5km_min near_overall_15km_min""".split())
# Why speed does not answer (volume always does).
OUTCOME_SELECTED, MIXED_CATEGORY = "outcome_selected", "mixed_category"
# The registry's portals that publish no broker: their properties count toward nothing.
_UNCOVERED = tuple(o.value for o in fr.PORTAL_OPTIONS if o.value not in BROKER_SOURCE_NAMES)
_BROKER_COLUMNS = ("display_name", "firm_id", "firm_name", "firm_domain")
_CONTACT_COLUMNS = ("primary_email", "primary_phone")


class RankArgs(filter_model(
    "RankArgs", FILTERS, None, required={"category_type"},
    descriptions={
        "category_type": "Deal type, always stated: pronajem = for rent, prodej = for sale, drazba = auction, podil "
        "= fractional ownership, null = every deal type. A cohort of more than one deal type or property type is "
        "counted but answers no speed.",
        "status": "The property's own status: any = live and delisted properties; active = a live property; "
        "inactive = a delisted property, every ad ended. A broker counts a property with one of their ads there in "
        "that status (with a portal filter: on that portal). Any status but any answers no speed.",
    },
)):
    bounds: Box | None = Field(
        None, description="The map box: a property matches when its point lies inside, as on Browse's map. Null = "
        "anywhere.")
    horizon_days: Literal[14, 30, 60, 90] = Field(
        30, description="Speed is the share of a broker's followed properties that left the market (sold or "
        "withdrawn, not necessarily sold) within this many days of the broker's first ad there.")
    sort: Literal["properties", "speed"] = Field(
        "properties", description="properties = most properties first; speed = the highest lower bound of the share "
        "first, then the brokers with too few followed properties, by properties. Ties break on broker_id.")
    min_properties: int = Field(1, ge=1, description="Leave out brokers with fewer properties in the cohort.")


class BoardArgs(RankArgs):
    limit: int = Field(100, ge=1, le=BOARD_MAX, description="How many brokers to return.")
    firm_ids: list[int] = Field(
        [], description="Only brokers of these firms (the broker's primary firm). Narrows the rows, never the market "
        "benchmark.")
    broker_id: int | None = Field(
        None, ge=1, description="Only the properties this broker has an ad on (in the status, on the portals, as "
        "Browse's broker scope); the board then lists every broker of those properties.")


def _statement(cohort: list[str]) -> str:
    flags = "".join(f", l.newest_ad_at_{p} IS NOT NULL AS on_{p}" for p in _UNCOVERED)
    uncovered = ", ".join(f"CASE WHEN bool_or(c.on_{p}) THEN '{p}' END" for p in _UNCOVERED)
    outcome = exit_within_sql("b", "c", "%(horizon_days)s::integer")
    # j: the cohort's book rows passing the broker side of the portal rule (the property side already applied the
    # status and every exclusion). The totals run over all of j: firm_ids and min_properties narrow only the rows
    # LEFT JOINed onto them, so the totals row stands when no broker does.
    return f"""
WITH c AS (
  SELECT l.property_id, l.is_active, l.last_seen_at, l.category_main, l.category_type{flags}
  FROM browse_list l WHERE {' AND '.join(cohort) or 'true'}
), j AS (
  SELECT b.broker_id, c.property_id,
         CASE WHEN NOT b.all_sources && %(indexless)s::text[] THEN {outcome} END AS outcome
  FROM c JOIN broker_book b USING (property_id)
  WHERE {book_rule_sql("b")}
), per AS (
  SELECT broker_id, count(*) AS properties,
         count(*) FILTER (WHERE outcome = 'exited') AS exited,
         count(*) FILTER (WHERE outcome = 'not_exited') AS not_exited,
         count(*) FILTER (WHERE outcome = 'unknown') AS unknown
  FROM j GROUP BY broker_id
)
SELECT s.*, m.*, p.broker_id, p.properties, p.exited, p.not_exited, p.unknown
FROM (SELECT count(*) AS cohort_properties, coalesce(min(c.category_main) <> max(c.category_main), false) AS mixed_main,
             coalesce(min(c.category_type) <> max(c.category_type), false) AS mixed_type, max(c.last_seen_at) AS data_freshness,
             array_remove(ARRAY[{uncovered}]::text[], NULL) AS uncovered_portals
      FROM c) s
CROSS JOIN (SELECT (SELECT count(*) FROM (SELECT property_id FROM j GROUP BY property_id) a) AS attributed_properties,
                   count(*) FILTER (WHERE j.outcome = 'exited') AS market_exited,
                   count(*) FILTER (WHERE j.outcome = 'not_exited') AS market_not_exited,
                   count(*) FILTER (WHERE j.outcome = 'unknown') AS market_unknown
            FROM j) m
LEFT JOIN per p ON p.properties >= %(min_properties)s
  AND (cardinality(%(firm_ids)s::bigint[]) = 0
       OR EXISTS (SELECT 1 FROM brokers f WHERE f.id = p.broker_id AND f.primary_firm_id = ANY(%(firm_ids)s::bigint[])))
"""


def _refusal(args: BoardArgs, head: Mapping[str, Any]) -> tuple[str | None, list[str]]:
    """Why speed does not answer, and the filters to change for it to: the OUTCOME ones set, else the category filter
    each mixed category of the cohort answers to."""
    fields = type(args).model_fields
    outcome = [f for f in FILTERS if f in OUTCOME and getattr(args, f) != fields[f].default]
    if outcome:
        return OUTCOME_SELECTED, outcome
    mixed = [f for f, key in (("category_type", "mixed_type"), ("category_main_in", "mixed_main")) if head[key]]
    return (MIXED_CATEGORY if mixed else None), mixed


def _speed(exited: int, n: int, refused: str | None) -> tuple[float | None, float | None]:
    """The share exited and its Wilson lower bound (the rank), from MIN_N followed properties up."""
    if refused or n < MIN_N:
        return None, None
    return exited / n, wilson_lower(exited, n)


def _order(row: dict[str, Any], sort: str) -> tuple[Any, ...]:
    by_volume = (-row["properties"], row["broker_id"])
    return (row["rank"] is None, -(row["rank"] or 0.0), *by_volume) if sort == "speed" else by_volume


def board(conn: psycopg.Connection, args: BoardArgs, *, contacts: bool) -> dict[str, Any]:
    """The cohort's top `args.limit` brokers with their volume and speed, and the cohort's own totals (the market
    benchmark); with `contacts`, each broker's primary e-mail and phone (the route's PII policy masks them)."""
    where, params = cohort_where(args)
    if args.broker_id is not None:
        where.append(f"EXISTS (SELECT 1 FROM broker_book s WHERE s.property_id = l.property_id "
                     f"AND s.broker_id = %(broker_id)s AND {book_rule_sql('s')})")
    params.update({
        # Bound here too: the property side binds them only when its portal rule applies.
        "portals": list(args.portals) if args.portals else None, "status": args.status,
        "broker_id": args.broker_id, "indexless": list(indexless_sources()), "horizon_days": args.horizon_days,
        "min_properties": args.min_properties, "firm_ids": list(args.firm_ids),
    })
    columns = ("broker_id", *_BROKER_COLUMNS, *(_CONTACT_COLUMNS if contacts else ()))
    with conn.transaction(), conn.cursor(row_factory=dict_row) as cur:
        tenant_pool.bound_read(cur)
        cur.execute(_statement(where), params)
        counted = cur.fetchall()
        head = counted[0]
        refused, refused_by = _refusal(args, head)
        found = []
        for r in counted:
            if r["broker_id"] is None:
                continue
            n = r["exited"] + r["not_exited"]
            share, rank = _speed(r["exited"], n, refused)
            found.append({"broker_id": r["broker_id"], "properties": r["properties"], "n": n, "exited": r["exited"],
                          "unknown": r["unknown"], "share": share, "rank": rank})
        found = sorted(found, key=lambda r: _order(r, args.sort))[:args.limit]
        cur.execute(f"SELECT {', '.join(columns)} FROM brokers_public WHERE broker_id = ANY(%(ids)s::bigint[])",
                    {"ids": [r["broker_id"] for r in found]})
        names = {b["broker_id"]: b for b in cur.fetchall()}
    data = [{**dict.fromkeys(columns), **names.get(r["broker_id"], {}), **r} for r in found]
    market_n = head["market_exited"] + head["market_not_exited"]
    cohort = head["cohort_properties"]
    return {
        "data": data,
        "metadata": {
            "tool": "broker_board",
            "filters_used": args.model_dump(mode="json", by_alias=True, exclude_defaults=True),
            "result_count": len(data),
            "queried_at": _now_iso(),
            "data_freshness": jsonable(head["data_freshness"]),
            "cohort_properties": cohort,
            "attributed_properties": head["attributed_properties"],
            "unattributed_share": (cohort - head["attributed_properties"]) / cohort if cohort else None,
            # Pair-weighted over every attributed (property, broker) of the cohort, as each broker's share is.
            "market": {"n": market_n, "exited": head["market_exited"],
                       "share": _speed(head["market_exited"], market_n, refused)[0]},
            "unknown": head["market_unknown"],
            "horizon_days": args.horizon_days,
            "clean_exits_since": EXIT_CLEAN_SINCE,
            "margin_days": EXIT_MARGIN_DAYS,
            "min_n": MIN_N,
            "speed_refused": refused,
            "speed_refused_by": refused_by,
            "uncovered_portals": head["uncovered_portals"],
        },
    }
