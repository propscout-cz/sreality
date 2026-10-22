"""Migration 621: the poller's production-fact columns and the signature engine's incidents retired.

The retirement is a predicate, so the tests run the file's own statements (not copies) against the replayed schema,
inside a transaction that always rolls back. Runs in CI's migrations lane (`TEST_DATABASE_URL`,
`DB_RAILS_REQUIRED=1`); skipped locally.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

_DB_URL = os.environ.get("TEST_DATABASE_URL")
_REQUIRED = os.environ.get("DB_RAILS_REQUIRED") == "1"
_MIGRATION = Path(__file__).resolve().parents[1] / "migrations" / "621_platform_checks.sql"

pytestmark = pytest.mark.skipif(
    not _DB_URL and not _REQUIRED,
    reason="TEST_DATABASE_URL not set — this rail runs in CI's migrations lane",
)

_TABLES = "array['public.workflow_failures', 'public.workflow_run_health', 'public.ops_incidents']::regclass[]"


@pytest.fixture()
def cur():
    """One transaction per test, always rolled back: the retirement closes real rows."""
    if not _DB_URL:
        pytest.fail("DB_RAILS_REQUIRED=1 but TEST_DATABASE_URL is not set — the migrations lane is "
                    "misconfigured and this rail would otherwise have skipped green.")
    import psycopg

    conn = psycopg.connect(_DB_URL)
    try:
        with conn.cursor() as c:
            yield c
    finally:
        conn.rollback()
        conn.close()


def _statement(start: str, end: str) -> str:
    """One statement of migration 621, exactly as the file runs it."""
    sql = _MIGRATION.read_text(encoding="utf-8")
    head = sql.index(start)
    return sql[head:sql.index(end, head) + len(end)]


_RETIRE = _statement("update public.ops_incidents", ";")
_POST = _statement("do $post$", "$post$;")


def _incident(cur: Any, signature: str, *, resolved: str | None = None) -> int:
    """An incident, open unless `resolved` names its close reason (closed three days ago)."""
    cur.execute("insert into ops_incidents (signature, resolved_at, resolve_reason) "
                "values (%s, case when %s::text is not null then now() - interval '3 days' end, %s) returning id",
                (signature, resolved, resolved))
    return cur.fetchone()[0]


def _state(cur: Any, incident_id: int) -> tuple[Any, ...]:
    """(open, resolve_reason, closed before this transaction began) of one incident."""
    cur.execute("select resolved_at is null, resolve_reason, resolved_at < now() from ops_incidents where id = %s",
                (incident_id,))
    return cur.fetchone()


def test_the_poller_columns_exist_nullable_with_no_default(cur):
    """NULL is a fact here (a pre-wave-3 row, a workflow not seen yet): a default would forge one."""
    cur.execute("select c.relname || '.' || a.attname, format_type(a.atttypid, a.atttypmod), a.attnotnull, a.atthasdef "
                "  from pg_attribute a join pg_class c on c.oid = a.attrelid "
                " where c.oid in ('public.workflow_failures'::regclass, 'public.workflow_run_health'::regclass) "
                "   and a.attname in ('reason', 'event', 'head_branch', 'state', 'on_main', 'recent_starts') "
                "   and not a.attisdropped")
    columns = {name: (typ, notnull, hasdef) for name, typ, notnull, hasdef in cur.fetchall()}
    assert columns == {
        "workflow_failures.reason": ("text", False, False),
        "workflow_failures.event": ("text", False, False),
        "workflow_failures.head_branch": ("text", False, False),
        "workflow_run_health.state": ("text", False, False),
        "workflow_run_health.on_main": ("boolean", False, False),
        "workflow_run_health.recent_starts": ("timestamp with time zone[]", False, False),
    }


def test_the_retirement_closes_signature_rows_and_leaves_cell_rows_open(cur):
    legacy = _incident(cur, "checkviolation|listings_area_basis_check")
    cell = _incident(cur, "cell:jobs:gh/detail_drain")
    closed = _incident(cur, "timeout|statement_timeout", resolved="max_age")

    cur.execute(_RETIRE)

    assert _state(cur, legacy) == (False, "retired: signature engine", False)
    assert _state(cur, cell) == (True, None, None)
    assert _state(cur, closed) == (False, "max_age", True)
    cur.execute(_RETIRE)
    assert cur.rowcount == 0
    cur.execute(_POST)


def test_the_post_condition_refuses_a_surviving_signature_row(cur):
    """The guard bites: an open signature row the retirement did not close stops the file instead of applying green."""
    import psycopg

    cur.execute(_RETIRE)
    _incident(cur, "checkviolation|listings_area_basis_check")
    with pytest.raises(psycopg.errors.RaiseException, match="621: an open incident outside the cell namespace"):
        with cur.connection.transaction():
            cur.execute(_POST)


def test_no_browser_role_reads_or_writes_the_three_tables(cur):
    cur.execute("select t.rel::text, r.role, c.relrowsecurity, "
                "       has_any_column_privilege(r.role, t.rel::oid, 'SELECT, INSERT, UPDATE, REFERENCES') "
                "       or has_table_privilege(r.role, t.rel::oid, 'DELETE, TRUNCATE, TRIGGER') "
                f"  from unnest({_TABLES}) as t(rel) "
                "  cross join unnest(array['anon', 'authenticated']::name[]) as r(role) "
                "  join pg_class c on c.oid = t.rel")
    rows = cur.fetchall()
    assert len(rows) == 6
    assert [(rel, role) for rel, role, rls, granted in rows if not rls or granted] == []
