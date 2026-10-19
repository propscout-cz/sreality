"""The default map call reads only browse_list_map_idx's columns, WHERE included (target list alone was a false green)."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterator
from typing import Any

import pytest

_DB_URL = os.environ.get("TEST_DATABASE_URL")
_REQUIRED = os.environ.get("DB_RAILS_REQUIRED") == "1"

pytestmark = pytest.mark.skipif(
    not _DB_URL and not _REQUIRED,
    reason="TEST_DATABASE_URL not set — this rail runs in CI's migrations lane",
)

_INDEX = "browse_list_map_idx"
_KEY = ["category_main", "category_type", "lat", "lng"]
_PREDICATE = "((lat IS NOT NULL) AND (lng IS NOT NULL))"
# The SPA's DEFAULT_FILTERS (byt / pronájem, no chip, hide_dismissed on), under both statuses
# the status pill sends without a portal chip.
_DEFAULT = ("category_main_filter => '{byt}'::text[], category_type_filter => 'pronajem', "
            "hide_dismissed => true")
_CALLS = {"any": _DEFAULT, "active": _DEFAULT + ", active_only_filter => true"}
_MUST_READ = {"any": {"category_main", "category_type", "lat", "lng", "property_id"},
              "active": {"category_main", "category_type", "lat", "lng", "property_id",
                         "is_active"}}


@pytest.fixture(scope="module")
def conn():
    if not _DB_URL:
        pytest.fail(
            "DB_RAILS_REQUIRED=1 but TEST_DATABASE_URL is not set — the migrations lane "
            "is misconfigured and this rail would otherwise have skipped green."
        )
    import psycopg

    with psycopg.connect(_DB_URL, autocommit=True) as c:
        # Migration 630 forces no rebuild, so the replay's browse_list predates its index: run
        # the */15 tick here (it also executes 630's restated rebuild once).
        c.execute("select public.rebuild_browse_list()")
        yield c


def _index(conn: Any) -> tuple[list[str], list[str], str | None]:
    """(key, INCLUDE, predicate) of the map index, read from the catalog."""
    row = conn.execute("select indexdef from pg_indexes where schemaname = 'public' "
                       "and indexname = %s", (_INDEX,)).fetchone()
    assert row, f"{_INDEX} does not exist — rebuild_browse_list() no longer creates it"
    m = re.fullmatch(r"CREATE INDEX \w+ ON public\.browse_list USING btree \(([^()]*)\)"
                     r"(?: INCLUDE \(([^()]*)\))?(?: WHERE (.*))?", row[0])
    assert m, f"{_INDEX} is not a btree on public.browse_list: {row[0]}"
    return _names(m.group(1)), _names(m.group(2)), m.group(3)


def _names(columns: str | None) -> list[str]:
    return [c.strip().strip('"') for c in columns.split(",")] if columns else []


def _nodes(plan: dict[str, Any]) -> Iterator[dict[str, Any]]:
    yield plan
    for child in plan.get("Plans", []):
        yield from _nodes(child)


def _columns_read(conn: Any, call: str) -> set[str]:
    """Every browse_list column the call's own plan reads, from auto_explain's VERBOSE plans of
    the statements the function runs (custom plans, so the off filters are already folded)."""
    notices: list[str] = []

    def handler(diag: Any) -> None:
        notices.append(diag.message_primary or "")

    for setting in ("load 'auto_explain'", "set client_min_messages = notice",
                    "set auto_explain.log_min_duration = 0",
                    "set auto_explain.log_nested_statements = on",
                    "set auto_explain.log_verbose = on",
                    "set auto_explain.log_format = json",
                    "set auto_explain.log_level = notice"):
        conn.execute(setting)
    conn.add_notice_handler(handler)
    try:
        conn.execute(f"select public.browse_map_cells({call})")
    finally:
        conn.remove_notice_handler(handler)
        conn.execute("set auto_explain.log_min_duration = -1")
    plans = [json.loads(n.split("plan:\n", 1)[1])["Plan"] for n in notices if "plan:\n" in n]
    nodes = [n for p in plans if any(m.get("Relation Name") == "browse_list" for m in _nodes(p))
             for n in _nodes(p)]
    scans = [n for n in nodes if n.get("Relation Name") == "browse_list"]
    assert scans, f"no plan the call ran scans browse_list: {len(plans)} plans logged"
    ref = re.compile(rf"\b{re.escape(scans[0]['Alias'])}\.([a-z_][a-z0-9_]*)\b")
    read: set[str] = set()
    for n in nodes:
        # A scan under a join may emit the whole row (Postgres' physical tlist); what the plan
        # needs from it shows in the scan's conditions and in every node above it.
        skip = {"Plans", "Output"} if n.get("Relation Name") == "browse_list" else {"Plans"}
        read |= set(ref.findall(json.dumps({k: v for k, v in n.items() if k not in skip})))
    return read


@pytest.mark.parametrize("status", sorted(_CALLS))
def test_the_default_call_reads_only_index_resident_columns(conn: Any, status: str) -> None:
    """RED by: an always-on conjunct or projected column outside the index, or an INCLUDE cut."""
    key, include, _ = _index(conn)
    read = _columns_read(conn, _CALLS[status])
    assert _MUST_READ[status] <= read, (
        f"the plan read {sorted(read)}, short of {sorted(_MUST_READ[status])} — the capture "
        "broke, so containment below would prove nothing"
    )
    assert sorted(read - set(key) - set(include)) == [], (
        f"the default call ({status}) reads columns {_INDEX} does not hold: "
        f"{sorted(read - set(key) - set(include))}; index holds {key} + {include}"
    )


def test_the_map_index_keeps_its_key_and_its_predicate(conn: Any) -> None:
    """RED by: reordering the key in rebuild_browse_list() or dropping its lat/lng predicate."""
    key, _, predicate = _index(conn)
    assert key == _KEY, f"{_INDEX}'s key changed: {key}"
    assert predicate == _PREDICATE, f"{_INDEX}'s predicate changed: {predicate}"
