"""Location W3, EXECUTED against the replayed schema (CI's migrations lane, `TEST_DATABASE_URL`;
locally `scripts/local_pg.sh test`): one claim per listing, claim type and author, written only
when its value changed, read one per type with the operator's first. Every test rolls back.
"""

from __future__ import annotations

import os
import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg
import pytest

from location_data import claims_intake as ci
from location_data import contracts
from location_data.resolver import resolve_db

_DB_URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not _DB_URL and os.environ.get("DB_RAILS_REQUIRED") != "1",
    reason="TEST_DATABASE_URL not set — schema-replay test runs only in the CI DB job")

T0 = datetime(2026, 10, 1, tzinfo=UTC)


@pytest.fixture()
def conn() -> Any:
    connection = psycopg.connect(_DB_URL, autocommit=True, options="-c statement_timeout=20000")
    with connection, connection.transaction(force_rollback=True):
        contracts.project(connection, next(
            c for c in contracts.load_all() if c.source == "bazos"), git_ref="ci")
        yield connection


def _listing(conn: Any) -> int:
    return int(conn.execute(
        "INSERT INTO listings (source, source_id_native, raw_json, category_main, category_type,"
        " price_czk, area_m2, is_active, last_seen_at) VALUES ('bazos', %s, '{}'::jsonb, 'byt',"
        " 'prodej', 5000000, 60, true, now()) RETURNING id", (f"w3-{uuid.uuid4()}",)).fetchone()[0])


def _claim(conn: Any, listing: int, entry_id: str, at: datetime = T0, **values: Any) -> ci.Claim:
    entry = {e.entry_id: e for e in ci.load_entries(conn)["bazos"]}[entry_id]
    return ci._base(entry, ci.ListingRow(listing, "bazos", "n", {}, at), **values)


def _operator(claim: ci.Claim, value: str, at: datetime = T0) -> ci.Claim:
    return replace(claim, value_text=value, first_observed_at=at, contract_entry_id=None,
                   licence_class="operator", surface="operator_input",
                   extraction_method="operator_manual", claim_confidence="exact")


def _write(conn: Any, *claims: ci.Claim, reason: str = "claim_insert") -> tuple[int, int]:
    with conn.cursor() as cur:
        return ci.write_claims(cur, list(claims), reason)


def _stored(conn: Any, listing: int) -> dict[tuple[str, str], tuple[Any, ...]]:
    rows = conn.execute(
        "SELECT claim_type::text, licence_class::text, id, value_text, contract_entry_id,"
        " first_observed_at FROM location_claims WHERE listing_id = %s", (listing,)).fetchall()
    return {(t, licence): tuple(rest) for t, licence, *rest in rows}


def _queued(conn: Any, listing: int) -> bool:
    """Whether the listing is queued, and the queue cleared for the next step."""
    return conn.execute("DELETE FROM dirty_locations WHERE listing_id = %s RETURNING 1",
                        (listing,)).fetchone() is not None


def _read(conn: Any, listing: int) -> dict[str, tuple[str, str | None]]:
    return {c.claim_type: (c.licence_class, c.value_text)
            for c in resolve_db.load_claims(conn, listing)}


def test_a_restated_value_writes_nothing_and_a_changed_one_updates_in_place(conn: Any) -> None:
    listing = _listing(conn)
    assert _write(conn, _claim(conn, listing, "bzs.det.psc", value_text="50011")) == (1, 1)
    assert _queued(conn, listing)
    row_id, _, entry, _ = first = _stored(conn, listing)[("psc", "portal")]

    # A bump restating the same value under another entry: no row version, no enqueue, and
    # the provenance stays the entry that FIRST stated it.
    other = _claim(conn, listing, "bzs.txt.street").contract_entry_id
    restated = replace(_claim(conn, listing, "bzs.det.psc", T0 + timedelta(days=1),
                              value_text="50011"), contract_entry_id=other)
    assert _write(conn, restated) == (0, 0)
    assert _stored(conn, listing)[("psc", "portal")] == first and not _queued(conn, listing)

    changed = _claim(conn, listing, "bzs.det.psc", T0 + timedelta(days=2), value_text="50012")
    assert _write(conn, changed) == (1, 1) and _queued(conn, listing)
    assert _stored(conn, listing)[("psc", "portal")] == (
        row_id, "50012", entry, T0 + timedelta(days=2))

    back = _claim(conn, listing, "bzs.det.psc", T0 + timedelta(days=3), value_text="50011")
    assert _write(conn, back) == (1, 1)                        # A -> B -> A ends on A
    assert _stored(conn, listing)[("psc", "portal")][:2] == (row_id, "50011")


def test_two_claims_of_one_key_in_one_batch_keep_the_later_observation(conn: Any) -> None:
    listing = _listing(conn)
    early = _claim(conn, listing, "bzs.txt.street", T0, value_text="Husova")
    late = _claim(conn, listing, "bzs.txt.street", T0 + timedelta(hours=1), value_text="Palackého")
    assert _write(conn, late, early) == (1, 1)
    assert _stored(conn, listing)[("street_name", "portal")][1] == "Palackého"


def test_the_operator_outranks_the_portal_which_still_updates_beneath(conn: Any) -> None:
    listing = _listing(conn)
    portal = _claim(conn, listing, "bzs.txt.street", value_text="Husova")
    _write(conn, portal)
    assert _write(conn, _operator(portal, "Palackého"), reason="operator_edit") == (1, 1)
    assert conn.execute("SELECT reason FROM dirty_locations WHERE listing_id = %s",
                        (listing,)).fetchone() == ("operator_edit",)
    assert _read(conn, listing)["street_name"] == ("operator", "Palackého")

    assert _write(conn, replace(portal, value_text="Dlouhá",
                                first_observed_at=T0 + timedelta(days=1))) == (1, 1)
    assert _read(conn, listing)["street_name"] == ("operator", "Palackého")
    assert _stored(conn, listing)[("street_name", "portal")][1] == "Dlouhá"

    # The operator restating their own value writes nothing; A -> B -> A writes each time.
    assert _write(conn, _operator(portal, "Palackého"), reason="operator_edit") == (0, 0)
    for value in ("Husova", "Palackého"):
        assert _write(conn, _operator(portal, value), reason="operator_edit") == (1, 1)

    conn.execute("DELETE FROM location_claims WHERE listing_id = %s"
                 " AND licence_class = 'operator'", (listing,))
    assert _read(conn, listing)["street_name"] == ("portal", "Dlouhá")


def test_a_tombstone_deletes_its_key_alone_and_enqueues(conn: Any) -> None:
    listing = _listing(conn)
    blur = _claim(conn, listing, "bzs.det.blur_hint", value_text="approximate_location")
    street = _claim(conn, listing, "bzs.txt.street", value_text="Husova")
    _write(conn, blur, street, _operator(street, "Palackého"))
    _queued(conn, listing)

    tombstone = replace(blur, value_text=None, declared_precision_label=None, retract=True)
    assert _write(conn, tombstone) == (1, 1) and _queued(conn, listing)
    assert set(_stored(conn, listing)) == {("street_name", "portal"), ("street_name", "operator")}
    assert _write(conn, tombstone) == (0, 0)                   # nothing left to retract

    # A portal tombstone never reaches the operator's claim of its type.
    assert _write(conn, replace(street, value_text=None, retract=True)) == (1, 1)
    assert set(_stored(conn, listing)) == {("street_name", "operator")}


def test_a_retract_deletes_what_its_version_first_stated_even_when_restated(conn: Any) -> None:
    """Ruling R4: provenance is the FIRST stater, so a retract also takes the values a later
    version restated unchanged; the source is then re-mined in full."""
    listing = _listing(conn)
    first = _claim(conn, listing, "bzs.det.psc", value_text="50011")
    _write(conn, first)
    restated = replace(first, contract_entry_id=_claim(
        conn, listing, "bzs.txt.street").contract_entry_id, first_observed_at=T0 + timedelta(1))
    assert _write(conn, restated) == (0, 0)
    _queued(conn, listing)
    deleted, enqueued = conn.execute(contracts._RETRACT_BATCH_SQL, {
        "entry_ids": [first.contract_entry_id], "batch_size": 100}).fetchone()
    assert (deleted, enqueued) == (1, 1) and not _stored(conn, listing)


def test_a_claim_whose_version_is_not_active_stays_readable(conn: Any) -> None:
    """No version filter: activating another version strands nothing. The read asks only
    whether the ACTIVE contract still declares the type."""
    listing = _listing(conn)
    _write(conn, _claim(conn, listing, "bzs.txt.street", value_text="Husova"))
    current = conn.execute("SELECT id FROM portal_contracts WHERE source = 'bazos'"
                           " AND is_active").fetchone()[0]
    other = conn.execute(
        "INSERT INTO portal_contracts (source, version, contract_sha256, git_ref)"
        " SELECT source, version + 1, contract_sha256, 'ci-other' FROM portal_contracts"
        " WHERE id = %s RETURNING id", (current,)).fetchone()[0]
    conn.execute(
        "INSERT INTO portal_contract_entries (contract_id, entry_id, surface, page_kind,"
        " locator, claim_type, extraction_method) SELECT %s, entry_id, surface, page_kind,"
        " locator, claim_type, extraction_method FROM portal_contract_entries"
        " WHERE contract_id = %s", (other, current))
    conn.execute("UPDATE portal_contracts SET is_active = false WHERE id = %s", (current,))
    conn.execute("UPDATE portal_contracts SET is_active = true WHERE id = %s", (other,))
    assert _read(conn, listing)["street_name"] == ("portal", "Husova")

    conn.execute("DELETE FROM portal_contract_entries WHERE contract_id = %s"
                 " AND claim_type = 'street_name'", (other,))
    assert "street_name" not in _read(conn, listing)       # an undeclared type is not read


def test_the_read_can_use_the_key(conn: Any) -> None:
    listing = _listing(conn)
    conn.execute("SET LOCAL enable_seqscan = off")
    plan = "\n".join(r[0] for r in conn.execute(
        "EXPLAIN " + resolve_db._CLAIMS_BULK_SQL, ([listing],)).fetchall())
    assert "location_claims_key" in plan, plan
