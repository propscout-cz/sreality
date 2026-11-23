"""Migrations 622 and 731: the daily property sweep is a derived artifact, judged by the jobs check at its 56 h line.

The tests read the replayed row and run 622's own seed (not a copy), the producer's stamp and the jobs check's artifact
read, inside a transaction that always rolls back. Runs in CI's migrations lane
(`TEST_DATABASE_URL`, `DB_RAILS_REQUIRED=1`); skipped locally.
"""

from __future__ import annotations

import datetime as _dt
import os
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


def _sweep(conn: Any) -> tuple[Any, ...]:
    """(last_succeeded_at, complete_through, last_rows, last_duration_ms) of the property_sweep row."""
    return conn.execute("select last_succeeded_at, complete_through, last_rows, last_duration_ms "
                        "from derived_artifacts where name = 'property_sweep'").fetchone()


def _completed(conn: Any, at: str) -> None:
    """The retired app_settings stamp, as the old sweep wrote it."""
    conn.execute("insert into app_settings (key, value, updated_by) "
                 "values ('property_sweep_last_complete', jsonb_build_object('completed_at', %s::text), 'test') "
                 "on conflict (key) do update set value = excluded.value", (at,))


def test_the_row_names_its_runner_entry(conn):
    """#2027 (migration 731): the row follows its job to the runner and names the registry entry, not a cron."""
    from scraper import jobs

    row = conn.execute("select producer, host, cadence, staleness_budget, is_serving from derived_artifacts "
                       "where name = 'property_sweep'").fetchone()
    [entry] = [j for j in jobs.runner_jobs() if j.name == "property_sweep"]
    assert (entry.every_s, f"{entry.argv[0].replace('.', '/')}.py") == (jobs.DAY, row[0])
    assert row[1:] == (jobs.RUNNER, "daily (scraper/jobs.py property_sweep)", _dt.timedelta(hours=56), True)


@pytest.mark.parametrize("age_h, status", [(55.9, "ok"), (56.1, "fail")])
def test_the_jobs_check_fails_the_sweep_past_56_hours(conn, age_h, status):
    """56 h, no delivery slack on the runner: the retired property_maintenance alarm's fail line, kept through the move
    (622 spelled it 38 h + GitHub's 18 h slack). It clears a cycle split across two daily runs and stays under three
    days."""
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


def test_the_producers_stamp_lands_on_the_row(conn):
    """The helper is a silent no-op on an unregistered name: the producer's literal must reach this row."""
    from scraper import db

    db.stamp_derived_artifact(conn, "property_sweep", rows=7, duration_ms=5000)

    last_ok, through, rows, duration_ms = _sweep(conn)
    started = conn.execute("select now()").fetchone()[0]
    assert (rows, duration_ms) == (7, 5000)
    # Migration 441's helper reads clock_timestamp() once per column: the two reads can straddle a microsecond tick.
    assert abs(last_ok - through) < _dt.timedelta(milliseconds=1)
    assert last_ok >= started and through >= started

