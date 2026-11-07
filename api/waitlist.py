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
        "Odesláním souhlasíte, že vám Limen Ventures s.r.o. (IČO 14053179), provozovatel PropScout.cz, bude e-mailem posílat "
        "pozvánku do aplikace a novinky k jejímu spuštění. Souhlas můžete kdykoli odvolat odkazem v každém e-mailu."
    ),
}
CONSENT_VERSION = "2026-10-31"

INVESTOR_TYPES = frozenset({"flip", "rent", "both", "starting"})
UTM_KEYS = ("utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term")
NOTE_MAX = 2000
# Resend's free plan sends 100 a day across everything, password resets included. 500 once the account is on Pro.
CONFIRMATION_DAILY_CAP = 80
# The same shared allowance: a big batch must not use up the day's password resets. Raised once the account is on Pro.
INVITATION_DAILY_CAP = 50
INVITE_BATCH_MAX = 200

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


def claim_confirmation(conn: Any, token: uuid.UUID, cap: int = CONFIRMATION_DAILY_CAP) -> str | None:
    """Stamp the entry's confirmation as sent, if today (UTC, Resend's day) is under the cap; the address, else None."""
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(
            "UPDATE waitlist_signups SET confirmation_sent_at = now() "
            "WHERE followup_token = %s AND confirmation_sent_at IS NULL AND unsubscribed_at IS NULL AND ("
            "  SELECT count(*) FROM waitlist_signups WHERE confirmation_sent_at >= date_trunc('day', now(), 'UTC')"
            ") < %s RETURNING email",
            (token, cap),
        )
        row = cur.fetchone()
    return row[0] if row else None


def release_confirmation(conn: Any, token: uuid.UUID) -> None:
    """A failed send was never sent: it leaves the stamp and today's count."""
    with conn.transaction(), conn.cursor() as cur:
        cur.execute("UPDATE waitlist_signups SET confirmation_sent_at = NULL WHERE followup_token = %s", (token,))


def unsubscribe(conn: Any, token: uuid.UUID) -> bool:
    """Withdraw the entry's consent once; the first withdrawal's time stands, and the entry stays as its record."""
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(
            "UPDATE waitlist_signups SET unsubscribed_at = now() WHERE followup_token = %s AND unsubscribed_at IS NULL",
            (token,),
        )
        return cur.rowcount == 1


def invitations_sent_today(conn: Any) -> int:
    """Sends today, in Resend's UTC day."""
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM waitlist_signups WHERE invitation_sent_at >= date_trunc('day', now(), 'UTC')")
        return cur.fetchone()[0]


def invitation_entries(conn: Any, ids: list[int]) -> list[dict[str, Any]]:
    """The asked-for entries, oldest first, each with what decides whether it can be sent."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT w.id, w.email, w.followup_token, w.unsubscribed_at IS NOT NULL, "
            f"{_HAS_ACCOUNT_SQL} FROM waitlist_signups w WHERE w.id = ANY(%s) ORDER BY w.created_at, w.id",
            (ids,),
        )
        keys = ("id", "email", "token", "unsubscribed", "has_account")
        return [dict(zip(keys, row)) for row in cur.fetchall()]


def stamp_invitation(conn: Any, entry_id: int) -> None:
    """The email went: the address may now create an account. The first permission's time stands."""
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(
            "UPDATE waitlist_signups SET invited_at = COALESCE(invited_at, now()), invitation_sent_at = now() WHERE id = %s",
            (entry_id,),
        )


SIGNUP_COLUMNS = ("id", "email", "created_at", "consent_version", "page_variant", "utm", "investor_type", "note",
                  "answered_at", "unsubscribed_at", "invited_at")
_HAS_ACCOUNT_SQL = "EXISTS (SELECT 1 FROM auth.users u WHERE lower(u.email) = w.email)"
LIST_MAX = 5000


def list_signups(conn: Any, limit: int = LIST_MAX) -> list[dict[str, Any]]:
    """Every entry, newest first (the admin Waitlist page), and whether it has an account yet; at most `limit` rows."""
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT {', '.join(f'w.{c}' for c in SIGNUP_COLUMNS)}, {_HAS_ACCOUNT_SQL} FROM waitlist_signups w "
            "ORDER BY w.created_at DESC, w.id DESC LIMIT %s",
            (limit,),
        )
        return [dict(zip((*SIGNUP_COLUMNS, "has_account"), row)) for row in cur.fetchall()]
