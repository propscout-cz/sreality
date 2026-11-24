"""E991 executed: the rescore's record is stamped by the database's clock and the sweep reads every
refresh against it, the live rows and the rest in two cycles, inside the scope only. The FakePg
tests re-implement both statements in Python; here the jsonb stamp's round trip to `timestamptz`
and the predicates run for real. Runs in CI's migrations job (`REQUIRED_DB`); conftest's `cur`
rolls every test back.
"""

from __future__ import annotations

import uuid
from typing import Any

from autodedup.incremental_sql import RT_RESCORE_SQL, RT_RESCORE_WRITE_SQL
from tests._live_property import REQUIRED_DB

pytestmark = REQUIRED_DB

TOWN, ELSEWHERE = 999_921, 999_922


def _ad(cur: Any, obec: int) -> int:
    cur.execute(
        "insert into listings (source, source_id_native, raw_json, category_main, "
        " category_type) values ('bazos', %s, '{}'::jsonb, 'byt', 'prodej') returning id",
        (f"e991-{uuid.uuid4()}",))
    listing_id = int(cur.fetchone()[0])
    cur.execute(
        "insert into listing_location (listing_id, match_confidence, granularity, "
        " uncertainty_radius_m, country_status, resolver_version, claim_set_hash, "
        " registry_version, kraj_kod, okres_kod, obec_kod) "
        "values (%s, 'exact', 'building', 5, 'cz', 'test', '\\x00'::bytea, 'test', 99, 9991, %s)",
        (listing_id, obec))
    return listing_id


def _fp(cur: Any, generation: str, listing_id: int, *, active: bool, offset: str) -> None:
    cur.execute("insert into autodedup.rt_fp (generation, listing_id, is_active, updated_at) "
                "values (%s, %s, %s, now() + %s::interval)",
                (generation, listing_id, active, offset))


def test_the_sweep_owes_what_was_refreshed_before_the_stamp_live_rows_first(cur) -> None:
    generation = f"e991-{uuid.uuid4()}"
    cur.execute(RT_RESCORE_WRITE_SQL, {"key": f"rt_rescore:{generation}",
                                       "value": '{"settings": "w34"}', "updated_by": "test"})
    since = cur.fetchone()[0]
    cur.execute("select value, (value ->> 'since')::timestamptz = now() "
                "from autodedup.settings where key = %s", (f"rt_rescore:{generation}",))
    value, stamped_now = cur.fetchone()
    assert value == {"settings": "w34", "since": since} and stamped_now

    before, after, away, gone = _ad(cur, TOWN), _ad(cur, TOWN), _ad(cur, ELSEWHERE), _ad(cur, TOWN)
    _fp(cur, generation, before, active=True, offset="-1 hour")
    _fp(cur, generation, after, active=True, offset="1 minute")
    _fp(cur, generation, away, active=True, offset="-1 hour")
    _fp(cur, generation, gone, active=False, offset="-1 hour")
    params = {"generation": generation, "after_id": 0, "limit": 10, "since": since,
              "all_scope": False, "obec": [TOWN], "cast_obce": [], "okres": []}

    cur.execute(RT_RESCORE_SQL, {**params, "active": True})
    assert cur.fetchone() == (away, 3, [before]), "the live cycle: in scope, refreshed before"
    cur.execute(RT_RESCORE_SQL, {**params, "active": False})
    assert cur.fetchone() == (gone, 1, [gone])
    cur.execute(RT_RESCORE_SQL, {**params, "active": True, "all_scope": True})
    assert cur.fetchone() == (away, 3, [before, away])
    cur.execute(RT_RESCORE_SQL, {**params, "active": True, "after_id": away})
    assert cur.fetchone() == (away, 0, []), "an empty slice past the end"
