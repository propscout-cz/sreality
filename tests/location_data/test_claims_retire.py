"""W3's purge (`scripts/location_claims_retire.py`, its last run before migration 699; deleted
with this file in W3's PR-2): one claim per (listing, claim type, licence class), the keeper the
row today's resolver reads. Offline pins on the statement, then the executed walk over a
pre-699 table (CI's migrations lane, `TEST_DATABASE_URL`; every test rolls back)."""

from __future__ import annotations

import gzip
import os
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import psycopg
import pytest

from location_data import contracts, what_if
from location_data.resolver import resolve_db
from scripts import location_claims_retire as purge

_DB_URL = os.environ.get("TEST_DATABASE_URL")
_live = pytest.mark.skipif(
    not _DB_URL and os.environ.get("DB_RAILS_REQUIRED") != "1",
    reason="TEST_DATABASE_URL not set — schema-replay test runs only in the CI DB job")
T0 = datetime(2026, 9, 1, tzinfo=UTC)


def _flat(sql: str) -> str:
    return " ".join(sql.split())


def test_the_keeper_is_the_row_the_resolver_reads_today() -> None:
    assert ("PARTITION BY c.listing_id, c.claim_type, c.licence_class ORDER BY "
            "(pc.version <= act.version) DESC NULLS LAST, pc.version DESC NULLS FIRST, "
            "c.first_observed_at DESC, c.id DESC") in _flat(purge._BATCH_SQL)


def test_a_batch_is_a_listing_keyset_and_the_dry_run_runs_the_same_statement() -> None:
    flat = _flat(purge._BATCH_SQL)
    assert "SELECT id FROM listings WHERE id > %(after)s ORDER BY id LIMIT %(listings)s" in flat
    assert "WHERE c.id = r.id AND r.rn > 1 AND %(armed)s" in flat
    assert purge.DEFAULT_LISTINGS == 2_000 and purge.DEFAULT_PAUSE_SECONDS == 0.5
    assert purge._BATCH_TIMEOUTS_SQL == ("SET LOCAL lock_timeout = '5s'",
                                         "SET LOCAL statement_timeout = '120s'")


def test_the_safety_check_counts_keys_not_listings() -> None:
    flat = _flat(purge._KEYS_SQL)
    assert "SELECT DISTINCT listing_id, claim_type, licence_class" in flat
    assert "listing_id > %(after)s AND listing_id <= %(through)s" in flat


def test_nothing_here_enqueues_or_touches_another_table() -> None:
    for sql in (purge._BATCH_SQL, purge._KEYS_SQL):
        flat = _flat(sql).lower()
        assert "dirty_locations" not in flat
        assert flat.count("delete from") == ("delete from location_claims" in flat)


# ------------------------------------------------------------------ executed


@pytest.fixture()
def conn() -> Any:
    connection = psycopg.connect(_DB_URL, autocommit=True, options="-c statement_timeout=20000")
    with connection, connection.transaction(force_rollback=True):
        connection.execute("DROP INDEX IF EXISTS location_claims_key")   # the pre-699 table
        contracts.project(connection, next(
            c for c in contracts.load_all() if c.source == "bazos"), git_ref="ci")
        yield connection


def _header(conn: Any, version_delta: int) -> dict[str, int]:
    """{claim type: entry id} of a bazos header `version_delta` from the active one: a new,
    INACTIVE copy of the active header unless the delta is 0."""
    header = active = conn.execute("SELECT id FROM portal_contracts WHERE source = 'bazos'"
                                   " AND is_active").fetchone()[0]
    if version_delta:
        header = conn.execute(
            "INSERT INTO portal_contracts (source, version, contract_sha256, git_ref) SELECT"
            " source, version + %s, contract_sha256, 'ci' FROM portal_contracts WHERE id = %s"
            " RETURNING id", (version_delta, active)).fetchone()[0]
        conn.execute(
            "INSERT INTO portal_contract_entries (contract_id, entry_id, surface, page_kind,"
            " locator, claim_type, extraction_method) SELECT %s, entry_id, surface, page_kind,"
            " locator, claim_type, extraction_method FROM portal_contract_entries"
            " WHERE contract_id = %s", (header, active))
    return dict(conn.execute("SELECT claim_type::text, min(id) FROM portal_contract_entries"
                             " WHERE contract_id = %s GROUP BY 1", (header,)).fetchall())


def _listing(conn: Any) -> int:
    return int(conn.execute(
        "INSERT INTO listings (source, source_id_native, raw_json, is_active, last_seen_at)"
        " VALUES ('bazos', %s, '{}'::jsonb, true, now()) RETURNING id",
        (f"purge-{uuid.uuid4()}",)).fetchone()[0])


def _claim(conn: Any, listing: int, claim_type: str, value: str, entry: int | None,
           hours: int, licence: str = "portal") -> None:
    conn.execute(
        "INSERT INTO location_claims (listing_id, source, first_observed_at, claim_type, surface,"
        " extraction_method, contract_entry_id, value_text, licence_class) VALUES (%s, 'bazos',"
        " %s, %s::location_claim_type, 'html_selector', 'html_selector_parse', %s, %s,"
        " %s::licence_class)", (listing, T0 + timedelta(hours=hours), claim_type, entry, value,
                                licence))


def _left(conn: Any, listing: int) -> set[tuple[str, str, str]]:
    return set(conn.execute("SELECT claim_type::text, licence_class::text, value_text"
                            " FROM location_claims WHERE listing_id = %s", (listing,)).fetchall())


def _seed(conn: Any) -> tuple[int, int, dict[str, int]]:
    """L1 carries every case; L2 one claim, nothing to purge."""
    older, active, newer = _header(conn, -100), _header(conn, 0), _header(conn, 100)
    l1, l2 = _listing(conn), _listing(conn)
    _claim(conn, l1, "obec_name", "Brno", older["obec_name"], hours=9)           # newer sighting,
    _claim(conn, l1, "obec_name", "Brno - Brno-jih", active["obec_name"], 1)     # older version
    _claim(conn, l1, "psc", "60200", active["psc"], 2)                           # same version:
    _claim(conn, l1, "psc", "60300", active["psc"], 3)                           # read together
    _claim(conn, l1, "street_name", "Husova", newer["street_name"], 8)           # above active
    _claim(conn, l1, "street_name", "Kounicova", active["street_name"], 4)
    _claim(conn, l1, "cast_obce_name", "Žabovřesky", older["cast_obce_name"], 0)  # its only row
    _claim(conn, l1, "obec_name", "Brno", None, 5, licence="operator")
    _claim(conn, l1, "obec_name", "Brno-střed", None, 6, licence="operator")
    _claim(conn, l2, "psc", "60200", active["psc"], 1)
    return l1, l2, older


@_live
def test_the_purge_keeps_one_claim_per_key_and_names_the_collapsed_listings(
    conn: Any, tmp_path: Path,
) -> None:
    l1, l2, _ = _seed(conn)
    before = {listing: _left(conn, listing) for listing in (l1, l2)}

    dry = purge.Totals(cursor=min(l1, l2) - 1)
    assert purge.purge(conn, dry, armed=False, pause=0)
    assert (dry.deleted, dry.surplus, dry.collapsed) == (0, 4, [l1])
    assert {listing: _left(conn, listing) for listing in (l1, l2)} == before

    totals = purge.Totals(cursor=min(l1, l2) - 1)
    assert purge.purge(conn, totals, armed=True, pause=0, listings=1)
    assert (totals.deleted, totals.collapsed, totals.kept_retired) == (4, [l1], 1)
    assert totals.keys == 6
    assert _left(conn, l1) == {
        ("obec_name", "portal", "Brno - Brno-jih"),   # the active version beats a newer sighting
        ("psc", "portal", "60300"),                   # same version: the newest observation
        ("street_name", "portal", "Kounicova"),       # a version above the active one loses
        ("cast_obce_name", "portal", "Žabovřesky"),   # a retired version's only row is KEPT
        ("obec_name", "operator", "Brno-střed"),      # the operator's own slot
    }
    assert _left(conn, l2) == before[l2]

    receipt = tmp_path / "purge.csv.gz"
    purge.write_receipt(str(receipt), totals.collapsed)
    assert gzip.decompress(receipt.read_bytes()).decode() == f"listing_id\n{l1}\n"


@_live
def test_a_batch_that_would_lose_a_key_rolls_back_and_stops_the_run(
    conn: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    l1, l2, _ = _seed(conn)
    before = _left(conn, l1)
    monkeypatch.setattr(purge, "_KEYS_SQL",
                        "SELECT 0 * %(after)s + 0 * %(through)s")   # every key "missing"
    with pytest.raises(purge.KeyLost):
        purge.purge(conn, purge.Totals(cursor=min(l1, l2) - 1), armed=True, pause=0)
    assert _left(conn, l1) == before


@_live
def test_the_wave_shadows_purge_reads_what_the_purge_leaves(conn: Any) -> None:
    """`what_if --claims purge` (W3's shadow) spells the keeper in Python over a raw read; on the
    same table it must hand the resolver exactly what the one read returns after the purge."""
    l1, l2, _ = _seed(conn)
    shadow = what_if.purge(conn, {l1: [], l2: []})
    purge.purge(conn, purge.Totals(cursor=min(l1, l2) - 1), armed=True, pause=0)
    after = {listing: resolve_db.load_claims(conn, listing) for listing in (l1, l2)}
    assert {k: [c.id for c in v] for k, v in shadow.items()} == {
        k: [c.id for c in v] for k, v in after.items()}
    assert len(shadow[l1]) == 4   # street, the newest psc, cast obce; the operator outranks the town
