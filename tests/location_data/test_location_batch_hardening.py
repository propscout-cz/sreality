"""Rails from the 2026-08-10 location-batch incident.

That night four heavy location lanes ran at once against the shared 75 GB production
instance: a RUIAN baseline (3 M-row COPY), the boundary pack (per-unit PostGIS), a Mapy
inventory scan and a full-corpus claims intake, with the resolve drain on its cadence.
Backends dropped ("SSL connection has been closed unexpectedly", one AdminShutdown), the
live Browse rebuild degraded to multi-minute DataFileReads, and TWO lanes wedged with no
error at all:

  * boundary pack (run 31434818469) — 2 h 04 min of silence inside OBCE_P, killed by hand;
  * resolve drain (run 31439340945) — 30 min of silence mid-batch, killed by the job ceiling.

Two rails answer that, and this file is the gate on both:

  1. the heavy lanes queue instead of competing: ONE outer concurrency group on GitHub until
     2026-11 (#2028), the job runner's shared `location-heavy` resource since;
  2. no batch statement runs without a ceiling — a wedge has to become an error that the
     existing per-row / per-unit resilience already knows how to handle.

AMENDED 2026-09-10 (operator decision): the RESOLVE DRAIN left the group. Rail 1 assumed a
skipped tick is free, which held while the queue was a few hundred rows per tick. It stopped
holding when the W2-13 archived-HTML sweeps began self-chaining ~55-minute runs back to back
against a queue above 100k: the drain resolves 0.7 listings/s and got zero ticks in three
hours. The drain is also the one member that is latency-bound rather than instance-bound
(11 small indexed reads + one projection write per listing — no COPY, no corpus scan, no
detoast), so it contributed least to the incident and lost most to the queueing. It READS
the claim spine that the intake and the archive sweep WRITE, so it never carried their
"must never overlap" constraint. Its guards are now the job-level `location-resolve` group
and the `scheduled_jobs` lease CAS; `test_the_resolve_drain_is_out_of_the_shared_group` below
is the rail that keeps it out.

AMENDED 2026-11 (#2028): the intake runs on the job runner. Its GitHub self-chain, the yield
that kept the chain off the group's one pending slot, and the group itself are gone; a run that
leaves work it moved exits 75 and the runner re-dues it a minute later (section 4).
"""

from __future__ import annotations

import inspect
import signal
from collections.abc import Callable, Iterator
from pathlib import Path

import psycopg
import pytest
import yaml

from location_data import claims_intake, contracts, loader_db, payloads, ruian_boundaries as rb, ruian_load
from location_data.resolver import drain, resolve_db
from scraper import jobs, stop

_ROOT = Path(__file__).resolve().parents[2]
_WORKFLOWS = _ROOT / ".github" / "workflows"

OUTER_GROUP = "location-batch"


# ---------------------------------------------------------------- 1. serialization


def test_no_workflow_holds_the_retired_location_batch_group():
    """The group was GitHub's. The claim intake left GitHub for the job runner (#2028), so the
    heavy lanes queue on the shared `location-heavy` resource, which the database enforces on any
    host; a workflow back in the group would serialise against nothing."""
    members = {
        path.name
        for path in sorted(_WORKFLOWS.glob("*.yml"))
        if OUTER_GROUP in str(((yaml.safe_load(path.read_text(encoding="utf-8")) or {})
                               .get("concurrency") or {}).get("group", ""))
    }
    assert members == set()


def test_the_heavy_location_jobs_share_one_resource():
    """The intake and the registry load never overlap: they share `location-heavy`, claimed
    through `scraper.job_lease` by the runner for the intake and by the load's own main."""
    assert {j.name for j in jobs.declared() if j.resource == jobs.LOCATION_HEAVY} == {
        "location_intake", "ruian_load"}
    assert "job_lease.held_job(" in inspect.getsource(ruian_load.main)


def test_env_timeout_s_never_yields_an_unbounded_or_broken_budget(monkeypatch):
    """0 means "no timeout" to Postgres — the exact state this mechanism exists to stop —
    so it is not reachable from an env var, and neither is a typo."""
    assert loader_db.env_timeout_s("LOCATION_TEST_TIMEOUT_S", 90) == 90
    for bad in ("0", "-5", "", "abc", "90s"):
        monkeypatch.setenv("LOCATION_TEST_TIMEOUT_S", bad)
        assert loader_db.env_timeout_s("LOCATION_TEST_TIMEOUT_S", 90) == 90
    monkeypatch.setenv("LOCATION_TEST_TIMEOUT_S", "45")
    assert loader_db.env_timeout_s("LOCATION_TEST_TIMEOUT_S", 90) == 45


def test_bounded_sets_transaction_local_timeouts_not_session_ones():
    """`SET LOCAL` scope is the whole point: the loader session runs
    statement_timeout = 0 for COPY, and a session-level SET here would clamp the next
    bulk phase instead of just this one."""
    conn = _RecordingConn()
    with loader_db.bounded(conn, 180) as cur:
        cur.execute("SELECT 1")
    guard, params = conn.executed[0]
    assert "set_config('statement_timeout'" in guard
    assert "set_config('lock_timeout'" in guard
    # third argument of both set_config calls == is_local
    assert guard.count("true") == 2
    assert params == {"statement_timeout": "180s", "lock_timeout": "5s"}
    assert conn.transactions == 1, "the guard must be inside a transaction or it no-ops"


# ---------------------------------------------------------------- 3. boundary loader


def test_the_per_unit_transaction_arms_a_bounded_statement_timeout(monkeypatch):
    """The wedge site. Run 31434818469 sat inside OBCE_P for 2 h 04 min under the
    session's statement_timeout = 0, with lock_timeout = 5 s proving it was not a lock
    wait and the libpq keepalives proving the socket was alive: a busy backend inside one
    per-unit PostGIS statement, which nothing could interrupt."""
    conn = _BoundaryConn(unit_id=7)
    layer = next(x for x in rb.LAYERS if x.token == "OBCE_P")
    rb.load_feature(conn, _feature("obec", 554782), layer, 3)

    guarded = conn.executed[conn.first_in_transaction]
    assert "set_config('statement_timeout'" in guarded[0]
    assert guarded[1]["statement_timeout"] == f"{rb.DEFAULT_UNIT_TIMEOUT_S}s"
    # and it is armed BEFORE any geometry statement, not after
    geometry_at = next(
        i for i, (sql, _) in enumerate(conn.executed)
        if "INSERT INTO ruian_admin_unit_geometries" in sql
    )
    assert conn.first_in_transaction < geometry_at


def test_the_per_unit_budget_is_env_overridable(monkeypatch):
    monkeypatch.setenv(rb.UNIT_TIMEOUT_ENV, "45")
    conn = _BoundaryConn(unit_id=7)
    layer = next(x for x in rb.LAYERS if x.token == "OBCE_P")
    rb.load_feature(conn, _feature("obec", 1), layer, 3)
    assert conn.executed[conn.first_in_transaction][1]["statement_timeout"] == "45s"


def test_the_name_upgrade_shares_the_units_guarded_transaction():
    """`upgrade_name` is part of the unit's transaction; opening its own cursor outside
    the guard would leave it as the one statement that can still hang."""
    assert list(inspect.signature(rb.upgrade_name).parameters) == ["cur", "unit_id", "name"]
    assert "conn.cursor()" not in inspect.getsource(rb.upgrade_name)
    source = inspect.getsource(rb.load_feature)
    assert "with loader_db.bounded(conn, budget) as cur:" in source
    assert "upgraded = upgrade_name(cur, unit_id, feature.name)" in source


def test_a_timed_out_unit_is_a_data_fault_and_never_spends_the_reconnect_budget():
    """`db.is_transient_db_error` answers True for EVERY OperationalError and
    QueryCanceled is one, so without the split a pathological geometry would burn two
    reconnects, and ~20 of them would abort a 253 MB pack with "reconnects exhausted" —
    blaming the environment for the data."""
    assert rb._is_unit_fault(psycopg.errors.QueryCanceled("canceling statement"))
    assert not rb._is_unit_fault(psycopg.OperationalError("SSL connection has been closed"))

    reconnects: list[int] = []

    def _never_called() -> object:  # pragma: no cover - asserted not to run
        reconnects.append(1)
        raise AssertionError("a statement timeout must not trigger a reconnect")

    calls: list[int] = []

    def _timeout(conn, feature, layer, version_id, *, unit_timeout_s=None):
        calls.append(1)
        raise psycopg.errors.QueryCanceled("canceling statement due to statement timeout")

    original = rb.load_feature
    rb.load_feature = _timeout
    try:
        how, upgraded, conn, error = rb.load_feature_resilient(
            "conn", _feature("obec", 1),
            next(x for x in rb.LAYERS if x.token == "OBCE_P"), 3,
            reconnector=rb.Reconnector(_never_called),
        )
    finally:
        rb.load_feature = original

    assert (how, upgraded) == (None, False)
    assert isinstance(error, psycopg.errors.QueryCanceled)
    assert calls == [1], "the unit is attempted once, not retried on a fresh session"
    assert reconnects == []


def test_the_unit_loop_emits_a_heartbeat_so_silence_is_diagnosable():
    """The whole diagnosis of run 31434818469 was "two hours of no output": the only
    per-unit lines the loader had were failure lines, so healthy-but-slow and wedged
    looked identical. The heartbeat names the code it is about to load."""
    source = inspect.getsource(rb.load_layers)
    assert "PROGRESS_EVERY" in source
    assert "BOUNDARY layer=%s at code=%s" in source
    assert rb.PROGRESS_EVERY > 0


# ---------------------------------------------------------------- 4. resolve drain


def test_the_batch_guard_is_set_local_and_env_overridable(monkeypatch):
    statements = drain._batch_guc(drain.DEFAULT_BATCH_TIMEOUT_S)
    assert statements[0] == "SET LOCAL statement_timeout = '30s'"
    assert statements[1] == f"SET LOCAL lock_timeout = '{drain.LOCK_TIMEOUT_S}s'"
    assert drain._batch_timeout_s() == drain.DEFAULT_BATCH_TIMEOUT_S
    monkeypatch.setenv(drain.BATCH_TIMEOUT_ENV, "90")
    assert drain._batch_timeout_s() == 90
    assert drain._batch_guc(90)[0] == "SET LOCAL statement_timeout = '90s'"


def test_every_statement_the_drain_runs_outside_a_batch_is_bounded_too():
    """The batch transaction already had a ceiling on 2026-08-10 — what did NOT was
    everything the loop runs between batches on the autocommit connection, where
    `SET LOCAL` from the previous transaction is long gone."""
    for fn in (drain._queue_health, drain._walk):
        assert "_bounded(" in inspect.getsource(fn), f"{fn.__name__} runs unbounded"
    for sweep in (drain.enqueue_full_sweep, drain.enqueue_portal_sweep):
        assert "_walk(" in inspect.getsource(sweep), sweep.__name__
    start = inspect.getsource(drain.run)
    # ONE run-start read survives W2-a (which registry version is current); the five policy
    # loads and the epoch probe went with their tables. It still has to be bounded — a run
    # that hangs on its first statement logs nothing at all.
    assert start.count("with _bounded(conn, batch_timeout_s)") >= 1, (
        "the run-start constant load must be bounded"
    )


def test_the_sweep_gets_its_own_budget_inside_the_workers_lease():
    """One sweep window is an anti-join over a 250k-id slice: far more than a per-listing
    statement (so the per-batch ceiling would fail it) and, measured, at most a few seconds —
    and since W2 it runs under the worker lane's lease (740 s), which one window must never
    outlive."""
    assert drain.DEFAULT_SWEEP_TIMEOUT_S > drain.DEFAULT_BATCH_TIMEOUT_S * 5
    assert drain.DEFAULT_SWEEP_TIMEOUT_S <= 600
    assert "SWEEP_TIMEOUT_ENV" in inspect.getsource(drain._walk)


def test_bounded_opens_a_transaction_so_set_local_is_not_a_no_op():
    """`db.connect_session()` is autocommit; outside a transaction `SET LOCAL` silently
    applies to nothing, which is the failure this helper exists to prevent."""
    source = inspect.getsource(drain._bounded)
    assert "with conn.transaction():" in source
    assert "_batch_guc(seconds)" in source


# ---------------------------------------------------------------- 5. claims intake


def test_the_failure_stamp_is_guarded_and_never_masks_the_real_exception():
    """Whatever broke the run may be the same pressure that hangs this one-row UPDATE.
    A bookkeeping write must not replace the exception the operator needs — the lesson
    `loader_db.record_discrepancy` already carries."""
    source = inspect.getsource(claims_intake.run)
    assert "with guarded(conn, _FAILURE_STAMP_TIMEOUT_S) as cur:" in source
    assert "INTAKE could not stamp batch" in source
    assert claims_intake._FAILURE_STAMP_TIMEOUT_S < claims_intake.DEFAULT_STATEMENT_TIMEOUT_S


def test_the_intake_preflight_reads_are_bounded_too():
    """A run that hangs before its first batch row exists leaves nothing to diagnose.
    Each preflight read must be the FIRST statement of a guarded block, not a bare
    `conn.cursor()` on the autocommit connection."""
    sources = {
        # Its own helper since #2028: a full walk carried on for one portal reads it too.
        "_ACTIVE_CONTRACT_SQL": (inspect.getsource(claims_intake._active_contract_id), "statement_timeout"),
        # W1-a2 replaced the watermark read with the cutover seed, in its own helper —
        # two reads, two guarded blocks, neither able to hang the run before its first
        # batch row exists.
        "_LEGACY_WATERMARK_SQL": (inspect.getsource(claims_intake._snapshot_seed),
                                  "statement_timeout"),
        "_SNAPSHOT_SEED_SQL": (inspect.getsource(claims_intake._snapshot_seed),
                               "statement_timeout"),
        # ... and added one more: the drain's window. (W1-a4 split the selection in two;
        # the WINDOW is the statement that opens the block.)
        "_UNMINED_WINDOW_SQL": (inspect.getsource(claims_intake.drain_unmined_bodies),
                                "statement_timeout"),
        # The run-end readout is bounded too, and by a ceiling OF ITS OWN (W6-b2): it is
        # best-effort, so a count that cannot answer in a minute reports `?` rather than
        # spending the per-batch 600 s on a run whose work is already done and stamped.
        "_UNMINED_BODY_BACKLOG_SQL": (inspect.getsource(claims_intake._unmined_body_backlog),
                                      "_BACKLOG_READOUT_TIMEOUT_S"),
    }
    for sql, (source, timeout) in sources.items():
        opener = source.split(f"cur.execute({sql}")[0].rstrip().splitlines()[-1].strip()
        assert opener == f"with guarded(conn, {timeout}) as cur:", (
            f"{sql} is not read inside a guarded transaction (opener was {opener!r})"
        )
    # And the drain's SECOND statement rides the SAME transaction — a window whose ids were
    # resolved by a later, separate transaction could see a listing delisted in between.
    drain = inspect.getsource(claims_intake.drain_unmined_bodies)
    between = drain.split("cur.execute(_UNMINED_WINDOW_SQL")[1].split(
        "cur.execute(_UNMINED_BODIES_SQL")[0]
    assert "with guarded(" not in between, (
        "the window and the join statement must share one guarded transaction"
    )


def test_the_intake_batch_budget_is_env_overridable(monkeypatch):
    """The CLI default is resolved from the env at parse time, so a lane can be widened
    without a deploy."""
    assert (
        "loader_db.env_timeout_s(STATEMENT_TIMEOUT_ENV, DEFAULT_STATEMENT_TIMEOUT_S)"
        in " ".join(inspect.getsource(claims_intake.build_parser).split())
    )
    monkeypatch.setenv(claims_intake.STATEMENT_TIMEOUT_ENV, "120")
    assert loader_db.env_timeout_s(
        claims_intake.STATEMENT_TIMEOUT_ENV, claims_intake.DEFAULT_STATEMENT_TIMEOUT_S
    ) == 120


# ---------------------------------------------------------------- 6. registry lookups


def _flat(sql: str) -> str:
    return " ".join(sql.split()).lower()


def test_address_point_lookups_address_the_indexed_column():
    """`ruian_ap_obec_hn` is (obec_unit_id, cislo_domovni); `obec_kod` has no index at all.
    Measured on the live 3,020,222-row mirror: 21,494 ms -> 25 ms. (The `cast_obce_kod`
    half of this lesson went with `_CAST_OBCE_EXTENT_SQL` in W2-a — the question had no
    reader left.)"""
    by_number = _flat(resolve_db._ADDRESS_POINTS_BY_NUMBER_SQL)
    assert "ap.obec_unit_id in (select u.id from ruian_admin_units u" in by_number
    assert "ap.obec_kod = %s" not in by_number


def test_the_unit_id_hop_does_not_narrow_the_answer_to_open_scd2_rows():
    """`ruian_admin_units` is SCD-2. An address point points at the unit row that was
    current when it was loaded, so restricting the code->id hop to `valid_to IS NULL`
    would return nothing for rows whose unit has since been superseded — strictly less
    than `obec_kod = %s` matched."""
    hop = _flat(resolve_db._ADDRESS_POINTS_BY_NUMBER_SQL).split(
        "from ruian_admin_units u", 1)[1].split(")", 1)[0]
    assert "valid_to" not in hop, f"the unit hop narrows to open rows only: {hop!r}"


def test_the_level_predicate_casts_the_parameter_not_the_column():
    """`u.level::text = %s` casts the COLUMN and throws away the leading column of
    `ruian_admin_units_code (level, code)`; `u.level = %s::ruian_level` casts the
    parameter and keeps both."""
    for sql in (resolve_db._ADMIN_CHAIN_BY_CODE_SQL,
                resolve_db._ADDRESS_POINTS_BY_NUMBER_SQL):
        flat = _flat(sql)
        assert "u.level::text = %s" not in flat
        if "ruian_admin_units u" in flat:
            assert "u.level = " in flat


def test_containing_obec_lets_the_partial_pip_index_do_its_job():
    """`purpose IN ('pip','authoritative')` cannot use `ruian_aug_pip_gist ... WHERE
    purpose = 'pip'`, so ST_Covers ran against raw obec polygons (194 ms, 1,225 buffers).
    The pip pieces alone answer it (0.24 ms/point measured across a 50-point batch); the
    authoritative fallback branch went with the partially-loaded pack it served (MF PR-B,
    pip = authoritative for every unit of every loaded version)."""
    flat = _flat(resolve_db.CONTAINING_OBEC_SQL)
    assert "g.purpose = 'pip'" in flat
    assert "authoritative" not in flat
    assert "union all" not in flat


def test_every_point_keyed_question_binds_one_array_shape():
    """The coordinate-keyed questions have ONE statement each, taking parallel arrays,
    so `warm_points` (whole slice, one round trip) and the lazy single-point path cannot
    drift — which is what makes warming invisible to the pure core. A placeholder mismatch
    here is a runtime error on the first coordinate the drain resolves."""
    for sql, expected in (
        (resolve_db.CONTAINING_OBEC_SQL, 4),       # 3 arrays + a version
        (resolve_db._NEAREST_OBEC_SQL, 6),         # 3 arrays + (version, box, radius)
        (resolve_db._IN_CZ_SQL, 4),                # 3 arrays + a version
    ):
        assert sql.count("%s") == expected, _flat(sql)
    for name in ("containing_obec", "in_czechia_polygon"):
        single = inspect.getsource(getattr(resolve_db.SqlRegistryView, name))
        assert f"self.{name}_bulk([(lat, lon)]).get(0)" in single


def test_the_geography_predicate_carries_an_index_usable_bbox():
    """`ST_DWithin(geom::geography, ...)` is a FILTER against a geometry GiST index, so the
    unbounded form scanned everything: 6,752 ms/point for `nearest_obec_within`, every
    boundary row cast to geography, the state polygon included. The `&&` box is the Index
    Cond that bounds it; 60,000 under-estimates metres per degree at CZ latitudes, so the box
    strictly CONTAINS the geodesic circle and cannot hide a row the exact `ST_DWithin` kept.

    (`cast_obce_for_point` carried the same lesson and went with the question in W2-a — FILL
    takes the quarter off the bound entity's own chain now.)"""
    flat = _flat(resolve_db._NEAREST_OBEC_SQL)
    assert "&& st_expand(" in flat, flat
    assert "/ 60000.0)" in flat, flat
    assert "st_dwithin(" in flat, flat


def test_nearest_obec_reads_the_subdivided_pieces_like_containment_does():
    """The pip pieces TILE the authoritative polygon, so the minimum distance over them IS
    the distance to the polygon — and they are small enough that the geography cast is cheap
    (2.95 ms/point vs 6,752). No authoritative branch, exactly as in `CONTAINING_OBEC_SQL`."""
    flat = _flat(resolve_db._NEAREST_OBEC_SQL)
    assert "g.purpose = 'pip'" in flat
    assert "authoritative" not in flat
    assert "union all" not in flat


def test_the_sliver_fallback_is_asked_lazily_not_warmed():
    """It is reached only when `containing_obec` missed — ~1 % of listings — so warming it
    would run a geography lateral for all 250 of a slice's points to answer the
    two or three that ask. The other two point-keyed questions ARE warmed."""
    warm = inspect.getsource(resolve_db.warm_points)
    assert "containing_obec_bulk" in warm and "in_czechia_polygon_bulk" in warm
    assert "nearest_obec" not in warm


def test_the_registry_lookup_index_migration_ships_as_a_file():
    """Additive-only, and deliberately NOT applied with the code: two of the three build
    over 3.02 M rows and want a quiet instance."""
    path = _ROOT / "migrations" / "389_location_w1_registry_lookup_indexes.sql"
    sql = path.read_text(encoding="utf-8").lower()
    assert "set local lock_timeout" in sql
    assert "set local statement_timeout" in sql
    for forbidden in ("drop ", "alter table", "truncate", "delete from"):
        assert forbidden not in sql, f"389 is additive; found {forbidden!r}"
    assert sql.count("create index if not exists") == 3


# ---------------------------------------------------------------- fakes


def _feature(level: str, code: int) -> rb.BoundaryFeature:
    return rb.BoundaryFeature(level=level, code=code, name=f"unit {code}", wkb=b"")


class _Cursor:
    def __init__(self, conn: "_RecordingConn") -> None:
        self.conn = conn
        self.rowcount = 1

    def __enter__(self) -> "_Cursor":
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def execute(self, sql: str, params: object = None) -> None:
        self.conn.executed.append((sql, params))

    def fetchone(self):
        return (self.conn.result,)

    def fetchall(self):
        return []


class _Tx:
    def __init__(self, conn: "_RecordingConn") -> None:
        self.conn = conn

    def __enter__(self) -> "_Tx":
        self.conn.transactions += 1
        self.conn.depth += 1
        return self

    def __exit__(self, *exc: object) -> bool:
        self.conn.depth -= 1
        return False


class _RecordingConn:
    def __init__(self, result: object = None) -> None:
        self.executed: list[tuple[str, object]] = []
        self.transactions = 0
        self.depth = 0
        self.result = result

    def cursor(self) -> _Cursor:
        return _Cursor(self)

    def transaction(self) -> _Tx:
        return _Tx(self)


class _BoundaryConn(_RecordingConn):
    """`unit_id_for` runs BEFORE the guarded transaction (it is the lookup that decides
    whether there is a unit at all), so the fake records where the transaction starts."""

    def __init__(self, unit_id: int) -> None:
        super().__init__(result=unit_id)
        self.first_in_transaction = -1

    def cursor(self) -> _Cursor:
        return _GuardAwareCursor(self)


class _GuardAwareCursor(_Cursor):
    def execute(self, sql: str, params: object = None) -> None:
        conn: _BoundaryConn = self.conn  # type: ignore[assignment]
        if conn.depth and conn.first_in_transaction < 0:
            conn.first_in_transaction = len(conn.executed)
        super().execute(sql, params)

# ------------------------------------- 4. the intake's more-remains exit (the chain's successor)

# `bodies_mined` / `listings` are the PROGRESS terms: work left is not enough, the run must
# also have moved the half that is unfinished.
BACKLOG = {"bodies_pass_complete": False, "bodies_mined": 1500,
           "reached_end": True, "listings": 400, "outcome": "stopped"}
DRAINED = {"bodies_pass_complete": True, "bodies_mined": 12,
           "reached_end": True, "listings": 400, "outcome": "ok"}


@pytest.mark.parametrize("stats,more", [
    # The backlog case, every run for the days after a contract bump.
    (BACKLOG, True),
    # Steady state: the hourly slot owns the hour.
    (DRAINED, False),
    # The other half's backlog: a scan stopped on its budget mid-log.
    (dict(DRAINED, reached_end=False, listings=20000), True),
    # THE ENDLESS-LOOP DEFECT: `bodies_pass_complete` is False until the drain sets it, so a
    # run with no page-capable portal or no R2 credential reports a backlog it never looked at.
    (dict(BACKLOG, bodies_mined=0), False),
    # The same rail on the payload half: a scan that stopped before its first batch.
    (dict(DRAINED, reached_end=False, listings=0), False),
])
def test_more_remains_only_for_work_this_run_moved(stats: dict, more: bool):
    assert claims_intake.more_remains(stats) is more


class _Connect:
    def __enter__(self) -> object:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False


def _main(monkeypatch: pytest.MonkeyPatch, stats: dict, *argv: str,
          refused: Exception | None = None, during: Callable[[], None] = lambda: None) -> tuple[int, list[str]]:
    calls: list[str] = []
    monkeypatch.setenv("SUPABASE_DB_URL", "postgresql://stub")
    monkeypatch.setattr(claims_intake.db, "connect", lambda *a, **k: _Connect())
    monkeypatch.setattr(contracts, "load_all", lambda: ["sreality@4"])

    def projected(conn: object, contract: str, **kwargs: str) -> None:
        calls.append(f"project {contract} {kwargs}")
        if refused is not None:
            raise refused

    def ran(conn: object, **kwargs: object) -> dict:
        calls.append("run")
        during()
        return dict(stats)

    monkeypatch.setattr(contracts, "project", projected)
    monkeypatch.setattr(claims_intake, "run", ran)
    return claims_intake.main(list(argv)), calls


def test_a_run_that_leaves_work_it_moved_exits_75_so_the_runner_re_dues_it(monkeypatch):
    """The runner reads 75 as ok and re-dues the job a minute later (`job_lease.MORE_REMAINS_S`):
    a backlog drains run after run, the chain's work with no dispatch, no yield and no group."""
    monkeypatch.setenv("RAILWAY_GIT_COMMIT_SHA", "abc123")
    assert _main(monkeypatch, BACKLOG) == (75, ["project sreality@4 {'git_ref': 'abc123'}", "run"])
    assert _main(monkeypatch, DRAINED)[0] == 0


def test_the_hourly_job_fetches_bodies_as_wide_as_the_workflow_did(monkeypatch):
    """The workflow set LOCATION_BODY_FETCH_WORKERS=32 (W1-a4: the R2 fetch is most of a bodies batch) and the runner
    sets no such variable, so the intake's own main does, a value already set first. The default stays 16 for every
    other user of the archive client (each scrape's `append_payload`); the minute lane sets its own 8."""
    monkeypatch.setenv(payloads.BODY_FETCH_WORKERS_ENV, "registers the undo")
    monkeypatch.delenv(payloads.BODY_FETCH_WORKERS_ENV)
    assert payloads.body_fetch_workers() == 16
    widths: list[int] = []
    _main(monkeypatch, DRAINED, during=lambda: widths.append(payloads.body_fetch_workers()))
    monkeypatch.setenv(payloads.BODY_FETCH_WORKERS_ENV, "24")
    _main(monkeypatch, DRAINED, during=lambda: widths.append(payloads.body_fetch_workers()))
    assert widths == [32, 24]


@pytest.fixture(autouse=True)
def _restore_signals() -> Iterator[None]:
    """`claims_intake.main` installs the stop handlers: pytest gets its own back, and its Ctrl-C."""
    saved = {s: signal.getsignal(s) for s in (signal.SIGTERM, signal.SIGINT)}
    yield
    for s, handler in saved.items():
        signal.signal(s, handler)
    stop.reset()


def test_a_deploy_s_sigterm_finalizes_the_run_and_exits_interrupted(monkeypatch):
    """The runner SIGTERMs its child at a deploy (`job_process.GRACE_S` before the SIGKILL). The handler only marks the
    stop, so `run` ends after the batch in hand and stamps its cursor; 143 reads as interrupted, due again at once,
    never as a 75 whose `ok` would hide the cut."""
    def sigterm() -> None:
        signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)  # SIG_DFL, an int, would kill the process

    assert _main(monkeypatch, BACKLOG, during=sigterm)[0] == stop.EXIT_CODE


def test_a_dry_run_projects_nothing_and_never_asks_for_more(monkeypatch):
    assert _main(monkeypatch, BACKLOG, "--dry-run") == (0, ["run"])


def test_a_contract_changed_without_a_bump_refuses_the_run(monkeypatch):
    """Every run first projects the contracts from the image, as the workflow's first step did:
    git is the store of record, and a changed contract under a loaded version fails loudly."""
    monkeypatch.delenv("RAILWAY_GIT_COMMIT_SHA", raising=False)
    refused = contracts.ContractError("sreality@4 is already loaded with a different sha256")
    assert _main(monkeypatch, BACKLOG, refused=refused) == (2, ["project sreality@4 {'git_ref': 'local'}"])
