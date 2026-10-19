"""ONE place predicate, in four places, provably the same one (W3 S3).

Browse's list, Browse's Stats, the map and the Watchdog each used to carry their
own copy of the chip predicate — six copies of five predicates. They are now one
rule, `<level>_id = any(codes)`, compiled by:

  * `api/location_filter.py`            — the API + the Watchdog matcher
  * `frontend/src/lib/districtCodes.ts` — the SPA (PostgREST string + the
                                          in-memory row predicate)
  * `browse_stats_properties`           — migration 605, the Stats cohort (626's pivot
                                          carries 625's WHERE byte for byte)
  * `browse_map_cells`                  — migration 605, the map cohort

This file holds the API half of the shared table (`tests/fixtures/
district_chip_plan.json`, read by `districtCodes.test.ts` for the SPA half) plus
the source-text rails over the two SQL bodies and over the deletion ledger.

Offline; the shipped SQL's behaviour is the DB lane's subject.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

from api.location_filter import (
    LEVEL_COLUMN,
    LEVEL_ORDER,
    DistrictChip,
    district_code_plan,
    district_where,
)
from tests.migration_defs import latest_definition

REPO = Path(__file__).resolve().parents[1]
MIGRATIONS = REPO / "migrations"
FIXTURE = REPO / "tests" / "fixtures" / "district_chip_plan.json"
TS_MODULE = REPO / "frontend" / "src" / "lib" / "districtCodes.ts"

_TABLE = json.loads(FIXTURE.read_text(encoding="utf-8"))
_CASES = _TABLE["cases"]


def _chips(case: dict) -> list[DistrictChip]:
    return [DistrictChip(**c) for c in case["chips"]]


@pytest.mark.parametrize("case", _CASES, ids=[c["name"] for c in _CASES])
def test_the_api_compiles_the_table(case: dict) -> None:
    """The plan and the SQL rendering, from the SAME chip sets the SPA is tested
    against. RED by: either side drifting from the table."""
    plan = district_code_plan(_chips(case))
    assert plan.include == {k: list(v) for k, v in case["plan"]["include"].items()}
    assert plan.exclude == {k: list(v) for k, v in case["plan"]["exclude"].items()}

    where, params = district_where(_chips(case), alias="l")
    assert where == case["sql_where"]
    assert params == {k: list(v) for k, v in case["sql_params"].items()}


def test_the_ts_module_declares_the_same_levels_and_columns() -> None:
    """The level → column map is the predicate. If the SPA spells one column
    differently the two cohorts diverge with no error anywhere — PostgREST would
    happily filter on a column that exists."""
    ts = TS_MODULE.read_text(encoding="utf-8")
    block = ts[ts.index("const DISTRICT_LEVEL_COLUMN = {"):]
    block = block[: block.index("}")]
    pairs = dict(re.findall(r"(\w+):\s*'([a-z_]+)'", block))
    assert pairs == LEVEL_COLUMN

    order_block = ts[ts.index("const DISTRICT_LEVEL_ORDER"):]
    order_block = order_block[: order_block.index("];")]
    assert tuple(re.findall(r"'([a-z_]+)'", order_block)) == LEVEL_ORDER


# --- the two RPC bodies -----------------------------------------------------

# The latest definition of both RPCs: 605 carries 598's stats body and 602's map
# body forward byte for byte except the four chip arms, where `locality` (a street
# pick filtered at its TOWN) became `ulice` (tests below); 625 appended
# tom_observed_only to both; 626 replaced the Stats body with the dashboard pivot
# over 625's WHERE verbatim (tests/test_626_stats_dashboard.py); 629 restated it with the
# floor at five and nothing else changed (tests/test_629_stats_min_n.py). Re-read the chip
# arms when this moves again.
_LATEST_RPC_DEFINITION = {
    "browse_stats_properties": "630_stats_min_n_5.sql",
    "browse_map_cells": "629_curated_set_scope.sql",
}


def _function_body(func: str) -> str:
    """Just this function's statement — 504 defines two, so a whole-file scan
    would silently mix their CASE blocks together."""
    sql = latest_definition(func).read_text(encoding="utf-8")
    at = re.search(
        rf"create or replace function (?:public\.)?{func}\s*\(", sql, re.IGNORECASE
    ).start()
    return sql[at: sql.index("$function$;", at)]


def _chip_case_arms(func: str) -> list[dict[str, str]]:
    """Every `when lvl = '<level>' then l.<column> = admin_id` arm of the chip
    CASE, one dict per CASE block (there are two: include and exclude)."""
    blocks = re.findall(
        r"and case\b(.*?)\bend\b", _function_body(func), re.IGNORECASE | re.DOTALL,
    )
    chip_blocks = [b for b in blocks if "lvl =" in b]
    assert len(chip_blocks) == 2, (
        f"{func}: expected exactly two chip CASE blocks (include + exclude), "
        f"found {len(chip_blocks)}"
    )
    return [
        dict(re.findall(r"when lvl = '([a-z_]+)'\s*then l\.([a-z_]+)\s*=", b))
        for b in chip_blocks
    ]


@pytest.mark.parametrize(
    "func,relation",
    [("browse_stats_properties", "browse_list"), ("browse_map_cells", "properties_map_mv")],
)
def test_the_stats_and_map_cohorts_inherit_the_consumer_rule(func: str, relation: str) -> None:
    """W5, operator ruling 2026-09-13. Neither RPC restates the rule that a listing is
    served only once its location is resolved or determined foreign — each reads a
    relation that is materialized FROM `browse_projection`, which carries it (migration
    512). RED by: re-pointing either body at `properties` or `listings`, which would let
    the Stats tab count a cohort Browse's own list cannot show."""
    body = _function_body(func).lower()
    assert f"from {relation} l" in body
    for legacy in ("from properties p", "from properties_public", "from listings l"):
        assert legacy not in body, f"{func} reads {legacy} — it would bypass the rule"


@pytest.mark.parametrize("func", ["browse_stats_properties", "browse_map_cells"])
def test_the_rpc_bodies_know_the_same_canonical_ownership(func: str) -> None:
    """The `__unknown__` ownership pill means "NULL or not canonical", and rule 16
    gives that predicate one definition. Browse's list compiles it from
    `OWNERSHIP_CANONICAL`; these two bodies spell the array out. RED by: widening
    the canon (W5 added `jine`) without carrying it into the SQL, which makes the
    aggregates count as unknown the rows the list beside them no longer does."""
    from toolkit.filter_registry import OWNERSHIP_CANONICAL

    arrays = re.findall(
        r"l\.ownership = any\(array\[([^\]]+)\]\)", _function_body(func)
    )
    assert arrays, f"{func} has no ownership __unknown__ predicate"
    for arr in arrays:
        assert tuple(re.findall(r"'([a-z_]+)'", arr)) == OWNERSHIP_CANONICAL


@pytest.mark.parametrize("func", ["browse_stats_properties", "browse_map_cells"])
def test_the_rpc_bodies_compile_the_same_level_map(func: str) -> None:
    """RED by: an RPC arm pointed at a different column than the API/SPA use, or
    a level served in one RPC and not the other — the Stats tab and the map
    would then answer for different cohorts under the same chips."""
    assert latest_definition(func).name == _LATEST_RPC_DEFINITION[func]
    for arms in _chip_case_arms(func):
        assert arms == LEVEL_COLUMN


@pytest.mark.parametrize("func", ["browse_stats_properties", "browse_map_cells"])
def test_the_rpc_bodies_read_no_text_column_for_a_chip(func: str) -> None:
    """The five predicates are gone: no ILIKE, no place_search_text, no context
    narrow. RED by: reintroducing any of them in either body."""
    body = "\n".join(
        line for line in _function_body(func).splitlines()
        if not line.strip().startswith("--")
    )
    for block in re.findall(r"and case\b(.*?)\bend\b", body, re.IGNORECASE | re.DOTALL):
        if "lvl =" not in block:
            continue
        low = block.lower()
        assert "ilike" not in low
        assert "place_search_text" not in low
        assert "ctx" not in low
        assert "needle" not in low


@pytest.mark.parametrize("func", ["browse_stats_properties", "browse_map_cells"])
def test_a_chip_without_a_code_matches_nothing_in_sql_too(func: str) -> None:
    """The null guard and the `else` are the SQL half of the sentinel rule: an
    include chip we could not resolve contributes nothing rather than widening
    the cohort to the whole country."""
    for block in re.findall(
        r"and case\b(.*?)\bend\b", _function_body(func), re.IGNORECASE | re.DOTALL
    ):
        if "lvl =" not in block:
            continue
        assert re.search(r"when admin_id is null\s+then false", block, re.IGNORECASE)
        assert re.search(r"else false", block, re.IGNORECASE)


def test_the_rpc_signatures_keep_the_chip_parameters() -> None:
    """The SPA builds ONE argument object for both RPCs (tests/test_browse_map_read_
    contract.py), so the chip parameters are the one place predicate's wire shape.
    Since 436/439 the lists grew 537's `hide_dismissed`, 597's two exclusion
    parameters and 598's `property_ids_excluded`, and 597 dropped the five nothing
    sent (and the map's `listing_ids_filter`) — tests/test_portal_exclusion_migration.py
    and tests/test_property_ids_excluded_migration.py pin each step. 626 appended the
    Stats pivot's chart `spec`, which shapes the answer and never the cohort; 629 the
    nine curated-set scope parameters (tests/test_curated_set_scope_migration.py)."""
    gone = {"tag_ids", "districts_context_filter", "city_index_rules", "city_proximity",
            "with_estimates", "listing_ids_filter"}
    for func, previous, own in (
        ("browse_stats_properties", "436_city_quality_obec_key.sql", ["spec"]),
        ("browse_map_cells", "439_browse_map_cells.sql", []),
    ):
        new = _params_of(latest_definition(func).read_text(encoding="utf-8"), func)
        old = _params_of((MIGRATIONS / previous).read_text(encoding="utf-8"), func)
        assert new == [p for p in old if p not in gone] + [
            "hide_dismissed", "portal_excluded_filter", "portal_excluded_active_only",
            "property_ids_excluded", "tom_observed_only", *own,
            # 629: the curated-set scope, three sets x (set, in, out), judged under RLS.
            "tags_set", "tags_in", "tags_out", "collections_set", "collections_in",
            "collections_out", "pipeline_set", "pipeline_in", "pipeline_out",
        ], f"{func}: parameter list changed"
        for chip_param in (
            "districts_filter", "districts_levels", "districts_ids", "districts_excluded_filter",
        ):
            assert chip_param in new


def _params_of(sql: str, func: str) -> list[str]:
    at = re.search(
        rf"create or replace function (?:public\.)?{func}\s*\(", sql, re.IGNORECASE
    ).end()
    depth, i = 1, at
    while depth:
        if sql[i] == "(":
            depth += 1
        elif sql[i] == ")":
            depth -= 1
        i += 1
    args = re.sub(r"--[^\n]*", "", sql[at: i - 1])
    return re.findall(r"(?:^|,)\s*([a-z][a-z0-9_]*)\s+[a-z]", args)


def _latest_view_definition(view: str) -> str:
    pat = re.compile(rf"create\s+(or\s+replace\s+)?view\s+(public\.)?{view}\b", re.IGNORECASE)
    hits = [p for p in MIGRATIONS.glob("*.sql") if pat.search(p.read_text(encoding="utf-8"))]
    return max(hits, key=lambda p: int(p.name.split("_", 1)[0])).name


@pytest.mark.parametrize(
    "view", ["browse_projection", "properties_public", "pipeline_board_public", "listing_feed_public"],
)
def test_every_level_column_exists_on_every_chip_filtered_relation(view: str) -> None:
    """A level is only a filter where the relation publishes its column: browse_list and
    properties_map_mv inherit browse_projection's, properties_public is the Watchdog's,
    pipeline_board_public the board's in-memory copy, listing_feed_public the portal-mirror
    lane's. RED by: a re-definition that drops one, or a level with no served column."""
    from tests.test_location_w3_projection import _columns, _sql

    columns = _columns(_sql(_latest_view_definition(view)), view)
    assert set(LEVEL_COLUMN.values()) <= set(columns)


# --- migration 605: the street arm, and 606: the rows without codes ---------

_S605 = "605_street_chip_level.sql"
_LOCALITY_ARM = (
    "              -- a street / POI / address pick carries its containing obec code\n"
    "              when lvl = 'locality'                  then l.obec_id      = admin_id\n"
)
_ULICE_ARM = "              when lvl = 'ulice'                     then l.ulice_id     = admin_id\n"
_ULICE_INDEX = (
    "  execute 'create index browse_list_next_ulice_idx on browse_list_next (ulice_id) "
    "where ulice_id is not null';\n"
)
_ULICE_RENAME = "  execute 'alter index browse_list_next_ulice_idx rename to browse_list_ulice_idx';\n"
_SOURCES = {
    "browse_stats_properties": "598_property_ids_excluded.sql",
    "browse_map_cells": "602_browse_map_cells_world_tiles.sql",
    "rebuild_browse_list": "590_read_model_portal_rule.sql",
}


def _statement(name: str, func: str) -> str:
    sql = (MIGRATIONS / name).read_text(encoding="utf-8")
    at = re.search(rf"create or replace function public\.{func}\s*\(", sql).start()
    return sql[at: sql.index("$function$;", at)]


def _md5_of_body(statement: str) -> str:
    return hashlib.md5(statement.split("$function$")[1].encode()).hexdigest()


@pytest.mark.parametrize("func", ["browse_stats_properties", "browse_map_cells"])
def test_605_restates_each_rpc_with_only_its_two_chip_arms_swapped(func: str) -> None:
    old = _statement(_SOURCES[func], func)
    assert old.count(_LOCALITY_ARM) == 2
    assert _statement(_S605, func) == old.replace(_LOCALITY_ARM, _ULICE_ARM)


def test_605_rebuilds_browse_list_with_590s_body_plus_the_street_index() -> None:
    new = _statement(_S605, "rebuild_browse_list")
    assert new.count(_ULICE_INDEX) == new.count(_ULICE_RENAME) == 1
    assert new.replace(_ULICE_INDEX, "").replace(_ULICE_RENAME, "") == _statement(
        _SOURCES["rebuild_browse_list"], "rebuild_browse_list")
    assert new.index(_ULICE_INDEX) < new.index("analyze browse_list_next") < new.index(_ULICE_RENAME)


def test_605_preconditions_skip_the_replay_and_name_the_live_bodies_and_its_own() -> None:
    sql = (MIGRATIONS / _S605).read_text(encoding="utf-8")
    pre = sql[sql.index("do $pre$"): sql.index("$pre$;")]
    assert pre.index("into populated") < pre.index("return;") < pre.index("pg_proc")
    for func, source in _SOURCES.items():
        assert _md5_of_body(_statement(source, func)) in pre, f"{func}: not the live body"
        assert _md5_of_body(_statement(_S605, func)) in pre, f"{func}: a re-run must pass"


def test_605_builds_the_feed_index_concurrently_outside_the_transaction() -> None:
    code = re.sub(r"--[^\n]*", "", (MIGRATIONS / _S605).read_text(encoding="utf-8"))
    at = code.index
    assert code.count("begin;") == code.count("commit;") == 1
    assert (at("do $guard$") < at("begin;") < at("commit;") < at("do $invalid$")
            < at("set lock_timeout = 0;\nset statement_timeout = '1900s';")
            < at("drop index concurrently if exists public.listing_location_ulice_kod_idx_invalid;")
            < at("create index concurrently if not exists listing_location_ulice_kod_idx")
            < at("do $post$") < at("select pg_notify('pgrst', 'reload schema');"))
    invalid = code[at("do $invalid$"): at("$invalid$;")]
    assert "rename to listing_location_ulice_kod_idx_invalid" in invalid and "drop index" not in invalid


def test_606_backs_up_every_row_it_deletes_in_the_same_transaction() -> None:
    code = re.sub(r"--[^\n]*", "", (MIGRATIONS / "606_chip_rows_without_codes_deleted.sql")
                  .read_text(encoding="utf-8"))
    at = code.index
    post = " ".join(code[at("do $post$"):].split())
    assert ("(case when jsonb_typeof(p.filter_spec->'filters') = 'object' then p.filter_spec->'filters' "
            "else p.filter_spec end)->'districts'") in post, "a bare legacy preset escapes the guard"
    assert (at("begin;") < at("create table if not exists backup_606.filter_presets")
            < at("create table if not exists backup_606.notification_dispatches") < at("do $gate$")
            < at("delete from public.filter_presets") < at("delete from public.notification_subscriptions")
            < at("do $post$") < at("commit;"))
    assert len(set(re.findall(r"'([0-9a-f]{8}-[0-9a-f-]{27})'", code))) == 4


# --- the deletion ledger ----------------------------------------------------

_DELETED = ("locality_district_id", "locality_region_id")


def test_the_two_sreality_only_filters_are_gone_from_the_registry() -> None:
    """They were SREALITY's own portal ids — one portal out of nine — and place
    is one thing now: a RÚIAN code at a level."""
    from toolkit.filter_registry import REGISTRY

    for gone in _DELETED:
        assert gone not in REGISTRY
    assert not [d for d in REGISTRY.values() if d.pg_column in _DELETED]


def test_the_two_filters_are_gone_from_the_generated_spa_registry() -> None:
    gen = (REPO / "frontend" / "src" / "lib" / "filterRegistry.generated.ts").read_text(
        encoding="utf-8"
    )
    for gone in _DELETED:
        assert f'"id": "{gone}"' not in gen


def test_the_two_filters_are_gone_from_the_api_schemas_and_predicates() -> None:
    import dataclasses

    from api import schemas as s
    from api.notifications import WatchdogFilterSpec
    from toolkit.comparables import ComparableFilters

    comparable_fields = {f.name for f in dataclasses.fields(ComparableFilters)}
    for gone in _DELETED:
        assert gone not in comparable_fields
        assert gone not in WatchdogFilterSpec.model_fields
        for model in (s.FindComparablesIn, s.CreateEstimationIn):
            assert gone not in model.model_fields
    for rel in ("toolkit/comparables.py", "api/notifications.py"):
        text = (REPO / rel).read_text(encoding="utf-8")
        for gone in _DELETED:
            assert f"l.{gone} = %" not in text, f"{rel} still filters on {gone}"


def test_the_two_filters_are_gone_from_the_spa_filter_types() -> None:
    """W3 S3 took them off every filter surface; W4-a (below) took them out of
    the payloads too. The `listings` COLUMNS themselves survive until W4-c."""
    types_ts = (REPO / "frontend" / "src" / "lib" / "types.ts").read_text(encoding="utf-8")
    for block in ("export interface WatchdogFilterSpec", "export interface EstimationFilters"):
        at = types_ts.index(block)
        body = types_ts[at: types_ts.index("\n}", at)]
        for gone in _DELETED:
            assert gone not in body
    default = types_ts[types_ts.index("export const DEFAULT_WATCHDOG_FILTER_SPEC"):]
    default = default[: default.index("\n};")]
    for gone in _DELETED:
        assert gone not in default


def test_the_two_portal_ids_are_gone_from_every_payload() -> None:
    """W4-a. They were SREALITY's portal ids, carried along in five projections
    and rendered by nothing: the comparable row, the transit-corridor row, the
    freshness snapshot, the URL-parse `listing` sidecar and the SPA's detail
    select. A filter that no longer exists does not get to keep a column in every
    payload on the way out. The `listings` columns themselves are W4-c's.

    Rule 12 is intact: an estimation trace already written keeps its keys, and the
    Timeline renderer simply has no row for them any more (absence renders as
    nothing, not as a crash)."""
    from toolkit.freshness import _LISTING_COLS

    from api.estimation_runs import _LISTING_FIELDS

    for gone in _DELETED:
        assert gone not in _LISTING_COLS
        assert gone not in _LISTING_FIELDS
        for rel in (
            "toolkit/comparables.py",
            "toolkit/transit_axis.py",
            "api/main.py",
            "frontend/src/lib/queries.ts",
            "frontend/src/components/estimation/Timeline.tsx",
        ):
            text = (REPO / rel).read_text(encoding="utf-8")
            assert gone not in text, f"{rel} still carries {gone}"


def test_every_spatial_reader_takes_its_point_from_listing_location() -> None:
    """W4-a. One store answers "where is this listing": `listing_location`. A
    reader that still selects `listings.geom` would be reading a column the
    resolver does not write and W4-c drops.

    The geography cast is checked with it, and it is the reason this test is
    worth having: `listing_location.geom` is geometry(Point,4326), so an
    UNCAST `ST_DWithin(ll.geom, point, 1000)` compiles, runs, and silently means
    1000 DEGREES. Every moved call site casts."""
    spatial = (
        "toolkit/comparables.py",
        "toolkit/velocity.py",
        "toolkit/transit_axis.py",
        "toolkit/neighborhoods.py",
        "api/notifications.py",
        "api/estimation_runs.py",
    )
    bare_l_geom = re.compile(r"(?<![a-z])l\.geom")
    for rel in spatial:
        text = (REPO / rel).read_text(encoding="utf-8")
        code = "\n".join(
            line for line in text.splitlines() if not line.lstrip().startswith("#")
        )
        assert bare_l_geom.search(code) is None, f"{rel} still reads listings.geom"
        assert "listings.geom" not in code, f"{rel} still reads listings.geom"
        assert "COALESCE(p.geom" not in code, f"{rel} still reads properties.geom"
        assert "listing_location" in code, f"{rel} does not join listing_location"

    for rel, expected in (
        ("toolkit/comparables.py", 2),
        ("toolkit/neighborhoods.py", 1),
        ("toolkit/transit_axis.py", 4),
    ):
        text = (REPO / rel).read_text(encoding="utf-8")
        assert text.count("ll.geom::geography") == expected, (
            f"{rel}: expected {expected} geography-cast uses of the resolved point"
        )
