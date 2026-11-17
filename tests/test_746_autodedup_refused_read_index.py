"""Migration 746 (#2525): the engine's refused-set read (LEDGER_HISTORY_SQL, E41) stays provable from the partial
index's predicate. The planner uses a partial index only when the query's own clauses imply its predicate, so the
read must keep both constant predicates as constants (a bound parameter would lose the index in a generic plan)
and filter on the index's key."""

from __future__ import annotations

import re
from pathlib import Path

from autodedup import apply_sql as S

SQL = (Path(__file__).resolve().parents[1] / "migrations" / "746_autodedup_applied_merges_refused_idx.sql").read_text(
    encoding="utf-8")


def _code(text: str) -> str:
    return " ".join(re.sub(r"--[^\n]*", "", text).split()).lower()


def _index() -> tuple[str, str]:
    m = re.search(r"create index concurrently if not exists autodedup_applied_merges_refused_idx "
                  r"on autodedup\.applied_merges \((\w+)\) where (.+?);", _code(SQL))
    assert m, "746 no longer creates autodedup_applied_merges_refused_idx in the shape this test reads"
    return m.group(1), m.group(2)


def test_the_index_predicate_is_the_reads_two_constant_predicates() -> None:
    key, predicate = _index()
    read = _code(S.LEDGER_HISTORY_SQL)
    assert predicate == "outcome = 'refused' and not dry_run"
    assert "a.outcome = 'refused'" in read
    assert "not a.dry_run" in read
    assert f"a.{key} = %(generation)s::text" in read


def test_a_cancelled_build_is_moved_aside_before_the_build_and_checked_after() -> None:
    code = _code(SQL)
    assert code.index("$invalid$;") < code.index("create index concurrently")
    assert code.index("create index concurrently") < code.index("do $post$")
    assert "i.indisvalid" in code[code.index("do $post$"):]
