"""Migration 629: the Stats floor is five properties. Offline, text only.

The pivot is restated whole (the Browse RPC rails parse the latest definition as text), so the
one thing worth pinning is that NOTHING ELSE moved: 629's statement is 626's with the single
`v_min_n` line changed, the floor equals the registry's, the apply is gated on 626's live body
or this file's own, and the ACL is left to the same-signature replace.
"""

from __future__ import annotations

import hashlib
import re

from tests.migration_defs import MIGRATIONS, latest_definition
from toolkit import stats_registry as sr

NAME = "629_stats_min_n_5.sql"
FN = "browse_stats_properties"
S626 = (MIGRATIONS / "626_stats_dashboard.sql").read_text(encoding="utf-8")
SQL = (MIGRATIONS / NAME).read_text(encoding="utf-8")
LIVE_626 = "a69d5fb1b9f1ae2321d8952683919757"  # pg_proc md5(prosrc), read live 2026-10-19


def _statement(text: str) -> str:
    at = text.index(f"create or replace function public.{FN}(")
    return text[at: text.index("$function$;", at) + len("$function$;")]


def _md5(text: str) -> str:
    return hashlib.md5(text.split("$function$")[1].encode()).hexdigest()


def test_it_is_the_effective_definition_and_the_only_one_in_the_file() -> None:
    assert latest_definition(FN).name == NAME
    assert SQL.count(f"create or replace function public.{FN}(") == 1


def test_the_floor_is_the_registrys_and_the_only_line_that_moved() -> None:
    old, new = _statement(S626), _statement(SQL)
    assert f"v_min_n     constant int := {sr.MIN_N};" in new and sr.MIN_N == 5
    assert new == old.replace("v_min_n     constant int := 10;", f"v_min_n     constant int := {sr.MIN_N};")
    assert new != old


def test_the_apply_is_gated_on_626s_live_body_or_this_files_own() -> None:
    assert _md5(_statement(S626)) == LIVE_626
    gate = re.search(r"not in \('([0-9a-f]{32})', '([0-9a-f]{32})'\)", SQL)
    assert gate and gate.groups() == (LIVE_626, _md5(_statement(SQL)))
    assert "raise notice '629: replay container" in SQL


def test_the_replace_keeps_the_signature_so_the_acl_stays() -> None:
    sig = re.compile(r"create or replace function public\.browse_stats_properties\((.*?)\)\nreturns jsonb", re.S)
    assert sig.search(SQL).group(1) == sig.search(S626).group(1)
    assert "language plpgsql\nstable\nset plan_cache_mode to 'force_custom_plan'" in _statement(SQL)
    code = re.sub(r"--[^\n]*", "", SQL)
    assert "drop function" not in code and "revoke" not in code and "grant" not in code.replace("has_function_privilege", "")


def test_the_post_condition_proves_the_floor_and_the_grant() -> None:
    post = SQL[SQL.index("do $post$"): SQL.index("$post$;", SQL.index("do $post$"))]
    assert "position('v_min_n     constant int := 5;' in src)" in post
    assert "has_function_privilege('authenticated'" in post
