"""GET /properties/{id}/building: what the RÚIAN register states about the property's building
(`api/property_building.load_building`), or `{"building": null}` when its door names none.

It rides `require_token`: register data is open data (ČÚZK, CC BY 4.0), not per-account data.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from api import dependencies as deps
from api.property_building import NoProperty, load_building

router = APIRouter(tags=["properties"], dependencies=[Depends(deps.require_token)])


@router.get("/properties/{property_id}/building")
def get_property_building(property_id: int, conn: Any = Depends(deps.get_db_conn)) -> dict[str, Any]:
    try:
        return {"building": load_building(conn, property_id)}
    except NoProperty:
        raise HTTPException(status_code=404, detail="Nemovitost nenalezena") from None
