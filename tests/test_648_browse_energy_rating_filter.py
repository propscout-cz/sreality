"""Migration 648's rails, offline: both aggregate RPCs gain the energy-rating parameter and its one conjunct, nothing else moves."""
# What only the text can show: each restated body is 644's with exactly one conjunct inserted after
# building_type's; the parameter list is 644's plus one trailing defaulted `energy_rating_filter`; the old
# signatures are dropped by their exact types and the ACL restated; the file is ONE transaction gated on the
# live bodies before and checked after; the revert restores 644's statements.

from __future__ import annotations

import re

from tests.migration_defs import latest_definition
from tests.test_portal_exclusion_migration import _params
from tests.test_read_model_rewrite_migration import _md5, _read

NAME = "648_browse_energy_rating_filter.sql"
SQL = _read(NAME)
CODE = re.sub(r"--[^\n]*", "", SQL)
SOURCE = _read("644_condition_unknown_filter.sql")
REVERT = (latest_definition("browse_stats_properties").parent / "reverts" / "648_revert_browse_energy_rating_filter.sql").read_text(encoding="utf-8")
FNS = ("browse_stats_properties", "browse_map_cells")

# The live bodies this file was written against (pg_proc, 2026-10-21): 644's.
LIVE = {"browse_stats_properties": "87575669ce4d9bb837d413369be2d413",
        "browse_map_cells": "5baf84ddce380a3e709025a621927648"}

_ANCHOR = ("      and (building_type_filter   is null or array_length(building_type_filter, 1) is null "
           "or l.building_type = any(building_type_filter))\n")
_ENERGY = ("      and (energy_rating_filter   is null or array_length(energy_rating_filter, 1) is null "
           "or l.energy_rating = any(energy_rating_filter))\n")
_OLD_TAIL = "  pipeline_out bigint[] default null::bigint[]\n)\nreturns jsonb"
_NEW_TAIL = "  pipeline_out bigint[] default null::bigint[],\n  energy_rating_filter text[] default null::text[]\n)\nreturns jsonb"


def _statement(text: str, fn: str) -> str:
    at = text.index(f"create or replace function public.{fn}(")
    return text[at: text.index("$function$;", at) + len("$function$;")]


def _body(stmt: str) -> str:
    return re.search(r"\$function\$(.*?)\$function\$", stmt, re.S).group(1)


def _block(text: str, tag: str) -> str:
    at = text.index(f"do ${tag}$")
    return text[at: text.index(f"${tag}$;", at)]


def _types(text: str, fn: str) -> str:
    return ", ".join(t for _, t in _params(text, fn))


def test_the_live_bodies_are_644s() -> None:
    for fn in FNS:
        assert _md5(_body(_statement(SOURCE, fn))) == LIVE[fn], fn


def test_each_rpc_is_644s_plus_the_parameter_and_one_conjunct() -> None:
    for fn in FNS:
        old, new = _statement(SOURCE, fn), _statement(SQL, fn)
        assert old.count(_ANCHOR) == 1 and old.count(_OLD_TAIL) == 1, fn
        assert new == old.replace(_OLD_TAIL, _NEW_TAIL, 1).replace(_ANCHOR, _ANCHOR + _ENERGY, 1), fn


def test_the_signature_is_644s_plus_one_trailing_parameter() -> None:
    for fn in FNS:
        assert _params(SQL, fn) == [*_params(SOURCE, fn), ("energy_rating_filter", "text[]")], fn


def test_the_old_signature_is_dropped_and_the_acl_restated() -> None:
    for fn in FNS:
        old_types, new_types = _types(SOURCE, fn), _types(SQL, fn)
        assert new_types == old_types + ", text[]", fn
        assert f"drop function if exists public.{fn}({old_types});" in SQL, fn
        assert f"revoke all on function public.{fn}({new_types}) from public, anon;" in SQL, fn
        assert f"grant execute on function public.{fn}({new_types}) to authenticated, service_role;" in SQL, fn
    assert "notify pgrst, 'reload schema';" in SQL


def test_one_transaction_gated_before_and_checked_after() -> None:
    at = CODE.index
    assert (at("set lock_timeout = '5s';") < at("begin;") < at("do $pre$")
            < at("drop function if exists public.browse_stats_properties(")
            < at("create or replace function public.browse_stats_properties(")
            < at("create or replace function public.browse_map_cells(")
            < at("grant execute on function public.browse_map_cells(") < at("do $post$") < at("commit;"))
    pre, post = _block(SQL, "pre"), _block(SQL, "post")
    for fn in FNS:
        new_md5 = _md5(_body(_statement(SQL, fn)))
        gate = re.search(rf"p\.proname = '{fn}'\)\s+not in \('(\w+)', '(\w+)'\)", pre)
        assert gate and gate.groups() == (LIVE[fn], new_md5), f"{fn}: the live body or a re-run"
        assert f"is distinct from '{new_md5}'" in post and LIVE[fn] not in post
    assert "'energy_rating_filter' = any(p.proargnames)" in post
    for role in ("anon", "authenticated", "service_role"):
        assert f"has_function_privilege('{role}', fid, 'execute')" in post, role


def test_the_revert_restores_644s_statements() -> None:
    for fn in FNS:
        assert _statement(REVERT, fn) == _statement(SOURCE, fn), fn
        assert f"drop function if exists public.{fn}({_types(SQL, fn)});" in REVERT, fn
        assert f"grant execute on function public.{fn}({_types(SOURCE, fn)}) to authenticated, service_role;" in REVERT, fn


def test_these_are_the_latest_definitions() -> None:
    # 657 restated the Stats pivot without the status lens (tests/test_657_status_is_the_propertys.py).
    later = dict.fromkeys(FNS, "662_status_filter_contract.sql")  # 657 the lens, 661/662 the status parameter
    for fn in FNS:
        assert latest_definition(fn).name == later.get(fn, NAME), fn


def test_the_receipt_probes_the_two_functions() -> None:
    from scripts.migration_objects import parse_objects

    assert [str(o) for o in parse_objects(SQL)] == [
        "function:public.browse_stats_properties", "function:public.browse_map_cells"]
