"""#2509 executed: `RT_CHANGED_LISTINGS_SQL` claims a snapshot that follows another of its listing
and skips a listing's first (its arrival, which the new feed claims), while its window still
crosses it. The FakePg tests re-implement the predicate in Python, so a neutralised one passes
there; here it fails. Runs in CI's migrations job (`REQUIRED_DB`); conftest's `cur` rolls
every test back.
"""

from __future__ import annotations

import uuid
from typing import Any

from autodedup.incremental_sql import RT_CHANGED_LISTINGS_SQL
from tests._live_property import REQUIRED_DB, _snapshot

pytestmark = REQUIRED_DB

TOWN, ELSEWHERE = 999_911, 999_912


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


def test_the_changed_feed_claims_a_follow_up_and_steps_over_an_arrival(cur) -> None:
    moved, arrived, away = _ad(cur, TOWN), _ad(cur, TOWN), _ad(cur, ELSEWHERE)
    first = _snapshot(cur, moved, 1, 1.0)
    _snapshot(cur, arrived, 1, 1.0)
    _snapshot(cur, away, 1, 1.0)
    follow = _snapshot(cur, moved, 1, 1.0)
    last = _snapshot(cur, away, 1, 1.0)
    params = {"after_id": first - 1, "lag": 0, "window": 5, "all_scope": False,
              "obec": [TOWN], "cast_obce": [], "okres": []}

    cur.execute(RT_CHANGED_LISTINGS_SQL, params)
    window_max, size, ids, _stamps, cursors = cur.fetchone()
    assert (window_max, size) == (last, 5), "the window crosses every row, arrivals included"
    assert (ids, cursors) == ([moved], [follow])

    cur.execute(RT_CHANGED_LISTINGS_SQL, {**params, "all_scope": True})
    _max, _size, ids, _stamps, cursors = cur.fetchone()
    assert (ids, cursors) == ([moved, away], [follow, last]), "the whole corpus skips them too"
