"""The one claims builder: a run's principal and account become the Caller its tools run under."""

from __future__ import annotations

import uuid

import psycopg

from api.capabilities.entry import RUN_ACCOUNT_CLAIM, Caller


def caller_for(market: psycopg.Connection, *, principal_user_id: uuid.UUID, account_id: uuid.UUID) -> Caller:
    """Keyword-only with no default: a NULL principal or account fails here, never as a wider read."""
    if not principal_user_id or not account_id:
        raise ValueError("a run needs a principal and an account")
    return Caller(market=market, claims={"sub": str(principal_user_id), "role": "authenticated",
                                         RUN_ACCOUNT_CLAIM: str(account_id)})
