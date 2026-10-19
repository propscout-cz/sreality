"""The delisting check's statements, executed: aligned outflow windows, per-ad verdicts, the backlog, the cohort.

The hermetic tests answer each statement with canned rows and the replay rebuilds them in Python, so only this file
runs the SQL. Each test seeds listings, queue, completion or walk rows of its own made-up portals inside a
transaction that always rolls back, sends the module's statement with the parameters the check sends, and asserts
the rule it encodes:
- outflow: each week back is read at every whole-hour offset around now (half-open 24 h windows), the current 24 h
  at offset 0 only, and the check judges a drop on each week's quietest window and a spike on its busiest;
- stuck: per ad, the earliest gone verdict newer than its last sighting (a later sighting refutes an earlier
  verdict, never a later one), counted once per active ad, against the portal's limit or the fallback;
- verify backlog: VERIFY-priority rows, claimed ones counted, given-up ones left out, aged from the oldest enqueue;
- precision: flipped completions 48-72 h old as the cohort, 2-7 d as the pool, "active again" = is_active now;
- walks: finished index walks only, a walk reached the end when ANY entry did, deferrals summed.
Runs in CI's migrations lane (`TEST_DATABASE_URL`, `DB_RAILS_REQUIRED=1`); skipped locally.
"""

from __future__ import annotations

import datetime as _dt
import os
import uuid
from typing import Any

import pytest

from scraper.db import QUEUE_PRIORITY_NEW, QUEUE_PRIORITY_VERIFY
from scripts.checks import delisting as dl
from scripts.verify_pipeline import THRESHOLDS

_DB_URL = os.environ.get("TEST_DATABASE_URL")
_REQUIRED = os.environ.get("DB_RAILS_REQUIRED") == "1"
_H = 3600.0
# A seed's time is now() - days + secs, the outflow windows' own arithmetic, so a seed can sit exactly on an edge.
_AT = "now() - make_interval(days => %(days)s::int) + make_interval(secs => %(secs)s::float8)"

pytestmark = pytest.mark.skipif(
    not _DB_URL and not _REQUIRED,
    reason="TEST_DATABASE_URL not set — this rail runs in CI's migrations lane",
)


@pytest.fixture()
def conn():
    """One connection in one transaction, always rolled back: the seeds, and the check's savepoints inside it."""
    if not _DB_URL:
        pytest.fail("DB_RAILS_REQUIRED=1 but TEST_DATABASE_URL is not set — the migrations lane is "
                    "misconfigured and this rail would otherwise have skipped green.")
    import psycopg

    connection = psycopg.connect(_DB_URL)
    try:
        connection.execute("select now()")  # opens the transaction every seed rolls back with
        yield connection
    finally:
        connection.rollback()
        connection.close()


def _now(conn: Any) -> _dt.datetime:
    """The transaction's now(), the clock every window in the check's SQL is measured on."""
    return conn.execute("select now()").fetchone()[0]


def _ads(conn: Any, source: str, n: int = 1, *, days: int = 0, hours: float = 0.0) -> None:
    """`n` inactive ads of `source`, made inactive (and last seen) at now() - days + hours."""
    conn.execute(
        "insert into listings (source, source_id_native, raw_json, is_active, inactive_at, last_seen_at) "
        f"select %(source)s, %(prefix)s || g, '{{}}'::jsonb, false, {_AT}, {_AT} "
        "  from generate_series(1, %(n)s::int) as g",
        {"source": source, "prefix": f"w4-{uuid.uuid4().hex[:12]}-", "days": days, "secs": hours * _H,
         "n": n})


def _ad(conn: Any, source: str, native: str, *, active: bool, seen_h: float) -> None:
    """One ad, last seen `seen_h` hours ago; an inactive one was made inactive then."""
    conn.execute(
        "insert into listings (source, source_id_native, raw_json, is_active, inactive_at, last_seen_at) "
        "values (%(source)s, %(native)s, '{}'::jsonb, %(active)s, "
        "        case when not %(active)s then now() - make_interval(secs => %(seen_s)s) end, "
        "        now() - make_interval(secs => %(seen_s)s))",
        {"source": source, "native": native, "active": active, "seen_s": seen_h * _H})


def _completion(conn: Any, source: str, native: str, outcome: str, *, days: int = 0,
                hours_ago: float = 0.0) -> None:
    """A drain's terminal row for this ad, completed at now() - days - hours_ago, enqueued a minute before."""
    conn.execute(
        "insert into detail_queue_completions (source, native_id, priority, attempts, enqueued_at, completed_at, "
        f"outcome) values (%(source)s, %(native)s, %(priority)s, 0, {_AT} - interval '1 minute', {_AT}, "
        "%(outcome)s)",
        {"source": source, "native": native, "priority": QUEUE_PRIORITY_VERIFY, "days": days,
         "secs": -hours_ago * _H, "outcome": outcome})


def _queued(conn: Any, source: str, *, priority: int, hours_ago: float, claimed: bool = False,
            given_up: bool = False) -> None:
    conn.execute(
        "insert into listing_detail_queue (source, native_id, priority, enqueued_at, claimed_at, attempts, given_up) "
        "values (%s, %s, %s, now() - make_interval(secs => %s), "
        "        case when %s then now() - interval '1 minute' end, %s, %s)",
        (source, f"w4-{uuid.uuid4().hex[:12]}", priority, hours_ago * _H, claimed, 5 if given_up else 0,
         given_up))


def _rows(rows: list[tuple[Any, ...]], *sources: str) -> dict[str, tuple[Any, ...]]:
    return {row[0]: tuple(row[1:]) for row in rows if row[0] in sources}


# --- outflow -----------------------------------------------------------------------------------------------------


def test_each_week_back_is_read_at_every_offset_and_this_24_h_at_offset_0_only(conn):
    """At ±3 h: half-open windows [end - 24 h, end), end = now() - lag days + offset hours; lag 0 at 0 only."""
    src = "w4-live-outflow"
    _ads(conn, src, hours=-1)                        # this 24 h
    _ads(conn, src, hours=-24)                       # its first instant: inside
    _ads(conn, src, hours=-24 - 1 / _H)              # a second before it: only an offset of lag 0 would hold it
    _ads(conn, src, days=7, hours=-0.5)              # inside the windows ending at 0..+3 h
    _ads(conn, src, days=7, hours=2.5)               # inside the +3 h window only
    _ads(conn, src, days=7, hours=-26.5)             # inside the -3 h window only
    _ads(conn, src, days=7, hours=-27)               # the -3 h window's first instant, the reach's edge
    _ads(conn, src, days=14)                         # a window's end is outside it: +1..+3 h
    _ads(conn, src, days=14, hours=-24)              # a window's start is inside it: -3..0 h

    rows = conn.execute(dl._OUTFLOW_SQL, {"lags": [0, *dl._OUTFLOW_LAGS_D], "align_h": 3}).fetchall()

    windows = {(lag, off): n for source, lag, off, n in rows if source == src}
    assert windows == {(0, 0): 2,
                       (7, -3): 2, (7, 0): 1, (7, 1): 1, (7, 2): 1, (7, 3): 2,
                       (14, -3): 1, (14, -2): 1, (14, -1): 1, (14, 0): 1, (14, 1): 1, (14, 2): 1, (14, 3): 1}


def test_the_check_judges_a_drop_on_the_quietest_window_and_a_spike_on_the_busiest(conn, monkeypatch):
    """A walk that ran late moves its burst out of a fixed window; the windows around the hour keep the baseline."""
    late, busy = "w4-live-late", "w4-live-busy"
    # The check reports the fleet (claims_common.SOURCES); the seeds live under made-up portals.
    monkeypatch.setattr(dl, "SOURCES", (late, busy))
    for lag in dl._OUTFLOW_LAGS_D:
        _ads(conn, late, 60, days=lag, hours=-10)    # the day's background, in every window around the hour
        _ads(conn, late, 300, days=lag, hours=-0.5)  # the walk's burst, half an hour before the hour
        _ads(conn, busy, 150, days=lag, hours=-10)
        _ads(conn, busy, 100, days=lag, hours=0.5)   # a burst half an hour after the hour: offset 0 misses it
    _ads(conn, late, 60, hours=-10)                  # this week's walk has not run yet
    _ads(conn, busy, 600, hours=-10)                 # a catch-up: 4 x the quiet windows, 2.4 x the busiest

    aligned = {c["subject"]: c for c in dl.check_delisting(conn, THRESHOLDS)["details"]["cells"]}
    fixed = {c["subject"]: c for c in dl.check_delisting(
        conn, {**THRESHOLDS, "delisting_outflow_align_h": 0})["details"]["cells"]}

    drop = aligned[f"{late}/outflow"]
    assert (drop["status"], drop["facts"]["baseline"], drop["facts"]["baseline_busy"]) == ("ok", 60, 360)
    assert fixed[f"{late}/outflow"]["status"] == "fail", "60 of a fixed window's 360 is a Problem"
    spike = aligned[f"{busy}/outflow"]
    assert (spike["status"], spike["facts"]["share"], spike["facts"]["baseline_busy"]) == ("ok", 4.0, 250)
    assert fixed[f"{busy}/outflow"]["status"] == "warn" and "a spike" in fixed[f"{busy}/outflow"]["message"]


# --- stuck confirmations -----------------------------------------------------------------------------------------


def test_stuck_takes_each_active_ads_earliest_unrefuted_verdict_and_counts_the_ad_once(conn):
    """A sighting refutes the verdicts before it, never one after it; an ad with two verdicts is one ad."""
    src, other = "w4-live-stuck", "w4-live-stuck-fallback"
    for native, active, seen_h, verdicts in (
            ("a", True, 50, (40, 10)),     # first unrefuted 40 h ago: stuck past the 37 h limit
            ("b", True, 30, (45, 5)),      # 45 h refuted by the 30 h sighting; 5 h stands: waiting
            ("d", True, 1, (3,)),          # refuted by the sighting an hour ago: not waiting
            ("e", True, 60, (45, 42)),     # two verdicts, one ad: stuck, the oldest at 45 h
            ("c", False, 50, (40,)),       # unrefuted, but the ad is inactive already
            ("f", True, 70, ())):          # its page was written, no verdict
        _ad(conn, src, native, active=active, seen_h=seen_h)
        for hours_ago in verdicts:
            _completion(conn, src, native, "gone", hours_ago=hours_ago)
    _completion(conn, src, "f", "written", hours_ago=50)
    _completion(conn, src, "orphan", "gone", hours_ago=50)    # no listing: not an active ad
    _ad(conn, other, "g", active=True, seen_h=40)              # no limit sent for it: the fallback's 25 h
    _completion(conn, other, "g", "gone", hours_ago=30)

    rows = conn.execute(dl._STUCK_SQL, {"sources": [src], "limits_s": [37 * _H],
                                        "fallback_s": 25 * _H}).fetchall()

    assert _rows(rows, src, other) == {src: (3, 2, 45 * _H), other: (1, 1, 30 * _H)}


# --- verify backlog ----------------------------------------------------------------------------------------------


def test_the_backlog_counts_claimed_presence_checks_and_leaves_out_given_up_ones(conn):
    src = "w4-live-verify"
    _queued(conn, src, priority=QUEUE_PRIORITY_VERIFY, hours_ago=2)
    _queued(conn, src, priority=QUEUE_PRIORITY_VERIFY, hours_ago=5, claimed=True)
    _queued(conn, src, priority=QUEUE_PRIORITY_VERIFY, hours_ago=30, given_up=True)
    _queued(conn, src, priority=QUEUE_PRIORITY_NEW, hours_ago=40)

    rows = conn.execute(dl._VERIFY_SQL, {"priority": QUEUE_PRIORITY_VERIFY}).fetchall()

    assert _rows(rows, src) == {src: (2, 1, 5 * _H)}


# --- precision ---------------------------------------------------------------------------------------------------


def test_precision_reads_the_48_72_h_cohort_and_the_2_7_d_pool_against_is_active_now(conn):
    src = "w4-live-precision"
    for native, days, hours_ago, active in (
            ("p1", 0, 50, True), ("p2", 0, 60, False), ("p3", 0, 72, False),  # the cohort, its 72 h edge in
            ("p5", 0, 48, True), ("p6", 0, 47, True),                         # too young: 48 h is open
            ("p7", 0, 100, True), ("p9", 7, 0, True),                         # the pool, its 7 d edge in
            ("p8", 8, 0, True)):                                              # older than the pool
        _ad(conn, src, native, active=active, seen_h=1)
        _completion(conn, src, native, "flipped", days=days, hours_ago=hours_ago)
    _completion(conn, src, "p4", "flipped", hours_ago=55)      # no listing row: not active again
    _ad(conn, src, "g", active=True, seen_h=1)
    _completion(conn, src, "g", "gone", hours_ago=50)          # a verdict, not a flip

    rows = conn.execute(dl._PRECISION_SQL, {"from_h": dl._COHORT_H[0], "to_h": dl._COHORT_H[1],
                                            "pool_d": dl._POOL_D}).fetchall()

    assert _rows(rows, src) == {src: (4, 1, 6, 3)}


# --- walks -------------------------------------------------------------------------------------------------------


def test_a_finished_walk_reached_the_end_when_any_entry_did_and_sums_its_deferrals(conn):
    """The deferrals arm's walk: ANY entry (the coverage arm's portal-level walk needs every one)."""
    src = "w4-live-walks"
    for run_type, started_h, ended_h, by_category in (
            ("index", 2, 1, '[{"walk_reached_end": true, "verify_deferred": 5},'
                            ' {"walk_reached_end": false, "verify_deferred": 2}]'),
            ("index", 8, 7, '[{"walk_reached_end": false}, {"category_main": "byt"}]'),
            ("index", 5, 4, "[]"),                                 # crashed, stamped later by the boot sweep
            ("index", 0.5, None, '[{"walk_reached_end": true}]'),  # still running
            ("index", 6, 5, '{"walk_reached_end": true}'),         # not an array
            ("detail", 3, 2, '[{"walk_reached_end": true}]'),       # a drain, not a walk
            ("index", 15 * 24, 15 * 24 - 1, '[{"walk_reached_end": true}]')):  # older than the 14 days
        conn.execute(
            "insert into scrape_runs (source, run_type, started_at, ended_at, by_category) "
            "values (%s, %s, now() - make_interval(secs => %s), now() - make_interval(secs => %s), %s::jsonb)",
            (src, run_type, started_h * _H, None if ended_h is None else ended_h * _H, by_category))
    now = _now(conn)

    rows = [row for row in conn.execute(dl._WALKS_SQL, {"days": dl._WALK_DAYS}).fetchall() if row[0] == src]

    assert rows == [(src, now - _dt.timedelta(hours=8), False, 0), (src, now - _dt.timedelta(hours=2), True, 7)]
