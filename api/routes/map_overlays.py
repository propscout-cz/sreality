"""Map reference overlays: the layer registry and its pre-rendered vector tiles.

`GET /map-overlays` lists every ingested section by sort, each with its layers by sort; a section
never ingested is not offered. `GET /map-overlays/{section}/{z}/{x}/{y}.mvt` reads one stored tile by
primary key. Tiles are rendered at ingest (`scripts/ingest_map_overlays.py`), never here. The tile
URL carries the section's `version` as `?v=` (a cache key the route does not read), so every tile
answer, bytes or an empty 204, is cached privately for a year as immutable. Both routes ride
`require_token` on the service-role connection: the geometry is reference data, not per-account
data, and the overlay tables are closed to the browser roles. Design: docs/design/map-overlays.md.
"""

from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from psycopg.rows import dict_row

from api import dependencies as deps

router = APIRouter(
    prefix="/map-overlays", tags=["map-overlays"], dependencies=[Depends(deps.require_token)]
)

MVT_MEDIA_TYPE = "application/vnd.mapbox-vector-tile"
CACHE_CONTROL = "private, max-age=31536000, immutable"

# At most nine digits: every bound value fits the int tile columns, so the read stays on the primary key.
_COORD = re.compile(r"[0-9]{1,9}")

_SECTIONS_SQL = """
select key, label, sort, minzoom, maxzoom, attribution, licence, source_url, note,
       source_revised_at, fetched_at, version
  from map_overlay_sections
"""

_LAYERS_SQL = """
select section, key, label, sort, legend, scale, variants, feature_count
  from map_overlay_layers
"""

_TILE_SQL = """
select s.minzoom, s.maxzoom, t.mvt
  from map_overlay_sections s
  left join map_overlay_tiles t
    on t.section = s.key and t.z = %(z)s and t.x = %(x)s and t.y = %(y)s
 where s.key = %(section)s
"""


def _refuse(status: int, reason: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"reason": reason, "message": message})


def _by_sort(row: dict[str, Any]) -> tuple[int, str]:
    return row["sort"], row["key"]


def registry(sections: list[dict[str, Any]], layers: list[dict[str, Any]]) -> dict[str, Any]:
    """The ingested sections by sort, each with its layers by sort; a never-ingested one is omitted."""
    offered = {
        section["key"]: {**section, "layers": []}
        for section in sorted(sections, key=_by_sort)
        if section["fetched_at"] is not None
    }
    for layer in sorted(layers, key=_by_sort):
        section = offered.get(layer["section"])
        if section is not None:
            section["layers"].append({k: v for k, v in layer.items() if k != "section"})
    return {"sections": list(offered.values())}


@router.get("")
def get_map_overlays(conn: Any = Depends(deps.get_db_conn)) -> dict[str, Any]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(_SECTIONS_SQL)
        sections = cur.fetchall()
        cur.execute(_LAYERS_SQL)
        layers = cur.fetchall()
    return registry(sections, layers)


def tile_address(z: str, x: str, y: str) -> tuple[int, int, int]:
    """z/x/y as ints, or a 400: non-negative integers with x and y inside the zoom's 2^z grid."""
    if not all(_COORD.fullmatch(c) for c in (z, x, y)):
        raise _refuse(400, "bad_tile", "Neplatná adresa dlaždice")
    zi, xi, yi = int(z), int(x), int(y)
    # x, y < 2^z compared by bit length: 2^z itself would be enormous for a nine-digit z.
    if max(xi, yi).bit_length() > zi:
        raise _refuse(400, "bad_tile", "Neplatná adresa dlaždice")
    return zi, xi, yi


@router.get("/{section}/{z}/{x}/{y}.mvt")
def get_map_overlay_tile(
    section: str, z: str, x: str, y: str, conn: Any = Depends(deps.get_db_conn)
) -> Response:
    zi, xi, yi = tile_address(z, x, y)
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(_TILE_SQL, {"section": section, "z": zi, "x": xi, "y": yi})
        row = cur.fetchone()
    if row is None:
        raise _refuse(404, "unknown_section", "Neznámá vrstva mapy")
    headers = {"Cache-Control": CACHE_CONTROL}
    if row["mvt"] is None or not row["minzoom"] <= zi <= row["maxzoom"]:
        return Response(status_code=204, headers=headers)
    return Response(content=row["mvt"], media_type=MVT_MEDIA_TYPE, headers=headers)
