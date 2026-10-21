"""Migration 644's rails, offline: the condition conjunct takes the furnished / ownership shape and nothing else moves."""
# What only the text can show: each restated body is 640's with exactly one conjunct swapped; the swapped
# conjunct spells the registry's condition canon; the parameter lists (so the ACLs) are 640's; the file is
# ONE transaction gated on the live bodies before and checked after; nothing is dropped.

from __future__ import annotations

import re

from tests.migration_defs import latest_definition
from tests.test_portal_exclusion_migration import _params
from tests.test_read_model_rewrite_migration import _md5, _read
from toolkit.filter_registry import COLUMN_CANONICAL_VALUES, UNKNOWN_FILTER_VALUE

NAME = "644_condition_unknown_filter.sql"
SQL = _read(NAME)
CODE = re.sub(r"--[^\n]*", "", SQL)
SOURCE = _read("640_drop_property_ids_excluded.sql")
FNS = ("browse_stats_properties", "browse_map_cells")

# The live bodies this file was written against (pg_proc, 2026-10-20): 640's.
LIVE = {"browse_stats_properties": "d2b76d866e64e288a19ff67e169627a3",
        "browse_map_cells": "76bda2c40ed8b9bd5abfe6bcf2d9ec67"}

_OLD = ("      and (condition_match_filter is null or array_length(condition_match_filter, 1) is null "
        "or l.condition = any(condition_match_filter))\n")
_CANON = ",".join(f"'{v}'" for v in COLUMN_CANONICAL_VALUES["condition"])
_NEW = ("      and (\n"
        "        condition_match_filter is null or array_length(condition_match_filter, 1) is null\n"
        "        or l.condition = any(condition_match_filter)\n"
        f"        or ('{UNKNOWN_FILTER_VALUE}' = any(condition_match_filter)\n"
        f"            and (l.condition is null or not (l.condition = any(array[{_CANON}]))))\n"
        "      )\n")


def _statement(text: str, fn: str) -> str:
    at = text.index(f"create or replace function public.{fn}(")
    return text[at: text.index("$function$;", at) + len("$function$;")]


def _body(stmt: str) -> str:
    return re.search(r"\$function\$(.*?)\$function\$", stmt, re.S).group(1)


def _block(text: str, tag: str) -> str:
    at = text.index(f"do ${tag}$")
    return text[at: text.index(f"${tag}$;", at)]


def test_the_live_bodies_are_640s() -> None:
    for fn in FNS:
        assert _md5(_body(_statement(SOURCE, fn))) == LIVE[fn], fn


def test_each_rpc_is_640s_with_the_condition_conjunct_swapped() -> None:
    for fn in FNS:
        old, new = _statement(SOURCE, fn), _statement(SQL, fn)
        assert old.count(_OLD) == 1, fn
        assert new == old.replace(_OLD, _NEW, 1), fn


def test_the_swapped_conjunct_is_furnisheds_shape_over_the_condition_canon() -> None:
    furnished = re.search(r"      and \(\n        furnished_filter.*?\n      \)\n", _statement(SOURCE, FNS[0]), re.S)
    assert furnished is not None
    shape = (furnished.group(0).replace("furnished_filter", "condition_match_filter")
             .replace("l.furnished", "l.condition").replace("'ano','ne','castecne'", _CANON))
    assert shape == _NEW
    assert UNKNOWN_FILTER_VALUE not in COLUMN_CANONICAL_VALUES["condition"]


def test_the_signatures_and_so_the_acls_are_640s() -> None:
    for fn in FNS:
        assert _params(SQL, fn) == _params(SOURCE, fn), fn
    assert not re.search(r"\b(drop|grant|revoke|alter)\b", re.sub(r"\$function\$.*?\$function\$", "", CODE, flags=re.S), re.I)


def test_one_transaction_gated_before_and_checked_after() -> None:
    at = CODE.index
    assert (at("set lock_timeout = '5s';") < at("begin;") < at("do $pre$")
            < at("create or replace function public.browse_stats_properties(")
            < at("create or replace function public.browse_map_cells(") < at("do $post$") < at("commit;"))
    pre, post = _block(SQL, "pre"), _block(SQL, "post")
    for fn in FNS:
        new_md5 = _md5(_body(_statement(SQL, fn)))
        gate = re.search(rf"p\.proname = '{fn}'\)\s+not in \('(\w+)', '(\w+)'\)", pre)
        assert gate and gate.groups() == (LIVE[fn], new_md5), f"{fn}: the live body or a re-run"
        assert f"is distinct from '{new_md5}'" in post and LIVE[fn] not in post
    for role in ("anon", "authenticated", "service_role"):
        assert f"has_function_privilege('{role}', fid, 'execute')" in post, role


def test_these_are_the_latest_definitions() -> None:
    for fn in FNS:
        assert latest_definition(fn).name == NAME, fn


def test_the_receipt_probes_the_two_functions() -> None:
    from scripts.migration_objects import parse_objects

    assert [str(o) for o in parse_objects(SQL)] == [
        "function:public.browse_stats_properties", "function:public.browse_map_cells"]
