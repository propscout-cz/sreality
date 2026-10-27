"""Tests for /buildings/* B1 endpoints (confirm_units, re_extract).

Hermetic — overrides the FastAPI dependencies, monkey-patches the
persistence helpers + the extractor so no real DB, no real LLM, no
real HTTP.
"""

from __future__ import annotations

from typing import Any

import pytest

fastapi = pytest.importorskip("fastapi")
TestClient = pytest.importorskip("fastapi.testclient").TestClient

from api import building_runs as br
from api import dependencies as deps
from api import main as api_main
from toolkit import building_extraction


@pytest.fixture()
def client(monkeypatch):
    api_main.app.dependency_overrides[deps.get_db_conn] = lambda: object()
    api_main.app.dependency_overrides[deps.require_token] = lambda: None
    api_main.app.dependency_overrides[deps.require_admin] = lambda: {"sub": "operator", "is_admin": True}
    api_main.app.dependency_overrides[deps.get_sreality_client] = lambda: object()
    api_main.app.dependency_overrides[deps.get_llm_client] = lambda: object()
    # Run scheduled BackgroundTasks synchronously in tests so the response
    # payload reflects the post-extraction state, not the pending row.
    from starlette.background import BackgroundTasks
    monkeypatch.setattr(
        BackgroundTasks, "add_task",
        lambda self, func, *args, **kwargs: func(*args, **kwargs),
    )
    yield TestClient(api_main.app)
    api_main.app.dependency_overrides.clear()


class _State:
    def __init__(self) -> None:
        self.rows: dict[int, dict[str, Any]] = {}
        self.next_id = 1


def _patch_persistence(monkeypatch) -> _State:
    state = _State()

    def fake_insert(conn, **fields: Any) -> int:
        rid = state.next_id
        state.next_id += 1
        state.rows[rid] = dict(fields)
        return rid

    def fake_update(conn, building_id: int, **fields: Any) -> None:
        if building_id in state.rows:
            state.rows[building_id].update(fields)

    def fake_fetch(conn, building_id: int) -> dict[str, Any] | None:
        if building_id not in state.rows:
            return None
        fields = state.rows[building_id]
        return {
            "id": building_id,
            "created_at": "2026-05-12T10:00:00+00:00",
            "source": fields.get("source"),
            "status": fields.get("status"),
            "input_url": fields.get("input_url"),
            "input_sreality_id": fields.get("input_sreality_id"),
            "input_spec": fields.get("input_spec"),
            "subject_summary": fields.get("subject_summary"),
            "units_proposal": fields.get("units_proposal"),
            "units": fields.get("units"),
            "total_rent_p25_czk": fields.get("total_rent_p25_czk"),
            "total_rent_p50_czk": fields.get("total_rent_p50_czk"),
            "total_rent_p75_czk": fields.get("total_rent_p75_czk"),
            "total_sale_p25_czk": fields.get("total_sale_p25_czk"),
            "total_sale_p50_czk": fields.get("total_sale_p50_czk"),
            "total_sale_p75_czk": fields.get("total_sale_p75_czk"),
            "business_case": fields.get("business_case"),
            "warnings": fields.get("warnings"),
            "error_message": fields.get("error_message"),
            "special_instructions": fields.get("special_instructions"),
            "contextual_text": fields.get("contextual_text"),
        }

    def fake_children(conn, building_id: int) -> list[dict[str, Any]]:
        return []

    def fake_attachments(conn, building_id: int) -> list[dict[str, Any]]:
        return []

    monkeypatch.setattr(br, "_insert_building", fake_insert)
    monkeypatch.setattr(br, "_update_building_fields", fake_update)
    monkeypatch.setattr(br, "_fetch_building", fake_fetch)
    monkeypatch.setattr(br, "_fetch_children", fake_children)
    monkeypatch.setattr(br, "_fetch_attachments", fake_attachments)
    return state


def _patch_extractor(monkeypatch, payload: dict[str, Any] | Exception) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def fake(conn, llm, **kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        if isinstance(payload, Exception):
            raise payload
        return {"data": payload, "metadata": {}}

    monkeypatch.setattr(building_extraction, "extract_building_units", fake)
    return calls


def _example_extractor_payload() -> dict[str, Any]:
    return {
        "sreality_id": 999,
        "snapshot_id": 42,
        "units": [
            {
                "unit_id": "u1", "label": None, "floor": "ground",
                "area_m2": 70.0, "disposition": "2+kk",
                "condition": "dobry", "is_potential": False,
                "source": "both", "notes": None,
            },
        ],
        "building": {
            "floor_count": 2, "has_attic": True, "year_built": 1932,
            "construction_type": "cihla", "total_area_m2": 70.0,
            "condition": "dobry", "notes": None,
        },
        "confidence": "high",
        "warnings": [],
        "n_images": 0,
        "model": "claude-sonnet-4-5",
        "cost_usd": 0.01,
        "cache_hit": False,
    }


# -- POST /buildings/{id}/confirm_units --------------------------------------


def test_confirm_units_happy_path(client, monkeypatch):
    state = _patch_persistence(monkeypatch)
    state.rows[1] = {
        "source": "ui", "status": "awaiting_input",
        "units_proposal": {"units": [], "building": {}, "confidence": "high",
                            "warnings": [], "n_images": 0,
                            "model": "x", "cost_usd": 0.01, "snapshot_id": 1},
    }
    state.next_id = 2
    # B2: confirm_units now schedules the per-unit fan-out as a BackgroundTask.
    # Capture the schedule instead of running it — the fan-out itself is
    # exercised in tests/api/test_buildings_b2.py.
    scheduled: list[int] = []
    monkeypatch.setattr(
        br, "_orchestrate_building_estimations_background",
        lambda **kw: scheduled.append(kw["building_id"]),
    )

    units = [
        {
            "unit_id": "u1", "label": "flat 1", "floor": "1",
            "area_m2": 60.0, "disposition": "2+kk",
            "condition": "dobry", "is_potential": False,
            "source": "user_added", "notes": None,
        },
    ]
    response = client.post(
        "/buildings/1/confirm_units", json={"units": units},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "estimating"
    assert body["units"] == units
    assert state.rows[1]["status"] == "estimating"
    assert scheduled == [1]


def test_confirm_units_rejects_wrong_status(client, monkeypatch):
    state = _patch_persistence(monkeypatch)
    state.rows[1] = {"source": "ui", "status": "estimating"}
    state.next_id = 2

    response = client.post(
        "/buildings/1/confirm_units",
        json={"units": [{"unit_id": "u1", "is_potential": False}]},
    )
    assert response.status_code == 409


def test_confirm_units_rejects_missing_building(client, monkeypatch):
    _patch_persistence(monkeypatch)
    response = client.post(
        "/buildings/999/confirm_units",
        json={"units": [{"unit_id": "u1", "is_potential": False}]},
    )
    assert response.status_code == 404


def test_confirm_units_rejects_duplicate_unit_ids(client, monkeypatch):
    state = _patch_persistence(monkeypatch)
    state.rows[1] = {"source": "ui", "status": "awaiting_input"}
    state.next_id = 2

    response = client.post(
        "/buildings/1/confirm_units",
        json={"units": [
            {"unit_id": "u1", "is_potential": False},
            {"unit_id": "u1", "is_potential": False},
        ]},
    )
    assert response.status_code == 400
    assert "duplicate unit_id" in response.json()["detail"]


# -- POST /buildings/{id}/re_extract -----------------------------------------


def test_re_extract_happy_path(client, monkeypatch):
    state = _patch_persistence(monkeypatch)
    state.rows[1] = {
        "source": "ui", "status": "awaiting_input",
        "input_sreality_id": 999,
        "subject_summary": {"source_url": "x", "fields": {}, "building": {}},
    }
    state.next_id = 2
    extractor_calls = _patch_extractor(monkeypatch, _example_extractor_payload())

    response = client.post("/buildings/1/re_extract")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "awaiting_input"
    assert body["units_proposal"]["units"][0]["unit_id"] == "u1"
    assert extractor_calls[0]["force_refresh"] is True


def test_re_extract_rejects_wrong_status(client, monkeypatch):
    state = _patch_persistence(monkeypatch)
    state.rows[1] = {
        "source": "ui", "status": "estimating",
        "input_sreality_id": 999,
    }
    state.next_id = 2

    response = client.post("/buildings/1/re_extract")
    assert response.status_code == 409
