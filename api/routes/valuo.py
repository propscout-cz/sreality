"""GET + POST /properties/{id}/valuo, the property page's Valuo section (api/valuo.py does the work). Every
signed-in user may value a property (operator ruling 2026-10-19), so both take `verify_jwt` and touch only the
caller's own account's rows; the account is resolved by hand on the service-role connection, because
`require_account_id`'s tenant transaction would stay open across the paid call."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from api import dependencies as deps
from api import schemas as s
from api import tenant_pool, valuo, valuo_client

router = APIRouter(tags=["valuo"])


def _account(conn: Any, claims: dict) -> uuid.UUID:
    account = tenant_pool.resolve_account_id(conn, claims)
    if account is None:
        raise HTTPException(status_code=400, detail={
            "code": "no_account", "message": "Přihlášený uživatel nemá účet, ocenění nelze uložit."})
    return account


@router.get("/properties/{property_id}/valuo")
def get_property_valuo(
    property_id: int,
    claims: dict = Depends(deps.verify_jwt),
    conn: Any = Depends(deps.get_db_conn),
) -> dict[str, Any]:
    return valuo.page(conn, property_id, account_id=_account(conn, claims), cred=valuo_client.credential())


@router.post("/properties/{property_id}/valuo")
def post_property_valuo(
    property_id: int,
    body: s.ValuoRunIn,
    claims: dict = Depends(deps.verify_jwt),
    conn: Any = Depends(deps.get_db_conn),
) -> dict[str, Any]:
    account = _account(conn, claims)
    cred = valuo_client.credential()
    if cred is None:
        raise HTTPException(status_code=503, detail={
            "code": "valuo_unconfigured", "message": valuo_client.ERRORS["unconfigured"]})
    return valuo.run(conn, property_id, body.overrides, account_id=account, cred=cred)
