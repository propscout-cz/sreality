"""Migration 597's rails: the two-sided portal rule, offline.

What only the text can show: every restated body is 590's plus exactly its named edits; the
parameter lists lose the dead five (six on the map) and gain the two exclusion parameters; the
old signatures are dropped and the new ones created inside ONE transaction, gated on the live
bodies before and checked after; every re-created function restates its ACL; nothing cascades.
The rule's behaviour is tests/test_portal_rule.py's (CI's migrations lane).
"""

from __future__ import annotations

import re

from tests.migration_defs import latest_definition
from tests.test_read_model_rewrite_migration import _function_bodies, _md5, _read

NAME = "597_portal_exclusion.sql"
SQL = _read(NAME)
CODE = re.sub(r"--[^\n]*", "", SQL)
S590 = _read("590_read_model_portal_rule.sql")

DEAD = ["tag_ids", "districts_context_filter", "city_index_rules", "city_proximity", "with_estimates"]
ADDED = ["portal_excluded_filter", "portal_excluded_active_only"]

_RULE_OLD = ("          public.portal_status_matches(l.is_active, l.all_sources, l.active_sources, "
             "portal_filter,\n            case when active_only_filter then 'active' "
             "when inactive_only_filter then 'inactive' else 'any' end)\n")
_RULE_NEW = _RULE_OLD[:-2] + ",\n            portal_excluded_filter, portal_excluded_active_only)\n"
_DEAD_ARMS = (
    "      and (not coalesce(with_estimates, false) or exists (\n"
    "            select 1 from property_estimates_public pe where pe.property_id = l.property_id))\n",
    "      and (tag_ids is null or array_length(tag_ids, 1) is null or l.property_id in (\n"
    "          select pt.property_id from property_tags pt where pt.tag_id = any(tag_ids)\n"
    "          group by pt.property_id having count(distinct pt.tag_id) = array_length(tag_ids, 1)))\n",
    "      and (city_index_rules is null or jsonb_array_length(city_index_rules) = 0\n"
    "           or l.obec_id = any (array(select curated_cities_matching(city_index_rules))))\n",
)
_LISTING_IDS_ARM = (
    "      -- The third id space (see the parameter's comment). browse_list's twin has\n"
    "      -- no equivalent; the map's does, and must.\n"
    "      and (listing_ids_filter is null or l.listing_id = any(listing_ids_filter))\n")


def _guard(fn: str) -> str:
    return (f"  if city_proximity is not null then\n"
            f"    raise exception using errcode = '22023',\n"
            f"      message = '{fn}: city_proximity is retired (W5, migration 436). '\n"
            f"                'Use the migration-142 near_*_min columns.';\n"
            f"  end if;\n")


def _block(tag: str) -> str:
    at = SQL.index(f"do ${tag}$")
    return SQL[at: SQL.index(f"${tag}$;", at)]


def _params(text: str, fn: str) -> list[tuple[str, str]]:
    at = re.search(rf"create or replace function public\.{fn}\(", text, re.I).end()
    depth, i = 1, at
    while depth:
        depth += {"(": 1, ")": -1}.get(text[i], 0)
        i += 1
    args = re.sub(r"--[^\n]*", "", text[at: i - 1])
    out = []
    for part in re.split(r",\s*(?=[a-z][a-z0-9_]* )", " ".join(args.split())):
        name, rest = part.split(" ", 1)
        out.append((name, re.split(r"\s+default\s+", rest, flags=re.I)[0].lower()))
    return out


def _types(params: list[tuple[str, str]]) -> str:
    return ", ".join(t for _, t in params)


def test_both_rpcs_are_590s_bodies_after_the_named_edits() -> None:
    old_stats, old_map = _function_bodies(S590)[1:]
    new_stats, new_map = _function_bodies(SQL)
    for fn, old, new, extra in (("browse_stats_properties", old_stats, new_stats, ()),
                                ("browse_map_cells", old_map, new_map, (_LISTING_IDS_ARM,))):
        guard = _guard(fn) + ("\n" if fn == "browse_map_cells" else "")
        expected = old
        for gone in (guard, *_DEAD_ARMS, *extra):
            assert expected.count(gone) == 1, (fn, gone[:50])
            expected = expected.replace(gone, "")
        assert expected.count(_RULE_OLD) == 1
        assert new == expected.replace(_RULE_OLD, _RULE_NEW), fn
        assert _md5(old) in _block("pre") and _md5(new) in _block("pre"), "a re-run is accepted"
        assert _md5(new) in _block("post")


def test_the_rule_is_590s_plus_one_conjunct_and_stays_inlinable() -> None:
    old = re.search(r"portal_status_matches\(.*?as \$\$(.*?)\$\$;", S590, re.S).group(1)
    new = re.search(r"portal_status_matches\(.*?as \$\$(.*?)\$\$;", CODE, re.S).group(1)
    assert new == old + (
        "  and not coalesce(\n"
        "    case when p_excluded_active_only then p_active_sources else p_all_sources end "
        "&& p_excluded, false)\n")
    at = CODE.index("create or replace function public.portal_status_matches(")
    stmt = " ".join(CODE[at: CODE.index("$$;", at)].split())
    assert ("p_status text, p_excluded text[] default null, p_excluded_active_only boolean "
            "default false) returns boolean language sql immutable parallel safe as $$") in stmt
    for defeat in ("security definer", " set ", "strict", "volatile", "select 1", "exists"):
        assert defeat not in stmt.lower().split("as $$")[0] + new.lower(), defeat
    assert _md5(old) in _block("pre") and _md5(new) in _block("pre") and _md5(new) in _block("post")


def test_the_parameter_lists_lose_the_dead_and_gain_the_exclusion() -> None:
    old_stats, new_stats = _params(S590, "browse_stats_properties"), _params(SQL, "browse_stats_properties")
    old_map, new_map = _params(S590, "browse_map_cells"), _params(SQL, "browse_map_cells")
    added = [("portal_excluded_filter", "text[]"), ("portal_excluded_active_only", "boolean")]
    assert new_stats == [p for p in old_stats if p[0] not in DEAD] + added
    assert new_map == [p for p in old_map if p[0] not in (*DEAD, "listing_ids_filter")] + added
    assert {n for n, _ in new_map} - {n for n, _ in new_stats} == {"point_budget"}


def test_the_old_signatures_are_dropped_by_their_exact_types() -> None:
    """IF EXISTS keeps a re-run harmless; a wrong type list would leave two overloads, which the
    post-conditions refuse (one md5 per name)."""
    for fn in ("browse_stats_properties", "browse_map_cells"):
        assert f"drop function if exists public.{fn}({_types(_params(S590, fn))});" in CODE
    assert ("drop function if exists public.portal_status_matches"
            "(boolean, text[], text[], text[], text);") in CODE


def test_one_transaction_gated_before_and_checked_after() -> None:
    at = CODE.index
    assert (at("set lock_timeout = '5s';") < at("begin;") < at("do $pre$")
            < at("drop function if exists public.browse_stats_properties(")
            < at("drop function if exists public.portal_status_matches(")
            < at("create or replace function public.portal_status_matches(")
            < at("create or replace function public.browse_stats_properties(")
            < at("create or replace function public.browse_map_cells(") < at("do $post$")
            < at("notify pgrst, 'reload schema';") < at("commit;"))
    post = " ".join(_block("post").split())
    for gone in (*DEAD, "listing_ids_filter"):
        assert f"'{gone}'" in post
    for new in ADDED:
        assert f"'{new}' = any(p.proargnames)" in post


def test_every_recreated_function_restates_its_acl_and_nothing_cascades() -> None:
    code = " ".join(CODE.split())
    sigs = {
        "portal_status_matches": "boolean, text[], text[], text[], text, text[], boolean",
        "browse_stats_properties": _types(_params(SQL, "browse_stats_properties")),
        "browse_map_cells": _types(_params(SQL, "browse_map_cells")),
    }
    for fn, sig in sigs.items():
        assert f"revoke execute on function public.{fn}({sig}) from public, anon;" in code
        assert f"grant execute on function public.{fn}({sig}) to authenticated, service_role;" in code
    assert "cascade" not in CODE.lower()
    outside = re.sub(r"\$function\$.*?\$function\$", "", CODE, flags=re.S)
    assert len(re.findall(r"\bdrop\s+\w+", outside, re.I)) == 3


def test_the_rule_is_still_this_file_and_598_builds_on_its_rpc_bodies() -> None:
    """598 re-stated both RPCs (one denylist parameter) and md5-gates its apply on these bodies;
    tests/test_property_ids_excluded_migration.py pins its edits. The rule is still this file's."""
    assert latest_definition("portal_status_matches").name == NAME
    s598 = _read("598_property_ids_excluded.sql")
    for body in _function_bodies(SQL):
        assert _md5(body) in s598


def test_the_receipt_probes_the_three_functions() -> None:
    from scripts.migration_objects import parse_objects

    assert [str(o) for o in parse_objects(SQL)] == [
        "function:public.portal_status_matches", "function:public.browse_stats_properties",
        "function:public.browse_map_cells"]
