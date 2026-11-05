"""The text lane's bounded walk (2026-11-05), EXECUTED against the replayed schema — CI's
migrations lane (`TEST_DATABASE_URL`); every test rolls back; locally it skips. Only a real
database shows the cursor reaching every archived advert: a text changed with no snapshot (a
reparse heal) is re-read within one cycle, and a new advert on the pass it arrives.
"""

from __future__ import annotations

import os
import uuid
from typing import Any

import pytest

from toolkit import description_extraction as tx

_DB_URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not _DB_URL and os.environ.get("DB_RAILS_REQUIRED") != "1",
    reason="TEST_DATABASE_URL not set — schema-replay test runs only in the CI DB job")

VERSION = "2:walk:m1"


@pytest.fixture()
def conn(monkeypatch):
    import psycopg

    connection = psycopg.connect(_DB_URL, autocommit=True, options="-c statement_timeout=20000")
    monkeypatch.setattr(tx, "ARCHIVE_WALK", 2)
    tx._ARCHIVE["below"] = None
    with connection, connection.transaction(force_rollback=True):
        yield connection
    tx._ARCHIVE["below"] = None


def _listing(conn: Any, age_days: float) -> int:
    return int(conn.execute(
        "INSERT INTO listings (source, source_id_native, raw_json, description, category_main,"
        " category_type, price_czk, area_m2, is_active, last_seen_at, first_seen_at)"
        " VALUES ('bazos', %s, jsonb_build_object('title', 'Prodej bytu'::text), 'Byt 2+kk',"
        " 'byt', 'prodej', 5000000, 60, true, now(), now() - %s::float8 * interval '1 day')"
        " RETURNING id", (f"walk-{uuid.uuid4()}", age_days)).fetchone()[0])


def _read(conn: Any, ids: list[int]) -> None:
    """The reading the lane would store for each of `ids`, keyed on its current text."""
    conn.execute(
        "INSERT INTO listing_description_enrichments (listing_id, text_hash, extractor_version,"
        f" extracted, filled, model) SELECT l.id, {tx._HASH_EXPR}, %s, '{{}}', '{{}}', 'm1'"
        " FROM listings l WHERE l.id = ANY(%s)", (VERSION, ids))


def _cycle(conn: Any) -> list[list[int]]:
    """Passes, each reading what it selected, until the archive cursor starts over."""
    passes: list[list[int]] = []
    for _ in range(50):
        picked = [r["id"] for r in tx.select_eligible(conn, version=VERSION)]
        _read(conn, picked)
        passes.append(picked)
        if tx._ARCHIVE["below"] is None:
            return passes
    raise AssertionError("the archive cursor never started over")


def test_a_reparse_heal_in_the_archive_is_re_read_within_one_cycle(conn) -> None:
    archived = [_listing(conn, days) for days in (3, 4, 5, 6, 7)]
    _read(conn, archived)
    fresh = _listing(conn, 0.01)
    first = _cycle(conn)
    assert first[0] == [fresh] and not any(first[1:])
    assert len(first) >= 3, "the archive took more than one slice"

    # A reparse heal: the description is rewritten in place, no snapshot.
    healed = archived[3]
    conn.execute("UPDATE listings SET description = 'Byt 2+kk, výtah' WHERE id = %s", (healed,))
    second = _cycle(conn)
    assert [i for p in second for i in p] == [healed]
