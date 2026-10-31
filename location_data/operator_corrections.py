"""Location W3b: the OPERATOR claim producer — the address picker (ruling Q5).

The admin picks ONE register entity (an address point, a street or an obec) and `submit_pick`
writes it on EVERY ad of the property as the four `PICK_TYPES`, in the register's own names;
the types it does not state are tombstones, so a re-pick replaces the operator's whole
statement and `pick=None` hands every ad back to its own words. Through the one write
(`claims_store.write_claims`, W3): licence `operator` outranks the portal at read, an unchanged
value writes nothing, and no retract or re-mine reaches it (no `contract_entry_id`).
`resolve_now()` then refreshes the location ad (05 5.5.5); if it fails, the enqueue converges.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

import psycopg
from psycopg.rows import dict_row

from location_data.claims_common import Claim
from location_data.claims_store import write_claims

LOG = logging.getLogger("location.operator_corrections")

EXTRACTOR_ID = "operator.correction"
EXTRACTOR_VERSION = "2"

# The picker's four types, a subset of the contract vocabulary (`contracts.CLAIM_TYPES`). A
# pin (`coordinate`) is deliberately absent: the portal's pin stays as evidence (limit L2).
PICK_TYPES: tuple[str, ...] = ("address_point_id", "street_name", "obec_name", "okres_name")
# A picked level -> the `listing_location` column that holds its code once the pick binds.
PICK_COLUMN: dict[str, str] = {
    "adresni_misto": "ruian_adm_kod", "ulice": "ulice_kod", "obec": "obec_kod",
}


class CorrectionError(ValueError):
    """Invalid pick (maps to HTTP 422 at the API layer)."""


class UnknownPropertyError(CorrectionError):
    """No active property with ads (HTTP 404)."""


_ADS_SQL = """
    SELECT l.id, l.source, l.source_id_native, l.id = p.location_listing_ref_id
      FROM properties p JOIN listings l ON l.property_id = p.id
     WHERE p.id = %s AND p.status = 'active'
     ORDER BY l.id
"""

_LISTINGS_SQL = """
    SELECT id, source, source_id_native FROM listings WHERE id = ANY(%s) ORDER BY id
"""

# The register's names for a picked code, as (street, obec, okres); the okres is the obec's
# parent at that level (Praha's parent is its kraj, so it has none). No row: not current.
_REGISTER_SQL: dict[str, str] = {
    "adresni_misto": "SELECT 1 FROM ruian_address_points WHERE kod_adm = %s AND valid_to IS NULL",
    "ulice": """
        SELECT s.name, o.name, k.name
          FROM ruian_streets s
          JOIN ruian_admin_units o ON o.id = s.obec_unit_id
          LEFT JOIN ruian_admin_units k ON k.id = o.parent_id AND k.level = 'okres'
         WHERE s.code = %s AND s.valid_to IS NULL
         ORDER BY s.id DESC LIMIT 1
    """,
    "obec": """
        SELECT NULL, o.name, k.name
          FROM ruian_admin_units o
          LEFT JOIN ruian_admin_units k ON k.id = o.parent_id AND k.level = 'okres'
         WHERE o.level = 'obec' AND o.code = %s AND o.valid_to IS NULL
         ORDER BY o.id DESC LIMIT 1
    """,
}

_BATCH_SQL = """
    INSERT INTO location_claim_batches
        (lane, source, extractor_version, finished_at, row_count, outcome, note)
    VALUES ('operator_correction', NULL, %s, now(), %s, 'ok', %s)
"""

# The read-your-writes echo (05 5.5.5): the codes a pick binds and the ONE label (rule 25).
_PROJECTION_SQL = """
    SELECT granularity::text, ruian_adm_kod, ulice_kod, obec_kod,
           location_display_label(street_name, house_number_cp, house_number_co, obec_name,
                                  cast_obce_name, country_code, country_status) AS label,
           resolved_at
      FROM listing_location WHERE listing_id = %s
"""


def _register_values(conn: psycopg.Connection, level: str, code: int) -> dict[str, str]:
    if level not in _REGISTER_SQL:
        raise CorrectionError(f"level {level!r} is not pickable; pick an address, a street or an obec")
    with conn.cursor() as cur:
        cur.execute(_REGISTER_SQL[level], (code,))
        row = cur.fetchone()
    if row is None:
        raise CorrectionError(f"{level} {code} is not a current RÚIAN unit")
    if level == "adresni_misto":
        return {"address_point_id": str(code)}
    return {t: v for t, v in zip(PICK_TYPES[1:], row) if v is not None}


def _claims(ads: Sequence[tuple], values: dict[str, str]) -> list[Claim]:
    """The four picker types on every ad: a value where the pick states one, else a tombstone."""
    now = datetime.now(UTC)
    return [
        Claim(listing_id=ad[0], source=ad[1], source_id_native=ad[2], claim_type=t,
              surface="operator_input", page_kind="none", extraction_method="operator_manual",
              extractor_id=EXTRACTOR_ID, extractor_version=EXTRACTOR_VERSION,
              contract_entry_id=None, first_observed_at=now, blur_evidence="none",
              licence_class="operator", value_text=values.get(t), subject_scoped=True,
              claim_confidence="exact", retract=t not in values)
        for ad in ads for t in PICK_TYPES
    ]


def _write(cur: psycopg.Cursor, claims: list[Claim], note: str) -> tuple[int, int]:
    written, enqueued = write_claims(cur, claims, reason="operator_edit")
    cur.execute(_BATCH_SQL, (EXTRACTOR_VERSION, written, note))
    return written, enqueued


def submit_pick(
    conn: psycopg.Connection, *, property_id: int, pick: tuple[str, int] | None,
    by: str | None,
) -> dict[str, Any]:
    """Write the pick on every ad of the property (+ enqueue the changed ones). One transaction."""
    with conn.cursor() as cur:
        cur.execute(_ADS_SQL, (property_id,))
        ads = cur.fetchall()
    if not ads:
        raise UnknownPropertyError(f"property {property_id} is not an active property with ads")
    values = {} if pick is None else _register_values(conn, *pick)
    what = "cleared" if pick is None else f"{pick[0]} {pick[1]}"
    with conn.transaction():
        with conn.cursor() as cur:
            written, enqueued = _write(
                cur, _claims(ads, values), f"property {property_id}: {what} by {by}")
    return {
        "property_id": property_id,
        "pick": None if pick is None else {"level": pick[0], "code": pick[1]},
        "ads": [{"listing_id": a[0], "source": a[1], "location_ad": bool(a[3])} for a in ads],
        "written": written,
        "enqueued": enqueued,
    }


def clear(conn: psycopg.Connection, listing_ids: Sequence[int]) -> int:
    """Hand these ads back to their own words: four tombstones each (R1, the detach). Runs in
    the caller's transaction; writes no batch row when no ad held an operator claim."""
    if not listing_ids:
        return 0
    with conn.cursor() as cur:
        cur.execute(_LISTINGS_SQL, (list(listing_ids),))
        ads = cur.fetchall()
        written, _ = write_claims(cur, _claims(ads, {}), reason="operator_edit")
        if written:
            ids = ", ".join(str(a[0]) for a in ads)
            cur.execute(_BATCH_SQL, (EXTRACTOR_VERSION, written, f"listings {ids}: detached"))
    return written


def resolve_now(conn: psycopg.Connection, listing_id: int) -> bool:
    """Synchronous single-listing resolve (05 5.5.5). Best-effort: on failure the
    write's enqueue guarantees the drain converges."""
    try:
        from location_data.resolver import drain

        drain.run(conn, only_listing_id=listing_id, batch_size=1)
        return True
    except Exception:
        LOG.exception("synchronous resolve failed for listing %s; drain will pick it up",
                      listing_id)
        return False


def read_projection(conn: psycopg.Connection, listing_id: int) -> dict[str, Any] | None:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(_PROJECTION_SQL, (listing_id,))
        row = cur.fetchone()
    if row is not None and row.get("resolved_at") is not None:
        row["resolved_at"] = row["resolved_at"].isoformat()
    return row
