"""Tenant ids are system-generated (migration 606), against the replayed schema.

RLS's WITH CHECK tests only account_id, so an integer id a browser role may write can be
pre-claimed just above its sequence, and every account's next insert then fails on the
primary key. GENERATED ALWAYS refuses a caller's id. Gated on TEST_DATABASE_URL like
tests/test_tenant_isolation_live.py, whose fixture and role switch this copies.
"""

from __future__ import annotations

import contextlib
import json
import os
import uuid
from collections.abc import Iterator
from typing import Any

import pytest

_DB_URL = os.environ.get("TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not _DB_URL,
    reason="TEST_DATABASE_URL not set — live tenant-id checks run only in the CI DB job",
)

# Every integer id a browser role may INSERT or UPDATE (table- or column-level grant), whether
# it is GENERATED ALWAYS, and whether a browser role holds a grant on the sequence behind it.
# A random uuid id cannot be pre-claimed, so only integer ids are in scope.
_BROWSER_WRITABLE_IDS_SQL = """
WITH r(role) AS (VALUES ('anon'::name), ('authenticated'::name)),
w AS (
  SELECT c.oid::regclass::text AS rel, a.attidentity = 'a' AS always,
         pg_get_serial_sequence(c.oid::regclass::text, 'id') AS seq
    FROM pg_class c
    JOIN pg_attribute a ON a.attrelid = c.oid AND a.attname = 'id' AND NOT a.attisdropped
   WHERE c.relnamespace = 'public'::regnamespace AND c.relkind IN ('r', 'p')
     AND a.atttypid IN ('int2'::regtype, 'int4'::regtype, 'int8'::regtype)
     AND EXISTS (SELECT 1 FROM r WHERE has_column_privilege(r.role, c.oid, a.attnum, 'INSERT')
                                    OR has_column_privilege(r.role, c.oid, a.attnum, 'UPDATE'))
)
SELECT rel, always,
       EXISTS (SELECT 1 FROM r WHERE has_sequence_privilege(r.role, seq, 'USAGE, SELECT, UPDATE'))
  FROM w ORDER BY rel
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
    """One account + auth user + membership; the signup trigger is off around the insert."""
    user = uuid.uuid4()
    with svc.cursor() as cur:
        cur.execute(
            "INSERT INTO accounts (kind, name) VALUES ('personal', %s) RETURNING id",
            (f"ids-{user.hex[:8]}",),
        )
        acc = cur.fetchone()[0]
        cur.execute("ALTER TABLE auth.users DISABLE TRIGGER USER")
        try:
            cur.execute(
                "INSERT INTO auth.users (id, email) VALUES (%s, %s)",
                (user, f"ids-{user.hex[:8]}@test.local"),
            )
        finally:
            cur.execute("ALTER TABLE auth.users ENABLE TRIGGER USER")
        cur.execute(
            "INSERT INTO account_members (account_id, user_id, role) VALUES (%s, %s, 'owner')",
            (acc, user),
        )
    try:
        yield {"user": user, "acc": acc}
    finally:
        with svc.cursor() as cur:
            cur.execute("DELETE FROM legacy_backfill_claim WHERE account_id = %s", (acc,))
            cur.execute("DELETE FROM accounts WHERE id = %s", (acc,))
            cur.execute("DELETE FROM auth.users WHERE id = %s", (user,))


@contextlib.contextmanager
def _scoped(sub: uuid.UUID) -> "Iterator[Any]":
    """One transaction scoped the way tenant_conn scopes it: SET LOCAL ROLE + JWT claims."""
    import psycopg

    conn = psycopg.connect(_DB_URL, autocommit=False)
    try:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute("SET LOCAL ROLE authenticated")
                cur.execute(
                    "SELECT set_config('request.jwt.claims', %s, true)",
                    (json.dumps({"sub": str(sub), "role": "authenticated"}),),
                )
            yield conn
    finally:
        conn.close()


def test_browser_writable_ids_are_generated_always(svc: Any) -> None:
    with svc.cursor() as cur:
        cur.execute(_BROWSER_WRITABLE_IDS_SQL)
        rows = cur.fetchall()
    assert rows, "the census must see the tenant tables (pipeline_stages, tags, ...)"
    assert [rel for rel, always, _ in rows if not always] == []
    assert [rel for rel, _, seq_granted in rows if seq_granted] == []


def test_tenant_cannot_choose_an_id(svc: Any, tenant: dict[str, uuid.UUID]) -> None:
    import psycopg

    with svc.cursor() as cur:
        cur.execute("SELECT coalesce(max(id), 0) + 1000 FROM tags")
        claimed = cur.fetchone()[0]
    name = f"ids-{uuid.uuid4().hex[:12]}"
    with _scoped(tenant["user"]) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO pipeline_stages (account_id, key, label, position) "
            "VALUES (%s, %s, %s, 1) RETURNING id",
            (tenant["acc"], name, name),
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
