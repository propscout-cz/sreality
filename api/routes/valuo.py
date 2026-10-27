"""GET + POST /properties/{id}/valuo (api/valuo.py does the work; tenancy: references/tenancy.md shape 3)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from api import dependencies as deps
from api import schemas as s
from api import tenant_pool, valuo, valuo_client

router = APIRouter(tags=["valuo"])


@router.get("/properties/{property_id}/valuo")
def get_property_valuo(
    property_id: int,
    claims: dict = Depends(deps.verify_jwt),
    conn: Any = Depends(deps.get_db_conn),
) -> dict[str, Any]:
    account = tenant_pool.require_account_id(conn, claims, None)
    return valuo.page(conn, property_id, account_id=account, cred=valuo_client.credential())


@router.post("/properties/{property_id}/valuo")
def post_property_valuo(
    property_id: int,
    body: s.ValuoValuationIn,
    claims: dict = Depends(deps.verify_jwt),
    conn: Any = Depends(deps.get_db_conn),
) -> dict[str, Any]:
    account = tenant_pool.require_account_id(conn, claims, None)
    return valuo.new_valuation(conn, property_id, body.overrides, account_id=account, cred=valuo_client.credential())
