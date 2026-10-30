"""Tools that read as the signed-in user: each runs in their RLS transaction and sees what their own screens show."""

from __future__ import annotations

from typing import Any

from api.capabilities.entry import Context, Effect, Entry, Tenancy
from api.capabilities.subject import sentence
from api.market_evidence import MAX_READ_RADIUS_M, SMALL_UNIT_M2, EvidenceArgs, market_evidence
from api.property_read import PROPERTIES_MAX, ContextIds, PropertyIds, dossier, own_context
from api.property_search import PAGE_MAX, SearchArgs, search_properties
from api.stats_pivot import AggregateArgs, aggregate_properties
from toolkit.measures import EXIT_CLEAN_SINCE
from toolkit.stats_registry import SOLD_MARKET_DAYS


def _search_properties(ctx: Context, args: SearchArgs) -> dict[str, Any]:
    return search_properties(ctx.conn, args)


def _dossier(ctx: Context, args: PropertyIds) -> dict[str, Any]:
    return dossier(ctx.conn, args)


def _own_context(ctx: Context, args: ContextIds) -> dict[str, Any]:
    return own_context(ctx.conn, args)


def _aggregate(ctx: Context, args: AggregateArgs) -> dict[str, Any]:
    return aggregate_properties(ctx.conn, args)


def _evidence(ctx: Context, args: EvidenceArgs) -> dict[str, Any]:
    return market_evidence(ctx.conn, args)


ENTRIES = (
    Entry(
        name="search_properties",
        description="The properties Browse lists for these filters, read as the user reads Browse: published, "
        "placed properties, each once, with the user's own dismissals, tags, collections and pipeline stages. A "
        f"page of at most {PAGE_MAX} cards (property and canonical ad ids, place, category, layout, areas, price, "
        "the per-m² price with its basis, MF gross yield, price changes, dates, status, time on market, portals, "
        "point) in the chosen order; result_count is the total. Page with offset; when metadata.truncated is "
        "present, continue from offset plus the rows kept. category_type is required (null = every deal type) and "
        "decides the per-m² unit: a price_per_m2_basis of 'mixed' or 'unknown' means the page has no single unit.",
        args=SearchArgs, handler=_search_properties, tenancy=Tenancy.TENANT, effect=Effect.READ,
    ),
    Entry(
        name="get_property_dossier",
        description=f"Up to {PROPERTIES_MAX} properties as their page shows them, read as the user reads it, one row "
        "each in the order asked: the facts (price, area, layout, condition, floor and description are its "
        "canonical ad's; building, ownership, energy rating and the other areas the first known across its ads, "
        "canonical first; an amenity is yes when any ad says so), the place (label, point, town code), the per-m² "
        "price with its basis, MF gross yield, price changes and time on market; every ad, the canonical first, then "
        "the live, then the ended, newest first (listing_id, portal, URL, the advertiser's reference, deal type, "
        "price, its own per-m² price with its basis and the points where its price changed); and the estimation runs "
        "its page lists on its ads, the user's and the platform's alike, newest first. A property too large to return "
        "whole keeps the first of its ads and runs that fit, its truncated naming each cut. No broker: "
        "get_property_brokers lists them. When metadata.truncated is present, ask again for the properties left out.",
        args=PropertyIds, handler=_dossier, tenancy=Tenancy.TENANT, effect=Effect.READ,
    ),
    Entry(
        name="get_my_property_context",
        description=f"What the user holds on up to {PROPERTIES_MAX} properties, read as their own screens read it "
        "(never as the owner of a board shared with them): per property their pipeline card (stage_id null = none; "
        "the newest when they hold several), the collections and tags holding it and whether they dismissed it; the "
        "notes they may read on them, newest first (their own, and a colleague's marked shared on a property in a "
        "collection shared between them, owner_email naming that colleague); and their vocabularies: collections (one "
        "shared with them names its owner in owner_email), tags and pipeline stages, the ids search_properties' tags, "
        "collections and pipeline filters take. With no property ids, the vocabularies alone. metadata.truncated names "
        "each list cut to its head: properties and notes reach further with fewer properties asked (notes newest "
        "first, down to one property); the vocabularies do not depend on the properties asked, so ask with no "
        "property ids to read the most of them.",
        args=ContextIds, handler=_own_context, tenancy=Tenancy.TENANT, effect=Effect.READ,
    ),
    Entry(
        name="aggregate_properties",
        description="The Stats dashboard's pivot over the properties Browse lists for these filters, read as the user "
        "reads Browse (their dismissals, tags, collections and pipeline): data is the dashboard function's own "
        "answer. filtered counts the cohort, sample those with the measure, both categories and a value on every "
        "chosen dimension, excluded the rest. cells hold every rollup level: keys [] is the cohort total, keys of "
        "length L the first L rows, col the column's key; a band key is its lower bound, a place key its RÚIAN code "
        "as text, named in places. n always; mean, median, p10, p25, p75, p90 from min_n properties up, below it "
        "small (min, max, mean, median). refused says why no cells came back: empty; mixed_category (more than one "
        "category_main or category_type: narrow them); mixed_status (time on market over live and ended "
        "properties); too_many_cells. basis names the unit of every per-m² figure; a metadata.price_per_m2_basis of "
        "'mixed' or 'unknown' means there is none. category_type is required (null = every deal type).",
        args=AggregateArgs, handler=_aggregate, tenancy=Tenancy.TENANT, effect=Effect.READ,
    ),
    Entry(
        name="market_evidence",
        description=f"Prices achieved round a subject, two arms never mixed, each row and summary labelled by "
        f"evidence. proxy: properties within radius_m that left the market (every ad ended, seen from its first ad) "
        f"after a time on market in the window, at its last asking price, no transaction price (a withdrawal "
        f"looks alike; exits before {EXIT_CLEAN_SINCE}, from absence sweeps, count as on the Stats tab); "
        f"proxy_summary: the dashboard's statistic. registered: cadastre-registered sales within radius_m, at most "
        f"{MAX_READ_RADIUS_M} m (reas.cz: ~30 days late, fetched only near properties someone works on; "
        f"registered_coverage: the centre's town only), byt and dum for sale, no time on market; its summary's median "
        f"holds out units under {SMALL_UNIT_M2} m². Window {SOLD_MARKET_DAYS[0]}-{SOLD_MARKET_DAYS[1]} days by "
        f"default; metadata.unapplied: what an arm cannot apply. {sentence(EvidenceArgs)}",
        args=EvidenceArgs, handler=_evidence, tenancy=Tenancy.TENANT, effect=Effect.READ,
    ),
)
