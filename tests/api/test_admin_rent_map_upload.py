"""POST /admin/rent-map/revisions reads its XLSX like an attachment: the same size cap and type check,
refused with the same sentences, and a valid file reaches the ingest byte for byte."""

from __future__ import annotations

import io
from typing import Any

import pytest

pytest.importorskip("fastapi")
TestClient = pytest.importorskip("fastapi.testclient").TestClient

from api import attachments  # noqa: E402
from api import dependencies as deps  # noqa: E402
from api import main as api_main  # noqa: E402
from api import rent_map  # noqa: E402

_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@pytest.fixture()
def client():
    conn = object()
    api_main.app.dependency_overrides[deps.get_db_conn] = lambda: conn
    api_main.app.dependency_overrides[deps.require_admin] = (
        lambda: {"sub": "00000000-0000-0000-0000-0000000000ad", "app_metadata": {"is_admin": True}}
    )
    yield TestClient(api_main.app)
    api_main.app.dependency_overrides.clear()


@pytest.fixture()
def ingested(monkeypatch) -> list[tuple[bytes, str, str | None]]:
    calls: list[tuple[bytes, str, str | None]] = []

    def fake(conn: Any, data: bytes, *, source_filename: str, uploaded_by: str | None) -> dict[str, Any]:
        calls.append((data, source_filename, uploaded_by))
        return {"ingested": True, "source_revision": 7}

    monkeypatch.setattr(rent_map, "ingest_bytes", fake)
    return calls


def _upload(client: Any, name: str, data: bytes, mime: str = _XLSX) -> Any:
    return client.post("/admin/rent-map/revisions", files={"file": (name, data, mime)})


def test_a_valid_xlsx_reaches_the_ingest_unchanged(client, ingested):
    res = _upload(client, "2026-05-15_Cenova-mapa.xlsx", b"PK\x03\x04 the workbook")
    assert res.status_code == 200, res.text
    assert ingested == [(b"PK\x03\x04 the workbook", "2026-05-15_Cenova-mapa.xlsx", "settings_ui")]


def test_an_oversize_body_is_refused_with_the_attachments_413(client, ingested):
    res = _upload(client, "2026-05-15_Cenova-mapa.xlsx", b"\0" * (attachments.MAX_BYTES + 1))
    assert res.status_code == 413
    assert res.json()["detail"] == "the file is over 25 MB"
    assert ingested == []


@pytest.mark.parametrize("name", ["map.png", "map.xlsx"])
def test_a_png_is_refused_with_the_attachments_415(client, ingested, name):
    from PIL import Image

    png = io.BytesIO()
    Image.new("RGB", (4, 4)).save(png, "PNG")
    res = _upload(client, name, png.getvalue(), "image/png")
    assert res.status_code == 415
    assert res.json()["detail"] == f"this file type cannot be attached: {name}"
    assert ingested == []
