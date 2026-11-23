"""The propscout.cz waitlist routes: POST /waitlist (the landing page's form), POST /waitlist/answer (its thank-you
question) and /waitlist/unsubscribe (every email's link). Mounted under `/waitlist`; the writes are api/waitlist.py.

OPEN BY DESIGN (tests/api/test_admin_route_coverage.py `_PUBLIC_ALLOWLIST`): a visitor from an ad has no account. The
page posts a plain HTML form cross-origin and every answer is a 303 back to the landing page, so the page needs no CORS
and no JavaScript. Guards: a honeypot field (`web`, filled only by bots, answered as a success and never stored), a
rate limit per client and in total, and strict input checks. A known email is answered exactly like a new one, so the
form cannot tell anyone whether an address is on the list.

THE CONFIRMATION EMAIL goes to a NEW entry only, after the 303 has gone out, so Resend can never slow or fail a signup.
Dark without RESEND_API_KEY. Over the daily cap or on a failed send the entry stays, with no email and no retry.

UNSUBSCRIBE: the entry's `followup_token` is the only credential, and every answer reads the same, so a link cannot tell
anyone whether a token exists. GET only asks (mail scanners open links); POST withdraws, and is what a mail client's own
button sends (RFC 8058).

WHERE IT REDIRECTS: the request's Origin when it is the landing origin (LANDING_ORIGIN, default https://propscout.cz)
or one of CORS_ALLOW_ORIGINS (the app host serves the same page at /landing/ for previews), else the landing origin;
the path is one of `_PAGES` only. Nothing the client sends can point the redirect anywhere else.
"""

from __future__ import annotations

import html
import logging
import os
import uuid
from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool

from api import dependencies as deps
from api import resend_client, waitlist, waitlist_email
from api.http_json import HttpJsonError

LOG = logging.getLogger(__name__)

router = APIRouter(prefix="/waitlist", tags=["waitlist"])

# The admin Sign-ups page's read (frontend/src/pages/Signups.tsx): e-mails are personal data, so admin-only, and the
# table is service-role only (mig 700), so it is served here, never read by the browser.
admin_router = APIRouter(prefix="/admin/waitlist", tags=["waitlist"], dependencies=[Depends(deps.require_admin)])

DEFAULT_LANDING_ORIGIN = "https://propscout.cz"

# The form's `next` (its thank-you path) → the landing page it came from. `/dekujeme` is the brand domain's; the
# `/landing/…` pair is the same page on the app host.
_PAGES: dict[str, str] = {"/dekujeme": "/", "/landing/dekujeme.html": "/landing/"}

_SIGNUP_LIMIT = waitlist.RateLimit(per_client=5, total=300, window_s=600)
_ANSWER_LIMIT = waitlist.RateLimit(per_client=10, total=600, window_s=600)
_UNSUBSCRIBE_LIMIT = waitlist.RateLimit(per_client=30, total=600, window_s=600)


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


def _send_confirmation(token: uuid.UUID) -> None:
    """Runs after the response: nothing here may reach the visitor, so every failure ends in the log."""
    if not resend_client.is_configured():
        LOG.info("WAITLIST confirmation skipped reason=unconfigured")
        return
    try:
        with deps.open_background_conn() as conn:
            email = waitlist.claim_confirmation(conn, token)
        if email is None:
            LOG.warning("WAITLIST confirmation skipped reason=cap")
            return
        try:
            resend_client.send(
                sender=waitlist_email.SENDER, to=email, reply_to=waitlist_email.REPLY_TO, subject=waitlist_email.SUBJECT,
                html=waitlist_email.confirmation_html(token), text=waitlist_email.confirmation_text(token),
                headers=waitlist_email.unsubscribe_headers(token),
            )
        except HttpJsonError as exc:
            LOG.warning("WAITLIST confirmation failed %s", exc)
            with deps.open_background_conn() as conn:
                waitlist.release_confirmation(conn, token)
            return
        LOG.info("WAITLIST confirmation sent")
    except Exception:
        LOG.exception("WAITLIST confirmation failed")


def _page(title: str, body: str, status_code: int = 200) -> HTMLResponse:
    return HTMLResponse(
        '<!doctype html><html lang="cs"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex">'
        f"<title>{html.escape(title)} · PropScout.cz</title>"
        "<style>body{font-family:system-ui,sans-serif;max-width:32rem;margin:4rem auto;padding:0 1rem;color:#222;"
        "line-height:1.5}button{font:inherit;padding:.6rem 1.2rem;border:0;background:#222;color:#fff;"
        f"border-radius:.4rem;cursor:pointer}}</style></head><body><h1>{html.escape(title)}</h1>{body}</body></html>",
        status_code=status_code,
    )


def _token(raw: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(raw)
    except ValueError:
        return None


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
    answer = _go(base, thanks, {"t": str(token) if token else ""})
    if token:
        answer.background = BackgroundTask(_send_confirmation, token)
    return answer


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


@router.get("/unsubscribe")
def get_unsubscribe(t: str = "") -> HTMLResponse:
    """Ask first: the withdrawal is the POST, so a scanner opening the link changes nothing."""
    token = _token(t)
    if token is None:
        return _page("Neplatný odkaz", "<p>Tento odkaz pro odhlášení není platný.</p>", 400)
    return _page(
        "Odhlásit se z čekací listiny",
        f'<p>Po odhlášení vám už nepošleme pozvánku ani novinky.</p><form method="post" action="?t={token}">'
        "<button>Odhlásit se</button></form>",
    )


@router.post("/unsubscribe")
async def post_unsubscribe(request: Request, t: str = "") -> HTMLResponse:
    """Withdraw consent for the entry behind this token; a repeat or an unknown token reads the same."""
    token = _token(t)
    if token is None:
        return _page("Neplatný odkaz", "<p>Tento odkaz pro odhlášení není platný.</p>", 400)
    if not _UNSUBSCRIBE_LIMIT.allow(_client(request)):
        return _page("Zkuste to prosím později", "<p>Odhlášení se teď nepodařilo. Zkuste to za pár minut.</p>", 429)

    def write() -> bool:
        with deps.open_background_conn() as conn:
            return waitlist.unsubscribe(conn, token)

    try:
        withdrawn = await run_in_threadpool(write)
    except Exception:
        LOG.exception("WAITLIST unsubscribe failed")
        return _page("Zkuste to prosím později", "<p>Odhlášení se teď nepodařilo. Zkuste to za pár minut.</p>", 503)
    LOG.info("WAITLIST unsubscribe new=%d", withdrawn)
    return _page("Jste odhlášeni", "<p>Už vám nepošleme pozvánku ani novinky.</p>")


@admin_router.get("")
def get_waitlist_signups(conn: Any = Depends(deps.get_db_conn)) -> dict[str, Any]:
    """Every signup, newest first, with `truncated` when the list hit its cap."""
    rows = waitlist.list_signups(conn, waitlist.LIST_MAX + 1)
    return {"data": rows[: waitlist.LIST_MAX], "truncated": len(rows) > waitlist.LIST_MAX}
