"""The live app's route table flattened to its `APIRoute`s, the one walker the route censuses share."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from fastapi.routing import APIRoute


def flat_routes(routes: Iterable[Any]) -> list[APIRoute]:
    """Every `APIRoute` under `routes`, recursing through each included router."""
    # Older FastAPI splices an included router's routes into app.routes; since 0.13x `include_router` appends one
    # `_IncludedRouter` wrapper holding the original router. Without recursing through either shape this returns
    # almost nothing and a census over it passes vacuously (each census keeps a mount sentinel for that).
    out: list[APIRoute] = []
    for r in routes:
        if isinstance(r, APIRoute):
            out.append(r)
            continue
        nested = getattr(getattr(r, "original_router", None), "routes", None)
        if nested is None:
            nested = getattr(r, "routes", None)
        if nested:
            out.extend(flat_routes(nested))
    return out
