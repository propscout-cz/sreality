"""Tests for api.skills — DB-backed skill loader + updater + validation."""

from __future__ import annotations

import json

import pytest

from api import skills as sk
from api.agents.definition import DefinitionError
from tests.api._fakes import _FakeConn, make_skill_row


def test_load_skill_returns_typed_dataclass():
    row = make_skill_row(name="rental_estimator_v1")
    conn = _FakeConn(skills={"rental_estimator_v1": row})
    s = sk.load_skill(conn, "rental_estimator_v1")
    assert s.name == "rental_estimator_v1"
    assert s.allowed_tools == [
        "find_comparables_relaxed", "analyze_distribution", "record_estimate",
    ]
    assert s.preferred_model["anthropic"] == "claude-sonnet-4-5"
    assert s.preferred_model["gemini"] == "gemini-2.5-pro"
    assert s.limits.max_iterations == 12
    assert s.limits.max_cost_usd == pytest.approx(1.0)


def test_load_skill_raises_when_missing():
    conn = _FakeConn(skills={})
    with pytest.raises(sk.SkillNotFound):
        sk.load_skill(conn, "nope")


def test_update_skill_checks_tools_against_the_estimators_own_list():
    """Fail-closed since #2065: the check no longer depends on api.main having filled a global first."""
    conn = _UpdateCapturingConn(make_skill_row())
    with pytest.raises(DefinitionError, match="unknown tool 'boguscallout'"):
        sk.update_skill(conn, "rental_estimator_v1", {"allowed_tools": ["find_comparables_relaxed", "boguscallout"]})
    with pytest.raises(DefinitionError, match="no price"):
        sk.update_skill(conn, "rental_estimator_v1", {"preferred_model": {"anthropic": "claude-unpriced-9"}})
    assert conn.updates == []


class _UpdateCapturingConn:
    """Records the UPDATE update_skill issues, then serves the row back."""

    def __init__(self, row: tuple) -> None:
        self.row = row
        self.updates: list[dict] = []
        self._last: tuple | None = None

    def cursor(self) -> "_UpdateCapturingConn":
        return self

    def transaction(self) -> "_UpdateCapturingConn":
        return self

    def __enter__(self) -> "_UpdateCapturingConn":
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def execute(self, sql: str, params: object = ()) -> None:
        self._last = self.row
        if sql.lstrip().lower().startswith("update skills"):
            self.updates.append(dict(params))
            self._last = (self.row[0],)

    def fetchone(self) -> tuple | None:
        return self._last


def test_update_skill_saves_the_settings_payload_against_the_full_registry():
    """The Settings page resends the row's own two-provider map on every save."""
    row = make_skill_row(name="rental_estimator_v1")
    current = sk.load_skill(_FakeConn(skills={"rental_estimator_v1": row}), "rental_estimator_v1")
    conn = _UpdateCapturingConn(row)
    sk.update_skill(conn, "rental_estimator_v1", {
        "system_prompt": "you are a fox",
        "allowed_tools": current.allowed_tools,
        "preferred_model": current.preferred_model,
        "limits": {
            "max_iterations": current.limits.max_iterations,
            "max_cost_usd": current.limits.max_cost_usd,
            "wall_clock_timeout_s": current.limits.wall_clock_timeout_s,
        },
    })
    assert len(conn.updates) == 1
    assert json.loads(conn.updates[0]["preferred_model"]) == {
        "anthropic": "claude-sonnet-4-5",
        "gemini": "gemini-2.5-pro",
    }
    assert json.loads(conn.updates[0]["limits"]) == {
        "max_iterations": 12, "max_cost_usd": 1.0, "wall_clock_timeout_s": 120.0,
    }


def test_jsonb_dumps_round_trips_via_json():
    out = sk._jsonb_dumps([1, 2, 3])
    assert json.loads(out) == [1, 2, 3]
