"""Migration 622: the daily property sweep is a derived artifact, judged by the jobs check at today's 56 h line.

The tests read the replayed row and run the file's own seed and post-condition (not copies), the producer's stamp and
the jobs check's artifact read, inside a transaction that always rolls back. Runs in CI's migrations lane
(`TEST_DATABASE_URL`, `DB_RAILS_REQUIRED=1`); skipped locally.
"""

from __future__ import annotations

import datetime as _dt
import os
import re
from pathlib import Path
from typing import Any

import pytest

_DB_URL = os.environ.get("TEST_DATABASE_URL")
_REQUIRED = os.environ.get("DB_RAILS_REQUIRED") == "1"
_REPO = Path(__file__).resolve().parents[1]
_MIGRATION = _REPO / "migrations" / "622_property_sweep_artifact.sql"

pytestmark = pytest.mark.skipif(
    not _DB_URL and not _REQUIRED,
    reason="TEST_DATABASE_URL not set — this rail runs in CI's migrations lane",
)


@pytest.fixture()
def conn():
    """One connection in one transaction, always rolled back: the seed and the stamp write the real row."""
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


def _statement(start: str, end: str) -> str:
    """One statement of migration 622, exactly as the file runs it."""
    sql = _MIGRATION.read_text(encoding="utf-8")
    head = sql.index(start)
    return sql[head:sql.index(end, head) + len(end)]


_SEED = _statement("update public.derived_artifacts", ";")
_POST = _statement("do $post$", "$post$;")


def _sweep(conn: Any) -> tuple[Any, ...]:
    """(last_succeeded_at, complete_through, last_rows, last_duration_ms) of the property_sweep row."""
    return conn.execute("select last_succeeded_at, complete_through, last_rows, last_duration_ms "
                        "from derived_artifacts where name = 'property_sweep'").fetchone()


def _completed(conn: Any, at: str) -> None:
    """The retired app_settings stamp, as the old sweep wrote it."""
    conn.execute("insert into app_settings (key, value, updated_by) "
                 "values ('property_sweep_last_complete', jsonb_build_object('completed_at', %s::text), 'test') "
                 "on conflict (key) do update set value = excluded.value", (at,))


def test_the_row_declares_the_workflows_own_cadence(conn):
    row = conn.execute("select producer, host, cadence, staleness_budget, is_serving from derived_artifacts "
                       "where name = 'property_sweep'").fetchone()
    cron = re.search(r'cron: "([^"]+)"',
                     (_REPO / ".github/workflows/recompute_property_stats.yml").read_text(encoding="utf-8"))
    assert row == ("scripts/recompute_property_stats.py", "github-actions", cron[1], _dt.timedelta(hours=38), True)


@pytest.mark.parametrize("age_h, status", [(55.9, "ok"), (56.1, "fail")])
def test_the_jobs_check_fails_the_sweep_past_todays_56_hours(conn, age_h, status):
    """38 h budget + GitHub's 18 h delivery slack = the retired property_maintenance alarm's fail line, which clears a
    cycle split across two daily runs (48 h + the cron's late start) and stays under three days."""
    from scripts.checks import platform as plat
    from scripts.verify_pipeline import THRESHOLDS

    conn.execute("update derived_artifacts set last_succeeded_at = now() - make_interval(secs => %s) "
                 "where name = 'property_sweep'", (age_h * 3600,))
    now = conn.execute("select now()").fetchone()[0]

    cells = {c["subject"]: c for c in plat._artifact_cells(conn, THRESHOLDS, now)}
    assert cells["artifact/property_sweep"]["status"] == status


def test_the_seed_carries_the_last_completion_over_and_never_moves_it_back(conn):
    conn.execute("update derived_artifacts set last_succeeded_at = null, complete_through = null "
                 "where name = 'property_sweep'")
    at = _dt.datetime(2026, 10, 18, 9, 6, 34, tzinfo=_dt.timezone.utc)
    _completed(conn, at.isoformat())

    assert conn.execute(_SEED).rowcount == 1
    assert _sweep(conn)[:2] == (at, at)
    _completed(conn, (at - _dt.timedelta(days=1)).isoformat())
    assert conn.execute(_SEED).rowcount == 0
    _completed(conn, (at + _dt.timedelta(days=1)).isoformat())   # a sweep between apply and deploy
    assert conn.execute(_SEED).rowcount == 1
    assert _sweep(conn)[:2] == (at + _dt.timedelta(days=1),) * 2
    conn.execute(_POST)


def test_the_producers_stamp_lands_on_the_row(conn):
    """The helper is a silent no-op on an unregistered name: the producer's literal must reach this row."""
    from scraper import db

    db.stamp_derived_artifact(conn, "property_sweep", rows=7, duration_ms=5000)

    last_ok, through, rows, duration_ms = _sweep(conn)
    started = conn.execute("select now()").fetchone()[0]
    assert (rows, duration_ms) == (7, 5000)
    assert last_ok == through and last_ok >= started


def test_the_post_condition_refuses_a_loosened_budget(conn):
    """The guard bites: a hand-made row with today's 56 h as its budget would fail only at 74 h."""
    import psycopg

    conn.execute("update derived_artifacts set staleness_budget = interval '56 hours' where name = 'property_sweep'")
    with pytest.raises(psycopg.errors.RaiseException, match="622: the property_sweep artifact"):
        with conn.transaction():
            conn.execute(_POST)
