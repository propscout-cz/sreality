"""Wave 1 (W1-3) — estimation execution job lane (Amendment A10).

Hermetic tests for the submit-side behavior of the flag-gated job lane:
`_job_payload` round-trip, `create_estimation_run` routing rows to the lane
(pending + payload, no in-process execution) vs. the legacy inline/background
path, `_insert_run`'s optional job_payload column, `execute_pending_run`
rehydration, and the claim-time-keyed stuck-run sweep. The worker-side lane
(claim + drain) is covered in tests/scraper/test_realtime_worker.py.
"""

from __future__ import annotations

from typing import Any

import pytest
from psycopg.types.json import Jsonb

from api import estimation_runs as er
from api import schemas as s


class _Ctx:
    def __enter__(self) -> "_Ctx":
        return self

    def __exit__(self, *exc: Any) -> None:
        return None


class _Cur:
    def __init__(self, conn: "_FakeConn") -> None:
        self._conn = conn

    def __enter__(self) -> "_Cur":
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def execute(self, sql: str, params: Any = None) -> None:
        self._conn.executed.append((" ".join(sql.split()), params))

    def fetchone(self) -> Any:
        return (42,)

    def fetchall(self) -> list[Any]:
        return []


class _FakeConn:
    def __init__(self) -> None:
        self.executed: list[tuple[str, Any]] = []

    def transaction(self) -> _Ctx:
        return _Ctx()

    def cursor(self) -> _Cur:
        return _Cur(self)


def _resolution() -> er._Resolution:
    return er._Resolution(
        target_spec={"lat": 50.0, "lng": 14.0, "area_m2": 42, "disposition": "2+kk"},
        input_listing_id=70, input_sreality_id=7,
        subject_source="sreality", subject_source_id_native="7",
        subject_listing_price_czk=4_000_000, subject_listing_category_type="prodej",
    )


def _stub_setup(monkeypatch: Any, resolution: er._Resolution) -> None:
    """Monkeypatch create_estimation_run's setup helpers so it reaches the
    INSERT + dispatch decision without a live LLM / DB."""
    monkeypatch.setattr(er, "_resolve_input", lambda *a, **k: resolution)
    monkeypatch.setattr(er, "load_filter_defaults", lambda *a, **k: object())
    monkeypatch.setattr(er, "_build_target", lambda *a, **k: object())
    monkeypatch.setattr(er, "_build_filters", lambda *a, **k: object())
    monkeypatch.setattr(er, "_derive_yield_inputs", lambda body, res: (None, None, None))
    monkeypatch.setattr(er, "_fetch_run", lambda conn, run_id: {"id": run_id})


# --- _job_payload round-trip ---------------------------------------------------


def test_job_payload_roundtrips() -> None:
    resolution = _resolution()
    body = s.CreateEstimationIn(listing_id=70, source="extension")
    payload = er._job_payload(body, resolution)

    b2 = s.CreateEstimationIn(**payload["body"])
    r2 = er._Resolution(**payload["resolution"])

    assert b2.source == "extension" and b2.listing_id == 70
    assert r2 == resolution


# --- create_estimation_run routing --------------------------------------------


def test_lane_on_inserts_pending_with_payload_and_skips_execution(monkeypatch: Any) -> None:
    conn = _FakeConn()
    resolution = _resolution()
    _stub_setup(monkeypatch, resolution)
    monkeypatch.setattr(er, "_job_lane_enabled", lambda conn: True)
    monkeypatch.setattr(er, "_execute_estimation_run", lambda *a, **k: pytest.fail(
        "lane-on must NOT execute in-process"))

    body = s.CreateEstimationIn(listing_id=70, source="extension")
    out = er.create_estimation_run(conn, sreality_client=None, llm_client=None, body=body)

    assert out == {"id": 42}
    insert_sql, params = conn.executed[0]
    assert params["status"] == "pending"
    assert "job_payload" in insert_sql
    assert isinstance(params["job_payload"], Jsonb)


def test_lane_on_agent_run_starts_pending_not_running(monkeypatch: Any) -> None:
    """Agent runs normally INSERT 'running'; on the lane they must start
    'pending' so the worker's claim (WHERE status='pending') can see them —
    the worker stamps 'running' + claimed_at at claim time."""
    conn = _FakeConn()
    _stub_setup(monkeypatch, _resolution())
    monkeypatch.setattr(er, "_job_lane_enabled", lambda conn: True)

    class _Skill:
        name = "rental_estimator_full_v1"
        version = 3

    monkeypatch.setattr("api.skills.load_skill", lambda conn, name: _Skill())
    monkeypatch.setattr(er, "_execute_estimation_run", lambda *a, **k: pytest.fail(
        "lane-on must NOT execute in-process"))

    body = s.CreateEstimationIn(listing_id=70, source="extension", mode="agent")
    er.create_estimation_run(conn, sreality_client=None, llm_client=None, body=body)

    _, params = conn.executed[0]
    assert params["status"] == "pending"
    assert params["mode"] == "agent"


def test_lane_off_runs_inline_without_payload(monkeypatch: Any) -> None:
    conn = _FakeConn()
    _stub_setup(monkeypatch, _resolution())
    monkeypatch.setattr(er, "_job_lane_enabled", lambda conn: False)
    ran: list[int] = []
    monkeypatch.setattr(
        er, "_execute_estimation_run",
        lambda conn, sc, lc, run_id, **k: ran.append(run_id))

    body = s.CreateEstimationIn(listing_id=70, source="extension")
    er.create_estimation_run(conn, sreality_client=None, llm_client=None, body=body)

    assert ran == [42]  # inline executor DID run
    insert_sql, params = conn.executed[0]
    assert params["status"] == "pending"
    assert "job_payload" not in insert_sql  # no snapshot when the lane is off


# --- _insert_run optional job_payload -----------------------------------------


def _base_fields() -> dict[str, Any]:
    return {
        "account_id": "11111111-1111-1111-1111-111111111111",
        "source": "extension", "mode": "deterministic", "status": "pending",
        "estimate_kind": "rent", "input_listing_id": 1,
        "input_sreality_id": 1,
    }


def test_insert_run_includes_job_payload_when_present() -> None:
    conn = _FakeConn()
    er._insert_run(conn, job_payload={"body": {}, "resolution": {}}, **_base_fields())
    sql, params = conn.executed[0]
    assert "job_payload" in sql
    assert isinstance(params["job_payload"], Jsonb)


def test_insert_run_omits_job_payload_when_none() -> None:
    conn = _FakeConn()
    er._insert_run(conn, job_payload=None, **_base_fields())
    sql, _ = conn.executed[0]
    assert "job_payload" not in sql


# --- execute_pending_run -------------------------------------------------------


def test_execute_pending_run_rehydrates_and_runs(monkeypatch: Any) -> None:
    conn = _FakeConn()
    monkeypatch.setattr(er, "load_filter_defaults", lambda conn: object())
    monkeypatch.setattr(er, "_build_target", lambda spec, sid=None, lid=None: object())
    monkeypatch.setattr(er, "_build_filters", lambda body, defaults: object())
    seen: dict[str, Any] = {}
    monkeypatch.setattr(
        er, "_execute_estimation_run",
        lambda conn, sc, lc, run_id, *, body, resolution, target, filters:
            seen.update(run_id=run_id, body=body, resolution=resolution))

    payload = er._job_payload(
        s.CreateEstimationIn(listing_id=70, source="extension"),
        _resolution())
    er.execute_pending_run(conn, None, None, 42, payload)

    assert seen["run_id"] == 42
    assert isinstance(seen["body"], s.CreateEstimationIn)
    assert isinstance(seen["resolution"], er._Resolution)
    assert seen["resolution"].target_spec["disposition"] == "2+kk"


def test_execute_pending_run_bad_payload_marks_failed(monkeypatch: Any) -> None:
    conn = _FakeConn()
    marked: dict[str, Any] = {}
    monkeypatch.setattr(
        er, "_safe_mark_failed",
        lambda conn, run_id, msg: marked.update(run_id=run_id, msg=msg))
    monkeypatch.setattr(er, "_execute_estimation_run", lambda *a, **k: pytest.fail(
        "must not execute on an invalid payload"))

    er.execute_pending_run(
        conn, None, None, 99,
        {"body": {"url": "x"}, "resolution": {"unexpected_field": 1}})

    assert marked["run_id"] == 99
    assert "payload invalid" in marked["msg"]


# --- sweep keyed off claim time ------------------------------------------------


def test_sweep_stuck_runs_keys_off_claimed_at() -> None:
    conn = _FakeConn()
    er.sweep_stuck_runs(conn, older_than_minutes=15)
    sql, params = conn.executed[0]
    low = sql.lower()
    assert "coalesce(claimed_at, created_at)" in low
    assert params == (15,)


class _SettingConn:
    """One app_settings row (None = no row); `boom` makes the read raise."""

    def __init__(self, row: tuple[Any] | None, boom: bool = False) -> None:
        self.row, self.boom = row, boom

    def cursor(self) -> "_SettingConn":
        return self

    def __enter__(self) -> "_SettingConn":
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False

    def execute(self, *args: Any) -> None:
        if self.boom:
            raise RuntimeError("settings down")

    def fetchone(self) -> tuple[Any] | None:
        return self.row


@pytest.mark.parametrize(("value", "on"), [
    (True, True), (False, False), (" Yes ", True), ("on", True), ("1", True), ("off", False),
    ("", False), (1, True), (0, False), (None, None),
])
def test_both_estimation_flags_read_a_setting_the_same_way_but_default_apart(value, on):
    """One parser for the job-lane flag and the budget flag; an absent value, a missing row or a
    failed read is OFF for the lane (it ships dark) and ON for the budget (fail-closed)."""
    assert er._job_lane_enabled(_SettingConn((value,))) is (on is True)
    assert er._budget_enabled(_SettingConn((value,))) is (on is not False)
    assert er._job_lane_enabled(_SettingConn(None)) is False
    assert er._budget_enabled(_SettingConn(None)) is True
    assert er._job_lane_enabled(_SettingConn(None, boom=True)) is False
    assert er._budget_enabled(_SettingConn(None, boom=True)) is True
