"""Tests for the phone's Poloha routes: GET /properties/{id}/location (the canonical ad's
point, granularity, label and zoom) and GET /properties/{id}/static-map.png (Mapy's static
map streamed through the API, never stored). Hermetic: the DB is a fake cursor and every
Mapy call is a mocked `requests.get`, so no credit is spent."""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

import pytest
import requests

fastapi = pytest.importorskip("fastapi")
TestClient = pytest.importorskip("fastapi.testclient").TestClient

from api import dependencies as deps
from api import main as api_main
from api.routes import property_location as pl
from tests.api._jwt import mint

RANKS = {"obec": 40, "cast_obce_or_quarter": 50, "street": 60, "address_point": 100, "okres": 30}


def _row(granularity: str | None = "address_point", **over: Any) -> dict[str, Any]:
    row = {
        "lat": 50.105123456,
        "lon": 14.5,
        "granularity": granularity,
        "granularity_rank": RANKS.get(granularity) if granularity else None,
        "street_rank": 60,
        "quarter_rank": 50,
        "obec_rank": 40,
        "country_status": "cz",
        "uncertainty_radius_m": Decimal("12.5"),
        "label": "Freyova 534/2, Praha",
    }
    row.update(over)
    return row


class _Cursor:
    def __init__(self, conn: _Conn) -> None:
        self.conn = conn

    def __enter__(self) -> _Cursor:
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def execute(self, sql: str, params: tuple) -> None:
        self.conn.statements.append((" ".join(sql.split()), params))

    def fetchone(self) -> dict[str, Any] | None:
        return self.conn.row


class _Conn:
    def __init__(self) -> None:
        self.row: dict[str, Any] | None = _row()
        self.statements: list[tuple[str, tuple]] = []

    def cursor(self, row_factory: Any = None) -> _Cursor:
        return _Cursor(self)


class _Resp:
    def __init__(self, status: int, content: bytes = b"", ctype: str = "image/png") -> None:
        self.status_code = status
        self.content = content
        self.headers = {"content-type": ctype}


PNG = b"\x89PNG\r\n\x1a\nfake"
AUTH = {"Authorization": f"Bearer {mint({'sub': 'u1'})}"}


@pytest.fixture()
def conn():
    c = _Conn()
    api_main.app.dependency_overrides[deps.get_db_conn] = lambda: c
    yield c
    api_main.app.dependency_overrides.clear()


@pytest.fixture()
def client():
    return TestClient(api_main.app)


@pytest.fixture()
def mapy(monkeypatch):
    """Records every Mapy call and answers from a queue of responses."""
    monkeypatch.setenv("MAPY_CZ_API_KEY", "primary")
    monkeypatch.setenv("MAPY2_CZ_API_KEY", "backup")
    monkeypatch.delenv(pl.ENABLE_ENV, raising=False)
    calls: list[dict[str, Any]] = []
    answers: list[Any] = []

    def fake_get(url: str, params: dict[str, Any], headers: dict[str, str], timeout: float) -> _Resp:
        calls.append({"url": url, "params": params, "headers": headers, "timeout": timeout})
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(pl.requests, "get", fake_get)
    return calls, answers


# --- /location -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("granularity", "level", "zoom", "note"),
    [
        ("address_point", "street", 16, None),
        ("street", "street", 16, None),
        ("cast_obce_or_quarter", "quarter", 14, "Poloha jen na úrovni části obce"),
        ("obec", "town", 12, "Poloha jen na úrovni obce"),
        # A district point is not a town: it used to read "Poloha jen na úrovni obce".
        ("okres", "coarse", 10, "Poloha jen přibližná, hrubší než obec"),
    ],
)
def test_location_answers_point_label_and_zoom_by_granularity(client, conn, granularity, level, zoom, note):
    conn.row = _row(granularity)
    res = client.get("/properties/7/location", headers=AUTH)
    assert res.status_code == 200
    assert res.json() == {
        "lat": 50.105123456,
        "lon": 14.5,
        "granularity": granularity,
        "granularity_rank": RANKS[granularity],
        "precision": level,
        "country_status": "cz",
        "uncertainty_radius_m": 12.5,
        "label": "Freyova 534/2, Praha",
        "zoom": zoom,
        "note": note,
    }


def test_location_reads_the_location_ad_and_compares_by_rank(client, conn):
    client.get("/properties/7/location", headers=AUTH)
    sql, params = conn.statements[0]
    assert params == (7,)
    assert "listing_location ll on ll.listing_id = p.location_listing_ref_id" in sql
    assert "repr_listing_ref_id" not in sql
    assert "location_display_label(" in sql
    assert "location_granularity_rank" in sql


def test_precision_steps_follow_the_rank_table_not_fixed_numbers():
    ranks = {"street_rank": 70, "quarter_rank": 55, "obec_rank": 45}
    assert pl.precision(70, **ranks) == "street"
    assert pl.precision(60, **ranks) == "quarter"
    assert pl.precision(45, **ranks) == "town"
    assert pl.precision(44, **ranks) == "coarse"
    assert pl.precision(None, **ranks) == "coarse"


def test_location_without_coordinates_is_404_no_location(client, conn):
    conn.row = _row(None, lat=None, lon=None)
    res = client.get("/properties/7/location", headers=AUTH)
    assert res.status_code == 404
    assert res.json()["detail"]["reason"] == "no_location"


def test_unknown_property_is_404_not_found(client, conn):
    conn.row = None
    res = client.get("/properties/7/location", headers=AUTH)
    assert res.status_code == 404
    assert res.json()["detail"]["reason"] == "not_found"


def test_both_routes_need_a_signed_in_caller(client, conn, monkeypatch):
    monkeypatch.delenv("API_AUTH_OPTIONAL", raising=False)  # tests/conftest opens it for dev
    assert client.get("/properties/7/location", headers=AUTH).status_code == 200
    assert client.get("/properties/7/location").status_code == 401
    assert client.get("/properties/7/static-map.png", params={"w": 1080}).status_code == 401


# --- /static-map.png -------------------------------------------------------------------


def test_static_map_streams_mapy_png_with_a_private_hour_cache(client, conn, mapy, caplog):
    calls, answers = mapy
    answers.append(_Resp(200, PNG))
    with caplog.at_level(logging.INFO, logger=pl.LOG.name):
        res = client.get("/properties/7/static-map.png", params={"w": 1080}, headers=AUTH)
    assert res.status_code == 200
    assert res.content == PNG
    assert res.headers["content-type"] == "image/png"
    assert res.headers["cache-control"] == "private, max-age=3600"
    assert "etag" not in res.headers
    assert len(calls) == 1
    call = calls[0]
    assert call["url"] == "https://api.mapy.cz/v1/static/map"
    assert call["params"] == {
        "lon": "14.500000",
        "lat": "50.105123",
        "zoom": 16,
        "width": 540,
        "height": 405,
        "scale": 2,
        "mapset": "basic",
        "format": "png",
        "markers": "color:red;size:normal;14.500000,50.105123",
    }
    assert call["headers"] == {"X-Mapy-Api-Key": "primary"}
    assert call["timeout"] == 10
    assert any(
        "STATIC_MAP property=7 zoom=16 status=200" in r.getMessage() for r in caplog.records
    )


def test_static_map_zoom_follows_the_granularity(client, conn, mapy):
    calls, answers = mapy
    conn.row = _row("obec")
    answers.append(_Resp(200, PNG))
    client.get("/properties/7/static-map.png", params={"w": 1080}, headers=AUTH)
    assert calls[0]["params"]["zoom"] == 12


def test_static_map_tries_the_backup_key_once_on_403(client, conn, mapy):
    calls, answers = mapy
    answers.extend([_Resp(403, b"", "application/json"), _Resp(200, PNG)])
    res = client.get("/properties/7/static-map.png", params={"w": 1080}, headers=AUTH)
    assert res.status_code == 200
    assert [c["headers"]["X-Mapy-Api-Key"] for c in calls] == ["primary", "backup"]


def test_static_map_does_not_fail_over_on_an_outage(client, conn, mapy):
    calls, answers = mapy
    answers.append(_Resp(500, b"", "text/plain"))
    res = client.get("/properties/7/static-map.png", params={"w": 1080}, headers=AUTH)
    assert res.status_code == 502
    assert res.json()["detail"]["reason"] == "map_unavailable"
    assert len(calls) == 1


@pytest.mark.parametrize(
    "answers_in",
    [
        [requests.ConnectionError("down")],
        [requests.Timeout("slow")],
        [_Resp(403, b"", "application/json"), _Resp(429, b"", "application/json")],
        [_Resp(200, b"<html>", "text/html")],
    ],
)
def test_static_map_failure_is_502_map_unavailable(client, conn, mapy, answers_in):
    _calls, answers = mapy
    answers.extend(answers_in)
    res = client.get("/properties/7/static-map.png", params={"w": 1080}, headers=AUTH)
    assert res.status_code == 502
    assert res.json()["detail"]["reason"] == "map_unavailable"


def test_static_map_without_a_key_is_502(client, conn, mapy, monkeypatch):
    calls, _answers = mapy
    monkeypatch.delenv("MAPY_CZ_API_KEY")
    monkeypatch.delenv("MAPY2_CZ_API_KEY")
    res = client.get("/properties/7/static-map.png", params={"w": 1080}, headers=AUTH)
    assert res.status_code == 502
    assert calls == []


@pytest.mark.parametrize("value", ["0", "false", "off"])
def test_kill_switch_answers_404_disabled_without_calling_mapy(client, conn, mapy, monkeypatch, value):
    calls, _answers = mapy
    monkeypatch.setenv(pl.ENABLE_ENV, value)
    res = client.get("/properties/7/static-map.png", params={"w": 1080}, headers=AUTH)
    assert res.status_code == 404
    assert res.json()["detail"]["reason"] == "disabled"
    assert calls == []
    assert conn.statements == []


def test_kill_switch_is_read_per_request(client, conn, mapy, monkeypatch):
    _calls, answers = mapy
    monkeypatch.setenv(pl.ENABLE_ENV, "0")
    assert client.get("/properties/7/static-map.png", params={"w": 1080}, headers=AUTH).status_code == 404
    monkeypatch.setenv(pl.ENABLE_ENV, "1")
    answers.append(_Resp(200, PNG))
    assert client.get("/properties/7/static-map.png", params={"w": 1080}, headers=AUTH).status_code == 200


@pytest.mark.parametrize("params", [{"w": 540}, {"w": 2160}, {}])
def test_only_the_1080_box_is_served(client, conn, mapy, params):
    calls, _answers = mapy
    res = client.get("/properties/7/static-map.png", params=params, headers=AUTH)
    assert res.status_code == 404
    assert calls == []


def test_static_map_of_a_property_without_a_point_is_404_and_free(client, conn, mapy):
    calls, _answers = mapy
    conn.row = _row(None, lat=None, lon=None)
    res = client.get("/properties/7/static-map.png", params={"w": 1080}, headers=AUTH)
    assert res.status_code == 404
    assert res.json()["detail"]["reason"] == "no_location"
    assert calls == []


def test_static_map_writes_nothing_to_storage(client, conn, mapy, monkeypatch):
    """Mapy's terms: show online, never store. The route must not reach R2."""
    from scraper import image_storage

    def boom(*_a: Any, **_kw: Any) -> None:
        raise AssertionError("the static map must never touch R2")

    monkeypatch.setattr(image_storage.R2Client, "from_env", classmethod(boom))
    _calls, answers = mapy
    answers.append(_Resp(200, PNG))
    assert client.get("/properties/7/static-map.png", params={"w": 1080}, headers=AUTH).status_code == 200
