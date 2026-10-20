"""Migration 598's rails: a property-id denylist on the two Browse aggregate RPCs, offline.

What only the text can show: both restated bodies are 597's plus exactly one conjunct, placed
right after the property_ids_filter arm; the parameter lists are 597's plus one trailing
`property_ids_excluded bigint[]`; the old signatures are dropped and the new ones created inside
ONE transaction, gated on the live bodies before and checked after; every re-created function
restates its ACL; nothing cascades and the portal rule is left alone.
"""

from __future__ import annotations

import re

from tests.migration_defs import latest_definition
from tests.test_portal_exclusion_migration import _params, _types
from tests.test_read_model_rewrite_migration import _function_bodies, _md5, _read

NAME = "598_property_ids_excluded.sql"
SQL = _read(NAME)
CODE = re.sub(r"--[^\n]*", "", SQL)
S597 = _read("597_portal_exclusion.sql")
FNS = ("browse_stats_properties", "browse_map_cells")

# The live bodies this file was written against (pg_proc, 2026-10-14): 597's.
LIVE = {"browse_stats_properties": "2f6f764d335b251707159073381fd589",
        "browse_map_cells": "61785058aef74cec02f1b95c02b24a41"}

_ALLOW = "      and (property_ids_filter is null or l.property_id = any(property_ids_filter))\n"
_DENY = ("      and (property_ids_excluded is null "
         "or not (l.property_id = any(property_ids_excluded)))\n")
_LAST_597 = "  portal_excluded_active_only boolean default false\n)\n"
_LAST_598 = ("  portal_excluded_active_only boolean default false,\n"
             "  -- migration 598: the property-id denylist (see the header).\n"
             "  property_ids_excluded bigint[] default null::bigint[]\n)\n")


def _block(tag: str) -> str:
    at = SQL.index(f"do ${tag}$")
    return SQL[at: SQL.index(f"${tag}$;", at)]


def _header(text: str, fn: str) -> str:
    at = text.index(f"create or replace function public.{fn}(")
    return text[at: text.index("$function$", at)]


def test_the_live_bodies_are_597s() -> None:
    assert dict(zip(FNS, map(_md5, _function_bodies(S597)))) == LIVE


def test_both_rpcs_are_597s_bodies_plus_the_one_conjunct() -> None:
    for fn, old, new in zip(FNS, _function_bodies(S597), _function_bodies(SQL)):
        assert old.count(_ALLOW) == 1 and new.count(_DENY) == 1, fn
        assert new == old.replace(_ALLOW, _ALLOW + _DENY), fn


def test_the_parameter_lists_are_597s_plus_the_denylist() -> None:
    for fn in FNS:
        assert _params(SQL, fn) == _params(S597, fn) + [("property_ids_excluded", "bigint[]")]
        assert _header(SQL, fn) == _header(S597, fn).replace(_LAST_597, _LAST_598), fn
    stats, cells = (_params(SQL, fn) for fn in FNS)
    assert {n for n, _ in cells} - {n for n, _ in stats} == {"point_budget"}


def test_the_old_signatures_are_dropped_by_their_exact_types() -> None:
    """IF EXISTS keeps a re-run harmless; a wrong type list would leave two overloads, which the
    post-conditions refuse (one md5 per name)."""
    for fn in FNS:
        assert f"drop function if exists public.{fn}({_types(_params(S597, fn))});" in CODE


def test_one_transaction_gated_before_and_checked_after() -> None:
    at = CODE.index
    assert (at("set lock_timeout = '5s';") < at("begin;") < at("do $pre$")
            < at("drop function if exists public.browse_stats_properties(")
            < at("drop function if exists public.browse_map_cells(")
            < at("create or replace function public.browse_stats_properties(")
            < at("create or replace function public.browse_map_cells(") < at("do $post$")
            < at("notify pgrst, 'reload schema';") < at("commit;"))
    pre, post = _block("pre"), _block("post")
    for fn, new in zip(FNS, _function_bodies(SQL)):
        gate = re.search(rf"p\.proname = '{fn}'\)\s+not in \('(\w+)', '(\w+)'\)", pre)
        assert gate and gate.groups() == (LIVE[fn], _md5(new)), f"{fn}: 597's body or a re-run"
        assert f"is distinct from '{_md5(new)}'" in post and LIVE[fn] not in post
    assert "p.proargnames[p.pronargs::int] = 'property_ids_excluded'" in post


def test_every_recreated_function_restates_its_acl_and_nothing_cascades() -> None:
    code = " ".join(CODE.split())
    for fn in FNS:
        sig = _types(_params(SQL, fn))
        assert f"revoke execute on function public.{fn}({sig}) from public, anon;" in code
        assert f"grant execute on function public.{fn}({sig}) to authenticated, service_role;" in code
    assert "cascade" not in CODE.lower()
    outside = re.sub(r"\$function\$.*?\$function\$", "", CODE, flags=re.S)
    assert len(re.findall(r"\bdrop\s+\w+", outside, re.I)) == 2
    assert "portal_status_matches" not in outside, "the portal rule is 597's, untouched"


def test_the_latest_definitions() -> None:
    """602 restated browse_map_cells (world tiles) with this file's signature and predicate
    verbatim (tests/test_602_browse_map_cells_world_tiles_migration.py); 605 restated both
    except the chip arms (tests/test_one_place_predicate.py). The Stats RPC's latest
    definition (626, the dashboard pivot, which keeps this file's denylist conjunct in 625's
    WHERE) is pinned by tests/test_626_stats_dashboard.py; 630 re-pointed it at browse_list."""
    assert latest_definition("browse_map_cells").name == "640_drop_property_ids_excluded.sql"
    assert latest_definition("portal_status_matches").name == "597_portal_exclusion.sql"


def test_the_receipt_probes_the_two_functions() -> None:
    from scripts.migration_objects import parse_objects

    assert [str(o) for o in parse_objects(SQL)] == [
        "function:public.browse_stats_properties", "function:public.browse_map_cells"]
