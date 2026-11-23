"""THE portal rule (MS19, migration 590; two-sided since 597; the status is the property's since 657),
executed and pinned to the shared fixture.

`tests/fixtures/portal_rule.json` is read by both languages. Here: every case through
`public.portal_status_matches`, through both aggregate RPCs (Stats and, since migration 630,
the map cells over `browse_list`) and through both property grains' compiled rule
(`toolkit.filter_compiler.PROPERTIES_GRAIN`, the Watchdog's, and `BROWSE_GRAIN`, the agents'
search over Browse's source); every broker case seeds its
ads, runs the broker lookup (`toolkit.brokers.broker_property_ids`, the portal side on the
broker's book row, migration 692) and keeps the property's own status beside it, as Browse does. The SPA half is
`applyPortalRule` in queries.test.ts. Also live: the rebuild's one partial index per portal
and the projection's and `browse_list`'s one shape. Offline: the fixture covers every arm.

Live rails run in CI's migrations lane (`TEST_DATABASE_URL`, `DB_RAILS_REQUIRED=1`), each
inside a transaction that always rolls back.
"""

from __future__ import annotations

import itertools
import json
import os
from pathlib import Path
from typing import Any

import pytest

from toolkit.filter_registry import PORTAL_OPTIONS

_DB_URL = os.environ.get("TEST_DATABASE_URL")
_REQUIRED = os.environ.get("DB_RAILS_REQUIRED") == "1"
live = pytest.mark.skipif(
    not _DB_URL and not _REQUIRED,
    reason="TEST_DATABASE_URL not set — this rail runs in CI's migrations lane",
)

_TABLE = json.loads(
    (Path(__file__).resolve().parent / "fixtures" / "portal_rule.json").read_text(encoding="utf-8"))
CASES = _TABLE["cases"]
BROKER_CASES = _TABLE["broker_cases"]
_STATUSES = ("any", "active", "inactive")
_PORTALS = [o.value for o in PORTAL_OPTIONS]
_IDS = itertools.count(970_000_001)


def _ids(cases: list[dict[str, Any]]) -> list[str]:
    return [c["name"] for c in cases]


# --- offline: the table itself ---------------------------------------------------------


def test_the_table_covers_every_arm_both_ways() -> None:
    seen = {(min(len(c["portals"]), 2), min(len(c["excluded"]), 1), c["status"], c["expected"])
            for c in CASES}
    for key in itertools.product((0, 1, 2), (0, 1), _STATUSES, (True, False)):
        if key == (0, 0, "any", False):
            continue  # no portal, no exclusion and no status is no constraint
        assert key in seen, key
    either_way = {(c["excluded_active_only"], c["expected"]) for c in CASES if c["excluded"]}
    assert either_way == set(itertools.product((True, False), (True, False)))


def test_every_case_names_known_portals_and_consistent_lists() -> None:
    for c in [*CASES, *BROKER_CASES]:
        assert set(c["portals"]) <= set(_PORTALS), c["name"]
    for c in CASES:
        assert set(c["excluded"]) <= set(_PORTALS), c["name"]
        assert c["excluded"] or not c["excluded_active_only"], c["name"]
        assert set(c["active_sources"]) <= set(c["all_sources"]), c["name"]
        assert c["is_active"] == bool(c["active_sources"]) or not c["all_sources"], c["name"]


# --- live ------------------------------------------------------------------------------


@pytest.fixture()
def conn():
    if not _DB_URL:
        pytest.fail(
            "DB_RAILS_REQUIRED=1 but TEST_DATABASE_URL is not set — the migrations lane "
            "is misconfigured and this rail would otherwise have skipped green."
        )
    import psycopg

    with psycopg.connect(_DB_URL, autocommit=False) as c:
        yield c
        c.rollback()


@live
@pytest.mark.parametrize("case", CASES, ids=_ids(CASES))
def test_the_function_answers_the_table(conn, case: dict[str, Any]) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "select public.portal_status_matches(%s, %s::text[], %s::text[], %s::text[], %s, "
            "%s::text[], %s)",
            (case["is_active"], case["all_sources"], case["active_sources"],
             case["portals"], case["status"], case["excluded"], case["excluded_active_only"]))
        assert cur.fetchone()[0] is case["expected"]


def _compiled(grain_name: str, case: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
    from toolkit import filter_compiler as fc

    grain = getattr(fc, grain_name)
    values = {"portals": case["portals"], "status": case["status"], "portals_excluded": case["excluded"],
              "portals_excluded_active_only": case["excluded_active_only"]}
    if grain is fc.PROPERTIES_GRAIN:
        # The Watchdog takes no status (its agenda refuses one): its `any` rows compile, every
        # other row reaches the grain's own portal rule directly.
        if case["status"] == "any":
            return fc.compile_filter_where({**values, "status": None}, grain)
        params: dict[str, Any] = {}
        return grain.hooks["portals"]("portals", case["portals"], values, params), params
    return fc.compile_filter_where(values, grain)


@live
@pytest.mark.parametrize("grain", ["PROPERTIES_GRAIN", "BROWSE_GRAIN"])
@pytest.mark.parametrize("case", CASES, ids=_ids(CASES))
def test_both_property_grains_compile_the_table(conn, case: dict[str, Any], grain: str) -> None:
    where, params = _compiled(grain, case)
    with conn.cursor() as cur:
        cur.execute(
            "select count(*) from (values (%(is_active)s, %(all)s::text[], %(active)s::text[])) "
            f"as l(is_active, all_sources, active_sources) where {' and '.join(where) or 'true'}",
            {**params, "is_active": case["is_active"], "all": case["all_sources"],
             "active": case["active_sources"]})
        assert cur.fetchone()[0] == int(case["expected"])


def _rpc_args(case: dict[str, Any]) -> dict[str, Any]:
    return {"portal": case["portals"] or None, "status": case["status"],
            "excluded": case["excluded"] or None,
            "excluded_active_only": case["excluded_active_only"]}


@live
@pytest.mark.parametrize("case", CASES, ids=_ids(CASES))
def test_stats_counts_the_table(conn, case: dict[str, Any]) -> None:
    """browse_stats_properties reads browse_list (590's rebuild made it a real table); since
    626 it is the dashboard pivot, whose `filtered` is the cohort the portal rule picked."""
    with conn.cursor() as cur:
        cur.execute(
            "insert into browse_list (property_id, is_active, all_sources, active_sources) "
            "values (%s, %s, %s, %s)",
            (next(_IDS), case["is_active"], case["all_sources"], case["active_sources"]))
        cur.execute(
            "select public.browse_stats_properties(portal_filter => %(portal)s::text[], "
            "status_filter => %(status)s, "
            "portal_excluded_filter => %(excluded)s::text[], "
            "portal_excluded_active_only => %(excluded_active_only)s, "
            "spec => '{\"rows\": [], \"cols\": [], \"measure\": \"price\"}'::jsonb)",
            _rpc_args(case))
        assert cur.fetchone()[0]["filtered"] == int(case["expected"])


@live
@pytest.mark.parametrize("case", CASES, ids=_ids(CASES))
def test_the_map_cells_count_the_table(conn, case: dict[str, Any]) -> None:
    """browse_map_cells reads browse_list, the Stats rail's table, since migration 630."""
    with conn.cursor() as cur:
        cur.execute(
            "insert into browse_list (property_id, listing_id, lat, lng, is_active, "
            "all_sources, active_sources) values (%s, %s, 50.08, 14.42, %s, %s, %s)",
            (next(_IDS), next(_IDS), case["is_active"], case["all_sources"],
             case["active_sources"]))
        cur.execute(
            "select public.browse_map_cells(portal_filter => %(portal)s::text[], "
            "status_filter => %(status)s, "
            "portal_excluded_filter => %(excluded)s::text[], "
            "portal_excluded_active_only => %(excluded_active_only)s, point_budget => 2000)",
            _rpc_args(case))
        assert cur.fetchone()[0]["total"] == int(case["expected"])


def _seed_broker_case(cur: Any, case: dict[str, Any]) -> tuple[dict[str, int], int]:
    """Each broker with one identity per portal they advertise on, as attribution keys them (an identity is one
    portal's), and the property's book kept by the maintenance pass the lookup reads (migration 692)."""
    from scripts.recompute_property_stats import properties_changed

    brokers: dict[str, int] = {}
    for label in sorted({ad["broker"] for ad in case["ads"]}):
        cur.execute("insert into brokers (display_name) values (%s) returning id",
                    (f"rule-{label}",))
        brokers[label] = cur.fetchone()[0]
    identities: dict[tuple[str, str], int] = {}
    for label, source in sorted({(ad["broker"], ad["source"]) for ad in case["ads"]}):
        cur.execute(
            "insert into broker_identities (source, source_broker_id_native, broker_id) "
            "values (%s, %s, %s) returning id", (source, f"rule-{next(_IDS)}", brokers[label]))
        identities[label, source] = cur.fetchone()[0]
    cur.execute("insert into properties default values returning id")
    property_id = cur.fetchone()[0]
    for ad in case["ads"]:
        native = next(_IDS)
        cur.execute(
            "insert into listings (source, source_id_native, sreality_id, raw_json, property_id, "
            "broker_identity_id, is_active) values (%s, %s, %s, '{}', %s, %s, %s)",
            (ad["source"], str(native), native if ad["source"] == "sreality" else None,
             property_id, identities[ad["broker"], ad["source"]], ad["is_active"]))
    properties_changed(cur.connection, [property_id])
    return brokers, property_id


@live
@pytest.mark.parametrize("case", BROKER_CASES, ids=_ids(BROKER_CASES))
def test_the_broker_lookup_with_the_propertys_status_answers_the_table(
        conn, case: dict[str, Any]) -> None:
    from toolkit.brokers import broker_property_ids

    with conn.cursor() as cur:
        brokers, property_id = _seed_broker_case(cur, case)
    out = broker_property_ids(conn, brokers[case["broker"]], status=case["status"],
                              portals=case["portals"])
    live = any(ad["is_active"] for ad in case["ads"])  # the property's is_active, over every ad
    status = {"any": True, "active": live, "inactive": not live}[case["status"]]
    assert (property_id in out["data"] and status) is case["expected"]


@live
def test_the_rebuild_builds_one_partial_index_per_portal(conn) -> None:
    """The one-portal "Newest first" page and count read these (migration 590). A tenth
    portal is offered only with its line here (tests/test_recompute_property_stats.py).
    Live, they exist once `rebuild_browse_list()` has run since the portal's migration (649
    forces no rebuild); in CI's migrations lane test_browse_map_cells_plan.py's fixture
    commits one earlier in the same job."""
    with conn.cursor() as cur:
        cur.execute("select indexname, indexdef from pg_indexes where schemaname = 'public' "
                    "and tablename = 'browse_list' and indexname like 'browse_list_newest_ad_at_%'")
        found = dict(cur.fetchall())
    assert found == {
        f"browse_list_newest_ad_at_{p}_idx": (
            f"CREATE INDEX browse_list_newest_ad_at_{p}_idx ON public.browse_list USING btree "
            f"(category_main, category_type, newest_ad_at_{p} DESC, property_id DESC) "
            f"WHERE (newest_ad_at_{p} IS NOT NULL)")
        for p in _PORTALS
    }


@live
def test_the_projection_and_the_read_model_have_one_shape(conn) -> None:
    """`sync_browse_list` inserts by POSITION (`insert into browse_list select * from
    browse_projection`): two relations, one column list, 590's twelve one block, and each
    later portal's date appended after it."""
    shapes = {}
    with conn.cursor() as cur:
        for rel in ("browse_projection", "browse_list"):
            cur.execute(
                "select attname, format_type(atttypid, atttypmod) from pg_attribute "
                "where attrelid = %s::regclass and attnum > 0 and not attisdropped "
                "order by attnum", (f"public.{rel}",))
            shapes[rel] = cur.fetchall()
    assert shapes["browse_list"] == shapes["browse_projection"]
    names = [n for n, _ in shapes["browse_projection"]]
    assert "asset_id" not in names
    # 590's twelve are one contiguous block in portal order; later columns may follow it (584's
    # recipe appends every new projection column LAST, so the block is not pinned to the end),
    # and a portal since 590 (`web`, migration 649) has its date among them, in portal order.
    at = names.index("all_sources")
    assert names[at:at + 12] == ["all_sources", "active_sources", "source_count",
                                 *(f"newest_ad_at_{p}" for p in _PORTALS[:9])]
    assert [c for c in names[at + 12:] if c.startswith("newest_ad_at_")] == [
        f"newest_ad_at_{p}" for p in _PORTALS[9:]]
