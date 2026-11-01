"""Apply and Undo on the replayed schema (#2072, ADR-0004): the real routes, row-level security, identity functions and
definer marker, logged in as the real `tenant_pool` role, since the marker refuses any other login. Runs in CI's migrations job."""

from __future__ import annotations

import json
import threading
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import psycopg
import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from api import dependencies as deps  # noqa: E402
from api import main as api_main  # noqa: E402
from api import notifications as nf  # noqa: E402
from api import schemas as s  # noqa: E402
from api import tenant_pool  # noqa: E402
from api.agents import proposals  # noqa: E402
from api import curation  # noqa: E402
from tests import test_agent_runs_live as ledger  # noqa: E402
from tests._live_property import OP, REQUIRED_DB, _advert, _property, db_url  # noqa: E402
from toolkit.property_identity import merge_property_set  # noqa: E402

pytestmark = REQUIRED_DB

svc = ledger.svc
people = ledger.people

COLLECTION = "create_collection"
NOTE = "add_note"
CARD = "add_pipeline_card"
WATCHDOG = "create_watchdog"
HOSTILE = '<img src=x onerror=alert(1)> [click](javascript:alert(1))'
# The curation an Apply can move, whole, so an UPDATE shows as an INSERT does, and Undo's "exactly what it added" is equality.
CURATED = ("collections", "collection_properties", "property_notes", "property_pipeline", "notification_subscriptions")


@pytest.fixture(scope="module")
def pool_url(svc: psycopg.Connection) -> str:
    """A login as the production pool's own role: NOINHERIT, no grants of its own, only what SET ROLE gives it."""
    svc.execute("ALTER ROLE tenant_pool LOGIN PASSWORD 'ci-tenant-pool'")
    parts = urlsplit(db_url())
    return urlunsplit(parts._replace(netloc=f"tenant_pool:ci-tenant-pool@{parts.hostname}:{parts.port}"))


@dataclass
class World:
    svc: psycopg.Connection
    people: dict[str, Any]
    pids: list[int]
    client: Callable[[uuid.UUID], TestClient]

    def run(self, *actions: dict[str, Any], key: str = "a", account: uuid.UUID | None = None) -> int:
        account = account or self.people[f"{key}_acc"]
        row = self.svc.execute(
            "INSERT INTO agent_runs (account_id, principal_user_id, agent, definition_sha, tool_set_sha, commit_sha, provider, "
            "model, input, status, proposals) VALUES (%s, %s, 'probe', 'd', 't', 'c', 'anthropic', 'm', '{}', 'succeeded', %s) "
            "RETURNING id", (account, self.people[key], json.dumps(list(actions)))).fetchone()
        return int(row[0])

    def post(self, run_id: int, verb: str, index: int = 0, key: str = "a") -> Any:
        return self.client(self.people[key]).post(f"/runs/{run_id}/proposals/{index}/{verb}")

    def view(self, run_id: int, index: int = 0, key: str = "a") -> dict[str, Any]:
        response = self.client(self.people[key]).get(f"/runs/{run_id}/proposals")
        assert response.status_code == 200, response.text
        return response.json()["data"][index]

    def rows(self) -> dict[str, str | None]:
        return {t: self.svc.execute(f"SELECT md5(string_agg(x::text, '' ORDER BY x::text)) FROM {t} x").fetchone()[0]  # noqa: S608 - fixed
                for t in CURATED}

    def marker(self, run_id: int, index: int = 0) -> dict[str, Any] | None:
        with self.svc.cursor(row_factory=psycopg.rows.dict_row) as cur:
            cur.execute("SELECT * FROM agent_proposal_applies WHERE run_id = %s AND proposal_index = %s", (run_id, index))
            return cur.fetchone()

    def one(self, sql: str, *params: Any) -> Any:
        row = self.svc.execute(sql, params).fetchone()
        return row[0] if row else None

    def merge(self, survivor: int, retired: int) -> None:
        merge_property_set(self.svc, [survivor, retired], source="operator", reason="manual_subset", decided_by=OP)


@pytest.fixture()
def w(svc: psycopg.Connection, people: dict[str, Any], pool_url: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[World]:
    monkeypatch.setenv("TENANT_POOL_DB_URL", pool_url)
    with svc.cursor() as cur:
        pids = [_property(cur) for _ in range(5)]
        ads = [_advert(cur, pid, source="idnes", condition="dobry") for pid in pids]

    def client(user: uuid.UUID) -> TestClient:
        api_main.app.dependency_overrides[deps.verify_jwt] = lambda: {"sub": str(user), "role": "authenticated"}
        return TestClient(api_main.app, raise_server_exceptions=False)

    try:
        yield World(svc, people, pids, client)
    finally:
        api_main.app.dependency_overrides.pop(deps.verify_jwt, None)
        svc.execute("DELETE FROM agent_runs")
        svc.execute("DELETE FROM notification_dispatches WHERE property_id = ANY(%s)", (pids,))
        svc.execute("DELETE FROM property_merge_carries WHERE to_property_id = ANY(%s) OR from_property_id = ANY(%s)", (pids, pids))
        svc.execute("DELETE FROM property_merge_events WHERE survivor_property_id = ANY(%s) OR retired_property_id = ANY(%s)", (pids, pids))
        svc.execute("DELETE FROM property_attachments WHERE property_id = ANY(%s)", (pids,))
        svc.execute("DELETE FROM listings WHERE id = ANY(%s)", (ads,))
        svc.execute("DELETE FROM properties WHERE id = ANY(%s)", (pids,))


def _collection(*pids: int, name: str = "Výběr") -> dict[str, Any]:
    return {"entry": COLLECTION, "args": {"name": name, "description": "Pro klienta", "property_ids": list(pids)}}


def _note(pid: int, body: str = "Zavolat makléři") -> dict[str, Any]:
    return {"entry": NOTE, "args": {"property_id": pid, "body": body}}


def _card(pid: int) -> dict[str, Any]:
    return {"entry": CARD, "args": {"property_id": pid}}


def _watchdog(**filters: Any) -> dict[str, Any]:
    return {"entry": WATCHDOG, "args": {"name": "Brno byty", "filters": {"category_type": "prodej", **filters}}}


def _applied(w: World, *actions: dict[str, Any], **who: Any) -> int:
    run_id = w.run(*actions, **who)
    for i in range(len(actions)):
        response = w.post(run_id, "apply", i)
        assert response.status_code == 200, response.text
    return run_id


def _collection_id(w: World, run_id: int, index: int = 0) -> int:
    return int(w.marker(run_id, index)["effect"]["undo"]["collection_id"])


def _refused_undo(w: World, run_id: int, says: str) -> None:
    """The view hides Undo with the reason, and a call anyway gets it, changes nothing and leaves the proposal applied."""
    view = w.view(run_id)
    assert view["undo"]["available"] is False and says in view["undo"]["says"]
    before = w.rows()
    response = w.post(run_id, "undo")
    assert response.status_code == 409 and says in response.json()["detail"]
    assert w.rows() == before
    assert w.marker(run_id)["undone_at"] is None


# --- the acceptance criteria ---------------------------------------------------------------------------------------

@pytest.mark.parametrize("action", ["collection", "note", "viewing", "card", "watchdog"])
def test_a_double_apply_writes_once_and_undo_removes_exactly_what_it_added(w: World, action: str) -> None:
    a, b = w.pids[:2]
    proposal = {"collection": _collection(a, b), "note": _note(a), "card": _card(a), "watchdog": _watchdog(max_price_czk=6_000_000),
                "viewing": {"entry": NOTE, "args": {"property_id": a, "viewing_at": "2026-12-01T10:00:00+01:00"}}}[action]
    before = w.rows()
    run_id = w.run(proposal)

    first = w.post(run_id, "apply")
    assert first.status_code == 200 and first.json()["state"] == "applied"
    after_one = w.rows()
    assert after_one != before
    again = w.post(run_id, "apply")
    assert again.status_code == 200 and again.json()["state"] == "applied"
    assert w.rows() == after_one, "the second Apply wrote nothing"

    undone = w.post(run_id, "undo")
    assert undone.status_code == 200 and undone.json()["state"] == "undone"
    if action == "watchdog":  # A deleted watchdog is retired, never deleted (rule 16).
        assert w.one("SELECT count(*) FROM notification_subscriptions WHERE account_id = %s AND retired_at IS NULL",
                     w.people["a_acc"]) == 0
    else:
        assert w.rows() == before, "exactly what it added"
    assert w.post(run_id, "undo").json()["state"] == "undone", "a second Undo does nothing"
    assert w.post(run_id, "apply").status_code == 409, "a proposal applies once"


def test_two_concurrent_applies_write_once(w: World) -> None:
    run_id = w.run(_collection(*w.pids[:2]))
    barrier = threading.Barrier(4)
    results: list[Any] = []

    def apply() -> None:
        client = w.client(w.people["a"])
        barrier.wait()
        results.append(client.post(f"/runs/{run_id}/proposals/0/apply"))

    threads = [threading.Thread(target=apply) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert [r.status_code for r in results] == [200] * 4, [r.text for r in results]
    assert w.one("SELECT count(*) FROM collections WHERE account_id = %s AND name = 'Výběr'", w.people["a_acc"]) == 1
    assert w.one("SELECT count(*) FROM agent_proposal_applies WHERE run_id = %s", run_id) == 1


def test_another_accounts_token_gets_404_and_writes_nothing(w: World) -> None:
    run_id = _applied(w, _note(w.pids[0]))
    open_run = w.run(_collection(w.pids[0]))
    before = w.rows()
    assert w.post(open_run, "apply", key="b").status_code == 404
    assert w.post(run_id, "undo", key="b").status_code == 404
    assert w.client(w.people["b"]).get(f"/runs/{open_run}/proposals").status_code == 404
    assert w.rows() == before
    assert w.marker(open_run) is None and w.marker(run_id)["undone_at"] is None


@pytest.mark.parametrize("verb", ["apply", "undo"])
def test_act_as_is_refused_even_under_an_edit_share(w: World, verb: str) -> None:
    w.svc.execute("INSERT INTO pipeline_shares (account_id, recipient_account_id, mode) VALUES (%s, %s, 'edit')",
                  (w.people["a_acc"], w.people["b_acc"]))
    run_id = w.run(_note(w.pids[0]))
    before = w.rows()
    response = w.client(w.people["b"]).post(f"/runs/{run_id}/proposals/0/{verb}",
                                            headers={"X-Act-As": str(w.people["a_acc"])})
    assert response.status_code == 403
    assert w.rows() == before and w.marker(run_id) is None


def test_a_crash_between_the_write_and_its_marker_leaves_neither(w: World, monkeypatch: pytest.MonkeyPatch) -> None:
    """One tenant transaction: nothing commits, so the proposal stays open and the next Apply writes once."""
    run_id = w.run(_collection(w.pids[0]))
    before = w.rows()
    with monkeypatch.context() as crash:
        crash.setattr(proposals, "_SETTLE", "SELECT 1/0")
        assert w.post(run_id, "apply").status_code == 500
    assert w.rows() == before and w.marker(run_id) is None
    assert w.post(run_id, "apply").status_code == 200
    assert w.one("SELECT count(*) FROM collections WHERE account_id = %s AND name = 'Výběr'", w.people["a_acc"]) == 1


def test_a_refused_write_leaves_the_proposal_open_to_try_again(w: World) -> None:
    taken = w.one("INSERT INTO collections (account_id, name) VALUES (%s, 'výběr') RETURNING id", w.people["a_acc"])
    run_id = w.run(_collection(w.pids[0]))
    assert "already have a collection" in w.view(run_id)["before"][0]
    refused = w.post(run_id, "apply")
    assert refused.status_code == 409 and "already exists" in refused.json()["detail"]
    assert w.marker(run_id) is None and w.view(run_id)["state"] == "open"
    w.svc.execute("UPDATE collections SET name = 'Staré' WHERE id = %s", (taken,))
    assert w.post(run_id, "apply").status_code == 200


def test_the_marker_is_no_browsers_to_write_and_only_the_pools_login_may_call_it(w: World) -> None:
    run_id = w.run(_note(w.pids[0]))
    claims = json.dumps({"sub": str(w.people["a"]), "role": "authenticated"})
    with ledger._connect() as conn:  # Not `tenant_pool`, as PostgREST's `authenticator` is not.
        conn.autocommit = False
        conn.execute("SET LOCAL ROLE authenticated")
        conn.execute("SELECT set_config('request.jwt.claims', %s, true)", (claims,))
        for sql in (f"SELECT agent_proposal_claim({run_id}, 0, '{NOTE}')",
                    f"SELECT agent_proposal_settle({run_id}, 0, '{{}}')",
                    f"SELECT agent_proposal_undo({run_id}, 0)",
                    f"SELECT agent_proposal_account({run_id})",
                    f"INSERT INTO agent_proposal_applies (run_id, account_id, proposal_index, entry, applied_by) VALUES ({run_id}, "
                    f"'{w.people['a_acc']}', 0, '{NOTE}', '{w.people['a']}')",
                    "UPDATE agent_proposal_applies SET undone_at = now()",
                    "DELETE FROM agent_proposal_applies",
                    "UPDATE agent_runs SET proposals = '[]'"):
            with conn.transaction(savepoint_name="probe", force_rollback=True), pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(sql)
    assert w.marker(run_id) is None


@pytest.mark.parametrize("bad", ["narrowed", "acting", "another_run", "not_succeeded", "wrong_entry"])
def test_the_marker_refuses_a_run_identity_and_what_the_run_does_not_hold(w: World, bad: str) -> None:
    run_id = w.run(_note(w.pids[0]))
    theirs = w.run(_note(w.pids[0]), key="b")
    claims: dict[str, Any] = {"sub": str(w.people["a"]), "role": "authenticated"}
    target, entry, act_as = run_id, NOTE, None
    if bad == "narrowed":
        claims["run_account_id"] = str(w.people["a_acc"])
    elif bad == "acting":
        act_as = w.people["b_acc"]
    elif bad == "another_run":
        target = theirs
    elif bad == "not_succeeded":
        w.svc.execute("UPDATE agent_runs SET status = 'failed' WHERE id = %s", (run_id,))
    else:
        entry = CARD
    with pytest.raises((psycopg.errors.InsufficientPrivilege, psycopg.errors.NoDataFound)):
        with tenant_pool.tenant_transaction(claims, act_as=act_as, method="POST") as conn:
            conn.execute("SELECT agent_proposal_claim(%s, 0, %s)", (target, entry))
    assert w.marker(target) is None


def test_a_second_membership_writes_into_the_runs_account_not_the_oldest(w: World) -> None:
    w.svc.execute("INSERT INTO account_members (user_id, account_id) VALUES (%s, %s)", (w.people["a"], w.people["b_acc"]))
    run_id = _applied(w, _collection(w.pids[0]), account=w.people["b_acc"])
    assert w.one("SELECT account_id FROM collections WHERE id = %s", _collection_id(w, run_id)) == w.people["b_acc"]
    assert w.marker(run_id)["account_id"] == w.people["b_acc"]


@pytest.mark.parametrize("proposal, family", [
    (lambda pid: _collection(pid), "collections"), (lambda pid: _note(pid), "notes"), (lambda pid: _card(pid), "board"),
])
def test_apply_and_undo_each_move_the_curation_clock(w: World, proposal: Callable[[int], dict[str, Any]], family: str) -> None:
    def counter() -> int:
        return w.one("SELECT coalesce(max(counter), 0) FROM curation_clock WHERE account_id = %s AND family = %s",
                     w.people["a_acc"], family)

    start = counter()
    run_id = _applied(w, proposal(w.pids[0]))
    applied = counter()
    assert applied == start + 1
    assert w.post(run_id, "undo").status_code == 200
    assert counter() == applied + 1


# --- what is drawn -------------------------------------------------------------------------------------------------

def test_the_diff_is_drawn_from_our_data_with_the_models_words_apart(w: World) -> None:
    pid = w.pids[0]
    w.svc.execute("UPDATE listings SET disposition = '2+kk' WHERE property_id = %s", (pid,))
    w.svc.execute("SELECT 1")
    with w.svc.cursor() as cur:
        ledger._recompute(cur, pid)
    run_id = w.run({"entry": COLLECTION, "args": {"name": HOSTILE, "description": HOSTILE, "property_ids": [pid]}}, _note(pid, HOSTILE))
    collection, note = w.view(run_id, 0), w.view(run_id, 1)
    assert collection["state"] == "open" and collection["title"] == "New collection"
    assert collection["before"] == ["No such collection."] and collection["after"] == ["A new collection holding 1 property."]
    assert {t["label"]: t["text"] for t in collection["agent_text"]} == {"Name": HOSTILE, "Description": HOSTILE}
    assert collection["properties"][0]["property_id"] == pid and "70 m²" in collection["properties"][0]["title"]
    for view in (collection, note):
        assert HOSTILE not in json.dumps({k: view[k] for k in ("title", "before", "after", "properties", "filters", "answer")})
    assert note["agent_text"] == [{"label": "Note", "text": HOSTILE}]
    assert note["before"] == ["0 notes you can read on this property."]


def test_a_watchdogs_places_come_from_ruian_not_the_model(w: World) -> None:
    code = 554_782
    version = w.one("INSERT INTO registry_versions (label, kind, source_date, artifact_urls, proj_version, proj_pipeline) "
                    "VALUES (%s, 'baseline', '2026-09-01', '{}', 'test', 'test') RETURNING id", uuid.uuid4().hex)
    w.svc.execute("INSERT INTO ruian_admin_units (level, code, name, name_norm, path, display_path, valid_from, first_version_id, "
                  "last_version_id) VALUES ('obec', %s, 'Testov', 'testov', 'obec_554782', 'Testov', '2020-01-01', %s, %s)",
                  (code, version, version))
    real = w.one("SELECT name FROM ruian_place_names_public WHERE level = 'obec' AND code = %s", code)
    lie = {"name": "Praha 1 (hezky)", "level": "obec", "id": code}
    run_id = w.run(_watchdog(districts=[lie], max_price_czk=6_000_000))
    filters = {f["label"]: f["value"] for f in w.view(run_id)["filters"]}
    assert filters["In"] == real and filters["max price czk"] == "6000000 CZK"
    assert w.post(run_id, "apply").status_code == 200
    stored = w.one("SELECT filter_spec -> 'districts' -> 0 ->> 'name' FROM notification_subscriptions WHERE account_id = %s", w.people["a_acc"])
    assert stored == real

    nowhere = w.run(_watchdog(districts=[{"name": "Nikde", "level": "obec", "id": 999_999_999}]))
    refused = w.post(nowhere, "apply")
    assert refused.status_code == 422 and w.marker(nowhere) is None


def test_a_watchdogs_city_index_is_one_we_hold_or_apply_refuses(w: World) -> None:
    rule = {"index_name": "<b>nic</b>", "op": ">=", "value": 5}
    run_id = w.run(_watchdog(city_index_rules=[rule]))
    assert "not one we hold" in " ".join(w.view(run_id)["before"])
    refused = w.post(run_id, "apply")
    assert refused.status_code == 422 and w.marker(run_id) is None


def test_a_property_we_no_longer_hold_is_drawn_as_a_refusal_before_the_click(w: World) -> None:
    run_id = w.run(_collection(w.pids[0], 987_654_321))
    view = w.view(run_id)
    assert "no longer hold a property" in " ".join(view["before"])
    assert any(p["property_id"] is None for p in view["properties"])
    assert w.post(run_id, "apply").status_code == 422 and w.marker(run_id) is None


def test_a_proposal_todays_schema_refuses_is_drawn_refused_and_never_written(w: World) -> None:
    run_id = w.run({"entry": COLLECTION, "args": {"name": "x", "property_ids": [w.pids[0]], "collection_id": 4242}})
    view = w.view(run_id)
    assert view["refused"] and view["state"] == "open"
    before = w.rows()
    assert w.post(run_id, "apply").status_code == 422
    assert w.rows() == before and w.marker(run_id) is None


def test_an_applied_proposal_keeps_its_drawing_and_its_undo_whatever_the_schema_says_later(w: World) -> None:
    run_id = _applied(w, _note(w.pids[0]))
    w.svc.execute("""UPDATE agent_runs SET proposals = '[{"entry": "add_note", "args": {"retired_field": 1}}]' WHERE id = %s""", (run_id,))
    view = w.view(run_id)
    assert view["state"] == "applied" and view["title"] == "New note" and view["undo"]["available"]
    undone = w.post(run_id, "undo")
    assert undone.status_code == 200 and undone.json()["state"] == "undone" and undone.json()["answer"] is None


def test_a_new_watchdog_alerts_nothing_that_is_already_there(w: World) -> None:
    """Rule 16: created by Apply as by the route, `active_since` is its baseline, so no flood of what already matches."""
    run_id = _applied(w, _watchdog())
    sub = w.marker(run_id)["effect"]["undo"]["subscription_id"]
    assert w.one("SELECT active_since >= created_at FROM notification_subscriptions WHERE id = %s", sub) is True
    nf.match_once(w.svc)
    assert w.one("SELECT count(*) FROM notification_dispatches WHERE subscription_id = %s", sub) == 0


# --- the Undo collision table --------------------------------------------------------------------------------------

def test_undo_with_a_member_the_user_added_removes_only_the_agents(w: World) -> None:
    a, b, yours = w.pids[:3]
    run_id = _applied(w, _collection(a, b))
    coll = _collection_id(w, run_id)
    assert w.client(w.people["a"]).post(f"/collections/{coll}/properties", json={"property_ids": [yours]}).status_code == 200
    says = w.view(run_id)["undo"]
    assert says["available"] and "keeps the collection" in says["says"]
    assert w.post(run_id, "undo").status_code == 200
    assert [r[0] for r in w.svc.execute("SELECT property_id FROM collection_properties WHERE collection_id = %s", (coll,))] == [yours]
    assert w.one("SELECT name FROM collections WHERE id = %s", coll) == "Výběr"


def test_undo_after_the_user_removed_one_member_still_deletes_the_collection(w: World) -> None:
    a, b = w.pids[:2]
    run_id = _applied(w, _collection(a, b))
    coll = _collection_id(w, run_id)
    assert w.client(w.people["a"]).delete(f"/collections/{coll}/properties/{a}").status_code == 200
    assert w.post(run_id, "undo").status_code == 200
    assert w.one("SELECT count(*) FROM collections WHERE id = %s", coll) == 0


def test_undo_when_only_the_users_members_are_left_is_refused(w: World) -> None:
    a, yours = w.pids[:2]
    run_id = _applied(w, _collection(a))
    coll = _collection_id(w, run_id)
    me = w.client(w.people["a"])
    me.post(f"/collections/{coll}/properties", json={"property_ids": [yours]})
    me.delete(f"/collections/{coll}/properties/{a}")
    _refused_undo(w, run_id, "Nothing of the agent's")


def test_a_member_the_user_removed_and_added_back_is_theirs(w: World) -> None:
    a, b = w.pids[:2]
    run_id = _applied(w, _collection(a, b))
    coll = _collection_id(w, run_id)
    me = w.client(w.people["a"])
    me.delete(f"/collections/{coll}/properties/{a}")
    me.post(f"/collections/{coll}/properties", json={"property_ids": [a]})
    assert w.post(run_id, "undo").status_code == 200
    assert [r[0] for r in w.svc.execute("SELECT property_id FROM collection_properties WHERE collection_id = %s", (coll,))] == [a]


@pytest.mark.parametrize("change, says", [
    ({"name": "Moje"}, "renamed or edited"), ({"description": "Můj popis"}, "renamed or edited"),
    ({"monitoring_enabled": True}, "monitoring"),
])
def test_undo_leaves_a_collection_the_user_changed(w: World, change: dict[str, Any], says: str) -> None:
    run_id = _applied(w, _collection(w.pids[0]))
    assert w.client(w.people["a"]).patch(f"/collections/{_collection_id(w, run_id)}", json=change).status_code == 200
    _refused_undo(w, run_id, says)


def test_undo_leaves_a_collection_the_user_shared(w: World) -> None:
    run_id = _applied(w, _collection(w.pids[0]))
    w.svc.execute("INSERT INTO collection_shares (collection_id, account_id, recipient_account_id) VALUES (%s, %s, %s)",
                  (_collection_id(w, run_id), w.people["a_acc"], w.people["b_acc"]))
    _refused_undo(w, run_id, "shared")


def test_undo_of_a_collection_the_user_deleted_says_so(w: World) -> None:
    run_id = _applied(w, _collection(w.pids[0]))
    assert w.client(w.people["a"]).delete(f"/collections/{_collection_id(w, run_id)}").status_code == 200
    _refused_undo(w, run_id, "already deleted")


def test_a_members_merge_is_followed_when_nothing_else_is_in_the_collection(w: World) -> None:
    survivor, agents = w.pids[:2]
    run_id = _applied(w, _collection(agents))
    coll = _collection_id(w, run_id)
    w.merge(survivor, agents)
    assert w.one("SELECT property_id FROM collection_properties WHERE collection_id = %s", coll) == survivor
    assert w.post(run_id, "undo").status_code == 200
    assert w.one("SELECT count(*) FROM collections WHERE id = %s", coll) == 0


def test_a_merge_that_folded_the_users_member_into_the_agents_keeps_it(w: World) -> None:
    agents, other, yours = w.pids[:3]
    run_id = _applied(w, _collection(agents, other))
    coll = _collection_id(w, run_id)
    assert w.client(w.people["a"]).post(f"/collections/{coll}/properties", json={"property_ids": [yours]}).status_code == 200
    w.merge(agents, yours)
    assert w.one("SELECT count(*) FROM collection_properties WHERE collection_id = %s", coll) == 2
    assert "1 of yours" in w.view(run_id)["undo"]["says"]
    assert w.post(run_id, "undo").status_code == 200
    assert [r[0] for r in w.svc.execute("SELECT property_id FROM collection_properties WHERE collection_id = %s", (coll,))] == [agents]


def test_a_merge_folding_the_agents_member_into_the_users_leaves_the_users(w: World) -> None:
    yours, agents, other = w.pids[:3]
    run_id = _applied(w, _collection(agents, other))
    coll = _collection_id(w, run_id)
    w.client(w.people["a"]).post(f"/collections/{coll}/properties", json={"property_ids": [yours]})
    w.merge(yours, agents)
    assert w.post(run_id, "undo").status_code == 200
    assert [r[0] for r in w.svc.execute("SELECT property_id FROM collection_properties WHERE collection_id = %s", (coll,))] == [yours]


def _note_of(w: World, run_id: int) -> int:
    return int(w.marker(run_id)["effect"]["undo"]["note_id"])


def test_undo_leaves_a_note_the_user_edited(w: World) -> None:
    run_id = _applied(w, _note(w.pids[0]))
    assert w.client(w.people["a"]).patch(f"/properties/{w.pids[0]}/notes/{_note_of(w, run_id)}", json={"body": "Moje"}).status_code == 200
    _refused_undo(w, run_id, "edited")


def test_undo_leaves_a_note_the_user_marked_shared(w: World) -> None:
    run_id = _applied(w, _note(w.pids[0]))
    assert w.client(w.people["a"]).patch(f"/properties/{w.pids[0]}/notes/{_note_of(w, run_id)}", json={"shared": True}).status_code == 200
    _refused_undo(w, run_id, "marked this note shared")


def test_undo_leaves_a_note_with_a_file_on_it(w: World) -> None:
    run_id = _applied(w, _note(w.pids[0]))
    w.svc.execute(
        "INSERT INTO property_attachments (property_id, note_id, account_id, storage_key, filename, mime_type, byte_size, sha256_hex) "
        "VALUES (%s, %s, %s, 'k', 'f.pdf', 'application/pdf', 1, repeat('a', 64))",
        (w.pids[0], _note_of(w, run_id), w.people["a_acc"]))
    _refused_undo(w, run_id, "attached files")


def test_undo_of_a_note_the_user_deleted_says_so(w: World) -> None:
    run_id = _applied(w, _note(w.pids[0]))
    assert w.client(w.people["a"]).delete(f"/properties/{w.pids[0]}/notes/{_note_of(w, run_id)}").status_code == 200
    _refused_undo(w, run_id, "already deleted")


def test_undo_finds_a_note_a_merge_moved(w: World) -> None:
    survivor, agents = w.pids[:2]
    run_id = _applied(w, _note(agents))
    w.merge(survivor, agents)
    assert w.one("SELECT property_id FROM property_notes WHERE id = %s", _note_of(w, run_id)) == survivor
    assert w.post(run_id, "undo").status_code == 200
    assert w.one("SELECT count(*) FROM property_notes WHERE id = %s", _note_of(w, run_id)) == 0


def _cards(w: World) -> list[tuple[Any, ...]]:
    return w.svc.execute("SELECT property_id, stage_id FROM property_pipeline WHERE account_id = %s ORDER BY 1", (w.people["a_acc"],)).fetchall()


def test_a_card_already_on_the_board_is_no_write_and_undo_cannot_remove_it(w: World) -> None:
    pid = w.pids[0]
    assert w.client(w.people["a"]).post("/pipeline/cards", json={"property_id": pid}).status_code == 200
    run_id = w.run(_card(pid))
    assert "No change" in w.view(run_id)["after"][0]
    before = w.rows()
    applied = w.post(run_id, "apply")
    assert applied.status_code == 200 and "nothing changed" in applied.json()["answer"]
    assert w.rows() == before
    _refused_undo(w, run_id, "Nothing to undo")
    assert w.one("SELECT count(*) FROM property_pipeline WHERE property_id = %s AND account_id = %s", pid, w.people["a_acc"]) == 1


def test_undo_of_a_card_the_user_moved_is_refused_even_back_at_the_entry_stage(w: World) -> None:
    pid = w.pids[0]
    run_id = _applied(w, _card(pid))
    me = w.client(w.people["a"])
    stages = [(r["id"], r["is_entry"]) for r in me.get("/pipeline/stages").json()["data"]]
    entry, later = next(i for i, e in stages if e), next(i for i, e in stages if not e)
    assert me.patch(f"/pipeline/cards/{pid}", json={"stage_id": later}).status_code == 200
    _refused_undo(w, run_id, "You moved this card")
    assert me.patch(f"/pipeline/cards/{pid}", json={"stage_id": entry}).status_code == 200
    _refused_undo(w, run_id, "You moved this card")


def test_undo_of_a_card_the_user_rearranged_is_refused(w: World) -> None:
    pid = w.pids[0]
    run_id = _applied(w, _card(pid))
    assert w.client(w.people["a"]).patch(f"/pipeline/cards/{pid}", json={"board_position": 0.5}).status_code == 200
    _refused_undo(w, run_id, "You moved this card")


def test_undo_of_a_card_the_user_removed_and_added_again_is_refused(w: World) -> None:
    pid = w.pids[0]
    run_id = _applied(w, _card(pid))
    me = w.client(w.people["a"])
    assert me.delete(f"/pipeline/cards/{pid}").status_code == 200
    _refused_undo(w, run_id, "already took this card off")
    assert me.post("/pipeline/cards", json={"property_id": pid}).status_code == 200
    _refused_undo(w, run_id, "not the one the agent added")


@pytest.mark.parametrize("survivor_holds_the_agents_card", [True, False])
def test_undo_of_a_card_on_a_merged_property_is_refused(w: World, survivor_holds_the_agents_card: bool) -> None:
    """A merge may have folded the user's card into the agent's, on either side: never take that card."""
    agents, yours = w.pids[:2]
    run_id = _applied(w, _card(agents))
    assert w.client(w.people["a"]).post("/pipeline/cards", json={"property_id": yours}).status_code == 200
    w.merge(*((agents, yours) if survivor_holds_the_agents_card else (yours, agents)))
    _refused_undo(w, run_id, "merged with another")
    assert len(_cards(w)) == 1


def test_a_cards_undo_puts_back_the_dismissal_its_apply_lifted(w: World) -> None:
    pid = w.pids[0]
    me = w.client(w.people["a"])
    assert me.post("/dismissals", json={"property_id": pid}).status_code == 200
    def active() -> int:
        return w.one("SELECT count(*) FROM property_dismissals WHERE property_id = %s AND account_id = %s AND lifted_at IS NULL",
                     pid, w.people["a_acc"])
    assert active() == 1
    run_id = w.run(_card(pid))
    assert "dismissed" in " ".join(w.view(run_id)["before"])
    assert w.post(run_id, "apply").status_code == 200
    assert active() == 0
    assert w.post(run_id, "undo").status_code == 200
    assert active() == 1 and _cards(w) == []


def test_undo_leaves_a_watchdog_the_user_changed(w: World) -> None:
    run_id = _applied(w, _watchdog())
    sub = w.marker(run_id)["effect"]["undo"]["subscription_id"]
    assert w.client(w.people["a"]).put(f"/notifications/subscriptions/{sub}", json={"name": "Moje"}).status_code == 200
    _refused_undo(w, run_id, "changed this watchdog")


def test_undo_of_a_watchdog_the_user_deleted_says_so_and_a_retired_one_keeps_its_row(w: World) -> None:
    run_id = _applied(w, _watchdog())
    sub = w.marker(run_id)["effect"]["undo"]["subscription_id"]
    assert w.client(w.people["a"]).delete(f"/notifications/subscriptions/{sub}").status_code == 200
    _refused_undo(w, run_id, "already deleted")
    assert w.one("SELECT retired_at IS NOT NULL FROM notification_subscriptions WHERE id = %s", sub) is True


def test_every_undo_refusal_in_the_module_rolls_the_marker_back_with_the_transaction(w: World) -> None:
    run_id = _applied(w, _collection(w.pids[0]))
    w.svc.execute("UPDATE collections SET name = 'Moje' WHERE id = %s", (_collection_id(w, run_id),))
    assert w.post(run_id, "undo").status_code == 409
    assert w.marker(run_id)["undone_at"] is None


def test_the_shared_modules_add_properties_returns_the_ids_it_added(w: World) -> None:
    a, b = w.pids[:2]
    with tenant_pool.tenant_transaction({"sub": str(w.people["a"]), "role": "authenticated"}, act_as=None, method="POST") as conn:
        coll = curation.create_collection(conn, s.CreateCollectionIn(name="Moje"), str(w.people["a_acc"]))["id"]
        curation.add_properties_to_collection(conn, coll, s.AddPropertiesToCollectionIn(property_ids=[a]))
        out = curation.add_properties_to_collection(conn, coll, s.AddPropertiesToCollectionIn(property_ids=[a, b]))
    assert out == {"added": 1, "skipped": 1, "added_ids": [b]}
