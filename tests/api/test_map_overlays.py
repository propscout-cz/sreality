"""Tests for the map reference-overlay routes: GET /map-overlays (the layer registry) and
GET /map-overlays/{section}/{z}/{x}/{y}.mvt (one stored vector tile, read by primary key).
Hermetic: the DB is a fake cursor answering from scripted rows."""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

import pytest

fastapi = pytest.importorskip("fastapi")
TestClient = pytest.importorskip("fastapi.testclient").TestClient

from api import dependencies as deps
from api import main as api_main
from api.routes import map_overlays as mo
from tests.api._jwt import mint

AUTH = {"Authorization": f"Bearer {mint({'sub': 'u1'})}"}
FETCHED = datetime(2026, 10, 14, 4, 0, tzinfo=timezone.utc)
MVT = b"\x1a\x0b\x0a\x05metro\x78\x02\x28\x80\x20"
TILE = "/map-overlays/metro/10/553/346.mvt"
YEAR_IMMUTABLE = "private, max-age=31536000, immutable"


def _section(key: str, sort: int, **over: Any) -> dict[str, Any]:
    row = {
        "key": key,
        "label": key.capitalize(),
        "sort": sort,
        "minzoom": 9,
        "maxzoom": 12,
        "attribution": "datový podklad © IPR Praha",
        "licence": "CC BY",
        "source_url": "https://mp.iprpraha.cz/arcgis/rest/services/Hosted/",
        "note": "Metropolitní plán hl. m. Prahy",
        "source_revised_at": date(2026, 9, 1),
        "fetched_at": FETCHED,
        "version": 1760414400,
    }
    row.update(over)
    return row


def _layer(section: str, key: str, sort: int, **over: Any) -> dict[str, Any]:
    row = {
        "section": section,
        "key": key,
        "label": key,
        "sort": sort,
        "legend": [{"class": key, "label": key, "color": "#1D4F91"}],
        "scale": None,
        "variants": None,
        "feature_count": 10,
    }
    row.update(over)
    return row


class _Cursor:
    def __init__(self, conn: _Conn) -> None:
        self.conn = conn
        self.rows: list[dict[str, Any]] = []

    def __enter__(self) -> _Cursor:
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def execute(self, sql: str, params: Any = None) -> None:
        text = " ".join(sql.split())
        self.conn.statements.append((text, params))
        if "map_overlay_tiles" in text:
            self.rows = [] if self.conn.tile is None else [self.conn.tile]
        elif "from map_overlay_layers" in text:
            self.rows = list(self.conn.layers)
        elif "from map_overlay_sections" in text:
            self.rows = list(self.conn.sections)
        else:
            raise AssertionError(f"unexpected statement: {text}")

    def fetchone(self) -> dict[str, Any] | None:
        return self.rows[0] if self.rows else None

    def fetchall(self) -> list[dict[str, Any]]:
        return list(self.rows)


class _Conn:
    def __init__(self) -> None:
        self.sections: list[dict[str, Any]] = []
        self.layers: list[dict[str, Any]] = []
        self.tile: dict[str, Any] | None = {
            "minzoom": 9, "maxzoom": 12, "version": 1760414400, "mvt": MVT,
        }
        self.statements: list[tuple[str, Any]] = []

    def cursor(self, row_factory: Any = None) -> _Cursor:
        return _Cursor(self)


@pytest.fixture()
def conn():
    c = _Conn()
    api_main.app.dependency_overrides[deps.get_db_conn] = lambda: c
    yield c
    api_main.app.dependency_overrides.clear()


@pytest.fixture()
def client():
    return TestClient(api_main.app)


# --- GET /map-overlays -----------------------------------------------------------------


def test_registry_entry_has_exactly_the_spec_shape(client, conn):
    scale = {"prop": "vk1", "stops": [[100, "#fff"], [400, "#000"]], "null_color": "#ccc", "unit": "rent_per_m2"}
    variants = [{"key": "vk1", "label": "1+kk, 1+1", "prop": "vk1"}]
    legend = [{"class": "D", "label": "Metro D – návrh", "color": "#1F77D0", "dash": True}]
    conn.sections = [_section("metro", 1)]
    conn.layers = [_layer("metro", "metro", 1, label="Metro", legend=legend, scale=scale, variants=variants)]
    res = client.get("/map-overlays", headers=AUTH)
    assert res.status_code == 200
    (section,) = res.json()["sections"]
    assert datetime.fromisoformat(section.pop("fetched_at")) == FETCHED
    assert section == {
        "key": "metro",
        "label": "Metro",
        "sort": 1,
        "minzoom": 9,
        "maxzoom": 12,
        "attribution": "datový podklad © IPR Praha",
        "licence": "CC BY",
        "source_url": "https://mp.iprpraha.cz/arcgis/rest/services/Hosted/",
        "note": "Metropolitní plán hl. m. Prahy",
        "source_revised_at": "2026-09-01",
        "version": 1760414400,
        "layers": [{
            "key": "metro",
            "label": "Metro",
            "sort": 1,
            "legend": legend,
            "scale": scale,
            "variants": variants,
            "feature_count": 10,
        }],
    }


def test_registry_orders_sections_and_their_layers_by_sort(client, conn):
    conn.sections = [
        _section("kraje", 3, minzoom=5, maxzoom=9),
        _section("metro", 1),
        _section("flood", 2, minzoom=10),
        _section("alpha", 3),
    ]
    conn.layers = [
        _layer("flood", "flood_q500", 4),
        _layer("kraje", "kraje", 1),
        _layer("flood", "flood_q5", 1),
        _layer("metro", "metro", 1),
        _layer("flood", "flood_az", 5),
        _layer("flood", "flood_q100", 3),
        _layer("flood", "flood_q20", 2),
    ]
    sections = client.get("/map-overlays", headers=AUTH).json()["sections"]
    assert [s["key"] for s in sections] == ["metro", "flood", "alpha", "kraje"]
    layers = {s["key"]: [layer["key"] for layer in s["layers"]] for s in sections}
    assert layers == {
        "metro": ["metro"],
        "flood": ["flood_q5", "flood_q20", "flood_q100", "flood_q500", "flood_az"],
        "alpha": [],
        "kraje": ["kraje"],
    }


def test_a_section_never_ingested_is_not_offered(client, conn):
    conn.sections = [_section("metro", 2), _section("rent_map", 1, fetched_at=None)]
    conn.layers = [_layer("rent_map", "rent_map", 1), _layer("metro", "metro", 1)]
    sections = client.get("/map-overlays", headers=AUTH).json()["sections"]
    assert [s["key"] for s in sections] == ["metro"]
    assert [layer["key"] for layer in sections[0]["layers"]] == ["metro"]


def test_an_empty_store_offers_nothing(client, conn):
    assert client.get("/map-overlays", headers=AUTH).json() == {"sections": []}


# --- GET /map-overlays/{section}/{z}/{x}/{y}.mvt ---------------------------------------


def test_tile_serves_the_stored_bytes_cached_privately_for_a_year(client, conn):
    res = client.get(TILE, params={"v": 1760414400}, headers=AUTH)
    assert res.status_code == 200
    assert res.content == MVT
    assert res.headers["content-type"] == "application/vnd.mapbox-vector-tile"
    assert res.headers["cache-control"] == YEAR_IMMUTABLE


def test_tile_is_one_primary_key_read_and_never_a_render(client, conn):
    client.get(TILE, params={"v": 1760414400}, headers=AUTH)
    [(sql, params)] = conn.statements
    assert params == {"section": "metro", "z": 10, "x": 553, "y": 346}
    assert "t.section = s.key and t.z = %(z)s and t.x = %(x)s and t.y = %(y)s" in sql
    assert "where s.key = %(section)s" in sql
    assert "st_as" not in sql.lower()


def test_tile_without_a_stored_row_is_an_empty_204(client, conn):
    conn.tile = {"minzoom": 9, "maxzoom": 12, "version": 1760414400, "mvt": None}
    res = client.get(TILE, params={"v": 1760414400}, headers=AUTH)
    assert res.status_code == 204
    assert res.content == b""
    assert res.headers["cache-control"] == YEAR_IMMUTABLE


@pytest.mark.parametrize("params", [{}, {"v": 1}, {"v": "1760414400x"}])
def test_a_tile_url_not_naming_the_live_version_is_served_but_never_pinned(client, conn, params):
    """A tab left open across an ingest keeps asking for the old version: it gets the
    live bytes, but they must not be cached for a year under that URL."""
    res = client.get(TILE, params=params, headers=AUTH)
    assert res.status_code == 200
    assert res.content == MVT
    assert res.headers["cache-control"] == "private, no-cache"


@pytest.mark.parametrize("z", ["8", "13", "999999999"])
def test_tile_outside_the_section_zoom_range_is_204_even_over_a_stray_row(client, conn, z):
    res = client.get(f"/map-overlays/metro/{z}/0/0.mvt", headers=AUTH)
    assert res.status_code == 204
    assert res.content == b""


@pytest.mark.parametrize("z", [9, 12])
def test_tile_at_either_end_of_the_zoom_range_is_served(client, conn, z):
    assert client.get(f"/map-overlays/metro/{z}/0/0.mvt", headers=AUTH).status_code == 200


def test_the_last_tile_of_the_grid_is_in_range(client, conn):
    res = client.get("/map-overlays/metro/10/1023/1023.mvt", headers=AUTH)
    assert res.status_code == 200
    assert conn.statements[0][1] == {"section": "metro", "z": 10, "x": 1023, "y": 1023}


def test_unknown_section_is_404(client, conn):
    conn.tile = None
    res = client.get("/map-overlays/nope/10/553/346.mvt", headers=AUTH)
    assert res.status_code == 404
    assert res.json()["detail"]["reason"] == "unknown_section"
    assert "cache-control" not in res.headers


@pytest.mark.parametrize(
    "path",
    [
        "/map-overlays/metro/10/abc/346.mvt",
        "/map-overlays/metro/10/553/y.mvt",
        "/map-overlays/metro/10/5.5/346.mvt",
        "/map-overlays/metro/10/-1/346.mvt",
        "/map-overlays/metro/10/553/-1.mvt",
        "/map-overlays/metro/10/+553/346.mvt",
        "/map-overlays/metro/10/%20553/346.mvt",
        "/map-overlays/metro/10/%EF%BC%95/346.mvt",
        "/map-overlays/metro/10/1024/346.mvt",
        "/map-overlays/metro/10/553/1024.mvt",
        "/map-overlays/metro/0/1/0.mvt",
        "/map-overlays/metro/10/1234567890/346.mvt",
        "/map-overlays/metro/ten/553/346.mvt",
        "/map-overlays/metro/1e1/553/346.mvt",
    ],
)
def test_a_malformed_or_out_of_range_tile_address_is_400_before_any_read(client, conn, path):
    res = client.get(path, headers=AUTH)
    assert res.status_code == 400
    assert res.json()["detail"]["reason"] == "bad_tile"
    assert "cache-control" not in res.headers
    assert conn.statements == []


def test_tile_address_bounds_x_and_y_by_the_zoom_grid():
    assert mo.tile_address("0", "0", "0") == (0, 0, 0)
    assert mo.tile_address("12", "4095", "0") == (12, 4095, 0)
    assert mo.tile_address("999999999", "999999999", "0") == (999999999, 999999999, 0)
    with pytest.raises(fastapi.HTTPException) as err:
        mo.tile_address("12", "0", "4096")
    assert err.value.status_code == 400


# --- auth --------------------------------------------------------------------------------


def test_both_routes_need_a_signed_in_caller(client, conn, monkeypatch):
    monkeypatch.delenv("API_AUTH_OPTIONAL", raising=False)  # tests/conftest opens it for dev
    conn.sections = [_section("metro", 1)]
    assert client.get("/map-overlays", headers=AUTH).status_code == 200
    assert client.get(TILE, headers=AUTH).status_code == 200
    reads = len(conn.statements)
    assert client.get("/map-overlays").status_code == 401
    assert client.get(TILE).status_code == 401
    assert client.get(TILE, headers={"Authorization": "Bearer not-a-jwt"}).status_code == 401
    assert len(conn.statements) == reads
