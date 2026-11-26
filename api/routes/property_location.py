"""The phone's Poloha: where a property is, as JSON.

GET /properties/{id}/location answers `api/property_location.load_location`, the location ad's point.

It rides `require_token`: a location is listing data, not per-account data.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from api import dependencies as deps
from api.property_location import NoLocation, load_location

router = APIRouter(tags=["properties"], dependencies=[Depends(deps.require_token)])


def _refuse(status: int, reason: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"reason": reason, "message": message})


def _located(conn: Any, property_id: int) -> dict[str, Any]:
    try:
        return load_location(conn, property_id)
    except NoLocation as exc:
        raise _refuse(404, *exc.args) from None


@router.get("/properties/{property_id}/location")
def get_property_location(property_id: int, conn: Any = Depends(deps.get_db_conn)) -> dict[str, Any]:
    return _located(conn, property_id)
