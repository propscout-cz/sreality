"""Read-only broker intelligence queries (detail, contacts, listings, Browse's broker scope).

The server side of the browser read layer (frontend/src/lib/brokers.ts): the
/brokers/* routes (api/routes/brokers.py), the property download and an agent's
get_property_brokers all hit the SAME public views — one definition of "who has
what". A broker's full contact set is NOT exposed by the anon public views (PII),
so broker_contacts here is the only path to it — `apply_pii_policy` is what the
route layer runs over every other envelope so a non-admin caller gets has_email /
has_phone flags instead of the values.

Read-only — no toolkit write exception (rule #5) is added.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

from psycopg.rows import dict_row

from toolkit import _now_iso

MAX_BATCH = 1000
_REDACTED = "[redacted]"
_EMAIL_RE = re.compile(r"[^@\s<>,;()\[\]\"']+@[^@\s<>,;()\[\]\"']+\.[a-z]{2,}",
                       re.IGNORECASE)
_PHONE_RE = re.compile(r"^\+?\d[\d\s./()-]{7,17}$")


def _envelope(tool: str, data: Any, filters_used: dict[str, Any], result_count: int,
              data_freshness: str | None) -> dict[str, Any]:
    return {
        "data": data,
        "metadata": {
            "tool": tool,
            "filters_used": filters_used,
            "result_count": result_count,
            "queried_at": _now_iso(),
            "data_freshness": data_freshness,
        },
    }


def _iso(v: Any) -> str | None:
    return v.isoformat() if v is not None and hasattr(v, "isoformat") else v


def _bounded(ids: list[int]) -> list[int]:
    """Deduped, sorted, capped. The one batch route (POST /brokers/by-listings)
    rejects an over-cap batch outright and get_property_brokers reads in MAX_BATCH
    batches; this slice is only the backstop for a direct toolkit/agent caller,
    which must never silently spill into a huge IN."""
    return sorted({int(i) for i in ids})[:MAX_BATCH]


def apply_pii_policy(envelope: dict[str, Any], *, include_pii: bool) -> dict[str, Any]:
    """Keep or mask broker contact PII, always stamping metadata.pii_masked.

    D1/D2 (2026-08-12 broker E2E review): these reads moved off the static shared
    secret — which shipped inside the SPA bundle until 2026-10-13 — onto real user
    identity, so an ordinary logged-in caller must not receive 2000 brokers' email + phone.
    """
    masked = envelope if include_pii else {**envelope, "data": _mask(envelope.get("data"))}
    return {**masked,
            "metadata": {**(masked.get("metadata") or {}), "pii_masked": not include_pii}}


def _pii_kind(key: str) -> str | None:
    lowered = key.lower()
    return next((k for k in ("email", "phone") if k in lowered), None)


def _redact_shaped(text: str) -> str:
    """Shape rule under the name rule: contact PII also arrives under non-contact
    columns — two live brokers' display_name IS their email address, and the portal
    name field is free text — so a name-only mask promises more than it delivers.
    A phone is matched whole-string only, or a source_url's numeric id would go."""
    if _PHONE_RE.match(text) and sum(c.isdigit() for c in text) >= 9:
        return _REDACTED
    return _EMAIL_RE.sub(_REDACTED, text)


def _like_escape(term: str) -> str:
    """`%` and `_` inside a BOUND LIKE value are still wildcards — psycopg passes
    the value as data and LIKE, correctly, interprets what is in it. Unescaped,
    `@_` was a one-character probe that walked straight under search()'s two-char
    minimum, and a bare `%` seq-scanned brokers_public on every request. Backslash
    is LIKE's own default escape character, so no ESCAPE clause is needed (and none
    is written: a literal backslash in the SQL TEXT would depend on
    standard_conforming_strings, while the bound value never does)."""
    for ch in ("\\", "%", "_"):
        term = term.replace(ch, "\\" + ch)
    return term


def _matches_masked(display_name: Any, term: str) -> bool:
    """Does `term` still match once the row is masked the way it will be returned?

    A non-admin's PREDICATE must not see what their PROJECTION hides, or search is
    an oracle: probe, and the presence of a row (with its broker_id) confirms the
    guess, recovering an email `_redact_shaped` redacted one character at a time.
    Filtering on the masked text — the same function that builds the response —
    keeps every ordinary name findable (only the redacted SPAN stops matching, so
    "Kancelář Honzík <info@honzik.cz>" is still found by "Honzík") and generalises
    to whatever the shape rule learns to redact next."""
    if not isinstance(display_name, str):
        return False
    return term.casefold() in _redact_shaped(display_name).casefold()


def _mask(value: Any) -> Any:
    """Swap every contact column for a has_* flag, recursing into the dossier.

    Matched on the column NAME, not a fixed list of today's columns: these queries
    are `select *` over views a migration can widen (broker_listings_public has
    already grown twice), so a denylist would leak a new *_email the day it lands.
    The dossier's contact list carries its PII in a generic `value` column no name
    rule can catch, so it is dropped whole — /brokers/{id}/contacts is admin-only.
    `has_email` / `has_phone` mean "a current primary contact is on file", i.e. the
    brokers rollup's primary_email / primary_phone, not every address ever seen —
    and primary_email is picked from the most-recently-seen IDENTITY while
    primary_phone is a max over all of a broker's identities, so a group spanning an
    email-less source can report has_email=false while the dossier still holds the
    address. Firm identifiers (firm_name, firm_domain) are deliberately NOT masked:
    a company's web domain is a business identifier printed on every listing page,
    so `pii_masked` promises that CONTACT VALUES are masked, not that nothing about
    the row is attributable.
    """
    if isinstance(value, list):
        return [_mask(v) for v in value]
    if isinstance(value, str):
        return _redact_shaped(value)
    if not isinstance(value, dict):
        return value
    out: dict[str, Any] = {}
    flags: dict[str, bool] = {}
    for key, val in value.items():
        if key == "contacts":
            continue
        kind = _pii_kind(key)
        if kind is None:
            out[key] = _mask(val)
        else:
            flags[kind] = flags.get(kind, False) or bool(val)
    for kind, present in flags.items():
        out[f"has_{kind}"] = present
    return out


def firm_options(conn: Any, *, q: str | None = None, limit: int = 20) -> dict[str, Any]:
    """Companies matching `q` (name or domain), busiest first; an empty query
    browses the top firms instead of returning nothing."""
    term = (q or "").strip()
    limit = max(1, min(int(limit), 100))
    like = f"%{_like_escape(term)}%"
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT firm_id, canonical_domain, display_name, is_franchise, broker_count "
            "FROM firms_public "
            # display_name is NULL for every franchise domain and any domain
            # under the resolver's 60% modal-label share (migration 190's
            # _FIRM_DISPLAY_NAMES) — canonical_domain must match too, or
            # "mmreality" would find nothing.
            "WHERE (%s = '' OR coalesce(display_name, '') ILIKE %s OR canonical_domain ILIKE %s) "
            # broker_count, not a listing/property count: firms never got the
            # migration 396 CZ-scope split, so ranking on inventory size would
            # resurface the foreign-syndication distortion that migration fixed.
            "ORDER BY broker_count DESC NULLS LAST, canonical_domain LIMIT %s",
            (term, like, like, limit))
        rows = cur.fetchall()
    return _envelope("broker_firm_options", rows, {"query": term, "limit": limit}, len(rows), None)


def search(conn: Any, query: str, *, limit: int = 12,
           include_pii: bool = False) -> dict[str, Any]:
    """Brokers whose display name matches `query` (>=2 chars), busiest first.

    Ranked on the CZ-scoped count (migration 396), like every other broker
    ranking: two idnes syndication feeds carry ~26k foreign listings between them
    and would otherwise head the results for any query they matched. Both counts
    are returned, so the row still shows the broker's whole book.

    `include_pii` is the caller's identity, not a formatting flag: without it the
    ILIKE ran over the RAW display_name while the response redacted it, so a
    non-admin could binary-search the redacted content back out. Defaults closed,
    so an agent or a new route gets the masked predicate unless it says otherwise.
    """
    term = (query or "").strip()
    limit = max(1, min(int(limit), 100))
    if len(term) < 2:
        return _envelope("broker_search", [], {"query": term, "limit": limit}, 0, None)
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT * FROM brokers_public WHERE display_name ILIKE %s "
            "ORDER BY cz_active_property_count DESC NULLS LAST LIMIT %s",
            (f"%{_like_escape(term)}%", limit))
        rows = cur.fetchall()
    if not include_pii:
        rows = [r for r in rows if _matches_masked(r.get("display_name"), term)]
    fresh = max((r["last_seen_at"] for r in rows if r.get("last_seen_at")), default=None)
    for r in rows:
        r["first_seen_at"], r["last_seen_at"] = _iso(r.get("first_seen_at")), _iso(r.get("last_seen_at"))
    return _envelope("broker_search", rows, {"query": term, "limit": limit}, len(rows), _iso(fresh))


def get_broker(conn: Any, broker_id: int) -> dict[str, Any] | None:
    """Full broker dossier: identity row + firm memberships + regional footprint +
    every distinct contact. Returns None if the broker id is unknown / merged away."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT * FROM brokers_public WHERE broker_id = %s", (broker_id,))
        broker = cur.fetchone()
        if broker is None:
            return None
        cur.execute(
            "SELECT * FROM broker_firm_memberships_public WHERE broker_id = %s "
            "ORDER BY last_seen_at DESC NULLS LAST", (broker_id,))
        memberships = cur.fetchall()
        # W4: the ('*','*') rollup cell per region IS the broker's regional
        # total, computed exactly — never sum category cells (a property whose
        # listings straddle cells would count twice).
        cur.execute(
            "SELECT s.geo_id, o.name, "
            "  s.property_count, s.active_property_count, s.listing_count "
            "FROM broker_region_type_stats s "
            "LEFT JOIN broker_geo_options o ON o.geo_level='region' AND o.geo_id=s.geo_id "
            "WHERE s.broker_id = %s AND s.geo_level='region' "
            "  AND s.category_main = '*' AND s.category_type = '*' "
            "ORDER BY s.active_property_count DESC", (broker_id,))
        region_shares = cur.fetchall()
        contacts = _contacts(cur, broker_id)
    for coll in (broker, *memberships):
        for k in ("first_seen_at", "last_seen_at"):
            if k in coll:
                coll[k] = _iso(coll[k])
    data = {"broker": broker, "memberships": memberships,
            "region_shares": region_shares, "contacts": contacts}
    return _envelope("broker_detail", data, {"broker_id": broker_id}, 1, broker.get("last_seen_at"))


def broker_listings(conn: Any, broker_id: int, *, limit: int = 500) -> dict[str, Any]:
    """A broker's listings (active first), via broker_listings_public."""
    limit = max(1, min(int(limit), 2000))
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT * FROM broker_listings_public WHERE broker_id = %s "
            "ORDER BY is_active DESC, last_seen_at DESC NULLS LAST LIMIT %s",
            (broker_id, limit))
        rows = cur.fetchall()
    fresh = max((r["last_seen_at"] for r in rows if r.get("last_seen_at")), default=None)
    for r in rows:
        r["last_seen_at"] = _iso(r.get("last_seen_at"))
    return _envelope("broker_listings", rows, {"broker_id": broker_id, "limit": limit},
                     len(rows), _iso(fresh))


def book_rule_sql(alias: str) -> str:
    """MS19's broker side, THE one rendering: the portal rule (migration 657) over a `broker_book` row (692), so
    "broker + portal" means ONE ad of the broker on `%(portals)s` (any, with none) in `%(status)s`, the names the
    property side binds. The property's own status and every exclusion are the caller's."""
    return (f"portal_status_matches({alias}.is_active, {alias}.all_sources, {alias}.active_sources, "
            "%(portals)s::text[], %(status)s::text)")


# No listing_location join: browse_list serves a property only when its canonical ad is placed (rule 25), and that is
# the read this allowlist narrows.
_BROKER_PROPERTY_IDS_SQL = f"""
    SELECT b.property_id FROM broker_book b
    WHERE b.broker_id = %(broker_id)s AND {book_rule_sql("b")}
    ORDER BY b.property_id LIMIT %(limit)s
"""


def broker_property_ids(conn: Any, broker_id: int, *, status: str = "any",
                        portals: Sequence[str] = (), limit: int = 50_000) -> dict[str, Any]:
    """The properties where the broker has an ad on `portals` in `status` (the portal rule's portal
    side, MS19): Browse's `brokerId` allowlist (frontend/src/lib/queries.ts `resolveBrokerPrefilter`), complete up to `limit`,
    because a silently truncated allowlist under-plots the map; `limit + 1` detects an overflow."""
    limit = max(1, min(int(limit), 50_000))
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(_BROKER_PROPERTY_IDS_SQL, {
            "broker_id": broker_id, "portals": list(portals), "status": status,
            "limit": limit + 1})
        ids = [r["property_id"] for r in cur.fetchall()]
    capped = len(ids) > limit
    if capped:
        ids = ids[:limit]
    envelope = _envelope("broker_property_ids", ids,
                         {"broker_id": broker_id, "status": status, "portals": list(portals)},
                         len(ids), None)
    envelope["metadata"]["capped"] = capped
    return envelope


def listing_brokers(conn: Any, listing_ids: list[int]) -> dict[str, Any]:
    """The brokers behind many listings in one round-trip, keyed on the surrogate
    listing_id: the property page's broker list and the pipeline board's broker line."""
    ids = _bounded(listing_ids)
    rows: list[dict[str, Any]] = []
    if ids:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                "SELECT * FROM listing_broker_public WHERE listing_id = ANY(%s)", (ids,))
            rows = cur.fetchall()
    return _envelope("listing_brokers", rows, {"listing_ids": ids}, len(rows), None)


def property_brokers(
    ads: list[dict[str, Any]], by_listing: dict[int, dict[str, Any]], canonical_id: int
) -> tuple[list[dict[str, Any]], bool]:
    """lib/brokers.propertyBrokers (MS7): the brokers of the active ads, one per person carrying
    the ads that person advertises (`ads`), the canonical ad's first; with no active ad,
    those of all its ads, flagged."""
    active = [a for a in ads if a["is_active"]]
    pool = active or ads
    ordered = sorted(pool, key=lambda a: a["id"] != canonical_id)
    seen: dict[int, dict[str, Any]] = {}
    out: list[dict[str, Any]] = []
    for ad in ordered:
        b = by_listing.get(ad["id"])
        if b is None:
            continue
        row = seen.get(b["broker_id"])
        if row is None:
            row = seen[b["broker_id"]] = {**b, "ads": []}
            out.append(row)
        row["ads"].append(ad)
    return out, not active


def broker_contacts(conn: Any, broker_id: int) -> dict[str, Any]:
    """Every distinct (kind, value) contact across a broker's identities — the full
    reachable set for outreach. PII; this is not exposed by the anon public views."""
    with conn.cursor(row_factory=dict_row) as cur:
        contacts = _contacts(cur, broker_id)
    return _envelope("broker_contacts", contacts, {"broker_id": broker_id}, len(contacts), None)


def _contacts(cur: Any, broker_id: int) -> list[dict[str, Any]]:
    cur.execute(
        "SELECT c.kind, c.value, array_agg(DISTINCT c.source ORDER BY c.source) AS sources, "
        "  max(c.last_seen_at) AS last_seen_at "
        "FROM broker_identity_contacts c "
        "JOIN broker_identities bi ON bi.id = c.broker_identity_id "
        "WHERE bi.broker_id = %s "
        "GROUP BY c.kind, c.value ORDER BY c.kind, max(c.last_seen_at) DESC", (broker_id,))
    rows = cur.fetchall()
    for r in rows:
        r["last_seen_at"] = _iso(r.get("last_seen_at"))
    return rows
