"""Location inspector reads - one listing through the location stack, by id.

Reads the ONE answer table (migration 501) plus `listings` and
`location_claims`. W1 deleted the aggregates that lived here: the Location
Quality page renders the `location_town_coverage` gauge's stored cells.

Service-role connection only: the location tables are RLS-on with anon /
authenticated revoked, and these queries are served through the admin-gated
API.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import psycopg
from psycopg.rows import dict_row


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


_INSPECTOR_ROW_SQL = """
    SELECT p.*, l.source AS listing_source, l.source_id_native, l.is_active
    FROM listings l
    LEFT JOIN listing_location p ON p.listing_id = l.id
    WHERE l.id = %s
"""

_INSPECTOR_CLAIMS_SQL = """
    SELECT id, claim_type::text, surface::text, extraction_method::text,
           value_text, value_num, licence_class::text,
           claim_confidence::text, blur_evidence::text,
           first_observed_at, subject_scoped
    FROM location_claims
    WHERE listing_id = %s
    ORDER BY claim_type, first_observed_at DESC, id DESC
    LIMIT 200
"""


def listing_inspector(
    conn: psycopg.Connection,
    listing_id: int | None = None,
    source: str | None = None,
    native_id: str | None = None,
) -> dict[str, Any] | None:
    """One listing through the stack: its answer row + the claims it was resolved
    from. The read-your-writes surface (05 5.5.5)."""
    with conn.cursor(row_factory=dict_row) as cur:
        if listing_id is None:
            if not (source and native_id):
                return None
            cur.execute(
                "SELECT id FROM listings WHERE source = %s AND source_id_native = %s",
                (source, native_id),
            )
            hit = cur.fetchone()
            if hit is None:
                return None
            listing_id = hit["id"]

        cur.execute(_INSPECTOR_ROW_SQL, (listing_id,))
        projection = cur.fetchone()
        if projection is None:
            return None
        # geometry is not JSON-serializable and the API shape rule (05 5.5.1)
        # forbids a bare coordinate without its precision object anyway; the
        # inspector serves the axes, not the point.
        projection.pop("geom", None)

        cur.execute(_INSPECTOR_CLAIMS_SQL, (listing_id,))
        claims = cur.fetchall()

    for row in (projection, *claims):
        for key, val in list(row.items()):
            if isinstance(val, datetime):
                row[key] = val.isoformat()
            elif isinstance(val, (bytes, bytearray, memoryview)):
                # claim_set_hash is bytea: hex so the envelope stays JSON.
                row[key] = bytes(val).hex()

    return {
        "data": {
            "listing_id": listing_id,
            "projection": projection,
            "claims": claims,
        },
        "metadata": {
            "tool": "location_quality.listing_inspector",
            "queried_at": _utcnow(),
            "grain": "listing",
        },
    }
