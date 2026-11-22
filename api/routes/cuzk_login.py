"""FastAPI routes for an account's own ČÚZK WSDP login. Mounted under `/account/cuzk-login`.

Each resolves the caller's OWN account (`require_account_id`, a 403 under X-Act-As: a login is never
handed over with a shared board), then opens the service role in the handler (the push routes' shape):
browser roles hold nothing on `account_cuzk_logins`. No route returns the password. A save first proves
the login with the service's free status call and stores nothing when it is refused. Checks are budgeted per
username and per account (`cuzk_login.admit_check`): past the budget the answer is 429 and nothing is sent.
"""

from __future__ import annotations

import datetime
import logging
import uuid
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Response

from api import cuzk_login, tenant_pool, wsdp_client
from api import dependencies as deps

LOG = logging.getLogger(__name__)

router = APIRouter(prefix="/account/cuzk-login", tags=["account"])

_REFUSED = ("ČÚZK refused this login. Check the username and password; "
            "three refused attempts block the ČÚZK account for 15 minutes.")
_NO_ANSWER = "ČÚZK did not answer, so the login could not be checked. Nothing was saved."
_THROTTLED = ("Too many login checks that ČÚZK did not accept. Nothing was sent to ČÚZK this time; "
              "try again in 15 minutes.")
_RETRY_AFTER_S = int(cuzk_login.USERNAME_WINDOW.total_seconds())
_NO_KEY = "Storing a ČÚZK login is not configured on this server."
_PASSWORD_LENGTH = "The password must be 1 to 128 characters long."
_USERNAME = "The username must be 1 to 64 characters long, with no spaces."
_SHAPE = "Send exactly a username and a password, both text."


def _login(body: Any) -> tuple[str, str]:
    """(username, password) of a save's body. Judged by hand, with fixed messages: a schema refusal
    would echo the rejected body, and the body holds a password."""
    if not isinstance(body, dict) or set(body) != {"username", "password"}:
        raise HTTPException(status_code=422, detail=_SHAPE)
    username, password = body["username"], body["password"]
    if not isinstance(username, str) or not isinstance(password, str):
        raise HTTPException(status_code=422, detail=_SHAPE)
    if not 1 <= len(username) <= 64 or any(ch.isspace() for ch in username):
        raise HTTPException(status_code=422, detail=_USERNAME)
    if not 1 <= len(password) <= 128:
        raise HTTPException(status_code=422, detail=_PASSWORD_LENGTH)
    return username, password


@router.get("")
def get_cuzk_login(account_id: uuid.UUID = Depends(tenant_pool.require_account_id)) -> dict[str, Any]:
    """Whether the caller's account has a login stored, under which username, and when it was last accepted."""
    with deps.open_background_conn() as conn:
        return cuzk_login.state(conn, account_id)


@router.put("")
def put_cuzk_login(
    body: Any = Body(),
    claims: dict = Depends(deps.verify_jwt),
    act_as: uuid.UUID | None = Depends(tenant_pool.act_as_header),
) -> dict[str, Any]:
    """Check the login with ČÚZK, then store it encrypted; a refused login stores nothing.

    The account is resolved in a transaction of its own (`own_account_id_released`), not by the
    `require_account_id` dependency: that one holds the request's tenant transaction until the handler
    returns, and this handler waits on ČÚZK for up to 25 s."""
    account_id = tenant_pool.own_account_id_released(claims, act_as, "PUT")
    username, password = _login(body)
    try:
        cuzk_login.require_key()
    except cuzk_login.NotConfigured:
        LOG.warning("CUZK login save refused: %s is not set or not a key", cuzk_login.KEY_ENV)
        raise HTTPException(status_code=503, detail=_NO_KEY) from None
    with deps.open_background_conn() as conn:
        try:
            check_id = cuzk_login.admit_check(conn, account_id, username)
        except cuzk_login.Throttled as exc:
            LOG.warning("CUZK login check throttled by=%s", exc.args[0])
            raise HTTPException(status_code=429, detail=_THROTTLED,
                                headers={"Retry-After": str(_RETRY_AFTER_S)}) from None
    try:
        wsdp_client.check_login(username, password)
    except wsdp_client.WsdpError as exc:
        LOG.warning("CUZK login check failed reason=%s status=%s", exc.reason, exc.status)
        refused = exc.reason == "refused"
        with deps.open_background_conn() as conn:
            cuzk_login.settle_check(conn, check_id, "refused" if refused else "no_answer")
        if refused:
            raise HTTPException(status_code=422, detail=_REFUSED) from None
        raise HTTPException(status_code=502, detail=_NO_ANSWER) from None
    with deps.open_background_conn() as conn:
        cuzk_login.settle_check(conn, check_id, "accepted")
        return cuzk_login.save(conn, account_id, username, password,
                               datetime.datetime.now(datetime.UTC))


@router.delete("", status_code=204, response_class=Response)
def delete_cuzk_login(account_id: uuid.UUID = Depends(tenant_pool.require_account_id)) -> Response:
    """Forget the caller's stored login; with none stored it is still 204."""
    with deps.open_background_conn() as conn:
        cuzk_login.remove(conn, account_id)
    return Response(status_code=204)
