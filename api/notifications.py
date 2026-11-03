"""New-listing notifications ("Watchdog") backend (Phase U2.7).

Two responsibilities:

1. CRUD over `notification_subscriptions` — the operator's saved
   filter specs. Each row holds a name + a `WatchdogFilterSpec` JSONB
   blob whose field names are filter-registry ids (`toolkit/filter_registry.py`).

2. The three producer steps (`match_once`, `match_changes_once`, `match_lifecycle_once`),
   run every 300 s by the realtime worker's `notifications` lane, their one caller and
   clock (the API runs none since W2b). They write one `notification_dispatches` row per
   event over fixed windows (a watchdog's 'new' over properties first seen in the last
   24 h), a watchdog only for the kinds it tells (`notify_kinds`); the one identity index
   (`EVENT_IDENTITY`, migration 653) makes every re-run idempotent. A watchdog tells a
   property only once it is PUBLISHED — settled by the pipeline or 6 h old, the rule
   Browse serves by (`toolkit/publication.py`, migrations 668/669).

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

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Annotated, Any, Literal, get_args

from fastapi import HTTPException
from pydantic import AfterValidator, BaseModel, field_validator, model_validator

from api.cursor import decode_cursor, encode_cursor
from api.location_filter import DistrictChip, MapBounds, MapCircle, area_where, district_where, one_area
from location_data.claims_common import served_location_predicate
from toolkit.filter_compiler import PROPERTIES_GRAIN, compile_filter_where
from toolkit.property_carriers import NOTIFICATION_EVENTS_SHARED_LOCK_SQL
from toolkit.publication import published_property_predicate, visible_since_sql

if TYPE_CHECKING:
    import psycopg

LOG = logging.getLogger(__name__)

# The three producers (rule 16; CHECK notification_dispatches_source_ck, narrowed by migration 656,
# which deleted the system_health history: ruling R2 (b)).
SourceKind = Literal["watchdog", "collection_monitor", "share"]
SourceKindFilter = Literal[SourceKind, "all"]

# The six property events the producers detect, in the order a watchdog stores the ones it tells
# (migration 720, `notify_kinds`). A collection tells every kind but 'new': a property it holds is
# never new to it.
WatchdogKind = Literal["new", "price_drop", "price_rise", "inactive", "reactivated", "new_source"]
WATCHDOG_KINDS: tuple[str, ...] = get_args(WatchdogKind)
PRICE_KINDS = ("price_drop", "price_rise")
LIFECYCLE_KINDS = ("inactive", "reactivated", "new_source")


def normalize_notify_kinds(kinds: list[str]) -> list[str]:
    """The kinds as a watchdog stores them: each once, in WATCHDOG_KINDS order. Refused when empty, and
    'reactivated' without 'inactive': Back on the market answers a Left the market the same watchdog told."""
    out = [k for k in WATCHDOG_KINDS if k in kinds]
    if not out:
        raise ValueError("choose at least one event to be notified about")
    if "reactivated" in out and "inactive" not in out:
        raise ValueError("'reactivated' (Back on the market) needs 'inactive' (Left the market)")
    return out


NotifyKinds = Annotated[list[WatchdogKind], AfterValidator(normalize_notify_kinds)]

# ONE event identity (program § 3.2, migration 653): the columns of notification_dispatches_identity_uq
# in index order, NULLS NOT DISTINCT over the rows that are not a marked repeat. Every producer INSERT
# and the share grant conflict on it; the merge carrier (toolkit/property_carriers.Dispatches) marks a
# retired property's alert a duplicate on it. The account trigger (migrations 364, 641) stamps
# account_id BEFORE INSERT, so the conflict check sees it. Tests pin all three to the migration.
EVENT_IDENTITY: tuple[str, ...] = (
    "account_id", "source_kind", "subscription_id", "collection_id", "change_kind", "property_id",
    "event_ref", "actor_account_id",
)
ON_EVENT_CONFLICT = f"ON CONFLICT ({', '.join(EVENT_IDENTITY)}) WHERE duplicate_of IS NULL DO NOTHING"


# --- filter spec ----------------------------------------------------------


class WatchdogFilterSpec(BaseModel):
    """JSON shape persisted in `notification_subscriptions.filter_spec`.

    Field names are registry ids (`toolkit/filter_registry.py`) for what the
    Browse sidebar exposes; the matcher converts this into a parameterised
    WHERE clause via `_build_match_clauses`. Every field defaults to
    `None` so a watchdog can be as wide ("any apartment for rent") or
    narrow ("furnished 3+kk in Praha 2 under 30 000 Kč near these
    coordinates") as the operator wants.

    The map area is optional: a box (`bounds`, Browse's viewport) or a true
    circle (`circle`, Browse's centre + radius), never both, so a watchdog
    saved from Browse watches the rows Browse showed. Place chips are an
    independent AND; null everywhere = no restriction.
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

    # The map area (rule 16): a box or a circle, tested by the predicate Browse's list and both stats RPCs test.
    bounds: MapBounds | None = None
    circle: MapCircle | None = None

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
    energy_rating_match: list[str] | None = None
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

    @model_validator(mode="after")
    def _one_area(self) -> "WatchdogFilterSpec":
        one_area(self.bounds, self.circle)
        return self


def _build_match_clauses(
    spec: WatchdogFilterSpec,
) -> tuple[list[str], dict[str, Any]]:
    """Render the filter spec as parameterised WHERE clauses.

    The matcher prepends a watermark / window clause; this helper owns
    the spec-derived part only. Spec-derived clauses come from
    `compile_filter_where(…, PROPERTIES_GRAIN)`; only the served and published
    predicates, the map box and the place chips are rendered here.
    """
    # THE CONSUMER RULE (W5, operator ruling 2026-09-13), unconditional and first.
    # Browse inherits it from `browse_projection` (migration 512); the matcher cannot,
    # because it reads `properties_public` — a DETAIL-by-id surface that deliberately
    # keeps serving an unresolved listing to a direct link. So the ONE definition is
    # rendered here instead, keyed on the property's LOCATION ad
    # (`properties_public.location_listing_ref_id`, migration 636), as browse_projection
    # keys it. Without it a watchdog would fire on a listing nobody can find in Browse,
    # and "matches" would mean two different things.
    # THE PUBLICATION RULE (rule 16, operator ruling 2026-10-23), unconditional and second,
    # for the same reason: browse_projection carries it (migration 669), properties_public
    # does not, so both producers render `toolkit/publication.py` here on the property.
    where: list[str] = [served_location_predicate("l.location_listing_ref_id"),
                        published_property_predicate("l.property_id")]
    params: dict[str, Any] = {}

    # The map area, on the lat/lng properties_public projects off the location ad's
    # point: the predicate Browse's list and both stats RPCs test (rule 16).
    b_where, b_params = area_where(spec.bounds, spec.circle, alias="l")
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
    notify_kinds: list[str]
    dispatch_count: int


_SUB_COLS = "id, name, filter_spec, is_active, created_at, updated_at, notify_kinds"


def _row_to_sub(row: tuple[Any, ...], dispatch_count: int) -> SubscriptionRow:
    return SubscriptionRow(
        id=str(row[0]),
        name=row[1],
        filter_spec=row[2] or {},
        is_active=bool(row[3]),
        created_at=row[4].isoformat() if row[4] else "",
        updated_at=row[5].isoformat() if row[5] else "",
        notify_kinds=list(row[6]),
        dispatch_count=dispatch_count,
    )


# How far back a watchdog's 'new' looks (program § 3.3); the retraction below reads it too.
NEW_WINDOW = "24 hours"

# VISIBLE (program § 3.6): what an account sees of its alerts, ONE fragment over
# `notification_dispatches d`, carried by every in-app read, count and update (the feed and its
# total, the unread count, mark-all-seen, each watchdog's count) and by W4's push fan-out, claim,
# badge and recall (api/push_delivery.py, by reference). REACHABLE's three arms, each answering
# the same under RLS and on the service role:
#   - not a marked repeat (`duplicate_of`, written by a merge since migration 653);
#   - not about a property the EVENT'S OWN account dismissed (migration 536's active row, probed
#     on its (property_id, account_id) index: the service role bypasses RLS, so the account is
#     named, never left to the caller's policy);
#   - not from a retired watchdog (a delete retires it, F21; its rows stay for analysis).
# And not RETRACTED (operator ruling 2026-10-23, Q5): a 'new' is hidden exactly when the property
# it now names was first seen more than NEW_WINDOW before the alert was told. No 'new' is told so
# (the producer's window), but a merge re-points alerts to the oldest property, so a 'new' the 6 h
# cap released for a duplicate hides itself once the engine merges it. Derived, no column: it
# survives a split (a detach leaves alerts where they are), and a survivor first seen 6 to 24 h
# before the alert keeps it (a known residue). The interval is a literal, so every statement
# embedding the fragment keeps its own parameters. A retraction withdraws an alert from attention —
# feed, badge, push — never from a direct tap: the tap path (mark_dispatch_seen, _fetch_dispatch)
# reads REACHABLE, so a push the user was already sent opens the property the alert now names (a
# merge survives a tap). Detection still writes every row — the `reactivated` detector reads prior
# ones — so an undo brings it back intact; a share (property_id NULL) is never dismissed nor
# retracted.
REACHABLE_SQL = (
    "d.duplicate_of IS NULL "
    "AND NOT EXISTS (SELECT 1 FROM property_dismissals pd "
    "WHERE pd.property_id = d.property_id AND pd.account_id = d.account_id "
    "AND pd.lifted_at IS NULL) "
    "AND (d.subscription_id IS NULL OR EXISTS (SELECT 1 FROM notification_subscriptions ws "
    "WHERE ws.id = d.subscription_id AND ws.retired_at IS NULL))"
)
RETRACTED_SQL = (
    "(d.change_kind = 'new' AND EXISTS (SELECT 1 FROM properties rp WHERE rp.id = d.property_id"
    f" AND rp.first_seen_at <= d.dispatched_at - interval '{NEW_WINDOW}'))"
)
VISIBLE_SQL = f"{REACHABLE_SQL} AND NOT {RETRACTED_SQL}"


def list_subscriptions(
    conn: "psycopg.Connection",
    *,
    include_inactive: bool = True,
) -> list[dict[str, Any]]:
    # A retired watchdog (deleted, F21) leaves the list; its rows stay, hidden by VISIBLE_SQL.
    where = "WHERE retired_at IS NULL" + ("" if include_inactive else " AND is_active = true")
    sql = (
        f"SELECT {_SUB_COLS}, "
        "  (SELECT count(*) FROM notification_dispatches d "
        "     WHERE d.subscription_id = notification_subscriptions.id "
        f"    AND {VISIBLE_SQL}) AS dispatch_count "
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
            f"    WHERE d.subscription_id = %s AND {VISIBLE_SQL}) AS dispatch_count "
            "FROM notification_subscriptions WHERE id = %s AND retired_at IS NULL",
            (subscription_id, subscription_id),
        )
        row = cur.fetchone()
    if row is None:
        return None
    return _row_to_sub(row[:-1], int(row[-1] or 0)).__dict__


def _refuse_unrenderable(spec: WatchdogFilterSpec | None) -> None:
    """422 for a spec the matcher's one compiler refuses (a retired filter, an agenda violation): stored, it would mute its
    watchdog every pass. Here, not at a route, so every caller of a write gets it."""
    if spec is None:
        return
    try:
        check_filter_spec(spec)
    except SPEC_REJECTIONS as exc:
        raise HTTPException(status_code=422, detail=f"filter_spec: {exc}") from exc


def create_subscription(
    conn: "psycopg.Connection",
    *,
    name: str,
    filter_spec: WatchdogFilterSpec,
    is_active: bool = True,
    notify_kinds: list[str] | None = None,
    account_id: uuid.UUID | str,
) -> dict[str, Any]:
    # account_id is NOT NULL (migration 364) and REQUIRED here (no default): the
    # route resolves exactly one via tenant_pool.require_account_id, which 400s
    # when the caller has none, so this INSERT can never bind a silent NULL.
    # notify_kinds left unnamed takes the column default (migration 720).
    _refuse_unrenderable(filter_spec)
    kinds = (notify_kinds,) if notify_kinds is not None else ()
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO notification_subscriptions "
            "  (name, filter_spec, is_active, account_id, notify_kinds) "
            f"VALUES (%s, %s::jsonb, %s, %s, {'%s' if kinds else 'DEFAULT'}) RETURNING id",
            (
                name,
                json.dumps(filter_spec.model_dump()),
                is_active,
                account_id,
                *kinds,
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
    notify_kinds: list[str] | None = None,
) -> dict[str, Any] | None:
    _refuse_unrenderable(filter_spec)
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
    if notify_kinds is not None:
        sets.append("notify_kinds = %s")
        params.append(notify_kinds)
    if not sets:
        return get_subscription(conn, subscription_id)
    params.append(subscription_id)
    with conn.cursor() as cur:
        cur.execute(
            f"UPDATE notification_subscriptions SET {', '.join(sets)} "
            "WHERE id = %s AND retired_at IS NULL",
            params,
        )
        if cur.rowcount == 0:
            return None
    return get_subscription(conn, subscription_id)


def delete_subscription(
    conn: "psycopg.Connection", subscription_id: str,
) -> bool:
    """Retire, never delete (F21; program § 3.2): the watchdog stops matching (the producers
    read `retired_at IS NULL`), leaves list and get, and its alerts leave every read through
    VISIBLE_SQL while the rows stay for analysis — the FK's cascade no longer erases them. True
    when a live watchdog was retired; a missing or already retired one is the route's 404."""
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE notification_subscriptions SET retired_at = now(), is_active = false "
            "WHERE id = %s AND retired_at IS NULL",
            (subscription_id,),
        )
        return cur.rowcount > 0


def new_subscription_undo_check(
    conn: "psycopg.Connection", subscription_id: str, applied_at: datetime,
) -> tuple[str | None, int]:
    """Why Undo may not retire the watchdog a proposal created, in the user's words, or None; and how many alerts it has told."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT retired_at, updated_at, "
            "  (SELECT count(*) FROM notification_dispatches d WHERE d.subscription_id = %s "
            f"    AND {VISIBLE_SQL}) "
            "FROM notification_subscriptions WHERE id = %s",
            (subscription_id, subscription_id),
        )
        row = cur.fetchone()
    if row is None or row[0] is not None:
        return "You already deleted this watchdog.", 0
    if row[1] != applied_at:
        return "You changed this watchdog, so Undo leaves it to you.", 0
    return None, int(row[2])


def undo_new_subscription(conn: "psycopg.Connection", subscription_id: str, applied_at: datetime) -> None:
    """Retire the watchdog a proposal created, or 409 with why not; the row lock stops an edit landing between the two."""
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM notification_subscriptions WHERE id = %s FOR UPDATE", (subscription_id,))
    refusal, _ = new_subscription_undo_check(conn, subscription_id, applied_at)
    if refusal:
        raise HTTPException(409, refusal)
    delete_subscription(conn, subscription_id)


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
# dashboard>`, migration 652); `share_ref` its id, off the share's own columns (api/shares.py),
# never a key: the collection id, the pipeline owner (the actor), the dashboard id (event_ref).
# A share stores no text (F17, R11; `message` dropped by migration 656): the bell's sentence is the
# SPA's, from `actor_email` (the sharer, through share_party_emails() exactly as
# api/shares.party_label spells it, so it is NULL once no share joins the two accounts, unless the
# sharer stands in the reader's own delegated-edit ledger: that arm keeps an actor's address for the
# owner after a revoke, migration 641; a detector's row has no actor and never calls it),
# `share_name` (the collection's or dashboard's live name, NULL once the caller cannot read it) and
# `share_mode` (a pipeline share's mode, its event_ref).
_DISPATCH_SELECT = (
    "d.id, d.source_kind, "
    "d.subscription_id, s.name AS subscription_name, "
    "d.collection_id, c.name AS collection_name, "
    "d.listing_id, d.property_id, d.change_kind, "
    "CASE WHEN d.source_kind = 'share' THEN split_part(d.change_kind, '_', 2) END AS share_kind, "
    "CASE d.change_kind WHEN 'shared_collection' THEN d.collection_id::text "
    "  WHEN 'shared_pipeline' THEN d.actor_account_id::text "
    "  WHEN 'shared_dashboard' THEN d.event_ref END AS share_ref, "
    "CASE WHEN d.actor_account_id IS NOT NULL THEN "
    "  (SELECT e.email FROM share_party_emails() e WHERE e.account_id = d.actor_account_id) END AS actor_email, "
    "CASE d.change_kind WHEN 'shared_collection' THEN c.name "
    "  WHEN 'shared_dashboard' THEN (SELECT sd.name FROM stats_dashboards sd WHERE sd.id::text = d.event_ref) "
    "  END AS share_name, "
    "CASE WHEN d.change_kind = 'shared_pipeline' THEN d.event_ref END AS share_mode, "
    "d.dispatched_at, d.seen_at, "
    "d.trigger_price_czk, d.prev_price_czk, "
    f"{_LISTING_PROJECTION}"
)

_DISPATCH_FROM = (
    "FROM notification_dispatches d "
    "LEFT JOIN notification_subscriptions s ON s.id = d.subscription_id "
    "LEFT JOIN collections c ON c.id = d.collection_id "
    # Surrogate join (R2): Phase A4's backfill gave every historical dispatch its
    # listing_id before migration 656 dropped the legacy sreality_id, so no row loses
    # its listing fields here. A plain equality keeps this an index lookup.
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
    where: list[str] = [VISIBLE_SQL]
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


# The row a tap on `dispatch_id` stands for: itself, or, for a row a merge marked a duplicate, the
# row it duplicates (the survivor's, which the carrier keeps flat: one hop). Under RLS a foreign id
# resolves to nothing.
_RESOLVE_DISPATCH_SQL = (
    "SELECT coalesce(d.duplicate_of, d.id) FROM notification_dispatches d WHERE d.id = %s"
)


def mark_dispatch_seen(
    conn: "psycopg.Connection", dispatch_id: str,
) -> dict[str, Any] | None:
    """Mark one reachable dispatch seen and return it. An id a merge later marked a duplicate
    resolves to the row it duplicates first, which is the one stamped and returned, so a tap on an
    old push lands on the survivor's alert; None (the route's 404) when the resolved row is missing
    or unreachable (REACHABLE_SQL: a retracted alert still opens)."""
    with conn.cursor() as cur:
        cur.execute(_RESOLVE_DISPATCH_SQL, (dispatch_id,))
        row = cur.fetchone()
        if row is None:
            return None
        target = str(row[0])
        cur.execute(
            "UPDATE notification_dispatches d SET seen_at = now() "
            f"WHERE d.id = %s AND d.seen_at IS NULL AND {REACHABLE_SQL}",
            (target,),
        )
    return _fetch_dispatch(conn, target)


def _fetch_dispatch(
    conn: "psycopg.Connection", dispatch_id: str,
) -> dict[str, Any] | None:
    sql = (
        f"SELECT {_DISPATCH_SELECT} "
        f"{_DISPATCH_FROM}"
        f"WHERE d.id = %s AND {REACHABLE_SQL}"
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
            f"WHERE d.seen_at IS NULL AND {VISIBLE_SQL}"
        )
        row = cur.fetchone()
    return {"unread_count": int(row[0]) if row else 0}


def mark_all_seen(
    conn: "psycopg.Connection",
    *,
    source_kind: SourceKindFilter = "all",
) -> int:
    """Mark every visible unseen dispatch (optionally scoped to a source) as seen.

    A hidden dispatch (VISIBLE_SQL: a repeat, a dismissed property's, a retired watchdog's, a
    retracted 'new') keeps its unseen state, so a dismissal undone brings it back unread.
    """
    sql = (
        "UPDATE notification_dispatches d SET seen_at = now() "
        f"WHERE d.seen_at IS NULL AND {VISIBLE_SQL}"
    )
    with conn.cursor() as cur:
        if source_kind == "all":
            cur.execute(sql)
        else:
            cur.execute(sql + " AND d.source_kind = %s", (source_kind,))
        return cur.rowcount or 0


# --- producers (the worker's notifications lane; program § 3.3) -----------
#
# Two detecting producers, one clock: the realtime worker's `notifications` lane runs `match_once`
# (step watchdog_new), `match_changes_once` (price) and `match_lifecycle_once` (lifecycle) every
# 300 s, each step on its own connection; nothing else calls them (W2b deleted the API's
# matcher_loop). Price and lifecycle run one detector per kind for both the watchdog and the
# collection monitor (`_Source`). Every INSERT ends in ON_EVENT_CONFLICT, the one identity
# (EVENT_IDENTITY, migration 653), so a re-run or a deploy overlap's second worker writes a
# union, never a duplicate. `event_ref` (migration 652) is the discriminator within (source, kind, property);
# each producer's equals what 652 backfilled from the pre-W3 key on the rows written before it
# (tests/api/test_notifications.py renders and pins both), so no event already told re-fires.
# A watchdog tells a property only once it is PUBLISHED (`_build_match_clauses`;
# toolkit/publication.py, migrations 668/669: settled by the pipeline, or 6 h old), as Browse
# serves it; that rule replaced the images-first gate 'new' was held by. The collection monitor
# does not: a change on a property the user curated is not its first appearance.

# How far back each producer looks (program § 3.3, ruling R6 (a)); NEW_WINDOW sits above with the
# fragment that also reads it. A watchdog tells only what became visible after its `active_since`
# ('new') or changed after it (every other kind); a collection no earlier than `monitor_since`.
PRICE_WINDOW = "7 days"
MONITOR_WINDOW = "7 days"
# 'Left the market' only once the property has stayed gone this long (F14): the delist pipeline's
# effective dwell, so a portal blink sends neither 'Left the market' nor, later, 'Back on the market'.
INACTIVE_DWELL = "24 hours"
# One producer statement past this is cancelled and fails its watchdog (or its step) instead of
# holding a pass. The heaviest measured, a broad watchdog's price statement over 7 days of
# canonical steps, read 1.4 s (2026-10-21).
PRODUCER_STATEMENT_TIMEOUT = "120s"
# What a stored spec fails with deterministically: the model's ValidationError (a ValueError) and
# the one compiler's ValueError / LookupError. The write routes refuse such a spec (422); a stored
# one is counted `subscriptions_invalid`, never a statement failure.
SPEC_REJECTIONS: tuple[type[Exception], ...] = (ValueError, LookupError)


def check_filter_spec(spec: "WatchdogFilterSpec") -> None:
    """Raise what the matcher would raise for this spec (one of SPEC_REJECTIONS), else nothing."""
    _build_match_clauses(spec)


def _commit_one(conn: "psycopg.Connection", sql: str, params: dict[str, Any]) -> int:
    """One producer statement in its own transaction under the bound, committed at once: the KEY
    SHARE locks its INSERT takes on the properties it dispatches last one statement, never a whole
    step, so a merge (autodedup's 5 s lock_timeout) never waits on a producer. SET LOCAL, never a
    session SET: connect() is the transaction-mode pooler, where a session GUC leaks to others.

    The notification-events lock, SHARED, as its own statement before the INSERT (the protocol is
    toolkit.property_carriers'): the identity includes property_id, which a merge changes, so the
    INSERT's snapshot must come after any merge holding the lock has committed, and a merge that
    comes second must wait for this commit and mark the twin it then sees."""
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(f"SET LOCAL statement_timeout = '{PRODUCER_STATEMENT_TIMEOUT}'")
        cur.execute(NOTIFICATION_EVENTS_SHARED_LOCK_SQL)
        cur.execute(sql, params)
        return cur.rowcount or 0


# The watchdogs a step serves: switched on, not retired (migration 652; deleting a watchdog retires
# it, `delete_subscription`) and telling at least one of the step's kinds (migration 720), each
# with the kinds it tells.
_LIVE_WATCHDOGS_SQL = (
    "SELECT id, filter_spec, active_since, notify_kinds FROM notification_subscriptions "
    "WHERE is_active = true AND retired_at IS NULL AND notify_kinds && %s::text[]"
)


def _live_watchdogs(conn: "psycopg.Connection", kinds: tuple[str, ...]) -> list[tuple[Any, ...]]:
    with conn.cursor() as cur:
        cur.execute(_LIVE_WATCHDOGS_SQL, (list(kinds),))
        return cur.fetchall()


@dataclass
class _Tally:
    """One producer's per-watchdog outcome: a spec the matcher refuses is `invalid` (the same every
    pass), a statement that raised is `failed` (the lane reddens its step for it)."""
    evaluated: int
    invalid: int = 0
    failed: int = 0
    inserted: int = 0

    def stats(self, inserted_key: str) -> dict[str, int]:
        return {"subscriptions_evaluated": self.evaluated,
                "subscriptions_invalid": self.invalid,
                "subscriptions_failed": self.failed,
                inserted_key: self.inserted}


def _insert_per_watchdog(
    conn: "psycopg.Connection", tally: _Tally, sub_id: Any, raw_spec: Any, statements: list[str],
    params: dict[str, Any], *, label: str,
) -> None:
    """One watchdog's INSERTs (`{where}` in each = its spec), each committed on its own
    (`_commit_one`) in order, so one failing watchdog rolls back alone and the others still match."""
    try:
        where, spec_params = _build_match_clauses(WatchdogFilterSpec(**(raw_spec or {})))
    except SPEC_REJECTIONS as exc:
        LOG.warning("%s: subscription %s has a spec the matcher refuses: %s", label, sub_id, exc)
        tally.invalid += 1
        return
    try:
        for sql in statements:
            tally.inserted += _commit_one(
                conn, sql.replace("{where}", " AND ".join(where)),
                {**spec_params, **params, "subscription_id": str(sub_id)},
            )
    except Exception as exc:  # noqa: BLE001 — isolate one sub, keep loop alive
        LOG.exception("%s: subscription %s failed, skipping: %s", label, sub_id, exc)
        tally.failed += 1


# Step watchdog_new: a published property the spec matches, first seen inside NEW_WINDOW, that became
# visible after the watchdog last started (`toolkit/publication.py`). `l` is properties_public:
# `listing_id` is the canonical advert (migration 561). 'new' carries no event_ref (program § 3.2:
# once ever per property).
_WATCHDOG_NEW_SQL = (
    "INSERT INTO notification_dispatches "
    "  (subscription_id, source_kind, property_id, listing_id, "
    "   change_kind, trigger_price_czk, event_ref) "
    "SELECT %(subscription_id)s, 'watchdog', l.property_id, l.listing_id, "
    "       'new', l.price_czk, NULL "
    "FROM properties_public l "
    "WHERE {where} "
    "  AND l.first_seen_at > now() - %(new_window)s::interval "
    "  AND EXISTS (SELECT 1 FROM properties pp WHERE pp.id = l.property_id AND "
    f"             {visible_since_sql('pp')} > %(active_since)s) "
    f"{ON_EVENT_CONFLICT}"
)


def match_once(conn: "psycopg.Connection") -> dict[str, int]:
    """Step watchdog_new: one 'new' dispatch per (live watchdog telling 'new', matching published
    property first seen inside the last 24 h that became visible after the watchdog last started).

    ONE INSERT per watchdog over a fixed window, `first_seen_at > now() − 24 h` and
    `visible_since > active_since` (F1; `toolkit/publication.py`): a property whose match facts
    arrive late (its location resolves an hour on) still fires while it is under a day old, and
    a watchdog created, switched back on or given a new filter or new kinds (each restamps
    `active_since`, migrations 652 and 720) alerts only on what becomes VISIBLE afterwards, never
    a backfill (R6 (a)). Keyed on visibility, not first seen (the publication gate's critique,
    2026-10-23): a property first seen up to 6 h before a watchdog starts and published after it is
    still told. One not yet published (`_build_match_clauses`) is HELD and told by the first pass
    after it is; the 6 h cap ends every hold inside the 24 h window, so none is lost. The identity
    (watchdog, 'new', property; no event_ref) is once ever and the only idempotence — no cursor, no
    lookback.
    """
    subs = _live_watchdogs(conn, ("new",))
    tally = _Tally(evaluated=len(subs))
    for sub_id, raw_spec, active_since, _ in subs:
        _insert_per_watchdog(
            conn, tally, sub_id, raw_spec, [_WATCHDOG_NEW_SQL],
            {"new_window": NEW_WINDOW, "active_since": active_since}, label="matcher",
        )
    return tally.stats("matches_inserted")


# --- the property events: one detector per kind, for both producers (program § 3.3) --------
#
# Each detector is written once and rendered for both producers, so 'Left the market' (or any kind) means
# one thing whichever producer tells it. It reads its events `e` off the market (the canonical
# ad's price steps, the recent delistings, the ads that brought a new portal), never off a cohort,
# and a `_Source` says who is told: `join` gives each event row `e` its `m` (source_id, listing_id
# — the canonical advert — and since), so a detector fires only for a change after `since` and a
# change that predates the watching never notifies. Detection never consults dismissals;
# VISIBLE_SQL hides a dismissed property's alerts at read time.


@dataclass(frozen=True)
class _Source:
    kind: str    # source_kind
    column: str  # the dispatch column naming the source (source_ck)
    ctes: str    # what `join` reads, each CTE followed by ", "
    join: str    # the event row `e` -> its `m`, one per source told about it
    scope: str   # this statement's own dispatches, on notification_dispatches


# A collection with monitoring_enabled tells each property it holds, from the later of when it was
# added (collection_properties.added_at) and when the collection last had monitoring turned on
# (collections.monitoring_enabled_at, migration 230). status='active' skips merged-away properties.
# Set-based: one statement serves every monitored collection.
_MONITOR = _Source(
    kind="collection_monitor",
    column="collection_id",
    ctes=(
        "members AS ("
        "  SELECT cp.collection_id AS source_id, p.id AS property_id, "
        "         p.repr_listing_ref_id AS listing_id, "
        "         greatest(cp.added_at, coalesce(c.monitoring_enabled_at, cp.added_at)) AS since "
        "  FROM collection_properties cp "
        "  JOIN collections c ON c.id = cp.collection_id AND c.monitoring_enabled = true "
        "  JOIN properties p ON p.id = cp.property_id AND p.status = 'active' "
        # Gate on the SURROGATE, not the legacy handle: post-Gate-2 a property whose
        # repr listing is a new non-sreality row has repr_listing_id NULL but
        # repr_listing_ref_id set — gating on the legacy column would silently drop
        # exactly those properties out of collection monitoring.
        "                   AND p.repr_listing_ref_id IS NOT NULL"
        "), "
    ),
    join="JOIN members m ON m.property_id = e.property_id",
    scope="source_kind = 'collection_monitor'",
)
# A watchdog tells each published property its spec matches NOW (`{where}`, rule 16) from when it
# last started (`active_since`); one statement per watchdog. `l` is properties_public. The spec joins
# the event directly, so the planner tests it per event by the property's id and pushes `since` into
# the event's own index; behind a CTE of matching properties it scanned the whole cohort once per
# price step instead (a broad watchdog timed out, 2026-11-03).
_WATCHDOG = _Source(
    kind="watchdog",
    column="subscription_id",
    ctes="",
    join=(
        "JOIN properties_public l ON l.property_id = e.property_id AND {where} "
        "CROSS JOIN LATERAL (SELECT %(subscription_id)s::uuid AS source_id, l.listing_id, "
        "                           %(active_since)s::timestamptz AS since) m"
    ),
    scope="source_kind = 'watchdog' AND subscription_id = %(subscription_id)s::uuid",
)

# THE canonical-ad price-step fragment (program § 3.3, step price): a step of the property's
# CANONICAL advert scraped since it became canonical (`repr_since`, migration 561) — a merge,
# detach or delisting that hands the slot over replays nothing, and the price an alert quotes is
# the price the property shows. `listing_price_steps` (migration 559) compares an advert only
# with its own previous priced snapshot, so a step never spans two.
_CANONICAL_PRICE_STEPS = (
    "canonical_steps AS ("
    "  SELECT ps.property_id, ps.listing_id, ps.snapshot_id, ps.scraped_at, "
    "         ps.price_czk, ps.prev_price_czk "
    "  FROM listing_price_steps ps "
    "  JOIN properties p ON p.id = ps.property_id "
    "   AND p.repr_listing_ref_id = ps.listing_id AND ps.scraped_at > p.repr_since "
    "  WHERE ps.scraped_at > now() - %(price_window)s::interval"
    ")"
)
_STEP_KIND = "CASE WHEN e.price_czk < e.prev_price_czk THEN 'price_drop' ELSE 'price_rise' END"


def _price_sql(src: _Source) -> str:
    """'Price drop' / 'Price rise': a canonical-ad step after `since`, of a kind in `%(kinds)s`.
    event_ref = the step's snapshot id, so a property that keeps dropping fires once per real cut
    and a re-scan of the window is idempotent."""
    return (
        f"WITH {src.ctes}{_CANONICAL_PRICE_STEPS} "
        "INSERT INTO notification_dispatches "
        f"  ({src.column}, source_kind, property_id, listing_id, change_kind, "
        "   trigger_price_czk, prev_price_czk, event_ref) "
        f"SELECT m.source_id, '{src.kind}', e.property_id, e.listing_id, {_STEP_KIND}, "
        "       e.price_czk, e.prev_price_czk, e.snapshot_id::text "
        f"FROM canonical_steps e {src.join} "
        f"WHERE e.scraped_at > m.since AND {_STEP_KIND} = ANY(%(kinds)s) "
        f"{ON_EVENT_CONFLICT}"
    )


def _inactive_sql(src: _Source) -> str:
    """'Left the market': the property went inactive (every listing delisted) after `since` and has STAYED
    inactive for INACTIVE_DWELL (F14). event_ref = the epoch of the latest child inactive_at (set on
    the flip, cleared on reactivation, so a re-inactivation is a fresh event), read off the recent
    delistings (`listings_inactive_at_idx`)."""
    return (
        f"WITH {src.ctes}"
        "gone AS ("
        "  SELECT g.property_id, max(g.inactive_at) AS inactive_at "
        "  FROM listings g "
        "  JOIN properties p ON p.id = g.property_id AND p.is_active = false "
        "  WHERE g.inactive_at > now() - %(monitor_window)s::interval "
        "  GROUP BY g.property_id "
        "  HAVING max(g.inactive_at) < now() - %(inactive_dwell)s::interval"
        ") "
        "INSERT INTO notification_dispatches "
        f"  ({src.column}, source_kind, property_id, listing_id, change_kind, event_ref) "
        f"SELECT m.source_id, '{src.kind}', e.property_id, m.listing_id, 'inactive', "
        "       floor(extract(epoch FROM e.inactive_at))::bigint::text "
        f"FROM gone e {src.join} "
        "WHERE e.inactive_at > m.since "
        f"{ON_EVENT_CONFLICT}"
    )


def _reactivated_sql(src: _Source) -> str:
    """'Back on the market': a property this source ALREADY told had left the market, after `since`, came back active.
    That 'inactive' dispatch is the durable "was dead" marker (listings.inactive_at is cleared on
    reactivation, so it can't be read), and only an event counts: both reads skip a row a merge
    marked a duplicate. event_ref = the epoch of its dispatched_at: the pre-W3 key's last segment,
    which 652 backfilled, so a reactivation already told keeps its identity (production's grammar
    supersedes PROGRAM.md § 3.2's "the id of the inactive dispatch")."""
    return (
        f"WITH {src.ctes}"
        "told AS ("
        f"  SELECT DISTINCT ON ({src.column}, property_id) {src.column} AS told_source, property_id, "
        "         dispatched_at AS told_at "
        "  FROM notification_dispatches "
        f"  WHERE {src.scope} AND change_kind = 'inactive' AND duplicate_of IS NULL "
        f"  ORDER BY {src.column}, property_id, dispatched_at DESC"
        ") "
        "INSERT INTO notification_dispatches "
        f"  ({src.column}, source_kind, property_id, listing_id, change_kind, event_ref) "
        f"SELECT m.source_id, '{src.kind}', e.property_id, m.listing_id, 'reactivated', "
        "       floor(extract(epoch FROM e.told_at))::bigint::text "
        "FROM told e "
        "JOIN properties p ON p.id = e.property_id AND p.is_active = true "
        "     AND p.last_seen_at > now() - %(monitor_window)s::interval "
        f"     AND p.last_seen_at > e.told_at {src.join} "
        "WHERE m.source_id = e.told_source AND e.told_at > m.since "
        "  AND NOT EXISTS ("
        "    SELECT 1 FROM notification_dispatches r "
        f"    WHERE r.source_kind = '{src.kind}' AND r.{src.column} = e.told_source "
        "      AND r.property_id = e.property_id AND r.change_kind = 'reactivated' "
        "      AND r.duplicate_of IS NULL AND r.dispatched_at > e.told_at"
        "  ) "
        f"{ON_EVENT_CONFLICT}"
    )


def _new_source_sql(src: _Source) -> str:
    """'New portal': a listing brought a NEW portal to the property (a cross-source sighting the
    dedup engine grouped in) after `since`; event_ref = that introducing listing's id. It is its
    portal's first ad on the property (by first seen, then the legacy handle, then the surrogate, so
    it never changes between passes) and not the property's first ad, so another portal was there
    before it. Limitation (kept, program § 3.3): fires only when that listing's first_seen_at is
    inside the window (the common "a portal just listed it" case); a merge of an OLD listing won't
    fire."""
    return (
        f"WITH {src.ctes}"
        "intro AS ("
        "  SELECT x.property_id, x.listing_id, x.first_seen_at FROM ("
        "    SELECT l.property_id, l.id AS listing_id, l.first_seen_at, "
        "           row_number() OVER (PARTITION BY l.property_id, l.source "
        "                              ORDER BY l.first_seen_at, l.sreality_id, l.id) AS rn, "
        "           min(l.first_seen_at) OVER (PARTITION BY l.property_id) AS prop_first "
        "    FROM listings l "
        "    WHERE l.property_id IN (SELECT r.property_id FROM listings r "
        "                            WHERE r.first_seen_at > now() - %(monitor_window)s::interval)"
        "  ) x "
        "  WHERE x.rn = 1 AND x.first_seen_at > x.prop_first "
        "    AND x.first_seen_at > now() - %(monitor_window)s::interval"
        ") "
        "INSERT INTO notification_dispatches "
        f"  ({src.column}, source_kind, property_id, listing_id, change_kind, event_ref) "
        f"SELECT m.source_id, '{src.kind}', e.property_id, e.listing_id, 'new_source', "
        "       e.listing_id::text "
        f"FROM intro e {src.join} "
        "WHERE e.first_seen_at > m.since "
        f"{ON_EVENT_CONFLICT}"
    )


_MONITOR_PRICE_SQL = _price_sql(_MONITOR)
_WATCHDOG_PRICE_SQL = _price_sql(_WATCHDOG)
_MONITOR_INACTIVE_SQL = _inactive_sql(_MONITOR)
_WATCHDOG_INACTIVE_SQL = _inactive_sql(_WATCHDOG)
_MONITOR_REACTIVATED_SQL = _reactivated_sql(_MONITOR)
_WATCHDOG_REACTIVATED_SQL = _reactivated_sql(_WATCHDOG)
_MONITOR_NEW_SOURCE_SQL = _new_source_sql(_MONITOR)
_WATCHDOG_NEW_SOURCE_SQL = _new_source_sql(_WATCHDOG)
# kind -> its watchdog statement, in this order, each committed before the next: reactivated reads
# the inactive rows.
_WATCHDOG_LIFECYCLE = (("inactive", _WATCHDOG_INACTIVE_SQL),
                       ("reactivated", _WATCHDOG_REACTIVATED_SQL),
                       ("new_source", _WATCHDOG_NEW_SOURCE_SQL))


def match_changes_once(conn: "psycopg.Connection") -> dict[str, int]:
    """Step price: every canonical-ad price step of the last 7 days, from ONE detector
    (`_price_sql`), for both producers.

    - Watchdog: one `price_drop` / `price_rise` per (live watchdog telling that kind x matching
      property x step after its `active_since`). The spec matches the property's CURRENT state
      through `_build_match_clauses` (rule 16).
    - Collection monitor: both kinds per (monitored collection x member property x step after
      `monitor_since`), set-based across every monitored collection.
    """
    params: dict[str, Any] = {"price_window": PRICE_WINDOW}
    subs = _live_watchdogs(conn, PRICE_KINDS)
    tally = _Tally(evaluated=len(subs))
    for sub_id, raw_spec, active_since, kinds in subs:
        _insert_per_watchdog(
            conn, tally, sub_id, raw_spec, [_WATCHDOG_PRICE_SQL],
            {**params, "active_since": active_since, "kinds": [k for k in kinds if k in PRICE_KINDS]},
            label="change matcher",
        )
    collection_inserted = _commit_one(conn, _MONITOR_PRICE_SQL, {**params, "kinds": list(PRICE_KINDS)})
    tally.inserted += collection_inserted
    return {**tally.stats("changes_inserted"),
            "collection_changes_inserted": collection_inserted}


def match_lifecycle_once(conn: "psycopg.Connection") -> dict[str, int]:
    """Step lifecycle: 'Left the market' (`inactive`, gone for a day, F14), 'Back on the market'
    (`reactivated`) and 'New portal' (`new_source`, a sibling listing appeared on another portal), one detector per kind
    for both producers; the price kinds are step price (`match_changes_once`).

    The collection monitor tells all three for every monitored property, set-based (one INSERT per
    kind across ALL monitored collections), so a property in N monitored collections fires N times:
    monitoring is per collection by design. A watchdog tells the ones it is set to, one INSERT per
    kind over the properties its spec matches now. Each dispatch carries its source and a per-event
    `event_ref`, so re-runs over an overlapping window are idempotent and a 2nd real change is its
    own event.

    There is no broker change: no property-level broker nor a broker-change timestamp keys a
    stable event (migration 656 dropped the never-emitted kind from the CHECK).
    """
    params: dict[str, Any] = {"monitor_window": MONITOR_WINDOW, "inactive_dwell": INACTIVE_DWELL}
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM collections WHERE monitoring_enabled = true")
        row = cur.fetchone()
    monitored = int(row[0]) if row else 0
    # In this order, each committed before the next: reactivated reads the inactive rows.
    collection_inserted = sum(_commit_one(conn, sql, params) for sql in (
        _MONITOR_INACTIVE_SQL, _MONITOR_REACTIVATED_SQL, _MONITOR_NEW_SOURCE_SQL)) if monitored else 0
    subs = _live_watchdogs(conn, LIFECYCLE_KINDS)
    tally = _Tally(evaluated=len(subs))
    for sub_id, raw_spec, active_since, kinds in subs:
        _insert_per_watchdog(
            conn, tally, sub_id, raw_spec, [sql for kind, sql in _WATCHDOG_LIFECYCLE if kind in kinds],
            {**params, "active_since": active_since}, label="lifecycle matcher",
        )
    tally.inserted += collection_inserted
    return {**tally.stats("events_inserted"), "monitored_collections": monitored,
            "collection_events_inserted": collection_inserted}
