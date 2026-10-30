"""Per-request tenant-scoped DB transaction (Phase 1 Amendment A1).

ONE explicit transaction per request, autocommit off: SET LOCAL ROLE +
the verified JWT claims are issued first, and the SAME transaction serves the
route handler's reads AND writes (a SET LOCAL evaporates when its transaction
ends, so a post-commit read-back on another transaction would run claim-less
and RLS would hide the row just written). Distinct from
api.dependencies.get_db_conn (service-role, autocommit, unscoped) — this is
the RLS-enforced path every per-account route must use: verify_jwt alone is
authentication, not authorization.

The pool role (migration 293) is LOGIN + NOINHERIT with zero direct grants;
data access exists only under the explicit `SET LOCAL ROLE authenticated`,
so a code path that forgets the switch fails closed, never leaks.

`tenant_transaction` is the same scoped transaction as a plain context manager,
for the route that must not hold it across slow service-role work and for the
tool registry's tenant tools (api/capabilities/registry.py).

A GET/HEAD transaction is a bounded read, as PostgREST runs the SPA's own (`bound_read`):
READ ONLY, so a write there fails loudly, and under `authenticated`'s 8 s statement budget.

Delegation (migration 641): the transaction also carries the request's method and,
when the caller sent `X-Act-As`, that header, as the GUCs PostgREST sets itself
(`request.method`, `request.headers`), so `acting_account_ids()` reads one shape on
both paths. Whether a WRITE may act is declared per route: `require_account_id`
(the caller's own account; 403 under the header) or `require_acting_account_id`
(the account the caller acts for on the eleven curation tables).
"""

from __future__ import annotations

import contextlib
import json
import os
import uuid
from collections.abc import Iterator

import psycopg
from fastapi import Depends, Header, HTTPException, Request

from api import dependencies as deps

_TENANT_POOL_ENV = "TENANT_POOL_DB_URL"

# PostgREST runs a signed-in GET/HEAD READ ONLY under `authenticated`'s own statement_timeout (8 s, that
# role's pg_db_role_setting in production). SET ROLE never applies a role's settings, so without this the
# API's reads ran on the 120 s server default. A constant, not a lookup: the CI replay's roles carry no settings.
READ_BOUND = ("SET TRANSACTION READ ONLY", "SET LOCAL statement_timeout = '8s'")


def bound_read(cur: psycopg.Cursor) -> None:
    """Make the open transaction a bounded read; both settings end with it."""
    for statement in READ_BOUND:
        cur.execute(statement)


def read_conn(conn: psycopg.Connection = Depends(deps.get_db_conn)) -> "Iterator[psycopg.Connection]":
    """FastAPI dependency: the service connection as one bounded read, held for the request like `tenant_conn`."""
    with conn.transaction():
        with conn.cursor() as cur:
            bound_read(cur)
        yield conn


@contextlib.contextmanager
def tenant_transaction(
    claims: dict, *, act_as: uuid.UUID | None, method: str,
) -> "Iterator[psycopg.Connection]":
    """One RLS-scoped transaction for `claims`, committed on a clean exit; no fallback connection."""
    dsn = os.environ.get(_TENANT_POOL_ENV)
    if not dsn:
        raise RuntimeError(f"{_TENANT_POOL_ENV} not configured")

    # prepare_threshold=None: the transaction-mode pooler rebinds physical
    # backends between transactions (scraper/db.py's standing rationale) —
    # which is also why the role/claims MUST be re-issued inside every
    # transaction rather than set once per connection.
    conn = psycopg.connect(dsn, autocommit=False, prepare_threshold=None)
    try:
        with conn.transaction():
            with conn.cursor() as cur:
                # The database grants a read share on GET/HEAD alone, whatever the statement does, so a
                # write there must fail loudly; first, before anything else runs in the transaction.
                if method in ("GET", "HEAD"):
                    bound_read(cur)
                cur.execute("SET LOCAL ROLE authenticated")
                # set_config(), NOT "SET LOCAL ... = %s": SET takes only a
                # literal (a bind parameter is a syntax error), and f-string
                # interpolation would be an injection surface — claims carry
                # attacker-shaped strings from the caller's own JWT.
                cur.execute(
                    "SELECT set_config('request.jwt.claims', %s, true)",
                    (json.dumps(claims),),
                )
                cur.execute("SELECT set_config('request.method', %s, true)", (method,))
                if act_as is not None:
                    cur.execute(
                        "SELECT set_config('request.headers', %s, true)",
                        (json.dumps({"x-act-as": str(act_as)}),),
                    )
            yield conn
        # conn.transaction() commits on clean resumption, rolls back on the
        # caller's exception — reads and writes share this one block.
    finally:
        conn.close()


def act_as_header(
    x_act_as: str | None = Header(default=None, alias="X-Act-As"),
) -> uuid.UUID | None:
    """The owner the caller asks to act for, or None; a non-uuid is a 400 before any SQL."""
    if x_act_as is None or not x_act_as.strip():
        return None
    try:
        return uuid.UUID(x_act_as.strip())
    except ValueError:
        raise HTTPException(status_code=400, detail="X-Act-As must be an account id")


def request_method(request: Request) -> str:
    return request.method


def tenant_conn(
    claims: dict = Depends(deps.verify_jwt),
    act_as: uuid.UUID | None = Depends(act_as_header),
    method: str = Depends(request_method),
) -> "Iterator[psycopg.Connection]":
    """FastAPI dependency: one transaction, RLS-scoped to the caller's accounts
    (or, under X-Act-As, to the owner a share lets them act for on curation),
    held for the whole request (it commits at dependency teardown).

    verify_jwt is a dependency of THIS function; FastAPI caches dependency
    results per request, so a route that also declares Depends(verify_jwt)
    pays no second verification — and act_as_header likewise.
    """
    with tenant_transaction(claims, act_as=act_as, method=method) as conn:
        yield conn


# A deterministic pick: account_members has only a composite PK, so a user with >1 membership
# (already legal — team/multi-account is anticipated) would otherwise resolve to a
# per-request-arbitrary account. Stable oldest-membership-wins until a real primary-account
# concept exists; ONE constant so a share lands on the account its recipient writes under.
_FIRST_MEMBERSHIP_ORDER = "ORDER BY m.created_at, m.account_id"

_RESOLVE_ACCOUNT_SQL = (
    "SELECT m.account_id FROM account_members m WHERE m.user_id = %s "
    f"{_FIRST_MEMBERSHIP_ORDER} LIMIT 1"
)

_ACCOUNT_FOR_EMAIL_SQL = (
    "SELECT m.account_id FROM auth.users u JOIN account_members m ON m.user_id = u.id "
    "WHERE lower(u.email) = lower(%s) AND u.email_confirmed_at IS NOT NULL "
    f"{_FIRST_MEMBERSHIP_ORDER} LIMIT 1"
)


def resolve_account_id(conn: psycopg.Connection, claims: dict) -> uuid.UUID | None:
    """The caller's own (first) account, or None when the user has no membership."""
    with conn.cursor() as cur:
        cur.execute(_RESOLVE_ACCOUNT_SQL, (claims["sub"],))
        row = cur.fetchone()
    return row[0] if row else None


def account_for_email(conn: psycopg.Connection, email: str) -> uuid.UUID | None:
    """A confirmed user's first account on the SERVICE-ROLE conn; None is the share route's 422, never a scope."""
    with conn.cursor() as cur:
        cur.execute(_ACCOUNT_FOR_EMAIL_SQL, (email,))
        row = cur.fetchone()
    return row[0] if row else None


def require_account_id(
    conn: psycopg.Connection = Depends(tenant_conn),
    claims: dict = Depends(deps.verify_jwt),
    act_as: uuid.UUID | None = Depends(act_as_header),
) -> uuid.UUID:
    """The caller's OWN account a write is scoped to, resolved once at the route edge.

    A caller with no membership is a loud 400, never a silently empty 200 or an
    opaque 500: every account-predicated write column is NOT NULL or refused NULL by
    WITH CHECK (290/295/608), and RLS's WITH CHECK can only validate the account a row claims, not
    choose one — so a route that forwards None writes a row RLS is guaranteed to
    reject, or predicates a DELETE/UPDATE that matches nothing.

    Lives in tenant_pool, not api.dependencies: this module imports that one
    (tenant_conn's verify_jwt default), so Depends(tenant_conn) over there is a
    circular import — and a lazily-imported wrapper would be a SECOND callable,
    which FastAPI caches separately, splitting a route's reads and writes across
    two tenant transactions (the Amendment A1 boundary).
    """
    # A route declaring it never acts (watchdog, presets, the stats board, sharing): 403, not ignored.
    if act_as is not None:
        raise HTTPException(status_code=403, detail="this route does not act for another account")
    account_id = resolve_account_id(conn, claims)
    if account_id is None:
        raise HTTPException(status_code=400, detail="no account for caller")
    return account_id


_ACTING_ACCOUNT_SQL = "SELECT DISTINCT a.account_id FROM acting_account_ids() a"


def require_acting_account_id(
    conn: psycopg.Connection = Depends(tenant_conn),
    claims: dict = Depends(deps.verify_jwt),
    act_as: uuid.UUID | None = Depends(act_as_header),
) -> uuid.UUID:
    """A curation write's account: the caller's own, or under X-Act-As the owner of an edit share, else 403."""
    if act_as is None:
        return require_account_id(conn, claims, None)
    with conn.cursor() as cur:
        cur.execute(_ACTING_ACCOUNT_SQL)
        rows = cur.fetchall()
    if len(rows) != 1:
        raise HTTPException(status_code=403, detail="no edit share from that account")
    return rows[0][0]
