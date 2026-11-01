"""Location W1v: the OPERATOR claim producer (03 S7 rank 1, 05 5.5.5).

One of the two claim producers (the contract-driven claim lane, over its three substrates,
and operator input). A correction is the listing's ONE operator claim of its type
(`surface='operator_input'`, `extraction_method='operator_manual'`, `licence_class='operator'`,
`claim_confidence='exact'`), written through the one write, `claims_store.write_claims` (W3):
a new value replaces the operator's previous one, the same value twice writes and enqueues
nothing, and the resolver's read puts it ahead of the portal's claim of that type. No retract
or re-mine reaches it: both act only on claims that carry a `contract_entry_id`.

The synchronous projection refresh (05 5.5.5: operator-initiated changes are visible on the
next read) is `resolve_now()` = `drain.run(only_listing_id=)`; `submit_correction` callers
invoke it after the claim transaction commits. If it fails, the enqueue in the write
guarantees the drain converges.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

import psycopg
from psycopg.rows import dict_row

from location_data.claims_common import Claim
from location_data.claims_store import write_claims

LOG = logging.getLogger("location.operator_corrections")

EXTRACTOR_ID = "operator.correction"
EXTRACTOR_VERSION = "1"

# Text-valued claim types an operator can assert in W1v. `coordinate` (a pin
# drag) is deliberately absent: it needs a geometry input surface and its own
# licence reasoning; the policy row for it already exists (migration 400), so
# adding it later is UI work, not schema work.
ALLOWED_CLAIM_TYPES: frozenset[str] = frozenset({
    "address_point_id",
    "street_name",
    "house_number_cp",
    "house_number_co",
    "psc",
    "obec_name",
    "cast_obce_name",
    "okres_name",
})


class CorrectionError(ValueError):
    """Invalid correction input (maps to HTTP 422 at the API layer)."""


class UnknownListingError(CorrectionError):
    pass


class UnknownKodAdmError(CorrectionError):
    pass


_KOD_ADM_SQL = """
    SELECT ap.kod_adm,
           u_obec.name AS obec,
           s.name      AS street,
           ap.cislo_domovni::text AS cislo_domovni,
           ap.cislo_orientacni    AS cislo_orientacni,
           ap.psc::text           AS psc
    FROM ruian_address_points ap
    LEFT JOIN ruian_streets s
           ON s.id = ap.street_id
    LEFT JOIN ruian_admin_units u_obec
           ON u_obec.id = ap.obec_unit_id
    WHERE ap.kod_adm = %s AND ap.valid_to IS NULL
"""

# The read-your-writes echo (05 5.5.5), off `listing_location` since W2-b. The
# axes it names are the answer table's own: no display label (derived at read),
# no position_source / admin_assignment_method (the four-step resolver emits
# neither), and `disputed` is the reason word itself, NULL when clean.
_PROJECTION_SQL = """
    SELECT listing_id, granularity::text, match_confidence::text,
           uncertainty_radius_m, ruian_adm_kod, street_name,
           house_number_cp, house_number_co, psc, obec_name, okres_name,
           disputed, resolver_version, resolved_at
    FROM listing_location WHERE listing_id = %s
"""


def submit_correction(
    conn: psycopg.Connection,
    *,
    listing_id: int,
    claim_type: str,
    value_text: str,
    note: str | None = None,
) -> dict[str, Any]:
    """Write the listing's operator claim of this type (+ enqueue on change). One transaction."""
    if claim_type not in ALLOWED_CLAIM_TYPES:
        raise CorrectionError(
            f"claim_type {claim_type!r} not correctable; allowed: "
            + ", ".join(sorted(ALLOWED_CLAIM_TYPES))
        )
    value_text = (value_text or "").strip()
    if not value_text:
        raise CorrectionError("value_text must be non-empty")

    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT id, source, source_id_native FROM listings WHERE id = %s",
            (listing_id,),
        )
        listing = cur.fetchone()
    if listing is None:
        raise UnknownListingError(f"listing {listing_id} does not exist")

    registry_echo: dict[str, Any] | None = None
    if claim_type == "address_point_id":
        if not value_text.isdigit():
            raise CorrectionError("address_point_id must be a bare kod ADM (digits only)")
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(_KOD_ADM_SQL, (int(value_text),))
            registry_echo = cur.fetchone()
        if registry_echo is None:
            raise UnknownKodAdmError(
                f"kod ADM {value_text} is not a current address point in the RUIAN mirror"
            )

    claim = Claim(
        listing_id=listing_id, source=listing["source"],
        source_id_native=listing["source_id_native"], claim_type=claim_type,
        surface="operator_input", page_kind="none", extraction_method="operator_manual",
        extractor_id=EXTRACTOR_ID, extractor_version=EXTRACTOR_VERSION, contract_entry_id=None,
        first_observed_at=datetime.now(UTC), blur_evidence="none", licence_class="operator",
        value_text=value_text, subject_scoped=True, claim_confidence="exact")
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO location_claim_batches
                    (lane, source, extractor_version, finished_at, row_count, outcome, note)
                VALUES ('operator_correction', %s, %s, now(), 1, 'ok', %s)
                """,
                (listing["source"], EXTRACTOR_VERSION, note),
            )
            written, enqueued = write_claims(cur, [claim], reason="operator_edit")

    return {
        "listing_id": listing_id,
        "source": listing["source"],
        "claim_type": claim_type,
        "value_text": value_text,
        "written": written == 1,
        "enqueued": enqueued == 1,
        "registry_echo": registry_echo,
        "resolved": False,
        "projection": None,
    }


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
