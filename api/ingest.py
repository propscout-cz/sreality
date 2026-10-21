"""POST /listings/ingest + GET /listings/ingest/{source}/{native_id}: a pasted ad link → ONE
ingest state (docs/design/new-property-from-link.md §3.2). The route never fetches a page: it
resolves the link (`scraper.portal_identity`), answers from what we hold, and otherwise hands the
ad to the worker's drain through the same `listing_detail_queue` row a walk would write. Market
data only, on the service-role connection: no per-account field ever rides along."""

from __future__ import annotations

from typing import Any

from scraper import db
from scraper.portal_identity import (
    MAX_URL_LENGTH,
    PORTALS,
    TOO_LONG_CS,
    UNKNOWN_HOST_CS,
    Refused,
    resolve_url,
)

GONE_CS = "Inzerát na portálu už není."
FAILED_CS = "Stránku se nepodařilo načíst, zkuste to později."
NOT_LANDED_CS = "Tento inzerát neumíme přidat."


def ingest_link(conn: Any, text: str) -> dict[str, Any]:
    """POST: resolve the first link in `text`; hand an ad we do not hold (or hold inactive)
    to the drain. Idempotent: a live queue row only gains acquisition priority."""
    resolved = resolve_url(text)
    if isinstance(resolved, Refused):
        return _state("refused", message_cs=resolved.message_cs)
    source, native = resolved.source, resolved.native_id
    queue = db.queue_state(conn, source, native)
    held = _held(conn, source, native)
    if held is not None:
        if not held["is_active"]:
            # The walk's stored URL, not the paste: a paste never re-aims a held ad's fetch.
            ref = held["source_url"] or resolved.detail_ref
            queue = _arm(conn, source, native, ref, queue)
        return _state("held", source=source, native=native, held=held, queue=queue)
    if queue is None and db.latest_completion_outcome(conn, source, native) == "gone":
        return _state("gone", source=source, native=native, message_cs=GONE_CS)
    queue = _arm(conn, source, native, resolved.detail_ref, queue)
    return _state("queued", source=source, native=native, queue=queue)


def ingest_state(conn: Any, source: str, native: str) -> dict[str, Any]:
    """GET: the same state, read-only — what a dialog polls while the drain works."""
    if source not in PORTALS:
        return _state("refused", message_cs=UNKNOWN_HOST_CS)
    if len(native) > MAX_URL_LENGTH:
        return _state("refused", message_cs=TOO_LONG_CS)
    # Queue first, listing second: the drain commits the listing BEFORE it deletes the
    # row, so a row already gone here means its listing is visible to the next read.
    queue = db.queue_state(conn, source, native)
    held = _held(conn, source, native)
    if held is not None:
        return _state("held", source=source, native=native, held=held, queue=queue)
    if queue is not None:
        if queue["given_up"]:
            return _state("failed", source=source, native=native, queue=queue, message_cs=FAILED_CS)
        return _state("queued", source=source, native=native, queue=queue)
    if db.latest_completion_outcome(conn, source, native) == "gone":
        return _state("gone", source=source, native=native, message_cs=GONE_CS)
    # Nothing queued, nothing held, no gone verdict: the drain read a page it could not
    # land (a category we do not write), or nothing was ever asked. Terminal, so no dialog
    # polls forever, and not "try later": a re-paste ends here again.
    return _state("failed", source=source, native=native, message_cs=NOT_LANDED_CS)


def _arm(
    conn: Any, source: str, native: str, detail_ref: str, queue: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if queue is not None and not queue["given_up"]:
        if queue["claimed_at"] is None:
            db.raise_unclaimed_priority(conn, source, [native], db.QUEUE_PRIORITY_NEW)
        return queue
    db.enqueue_detail(
        conn, source, [(native, db.detail_ref(source, detail_ref), None, db.QUEUE_PRIORITY_NEW)],
    )
    if queue is not None:
        db.rearm_given_up(conn, source, [native])
    return db.queue_state(conn, source, native)


def _held(conn: Any, source: str, native: str) -> dict[str, Any] | None:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, is_active, property_id, source_url FROM listings "
            "WHERE source = %s AND source_id_native = %s",
            (source, native),
        )
        row = cur.fetchone()
    if row is None:
        return None
    listing_id, is_active, property_id, source_url = row
    return {"listing_id": listing_id, "is_active": bool(is_active), "property_id": property_id,
            "source_url": source_url}


def _state(
    state: str,
    *,
    source: str | None = None,
    native: str | None = None,
    held: dict[str, Any] | None = None,
    queue: dict[str, Any] | None = None,
    message_cs: str | None = None,
) -> dict[str, Any]:
    return {
        "state": state,
        "message_cs": message_cs,
        "source": source,
        "source_id_native": native,
        "listing_id": held["listing_id"] if held else None,
        "is_active": held["is_active"] if held else None,
        "property_id": held["property_id"] if held else None,
        "queue": queue,
    }
