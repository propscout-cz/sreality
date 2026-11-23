"""GET /properties/{id}/price-history: the SPA property page's ad + snapshot reads for the extension chart (require_token, service role)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from api import dependencies as deps
from api import property_read

router = APIRouter(tags=["properties"], dependencies=[Depends(deps.require_token)])


@router.get("/properties/{property_id}/price-history")
def get_property_price_history(
    property_id: int, conn: Any = Depends(deps.get_db_conn)
) -> dict[str, Any]:
    # The canonical ad (first track) is the lookup's canonical_listing_id.
    sources = property_read.sources(conn, [property_id])
    if not sources:
        # Unknown or merged away: a merge re-points every ad, so no ad means no property.
        raise HTTPException(
            status_code=404, detail={"reason": "not_found", "message": "Nemovitost nenalezena"}
        )
    snapshots = property_read.snapshots(conn, [s["id"] for s in sources])
    return {"data": {"sources": sources, "snapshots": snapshots}}
