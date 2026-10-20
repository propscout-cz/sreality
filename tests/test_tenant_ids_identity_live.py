"""Tenant ids are system-generated (migration 611), against the replayed schema.

RLS's WITH CHECK tests only account_id, so an integer id a browser role may write can be
pre-claimed just above its sequence, and every account's next insert then fails on the
primary key. GENERATED ALWAYS refuses a caller's id. Gated on TEST_DATABASE_URL like
tests/test_tenant_isolation_live.py; the tenant signs up through the real auth.users trigger.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from typing import Any

import pytest

from tests._tenant_scope import scoped

_DB_URL = os.environ.get("TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not _DB_URL,
    reason="TEST_DATABASE_URL not set — live tenant-id checks run only in the CI DB job",
)

# Every integer serial or identity key, whatever its name, that a browser role may INSERT or UPDATE
# (table- or column-level grant): whether it is GENERATED ALWAYS, and whether a browser role holds a
# grant on its sequence. A random uuid id cannot be pre-claimed, so it is out of scope.
_BROWSER_WRITABLE_KEYS_SQL = """
WITH r(role) AS (VALUES ('anon'::name), ('authenticated'::name)),
k AS (
  SELECT c.oid::regclass::text || '.' || a.attname AS col, a.attidentity = 'a' AS always,
         pg_get_serial_sequence(c.oid::regclass::text, a.attname) AS seq
    FROM pg_class c
    JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped
    LEFT JOIN pg_attrdef d ON d.adrelid = c.oid AND d.adnum = a.attnum
   WHERE c.relnamespace = 'public'::regnamespace AND c.relkind IN ('r', 'p')
     AND a.atttypid IN ('int2'::regtype, 'int4'::regtype, 'int8'::regtype)
     AND (a.attidentity <> '' OR starts_with(pg_get_expr(d.adbin, d.adrelid), 'nextval('))
     AND EXISTS (SELECT 1 FROM r WHERE has_column_privilege(r.role, c.oid, a.attnum, 'INSERT')
                                    OR has_column_privilege(r.role, c.oid, a.attnum, 'UPDATE'))
)
SELECT col, always,
       EXISTS (SELECT 1 FROM r WHERE has_sequence_privilege(r.role, seq, 'USAGE, SELECT, UPDATE'))
  FROM k ORDER BY col
"""


@pytest.fixture(scope="module")
def svc() -> "Iterator[Any]":
    import psycopg

    conn = psycopg.connect(_DB_URL, autocommit=True)
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture(scope="module")
def tenant(svc: Any) -> "Iterator[dict[str, uuid.UUID]]":
    """A user signed up through the auth.users trigger: it mints the account and seeds a board."""
    user = uuid.uuid4()
    with svc.cursor() as cur:
        cur.execute(
            "INSERT INTO auth.users (id, email) VALUES (%s, %s)",
            (user, f"ids-{user.hex[:8]}@test.local"),
        )
        cur.execute("SELECT account_id FROM account_members WHERE user_id = %s", (user,))
        acc = cur.fetchone()[0]
    try:
        yield {"user": user, "acc": acc}
    finally:
        with svc.cursor() as cur:
            cur.execute("DELETE FROM accounts WHERE id = %s", (acc,))
            cur.execute("DELETE FROM auth.users WHERE id = %s", (user,))


def test_browser_writable_keys_are_generated_always(svc: Any) -> None:
    with svc.cursor() as cur:
        cur.execute(_BROWSER_WRITABLE_KEYS_SQL)
        rows = cur.fetchall()
    assert "tags.id" in [col for col, _, _ in rows], "the census must see the tenant tables"
    assert [col for col, always, _ in rows if not always] == []
    assert [col for col, _, seq_granted in rows if seq_granted] == []


def test_tenant_cannot_choose_an_id(svc: Any, tenant: dict[str, uuid.UUID]) -> None:
    import psycopg

    with svc.cursor() as cur:
        cur.execute("SELECT coalesce(max(id), 0) + 1000 FROM tags")
        claimed = cur.fetchone()[0]
    name = f"ids-{uuid.uuid4().hex[:12]}"
    with scoped(tenant["user"]) as conn, conn.cursor() as cur:
        # The signup seeded a board with an entry stage: add a non-entry stage after it.
        cur.execute(
            "INSERT INTO pipeline_stages (account_id, key, label, position, is_entry) "
            "SELECT %s, %s, %s, coalesce(max(position), 0) + 1, false "
            "FROM pipeline_stages WHERE account_id = %s RETURNING id",
            (tenant["acc"], name, name, tenant["acc"]),
        )
        stage_id = cur.fetchone()[0]
        with pytest.raises(psycopg.errors.GeneratedAlways), conn.transaction():
            cur.execute(
                "INSERT INTO tags (id, account_id, name, color) VALUES (%s, %s, %s, 'slate')",
                (claimed, tenant["acc"], name),
            )
        with pytest.raises(psycopg.errors.GeneratedAlways), conn.transaction():
            cur.execute("UPDATE pipeline_stages SET id = %s WHERE id = %s", (claimed, stage_id))
        cur.execute(
            "INSERT INTO tags (account_id, name, color) VALUES (%s, %s, 'slate') RETURNING id",
            (tenant["acc"], name),
        )
        tag_id = cur.fetchone()[0]
        assert tag_id < claimed
        cur.execute("DELETE FROM tags WHERE id = %s", (tag_id,))
        cur.execute("DELETE FROM pipeline_stages WHERE id = %s", (stage_id,))
