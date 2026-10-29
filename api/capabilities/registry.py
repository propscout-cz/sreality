"""Every tool, declared once, and the one path to it: `call` validates, picks the connection, runs, maps, fits."""

from __future__ import annotations

import contextlib
from collections.abc import Iterable, Iterator, Mapping
from types import MappingProxyType
from typing import Any

import psycopg
from pydantic import ValidationError

from api import tenant_pool
from api.capabilities import market, tenant
from api.capabilities.entry import Caller, Context, Effect, Entry, Tenancy
from api.capabilities.errors import ToolError, ToolErrorCode, invalid_arguments, to_tool_error
from api.capabilities.truncate import fit
from api.filter_args import Args


def _index(entries: Iterable[Entry]) -> Mapping[str, Entry]:
    out: dict[str, Entry] = {}
    for entry in entries:
        if entry.name in out:
            raise ValueError(f"two tools are named {entry.name}")
        out[entry.name] = entry
    return MappingProxyType(out)


ENTRIES: Mapping[str, Entry] = _index((*market.ENTRIES, *tenant.ENTRIES))


def validate(name: str, raw_args: object) -> tuple[Entry, Args]:
    """The tool and its parsed arguments, or UNKNOWN_TOOL / INVALID_ARGUMENTS; touches no database."""
    entry = ENTRIES.get(name)
    if entry is None:
        raise ToolError(ToolErrorCode.UNKNOWN_TOOL, f"there is no tool named {name!r}")
    try:
        return entry, entry.args.model_validate(raw_args)
    except ValidationError as exc:
        raise invalid_arguments(exc) from exc


@contextlib.contextmanager
def _connection(caller: Caller, entry: Entry) -> Iterator[psycopg.Connection]:
    """The one place a tool's connection is chosen: the market's, or the caller's own RLS transaction."""
    if entry.tenancy is Tenancy.MARKET:
        yield caller.market
        return
    if caller.claims is None:
        raise ToolError(ToolErrorCode.FORBIDDEN, f"{entry.name} reads the signed-in user's own data")
    # act_as is never set: a run acts for its own user only (MVP: no act-as on runs).
    method = "GET" if entry.effect is Effect.READ else "POST"
    with tenant_pool.tenant_transaction(dict(caller.claims), act_as=None, method=method) as conn:
        yield conn


def call(caller: Caller, name: str, raw_args: object) -> dict[str, Any]:
    """Run one tool; its result envelope, or a ToolError."""
    entry, args = validate(name, raw_args)
    try:
        # Every refusal is raised inside the `with`, so a tenant transaction rolls back before the error is mapped.
        with _connection(caller, entry) as conn:
            result = entry.handler(Context(conn), args)
            if not isinstance(result, dict) or "data" not in result:
                raise TypeError(f"{name} returned no envelope")
            return fit(result)
    except Exception as exc:
        raise to_tool_error(exc, tool=name) from exc


def schemas() -> list[dict[str, Any]]:
    """Every tool as a provider sees it, sorted by name."""
    return [{"name": e.name, "description": e.description, "input_schema": e.input_schema}
            for e in sorted(ENTRIES.values(), key=lambda e: e.name)]
