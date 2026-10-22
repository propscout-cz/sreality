"""The Engine check's statements, executed: the state, the build, the decision lag, the lease, rulings and merges.

The hermetic tests answer each statement with canned rows and the replay rebuilds the arms in Python, so only this
file runs the SQL. Each test seeds the engine's rows under names of its own (a generation, both settings, the scan
cursor, the lease and the worker: the check's module names point at them), ads of a made-up portal in made-up towns,
and merges and rulings on CI's empty replay (both stores are global), inside a transaction that always rolls back;
then it sends the module's statement with the parameters the check sends, or runs the whole check. The rules pinned:
- state: off, building, merging, idle in that order, each dated by the change that began it; the brake's and the
  apply scope's history is read by the OLD value the trigger keeps;
- build: decisions since the window clipped at the build start, the backlog over the scope snapshot's rows this
  generation has not decided, the walked blocks from the scan ledger, the wait for blocks not walked yet;
- decision lag: in-scope ads first seen 1-24 h ago, read through the lane's own new feed above the arrival floor and
  its slack, decided only by this generation's own row; a brake lift excuses the day, the 10-19 stall does not;
- lease: live by the database's clock, whose pass holds it by the worker's boot, an ended lease no pass took;
- rulings: a pair ruled apart, on one property now and merged by the engine after its negative run began, is a
  breach; a merge of the removed engine (source 'auto') never is: rule 15, executed;
- merges: per source with a split's births left out, the 7-14 d cohort's edges, the floor's slack, operator splits
  apart from the engine's own take-backs; the removed engine's rows, W8's take-backs included, move no number.
Runs in CI's migrations lane (`TEST_DATABASE_URL`, `DB_RAILS_REQUIRED=1`); skipped locally.
"""

from __future__ import annotations

import dataclasses
import datetime as _dt
import json
import os
import uuid
from collections.abc import Iterator
from typing import Any

import pytest

from autodedup.apply import MERGE_SOURCE, UNAPPLY_BY_PREFIX
from autodedup.incremental import SEED_VERSION, bootstrap_key, seed_version_key
from autodedup.incremental_lane import LEASE_TTL_S, engine_key, scope_setting_key
from autodedup.incremental_scope import parse_scope
from autodedup.incremental_sql import (RT_LEASE_READ_SQL, RT_NEW_LISTINGS_SQL, RT_SCOPE_BACKLOG_SQL,
                                       RT_SCOPE_SCAN_SEEN_SQL, RT_STORE_PRESENT_SQL)
from autodedup.legacy_retire import LEGACY_SOURCE, RETIRE_BY
from autodedup.ui_sql import NEGATIVE_VERDICTS
from scripts.checks import dedup as dd
from scripts.checks.common import iso, wilson_lower
from scripts.verify_pipeline import THRESHOLDS
from toolkit.property_identity import BIRTH_REASONS

_DB_URL = os.environ.get("TEST_DATABASE_URL")
_REQUIRED = os.environ.get("DB_RAILS_REQUIRED") == "1"
_H = 3600.0
_UTC = _dt.timezone.utc
# Made up: a portal no fleet arm counts, registry codes no ad carries, an operator's login.
_PORTAL = "w5-live"
_TOWN, _QUARTER, _ELSEWHERE = 990_001, 990_002, 990_003
_OPERATOR = "w5-operator"
# The lane's scope as its seed writes it; the apply scope open (one town, both deal types) and closed (no area).
_LANE_SCOPE = [{"grain": "obec", "code": _TOWN}, {"grain": "cast_obce", "code": _QUARTER}]
_OPEN = {"blocks": [f"town:{_TOWN}"], "category_types": ["prodej", "pronajem"], "all_blocks": False,
         "listing_ids": None, "max_clusters_per_run": 600}
_CLOSED = {**_OPEN, "blocks": []}
_SUBJECTS = ["state", "stream", "build", "merge_pause", "decision_lag", "lease", "rulings/ads", "rulings/brokers",
             "splits"]

pytestmark = pytest.mark.skipif(
    not _DB_URL and not _REQUIRED,
    reason="TEST_DATABASE_URL not set — this rail runs in CI's migrations lane",
)


@dataclasses.dataclass(frozen=True)
class _Names:
    """The made-up names one test's engine rows live under."""

    generation: str
    interval: str
    scope: str
    cursor: str
    lease: str
    worker: str


@pytest.fixture()
def conn() -> Iterator[Any]:
    """One connection in one transaction, always rolled back, on a replay that holds no merge, ruling or broker
    ruling: the ledger and the rulings are global, so these tests count their own seeds."""
    if not _DB_URL:
        pytest.fail("DB_RAILS_REQUIRED=1 but TEST_DATABASE_URL is not set — the migrations lane is "
                    "misconfigured and this rail would otherwise have skipped green.")
    import psycopg

    connection = psycopg.connect(_DB_URL)
    try:
        held = connection.execute(  # opens the transaction every seed rolls back with
            "select (select count(*) from property_merge_events), (select count(*) from autodedup.verdicts), "
            "(select count(*) from autodedup.must_not_link), (select count(*) from broker_merge_suppressions)"
        ).fetchone()
        if any(held):
            pytest.fail(f"the replay holds {held[0]} merge rows, {held[1]} rulings, {held[2]} must_not_link rows "
                        f"and {held[3]} broker rulings: these tests count their own seeds, so they run on CI's "
                        "empty replay")
        yield connection
    finally:
        connection.rollback()
        connection.close()


@pytest.fixture()
def names(monkeypatch: pytest.MonkeyPatch) -> _Names:
    """The check's module names pointed at this test's own generation, settings, scan cursor, lease and worker, so
    no seed touches a live engine row: the statements bound to them take them as parameters."""
    tag = uuid.uuid4().hex[:10]
    made_up = _Names(f"w5{tag}", f"w5_interval_{tag}", f"w5_apply_scope_{tag}", f"w5_new_{tag}",
                     f"w5-lease-{tag}", f"w5-worker-{tag}")
    for name, value in (("GENERATION", made_up.generation), ("LANE_INTERVAL_SETTING", made_up.interval),
                        ("SCOPE_SETTING", made_up.scope), ("CURSOR_NEW", made_up.cursor),
                        ("LEASE_NAME", made_up.lease), ("WORKER", made_up.worker)):
        monkeypatch.setattr(dd, name, value)
    return made_up


def _now(conn: Any) -> _dt.datetime:
    """The transaction's now() in UTC, the clock every seed and every window of the check is measured on."""
    return conn.execute("select now()").fetchone()[0].astimezone(_UTC)


def _ago(now: _dt.datetime, **delta: float) -> _dt.datetime:
    return now - _dt.timedelta(**delta)


def _judged_facts(cell: dict[str, Any]) -> dict[str, Any]:
    """A judged cell's facts without the reasons `common.judged` adds beside them."""
    return {key: value for key, value in cell["facts"].items() if key != "conditions"}


def _cells(conn: Any) -> dict[str, dict[str, Any]]:
    """The whole check's cells by subject, every one of its statements executed."""
    out = dd.check_dedup(conn, THRESHOLDS)
    assert out["check_key"] == "dedup" and [c["subject"] for c in out["details"]["cells"]] == _SUBJECTS
    return {c["subject"]: c for c in out["details"]["cells"]}


# --- seeds -------------------------------------------------------------------------------------------------------


def _app_setting(conn: Any, key: str, value: Any) -> None:
    conn.execute("insert into app_settings (key, value) values (%s, %s::jsonb)", (key, json.dumps(value)))


def _edit(conn: Any, key: str, value: Any) -> None:
    """An operator's edit: the trigger keeps the value it replaces, stamped now()."""
    conn.execute("update app_settings set value = %s::jsonb where key = %s", (json.dumps(value), key))


def _replaced(conn: Any, key: str, old: Any, hours_ago: float) -> None:
    """A history row as the trigger writes it: the OLD value, which held until `hours_ago`."""
    conn.execute("insert into app_settings_history (key, value, replaced_at) "
                 "values (%s, %s::jsonb, now() - make_interval(secs => %s))", (key, json.dumps(old), hours_ago * _H))


def _engine_setting(conn: Any, key: str, value: Any, hours_ago: float) -> None:
    conn.execute("insert into autodedup.settings (key, value, updated_at, updated_by) "
                 "values (%s, %s::jsonb, now() - make_interval(secs => %s), 'w5-live')",
                 (key, json.dumps(value), hours_ago * _H))


def _world(conn: Any, n: _Names, *, interval: Any = 60, building: bool = False, build_h: float = 48,
           seed_h: float = 96, engine_h: float = 48, commit_min: float = 3, lifted_h: float = 30) -> None:
    """A seeded lane under `n`: the brake lifted `lifted_h` ago, the apply scope open, the build flag written
    `build_h` ago, the seed and its scope `seed_h` ago, the engine digest since `engine_h` ago (stamped by the
    database's clock, as the lane writes it) and the last committed pass `commit_min` ago."""
    _app_setting(conn, n.interval, interval)
    _app_setting(conn, n.scope, _OPEN)
    _replaced(conn, n.interval, 0, lifted_h)
    g = n.generation
    _engine_setting(conn, bootstrap_key(g), building, build_h)
    _engine_setting(conn, seed_version_key(g), SEED_VERSION, seed_h)
    _engine_setting(conn, scope_setting_key(g), _LANE_SCOPE, seed_h)
    conn.execute("insert into autodedup.settings (key, value, updated_at) values (%(key)s, "
                 "jsonb_build_object('digest', 'w5', 'since', now() - make_interval(secs => %(secs)s)), "
                 "now() - make_interval(secs => %(secs)s))", {"key": engine_key(g), "secs": engine_h * _H})
    conn.execute("insert into autodedup.rt_calibration (generation, digest) values (%s, 'w5')", (g,))
    conn.execute("insert into autodedup.scan_cursor (name, updated_at) values (%s, now() - make_interval(secs => %s))",
                 (n.cursor, commit_min * 60))


def _property(conn: Any) -> int:
    return int(conn.execute("insert into properties default values returning id").fetchone()[0])


def _ad(conn: Any, *, seen_h: float = 48, property_id: int | None = None, town: int | None = None,
        quarter: int | None = None) -> int:
    """One active ad of the made-up portal, first seen `seen_h` hours ago, located in `town` or `quarter` if given."""
    listing_id = int(conn.execute(
        "insert into listings (source, source_id_native, raw_json, is_active, first_seen_at, property_id) "
        "values (%s, %s, '{}'::jsonb, true, now() - make_interval(secs => %s), %s) returning id",
        (_PORTAL, f"w5-{uuid.uuid4().hex}", seen_h * _H, property_id)).fetchone()[0])
    if town is not None or quarter is not None:
        conn.execute(
            "insert into listing_location (listing_id, obec_kod, cast_obce_kod, match_confidence, granularity, "
            "uncertainty_radius_m, country_status, resolver_version, claim_set_hash, registry_version) "
            "values (%s, %s, %s, 'exact', 'building', 5, 'cz', 'test', '\\x00'::bytea, 'test')",
            (listing_id, town, quarter))
    return listing_id


def _decided(conn: Any, generation: str, listing_id: int, *, after_min: float) -> None:
    """A generation's first decision on an ad, `after_min` minutes after the ad was first seen."""
    conn.execute("insert into autodedup.rt_fp (generation, listing_id, first_decided_at) "
                 "select %s::text, l.id, l.first_seen_at + make_interval(secs => %s) from listings l where l.id = %s",
                 (generation, after_min * 60, listing_id))


def _fp(conn: Any, generation: str, listing_id: int, *, decided_h: float | None) -> None:
    """A store row; `decided_h` None is a row written before first decisions were stamped."""
    conn.execute("insert into autodedup.rt_fp (generation, listing_id, first_decided_at) "
                 "values (%s, %s, now() - make_interval(secs => %s))",
                 (generation, listing_id, None if decided_h is None else decided_h * _H))


def _scoped(conn: Any, generation: str, block: str, *listing_ids: int) -> None:
    """Rows of the scope snapshot a block's walk wrote."""
    for listing_id in listing_ids:
        conn.execute("insert into autodedup.rt_scope_ids (generation, block_key, listing_id) values (%s, %s, %s)",
                     (generation, block, listing_id))


def _walked(conn: Any, generation: str, block: str) -> None:
    conn.execute("insert into autodedup.rt_scope_scan (generation, block_key) values (%s, %s)", (generation, block))


def _merge(conn: Any, homes: tuple[int, int], source: str, *, days: int = 0, hours: float = 0, rows: int = 1,
           reason: str = "w5 merge", ad: int | None = None, undone: int = 0, undone_by: str | None = None,
           undone_h: float = 1) -> None:
    """One merge group of `rows` ledger rows, all stamped by its one transaction at now() - days - hours; the
    first `undone` rows taken back `undone_h` ago by `undone_by`."""
    conn.execute(
        "insert into property_merge_events (merge_group_id, survivor_property_id, retired_property_id, "
        "  listing_ref_id, prev_property_id, reason, source, created_at, undone_at, undone_by) "
        "select %(group)s::uuid, %(survivor)s::bigint, %(retired)s::bigint, %(ad)s::bigint, %(retired)s::bigint, "
        "       %(reason)s::text, %(source)s::text, "
        "       now() - make_interval(days => %(days)s::int, secs => %(secs)s::float8), "
        "       case when g <= %(undone)s::int then now() - make_interval(secs => %(undone_s)s::float8) end, "
        "       case when g <= %(undone)s::int then %(undone_by)s::text end "
        "  from generate_series(1, %(rows)s::int) as g",
        {"group": str(uuid.uuid4()), "survivor": homes[0], "retired": homes[1], "ad": ad, "reason": reason,
         "source": source, "days": days, "secs": hours * _H, "undone": undone, "undone_s": undone_h * _H,
         "undone_by": undone_by, "rows": rows})


def _cohort(conn: Any, homes: tuple[int, int], *, days: int, groups: int, split: int, taken_back: int) -> None:
    """`groups` one-row engine merge groups `days` old: the first `split` split by an operator, the next
    `taken_back` taken back by the engine itself."""
    conn.execute(
        "insert into property_merge_events (merge_group_id, survivor_property_id, retired_property_id, "
        "  prev_property_id, reason, source, created_at, undone_at, undone_by) "
        "select gen_random_uuid(), %(survivor)s::bigint, %(retired)s::bigint, %(retired)s::bigint, 'w5 merge', "
        "       %(source)s::text, now() - make_interval(days => %(days)s::int), "
        "       case when g <= %(split)s::int + %(back)s::int then now() - interval '1 day' end, "
        "       case when g <= %(split)s::int then %(operator)s::text "
        "            when g <= %(split)s::int + %(back)s::int then %(unapply)s::text end "
        "  from generate_series(1, %(groups)s::int) as g",
        {"survivor": homes[0], "retired": homes[1], "source": MERGE_SOURCE, "days": days, "split": split,
         "back": taken_back, "operator": _OPERATOR, "unapply": f"{UNAPPLY_BY_PREFIX}w5-run", "groups": groups})


def _ruled(conn: Any, pair: tuple[int, int], verdict: str, *, hours_ago: float, note: str = "w5") -> None:
    """The operator's word on a pair; the ledger appends, and the newest row is the ruling."""
    conn.execute("insert into autodedup.verdicts (kind, listing_lo, listing_hi, verdict, note, decided_by, "
                 "decided_at) values ('pair', %s, %s, %s, %s, %s, now() - make_interval(secs => %s))",
                 (min(pair), max(pair), verdict, note, _OPERATOR, hours_ago * _H))


def _vetoed(conn: Any, pair: tuple[int, int], source: str, *, hours_ago: float) -> None:
    conn.execute("insert into autodedup.must_not_link (listing_lo, listing_hi, source, reason, created_at) "
                 "values (%s, %s, %s, 'w5', now() - make_interval(secs => %s))",
                 (min(pair), max(pair), source, hours_ago * _H))


def _set_ruled(conn: Any, members: list[int] | None, verdict: str, *, key: int, hours_ago: float) -> None:
    """A ruling on a whole set; with no members it names only its key."""
    conn.execute("insert into autodedup.verdicts (kind, cluster_key, verdict, decided_by, decided_at, generation, "
                 "member_ids) values ('cluster', %s, %s, %s, now() - make_interval(secs => %s), %s, %s::bigint[])",
                 (key, verdict, _OPERATOR, hours_ago * _H, None if members is None else "w5", members))


def _lease(conn: Any, name: str, holder: str, *, taken_min: float, ends_min: float) -> None:
    """The lane's one lease row: taken `taken_min` ago, ending `ends_min` from now (negative: it has ended)."""
    conn.execute("insert into autodedup.rt_lease (name, holder, taken_at, expires_at) values (%(name)s, %(holder)s, "
                 "now() - make_interval(secs => %(taken)s), now() + make_interval(secs => %(ends)s)) "
                 "on conflict (name) do update set holder = excluded.holder, taken_at = excluded.taken_at, "
                 "expires_at = excluded.expires_at",
                 {"name": name, "holder": holder, "taken": taken_min * 60, "ends": ends_min * 60})


def _booted(conn: Any, worker: str, *, started_min: float) -> None:
    """The worker's heartbeat, fresh, from a process that booted `started_min` ago."""
    conn.execute("insert into worker_heartbeats (worker, beat_at, started_at) values (%s, now() - interval "
                 "'10 seconds', now() - make_interval(secs => %s)) on conflict (worker) do update set "
                 "beat_at = excluded.beat_at, started_at = excluded.started_at", (worker, started_min * 60))


def _pass(now: _dt.datetime, started_min: float) -> str:
    """A worker pass's holder, `<host>:<pid>:<the second its pass started>`."""
    return f"w5host:1:{int(_ago(now, minutes=started_min).timestamp())}"


# --- the state ---------------------------------------------------------------------------------------------------


def test_the_state_is_off_building_merging_idle_from_the_rows_each_dated_by_the_change_that_began_it(conn, names):
    """History stores the OLD value: the trigger writes the value an edit replaces, stamped now()."""
    _world(conn, names)
    now = _now(conn)

    merging = dd._read_engine(conn)
    assert (merging.state, merging.since) == ("merging", _ago(now, hours=30))
    assert (merging.present, merging.interval, merging.scope_blocks, merging.scope_all, merging.seeded) == (
        True, 60, 1, False, True)
    assert (merging.build_start, merging.build_end, merging.last_lift) == (
        None, _ago(now, hours=48), _ago(now, hours=30))
    assert (merging.seeded_at, merging.engine_since, merging.last_commit) == (
        _ago(now, hours=96), _ago(now, hours=48), _ago(now, minutes=3))

    _edit(conn, names.scope, _CLOSED)              # history keeps the open scope: the gate closed now
    idle = dd._read_engine(conn)
    assert (idle.state, idle.since) == ("idle", now)

    conn.execute("update autodedup.settings set value = 'true'::jsonb, updated_at = now() - interval '10 hours' "
                 "where key = %s", (bootstrap_key(names.generation),))
    building = dd._read_engine(conn)
    assert (building.state, building.since, building.build_start) == (
        "building", _ago(now, hours=10), _ago(now, hours=10))

    _edit(conn, names.interval, 0)                 # history keeps 60: the brake went on now
    off = dd._read_engine(conn)
    assert (off.state, off.since) == ("off", now)
    state = _cells(conn)["state"]
    assert (state["status"], state["value"], state["message"]) == ("ok", None, "off since 0 min ago")
    assert (state["facts"]["state"], state["facts"]["state_since"], state["facts"]["interval_s"]) == (
        "off", iso(now), 0)


def test_the_whole_check_reads_every_statement_on_the_replayed_schema(conn, names):
    """Every migration applied leaves the engine's store whole; a seed version this code does not take is the
    only signal that the engine's merges stopped, so it reads as Watch."""
    assert conn.execute(RT_STORE_PRESENT_SQL).fetchone() == (True,)
    _world(conn, names)
    now = _now(conn)
    _booted(conn, names.worker, started_min=60)
    _lease(conn, names.lease, _pass(now, 2), taken_min=2, ends_min=38)

    cells = _cells(conn)

    assert {subject: c["status"] for subject, c in cells.items()} == {
        "state": "ok", "stream": "ok", "build": "ok", "merge_pause": "ok", "decision_lag": "unknown",
        "lease": "ok", "rulings/ads": "ok", "rulings/brokers": "ok", "splits": "unknown"}
    assert cells["stream"]["facts"]["live"] is True and cells["build"]["message"] == "merging: not building"
    assert cells["decision_lag"]["facts"]["n"] == 0
    assert cells["rulings/brokers"]["facts"] == {"state": "merging", "active": 0, "lifted": 0, "violations": 0}

    conn.execute("update autodedup.settings set value = %s::jsonb where key = %s",
                 (json.dumps("e900"), seed_version_key(names.generation)))
    stream = _cells(conn)["stream"]
    assert (stream["status"], stream["facts"]["live"], stream["facts"]["seed_version"]) == ("warn", False, "e900")


# --- the build ---------------------------------------------------------------------------------------------------


def test_the_backlog_counts_the_snapshots_rows_this_generation_has_not_decided(conn):
    """Rows, not ads (an ad in two blocks counts twice); another generation's rows and decisions count for none."""
    g, other = f"w5{uuid.uuid4().hex[:10]}", f"w5{uuid.uuid4().hex[:10]}"
    _scoped(conn, g, "obec:1", 101, 102, 103)
    _scoped(conn, g, "cast_obce:2", 103, 104)
    _scoped(conn, other, "obec:1", 105)
    _fp(conn, g, 101, decided_h=1)
    _fp(conn, other, 102, decided_h=1)
    _fp(conn, g, 999, decided_h=2.5)               # decided, out of the snapshot
    _fp(conn, g, 106, decided_h=3)                 # the window's first instant
    _fp(conn, g, 107, decided_h=4)
    _fp(conn, g, 108, decided_h=None)
    for block in ("obec:1", "obec:1", "cast_obce:2"):
        _walked(conn, g, block)
    _walked(conn, other, "obec:9")

    assert conn.execute(RT_SCOPE_BACKLOG_SQL, {"generation": g}).fetchone() == (4,)
    assert sorted(r[0] for r in conn.execute(RT_SCOPE_SCAN_SEEN_SQL, {"generation": g}).fetchall()) == [
        "cast_obce:2", "obec:1"]
    since = _ago(_now(conn), hours=3)
    assert conn.execute(dd._DECISIONS_SQL, {"generation": g, "since": since}).fetchone() == (3,)


def test_a_build_counts_its_decisions_since_the_window_clipped_at_the_build_start(conn, names):
    """The build began 2 h ago: its window is 2 h, so a decision taken before it is out and no Problem can ring."""
    _world(conn, names, building=True, build_h=2)
    now, g = _now(conn), names.generation
    walked = f"obec:{_TOWN}"
    _scoped(conn, g, walked, 201, 202, 203)
    _walked(conn, g, walked)                       # the quarter's block is not walked yet
    _fp(conn, g, 201, decided_h=1)
    _fp(conn, g, 202, decided_h=2.5)

    build = _cells(conn)["build"]

    assert (build["status"], build["value"], build["unit"]) == ("ok", 0.5, "decisions/h")
    assert build["facts"] == {
        "state": "building", "window_h": 2.0, "window_start": iso(_ago(now, hours=2)), "decisions": 1,
        "rate_per_h": 0.5, "backlog": 1, "eta_h": 2.0, "blocks_total": 2, "blocks_walked": 1,
        "build_started_at": iso(_ago(now, hours=2)), "last_lift_at": iso(_ago(now, hours=30))}
    assert "1 of 2 blocks not walked yet" in build["message"]

    conn.execute("update autodedup.settings set updated_at = now() - interval '10 hours' where key = %s",
                 (bootstrap_key(g),))
    conn.execute("update autodedup.rt_fp set first_decided_at = now() - interval '5 hours' where generation = %s",
                 (g,))
    stalled = _cells(conn)["build"]
    assert (stalled["status"], stalled["facts"]["window_h"], stalled["facts"]["decisions"]) == ("fail", 3.0, 0)

    conn.execute("delete from autodedup.rt_scope_ids where generation = %s and listing_id = 203", (g,))
    waiting = _cells(conn)["build"]
    assert waiting["status"] == "ok" and waiting["facts"]["backlog"] == 0
    assert waiting["message"].startswith("waiting for the walk of 1 block; ")


# --- the decision lag --------------------------------------------------------------------------------------------


def test_the_decision_lag_reads_the_new_feeds_in_scope_ads_first_seen_1_to_24_h_ago(conn, names):
    """Read above the arrival floor through RT_NEW_LISTINGS_SQL and RT_FP_READ_SQL, as the lane reads its arrivals.
    A late commit stamped before the window sits above the floor, so the floor's slack keeps the window whole."""
    _world(conn, names)
    floor = _ad(conn, seen_h=26, town=_TOWN)       # older than the window and its slack: the floor
    oldest = _ad(conn, seen_h=24, town=_TOWN)      # the window's first instant
    middle = _ad(conn, seen_h=12, town=_TOWN)
    quarter = _ad(conn, seen_h=10, quarter=_QUARTER)
    _ad(conn, seen_h=9, town=_ELSEWHERE)           # out of the scope
    _ad(conn, seen_h=8)                            # no location: out of the scope
    late = _ad(conn, seen_h=24.5, town=_TOWN)      # read by the feed, then left out by its first sighting
    newest = _ad(conn, seen_h=1, town=_TOWN)       # the feed's lag, its last instant
    _ad(conn, seen_h=59 / 60, town=_TOWN)          # not an hour old yet
    _decided(conn, names.generation, middle, after_min=10)
    _decided(conn, names.generation, quarter, after_min=20)
    _decided(conn, names.generation, newest, after_min=30)
    _decided(conn, f"w5{uuid.uuid4().hex[:10]}", oldest, after_min=5)   # another generation's decision

    young_h, old_h = dd._LAG_WINDOW_H
    assert conn.execute(dd._ARRIVAL_FLOOR_SQL, {"hours": old_h + dd._FLOOR_SLACK_H}).fetchone() == (floor,)
    window = conn.execute(RT_NEW_LISTINGS_SQL, {"after_id": floor, "lag": young_h * 3600,
                                                "window": dd._LAG_WINDOW_ROWS,
                                                **parse_scope(_LANE_SCOPE).params()}).fetchone()
    assert window[:3] == (newest, 7, [oldest, middle, quarter, late, newest])

    lag = _cells(conn)["decision_lag"]
    assert lag["status"] == "unknown"
    assert lag["message"] == "Not measured: 4 in-scope ads first seen 1-24 h ago, under the 20 this arm needs"
    assert lag["facts"] == {"state": "merging", "excused": [], "stall_s": 180, "n": 4, "undecided": 1,
                            "p50_min": 20.0, "p90_min": 28.0}

    _edit(conn, names.interval, 0)
    _edit(conn, names.interval, 60)                # the trigger keeps the 0 it replaced: a brake lifted now
    lifted = _cells(conn)["decision_lag"]
    assert lifted["status"] == "unknown" and lifted["facts"]["excused"] == ["a brake lifted 0 min ago"]
    assert lifted["message"] == ("Not measured: a brake lifted 0 min ago, inside the last 24 h; brakes, builds, "
                                 "seeds and engine changes are not lag")


def test_the_10_19_stall_reads_as_a_problem_whatever_changed_in_the_day(conn, names):
    """09:19Z on 10-19: the brake lifted 6.6 h before, the build ended 13.2 h before, the engine changed 58 min
    before and no pass had committed for 84 min (68 of 429 undecided; here 6 of 24)."""
    _world(conn, names, lifted_h=6.6, build_h=13.2, engine_h=58 / 60, commit_min=84)
    arrivals = [_ad(conn, seen_h=23 - i * 0.875, town=_TOWN) for i in range(24)]
    for listing_id in arrivals[:18]:
        _decided(conn, names.generation, listing_id, after_min=11)

    lag = _cells(conn)["decision_lag"]

    assert lag["status"] == "fail" and lag["value"] == 25.0
    assert (lag["facts"]["n"], lag["facts"]["undecided"], lag["facts"]["stall_s"]) == (24, 6, 84 * 60)
    assert lag["facts"]["excused"] == ["a brake lifted 6.6 h ago", "a build ended 13.2 h ago",
                                       "the engine changed 58 min ago"]
    assert lag["facts"]["lower_bound"] == round(wilson_lower(6, 24), 4) > THRESHOLDS["dedup_lag_fail"]
    assert lag["facts"]["p50_min"] == lag["facts"]["p90_min"] == 11.0


# --- the lease ---------------------------------------------------------------------------------------------------


def test_the_lease_is_live_by_the_databases_clock_and_whose_pass_holds_it_by_the_workers_boot(conn, names):
    _world(conn, names)
    now = _now(conn)
    _lease(conn, names.lease, "w5-holder", taken_min=40, ends_min=0)
    assert conn.execute(RT_LEASE_READ_SQL, {"name": names.lease}).fetchone() == (
        "w5-holder", _ago(now, minutes=40), now, False)      # a lease that ends this instant is free

    _booted(conn, names.worker, started_min=60)
    _lease(conn, names.lease, _pass(now, 2), taken_min=2, ends_min=38)
    own = _cells(conn)["lease"]
    assert (own["status"], own["value"], own["facts"]["holder_kind"]) == ("ok", 38 * 60, "pass")
    assert own["message"] == "the worker's own pass holds the lease (38 min left)"

    _booted(conn, names.worker, started_min=5)     # a deploy: the pass that holds it belongs to the old process
    _lease(conn, names.lease, _pass(now, 30), taken_min=30, ends_min=10)
    gone = _cells(conn)["lease"]
    assert (gone["status"], gone["facts"]["foreign_s"]) == ("warn", 300)

    _lease(conn, names.lease, f"rt_seed:w5host:1:{int(now.timestamp())}", taken_min=10, ends_min=290)
    seed = _cells(conn)["lease"]
    assert (seed["status"], seed["facts"]["holder_kind"]) == ("warn", "seed")

    _lease(conn, names.lease, _pass(now, 15 + LEASE_TTL_S / 60), taken_min=15 + LEASE_TTL_S / 60, ends_min=-15)
    untaken = _cells(conn)["lease"]
    assert (untaken["status"], untaken["value"], untaken["unit"]) == ("fail", 15, "min")
    assert (untaken["facts"]["ended_by"], untaken["facts"]["past_expiry_s"]) == ("expiry", 900)


# --- the rulings -------------------------------------------------------------------------------------------------


def test_a_pair_ruled_apart_and_merged_by_the_engine_after_its_negative_run_began_is_a_breach(conn, names):
    """The removed engine's twin of a breach is none (rule 15, executed); an operator's merge is none either."""

    def pair(*, together: bool = True) -> tuple[int, int]:
        home = _property(conn)
        return _ad(conn, property_id=home), _ad(conn, property_id=home if together else _property(conn))

    def merged(ad: int, source: str, *, hours_ago: float, undone_by: str | None = None) -> None:
        home = int(conn.execute("select property_id from listings where id = %s", (ad,)).fetchone()[0])
        _merge(conn, (home, _property(conn)), source, hours=hours_ago, ad=ad, undone=1 if undone_by else 0,
               undone_by=undone_by, undone_h=hours_ago / 2)

    breach = pair()
    _ruled(conn, breach, "different", hours_ago=10)
    merged(breach[0], MERGE_SOURCE, hours_ago=5)
    removed = pair()                               # the same, merged by the removed engine
    _ruled(conn, removed, "different", hours_ago=10)
    merged(removed[0], LEGACY_SOURCE, hours_ago=5)
    before = pair()
    _ruled(conn, before, "different", hours_ago=5)
    merged(before[1], MERGE_SOURCE, hours_ago=10)
    reruled = pair()                               # the run began at 20 h: a re-ruling 10 h ago does not move it
    _ruled(conn, reruled, "different", hours_ago=20, note="a")
    merged(reruled[1], MERGE_SOURCE, hours_ago=15)
    _ruled(conn, reruled, "same_building_different_unit", hours_ago=10, note="b")
    flipped = pair()                               # 'same' at 25 h ended the first run; the standing one began 10 h ago
    _ruled(conn, flipped, "different", hours_ago=30)
    _ruled(conn, flipped, "same", hours_ago=25)
    _ruled(conn, flipped, "different", hours_ago=10)
    merged(flipped[0], MERGE_SOURCE, hours_ago=15)
    withdrawn = pair()
    _ruled(conn, withdrawn, "different", hours_ago=10)
    _ruled(conn, withdrawn, "unsure", hours_ago=8)
    merged(withdrawn[0], MERGE_SOURCE, hours_ago=5)
    taken_back = pair()                            # the engine's merge after the ruling, no longer live
    _ruled(conn, taken_back, "different", hours_ago=10)
    merged(taken_back[0], MERGE_SOURCE, hours_ago=5, undone_by=_OPERATOR)
    vetoed = pair()                                # a must_not_link row of any source, with no pair ruling
    _vetoed(conn, vetoed, "guard", hours_ago=10)
    merged(vetoed[1], MERGE_SOURCE, hours_ago=5)
    restamped = pair()                             # the veto re-stamped 2 h ago; the ruling's run began 10 h ago
    _ruled(conn, restamped, "different", hours_ago=10)
    _vetoed(conn, restamped, "operator", hours_ago=2)
    merged(restamped[0], MERGE_SOURCE, hours_ago=5)
    apart = pair(together=False)
    _ruled(conn, apart, "different", hours_ago=10)
    merged(apart[0], MERGE_SOURCE, hours_ago=5)
    by_hand = pair()
    _ruled(conn, by_hand, "different", hours_ago=10)
    merged(by_hand[0], "operator", hours_ago=5)
    _set_ruled(conn, [903, 901, 902], "different", key=1, hours_ago=10)
    _set_ruled(conn, [901, 902, 903], "same", key=2, hours_ago=5)    # the same set, ruled again under another key
    _set_ruled(conn, [904, 905], "different", key=3, hours_ago=10)
    _set_ruled(conn, None, "same_project_different_unit", key=4, hours_ago=10)

    row = conn.execute(dd._RULINGS_SQL, {"negatives": list(NEGATIVE_VERDICTS), "sources": [MERGE_SOURCE]}).fetchone()

    # standing: all but `withdrawn`; together: all of those but `apart`; breaches: breach, reruled, vetoed, restamped
    assert row == (10, 9, 4, 2)
    ads = _cells(conn)["rulings/ads"]
    assert (ads["status"], ads["value"], ads["unit"]) == ("fail", 4, "pairs")
    assert _judged_facts(ads) == {"state": "off", "breaches": 4, "negatives": 10, "together_now": 9,
                                  "set_rulings_unjudged": 2}


# --- the merges --------------------------------------------------------------------------------------------------


def test_merges_are_counted_per_source_over_the_cohorts_edges_and_the_removed_engines_rows_move_none(
        conn, monkeypatch):
    """Ids rise with the stamps, as one insert after another writes them; one row stamped before the window but
    written after rows inside it is the floor's slack at work. The removed engine's rows, W8's take-backs of them
    included, would read as a third source with two cohort groups and a split if the filter let them in."""
    homes = _property(conn), _property(conn)
    someone = str(uuid.uuid4())                    # an operator's login, as a split stamps it
    w8 = f"{RETIRE_BY}:w5-run"
    _merge(conn, homes, MERGE_SOURCE, days=14, hours=2)                          # the floor
    _merge(conn, homes, MERGE_SOURCE, days=14)                                   # the cohort's first instant
    _merge(conn, homes, LEGACY_SOURCE, days=13, rows=2, undone=2, undone_by=w8, undone_h=24)
    _merge(conn, homes, LEGACY_SOURCE, days=12)
    _merge(conn, homes, MERGE_SOURCE, days=12, rows=2, undone=1, undone_by=someone)          # split
    _merge(conn, homes, MERGE_SOURCE, days=11, rows=2, undone=2, undone_by=f"{UNAPPLY_BY_PREFIX}w5-run")
    _merge(conn, homes, MERGE_SOURCE, days=10)
    _merge(conn, homes, MERGE_SOURCE, days=14, hours=0.5)                        # the late commit
    _merge(conn, homes, MERGE_SOURCE, days=7)                                    # the cohort's end: the week's
    _merge(conn, homes, MERGE_SOURCE, days=5, undone=1, undone_by=someone)       # split, too young for the cohort
    _merge(conn, homes, "operator", hours=3, reason="operator_split")           # a split's letter join
    _merge(conn, homes, "operator", hours=2, rows=2, reason="manual_subset")    # a merge in Browse
    for reason in sorted(BIRTH_REASONS):                                         # a split's births, born taken back
        _merge(conn, homes, "operator", hours=1, reason=reason, undone=1, undone_by=someone, undone_h=1)
    _merge(conn, homes, MERGE_SOURCE, hours=0.5)                                 # the newest live merge
    _merge(conn, homes, MERGE_SOURCE, hours=1 / 6, undone=1, undone_by=someone, undone_h=1 / 12)
    now = _now(conn)

    merges = dd._merges(conn)

    assert set(merges) == {MERGE_SOURCE, "operator"}
    assert merges[MERGE_SOURCE] == dd._Merges(day=2, week=4, newest=_ago(now, minutes=30), cohort=4, split=1,
                                              unapplied=1)
    assert merges["operator"] == dd._Merges(day=2, week=2, newest=_ago(now, hours=2))
    monkeypatch.setattr(dd, "_FLOOR_SLACK_H", 0)   # with no slack the late commit is the floor
    assert dd._merges(conn)[MERGE_SOURCE].cohort == 0


def test_the_splits_cell_judges_operator_splits_of_the_matured_cohort_and_leaves_out_the_engines_take_backs(
        conn, names):
    homes = _property(conn), _property(conn)
    _merge(conn, homes, MERGE_SOURCE, days=15)
    _cohort(conn, homes, days=9, groups=120, split=10, taken_back=20)

    splits = _cells(conn)["splits"]

    assert (splits["status"], splits["value"], splits["unit"]) == ("warn", round(10 / 120 * 100, 2), "%")
    assert _judged_facts(splits) == {"state": "off", "groups": 120, "split": 10, "unapplied": 20,
                                     "share": round(10 / 120, 4), "lower_bound": round(wilson_lower(10, 120), 4)}
