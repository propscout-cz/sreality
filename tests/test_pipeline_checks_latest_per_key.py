"""`pipeline_checks_public` serves the check REGISTRY, each key with its newest result (migration 619).

Migration 422 served every check_key that ever wrote a row, so a check whose producer was deleted
(outbound_url_parity, 2026-09-13) stayed on /health as a current verdict. 619 drives the rows from
`health_checks`: a registered key that never ran is served as not measured, a result key nobody
declares is not served, and `effective_status` reads `unknown` for a stale or unmeasured newest row
while `status` keeps what was stored. Each row also carries its key's cell incidents.

These are claims about rows, so the view is executed against the replayed schema inside a
transaction that always rolls back. Runs in CI's migrations job (`TEST_DATABASE_URL`); skipped locally.
"""

from __future__ import annotations

import os

import pytest

_DB_URL = os.environ.get("TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not _DB_URL,
    reason="TEST_DATABASE_URL not set — schema-replay test runs only in the CI DB job",
)

_REGISTRY = """
insert into health_checks (key, domain, class, host, cadence_s, staleness_budget, label) values
  ('aaa_first', 'platform', 'alarm', 'github-actions', 3600, '19 hours', 'First'),
  ('mmm_mid',   'scraping', 'gauge', 'github-actions', 3600, '19 hours', 'Middle'),
  ('zzz_last',  'platform', 'alarm', 'github-actions', 3600, '19 hours', 'Last')
"""

_RESULTS = """
insert into pipeline_check_results (run_at, check_key, status, value, details) values
  (now() - interval '3 hours', 'zzz_last',  'ok',   1, '{"n":1}'),
  (now() - interval '1 hour',  'zzz_last',  'fail', 3, '{"n":3}'),
  (now() - interval '2 hours', 'zzz_last',  'warn', 2, '{"n":2}'),
  (now() - interval '1 hour',  'aaa_first', 'warn', 9, '{"n":9}'),
  (now() - interval '2 hours', 'aaa_first', 'ok',   8, '{"n":8}'),
  (now() - interval '1 hour',  'mmm_mid',   'ok',   7, '{"n":7}')
"""


@pytest.fixture()
def cur():
    """Each test runs in a transaction that is ALWAYS rolled back.

    These tests empty and reseed real tables, so the rollback is not tidiness — it is what
    keeps them from corrupting the schema the rest of the CI job asserts against.
    """
    import psycopg

    conn = psycopg.connect(_DB_URL)  # autocommit off: everything below is one transaction
    try:
        with conn.cursor() as c:
            yield c
    finally:
        conn.rollback()
        conn.close()


def _empty(cur) -> None:
    """Start from no registry, no results and no cell incidents (a later migration may seed rows)."""
    cur.execute("delete from pipeline_check_results")
    cur.execute("delete from health_checks")
    cur.execute("delete from ops_incidents where signature like 'cell:%'")


def test_admin_gate_is_open_for_this_session(cur):
    """Guard against the rest of this file passing vacuously.

    The view is admin-gated; a session the gate rejects sees zero rows, which would make
    every assertion below trivially true.
    """
    cur.execute("select is_platform_admin()")
    assert cur.fetchone()[0], "is_platform_admin() is false — the tests below prove nothing"


def test_serves_the_newest_row_of_each_registered_key(cur):
    _empty(cur)
    cur.execute(_REGISTRY)
    cur.execute(_RESULTS)
    cur.execute(
        "select check_key, status, value::int, label from pipeline_checks_public order by check_key"
    )
    assert cur.fetchall() == [
        ("aaa_first", "warn", 9, "First"),
        ("mmm_mid", "ok", 7, "Middle"),
        ("zzz_last", "fail", 3, "Last"),
    ]


def test_a_registered_key_that_never_ran_is_served_as_not_measured(cur):
    """The registry, not the results table, decides what the page lists."""
    _empty(cur)
    cur.execute(_REGISTRY)
    cur.execute(
        "select run_at, status, effective_status, last_measured_at, incidents "
        "from pipeline_checks_public where check_key = 'mmm_mid'"
    )
    assert cur.fetchone() == (None, None, "unknown", None, [])


def test_a_stale_newest_row_reads_unknown_and_keeps_its_stored_status(cur):
    """RED by: dropping the health_stale arm of effective_status."""
    _empty(cur)
    cur.execute(
        "insert into health_checks (key, domain, class, host, cadence_s, staleness_budget, label) "
        "values ('fresh', 'platform', 'alarm', 'github-actions', 3600, '2 hours', 'Fresh'), "
        "       ('stale', 'platform', 'alarm', 'github-actions', 3600, '2 hours', 'Stale')"
    )
    cur.execute(
        "insert into pipeline_check_results (run_at, check_key, status) values "
        "(now() - interval '1 hour', 'fresh', 'fail'), (now() - interval '3 hours', 'stale', 'fail')"
    )
    cur.execute(
        "select check_key, status, effective_status from pipeline_checks_public order by check_key"
    )
    assert cur.fetchall() == [("fresh", "fail", "fail"), ("stale", "fail", "unknown")]


def test_an_unknown_newest_row_reads_unknown_and_last_measured_skips_it(cur):
    """A row the harness could not measure is stored (the widened CHECK) and never reads as measured."""
    _empty(cur)
    cur.execute(_REGISTRY)
    cur.execute(
        "insert into pipeline_check_results (run_at, check_key, status) values "
        "(now() - interval '3 hours', 'aaa_first', 'ok'), "
        "(now() - interval '2 hours', 'aaa_first', 'unknown'), "
        "(now() - interval '1 hour',  'aaa_first', 'unknown')"
    )
    cur.execute(
        "select status, effective_status, "
        "       run_at = now() - interval '1 hour', "
        "       last_measured_at = now() - interval '3 hours' "
        "from pipeline_checks_public where check_key = 'aaa_first'"
    )
    assert cur.fetchone() == ("unknown", "unknown", True, True)


def test_a_result_key_without_a_registry_row_is_not_served(cur):
    """How a retired check leaves the page: its rows stay, its registry row does not."""
    _empty(cur)
    cur.execute(_REGISTRY)
    cur.execute(_RESULTS)
    cur.execute(
        "insert into pipeline_check_results (run_at, check_key, status) "
        "values (now(), 'retired_key', 'ok')"
    )
    cur.execute(
        "select count(*) filter (where check_key = 'retired_key'), count(*) from pipeline_checks_public"
    )
    assert cur.fetchone() == (0, 3)


def test_incidents_carry_the_open_cell_incident(cur):
    _empty(cur)
    cur.execute(_REGISTRY)
    cur.execute("select incidents from pipeline_checks_public where check_key = 'aaa_first'")
    assert cur.fetchone()[0] == []

    cur.execute("insert into ops_incidents (signature) values ('cell:aaa_first:x')")
    cur.execute("select incidents from pipeline_checks_public where check_key = 'aaa_first'")
    (incident,) = cur.fetchone()[0]
    assert set(incident) == {
        "id", "subject", "first_seen_at", "last_seen_at", "resolved_at",
        "acknowledged_at", "notice_count", "last_notice_at",
    }
    assert incident["subject"] == "x"
    assert (incident["resolved_at"], incident["acknowledged_at"]) == (None, None)
    assert (incident["notice_count"], incident["last_notice_at"]) == (0, None)


def test_incidents_stop_at_their_own_key_and_at_fourteen_days(cur):
    """Every key holds `_`, a LIKE wildcard: `cell:a_b:%` would also claim `cell:aXb:...`.

    RED by: matching signatures with LIKE instead of starts_with, or dropping the 14-day window.
    """
    _empty(cur)
    cur.execute(
        "insert into health_checks (key, domain, class, host, cadence_s, staleness_budget, label) "
        "values ('a_b', 'platform', 'alarm', 'github-actions', 3600, '19 hours', 'A b')"
    )
    cur.execute(
        "insert into ops_incidents (signature, resolved_at) values "
        "('cell:a_b:recent', now() - interval '1 day'), "
        "('cell:a_b:old', now() - interval '15 days'), "
        "('cell:aXb:other', null), "
        "('a_b:engine-signature', null)"
    )
    cur.execute("select incidents from pipeline_checks_public where check_key = 'a_b'")
    assert [i["subject"] for i in cur.fetchone()[0]] == ["recent"]


def test_the_six_old_columns_stay_first(cur):
    """CREATE OR REPLACE VIEW may only append, and the old page reads the first six."""
    cur.execute("select * from pipeline_checks_public limit 0")
    assert [d.name for d in cur.description] == [
        "check_key", "run_at", "status", "value", "details", "created_at",
        "label", "unit", "domain", "class", "host", "cadence_s", "owner_path", "sort",
        "declared_at", "last_measured_at", "effective_status", "incidents",
    ]


def test_the_registry_drives_one_newest_row_probe_per_key(cur):
    """A DISTINCT ON over the results would stay correct and silently read the whole table again."""
    cur.execute("select pg_get_viewdef('public.pipeline_checks_public'::regclass, true)")
    body = cur.fetchone()[0].lower()
    assert "health_checks" in body and "lateral" in body
    assert "distinct on" not in body
