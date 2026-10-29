"""GET /properties/{property_id}/download.zip — the property page's download button.

One zip: a PDF of the ad as the page shows it (api/property_sheet.py), the canonical ad's
stored photos in gallery order (the ones the page's gallery shows), and in `moje-fotky/` the
originals of the acting account's own photos of the property ("Moje fotky", migration 660: the
strip's photos, a note's included), oldest first. Built here because the R2 bucket sends no CORS
header, so the SPA cannot read the bytes behind /images/{key}. The ad's data is read on the
service role; the own photos on the caller's tenant connection (RLS, X-Act-As as the attachment
routes), and of the curation nothing else (tags, notes, collections, pipeline). Signed-in users
only, since it proxies photo bytes through us; a broker's phone and e-mail follow the /brokers/*
PII policy (values for an admin, "hidden" for anyone else).
"""

from __future__ import annotations

import io
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from psycopg.rows import dict_row

from api import attachments
from api import dependencies as deps
from api import property_read, tenant_pool
from api.property_sheet import render_property_sheet
from api.routes.images import r2_client
from toolkit import brokers

router = APIRouter(tags=["properties"])

# Parallel R2 reads: a 40-photo listing fetched serially spends seconds on round trips alone.
_ZIP_FETCH_WORKERS = 8

# The acting identity's own images on the property (a merged-away id has none: the 404 above).
_OWN_PHOTOS_SQL = (
    "SELECT storage_key, filename FROM property_attachments "
    "WHERE property_id = %s AND account_id IN (SELECT acting_account_ids()) "
    "AND mime_type = ANY(%s) ORDER BY created_at, id"
)
_PHOTO_TYPES = ["image/jpeg", "image/png", "image/webp", "image/gif"]


def _fetch_all(fetch: Any, keys: list[str]) -> list[bytes]:
    """Every object or a 502: a half-filled zip would pass for the full set."""
    if not keys:
        return []
    try:
        with ThreadPoolExecutor(max_workers=_ZIP_FETCH_WORKERS) as pool:
            return list(pool.map(fetch, keys))
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail="A photo could not be read from storage"
        ) from exc


@router.get("/properties/{property_id}/download.zip")
def get_property_download(
    property_id: int,
    conn: Any = Depends(deps.get_db_conn),
    claims: dict = Depends(deps.verify_jwt),
    tconn: Any = Depends(tenant_pool.tenant_conn),
) -> Response:
    # The page's own reads (its brokers come through the PII policy below, never properties_public).
    found = property_read.properties(conn, [property_id])
    if not found:
        raise HTTPException(status_code=404, detail="Property not found")
    prop = found[0]
    ads = property_read.sources(conn, [property_id])
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "select storage_path from images"
            " where listing_id = %s and storage_path is not null"
            " order by sequence nulls last, id",
            (prop["listing_id"],),
        )
        keys = [r["storage_path"] for r in cur.fetchall()]

    ad_ids = [a["id"] for a in ads] or [prop["listing_id"]]
    rows = brokers.apply_pii_policy(
        brokers.listing_brokers(conn, ad_ids), include_pii=deps.is_admin(claims)
    )["data"]
    shown, from_inactive = brokers.property_brokers(
        ads, {r["listing_id"]: r for r in rows}, prop["listing_id"]
    )

    with tconn.cursor() as cur:
        cur.execute(_OWN_PHOTOS_SQL, (property_id, _PHOTO_TYPES))
        own = cur.fetchall()

    photos: list[bytes] = []
    if keys:
        client = r2_client()
        if client is None:
            raise HTTPException(status_code=503, detail="Image storage not configured")
        photos = _fetch_all(client.download_bytes, keys)
    mine = _fetch_all(attachments.storage().download_bytes, [k for k, _n in own]) if own else []

    sheet = render_property_sheet(
        prop,
        ads,
        shown,
        brokers_from_inactive=from_inactive,
        cover=photos[0] if photos else None,
        today=datetime.now(UTC).date(),
    )
    width = max(2, len(str(len(photos))))
    buf = io.BytesIO()
    # Stored, not deflated: JPEG bytes don't compress and the PDF's streams already are.
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as archive:
        archive.writestr(f"property-{property_id}.pdf", sheet)
        for n, data in enumerate(photos, start=1):
            archive.writestr(f"{n:0{width}d}.jpg", data)
        # Numbered: a phone names every capture alike ("image.jpg").
        mine_width = max(2, len(str(len(mine))))
        for n, ((_key, filename), data) in enumerate(zip(own, mine), start=1):
            archive.writestr(f"moje-fotky/{n:0{mine_width}d}-{filename}", data)
    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="property-{property_id}.zip"'},
    )
