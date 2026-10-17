"""The leaderboard tests a location chip with the one place predicate (migration 609, rule 25).

`broker_leaderboard`'s live branch is the SQL twin of `api.location_filter.district_where`:
per level `ll.<level>_kod = any(<that level's codes>)`, an exclude wrapped in
`NOT COALESCE(..., false)` so a row whose code is NULL survives it. Every code array is a
`district_level_codes(...)` call, which the planner folds to a constant, so each arm is
estimated like 580's id lists were. Three rails:

  * offline — five include arms and five exclude arms, one per `LEVEL_COLUMN` level, in
    order, each reading that level's codes with the right flag, and the exclude wrapper;
  * DB lane — the blocks, bound through `api.location_filter.district_arrays` (what
    toolkit/brokers.py and api/outreach.py send), select exactly the rows the shared table
    `tests/fixtures/district_chip_plan.json` says match — the rows `district_where` is held
    to in tests/test_filter_compiler_rows_live.py, so a NULL code under an exclude is kept
    and a street chip never widens to its town;
  * DB lane — `district_level_codes` itself: the include/exclude split, a NULL flag, a NULL
    flag array, no chips.
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
_INCLUDE_START = "      and (\n        -- no include chip"
_EXCLUDE_START = "      and (\n        not coalesce(true = any(districts_excluded_filter), false)"
_ARM = (r"ll\.(\w+) = any\(district_level_codes\(\s*districts_levels, districts_ids, "
        r"districts_excluded_filter, '(\w+)', (true|false)\)\)")


def _body(func: str) -> str:
    sql = latest_definition(func).read_text(encoding="utf-8")
    at = re.search(rf"create or replace function public\.{func}\s*\(", sql).start()
    return sql[at: sql.index("$function$;", at)]


def _block(start: str) -> str:
    body = _body("broker_leaderboard")
    at = body.index(start)
    return body[at: body.index("\n      )\n", at) + len("\n      )\n")]


def _chip_blocks() -> str:
    """The include + exclude blocks (contiguous, in that order), comment lines dropped."""
    include, exclude = _block(_INCLUDE_START), _block(_EXCLUDE_START)
    assert _body("broker_leaderboard").index(include + exclude) > 0, "not contiguous"
    return "\n".join(
        line for line in (include + exclude).splitlines() if not line.strip().startswith("--")
    )


def test_the_live_branch_has_five_include_and_five_exclude_arms_in_level_order() -> None:
    """RED by: an arm dropped, added, reordered, pointed at another level's column, or
    reading the other flag's codes — and by losing the NOT COALESCE that keeps a row whose
    code is NULL under an exclude (the bare NOT drops it: "not Prague" would mean "not
    Prague AND located")."""
    assert latest_definition("broker_leaderboard").name == "609_broker_leaderboard_chip_arrays.sql"
    include, exclude = _block(_INCLUDE_START), _block(_EXCLUDE_START)
    want = [(column, level) for level, column in _LL_COLUMN.items()]
    assert [(c, lv) for c, lv, flag in re.findall(_ARM, include) if flag == "false"] == want
    assert [(c, lv) for c, lv, flag in re.findall(_ARM, exclude) if flag == "true"] == want
    assert len(re.findall(_ARM, include)) == len(re.findall(_ARM, exclude)) == len(LEVEL_COLUMN)
    assert tuple(_LL_COLUMN) == tuple(LEVEL_COLUMN)
    assert "or not coalesce(\n" in exclude and exclude.rstrip().endswith("false)\n      )")
    assert "case" not in _chip_blocks() and "unnest" not in _chip_blocks()


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
    sql = _chip_blocks()
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


@pytest.mark.parametrize("args,expected", [
    (("{kraj,obec,kraj}", "{1,2,3}", "{f,f,t}", "kraj", False), [1]),
    (("{kraj,obec,kraj}", "{1,2,3}", "{f,f,t}", "kraj", True), [3]),
    (("{kraj,obec,kraj}", "{1,2,3}", "{f,f,t}", "obec", False), [2]),
    (("{ulice,ulice}", "{9,7}", "{f,NULL}", "ulice", False), [9, 7]),
    (("{kraj}", "{1}", None, "kraj", False), [1]),
    (("{kraj}", "{1}", None, "kraj", True), []),
    (("{kraj}", "{1}", "{f}", "okres", False), []),
    ((None, None, None, "kraj", False), []),
], ids=["include", "exclude", "another level", "a NULL flag is false, chip order kept",
        "a NULL flag array excludes nothing", "so nothing is excluded",
        "a level with no chip", "no chips"])
def test_district_level_codes_answers_one_level_and_one_flag(conn, args, expected) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "select public.district_level_codes(%s::text[], %s::bigint[], %s::boolean[], %s, %s)",
            args,
        )
        assert cur.fetchone()[0] == expected
