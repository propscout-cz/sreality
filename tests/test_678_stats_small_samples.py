"""Migration 678: a Stats cell under the floor carries its small-sample summary. Offline, text only.

The pivot is 662's statement with exactly two edits and nothing else moved: the `cells` aggregate also takes
min(v) / max(v), and each cell's jsonb gains `small` = {min, max, mean, median} under the floor, null at or
above it. Every existing key keeps its meaning (mean / median / p* stay null under the floor). Same signature,
so no drop; the ACL is restated as 662 states it. What the cells answer is tests/test_stats_pivot_live.py's.
"""

from __future__ import annotations

import hashlib
import re

from tests.migration_defs import MIGRATIONS, latest_definition

NAME = "678_stats_small_samples.sql"
SQL = (MIGRATIONS / NAME).read_text(encoding="utf-8")
CODE = re.sub(r"--[^\n]*", "", SQL)
S662 = (MIGRATIONS / "662_status_filter_contract.sql").read_text(encoding="utf-8")
FN = "browse_stats_properties"
LIVE_662 = "2cc02ff4a70079bc91695eb6358ce973"  # 662's own $post$ gate: the body it left live

EDIT_AGGREGATE = (
    "           count(*)::int as n, avg(v) as mean,\n",
    "           count(*)::int as n, avg(v) as mean, min(v) as v_min, max(v) as v_max,\n",
)
EDIT_CELL = (
    "        'p90',    case when u.n >= v_min_n then round(u.q[5]::numeric, 2) end\n",
    "        'p90',    case when u.n >= v_min_n then round(u.q[5]::numeric, 2) end,\n"
    "        -- migration 678: under the floor, the small-sample summary only (never a percentile).\n"
    "        'small',  case when u.n < v_min_n then jsonb_build_object(\n"
    "                    'min', round(u.v_min::numeric, 2), 'max', round(u.v_max::numeric, 2),\n"
    "                    'mean', round(u.mean::numeric, 2), 'median', round(u.q[3]::numeric, 2)) end\n",
)


def _statement(text: str) -> str:
    at = text.index(f"create or replace function public.{FN}(")
    return text[at: text.index("$function$;", at) + len("$function$;")]


def _md5(statement: str) -> str:
    """pg_proc.prosrc: the whole dollar-quoted string, its first and last newline included."""
    return hashlib.md5(statement.split("$function$")[1].encode()).hexdigest()


def _block(tag: str) -> str:
    at = SQL.index(f"do ${tag}$")
    return SQL[at: SQL.index(f"${tag}$;", at)]


def _acl(text: str) -> list[str]:
    return [ln for ln in text.splitlines()
            if ln.startswith((f"revoke all on function public.{FN}(", f"grant execute on function public.{FN}("))]


def test_it_is_the_latest_definition_and_restates_the_pivot_once() -> None:
    assert latest_definition(FN).name == NAME
    assert SQL.count(f"create or replace function public.{FN}(") == 1
    assert CODE.count("create or replace function") == 1


def test_the_pivot_is_662s_with_exactly_the_two_edits() -> None:
    old = _statement(S662)
    for before, _ in (EDIT_AGGREGATE, EDIT_CELL):
        assert old.count(before) == 1, before
    expected = old.replace(*EDIT_AGGREGATE).replace(*EDIT_CELL)
    assert _statement(SQL) == expected
    assert _statement(SQL) != old


def test_every_existing_cell_key_keeps_its_floor() -> None:
    body = _statement(SQL).split("$function$")[1]
    assert "v_min_n     constant int := 5;" in body
    assert body.count("case when u.n >= v_min_n then") == 6
    for key, expr in (("mean", "u.mean"), ("median", "u.q[3]"), ("p10", "u.q[1]"), ("p25", "u.q[2]"),
                      ("p75", "u.q[4]"), ("p90", "u.q[5]")):
        assert re.search(rf"'{key}',\s+case when u\.n >= v_min_n then round\({re.escape(expr)}::numeric, 2\) end",
                         body), key
    assert body.count("case when u.n < v_min_n then") == 1
    small = body[body.index("'small',"): body.index(") order by u.g desc")]
    assert "u.q[1]" not in small and "u.q[2]" not in small and "u.q[4]" not in small and "u.q[5]" not in small


def test_the_apply_is_gated_on_662s_live_body_or_this_files_own() -> None:
    assert _md5(_statement(S662)) == LIVE_662
    assert f"is distinct from '{LIVE_662}'" in S662  # 662's post-condition left exactly this body live
    own = _md5(_statement(SQL))
    assert own == "7d506193953da1877f1acfbf5871d362"
    pre = _block("pre")
    assert (f"p.proname = '{FN}'), '')\n     not in ('{LIVE_662}', '{own}')") in pre
    assert f"678 refused: {FN} is not 662''s body nor this file''s" in pre
    post = _block("post")
    assert f"p.proname = '{FN}')\n     is distinct from '{own}'" in post
    assert "p.prosecdef" in post
    for role in ("anon", "authenticated", "service_role"):
        assert f"has_function_privilege('{role}', fid, 'execute')" in post, role


def test_same_signature_no_drop_and_the_acl_restated_as_662_states_it() -> None:
    sig = re.compile(rf"create or replace function public\.{FN}\((.*?)\)\nreturns jsonb", re.S)
    assert sig.search(SQL).group(1) == sig.search(S662).group(1)
    assert "language plpgsql\nstable\nset plan_cache_mode to 'force_custom_plan'" in _statement(SQL)
    assert "drop function" not in CODE and "cascade" not in CODE.lower()
    assert _acl(SQL) == _acl(S662) and len(_acl(SQL)) == 2
    at = CODE.index
    assert (at("set lock_timeout = '5s';") < at("set statement_timeout = '300s';") < at("begin;")
            < at("do $pre$") < at(f"create or replace function public.{FN}(") < at("revoke all on function")
            < at("grant execute on function") < at("do $post$") < at("notify pgrst, 'reload schema';")
            < at("commit;"))


def test_the_receipt_probes_the_function() -> None:
    from scripts.migration_objects import parse_objects

    assert [str(o) for o in parse_objects(SQL)] == [f"function:public.{FN}"]
