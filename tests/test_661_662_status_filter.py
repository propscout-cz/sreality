"""Migrations 661 + 662: the Status filter reaches Stats and the map as ONE parameter (expand, then contract). Offline.

661 (expand) is 657's / 648's statements with one trailing `status_filter text default null` and the status read as
`coalesce(status_filter, <the two booleans>)`, so every caller of the old signature keeps its answer. 662
(contract) is 661's without the two booleans and the coalesce: `status_filter text default 'any'`, read as it is.
Both drop the old signature by its exact types, restate the ACL and gate on the body before and after. What the
RPCs answer per status is executed by tests/test_portal_rule.py and tests/test_stats_pivot_live.py.
"""

from __future__ import annotations

import re

from tests.migration_defs import latest_definition
from tests.test_portal_exclusion_migration import _params
from tests.test_read_model_rewrite_migration import _md5, _read

FNS = ("browse_stats_properties", "browse_map_cells")
SOURCE = {"browse_stats_properties": _read("657_status_is_the_propertys.sql"),
          "browse_map_cells": _read("648_browse_energy_rating_filter.sql")}
LIVE = {"browse_stats_properties": "ab2c1af34d972302689b935e8499425e",  # pg_proc, 2026-10-23
        "browse_map_cells": "5accb5fa40f680be14f636d0d56525be"}
EXPAND = _read("661_status_filter_expand.sql")
CONTRACT = _read("662_status_filter_contract.sql")
BOOLEANS = "case when active_only_filter then 'active' when inactive_only_filter then 'inactive' else 'any' end,"


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


def test_the_latest_definitions_are_the_contract() -> None:
    # 678 restated the Stats pivot with its small-sample summary (tests/test_678_stats_small_samples.py).
    later = {"browse_stats_properties": "678_stats_small_samples.sql"}
    for fn in FNS:
        assert latest_definition(fn).name == later.get(fn, "662_status_filter_contract.sql"), fn
    assert {fn: _md5(_body(_statement(SOURCE[fn], fn))) for fn in FNS} == LIVE


def test_expand_adds_one_trailing_parameter_and_reads_it_first() -> None:
    for fn in FNS:
        old, new = _statement(SOURCE[fn], fn), _statement(EXPAND, fn)
        assert _params(EXPAND, fn) == [*_params(SOURCE[fn], fn), ("status_filter", "text")], fn
        assert "  status_filter text default null::text\n)\nreturns jsonb" in new, fn
        expected = old.replace(BOOLEANS, f"coalesce(status_filter, {BOOLEANS[:-1]}),").replace(
            "  energy_rating_filter text[] default null::text[]\n)",
            "  energy_rating_filter text[] default null::text[],\n  status_filter text default null::text\n)")
        assert new == expected, fn


def test_contract_drops_the_two_booleans_and_reads_the_one_value() -> None:
    for fn in FNS:
        names = [n for n, _ in _params(CONTRACT, fn)]
        assert names == [n for n, _ in _params(EXPAND, fn) if n not in ("active_only_filter", "inactive_only_filter")]
        new = _statement(CONTRACT, fn)
        assert "  status_filter text default 'any'::text\n)\nreturns jsonb" in new, fn
        assert "active_only_filter" not in new and "coalesce(status_filter" not in new, fn
        assert _body(new).count("portal_filter,\n            status_filter,\n") == 1, fn


def test_each_file_drops_the_old_signature_by_its_exact_types_and_restates_the_acl() -> None:
    for text, before in ((EXPAND, SOURCE), (CONTRACT, {fn: EXPAND for fn in FNS})):
        code = re.sub(r"--[^\n]*", "", text)
        for fn in FNS:
            assert f"drop function if exists public.{fn}({_types(before[fn], fn)});" in code, fn
            assert f"revoke all on function public.{fn}({_types(text, fn)}) from public, anon;" in code, fn
            assert (f"grant execute on function public.{fn}({_types(text, fn)}) "
                    "to authenticated, service_role;") in code, fn
        assert code.index("begin;") < code.index("do $pre$") < code.index("drop function") \
            < code.index("create or replace function") < code.index("do $post$") < code.index("commit;")
        assert "notify pgrst, 'reload schema';" in code


def test_each_file_is_gated_on_the_body_before_and_checked_after() -> None:
    for text, before in ((EXPAND, {fn: _md5(_body(_statement(SOURCE[fn], fn))) for fn in FNS}),
                         (CONTRACT, {fn: _md5(_body(_statement(EXPAND, fn))) for fn in FNS})):
        pre, post = _block(text, "pre"), _block(text, "post")
        for fn in FNS:
            own = _md5(_body(_statement(text, fn)))
            assert f"p.proname = '{fn}')\n     not in ('{before[fn]}', '{own}')" in pre, fn
            assert f"p.proname = '{fn}')\n     is distinct from '{own}'" in post, fn
        assert "'status_filter' = any(p.proargnames)" in post
    assert "'active_only_filter' = any(p.proargnames)" in _block(CONTRACT, "post")
