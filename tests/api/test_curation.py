"""Tests for the curation endpoints (collections, notes, tags).

Curation is PROPERTY-grain (migration 202): collection membership, notes and
tags key on property_id so operator curation is dedup-stable.

Hermetic — overrides get_db_conn so no real DB is hit, and patches the
persistence helpers in api.curation to in-memory dicts. Pydantic
validation tests run unmodified through the route layer; CHECK
constraints and unique indexes are exercised in a separate integration
test path against a real Postgres (out of scope for this file).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import psycopg
import pytest

fastapi = pytest.importorskip("fastapi")
TestClient = pytest.importorskip("fastapi.testclient").TestClient

from api import curation
from api import dependencies as deps
from api import main as api_main
from api import schemas as _s
from api import tenant_pool
from tests.api._script_conn import ScriptConn

_SUB = "11111111-1111-1111-1111-111111111111"
_ACCT = "22222222-2222-2222-2222-222222222222"


@pytest.fixture()
def client(monkeypatch):
    api_main.app.dependency_overrides[deps.get_db_conn] = lambda: object()
    # /collections list + /collections/{id}/properties + /properties/{id}/notes
    # moved onto the tenant pool (Wave 1 W1-1) — overridden to a production-
    # shaped JWT identity here, same as tests/api/test_pipeline.py; auth
    # correctness itself lives in tests/api/test_auth.py.
    api_main.app.dependency_overrides[tenant_pool.tenant_conn] = lambda: object()
    api_main.app.dependency_overrides[deps.verify_jwt] = lambda: {"sub": _SUB}
    monkeypatch.setattr(
        tenant_pool, "resolve_account_id", lambda conn, claims: _ACCT,
    )
    yield TestClient(api_main.app)
    api_main.app.dependency_overrides.clear()


@pytest.fixture()
def store(monkeypatch):
    """In-memory replacement for the curation.* persistence functions."""
    state: dict[str, Any] = {
        "collections":      {},
        "next_collection":  1,
        "memberships":      set(),   # (collection_id, property_id)
        "notes":            {},
        "next_note":        1,
        "tags":             {},
        "next_tag":         1,
        "tag_links":        set(),   # (property_id, tag_id)
    }

    def _coll_to_dict(cid: int) -> dict[str, Any]:
        c = state["collections"][cid]
        count = sum(1 for (cc, _) in state["memberships"] if cc == cid)
        return {**c, "listing_count": count}

    def _tag_to_dict(tid: int) -> dict[str, Any]:
        t = state["tags"][tid]
        count = sum(1 for (_, tt) in state["tag_links"] if tt == tid)
        return {**t, "listing_count": count}

    def fake_create_collection(conn, body, account_id):
        for c in state["collections"].values():
            if c["name"].lower() == body.name.lower():
                from fastapi import HTTPException
                raise HTTPException(409, "collection name already exists")
        cid = state["next_collection"]
        state["next_collection"] += 1
        state["collections"][cid] = {
            "id": cid,
            "name": body.name,
            "description": body.description,
            "created_at": "2026-05-10T00:00:00+00:00",
            "updated_at": "2026-05-10T00:00:00+00:00",
        }
        return {**state["collections"][cid], "listing_count": 0}

    def fake_list_collections(conn):
        rows = [_coll_to_dict(cid) for cid in state["collections"]]
        rows.sort(key=lambda r: r["updated_at"], reverse=True)
        return {"data": rows, "total": len(rows)}

    def fake_get_collection(conn, cid):
        if cid not in state["collections"]:
            from fastapi import HTTPException
            raise HTTPException(404, "collection not found")
        properties = [
            {
                "property_id": pid, "sreality_id": pid, "district": None,
                "disposition": None, "area_m2": None, "price_czk": None,
                "last_seen_at": "2026-05-10T00:00:00+00:00",
                "is_active": True,
                "added_at": "2026-05-10T00:00:00+00:00",
            }
            for (cc, pid) in state["memberships"] if cc == cid
        ]
        return {"collection": _coll_to_dict(cid), "properties": properties}

    def fake_update_collection(conn, cid, body):
        if cid not in state["collections"]:
            from fastapi import HTTPException
            raise HTTPException(404, "collection not found")
        if body.name is not None:
            for other_cid, c in state["collections"].items():
                if other_cid != cid and c["name"].lower() == body.name.lower():
                    from fastapi import HTTPException
                    raise HTTPException(409, "collection name already exists")
            state["collections"][cid]["name"] = body.name
        if body.description is not None:
            state["collections"][cid]["description"] = body.description
        return _coll_to_dict(cid)

    def fake_delete_collection(conn, cid):
        if cid not in state["collections"]:
            from fastapi import HTTPException
            raise HTTPException(404, "collection not found")
        del state["collections"][cid]
        state["memberships"] = {
            (cc, pid) for (cc, pid) in state["memberships"] if cc != cid
        }
        return {"deleted": True}

    def fake_add_properties(conn, cid, body):
        if cid not in state["collections"]:
            from fastapi import HTTPException
            raise HTTPException(404, "collection not found")
        added = 0
        for pid in body.property_ids:
            key = (cid, pid)
            if key not in state["memberships"]:
                state["memberships"].add(key)
                added += 1
        return {"added": added, "skipped": len(body.property_ids) - added}

    def fake_remove_property(conn, cid, pid):
        key = (cid, pid)
        removed = key in state["memberships"]
        state["memberships"].discard(key)
        return {"removed": removed}

    def fake_list_notes(conn, pid):
        items = [n for n in state["notes"].values() if n["property_id"] == pid]
        items.sort(key=lambda n: (n["created_at"], n["id"]), reverse=True)
        return {"data": items}

    def fake_create_note(conn, pid, body, account_id):
        nid = state["next_note"]
        state["next_note"] += 1
        note = {
            "id": nid,
            "property_id": pid,
            "body": body.body,
            "origin_listing_id": body.origin_listing_id,
            "created_at": f"2026-05-10T00:00:0{nid}+00:00",
        }
        state["notes"][nid] = note
        return note

    def fake_list_tags(conn):
        rows = [_tag_to_dict(tid) for tid in state["tags"]]
        rows.sort(key=lambda r: r["name"].lower())
        return {"data": rows}

    def fake_create_tag(conn, body, account_id):
        for t in state["tags"].values():
            if t["name"].lower() == body.name.lower():
                from fastapi import HTTPException
                raise HTTPException(409, "tag name already exists")
        tid = state["next_tag"]
        state["next_tag"] += 1
        state["tags"][tid] = {
            "id": tid,
            "name": body.name,
            "color": body.color,
            "created_at": "2026-05-10T00:00:00+00:00",
        }
        return {**state["tags"][tid], "listing_count": 0}

    def fake_delete_tag(conn, tid):
        if tid not in state["tags"]:
            from fastapi import HTTPException
            raise HTTPException(404, "tag not found")
        del state["tags"][tid]
        state["tag_links"] = {
            (pid, tt) for (pid, tt) in state["tag_links"] if tt != tid
        }
        return {"deleted": True}

    def fake_update_tag(conn, tid, body):
        if tid not in state["tags"]:
            from fastapi import HTTPException
            raise HTTPException(404, "tag not found")
        if body.name is not None:
            for other_tid, t in state["tags"].items():
                if other_tid != tid and t["name"].lower() == body.name.lower():
                    from fastapi import HTTPException
                    raise HTTPException(409, "tag name already exists")
            state["tags"][tid]["name"] = body.name
        if body.color is not None:
            state["tags"][tid]["color"] = body.color
        return _tag_to_dict(tid)

    def fake_attach_tag(conn, pid, body):
        if body.tag_id not in state["tags"]:
            from fastapi import HTTPException
            raise HTTPException(404, "tag not found")
        key = (pid, body.tag_id)
        attached = key not in state["tag_links"]
        state["tag_links"].add(key)
        return {"attached": attached}

    def fake_detach_tag(conn, pid, tid):
        key = (pid, tid)
        detached = key in state["tag_links"]
        state["tag_links"].discard(key)
        return {"detached": detached}

    monkeypatch.setattr(curation, "create_collection", fake_create_collection)
    monkeypatch.setattr(curation, "list_collections", fake_list_collections)
    monkeypatch.setattr(curation, "get_collection", fake_get_collection)
    monkeypatch.setattr(curation, "update_collection", fake_update_collection)
    monkeypatch.setattr(curation, "delete_collection", fake_delete_collection)
    monkeypatch.setattr(
        curation, "add_properties_to_collection", fake_add_properties,
    )
    monkeypatch.setattr(
        curation, "remove_property_from_collection", fake_remove_property,
    )
    monkeypatch.setattr(curation, "list_notes", fake_list_notes)
    monkeypatch.setattr(curation, "create_note", fake_create_note)
    monkeypatch.setattr(curation, "list_tags", fake_list_tags)
    monkeypatch.setattr(curation, "create_tag", fake_create_tag)
    monkeypatch.setattr(curation, "delete_tag", fake_delete_tag)
    monkeypatch.setattr(curation, "update_tag", fake_update_tag)
    monkeypatch.setattr(curation, "attach_tag", fake_attach_tag)
    monkeypatch.setattr(curation, "detach_tag", fake_detach_tag)

    return state


# --- collections -----------------------------------------------------------

def test_create_collection_returns_row(client, store):
    res = client.post("/collections", json={"name": "Vinohrady picks"})
    assert res.status_code == 200
    body = res.json()
    assert body["id"] == 1
    assert body["name"] == "Vinohrady picks"
    assert body["listing_count"] == 0
    assert body["description"] is None


def test_create_collection_duplicate_name_409(client, store):
    client.post("/collections", json={"name": "shortlist"})
    res = client.post("/collections", json={"name": "SHORTLIST"})
    assert res.status_code == 409


def test_create_collection_blank_name_422(client, store):
    res = client.post("/collections", json={"name": ""})
    assert res.status_code == 422


def test_list_collections_includes_listing_count(client, store):
    client.post("/collections", json={"name": "a"})
    client.post("/collections", json={"name": "b"})
    client.post("/collections/1/properties", json={"property_ids": [10, 11]})

    res = client.get("/collections")
    assert res.status_code == 200
    body = res.json()
    assert body["total"] == 2
    counts = {c["name"]: c["listing_count"] for c in body["data"]}
    assert counts["a"] == 2
    assert counts["b"] == 0


def test_get_collection_404_when_missing(client, store):
    res = client.get("/collections/999")
    assert res.status_code == 404


def test_get_collection_returns_metadata_and_properties(client, store):
    client.post("/collections", json={"name": "shortlist"})
    client.post("/collections/1/properties", json={"property_ids": [42, 43]})
    res = client.get("/collections/1")
    assert res.status_code == 200
    body = res.json()
    assert body["collection"]["name"] == "shortlist"
    assert {p["property_id"] for p in body["properties"]} == {42, 43}


def test_patch_rename_collection(client, store):
    client.post("/collections", json={"name": "old"})
    res = client.patch("/collections/1", json={"name": "new"})
    assert res.status_code == 200
    assert res.json()["name"] == "new"


def test_patch_rename_collection_conflict_409(client, store):
    client.post("/collections", json={"name": "a"})
    client.post("/collections", json={"name": "b"})
    res = client.patch("/collections/2", json={"name": "A"})
    assert res.status_code == 409


def test_delete_collection_404_when_missing(client, store):
    res = client.delete("/collections/999")
    assert res.status_code == 404


def test_delete_collection_succeeds(client, store):
    client.post("/collections", json={"name": "doomed"})
    res = client.delete("/collections/1")
    assert res.status_code == 200
    assert res.json() == {"deleted": True}
    assert client.get("/collections/1").status_code == 404


# --- collection_properties -------------------------------------------------

def test_add_properties_returns_added_skipped(client, store):
    client.post("/collections", json={"name": "x"})
    res = client.post(
        "/collections/1/properties", json={"property_ids": [1, 2, 3]},
    )
    assert res.status_code == 200
    assert res.json() == {"added": 3, "skipped": 0}

    # Re-adding 2 + new is skipped+added.
    res = client.post(
        "/collections/1/properties", json={"property_ids": [2, 3, 4]},
    )
    assert res.json() == {"added": 1, "skipped": 2}


def test_add_properties_empty_body_422(client, store):
    client.post("/collections", json={"name": "x"})
    res = client.post(
        "/collections/1/properties", json={"property_ids": []},
    )
    assert res.status_code == 422


def test_add_properties_to_missing_collection_404(client, store):
    res = client.post(
        "/collections/999/properties", json={"property_ids": [1]},
    )
    assert res.status_code == 404


def test_remove_property_from_collection(client, store):
    client.post("/collections", json={"name": "x"})
    client.post("/collections/1/properties", json={"property_ids": [10, 11]})
    res = client.delete("/collections/1/properties/10")
    assert res.status_code == 200
    assert res.json() == {"removed": True}
    res = client.delete("/collections/1/properties/10")  # idempotent
    assert res.json() == {"removed": False}


# --- notes -----------------------------------------------------------------

def test_create_note_round_trip(client, store):
    res = client.post("/properties/12345/notes", json={"body": "first"})
    assert res.status_code == 200
    assert res.json()["body"] == "first"
    client.post("/properties/12345/notes", json={"body": "second"})

    res = client.get("/properties/12345/notes")
    assert res.status_code == 200
    bodies = [n["body"] for n in res.json()["data"]]
    assert bodies == ["second", "first"]  # newest first


def test_create_note_records_origin_listing(client, store):
    res = client.post(
        "/properties/12345/notes",
        json={"body": "from the bazos ad", "origin_listing_id": 777},
    )
    assert res.status_code == 200
    assert res.json()["origin_listing_id"] == 777
    # Omitting it defaults to null.
    res = client.post("/properties/12345/notes", json={"body": "no origin"})
    assert res.json()["origin_listing_id"] is None


def test_create_note_blank_body_422(client, store):
    res = client.post("/properties/12345/notes", json={"body": ""})
    assert res.status_code == 422


def test_create_note_too_long_422(client, store):
    res = client.post(
        "/properties/12345/notes", json={"body": "x" * 4001},
    )
    assert res.status_code == 422


def test_notes_for_unknown_property_returns_empty(client, store):
    res = client.get("/properties/0/notes")
    assert res.status_code == 200
    assert res.json() == {"data": []}


# --- tags ------------------------------------------------------------------

def test_create_tag(client, store):
    res = client.post("/tags", json={"name": "hot", "color": "brick"})
    assert res.status_code == 200
    assert res.json()["color"] == "brick"
    assert res.json()["listing_count"] == 0


def test_create_tag_duplicate_name_409(client, store):
    client.post("/tags", json={"name": "hot", "color": "brick"})
    res = client.post("/tags", json={"name": "HOT", "color": "sage"})
    assert res.status_code == 409


def test_create_tag_invalid_color_422(client, store):
    res = client.post("/tags", json={"name": "hot", "color": "neon"})
    assert res.status_code == 422


def test_create_tag_missing_color_422(client, store):
    res = client.post("/tags", json={"name": "hot"})
    assert res.status_code == 422


def test_list_tags_alphabetic(client, store):
    client.post("/tags", json={"name": "zebra", "color": "slate"})
    client.post("/tags", json={"name": "alpha", "color": "copper"})
    res = client.get("/tags")
    names = [t["name"] for t in res.json()["data"]]
    assert names == ["alpha", "zebra"]


def test_attach_tag_idempotent(client, store):
    client.post("/tags", json={"name": "hot", "color": "brick"})
    res = client.post("/properties/42/tags", json={"tag_id": 1})
    assert res.status_code == 200
    assert res.json() == {"attached": True}
    res = client.post("/properties/42/tags", json={"tag_id": 1})
    assert res.json() == {"attached": False}


def test_attach_unknown_tag_404(client, store):
    res = client.post("/properties/42/tags", json={"tag_id": 999})
    assert res.status_code == 404


def test_detach_tag(client, store):
    client.post("/tags", json={"name": "hot", "color": "brick"})
    client.post("/properties/42/tags", json={"tag_id": 1})
    res = client.delete("/properties/42/tags/1")
    assert res.status_code == 200
    assert res.json() == {"detached": True}
    res = client.delete("/properties/42/tags/1")
    assert res.json() == {"detached": False}


def test_delete_tag_cascades_attachments(client, store):
    client.post("/tags", json={"name": "hot", "color": "brick"})
    client.post("/properties/42/tags", json={"tag_id": 1})
    res = client.delete("/tags/1")
    assert res.status_code == 200
    assert res.json() == {"deleted": True}
    # In-memory mock cascades; tag is gone.
    res = client.get("/tags")
    assert res.json()["data"] == []


def test_delete_unknown_tag_404(client, store):
    res = client.delete("/tags/999")
    assert res.status_code == 404


def test_patch_tag_rename(client, store):
    client.post("/tags", json={"name": "hot", "color": "brick"})
    res = client.patch("/tags/1", json={"name": "scorching"})
    assert res.status_code == 200
    assert res.json()["name"] == "scorching"
    assert res.json()["color"] == "brick"


def test_patch_tag_recolour(client, store):
    client.post("/tags", json={"name": "hot", "color": "brick"})
    res = client.patch("/tags/1", json={"color": "sage"})
    assert res.status_code == 200
    assert res.json()["color"] == "sage"
    assert res.json()["name"] == "hot"


def test_patch_tag_preserves_attachments(client, store):
    client.post("/tags", json={"name": "hot", "color": "brick"})
    client.post("/properties/42/tags", json={"tag_id": 1})
    res = client.patch("/tags/1", json={"name": "scorching", "color": "sage"})
    assert res.status_code == 200
    assert res.json()["listing_count"] == 1


def test_patch_tag_duplicate_name_409(client, store):
    client.post("/tags", json={"name": "hot", "color": "brick"})
    client.post("/tags", json={"name": "warm", "color": "sage"})
    res = client.patch("/tags/2", json={"name": "HOT"})
    assert res.status_code == 409


def test_patch_unknown_tag_404(client, store):
    res = client.patch("/tags/999", json={"name": "ghost"})
    assert res.status_code == 404


def test_patch_tag_invalid_color_422(client, store):
    client.post("/tags", json={"name": "hot", "color": "brick"})
    res = client.patch("/tags/1", json={"color": "neon"})
    assert res.status_code == 422


# --- the REAL helpers, against a predicate-scripted connection ---------------
# (route tests above stub the handlers; these exercise the persistence functions.)

_T0 = datetime(2026, 5, 10)
# _COLLECTION_PROJECTION: id, name, description, created_at, updated_at, listing_count,
# monitoring_enabled, notify_channels, is_system.
_COLL = (1, "x", None, _T0, _T0, 0, False, [], False)
_SYSTEM = (6, "monitoring", None, _T0, _T0, 3, True, ["in_app"], True)
_COLL_PAYLOAD = {
    "id": 1, "name": "x", "description": None,
    "created_at": "2026-05-10T00:00:00", "updated_at": "2026-05-10T00:00:00",
    "listing_count": 0, "monitoring_enabled": False, "notify_channels": [], "is_system": False,
}


def _probes(conn: ScriptConn) -> list[str]:
    """Plain SELECTs that touch a curation table (the visibility probes W1 deleted)."""
    return [q for q, _ in conn.executed
            if q.startswith("SELECT") and ("collections" in q or "property_notes" in q)]


def test_create_collection_returns_the_one_collection_shape():
    conn = ScriptConn([(lambda q: "INSERT INTO collections" in q, [_COLL])])
    out = curation.create_collection(conn, _s.CreateCollectionIn(name="x"), account_id=_ACCT)
    assert out == _COLL_PAYLOAD
    sql = conn.executed[0][0]
    assert "RETURNING c.id, c.name" in sql and "AS listing_count" in sql


def test_list_and_get_map_the_same_shape_as_create():
    conn = ScriptConn([(lambda q: "FROM collections c" in q, [_COLL])])
    assert curation.list_collections(conn) == {"data": [_COLL_PAYLOAD], "total": 1}
    assert curation.get_collection(conn, 1)["collection"] == _COLL_PAYLOAD


# --- account stamping (W-1c) ----------------------------------------------
#
# `collections` and `tags` are top-level: no parent trigger fills account_id
# (unlike collection_properties / property_tags, mig 292) and no DEFAULT. On
# the tenant connection an unstamped INSERT fails the WITH CHECK closed, so
# the column has to be in the statement AND carry the caller's account — a
# regression here is silent (rows land unreachable, or worse, unscoped).

def test_create_collection_stamps_account_id():
    conn = ScriptConn([(lambda q: "INSERT INTO collections" in q, [_COLL])])
    curation.create_collection(conn, _s.CreateCollectionIn(name="x"), account_id="acct-1")
    sql, params = conn.executed[0]
    assert "account_id" in sql
    assert "acct-1" in params


def test_create_tag_stamps_account_id_and_returns_the_one_tag_shape():
    conn = ScriptConn([(lambda q: "INSERT INTO tags" in q, [(1, "t", "sage", _T0, 0)])])
    out = curation.create_tag(conn, _s.CreateTagIn(name="t", color="sage"), account_id="acct-1")
    assert out == {"id": 1, "name": "t", "color": "sage",
                   "created_at": "2026-05-10T00:00:00", "listing_count": 0}
    sql, params = conn.executed[0]
    assert "account_id" in sql
    assert "acct-1" in params


def test_create_tag_helper_409_on_unique_violation():
    conn = ScriptConn([(lambda q: "INSERT INTO tags" in q, psycopg.errors.UniqueViolation("dup"))])
    with pytest.raises(fastapi.HTTPException) as exc:
        curation.create_tag(conn, _s.CreateTagIn(name="hot", color="brick"), account_id=_ACCT)
    assert exc.value.status_code == 409


# --- the write is the gate -------------------------------------------------
# A SELECT proves only that a row is VISIBLE; once collections can be shared (W4) visibility is
# wider than ownership, so each write is its own gate: rowcount 0 -> 404 before any side effect, and a
# refusal raised inside the transaction rolls the statement back.


def test_update_collection_is_one_update_returning_the_collection():
    conn = ScriptConn([(lambda q: q.startswith("UPDATE collections"), [_COLL])])
    out = curation.update_collection(conn, 1, _s.UpdateCollectionIn(name="x", description="d"))
    assert out == _COLL_PAYLOAD
    [(sql, params)] = conn.executed
    assert "name = CASE WHEN c.is_system THEN c.name ELSE coalesce(%s, c.name) END" in sql
    assert "RETURNING c.id, c.name" in sql
    assert params == ("x", "d", None, None, 1)
    assert conn.tx == ["commit"]


def test_update_collection_not_owned_is_404_without_a_probe():
    conn = ScriptConn([])
    with pytest.raises(fastapi.HTTPException) as exc:
        curation.update_collection(conn, 1, _s.UpdateCollectionIn(description="d"))
    assert exc.value.status_code == 404
    assert len(conn.executed) == 1 and conn.executed[0][0].startswith("UPDATE collections")
    assert _probes(conn) == []
    assert conn.tx == ["rollback"]


def test_rename_system_collection_is_refused_409_and_rolled_back():
    conn = ScriptConn([(lambda q: q.startswith("UPDATE collections"), [_SYSTEM])])
    with pytest.raises(fastapi.HTTPException) as exc:
        curation.update_collection(conn, 6, _s.UpdateCollectionIn(name="renamed"))
    assert exc.value.status_code == 409
    assert len(conn.executed) == 1 and _probes(conn) == []
    # The SET keeps a system name, so the RETURNING row (not the request) decides the 409.
    assert "CASE WHEN c.is_system THEN c.name" in conn.executed[0][0]
    assert conn.tx == ["rollback"]


def test_system_collection_keeps_its_name_and_takes_other_fields():
    """The SPA's edit form always resends the name; an unchanged one is no rename."""
    conn = ScriptConn([(lambda q: q.startswith("UPDATE collections"), [_SYSTEM])])
    out = curation.update_collection(
        conn, 6, _s.UpdateCollectionIn(name="monitoring", monitoring_enabled=True))
    assert out["name"] == "monitoring" and out["is_system"] is True
    assert conn.tx == ["commit"]


def test_update_collection_duplicate_name_is_409():
    conn = ScriptConn([(lambda q: q.startswith("UPDATE collections"),
                        psycopg.errors.UniqueViolation("dup"))])
    with pytest.raises(fastapi.HTTPException) as exc:
        curation.update_collection(conn, 1, _s.UpdateCollectionIn(name="taken"))
    assert exc.value.status_code == 409
    assert exc.value.detail == "collection name already exists"


def test_update_collection_empty_patch_is_422_and_touches_nothing():
    conn = ScriptConn([])
    with pytest.raises(fastapi.HTTPException) as exc:
        curation.update_collection(conn, 1, _s.UpdateCollectionIn())
    assert exc.value.status_code == 422
    assert conn.executed == []


def test_delete_collection_is_one_delete():
    conn = ScriptConn([(lambda q: q.startswith("DELETE FROM collections"), [(False,)])])
    assert curation.delete_collection(conn, 1) == {"deleted": True}
    [(sql, params)] = conn.executed
    assert "RETURNING is_system" in sql and params == (1,)
    assert conn.tx == ["commit"]


def test_delete_collection_not_owned_is_404_not_a_false_deleted():
    conn = ScriptConn([])
    with pytest.raises(fastapi.HTTPException) as exc:
        curation.delete_collection(conn, 1)
    assert exc.value.status_code == 404
    assert len(conn.executed) == 1 and _probes(conn) == []
    assert conn.executed[0][0].startswith("DELETE FROM collections")


def test_delete_system_collection_is_refused_409_and_rolled_back():
    conn = ScriptConn([(lambda q: q.startswith("DELETE FROM collections"), [(True,)])])
    with pytest.raises(fastapi.HTTPException) as exc:
        curation.delete_collection(conn, 6)
    assert exc.value.status_code == 409
    assert len(conn.executed) == 1
    assert conn.tx == ["rollback"]


def test_add_properties_not_owned_is_404_before_any_insert():
    conn = ScriptConn([(lambda q: "RECURSIVE chain" in q, [(10, 10)])])
    with pytest.raises(fastapi.HTTPException) as exc:
        curation.add_properties_to_collection(
            conn, 1, _s.AddPropertiesToCollectionIn(property_ids=[10]))
    assert exc.value.status_code == 404
    assert [q for q, _ in conn.executed] == [
        "UPDATE collections SET updated_at = now() WHERE id = %s"]
    assert conn.tx == ["rollback"]


def test_update_note_empty_patch_is_422_and_touches_nothing():
    conn = ScriptConn([])
    with pytest.raises(fastapi.HTTPException) as exc:
        curation.update_note(conn, 42, 5, _s.UpdateNoteIn())
    assert exc.value.status_code == 422
    assert conn.executed == []


def test_update_note_is_one_update_and_no_probe():
    conn = ScriptConn([
        (lambda q: "RECURSIVE chain" in q, [(42, 42)]),
        (lambda q: q.startswith("UPDATE property_notes"),
         [(5, 42, "edited", None, _T0, _T0)]),
    ])
    out = curation.update_note(conn, 42, 5, _s.UpdateNoteIn(body="edited"))
    assert out["body"] == "edited"
    ups = [q for q, _ in conn.executed if q.startswith("UPDATE property_notes")]
    assert len(ups) == 1 and "WHERE id = %s AND property_id = %s RETURNING" in ups[0]
    assert _probes(conn) == []


def test_update_note_not_owned_is_404():
    conn = ScriptConn([(lambda q: "RECURSIVE chain" in q, [(42, 42)])])
    with pytest.raises(fastapi.HTTPException) as exc:
        curation.update_note(conn, 42, 5, _s.UpdateNoteIn(body="edited"))
    assert exc.value.status_code == 404
    assert _probes(conn) == []
    assert sum(q.startswith("UPDATE property_notes") for q, _ in conn.executed) == 1


def test_list_notes_helper_orders_newest_first():
    conn = ScriptConn([
        (lambda q: "RECURSIVE chain" in q, [(42, 42)]),
        (lambda q: "FROM property_notes" in q, [
            (2, 42, "second", None, "2026-05-10T00:00:02+00:00", None),
            (1, 42, "first",  777,  "2026-05-10T00:00:01+00:00", None),
        ]),
    ])
    out = curation.list_notes(conn, property_id=42)
    assert [n["body"] for n in out["data"]] == ["second", "first"]
    assert out["data"][1]["origin_listing_id"] == 777
    assert "ORDER BY created_at DESC" in conn.executed[1][0]


# --- merged-away redirect --------------------------------------------------
# (the survivor-redirect wiring — resolve_active_property_id — on the real functions.)


def test_create_note_redirects_merged_away_to_survivor():
    conn = ScriptConn([
        (lambda q: "RECURSIVE chain" in q, [(99, 42)]),  # 99 merged into active 42
        (lambda q: "INSERT INTO property_notes" in q,
         [(1, 42, "hi", None, datetime(2026, 1, 1), None)]),
    ])
    out = curation.create_note(conn, 99, _s.CreateNoteIn(body="hi"), account_id=_ACCT)
    assert out["property_id"] == 42
    ins = [p for q, p in conn.executed if "INSERT INTO property_notes" in q]
    assert ins and ins[0][0] == 42  # inserted onto the survivor, not 99


def test_create_note_no_survivor_is_404():
    conn = ScriptConn([(lambda q: "RECURSIVE chain" in q, [])])
    with pytest.raises(fastapi.HTTPException) as ei:
        curation.create_note(conn, 7, _s.CreateNoteIn(body="x"), account_id=_ACCT)
    assert ei.value.status_code == 404


def test_add_properties_redirects_merged_away_to_survivor():
    conn = ScriptConn([
        (lambda q: "UPDATE collections SET updated_at" in q, [(1,)]),  # the owner's gate
        (lambda q: "RECURSIVE chain" in q, [(99, 42)]),  # input 99 -> survivor 42
        (lambda q: "INSERT INTO collection_properties" in q, [(1,)]),  # rowcount 1
    ])
    out = curation.add_properties_to_collection(
        conn, 1, _s.AddPropertiesToCollectionIn(property_ids=[99]),
    )
    assert out["added"] == 1
    ins = [p for q, p in conn.executed if "INSERT INTO collection_properties" in q]
    assert ins and ins[0][1] == [42]  # survivor id inserted, not 99
    bumps = [q for q, _ in conn.executed if q.startswith("UPDATE collections")]
    assert len(bumps) == 1 and conn.executed[0][0] == bumps[0]  # one bump, and it is the gate


# --- the REMOVE half of each affordance resolves too -----------------------
# 426fa575 hardened only the add half, so the same cached id could create a
# membership it could then never remove: merge re-points the row onto the
# survivor, the DELETE keyed on the retired id matched nothing, and the handler
# reported {"removed": false} with HTTP 200. Enforced tree-wide by
# tests/api/test_property_anchored_write_census.py.


def test_remove_property_from_collection_redirects_merged_away_to_survivor():
    conn = ScriptConn([
        (lambda q: "RECURSIVE chain" in q, [(99, 42)]),  # 99 merged into active 42
        (lambda q: "DELETE FROM collection_properties" in q, [(1,)]),  # rowcount 1
        (lambda q: "UPDATE collections SET updated_at" in q, []),
    ])
    assert curation.remove_property_from_collection(conn, 1, 99) == {"removed": True}
    dels = [p for q, p in conn.executed if "DELETE FROM collection_properties" in q]
    assert dels and dels[0] == (1, 42)  # the survivor's row, not 99's phantom
    # the bump is still gated on a real removal
    assert any("UPDATE collections SET updated_at" in q for q, _ in conn.executed)


def test_remove_property_from_collection_unresolvable_id_stays_idempotent():
    """An id with no active survivor must NOT become a 404.

    A DELETE stays forgiving where an INSERT 4xx's — no caller reads the boolean
    (CollectionSaveMenu only invalidates), so a new 4xx would be a contract change
    for a case that already means "nothing to remove".
    """
    conn = ScriptConn([(lambda q: "RECURSIVE chain" in q, [])])
    assert curation.remove_property_from_collection(conn, 1, 7) == {"removed": False}
    dels = [p for q, p in conn.executed if "DELETE FROM collection_properties" in q]
    assert dels and dels[0] == (1, 7)  # falls through to the raw id, still runs


def test_detach_tag_redirects_merged_away_to_survivor():
    conn = ScriptConn([
        (lambda q: "RECURSIVE chain" in q, [(99, 42)]),
        (lambda q: "DELETE FROM property_tags" in q, [(1,)]),
    ])
    assert curation.detach_tag(conn, 99, 3) == {"detached": True}
    dels = [p for q, p in conn.executed if "DELETE FROM property_tags" in q]
    assert dels and dels[0] == (42, 3)


def test_update_note_redirects_merged_away_to_survivor():
    conn = ScriptConn([
        (lambda q: "RECURSIVE chain" in q, [(99, 42)]),
        (lambda q: "UPDATE property_notes" in q,
         [(5, 42, "edited", None, datetime(2026, 1, 1), datetime(2026, 1, 2))]),
    ])
    out = curation.update_note(conn, 99, 5, _s.UpdateNoteIn(body="edited"))
    assert out["property_id"] == 42
    ups = [p for q, p in conn.executed if "UPDATE property_notes" in q]
    assert ups and ups[0] == ("edited", "edited", 5, 42)


def test_list_notes_follows_a_merged_away_id_to_the_survivor():
    """A tab opened before a merge reads the survivor's notes, not an empty list:
    the merge moved them there, and every note write already follows the id."""
    conn = ScriptConn([
        (lambda q: "RECURSIVE chain" in q, [(99, 42)]),
        (lambda q: "FROM property_notes" in q,
         [(5, 42, "on the survivor", None, datetime(2026, 1, 1), None)]),
    ])
    out = curation.list_notes(conn, 99)
    assert [n["property_id"] for n in out["data"]] == [42]
    reads = [p for q, p in conn.executed if "FROM property_notes" in q]
    assert reads == [(42,)]


def test_list_notes_unresolvable_id_reads_its_own_id():
    conn = ScriptConn([(lambda q: "RECURSIVE chain" in q, [])])
    assert curation.list_notes(conn, 7) == {"data": []}
    reads = [p for q, p in conn.executed if "FROM property_notes" in q]
    assert reads == [(7,)]


def test_delete_note_redirects_merged_away_to_survivor():
    """The 404 this used to raise was the misleading kind: the note was alive on
    the survivor, and only the (id, property_id) pair was unsatisfiable."""
    conn = ScriptConn([
        (lambda q: "RECURSIVE chain" in q, [(99, 42)]),
        (lambda q: "DELETE FROM property_notes" in q, [(1,)]),
    ])
    assert curation.delete_note(conn, 99, 5) == {"deleted": True}
    dels = [p for q, p in conn.executed if "DELETE FROM property_notes" in q]
    assert dels and dels[0] == (5, 42)


def test_get_collection_properties_repr_join_uses_the_surrogate(monkeypatch):
    # Pre-Gate-2 hardening (mirrors #873's Browse fix): the repr listing must be
    # joined on repr_listing_ref_id (listings.id), not the legacy sreality_id
    # handle, or a non-sreality repr silently blanks `source` on the collection page.
    # W3: joined to listings_public, not to `listings`. This route runs under
    # `SET LOCAL ROLE authenticated`, where the bare table is RLS-invisible (no
    # policy since migration 001) and `source` came back NULL for every row.
    monkeypatch.setattr(
        curation, "_fetch_collection", lambda conn, cid: {"id": cid, "name": "x"},
    )
    conn = ScriptConn([])
    out = curation.get_collection(conn, 1)
    assert out["properties"] == []
    props_sql = next(q for q, _ in conn.executed if "collection_properties cp" in q)
    assert "LEFT JOIN listings_public rl ON rl.id = p.repr_listing_ref_id" in props_sql
    assert "sreality_id = p.repr_listing_id" not in props_sql
    # The owner-rights view, never the RLS-dark table.
    assert "JOIN listings rl" not in props_sql


# --- the no-account posture (W4) -------------------------------------------


def test_curation_writes_without_an_account_are_400(client, store, monkeypatch):
    """A membership-less JWT is ONE loud 400 on every top-level curation write.

    These three routes used to fall back to `deps.SYSTEM_ACCOUNT_ID`, which
    CANNOT work: migration 290 gives collections / tags / property_notes a plain
    `with check (account_id in (select current_account_ids()))` with no SYSTEM
    arm (that exists only in 291/292, for the estimation tables), so the fallback
    wrote a value RLS is guaranteed to reject — turning a diagnosable "you have
    no account" into an opaque 500. Reads stay RLS-only, hence 200.
    """
    monkeypatch.setattr(tenant_pool, "resolve_account_id", lambda conn, claims: None)

    assert client.get("/collections").status_code == 200

    writes = [
        ("post", "/collections", {"name": "test"}),
        ("post", "/properties/1/notes", {"body": "smoke"}),
        ("post", "/tags", {"name": "hot", "color": "brick"}),
    ]
    for method, path, body in writes:
        res = getattr(client, method)(path, json=body)
        assert res.status_code == 400, f"{method.upper()} {path} -> {res.status_code}"
        assert res.json()["detail"] == "no account for caller"
