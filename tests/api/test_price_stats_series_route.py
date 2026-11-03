"""The price-stats series route is keyed by RÚIAN town code (ruling Q10d, #2362):
/price-stats/datasets/{dataset_id}/cities/{obec_id}/series, no sreality entity in the path."""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from api import dependencies as deps
from api import main as api_main
from tests.api._routes import flat_routes

ROUTE = "/price-stats/datasets/{dataset_id}/cities/{obec_id}/series"


class _Cur:
    def __init__(self, conn: "_Conn") -> None:
        self._conn = conn

    def __enter__(self) -> "_Cur":
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        self._conn.executed.append((" ".join(sql.split()), params))

    def fetchall(self) -> list[dict[str, Any]]:
        return [{"category_type_cb": 1, "year": 2026, "month": 5, "price": 90_000, "active_count": 3,
                 "new_count": 1, "deleted_count": 0}]

    def fetchone(self) -> tuple[None]:
        return (None,)


class _Conn:
    def __init__(self) -> None:
        self.executed: list[tuple[str, tuple[Any, ...]]] = []

    def cursor(self, **_kw: Any) -> _Cur:
        return _Cur(self)


def test_the_series_route_takes_a_town_code_and_reads_by_it() -> None:
    conn = _Conn()
    api_main.app.dependency_overrides[deps.get_db_conn] = lambda: conn
    api_main.app.dependency_overrides[deps.require_token] = lambda: None
    try:
        res = TestClient(api_main.app).get("/price-stats/datasets/9/cities/554782/series")
    finally:
        api_main.app.dependency_overrides.clear()
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["metadata"]["filters_used"] == {"dataset_id": 9, "obec_id": 554782}
    assert len(body["data"]["sale"]) == 1 and body["data"]["rent"] == []
    series_sql, params = conn.executed[0]
    assert "WHERE dataset_id = %s AND obec_id = %s" in series_sql and params == (9, 554782)


def test_no_price_stats_route_names_a_sreality_entity() -> None:
    paths = {r.path for r in flat_routes(api_main.app.routes)}
    assert ROUTE in paths
    assert not [p for p in paths if p.startswith("/price-stats") and "entity" in p]
