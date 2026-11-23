"""`db.connect_read_only` against a real server: a write inside its transaction is refused.

The wave shadow reads production through it, so "no production write" rests on Postgres itself
refusing, not on the code being careful. Gated on TEST_DATABASE_URL: runs in the schema-replay job
(.github/workflows/migrations.yml), skips in the offline suite.
"""

from __future__ import annotations

import os

import psycopg
import pytest

from scraper import db

_DB_URL = os.environ.get("TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not _DB_URL,
    reason="TEST_DATABASE_URL not set — the read-only connection is checked only in CI's DB job",
)


def test_a_write_through_the_read_only_connection_is_refused(monkeypatch):
    monkeypatch.setenv("SUPABASE_DB_URL", _DB_URL)
    with db.connect_read_only() as conn:
        with conn.transaction(), conn.cursor() as cur:
            cur.execute("SHOW transaction_read_only")
            assert cur.fetchone() == ("on",)
        with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
            with conn.transaction(), conn.cursor() as cur:
                cur.execute("INSERT INTO app_settings (key, value) VALUES ('shadow_probe', '1')")
    with db.connect() as conn, conn.transaction(), conn.cursor() as cur:
        cur.execute("SHOW transaction_read_only")
        assert cur.fetchone() == ("off",), "the read-only state leaked past its own transactions"
