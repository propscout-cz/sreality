"""The migration that lowers the Stats floor to five: offline, text only.

The pivot is restated whole (the Browse RPC rails parse the latest definition as text), so the one
thing worth pinning is that NOTHING ELSE moved: the statement is its predecessor's with the single
`v_min_n` line changed, the floor equals the registry's, the apply is gated on the predecessor's live
body or this file's own, and the ACL is left to the same-signature replace. The predecessor is the
highest-numbered definition below this file, whatever it is called, so the test survives renumbering
and later restatements by other programs.
"""

from __future__ import annotations

import hashlib
import re

from tests.migration_defs import MIGRATIONS
from toolkit import stats_registry as sr

FN = "browse_stats_properties"
_FILES = sorted(MIGRATIONS.glob("*_stats_min_n_5.sql"))
assert len(_FILES) == 1, _FILES
THIS = _FILES[0]
NUMBER = int(THIS.name.split("_", 1)[0])
SQL = THIS.read_text(encoding="utf-8")
_DEFINES = re.compile(rf"create or replace function (?:public\.)?{FN}\s*\(", re.IGNORECASE)


def _number(path) -> int:
    return int(path.name.split("_", 1)[0])


def _previous():
    below = [p for p in MIGRATIONS.glob("*.sql") if _number(p) < NUMBER and _DEFINES.search(p.read_text(encoding="utf-8"))]
    return max(below, key=_number)


PREV = _previous()
PREV_SQL = PREV.read_text(encoding="utf-8")


def _statement(text: str) -> str:
    at = text.index(f"create or replace function public.{FN}(")
    return text[at: text.index("$function$;", at) + len("$function$;")]


def _md5(statement: str) -> str:
    """pg_proc.prosrc: the whole dollar-quoted string, its first and last newline included."""
    return hashlib.md5(statement.split("$function$")[1].encode()).hexdigest()


def test_it_restates_the_pivot_once() -> None:
    assert SQL.count(f"create or replace function public.{FN}(") == 1


def test_the_floor_is_the_registrys_and_the_only_line_that_moved() -> None:
    old, new = _statement(PREV_SQL), _statement(SQL)
    assert sr.MIN_N == 5 and f"v_min_n     constant int := {sr.MIN_N};" in new
    assert new == old.replace("v_min_n     constant int := 10;", f"v_min_n     constant int := {sr.MIN_N};")
    assert new != old


def test_the_apply_is_gated_on_the_predecessors_live_body_or_this_files_own() -> None:
    gate = re.search(r"not in \('([0-9a-f]{32})', '([0-9a-f]{32})'\)", SQL)
    assert gate and gate.groups() == (_md5(_statement(PREV_SQL)), _md5(_statement(SQL)))
    assert f"raise notice '{NUMBER}: replay container" in SQL
    assert f"{NUMBER} refused: {FN} is not {_number(PREV)}''s body" in SQL


def test_the_replace_keeps_the_signature_so_the_acl_stays() -> None:
    sig = re.compile(rf"create or replace function public\.{FN}\((.*?)\)\nreturns jsonb", re.S)
    assert sig.search(SQL).group(1) == sig.search(PREV_SQL).group(1)
    assert "language plpgsql\nstable\nset plan_cache_mode to 'force_custom_plan'" in _statement(SQL)
    code = re.sub(r"--[^\n]*", "", SQL)
    assert "drop function" not in code and "revoke" not in code
    assert "grant" not in code.replace("has_function_privilege", "")


def test_the_post_condition_proves_the_floor_and_the_grant() -> None:
    post = SQL[SQL.index("do $post$"): SQL.index("$post$;", SQL.index("do $post$"))]
    assert "position('v_min_n     constant int := 5;' in src)" in post
    assert "has_function_privilege('authenticated'" in post
