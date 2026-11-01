"""What a tool is (an Entry) and the two contexts its call passes through."""

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any

import psycopg

from api.capabilities.run_scope import RunScope
from api.capabilities.schema import portable
from api.filter_args import Args


# Narrows the identity functions (migration 704): one account of the principal's memberships, no admin powers, no acting
# for another account. It can only remove power. A claim rather than a new setting, so `tenant_transaction` stays as it is.
RUN_ACCOUNT_CLAIM = "run_account_id"


class Tenancy(StrEnum):
    """Whose data a tool reads: the market's, on the service connection, or the signed-in user's own, under RLS (a paid
    tool's account comes from the login, its ledger write from the service role)."""

    MARKET = "market"
    TENANT = "tenant"


class Effect(StrEnum):
    """What a call does beyond reading: change the user's data, spend money, or run code."""

    READ = "read"
    WRITE = "write"
    COSTLY = "costly"
    CODE = "code"


@dataclass(frozen=True)
class Caller:
    """What a transport hands `call`: the autocommit service connection and the caller's verified JWT claims."""

    market: psycopg.Connection
    claims: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class Context:
    """What a handler gets: the one connection its tenancy picked, the run it is called in, if any, and, for a tenant's
    paid tool, the account that run spends for."""

    conn: psycopg.Connection
    run: RunScope | None = None
    account_id: uuid.UUID | None = None


@dataclass(frozen=True)
class Entry:
    """One tool. No field has a default: a tool cannot be declared without its tenancy and effect."""

    name: str
    description: str
    args: type[Args]
    handler: Callable[[Context, Any], dict[str, Any]]
    tenancy: Tenancy
    effect: Effect

    @property
    def input_schema(self) -> dict[str, Any]:
        return portable(self.args)


@dataclass(frozen=True)
class UndoCheck:
    """Whether Undo can work now, in words the user reads: why not, or what it will leave behind."""

    available: bool
    says: str | None = None


@dataclass(frozen=True)
class WriteEntry(Entry):
    """A write the user applies (ADR-0004): never called by a run. `handler` returns `data` (with the `answer` the user
    reads) and `undo`, the record its inverse takes. Each of `draw`, `undo_check` and `undo` takes the transaction's Context;
    the last two also take that record and the Apply's stamp, which is the stamp every row it wrote carries."""

    draw: Callable[[Context, Any], dict[str, Any]]
    undo_check: Callable[[Context, Mapping[str, Any], datetime], UndoCheck]
    undo: Callable[[Context, Mapping[str, Any], datetime], str]
    # Undo removes something rule 22 lets a surface remove only behind a confirm step.
    undo_confirm: bool = False
