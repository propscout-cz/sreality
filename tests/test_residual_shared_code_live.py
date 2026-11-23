"""The residual queue's shared-order-code filter (operator, 2026-10-31), EXECUTED.

Which pairs `ui_sql._SHARED_CODE` keeps is a question of regexes, a suffix pre-check and a NOT IN
over every listing's code; the fake connections run none of it and PREPARE proves only that it
compiles. Seeded pairs here pin the normalisation (portal prefixes, remax's `ID `, case), the two
gates (>= 5 characters, a digit), the population cap at exactly 8 carriers, and that the count
says what the page shows. Runs in CI's migrations job (`TEST_DATABASE_URL`); every test rolls back.
"""

from __future__ import annotations

import os
import uuid
from typing import Any, Iterator

import pytest

from autodedup import ui_sql as usql
from autodedup.text_facts import MAX_CODE_POPULATION

_DB_URL = os.environ.get("TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not _DB_URL,
    reason="TEST_DATABASE_URL not set — schema-replay test runs only in the CI DB job",
)


@pytest.fixture()
def cur() -> Iterator[Any]:
    import psycopg

    conn = psycopg.connect(
        _DB_URL,
        options="-c statement_timeout=20000 -c lock_timeout=5000"
        " -c idle_in_transaction_session_timeout=30000",
    )
    try:
        with conn.cursor() as c:
            yield c
    finally:
        conn.rollback()
        conn.close()


@pytest.fixture()
def generation() -> str:
    return f"ci-{uuid.uuid4()}"


def _ad(cur: Any, reference: str | None, source: str = "remax") -> int:
    cur.execute(
        "insert into listings (source, source_id_native, raw_json, category_main, category_type,"
        " ad_reference) values (%s, %s, '{}'::jsonb, 'byt', 'prodej', %s) returning id",
        (source, f"shared-code-{uuid.uuid4()}", reference))
    return int(cur.fetchone()[0])


def _pair(cur: Any, generation: str, a: str | None, b: str | None, score: float = 0.5) -> tuple[int, int]:
    lo, hi = sorted((_ad(cur, a, "idnes"), _ad(cur, b)))
    cur.execute(
        "insert into autodedup.pairs (listing_lo, listing_hi, score, zone, probes, generation)"
        " values (%s, %s, %s, 'band', '{}', %s)",
        (lo, hi, score, generation))
    return lo, hi


def _params(generation: str, shared_code: bool) -> dict[str, Any]:
    return {
        "generation": generation, "min_score": 0.0, "zone": None, "block": None,
        "block_grain": None, "source_pair": None, "verdict": None, "shared_code": shared_code,
        "limit": 100, "seed": "v1", "card_frames": 8, "after_score": None, "after_hash": None,
        "after_lo": None, "after_hi": None,
    }


def _code() -> str:
    """A code no other seeded row can carry, so the population cap counts only this test's ads."""
    return f"Q{uuid.uuid4().int % 10**9:09d}"


def test_only_pairs_printing_one_rare_order_code_are_kept(cur: Any, generation: str) -> None:
    idnes, remax, eight, crowd = _code(), _code(), _code(), _code()
    keep = {
        _pair(cur, generation, f"IDNES-{idnes}", idnes.lower()),
        _pair(cur, generation, f"ID {remax}", remax),
        _pair(cur, generation, f"REALITYMIX-{eight[:3]} {eight[3:]}", f"realitymix-{eight}"),
        _pair(cur, generation, eight, eight),
    }
    drop = {
        _pair(cur, generation, "IDNES-1166", "1166"),
        _pair(cur, generation, "ABCDEFGH", "abcdefgh"),
        _pair(cur, generation, f"{crowd}1", f"{crowd}2"),
        _pair(cur, generation, crowd, crowd),
        _pair(cur, generation, None, crowd),
    }
    # `eight` is on four ads above: four more make exactly the cap, which still keeps its pairs.
    for _ in range(MAX_CODE_POPULATION - 4):
        _ad(cur, eight)
    # `crowd` is on three: six more is one carrier past it, a developer's product code.
    for _ in range(MAX_CODE_POPULATION - 2):
        _ad(cur, f"IDNES-{crowd}")

    cur.execute(usql.RESIDUAL_SQL, _params(generation, shared_code=True))
    assert {(int(r[0]), int(r[1])) for r in cur.fetchall()} == keep

    cur.execute(usql.RESIDUAL_RANDOM_SQL, _params(generation, shared_code=True))
    assert {(int(r[0]), int(r[1])) for r in cur.fetchall()} == keep

    cur.execute(usql.RESIDUAL_COUNT_SQL, _params(generation, shared_code=True))
    assert cur.fetchone()[0] == len(keep)

    cur.execute(usql.RESIDUAL_COUNT_SQL, _params(generation, shared_code=False))
    assert cur.fetchone()[0] == len(keep | drop)
