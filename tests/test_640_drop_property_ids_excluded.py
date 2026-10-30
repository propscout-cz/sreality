"""Migration 640's rails, offline: the denylist parameter leaves the two aggregate RPCs and nothing else moves."""
# What only the text can show: each restated body is its source's (634 for Stats, 630 for the map
# cells) minus exactly one conjunct; each parameter list is the source's minus `property_ids_excluded`;
# the old signatures are dropped by their exact types and the new ones created inside ONE transaction,
# gated on the live bodies before and checked after; every re-created function restates its ACL; the
# revert restores the sources verbatim; nothing cascades.

from __future__ import annotations

import re

from tests.migration_defs import latest_definition
from tests.test_portal_exclusion_migration import _params, _types
from tests.test_read_model_rewrite_migration import _md5, _read

NAME = "640_drop_property_ids_excluded.sql"
SQL = _read(NAME)
CODE = re.sub(r"--[^\n]*", "", SQL)
REVERT = _read("reverts/640_revert_drop_property_ids_excluded.sql")
FNS = ("browse_stats_properties", "browse_map_cells")
SOURCE_OF = {"browse_stats_properties": "634_stats_status_lens.sql", "browse_map_cells": "630_one_browse_read_model.sql"}

# The live bodies this file was written against (pg_proc, 2026-10-20).
LIVE = {"browse_stats_properties": "d7873469c739117c31b5ced61589dc52",
        "browse_map_cells": "daf29a7a3ba7a1f76768d7b0203ec461"}

_PARAM_LINES = ("  -- migration 598: the property-id denylist (see the header).\n"
                "  property_ids_excluded bigint[] default null::bigint[],\n")
_CONJUNCT = ("      and (property_ids_excluded is null "
             "or not (l.property_id = any(property_ids_excluded)))\n")


def _statement(text: str, fn: str) -> str:
    at = text.index(f"create or replace function public.{fn}(")
    return text[at: text.index("$function$;", at) + len("$function$;")]


def _body(stmt: str) -> str:
    return re.search(r"\$function\$(.*?)\$function\$", stmt, re.S).group(1)


def _source(fn: str) -> str:
    return _statement(_read(SOURCE_OF[fn]), fn)


def _block(text: str, tag: str) -> str:
    at = text.index(f"do ${tag}$")
    return text[at: text.index(f"${tag}$;", at)]


def test_the_live_bodies_are_the_sources() -> None:
    for fn in FNS:
        assert _md5(_body(_source(fn))) == LIVE[fn], fn


def test_each_rpc_is_its_source_minus_the_parameter_and_its_one_conjunct() -> None:
    for fn in FNS:
        old, new = _source(fn), _statement(SQL, fn)
        assert old.count(_PARAM_LINES) == 1 and old.count(_CONJUNCT) == 1, fn
        assert new == old.replace(_PARAM_LINES, "", 1).replace(_CONJUNCT, "", 1), fn
        assert "property_ids_excluded" not in new, fn


def test_the_parameter_lists_are_the_sources_minus_the_denylist() -> None:
    for fn in FNS:
        old = _params(_read(SOURCE_OF[fn]), fn)
        assert ("property_ids_excluded", "bigint[]") in old, fn
        assert _params(SQL, fn) == [p for p in old if p[0] != "property_ids_excluded"], fn


def test_the_old_signatures_are_dropped_by_their_exact_types() -> None:
    for fn in FNS:
        assert f"drop function if exists public.{fn}({_types(_params(_read(SOURCE_OF[fn]), fn))});" in CODE, fn


def test_one_transaction_gated_before_and_checked_after() -> None:
    at = CODE.index
    assert (at("set lock_timeout = '5s';") < at("begin;") < at("do $pre$")
            < at("drop function if exists public.browse_stats_properties(")
            < at("drop function if exists public.browse_map_cells(")
            < at("create or replace function public.browse_stats_properties(")
            < at("create or replace function public.browse_map_cells(") < at("do $post$")
            < at("notify pgrst, 'reload schema';") < at("commit;"))
    pre, post = _block(SQL, "pre"), _block(SQL, "post")
    for fn in FNS:
        new_md5 = _md5(_body(_statement(SQL, fn)))
        gate = re.search(rf"p\.proname = '{fn}'\)\s+not in \('(\w+)', '(\w+)'\)", pre)
        assert gate and gate.groups() == (LIVE[fn], new_md5), f"{fn}: the live body or a re-run"
        assert f"is distinct from '{new_md5}'" in post and LIVE[fn] not in post
    assert "'property_ids_excluded' = any(p.proargnames)" in post


def test_every_recreated_function_restates_its_acl_and_nothing_cascades() -> None:
    code = " ".join(CODE.split())
    for fn in FNS:
        sig = _types(_params(SQL, fn))
        assert f"revoke execute on function public.{fn}({sig}) from public, anon;" in code, fn
        assert f"grant execute on function public.{fn}({sig}) to authenticated, service_role;" in code, fn
    assert "cascade" not in CODE.lower()
    outside = re.sub(r"\$function\$.*?\$function\$", "", CODE, flags=re.S)
    assert len(re.findall(r"\bdrop\s+\w+", outside, re.I)) == 2


def test_the_revert_restores_the_sources_verbatim() -> None:
    for fn in FNS:
        assert _statement(REVERT, fn) == _source(fn), fn
        assert f"drop function if exists public.{fn}({_types(_params(SQL, fn))});" in REVERT, fn
        assert f"is distinct from '{LIVE[fn]}'" in _block(REVERT, "post"), fn
    assert "SHIPPED UNAPPLIED" in REVERT


def test_these_are_the_latest_definitions() -> None:
    # 644 restated both with one conjunct swapped (tests/test_644_condition_unknown_filter.py), 648 with
    # the energy-rating parameter appended (tests/test_648_browse_energy_rating_filter.py), 657 the Stats
    # pivot without the status lens (tests/test_657_status_is_the_propertys.py).
    later = {"browse_stats_properties": "694_true_circle_map_area.sql",  # 678 the small-sample summary, 694 the circle
             "browse_map_cells": "694_true_circle_map_area.sql"}  # 657 the lens, 661/662 the status parameter, 694 the circle
    for fn in FNS:
        assert latest_definition(fn).name == later.get(fn, "648_browse_energy_rating_filter.sql"), fn


def test_the_receipt_probes_the_two_functions() -> None:
    from scripts.migration_objects import parse_objects

    assert [str(o) for o in parse_objects(SQL)] == [
        "function:public.browse_stats_properties", "function:public.browse_map_cells"]
