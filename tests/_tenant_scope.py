"""The ONE way a live lane scopes a statement as a tenant: the shape api.tenant_pool gives a request.

SET LOCAL ROLE authenticated, then the transaction-local GUCs PostgREST sets on every request and
the API's tenant transaction mirrors: `request.jwt.claims` (the caller), `request.headers` (JSON,
lower-case keys; `x-act-as` names the owner a pipeline share lets the caller act for, migration 641)
and `request.method` (a read share acts on GET/HEAD only). Everything evaporates at transaction end.
"""

from __future__ import annotations

import contextlib
import json
import os
import uuid
from collections.abc import Iterator
from typing import Any


def _enter(cur: Any, sub: uuid.UUID, act_as: uuid.UUID | str | None, method: str) -> None:
    cur.execute("SET LOCAL ROLE authenticated")
    cur.execute(
        "SELECT set_config('request.jwt.claims', %s, true)",
        (json.dumps({"sub": str(sub), "role": "authenticated"}),),
    )
    # Always set, like PostgREST: a cursor-mode scope reused in one transaction must not inherit
    # the previous scope's header.
    cur.execute(
        "SELECT set_config('request.headers', %s, true)",
        (json.dumps({"x-act-as": str(act_as)} if act_as else {}),),
    )
    cur.execute("SELECT set_config('request.method', %s, true)", (method,))


@contextlib.contextmanager
def scoped(
    sub: uuid.UUID,
    *,
    act_as: uuid.UUID | str | None = None,
    method: str = "GET",
    cur: Any = None,
) -> Iterator[Any]:
    """Scope as tenant `sub` (optionally acting for `act_as`) on a fresh connection in one
    transaction, yielding it; or, given `cur`, inside the caller's transaction (RESET ROLE on exit)."""
    if cur is not None:
        _enter(cur, sub, act_as, method)
        yield cur.connection
        # Not in a `finally`: after a failed statement the transaction is aborted and the caller's
        # rollback ends the role with it; a RESET there would mask the real error. The header is
        # cleared too, or the owner login's next write would be stamped as a delegated edit.
        cur.execute("RESET ROLE")
        cur.execute("SELECT set_config('request.headers', '{}', true)")
        return

    import psycopg

    conn = psycopg.connect(os.environ["TEST_DATABASE_URL"], autocommit=False)
    try:
        with conn.transaction():
            with conn.cursor() as c:
                _enter(c, sub, act_as, method)
            yield conn
    finally:
        conn.close()


def tool_call(cur: Any, sub: uuid.UUID, name: str, raw_args: object) -> dict[str, Any]:
    """Tool `name` as tenant `sub` inside the caller's transaction, so it reads the test's uncommitted seeds: its
    arguments validated as `registry.call` validates them, its handler run under the user's RLS. The bound, the error
    mapping, the fit and the recorder are the call path's own, pinned by tests/test_capabilities_live.py."""
    from api.capabilities import registry
    from api.capabilities.entry import Context

    entry, args = registry.validate(name, raw_args)
    with scoped(sub, cur=cur) as conn:
        return entry.handler(Context(conn), args)
