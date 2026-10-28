"""GET /properties/{id}/price-history: the SPA property page's ad + snapshot reads for the extension chart (require_token, service role)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from psycopg.rows import dict_row

from api import dependencies as deps

router = APIRouter(tags=["properties"], dependencies=[Depends(deps.require_token)])

# The SPA's PROPERTY_SOURCE_COLS (queries.ts fetchPropertySources), oldest ad first; `id` breaks
# a tie as property_download does. The canonical ad (first track) is the lookup's canonical_listing_id.
_SOURCES_SQL = (
    "select id, property_id, source, source_url, source_id_native, ad_reference, is_active,"
    " price_czk, first_seen_at, last_seen_at from property_sources_public"
    " where property_id = %s order by first_seen_at, id"
)
# Never `description` (or `*`): the view derives it from raw_json, detoasting every snapshot.
_SNAPSHOTS_SQL = (
    "select id, listing_id, scraped_at, price_czk from listing_snapshots_public"
    " where listing_id = any(%s::bigint[]) order by scraped_at, id"
)


@router.get("/properties/{property_id}/price-history")
def get_property_price_history(
    property_id: int, conn: Any = Depends(deps.get_db_conn)
) -> dict[str, Any]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(_SOURCES_SQL, (property_id,))
        sources = cur.fetchall()
        if not sources:
            # Unknown or merged away: a merge re-points every ad, so no ad means no property.
            raise HTTPException(
                status_code=404, detail={"reason": "not_found", "message": "Nemovitost nenalezena"}
            )
        cur.execute(_SNAPSHOTS_SQL, ([s["id"] for s in sources],))
        snapshots = cur.fetchall()
    return {"data": {"sources": sources, "snapshots": snapshots}}
