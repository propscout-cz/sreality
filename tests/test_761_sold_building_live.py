"""Migration 761 on a real schema: a sale's building code and unit label are read from its own key."""

from __future__ import annotations

import os

import pytest

_DB_URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not _DB_URL, reason="TEST_DATABASE_URL not set")

_INSERT = """
insert into sold_transactions (source, source_record_id, sold_at, price_czk, category_main, category_type,
                               geom, raw)
values ('reas', %s, '2025-03-14', 6450000, %s, 'prodej', ST_SetSRID(ST_MakePoint(14.4, 50.1), 4326), '{}')
returning building_code, unit_label
"""


@pytest.mark.parametrize(("key", "category", "expected"), [
    ("108473925010_unit_22906291-105-37", "byt", (22906291, "105/37")),
    ("99964975010_unit_22948236-565-54", "byt", (22948236, "565/54")),
    ("108651789010_building_23214937", "dum", (23214937, None)),
])
def test_the_key_names_the_building_and_the_unit(key: str, category: str, expected: tuple) -> None:
    import psycopg

    with psycopg.connect(_DB_URL) as conn:
        try:
            assert conn.execute(_INSERT, (key, category)).fetchone() == expected
        finally:
            conn.rollback()
