"""FastAPI routes for push on this device (notifications push W4, PROGRAM.md § 3.4). Mounted under `/push/*`.

`GET /push/key` is any signed-in caller's (no connection: the public key is derived from VAPID_PRIVATE_KEY). The
three writes resolve the caller's OWN account (`require_account_id`, a 403 under X-Act-As: a push belongs to the
signed-in user, never to an account acted for), then open the service role in the handler (the share routes'
shape, `.claude/skills/database/references/tenancy.md`): browser roles hold nothing on either push table. Each
answers 204 with no body and is idempotent; an endpoint rides in a JSON body, never a URL.
"""

from __future__ import annotations

import logging
import os
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Response
from pydantic import BaseModel, Field

from api import dependencies as deps
from api import push, push_devices, tenant_pool

LOG = logging.getLogger(__name__)

router = APIRouter(prefix="/push", tags=["push"])


class PushKeys(BaseModel):
    p256dh: str
    auth: str


class PushSubscriptionIn(BaseModel):
    """`PushSubscription.toJSON()` plus the browser's user agent (frontend/src/lib/api.ts)."""

    endpoint: str
    expirationTime: float | None = None
    keys: PushKeys
    user_agent: str | None = None


class PushSubscriptionEndIn(BaseModel):
    endpoint: str
    reason: Literal["signed_out", "switched_off"]


class PushOpenedIn(BaseModel):
    delivery: int = Field(ge=1, le=9_223_372_036_854_775_807)


def _allowed_origins() -> list[str]:
    """CORS_ALLOW_ORIGINS as api/main.py reads it: the app origins a device may register from."""
    return [o.strip() for o in os.environ.get("CORS_ALLOW_ORIGINS", "").split(",") if o.strip()]


@router.get("/key", dependencies=[Depends(deps.require_token)])
def get_push_key() -> dict[str, str]:
    """The VAPID public key the browser subscribes with; 503 while push is not configured."""
    raw = os.environ.get(push.VAPID_ENV, "").strip()
    if raw:
        try:
            return {"public_key": push.public_key_b64url(raw)}
        except ValueError:
            LOG.warning("PUSH %s is set but unreadable; GET /push/key answers 503", push.VAPID_ENV)
    raise HTTPException(status_code=503, detail="push is not configured")


@router.put("/subscription", status_code=204, response_class=Response)
def put_push_subscription(
    body: PushSubscriptionIn,
    origin: str | None = Header(default=None),
    account_id: uuid.UUID = Depends(tenant_pool.require_account_id),
) -> Response:
    """Register (or re-send) this device for the caller: idempotent from the first call."""
    device = push_devices.validate(body.endpoint, body.keys.p256dh, body.keys.auth, body.user_agent, origin,
                                   _allowed_origins())
    with deps.open_background_conn() as conn:
        push_devices.register(conn, account_id, device)
    return Response(status_code=204)


@router.delete("/subscription", status_code=204, response_class=Response)
def delete_push_subscription(
    body: PushSubscriptionEndIn,
    account_id: uuid.UUID = Depends(tenant_pool.require_account_id),
) -> Response:
    """End the caller's row for this device (signed out, or switched off); an unknown endpoint is still 204."""
    with deps.open_background_conn() as conn:
        push_devices.end(conn, account_id, body.endpoint, body.reason)
    return Response(status_code=204)


@router.post("/opened", status_code=204, response_class=Response)
def post_push_opened(
    body: PushOpenedIn,
    account_id: uuid.UUID = Depends(tenant_pool.require_account_id),
) -> Response:
    """A tapped push reports its delivery: `opened_at` once, on the caller's own; an unknown id is still 204."""
    with deps.open_background_conn() as conn:
        push_devices.mark_opened(conn, account_id, body.delivery)
    return Response(status_code=204)
