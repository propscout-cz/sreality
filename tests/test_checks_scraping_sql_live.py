"""The scraping alarm's and the coverage gauge's SQL, executed: each rule read off seeded rows.

The hermetic tests feed canned rows and the replays model these statements in Python, so a clause could
regress with every rail green. Each test seeds rows for portals named only by it (a fresh source, so no
other row enters a count) at hours before the transaction's now(), runs one of scripts/checks/scraping.py's
SQL constants through psycopg with the parameters the check sends, and reads the rule off the rows, in a
transaction that always rolls back. The rules:
- walks: a run is a finished walk only when it ended with a non-empty by_category, and a portal whose runs
  never finished keeps one row with no unit; a unit reached the portal's end in a walk only when every entry
  of its label did (bool_and), its totals are its newest end-reached walk's, and the portal's walk is the
  newest in which every unit did; G is the median start gap of finished walks in the stale window, the open
  gap and an unfinished run never entering it; declared sums the entries, undeclared counts a missing or
  negative total, counted is any total_is_count;
- inflow: written ads by whole days since first_seen_at, and NEW-priority queue rows with no ad of their
  portal written;
- fetch errors: a completion carrying attempts is a failed id, a live failed queue row counts in a window by
  greatest(claimed_at, enqueued_at), and an id given up 3+ times in 7 d is named and still counted;
- held: active ads per portal and category, and those in dwell (a gone verdict newer than the last sighting);
- unnominated: active ads last seen before their unit's newest end-reached walk started (the portal's
  every-unit walk for a category none dates), with no queue row and no completion inside the recheck throttle
  before that start (db.nominable_predicate, the walks' own);
  the deferrals the cell sets aside are that walk's (the subtraction is Python, pinned hermetically);
- trend (common, wave 6): the newest MEASURED result with a cells array is every series' source, and a
  subject it lacks is read from the newest measured result in the window whose cell carries a series.
Runs in CI's migrations lane (`TEST_DATABASE_URL`, `DB_RAILS_REQUIRED=1`); skipped locally.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import uuid
from collections.abc import Iterator
from typing import Any

import pytest

from scraper.db import QUEUE_PRIORITY_CHANGED, QUEUE_PRIORITY_NEW, QUEUE_PRIORITY_VERIFY
from scraper.portal_runner import DECLARED_TOTAL_KEY
from scripts.checks import common
from scripts.checks import scraping as sc
from scripts.verify_pipeline import THRESHOLDS

_DB_URL = os.environ.get("TEST_DATABASE_URL")
_REQUIRED = os.environ.get("DB_RAILS_REQUIRED") == "1"
_UTC = _dt.timezone.utc

pytestmark = pytest.mark.skipif(
    not _DB_URL and not _REQUIRED,
    reason="TEST_DATABASE_URL not set — this rail runs in CI's migrations lane",
)

# _WALKS_SQL's parameters as scraping._ledger sends them, and its row, column by column.
_WALKS = {"recent_days": int(THRESHOLDS["scraping_walk_recent_days"]),
          "stale_days": int(THRESHOLDS["scraping_walk_stale_days"])}
_WALK_COLUMNS = ("source", "started_at", "finished_at", "gap_s", "gaps", "all_reached_at", "category_main",
                 "category_type", "walked_at", "end_at", "declared", "undeclared", "collected", "entries",
                 "recorded", "counted", "upper_bound", "deferred")


@pytest.fixture()
def conn() -> Iterator[Any]:
    """One connection in one transaction, always rolled back: every seed and read share its now()."""
    if not _DB_URL:
        pytest.fail("DB_RAILS_REQUIRED=1 but TEST_DATABASE_URL is not set — the migrations lane is "
                    "misconfigured and this rail would otherwise have skipped green.")
    import psycopg

    connection = psycopg.connect(_DB_URL)
    try:
        yield connection
    finally:
        connection.rollback()
        connection.close()


class _Seed:
    """One test's rows, for portals only it names, at hours before the transaction's now()."""

    def __init__(self, conn: Any) -> None:
        self.conn = conn
        self.now = conn.execute("select now()").fetchone()[0].astimezone(_UTC)
        self._tag = uuid.uuid4().hex[:10]

    def portal(self, name: str) -> str:
        return f"test-{name}-{self._tag}"

    def ago(self, hours: float) -> _dt.datetime:
        return self.now - _dt.timedelta(hours=hours)

    def walk(self, source: str, started_h: float, entries: list[dict[str, Any]], *,
             ended: bool = True, run_type: str = "index") -> None:
        started = self.ago(started_h)
        self.conn.execute("insert into scrape_runs (source, run_type, started_at, ended_at, by_category) "
                          "values (%s, %s, %s, %s, %s::jsonb)",
                          (source, run_type, started, started + _dt.timedelta(minutes=15) if ended else None,
                           json.dumps(entries)))

    def ad(self, source: str, native: str, *, main: str | None = "byt", kind: str | None = "prodej",
           active: bool = True, first_seen_h: float = 100.0, last_seen_h: float = 1.0) -> None:
        self.conn.execute("insert into listings (source, source_id_native, raw_json, category_main, "
                          "category_type, is_active, first_seen_at, last_seen_at) "
                          "values (%s, %s, '{}'::jsonb, %s, %s, %s, %s, %s)",
                          (source, native, main, kind, active, self.ago(first_seen_h), self.ago(last_seen_h)))

    def queued(self, source: str, native: str, *, priority: int = QUEUE_PRIORITY_NEW, enqueued_h: float = 1.0,
               claimed_h: float | None = None, attempts: int = 0) -> None:
        self.conn.execute("insert into listing_detail_queue (source, native_id, priority, enqueued_at, "
                          "claimed_at, attempts) values (%s, %s, %s, %s, %s, %s)",
                          (source, native, priority, self.ago(enqueued_h),
                           None if claimed_h is None else self.ago(claimed_h), attempts))

    def completed(self, source: str, native: str, *, outcome: str = "written", attempts: int = 0,
                  completed_h: float = 1.0) -> None:
        at = self.ago(completed_h)
        self.conn.execute("insert into detail_queue_completions (source, native_id, attempts, enqueued_at, "
                          "claimed_at, completed_at, outcome) values (%s, %s, %s, %s, %s, %s, %s)",
                          (source, native, attempts, at - _dt.timedelta(hours=1), at, at, outcome))


@pytest.fixture()
def seed(conn: Any) -> _Seed:
    return _Seed(conn)


def _entry(main: str | None, kind: str | None = "prodej", *, reached: bool = True,
           declared: int | None = 1_000, collected: int = 1_000, counted: bool | None = False,
           upper: bool = False, deferred: int = 0) -> dict[str, Any]:
    """One by_category entry keyed as portal_runner writes it; counted None leaves total_is_count out."""
    entry = {"category_main": main, "category_type": kind, "walk_reached_end": reached,
             DECLARED_TOTAL_KEY: declared, "collected": collected, "declared_is_upper_bound": upper,
             "verify_deferred": deferred}
    if counted is not None:
        entry["total_is_count"] = counted
    return entry


def _end(anchor: _dt.datetime, *, reached: bool = True) -> dict[str, Any]:
    """The entry that ends a walk cycle (scope {}, NULL labels), keyed as portal_runner writes it."""
    return {**_entry(None, None, reached=reached, declared=0, collected=0), "scope": {},
            "cycle_started_at": anchor.isoformat()}


def _walks(conn: Any, *sources: str) -> dict[tuple[str, str | None, str | None], dict[str, Any]]:
    """_WALKS_SQL as the check sends it: these portals' rows by (portal, category_main, category_type)."""
    rows = [dict(zip(_WALK_COLUMNS, row, strict=True))
            for row in conn.execute(sc._WALKS_SQL, _WALKS).fetchall() if row[0] in sources]
    return {(r["source"], r["category_main"], r["category_type"]): r for r in rows}


def _inflow(conn: Any, source: str) -> dict[tuple[str, int], int]:
    """_INFLOW_SQL as the check sends it, for one portal: {(arm, whole days back): ads}."""
    rows = conn.execute(sc._INFLOW_SQL, {"sources": [source], "days": 7 * (sc._BASELINE_WEEKS + 1),
                                         "new": QUEUE_PRIORITY_NEW}).fetchall()
    assert {row[1] for row in rows} <= {source}
    return {(kind, int(days)): int(n) for kind, _, days, n in rows}


def _fetch_errors(conn: Any, source: str) -> tuple[Any, ...]:
    """_FETCH_ERRORS_SQL as the check sends it, for one portal: its row less the portal."""
    rows = conn.execute(sc._FETCH_ERRORS_SQL, {"sources": [source], "poison": sc._POISON_GIVE_UPS}).fetchall()
    assert [row[0] for row in rows] == [source]
    return tuple(rows[0][1:])


# --- walks -----------------------------------------------------------------------------------


def test_a_run_is_a_finished_walk_only_when_it_ended_with_categories(seed: _Seed) -> None:
    """A run still going or one that ended with an empty by_category dates no unit; a detail run, or a walk
    older than 30 d, is not read; a portal whose runs never finished keeps one row with no unit."""
    s, bare = seed.portal("finished"), seed.portal("bare")
    seed.walk(s, 30, [_entry("byt")])
    seed.walk(s, 20, [_entry("dum")], ended=False)
    seed.walk(s, 10, [])
    seed.walk(s, 5, [_entry("pozemek")], run_type="detail")
    seed.walk(s, 31 * 24 + 2, [_entry("komercni")])
    seed.walk(bare, 2, [], ended=False)

    walks = _walks(seed.conn, s, bare)
    assert set(walks) == {(s, "byt", "prodej"), (bare, None, None)}
    unit = walks[(s, "byt", "prodej")]
    assert (unit["started_at"], unit["finished_at"], unit["walked_at"], unit["end_at"]) == (
        seed.ago(10), seed.ago(30), seed.ago(30), seed.ago(30))
    assert walks[(bare, None, None)] == {
        "source": bare, "started_at": seed.ago(2), "finished_at": None, "gap_s": None, "gaps": 0,
        "all_reached_at": None, "category_main": None, "category_type": None, "walked_at": None,
        "end_at": None, "declared": None, "undeclared": 0, "collected": None, "entries": 0, "recorded": 0,
        "counted": False, "upper_bound": False, "deferred": 0}


def test_a_unit_reached_the_end_only_when_every_entry_of_its_label_did(seed: _Seed) -> None:
    """Labels repeat across a walk's entries: one entry short of the end, or recording no verdict, leaves the
    unit's newest end-reached walk (and the totals read with it) at the older walk."""
    s = seed.portal("labels")
    seed.walk(s, 8, [_entry("dum", declared=800), _entry("dum", declared=150), _entry("byt", declared=4_900)])
    newest = [_entry("dum", declared=900), _entry("dum", reached=False, declared=100),
              _entry("byt", declared=5_000), _entry("pozemek")]
    del newest[-1]["walk_reached_end"]
    seed.walk(s, 2, newest)

    walks = _walks(seed.conn, s)
    got = {key[1]: (r["walked_at"], r["end_at"], r["declared"], r["entries"]) for key, r in walks.items()}
    assert got == {"dum": (seed.ago(2), seed.ago(8), 950, 2), "byt": (seed.ago(2), seed.ago(2), 5_000, 1),
                   "pozemek": (seed.ago(2), None, None, 0)}


def test_a_cycle_end_is_dated_at_its_anchor_and_the_newest_closed_one_is_the_portals_walk(seed: _Seed) -> None:
    """A walk cycle cut once ends in the run that finished it (11 h ago); its orphans were judged against its
    anchor (12 h ago), so that dates the end entry's unit and the portal's every-unit walk. A cycle that ended
    short, or a run whose entries all reached the end without ending a cycle, dates neither."""
    s = seed.portal("cycles")
    seed.walk(s, 30, [_entry("byt"), _end(seed.ago(30))])
    seed.walk(s, 12, [_entry("byt")])
    seed.walk(s, 11, [_entry("dum"), _end(seed.ago(12))])
    seed.walk(s, 4, [_entry("byt"), _entry("dum", reached=False), _end(seed.ago(4), reached=False)])
    seed.walk(s, 2, [_entry("byt"), _entry("dum")])

    walks = _walks(seed.conn, s)
    assert {r["all_reached_at"] for r in walks.values()} == {seed.ago(12)}
    assert (walks[(s, None, None)]["walked_at"], walks[(s, None, None)]["end_at"]) == (seed.ago(4), seed.ago(12))


def test_the_walk_gap_is_the_median_start_gap_of_finished_walks_in_the_stale_window(seed: _Seed) -> None:
    """Finished walks 10, 16, 22 and 34 h ago: G = 6 h over 3 gaps. The open 10 h since the newest, the run
    still going since 1 h ago and the walk 15 d ago (past the 14 d window) would each move it."""
    s = seed.portal("gap")
    for hours in (10, 16, 22, 34, 15 * 24):
        seed.walk(s, hours, [_entry("byt")])
    seed.walk(s, 1, [_entry("byt")], ended=False)

    (unit,) = _walks(seed.conn, s).values()
    assert (unit["gap_s"], unit["gaps"]) == (6 * 3600.0, 3)
    assert (unit["started_at"], unit["finished_at"]) == (seed.ago(1), seed.ago(10))


def test_a_units_totals_sum_its_entries_and_count_what_cannot_be_judged(seed: _Seed) -> None:
    """declared, collected and deferred sum the entries; undeclared counts a total that is null, absent or
    negative; recorded counts the entries carrying total_is_count; counted and upper_bound are any entry's."""
    s = seed.portal("totals")
    absent = _entry("dum", collected=7)
    del absent[DECLARED_TOTAL_KEY]
    seed.walk(s, 3, [_entry("byt", declared=600, collected=590, deferred=3),
                     _entry("byt", declared=400, collected=400, upper=True, deferred=4),
                     _entry("dum", declared=500, collected=480, counted=True),
                     _entry("dum", declared=None, collected=10),
                     _entry("dum", declared=-1, collected=5, counted=None), absent])

    walks = _walks(seed.conn, s)
    byt, dum = walks[(s, "byt", "prodej")], walks[(s, "dum", "prodej")]
    assert tuple(byt[c] for c in ("declared", "undeclared", "collected", "entries", "recorded", "counted",
                                  "upper_bound", "deferred")) == (1_000, 0, 990, 2, 2, False, True, 7)
    assert (dum["undeclared"], dum["entries"], dum["recorded"], dum["counted"]) == (3, 4, 3, True)


# --- inflow ----------------------------------------------------------------------------------


def test_inflow_counts_written_ads_by_whole_days_since_their_discovery(seed: _Seed) -> None:
    """first_seen_at 23.5 h ago is day 0 and 25 h day 1, 167 h day 6 and 170 h day 7; an ad inactive since is
    still discovered; 36 d is past the 35 d the check reads, and another portal's ad is not read."""
    s, other = seed.portal("written"), seed.portal("other")
    for n, hours in enumerate((1, 1, 23.5, 25, 167, 170, 170, 36 * 24)):
        seed.ad(s, f"w{n}", first_seen_h=hours)
    seed.ad(s, "inactive", first_seen_h=2, active=False)
    seed.ad(other, "w0", first_seen_h=1)

    assert _inflow(seed.conn, s) == {("written", 0): 4, ("written", 1): 1, ("written", 6): 1,
                                     ("written", 7): 2}


def test_inflow_counts_new_priority_rows_no_ad_of_their_portal_was_written_for(seed: _Seed) -> None:
    """A stopped drain is not lost discovery: NEW rows still queued count by enqueued_at, one whose ad its
    portal wrote counts once (as written), and refresh, verify or week-old rows never count."""
    s, other = seed.portal("queued"), seed.portal("other")
    seed.queued(s, "n1", enqueued_h=1)
    seed.queued(s, "n2", enqueued_h=30)
    seed.queued(s, "n3", enqueued_h=2)
    seed.ad(s, "n3", first_seen_h=2)
    seed.queued(s, "n4", enqueued_h=3)
    seed.ad(other, "n4", first_seen_h=3)
    seed.queued(s, "c1", priority=QUEUE_PRIORITY_CHANGED)
    seed.queued(s, "v1", priority=QUEUE_PRIORITY_VERIFY)
    seed.queued(s, "old", enqueued_h=8 * 24)
    seed.queued(other, "n5")

    assert _inflow(seed.conn, s) == {("written", 0): 1, ("queued", 0): 2, ("queued", 1): 1}


# --- fetch errors ----------------------------------------------------------------------------


def test_an_id_failed_when_a_completion_carries_attempts_or_a_live_row_has_failed(seed: _Seed) -> None:
    """Per id over both runtimes: a completion with attempts > 0 failed; a live row with attempts counts at
    greatest(claimed_at, enqueued_at), so one enqueued 10 d ago and claimed 3 d ago is in the week; an
    untried row is no attempt, and 8 d is past the week."""
    s = seed.portal("fetch")
    seed.completed(s, "a", completed_h=1)
    seed.completed(s, "a", attempts=1, completed_h=72)
    seed.completed(s, "b", attempts=2, completed_h=2)
    seed.completed(s, "c", outcome="gone", completed_h=72)
    seed.completed(s, "d", outcome="gone", attempts=1, completed_h=72)
    seed.completed(s, "e", completed_h=8 * 24)
    seed.queued(s, "f", attempts=1, enqueued_h=72, claimed_h=2)
    seed.queued(s, "g", attempts=2, enqueued_h=3)
    seed.queued(s, "h", attempts=1, enqueued_h=9 * 24, claimed_h=8 * 24)
    seed.queued(s, "i", attempts=1, enqueued_h=10 * 24, claimed_h=72)
    seed.queued(s, "j")

    assert _fetch_errors(seed.conn, s) == (4, 3, 7, 6, None)


def test_an_id_given_up_three_times_in_seven_days_is_named_and_still_counted(seed: _Seed) -> None:
    """In the week: p given up 4 times (a 5th 9 d ago is not), q 3, r 2, s 2 (its 3rd 8 d ago)."""
    s = seed.portal("poison")
    for native, hours in (("p", (2, 26, 50, 74, 9 * 24)), ("q", (2, 26, 50)), ("r", (2, 26)),
                          ("s", (2, 26, 8 * 24))):
        for h in hours:
            seed.completed(s, native, outcome="given_up", attempts=5, completed_h=h)

    assert _fetch_errors(seed.conn, s) == (4, 4, 4, 4, [["p", 4], ["q", 3]])


# --- coverage --------------------------------------------------------------------------------


def test_held_counts_active_ads_per_unit_and_those_in_dwell_once_each(seed: _Seed) -> None:
    """In dwell: active, with a gone verdict newer than the last sighting (two verdicts are one ad); a later
    sighting refutes a verdict, and an ad with no category pairs with its own verdicts."""
    s = seed.portal("held")
    for native in ("x1", "x2", "x3"):
        seed.ad(s, native, last_seen_h=5)
    seed.ad(s, "x4", last_seen_h=5, active=False)
    seed.ad(s, "y1", main="dum")
    seed.ad(s, "y2", main="dum")
    seed.ad(s, "z1", main=None, kind=None, last_seen_h=10)
    for native, hours in (("x1", 2), ("x1", 1), ("x2", 6), ("x4", 2), ("z1", 3)):
        seed.completed(s, native, outcome="gone", completed_h=hours)
    seed.completed(s, "x3", completed_h=1)

    rows = seed.conn.execute(sc._HELD_SQL, {"sources": [s]}).fetchall()
    assert {(main, kind): (held, pending) for _, main, kind, held, pending in rows} == {
        ("byt", "prodej"): (3, 1), ("dum", "prodej"): (2, 0), (None, None): (1, 1)}


def test_unnominated_ads_are_active_unseen_since_their_walk_started_and_neither_queued_nor_checked(
        seed: _Seed) -> None:
    """byt's newest end-reached walk started 10 h ago, dum's 30 h ago (also the portal's every-unit walk, which
    dates the categories no end-reached walk covers, and its cycle's end, which dates the NULL labels). Counted:
    a1 (last seen 12 h ago), a7 (last seen 50 h ago, checked 40 h ago: after its sighting but over 24 h before
    the walk, which nominated it), b1 (35 h), c1 (40 h) and p1 (40 h, unwalked). Not: a2 seen since, a3 queued, a4 and a5 checked inside the 24 h
    before the walk (11 h and 13 h ago, after and before their sighting: the walk's throttle skipped both),
    a6 inactive, b2 and c2 seen since; and nothing of a portal no walk reached the end of (sent with no
    start, as every portal without a walk is)."""
    s, idle = seed.portal("unnominated"), seed.portal("idle")
    seed.walk(s, 30, [_entry("byt", deferred=5), _entry("dum"), _end(seed.ago(30))])
    seed.walk(s, 10, [_entry("byt", deferred=2), _entry("dum", reached=False),
                      _entry("pozemek", reached=False)])
    for native, hours in (("a1", 12), ("a2", 5), ("a3", 12), ("a4", 12), ("a5", 12)):
        seed.ad(s, native, last_seen_h=hours)
    seed.ad(s, "a6", last_seen_h=12, active=False)
    seed.queued(s, "a3", priority=QUEUE_PRIORITY_VERIFY)
    seed.completed(s, "a4", outcome="gone", completed_h=11)
    seed.completed(s, "a5", outcome="gone", completed_h=13)
    seed.ad(s, "a7", last_seen_h=50)
    seed.completed(s, "a7", outcome="gone", completed_h=40)
    seed.ad(s, "b1", main="dum", last_seen_h=35)
    seed.ad(s, "b2", main="dum", last_seen_h=20)
    seed.ad(s, "c1", main=None, kind=None, last_seen_h=40)
    seed.ad(s, "c2", main=None, kind=None, last_seen_h=20)
    seed.ad(s, "p1", main="pozemek", last_seen_h=40)
    seed.ad(idle, "i1", last_seen_h=40)

    reached = [r for r in _walks(seed.conn, s).values() if r["end_at"] is not None]
    assert {r["category_main"]: (r["end_at"], r["deferred"]) for r in reached} == {
        "byt": (seed.ago(10), 2), "dum": (seed.ago(30), 0), None: (seed.ago(30), 0)}, \
        "deferrals: the newest end-reached walk's"
    rows = seed.conn.execute(sc._UNNOMINATED_SQL, {  # as scraping._unnominated_cells sends them
        "sources": [r["source"] for r in reached], "mains": [r["category_main"] for r in reached],
        "types": [r["category_type"] for r in reached], "starts": [r["end_at"] for r in reached],
        "portal_sources": [s, idle], "portal_starts": [reached[0]["all_reached_at"], None]}).fetchall()
    assert rows == [(s, 5, 1, seed.ago(50))]


def test_the_trend_rolls_from_the_newest_measured_result_and_looks_back_for_a_missing_one(
        seed: _Seed) -> None:
    """common.TREND_SQL: the newest result that measured something (an unknown run, or one with no cells
    array, never resets a series). common.TREND_LOOKBACK_SQL: for a subject that result lacks, the newest
    measured result since `since` whose cell carries a series; known subjects and older rows never."""
    key = seed.portal("coverage")  # the statements' own key parameter; a fresh one keeps other rows out
    newest = [{"subject": "x", "facts": {"trend": [["2026-10-18", 5, "ok"]]}}]
    for hours, status, cells in (
            (40 * 24, "ok", [{"subject": "old", "facts": {"trend": [["2026-09-01", 1, "ok"]]}}]),
            (50, "ok", [{"subject": "gone", "facts": {"trend": [["2026-10-16", 2, "ok"]]}},
                        {"subject": "x", "facts": {"trend": [["2026-10-16", 3, "ok"]]}}]),
            (30, "warn", [{"subject": "gone", "facts": {"trend": [["2026-10-17", 4, "warn"]]}},
                          {"subject": "bare", "facts": {}}]),
            (20, "ok", newest),
            (10, "unknown", [{"subject": "x", "facts": {"trend": []}}]),
            (5, "ok", None)):
        seed.conn.execute("insert into pipeline_check_results (run_at, check_key, status, details) "
                          "values (%s, %s, %s, %s::jsonb)",
                          (seed.ago(hours), key, status, json.dumps({"cells": cells})))

    assert seed.conn.execute(common.TREND_SQL, {"key": key}).fetchall() == [(newest,)]
    rows = seed.conn.execute(common.TREND_LOOKBACK_SQL, {  # as common._Series sends it
        "key": key, "since": seed.ago(30 * 24), "known": ["x"]}).fetchall()
    assert rows == [("gone", [["2026-10-17", 4, "warn"]])]
