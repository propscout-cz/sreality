"""The phone's Poloha: where a property is, as JSON and as a Mapy static map image.

GET /properties/{id}/location answers `api/property_location.load_location`, the location ad's point.

GET /properties/{id}/static-map.png streams Mapy's static map of that point through us on
every view. Mapy's terms allow showing the image online but never storing or caching it
long-term, so nothing is written to R2 or disk; the browser keeps it for an hour, privately.
Each fetch costs 4 Mapy credits and logs one line (STATIC_MAP) so the spend is countable in
the API logs. `MAPY_STATIC_MAPS_ENABLED=0` turns the image off; the location JSON stays.

Both ride `require_token`: a location is listing data, not per-account data.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any

import requests
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response

from api import dependencies as deps
from api.property_location import NoLocation, load_location
from scraper.geocoding import KEY_LEVEL_STATUS, MapyKeyRefused, mapy_api_keys, with_mapy_key_failover

LOG = logging.getLogger(__name__)
# uvicorn configures only its own loggers, so the root stays at WARNING and an api.* INFO line
# never reaches the Railway log. The STATIC_MAP line is how Mapy credits are counted, so this
# logger carries its own level and, when nothing above it prints, its own handler.
LOG.setLevel(logging.INFO)
if not logging.getLogger().handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(levelname)s %(name)s %(message)s"))
    LOG.addHandler(_handler)

router = APIRouter(tags=["properties"], dependencies=[Depends(deps.require_token)])

MAPY_STATIC_URL = "https://api.mapy.cz/v1/static/map"
ENABLE_ENV = "MAPY_STATIC_MAPS_ENABLED"
_OFF_VALUES = frozenset({"0", "false", "no", "off"})

# The one size the phone asks for: a 4:3 box 1080 px wide, which Mapy draws as 540x405 at
# scale 2 (its width/height cap is 1024 each).
STATIC_WIDTHS: dict[int, tuple[int, int]] = {1080: (540, 405)}
_SCALE = 2
_TIMEOUT_S = 10
_CACHE_CONTROL = "private, max-age=3600"

# At most four Mapy fetches at once from this process (Mapy allows 30 req/s per key); a
# caller that cannot get a slot within the timeout gets the 502 like any other failure.
_FETCH_SLOTS = threading.BoundedSemaphore(4)


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


def static_maps_enabled() -> bool:
    return (os.environ.get(ENABLE_ENV) or "").strip().lower() not in _OFF_VALUES


def _coord(v: float) -> str:
    return f"{v:.6f}"


def static_map_params(lat: float, lon: float, zoom: int, w: int) -> dict[str, Any]:
    width, height = STATIC_WIDTHS[w]
    return {
        "lon": _coord(lon),
        "lat": _coord(lat),
        "zoom": zoom,
        "width": width,
        "height": height,
        "scale": _SCALE,
        "mapset": "basic",
        "format": "png",
        "markers": f"color:red;size:normal;{_coord(lon)},{_coord(lat)}",
    }


def fetch_static_map(params: dict[str, Any]) -> tuple[bytes | None, int | None]:
    """Mapy's PNG, or (None, last status). The fallback key is tried once, only when the
    primary is rejected or throttled; the key rides a header so no URL in a log carries it."""
    keys = mapy_api_keys()
    if not keys:
        LOG.warning("static map: no Mapy key configured")
        return None, None

    def attempt(key: str, last: bool) -> tuple[bytes | None, int | None]:
        try:
            resp = requests.get(
                MAPY_STATIC_URL, params=params, headers={"X-Mapy-Api-Key": key}, timeout=_TIMEOUT_S,
            )
        except requests.RequestException as exc:
            LOG.warning("static map: Mapy request failed: %s", type(exc).__name__)
            return None, None
        status = resp.status_code
        if status == 200 and resp.headers.get("content-type", "").startswith("image/"):
            return resp.content, status
        if status in KEY_LEVEL_STATUS and not last:
            raise MapyKeyRefused(status)
        return None, status

    return with_mapy_key_failover(
        attempt, keys, log=LOG, refused="static map: Mapy key %d/%d refused (status=%s), trying the backup",
    )


@router.get("/properties/{property_id}/static-map.png")
def get_property_static_map(
    property_id: int, w: int | None = None, conn: Any = Depends(deps.get_db_conn)
) -> Response:
    if not static_maps_enabled():
        raise _refuse(404, "disabled", "Mapa je vypnutá")
    if w not in STATIC_WIDTHS:
        raise _refuse(404, "unsupported_width", "Nepodporovaná šířka mapy")
    loc = _located(conn, property_id)
    params = static_map_params(loc["lat"], loc["lon"], loc["zoom"], w)
    started = time.monotonic()
    if not _FETCH_SLOTS.acquire(timeout=_TIMEOUT_S):
        body, status = None, None
    else:
        try:
            body, status = fetch_static_map(params)
        finally:
            _FETCH_SLOTS.release()
    ms = int((time.monotonic() - started) * 1000)
    LOG.info("STATIC_MAP property=%s zoom=%s status=%s ms=%d", property_id, loc["zoom"], status, ms)
    if body is None:
        raise _refuse(502, "map_unavailable", "Mapu se nepodařilo načíst")
    return Response(content=body, media_type="image/png", headers={"Cache-Control": _CACHE_CONTROL})
