"""Tests for `PUT /stats/dashboard` — the Browse > Stats board, stored whole (migration 626).

Hermetic: the tenant connection is a scripted fake that records every statement, the JWT is
overridden, and the account resolver returns a distinctive sentinel (never None — tenancy.md's
#917 rule), so the tests can assert the sentinel ARRIVES at the upsert. Validation runs
unmodified through the route: every chart passes `toolkit.stats_registry.validate_chart`, and
a refusal is a 422 that names the chart and the registry's reason without touching the table.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import pytest

fastapi = pytest.importorskip("fastapi")
TestClient = pytest.importorskip("fastapi.testclient").TestClient

from fastapi.routing import APIRoute  # noqa: E402

from api import dependencies as deps  # noqa: E402
from api import main as api_main  # noqa: E402
from api import stats_dashboard as sd  # noqa: E402
from api import tenant_pool  # noqa: E402
from toolkit import stats_registry as sr  # noqa: E402

_SUB = "11111111-1111-1111-1111-111111111111"
_ACCT = "44444444-4444-4444-4444-444444444444"
_AT = datetime(2026, 10, 19, 8, 0, tzinfo=timezone.utc)

_CHART = {"id": "c1", "view": "box", "rows": ["disposition", "condition"], "cols": [],
          "measure": "ppm2"}


class _Cursor:
    def __init__(self, conn: "_Conn") -> None:
        self.conn = conn

    def __enter__(self) -> "_Cursor":
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def execute(self, sql: str, params: Any = None) -> None:
        self.conn.log.append((" ".join(sql.split()), params))
        account_id, charts = params
        self.conn.rows[str(account_id)] = json.loads(charts)

    def fetchone(self) -> tuple:
        account_id = self.conn.log[-1][1][0]
        return (self.conn.rows[str(account_id)], _AT)


class _Conn:
    def __init__(self) -> None:
        self.log: list[tuple[str, Any]] = []
        self.rows: dict[str, Any] = {}

    def cursor(self) -> _Cursor:
        return _Cursor(self)


@pytest.fixture()
def conn() -> _Conn:
    return _Conn()


@pytest.fixture()
def client(monkeypatch, conn: _Conn):
    api_main.app.dependency_overrides[tenant_pool.tenant_conn] = lambda: conn
    api_main.app.dependency_overrides[deps.verify_jwt] = lambda: {"sub": _SUB}
    monkeypatch.setattr(tenant_pool, "resolve_account_id", lambda c, claims: _ACCT)
    yield TestClient(api_main.app)
    api_main.app.dependency_overrides.clear()


def test_the_board_is_upserted_whole_for_the_callers_account(client, conn: _Conn) -> None:
    charts = [
        {**_CHART, "title": "derived, never stored"},
        {"id": "c2", "view": "line", "rows": ["area_band_10"], "cols": ["condition"],
         "measure": "price", "stat": "p75"},
    ]
    res = client.put("/stats/dashboard", json={"charts": charts})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body == {
        "charts": [
            {"id": "c1", "view": "box", "stat": "median", "rows": ["disposition", "condition"],
             "cols": [], "measure": "ppm2"},
            {"id": "c2", "view": "line", "stat": "p75", "rows": ["area_band_10"],
             "cols": ["condition"], "measure": "price"},
        ],
        "updated_at": _AT.isoformat(),
    }
    [(sql, params)] = conn.log
    assert sql.startswith("INSERT INTO stats_dashboards (account_id, charts, updated_at)")
    assert "ON CONFLICT (account_id) DO UPDATE SET charts = EXCLUDED.charts" in sql
    assert "RETURNING charts, updated_at" in sql
    assert str(params[0]) == _ACCT
    assert json.loads(params[1]) == body["charts"]


def test_an_empty_board_is_a_valid_board(client, conn: _Conn) -> None:
    res = client.put("/stats/dashboard", json={"charts": []})
    assert res.status_code == 200
    assert res.json()["charts"] == []
    assert conn.rows == {_ACCT: []}


@pytest.mark.parametrize(
    "charts, detail",
    [
        ([{**_CHART, "cols": ["kraj"]}], "chart 0: a box chart takes row dimensions only"),
        ([_CHART, {**_CHART, "id": "c2", "view": "pie"}], "chart 1: unknown view 'pie'"),
        ([{**_CHART, "rows": ["disposition"] * 2}], "chart 0: a dimension may appear once"),
        ([{**_CHART, "rows": ["kraj", "okres", "obec", "cast_obce", "ulice", "condition", "disposition"]}],
         "chart 0: at most 6 row dimensions"),
        ([{**_CHART, "measure": "yield"}], "chart 0: unknown measure 'yield'"),
        ([{**_CHART, "stat": "n"}], "chart 0: unknown stat 'n'"),
        ([_CHART, dict(_CHART)], "chart 1: duplicate id 'c1'"),
        ([{**_CHART, "id": ""}], "chart 0: chart id must be a short string"),
        ([_CHART, {**_CHART, "id": "c2", "view": "line", "rows": ["kraj", "obec"]}],
         "chart 1: a line chart takes exactly 1 row dimension"),
        ([{**_CHART, "view": "bar", "rows": []}], "chart 0: a bar chart takes exactly 1 row dimension"),
        ([{**_CHART, "rows": [["kraj"]]}], "chart 0: dimensions must be strings"),
        ([{**_CHART, "rows": [{"a": 1}]}], "chart 0: dimensions must be strings"),
        ([{**_CHART, "measure": ["ppm2"]}], "chart 0: unknown measure ['ppm2']"),
        ([{**_CHART, "id": f"c{i}"} for i in range(sr.MAX_CHARTS + 1)],
         f"at most {sr.MAX_CHARTS} charts on a board"),
    ],
)
def test_a_chart_the_registry_refuses_is_a_422_and_writes_nothing(
    client, conn: _Conn, charts: list, detail: str,
) -> None:
    res = client.put("/stats/dashboard", json={"charts": charts})
    assert res.status_code == 422
    assert res.json()["detail"] == detail
    assert conn.log == []


def test_the_body_shape_is_pydantics(client, conn: _Conn) -> None:
    assert client.put("/stats/dashboard", json={}).status_code == 422
    assert client.put("/stats/dashboard", json={"charts": ["c1"]}).status_code == 422
    assert conn.log == []


def test_a_full_board_is_accepted(client, conn: _Conn) -> None:
    full = [{**_CHART, "id": f"c{i}"} for i in range(sr.MAX_CHARTS)]
    res = client.put("/stats/dashboard", json={"charts": full})
    assert res.status_code == 200, res.text
    assert len(res.json()["charts"]) == sr.MAX_CHARTS


def test_a_caller_without_an_account_is_400_and_writes_nothing(client, conn, monkeypatch) -> None:
    monkeypatch.setattr(tenant_pool, "resolve_account_id", lambda c, claims: None)
    res = client.put("/stats/dashboard", json={"charts": [_CHART]})
    assert res.status_code == 400
    assert res.json()["detail"] == "no account for caller"
    assert conn.log == []


def test_the_route_rides_the_tenant_connection_and_names_its_owner() -> None:
    [route] = [r for r in api_main.app.routes
               if isinstance(r, APIRoute) and r.path == "/stats/dashboard"]
    assert route.methods == {"PUT"}
    calls, stack = set(), list(route.dependant.dependencies)
    while stack:
        d = stack.pop()
        if d.call is not None:
            calls.add(d.call)
        stack.extend(d.dependencies)
    assert tenant_pool.tenant_conn in calls and tenant_pool.require_account_id in calls
    assert deps.get_db_conn not in calls and deps.require_token not in calls


def test_save_requires_its_account() -> None:
    """No default: a dropped account is a TypeError, not a NULL owner (#917)."""
    with pytest.raises(TypeError):
        sd.save_dashboard(_Conn(), [])  # type: ignore[call-arg]
