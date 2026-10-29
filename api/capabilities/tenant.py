"""Tools over the signed-in user's own data: each runs in their RLS transaction and sees only their rows."""

from __future__ import annotations

from typing import Any

from api import pipeline
from api.capabilities.entry import Context, Effect, Entry, Tenancy
from api.filter_args import Args


class NoArgs(Args):
    pass  # takes nothing, and still refuses an unknown key (a docstring would reach the schema)


def _list_pipeline_stages(ctx: Context, args: NoArgs) -> dict[str, Any]:
    return pipeline.list_stages(ctx.conn)


ENTRIES = (
    Entry(
        name="list_pipeline_stages",
        description="The stages of the user's deal pipeline board, in board order: id, key, label, position, "
        "colour, whether a stage is the entry stage or a terminal (closed-deal) one, and its badge code.",
        args=NoArgs, handler=_list_pipeline_stages, tenancy=Tenancy.TENANT, effect=Effect.READ,
    ),
)
