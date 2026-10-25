"""Migration 674 (Actions exit W0, #2012): the hourly city-proximity refresh runs on pg_cron, not GitHub.

The offline half reads the files: the workflow is gone, the migration schedules the hourly call and the
history trim, and the two attended tools that shift the precomputed maxes end with the full rebuild. The live
half runs in CI's migrations job (`TEST_DATABASE_URL`; every test rolls back): the function must raise no
NOTICE, because a NOTICE inside a pg_cron command blocks pg_cron's single launcher until the command ends
(migration 658).
"""

from __future__ import annotations

import pathlib
import re
from typing import Any

import pytest
import yaml

from tests._live_property import REQUIRED_DB, _advert, _property, _recompute, cur  # noqa: F401 -- `cur` is the fixture

ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
MIGRATION = ROOT / "migrations" / "674_city_proximity_on_pg_cron.sql"
FULL_REBUILD = "python -m scripts.recompute_city_proximity --full"


def _scheduled() -> dict[str, tuple[str, str]]:
    """jobname -> (schedule, command) for every cron.schedule call in the migration."""
    calls = re.findall(r"cron\.schedule\(\s*'([^']+)',\s*'([^']+)',\s*\$\$(.*?)\$\$\s*\)", MIGRATION.read_text(), re.S)
    return {name: (schedule, command.strip()) for name, schedule, command in calls}


def test_the_hourly_workflow_is_gone() -> None:
    assert not (WORKFLOWS / "recompute_city_proximity.yml").exists()


def test_the_migration_schedules_the_hourly_incremental_call_under_a_statement_timeout() -> None:
    schedule, command = _scheduled()["recompute-city-proximity"]
    assert schedule == "40 * * * *"
    assert re.fullmatch(r"set statement_timeout='\d+s'; select public\.recompute_city_proximity\(false\);", command)


def test_the_migration_trims_pg_crons_run_history_daily_and_keeps_90_days() -> None:
    # 90 days: Health's capacity reference reaches that far back (CAPACITY_REFERENCE_FROM's rail).
    # Daily: the capacity check measures only a job with a finished run in the last 24 h.
    schedule, command = _scheduled()["cron-history-trim"]
    assert re.fullmatch(r"\d+ \d+ \* \* \*", schedule)
    assert "delete from cron.job_run_details where start_time < now() - interval '90 days'" in command
    assert command.startswith("set statement_timeout=")


@pytest.mark.parametrize("workflow", ["load_obec_population.yml", "seed_curated_cities.yml"])
def test_the_attended_tools_end_with_the_full_rebuild(workflow: str) -> None:
    doc = yaml.safe_load((WORKFLOWS / workflow).read_text())
    (job,) = doc["jobs"].values()
    last = job["steps"][-1]
    assert last["run"].strip() == FULL_REBUILD
    assert "secrets.SUPABASE_DB_URL" in str(job.get("env", {}).get("SUPABASE_DB_URL"))


def test_a_dry_run_seed_skips_the_full_rebuild() -> None:
    doc = yaml.safe_load((WORKFLOWS / "seed_curated_cities.yml").read_text())
    (job,) = doc["jobs"].values()
    assert job["steps"][-1]["if"] == "${{ inputs.dry_run != 'true' }}"


@REQUIRED_DB
def test_recompute_city_proximity_raises_no_notice(cur: Any) -> None:  # noqa: F811
    notices: list[str] = []
    cur.connection.add_notice_handler(lambda d: notices.append(d.message_primary or ""))
    cur.execute("SELECT recompute_city_proximity(false)")
    cur.execute("SELECT recompute_city_proximity(false)")  # its anchor table already exists in this transaction
    assert notices == []


@REQUIRED_DB
def test_recompute_city_proximity_holds_an_advisory_lock_until_its_transaction_ends(cur: Any) -> None:  # noqa: F811
    # What keeps the hourly run and a full rebuild from updating the same rows at once.
    cur.execute("SELECT recompute_city_proximity(false)")
    cur.execute("SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND pid = pg_backend_pid() AND granted")
    assert cur.fetchone()[0] == 1


def _placed_property(cur: Any, at: tuple[float, float]) -> int:
    """A property whose location ad has a map point, stamped long ago."""
    pid = _property(cur)
    lid = _advert(cur, pid, source="idnes")
    cur.execute(
        "INSERT INTO listing_location (listing_id, geom, match_confidence, granularity, uncertainty_radius_m, "
        "  country_status, resolver_version, claim_set_hash, registry_version, country_code, obec_name) "
        "VALUES (%s, ST_SetSRID(ST_MakePoint(%s, %s), 4326), 'exact', 'address_point'::location_granularity, 10, "
        "  'cz'::country_status, 'test', '\\x00'::bytea, 'test', 'CZ', 'Testov')", (lid, *at))
    _recompute(cur, pid)
    cur.execute("UPDATE properties SET city_proximity_computed_at = '2026-01-01 00:00+00' WHERE id = %s", (pid,))
    return pid


@REQUIRED_DB
def test_the_full_rebuild_recomputes_every_placeable_property_in_batches(cur: Any) -> None:  # noqa: F811
    from scripts.recompute_city_proximity import full_rebuild

    centre = (15.50, 49.50)
    cur.execute(
        "INSERT INTO admin_boundaries (id, level, name, geom, population) VALUES (9990101, 'obec', 'Testov', "
        "ST_Multi(ST_MakeEnvelope(15.45, 49.45, 15.55, 49.55, 4326))::geography, 25000)")
    pids = [_placed_property(cur, (centre[0] + d, centre[1])) for d in (0.0, 0.001, 0.002)]
    unplaced = _property(cur)
    assert full_rebuild(cur.connection, batch_size=2) >= 3
    cur.execute("SELECT id, home_obec_pop, city_proximity_computed_at > '2026-01-02' FROM properties "
                "WHERE id = ANY(%s) ORDER BY id", (pids,))
    assert cur.fetchall() == [(pid, 25000, True) for pid in sorted(pids)]
    cur.execute("SELECT city_proximity_computed_at FROM properties WHERE id = %s", (unplaced,))
    assert cur.fetchone()[0] is None, "a property with no map point is left as it was"


@REQUIRED_DB
def test_a_browser_role_cannot_execute_recompute_city_proximity(cur: Any) -> None:  # noqa: F811
    for role in ("anon", "authenticated"):
        cur.execute("SELECT has_function_privilege(%s, 'public.recompute_city_proximity(boolean)', 'execute')",
                    (role,))
        assert cur.fetchone()[0] is False, role
