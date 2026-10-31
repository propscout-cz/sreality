"""The propscout.cz waitlist (migration 700): the consent wording, input checks, a rate limit and the two writes.

The routes are api/routes/waitlist.py. Nothing here reads a request; nothing in the routes writes SQL.
"""

from __future__ import annotations

import re
import threading
import time
import uuid
from collections import deque
from collections.abc import Mapping
from typing import Any

from psycopg.types.json import Jsonb

# The wording shown under the landing page's buttons (frontend/landing/index.html), keyed by the version the form
# posts. An invitation email is a commercial communication (zákon 480/2004 Sb. § 7), and proving consent is the
# sender's burden, so each row stores the version it agreed to. Changing the wording = a NEW key (and the page's hidden
# `consent` value); never edit a published entry. tests/api/test_waitlist_routes.py checks that the page shows exactly
# CONSENT_TEXTS[CONSENT_VERSION].
CONSENT_TEXTS: dict[str, str] = {
    "2026-10-31": (
        "Odesláním souhlasíte, že vám [PROVOZOVATEL] (IČO [IČO]), provozovatel PropScout.cz, bude e-mailem posílat "
        "pozvánku do aplikace a novinky k jejímu spuštění. Souhlas můžete kdykoli odvolat odkazem v každém e-mailu."
    ),
}
CONSENT_VERSION = "2026-10-31"

INVESTOR_TYPES = frozenset({"flip", "rent", "both", "starting"})
UTM_KEYS = ("utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term")
NOTE_MAX = 2000

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_VARIANT_RE = re.compile(r"^[a-z0-9-]{1,40}$")


def normalize_email(raw: str) -> str | None:
    """The address as stored (trimmed, lower-case), or None when it is not an address."""
    email = raw.strip().lower()
    if not 3 <= len(email) <= 254 or not _EMAIL_RE.match(email):
        return None
    if any(ord(c) < 32 or ord(c) == 127 for c in email):
        return None
    return email


def clean_variant(raw: str) -> str | None:
    v = raw.strip().lower()
    return v if _VARIANT_RE.match(v) else None


def clean_utm(form: Mapping[str, Any]) -> dict[str, str]:
    """The ad's utm_* tags only, each trimmed to 200 characters; anything else in the form is ignored."""
    out: dict[str, str] = {}
    for key in UTM_KEYS:
        value = form.get(key)
        if isinstance(value, str) and value.strip():
            out[key] = value.strip()[:200]
    return out


def clean_note(raw: str) -> str | None:
    note = raw.strip()[:NOTE_MAX]
    return note or None


class RateLimit:
    """Sliding-window counts per client and in total, in this process's memory (a restart forgets them)."""

    def __init__(self, per_client: int, total: int, window_s: float) -> None:
        self.per_client, self.total, self.window_s = per_client, total, window_s
        self._clients: dict[str, deque[float]] = {}
        self._all: deque[float] = deque()
        self._lock = threading.Lock()

    def allow(self, client: str, now: float | None = None) -> bool:
        t = time.monotonic() if now is None else now
        cutoff = t - self.window_s
        with self._lock:
            while self._all and self._all[0] <= cutoff:
                self._all.popleft()
            hits = self._clients.setdefault(client, deque())
            while hits and hits[0] <= cutoff:
                hits.popleft()
            if len(self._all) >= self.total or len(hits) >= self.per_client:
                return False
            self._all.append(t)
            hits.append(t)
            if len(self._clients) > 10_000:
                self._clients = {k: d for k, d in self._clients.items() if d and d[-1] > cutoff}
            return True


def record_signup(conn: Any, email: str, consent_version: str, page_variant: str | None,
                  utm: dict[str, str]) -> uuid.UUID | None:
    """Insert the email; its follow-up token when it is new, None when the address was already on the list."""
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(
            "INSERT INTO waitlist_signups (email, consent_version, page_variant, utm) VALUES (%s, %s, %s, %s) "
            "ON CONFLICT (email) DO NOTHING RETURNING followup_token",
            (email, consent_version, page_variant, Jsonb(utm)),
        )
        row = cur.fetchone()
    return row[0] if row else None


def record_answer(conn: Any, token: uuid.UUID, investor_type: str | None, note: str | None) -> bool:
    """Store the thank-you page's answer once per token; False for an unknown or already-answered token."""
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(
            "UPDATE waitlist_signups SET investor_type = %s, note = %s, answered_at = now() "
            "WHERE followup_token = %s AND answered_at IS NULL",
            (investor_type, note, token),
        )
        return cur.rowcount == 1
