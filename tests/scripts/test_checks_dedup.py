"""Hermetic tests for scripts.checks.dedup: the engine's state and its nine cells."""

# No DB: the wave-3 fake connection answers each statement by the SQL constant it carries, through
# the per-check clock. At build time every new statement was EXPLAINed read-only against
# production, the imported engine statements bound as this check binds them.

from __future__ import annotations

import ast
import datetime as _dt
import inspect
import re
from typing import Any

import pytest

from autodedup import apply, incremental, incremental_lane, rt_lease, ui_sql
from autodedup import incremental_sql as isql
from autodedup.apply_sql import SETTING_SQL
from autodedup.incremental_scope import parse_scope
from scripts.checks import common
from scripts.checks import dedup as dd
from scripts.checks import platform as plat
from scripts.verify_pipeline import CHECKS, THRESHOLDS
from tests.scripts.test_checks_platform import _Conn
from tests.scripts.test_checks_platform_replay import _incidents, _told
from toolkit import property_identity, system_alerts

_UTC = _dt.timezone.utc
_NOW = _dt.datetime(2026, 10, 19, 9, 0, tzinfo=_UTC)

# The dedup keys exactly as the wave-5 contract spells them (D4); the check runs on the harness's
# own values, which a rail below pins to these.
_CONTRACT: dict[str, Any] = {
    "dedup_rulings_fail": 1, "dedup_build_window_h": 3, "dedup_build_eta_warn_d": 7,
    "dedup_pause_warn_h": 48, "dedup_lag_warn": 0.01, "dedup_lag_fail": 0.10,
    "dedup_lag_min_n": 20, "dedup_lease_fail_past_expiry_min": 10, "dedup_splits_warn": 0.02,
    "dedup_splits_min_groups": 100,
}
T: dict[str, Any] = {key: THRESHOLDS[key] for key in _CONTRACT}

_INTERVAL, _APPLY = apply.LANE_INTERVAL_SETTING, apply.SCOPE_SETTING
_FLAG, _SEED = incremental.bootstrap_key(dd.GENERATION), incremental.seed_version_key(dd.GENERATION)
_LANE = incremental_lane.scope_setting_key(dd.GENERATION)
_ENGINE = incremental_lane.engine_key(dd.GENERATION)
_OPEN = {"blocks": ["town:563510", "quarter:490245"], "category_types": ["prodej", "pronajem"],
         "all_blocks": False, "listing_ids": None, "max_clusters_per_run": 600}
_CLOSED = {**_OPEN, "blocks": []}
_LANE_SCOPE = [{"grain": "obec", "code": 563510}, {"grain": "cast_obce", "code": 490245}]
_SUBJECTS = ["state", "stream", "build", "merge_pause", "decision_lag", "lease", "rulings/ads",
             "rulings/brokers", "splits"]


def _ago(**delta: float) -> _dt.datetime:
    return _NOW - _dt.timedelta(**delta)


def _lease(holder: str, *, taken: _dt.datetime, ttl_s: float = 2400.0,
           ended: _dt.datetime | None = None) -> tuple[Any, ...]:
    """RT_LEASE_READ_SQL's row: live until `ended` (a release or its TTL), else its TTL."""
    expires = ended or taken + _dt.timedelta(seconds=ttl_s)
    return holder, taken, expires, expires > _NOW


def _pass(started: _dt.datetime, **kw: Any) -> tuple[Any, ...]:
    """A worker pass's lease, `<host>:<pid>:<the second it started>`."""
    return _lease(f"1f2e3d4c5b6a:1:{int(started.timestamp())}", taken=started, **kw)


def _arrivals(n: int, undecided: int, *, wait_min: float = 6.0, older: int = 0,
              at: _dt.datetime = _NOW) -> tuple[tuple[Any, ...], list[tuple[Any, ...]]]:
    """RT_NEW_LISTINGS_SQL's row for `n` in-scope ads first seen 1-24 h before `at` (after `older`
    ones first seen 24-25 h before) and RT_FP_READ_SQL's rows for all but the newest `undecided`."""
    ids, stamps = [], []
    for i in range(older):
        ids.append(1_000 + i)
        stamps.append(at - _dt.timedelta(hours=24.5))
    for i in range(n):
        ids.append(2_000 + i)
        stamps.append(at - _dt.timedelta(hours=24 - 23 * (i + 0.5) / n))
    decided = [(listing_id, stamp) for listing_id, stamp in zip(ids[older:], stamps[older:])
               ][: n - undecided]
    rows = [(listing_id, *([None] * 13), stamp.timestamp() + wait_min * 60, None)
            for listing_id, stamp in decided]
    rows += [(listing_id, *([None] * 13), stamp.timestamp() + wait_min * 60, None)
             for listing_id, stamp in zip(ids[:older], stamps[:older])]
    return (max(ids, default=0), len(ids) + 3_000, ids, stamps), rows


_DEFAULT: dict[str, Any] = {
    "interval": 60, "apply_scope": _OPEN, "history": [(_INTERVAL, 0, _ago(hours=30))],
    "flag": False, "flag_at": _ago(days=2), "seed": incremental.SEED_VERSION,
    "seed_at": _ago(days=4), "lane_scope": _LANE_SCOPE,
    "engine": {"digest": "29d2a9025448563f", "since": _ago(days=2).isoformat()},
    "commit_at": _ago(minutes=3), "present": True, "seeded": True,
    "lease": _pass(_ago(minutes=2)), "beat": (_ago(seconds=10), _ago(hours=1)),
    "merges": [("autodedup", 941, 6_693, _ago(minutes=10), 149, 0, 0),
               ("operator", 0, 16, _ago(days=4), 21, 0, 0)],
    "rulings": (765, 1, 0, 3), "brokers": (0, 0, 0), "decisions": 0, "backlog": 0,
    "walked": ["obec:563510", "cast_obce:490245"], "floor": 19_340_583,
    "arrivals": _arrivals(400, 0), "previous": None, "previous_at": _ago(minutes=10),
}


def _answers(**overrides: Any) -> dict[str, Any]:
    """Every statement's answer for one world: a healthy merging engine unless overridden."""
    w = {**_DEFAULT, **overrides}
    settings = {_INTERVAL: w["interval"], _APPLY: w["apply_scope"]}
    control = {_FLAG: w["flag"], _SEED: w["seed"], _LANE: w["lane_scope"], _ENGINE: w["engine"]}
    stamps = {_FLAG: w["flag_at"], _SEED: w["seed_at"], incremental_lane.CURSOR_NEW: w["commit_at"]}
    window, fp = w["arrivals"]
    return {
        isql.RT_STORE_PRESENT_SQL: [(w["present"],)],
        SETTING_SQL: lambda p: [] if settings.get(p["key"]) is None else [(settings[p["key"]],)],
        dd._HISTORY_SQL: list(w["history"]),
        ui_sql.LIVE_STREAM_SQL: lambda p: [(k, control[k]) for k in p["keys"]
                                           if control.get(k) is not None],
        dd._STAMPS_SQL: lambda p: [(k, stamps[k]) for k in [*p["settings"], *p["cursors"]]
                                   if stamps.get(k) is not None
                                   and (k in p["cursors"] or control.get(k) is not None)],
        isql.RT_CALIBRATION_PRESENT_SQL: [("rt", "c0ffee", _ago(days=4))] if w["seeded"] else [],
        isql.RT_LEASE_READ_SQL: [w["lease"]] if w["lease"] else [],
        dd._WORKER_SQL: [w["beat"]] if w["beat"] else [],
        dd._MERGES_SQL: list(w["merges"]),
        dd._RULINGS_SQL: [w["rulings"]],
        dd._BROKER_SUPPRESSION_SQL: [w["brokers"]],
        dd._DECISIONS_SQL: [(w["decisions"],)],
        isql.RT_SCOPE_BACKLOG_SQL: [(w["backlog"],)],
        isql.RT_SCOPE_SCAN_SEEN_SQL: [(block,) for block in w["walked"]],
        dd._ARRIVAL_FLOOR_SQL: [(w["floor"],)],
        isql.RT_NEW_LISTINGS_SQL: [window],
        isql.RT_FP_READ_SQL: lambda p: [row for row in fp if row[0] in set(p["ids"])],
        common.PREVIOUS_CELLS_SQL: ([] if w["previous"] is None
                                    else [(w["previous_at"], w["previous"])]),
    }


def _check(**overrides: Any) -> tuple[dict[str, Any], _Conn]:
    conn = _Conn(_answers(**overrides), now=_NOW)
    return dd.check_dedup(conn, T), conn


def _cells(result: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {c["subject"]: c for c in result["details"]["cells"]}


def _cell(subject: str, **overrides: Any) -> dict[str, Any]:
    return _cells(_check(**overrides)[0])[subject]


def _params(conn: _Conn, sql: str) -> Any:
    return next(params for sent, params, _ in conn.sent if sent is sql)


def _building(**overrides: Any) -> dict[str, Any]:
    """A build seeded `flag_at` ago, the lane on since long before."""
    return {"flag": True, "flag_at": _ago(hours=10), "seed_at": _ago(hours=10), **overrides}


def _held(subject: str, status: str, **facts: Any) -> dict[str, Any]:
    """One cell of this check's previous stored result."""
    return common.cell(subject, status, "a reading", facts=facts)


def _reopened(**overrides: Any) -> dict[str, Any]:
    """Passes commit again after a stall, the engine change of 40 min ago still excusing the day,
    and this check's previous decision-lag reading a Problem."""
    return {"engine": {"digest": "29d2", "since": _ago(minutes=40).isoformat()},
            "commit_at": _ago(minutes=2), "previous": [_held("decision_lag", "fail")],
            **overrides}


# --- the state ----------------------------------------------------------------------------------


@pytest.mark.parametrize("interval", [None, 0, -5, "0", "x", {"seconds": 60}])
def test_an_interval_absent_unparseable_or_not_above_zero_is_off(interval: Any) -> None:
    state = _cell("state", interval=interval, flag=True)
    assert state["status"] == "ok" and state["value"] is None
    assert state["facts"]["state"] == "off" and state["message"].startswith("off ")


def test_off_wins_over_a_build_and_a_build_over_the_apply_scope() -> None:
    assert _cell("state", interval=0, flag=True)["facts"]["state"] == "off"
    assert _cell("state", flag=True)["facts"]["state"] == "building"
    assert _cell("state", flag="true")["facts"]["state"] == "building"
    assert _cell("state", flag="yes")["facts"]["state"] == "merging"
    assert _cell("state")["facts"]["state"] == "merging"
    assert _cell("state", apply_scope=_CLOSED)["facts"]["state"] == "idle"


@pytest.mark.parametrize(("scope", "state"), [
    (_OPEN, "merging"), ({**_OPEN, "category_types": []}, "idle"), (_CLOSED, "idle"),
    ({**_OPEN, "blocks": None, "all_blocks": True}, "merging"),
    ({**_OPEN, "blocks": None, "listing_ids": [7]}, "merging"), (None, "idle"),
    ({**_OPEN, "blocks": ["somewhere:1"]}, "idle"), ({**_OPEN, "bogus": 1}, "idle"),
    ("not a scope", "idle")])
def test_the_engine_merges_only_while_the_apply_scope_admits_a_live_merge(scope: Any,
                                                                          state: str) -> None:
    assert _cell("state", apply_scope=scope)["facts"]["state"] == state


@pytest.mark.parametrize(("world", "state"), [({"interval": 0}, "off"), (_building(), "building"),
                                              ({}, "merging"), ({"apply_scope": _CLOSED}, "idle")])
def test_every_cell_carries_the_state_as_a_fact_and_off_or_building_read_ok(
        world: dict[str, Any], state: str) -> None:
    """All nine cells, in every state; the state cell itself is never unknown."""
    cells = _cells(_check(**world)[0])
    assert cells["state"]["status"] == "ok"
    assert {subject: c["facts"]["state"] for subject, c in cells.items()} == dict.fromkeys(
        _SUBJECTS, state)


@pytest.mark.parametrize("world", [{"interval": 0}, _building(), {}, {"apply_scope": _CLOSED},
                                   {"present": False}, {"seeded": False}, {"lease": None}])
def test_every_cell_is_present_in_every_state(world: dict[str, Any]) -> None:
    """A subject that drops out strands its incident for seven days: none ever does."""
    out, _ = _check(**world)
    assert [c["subject"] for c in out["details"]["cells"]] == _SUBJECTS
    assert all(c["portal"] is None for c in out["details"]["cells"])
    assert out["check_key"] == "dedup"


def test_the_state_counts_from_the_change_that_began_it() -> None:
    """History stores the OLD value: a row of 60 replaced 5 h ago is the brake going on then."""
    off = _cell("state", interval=0, history=[(_INTERVAL, 60, _ago(hours=5)),
                                              (_INTERVAL, 0, _ago(hours=30))])
    assert off["message"] == "off since 5.0 h ago"
    assert off["facts"]["state_since"] == _ago(hours=5).isoformat()
    lifted = _cell("state", **_building(history=[(_INTERVAL, 0, _ago(hours=2))]))
    assert lifted["message"] == "building since 2.0 h ago"
    seeded = _cell("state", **_building())
    assert seeded["message"] == "building since 10.0 h ago"
    opened = _cell("state", history=[(_APPLY, _CLOSED, _ago(hours=3)),
                                     (_INTERVAL, 0, _ago(hours=30))])
    assert opened["message"] == "merging since 3.0 h ago"
    widened = _cell("state", history=[(_APPLY, {**_OPEN, "blocks": ["town:563510"]},
                                       _ago(hours=3)), (_INTERVAL, 0, _ago(hours=30))])
    assert widened["message"] == "merging since 30.0 h ago"
    closed = _cell("state", apply_scope=_CLOSED, history=[(_APPLY, _OPEN, _ago(hours=4))])
    assert closed["message"] == "idle since 4.0 h ago"
    assert _cell("state", history=[])["message"] == "merging since 2.0 d ago"


def test_a_state_with_no_change_on_record_says_so() -> None:
    state = _cell("state", interval=0, history=[])
    assert state["message"] == ("off (no change of the brake, the build or the apply scope in "
                                "28 d)")
    assert state["facts"]["state_since"] is None


def test_the_state_cell_carries_the_facts_the_engine_panel_renders() -> None:
    state = _cell("state")
    assert state["facts"] == {
        "state": "merging", "state_since": _ago(hours=30).isoformat(), "interval_s": 60,
        "scope_blocks": 2, "scope_all_blocks": False, "seed_version": incremental.SEED_VERSION,
        "newest_merge_age_s": 600, "last_commit_age_s": 180, "merges_24h_engine": 941,
        "merges_7d_engine": 6_693, "merges_24h_operator": 0, "merges_7d_operator": 16}


def test_the_newest_merge_on_the_state_is_the_engines_even_when_yours_came_later() -> None:
    state = _cell("state", merges=[("autodedup", 941, 6_693, _ago(hours=5), 149, 0, 0),
                                   ("operator", 3, 16, _ago(minutes=10), 21, 0, 0)])
    assert state["facts"]["newest_merge_age_s"] == 5 * 3600
    assert (state["facts"]["merges_24h_operator"], state["facts"]["merges_7d_operator"]) == (3, 16)


def test_the_state_says_when_no_pass_has_committed_a_new_ad_for_an_hour() -> None:
    """The stamp is the new feed's cursor, which moves only when new ads arrive: said so."""
    stale = _cell("state", commit_at=_ago(minutes=65))
    assert stale["message"] == "merging since 30.0 h ago; no pass has committed a new ad for 65 min"
    assert stale["facts"]["last_commit_age_s"] == 3_900
    assert _cell("state", commit_at=_ago(minutes=59))["message"] == "merging since 30.0 h ago"
    for world in ({"interval": 0}, _building()):
        assert "no pass" not in _cell("state", commit_at=_ago(hours=5), **world)["message"]


def test_the_ledger_is_read_by_a_positive_source_filter_with_births_left_out() -> None:
    _, conn = _check()
    assert _params(conn, dd._MERGES_SQL) == {
        "old_d": 14, "slack_h": 1, "young_d": 7, "sources": [apply.MERGE_SOURCE, "operator"],
        "birth_reasons": ["ingest_grouping", "split_new"],
        "unapply_by": apply.UNAPPLY_BY_PREFIX}


def test_the_history_reads_both_settings_whose_changes_date_the_state() -> None:
    _, conn = _check()
    assert _params(conn, dd._HISTORY_SQL) == {"keys": [_INTERVAL, _APPLY], "days": 28}
    assert _params(conn, ui_sql.LIVE_STREAM_SQL) == {"keys": [_FLAG, _SEED, _LANE, _ENGINE]}
    assert _params(conn, dd._STAMPS_SQL) == {"settings": [_FLAG, _SEED],
                                             "cursors": [incremental_lane.CURSOR_NEW]}


# --- the stream ---------------------------------------------------------------------------------


def test_a_live_stream_is_ok_and_an_off_lane_reads_no_calibration() -> None:
    assert _cell("stream")["status"] == "ok"
    assert _cell("stream")["message"] == f"the stream is live (seed {incremental.SEED_VERSION})"
    out, conn = _check(interval=0)
    assert _cells(out)["stream"]["status"] == "ok"
    assert _cells(out)["stream"]["message"] == "off: the stream is judged while the lane runs"
    assert isql.RT_CALIBRATION_PRESENT_SQL not in {sql for sql, _, _ in conn.sent}


def test_a_seed_version_the_code_does_not_take_is_watch_and_names_both() -> None:
    """A deploy that bumps SEED_VERSION stops production merges silently: the only signal."""
    stream = _cell("stream", seed="w5")
    assert stream["status"] == "warn" and stream["value"] is None
    assert stream["message"] == (f"the engine's store was built by seed w5 and this code needs "
                                 f"{incremental.SEED_VERSION}: it decides but merges nothing "
                                 "until it is seeded again (Watch)")
    assert _cell("stream", seed=None)["status"] == "warn"


def test_an_unseeded_lane_and_an_absent_store_are_watch_never_problem() -> None:
    unseeded = _cell("stream", seeded=False)
    assert unseeded["status"] == "warn" and "no seed" in unseeded["message"]
    out, conn = _check(present=False)
    cells = _cells(out)
    assert cells["stream"]["status"] == "warn" and "store is absent" in cells["stream"]["message"]
    for subject in ("decision_lag", "lease"):
        assert cells[subject]["status"] == "unknown", subject
        assert cells[subject]["message"] == "Not measured: the engine's store is absent"
    sent = {sql for sql, _, _ in conn.sent}
    assert not sent & {isql.RT_LEASE_READ_SQL, isql.RT_CALIBRATION_PRESENT_SQL,
                       isql.RT_NEW_LISTINGS_SQL, isql.RT_FP_READ_SQL}


def test_a_build_reads_its_stream_ok_until_it_ends() -> None:
    stream = _cell("stream", **_building())
    assert stream["status"] == "ok"
    assert stream["message"] == "building: the stream goes live when the build ends"
    assert stream["facts"]["live"] is False


# --- the build ----------------------------------------------------------------------------------


def test_a_build_that_decides_nothing_for_three_hours_with_ads_waiting_is_a_problem() -> None:
    """The 10-02 stall: every pass raised and rolled back, so nothing was decided."""
    out, conn = _check(**_building(decisions=0, backlog=5_000))
    build = _cells(out)["build"]
    assert build["status"] == "fail" and build["value"] == 0.0
    assert build["unit"] == "decisions/h"
    assert build["message"] == ("no decision in 3.0 h while 5,000 ads wait and the lane runs "
                                "(Problem at 0 in 3 h)")
    assert _params(conn, dd._DECISIONS_SQL) == {"generation": "rt", "since": _ago(hours=3)}
    assert _params(conn, isql.RT_SCOPE_BACKLOG_SQL) == {"generation": "rt"}


def test_a_window_clipped_by_the_build_start_or_a_brake_lift_is_never_a_problem() -> None:
    young, conn = _check(**_building(flag_at=_ago(hours=1), decisions=0, backlog=5_000))
    assert _cells(young)["build"]["status"] == "ok"
    assert _cells(young)["build"]["facts"]["window_h"] == 1.0
    assert _params(conn, dd._DECISIONS_SQL)["since"] == _ago(hours=1)
    lifted, conn = _check(**_building(history=[(_INTERVAL, 0, _ago(hours=2))], decisions=0,
                                      backlog=5_000))
    assert _cells(lifted)["build"]["status"] == "ok"
    assert _params(conn, dd._DECISIONS_SQL)["since"] == _ago(hours=2)
    assert _cells(lifted)["build"]["facts"]["last_lift_at"] == _ago(hours=2).isoformat()


def test_an_eta_past_seven_days_is_watch_and_said_to_be_a_lower_bound() -> None:
    """SYNTHETIC: a crawl of ~90 decisions an hour over 95k waiting ads."""
    build = _cell("build", **_building(decisions=270, backlog=95_000))
    assert build["status"] == "warn" and build["value"] == 90.0
    assert build["facts"]["eta_h"] == pytest.approx(1055.6, abs=0.1)
    assert "at least 44.0 d to go, a lower bound (Watch past 7 d)" in build["message"]


def test_a_normal_build_is_ok_with_its_eta() -> None:
    """SYNTHETIC: a normal build, 3,000 decisions an hour."""
    build = _cell("build", **_building(decisions=9_000, backlog=60_000))
    assert build["status"] == "ok" and build["value"] == 3_000.0
    assert build["message"] == ("building since 10.0 h ago: 9,000 decisions in the last 3.0 h "
                                "(3,000 an hour), 60,000 ads wait in the walked blocks: at least "
                                "20.0 h to go (a lower bound)")
    assert build["facts"]["blocks_total"] == build["facts"]["blocks_walked"] == 2


def test_an_empty_backlog_with_blocks_unwalked_waits_for_their_walk() -> None:
    """The backlog counts walked blocks only: the scan cap's wait reads as waiting, not done."""
    scope = [*_LANE_SCOPE, {"grain": "cast_obce", "code": 490156}, {"grain": "obec",
                                                                     "code": 586846}]
    build = _cell("build", **_building(lane_scope=scope, decisions=40, backlog=0,
                                       walked=["obec:563510"]))
    assert build["status"] == "ok"
    assert build["message"].startswith("waiting for the walk of 3 blocks; building since")
    assert (build["facts"]["blocks_total"], build["facts"]["blocks_walked"]) == (4, 1)


def test_an_eta_under_a_minute_reads_so_and_never_at_least_under_a_minute() -> None:
    """The replay's 10-18 20:00Z tick: 12 ads wait at 2,474 decisions an hour."""
    build = _cell("build", **_building(decisions=7_422, backlog=12))
    assert build["status"] == "ok"
    assert build["message"].endswith("12 ads wait in the walked blocks: under a minute to go (a "
                                     "lower bound)")
    assert "at least under" not in build["message"]


def test_a_count_of_one_reads_in_the_singular_on_the_build() -> None:
    scope = [*_LANE_SCOPE, {"grain": "obec", "code": 586846}]
    waiting = _cell("build", **_building(lane_scope=scope, decisions=1, backlog=0,
                                         walked=["obec:563510", "cast_obce:490245"]))
    assert waiting["message"].startswith("waiting for the walk of 1 block; building since 10.0 h "
                                         "ago: 1 decision in the last 3.0 h (0 an hour), 0 ads "
                                         "wait in the walked blocks")
    stalled = _cell("build", **_building(decisions=0, backlog=1))
    assert stalled["message"] == ("no decision in 3.0 h while 1 ad waits and the lane runs "
                                  "(Problem at 0 in 3 h)")


def test_the_build_is_ok_and_names_the_state_when_not_building() -> None:
    assert _cell("build")["message"] == "merging: not building"
    assert _cell("build", interval=0, flag=True)["message"] == (
        "off: the build waits for the brake to lift")
    assert _cell("build", interval=0)["message"] == "off: not building"


# --- the merge pause ----------------------------------------------------------------------------


def test_a_build_pausing_merges_past_48_h_is_watch_and_counted_from_the_build_start() -> None:
    """Prague's build paused the engine's merges 52.3 h: Watch, and never a notice."""
    pause = _cell("merge_pause", **_building(
        flag_at=_ago(hours=50), merges=[("autodedup", 0, 3, _ago(hours=52), 149, 0, 0)]))
    assert pause["status"] == "warn" and pause["value"] == 50.0 and pause["unit"] == "h"
    assert pause["message"] == ("the engine has merged nothing for 2.1 d, since the build began; "
                                "a build pauses its merges and yours go on (Watch past 48 h, "
                                "never Problem)")
    long = _cell("merge_pause", **_building(flag_at=_ago(days=9), merges=[]))
    assert long["status"] == "warn" and long["facts"]["newest_merge_at"] is None


def test_your_merges_during_a_build_do_not_end_the_engines_pause() -> None:
    """Operator merges go on during a build: the pause counts the engine's merges alone."""
    pause = _cell("merge_pause", **_building(
        flag_at=_ago(hours=50), merges=[("autodedup", 0, 3, _ago(hours=52), 149, 0, 0),
                                        ("operator", 4, 9, _ago(hours=1), 21, 0, 0)]))
    assert pause["status"] == "warn" and pause["value"] == 50.0
    assert "since the build began" in pause["message"]
    assert pause["facts"]["newest_merge_at"] == _ago(hours=52).isoformat()


def test_a_pause_under_48_h_is_ok_and_a_merge_inside_the_build_restarts_it() -> None:
    assert _cell("merge_pause", **_building(flag_at=_ago(hours=29)))["status"] == "ok"
    pause = _cell("merge_pause", **_building(
        flag_at=_ago(hours=60), merges=[("autodedup", 1, 9, _ago(hours=5), 149, 0, 0)]))
    assert pause["status"] == "ok" and pause["value"] == 5.0
    assert "since its newest merge" in pause["message"]


def test_the_merge_pause_is_ok_outside_a_build() -> None:
    pause = _cell("merge_pause", merges=[])
    assert pause["status"] == "ok" and pause["value"] is None
    assert pause["message"] == ("merging: a build pauses the engine's merges, and it is not "
                                "building")


# --- the decision lag ---------------------------------------------------------------------------


def test_every_new_in_scope_ad_decided_is_ok() -> None:
    lag = _cell("decision_lag")
    assert lag["status"] == "ok" and lag["value"] == 0.0 and lag["unit"] == "%"
    assert lag["facts"]["lower_bound"] == 0.0 and lag["facts"]["n"] == 400
    assert lag["facts"]["p50_min"] == lag["facts"]["p90_min"] == 6.0


def test_the_lag_is_judged_on_its_wilson_lower_bound() -> None:
    watch = _cell("decision_lag", arrivals=_arrivals(400, 12))
    assert watch["status"] == "warn" and watch["value"] == 3.0
    assert watch["facts"]["lower_bound"] == pytest.approx(0.0172, abs=1e-4)
    assert watch["message"] == ("12 of 400 in-scope ads first seen 1-24 h ago have no decision "
                                "(3.0%, lower bound 1.7%); the rest were decided p50 6 / p90 6 "
                                "min after arrival (Watch over 1%)")
    assert _cell("decision_lag", arrivals=_arrivals(400, 2))["status"] == "ok"


def test_the_10_19_stall_is_a_problem() -> None:
    """09:19Z: 68 of 429 undecided (15.9 %, lower bound 12.7 %), no pass committed since 07:55Z."""
    lag = _cell("decision_lag", arrivals=_arrivals(429, 68, wait_min=10.8))
    assert lag["status"] == "fail" and lag["value"] == 15.85
    assert lag["facts"]["lower_bound"] == pytest.approx(0.127, abs=1e-3)
    assert lag["message"].endswith("(Problem over 10%)")


def test_under_twenty_ads_the_lag_is_not_measured() -> None:
    lag = _cell("decision_lag", arrivals=_arrivals(19, 19))
    assert lag["status"] == "unknown"
    assert lag["message"] == ("Not measured: 19 in-scope ads first seen 1-24 h ago, under the 20 "
                              "this arm needs")


@pytest.mark.parametrize(("world", "reason"), [
    ({"history": [(_INTERVAL, 0, _ago(hours=5))]}, "a brake lifted 5.0 h ago"),
    ({"flag_at": _ago(hours=10)}, "a build ended 10.0 h ago"),
    ({"seed_at": _ago(hours=20)}, "a seed began 20.0 h ago"),
    ({"engine": {"digest": "29d2", "since": _ago(hours=3).isoformat()}},
     "the engine changed 3.0 h ago")])
def test_brakes_builds_seeds_and_engine_changes_inside_the_day_are_not_lag(
        world: dict[str, Any], reason: str) -> None:
    out, conn = _check(arrivals=_arrivals(400, 300), **world)
    lag = _cells(out)["decision_lag"]
    assert lag["status"] == "unknown" and lag["facts"]["excused"] == [reason]
    assert lag["message"] == (f"Not measured: {reason}, inside the last 24 h; brakes, builds, "
                              "seeds and engine changes are not lag")
    assert isql.RT_NEW_LISTINGS_SQL not in {sql for sql, _, _ in conn.sent}


def test_off_and_building_leave_the_lag_not_measured() -> None:
    assert _cell("decision_lag", interval=0)["message"] == (
        "Not measured: the lane is off; a brake is not lag")
    assert _cell("decision_lag", **_building())["message"] == "Not measured: building"


def test_a_lane_that_committed_nothing_for_an_hour_is_judged_whatever_changed() -> None:
    """10-19: the engine changed at 08:21Z and every pass after aborted on its pair budget."""
    world = {"engine": {"digest": "29d2", "since": _ago(minutes=40).isoformat()},
             "commit_at": _ago(minutes=65), "arrivals": _arrivals(429, 68)}
    lag = _cell("decision_lag", **world)
    assert lag["status"] == "fail" and lag["facts"]["stall_s"] == 3_900
    assert lag["message"].endswith("; no pass has committed a new ad for 65 min, so the engine "
                                   "change of the last 24 h does not excuse it (Problem over 10%)")
    fresh = _cell("decision_lag", **{**world, "commit_at": _ago(minutes=10)})
    assert fresh["status"] == "unknown"


def test_the_10_19_reading_names_every_excuse_the_stall_overrides() -> None:
    """09:19Z: the brake lifted at 02:41Z, the build ended 10-18 20:08Z, the engine changed at
    08:21Z, and no pass has committed since 07:55Z."""
    lag = _cell("decision_lag", history=[(_INTERVAL, 0, _ago(hours=6, minutes=38))],
                flag_at=_ago(hours=13, minutes=11), commit_at=_ago(hours=1, minutes=24),
                engine={"digest": "29d2", "since": _ago(minutes=58).isoformat()},
                arrivals=_arrivals(429, 68, wait_min=10.8))
    assert lag["status"] == "fail"
    assert lag["facts"]["excused"] == ["a brake lifted 6.6 h ago", "a build ended 13.2 h ago",
                                       "the engine changed 58 min ago"]
    assert lag["message"] == (
        "68 of 429 in-scope ads first seen 1-24 h ago have no decision (15.9%, lower bound "
        "12.7%); the rest were decided p50 11 / p90 11 min after arrival; no pass has committed "
        "a new ad for 84 min, so the brake, build and engine change of the last 24 h do not "
        "excuse it (Problem over 10%)")


def test_the_stall_counts_from_a_brake_lift_not_from_the_last_commit_before_it() -> None:
    """A lane back from a five-hour brake has not stalled until it misses an hour of its own."""
    world = {"history": [(_INTERVAL, 0, _ago(minutes=30))], "commit_at": _ago(hours=6),
             "arrivals": _arrivals(400, 100)}
    assert _cell("decision_lag", **world)["status"] == "unknown"
    stalled = _cell("decision_lag", **{**world, "history": [(_INTERVAL, 0, _ago(hours=2))]})
    assert stalled["status"] == "fail"


def test_the_cohort_is_read_through_the_lanes_own_feed_statements() -> None:
    """The new feed with an hour's lag from the arrival floor, its scope's parameters whole."""
    out, conn = _check(arrivals=_arrivals(400, 0, older=5))
    assert _params(conn, dd._ARRIVAL_FLOOR_SQL) == {"hours": 25}
    assert _params(conn, isql.RT_NEW_LISTINGS_SQL) == {
        "after_id": 19_340_583, "lag": 3_600, "window": 50_000,
        **parse_scope(_LANE_SCOPE).params()}
    sent = _params(conn, isql.RT_FP_READ_SQL)
    assert sent["generation"] == "rt" and sent["ids"] == list(range(2_000, 2_400))
    assert _cells(out)["decision_lag"]["facts"]["n"] == 400


def test_a_whole_corpus_lane_scope_reads_every_arrival() -> None:
    _, conn = _check(lane_scope="all")
    assert _params(conn, isql.RT_NEW_LISTINGS_SQL)["all_scope"] is True


def test_a_full_window_or_an_unreadable_lane_scope_is_not_measured() -> None:
    window, fp = _arrivals(400, 0)
    full = _cell("decision_lag", arrivals=((window[0], 50_000, window[2], window[3]), fp))
    assert full["status"] == "unknown" and "more than 50,000 ads arrived" in full["message"]
    missing = _cell("decision_lag", lane_scope=None)
    assert missing["status"] == "unknown"
    assert missing["message"].startswith("Not measured: the lane's scope cannot be read")


# --- the open reading: a Problem the excuses must not keep from recovering ----------------------

_ENGINE_CHANGED = ("Not measured: the engine changed 40 min ago, inside the last 24 h; brakes, "
                   "builds, seeds and engine changes are not lag")
_OPENED = _ago(hours=3).isoformat()


def _open(status: str, **facts: Any) -> list[dict[str, Any]]:
    """A previous result whose decision-lag reading has been open since _OPENED."""
    return [_held("decision_lag", status, open_reading=True, opened_at=_OPENED, **facts)]


def _memory(lag: dict[str, Any]) -> dict[str, Any]:
    """The open reading's facts on a decision-lag cell; none once it closed."""
    return {k: v for k, v in lag["facts"].items()
            if k in ("open_reading", "opened_at", "recovering_since")}


def test_a_problem_opens_the_reading_and_a_watch_does_not() -> None:
    """The ladder opens an incident on a Problem, never on a Watch: so does the open reading."""
    fail = _cell("decision_lag", arrivals=_arrivals(400, 300))
    assert fail["status"] == "fail"
    assert _memory(fail) == {"open_reading": True, "opened_at": _NOW.isoformat()}
    watch = _cell("decision_lag", arrivals=_arrivals(400, 12))
    assert watch["status"] == "warn" and _memory(watch) == {}


def test_an_open_lag_reading_is_judged_through_its_excuses_so_it_can_recover() -> None:
    """After the 10-19 fix passes commit again while the engine change still excuses the day: the
    Problem read before is judged again, and its clean cohort reads OK for the ladder to see."""
    out, conn = _check(**_reopened())
    lag = _cells(out)["decision_lag"]
    assert lag["status"] == "ok" and lag["facts"]["n"] == 400
    assert lag["message"].endswith("; judged through the engine change of the last 24 h so the "
                                   "open reading can recover")
    assert _memory(lag) == {"open_reading": True, "opened_at": _ago(minutes=10).isoformat(),
                            "recovering_since": _NOW.isoformat()}
    assert _params(conn, common.PREVIOUS_CELLS_SQL) == {"key": "dedup"}


@pytest.mark.parametrize("undecided", [12, 300])
def test_through_its_excuses_an_open_reading_reads_ok_or_not_measured_never_watch_or_problem(
        undecided: int) -> None:
    """What a brake, build, seed or engine change leaves undecided is its own backlog: a cohort that
    would read Watch or Problem reads the excuse's Not measured, and the open reading is carried."""
    dirty = _arrivals(400, undecided)
    from_fail = _cell("decision_lag", **_reopened(arrivals=dirty))
    assert (from_fail["status"], from_fail["message"]) == ("unknown", _ENGINE_CHANGED)
    assert from_fail["facts"]["undecided"] == undecided
    assert _memory(from_fail) == {"open_reading": True, "opened_at": _ago(minutes=10).isoformat()}
    first_ok = _ago(minutes=20).isoformat()
    recovering = _cell("decision_lag", **_reopened(arrivals=dirty, previous=_open(
        "ok", recovering_since=first_ok)))
    assert (recovering["status"], recovering["message"]) == ("unknown", _ENGINE_CHANGED)
    assert _memory(recovering) == {"open_reading": True, "opened_at": _OPENED,
                                   "recovering_since": first_ok}


@pytest.mark.parametrize("previous", [
    None, [], [_held("decision_lag", "ok")], [_held("decision_lag", "unknown")],
    [_held("decision_lag", "warn")], [_held("lease", "fail")],
    [_held("decision_lag", "ok", recovering_since=_ago(minutes=5).isoformat())],
    [_held("decision_lag", "unknown", open_reading="yes", opened_at=_OPENED)],
    [_held("decision_lag", "ok", open_reading=True)],
    [_held("decision_lag", "unknown", open_reading=True, opened_at="not a time")]])
def test_without_an_open_lag_reading_the_excuses_still_hold(previous: Any) -> None:
    """Only a Problem opens a reading, and one that cannot say when it opened is not open."""
    out, conn = _check(**_reopened(previous=previous))
    lag = _cells(out)["decision_lag"]
    assert (lag["status"], lag["message"]) == ("unknown", _ENGINE_CHANGED)
    assert _memory(lag) == {}
    assert isql.RT_NEW_LISTINGS_SQL not in {sql for sql, _, _ in conn.sent}


def test_with_no_excuse_an_open_reading_reads_as_any_and_keeps_the_ladders_clock() -> None:
    """No excuse inside the day, or a stall that overrides them: the lag reads as it would with no
    open reading; an OK starts the recovery clock, a Watch or Problem stops it (the ladder resets
    its own on either), and the reading stays open through all three."""
    ok = _cell("decision_lag", previous=_open("fail"))
    assert ok["status"] == "ok" and "judged through" not in ok["message"]
    assert _memory(ok) == {"open_reading": True, "opened_at": _OPENED,
                           "recovering_since": _NOW.isoformat()}
    first_ok = _ago(minutes=20).isoformat()
    kept = _cell("decision_lag", previous=_open("ok", recovering_since=first_ok))
    assert kept["status"] == "ok" and kept["facts"]["recovering_since"] == first_ok
    for undecided, status in ((12, "warn"), (300, "fail")):
        lag = _cell("decision_lag", arrivals=_arrivals(400, undecided),
                    previous=_open("ok", recovering_since=first_ok))
        assert lag["status"] == status
        assert _memory(lag) == {"open_reading": True, "opened_at": _OPENED}
    stalled = _cell("decision_lag", **_reopened(commit_at=_ago(minutes=65),
                                                 arrivals=_arrivals(400, 300),
                                                 previous=_open("ok", recovering_since=first_ok)))
    assert stalled["status"] == "fail"
    assert stalled["message"].endswith("so the engine change of the last 24 h does not excuse it "
                                       "(Problem over 10%)")
    assert _memory(stalled) == {"open_reading": True, "opened_at": _OPENED}


@pytest.mark.parametrize("world", [{"interval": 0}, {"present": False}, {"lane_scope": None},
                                   {"arrivals": _arrivals(19, 0)}])
def test_a_reading_not_measured_carries_the_open_reading_unchanged(world: dict[str, Any]) -> None:
    """A brake or a thin cohort between the Problem and its recovery: when it opened and since when
    it has read OK are carried, never reset, as the ladder keeps its own clock through them."""
    first_ok = _ago(minutes=20).isoformat()
    for previous, carried in (
            ([_held("decision_lag", "fail")], {"opened_at": _ago(minutes=10).isoformat()}),
            (_open("unknown"), {"opened_at": _OPENED}),
            (_open("ok", recovering_since=first_ok),
             {"opened_at": _OPENED, "recovering_since": first_ok})):
        lag = _cell("decision_lag", previous=previous, **world)
        assert lag["status"] == "unknown"
        assert _memory(lag) == {"open_reading": True, **carried}
    after = _cell("decision_lag", **_reopened(previous=_open("unknown")))
    assert after["status"] == "ok" and after["facts"]["recovering_since"] == _NOW.isoformat()


def test_the_open_reading_closes_after_an_ok_stored_long_enough_for_the_ladder_to_recover(
) -> None:
    """Measured time, as the ladder's: it closes at the reading after an OK stored _RECOVERY_S after
    its first OK; neither the wall clock nor a Not measured reading closes it."""
    first_ok = _ago(minutes=50)

    def lag_after(stored: _dt.datetime, status: str = "ok", since: str | None = None,
                  ) -> dict[str, Any]:
        previous = _open(status, recovering_since=since or first_ok.isoformat())
        return _cell("decision_lag", **_reopened(previous=previous, previous_at=stored))

    early = lag_after(first_ok + _dt.timedelta(seconds=dd._RECOVERY_S - 1))
    assert early["status"] == "ok" and early["facts"]["recovering_since"] == first_ok.isoformat()
    closed = lag_after(first_ok + _dt.timedelta(seconds=dd._RECOVERY_S))
    assert (closed["status"], closed["message"]) == ("unknown", _ENGINE_CHANGED)
    assert _memory(closed) == {}
    inert = lag_after(first_ok + _dt.timedelta(seconds=dd._RECOVERY_S), status="unknown")
    assert inert["status"] == "ok"
    silent = lag_after(_ago(hours=2), since=_ago(hours=2).isoformat())
    assert silent["status"] == "ok"
    assert silent["facts"]["recovering_since"] == _ago(hours=2).isoformat()
    restarted = lag_after(_ago(minutes=10), since="not a time")
    assert restarted["status"] == "ok"
    assert restarted["facts"]["recovering_since"] == _NOW.isoformat()


def test_the_open_reading_closes_with_the_ladders_week_and_at_a_new_seed() -> None:
    """The ladder closes an incident nobody measured for a week, and a seed after the Problem cut a
    new generation, whose cohort is not the one that read it: either way the excuses hold again."""
    def lag_after(opened: _dt.datetime, **world: Any) -> dict[str, Any]:
        previous = [_held("decision_lag", "unknown", open_reading=True,
                          opened_at=opened.isoformat())]
        return _cell("decision_lag", **{**_reopened(previous=previous), **world})

    long_ago = {"seed_at": _ago(days=8), "flag_at": _ago(days=7, hours=12)}
    assert lag_after(_ago(days=7), **long_ago)["status"] == "ok"
    week = lag_after(_ago(days=7, seconds=1), **long_ago)
    assert (week["status"], week["message"]) == ("unknown", _ENGINE_CHANGED)
    assert _memory(week) == {}
    built = lag_after(_ago(hours=3), seed_at=_ago(hours=2), flag_at=_ago(hours=1))
    assert built["status"] == "unknown" and _memory(built) == {}
    assert built["facts"]["excused"] == ["a build ended 60 min ago", "a seed began 2.0 h ago",
                                         "the engine changed 40 min ago"]
    building = lag_after(_ago(hours=3), **_building(flag_at=_ago(hours=1), seed_at=_ago(hours=1)))
    assert building["message"] == "Not measured: building" and _memory(building) == {}


def test_the_open_reading_waits_for_the_ladders_hold_and_closes_with_its_week() -> None:
    """Its first OK and the ladder's can be a reading apart: the hold plus one cadence."""
    cadence = next(check.cadence_s for check in CHECKS if check.key == "dedup")
    assert dd._RECOVERY_S >= system_alerts._hold(cadence).total_seconds() + cadence
    assert dd._OPEN_READING_MAX_S == system_alerts._unmeasured_window(cadence).total_seconds()


# --- the open reading through the real ladder, at ten-minute ticks ------------------------------


def _tick(k: int) -> _dt.datetime:
    return _NOW + _dt.timedelta(minutes=10 * k)


def _ticked(worlds: list[dict[str, Any]], ticks: list[_dt.datetime] | None = None,
            ) -> list[tuple[_dt.datetime, dict[str, Any]]]:
    """check_dedup at ten-minute ticks from _NOW (or at `ticks`), one world each, fed the cells the
    tick before stored and its run_at; a gap between `ticks` stores no cells, as a timed-out run."""
    results: list[tuple[_dt.datetime, dict[str, Any]]] = []
    for k, world in enumerate(worlds):
        at = ticks[k] if ticks else _tick(k)
        world = {"lease": _pass(at - _dt.timedelta(minutes=2)), "beat": (at, _ago(hours=1)),
                 **world, "previous": results[-1][1]["details"]["cells"] if results else None,
                 "previous_at": results[-1][0] if results else None}
        results.append((at, dd.check_dedup(_Conn(_answers(**world), now=at), T)))
    return results


def _lags(results: list[tuple[_dt.datetime, dict[str, Any]]]) -> list[dict[str, Any]]:
    return [_cells(out)["decision_lag"] for _, out in results]


def _kinds(ladder: Any) -> list[str]:
    """What the ladder told, in order: onset, reminder or recovery."""
    return [t.kind for t in ladder.told]


def _committing(k: int, **overrides: Any) -> dict[str, Any]:
    """Tick k of _reopened: a pass committed two minutes before it."""
    return _reopened(commit_at=_tick(k) - _dt.timedelta(minutes=2), **overrides)


_STALL = _reopened(commit_at=_ago(minutes=65), arrivals=_arrivals(429, 68))


@pytest.mark.parametrize("braked", [False, True])
def test_a_lag_problem_recovers_through_the_ladder_while_its_excuse_still_holds(
        braked: bool) -> None:
    """(e) After the 10-19 shape the stall opens one incident; passes commit again from the next
    tick, or after a ten-minute brake, the excuses still inside the day. The ladder recovers the
    incident on the OK 30 min after the first; the reading after it still carries the open reading
    (that OK was stored only 1,800 s after the first), which closes at the first reading after an
    OK stored 2,400 s after it, and the excuses hide the lag again."""
    went_on, lifted = _tick(1) - _dt.timedelta(minutes=5), _tick(2) - _dt.timedelta(minutes=3)
    worlds = [_STALL]
    for k in range(1, 10):
        world = _committing(k)
        if braked and k == 1:
            world.update(interval=0, history=[(_INTERVAL, 60, went_on), *_DEFAULT["history"]])
        elif braked:
            world["history"] = [(_INTERVAL, 0, lifted), (_INTERVAL, 60, went_on),
                                *_DEFAULT["history"]]
        worlds.append(world)
    results = _ticked(worlds)
    first_ok = 2 if braked else 1
    lags = _lags(results)
    assert [lag["status"] for lag in lags] == [
        "fail", *["unknown"] * (first_ok - 1), *["ok"] * 5, *["unknown"] * (5 - first_ok)]
    assert {c["status"] for _, out in results for c in out["details"]["cells"]
            if c["subject"] != "decision_lag"} == {"ok"}
    assert [_memory(lag).get("recovering_since") for lag in lags[first_ok:first_ok + 5]] == [
        _tick(first_ok).isoformat()] * 5
    assert {lag["facts"].get("opened_at") for lag in lags[:first_ok + 5]} == {_NOW.isoformat()}
    assert [_memory(lag) for lag in lags[first_ok + 5:]] == [{}] * (5 - first_ok)
    ladder = _told("dedup", 600, results)
    assert [(i["subject"], i["first_seen_at"], i["resolved_at"])
            for i in _incidents(ladder, "dedup")] == [
        ("decision_lag", _tick(0), _tick(first_ok + 3))]
    assert _kinds(ladder) == ["onset", "recovery"]


def test_a_watch_opens_nothing_so_the_backlog_a_brake_leaves_reads_not_measured() -> None:
    """(a) A Watch, a four-hour brake, then its lift with a fifth of the day undecided: a Watch
    opens no incident and no open reading, so the lift excuses that backlog and nothing is told."""
    went_on, lifted = _tick(0) + _dt.timedelta(minutes=5), _tick(24) + _dt.timedelta(minutes=7)
    braked = {"interval": 0, "history": [(_INTERVAL, 60, went_on), *_DEFAULT["history"]]}
    back = [(_INTERVAL, 0, lifted), (_INTERVAL, 60, went_on), *_DEFAULT["history"]]
    worlds = [{"arrivals": _arrivals(400, 10)}, *[braked] * 24,
              *({"history": back, "commit_at": _tick(k) - _dt.timedelta(minutes=2),
                 "arrivals": _arrivals(400, 80, at=_tick(k))} for k in range(25, 28))]
    results = _ticked(worlds)
    lags = _lags(results)
    assert [lag["status"] for lag in lags] == ["warn", *["unknown"] * 27]
    assert lags[-1]["message"].startswith("Not measured: a brake lifted 23 min ago, inside")
    assert [_memory(lag) for lag in lags] == [{}] * 28
    ladder = _told("dedup", 600, results)
    assert _incidents(ladder, "dedup") == [] and ladder.told == []


@pytest.mark.parametrize("last_ok", [4, 5])
def test_a_recovered_problem_rings_no_second_onset_on_the_backlog_a_later_brake_leaves(
        last_ok: int) -> None:
    """(b) The stall opens one incident, passes commit again and the ladder recovers it on the OK
    30 min after the first; a four-hour brake follows, and its lift leaves a fifth of the day
    undecided. Whether the open reading closed before the brake (OKs to tick 5) or was carried
    through it (to tick 4), that backlog reads Not measured: the onset and the recovery are all."""
    went_on = _tick(last_ok) + _dt.timedelta(minutes=5)
    lifted = _tick(last_ok + 24) + _dt.timedelta(minutes=7)
    braked = _reopened(interval=0, history=[(_INTERVAL, 60, went_on), *_DEFAULT["history"]])
    back = [(_INTERVAL, 0, lifted), (_INTERVAL, 60, went_on), *_DEFAULT["history"]]
    worlds = [_STALL, *(_committing(k) for k in range(1, last_ok + 1)), *[braked] * 24,
              *(_committing(k, history=back, arrivals=_arrivals(400, 80, at=_tick(k)))
                for k in range(last_ok + 25, last_ok + 28))]
    results = _ticked(worlds)
    lags = _lags(results)
    assert [lag["status"] for lag in lags] == ["fail", *["ok"] * last_ok, *["unknown"] * 27]
    for lag in lags[-3:]:
        assert lag["message"].startswith("Not measured: a brake lifted ")
        assert lag["message"].endswith(" engine changes are not lag")
    assert {bool(_memory(lag)) for lag in lags[last_ok + 1:]} == {last_ok == 4}
    ladder = _told("dedup", 600, results)
    assert [(i["first_seen_at"], i["resolved_at"]) for i in _incidents(ladder, "dedup")] == [
        (_tick(0), _tick(4))]
    assert _kinds(ladder) == ["onset", "recovery"]


def test_a_problem_braked_past_a_week_closes_with_the_ladder_and_its_lift_tells_nothing() -> None:
    """(c) The stall's Problem, then an eight-day brake: the ladder closes the unmeasured incident
    after seven days and the open reading closes with it, so the lift's backlog reads Not measured
    and the onset is all the ladder ever told."""
    week, braked_for = 7 * 144, 8 * 144
    went_on = _tick(0) + _dt.timedelta(minutes=5)
    lifted = _tick(braked_for) + _dt.timedelta(minutes=7)
    off = {"interval": 0, "history": [(_INTERVAL, 60, went_on), *_DEFAULT["history"]]}
    back = [(_INTERVAL, 0, lifted), (_INTERVAL, 60, went_on), *_DEFAULT["history"]]
    worlds = [_STALL, *[off] * braked_for,
              *({"history": back, "commit_at": _tick(k) - _dt.timedelta(minutes=2),
                 "arrivals": _arrivals(400, 400, at=_tick(k))}
                for k in range(braked_for + 1, braked_for + 4))]
    results = _ticked(worlds)
    lags = _lags(results)
    assert [lag["status"] for lag in lags] == ["fail", *["unknown"] * (braked_for + 3)]
    assert [bool(_memory(lag)) for lag in lags].index(False) == week + 1
    assert lags[-1]["message"].startswith("Not measured: a brake lifted 23 min ago, inside")
    ladder = _told("dedup", 600, results)
    (incident,) = _incidents(ladder, "dedup")
    assert (incident["resolved_at"], incident["resolve_reason"]) == (_tick(week + 1),
                                                                     "unmeasured 7d")
    assert _kinds(ladder) == ["onset"]


def test_a_first_ok_then_an_hour_with_no_stored_cells_still_recovers_at_the_next_ok() -> None:
    """(d) The stall's Problem, one OK, then 65 min in which the check stored no cells (it timed
    out): the open reading counts stored readings, not the wall clock, so the next OK is judged
    through the engine change and recovers the incident."""
    ticks = [_NOW + _dt.timedelta(minutes=m) for m in (0, 10, 75, 85)]
    worlds = [_STALL, *(_reopened(commit_at=at - _dt.timedelta(minutes=2)) for at in ticks[1:])]
    results = _ticked(worlds, ticks)
    lags = _lags(results)
    assert [lag["status"] for lag in lags] == ["fail", "ok", "ok", "unknown"]
    assert lags[2]["facts"]["recovering_since"] == ticks[1].isoformat()
    assert _memory(lags[3]) == {}
    ladder = _told("dedup", 600, results)
    assert [(i["first_seen_at"], i["resolved_at"]) for i in _incidents(ladder, "dedup")] == [
        (ticks[0], ticks[2])]
    assert _kinds(ladder) == ["onset", "recovery"]


def test_a_seed_after_a_problem_closes_its_open_reading() -> None:
    """(f) A Problem, then a re-seed: the build reads Not measured with no open reading, and once
    the build ends its backlog is excused by it, so nothing more is told."""
    seeded, ended = _tick(0) + _dt.timedelta(minutes=5), _tick(2) + _dt.timedelta(minutes=5)
    worlds = [{"arrivals": _arrivals(400, 300)},
              *[_building(flag_at=seeded, seed_at=seeded)] * 2,
              *({"flag_at": ended, "seed_at": seeded, "commit_at": _tick(k) - _dt.timedelta(
                  minutes=2), "arrivals": _arrivals(400, 300, at=_tick(k))} for k in (3, 4))]
    results = _ticked(worlds)
    lags = _lags(results)
    assert [lag["status"] for lag in lags] == ["fail", *["unknown"] * 4]
    assert [lag["message"] for lag in lags[1:3]] == ["Not measured: building"] * 2
    assert [bool(_memory(lag)) for lag in lags] == [True, False, False, False, False]
    assert lags[3]["facts"]["excused"] == ["a build ended 5 min ago", "a seed began 25 min ago"]
    ladder = _told("dedup", 600, results)
    assert [i["subject"] for i in _incidents(ladder, "dedup")] == ["decision_lag"]
    assert _kinds(ladder) == ["onset"]


def test_a_watch_stops_the_recovery_clock_as_it_stops_the_ladders() -> None:
    """A Watch between two OKs restarts the ladder's hold, and recovering_since restarts with it:
    a deploy after the Watch excuses the day, and the OKs judged through it recover the incident
    30 min after the first OK that followed the Watch, before the open reading closes."""
    deployed = {"engine": {"digest": "29d3", "since": (_tick(2) + _dt.timedelta(
        minutes=5)).isoformat()}}
    worlds = [{"arrivals": _arrivals(429, 68)}, {}, {"arrivals": _arrivals(400, 12)},
              *({**deployed, "commit_at": _tick(k) - _dt.timedelta(minutes=2)}
                for k in range(3, 10))]
    results = _ticked(worlds)
    lags = _lags(results)
    assert [lag["status"] for lag in lags] == ["fail", "ok", "warn", *["ok"] * 5,
                                               "unknown", "unknown"]
    assert [_memory(lag).get("recovering_since") for lag in lags[:8]] == [
        None, _tick(1).isoformat(), None, *[_tick(3).isoformat()] * 5]
    ladder = _told("dedup", 600, results)
    assert [(i["first_seen_at"], i["resolved_at"]) for i in _incidents(ladder, "dedup")] == [
        (_tick(0), _tick(6))]
    assert _kinds(ladder) == ["onset", "recovery"]


# --- the lease ----------------------------------------------------------------------------------


def test_the_live_workers_own_pass_holding_the_lease_is_ok() -> None:
    lease = _cell("lease")
    assert lease["status"] == "ok" and lease["value"] == 2_280 and lease["unit"] == "s"
    assert lease["facts"]["holder_kind"] == "pass"
    assert lease["message"] == "the worker's own pass holds the lease (38 min left)"


def test_a_gone_workers_pass_is_watch_after_the_deploy_grace() -> None:
    """Every deploy shows the old container's lease to the new worker for about a minute."""
    started = _ago(minutes=5)
    old = _pass(_ago(minutes=9))
    lease = _cell("lease", lease=old, beat=(_ago(seconds=5), started))
    assert lease["status"] == "warn" and lease["facts"]["foreign_s"] == 300
    assert lease["message"].startswith("a pass of a worker that is gone holds the lease (31 min "
                                       "left, taken 9 min ago), so the worker's passes wait")
    grace = _cell("lease", lease=old, beat=(_ago(seconds=5), _ago(seconds=90)))
    assert grace["status"] == "ok" and grace["message"].endswith(
        "; a deploy hands it over within 120 s")


@pytest.mark.parametrize(("holder", "kind"), [("rt_seed:5c4b3a2f1e0d:12:1792398060", "seed"),
                                              ("dispatch:39169897786-1", "dispatch"),
                                              ("someone else", "other")])
def test_a_seed_or_a_batch_run_holding_the_lease_is_watch(holder: str, kind: str) -> None:
    held = _lease(holder, taken=_ago(minutes=10), ttl_s=18_000)
    lease = _cell("lease", lease=held, interval=0)
    assert lease["status"] == "warn" and lease["facts"]["holder_kind"] == kind
    fresh = _cell("lease", lease=_lease(holder, taken=_ago(seconds=30), ttl_s=18_000))
    assert fresh["status"] == "ok"


def test_with_no_fresh_worker_start_a_pass_holder_is_not_judged() -> None:
    old = _pass(_ago(minutes=9))
    for beat in (None, (_ago(minutes=11), _ago(minutes=5))):
        lease = _cell("lease", lease=old, beat=beat)
        assert lease["status"] == "ok" and lease["facts"]["worker_started_at"] is None


def test_a_lease_no_pass_took_for_ten_minutes_past_the_lanes_wait_is_a_problem() -> None:
    """The lane stopped taking it: a frozen loop or a pass stuck past its timeout."""
    ended = _pass(_ago(minutes=40), ended=_ago(minutes=12))
    lease = _cell("lease", lease=ended)
    assert lease["status"] == "fail" and lease["value"] == 12 and lease["unit"] == "min"
    assert lease["message"] == ("no pass has taken the lease in the 12 min since it ended, while "
                                "the lane runs every 60 s: the lane stopped taking it (Problem "
                                "past 10 min beyond that wait)")
    assert lease["facts"]["ended_by"] == "release"
    assert _cell("lease", lease=ended, apply_scope=_CLOSED)["status"] == "fail"
    assert _cell("lease", lease=_pass(_ago(minutes=40), ended=_ago(minutes=10.5)))[
        "status"] == "ok"


def test_the_lease_wait_counts_from_a_brake_lift_or_a_build_end_not_an_older_expiry() -> None:
    """A braked lane leaves the row as old as the brake: the wait starts when the lane could take
    the lease again, the clock the lag's stall rule uses."""
    ended = _pass(_ago(hours=6), ended=_ago(hours=5))
    lifted = _cell("lease", lease=ended, history=[(_INTERVAL, 0, _ago(seconds=30))])
    assert lifted["status"] == "ok" and lifted["value"] == 0
    assert lifted["message"] == ("no pass holds the lease; it ended 5.0 h ago, before the brake "
                                 "lifted 0 min ago")
    assert (lifted["facts"]["past_expiry_s"], lifted["facts"]["untaken_s"]) == (18_000, 30)
    late = _cell("lease", lease=ended, history=[(_INTERVAL, 0, _ago(minutes=15))])
    assert late["status"] == "fail" and late["value"] == 15
    assert late["message"] == ("no pass has taken the lease in the 15 min since the brake lifted, "
                               "while the lane runs every 60 s: the lane stopped taking it "
                               "(Problem past 10 min beyond that wait)")
    assert late["facts"]["untaken_since"] == _ago(minutes=15).isoformat()
    built = _cell("lease", lease=ended, flag_at=_ago(minutes=5))
    assert built["status"] == "ok"
    assert built["message"].endswith("it ended 5.0 h ago, before the build ended 5 min ago")


@pytest.mark.parametrize("world", [{"interval": 0}, _building(), {"seeded": False}])
def test_an_ended_lease_is_ok_while_off_building_or_unseeded(world: dict[str, Any]) -> None:
    ended = _pass(_ago(hours=5), ended=_ago(hours=4))
    lease = _cell("lease", lease=ended, **world)
    assert lease["status"] == "ok" and lease["message"] == (
        "no pass holds the lease; it ended 4.0 h ago")


def test_a_lease_its_holder_never_released_says_it_ended_by_expiry() -> None:
    stranded = _pass(_ago(minutes=45))
    lease = _cell("lease", lease=stranded, interval=0)
    assert lease["facts"]["ended_by"] == "expiry" and lease["facts"]["past_expiry_s"] == 300


def test_no_lease_on_record_is_ok() -> None:
    lease = _cell("lease", lease=None)
    assert lease["status"] == "ok" and lease["value"] is None
    assert lease["message"] == "no lease on record" and lease["facts"]["holder_kind"] == "none"


# --- the rulings --------------------------------------------------------------------------------


def test_a_pair_ruled_apart_and_merged_by_the_engine_after_the_ruling_is_a_problem() -> None:
    out, conn = _check(rulings=(765, 1, 1, 3))
    ads = _cells(out)["rulings/ads"]
    assert ads["status"] == "fail" and ads["value"] == 1 and ads["unit"] == "pairs"
    assert ads["message"] == ("1 pair you ruled apart was merged by the engine after your "
                              "ruling (765 pair rulings of yours stand; 3 rulings on whole sets "
                              "cannot be judged pair by pair) (Problem at 1)")
    assert _params(conn, dd._RULINGS_SQL) == {"negatives": list(ui_sql.NEGATIVE_VERDICTS),
                                             "sources": [apply.MERGE_SOURCE]}


def test_a_pair_merged_before_the_ruling_or_by_another_hand_counts_together_only() -> None:
    """10-19: one pair ruled apart shares a property, held by merges no ruling postdates."""
    ads = _cell("rulings/ads")
    assert ads["status"] == "ok" and ads["value"] == 0
    assert ads["facts"] == {"state": "merging", "breaches": 0, "negatives": 765,
                            "together_now": 1, "set_rulings_unjudged": 3}
    assert ads["message"] == ("no pair you ruled apart was merged by the engine after your ruling "
                              "(765 pair rulings of yours stand; 1 other such pair shares a "
                              "property now, merged before your ruling or by another hand: a "
                              "split for you to make; 3 rulings on whole sets cannot be judged "
                              "pair by pair)")


def test_the_rulings_counts_agree_in_number_one_by_one() -> None:
    ads = _cell("rulings/ads", rulings=(1, 3, 2, 1))
    assert ads["message"] == ("2 pairs you ruled apart were merged by the engine after your "
                              "ruling (1 pair ruling of yours stands; 1 other such pair shares a "
                              "property now, merged before your ruling or by another hand: a "
                              "split for you to make; 1 ruling on a whole set cannot be judged "
                              "pair by pair) (Problem at 1)")


def test_the_breach_is_a_live_engine_merge_newer_than_the_start_of_the_negative_run() -> None:
    """The rule the live rail executes, pinned in the text: a re-ruling with a new note keeps
    the run's first date, any must_not_link source counts, and only live rows of the engine's."""
    sql = " ".join(dd._RULINGS_SQL.split())
    assert "where p.words_since = 0" in sql and "min(p.decided_at) as ruled_at" in sql
    assert "from autodedup.must_not_link m where not exists" in sql
    assert "m.source" not in sql
    assert ("where e.listing_ref_id in (t.listing_lo, t.listing_hi) and e.undone_at is null and "
            "e.source = any(%(sources)s::text[]) and e.created_at > t.ruled_at") in sql
    assert "where a.property_id = b.property_id" in sql


def test_no_broker_suppression_is_ok_and_a_lifted_one_is_no_violation() -> None:
    assert _cell("rulings/brokers")["message"] == "your broker rulings hold (0 standing, 0 lifted)"
    lifted = _cell("rulings/brokers", brokers=(0, 2, 0))
    assert lifted["status"] == "ok" and lifted["facts"] == {"state": "merging", "active": 0,
                                                            "lifted": 2, "violations": 0}
    sql = dd._BROKER_SUPPRESSION_SQL
    assert "s.lifted_at is null" in sql and sql.count("join broker_identities") == 2
    assert "lo.broker_id = hi.broker_id" in sql


def test_one_broker_violation_is_a_problem_with_no_watch_tier() -> None:
    brokers = _cell("rulings/brokers", brokers=(3, 1, 1))
    assert brokers["status"] == "fail" and brokers["value"] == 1 and brokers["unit"] == "pairs"
    assert brokers["message"] == ("1 broker pair you kept apart sits under one broker again while "
                                  "your no stands: the rail was bypassed (Problem at 1)")
    assert _cell("rulings/brokers", brokers=(3, 1, 2))["message"].startswith(
        "2 broker pairs you kept apart sit under one broker again")


# --- the splits ---------------------------------------------------------------------------------


def test_the_matured_cohort_split_by_operators_is_ok_under_two_percent() -> None:
    """10-19: none of the 149 groups the engine merged 7-14 d ago was split."""
    splits = _cell("splits")
    assert splits["status"] == "ok" and splits["value"] == 0.0 and splits["unit"] == "%"
    assert splits["message"] == ("0 of 149 groups the engine merged 7-14 d ago were split by an "
                                 "operator (0.0%; lower bound 0.0%)")


def test_splits_past_two_percent_are_watch_and_never_problem() -> None:
    for split in (50, 300):
        cell = _cell("splits", merges=[("autodedup", 0, 0, None, 1_000, split, 0)])
        assert cell["status"] == "warn" and cell["value"] == split / 10
        assert cell["message"].endswith("(Watch over 2%, never Problem)")
    assert _cell("splits", merges=[("autodedup", 0, 0, None, 1_000, 25, 0)])["status"] == "ok"


def test_under_a_hundred_groups_the_splits_are_not_measured() -> None:
    splits = _cell("splits", merges=[("autodedup", 0, 0, None, 99, 9, 0)])
    assert splits["status"] == "unknown"
    assert splits["message"] == ("Not measured: 99 groups the engine merged 7-14 d ago, under the "
                                 "100 this arm needs")


def test_the_engines_own_take_backs_are_a_fact_and_not_counted() -> None:
    splits = _cell("splits", merges=[("autodedup", 0, 0, None, 1_000, 5, 400)])
    assert splits["status"] == "ok" and splits["facts"]["unapplied"] == 400
    assert splits["message"].endswith("; 400 more the engine took back itself, not counted")


def test_a_count_of_one_reads_in_the_singular_on_the_splits_and_the_lag() -> None:
    one = _cell("splits", merges=[("autodedup", 0, 0, None, 100, 1, 0)])
    assert one["message"].startswith("1 of 100 groups the engine merged 7-14 d ago was split by")
    assert _cell("splits", merges=[("autodedup", 0, 0, None, 1, 0, 0)])["message"].startswith(
        "Not measured: 1 group the engine merged 7-14 d ago, under the 100")
    assert _cell("decision_lag", arrivals=_arrivals(400, 1))["message"].startswith(
        "1 of 400 in-scope ads first seen 1-24 h ago has no decision")
    assert _cell("decision_lag", arrivals=_arrivals(1, 0))["message"].startswith(
        "Not measured: 1 in-scope ad first seen 1-24 h ago, under the 20")


# --- the result and the statements --------------------------------------------------------------


def test_the_result_is_the_worst_measured_cell_and_counts_the_cells_at_it() -> None:
    out, _ = _check(rulings=(765, 1, 1, 3), brokers=(3, 0, 1), seed="w5")
    assert out["check_key"] == "dedup" and out["status"] == "fail" and out["value"] == 2
    assert out["message"].startswith("2 Problem — rulings/ads: ")


def _merging_statements() -> set[str]:
    return {common.CLOCK_SQL, isql.RT_STORE_PRESENT_SQL, SETTING_SQL, dd._HISTORY_SQL,
            ui_sql.LIVE_STREAM_SQL, dd._STAMPS_SQL, isql.RT_CALIBRATION_PRESENT_SQL,
            dd._MERGES_SQL, isql.RT_LEASE_READ_SQL, dd._WORKER_SQL, dd._RULINGS_SQL,
            dd._BROKER_SUPPRESSION_SQL, dd._ARRIVAL_FLOOR_SQL, isql.RT_NEW_LISTINGS_SQL,
            isql.RT_FP_READ_SQL, common.PREVIOUS_CELLS_SQL}


def test_every_statement_runs_on_the_per_check_clock() -> None:
    _, conn = _check()
    assert {sql for sql, _, _ in conn.sent} == _merging_statements()
    assert all(armed for _, _, armed in conn.sent)
    _, conn = _check(**_building())
    assert {sql for sql, _, _ in conn.sent} == (
        _merging_statements() - {dd._ARRIVAL_FLOOR_SQL, isql.RT_NEW_LISTINGS_SQL,
                                 isql.RT_FP_READ_SQL}) | {dd._DECISIONS_SQL,
                                                          isql.RT_SCOPE_BACKLOG_SQL,
                                                          isql.RT_SCOPE_SCAN_SEEN_SQL}
    assert all(armed for _, _, armed in conn.sent)
    _, conn = _check(**_reopened())
    assert {sql for sql, _, _ in conn.sent} == _merging_statements()
    assert all(armed for _, _, armed in conn.sent)


def test_the_previous_measurement_is_read_by_the_one_shared_statement() -> None:
    """common.previous_result: when the check last stored a result and its cells by subject; what
    is not a cell is skipped, and no stored result reads as none."""
    lag, lease = _held("decision_lag", "fail"), _held("lease", "ok")
    conn = _Conn({common.PREVIOUS_CELLS_SQL: [(_ago(minutes=10), [lag, "junk", None, lease])]})
    assert common.previous_result(conn, "dedup") == (_ago(minutes=10),
                                                     {"decision_lag": lag, "lease": lease})
    assert conn.sent == [(common.PREVIOUS_CELLS_SQL, {"key": "dedup"}, True)]
    assert common.previous_cells(conn, "dedup") == {"decision_lag": lag, "lease": lease}
    assert common.previous_result(_Conn({}), "dedup") == (None, {})
    shapeless = _Conn({common.PREVIOUS_CELLS_SQL: [(_ago(minutes=10), {"cells": [lag]})]})
    assert common.previous_cells(shapeless, "dedup") == {}


def test_every_imported_statement_is_sent_as_the_engines_own_text() -> None:
    """Imported, never copied: the string sent IS the engine's object, not an equal copy."""
    imported = [isql.RT_STORE_PRESENT_SQL, SETTING_SQL, ui_sql.LIVE_STREAM_SQL,
                isql.RT_CALIBRATION_PRESENT_SQL, isql.RT_LEASE_READ_SQL,
                isql.RT_NEW_LISTINGS_SQL, isql.RT_FP_READ_SQL, isql.RT_SCOPE_BACKLOG_SQL,
                isql.RT_SCOPE_SCAN_SEEN_SQL]
    sent = [sql for sql, _, _ in _check()[1].sent + _check(**_building())[1].sent]
    for constant in imported:
        assert any(sql is constant for sql in sent), constant[:40]
    tree = ast.parse(inspect.getsource(dd))
    names = {(n.module, a.name) for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)
             for a in n.names}
    assert {("autodedup.incremental_sql", "RT_SCOPE_BACKLOG_SQL"),
            ("autodedup.incremental_sql", "RT_NEW_LISTINGS_SQL"),
            ("autodedup.incremental_sql", "RT_FP_READ_SQL"),
            ("autodedup.incremental_sql", "RT_LEASE_READ_SQL"),
            ("autodedup.ui_sql", "LIVE_STREAM_SQL"), ("autodedup.ui_sql", "NEGATIVE_VERDICTS"),
            ("autodedup.apply_sql", "SETTING_SQL"), ("autodedup.apply", "MERGE_SOURCE"),
            ("autodedup.apply", "LANE_INTERVAL_SETTING"), ("autodedup.apply", "UNAPPLY_BY_PREFIX"),
            ("autodedup.incremental", "GENERATION"), ("autodedup.incremental", "SEED_VERSION"),
            ("autodedup.incremental", "setting_flag"), ("autodedup.incremental", "stream_live"),
            ("toolkit.property_identity", "BIRTH_REASONS")} <= names


# --- the spellings this check shares with the engine ---------------------------------------------


class _Setting:
    """A connection the engine's own readers can run SETTING_SQL on, answering one value."""

    def __init__(self, value: Any) -> None:
        self.conn = _Conn({SETTING_SQL: [] if value is None else [(value,)]})


@pytest.mark.parametrize("value", [None, 0, 60, -5, 60.5, "60", "60.5", "x", "true", True,
                                   {"seconds": 60}, [60]])
def test_the_interval_reads_as_the_engines_lane_interval(value: Any) -> None:
    assert dd._interval(value) == apply.lane_interval(_Setting(value).conn)


@pytest.mark.parametrize("value", [None, {}, _OPEN, _CLOSED, {**_OPEN, "category_types": []},
                                   {**_OPEN, "blocks": None, "all_blocks": True},
                                   {**_OPEN, "blocks": ["somewhere:1"]}, {**_OPEN, "bogus": 1},
                                   "not a scope", [1], '{"blocks": ["town:1"], '
                                   '"category_types": ["prodej"]}'])
def test_the_apply_scope_reads_as_the_reconciles_own_gate(value: Any) -> None:
    assert dd._apply_scope(value)[1] == apply.scope_closed(_Setting(value).conn)


@pytest.mark.parametrize("holder", ["1f2e3d4c5b6a:1:1792398060", "h:123:1", "h:1:",
                                    "h::1", ":1:1", "h:x:1", "h:1:1234567890123456789",
                                    "rt_seed:h:1:1792398060", "dispatch:39169897786-1",
                                    "h:1:1792398060:extra"])
def test_a_pass_holder_is_the_shape_the_engines_predecessor_rule_matches(holder: str) -> None:
    pattern = re.search(r"holder ~ '([^']+)'", isql._RT_LEASE_PREDECESSOR_WHERE).group(1)
    assert (dd._holder(holder)[0] == "pass") == bool(re.search(pattern, holder))


def test_the_holder_kinds_are_the_writers_own_spellings() -> None:
    assert dd._holder(incremental_lane.pass_holder())[0] == "pass"
    assert dd._holder(apply._Writer(None, False).holder)[0] == "dispatch"
    assert f'f"{dd._SEED_HOLDER}{{socket.gethostname()}}' in inspect.getsource(
        incremental_lane.run_rt_seed)
    assert dd._holder("rt_seed:5c4b3a2f1e0d:12:1792398060") == ("seed", None)
    assert dd._holder(None) == ("none", None)


def test_the_lease_ttls_and_name_are_the_engines() -> None:
    assert dd.LEASE_TTL_S == incremental_lane.LEASE_TTL_S == 2_400
    assert dd.DISPATCH_TTL_S == rt_lease.DISPATCH_TTL_S and dd.LEASE_NAME == rt_lease.NAME
    _, conn = _check()
    assert _params(conn, isql.RT_LEASE_READ_SQL) == {"name": rt_lease.NAME}
    assert _params(conn, dd._WORKER_SQL) == {"worker": common.WORKER}


def _select_list(sql: str) -> list[str]:
    """A statement's top-level select items (commas inside parentheses kept)."""
    body = sql.split("select", 1)[1].split("\n  from ", 1)[0]
    items, depth, item = [], 0, ""
    for char in body:
        depth += {"(": 1, ")": -1}.get(char, 0)
        if char == "," and depth == 0:
            items.append(item.strip())
            item = ""
        else:
            item += char
    return [*items, item.strip()]


def test_the_first_decision_is_read_from_its_own_column_of_the_stores_read() -> None:
    assert _select_list(isql.RT_FP_READ_SQL)[0] == "f.listing_id"
    assert _select_list(isql.RT_FP_READ_SQL)[dd._FP_DECIDED_COLUMN].endswith(
        "as first_decided_epoch")


def test_the_lane_scope_blocks_are_spelled_as_its_walks_key_them() -> None:
    scope = parse_scope(_LANE_SCOPE)
    walked = {block.key for block in incremental_lane.walk_blocks(scope, {490245: 563510})}
    assert set(scope.label().split()) == walked


def test_the_birth_reasons_are_the_ones_a_split_writes() -> None:
    source = inspect.getsource(property_identity._born)
    assert set(re.findall(r'"(split_new|ingest_grouping)"', source)) == (
        property_identity.BIRTH_REASONS)


def test_platform_reads_the_worker_constants_from_common() -> None:
    assert plat._WORKER is common.WORKER and plat._BEAT_FRESH_S is common.BEAT_FRESH_S


# --- rails ---------------------------------------------------------------------------------------


def test_the_module_never_imports_the_harness_or_the_worker() -> None:
    """The harness imports this check, so importing it back is a cycle; the worker is a lane."""
    tree = ast.parse(inspect.getsource(dd))
    imported = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    imported |= {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert not imported & {"scripts.verify_pipeline", "scraper.realtime_worker",
                           "toolkit.system_alerts"}


def test_the_module_calls_no_engine_reader_that_runs_its_own_sql() -> None:
    """They bypass the per-check clock: the same rows are read through it instead."""
    calls = {getattr(n.func, "attr", getattr(n.func, "id", None))
             for n in ast.walk(ast.parse(inspect.getsource(dd))) if isinstance(n, ast.Call)}
    assert not calls & {"lane_interval", "scope_closed", "read_setting", "current", "describe",
                        "lane_settings", "rt_unread_blocks", "read_scope"}


def test_the_check_reads_exactly_the_thresholds_the_contract_names() -> None:
    read = set(re.findall(r"""thresholds\[["'](\w+)["']\]""", inspect.getsource(dd)))
    assert read == set(_CONTRACT)


def test_the_harness_declares_every_threshold_this_check_reads_with_the_contracts_value() -> None:
    assert T == _CONTRACT
