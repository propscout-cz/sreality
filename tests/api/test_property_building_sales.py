"""GET /properties/{id}/building/sales, hermetic: the rows' shape and the path through the door. The SQL is
PREPAREd against the replayed schema by the SQL sweep; the generated columns run in the migration's live test."""

from __future__ import annotations

import datetime
from decimal import Decimal
from typing import Any

import pytest

fastapi = pytest.importorskip("fastapi")
TestClient = pytest.importorskip("fastapi.testclient").TestClient

from api import dependencies as deps
from api import main as api_main
from tests.api._jwt import mint

AUTH = {"Authorization": f"Bearer {mint({'sub': 'u1'})}"}
ROW = {
    "source": "reas", "source_record_id": "108473925010_unit_22906291-105-37", "sold_at": datetime.date(2025, 3, 14),
    "price_czk": 6450000, "asking_last_czk": 6790000, "category_main": "byt", "disposition": "3+kk",
    "area_m2": Decimal("62.0"), "unit_label": "105/37", "source_url": "https://example.test/x",
    "price_per_m2": Decimal("104032.26"), "price_per_m2_basis": "sale_capital_czk_m2",
}


class _Cursor:
    def __init__(self, conn: _Conn) -> None:
        self.conn = conn

    def __enter__(self) -> _Cursor:
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def execute(self, sql: str, params: tuple) -> None:
        self.conn.statements.append((" ".join(sql.split()), params))

    def fetchall(self) -> list[dict[str, Any]]:
        return self.conn.rows


class _Conn:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = [dict(ROW)]
        self.statements: list[tuple[str, tuple]] = []

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


def test_a_sale_carries_its_unit_its_date_and_the_measured_per_m2(client, conn):
    sale, = client.get("/properties/7/building/sales", headers=AUTH).json()["sales"]
    assert (sale["unit_label"], sale["sold_at"], sale["price_czk"]) == ("105/37", "2025-03-14", 6450000)
    assert (sale["area_m2"], sale["price_per_m2"], sale["price_per_m2_basis"]) == (62.0, 104032.26, "sale_capital_czk_m2")


def test_the_read_joins_the_doors_building_to_the_sales_own(client, conn):
    client.get("/properties/7/building/sales", headers=AUTH)
    sql, params = conn.statements[0]
    assert params == (7,)
    assert "ap.kod_adm = ll.ruian_adm_kod" in sql
    assert "s.building_code = ap.stavebni_objekt_code" in sql
    assert "measure_price_per_m2(" in sql and "price / " not in sql


def test_a_sale_without_an_area_has_no_per_m2(client, conn):
    conn.rows = [{**ROW, "area_m2": None, "price_per_m2": None}]
    sale, = client.get("/properties/7/building/sales", headers=AUTH).json()["sales"]
    assert sale["area_m2"] is None and sale["price_per_m2"] is None


def test_no_building_or_no_sale_is_an_empty_list(client, conn):
    conn.rows = []
    assert client.get("/properties/7/building/sales", headers=AUTH).json() == {"sales": []}
