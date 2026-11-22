"""GET /properties/{id}/building/sales: the registered sales we hold in the property's building
(`api/property_building_sales.load_building_sales`); an empty list when its door names no building or
the building has none. `require_token`: sales are shared market data, like the radius read."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from api import dependencies as deps
from api.property_building_sales import load_building_sales

router = APIRouter(tags=["properties"], dependencies=[Depends(deps.require_token)])


@router.get("/properties/{property_id}/building/sales")
def get_property_building_sales(property_id: int, conn: Any = Depends(deps.get_db_conn)) -> dict[str, Any]:
    return {"sales": load_building_sales(conn, property_id)}
