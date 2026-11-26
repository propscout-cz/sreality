"""The completions ledger keeps three weeks; what the drain, the paste path and Health judge from it stays one (#2665).

`detail_queue_completions` was pruned to 7 days, so a 7-day gate read a week late found its window gone (#2104,
#2415). It keeps 21 now. Three live readers had no lower bound of their own and read whatever the prune left: the
drain's gone evidence (rule #3's hysteresis), the paste path's "gone" answer and Health's in-dwell ads. Each now
reads the week the prune used to leave, so the longer ledger changes no verdict, answer or cell. Executed against
the replayed schema, every seed under its own made-up portal, rolled back.
"""

from __future__ import annotations

import datetime as _dt
import os
import uuid
from collections.abc import Iterator
from typing import Any

import psycopg
import pytest

from scraper import db
from scripts.checks import delisting as dl

_DB_URL = os.environ.get("TEST_DATABASE_URL")
_H = 3600.0

pytestmark = pytest.mark.skipif(
    not _DB_URL,
    reason="TEST_DATABASE_URL not set — the live ledger window runs in the CI DB job",
)


@pytest.fixture()
def conn() -> Iterator[psycopg.Connection]:
    connection = psycopg.connect(_DB_URL)
    try:
        connection.execute("select now()")
        yield connection
    finally:
        connection.rollback()
        connection.close()


@pytest.fixture()
def src() -> str:
    return f"w2665-{uuid.uuid4().hex[:12]}"


def _ad(conn: Any, source: str, native: str, *, seen_days: float) -> None:
    conn.execute(
        "insert into listings (source, source_id_native, raw_json, is_active, last_seen_at) "
        "values (%s, %s, '{}'::jsonb, true, now() - make_interval(secs => %s))",
        (source, native, seen_days * 24 * _H))


def _completion(conn: Any, source: str, native: str, outcome: str, *, days: float) -> _dt.datetime:
    return conn.execute(
        "insert into detail_queue_completions (source, native_id, priority, attempts, enqueued_at, completed_at, "
        "outcome) values (%(source)s, %(native)s, %(priority)s, 0, now() - make_interval(secs => %(secs)s) "
        "- interval '1 minute', now() - make_interval(secs => %(secs)s), %(outcome)s) returning completed_at",
        {"source": source, "native": native, "priority": db.QUEUE_PRIORITY_VERIFY, "secs": days * 24 * _H,
         "outcome": outcome}).fetchone()[0]


def test_the_ledger_keeps_three_weeks_and_prunes_what_is_older(conn, src):
    kept = _completion(conn, src, "kept", "written", days=db.COMPLETION_RETENTION_DAYS - 1)
    _completion(conn, src, "pruned", "written", days=db.COMPLETION_RETENTION_DAYS + 1)

    db.reclaim_stale_claims(conn, src)

    left = conn.execute("select native_id, completed_at from detail_queue_completions where source = %s",
                        (src,)).fetchall()
    assert left == [("kept", kept)]


def test_a_gone_verdict_older_than_the_evidence_week_is_no_evidence(conn, src):
    """Rule #3's hysteresis reads the week the prune left: a verdict from 8 days ago never confirms today's."""
    _ad(conn, src, "old-and-new", seen_days=10)
    _completion(conn, src, "old-and-new", "gone", days=db.COMPLETION_EVIDENCE_DAYS + 1)
    recent = _completion(conn, src, "old-and-new", "gone", days=2 / 24)
    _ad(conn, src, "old-only", seen_days=10)
    _completion(conn, src, "old-only", "gone", days=db.COMPLETION_EVIDENCE_DAYS + 1)
    _ad(conn, src, "inside", seen_days=10)
    inside = _completion(conn, src, "inside", "gone", days=db.COMPLETION_EVIDENCE_DAYS - 1)

    assert db.gone_evidence(conn, src, "old-and-new") == (True, recent)
    assert db.gone_evidence(conn, src, "old-only") == (True, None)
    assert db.gone_evidence(conn, src, "inside") == (True, inside)


def test_a_paste_answers_gone_only_from_the_evidence_week(conn, src):
    _completion(conn, src, "old", "gone", days=db.COMPLETION_EVIDENCE_DAYS + 1)
    _completion(conn, src, "inside", "gone", days=db.COMPLETION_EVIDENCE_DAYS - 1)

    assert db.latest_completion_outcome(conn, src, "old") is None
    assert db.latest_completion_outcome(conn, src, "inside") == "gone"


def test_health_counts_an_ad_in_dwell_only_from_the_evidence_week(conn, src):
    """The stuck arm reads the in-dwell ads as db.gone_evidence does: the 8-day verdict is no ad in dwell."""
    _ad(conn, src, "old", seen_days=10)
    _completion(conn, src, "old", "gone", days=db.COMPLETION_EVIDENCE_DAYS + 1)
    _ad(conn, src, "inside", seen_days=10)
    _completion(conn, src, "inside", "gone", days=db.COMPLETION_EVIDENCE_DAYS - 1)

    rows = conn.execute(dl._STUCK_SQL, {"sources": [src], "limits_s": [37 * _H], "fallback_s": 25 * _H}).fetchall()

    assert [(row[0], row[1], row[2]) for row in rows if row[0] == src] == [(src, 1, 1)]
