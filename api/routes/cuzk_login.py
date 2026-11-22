"""FastAPI routes for an account's own ČÚZK WSDP login. Mounted under `/account/cuzk-login`.

Each resolves the caller's OWN account (`require_account_id`, a 403 under X-Act-As: a login is never
handed over with a shared board), then opens the service role in the handler (the push routes' shape):
browser roles hold nothing on `account_cuzk_logins`. No route returns the password. A save first proves
the login with the service's free status call and stores nothing when it is refused.
"""

from __future__ import annotations

import datetime
import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field

from api import cuzk_login, tenant_pool, wsdp_client
from api import dependencies as deps

LOG = logging.getLogger(__name__)

router = APIRouter(prefix="/account/cuzk-login", tags=["account"])

_REFUSED = ("ČÚZK refused this login. Check the username and password; "
            "three refused attempts block the ČÚZK account for 15 minutes.")
_NO_ANSWER = "ČÚZK did not answer, so the login could not be checked. Nothing was saved."
_NO_KEY = "Storing a ČÚZK login is not configured on this server."
_PASSWORD_LENGTH = "The password must be 1 to 128 characters long."


class CuzkLoginIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)

    username: str = Field(min_length=1, max_length=64, pattern=r"^\S+$")
    # Checked in the handler: a pydantic refusal would echo the rejected value, here a password.
    password: str


@router.get("")
def get_cuzk_login(account_id: uuid.UUID = Depends(tenant_pool.require_account_id)) -> dict[str, Any]:
    """Whether the caller's account has a login stored, under which username, and when it was last accepted."""
    with deps.open_background_conn() as conn:
        return cuzk_login.state(conn, account_id)


@router.put("")
def put_cuzk_login(
    body: CuzkLoginIn,
    account_id: uuid.UUID = Depends(tenant_pool.require_account_id),
) -> dict[str, Any]:
    """Check the login with ČÚZK, then store it encrypted; a refused login stores nothing."""
    if not 1 <= len(body.password) <= 128:
        raise HTTPException(status_code=422, detail=_PASSWORD_LENGTH)
    try:
        cuzk_login.require_key()
    except cuzk_login.NotConfigured:
        LOG.warning("CUZK login save refused: %s is not set or not a key", cuzk_login.KEY_ENV)
        raise HTTPException(status_code=503, detail=_NO_KEY) from None
    try:
        wsdp_client.check_login(body.username, body.password)
    except wsdp_client.WsdpError as exc:
        LOG.warning("CUZK login check failed reason=%s status=%s", exc.reason, exc.status)
        if exc.reason == "refused":
            raise HTTPException(status_code=422, detail=_REFUSED) from None
        raise HTTPException(status_code=502, detail=_NO_ANSWER) from None
    with deps.open_background_conn() as conn:
        return cuzk_login.save(conn, account_id, body.username, body.password,
                               datetime.datetime.now(datetime.UTC))


@router.delete("", status_code=204, response_class=Response)
def delete_cuzk_login(account_id: uuid.UUID = Depends(tenant_pool.require_account_id)) -> Response:
    """Forget the caller's stored login; with none stored it is still 204."""
    with deps.open_background_conn() as conn:
        cuzk_login.remove(conn, account_id)
    return Response(status_code=204)
