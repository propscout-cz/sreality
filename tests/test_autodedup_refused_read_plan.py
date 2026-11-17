"""The engine's refused-set read (LEDGER_HISTORY_SQL, E41) can be served by migration 746's partial index, in a
custom plan and a generic one, on the replayed schema.

test_746_autodedup_refused_read_index.py pins the text; only a planner can tell whether that text still implies the
index's predicate (a column type, a cast or a spelling that drifts keeps the text test green). CI's replayed ledger
is empty, so a seq scan is honestly cheapest there: the rail turns seq scans off and asks whether the index CAN
serve the read, as tests/test_listing_location_index_plan.py does. DB_RAILS_REQUIRED=1 turns a missing
TEST_DATABASE_URL in the migrations lane into a failure instead of a green skip.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterator
from typing import Any

import pytest

from autodedup import apply_sql as S

_DB_URL = os.environ.get("TEST_DATABASE_URL")
_REQUIRED = os.environ.get("DB_RAILS_REQUIRED") == "1"

pytestmark = pytest.mark.skipif(
    not _DB_URL and not _REQUIRED,
    reason="TEST_DATABASE_URL not set — this rail runs in CI's migrations lane",
)

_INDEX = "autodedup_applied_merges_refused_idx"
_PARAMS = {"generation": "rt", "listing_ids": [101, 202, 303]}


@pytest.fixture(scope="module")
def conn() -> Iterator[Any]:
    if not _DB_URL:
        pytest.fail(
            "DB_RAILS_REQUIRED=1 but TEST_DATABASE_URL is not set — the migrations lane "
            "is misconfigured and this rail would otherwise have skipped green."
        )
    import psycopg

    # prepare_threshold=None is the engine's connection (scraper.db.connect): never server-prepared.
    with psycopg.connect(_DB_URL, autocommit=True, prepare_threshold=None) as c:
        # Plain SET: on an autocommit connection SET LOCAL applies to nothing.
        c.execute("set enable_seqscan = off")
        yield c


def _nodes(node: dict[str, Any]) -> Iterator[dict[str, Any]]:
    yield node
    for child in node.get("Plans", []):
        yield from _nodes(child)


def _uses_index(plan: dict[str, Any]) -> None:
    nodes = list(_nodes(plan))
    assert any(n.get("Index Name") == _INDEX for n in nodes), (
        f"LEDGER_HISTORY_SQL cannot use {_INDEX}: {[n['Node Type'] for n in nodes]}\n" + json.dumps(plan, indent=2))


def test_the_index_is_valid_and_partial_on_the_reads_constants(conn: Any) -> None:
    row = conn.execute(
        "select i.indisvalid, pg_get_indexdef(i.indexrelid) from pg_index i"
        " where i.indexrelid = to_regclass(%s)", (f"autodedup.{_INDEX}",)).fetchone()
    assert row, f"{_INDEX} is missing — migration 746 did not build it"
    valid, indexdef = row
    assert valid, indexdef
    assert "(generation) WHERE ((outcome = 'refused'::text) AND (NOT dry_run))" in indexdef, indexdef


def test_the_read_with_its_values_bound_uses_the_index(conn: Any) -> None:
    plan = conn.execute("explain (format json) " + S.LEDGER_HISTORY_SQL, _PARAMS).fetchone()[0][0]["Plan"]
    _uses_index(plan)


def test_a_generic_plan_of_the_read_uses_the_index(conn: Any) -> None:
    """A prepared statement's generic plan sees no values, so it proves the predicate from the constants alone."""
    generic = re.sub(r"%\(generation\)s", "$1", S.LEDGER_HISTORY_SQL)
    generic = re.sub(r"%\(listing_ids\)s", "$2", generic)
    assert "%(" not in generic
    plan = conn.execute("explain (generic_plan, format json) " + generic).fetchone()[0][0]["Plan"]
    _uses_index(plan)
