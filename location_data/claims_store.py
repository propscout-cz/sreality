"""THE one write of `location_claims` (location W3), shared by both producers.

`claims_intake` (the lane: payload, page body, reading) and `operator_corrections` write every
claim through `write_claims`; nothing else INSERTs, UPDATEs or DELETEs a claim but
`contracts.retract`. A small module of its own so the operator's API route does not import the
intake lane.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Sequence
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from location_data.claims_common import Claim, env_positive_int

# THE SCAN BATCH IS NOT THE WRITE SIZE.
#
# Every claim write passes ONE jsonb array as ONE parameter to
# `jsonb_to_recordset`, and Postgres caps the total size of a jsonb array's elements at
# 256 MB: `total size of jsonb array elements exceeds the maximum of 268435455 bytes`
# (ProgramLimitExceeded). A 20 000-listing batch crossed it in production (Actions run
# 31482522487, the hourly incremental) — the incremental scan orders by `last_seen_at`, not
# `id`, which concentrated the geometry-heavy sreality rows into one batch where the earlier
# id-ordered full pass had diluted them across nine portals. The cap is a property of the
# WRITE, not of the scan, so shrinking the batch would only move the cliff: the arrays are
# flushed in chunks bounded by BOTH a row count and a cumulative serialized-byte budget,
# whichever trips first, all inside the same batch transaction as before.
WRITE_CHUNK_ROWS_ENV = "LOCATION_INTAKE_CHUNK_ROWS"
WRITE_CHUNK_BYTES_ENV = "LOCATION_INTAKE_CHUNK_BYTES"
DEFAULT_WRITE_CHUNK_ROWS = 5_000
DEFAULT_WRITE_CHUNK_BYTES = 32 * 1024 * 1024  # ~8x under the hard limit, per statement.

# THE ONE WRITE (location W3, migration 699). For each listing, claim type and author
# (`licence_class`: the portal or the operator) the store holds ONE claim: the value that author
# stated most recently. Both producers, this lane and `operator_corrections`, run this statement.
#  * It writes only on CHANGE: an unchanged re-mine, and every contract bump that restates the
#    same value, creates no row version and enqueues nothing. A changed value is an UPDATE in
#    place (the claim keeps its id; `claim_set_hash` hashes value + `first_observed_at`, so the
#    listing re-resolves). A -> B -> A writes twice and ends on A.
#  * Provenance is the entry that FIRST stated the current value: `contract_entry_id`, `surface`,
#    `extraction_method` and `first_observed_at` move only with the value. So it is provenance,
#    not admissibility (the read has no version filter), and `contracts.retract <portal>@N`
#    deletes every value N first stated, re-confirmed later or not: a retract, like an
#    activation, is followed by one full re-mine of that source.
#  * Silence keeps the value (ruling Q4 = A): a reader that emits nothing for a type writes
#    nothing. A value goes only when the same author states another one, when the operator
#    outranks it (at read), by `contracts.retract`, or by a TOMBSTONE (`Claim.retract`: a marker
#    whose page states the fact's absence, `gone` below).
#  * The enqueue BUMPS, it does not skip (W2-a2: `DO NOTHING` once let the drain resolve 60 k
#    re-mined listings from their OLD claims), and it is in the same statement as the write
#    (the only coupling between intake and resolution).
_WRITE_CLAIMS_SQL = """
    WITH input AS (
        SELECT * FROM jsonb_to_recordset(%(rows)s::jsonb) AS x(
            listing_id bigint, source text, first_observed_at timestamptz,
            claim_type text, surface text, extraction_method text, contract_entry_id bigint,
            value_text text, value_num numeric, value_geom_wkt text, value_jsonb jsonb,
            declared_precision_label text, declared_radius_m numeric, claim_confidence text,
            blur_evidence text, licence_class text, subject_scoped boolean, retract boolean)
    ), one AS (
        SELECT DISTINCT ON (listing_id, claim_type, licence_class) * FROM input
         ORDER BY listing_id, claim_type, licence_class, first_observed_at DESC
    ), up AS (
        INSERT INTO location_claims AS c (
            listing_id, source, first_observed_at, claim_type, surface,
            extraction_method, contract_entry_id, value_text, value_num, value_geom,
            value_jsonb, declared_precision_label, declared_radius_m, claim_confidence,
            blur_evidence, licence_class, subject_scoped)
        SELECT o.listing_id, o.source, o.first_observed_at,
               o.claim_type::location_claim_type, o.surface::location_claim_surface,
               o.extraction_method::location_extraction_method, o.contract_entry_id,
               o.value_text, o.value_num,
               CASE WHEN o.value_geom_wkt IS NULL THEN NULL
                    ELSE ST_GeomFromText(o.value_geom_wkt, 4326) END,
               o.value_jsonb, o.declared_precision_label, o.declared_radius_m,
               o.claim_confidence::match_confidence, o.blur_evidence::blur_evidence,
               o.licence_class::licence_class, o.subject_scoped
          FROM one o
         WHERE NOT coalesce(o.retract, false)
        ON CONFLICT (listing_id, claim_type, licence_class) DO UPDATE SET
               source = EXCLUDED.source, first_observed_at = EXCLUDED.first_observed_at,
               surface = EXCLUDED.surface, extraction_method = EXCLUDED.extraction_method,
               contract_entry_id = EXCLUDED.contract_entry_id,
               value_text = EXCLUDED.value_text, value_num = EXCLUDED.value_num,
               value_geom = EXCLUDED.value_geom, value_jsonb = EXCLUDED.value_jsonb,
               declared_precision_label = EXCLUDED.declared_precision_label,
               declared_radius_m = EXCLUDED.declared_radius_m,
               claim_confidence = EXCLUDED.claim_confidence,
               blur_evidence = EXCLUDED.blur_evidence, subject_scoped = EXCLUDED.subject_scoped
         WHERE (c.value_text, c.value_num, c.value_geom, c.value_jsonb,
                c.declared_precision_label, c.declared_radius_m, c.blur_evidence,
                c.subject_scoped)
               IS DISTINCT FROM
               (EXCLUDED.value_text, EXCLUDED.value_num, EXCLUDED.value_geom,
                EXCLUDED.value_jsonb, EXCLUDED.declared_precision_label,
                EXCLUDED.declared_radius_m, EXCLUDED.blur_evidence, EXCLUDED.subject_scoped)
        RETURNING c.listing_id
    ), gone AS (
        DELETE FROM location_claims c
         USING one o
         WHERE o.retract AND c.listing_id = o.listing_id
           AND c.claim_type = o.claim_type::location_claim_type
           AND c.licence_class = o.licence_class::licence_class
        RETURNING c.listing_id
    ), enqueued AS (
        INSERT INTO dirty_locations (listing_id, reason)
        SELECT DISTINCT listing_id, %(reason)s::text
          FROM (SELECT listing_id FROM up UNION ALL SELECT listing_id FROM gone) changed
        ON CONFLICT (listing_id) DO UPDATE
           SET enqueued_at = now(), reason = EXCLUDED.reason,
               attempts = 0, next_eligible_at = now()
        RETURNING listing_id
    )
    SELECT (SELECT count(*) FROM up) + (SELECT count(*) FROM gone),
           (SELECT count(*) FROM enqueued)
"""

def chunk_rows(
    rows: list[dict[str, Any]], *, max_rows: int, max_bytes: int,
) -> Iterator[list[dict[str, Any]]]:
    """Split one jsonb array into statement-sized arrays, WITHOUT splitting a listing.

    Two bounds, whichever trips first — a row count (cheap, predictable) and a cumulative
    serialized-byte budget (the one that actually matters, because a batch's row count says
    nothing about its bytes).

    The listing boundary is what makes chunking SEMANTICS-PRESERVING, not merely smaller:
    the claim key leads with `listing_id`, so one key never spans two statements and the
    write's `DISTINCT ON` still arbitrates it.

    A group that exceeds `max_bytes` on its own is still emitted (a budget cannot split an
    array element) — that case is what `_refuse_oversized` exists to keep out of reach.
    """
    chunk: list[dict[str, Any]] = []
    size = 0
    for index, row in enumerate(rows):
        row_bytes = len(json.dumps(row, ensure_ascii=False, default=str).encode("utf-8"))
        starts_group = index == 0 or row["listing_id"] != rows[index - 1]["listing_id"]
        if chunk and starts_group and (len(chunk) >= max_rows or size + row_bytes > max_bytes):
            yield chunk
            chunk, size = [], 0
        chunk.append(row)
        size += row_bytes
    if chunk:
        yield chunk


def write_claims(
    cur: psycopg.Cursor, claims: Sequence[Claim], reason: str = "claim_insert",
) -> tuple[int, int]:
    """THE one write of `location_claims` (W3), both producers' -> (claims changed, enqueued).

    Inside the caller's transaction: every chunk is flushed in it, so a batch stays
    all-or-nothing. `reason` is the `dirty_locations` reason of the listings it changed.
    """
    max_rows = env_positive_int(WRITE_CHUNK_ROWS_ENV, DEFAULT_WRITE_CHUNK_ROWS)
    max_bytes = env_positive_int(WRITE_CHUNK_BYTES_ENV, DEFAULT_WRITE_CHUNK_BYTES)
    changed = enqueued = 0
    rows = sorted((c.to_row() for c in claims), key=lambda r: r["listing_id"])
    for chunk in chunk_rows(rows, max_rows=max_rows, max_bytes=max_bytes):
        cur.execute(_WRITE_CLAIMS_SQL, {"rows": Jsonb(chunk), "reason": reason})
        chunk_changed, chunk_enqueued = (int(x) for x in cur.fetchone())
        changed += chunk_changed
        enqueued += chunk_enqueued
    return changed, enqueued
