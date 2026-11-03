"""The notification email's routes (docs/design/email/PROGRAM.md PR 4; the sending is api/notification_email.py).

`GET` / `PUT /account/notification-email`: the account's own choice, `{"mode": "off" | "daily" | "instant"}` (no row reads
as daily). They resolve the caller's OWN account (`require_account_id`, a 403 under X-Act-As: a board shared with
someone never hands them its owner's email), then open the service role in the handler, the push routes' shape:
browser roles hold nothing on the table.

`/notifications/email/unsubscribe?t=`: every notification email's off link, OPEN BY DESIGN
(tests/api/test_admin_route_coverage.py `_PUBLIC_ALLOWLIST`): its reader may not be signed in, and the account's
`unsubscribe_token` is the only credential. GET only asks (mail scanners open links); POST sets the mode to `off`, and
is what a mail client's own button sends (RFC 8058). An unknown token and a repeat read the same, so a link cannot tell
anyone whether a token exists.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from api import dependencies as deps
from api import notification_email, tenant_pool, waitlist
from api.routes.waitlist import _client, _page, _token

LOG = logging.getLogger(__name__)

router = APIRouter(tags=["notification-email"])

_UNSUBSCRIBE_LIMIT = waitlist.RateLimit(per_client=30, total=600, window_s=600)
_INVALID = ("Neplatný odkaz", "<p>Tento odkaz pro vypnutí e-mailů není platný.</p>")
_LATER = ("Zkuste to prosím později", "<p>Vypnutí se teď nepodařilo. Zkuste to za pár minut.</p>")


class NotificationEmailIn(BaseModel):
    mode: Literal["off", "daily", "instant"]


@router.get("/account/notification-email")
def get_notification_email(account_id: uuid.UUID = Depends(tenant_pool.require_account_id)) -> dict[str, Any]:
    """The account's choice; `daily` while it has no row."""
    with deps.open_background_conn() as conn:
        return {"mode": notification_email.read_mode(conn, account_id)}


@router.put("/account/notification-email")
def put_notification_email(
    body: NotificationEmailIn,
    account_id: uuid.UUID = Depends(tenant_pool.require_account_id),
) -> dict[str, Any]:
    """Set the account's choice; idempotent."""
    with deps.open_background_conn() as conn:
        return {"mode": notification_email.set_mode(conn, account_id, body.mode)}


@router.get("/notifications/email/unsubscribe")
def get_notification_email_unsubscribe(t: str = "") -> HTMLResponse:
    """Ask first: the switch-off is the POST, so a scanner opening the link changes nothing."""
    token = _token(t)
    if token is None:
        return _page(*_INVALID, 400)
    return _page(
        "Vypnout e-mailová upozornění",
        "<p>Po vypnutí vám už nepošleme e-mail o změnách u nabídek, které hlídáte. Upozornění v aplikaci zůstanou a "
        f'zapnout je můžete znovu pod svým profilem.</p><form method="post" action="?t={token}">'
        "<button>Vypnout</button></form>",
    )


@router.post("/notifications/email/unsubscribe")
async def post_notification_email_unsubscribe(request: Request, t: str = "") -> HTMLResponse:
    """Set the token's account to `off`; a repeat or an unknown token reads the same."""
    token = _token(t)
    if token is None:
        return _page(*_INVALID, 400)
    if not _UNSUBSCRIBE_LIMIT.allow(_client(request)):
        return _page(*_LATER, 429)

    def write() -> bool:
        with deps.open_background_conn() as conn:
            return notification_email.unsubscribe(conn, token)

    try:
        switched = await run_in_threadpool(write)
    except Exception:
        LOG.exception("EMAIL unsubscribe failed")
        return _page(*_LATER, 503)
    LOG.info("EMAIL unsubscribe new=%d", switched)
    return _page("E-maily jsou vypnuté", "<p>Už vám nepošleme e-mail o změnách. Zapnout je můžete znovu v aplikaci "
                 "pod svým profilem.</p>")
