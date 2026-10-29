"""Every tool, declared once, and the one path to it: `call` validates, picks the connection, runs, maps, fits,
records."""

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
from api.capabilities.errors import ToolError, ToolErrorCode, busy, invalid_arguments, to_tool_error
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
        # A market tool only reads: bounded like a tenant GET, in its own transaction on the autocommit service
        # connection, so the SET LOCALs end at its COMMIT or ROLLBACK and never outlive the call.
        with caller.market.transaction():
            with caller.market.cursor() as cur:
                tenant_pool.bound_read(cur)
            yield caller.market
        return
    if caller.claims is None:
        raise ToolError(ToolErrorCode.FORBIDDEN, f"{entry.name} reads the signed-in user's own data")
    # act_as is never set: a run acts for its own user only (MVP: no act-as on runs).
    method = "GET" if entry.effect is Effect.READ else "POST"
    with tenant_pool.tenant_transaction(dict(caller.claims), act_as=None, method=method) as conn:
        yield conn


def call(caller: Caller, name: str, raw_args: object) -> dict[str, Any]:
    """Run one tool; its result envelope, or a ToolError. The caller's recorder sees whichever, once."""
    try:
        entry, args = validate(name, raw_args)
        # Every refusal is raised inside the `with`, so a tenant transaction rolls back before the error is mapped.
        with _connection(caller, entry) as conn:
            result = entry.handler(Context(conn), args)
            # A handler that swallowed a database error answered without that read (rent_map's reference swallows
            # any, a time-limit cancel included). The usual cause is that cancel, hence its retryable BUSY.
            if conn.info.transaction_status is psycopg.pq.TransactionStatus.INERROR:
                raise busy()
            if not isinstance(result, dict) or "data" not in result:
                raise TypeError(f"{name} returned no envelope")
            result = fit(result)
    except Exception as exc:
        error = to_tool_error(exc, tool=name)
        if caller.record is not None:
            caller.record(name, raw_args, error.payload())
        if error is exc:
            raise
        raise error from exc
    if caller.record is not None:
        caller.record(name, raw_args, result)
    return result


def schemas() -> list[dict[str, Any]]:
    """Every tool as a provider sees it, sorted by name."""
    return [{"name": e.name, "description": e.description, "input_schema": e.input_schema}
            for e in sorted(ENTRIES.values(), key=lambda e: e.name)]
