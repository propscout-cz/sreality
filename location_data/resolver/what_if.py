"""A location wave's movers, computed without a write: step 1 of the wave shadow.

    python -m location_data.resolver.what_if --ids ids.txt --out shadow/w4 \\
        --claims remine --contract shadow/w4/realitymix@8.yaml

Re-resolves an id list with this checkout's resolver over `drain.recompute` on a READ ONLY
connection, after the wave's claim change (`none` W5/W5b/W6, `purge` W3, `remine` W4). A mover's row
differs from today's `COHORT_LOCATION_SQL` row; `positions.jsonl` holds its new row in that read's
columns, `movers.jsonl` old beside new. Design: `docs/design/location-quality/wave-shadow.md`.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import Counter
from collections.abc import Sequence
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from typing import Any

import psycopg

from autodedup.export_sql import COHORT_LOCATION_SQL
from location_data import claims_intake, contracts, page_readers, payloads
from location_data.claims_common import (
    DEFAULT_MAX_CLAIM_VALUE_BYTES,
    MAX_CLAIM_VALUE_BYTES_ENV,
    IntakeResult,
    env_positive_int,
)
from location_data.claims_common import Claim as IntakeClaim
from location_data.resolver import drain, projection
from location_data.resolver.types import Claim, Resolution
from scraper import db

LOG = logging.getLogger("location_data.resolver.what_if")

POSITIONS_FILE = "positions.jsonl"
MOVERS_FILE = "movers.jsonl"
# A re-mined claim is inserted after every stored one, and the resolver orders claims by id.
NEW_CLAIM_ID = 10**12
# The store keeps the radius as `numeric`, which rounds a float to 15 digits on the way in.
TOLERANCE = 1e-6

_RANK_SQL = "SELECT granularity::text, rank, is_address_grain FROM location_granularity_rank"


def purge(conn: psycopg.Connection, claims: dict[int, list[Claim]]) -> dict[int, list[Claim]]:
    """W3: one claim per (listing, type, author class), the newest observation of today's read."""
    out: dict[int, list[Claim]] = {}
    for listing_id, rows in claims.items():
        keep: dict[tuple[str, str], Claim] = {}
        for claim in rows:
            key = (claim.claim_type, claim.licence_class)
            if key not in keep or (claim.observed_at, claim.id) > (keep[key].observed_at,
                                                                   keep[key].id):
                keep[key] = claim
        out[listing_id] = sorted(keep.values(), key=lambda claim: claim.id)
    return out


def restate(today: list[Claim], mined: list[Claim], source: str,
            declared: frozenset[str]) -> list[Claim]:
    """Today's claims once a bump of `source`'s contract mined `mined`, as `_claims_sql` reads."""
    # Per claim type the newest version wins: the re-mine's where it stated the type, today's
    # otherwise. A type the new contract no longer declares is not read; operator claims and
    # another portal's are untouched.
    stated = {claim.claim_type for claim in mined}
    kept = [claim for claim in today
            if claim.licence_class == "operator" or claim.source != source
            or (claim.claim_type in declared and claim.claim_type not in stated)]
    return kept + mined


def as_resolver_claim(claim: IntakeClaim, claim_id: int) -> Claim:
    """An intake claim as `location_claims` hands it back to the resolver (`resolve_db._claim`)."""
    lat = lon = None
    if claim.value_geom_wkt:
        lon, lat = (float(v) for v in claim.value_geom_wkt.removeprefix("POINT(")
                    .removesuffix(")").split())
    return Claim(
        id=claim_id, listing_id=claim.listing_id, source=claim.source,
        claim_type=claim.claim_type, surface=claim.surface,
        extraction_method=claim.extraction_method, licence_class=claim.licence_class,
        observed_at=claim.first_observed_at, value_text=claim.value_text,
        value_num=None if claim.value_num is None else float(claim.value_num),
        lat=lat, lon=lon, value_jsonb=claim.value_jsonb or {},
        declared_precision_label=claim.declared_precision_label,
        declared_radius_m=None if claim.declared_radius_m is None
        else float(claim.declared_radius_m),
        blur_evidence=claim.blur_evidence, subject_scoped=claim.subject_scoped)


def remine(contract: contracts.PortalContract, store: page_readers.BodyStore
           ) -> drain.ClaimChange:
    """W4: the contract's portal re-mined under it, payload and latest stored page alike."""
    entries = contracts.entries_of(contract)
    registers = {contract.source: page_readers.ScopeRegister.from_zones(
        contract.source, contract.exclusion_zones)}
    declared = frozenset(entry.claim_type for entry in entries)
    limit = env_positive_int(MAX_CLAIM_VALUE_BYTES_ENV, DEFAULT_MAX_CLAIM_VALUE_BYTES)

    def change(conn: psycopg.Connection, claims: dict[int, list[Claim]]
               ) -> dict[int, list[Claim]]:
        with conn.transaction(), conn.cursor() as cur:
            scans = [scan for scan in claims_intake.scan_rows(cur, list(claims))
                     if scan.row.source == contract.source]
            bodies, _ = page_readers.load_bodies(
                cur, [scan.body.id for scan in scans if scan.body is not None], store=store)
        pages = [(scan.row, replace(scan.body, body=bodies[scan.body.id])) for scan in scans
                 if scan.body is not None and scan.body.id in bodies]
        outcomes = dict(zip((row.listing_id for row, _ in pages), page_readers.extract_pages(
            pages, entries_by_source={contract.source: entries}, registers=registers,
            max_value_bytes=limit)))
        out = dict(claims)
        for scan in scans:
            mined = claims_intake.extract_listing(scan.row, entries).claims
            outcome = outcomes.get(scan.row.listing_id)
            if isinstance(outcome, IntakeResult):
                mined += outcome.claims
            elif outcome is not None:
                LOG.warning("WHAT-IF page not re-mined listing_id=%d: %s",
                            scan.row.listing_id, outcome)
            new = [as_resolver_claim(claim, NEW_CLAIM_ID + n) for n, claim in enumerate(mined)]
            out[scan.row.listing_id] = restate(claims[scan.row.listing_id], new,
                                               contract.source, declared)
        LOG.info("WHAT-IF re-mined %d listings, %d with a stored page", len(scans), len(pages))
        return out

    return change


def position_row(resolution: Resolution, ranks: dict[str, tuple[int, bool]]) -> dict[str, Any]:
    """The resolution as the answer row, plus the rank the cohort read joins onto it."""
    row = projection.build_listing_row(resolution)
    row["granularity_rank"], row["is_address_grain"] = ranks.get(row["granularity"],
                                                                 (None, None))
    return row


def moved(old: dict[str, Any], new: dict[str, Any]) -> bool:
    """Does any field of today's cohort read differ in the new row?"""
    for key, before in old.items():
        after = new.get(key)
        if isinstance(before, (int, float, Decimal)) and isinstance(after, (int, float)):
            if abs(float(before) - float(after)) > TOLERANCE:
                return True
        elif before != after:
            return True
    return False


def _stored(conn: psycopg.Connection, listing_ids: list[int]
            ) -> tuple[list[str], dict[int, dict[str, Any]]]:
    """Today's rows as the cohort export reads them, and that read's column names."""
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(COHORT_LOCATION_SQL, {"ids": listing_ids})
        names = [column.name for column in cur.description or ()]
        return names, {int(row[0]): dict(zip(names, row)) for row in cur.fetchall()}


def _line(record: dict[str, Any]) -> str:
    return json.dumps(record, ensure_ascii=False, sort_keys=True,
                      default=lambda v: float(v) if isinstance(v, Decimal) else str(v)) + "\n"


def run(conn: psycopg.Connection, listing_ids: Sequence[int], out_dir: Path, *,
        claim_change: drain.ClaimChange | None = None, batch_size: int = drain.DEFAULT_BATCH,
        ) -> dict[str, Counter[str]]:
    """Positions and movers for `listing_ids`; returns the dry run's tally per portal."""
    out_dir.mkdir(parents=True, exist_ok=True)
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(_RANK_SQL)
        ranks = {str(g): (int(rank), bool(grain)) for g, rank, grain in cur.fetchall()}
    tally: dict[str, Counter[str]] = {"*": Counter(of=len(listing_ids))}
    with (out_dir / POSITIONS_FILE).open("w", encoding="utf-8") as positions, \
            (out_dir / MOVERS_FILE).open("w", encoding="utf-8") as movers:
        for page, new in drain.recompute(conn, listing_ids, batch_size=batch_size,
                                         claim_change=claim_change):
            if isinstance(new, Exception):
                tally["*"]["failed"] += len(page)
                LOG.warning("WHAT-IF page from listing %d failed: %s", page[0], new)
                continue
            names, stored = _stored(conn, page)
            for item in new:
                full, old = position_row(item, ranks), stored.get(item.listing_id)
                row = {name: full.get(name) for name in names}
                counts = tally.setdefault(item.source, Counter())
                drain._tally(counts, (item.listing_id, *((old or {}).get(k) for k in (
                    "obec_kod", "cast_obce_kod", "house_number_cp", "granularity", "lat", "lon"))),
                    item)
                if old is not None and not moved(old, row):
                    continue
                counts["movers"] += 1
                positions.write(_line(row))
                movers.write(_line({"listing_id": item.listing_id, "source": item.source,
                                    "old": old, "new": row}))
    for source, counts in sorted(tally.items()):
        LOG.info("WHAT-IF source=%s %s", source, " ".join(f"{k}={v}" for k, v in counts.items()))
    return tally


def select_ids(conn: psycopg.Connection, sql: str) -> list[int]:
    """The listing ids an operator's SELECT names (first column), as one read-only statement."""
    # A subquery admits no second statement and no data-modifying CTE, and the extended protocol
    # (`binary=True`) refuses a multi-statement string, so a `COMMIT; ...` cannot leave the
    # READ ONLY transaction (the simple protocol would run it).
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(f"SELECT * FROM (\n{sql.strip().rstrip(';')}\n) AS shadow_ids", binary=True)
        return [int(row[0]) for row in cur.fetchall()]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="a location wave's movers, without a write")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--ids", help="a file of listing ids, whitespace-separated")
    source.add_argument("--ids-sql", help="a SELECT whose first column is the listing id")
    parser.add_argument("--limit", type=int, default=0, help="the lowest N ids only (0 = all)")
    parser.add_argument("--out", required=True, help="directory for positions.jsonl, movers.jsonl")
    parser.add_argument("--claims", choices=("none", "purge", "remine"), default="none")
    parser.add_argument("--contract", default=None, help="remine: the wave's contract file")
    parser.add_argument("--batch-size", type=int, default=drain.DEFAULT_BATCH)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    change: drain.ClaimChange | None = purge if args.claims == "purge" else None
    if args.claims == "remine":
        if not args.contract:
            parser.error("--claims remine needs --contract <the wave's contract file>")
        store = payloads.open_store()
        if store is None:
            raise SystemExit("--claims remine reads the stored bodies from R2: set R2_*")
        change = remine(contracts.parse_contract(Path(args.contract)), store)
    with db.connect_read_only() as conn:
        ids = sorted(set(select_ids(conn, args.ids_sql) if args.ids_sql is not None else
                         (int(t) for t in Path(args.ids).read_text(encoding="utf-8").split())))
        ids = ids[:args.limit] if args.limit > 0 else ids
        LOG.info("WHAT-IF %d listing ids", len(ids))
        tally = run(conn, ids, Path(args.out), claim_change=change, batch_size=args.batch_size)
    # A shadow over part of its ids is not a shadow: the operator re-runs, never judges it.
    return 1 if tally["*"]["failed"] else 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
