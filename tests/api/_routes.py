"""The live app's route table walked for the route censuses: its `APIRoute`s, each route's dependencies, its auth bucket."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from fastapi.routing import APIRoute

from api import dependencies as deps
from api import tenant_pool
from api.main import app


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


def _reachable_calls(dependant) -> set:
    """Every dependency callable reachable from a route, including nested ones."""
    seen: set[int] = set()
    out: set = set()
    stack = list(dependant.dependencies)
    while stack:
        d = stack.pop()
        call = getattr(d, "call", None)
        if call is not None and id(call) not in seen:
            seen.add(id(call))
            out.add(call)
        stack.extend(d.dependencies)
    return out


def _bucket(route: APIRoute) -> str:
    calls = _reachable_calls(route.dependant)
    if deps.require_admin in calls:
        return "admin"
    if tenant_pool.tenant_conn in calls or deps.verify_jwt in calls:
        return "tenant"
    if deps.require_token in calls or deps.token_claims in calls:
        return "token"
    # account_scope is require_token's reader (`_bearer`: a verified JWT) plus a
    # read scope, so it buckets with it.
    if deps.account_scope in calls:
        return "token"
    return "public"


def _api_routes() -> list[tuple[str, str, APIRoute]]:
    return [
        (method, r.path, r)
        for r in flat_routes(app.routes)
        for method in sorted(r.methods - {"HEAD", "OPTIONS"})
    ]
