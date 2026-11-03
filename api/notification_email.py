"""The notification email (docs/design/email/PROGRAM.md PR 4): an account's VISIBLE unseen events, sent to its sign-in
address as a daily summary (at or after 07:00 Prague, once per Prague day) or as they come, at most once per 15 minutes,
per `account_notification_email.mode` (no row reads as daily). scraper/realtime_worker.py's `email` lane runs
`deliver_once` every 60 s; the routes in api/routes/notification_email.py read and set the mode.

THE CLAIM is the only lock. Under the account's settings row (FOR UPDATE), a pass reads where the account's last email
that did not fail stopped (`covered_until`), then appends a `sending` row covering everything up to now. A daily row is
unique per Prague day, so a second worker in a deploy overlap finds the day taken; an instant claim is refused while
any row of the account is under 15 minutes old. The send follows the commit, then one compare-and-set on the claim
records sent, empty or failed. A process that dies between the send and that update leaves `sending`, which counts as
sent: an email can be lost, never doubled. No address, token or text reaches the log or the heartbeat; a failed send is
its kind and HTTP status alone.
"""

from __future__ import annotations

import contextlib
import html
import logging
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from api import email_layout, resend_client
from api.http_json import HttpJsonError
from api.notification_render import render
from api.notifications import VISIBLE_SQL

LOG = logging.getLogger(__name__)

SENDER = "PropScout <tym@mail.propscout.cz>"
REPLY_TO = "support@propscout.cz"
APP_ORIGIN = "https://app.propscout.cz"
MODES: tuple[str, ...] = ("off", "daily", "instant")
DEFAULT_MODE = "daily"
DAILY_HOUR = 7
# The instant window, and the wait before a failed daily is claimed again.
GAP = "15 minutes"
# A failed daily is claimed again until its row has been claimed this many times.
MAX_ATTEMPTS = 3
MAX_LINES = 50
MAX_ACCOUNTS = 50
# Resend's default rate limit is two requests a second per team.
SEND_SPACING_S = 0.6
# The pass's `portal_errors` subjects: `lane/email/<step>` in the jobs check.
STEPS: tuple[str, ...] = ("settings", "select", "send")
_PRAGUE = ZoneInfo("Europe/Prague")

# Every account with a member gets its row, so every email it is sent carries an unsubscribe token.
ENSURE_SETTINGS_SQL = (
    "INSERT INTO account_notification_email (account_id) "
    "SELECT a.id FROM accounts a "
    "WHERE EXISTS (SELECT 1 FROM account_members m WHERE m.account_id = a.id) "
    "  AND NOT EXISTS (SELECT 1 FROM account_notification_email s WHERE s.account_id = a.id) "
    "ON CONFLICT (account_id) DO NOTHING"
)

# The oldest member's sign-in email, in account_email's order (migration 610) but never its name fallback.
_ADDRESS = (
    "(SELECT u.email FROM account_members m JOIN auth.users u ON u.id = m.user_id "
    " WHERE m.account_id = s.account_id ORDER BY m.created_at, m.user_id LIMIT 1)"
)
# Where the account's next email starts: its last claim that did not fail, and never more than a day back.
_SINCE = (
    "greatest(now() - interval '24 hours', (SELECT max(p.covered_until) FROM notification_email_sends p "
    "WHERE p.account_id = s.account_id AND p.status <> 'failed'))"
)

# The accounts this pass may claim: a daily one whose Prague day is still unsent (or failed, waited out and under
# MAX_ATTEMPTS) once the day has opened, and an instant one with an event to tell and no claim in the last GAP.
CANDIDATES_SQL = (
    "SELECT c.account_id, c.mode, c.email FROM ("
    f"  SELECT s.account_id, s.mode, {_ADDRESS} AS email FROM account_notification_email s "
    "   WHERE (s.mode = 'daily' AND %(daily_open)s AND NOT EXISTS ("
    "            SELECT 1 FROM notification_email_sends r WHERE r.account_id = s.account_id AND r.mode = 'daily' "
    "               AND r.local_day = %(day)s AND NOT (r.status = 'failed' AND r.attempts < %(max_attempts)s "
    f"              AND r.claimed_at <= now() - interval '{GAP}'))) "
    "      OR (s.mode = 'instant' AND NOT EXISTS ("
    "            SELECT 1 FROM notification_email_sends r WHERE r.account_id = s.account_id "
    f"              AND r.claimed_at > now() - interval '{GAP}') "
    "          AND EXISTS (SELECT 1 FROM notification_dispatches d WHERE d.account_id = s.account_id "
    f"              AND d.seen_at IS NULL AND {VISIBLE_SQL} AND d.dispatched_at > {_SINCE}))"
    ") c WHERE c.email IS NOT NULL ORDER BY c.account_id LIMIT %(limit)s"
)

# The claim's three statements, in one transaction under the settings row's lock.
LOCK_SQL = "SELECT mode, unsubscribe_token FROM account_notification_email WHERE account_id = %(account)s FOR UPDATE"
SINCE_SQL = (
    "SELECT greatest(now() - interval '24 hours', max(covered_until)) FROM notification_email_sends "
    "WHERE account_id = %(account)s AND status <> 'failed'"
)
DAILY_CLAIM_SQL = (
    "INSERT INTO notification_email_sends (account_id, mode, local_day, covered_until, status) "
    "VALUES (%(account)s, 'daily', %(day)s, now(), 'sending') "
    "ON CONFLICT (account_id, local_day) WHERE mode = 'daily' DO UPDATE "
    "SET status = 'sending', attempts = notification_email_sends.attempts + 1, claimed_at = now(), "
    "    covered_until = now(), event_count = 0, reason = NULL "
    "WHERE notification_email_sends.status = 'failed' AND notification_email_sends.attempts < %(max_attempts)s "
    f"  AND notification_email_sends.claimed_at <= now() - interval '{GAP}' "
    "RETURNING id, attempts, covered_until"
)
_EVENTS_WHERE = (
    "d.account_id = %(account)s AND d.seen_at IS NULL "
    f"AND {VISIBLE_SQL} "
    "AND d.dispatched_at > %(since)s AND d.dispatched_at <= %(until)s"
)
INSTANT_CLAIM_SQL = (
    "INSERT INTO notification_email_sends (account_id, mode, covered_until, status) "
    "SELECT %(account)s, 'instant', now(), 'sending' "
    "WHERE NOT EXISTS (SELECT 1 FROM notification_email_sends r WHERE r.account_id = %(account)s "
    f"                   AND r.claimed_at > now() - interval '{GAP}') "
    "  AND EXISTS (SELECT 1 FROM notification_dispatches d WHERE d.account_id = %(account)s AND d.seen_at IS NULL "
    f"               AND {VISIBLE_SQL} AND d.dispatched_at > %(since)s AND d.dispatched_at <= now()) "
    "RETURNING id, attempts, covered_until"
)

# What the claim covers, read after its commit: still VISIBLE and unseen now.
COUNTS_SQL = f"SELECT d.change_kind, count(*) FROM notification_dispatches d WHERE {_EVENTS_WHERE} GROUP BY 1"
# The renderer's row, joined as the push claim joins it (api/push_delivery.py CLAIM_SQL); newest first.
EVENTS_SQL = (
    "SELECT d.id::text, d.change_kind, d.trigger_price_czk, d.prev_price_czk, pp.display_label, "
    "       l.category_main, l.subtype, l.disposition, l.price_unit "
    "FROM notification_dispatches d "
    "LEFT JOIN properties_public pp ON pp.property_id = d.property_id "
    "LEFT JOIN listings l ON l.id = d.listing_id "
    f"WHERE {_EVENTS_WHERE} "
    "ORDER BY d.dispatched_at DESC, d.id LIMIT %(limit)s"
)

# Each outcome is a compare-and-set on the claim it holds (id and attempts as claimed, still sending).
_AS_CLAIMED = "WHERE id = %(id)s AND attempts = %(attempts)s AND status = 'sending'"
SENT_SQL = (
    "UPDATE notification_email_sends SET status = 'sent', sent_at = now(), provider_message_id = %(message_id)s, "
    f"event_count = %(events)s {_AS_CLAIMED}"
)
EMPTY_SQL = f"UPDATE notification_email_sends SET status = 'empty', event_count = 0 {_AS_CLAIMED}"
FAILED_SQL = (
    "UPDATE notification_email_sends SET status = 'failed', reason = %(reason)s, event_count = %(events)s "
    f"{_AS_CLAIMED}"
)

READ_MODE_SQL = "SELECT mode FROM account_notification_email WHERE account_id = %(account)s"
SET_MODE_SQL = (
    "INSERT INTO account_notification_email (account_id, mode) VALUES (%(account)s, %(mode)s) "
    "ON CONFLICT (account_id) DO UPDATE SET mode = EXCLUDED.mode, updated_at = now() RETURNING mode"
)
UNSUBSCRIBE_SQL = (
    "UPDATE account_notification_email SET mode = 'off', updated_at = now() "
    "WHERE unsubscribe_token = %(token)s AND mode <> 'off'"
)

_INTRO = {
    "daily": "tohle se za posledních 24 hodin změnilo u nabídek, které hlídáte:",
    "instant": "tohle se právě změnilo u nabídek, které hlídáte:",
}
_WHY = {
    "daily": "Tento přehled chodí jednou denně, protože máte zapnutá e-mailová upozornění.",
    "instant": "Tato upozornění chodí nejvýš jednou za 15 minut, protože jste si je tak nastavili.",
}
_CHANGE = "Změnit to můžete v aplikaci pod svým profilem."
_OPEN = "Otevřít v PropScoutu"
_OFF = "Vypnout"
_BUTTON_STYLE = ("display:inline-block;padding:10px 18px;background:#222;color:#fff;text-decoration:none;"
                 "border-radius:6px")


@dataclass(frozen=True)
class Candidate:
    account_id: uuid.UUID
    mode: str
    email: str


@dataclass(frozen=True)
class Claim:
    """One claimed email: its row as claimed, the token its off link carries and the events it covers."""

    id: int
    attempts: int
    mode: str
    token: uuid.UUID
    since: datetime
    until: datetime


@dataclass(frozen=True)
class Email:
    subject: str
    html: str
    text: str


def _fetch(conn: Any, sql: str, params: Mapping[str, Any] | None = None) -> list[tuple[Any, ...]]:
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def _one(conn: Any, sql: str, params: Mapping[str, Any]) -> tuple[Any, ...] | None:
    rows = _fetch(conn, sql, params)
    return rows[0] if rows else None


def _run(conn: Any, sql: str, params: Mapping[str, Any] | None = None) -> int:
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.rowcount or 0


def read_mode(conn: Any, account_id: uuid.UUID) -> str:
    row = _one(conn, READ_MODE_SQL, {"account": account_id})
    return str(row[0]) if row else DEFAULT_MODE


def set_mode(conn: Any, account_id: uuid.UUID, mode: str) -> str:
    if mode not in MODES:
        raise ValueError(f"unknown mode {mode!r}")
    row = _one(conn, SET_MODE_SQL, {"account": account_id, "mode": mode})
    return str(row[0]) if row else mode


def unsubscribe(conn: Any, token: uuid.UUID) -> bool:
    """The off link: True when this switched an account off, False for an unknown token or one already off."""
    return _run(conn, UNSUBSCRIBE_SQL, {"token": token}) > 0


def prague_now(now: datetime | None = None) -> datetime:
    return (now or datetime.now(_PRAGUE)).astimezone(_PRAGUE)


def candidates(conn: Any, local: datetime, *, limit: int = MAX_ACCOUNTS) -> list[Candidate]:
    params = {"daily_open": local.hour >= DAILY_HOUR, "day": local.date(), "max_attempts": MAX_ATTEMPTS,
              "limit": limit}
    return [Candidate(a, m, e) for a, m, e in _fetch(conn, CANDIDATES_SQL, params)]


def claim(conn: Any, account_id: uuid.UUID, mode: str, day: date) -> Claim | None:
    """One email for this account, or None: its mode moved, its day is taken, its window is not over, nothing new."""
    params: dict[str, Any] = {"account": account_id, "day": day, "max_attempts": MAX_ATTEMPTS}
    with conn.transaction():
        settings = _one(conn, LOCK_SQL, params)
        if settings is None or settings[0] != mode or mode == "off":
            return None
        since = _fetch(conn, SINCE_SQL, params)[0][0]
        claimed = _one(conn, DAILY_CLAIM_SQL if mode == "daily" else INSTANT_CLAIM_SQL, {**params, "since": since})
    if claimed is None:
        return None
    return Claim(int(claimed[0]), int(claimed[1]), mode, settings[1], since, claimed[2])


def _window(c: Claim, account_id: uuid.UUID) -> dict[str, Any]:
    return {"account": account_id, "since": c.since, "until": c.until, "limit": MAX_LINES}


def _plural(n: int, one: str, few: str, many: str) -> str:
    return f"{n} {one if n == 1 else few if 2 <= n <= 4 else many}"


def subject(counts: Mapping[str, int]) -> str:
    """'3 nové inzeráty a 1 zlevnění pro vaše hledání': new adverts, price drops, then every other kind together."""
    new, drops = counts.get("new", 0), counts.get("price_drop", 0)
    other = sum(n for kind, n in counts.items() if kind not in ("new", "price_drop"))
    parts = []
    if new:
        parts.append(_plural(new, "nový inzerát", "nové inzeráty", "nových inzerátů"))
    if drops:
        parts.append(f"{drops} zlevnění")
    if other:
        parts.append(_plural(other, "další změna", "další změny", "dalších změn"))
    told = parts[-1] if len(parts) < 2 else f"{', '.join(parts[:-1])} a {parts[-1]}"
    return f"{told} pro vaše hledání"


def _line(row: Mapping[str, Any]) -> tuple[str, str]:
    text = render(row)
    return text.title, text.body


def unsubscribe_url(token: uuid.UUID) -> str:
    return f"{email_layout.API_ORIGIN}/notifications/email/unsubscribe?t={token}"


def compose(mode: str, rows: Sequence[Mapping[str, Any]], counts: Mapping[str, int], token: uuid.UUID) -> Email:
    """The email for these event rows (newest first, at most MAX_LINES) out of `counts`' total."""
    esc = html.escape
    lines = [(_line(r), f"{APP_ORIGIN}/n/{r['id']}") for r in rows]
    told = [f"{title}: {body}" if body else title for (title, body), _ in lines]
    more = sum(counts.values()) - len(rows)
    feed, off = f"{APP_ORIGIN}/notifications", unsubscribe_url(token)
    items = "".join(
        f'<p style="margin:0 0 8px"><a href="{esc(url)}" style="color:#222">'
        f"<strong>{esc(title)}{':' if body else ''}</strong>{' ' + esc(body) if body else ''}</a></p>"
        for (title, body), url in lines
    )
    if more > 0:
        items += f'<p style="margin:0 0 8px">a dalších {more} v aplikaci</p>'
    body = (
        f"<p>Dobrý den,</p><p>{_INTRO[mode]}</p>{items}"
        f'<p style="margin:24px 0"><a href="{esc(feed)}" style="{_BUTTON_STYLE}">{_OPEN}</a></p>'
        f'<p style="color:#555;font-size:14px">{_WHY[mode]} {_CHANGE} <a href="{esc(off)}">{_OFF}</a></p>'
    )
    text = "\n\n".join((
        "Dobrý den,", _INTRO[mode],
        *(f"{t}\n{url}" for t, (_, url) in zip(told, lines)),
        *([f"a dalších {more} v aplikaci"] if more > 0 else []),
        f"{_OPEN}: {feed}",
        f"{_WHY[mode]} {_CHANGE}\n{_OFF}: {off}",
        email_layout.OPERATOR,
    ))
    return Email(subject(counts), email_layout.page(told[0] if told else "", body), text)


def failure_reason(exc: HttpJsonError) -> str:
    """'status_429', 'timeout': the failure's kind and status, never its host's answer or an address."""
    return f"{exc.reason}_{exc.status}" if exc.status else exc.reason


def _finish(conn: Any, c: Claim, sql: str, **params: Any) -> None:
    _run(conn, sql, {"id": c.id, "attempts": c.attempts, **params})


def deliver(conn: Any, candidate: Candidate, day: date, send: Callable[..., str]) -> tuple[str, str | None] | None:
    """Claim, read, send and record one account's email: (sent | empty | failed, the failure's reason), or None
    when nothing was claimed."""
    c = claim(conn, candidate.account_id, candidate.mode, day)
    if c is None:
        return None
    try:
        counts = {str(k): int(n) for k, n in _fetch(conn, COUNTS_SQL, _window(c, candidate.account_id))}
        if not sum(counts.values()):
            _finish(conn, c, EMPTY_SQL)
            return "empty", None
        rows = [dict(zip(("id", "change_kind", "trigger_price_czk", "prev_price_czk", "display_label",
                          "category_main", "subtype", "disposition", "price_unit"), r))
                for r in _fetch(conn, EVENTS_SQL, _window(c, candidate.account_id))]
        email = compose(c.mode, rows, counts, c.token)
    except Exception as exc:
        with contextlib.suppress(Exception):
            _finish(conn, c, FAILED_SQL, reason=type(exc).__name__, events=0)
        raise
    events = sum(counts.values())
    try:
        message_id = send(sender=SENDER, to=candidate.email, reply_to=REPLY_TO, subject=email.subject,
                          html=email.html, text=email.text,
                          headers=email_layout.one_click_headers(unsubscribe_url(c.token)))
    except HttpJsonError as exc:
        reason = failure_reason(exc)
        _finish(conn, c, FAILED_SQL, reason=reason, events=events)
        return "failed", reason
    _finish(conn, c, SENT_SQL, message_id=message_id or None, events=events)
    return "sent", None


def deliver_once(connect: Callable[[], Any], *, stop: Callable[[], bool],
                 send: Callable[..., str] = resend_client.send, now: datetime | None = None,
                 sleep: Callable[[float], None] = time.sleep) -> dict[str, Any]:
    """One pass on one connection from `connect`. Returns the heartbeat's `last`, plus `attempted` and `raised`
    (step -> a class name or a failure's reason, never a text that could hold an address)."""
    local = prague_now(now)
    last: dict[str, Any] = {"daily_open": local.hour >= DAILY_HOUR, "candidates": 0, "claimed": 0, "sent": 0,
                            "empty": 0, "failed": 0}
    raised: dict[str, str] = {}
    conn = connect()
    try:
        try:
            last["settings_added"] = _run(conn, ENSURE_SETTINGS_SQL)
        except Exception as exc:  # noqa: BLE001 - a step's error is its class name; its text may hold a row
            raised["settings"] = type(exc).__name__
        try:
            due = candidates(conn, local)
        except Exception as exc:  # noqa: BLE001
            raised["select"] = type(exc).__name__
            due = []
        last["candidates"] = len(due)
        errors: dict[str, int] = {}
        for i, candidate in enumerate(due):
            if stop():
                last["stopped"] = True
                break
            if i:
                sleep(SEND_SPACING_S)
            try:
                outcome = deliver(conn, candidate, local.date(), send)
            except Exception as exc:  # noqa: BLE001 - one account must not end the pass
                errors[type(exc).__name__] = errors.get(type(exc).__name__, 0) + 1
                continue
            if outcome is None:
                continue
            kind, reason = outcome
            last["claimed"] += 1
            last[kind] += 1
            if reason:
                errors[reason] = errors.get(reason, 0) + 1
        if errors:
            raised["send"] = f"{sum(errors.values())} of {len(due)} accounts failed: " + ", ".join(sorted(errors))
    finally:
        with contextlib.suppress(Exception):
            conn.close()
    if raised or last["claimed"]:
        LOG.info("EMAIL candidates=%d sent=%d empty=%d failed=%d raised=%s", last["candidates"], last["sent"],
                 last["empty"], last["failed"], raised)
    return {**last, "attempted": list(STEPS), "raised": raised}
