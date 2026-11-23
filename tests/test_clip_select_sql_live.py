"""The clip job's selection, executed (#2032): a photo stored in the last 45 min comes before a priority kraj's older
one, whatever their ids, and the snapshot runs under the job's batch statement timeout.

The hermetic tests answer each arm with canned rows, so only this runs the SQL that encodes the order.
Runs in CI's migrations lane (`TEST_DATABASE_URL`, `DB_RAILS_REQUIRED=1`); skipped locally.
"""

from __future__ import annotations

import datetime as _dt
import os
import uuid
from collections.abc import Iterator
from typing import Any

import pytest

from scripts import clip_tag_backfill as ctb

_DB_URL = os.environ.get("TEST_DATABASE_URL")
_REQUIRED = os.environ.get("DB_RAILS_REQUIRED") == "1"
_PRAHA, _ELSEWHERE = 19, 141

pytestmark = pytest.mark.skipif(
    not _DB_URL and not _REQUIRED,
    reason="TEST_DATABASE_URL not set — this rail runs in CI's migrations lane",
)


@pytest.fixture()
def conn() -> Iterator[Any]:
    """One connection in one transaction, always rolled back."""
    if not _DB_URL:
        pytest.fail("DB_RAILS_REQUIRED=1 but TEST_DATABASE_URL is not set — the migrations lane is "
                    "misconfigured and this rail would otherwise have skipped green.")
    import psycopg

    connection = psycopg.connect(_DB_URL)
    try:
        yield connection
    finally:
        connection.rollback()
        connection.close()


def _stored_photo(conn: Any, kraj: int, stored_ago: _dt.timedelta) -> int:
    """A pending photo (stored, not CLIP-tagged) of a new ad located in `kraj`, stored `stored_ago`."""
    listing = conn.execute(
        "insert into listings (source, source_id_native, raw_json, category_main, category_type, is_active) "
        "values ('bazos', %s, '{}'::jsonb, 'byt', 'prodej', true) returning id",
        (f"w5-{uuid.uuid4().hex}",)).fetchone()[0]
    conn.execute(
        "insert into listing_location (listing_id, geom, match_confidence, granularity, uncertainty_radius_m, "
        "country_status, resolver_version, claim_set_hash, registry_version, kraj_kod) values (%s, "
        "ST_SetSRID(ST_MakePoint(14.42, 50.08), 4326), 'exact', 'building', 5, 'cz', 'test', '\\x00'::bytea, "
        "'test', %s)", (listing, kraj))
    return conn.execute(
        "insert into images (listing_id, sreality_url, sequence, storage_path, last_download_attempt_at) "
        "values (%s, %s, 1, %s, now() - make_interval(secs => %s)) returning id",
        (listing, f"https://cdn.test/{listing}/1.jpg", f"img/{listing}/x.jpg", stored_ago.total_seconds())
    ).fetchone()[0]


def test_a_fresh_photo_comes_before_an_older_one_in_a_priority_kraj(conn: Any) -> None:
    fresh = _stored_photo(conn, _ELSEWHERE, _dt.timedelta(minutes=10))
    priority = _stored_photo(conn, _PRAHA, _dt.timedelta(hours=2))
    assert priority > fresh, "the older photo holds the newer id: only the fresh arm puts the other first"
    rows, phase = ctb._select_pending(conn, limit=1000, priority_regions=[_PRAHA])
    ours = [r[0] for r in rows if r[0] in (fresh, priority)]
    assert ours == [fresh, priority], phase
    assert phase.startswith("fresh:")
    timeout = conn.execute("select current_setting('statement_timeout')").fetchone()[0]
    assert timeout == f"{ctb.SELECT_TIMEOUT_MS // 60_000}min", "the snapshot runs under the batch timeout"
