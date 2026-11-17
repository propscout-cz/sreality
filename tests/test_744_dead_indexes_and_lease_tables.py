"""Migration 744 (#2514): the revert rebuilds exactly what 744 drops, #2511's index is in neither file, and the
production read-count refusal runs before any drop."""

from __future__ import annotations

import re
from pathlib import Path

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"
SQL = (MIGRATIONS / "744_dead_indexes_and_lease_tables.sql").read_text(encoding="utf-8")
REVERT = (MIGRATIONS / "reverts" / "744_revert_dead_indexes_and_lease_tables.sql").read_text(encoding="utf-8")
ORPHAN_NOMINATION_INDEX = "listings_is_active_last_seen_at_idx"


def _code(text: str) -> str:
    return " ".join(re.sub(r"--[^\n]*", "", text).split()).lower()


def _dropped_indexes() -> list[str]:
    return re.findall(r"drop index concurrently if exists public\.([a-z0-9_]+);", _code(SQL))


def _pre_block() -> str:
    code = _code(SQL)
    at = code.index("do $pre$")
    return code[at: code.index("$pre$;", at)]


def test_the_revert_rebuilds_every_index_744_drops_and_no_other():
    rebuilt = re.findall(r"create index concurrently if not exists ([a-z0-9_]+) on public\.", _code(REVERT))
    assert len(_dropped_indexes()) == 15
    assert rebuilt == _dropped_indexes()


def test_the_refusal_checks_exactly_the_indexes_744_drops():
    listed = re.search(r"dropped text\[\] := array\[(.*?)\];", _pre_block()).group(1)
    assert re.findall(r"'([a-z0-9_]+)'", listed) == _dropped_indexes()


def test_the_revert_recreates_both_lease_tables_744_drops():
    dropped = re.findall(r"drop table if exists public\.([a-z0-9_]+);", _code(SQL))
    recreated = re.findall(r"create table if not exists public\.([a-z0-9_]+) \(", _code(REVERT))
    assert dropped == recreated == ["property_maintenance_lease", "broker_resolution_lock"]


def test_the_orphan_nomination_index_is_left_to_its_own_ticket():
    assert ORPHAN_NOMINATION_INDEX not in SQL
    assert ORPHAN_NOMINATION_INDEX not in REVERT


def test_the_read_count_refusal_runs_before_any_drop():
    code = _code(SQL)
    pre_end = code.index("$pre$;")
    assert "idx_scan > 0" in _pre_block()
    assert "raise exception" in _pre_block()
    assert pre_end < code.index("drop table")
    assert pre_end < code.index("drop index")
