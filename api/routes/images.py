"""Public image-redirect route: GET /images/{key} -> 302 to a presigned R2 URL.

The frontend serves every listing photo through this endpoint instead of the
sreality CDN, whose tokenised URLs expire within weeks (any stored *.sdn.cz URL
404s once sreality rotates the render token). R2 holds the durable copy; this
route mints a short-lived presigned GET so a *private* bucket can stream bytes
straight to the browser without proxying them through us.

Unauthenticated (like /health) — listing photos are public data and an <img>
tag can't send a bearer header. The key is constrained to the listing-image
shape `-?<id>/<seq>.jpg` so this can never presign the operator-private
`custom-attachments/` building uploads that share the bucket.

`?w=1080` asks for the phone-size copy instead (#1765): a WebP 1080 px wide at
`thumb/w1080/<id>/<seq>.webp`, derived from the validated key (never taken from
the client), made from the full-size photo on its first request and redirected
to like any photo after that. Any failure serves the full-size photo instead. A
re-master that overwrites the photo deletes its copy (scripts/remaster_sreality_images.py).
"""

from __future__ import annotations

import asyncio
import io
import logging
import os
import re

from fastapi import APIRouter, HTTPException
from fastapi.responses import RedirectResponse
from starlette.concurrency import run_in_threadpool

from scraper import image_storage

LOG = logging.getLogger(__name__)

router = APIRouter(prefix="/images", tags=["images"])

# `{native_id}/{seq:04d}.jpg`; native_id is negative for non-sreality portals.
_KEY_RE = re.compile(r"^-?\d+/\d{4}\.jpg$")

# Presign lifetime (R2/SigV4 max is 7 days). The 302 is cached only briefly on purpose:
# a long cache means a serve-path change (e.g. an R2 credential rotation) leaves browsers
# and the edge following a *cached* redirect to a presigned URL signed with the
# rotated-out key — broken images for days, not fixable by a client hard-reload. A short
# TTL makes such a change self-heal within the hour. (imageUrl.ts also carries a
# cache-bust token to flush already-cached redirects on demand.)
_PRESIGN_TTL = 604800
_REDIRECT_MAX_AGE = 3600  # 1 hour

# The redirect above re-mints hourly BY DESIGN, but SigV4 signs with the wall clock, so
# each re-mint used to hand back a different URL for the same bytes. The browser's HTTP
# cache keys on the full URL, so the object's own `Cache-Control: public, max-age=2592000`
# never applied twice: every photo re-downloaded once an hour, forever. Signing as at the
# start of the current UTC day makes the target byte-identical all day, so the hourly
# re-mint costs one header-sized 302 and zero image bytes — while the 1-hour redirect cache,
# and with it the credential-rotation self-heal above, is untouched. Must stay well under
# _PRESIGN_TTL: the last URL minted in a bucket still has TTL - anchor left to live
# (6 days here). Set IMAGE_PRESIGN_ANCHOR_SECONDS=0 to fall back to per-request signing.
_PRESIGN_ANCHOR_DEFAULT = 86400  # 1 day

# The phone copy. One width by operator ruling (2026-10-12); any other `w` is a 404, so
# nobody can mint arbitrary sizes into the bucket.
_COPY_WIDTHS = frozenset(str(w) for w in image_storage.COPY_WIDTHS)
_COPY_QUALITY = 75
# A fallback redirect to the full-size photo is cached briefly, so the copy is tried
# again within minutes instead of being skipped for the hour.
_FALLBACK_MAX_AGE = 300
# Making a copy is a GET, a decode/resize/encode and a PUT (~150-300 ms). The API is ONE
# uvicorn process, so at most this many run at once, off the threadpool the sync routes
# share; a request that waits longer than _COPY_WAIT_SECONDS for a slot gets the fallback.
_COPY_SLOTS = asyncio.Semaphore(4)
_COPY_WAIT_SECONDS = 5.0

_client: image_storage.R2Client | None = None


def _anchor_seconds() -> int:
    """Read per request so the kill switch takes effect without a redeploy."""
    raw = os.environ.get("IMAGE_PRESIGN_ANCHOR_SECONDS")
    if raw is None or not raw.strip().lstrip("-").isdigit():
        return _PRESIGN_ANCHOR_DEFAULT
    return min(int(raw), _PRESIGN_TTL // 2)


def r2_client() -> image_storage.R2Client | None:
    global _client
    if _client is None and image_storage.is_configured():
        _client = image_storage.R2Client.from_env()
    return _client


def make_copy(data: bytes, width: int) -> bytes:
    """A WebP no wider than `width` (never upscaled), EXIF rotation applied, RGB colour
    profile kept."""
    from PIL import ExifTags, Image, ImageOps

    with Image.open(io.BytesIO(data)) as src:
        stored_w, stored_h = src.size
        quarter_turn = src.getexif().get(ExifTags.Base.Orientation) in (5, 6, 7, 8)
        scale = min(1.0, width / (stored_h if quarter_turn else stored_w))
        if src.format in ("JPEG", "MPO"):
            # Decode at 1/2, 1/4 or 1/8 scale straight away instead of the full frame.
            src.draft("RGB", (max(1, int(stored_w * scale)), max(1, int(stored_h * scale))))
        icc = src.info.get("icc_profile")
        image = ImageOps.exif_transpose(src) or src
        image.thumbnail((width, image.height), Image.Resampling.LANCZOS)
    if image.mode not in ("RGB", "RGBA"):
        alpha = "A" in image.getbands() or "transparency" in image.info
        image = image.convert("RGBA" if alpha else "RGB")
    if icc and icc[16:20] != b"RGB ":  # a CMYK/grey profile would mis-colour RGB pixels
        icc = None
    out = io.BytesIO()
    image.save(out, "WEBP", quality=_COPY_QUALITY, method=4, icc_profile=icc)
    return out.getvalue()


def _store_copy(client: image_storage.R2Client, key: str, copy: str, width: int) -> None:
    client.upload_bytes(copy, make_copy(client.download_bytes(key), width), "image/webp")


def _redirect(client: image_storage.R2Client, key: str, max_age: int) -> RedirectResponse:
    url = client.presigned_get(
        key, expires_in=_PRESIGN_TTL, anchor_seconds=_anchor_seconds()
    )
    return RedirectResponse(
        url,
        status_code=302,
        headers={"Cache-Control": f"public, max-age={max_age}"},
    )


async def _copy_redirect(
    client: image_storage.R2Client, key: str, width: int
) -> RedirectResponse:
    copy = image_storage.copy_key(key, width)
    try:
        if await asyncio.to_thread(client.object_size, copy) is None:
            await asyncio.wait_for(_COPY_SLOTS.acquire(), _COPY_WAIT_SECONDS)
            try:
                await asyncio.to_thread(_store_copy, client, key, copy, width)
            finally:
                _COPY_SLOTS.release()
    except Exception as exc:  # noqa: BLE001 - the full-size photo is always servable
        LOG.warning("image copy %s unavailable, serving full size: %r", copy, exc)
        return _redirect(client, key, _FALLBACK_MAX_AGE)
    return _redirect(client, copy, _REDIRECT_MAX_AGE)


@router.get("/{key:path}")
async def get_image(key: str, w: str | None = None) -> RedirectResponse:
    if not _KEY_RE.match(key) or (w is not None and w not in _COPY_WIDTHS):
        raise HTTPException(status_code=404, detail="Not found")
    client = await run_in_threadpool(r2_client)
    if client is None:
        raise HTTPException(status_code=503, detail="Image storage not configured")
    if w is None:
        return _redirect(client, key, _REDIRECT_MAX_AGE)
    return await _copy_redirect(client, key, int(w))
