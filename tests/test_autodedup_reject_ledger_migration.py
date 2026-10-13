"""Shape gate for migration 596 — the real-time lane's reject ledger (PROGRAM.md E943).

Offline, no DB. The generic RLS/grant rails see every statement; this file checks what they
cannot know: the ledger stays inside schema `autodedup` (ruling D8), it is keyed by GENERATION
and pair so a fresh seed's reset and a re-decision's overwrite each touch one row, and it carries
the five values a pass must match before it may skip a pair.
"""

from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_MIGRATION = _ROOT / "migrations" / "596_autodedup_reject_ledger.sql"


def _code() -> str:
    body = "\n".join(line.split("--")[0]
                     for line in _MIGRATION.read_text(encoding="utf-8").lower().splitlines())
    return re.sub(r"\s+", " ", body)


def test_the_migration_touches_only_schema_autodedup() -> None:
    assert _MIGRATION.is_file()
    code = _code()
    for table in re.findall(r"create table if not exists (\S+)", code):
        assert table.startswith("autodedup."), table
    for table in re.findall(r"alter table (\S+)", code):
        assert table.startswith("autodedup."), table
    for table in re.findall(r" on (\S+) from", code):
        assert table.startswith("autodedup."), table


def test_it_is_additive_and_idempotent() -> None:
    code = _code()
    assert "drop " not in code and "delete from" not in code and "truncate" not in code
    assert re.findall(r"create table (\S+)", code) == ["if"]
    assert "set lock_timeout = '5s';" in code and "reset lock_timeout;" in code
    assert "set local" not in code


def test_the_ledger_is_keyed_by_generation_and_pair_with_no_other_index() -> None:
    code = _code()
    assert "create table if not exists autodedup.rt_reject_ledger (" in code
    assert "primary key (generation, listing_lo, listing_hi)" in code
    assert "create index" not in code and "create unique index" not in code


def test_it_carries_the_five_values_a_skip_must_match() -> None:
    code = _code()
    for column, kind in (("dd_lo", "text"), ("dd_hi", "text"), ("calibration_digest", "text"),
                         ("feature_version", "smallint"), ("engine_digest", "text")):
        assert f"{column} {kind} not null" in code, column
    assert "decided_at timestamptz not null default now()" in code


def test_rls_is_on_and_the_browser_roles_are_revoked() -> None:
    code = _code()
    assert "alter table autodedup.rt_reject_ledger enable row level security;" in code
    assert "revoke all on autodedup.rt_reject_ledger from anon, authenticated;" in code
    assert "create policy" not in code and "grant " not in code


def test_the_table_says_why_it_exists() -> None:
    sql = _MIGRATION.read_text(encoding="utf-8").lower()
    assert "comment on table autodedup.rt_reject_ledger is" in sql
    assert "store_floor" in sql and "re-scores" in sql
