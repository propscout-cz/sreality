"""Migration 602's rails, offline: browse_map_cells counts by WORLD TILE (docs/design/map-counts.md).

What only the text can show: the signature is 598's verbatim (the SPA builds ONE argument object
for both aggregate RPCs, tests/test_browse_map_read_contract.py); the cohort predicate is 598's
byte for byte, in the map body AND the stats body (rule 16: one definition of "matches"); the `g`
CTE still reads properties_map_mv and projects nothing but l.lat and l.lng (the index-only scan a
plan rail pinned until 630 re-pointed the map at browse_list); the one transaction is gated on 598's live body before and
checked after; nothing is dropped, granted or cascaded; the answer is four keys and bounded by
construction. The executed half lives in tests/test_browse_map_cells_live.py.
"""

from __future__ import annotations

import re

from tests.migration_defs import latest_definition
from tests.test_portal_exclusion_migration import _params
from tests.test_read_model_rewrite_migration import _function_bodies, _md5, _read

NAME = "602_browse_map_cells_world_tiles.sql"
SQL = _read(NAME)
CODE = re.sub(r"--[^\n]*", "", SQL)
S598 = _read("598_property_ids_excluded.sql")
FN = "browse_map_cells"

# 598's map body = the live prosrc, read 2026-10-15 (one overload).
LIVE_598 = "8bf9e3adea58d433a72881386ef25df1"


def _header(text: str, fn: str) -> str:
    at = text.index(f"create or replace function public.{fn}(")
    return text[at: text.index("$function$", at)]


def _where(body: str) -> str:
    """From `where` through the predicate's closing `and true` line."""
    start = body.index("    where\n")
    end = body.index("      and true\n", start) + len("      and true\n")
    return body[start:end]


def _block(tag: str) -> str:
    at = SQL.index(f"do ${tag}$")
    return SQL[at: SQL.index(f"${tag}$;", at)]


def _body() -> str:
    bodies = _function_bodies(SQL)
    assert len(bodies) == 1
    return bodies[0]


def _target_list() -> str:
    body = _body()
    start = body.index("with g as (")
    sel = body.index("select", start) + len("select")
    return body[sel: body.index("from properties_map_mv l", sel)]


def test_the_live_body_is_598s() -> None:
    assert _md5(_function_bodies(S598)[1]) == LIVE_598


def test_the_signature_is_598s_verbatim() -> None:
    assert _header(SQL, FN) == _header(S598, FN)
    assert _params(SQL, FN) == _params(S598, FN)
    assert len(_params(SQL, FN)) == 74


def test_the_cohort_predicate_is_598s_byte_for_byte_in_both_rpcs() -> None:
    stats_598, map_598 = _function_bodies(S598)
    assert _where(_body()) == _where(map_598) == _where(stats_598)


def test_the_g_cte_projects_only_the_index_columns() -> None:
    target = _target_list()
    assert {m.group(1) for m in re.finditer(r"\bl\.([a-z_][a-z0-9_]*)", target)} == {"lat", "lng"}
    # The fixture's formula: level 18, the latitude clamp inside the expression.
    assert "262144" in target and "262143" in target and target.count("85.05112878") == 4


def test_one_transaction_gated_before_and_checked_after() -> None:
    at = CODE.index
    assert (at("set lock_timeout = '5s';") < at("begin;") < at("do $pre$")
            < at(f"create or replace function public.{FN}(") < at("do $post$")
            < at("notify pgrst, 'reload schema';") < at("commit;"))
    pre, post = _block("pre"), _block("post")
    gate = re.search(rf"p\.proname = '{FN}'\)\s+not in \('(\w+)', '(\w+)'\)", pre)
    assert gate and gate.groups() == (LIVE_598, _md5(_body())), "598's body or a re-run"
    assert f"is distinct from '{_md5(_body())}'" in post and LIVE_598 not in post
    assert "p.pronargs = 74" in post
    assert "p.proargnames[p.pronargs::int] = 'property_ids_excluded'" in post


def test_nothing_is_dropped_granted_or_cascaded() -> None:
    low = CODE.lower()
    for verb in ("drop ", "grant ", "revoke ", "cascade"):
        assert verb not in low, verb
    assert CODE.count("create or replace function") == 1


def test_the_answer_is_four_keys_and_bounded_by_construction() -> None:
    body = _body()
    assert "c_max_cells constant integer := 1024" in body
    assert "limit" not in body.lower()
    for key in ("'total'", "'clustered'", "'level'", "'cells'"):
        assert key in body
    for gone in ("off_grid", "cell_lat_deg", "grid_west", "c_cols", "c_rows", "v_cw"):
        assert gone not in body, gone
    # The strict threshold: at the budget the caller gets points.
    assert "if v_total <= point_budget then" in body
    # The cascade reads the level-18 groups, never the base rows again.
    assert body.count("from properties_map_mv l") == 1
    assert "unnest(v_x, v_y, v_n)" in body


def test_the_latest_definitions() -> None:
    """605 restated both RPCs with this file's map body except the chip arms
    (tests/test_one_place_predicate.py pins the swap); 625 restated 605's with one appended
    parameter and one predicate (tom_observed_only); 630 re-pointed it at browse_list. The
    Stats RPC's latest definition is pinned where it lives: tests/test_626_stats_dashboard.py."""
    assert latest_definition(FN).name == "630_one_browse_read_model.sql"


def test_the_receipt_probes_the_function() -> None:
    from scripts.migration_objects import parse_objects

    assert [str(o) for o in parse_objects(SQL)] == [f"function:public.{FN}"]
