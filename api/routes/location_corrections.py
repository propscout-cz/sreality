"""The address picker's write (location W3b, ruling Q5): `POST /location/corrections`.

Admin-gated at the router. The property page's pencil sends ONE register entity — an
address point, a street or an obec, bound by `/maps/resolve` — or `pick: null`, and
`operator_corrections.submit_pick` writes it as operator claims on every ad of the
property. A WRITE EXCEPTION in the toolkit sense: after the claim transaction the location
ad is resolved synchronously, so the response already carries the refreshed label and
`held` (whether the row binds the picked code) — 05 5.5.5 read-your-writes. If that resolve
fails, the write's enqueue makes the drain converge; the response says which happened.

Gone with W3b: the listing inspector (`/location/listing/*`). Before it: `/sample/*` and
`/compare/*` (W2-b) and `/quality/*` (W1, the page renders the gauge's stored cells).
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api import dependencies as deps

router = APIRouter(
    prefix="/location", tags=["location"], dependencies=[Depends(deps.require_admin)]
)


class PickIn(BaseModel):
    level: Literal["adresni_misto", "ulice", "obec"]
    code: int = Field(ge=1)  # kód ADM / street code / obec code (RÚIAN)


class CorrectionIn(BaseModel):
    property_id: int = Field(ge=1)
    pick: PickIn | None  # None: back to the ads' own words


@router.post("/corrections")
def submit_pick(
    body: CorrectionIn,
    conn: Any = Depends(deps.get_db_conn),
    claims: dict = Depends(deps.require_admin),
) -> dict[str, Any]:
    # Imported lazily: the module pulls the resolver machinery on first
    # resolve_now(), and a boot-time import failure in a route module takes the
    # whole API down silently (api-docker-import-surface).
    from location_data import operator_corrections as oc

    pick = None if body.pick is None else (body.pick.level, body.pick.code)
    try:
        result = oc.submit_pick(conn, property_id=body.property_id, pick=pick,
                                by=claims.get("sub"))
    except oc.UnknownPropertyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except oc.CorrectionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    ads = result["ads"]
    location_ad = next((a["listing_id"] for a in ads if a["location_ad"]), ads[0]["listing_id"])
    result["resolved"] = oc.resolve_now(conn, location_ad)
    location = oc.read_projection(conn, location_ad)
    result["location"] = location
    result["held"] = None if pick is None else (
        location is not None and location.get(oc.PICK_COLUMN[pick[0]]) == pick[1])
    return {"data": result}
