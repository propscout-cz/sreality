"""The price-stats series route is keyed by RÚIAN town code (ruling Q10d, #2362); its SQL by obec_id runs in
tests/scraper/test_price_stats_translation_live.py."""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from api import dependencies as deps
from api import main as api_main
from tests.api._routes import flat_routes


def test_the_series_route_takes_a_town_code(monkeypatch: Any) -> None:
    calls: list[tuple[int, int]] = []
    monkeypatch.setattr(api_main.price_stats_module.analysis, "dataset_city_series",
                        lambda _conn, dataset_id, obec_id: calls.append((dataset_id, obec_id)) or {})
    api_main.app.dependency_overrides[deps.get_db_conn] = lambda: None
    api_main.app.dependency_overrides[deps.require_token] = lambda: None
    try:
        res = TestClient(api_main.app).get("/price-stats/datasets/9/cities/554782/series")
    finally:
        api_main.app.dependency_overrides.clear()
    assert (res.status_code, calls) == (200, [(9, 554782)]), res.text
    paths = {r.path for r in flat_routes(api_main.app.routes) if r.path.startswith("/price-stats")}
    assert not [p for p in paths if "entity" in p], "no price-stats route names a sreality entity"
