"""#2509 executed: `RT_CHANGED_LISTINGS_SQL` claims a snapshot that follows another of its listing
and skips a listing's first (its arrival, which the new feed claims), while its window still
crosses it. The FakePg tests re-implement the predicate in Python, so a neutralised one passes
there; here it fails. Runs in CI's migrations job (`TEST_DATABASE_URL`); every test rolls back.
"""

from __future__ import annotations

import os
import uuid
from typing import Any

import pytest

from autodedup.incremental_sql import RT_CHANGED_LISTINGS_SQL

_DB_URL = os.environ.get("TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not _DB_URL,
    reason="TEST_DATABASE_URL not set — schema-replay test runs only in the CI DB job",
)

TOWN, ELSEWHERE = 999_911, 999_912


@pytest.fixture()
def cur():
    import psycopg

    conn = psycopg.connect(
        _DB_URL,
        options="-c statement_timeout=20000 -c lock_timeout=5000"
        " -c idle_in_transaction_session_timeout=30000",
    )
    try:
        with conn.cursor() as c:
            yield c
    finally:
        conn.rollback()
        conn.close()


def _ad(cur: Any, obec: int) -> int:
    cur.execute(
        "insert into listings (source, source_id_native, raw_json, category_main, "
        " category_type) values ('bazos', %s, '{}'::jsonb, 'byt', 'prodej') returning id",
        (f"2509-{uuid.uuid4()}",))
    listing_id = int(cur.fetchone()[0])
    cur.execute(
        "insert into listing_location (listing_id, match_confidence, granularity, "
        " uncertainty_radius_m, country_status, resolver_version, claim_set_hash, "
        " registry_version, kraj_kod, okres_kod, obec_kod) "
        "values (%s, 'exact', 'building', 5, 'cz', 'test', '\\x00'::bytea, 'test', 99, 9991, %s)",
        (listing_id, obec))
    return listing_id


def _snapshot(cur: Any, listing_id: int) -> int:
    cur.execute(
        "insert into listing_snapshots (listing_id, scraped_at, price_czk, content_hash, raw_json) "
        "values (%s, now() - interval '1 hour', 1, %s, '{}'::jsonb) returning id",
        (listing_id, f"2509-{uuid.uuid4()}"))
    return int(cur.fetchone()[0])


def test_the_changed_feed_claims_a_follow_up_and_steps_over_an_arrival(cur) -> None:
    moved, arrived, away = _ad(cur, TOWN), _ad(cur, TOWN), _ad(cur, ELSEWHERE)
    first = _snapshot(cur, moved)
    _snapshot(cur, arrived)
    _snapshot(cur, away)
    follow = _snapshot(cur, moved)
    last = _snapshot(cur, away)
    params = {"after_id": first - 1, "lag": 0, "window": 5, "all_scope": False,
              "obec": [TOWN], "cast_obce": [], "okres": []}

    cur.execute(RT_CHANGED_LISTINGS_SQL, params)
    window_max, size, ids, _stamps, cursors = cur.fetchone()
    assert (window_max, size) == (last, 5), "the window crosses every row, arrivals included"
    assert (ids, cursors) == ([moved], [follow])

    cur.execute(RT_CHANGED_LISTINGS_SQL, {**params, "all_scope": True})
    _max, _size, ids, _stamps, cursors = cur.fetchone()
    assert (ids, cursors) == ([moved, away], [follow, last]), "the whole corpus skips them too"
