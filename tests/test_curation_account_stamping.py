"""Tests for account_id stamping on property_notes writes (Wave 1 W1-1).

Hermetic: property_notes has no owning-parent to derive account_id from by
trigger (unlike collection_properties/property_tags, migration 292) and no
column DEFAULT (unlike estimation_runs, migration 291) -- a tenant_conn
caller that leaves it unset fails the table's WITH CHECK closed. This pins
that create_note actually names the column in its INSERT.
"""

from __future__ import annotations

from typing import Any

import pytest

from api import curation
from api import schemas as s
from tests.api._script_conn import ScriptConn


_NOTE_ROW = (7, 99, "note body", False, "2026-07-21T00:00:00Z", None, None, None, None)


def _conn() -> ScriptConn:
    return ScriptConn([(lambda q: q.startswith("INSERT INTO property_notes"), [_NOTE_ROW])])


def test_create_note_stamps_explicit_account_id(monkeypatch: Any) -> None:
    monkeypatch.setattr(curation, "resolve_active_property_id", lambda conn, pid: pid)
    conn = _conn()
    tenant_id = "22222222-2222-2222-2222-222222222222"
    curation.create_note(
        conn, 99, s.CreateNoteIn(body="hello"), account_id=tenant_id,
    )
    sql, params = conn.executed[0]
    assert "account_id" in sql
    assert params[-1] == tenant_id


def test_create_note_without_account_id_is_a_type_error(monkeypatch: Any) -> None:
    """The sharp edge is now unreachable: account_id is REQUIRED (W4), so the
    service-role bridge that used to bind a silent NULL can't exist. An identical
    one-line revert on post_property_note raises here instead of writing a row
    that migration 290's WITH CHECK (no SYSTEM arm) would reject."""
    monkeypatch.setattr(curation, "resolve_active_property_id", lambda conn, pid: pid)
    conn = _conn()
    with pytest.raises(TypeError, match="account_id"):
        curation.create_note(conn, 99, s.CreateNoteIn(body="hello"))
