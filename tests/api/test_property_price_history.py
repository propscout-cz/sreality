"""GET /properties/{id}/price-history: the SPA property page's two reads (every ad, every ad's
price snapshots) behind require_token on the service role. Hermetic: the DB is a fake cursor."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("fastapi")
TestClient = pytest.importorskip("fastapi.testclient").TestClient

from api import dependencies as deps
from api import main as api_main
from api.routes import property_price_history as ph
from tests.api._jwt import mint
from tests.api.test_admin_route_coverage import _bucket, _collect

_QUERIES = Path(__file__).resolve().parents[2] / "frontend/src/lib/queries.ts"
AUTH = {"Authorization": f"Bearer {mint({'sub': 'u1'})}"}
_TS = datetime(2026, 10, 1, 8, 0)
AD = {
    "id": 11, "property_id": 7, "source": "sreality", "source_url": "https://www.sreality.cz/x",
    "source_id_native": "1", "ad_reference": None, "is_active": True, "price_czk": 5_000_000,
    "first_seen_at": _TS, "last_seen_at": _TS,
}
SNAPSHOT = {"id": 3, "listing_id": 11, "scraped_at": _TS, "price_czk": 5_000_000}


class _Cursor:
    def __init__(self, conn: _Conn) -> None:
        self.conn = conn

    def __enter__(self) -> _Cursor:
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def execute(self, sql: str, params: tuple) -> None:
        self.conn.statements.append((sql, params))

    def fetchall(self) -> list[dict[str, Any]]:
        return self.conn.answers.pop(0)


class _Conn:
    def __init__(self, *answers: list[dict[str, Any]]) -> None:
        self.answers = list(answers)
        self.statements: list[tuple[str, tuple]] = []

    def cursor(self, row_factory: Any = None) -> _Cursor:
        return _Cursor(self)


@pytest.fixture()
def serve():
    def _serve(*answers: list[dict[str, Any]]) -> _Conn:
        conn = _Conn(*answers)
        api_main.app.dependency_overrides[deps.get_db_conn] = lambda: conn
        return conn

    yield _serve
    api_main.app.dependency_overrides.clear()


def _get() -> Any:
    return TestClient(api_main.app).get("/properties/7/price-history", headers=AUTH)


def test_serves_every_ad_and_their_snapshots_whole_in_order(serve) -> None:
    conn = serve([AD, {**AD, "id": 12, "is_active": False}], [SNAPSHOT])
    res = _get()
    assert res.status_code == 200
    data = res.json()["data"]
    assert [a["id"] for a in data["sources"]] == [11, 12]
    assert data["snapshots"] == [{**SNAPSHOT, "scraped_at": _TS.isoformat()}]
    (ads_sql, ads_args), (snap_sql, snap_args) = conn.statements
    assert ads_args == (7,) and snap_args == ([11, 12],)
    assert ads_sql.endswith("order by first_seen_at, id")
    assert snap_sql.endswith("order by scraped_at, id")
    assert "limit" not in (ads_sql + snap_sql).lower()


def test_an_unknown_property_is_404_and_reads_no_snapshot(serve) -> None:
    conn = serve([])
    res = _get()
    assert res.status_code == 404
    assert res.json()["detail"]["reason"] == "not_found"
    assert len(conn.statements) == 1


def test_reads_the_spas_columns_from_the_public_views_only() -> None:
    """The extension types the response as the SPA's PropertySource / ListingSnapshotPublic."""
    spa = _QUERIES.read_text(encoding="utf-8")
    ad_cols = re.search(r"PROPERTY_SOURCE_COLS =\s*'([^']+)'", spa).group(1)
    snap_cols = re.search(r"from\('listing_snapshots_public'\)\s*\.select\('([^']+)'", spa).group(1)
    for sql, cols, view in ((ph._SOURCES_SQL, ad_cols, "property_sources_public"),
                            (ph._SNAPSHOTS_SQL, snap_cols, "listing_snapshots_public")):
        head = re.match(r"select (.+?) from (\w+) ", sql)
        assert head.group(1).replace(" ", "") == cols
        assert head.group(2) == view


def test_requires_a_token_and_never_the_tenant_connection(serve, monkeypatch) -> None:
    serve()
    monkeypatch.delenv("API_AUTH_OPTIONAL", raising=False)  # tests/conftest opens it for dev
    assert TestClient(api_main.app).get("/properties/7/price-history").status_code == 401
    (route,) = [r for r in _collect(api_main.app.routes) if r.path.endswith("/price-history")]
    assert _bucket(route) == "token"  # require_token; no tenant_conn, no verify_jwt
