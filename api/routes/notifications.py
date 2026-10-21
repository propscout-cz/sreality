"""FastAPI routes for the Watchdog (new-listing notification) surface.

Mounted under `/notifications/*`.

The user-facing surface — subscription CRUD, the dispatch feed, unread-count,
mark-seen/mark-all-seen — runs on the **tenant pool** (`tenant_pool.tenant_conn`,
RLS-scoped by the caller's JWT claims), so every read/write is account-isolated
by the policies migrations 290/292 put on `notification_subscriptions` /
`notification_dispatches`. There is no bypass: `verify_jwt` accepts a real Supabase
JWT and nothing else, and `tenant_conn` has no fallback connection — RLS is a live
boundary on every request. Reads here take no account at all (`current_account_ids()`
is the scope); `POST /subscriptions` is the one write, and it carries exactly one
account via `tenant_pool.require_account_id`. Doctrine:
`.claude/skills/database/references/tenancy.md`.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from api import notifications as nf
from api import tenant_pool
from api.notifications import WatchdogFilterSpec

if TYPE_CHECKING:
    import psycopg

router = APIRouter(prefix="/notifications", tags=["notifications"])


# --- request bodies -------------------------------------------------------


class CreateSubscriptionIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    filter_spec: WatchdogFilterSpec
    is_active: bool = True


class UpdateSubscriptionIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    filter_spec: WatchdogFilterSpec | None = None
    is_active: bool | None = None


# --- subscriptions --------------------------------------------------------


def _refuse_unrenderable(spec: WatchdogFilterSpec | None) -> None:
    """422 for a spec the matcher's one compiler refuses (a retired filter, an agenda violation):
    stored, it would mute its watchdog every pass. The model's own refusals (a chip without a code)
    are FastAPI's 422 before this runs; this is the same answer one layer further in."""
    if spec is None:
        return
    try:
        nf.check_filter_spec(spec)
    except nf.SPEC_REJECTIONS as exc:
        raise HTTPException(status_code=422, detail=f"filter_spec: {exc}") from exc


@router.get("/subscriptions")
def get_subscriptions(
    include_inactive: bool = True,
    conn: Any = Depends(tenant_pool.tenant_conn),
) -> dict[str, Any]:
    rows = nf.list_subscriptions(conn, include_inactive=include_inactive)
    return {"data": rows, "total": len(rows)}


@router.post("/subscriptions")
def post_subscription(
    body: CreateSubscriptionIn,
    conn: Any = Depends(tenant_pool.tenant_conn),
    account_id: uuid.UUID = Depends(tenant_pool.require_account_id),
) -> dict[str, Any]:
    _refuse_unrenderable(body.filter_spec)
    return nf.create_subscription(
        conn,
        name=body.name,
        filter_spec=body.filter_spec,
        is_active=body.is_active,
        account_id=account_id,
    )


@router.get("/subscriptions/{subscription_id}")
def get_subscription(
    subscription_id: str,
    conn: Any = Depends(tenant_pool.tenant_conn),
) -> dict[str, Any]:
    row = nf.get_subscription(conn, subscription_id)
    if row is None:
        raise HTTPException(status_code=404, detail="subscription not found")
    return row


@router.put("/subscriptions/{subscription_id}")
def put_subscription(
    subscription_id: str,
    body: UpdateSubscriptionIn,
    conn: Any = Depends(tenant_pool.tenant_conn),
) -> dict[str, Any]:
    _refuse_unrenderable(body.filter_spec)
    row = nf.update_subscription(
        conn,
        subscription_id,
        name=body.name,
        filter_spec=body.filter_spec,
        is_active=body.is_active,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="subscription not found")
    return row


@router.delete("/subscriptions/{subscription_id}")
def delete_subscription(
    subscription_id: str,
    conn: Any = Depends(tenant_pool.tenant_conn),
) -> dict[str, Any]:
    """Retire the watchdog (F21): it stops matching and leaves the list, its alerts stay as hidden history."""
    if not nf.delete_subscription(conn, subscription_id):
        raise HTTPException(status_code=404, detail="subscription not found")
    return {"deleted": True}


# --- dispatches (the feed) ------------------------------------------------


@router.get("/dispatches")
def get_dispatches(
    subscription_id: str | None = None,
    collection_id: int | None = None,
    source_kind: nf.SourceKindFilter = "all",
    seen: Literal["all", "seen", "unseen"] = "all",
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    cursor: str | None = Query(default=None, description="Keyset cursor (next_cursor)"),
    conn: Any = Depends(tenant_pool.tenant_conn),
) -> dict[str, Any]:
    try:
        return nf.list_dispatches(
            conn,
            subscription_id=subscription_id,
            collection_id=collection_id,
            source_kind=source_kind,
            seen=seen,
            limit=limit,
            offset=offset,
            cursor=cursor,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/unread-count")
def get_unread_count(
    conn: Any = Depends(tenant_pool.tenant_conn),
) -> dict[str, int]:
    """`{"unread_count": N}`, the one unread number; RLS scopes it to the caller's own account."""
    return nf.get_unread_count(conn)


@router.post("/mark-all-seen")
def post_mark_all_seen(
    source_kind: nf.SourceKindFilter = "all",
    conn: Any = Depends(tenant_pool.tenant_conn),
) -> dict[str, int]:
    """Mark every unseen dispatch (optionally scoped to a source) as seen.

    The UPDATE is account-blind SQL; the tenant pool's RLS UPDATE policy scopes it
    to the caller's own dispatches.
    """
    return {"updated": nf.mark_all_seen(conn, source_kind=source_kind)}


@router.post("/dispatches/{dispatch_id}/mark-seen")
def post_mark_seen(
    dispatch_id: str,
    conn: Any = Depends(tenant_pool.tenant_conn),
) -> dict[str, Any]:
    row = nf.mark_dispatch_seen(conn, dispatch_id)
    if row is None:
        raise HTTPException(status_code=404, detail="dispatch not found")
    return row

