"""The propscout.cz waitlist routes: POST /waitlist (the landing page's form) and POST /waitlist/answer (its thank-you
question). Mounted under `/waitlist`; the writes are api/waitlist.py.

OPEN BY DESIGN (tests/api/test_admin_route_coverage.py `_PUBLIC_ALLOWLIST`): a visitor from an ad has no account. The
page posts a plain HTML form cross-origin and every answer is a 303 back to the landing page, so the page needs no CORS
and no JavaScript. Guards: a honeypot field (`web`, filled only by bots, answered as a success and never stored), a
rate limit per client and in total, and strict input checks. A known email is answered exactly like a new one, so the
form cannot tell anyone whether an address is on the list.

WHERE IT REDIRECTS: the request's Origin when it is the landing origin (LANDING_ORIGIN, default https://propscout.cz)
or one of CORS_ALLOW_ORIGINS (the app host serves the same page at /landing/ for previews), else the landing origin;
the path is one of `_PAGES` only. Nothing the client sends can point the redirect anywhere else.
"""

from __future__ import annotations

import logging
import os
import uuid
from urllib.parse import urlencode

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse
from starlette.concurrency import run_in_threadpool

from api import dependencies as deps
from api import waitlist

LOG = logging.getLogger(__name__)

router = APIRouter(prefix="/waitlist", tags=["waitlist"])

DEFAULT_LANDING_ORIGIN = "https://propscout.cz"

# The form's `next` (its thank-you path) → the landing page it came from. `/dekujeme` is the brand domain's; the
# `/landing/…` pair is the same page on the app host.
_PAGES: dict[str, str] = {"/dekujeme": "/", "/landing/dekujeme.html": "/landing/"}

_SIGNUP_LIMIT = waitlist.RateLimit(per_client=5, total=300, window_s=600)
_ANSWER_LIMIT = waitlist.RateLimit(per_client=10, total=600, window_s=600)


def _landing_origin() -> str:
    return os.environ.get("LANDING_ORIGIN", "").strip().rstrip("/") or DEFAULT_LANDING_ORIGIN


def _base(origin: str | None) -> str:
    allowed = {_landing_origin()} | {
        o.strip() for o in os.environ.get("CORS_ALLOW_ORIGINS", "").split(",") if o.strip()
    }
    return origin if origin and origin in allowed else _landing_origin()


def _client(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    return request.headers.get("x-real-ip") or forwarded or (request.client.host if request.client else "?")


def _go(base: str, path: str, params: dict[str, str], fragment: str = "") -> RedirectResponse:
    query = urlencode({k: v for k, v in params.items() if v})
    url = f"{base}{path}" + (f"?{query}" if query else "") + (f"#{fragment}" if fragment else "")
    return RedirectResponse(url, status_code=303)


@router.post("")
async def post_waitlist(request: Request) -> RedirectResponse:
    """Add an email to the waitlist; 303 to the thank-you page, or back to the form with `chyba=<reason>`."""
    form = await request.form()
    base = _base(request.headers.get("origin"))
    nxt = str(form.get("next") or "")
    thanks = nxt if nxt in _PAGES else "/dekujeme"
    home = _PAGES[thanks]
    variant = waitlist.clean_variant(str(form.get("v") or ""))

    def fail(reason: str) -> RedirectResponse:
        LOG.info("WAITLIST refused reason=%s variant=%s", reason, variant)
        return _go(base, home, {"chyba": reason, "v": variant or ""}, "prihlaska")

    if str(form.get("web") or ""):
        LOG.info("WAITLIST honeypot variant=%s", variant)
        return _go(base, thanks, {})
    email = waitlist.normalize_email(str(form.get("email") or ""))
    if email is None:
        return fail("email")
    consent = str(form.get("consent") or "")
    if consent not in waitlist.CONSENT_TEXTS:
        return fail("obnovit")
    if not _SIGNUP_LIMIT.allow(_client(request)):
        return fail("limit")
    utm = waitlist.clean_utm(form)

    def write() -> uuid.UUID | None:
        with deps.open_background_conn() as conn:
            return waitlist.record_signup(conn, email, consent, variant, utm)

    try:
        token = await run_in_threadpool(write)
    except Exception:
        LOG.exception("WAITLIST signup failed variant=%s", variant)
        return fail("server")
    LOG.info("WAITLIST signup new=%d variant=%s utm_source=%s utm_campaign=%s", token is not None, variant,
             utm.get("utm_source"), utm.get("utm_campaign"))
    return _go(base, thanks, {"t": str(token) if token else ""})


@router.post("/answer")
async def post_waitlist_answer(request: Request) -> RedirectResponse:
    """Store the thank-you page's optional answer (investor type + a note) once; 303 back with `hotovo=1`."""
    form = await request.form()
    base = _base(request.headers.get("origin"))
    nxt = str(form.get("next") or "")
    thanks = nxt if nxt in _PAGES else "/dekujeme"
    try:
        token = uuid.UUID(str(form.get("t") or ""))
    except ValueError:
        return _go(base, thanks, {"hotovo": "1"})
    kind = str(form.get("investor_type") or "")
    investor_type = kind if kind in waitlist.INVESTOR_TYPES else None
    note = waitlist.clean_note(str(form.get("note") or ""))
    if (investor_type or note) and _ANSWER_LIMIT.allow(_client(request)):

        def write() -> bool:
            with deps.open_background_conn() as conn:
                return waitlist.record_answer(conn, token, investor_type, note)

        try:
            stored = await run_in_threadpool(write)
            LOG.info("WAITLIST answer stored=%d investor_type=%s note=%d", stored, investor_type, note is not None)
        except Exception:
            LOG.exception("WAITLIST answer failed")
    return _go(base, thanks, {"hotovo": "1"})
