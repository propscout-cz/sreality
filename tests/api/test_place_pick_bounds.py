"""The place pick's bounds live in three places: the SPA (which drops a pick outside them), the request schema (a 422 for
the whole resolve) and migration 737's CHECKs. A drift fails the user's resolve, not only the record, so they are held
equal here."""

from __future__ import annotations

import re
from pathlib import Path

from api.schemas import PlacePickIn

ROOT = Path(__file__).resolve().parents[2]


def _ts_const(name: str) -> int:
    match = re.search(rf"^const {name} = (\d+);$", (ROOT / "frontend/src/lib/maps.ts").read_text(), re.M)
    assert match, f"{name} not found in frontend/src/lib/maps.ts"
    return int(match.group(1))


def _bound(field: str, attr: str) -> int:
    (bound,) = [getattr(m, attr) for m in PlacePickIn.model_fields[field].metadata if hasattr(m, attr)]
    return bound


def test_the_spa_the_schema_and_the_table_hold_one_text_bound() -> None:
    sql = (ROOT / "migrations/737_place_picks.sql").read_text()
    assert _ts_const("PICK_TEXT_MAX") == _bound("text", "max_length") == 200
    assert _bound("text", "min_length") == 1
    assert "char_length(text) between 1 and 200" in sql


def test_the_spa_the_schema_and_the_table_hold_one_rank_bound() -> None:
    sql = (ROOT / "migrations/737_place_picks.sql").read_text()
    assert _ts_const("PICK_RANK_MAX") == _bound("rank", "le") == 10
    assert _bound("rank", "ge") == 1
    assert "check (rank between 1 and 10)" in sql
