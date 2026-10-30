"""Outreach CRM route tests — hermetic (no DB/HTTP/LLM)."""

from __future__ import annotations

import pytest

fastapi = pytest.importorskip("fastapi")
TestClient = pytest.importorskip("fastapi.testclient").TestClient

from api import dependencies as deps
from api import main as api_main
from api import outreach
from api.broker_board import BOARD_MAX
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
    assert captured["target"] == {"districts": [_PRAHA], "category_main": "byt", "category_type": None}
    assert res.json()["id"] == 1


def test_create_campaign_without_a_target_stores_the_national_shape(client, monkeypatch):
    captured = {}
    monkeypatch.setattr(outreach_routes.outreach, "create_campaign",
                        lambda conn, **kw: captured.update(kw) or {"id": 2})
    assert client.post("/outreach/campaigns", json={"name": "Celá ČR"}).status_code == 200
    assert captured["target"] == {"districts": [], "category_main": None, "category_type": None}


@pytest.mark.parametrize("target", [
    {"districts": [{"name": "Praha"}]},
    {"districts": [{"name": "Praha", "level": "locality", "id": 554782}]},
    {"districts": [{"name": "Praha", "level": "obec", "id": 0}]},
    {"region_ids": [27]},
    {"districts": [], "metric": "active_property_count"},
], ids=["no level or code", "not one of the five levels", "no code", "the deleted id lists", "a count metric"])
def test_a_campaign_target_refuses_a_chip_without_a_level_and_code(client, monkeypatch, target):
    """Migration 609: a target is chips or nothing — a stale client's `region_ids` would
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


def _board(monkeypatch, found: list[dict]) -> list:
    calls = []
    monkeypatch.setattr(outreach, "board", lambda conn, args, *, contacts: calls.append((args, contacts))
                        or {"data": found, "metadata": {}})
    return calls


@pytest.mark.parametrize("target, chips, category_main_in, category_type", [
    ({"districts": [_PRAHA, {**_PRAHA, "name": "Žižkov", "level": "cast_obce", "id": 490067, "excluded": True}],
      "category_main": "byt", "category_type": "prodej"},
     [("obec", 554782, False), ("cast_obce", 490067, True)], ["byt"], "prodej"),
    ({"districts": []}, [], None, None),
], ids=["chips, category and deal type", "national"])
def test_select_targets_reads_the_board_over_the_campaigns_live_cohort(
        monkeypatch, target, chips, category_main_in, category_type) -> None:
    """The board's top CANDIDATES by live properties, past the wire's cap: the exclusions come before the cut."""
    calls = _board(monkeypatch, [])
    outreach.select_targets(_Conn(), target, campaign_id=7, limit=30)
    ((args, contacts),) = calls
    assert contacts is True and args.limit == outreach.CANDIDATES > BOARD_MAX
    assert [(c.level, c.id, c.excluded) for c in args.districts] == chips
    assert (args.category_main_in, args.category_type, args.status, args.sort) == (
        category_main_in, category_type, "active", "properties")


def test_select_targets_keeps_reachable_unsuppressed_undrafted_brokers_in_the_boards_order(monkeypatch) -> None:
    rows = [{"broker_id": b, "primary_email": e} for b, e in ((1, "a@rk.cz"), (2, None), (3, "c@rk.cz"),
                                                               (4, "d@rk.cz"), (5, "e@rk.cz"))]
    _board(monkeypatch, rows)
    conn = _Conn([(3,)])  # suppressed or already drafted for this campaign
    out = outreach.select_targets(conn, {"districts": []}, campaign_id=7, limit=2)
    assert [r["broker_id"] for r in out] == [1, 4]
    sql, params = conn.cur.seen[0]
    assert "broker_outreach_suppression" in sql and "outreach_messages" in sql
    assert params == {"ids": [1, 2, 3, 4, 5], "campaign": 7}


@pytest.mark.parametrize("target", [
    {"region_ids": [108], "category_main": "byt"},
    {"districts": [{"name": "Praha", "level": "locality", "id": 554782}]},
    {"districts": [{"name": "Praha"}]},
    {"districts": [], "metric": "listing_count"},
], ids=["the id lists", "not one of the five levels", "no level or code", "a leaderboard metric"])
def test_select_targets_refuses_a_stored_target_that_is_not_chips(target) -> None:
    """An old API image can write `region_ids` between 609's apply and the deploy. Read
    leniently, that campaign would target the whole country; it is refused instead."""
    conn = _Conn()
    with pytest.raises(outreach.StaleTarget):
        outreach.select_targets(conn, target)
    assert conn.cur.seen == []


def test_preview_and_generate_answer_409_for_a_stale_target(client, monkeypatch):
    monkeypatch.setattr(outreach_routes.outreach, "get_campaign",
                        lambda conn, cid: {"id": cid, "target": {"region_ids": [108]}})
    assert client.get("/outreach/campaigns/4/targets").status_code == 409
    res = client.post("/outreach/campaigns/4/generate")
    assert res.status_code == 409 and "609" in res.json()["detail"]


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
