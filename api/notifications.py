"""New-listing notifications ("Watchdog") backend (Phase U2.7).

Two responsibilities:

1. CRUD over `notification_subscriptions` — the operator's saved
   filter specs. Each row holds a name + a `WatchdogFilterSpec` JSONB
   blob whose field names are filter-registry ids (`toolkit/filter_registry.py`).

2. The background matcher. A FastAPI lifespan-spawned asyncio task
   wakes every `notifications_matcher_interval_seconds`, walks the
   properties (`properties_public`) whose `first_seen_at` is past each
   active subscription's cursor, and writes one `notification_dispatches`
   row per (subscription, property) match. Its UNIQUE `dedupe_key`
   (`wd:{sub}:new:{property_id}`) makes a re-run over the same window idempotent.

`WatchdogFilterSpec` is intentionally a separate model from
`ComparableFilters`: the watchdog matcher does NOT require a target
lat/lng (district / disposition / price filters alone are useful), but
DOES accept the map box Browse filters on ("alert me about anything in
this area"), and it carries the city-quality filters rule 17 keeps off the
cohort model. `_build_match_clauses` renders it over `properties_public`
through the one filter compiler (`toolkit/filter_compiler.compile_filter_where`)
that also renders every estimation cohort, so the matcher and the cohort
tools cannot disagree on what a filter means. One named exception: a portal
matches when ANY of the property's ads is on it here (MS19, `all_sources`),
and the canonical ad's own portal on the cohorts.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, field_validator, model_validator

from api.cursor import decode_cursor, encode_cursor
from api.location_filter import DistrictChip, MapBounds, bounds_where, district_where
from location_data.claims_common import served_location_predicate
from scraper import db as scraper_db
from toolkit.filter_compiler import PROPERTIES_GRAIN, compile_filter_where

if TYPE_CHECKING:
    import psycopg

LOG = logging.getLogger(__name__)

# The producers whose rows the feed shows (rule 16; CHECK notification_dispatches_source_ck).
# system_health rows are history the app no longer shows (ruling R2, notifications W1).
SourceKind = Literal["watchdog", "collection_monitor", "share"]
SourceKindFilter = Literal[SourceKind, "all"]


# --- filter spec ----------------------------------------------------------


class WatchdogFilterSpec(BaseModel):
    """JSON shape persisted in `notification_subscriptions.filter_spec`.

    Field names are registry ids (`toolkit/filter_registry.py`) for what the
    Browse sidebar exposes; the matcher converts this into a parameterised
    WHERE clause via `_build_match_clauses`. Every field defaults to
    `None` so a watchdog can be as wide ("any apartment for rent") or
    narrow ("furnished 3+kk in Praha 2 under 30 000 Kč near these
    coordinates") as the operator wants.

    The map box is optional and the ONE spatial shape: `bounds` is what
    Browse filters on (its viewport, or the square around a centre+radius
    pick), so a watchdog saved from Browse watches the rows Browse showed.
    Place chips are an independent AND; null everywhere = no restriction.
    """

    # Category. `category_type` (deal type) stays single-valued — rent and
    # sale are different price scales, so Browse keeps it an exclusive pill;
    # its default targets "for rent" so a blank-save watchdog isn't every
    # deal type. `category_main_in` is multi-select (a listing matches if
    # its category_main is in the list); null = no constraint. Mirrors the
    # Browse split (scalar category_type, multi category_main_in) and the
    # dispositions / disposition_match precedent.
    category_main_in: list[str] | None = None
    category_type: str | None = "pronajem"
    category_sub_cb: int | None = None

    # Portal-agnostic property sub-type (multi-select; matches any).
    subtype: list[str] | None = None

    # Disposition (multi-select; matches any).
    dispositions: list[str] | None = None

    # The map box (rule 16): the lat/lng band Browse's list and both stats RPCs apply.
    bounds: MapBounds | None = None

    # Place. Each chip is a `DistrictChip` — a LEVEL plus a RÚIAN CODE
    # (`api/location_filter.py`), the one predicate Browse, Stats, the map and
    # this matcher share (rule 16). A chip without both is refused (422).
    districts: list["DistrictChip"] | None = None

    # Price + area bounds.
    min_price_czk: int | None = None
    max_price_czk: int | None = None
    # When true AND a price bound is set, keep no-price (price_czk IS NULL)
    # listings instead of dropping them. No-op when no bound is set. Mirrors
    # the Browse toggle so a saved alert means the same thing (rule #16).
    include_no_price: bool = False
    # Price per m²: properties_public.price_per_m2, i.e. measure_price_per_m2
    # (migration 425) -- basis-resolved from (category_main, category_type) and
    # withheld below its basis floor. Rows with no area, no price, an
    # undecidable basis, or a sub-floor price fall out when either bound is set.
    min_price_per_m2: float | None = None
    max_price_per_m2: float | None = None
    # MF gross rental yield %: properties_public's read-time mf_reference() (migrations 565 + 567).
    # Sale flats with a value only -- a town-level range carries no yield.
    min_mf_gross_yield_pct: float | None = None
    max_mf_gross_yield_pct: float | None = None
    min_area_m2: float | None = None
    max_area_m2: float | None = None
    min_usable_area: float | None = None
    max_usable_area: float | None = None
    min_estate_area: float | None = None
    max_estate_area: float | None = None
    # Lost by merge f2d7b359 (extra='ignore' then dropped them on every save); now pinned
    # by an equality test against the WATCHDOG agenda. A blob saved before that merge
    # holds `building_material` as one scalar bucket, which `_wrap_bare_str` lifts.
    building_material: list[str] | None = None
    min_garden_area: float | None = None
    max_garden_area: float | None = None

    # Tri-state amenities (None = don't care).
    has_balcony: bool | None = None
    has_lift: bool | None = None
    has_parking: bool | None = None
    terrace: bool | None = None
    cellar: bool | None = None
    garage: bool | None = None

    # Enumerated columns. furnished/ownership/condition_match are multi-select;
    # each may carry the `__unknown__` sentinel (NULL or a non-canonical value).
    furnished: list[str] | None = None
    ownership: list[str] | None = None
    # The portal rule's two sides (MS19 + migration 597), compiled by toolkit/filter_compiler.
    portals: list[str] | None = None
    portals_excluded: list[str] | None = None
    portals_excluded_active_only: bool = False
    condition_match: list[str] | None = None

    # Parking lots minimum.
    min_parking_lots: int | None = None

    # RETIRED (condition scoring parked, operator ruling 2026-10-20): kept only so an old
    # saved spec deserialises; any value raises in toolkit/filter_compiler.RETIRED_FILTERS.
    building_condition_level_min: int | None = None
    building_condition_level_max: int | None = None
    apartment_condition_level_min: int | None = None
    apartment_condition_level_max: int | None = None

    # Price-history aggregates (migrations 091 / 093 / 095 / 173).
    # Property-grain columns maintained by the recompute job; the matcher
    # reads them off properties_public, and registry ids match 1:1.
    # `price_change_window_days` (30 / 90 / 365, None = all time) picks
    # which precomputed count column `price_change_count_min` reads.
    # `total_price_change_pct` is signed: negative = total drop of at
    # least that much, positive = total rise. Stored specs predating
    # migration 173 may carry the retired per-direction keys
    # (price_drop_count_min etc.) — Pydantic's extra='ignore' default
    # drops them on load.
    price_change_count_min: int | None = None
    price_change_window_days: Literal[30, 90, 365] | None = None
    total_price_change_pct: float | None = None

    # Phase QUAL — curated-city quality predicates. Browse + Watchdog
    # only; not exposed to the estimation agent.
    city_index_rules: list[dict[str, Any]] | None = None
    min_city_population: int | None = None
    max_city_population: int | None = None
    near_city_proximity: dict[str, Any] | None = None
    # Fast polygon-edge proximity (migration 142). Precomputed columns on
    # properties_public; `>= value`. Compiled as plain `>=` bounds by
    # toolkit/filter_compiler.
    near_pop_5km_min: int | None = None
    near_pop_15km_min: int | None = None
    near_jobs_5km_min: float | None = None
    near_jobs_15km_min: float | None = None
    near_youth_5km_min: float | None = None
    near_youth_15km_min: float | None = None
    near_overall_5km_min: float | None = None
    near_overall_15km_min: float | None = None

    @field_validator(
        "furnished", "ownership", "category_main_in", "building_material", mode="before"
    )
    @classmethod
    def _wrap_bare_str(cls, v: Any) -> Any:
        """Accept a bare string (legacy single-select callers) as [string]."""
        return [v] if isinstance(v, str) else v

    @model_validator(mode="before")
    @classmethod
    def _migrate_legacy_category_main(cls, data: Any) -> Any:
        """Lift a legacy scalar `category_main` key (specs saved before the
        scalar→multiselect split) onto `category_main_in`, so existing
        watchdogs keep their category constraint instead of silently
        widening to "any category" on load."""
        if not isinstance(data, dict):
            return data
        if "category_main_in" not in data and "category_main" in data:
            legacy = data.get("category_main")
            if legacy is not None:
                data = {
                    **data,
                    "category_main_in": (
                        [legacy] if isinstance(legacy, str) else legacy
                    ),
                }
        return data


def _build_match_clauses(
    spec: WatchdogFilterSpec,
) -> tuple[list[str], dict[str, Any]]:
    """Render the filter spec as parameterised WHERE clauses.

    The matcher prepends a watermark / window clause; this helper owns
    the spec-derived part only. Spec-derived clauses come from
    `compile_filter_where(…, PROPERTIES_GRAIN)`; only the served predicate,
    the map box and the place chips are rendered here.
    """
    # THE CONSUMER RULE (W5, operator ruling 2026-09-13), unconditional and first.
    # Browse inherits it from `browse_projection` (migration 512); the matcher cannot,
    # because it reads `properties_public` — a DETAIL-by-id surface that deliberately
    # keeps serving an unresolved listing to a direct link. So the ONE definition is
    # rendered here instead, keyed on the property's LOCATION ad
    # (`properties_public.location_listing_ref_id`, migration 636), as browse_projection
    # keys it. Without it a watchdog would fire on a listing nobody can find in Browse,
    # and "matches" would mean two different things.
    where: list[str] = [served_location_predicate("l.location_listing_ref_id")]
    params: dict[str, Any] = {}

    # The map box, on the lat/lng properties_public projects off the location ad's
    # point: the band Browse's list and both stats RPCs apply (rule 16).
    b_where, b_params = bounds_where(spec.bounds, alias="l")
    where.extend(b_where)
    params.update(b_params)

    if spec.districts:
        # Delegates to the shared builder (`api.location_filter`) so Browse,
        # Stats, the map and the Watchdog can never disagree on what a place
        # chip means (rule 16): `<level>_id = any(codes)`, nothing else.
        d_where, d_params = district_where(spec.districts, alias="l")
        where.extend(d_where)
        params.update(d_params)

    compiled, compiled_params = compile_filter_where(dict(spec), PROPERTIES_GRAIN)
    where.extend(compiled)
    params.update(compiled_params)
    return where, params


# --- CRUD: subscriptions --------------------------------------------------


@dataclass
class SubscriptionRow:
    id: str
    name: str
    filter_spec: dict[str, Any]
    is_active: bool
    created_at: str
    updated_at: str
    dispatch_count: int


_SUB_COLS = "id, name, filter_spec, is_active, created_at, updated_at"


def _row_to_sub(row: tuple[Any, ...], dispatch_count: int) -> SubscriptionRow:
    return SubscriptionRow(
        id=str(row[0]),
        name=row[1],
        filter_spec=row[2] or {},
        is_active=bool(row[3]),
        created_at=row[4].isoformat() if row[4] else "",
        updated_at=row[5].isoformat() if row[5] else "",
        dispatch_count=dispatch_count,
    )


# Every in-app read and count of `notification_dispatches d` hides a dispatch
# about a property the caller dismissed (migration 536). Detection still writes
# it — the matcher's cursor and the `reactivated` detector depend on every row
# existing — so an undo brings it back intact. RLS-scoped: the view is the
# caller's own; a system_health row (property_id NULL) never matches.
_NOT_DISMISSED = (
    "NOT EXISTS (SELECT 1 FROM property_dismissals_public pd "
    "WHERE pd.property_id = d.property_id)"
)


def list_subscriptions(
    conn: "psycopg.Connection",
    *,
    include_inactive: bool = True,
) -> list[dict[str, Any]]:
    where = "" if include_inactive else "WHERE is_active = true"
    sql = (
        f"SELECT {_SUB_COLS}, "
        "  (SELECT count(*) FROM notification_dispatches d "
        "     WHERE d.subscription_id = notification_subscriptions.id "
        f"    AND {_NOT_DISMISSED}) AS dispatch_count "
        "FROM notification_subscriptions "
        f"{where} "
        "ORDER BY created_at DESC"
    )
    with conn.cursor() as cur:
        cur.execute(sql)
        rows = cur.fetchall()
    return [_row_to_sub(r[:-1], int(r[-1] or 0)).__dict__ for r in rows]


def get_subscription(
    conn: "psycopg.Connection", subscription_id: str,
) -> dict[str, Any] | None:
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT {_SUB_COLS}, "
            "  (SELECT count(*) FROM notification_dispatches d "
            f"    WHERE d.subscription_id = %s AND {_NOT_DISMISSED}) AS dispatch_count "
            "FROM notification_subscriptions WHERE id = %s",
            (subscription_id, subscription_id),
        )
        row = cur.fetchone()
    if row is None:
        return None
    return _row_to_sub(row[:-1], int(row[-1] or 0)).__dict__


def create_subscription(
    conn: "psycopg.Connection",
    *,
    name: str,
    filter_spec: WatchdogFilterSpec,
    is_active: bool = True,
    account_id: uuid.UUID | str,
) -> dict[str, Any]:
    # account_id is NOT NULL (migration 364) and REQUIRED here (no default): the
    # route resolves exactly one via tenant_pool.require_account_id, which 400s
    # when the caller has none, so this INSERT can never bind a silent NULL.
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO notification_subscriptions "
            "  (name, filter_spec, is_active, account_id) "
            "VALUES (%s, %s::jsonb, %s, %s) RETURNING id",
            (
                name,
                json.dumps(filter_spec.model_dump()),
                is_active,
                account_id,
            ),
        )
        row = cur.fetchone()
    assert row is not None
    return get_subscription(conn, str(row[0])) or {}


def update_subscription(
    conn: "psycopg.Connection",
    subscription_id: str,
    *,
    name: str | None = None,
    filter_spec: WatchdogFilterSpec | None = None,
    is_active: bool | None = None,
) -> dict[str, Any] | None:
    sets: list[str] = []
    params: list[Any] = []
    if name is not None:
        sets.append("name = %s")
        params.append(name)
    if filter_spec is not None:
        sets.append("filter_spec = %s::jsonb")
        params.append(json.dumps(filter_spec.model_dump()))
    if is_active is not None:
        sets.append("is_active = %s")
        params.append(is_active)
    if not sets:
        return get_subscription(conn, subscription_id)
    params.append(subscription_id)
    with conn.cursor() as cur:
        cur.execute(
            f"UPDATE notification_subscriptions SET {', '.join(sets)} "
            "WHERE id = %s",
            params,
        )
        if cur.rowcount == 0:
            return None
    return get_subscription(conn, subscription_id)


def delete_subscription(
    conn: "psycopg.Connection", subscription_id: str,
) -> bool:
    """Hard delete. Cascade drops the dispatches via the FK."""
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM notification_subscriptions WHERE id = %s",
            (subscription_id,),
        )
        return cur.rowcount > 0


# --- dispatches (the notification feed) -----------------------------------


# EVERY column here reads an owner-rights view (`listings_public`; the place and
# the MF yield `properties_public`), never `listings` — and that is a FIX, not a style
# choice. This feed runs on the tenant pool, i.e. under
# `SET LOCAL ROLE authenticated`, and `listings` has carried RLS with NO POLICY
# since migration 001: under that role the table is empty, so the LEFT JOIN this
# projection used to sit on matched nothing and every one of these columns
# arrived NULL in the feed. `listings_public` is an owner-rights view
# over the same rows with no WHERE of its own (migration 494), so it returns
# exactly what a BYPASSRLS reader always saw here and what the browser role never
# did. The two views are also the only way this query can reach a resolved location:
# migration 501 revoked `listing_location` from `authenticated`, which is the
# same reason pipeline_board_public reads its label off properties_public.
#
# `display_label` is the ONE place string (migration 503). It replaced the
# `locality, district` pair the feed used to ship, which the SPA fell back
# through as `locality ?? district` while the extension fell back the other way
# — two surfaces naming the same listing two different places. It is the
# PROPERTY's (ruling R5): a dispatch is property-grain (rule 16), so the feed
# names the place its property page, Browse and the map name, its location ad's.
_LISTING_PROJECTION = (
    "lp.category_main, lp.category_type, lp.price_czk, "
    "lp.price_unit, lp.area_m2, lp.disposition, lp.subtype, "
    "pp.display_label, "
    "lp.is_active, lp.first_seen_at, lp.last_seen_at, "
    "lp.source, lp.source_url, "
    # MF is property-grain, like the dispatch itself (rule 16): the feed shows
    # the number the matcher filtered on, off the same properties_public row.
    "pp.mf_gross_yield_pct"
)

# The unified feed projection + FROM, shared by list_dispatches + _fetch_dispatch
# so the two never diverge. LEFT JOINs (not INNER): a collection_monitor row has
# subscription_id NULL, so an INNER join to subscriptions would silently drop it.
# Exposes the source discriminator + provenance the watchdog feed never needed.
# `share_kind` says what a share opens, read off its change_kind (`shared_<collection|pipeline|
# dashboard>`, migration 652); `share_ref` its id, still read off the key (api/shares.py:
# `share:<kind>:<collection id | owner | dashboard id>:…`) until W3 retires the key.
_DISPATCH_SELECT = (
    "d.id, d.source_kind, "
    "d.subscription_id, s.name AS subscription_name, "
    "d.collection_id, c.name AS collection_name, "
    "d.listing_id, d.property_id, d.change_kind, d.message, "
    "CASE WHEN d.source_kind = 'share' THEN split_part(d.change_kind, '_', 2) END AS share_kind, "
    "CASE WHEN d.source_kind = 'share' THEN split_part(d.dedupe_key, ':', 3) END AS share_ref, "
    "d.dispatched_at, d.seen_at, "
    "d.trigger_price_czk, d.prev_price_czk, d.trigger_snapshot_id, "
    f"{_LISTING_PROJECTION}"
)

_DISPATCH_FROM = (
    "FROM notification_dispatches d "
    "LEFT JOIN notification_subscriptions s ON s.id = d.subscription_id "
    "LEFT JOIN collections c ON c.id = d.collection_id "
    # Surrogate join (R2): safe for the whole append-only ledger, not just new
    # rows — Phase A4's backfill left zero (sreality_id NOT NULL, listing_id NULL)
    # rows, so no historical dispatch loses its listing fields here. A plain
    # equality keeps this an index lookup; a COALESCE/OR fallback would not.
    # ONE join, to the VIEW: the bare `listings` join it replaced was invisible to
    # the tenant role (RLS, no policy) — see _LISTING_PROJECTION.
    "LEFT JOIN listings_public lp ON lp.id = d.listing_id "
    "LEFT JOIN properties_public pp ON pp.property_id = d.property_id "
)


def list_dispatches(
    conn: "psycopg.Connection",
    *,
    subscription_id: str | None = None,
    collection_id: int | None = None,
    source_kind: SourceKindFilter = "all",
    seen: Literal["all", "seen", "unseen"] = "all",
    limit: int = 50,
    offset: int = 0,
    cursor: str | None = None,
) -> dict[str, Any]:
    """Return the notification feed, KEYSET-paginated on (dispatched_at, id).

    One row per `notification_dispatches` × `listings` join; rows that
    fired against multiple subscriptions are grouped client-side by the
    matching subscription names — but on the wire each row is the
    canonical (dispatch, listing) pair so the table renders one line
    per dispatch and the UI dedups (property_id → list of subscriptions)
    when it wants the "fired by N watchdogs" presentation.

    The feed is append-only and grows under the background matcher; keyset
    on (dispatched_at, id) keeps a live scroll dup/skip-free (offset would
    shift as new dispatches prepend). `id` is a uuid — fine as a
    deterministic tiebreaker. `total` is computed once, on the first page.
    """
    where: list[str] = [_NOT_DISMISSED]
    params: dict[str, Any] = {}
    if subscription_id is not None:
        where.append("d.subscription_id = %(subscription_id)s")
        params["subscription_id"] = subscription_id
    if collection_id is not None:
        where.append("d.collection_id = %(collection_id)s")
        params["collection_id"] = collection_id
    if source_kind != "all":
        where.append("d.source_kind = %(source_kind)s")
        params["source_kind"] = source_kind
    if seen == "seen":
        where.append("d.seen_at IS NOT NULL")
    elif seen == "unseen":
        where.append("d.seen_at IS NULL")
    filter_sql = "WHERE " + " AND ".join(where)

    page_where = list(where)
    if cursor is not None:
        c_ts, c_id = decode_cursor(cursor)
        page_where.append(
            "(d.dispatched_at, d.id) < (%(c_ts)s::timestamptz, %(c_id)s::uuid)"
        )
        params["c_ts"] = c_ts
        params["c_id"] = c_id
    page_where_sql = "WHERE " + " AND ".join(page_where)

    sql = (
        f"SELECT {_DISPATCH_SELECT} "
        f"{_DISPATCH_FROM}"
        f"{page_where_sql} "
        "ORDER BY d.dispatched_at DESC, d.id DESC "
        "LIMIT %(limit)s OFFSET %(offset)s"
    )
    list_params = {**params, "limit": limit, "offset": 0 if cursor else offset}

    with conn.cursor() as cur:
        cur.execute(sql, list_params)
        rows = cur.fetchall()
        cols = [d[0] for d in cur.description] if cur.description else []
        total: int | None = None
        # Count on the first page only (cursor None); legacy offset path keeps
        # its total. See list_estimation_runs for the rationale.
        if cursor is None:
            count_params = {k: params[k] for k in params if k not in ("c_ts", "c_id")}
            cur.execute(
                f"SELECT count(*) FROM notification_dispatches d {filter_sql}",
                count_params,
            )
            total_row = cur.fetchone()
            total = int(total_row[0]) if total_row else 0

    next_cursor: str | None = None
    if len(rows) == limit and rows:
        ts_idx = cols.index("dispatched_at")
        id_idx = cols.index("id")
        last = rows[-1]
        next_cursor = encode_cursor([last[ts_idx].isoformat(), str(last[id_idx])])

    return {
        "data": [_dispatch_row_to_dict(cols, r) for r in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
        "next_cursor": next_cursor,
    }


def _dispatch_row_to_dict(cols: list[str], row: tuple[Any, ...]) -> dict[str, Any]:
    out: dict[str, Any] = dict(zip(cols, row))
    for k in ("dispatched_at", "seen_at", "first_seen_at", "last_seen_at"):
        v = out.get(k)
        if isinstance(v, datetime):
            out[k] = v.isoformat()
    if "subscription_id" in out and out["subscription_id"] is not None:
        out["subscription_id"] = str(out["subscription_id"])
    if "id" in out and out["id"] is not None:
        out["id"] = str(out["id"])
    if out.get("area_m2") is not None:
        out["area_m2"] = float(out["area_m2"])
    if out.get("mf_gross_yield_pct") is not None:
        out["mf_gross_yield_pct"] = float(out["mf_gross_yield_pct"])
    return out


def mark_dispatch_seen(
    conn: "psycopg.Connection", dispatch_id: str,
) -> dict[str, Any] | None:
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE notification_dispatches SET seen_at = now() "
            "WHERE id = %s AND seen_at IS NULL",
            (dispatch_id,),
        )
    return _fetch_dispatch(conn, dispatch_id)


def _fetch_dispatch(
    conn: "psycopg.Connection", dispatch_id: str,
) -> dict[str, Any] | None:
    sql = (
        f"SELECT {_DISPATCH_SELECT} "
        f"{_DISPATCH_FROM}"
        "WHERE d.id = %s"
    )
    with conn.cursor() as cur:
        cur.execute(sql, (dispatch_id,))
        row = cur.fetchone()
        cols = [d[0] for d in cur.description] if cur.description else []
    if row is None:
        return None
    return _dispatch_row_to_dict(cols, row)


def get_unread_count(conn: "psycopg.Connection") -> dict[str, int]:
    """The one unread number (bell and badge): the caller's visible unseen dispatches."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM notification_dispatches d "
            f"WHERE d.seen_at IS NULL AND {_NOT_DISMISSED}"
        )
        row = cur.fetchone()
    return {"unread_count": int(row[0]) if row else 0}


def mark_all_seen(
    conn: "psycopg.Connection",
    *,
    source_kind: SourceKindFilter = "all",
) -> int:
    """Mark every visible unseen dispatch (optionally scoped to a source) as seen.

    A dismissed property's dispatches are not visible, so they keep their unseen
    state for when the dismissal is undone.
    """
    sql = (
        "UPDATE notification_dispatches d SET seen_at = now() "
        f"WHERE d.seen_at IS NULL AND {_NOT_DISMISSED}"
    )
    with conn.cursor() as cur:
        if source_kind == "all":
            cur.execute(sql)
        else:
            cur.execute(sql + " AND d.source_kind = %s", (source_kind,))
        return cur.rowcount or 0


# --- matcher loop ---------------------------------------------------------


@dataclass
class MatcherSettings:
    interval_seconds: int
    window_listings: int


def _load_matcher_settings(conn: "psycopg.Connection") -> MatcherSettings:
    interval = _read_int_setting(
        conn, "notifications_matcher_interval_seconds", default=300,
    )
    window = _read_int_setting(
        conn, "notifications_match_window_listings", default=1000,
    )
    return MatcherSettings(
        interval_seconds=max(0, interval),
        window_listings=max(1, window),
    )


def _read_int_setting(
    conn: "psycopg.Connection", key: str, *, default: int,
) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT value FROM app_settings WHERE key = %s", (key,))
        row = cur.fetchone()
    if row is None or row[0] is None:
        return default
    try:
        return int(row[0])
    except (TypeError, ValueError):
        return default


def _read_bool_setting(
    conn: "psycopg.Connection", key: str, *, default: bool,
) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT value FROM app_settings WHERE key = %s", (key,))
        row = cur.fetchone()
    if row is None or row[0] is None:
        return default
    value = row[0]
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    if isinstance(value, (int, float)):
        return bool(value)
    return default


# --- images-first publication gate (operator decision 2026-07-02) ----------
#
# Never surface a NEW listing before its first image is stored. A candidate
# property is dispatchable only when (a) any of its listings has a stored
# image, OR (b) it is older than the timeout (portals with slow CDNs / genuinely
# photoless listings must not be silenced forever), OR (c) it has ZERO image
# rows at all AND is past a short floor — image rows appear at detail write, so
# zero rows after the floor ≈ no photos at source.

IMAGE_GATE_ZERO_ROWS_FLOOR_MINUTES = 5

_IMAGE_GATE_SQL = (
    "( EXISTS ("
    "    SELECT 1 FROM listings gl"
    "    JOIN images gi ON gi.listing_id = gl.id"
    "    WHERE gl.property_id = l.property_id"
    "      AND gi.storage_path IS NOT NULL"
    "  )"
    "  OR l.first_seen_at < now() - make_interval(mins => %(image_timeout_minutes)s)"
    "  OR ("
    "    l.first_seen_at < now()"
    "      - make_interval(mins => %(image_zero_rows_floor_minutes)s)"
    "    AND NOT EXISTS ("
    "      SELECT 1 FROM listings gl"
    "      JOIN images gi ON gi.listing_id = gl.id"
    "      WHERE gl.property_id = l.property_id"
    "    )"
    "  )"
    ")"
)


@dataclass
class ImageGateSettings:
    enabled: bool
    timeout_minutes: int

    @property
    def lookback_minutes(self) -> int:
        # The re-scan window must exceed the longest possible hold (the
        # timeout), or a property gated the whole way would leave the lookback
        # before its release and be lost.
        #
        # It must also exceed the longest a MATCHING FACT can be late, and the latest such
        # fact is the post-publication text lane's (SLO 20 min, W7): the `:new:` cursor
        # advances past a property that did not match and the dedupe key is once-ever, so
        # an attribute filled after the cursor passed can only ever cost a MISSED alert,
        # never a duplicate, and this re-scan is the only second look there is. 60 min
        # already clears 20 by 3x. Reading `description_extraction.SLO_MINUTES` here was
        # tried and removed: as a third `max()` term it can never be the maximum, so it
        # bought an api -> toolkit -> scraper import at call time and changed nothing.
        return max(60, self.timeout_minutes * 2)


def _load_image_gate_settings(conn: "psycopg.Connection") -> ImageGateSettings:
    enabled = _read_bool_setting(
        conn, "notifications_new_requires_image", default=True,
    )
    timeout = _read_int_setting(
        conn, "notifications_new_requires_image_timeout_minutes", default=30,
    )
    return ImageGateSettings(enabled=enabled, timeout_minutes=max(1, timeout))


def _insert_new_dispatches(
    conn: "psycopg.Connection", where: list[str], params: dict[str, Any],
) -> int:
    """One set-based 'new' dispatch INSERT; ON CONFLICT (dedupe_key) makes any
    re-consideration of the same property idempotent."""
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO notification_dispatches "
            "  (subscription_id, source_kind, property_id, listing_id, "
            "   change_kind, trigger_price_czk, dedupe_key) "
            # `l` is properties_public: `listing_id` is the canonical advert (migration 561).
            "SELECT %(subscription_id)s, 'watchdog', l.property_id, l.listing_id, "
            "       'new', l.price_czk, "
            "       'wd:' || %(subscription_id)s || ':new:' || l.property_id::text "
            "FROM properties_public l "
            f"WHERE {' AND '.join(where)} "
            "ON CONFLICT (dedupe_key) DO NOTHING",
            params,
        )
        return cur.rowcount or 0


def match_once(conn: "psycopg.Connection") -> dict[str, int]:
    """One pass of the matcher. Returns counters useful for logging.

    Called by the lifespan loop (tests call it directly). Idempotent against the
    `dedupe_key` UNIQUE (`wd:{sub}:new:{property_id}` — once ever per
    property); emits change_kind='new'.

    Property grain (Slice 2b): the matcher walks `properties_public`, so a
    property listed on several portals fires once, not once per source.

    Per-subscription cursor model (migration 065). Each subscription
    has its own `last_matched_first_seen_at`; the matcher considers
    properties with `first_seen_at > cursor` for that subscription only,
    then advances the cursor to the max first_seen_at of the evaluated
    window. New watchdogs default the cursor to `now() - 24h` so the
    feed shows immediate backfill matches rather than sitting empty
    until the next scrape lands.

    Images-first gate (`notifications_new_requires_image`, default on): the
    dispatch INSERTs additionally require `_IMAGE_GATE_SQL`, so a brand-new
    property with pending image downloads is HELD, not dispatched. The cursor
    deliberately advances past held properties (the window query carries NO
    gate — one photoless CDN must never stall the whole feed); instead every
    pass re-scans a lookback window of already-passed properties
    (`first_seen_at <= cursor AND > now() - lookback`) under the same gate, so
    a held property is dispatched on the first pass after its images land (or
    its timeout expires) — the dedupe_key makes that re-scan idempotent. The
    lookback exceeds the timeout by construction, so a release can't slip out
    of the window; it also runs when the forward window is empty, so a held
    property is never stranded waiting for unrelated new inventory.
    """
    settings = _load_matcher_settings(conn)
    gate = _load_image_gate_settings(conn)

    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, filter_spec, last_matched_first_seen_at "
            "FROM notification_subscriptions "
            "WHERE is_active = true"
        )
        sub_rows = cur.fetchall()

    total_inserted = 0
    total_lookback_inserted = 0
    total_listings_in_window = 0
    cursors_advanced = 0
    # A spec that cannot even be rendered mutes its watchdog every pass; counted, so the
    # loop's INFO line says so instead of one LOG per subscription nobody reads.
    failed = 0

    for sub_id, raw_spec, cursor_ts in sub_rows:
        try:
            spec = WatchdogFilterSpec(**(raw_spec or {}))
        except Exception as exc:  # noqa: BLE001 — bad spec, skip but keep loop alive
            LOG.warning(
                "matcher: subscription %s has invalid filter_spec: %s",
                sub_id, exc,
            )
            failed += 1
            continue

        # One failing subscription must not zero the whole feed. The
        # connection is autocommit, so a raised execute aborts only that
        # statement — no rollback needed — but we still isolate the rest of
        # the per-subscription body so an unexpected error (bad SQL from a
        # future spec field, a transient DB hiccup) skips just this
        # subscription and the others still match.
        try:
            where, params = _build_match_clauses(spec)
            # NEW-dispatch detection is keyed on arrival (first_seen_at), the
            # pre-migration-273 anchor restored now the publication gate is gone
            # (NEW DEDUP cutoff §3): nothing stamps published_at for ordinary new
            # properties anymore, so a published_at window would never fire. The
            # per-subscription cursor column (last_matched_first_seen_at) once again
            # holds what its name says.
            window_where = [*where, "l.first_seen_at > %(cursor)s"]
            params["cursor"] = cursor_ts
            joined_where = " AND ".join(window_where)

            # Phase 1: find the window upper bound (max first_seen_at of
            # the next batch of matching listings, capped at the operator
            # knob). Reading the max separately means we can advance the
            # cursor past listings even when the dedup constraint blocked
            # the dispatch insert (re-running the matcher won't re-evaluate
            # the same listings forever). NO image gate here: the cursor
            # must advance past held properties (the lookback re-scan below
            # re-considers them), or one photoless property would stall the
            # whole subscription.
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT max(first_seen_at), count(*) FROM ("
                    "  SELECT l.first_seen_at FROM properties_public l "
                    f"  WHERE {joined_where} "
                    "  ORDER BY l.first_seen_at ASC "
                    "  LIMIT %(window_size)s"
                    ") sub",
                    {**params, "window_size": settings.window_listings},
                )
                row = cur.fetchone()
            upper = row[0] if row and row[0] is not None else None
            listings_in_window = int(row[1]) if row and row[1] is not None else 0
            total_listings_in_window += listings_in_window

            gate_where: list[str] = []
            if gate.enabled:
                gate_where = [_IMAGE_GATE_SQL]
                params["image_timeout_minutes"] = gate.timeout_minutes
                params["image_zero_rows_floor_minutes"] = (
                    IMAGE_GATE_ZERO_ROWS_FLOOR_MINUTES
                )

            insert_params = {**params, "subscription_id": str(sub_id)}

            if upper is not None:
                # Phase 2: insert dispatches for matches in the window (held
                # back by the image gate when it is enabled).
                insert_where = [
                    *window_where, "l.first_seen_at <= %(upper)s", *gate_where,
                ]
                total_inserted += _insert_new_dispatches(
                    conn, insert_where, {**insert_params, "upper": upper},
                )

                # Phase 3: advance the cursor for this subscription. Done in a
                # separate UPDATE so a crash between INSERT and UPDATE only
                # costs us a re-scan of the same window on the next pass —
                # ON CONFLICT means no duplicate dispatches.
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE notification_subscriptions "
                        "SET last_matched_first_seen_at = %s "
                        "WHERE id = %s AND last_matched_first_seen_at < %s",
                        (upper, sub_id, upper),
                    )
                    if cur.rowcount:
                        cursors_advanced += 1

            if gate.enabled:
                # Phase 4: gate-pending re-scan. Properties the cursor already
                # passed while they were held re-enter here until released
                # (image stored / timeout / zero-rows floor); runs even on an
                # empty forward window so a held property is never stranded.
                # Already-dispatched properties in the lookback are no-ops via
                # the dedupe_key conflict.
                lookback_where = [
                    *where,
                    "l.first_seen_at <= %(cursor)s",
                    "l.first_seen_at > now()"
                    " - make_interval(mins => %(image_lookback_minutes)s)",
                    *gate_where,
                ]
                inserted = _insert_new_dispatches(
                    conn, lookback_where,
                    {
                        **insert_params,
                        "image_lookback_minutes": gate.lookback_minutes,
                    },
                )
                total_inserted += inserted
                total_lookback_inserted += inserted
        except Exception as exc:  # noqa: BLE001 — isolate one sub, keep loop alive
            LOG.exception(
                "matcher: subscription %s failed, skipping: %s", sub_id, exc,
            )
            failed += 1
            continue

    return {
        "subscriptions_evaluated": len(sub_rows),
        "subscriptions_failed": failed,
        "matches_inserted": total_inserted,
        "gate_lookback_inserted": total_lookback_inserted,
        "listings_in_window": total_listings_in_window,
        "cursors_advanced": cursors_advanced,
    }


def _recent_price_drops(
    conn: "psycopg.Connection", *, window_days: int,
) -> list[tuple[int, int, int, int]]:
    """Per-snapshot price decreases observed inside the window.

    Returns one `(property_id, snapshot_id, price_czk, prev_price_czk)` tuple
    PER in-window drop step — not one per property — so each genuine price cut
    is its own notification event (the per-snapshot dedup grain). Steps come from
    `listing_price_steps` (migration 559): `prev` is the SAME advert's previous
    priced snapshot, so a step never spans two adverts. Only the CANONICAL
    advert's steps since it became canonical count (`repr_since`, migration 561):
    the price an alert quotes is the price the property shows, and a merge,
    detach or delisting that hands the slot to another advert fires nothing.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT ps.property_id, ps.snapshot_id, ps.price_czk, ps.prev_price_czk "
            "FROM listing_price_steps ps "
            "JOIN properties p ON p.id = ps.property_id "
            " AND p.repr_listing_ref_id = ps.listing_id AND ps.scraped_at > p.repr_since "
            "WHERE ps.price_czk < ps.prev_price_czk "
            "  AND ps.scraped_at > now() - %(win)s::interval "
            "ORDER BY ps.property_id, ps.snapshot_id",
            {"win": f"{window_days} days"},
        )
        return [
            (int(r[0]), int(r[1]), int(r[2]), int(r[3]))
            for r in cur.fetchall()
        ]


def match_changes_once(conn: "psycopg.Connection") -> dict[str, int]:
    """One pass of the property-change matcher (Slice 2b).

    Emits `change_kind='price_drop'` dispatches for properties that had a
    price decrease observed within the lookback window
    (`notifications_price_drop_window_days`, default 2) AND match an active
    subscription's spec. Dedup grain is PER-SNAPSHOT (`dedupe_key` =
    `wd:{sub}:price_drop:{snapshot_id}`): each genuine price cut is its own
    event, so a property that keeps dropping fires once per drop — re-scans of
    the same window stay idempotent because the snapshot id is stable. Each
    dispatch carries its trigger provenance (snapshot id + new/previous price)
    so "why was I pinged" survives latest-wins. Reuses `_build_match_clauses`
    against properties_public so Browse / Watchdog stay in lockstep on what
    every filter means.

    Distinct from `match_once` (`change_kind='new'`, fires on newly-seen
    properties via the first_seen_at cursor): this fires on EXISTING
    properties that change, so it has no cursor — the window bounds the scan
    and the dedupe_key makes re-scans idempotent. Meant to run on a ~daily
    cadence; the matcher loop gates it.
    """
    window_days = _read_int_setting(
        conn, "notifications_price_drop_window_days", default=2,
    )
    drops = _recent_price_drops(conn, window_days=window_days)
    if not drops:
        return {
            "subscriptions_evaluated": 0,
            "subscriptions_failed": 0,
            "price_drops_in_window": 0,
            "changes_inserted": 0,
        }

    drop_pids = [d[0] for d in drops]
    drop_sids = [d[1] for d in drops]
    drop_prices = [d[2] for d in drops]
    drop_prevs = [d[3] for d in drops]

    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, filter_spec FROM notification_subscriptions "
            "WHERE is_active = true"
        )
        sub_rows = cur.fetchall()

    total_inserted = 0
    failed = 0
    for sub_id, raw_spec in sub_rows:
        try:
            spec = WatchdogFilterSpec(**(raw_spec or {}))
        except Exception as exc:  # noqa: BLE001 — bad spec, skip but keep loop alive
            LOG.warning(
                "change matcher: subscription %s has invalid filter_spec: %s",
                sub_id, exc,
            )
            failed += 1
            continue

        try:
            where, params = _build_match_clauses(spec)
            joined_where = " AND ".join(where) if where else "true"
            params["subscription_id"] = str(sub_id)
            params["drop_pids"] = drop_pids
            params["drop_sids"] = drop_sids
            params["drop_prices"] = drop_prices
            params["drop_prevs"] = drop_prevs
            with conn.cursor() as cur:
                # One dispatch per (matching property x in-window drop snapshot).
                # The unnest JOIN restricts to dropped properties; the spec WHERE
                # matches against current property state (lockstep with Browse).
                cur.execute(
                    "INSERT INTO notification_dispatches "
                    "  (subscription_id, source_kind, property_id, listing_id, change_kind, "
                    "   trigger_snapshot_id, trigger_price_czk, prev_price_czk, dedupe_key) "
                    "SELECT %(subscription_id)s, 'watchdog', l.property_id, "
                    "       l.listing_id, 'price_drop', "
                    "       d.snapshot_id, d.price_czk, d.prev_price, "
                    "       'wd:' || %(subscription_id)s || ':price_drop:' || d.snapshot_id::text "
                    "FROM properties_public l "
                    "JOIN unnest("
                    "       %(drop_pids)s::bigint[], %(drop_sids)s::bigint[], "
                    "       %(drop_prices)s::int[], %(drop_prevs)s::int[]"
                    "     ) AS d(property_id, snapshot_id, price_czk, prev_price) "
                    "  ON d.property_id = l.property_id "
                    f"WHERE {joined_where} "
                    "ON CONFLICT (dedupe_key) DO NOTHING",
                    params,
                )
                total_inserted += cur.rowcount or 0
        except Exception as exc:  # noqa: BLE001 — isolate one sub, keep loop alive
            LOG.exception(
                "change matcher: subscription %s failed, skipping: %s",
                sub_id, exc,
            )
            failed += 1
            continue

    return {
        "subscriptions_evaluated": len(sub_rows),
        "subscriptions_failed": failed,
        "price_drops_in_window": len(drops),
        "changes_inserted": total_inserted,
    }


# --- collection monitor producer (Sprint C) -------------------------------


def _read_monitor_window_days(conn: "psycopg.Connection") -> int:
    return max(1, _read_int_setting(
        conn, "notifications_monitor_window_days", default=7,
    ))


# The monitored-membership CTE shared by every detector below: each property in
# a collection with monitoring_enabled. status='active' skips merged-away properties.
# `monitor_since` is the anchor that makes every detector fire only for changes
# AFTER monitoring began for this (collection, property) pair: the later of when
# the property was added (collection_properties.added_at) and when the collection
# last had monitoring turned on (collections.monitoring_enabled_at, migration 230).
# A pre-membership change never notifies.
_MONITORED_CTE = (
    "monitored AS ("
    "  SELECT cp.collection_id, p.id AS property_id, "
    "         p.repr_listing_ref_id, p.repr_since, "
    "         greatest(cp.added_at, coalesce(c.monitoring_enabled_at, cp.added_at)) "
    "           AS monitor_since "
    "  FROM collection_properties cp "
    "  JOIN collections c ON c.id = cp.collection_id AND c.monitoring_enabled = true "
    "  JOIN properties p ON p.id = cp.property_id AND p.status = 'active' "
    # Gate on the SURROGATE, not the legacy handle: post-Gate-2 a property whose
    # repr listing is a new non-sreality row has repr_listing_id NULL but
    # repr_listing_ref_id set — gating on the legacy column would silently drop
    # exactly those properties out of collection monitoring.
    "                   AND p.repr_listing_ref_id IS NOT NULL"
    ")"
)


def match_monitored_collections_once(conn: "psycopg.Connection") -> dict[str, int]:
    """One pass of the collection-monitor producer (Sprint C).

    For every property in a collection with `monitoring_enabled = true`, emit
    `source_kind='collection_monitor'` dispatches for the changes the operator
    watches: price_drop / price_rise (per-snapshot), inactive / reactivated
    (lifecycle), and new_source (a sibling listing appeared on another portal).
    Each dispatch carries `collection_id` (the source, required by source_ck) and
    a per-event `dedupe_key` (`cm:{collection}:{kind}:{discriminator}`) so re-runs
    over an overlapping window are idempotent and a 2nd real change is its own
    event. Set-based: one INSERT...SELECT per kind across ALL monitored
    collections — no per-collection Python loop.

    A property in N monitored collections fires N times (once per collection):
    the source_ck requires a collection_id, so monitoring is per-collection by
    design (the operator chose which collections alert).

    Every detector is anchored on `monitored.monitor_since` (the later of the
    property's `added_at` and the collection's `monitoring_enabled_at`), so a
    change that predates the operator starting to watch the property never
    notifies — only changes observed AFTER monitoring began do.

    `broker_change` is intentionally NOT emitted yet: there is no property-level
    broker nor a broker-change timestamp to key a stable, non-fragile event off
    (`listing_broker_public` is current-state only). The change_kind is reserved
    (migration 209); the detector lands when a broker-change signal exists. See
    docs/design/notifications-unified.md.
    """
    # Cheap early-out: nothing monitored, nothing to do.
    with conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM collections WHERE monitoring_enabled = true"
        )
        row = cur.fetchone()
    if not row or not row[0]:
        return {"monitored_collections": 0, "events_inserted": 0}
    monitored_collections = int(row[0])

    win = f"{_read_monitor_window_days(conn)} days"
    inserted = 0

    with conn.cursor() as cur:
        # 1+2) price_drop / price_rise — the canonical advert's own price steps
        # since it became canonical (`listing_price_steps`, migration 559;
        # `repr_since`, migration 561), replicated per monitored collection so each
        # collection alerts independently. Snapshot grain == the watchdog's.
        cur.execute(
            f"WITH {_MONITORED_CTE} "
            "INSERT INTO notification_dispatches "
            "  (source_kind, collection_id, property_id, listing_id, change_kind, "
            "   trigger_snapshot_id, trigger_price_czk, prev_price_czk, dedupe_key) "
            "SELECT 'collection_monitor', m.collection_id, st.property_id, st.listing_id, "
            "       CASE WHEN st.price_czk < st.prev_price_czk "
            "            THEN 'price_drop' ELSE 'price_rise' END, "
            "       st.snapshot_id, st.price_czk, st.prev_price_czk, "
            "       'cm:' || m.collection_id::text || ':' || "
            "         CASE WHEN st.price_czk < st.prev_price_czk "
            "              THEN 'price_drop' ELSE 'price_rise' END || "
            "         ':' || st.snapshot_id::text "
            "FROM monitored m "
            "JOIN listing_price_steps st ON st.listing_id = m.repr_listing_ref_id "
            "WHERE st.scraped_at > now() - %(win)s::interval "
            "  AND st.scraped_at > m.monitor_since AND st.scraped_at > m.repr_since "
            "ON CONFLICT (dedupe_key) DO NOTHING",
            {"win": win},
        )
        inserted += cur.rowcount or 0

        # 3) inactive — the property went inactive (every listing delisted). No
        # snapshot; key on the epoch of the latest child inactive_at (set on the
        # flip, cleared on reactivation, so a re-inactivation is a fresh event).
        cur.execute(
            f"WITH {_MONITORED_CTE}, "
            "gone AS ("
            "  SELECT m.collection_id, m.property_id, m.repr_listing_ref_id, "
            "         max(l.inactive_at) AS inactive_at "
            "  FROM monitored m "
            "  JOIN properties p ON p.id = m.property_id AND p.is_active = false "
            "  JOIN listings l ON l.property_id = m.property_id "
            "  GROUP BY m.collection_id, m.property_id, m.repr_listing_ref_id, "
            "           m.monitor_since "
            "  HAVING max(l.inactive_at) > now() - %(win)s::interval "
            "     AND max(l.inactive_at) > m.monitor_since"
            ") "
            "INSERT INTO notification_dispatches "
            "  (source_kind, collection_id, property_id, listing_id, change_kind, dedupe_key) "
            "SELECT 'collection_monitor', g.collection_id, g.property_id, "
            "       g.repr_listing_ref_id, 'inactive', "
            "       'cm:' || g.collection_id::text || ':inactive:' || g.property_id::text || ':' || "
            "         floor(extract(epoch FROM g.inactive_at))::bigint::text "
            "FROM gone g "
            "ON CONFLICT (dedupe_key) DO NOTHING",
            {"win": win},
        )
        inserted += cur.rowcount or 0

        # 4) reactivated — a property we ALREADY alerted as inactive came back
        # active. The prior 'inactive' dispatch is the durable "was dead" marker
        # (listings.inactive_at is cleared on reactivation, so we can't read it).
        # The prior 'inactive' dispatch must itself postdate monitor_since (the
        # LATERAL ON below), so a reactivation can only fire for a property that
        # went inactive while being monitored — never off a pre-fix, ungated
        # 'inactive' dispatch that predates monitoring.
        cur.execute(
            f"WITH {_MONITORED_CTE}, "
            "back AS ("
            "  SELECT m.collection_id, m.property_id, m.repr_listing_ref_id, "
            "         nd.dispatched_at AS inactive_at "
            "  FROM monitored m "
            "  JOIN properties p ON p.id = m.property_id "
            "       AND p.is_active = true "
            "       AND p.last_seen_at > now() - %(win)s::interval "
            "  JOIN LATERAL ("
            "    SELECT dispatched_at FROM notification_dispatches "
            "    WHERE source_kind = 'collection_monitor' "
            "      AND collection_id = m.collection_id "
            "      AND property_id = m.property_id "
            "      AND change_kind = 'inactive' "
            "    ORDER BY dispatched_at DESC LIMIT 1"
            "  ) nd ON p.last_seen_at > nd.dispatched_at "
            "       AND nd.dispatched_at > m.monitor_since "
            "  WHERE NOT EXISTS ("
            "    SELECT 1 FROM notification_dispatches r "
            "    WHERE r.source_kind = 'collection_monitor' "
            "      AND r.collection_id = m.collection_id "
            "      AND r.property_id = m.property_id "
            "      AND r.change_kind = 'reactivated' "
            "      AND r.dispatched_at > nd.dispatched_at"
            "  )"
            ") "
            "INSERT INTO notification_dispatches "
            "  (source_kind, collection_id, property_id, listing_id, change_kind, dedupe_key) "
            "SELECT 'collection_monitor', b.collection_id, b.property_id, "
            "       b.repr_listing_ref_id, 'reactivated', "
            "       'cm:' || b.collection_id::text || ':reactivated:' || b.property_id::text || ':' || "
            "         floor(extract(epoch FROM b.inactive_at))::bigint::text "
            "FROM back b "
            "ON CONFLICT (dedupe_key) DO NOTHING",
            {"win": win},
        )
        inserted += cur.rowcount or 0

        # 5) new_source — a sibling listing introduced a NEW portal to the
        # property (a cross-source sighting the dedup engine grouped in). Key on
        # the introducing listing id. Limitation: fires only when that listing's
        # first_seen_at is inside the window (the common "a portal just listed
        # it" case); a merge of an OLD listing won't fire.
        cur.execute(
            f"WITH {_MONITORED_CTE}, "
            "src AS ("
            "  SELECT m.collection_id, m.monitor_since, l.property_id, l.sreality_id, "
            "         l.id AS listing_id, l.first_seen_at, "
            "         row_number() OVER (PARTITION BY m.collection_id, l.property_id, l.source "
            "                            ORDER BY l.first_seen_at, l.sreality_id) AS rn, "
            "         min(l.first_seen_at) OVER (PARTITION BY m.collection_id, l.property_id) AS prop_first "
            "  FROM monitored m "
            "  JOIN listings l ON l.property_id = m.property_id"
            "), "
            # count(DISTINCT ...) OVER (...) is unsupported in Postgres; exactly
            # one row per source has rn=1, so a filtered count over the property
            # partition IS the distinct-source count.
            "src_counted AS ("
            "  SELECT src.*, "
            "         count(*) FILTER (WHERE rn = 1) "
            "           OVER (PARTITION BY collection_id, property_id) AS n_sources "
            "  FROM src"
            ") "
            "INSERT INTO notification_dispatches "
            "  (source_kind, collection_id, property_id, listing_id, change_kind, dedupe_key) "
            "SELECT 'collection_monitor', src.collection_id, src.property_id, "
            "       src.listing_id, 'new_source', "
            # dedupe_key is NOT NULL, and `||` yields NULL if ANY operand is NULL:
            # post-Gate-2 a new non-sreality listing (sreality_id NULL) would make
            # the whole key NULL and abort the ENTIRE collection-monitor pass with a
            # not-null violation — not just skip that row. COALESCE onto the
            # surrogate keeps every EXISTING key byte-identical (so nothing already
            # dispatched can re-fire) while giving NULL-sreality rows a stable,
            # collision-free key of their own ('l' prefix disambiguates the two
            # id-spaces).
            "       'cm:' || src.collection_id::text || ':new_source:' || "
            "         coalesce(src.sreality_id::text, 'l' || src.listing_id::text) "
            "FROM src_counted src "
            "WHERE src.rn = 1 AND src.n_sources >= 2 "
            "  AND src.first_seen_at > src.prop_first "
            "  AND src.first_seen_at > now() - %(win)s::interval "
            "  AND src.first_seen_at > src.monitor_since "
            "ON CONFLICT (dedupe_key) DO NOTHING",
            {"win": win},
        )
        inserted += cur.rowcount or 0

    return {
        "monitored_collections": monitored_collections,
        "events_inserted": inserted,
    }


def _read_monitor_interval_seconds() -> int:
    conn = scraper_db.connect()
    try:
        return _read_int_setting(
            conn, "notifications_monitor_interval_seconds", default=86400,
        )
    finally:
        with contextlib.suppress(Exception):
            conn.close()


def _match_monitored_in_thread() -> dict[str, int]:
    conn = scraper_db.connect()
    try:
        return match_monitored_collections_once(conn)
    finally:
        with contextlib.suppress(Exception):
            conn.close()


_MATCHER_LEASE_TTL = "15 minutes"

_TRY_MATCHER_LEASE_SQL = """
    UPDATE notification_matcher_lease
       SET holder = %(holder)s, expires_at = now() + %(lease)s::interval
     WHERE id = 1
       AND (holder IS NULL OR expires_at < now() OR holder = %(holder)s)
    RETURNING 1
"""

_RELEASE_MATCHER_LEASE_SQL = """
    UPDATE notification_matcher_lease
       SET holder = NULL, expires_at = NULL
     WHERE id = 1 AND holder = %(holder)s
"""


def _new_matcher_holder() -> str:
    return f"matcher:{uuid.uuid4()}"


def _try_matcher_lease(conn: "psycopg.Connection", holder: str) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            _TRY_MATCHER_LEASE_SQL, {"holder": holder, "lease": _MATCHER_LEASE_TTL}
        )
        return cur.fetchone() is not None


def _acquire_matcher_lease(holder: str) -> bool:
    """Claim the single-runner lease (migration 366) so N API replicas don't fan
    out N× market-wide scans. Fail-OPEN (return True → run the pass) on any error,
    incl. the table not existing during a rollout window: the per-event dedupe_key
    keeps a duplicated pass harmless (rule #16), and failing open preserves the
    pre-lease single-replica behaviour."""
    try:
        conn = scraper_db.connect()
    except Exception as exc:  # noqa: BLE001
        LOG.warning("matcher lease: connect failed, running unguarded: %s", exc)
        return True
    try:
        return _try_matcher_lease(conn, holder)
    except Exception as exc:  # noqa: BLE001
        LOG.warning("matcher lease: acquire failed, running unguarded: %s", exc)
        return True
    finally:
        with contextlib.suppress(Exception):
            conn.close()


def _release_matcher_lease(holder: str) -> None:
    """Best-effort release so a graceful shutdown hands the lease over at once (a
    crash instead waits out the ~15-min expiry)."""
    try:
        conn = scraper_db.connect()
    except Exception:  # noqa: BLE001
        return
    try:
        with conn.cursor() as cur:
            cur.execute(_RELEASE_MATCHER_LEASE_SQL, {"holder": holder})
    except Exception:  # noqa: BLE001
        pass
    finally:
        with contextlib.suppress(Exception):
            conn.close()


async def matcher_loop(stop_event: asyncio.Event) -> None:
    """The forever-running async matcher. Reads its own DB connection
    each pass; idle waits respect `notifications_matcher_interval_seconds`
    so an operator who edits the row only needs to wait for the current
    sleep to elapse.

    Each pass runs the new-listing matcher (`match_once`). The property-change
    matcher (`match_changes_once`) runs at most once per
    `notifications_change_match_interval_seconds` (default 86400 = daily); the
    gate is in-memory, so a process restart simply runs it once on the next
    pass — harmless, the UNIQUE constraint dedups.
    """
    LOG.info("notification matcher loop starting")
    # monotonic timestamp of the last property-change pass; 0 => run on the
    # first pass after (re)start.
    last_change_run = 0.0
    # same for the collection-monitor producer.
    last_monitor_run = 0.0
    # per-process holder id for the single-runner lease (migration 366).
    holder = _new_matcher_holder()
    while not stop_event.is_set():
        # Read interval each pass so live edits to app_settings take
        # effect without a restart.
        interval = 300
        try:
            interval = await asyncio.to_thread(_read_interval_seconds)
        except Exception as exc:  # noqa: BLE001
            LOG.warning("matcher: failed to read interval: %s", exc)

        if interval <= 0:
            LOG.info("notification matcher disabled (interval=0); idling")
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=60.0)
            except asyncio.TimeoutError:
                continue
            else:
                break

        # Single-runner guard (migration 366): only the lease holder runs the
        # producer passes this round, so N API replicas don't fan out N×
        # market-wide scans. The holder re-acquires each pass (renewal) so it
        # stays sticky; a crashed holder's lease expires (~15 min) and another
        # replica takes over. Fail-open lives inside _acquire_matcher_lease.
        if not await asyncio.to_thread(_acquire_matcher_lease, holder):
            LOG.debug("notification matcher: lease held elsewhere; skipping pass")
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=float(interval))
            except asyncio.TimeoutError:
                continue
            else:
                break

        try:
            stats = await asyncio.to_thread(_match_once_in_thread)
            if (stats.get("matches_inserted", 0) > 0
                    or stats.get("subscriptions_failed", 0) > 0):
                LOG.info("notification matcher: %s", stats)
            else:
                LOG.debug("notification matcher: %s", stats)
        except Exception as exc:  # noqa: BLE001
            LOG.exception("notification matcher pass failed: %s", exc)

        # Property-change matcher, gated to ~daily. interval<=0 disables it.
        change_interval = 86400
        try:
            change_interval = await asyncio.to_thread(_read_change_interval_seconds)
        except Exception as exc:  # noqa: BLE001
            LOG.warning("change matcher: failed to read interval: %s", exc)
        if change_interval > 0 and (time.monotonic() - last_change_run) >= change_interval:
            try:
                cstats = await asyncio.to_thread(_match_changes_in_thread)
                last_change_run = time.monotonic()
                if (cstats.get("changes_inserted", 0) > 0
                        or cstats.get("subscriptions_failed", 0) > 0):
                    LOG.info("notification change matcher: %s", cstats)
                else:
                    LOG.debug("notification change matcher: %s", cstats)
            except Exception as exc:  # noqa: BLE001
                LOG.exception("notification change matcher pass failed: %s", exc)

        # Collection-monitor producer (Sprint C), gated to its own cadence
        # (`notifications_monitor_interval_seconds`, default daily). Same
        # in-memory monotonic gate as the change matcher; a restart re-runs it
        # once (the dedupe_key UNIQUE absorbs the overlap).
        monitor_interval = 86400
        try:
            monitor_interval = await asyncio.to_thread(_read_monitor_interval_seconds)
        except Exception as exc:  # noqa: BLE001
            LOG.warning("monitor matcher: failed to read interval: %s", exc)
        if monitor_interval > 0 and (time.monotonic() - last_monitor_run) >= monitor_interval:
            try:
                mstats = await asyncio.to_thread(_match_monitored_in_thread)
                last_monitor_run = time.monotonic()
                if mstats.get("events_inserted", 0) > 0:
                    LOG.info("collection monitor matcher: %s", mstats)
                else:
                    LOG.debug("collection monitor matcher: %s", mstats)
            except Exception as exc:  # noqa: BLE001
                LOG.exception("collection monitor matcher pass failed: %s", exc)

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=float(interval))
        except asyncio.TimeoutError:
            continue
        else:
            break
    with contextlib.suppress(Exception):
        await asyncio.to_thread(_release_matcher_lease, holder)
    LOG.info("notification matcher loop stopped")


def _read_interval_seconds() -> int:
    conn = scraper_db.connect()
    try:
        return _read_int_setting(
            conn, "notifications_matcher_interval_seconds", default=300,
        )
    finally:
        with contextlib.suppress(Exception):
            conn.close()


def _read_change_interval_seconds() -> int:
    conn = scraper_db.connect()
    try:
        return _read_int_setting(
            conn, "notifications_change_match_interval_seconds", default=86400,
        )
    finally:
        with contextlib.suppress(Exception):
            conn.close()


def _match_once_in_thread() -> dict[str, int]:
    conn = scraper_db.connect()
    try:
        return match_once(conn)
    finally:
        with contextlib.suppress(Exception):
            conn.close()


def _match_changes_in_thread() -> dict[str, int]:
    conn = scraper_db.connect()
    try:
        return match_changes_once(conn)
    finally:
        with contextlib.suppress(Exception):
            conn.close()
