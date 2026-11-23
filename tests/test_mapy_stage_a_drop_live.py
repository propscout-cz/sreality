"""Mapy exit, Stage A (#2454; migration 739): curated_cities' Mapy geocode (centroid, source, source_confidence and
the centroid index) and the backup_w4 schema are dropped with no copy, and a curated city's town code is unique.
Live over the replayed catalog; 739's own guard refuses a function body still naming what it drops.
"""

from __future__ import annotations

import uuid
from typing import Any

import psycopg
import pytest

from tests._live_property import REQUIRED_DB
from tests.test_admin_boundaries_unread import _plant, _pre

pytestmark = REQUIRED_DB

MIGRATION = "739_mapy_stage_a_drops.sql"
OBEC = 990_739


def test_the_mapy_columns_index_and_backup_schema_are_gone_and_curated_cities_public_is_unchanged(cur: Any) -> None:
    cur.execute("SELECT to_regnamespace('backup_w4'), to_regclass('public.curated_cities_centroid_idx')")
    assert cur.fetchone() == (None, None)
    cur.execute("SELECT attname FROM pg_attribute WHERE attrelid = 'public.curated_cities'::regclass "
                "AND NOT attisdropped AND attname IN ('centroid', 'source', 'source_confidence')")
    assert cur.fetchall() == []
    cur.execute("SELECT string_agg(attname, ',' ORDER BY attnum) FROM pg_attribute "
                "WHERE attrelid = 'public.curated_cities_public'::regclass AND attnum > 0 AND NOT attisdropped")
    assert cur.fetchone() == ("city_id,name,kraj_name,population,population_as_of_year,obec_kod",)


@pytest.mark.parametrize("body", [
    "PERFORM source_confidence FROM curated_cities",
    "PERFORM centroid FROM curated_cities",
    "PERFORM count(*) FROM backup_w4.mapy_affected",
])
def test_the_guard_refuses_a_function_that_still_names_what_739_drops(cur: Any, body: str) -> None:
    cur.execute(_pre(MIGRATION))  # the replayed schema passes its own guard
    _plant(cur, body)
    with pytest.raises(psycopg.errors.RaiseException, match="739 refused"):
        cur.execute(_pre(MIGRATION))


def test_a_second_curated_city_on_one_town_is_refused(cur: Any) -> None:
    first = f"w739-{uuid.uuid4().hex[:8]}"
    cur.execute("INSERT INTO curated_cities (name, kraj_name, obec_kod) VALUES (%s, 'w739', %s)", (first, OBEC))
    with pytest.raises(psycopg.errors.UniqueViolation):
        cur.execute("INSERT INTO curated_cities (name, kraj_name, obec_kod) VALUES (%s, 'w739', %s)",
                    (first + "-twin", OBEC))
