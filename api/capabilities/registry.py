"""Every tool, declared once, and the one path to it: `call` validates, picks the connection, runs, maps, fits. A write has
no path here: `validate` hands it to the Apply route's `api/agents/proposals.py`."""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import Iterable, Iterator, Mapping
from types import MappingProxyType
from typing import Any

import psycopg
from pydantic import ValidationError

from api import tenant_pool
from api.capabilities import market, run_scope, tenant, valuo_tool, visuals, writes
from api.capabilities.entry import RUN_ACCOUNT_CLAIM, Caller, Context, Effect, Entry, Tenancy
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


_ACCOUNTS_UNDER_LOGIN = "SELECT current_account_ids()"

ENTRIES: Mapping[str, Entry] = _index((*market.ENTRIES, *tenant.ENTRIES, *visuals.ENTRIES, *valuo_tool.ENTRIES, *writes.ENTRIES))


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
def _connection(caller: Caller, entry: Entry) -> Iterator[Context]:
    """The one place a tool's connection is chosen: the market's, the caller's own RLS transaction, or, for a tenant's
    paid tool, the market's once the login has named the account."""
    run = run_scope.current()
    if entry.tenancy is Tenancy.MARKET:
        # A paid market fact waits on a model and then caches what it paid for: not inside a transaction, and not READ
        # ONLY. Each of its statements commits alone on the autocommit connection.
        if entry.effect is Effect.COSTLY:
            yield Context(caller.market, run=run)
            return
        # A market tool that reads: bounded like a tenant GET, in its own transaction on the autocommit service
        # connection, so the SET LOCALs end at its COMMIT or ROLLBACK and never outlive the call.
        with caller.market.transaction():
            with caller.market.cursor() as cur:
                tenant_pool.bound_read(cur)
            yield Context(caller.market, run=run)
        return
    if caller.claims is None:
        raise ToolError(ToolErrorCode.FORBIDDEN, f"{entry.name} reads the signed-in user's own data")
    # act_as is never set: a run acts for its own user only (MVP: no act-as on runs).
    if entry.effect is Effect.COSTLY:
        # A tenant's paid tool: the login names the account and is gone before the money, since a transaction must not span
        # a paid call and a ledger write is the service role's alone (rule 12). Each statement commits alone, as above.
        with tenant_pool.tenant_transaction(dict(caller.claims), act_as=None, method="GET") as conn:
            with conn.cursor() as cur:
                cur.execute(_ACCOUNTS_UNDER_LOGIN)
                accounts = [str(row[0]) for row in cur.fetchall()]
        if accounts != [caller.claims.get(RUN_ACCOUNT_CLAIM)]:
            raise ToolError(ToolErrorCode.FORBIDDEN, f"{entry.name} spends only for the account a run holds")
        yield Context(caller.market, run=run, account_id=uuid.UUID(accounts[0]))
        return
    method = "GET" if entry.effect is Effect.READ else "POST"
    with tenant_pool.tenant_transaction(dict(caller.claims), act_as=None, method=method) as conn:
        yield Context(conn, run=run)


def call(caller: Caller, name: str, raw_args: object) -> dict[str, Any]:
    """Run one tool; its result envelope, or a ToolError."""
    entry, args = validate(name, raw_args)
    # ADR-0004: a write runs only in `api/agents/proposals.py`, in the transaction that marks it applied, on the user's click.
    if entry.effect is Effect.WRITE:
        raise ToolError(ToolErrorCode.FORBIDDEN, f"{name} changes the user's data: only they can apply it")
    try:
        # Every refusal is raised inside the `with`, so a tenant transaction rolls back before the error is mapped.
        with _connection(caller, entry) as ctx:
            result = entry.handler(ctx, args)
            # A handler that swallowed a database error answered without that read (rent_map's reference swallows
            # any, a time-limit cancel included). The usual cause is that cancel, hence its retryable BUSY.
            if ctx.conn.info.transaction_status is psycopg.pq.TransactionStatus.INERROR:
                raise busy()
            if not isinstance(result, dict) or "data" not in result:
                raise TypeError(f"{name} returned no envelope")
            return fit(result)
    except Exception as exc:
        raise to_tool_error(exc, tool=name) from exc


def schemas() -> list[dict[str, Any]]:
    """Every tool as a provider sees it, sorted by name."""
    return [{"name": e.name, "description": e.description, "input_schema": e.input_schema}
            for e in sorted(ENTRIES.values(), key=lambda e: e.name)]
