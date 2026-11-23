"""Every served matview stays CONCURRENTLY-refreshable (Broker Unify W1).

The structural precondition of the migration-578 chokepoint: REFRESH ... CONCURRENTLY
needs one unique, predicate-free, expression-free index per matview. Without one a
matview could only be published by the blocking form, the class 578 exists to end.

Lane: migrations, with `DB_RAILS_REQUIRED=1` (the test_derived_artifacts_stamping
idiom) so a lane that loses `TEST_DATABASE_URL` goes RED instead of skipping green.
"""

from __future__ import annotations

import os

import pytest

_DB_URL = os.environ.get("TEST_DATABASE_URL")
_REQUIRED = os.environ.get("DB_RAILS_REQUIRED") == "1"

pytestmark = pytest.mark.skipif(
    not _DB_URL and not _REQUIRED,
    reason="TEST_DATABASE_URL not set — this rail runs in CI's migrations lane",
)


@pytest.fixture(scope="module")
def conn():
    if not _DB_URL:
        pytest.fail(
            "DB_RAILS_REQUIRED=1 but TEST_DATABASE_URL is not set — the migrations "
            "lane is misconfigured and this rail would otherwise have skipped green."
        )
    import psycopg

    with psycopg.connect(_DB_URL, autocommit=True) as c:
        yield c


def test_every_matview_is_concurrently_refreshable(conn):
    """RED by: creating a matview without a unique index, or making the only unique
    index partial or expression-based. Any of those makes REFRESH ... CONCURRENTLY
    impossible, so the matview could only ever be published by the blocking form —
    the class migration 578 exists to end."""
    with conn.cursor() as cur:
        cur.execute(
            """
            select m.matviewname
              from pg_matviews m
             where m.schemaname = 'public'
               and not exists (
                    select 1
                      from pg_index i
                      join pg_class c on c.oid = i.indexrelid
                      join pg_class t on t.oid = i.indrelid
                      join pg_namespace n on n.oid = t.relnamespace
                     where n.nspname = 'public'
                       and t.relname = m.matviewname
                       and i.indisunique
                       and i.indpred is null
                       and i.indexprs is null
               )
             order by 1
            """
        )
        offenders = [r[0] for r in cur.fetchall()]
    assert not offenders, (
        f"matview(s) with no plain unique index, i.e. not CONCURRENTLY-refreshable: "
        f"{offenders}. Add a unique index over the natural key in the same migration."
    )
