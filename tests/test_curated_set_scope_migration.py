"""Migration 629's rails: curated membership is judged in the database, offline."""

# What only the text can show: the two aggregate RPCs are 626's / 625's bodies plus exactly one
# clause block (right after the property_ids_excluded conjunct) and the nine scope parameters; the
# two inlinable sources keep 537's shape rails while taking the same nine plus `hide_dismissed`;
# the clause block is ONE text in all four bodies (but for the schema prefix); the old signatures
# drop by exact types and the new ones are created in ONE gated transaction; every re-created
# function restates its ACL; nothing cascades. Live behaviour: tests/test_curated_set_scope_live.py.

from __future__ import annotations

import re

from tests.migration_defs import latest_definition
from tests.test_portal_exclusion_migration import _params, _types
from tests.test_read_model_rewrite_migration import _function_bodies, _md5, _read

NAME = "629_curated_set_scope.sql"
SQL = _read(NAME)
CODE = re.sub(r"--[^\n]*", "", SQL)
RPCS = ("browse_stats_properties", "browse_map_cells")
SOURCES = ("browse_list_visible", "properties_map_visible")
SOURCE_OF = {"browse_stats_properties": "626_stats_dashboard.sql", "browse_map_cells": "625_tom_observed.sql"}

# The live bodies this file was written against (pg_proc, 2026-10-19).
LIVE = {
    "browse_stats_properties": "a69d5fb1b9f1ae2321d8952683919757",
    "browse_map_cells": "7ae1999dccd5619b634c3a92d923e2c1",
    "browse_list_visible": "47291211d34d2d14ca403dd3bbcdf176",
    "properties_map_visible": "87ac5ed4f4c1eaaef58a06c9a4e42071",
}

SCOPE = [
    ("tags_set", "text"), ("tags_in", "bigint[]"), ("tags_out", "bigint[]"),
    ("collections_set", "text"), ("collections_in", "bigint[]"), ("collections_out", "bigint[]"),
    ("pipeline_set", "text"), ("pipeline_in", "bigint[]"), ("pipeline_out", "bigint[]"),
]
SIDES = {"property_tags_public": "tag_id", "collection_properties_public": "collection_id",
         "property_pipeline_public": "stage_id"}

_DENY = ("      and (property_ids_excluded is null "
         "or not (l.property_id = any(property_ids_excluded)))\n")


def _statement(fn: str) -> str:
    at = SQL.index(f"create or replace function public.{fn}(")
    tag = "$function$;" if fn in RPCS else "$$;"
    return SQL[at: SQL.index(tag, at) + len(tag)]


def _rpc_body(fn: str) -> str:
    return re.search(r"\$function\$(.*?)\$function\$", _statement(fn), re.S).group(1)


def _source_body(fn: str) -> str:
    stmt = _statement(fn)
    return stmt[stmt.index("as $$") + len("as $$"): stmt.rindex("$$;")]


def _source_rpc_body(fn: str) -> str:
    text = _read(SOURCE_OF[fn])
    at = text.index(f"create or replace function public.{fn}(")
    return re.search(r"\$function\$(.*?)\$function\$", text[at:], re.S).group(1)


def _clause_block(body: str) -> str:
    """The RPCs' block sits between the denylist conjunct and the dismissal conjunct."""
    start = body.index(_DENY) + len(_DENY)
    return body[start: body.index("      and (not hide_dismissed or not exists (", start)]


_DISMISSAL_END = "where d.property_id = l.property_id))\n"


def _source_clause_block(body: str) -> str:
    """The sources' block is everything after their dismissal clause."""
    return body[body.index(_DISMISSAL_END) + len(_DISMISSAL_END):]


def _block(tag: str) -> str:
    at = SQL.index(f"do ${tag}$")
    return SQL[at: SQL.index(f"${tag}$;", at)]


def test_the_live_bodies_are_the_sources() -> None:
    for fn in RPCS:
        assert _md5(_source_rpc_body(fn)) == LIVE[fn], fn
    s561 = _read("561_one_property_view.sql")
    at = s561.index("create or replace function public.browse_list_visible()")
    assert _md5(s561[s561.index("as $$", at) + 5: s561.index("$$;", at)]) == LIVE["browse_list_visible"]
    s625 = _read("625_tom_observed.sql")
    at = s625.rindex("create or replace function public.properties_map_visible()")
    assert _md5(s625[s625.index("as $$", at) + 5: s625.index("$$;", at)]) == LIVE["properties_map_visible"]


def test_both_rpcs_are_the_sources_bodies_plus_the_one_clause_block() -> None:
    for fn in RPCS:
        old, new = _source_rpc_body(fn), _rpc_body(fn)
        assert old.count(_DENY) == 1 and new.count(_DENY) == 1, fn
        block = _clause_block(new)
        assert new == old.replace(_DENY, _DENY + block), fn


def test_the_clause_block_is_one_text_in_all_four_bodies() -> None:
    blocks = {fn: _clause_block(_rpc_body(fn)) for fn in RPCS}
    for fn in SOURCES:
        blocks[fn] = _source_clause_block(_source_body(fn)).replace("public.", "")
    assert len(set(blocks.values())) == 1, "the scope predicate drifted between bodies"
    block = blocks["browse_stats_properties"]
    # One clause shape per side, each gated on its own parameter.
    for name, _ in SCOPE:
        gates = block.count(f"({name} is distinct from") if name.endswith("_set") else block.count(f"({name} is null or")
        assert gates == (2 if name.endswith("_set") else 1), name
    assert block.count("is distinct from 'in' or exists (") == 3
    assert block.count("is distinct from 'out' or not exists (") == 3
    for name in ("tags_in", "collections_in", "pipeline_in"):
        assert f"({name} is null or array_length({name}, 1) is null or" in block, (
            f"{name}: '{{}}' must constrain nothing, the sibling array parameters' reading")
    for name in ("tags_out", "collections_out", "pipeline_out"):
        assert f"({name} is null or not exists (" in block
    # Tags keep the registry's AND: every listed tag, counted once.
    assert "having count(distinct c.tag_id) = (select count(distinct u) from unnest(tags_in) u)" in block
    for view in SIDES:
        assert block.count(f"from {view} c") == 4, view


def test_the_parameter_lists_are_the_sources_plus_the_scope() -> None:
    for fn in RPCS:
        assert _params(SQL, fn) == _params(_read(SOURCE_OF[fn]), fn) + SCOPE, fn
    for fn in SOURCES:
        assert _params(SQL, fn) == [("hide_dismissed", "boolean")] + SCOPE, fn
    assert "hide_dismissed boolean default true" in _statement("browse_list_visible")
    assert "hide_dismissed boolean default true" in _statement("properties_map_visible")


def test_the_sources_keep_537s_inlinability_rails() -> None:
    for fn, relation in (("browse_list_visible", "public.browse_list"),
                         ("properties_map_visible", "public.properties_map_mv")):
        stmt = _statement(fn)
        assert "returns setof public.browse_projection\nlanguage sql\nstable\nas $$" in stmt
        assert f"select l.* from {relation} l" in stmt
        assert ("(not hide_dismissed or not exists (\n        select 1 from "
                "public.property_dismissals_public d where d.property_id = l.property_id))") in stmt
        for defeat in ("security definer", "set search_path", "set plan_cache_mode", "plpgsql"):
            assert defeat not in stmt.lower(), f"{fn}: `{defeat}` stops inlining"
        assert not re.search(r"returns setof (public\.)?(browse_list|properties_map_mv)\b", stmt)


def test_the_old_signatures_are_dropped_by_their_exact_types() -> None:
    assert "drop function if exists public.browse_list_visible();" in CODE
    assert "drop function if exists public.properties_map_visible();" in CODE
    for fn in RPCS:
        assert f"drop function if exists public.{fn}({_types(_params(_read(SOURCE_OF[fn]), fn))});" in CODE


def test_one_transaction_gated_before_and_checked_after() -> None:
    at = CODE.index
    assert (at("set lock_timeout = '5s';") < at("begin;") < at("do $pre$")
            < at("drop function if exists public.browse_list_visible();")
            < at("drop function if exists public.browse_stats_properties(")
            < at("create or replace function public.browse_list_visible(")
            < at("create or replace function public.properties_map_visible(")
            < at("create or replace function public.browse_stats_properties(")
            < at("create or replace function public.browse_map_cells(") < at("do $post$")
            < at("notify pgrst, 'reload schema';") < at("commit;"))
    pre, post = _block("pre"), _block("post")
    new_md5 = {fn: _md5(_rpc_body(fn)) for fn in RPCS}
    new_md5.update({fn: _md5(_source_body(fn)) for fn in SOURCES})
    for fn in (*SOURCES, *RPCS):
        gate = re.search(rf"p\.proname = '{fn}'\)\s+not in \('(\w+)', '(\w+)'\)", pre)
        assert gate and gate.groups() == (LIVE[fn], new_md5[fn]), f"{fn}: the live body or a re-run"
        assert f"is distinct from '{new_md5[fn]}'" in post and LIVE[fn] not in post
    assert "p.proargnames[p.pronargs::int] = 'pipeline_out'" in post
    assert "p.proargnames[p.pronargs::int - 8] = 'tags_set'" in post


def test_every_recreated_function_restates_its_acl_and_nothing_cascades() -> None:
    code = " ".join(CODE.split())
    for fn in (*SOURCES, *RPCS):
        sig = _types(_params(SQL, fn))
        assert f"revoke execute on function public.{fn}({sig}) from public, anon;" in code, fn
        assert f"grant execute on function public.{fn}({sig}) to authenticated, service_role;" in code, fn
    assert "cascade" not in CODE.lower()
    outside = re.sub(r"\$function\$.*?\$function\$|\$\$.*?\$\$", "", CODE, flags=re.S)
    assert len(re.findall(r"\bdrop\s+\w+", outside, re.I)) == 4
    assert "portal_status_matches" not in outside


# 632 restated the Stats pivot with its floor at five, 634 with its status fact on the cohort's own portal
# rule (each pinned as 'nothing else moved'), so the pivot's latest definition is 634's; the rest is 629's.
_LATER = {
    "browse_stats_properties": "634_stats_status_lens.sql",
    "browse_map_cells": "630_one_browse_read_model.sql",
}


def test_these_are_the_latest_definitions() -> None:
    for fn in (*SOURCES, *RPCS):
        assert latest_definition(fn).name == _LATER.get(fn, NAME), fn


def test_the_receipt_probes_the_four_functions() -> None:
    from scripts.migration_objects import parse_objects

    assert [str(o) for o in parse_objects(SQL)] == [
        "function:public.browse_list_visible", "function:public.properties_map_visible",
        "function:public.browse_stats_properties", "function:public.browse_map_cells"]
