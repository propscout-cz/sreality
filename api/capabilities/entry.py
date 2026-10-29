"""What a tool is (an Entry) and the two contexts its call passes through."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

import psycopg

from api.capabilities.schema import portable
from api.filter_args import Args


class Tenancy(StrEnum):
    """Whose data a tool reads: the market's, on the service connection, or the signed-in user's own, under RLS."""

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
    """What a transport hands `call`: the autocommit service connection, the caller's verified JWT claims, and
    the run's recorder, handed (tool name, raw arguments, the envelope or the error payload) once per call."""

    market: psycopg.Connection
    claims: Mapping[str, Any] | None = None
    record: Callable[[str, object, dict[str, Any]], None] | None = None


@dataclass(frozen=True)
class Context:
    """What a handler gets: the one connection its tenancy picked."""

    conn: psycopg.Connection


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
