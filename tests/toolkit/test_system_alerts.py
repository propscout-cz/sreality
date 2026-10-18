"""Hermetic tests for the health ladder (toolkit.system_alerts) over an in-memory fake database."""

from __future__ import annotations

import datetime as _dt
import json
from typing import Any

import pytest

from toolkit.system_alerts import MESSAGE_MAX_CHARS, emit_system_alert, reconcile

_MISSING = object()
_T0 = _dt.datetime(2026, 10, 18, 6, 0, tzinfo=_dt.timezone.utc)
# A registration older than every row a test writes, so only the cutover tests meet the guard.
_DECLARED = _dt.datetime(2026, 1, 1, tzinfo=_dt.timezone.utc)
# Check.staleness_budget on the github-actions host: a cadence plus GitHub's 18 h delivery slack.
_GITHUB_SLACK_S = 18 * 3600
_CUTOVER = "c.run_at >= hc.declared_at"
_MEASURED_CUTOVER = "r.run_at >= hc.declared_at"
# An hourly key's hold: two 54-minute due intervals (a cadence less its 6-minute due slack).
_HOURLY_HOLD = _dt.timedelta(minutes=108)
_ZERO = {"opened": 0, "reminded": 0, "recovered": 0, "closed_silently": 0, "notices": 0}
_INCIDENT_COLUMNS = ("id", "signature", "first_seen_at", "last_seen_at", "alert_count",
                     "alerted_at", "ok_since", "acknowledged_at")


def _h(hours: float) -> _dt.timedelta:
    return _dt.timedelta(hours=hours)


def _s(seconds: float) -> _dt.timedelta:
    return _dt.timedelta(seconds=seconds)


def _iso(at: _dt.datetime) -> str:
    return at.strftime("%Y-%m-%dT%H:%M:%SZ")


def _cell(subject: str, status: str, message: str = "") -> dict[str, Any]:
    return {"subject": subject, "portal": subject, "status": status, "value": None, "unit": "",
            "message": message, "facts": {}}


class _FakeCursor:
    """Routes the ladder's statements, by their SQL text, onto the fake's tables."""

    def __init__(self, conn: "_FakeConn") -> None:
        self._conn = conn
        self._rows: list[tuple[Any, ...]] = []
        self.rowcount = 0

    def execute(self, sql: str, params: Any = None) -> None:
        if params is not None:
            sql % params  # psycopg's placeholder rules: a stray % or a missing parameter raises
        c = self._conn
        c.executed.append((sql, params))
        norm = " ".join(sql.split())
        self._rows, self.rowcount = [], 0
        if "pg_try_advisory_xact_lock" in norm:
            self._rows = [(c.lock_free,)]
        elif "FROM health_checks" in norm:
            # The registration guard holds only where the statement states it.
            self._rows = c.newest_rows(cutover=_CUTOVER in norm)
        elif "FROM app_settings" in norm:
            self._rows = [] if c.channels is _MISSING else [(c.channels,)]
        elif "INSERT INTO notification_dispatches" in norm:
            message, key, channels = params
            if key not in {d[1] for d in c.dispatches}:  # ON CONFLICT (dedupe_key) DO NOTHING
                c.dispatches.append((message, key, channels))
                self.rowcount = 1
        elif "INSERT INTO ops_incidents" in norm:
            if not any(i["signature"] == params["sig"] for i in c.open_incidents()):
                inc = c.insert_incident(params["sig"], params["at"])
                self._rows, self.rowcount = [(inc["id"],)], 1
        elif "FROM ops_incidents" in norm:
            prefix = params["cells"].removesuffix("%")
            self._rows = [tuple(i[col] for col in _INCIDENT_COLUMNS) for i in c.open_incidents()
                          if i["signature"].startswith(prefix)]
        elif "FROM pipeline_check_results" in norm:
            guarded = _MEASURED_CUTOVER in norm  # again only where the statement states it
            self._rows = [(key, at, status, details.get("cells"))
                          for key in params["keys"]
                          for at, status, _value, details in c.results.get(key, [])
                          if status != "unknown" and at >= params["since"]
                          and not (guarded and at < c.registry[key]["declared_at"])]
        elif norm.startswith("UPDATE ops_incidents"):
            self.rowcount = c.update(norm, params)
        else:
            raise AssertionError(f"unrouted statement: {norm}")

    def fetchone(self) -> tuple[Any, ...] | None:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[tuple[Any, ...]]:
        return list(self._rows)

    def __enter__(self) -> "_FakeCursor":
        return self

    def __exit__(self, *exc: Any) -> None:
        return None


class _FakeTx:
    def __init__(self, conn: "_FakeConn") -> None:
        self._conn = conn

    def __enter__(self) -> "_FakeTx":
        self._conn.transactions += 1
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False


class _FakeConn:
    """The four tables the ladder touches, in memory."""

    def __init__(self, *, channels: Any = _MISSING, lock_free: bool = True) -> None:
        self.channels = channels
        self.lock_free = lock_free
        self.registry: dict[str, dict[str, Any]] = {}
        self.results: dict[str, list[tuple[_dt.datetime, str, Any, dict[str, Any]]]] = {}
        self.newest: dict[str, tuple[_dt.datetime, str, Any, dict[str, Any]]] = {}
        self.incidents: list[dict[str, Any]] = []
        self.dispatches: list[tuple[str, str, list[str]]] = []
        self.executed: list[tuple[str, Any]] = []
        self.transactions = 0

    def cursor(self) -> _FakeCursor:
        return _FakeCursor(self)

    def transaction(self) -> _FakeTx:
        return _FakeTx(self)

    def register(self, key: str, *, cls: str = "alarm", cadence_s: int = 3600,
                 label: str = "", unit: str = "", budget_s: float | None = None,
                 declared_at: _dt.datetime = _DECLARED) -> None:
        """A health_checks row; the budget defaults to the github-actions rule."""
        budget = cadence_s + _GITHUB_SLACK_S if budget_s is None else budget_s
        self.registry[key] = {"class": cls, "cadence_s": cadence_s, "label": label or key,
                              "unit": unit, "budget_s": float(budget), "declared_at": declared_at}

    def result(self, key: str, at: _dt.datetime, status: str, *, message: str = "",
               cells: list[dict[str, Any]] | None = None, value: Any = None) -> None:
        details: dict[str, Any] = {"message": message} if message else {}
        if cells is not None:
            details["cells"] = cells
        row = (at, status, value, details)
        self.results.setdefault(key, []).append(row)
        if key not in self.newest or at >= self.newest[key][0]:
            self.newest[key] = row

    def newest_rows(self, *, cutover: bool) -> list[tuple[Any, ...]]:
        rows = []
        for key, reg in self.registry.items():
            newest = self.newest.get(key)
            if cutover and newest is not None and newest[0] < reg["declared_at"]:
                newest = None  # the newest row predates the registration, so every row does
            at, status, value, details = newest or (None, None, None, {})
            rows.append((key, reg["class"], reg["cadence_s"], reg["label"], reg["unit"],
                         reg["budget_s"], status, at, value, details.get("cells"),
                         details.get("message")))
        return rows

    def newest_status(self, key: str) -> str | None:
        return self.newest[key][1] if key in self.newest else None

    def insert_incident(self, signature: str, at: _dt.datetime) -> dict[str, Any]:
        inc = {"id": len(self.incidents) + 1, "signature": signature, "first_seen_at": at,
               "last_seen_at": at, "alert_count": 0, "alerted_at": None, "ok_since": None,
               "acknowledged_at": None, "resolved_at": None, "resolve_reason": None}
        self.incidents.append(inc)
        return inc

    def seed(self, key: str, subject: str = "", *, first: _dt.datetime,
             last: _dt.datetime | None = None, alerted: _dt.datetime | None = None,
             ok_since: _dt.datetime | None = None,
             acked: _dt.datetime | None = None) -> dict[str, Any]:
        """An open cell incident as an earlier pass left it (told at onset unless `alerted`)."""
        inc = self.insert_incident(f"cell:{key}:{subject or key}", first)
        inc.update(last_seen_at=last or first, alert_count=1, alerted_at=alerted or first,
                   ok_since=ok_since, acknowledged_at=acked)
        return inc

    def open_incidents(self) -> list[dict[str, Any]]:
        return [i for i in self.incidents if i["resolved_at"] is None]

    def update(self, norm: str, p: dict[str, Any]) -> int:
        ids = set(p["ids"]) if "ids" in p else {p["id"]}
        hit = 0
        for inc in self.incidents:
            if inc["id"] not in ids:
                continue
            if "SET last_seen_at" in norm:
                inc["last_seen_at"], inc["ok_since"] = max(inc["last_seen_at"], p["at"]), None
            elif "SET ok_since" in norm:
                if inc["ok_since"] is not None:
                    continue
                inc["ok_since"] = p["at"]
            elif "SET resolved_at" in norm:
                if inc["resolved_at"] is not None:
                    continue
                inc["resolved_at"], inc["resolve_reason"] = p["now"], p["reason"]
            elif "SET alert_count" in norm:
                inc["alert_count"], inc["alerted_at"] = inc["alert_count"] + 1, p["now"]
            else:
                raise AssertionError(f"unrouted UPDATE: {norm}")
            hit += 1
        return hit

    def writes(self) -> list[str]:
        return [s for s, _ in self.executed if s.lstrip().startswith(("INSERT", "UPDATE"))]


# --- opening ---------------------------------------------------------------


def test_a_fail_opens_one_incident_per_cell_and_tells_them_in_one_onset() -> None:
    conn = _FakeConn()
    conn.register("walk_end_streak", cadence_s=21600, label="Walks not reaching the end")
    conn.result("walk_end_streak", _T0, "fail", cells=[
        _cell("sreality", "fail", "3 walks stopped early"),
        _cell("bazos", "fail", "2 walks stopped early"),
        _cell("idnes", "ok", "every walk reached the end"),
    ])
    now = _T0 + _h(0.1)
    assert reconcile(conn, now=now) == {**_ZERO, "opened": 2, "notices": 1}
    assert sorted(i["signature"] for i in conn.open_incidents()) == [
        "cell:walk_end_streak:bazos", "cell:walk_end_streak:sreality"]
    assert conn.dispatches == [(
        "Walks not reaching the end: Problem — sreality: 3 walks stopped early; "
        "bazos: 2 walks stopped early",
        "sys:walk_end_streak:onset:2026-10-18T06:00:00Z", [])]
    for inc in conn.open_incidents():
        assert inc["first_seen_at"] == inc["last_seen_at"] == _T0
        assert (inc["alert_count"], inc["alerted_at"]) == (1, now)


def test_a_result_without_cells_is_one_implicit_cell_named_by_its_key() -> None:
    conn = _FakeConn()
    conn.register("llm_errors", label="LLM errors")
    conn.result("llm_errors", _T0, "fail", message="openai: 41 credit errors in the last hour")
    reconcile(conn, now=_T0)
    assert [i["signature"] for i in conn.open_incidents()] == ["cell:llm_errors:llm_errors"]
    # The implicit cell is the check itself, so the notice does not repeat the key.
    assert conn.dispatches[0][0] == (
        "LLM errors: Problem — openai: 41 credit errors in the last hour")


def test_warn_never_opens_an_incident() -> None:
    conn = _FakeConn()
    conn.register("field_fill_matrix", cadence_s=21600)
    conn.result("field_fill_matrix", _T0, "warn", cells=[_cell("bazos", "warn", "floor 61 %")])
    assert reconcile(conn, now=_T0) == _ZERO
    assert conn.incidents == [] and conn.dispatches == []


def test_a_long_notice_is_clipped_to_the_message_cap() -> None:
    conn = _FakeConn()
    conn.register("field_fill_matrix", cadence_s=21600)
    conn.result("field_fill_matrix", _T0, "fail", message="x" * 5000)
    reconcile(conn, now=_T0)
    assert len(conn.dispatches[0][0]) == MESSAGE_MAX_CHARS
    assert conn.dispatches[0][0].endswith("…")


# --- not measured ----------------------------------------------------------


def test_unknown_neither_opens_closes_nor_ages_an_incident() -> None:
    conn = _FakeConn()
    conn.register("llm_errors")
    conn.register("worker_lane_stall", cadence_s=21600)
    conn.register("acquisition_lag", cadence_s=21600)
    conn.seed("llm_errors", first=_T0 - _h(30), ok_since=_T0 - _h(20))  # its hold long passed
    conn.seed("worker_lane_stall", "detail_drain", first=_T0 - _h(30))  # its 24 h rung is due
    conn.result("llm_errors", _T0, "unknown", message="timed out after its 45 s budget")
    conn.result("worker_lane_stall", _T0, "warn", cells=[_cell("detail_drain", "unknown")])
    conn.result("acquisition_lag", _T0, "unknown", cells=[_cell("sreality", "fail")])
    before = [dict(i) for i in conn.incidents]
    assert reconcile(conn, now=_T0 + _h(1)) == _ZERO
    assert conn.incidents == before
    assert conn.writes() == []


def test_a_stale_fail_neither_opens_nor_reminds() -> None:
    """Past its staleness budget the newest row reads Not measured on Health, so it is inert."""
    conn = _FakeConn()
    conn.register("llm_errors")  # hourly on GitHub: a 19 h budget
    conn.register("property_maintenance")
    inc = conn.seed("property_maintenance", first=_T0 - _h(30))  # its 24 h rung would be due
    conn.result("llm_errors", _T0 - _h(20), "fail")
    conn.result("property_maintenance", _T0 - _h(20), "fail")
    assert reconcile(conn, now=_T0) == _ZERO
    assert conn.open_incidents() == [inc] and conn.writes() == []


def test_a_stale_ok_never_recovers_nor_starts_a_hold() -> None:
    conn = _FakeConn()
    conn.register("llm_errors")  # hourly: a 108-minute hold, a 19 h budget
    conn.register("worker_liveness")
    held = conn.seed("llm_errors", first=_T0 - _h(48), last=_T0 - _h(30),
                     ok_since=_T0 - _h(25))
    unheld = conn.seed("worker_liveness", first=_T0 - _h(48), last=_T0 - _h(30))
    conn.result("llm_errors", _T0 - _h(25), "ok")
    conn.result("llm_errors", _T0 - _h(20), "ok")  # measured across the hold, but stale now
    conn.result("worker_liveness", _T0 - _h(20), "ok")
    assert reconcile(conn, now=_T0) == _ZERO
    assert held["resolved_at"] is None and unheld["ok_since"] is None
    assert conn.writes() == []


@pytest.mark.parametrize(("age", "counts"), [
    (_h(19) - _s(1), True), (_h(19), True), (_h(19) + _s(1), False),
])
def test_the_staleness_boundary_is_health_stale(age: _dt.timedelta, counts: bool) -> None:
    """health_stale is `stamp < now() - budget`: a row one second inside its budget still counts."""
    conn = _FakeConn()
    conn.register("llm_errors", budget_s=19 * 3600)
    conn.result("llm_errors", _T0, "fail")
    assert reconcile(conn, now=_T0 + age)["opened"] == int(counts)


def test_rows_from_before_the_keys_registration_are_not_measured() -> None:
    """The cutover: the old harness stored a check's own error as fail, with no message."""
    conn = _FakeConn()
    conn.register("llm_errors", label="LLM errors", declared_at=_T0)
    conn.result("llm_errors", _T0 - _s(1), "fail")
    assert reconcile(conn, now=_T0 + _h(0.5)) == _ZERO
    assert conn.incidents == []
    conn.result("llm_errors", _T0, "fail", message="openai: 41 credit errors")  # the new harness
    assert reconcile(conn, now=_T0 + _h(0.5)) == {**_ZERO, "opened": 1, "notices": 1}
    assert conn.dispatches[0][0] == "LLM errors: Problem — openai: 41 credit errors"


def test_a_cell_unmeasured_for_seven_days_closes_silently() -> None:
    conn = _FakeConn()
    conn.register("walk_end_streak", cadence_s=21600)
    conn.register("dual_write_parity", cadence_s=21600)
    gone = conn.seed("walk_end_streak", "maxima", first=_T0 - _h(240), last=_T0 - _h(200))
    holding = conn.seed("walk_end_streak", "sreality", first=_T0 - _h(240),
                        last=_T0 - _h(200), ok_since=_T0 - _h(5))
    dark = conn.seed("dual_write_parity", first=_T0 - _h(240), last=_T0 - _h(200))
    # maxima dropped out of the cells; dual_write_parity has timed out ever since its last fail.
    conn.result("walk_end_streak", _T0 - _h(5), "ok", cells=[_cell("sreality", "ok")])
    conn.result("dual_write_parity", _T0 - _h(200), "fail")
    conn.result("dual_write_parity", _T0 - _h(1), "unknown")
    assert reconcile(conn, now=_T0) == {**_ZERO, "closed_silently": 2}
    assert gone["resolve_reason"] == dark["resolve_reason"] == "unmeasured 7d"
    assert holding["resolved_at"] is None  # measured OK inside the week, still in its hold
    assert conn.dispatches == []


def test_rows_from_before_the_keys_registration_never_hold_off_the_silent_close() -> None:
    """Rows the ladder ignores everywhere else must not keep a cell it no longer measures open,
    never told, until they age out of the window."""
    conn = _FakeConn()
    conn.register("dual_write_parity", cadence_s=21600, declared_at=_T0 - _h(24))  # re-declared
    inc = conn.seed("dual_write_parity", first=_T0 - _h(240), last=_T0 - _h(200),
                    ok_since=_T0 - _h(72))
    conn.result("dual_write_parity", _T0 - _h(72), "ok")  # a lone OK of the earlier declaration
    assert reconcile(conn, now=_T0) == {**_ZERO, "closed_silently": 1}
    assert inc["resolve_reason"] == "unmeasured 7d"
    assert conn.dispatches == []


def test_a_result_older_than_the_window_never_reopens_what_it_closed() -> None:
    """The lanes stopped eight days ago: the incident closes once, and the stale fail is inert."""
    conn = _FakeConn()
    conn.register("llm_errors")
    conn.seed("llm_errors", first=_T0 - _h(200), last=_T0 - _h(192))
    conn.result("llm_errors", _T0 - _h(192), "fail")
    assert reconcile(conn, now=_T0) == {**_ZERO, "closed_silently": 1}
    assert reconcile(conn, now=_T0 + _h(0.1)) == _ZERO
    assert conn.open_incidents() == [] and conn.dispatches == []


def test_a_weekly_check_keeps_its_incident_between_two_measurements() -> None:
    conn = _FakeConn()
    conn.register("outbound_url_conformance", cadence_s=604800)
    inc = conn.seed("outbound_url_conformance", "sreality", first=_T0 - _h(178),
                    alerted=_T0 - _h(1))
    conn.result("outbound_url_conformance", _T0 - _h(178), "fail",
                cells=[_cell("sreality", "fail")])
    assert reconcile(conn, now=_T0) == _ZERO
    assert inc["resolved_at"] is None


def test_an_incident_of_a_retired_key_closes_silently() -> None:
    conn = _FakeConn()
    conn.register("llm_errors")
    inc = conn.seed("outbound_url_parity", first=_T0 - _h(48))
    assert reconcile(conn, now=_T0) == {**_ZERO, "closed_silently": 1}
    assert (inc["resolved_at"], inc["resolve_reason"]) == (_T0, "retired")
    assert conn.dispatches == []


# --- keeping and recovering ------------------------------------------------


def test_warn_keeps_an_incident_open_resets_its_hold_and_never_recovers_it() -> None:
    conn = _FakeConn()
    conn.register("false_delist_share", cadence_s=21600, label="False delist share")
    first = _T0 - _h(40)
    inc = conn.seed("false_delist_share", first=first, last=_T0 - _h(13),
                    alerted=_T0 - _h(16), ok_since=_T0 - _h(12))
    conn.result("false_delist_share", _T0 - _h(1), "warn", message="31 % came back")
    assert reconcile(conn, now=_T0) == _ZERO
    assert (inc["ok_since"], inc["last_seen_at"]) == (None, _T0 - _h(1))
    conn.result("false_delist_share", _T0 + _h(39), "warn", message="29 % came back")
    assert reconcile(conn, now=_T0 + _h(40)) == {**_ZERO, "reminded": 1, "notices": 1}
    assert conn.dispatches == [(
        "False delist share: still Watch for 80 h",
        f"sys:false_delist_share:reminder:72h:{_iso(first)}:{inc['id']}x1", [])]
    assert inc["resolved_at"] is None


def test_a_measured_ok_recovers_only_after_the_hold() -> None:
    conn = _FakeConn()
    conn.register("llm_errors", label="LLM errors")  # hourly: a 108-minute hold
    first = _T0 - _h(30)
    inc = conn.seed("llm_errors", first=first, last=_T0 - _h(1), alerted=_T0 - _h(6))
    conn.result("llm_errors", _T0, "ok")
    assert reconcile(conn, now=_T0) == _ZERO
    assert inc["ok_since"] == _T0
    conn.result("llm_errors", _T0 + _HOURLY_HOLD - _s(1), "ok")
    # measured one second short of the hold
    assert reconcile(conn, now=_T0 + _HOURLY_HOLD) == _ZERO
    conn.result("llm_errors", _T0 + _HOURLY_HOLD, "ok")
    assert reconcile(conn, now=_T0 + _HOURLY_HOLD) == {**_ZERO, "recovered": 1, "notices": 1}
    assert (inc["resolved_at"], inc["resolve_reason"]) == (_T0 + _HOURLY_HOLD, "measured ok")
    assert conn.dispatches == [("✓ LLM errors: recovered after 30 h",
                                f"sys:llm_errors:recovery:{_iso(first)}:{inc['id']}x1", [])]


def test_a_lone_ok_followed_by_silence_never_recovers() -> None:
    """The hold is measured, not wall-clock: a second OK must arrive across it."""
    conn = _FakeConn()
    conn.register("llm_errors")  # hourly: a 108-minute hold
    conn.result("llm_errors", _T0, "fail")
    reconcile(conn, now=_T0)
    (inc,) = conn.open_incidents()
    conn.result("llm_errors", _T0 + _h(1), "ok")
    for at in (_T0 + _h(1), _T0 + _h(3), _T0 + _h(18)):
        assert reconcile(conn, now=at)["recovered"] == 0
    assert inc["resolved_at"] is None and inc["ok_since"] == _T0 + _h(1)


def test_a_second_ok_measured_across_the_hold_recovers() -> None:
    conn = _FakeConn()
    conn.register("llm_errors")  # hourly: a 108-minute hold
    conn.result("llm_errors", _T0, "fail")
    reconcile(conn, now=_T0)
    (inc,) = conn.open_incidents()
    conn.result("llm_errors", _T0 + _h(1), "ok")
    assert reconcile(conn, now=_T0 + _h(1))["recovered"] == 0
    conn.result("llm_errors", _T0 + _h(3), "ok")
    assert reconcile(conn, now=_T0 + _h(3))["recovered"] == 1
    assert (inc["resolved_at"], inc["resolve_reason"]) == (_T0 + _h(3), "measured ok")


@pytest.mark.parametrize(("cadence_s", "hold_s"), [
    (300, 1800),                    # two 270 s intervals, so the 30-minute floor
    (3600, 2 * (3600 - 360)),       # the slack is a tenth of the cadence ...
    (21600, 2 * (21600 - 900)),     # ... at most 15 minutes
    (604800, 2 * (604800 - 900)),
])
def test_the_hold_is_two_cadences_less_the_due_slack_and_never_under_thirty_minutes(
    cadence_s: int, hold_s: int,
) -> None:
    conn = _FakeConn()
    conn.register("db_saturation", cadence_s=cadence_s)
    inc = conn.seed("db_saturation", first=_T0 - _h(1))
    conn.result("db_saturation", _T0, "ok")
    reconcile(conn, now=_T0)
    conn.result("db_saturation", _T0 + _s(hold_s) - _s(1), "ok")
    reconcile(conn, now=_T0 + _s(hold_s) - _s(1))
    assert inc["resolved_at"] is None
    conn.result("db_saturation", _T0 + _s(hold_s), "ok")
    reconcile(conn, now=_T0 + _s(hold_s))
    assert inc["resolve_reason"] == "measured ok"


def test_oks_measured_a_due_interval_apart_recover_at_the_third() -> None:
    """A lane measures an hourly check once its row is 54 minutes old, so its OKs can land 59
    minutes apart: a two-cadence hold made those wait for a fourth OK."""
    conn = _FakeConn()
    conn.register("llm_errors")  # hourly
    inc = conn.seed("llm_errors", first=_T0 - _h(5))
    for n in range(3):
        at = _T0 + _dt.timedelta(minutes=59 * n)
        conn.result("llm_errors", at, "ok")
        assert reconcile(conn, now=at)["recovered"] == int(n == 2), n
    assert (inc["resolved_at"], inc["resolve_reason"]) == (
        _T0 + _dt.timedelta(minutes=118), "measured ok")


# --- reminders -------------------------------------------------------------


@pytest.mark.parametrize(("elapsed_h", "rung"), [
    (5.9, None), (6, "6h"), (23.9, "6h"), (24, "24h"), (72, "72h"), (168, "w2"), (340, "w3"),
])
def test_alarm_reminders_climb_6h_24h_72h_then_weekly_and_only_the_highest_fires(
    elapsed_h: float, rung: str | None,
) -> None:
    conn = _FakeConn()
    conn.register("property_maintenance", label="Property maintenance")
    first = _T0 - _h(elapsed_h)
    conn.seed("property_maintenance", first=first)  # told at onset only: every lower rung is due
    conn.result("property_maintenance", _T0, "fail")
    counts = reconcile(conn, now=_T0)
    if rung is None:
        assert counts == _ZERO
        return
    assert counts == {**_ZERO, "reminded": 1, "notices": 1}
    assert conn.dispatches == [(
        f"Property maintenance: still Problem for {elapsed_h:.0f} h",
        f"sys:property_maintenance:reminder:{rung}:{_iso(first)}:1x1", [])]
    assert reconcile(conn, now=_T0 + _dt.timedelta(seconds=60)) == _ZERO  # told once per rung


def test_gauge_reminders_come_once_a_day() -> None:
    conn = _FakeConn()
    conn.register("location_town_coverage", cls="gauge", cadence_s=21600,
                  label="Location coverage")
    first = _T0 - _h(23)
    conn.seed("location_town_coverage", first=first)
    for at in (_T0, first + _h(24), first + _h(30), first + _h(49), first + _h(121)):
        conn.result("location_town_coverage", at, "fail")
        reconcile(conn, now=at)
    assert [d[1] for d in conn.dispatches] == [
        f"sys:location_town_coverage:reminder:{r}:{_iso(first)}:1x1" for r in ("d1", "d2", "d5")]
    assert conn.dispatches[0][0] == "Location coverage: still Problem for 24 h"


def test_an_acknowledged_incident_gets_no_reminder_but_its_recovery_is_still_told() -> None:
    conn = _FakeConn()
    conn.register("llm_burn_rate", label="LLM spend")
    conn.seed("llm_burn_rate", first=_T0 - _h(30), acked=_T0 - _h(29))
    conn.result("llm_burn_rate", _T0, "fail")
    assert reconcile(conn, now=_T0) == _ZERO
    conn.result("llm_burn_rate", _T0 + _h(1), "ok")
    reconcile(conn, now=_T0 + _h(1))
    conn.result("llm_burn_rate", _T0 + _h(3), "ok")
    assert reconcile(conn, now=_T0 + _h(3)) == {**_ZERO, "recovered": 1, "notices": 1}
    assert conn.dispatches[0][0] == "✓ LLM spend: recovered after 31 h"


def test_reminders_and_recoveries_group_a_checks_cells_into_one_notice() -> None:
    conn = _FakeConn()
    conn.register("walk_end_streak", cadence_s=21600, label="Walks not reaching the end")
    first = _T0 - _h(30)
    sreality = conn.seed("walk_end_streak", "sreality", first=first)
    bazos = conn.seed("walk_end_streak", "bazos", first=_T0 - _h(8))
    conn.result("walk_end_streak", _T0, "fail",
                cells=[_cell("sreality", "fail"), _cell("bazos", "warn")])
    assert reconcile(conn, now=_T0) == {**_ZERO, "reminded": 2, "notices": 1}
    assert conn.dispatches == [(
        "Walks not reaching the end: still Problem for 30 h — bazos, sreality",
        f"sys:walk_end_streak:reminder:24h:{_iso(first)}:{sreality['id']}x2", [])]
    assert sreality["alert_count"] == bazos["alert_count"] == 2
    for hours in (1, 13):  # the six-hourly hold is 11.5 hours
        conn.result("walk_end_streak", _T0 + _h(hours), "ok",
                    cells=[_cell("sreality", "ok"), _cell("bazos", "ok")])
        reconcile(conn, now=_T0 + _h(hours))
    assert {sreality["resolve_reason"], bazos["resolve_reason"]} == {"measured ok"}
    assert conn.dispatches[-1] == (
        "✓ Walks not reaching the end: bazos, sreality recovered after 31 h",
        f"sys:walk_end_streak:recovery:{_iso(first)}:{sreality['id']}x2", [])


def test_cells_of_one_check_that_recover_in_different_passes_are_each_told() -> None:
    conn = _FakeConn()
    conn.register("walk_end_streak", cadence_s=21600, label="Walks")  # an 11.5-hour hold
    for hours, sreality, bazos in ((0, "fail", "fail"), (1, "ok", "fail"), (13, "ok", "ok"),
                                   (25, "ok", "ok")):
        worst = "fail" if "fail" in (sreality, bazos) else "ok"
        conn.result("walk_end_streak", _T0 + _h(hours), worst,
                    cells=[_cell("sreality", sreality), _cell("bazos", bazos)])
        reconcile(conn, now=_T0 + _h(hours))
    recoveries = [d[:2] for d in conn.dispatches if ":recovery:" in d[1]]
    assert recoveries == [
        ("✓ Walks: sreality recovered after 1 h", f"sys:walk_end_streak:recovery:{_iso(_T0)}:1x1"),
        ("✓ Walks: bazos recovered after 13 h", f"sys:walk_end_streak:recovery:{_iso(_T0)}:2x1"),
    ]
    assert conn.open_incidents() == []


def test_cells_of_one_check_reminded_in_different_passes_are_each_told() -> None:
    conn = _FakeConn()
    conn.register("walk_end_streak", cadence_s=21600, label="Walks")
    for hours, bazos in ((0, "fail"), (5, "ok"), (6, "ok"), (7, "fail")):
        conn.result("walk_end_streak", _T0 + _h(hours), "fail",
                    cells=[_cell("sreality", "fail"), _cell("bazos", bazos)])
        reconcile(conn, now=_T0 + _h(hours))
    # bazos sat in its hold at the 6 h rung, so its own 6 h reminder comes an hour later.
    assert [d[1] for d in conn.dispatches if ":reminder:" in d[1]] == [
        f"sys:walk_end_streak:reminder:6h:{_iso(_T0)}:1x1",
        f"sys:walk_end_streak:reminder:6h:{_iso(_T0)}:2x1",
    ]
    assert [d[0] for d in conn.dispatches[1:]] == [
        "Walks: still Problem for 6 h — sreality", "Walks: still Problem for 7 h — bazos"]


# --- the pass --------------------------------------------------------------


def test_a_second_pass_over_the_same_state_writes_and_tells_nothing() -> None:
    conn = _FakeConn()
    for key in ("llm_errors", "llm_burn_rate", "worker_liveness", "db_saturation"):
        conn.register(key)
    conn.result("llm_errors", _T0, "fail")
    conn.seed("llm_burn_rate", first=_T0 - _h(7))
    conn.result("llm_burn_rate", _T0, "fail")
    conn.seed("worker_liveness", first=_T0 - _h(5), ok_since=_T0 - _h(3))
    conn.result("worker_liveness", _T0 - _h(3), "ok")
    conn.result("worker_liveness", _T0 - _h(1), "ok")
    conn.seed("db_saturation", "cron", first=_T0 - _h(400), last=_T0 - _h(200))
    conn.seed("outbound_url_parity", first=_T0 - _h(9))
    assert reconcile(conn, now=_T0) == {
        "opened": 1, "reminded": 1, "recovered": 1, "closed_silently": 2, "notices": 3}
    assert [d[1] for d in conn.dispatches] == [
        "sys:llm_errors:onset:2026-10-18T06:00:00Z",
        "sys:llm_burn_rate:reminder:6h:2026-10-17T23:00:00Z:1x1",
        "sys:worker_liveness:recovery:2026-10-18T01:00:00Z:2x1",
    ]
    writes = len(conn.writes())
    assert reconcile(conn, now=_T0 + _dt.timedelta(seconds=60)) == _ZERO
    assert len(conn.writes()) == writes


def test_a_pass_that_loses_the_lock_returns_zeros_and_writes_nothing() -> None:
    conn = _FakeConn(lock_free=False)
    conn.register("llm_errors")
    conn.result("llm_errors", _T0, "fail")
    assert reconcile(conn, now=_T0) == _ZERO
    assert [s for s, _ in conn.executed] == [
        "SELECT pg_try_advisory_xact_lock(hashtext('health_reconcile'))"]
    assert conn.transactions == 1


def test_the_sql_the_fake_cannot_enforce_is_pinned() -> None:
    """The fake has no partial unique index, no lock and no LIKE: pin the statements that do."""
    conn = _FakeConn()
    conn.register("llm_errors")
    conn.result("llm_errors", _T0, "fail")
    reconcile(conn, now=_T0)
    norms = [(" ".join(s.split()), p) for s, p in conn.executed]
    assert conn.transactions == 1 and "pg_try_advisory_xact_lock" in norms[0][0]
    assert any("ON CONFLICT (signature) WHERE resolved_at IS NULL DO NOTHING RETURNING id" in s
               for s, _ in norms)
    newest = next(s for s, _ in norms if "FROM health_checks" in s)
    assert "WHERE c.check_key = hc.key AND c.run_at >= hc.declared_at" in newest
    assert "ORDER BY c.run_at DESC LIMIT 1" in newest
    assert "extract(epoch FROM hc.staleness_budget)::float8" in newest
    read, params = next((s, p) for s, p in norms if "FROM ops_incidents" in s)
    assert "WHERE resolved_at IS NULL AND signature LIKE" in read
    assert params == {"cells": "cell:%"}


# --- emit_system_alert -----------------------------------------------------


def test_emit_inserts_one_system_health_row_and_a_repeat_is_a_no_op() -> None:
    conn = _FakeConn()
    assert emit_system_alert(conn, "llm_errors", "down", dedupe_key="sys:llm_errors:onset:x")
    assert not emit_system_alert(conn, "llm_errors", "down", dedupe_key="sys:llm_errors:onset:x")
    sql = next(s for s, _ in conn.executed if "INSERT INTO notification_dispatches" in s)
    assert "'system_health'" in sql and "'system_alert'" in sql and "'in_app'" in sql
    assert "ON CONFLICT (dedupe_key) DO NOTHING" in sql
    assert conn.dispatches == [("down", "sys:llm_errors:onset:x", [])]


def test_emit_requires_a_dedupe_key() -> None:
    with pytest.raises(TypeError):
        emit_system_alert(_FakeConn(), "llm_errors", "down")  # type: ignore[call-arg]


@pytest.mark.parametrize(("check_key", "dedupe_key"), [
    ("llm_errors", "sys:llm_burn_rate:onset:x"),
    ("llm", "sys:llm_errors:onset:x"),  # a key's prefix is not the key
    ("llm_errors", "llm_errors:onset:x"),
])
def test_emit_refuses_a_dedupe_key_outside_its_checks_namespace(
    check_key: str, dedupe_key: str,
) -> None:
    conn = _FakeConn()
    with pytest.raises(ValueError):
        emit_system_alert(conn, check_key, "down", dedupe_key=dedupe_key)
    assert conn.executed == [] and conn.dispatches == []


@pytest.mark.parametrize(("stored", "channels"), [
    (["email", "telegram"], ["email", "telegram"]),
    (_MISSING, []),
    (json.dumps(["telegram"]), ["telegram"]),
    ({"not": "a list"}, []),
    (["email", 42, None, "telegram"], ["email", "telegram"]),
])
def test_emit_stamps_the_system_health_channels(stored: Any, channels: list[str]) -> None:
    conn = _FakeConn(channels=stored)
    emit_system_alert(conn, "llm_errors", "down", dedupe_key="sys:llm_errors:onset:x")
    assert conn.dispatches[0][2] == channels


def test_a_cells_list_without_verdicts_is_not_a_cell_list() -> None:
    """A legacy per-portal table stored under `cells` (rows with no status) must not mute the
    check: the check measured, so its own status is the one implicit cell."""
    from toolkit.system_alerts import _cells

    legacy = {"cells": [{"source": "bazos", "hidden": 7294}], "message": "13,239 ads lack a town"}
    cells = _cells("location_town_coverage", "fail", 13239, legacy, "ads")
    assert list(cells) == ["location_town_coverage"]
    assert cells["location_town_coverage"]["status"] == "fail"
    assert cells["location_town_coverage"]["message"] == "13,239 ads lack a town"

    real = {"cells": [{"subject": "bazos", "status": "warn"}, {"subject": "idnes", "status": "fail"}]}
    assert sorted(_cells("k", "fail", None, real, "")) == ["bazos", "idnes"]
