"""A property as its page reads it (#2063): the page's own relations and columns (frontend/src/lib/queries.ts), read
here once for every server-side reader of them: an agent's dossier and own context (under the user's own login) and
broker list (on the service connection, as the page's broker read), the extension's price chart and the download."""

from __future__ import annotations

import itertools
from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from typing import Annotated, Any

import psycopg
from pydantic import Field

from api import curation, pipeline
from api.capabilities import truncate
from api.capabilities.errors import ToolError, ToolErrorCode
from api.filter_args import Args
from toolkit import _max_last_seen, _now_iso, brokers
from toolkit.property_identity import resolve_active_property_ids

# The page's PROPERTY_COLS over properties_public, its canonical ad as `listing_id`. Never the MF breakdown (per-m²
# parts with no basis beside them; compute_reference_rent returns it with one) or the legacy sreality_id; never a
# parked condition level (rule 14), a city-quality column (rule 17) or a broker (get_property_brokers lists them).
PROPERTY_COLS = (
    "property_id", "listing_id", "first_seen_at", "last_seen_at", "is_active", "source", "source_id_native",
    "tom_days", "category_main", "category_type", "price_czk", "price_unit", "area_m2", "disposition", "subtype",
    "display_label", "lat", "lng", "floor", "total_floors", "has_balcony", "has_parking", "has_lift", "building_type",
    "condition", "energy_rating", "estate_area", "usable_area", "garden_area", "category_sub_cb", "furnished",
    "terrace", "cellar", "garage", "parking_lots", "ownership", "price_per_m2", "price_per_m2_basis", "description",
    "mf_gross_yield_pct", "price_change_count", "total_price_change_pct", "obec_id",
)
# The page's PROPERTY_SOURCE_COLS over property_sources_public: every ad of the property.
SOURCE_COLS = (
    "id", "property_id", "source", "source_url", "source_id_native", "ad_reference", "is_active", "price_czk",
    "first_seen_at", "last_seen_at",
)
# Each ad's deal type and its own per-m² price with its basis: the page's read of listings_public for its ads.
AD_MEASURE_COLS = ("id", "category_type", "price_per_m2", "price_per_m2_basis")
# Never `description` (or `*`): the view derives it from raw_json, detoasting every snapshot.
SNAPSHOT_COLS = ("id", "listing_id", "scraped_at", "price_czk")
# A run as the page's estimations list sums it up: never its trace, comparables, inputs or MF breakdown.
RUN_COLS = (
    "id", "created_at", "status", "source", "estimate_kind", "input_listing_id", "parent_run_id",
    "estimated_monthly_rent_czk", "rent_p25_czk", "rent_p75_czk", "estimated_sale_price_czk", "sale_p25_czk",
    "sale_p75_czk", "gross_yield_pct", "confidence",
)
_ANY = "= any(%s::bigint[])"
_PROPERTIES_SQL = f"select {', '.join(PROPERTY_COLS)} from properties_public where property_id {_ANY}"
_SOURCES_SQL = (f"select {', '.join(SOURCE_COLS)} from property_sources_public where property_id {_ANY}"
                " order by first_seen_at, id")
_AD_MEASURES_SQL = f"select {', '.join(AD_MEASURE_COLS)} from listings_public where id {_ANY}"
_SNAPSHOTS = f"select {', '.join(SNAPSHOT_COLS)} from listing_snapshots_public where listing_id {_ANY}"
_SNAPSHOTS_SQL = f"{_SNAPSHOTS} order by scraped_at, id"
# A snapshot is appended on any content change (rule 2), so an ad's price points are where its price moves.
_PRICE_POINTS_SQL = (
    "select listing_id, scraped_at, price_czk from (select listing_id, scraped_at, price_czk, id, price_czk is "
    "distinct from lag(price_czk) over (partition by listing_id order by scraped_at, id) as moved from ("
    f"{_SNAPSHOTS}) s) s where moved order by listing_id, scraped_at, id"
)
# The runs the page lists: the user's and SYSTEM's (deps.account_scope). RLS also shows a platform admin the runs of a
# deleted account (account_id NULL), which the page leaves out (api/estimation_runs.py list_estimation_runs).
_RUNS_SQL = (f"select {', '.join(RUN_COLS)} from estimation_runs where input_listing_id {_ANY}"
             " and account_id is not null order by created_at desc, id desc")
# An id outside bigint names nothing we hold; bound as one, it would fail the statement's cast.
_BIGINT = range(-(2**63), 2**63)
PROPERTIES_MAX = 20
BROKERS_MAX = 20
# The page lists a property's newest 100 runs (queries.ts fetchEstimationsForListings).
_PAGE_RUNS = 100
# truncate.fit's note of a cut dossier at its widest, which a row kept alone must leave room for.
_CUT_NOTE = len(',"truncated":{"data":{"kept":20,"total":20}}')
_BROKER = ("broker_id", "broker_display_name", "broker_firm_label", "primary_phone", "primary_email")


_PropertyId = Annotated[int, Field(ge=1, le=_BIGINT.stop - 1)]
_FOLLOWED = ("A merged-away id follows its survivor (metadata.resolved); an id we do not hold is listed in "
             "metadata.missing.")


class PropertyIds(Args):
    property_ids: list[_PropertyId] = Field(
        min_length=1, max_length=PROPERTIES_MAX, description=f"Properties we hold (property ids), 1 to "
        f"{PROPERTIES_MAX}, answered in this order. {_FOLLOWED}")


class ContextIds(Args):
    property_ids: list[_PropertyId] = Field(
        default_factory=list, max_length=PROPERTIES_MAX, description=f"Properties we hold (property ids), 0 to "
        f"{PROPERTIES_MAX}, answered in this order; none returns the vocabularies alone. {_FOLLOWED}")


def jsonable(value: Any) -> Any:
    """A read value as a tool returns it, at any depth: dates and timestamps in ISO form, numerics as floats."""
    if isinstance(value, dict):
        return {k: jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [jsonable(v) for v in value]
    if isinstance(value, date):   # a datetime too
        return value.isoformat()
    return float(value) if isinstance(value, Decimal) else value


def _select(conn: psycopg.Connection, sql: str, ids: Sequence[int], columns: tuple[str, ...]) -> list[dict[str, Any]]:
    ids = [i for i in ids if i in _BIGINT]
    if not ids:
        return []
    with conn.cursor() as cur:
        cur.execute(sql, (list(ids),))
        return [dict(zip(columns, row, strict=True)) for row in cur.fetchall()]


def properties(conn: psycopg.Connection, ids: Sequence[int]) -> list[dict[str, Any]]:
    """The active properties among `ids`: a merged-away or unknown id has no row."""
    return _select(conn, _PROPERTIES_SQL, ids, PROPERTY_COLS)


def sources(conn: psycopg.Connection, ids: Sequence[int]) -> list[dict[str, Any]]:
    """Every ad of these properties, oldest first, `id` breaking a tie (queries.ts fetchPropertySources)."""
    return _select(conn, _SOURCES_SQL, ids, SOURCE_COLS)


def snapshots(conn: psycopg.Connection, ad_ids: Sequence[int]) -> list[dict[str, Any]]:
    """Every price snapshot of these ads, the page's chart lines (queries.ts fetchSnapshotsForListings)."""
    return _select(conn, _SNAPSHOTS_SQL, ad_ids, SNAPSHOT_COLS)


def price_points(conn: psycopg.Connection, ad_ids: Sequence[int]) -> list[dict[str, Any]]:
    """Each ad's snapshots where its price changes, the first priced one included."""
    return _select(conn, _PRICE_POINTS_SQL, ad_ids, ("listing_id", "scraped_at", "price_czk"))


def ad_measures(conn: psycopg.Connection, ad_ids: Sequence[int]) -> list[dict[str, Any]]:
    return _select(conn, _AD_MEASURES_SQL, ad_ids, AD_MEASURE_COLS)


def runs(conn: psycopg.Connection, ad_ids: Sequence[int]) -> list[dict[str, Any]]:
    """The runs on these ads that the connection's login may read (RLS under a user's), newest first."""
    return _select(conn, _RUNS_SQL, ad_ids, RUN_COLS)


def _survivors(conn: psycopg.Connection, asked: list[int]) -> tuple[list[int], dict[str, Any]]:
    """Each asked property's live survivor, once each in the asked order, and the metadata naming the followed and
    the unknown ids (HeldSubject follows a survivor the same way)."""
    asked = list(dict.fromkeys(asked))
    found = resolve_active_property_ids(conn, asked)
    return list(dict.fromkeys(found[i] for i in asked if i in found)), {
        "resolved": {i: found[i] for i in asked if found.get(i, i) != i},
        "missing": [i for i in asked if i not in found],
    }


def _envelope(tool: str, args: PropertyIds | ContextIds, data: list[dict[str, Any]] | dict[str, Any], count: int,
              freshness: str | None, **metadata: Any) -> dict[str, Any]:
    return {"data": data, "metadata": {
        "tool": tool, "filters_used": {"property_ids": args.property_ids}, "result_count": count,
        "queried_at": _now_iso(), "data_freshness": freshness, **metadata}}


def _ad(ad: dict[str, Any], measure: dict[str, Any], prices: list[dict[str, Any]], canonical: int) -> dict[str, Any]:
    facts = {k: v for k, v in ad.items() if k not in ("id", "property_id")}
    return {"listing_id": ad["id"], "canonical": ad["id"] == canonical, **facts,
            **{c: measure.get(c) for c in AD_MEASURE_COLS if c != "id"},
            "prices": [{"scraped_at": p["scraped_at"], "price_czk": p["price_czk"]} for p in prices]}


def _alone(row: dict[str, Any], room: int) -> dict[str, Any]:
    """`row` whole when it fits `room`, else its ads and runs cut as truncate.fit cuts a result, each cut named in its
    `truncated`: truncate.fit drops a row whole, so one that no result can hold would be left out of every answer."""
    lists = {k: row[k] for k in ("ads", "estimates")}

    def cut(kept: dict[str, int]) -> dict[str, Any]:
        truncated = {k: {"kept": n, "total": len(lists[k])} for k, n in kept.items() if n < len(lists[k])}
        return {**row, **{k: v[:kept[k]] for k, v in lists.items()}, **({"truncated": truncated} if truncated else {})}

    def fits(kept: dict[str, int]) -> bool:
        return len(truncate.encode(cut(kept))) <= room
    if fits({k: len(v) for k, v in lists.items()}):
        return row
    kept = truncate.heads(lists, fits)
    if kept is None:
        raise ToolError(ToolErrorCode.RESULT_TOO_LARGE,
                        f"property {row['property_id']} is too large to return even without its ads and runs")
    return cut(kept)


def dossier(conn: psycopg.Connection, args: PropertyIds) -> dict[str, Any]:
    """One self-contained row per property, in the asked order: its page's facts, every ad, the runs on its ads."""
    ids, followed = _survivors(conn, args.property_ids)
    found = {p["property_id"]: p for p in properties(conn, ids)}
    ads = sources(conn, ids)
    ad_ids = [a["id"] for a in ads]
    measures = {m["id"]: m for m in ad_measures(conn, ad_ids)}
    points: dict[int, list[dict[str, Any]]] = {}
    for p in price_points(conn, ad_ids):
        points.setdefault(p["listing_id"], []).append(p)
    estimates = runs(conn, ad_ids)
    rows = []
    for prop in (found[i] for i in ids if i in found):
        # The canonical ad, then the live, then the ended, newest first: the head a cut row keeps (sources are oldest
        # first).
        own = sorted((a for a in reversed(ads) if a["property_id"] == prop["property_id"]),
                     key=lambda a: (a["id"] != prop["listing_id"], not a["is_active"]))
        mine = {a["id"] for a in own}
        rows.append(jsonable({
            **prop,
            "ads": [_ad(a, measures.get(a["id"], {}), points.get(a["id"], []), prop["listing_id"]) for a in own],
            "estimates": [r for r in estimates if r["input_listing_id"] in mine][:_PAGE_RUNS],
        }))
    out = _envelope("get_property_dossier", args, [], len(rows), _max_last_seen(rows), **followed)
    room = truncate.RESULT_BUDGET - len(truncate.encode(out)) - _CUT_NOTE
    return {**out, "data": [_alone(r, room) for r in rows]}


def property_brokers(conn: psycopg.Connection, args: PropertyIds) -> dict[str, Any]:
    """Each property's brokers as its page lists them (MS7), contacts as the page shows them, until a call's
    BROKERS_MAX-th person: the rows after are left out and their properties named."""
    ids, followed = _survivors(conn, args.property_ids)
    canonical = {p["property_id"]: p["listing_id"] for p in properties(conn, ids)}
    ads = sources(conn, ids)
    # POST /brokers/by-listings' own read: every signed-in user sees these contacts (operator ruling 2026-10-18). In
    # its batches, as twenty properties can hold more ads than one (136 on the largest, 2026-10-29).
    listed = [brokers.apply_pii_policy(brokers.listing_brokers(conn, list(batch)), include_pii=True)
              for batch in itertools.batched((a["id"] for a in ads), brokers.MAX_BATCH)]
    by_listing = {r["listing_id"]: r for batch in listed for r in batch["data"]}
    rows = []
    for pid in (i for i in ids if i in canonical):
        shown, from_inactive = brokers.property_brokers(
            [a for a in ads if a["property_id"] == pid], by_listing, canonical[pid])
        rows += [{"property_id": pid, **{k: b[k] for k in _BROKER}, "from_inactive": from_inactive,
                  "ads": [{"listing_id": a["id"], "source": a["source"], "ad_reference": a["ad_reference"],
                           "is_active": a["is_active"]} for a in b["ads"]]} for b in shown]
    # Each person by the order they first appear in; the call stops at the first row of the one past the cap.
    rank = {b: n for n, b in enumerate(dict.fromkeys(r["broker_id"] for r in rows))}
    cut = next((n for n, r in enumerate(rows) if rank[r["broker_id"]] >= BROKERS_MAX), len(rows))
    return _envelope(
        "get_property_brokers", args, rows[:cut], len(rows), None, pii_masked=any(batch["metadata"]["pii_masked"] for batch in listed),
        brokers={"kept": min(len(rank), BROKERS_MAX), "total": len(rank)},
        unlisted_property_ids=list(dict.fromkeys(r["property_id"] for r in rows[cut:])), **followed)


def own_context(conn: psycopg.Connection, args: ContextIds) -> dict[str, Any]:
    """What the user holds on these properties in their own identity, flat lists keyed by property id (so a cut drops
    whole notes), and the vocabularies its ids name: their collections, tags and stages as their screens list them."""
    ids, followed = _survivors(conn, args.property_ids)
    state = {s["property_id"]: s for s in curation.property_state(conn, ids)}
    rows = [jsonable(state[i]) for i in ids if i in state]
    return _envelope("get_my_property_context", args, {
        "properties": rows,
        "notes": curation.notes_of(conn, ids),
        "collections": curation.list_collections(conn)["data"],
        "tags": curation.list_tags(conn)["data"],
        "stages": pipeline.list_stages(conn)["data"],
    }, len(rows), None, **followed)
