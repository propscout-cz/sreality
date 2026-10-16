"""Outreach CRM route tests — hermetic (no DB/HTTP/LLM)."""

from __future__ import annotations

import pytest

fastapi = pytest.importorskip("fastapi")
TestClient = pytest.importorskip("fastapi.testclient").TestClient

from api import dependencies as deps
from api import main as api_main
from api import outreach
from api.routes import outreach as outreach_routes
from tests.test_brokers_toolkit import _Conn

_PRAHA = {"name": "Praha", "context": None, "excluded": False, "level": "obec", "id": 554782}


@pytest.fixture()
def client():
    api_main.app.dependency_overrides[deps.get_db_conn] = lambda: object()
    api_main.app.dependency_overrides[deps.get_llm_client] = lambda: object()
    api_main.app.dependency_overrides[deps.require_admin] = (
        lambda: {"sub": "00000000-0000-0000-0000-0000000000ad", "app_metadata": {"is_admin": True}}
    )
    yield TestClient(api_main.app)
    api_main.app.dependency_overrides.clear()


def test_create_campaign(client, monkeypatch):
    captured = {}
    def fake(conn, **kw):
        captured.update(kw)
        return {"id": 1, "name": kw["name"], "status": "draft"}
    monkeypatch.setattr(outreach_routes.outreach, "create_campaign", fake)

    res = client.post("/outreach/campaigns", json={
        "name": "Praha byty", "goal": "off-market",
        "target": {"districts": [{"name": "Praha", "level": "obec", "id": 554782}],
                   "category_main": "byt"}})
    assert res.status_code == 200
    assert captured["name"] == "Praha byty"
    assert captured["target"] == {"districts": [_PRAHA], "category_main": "byt",
                                  "category_type": None, "metric": None}
    assert res.json()["id"] == 1


def test_create_campaign_without_a_target_stores_the_national_shape(client, monkeypatch):
    captured = {}
    monkeypatch.setattr(outreach_routes.outreach, "create_campaign",
                        lambda conn, **kw: captured.update(kw) or {"id": 2})
    assert client.post("/outreach/campaigns", json={"name": "Celá ČR"}).status_code == 200
    assert captured["target"] == {"districts": [], "category_main": None,
                                  "category_type": None, "metric": None}


@pytest.mark.parametrize("target", [
    {"districts": [{"name": "Praha"}]},
    {"districts": [{"name": "Praha", "level": "locality", "id": 554782}]},
    {"districts": [{"name": "Praha", "level": "obec", "id": 0}]},
    {"region_ids": [27]},
], ids=["no level or code", "not one of the five levels", "no code", "the deleted id lists"])
def test_a_campaign_target_refuses_a_chip_without_a_level_and_code(client, monkeypatch, target):
    """Migration 607: a target is chips or nothing — a stale client's `region_ids` would
    otherwise be dropped and the campaign silently aimed at the whole country."""
    calls = []
    monkeypatch.setattr(outreach_routes.outreach, "create_campaign",
                        lambda conn, **kw: calls.append(kw) or {"id": 1})
    monkeypatch.setattr(outreach_routes.outreach, "update_campaign",
                        lambda conn, cid, **kw: calls.append(kw) or {"id": cid})
    assert client.post("/outreach/campaigns", json={"name": "x", "target": target}).status_code == 422
    assert client.patch("/outreach/campaigns/1", json={"target": target}).status_code == 422
    assert calls == []


def test_update_campaign_replaces_the_target_only_when_sent(client, monkeypatch):
    captured: list[dict] = []
    monkeypatch.setattr(outreach_routes.outreach, "update_campaign",
                        lambda conn, cid, **kw: captured.append(kw) or {"id": cid})
    client.patch("/outreach/campaigns/3", json={"name": "renamed"})
    client.patch("/outreach/campaigns/3", json={"target": {"districts": [_PRAHA]}})
    assert captured[0]["target"] is None
    assert captured[1]["target"]["districts"] == [_PRAHA]


def test_select_targets_compiles_the_stored_chips_like_the_leaderboard() -> None:
    """The stored chips reach the RPC as the four arrays `toolkit.brokers.leaderboard`
    sends, ahead of the categories, the metric and the 2000 cap (the contract test pins
    the 8-argument call)."""
    conn = _Conn()
    target = {"districts": [_PRAHA, {**_PRAHA, "name": "Žižkov", "level": "cast_obce",
                                     "id": 490067, "excluded": True}],
              "category_main": "byt", "category_type": "prodej", "metric": "listing_count"}
    outreach.select_targets(conn, target, campaign_id=7, limit=30)
    _, params = conn.cur.seen[0]
    assert params == (["Praha", "Žižkov"], [False, True], ["obec", "cast_obce"],
                      [554782, 490067], "byt", "prodej", "listing_count", 2000, 7, 7, 30)


def test_select_targets_without_chips_is_national() -> None:
    conn = _Conn()
    outreach.select_targets(conn, {"districts": []})
    _, params = conn.cur.seen[0]
    assert params == (None, None, None, None, None, None, "active_property_count", 2000,
                      None, None, 25)


def test_get_campaign_404(client, monkeypatch):
    monkeypatch.setattr(outreach_routes.outreach, "get_campaign", lambda conn, cid: None)
    assert client.get("/outreach/campaigns/9").status_code == 404


def test_generate_drafts(client, monkeypatch):
    captured = {}
    def fake(conn, llm, cid, *, limit):
        captured["cid"], captured["limit"] = cid, limit
        return {"generated": 3, "targets": 5}
    monkeypatch.setattr(outreach_routes.outreach, "generate_drafts", fake)

    res = client.post("/outreach/campaigns/2/generate", params={"limit": 10})
    assert res.status_code == 200
    assert captured == {"cid": 2, "limit": 10}
    assert res.json() == {"generated": 3, "targets": 5}


def test_update_message_404(client, monkeypatch):
    monkeypatch.setattr(outreach_routes.outreach, "update_message", lambda conn, mid, **kw: None)
    assert client.patch("/outreach/messages/5", json={"status": "approved"}).status_code == 404


def test_update_message_ok(client, monkeypatch):
    captured = {}
    def fake(conn, mid, **kw):
        captured.update(kw); captured["mid"] = mid
        return {"id": mid, "status": kw.get("status")}
    monkeypatch.setattr(outreach_routes.outreach, "update_message", fake)
    res = client.patch("/outreach/messages/5", json={"status": "sent"})
    assert res.status_code == 200
    assert captured["mid"] == 5 and captured["status"] == "sent"


def test_suppression_add_and_remove(client, monkeypatch):
    monkeypatch.setattr(outreach_routes.outreach, "suppress_broker",
                        lambda conn, bid, **kw: {"broker_id": bid, "reason": kw.get("reason")})
    res = client.post("/outreach/suppressions", json={"broker_id": 7, "reason": "asked to stop"})
    assert res.status_code == 200 and res.json()["broker_id"] == 7

    monkeypatch.setattr(outreach_routes.outreach, "unsuppress_broker", lambda conn, bid: True)
    assert client.delete("/outreach/suppressions/7").status_code == 200

    monkeypatch.setattr(outreach_routes.outreach, "unsuppress_broker", lambda conn, bid: False)
    assert client.delete("/outreach/suppressions/7").status_code == 404
