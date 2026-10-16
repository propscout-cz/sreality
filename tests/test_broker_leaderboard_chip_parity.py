"""The leaderboard tests a location chip with Browse's predicate (migration 607, rule 25).

`broker_leaderboard`'s live branch carries the include + exclude chip blocks of
`browse_stats_properties` (605), over `listing_location`'s five codes instead of the read
model's five columns. Two rails keep it the ONE place predicate:

  * offline — the two blocks ARE 605's, token for token once the columns are mapped, and
    their arms are exactly `api.location_filter.LEVEL_COLUMN`'s five levels;
  * DB lane — the blocks, bound through `api.location_filter.district_arrays` (what
    toolkit/brokers.py and api/outreach.py send), select exactly the rows the shared table
    `tests/fixtures/district_chip_plan.json` says match — the rows `district_where` is held
    to in tests/test_filter_compiler_rows_live.py, so a NULL code under an exclude is kept
    and a street chip never widens to its town.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

import pytest

from api.location_filter import LEVEL_COLUMN, LEVEL_ORDER, DistrictChip, district_arrays
from tests.migration_defs import MIGRATIONS, latest_definition

_DB_URL = os.environ.get("TEST_DATABASE_URL")
_REQUIRED = os.environ.get("DB_RAILS_REQUIRED") == "1"
_CASES = json.loads(
    (Path(__file__).parent / "fixtures" / "district_chip_plan.json").read_text(encoding="utf-8")
)["cases"]

# level -> its code on listing_location (migration 501), the store the leaderboard reads.
_LL_COLUMN = {level: f"{level}_kod" for level in LEVEL_ORDER}
_BLOCK_START = "      and (\n        districts_filter is null"


def _body(func: str) -> str:
    sql = latest_definition(func).read_text(encoding="utf-8")
    at = re.search(rf"create or replace function public\.{func}\s*\(", sql).start()
    return sql[at: sql.index("$function$;", at)]


def _chip_blocks(func: str) -> str:
    """The include + exclude blocks (contiguous, in that order), comment lines dropped."""
    body = _body(func)
    start = body.index(_BLOCK_START)
    second = body.index(_BLOCK_START, start + 1)
    end = body.index("\n      )\n", second) + len("\n      )\n")
    return "\n".join(
        line for line in body[start:end].splitlines() if not line.strip().startswith("--")
    )


def _tokens(sql: str) -> list[str]:
    return sql.split()


def test_the_leaderboard_carries_605s_chip_blocks_over_listing_location() -> None:
    """RED by: any edit to one copy of the predicate and not the other — an arm, the NULL
    guard, the `coalesce(excl, false)` that keeps an unresolved row under an exclude."""
    assert latest_definition("broker_leaderboard").name == "607_broker_leaderboard_chip_arrays.sql"
    browse = _chip_blocks("browse_stats_properties")
    for level, column in LEVEL_COLUMN.items():
        browse = browse.replace(f"l.{column} ", f"ll.{_LL_COLUMN[level]} ")
    assert _tokens(_chip_blocks("broker_leaderboard")) == _tokens(browse)


def test_the_leaderboard_arms_are_the_five_levels_in_order() -> None:
    blocks = re.findall(r"and case\b(.*?)\bend\b", _chip_blocks("broker_leaderboard"), re.DOTALL)
    assert len(blocks) == 2, f"expected the include and the exclude CASE, found {len(blocks)}"
    for block in blocks:
        arms = re.findall(r"when lvl = '([a-z_]+)'\s*then ll\.([a-z_]+)\s*= admin_id", block)
        assert arms == list(_LL_COLUMN.items())
        assert tuple(_LL_COLUMN) == tuple(LEVEL_COLUMN)
        assert re.search(r"when admin_id is null\s+then false", block)
        assert "else false" in block


def test_every_code_column_the_arms_read_is_a_listing_location_column() -> None:
    sql = (MIGRATIONS / "501_location_w2a_listing_location.sql").read_text(encoding="utf-8")
    at = sql.index("create table listing_location (")
    table = sql[at: sql.index("\n);", at)]
    for column in _LL_COLUMN.values():
        assert re.search(rf"^\s+{column}\s+bigint", table, re.MULTILINE), column


@pytest.fixture(scope="module")
def conn():
    if not _DB_URL:
        if _REQUIRED:
            pytest.fail("DB_RAILS_REQUIRED=1 but TEST_DATABASE_URL is not set")
        pytest.skip("TEST_DATABASE_URL not set — the DB half runs in CI's migrations lane")
    import psycopg

    with psycopg.connect(_DB_URL, autocommit=True) as c:
        yield c


def _bound_predicate() -> str:
    sql = _chip_blocks("broker_leaderboard")
    for name, (key, cast) in {
        "districts_filter": ("names", "text[]"),
        "districts_excluded_filter": ("excluded", "boolean[]"),
        "districts_levels": ("levels", "text[]"),
        "districts_ids": ("ids", "bigint[]"),
    }.items():
        sql = re.sub(rf"\b{name}\b", f"%({key})s::{cast}", sql)
    return sql


@pytest.mark.parametrize("case", _CASES, ids=[c["name"] for c in _CASES])
def test_the_chip_table_rows_match_in_the_leaderboard_too(conn, case: dict[str, Any]) -> None:
    columns = list(_LL_COLUMN.values())
    params: dict[str, Any] = {}
    values: list[str] = []
    for i, r in enumerate(case["rows"]):
        cells = [str(i)]
        for level, column in _LL_COLUMN.items():
            params[f"row{i}_{column}"] = r["row"].get(LEVEL_COLUMN[level])
            cells.append(f"%(row{i}_{column})s::bigint")
        values.append(f"({', '.join(cells)})")
    names, excluded, levels, ids = district_arrays([DistrictChip(**c) for c in case["chips"]])
    params.update(names=names, excluded=excluded, levels=levels, ids=ids)
    sql = (
        f"SELECT ll.idx FROM (VALUES {', '.join(values)}) AS ll(idx, {', '.join(columns)}) "
        f"WHERE true\n{_bound_predicate()}"
    )
    with conn.cursor() as cur:
        cur.execute(sql, params)
        got = {int(r[0]) for r in cur.fetchall()}
    assert got == {i for i, r in enumerate(case["rows"]) if r["matches"]}
