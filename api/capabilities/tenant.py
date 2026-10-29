"""Tools that read as the signed-in user: each runs in their RLS transaction and sees what their own screens show."""

from __future__ import annotations

from typing import Any

from api import pipeline
from api.capabilities.entry import Context, Effect, Entry, Tenancy
from api.filter_args import Args
from api.property_search import PAGE_MAX, SearchArgs, search_properties


class NoArgs(Args):
    pass  # takes nothing, and still refuses an unknown key (a docstring would reach the schema)


def _list_pipeline_stages(ctx: Context, args: NoArgs) -> dict[str, Any]:
    return pipeline.list_stages(ctx.conn)


def _search_properties(ctx: Context, args: SearchArgs) -> dict[str, Any]:
    return search_properties(ctx.conn, args)


ENTRIES = (
    Entry(
        name="list_pipeline_stages",
        description="The stages of the user's deal pipeline board, in board order: id, key, label, position, "
        "colour, whether a stage is the entry stage or a terminal (closed-deal) one, and its badge code.",
        args=NoArgs, handler=_list_pipeline_stages, tenancy=Tenancy.TENANT, effect=Effect.READ,
    ),
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
)
