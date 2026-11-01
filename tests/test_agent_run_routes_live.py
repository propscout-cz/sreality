"""The run routes through the API test client on the replayed schema (#2071): real row-level security on the tenant
pool, real `executor`, a scripted model. Runs in CI's migrations job (`TEST_DATABASE_URL`)."""

from __future__ import annotations

import contextlib
import threading
import uuid
from collections.abc import Callable, Iterator
from typing import Any

import psycopg
import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from api import dependencies as deps  # noqa: E402
from api import main as api_main  # noqa: E402
from api.agents import executor  # noqa: E402
from api.routes import agent_runs  # noqa: E402
from tests._live_property import REQUIRED_DB, db_url  # noqa: E402
from tests.api._agent_fixtures import reply  # noqa: E402
from tests import test_agent_runs_live as ledger  # noqa: E402
from tests.test_agent_runs_live import (  # noqa: E402
    AGENT,
    ANSWER,
    OWN_CONTEXT,
    SUBMIT,
    _connect,
    _Model,
    _run,
    _set,
    _work_on,
)

pytestmark = REQUIRED_DB

BODY = {"agent": AGENT, "input": {"town": "Brno"}}

# The ledger's own world: the executor on, a probe agent, three allowlisted signups, the ledger empty.
svc = ledger.svc
people = ledger.people
world = ledger.world


@contextlib.contextmanager
def _service_conn() -> Iterator[psycopg.Connection]:
    conn = _connect()
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture()
def as_user(monkeypatch: pytest.MonkeyPatch) -> Iterator[Callable[[uuid.UUID], TestClient]]:
    """A test client signed in as one user: verify_jwt answers with their claims, the rest is the real path."""
    monkeypatch.setenv("TENANT_POOL_DB_URL", db_url())
    monkeypatch.setattr(deps, "open_background_conn", _service_conn)

    def sign_in(user: uuid.UUID) -> TestClient:
        api_main.app.dependency_overrides[deps.verify_jwt] = lambda: {"sub": str(user), "role": "authenticated"}
        return TestClient(api_main.app)

    try:
        yield sign_in
    finally:
        api_main.app.dependency_overrides.pop(deps.verify_jwt, None)


def test_an_allowlisted_token_starts_a_run_and_reads_its_steps(svc: psycopg.Connection, world: dict[str, Any],
                                                               as_user: Callable[[uuid.UUID], TestClient]) -> None:
    client = as_user(world["a"])
    started = client.post("/runs", json=BODY)
    assert started.status_code == 201, started.text
    run_id = started.json()["id"]
    assert started.json() == {"id": run_id, "status": "queued", "created": True}
    row = _run(svc, run_id)
    assert (row["account_id"], row["principal_user_id"], row["input"]) == (world["a_acc"], world["a"], {"town": "Brno"})

    assert client.get(f"/runs/{run_id}/steps").json() == {"data": []}
    assert _work_on(_Model(reply((OWN_CONTEXT, {}), text="Looking."), reply((SUBMIT, {"markdown": "# Done"})))).state == "succeeded"

    run = client.get(f"/runs/{run_id}").json()
    assert run["status"] == "succeeded" and run["result_markdown"] == "# Done" and run["cost_usd"] > 0
    assert (run["agent"], run["model"], run["commit_sha"]) == (AGENT, row["model"], "c0mm1t")
    assert "principal_user_id" not in run and "account_id" not in run

    steps = client.get(f"/runs/{run_id}/steps").json()["data"]
    assert [(s["attempt"], s["n"], s["kind"], s["name"]) for s in steps] == [
        (1, 1, "model", row["model"]), (1, 2, "tool", OWN_CONTEXT), (1, 3, "model", row["model"]), (1, 4, "tool", SUBMIT)]
    assert steps[0]["text"] == ["Looking."] and steps[1]["input"] == {} and "payload" not in steps[0]
    assert steps[1]["text"] is None, "a tool result's own `text` stays in the diary"
    assert steps[1]["summary"] and steps[0]["cost_usd"] > 0

    listed = client.get("/runs").json()
    assert [r["id"] for r in listed["data"]] == [run_id] and listed["truncated"] is False
    assert "result_markdown" not in listed["data"][0], "the list stays light: the result is on the run's own read"


def test_a_token_off_the_allowlist_gets_403_and_no_row(svc: psycopg.Connection, world: dict[str, Any],
                                                       as_user: Callable[[uuid.UUID], TestClient]) -> None:
    _set(svc, "agent_run_allowlist", [e for e in world["emails"] if "-b-" in e])
    client = as_user(world["a"])
    for body in (BODY, {"agent": "no_such_agent", "input": {}}):
        refused = client.post("/runs", json=body)
        assert refused.status_code == 403, refused.text
        assert "not open" in refused.json()["detail"]
    assert svc.execute("SELECT count(*) FROM agent_runs").fetchone()[0] == 0


def test_an_act_as_header_is_403_on_every_route_even_with_an_edit_share(svc: psycopg.Connection, world: dict[str, Any],
                                                                        as_user: Callable[[uuid.UUID], TestClient]) -> None:
    run_id = executor.submit(svc, principal_user_id=world["a"], account_id=world["a_acc"], agent=AGENT,
                             value={"town": "Brno"}).run_id
    svc.execute("INSERT INTO pipeline_shares (account_id, recipient_account_id, mode) VALUES (%s, %s, 'edit')",
                (world["a_acc"], world["b_acc"]))
    client = as_user(world["b"])
    head = {"X-Act-As": str(world["a_acc"])}
    calls = [("POST", "/runs", BODY), ("GET", "/runs", None), ("GET", "/runs/options", None),
             ("GET", f"/runs/{run_id}", None), ("GET", f"/runs/{run_id}/steps", None),
             ("POST", f"/runs/{run_id}/cancel", None)]
    for method, path, body in calls:
        assert client.request(method, path, json=body, headers=head).status_code == 403, f"{method} {path}"
    assert _run(svc, run_id)["cancel_requested_at"] is None
    assert svc.execute("SELECT count(*) FROM agent_runs").fetchone()[0] == 1


def test_another_accounts_run_is_404_on_every_route_and_absent_from_the_list(svc: psycopg.Connection, world: dict[str, Any],
                                                                             as_user: Callable[[uuid.UUID], TestClient]) -> None:
    run_id = executor.submit(svc, principal_user_id=world["a"], account_id=world["a_acc"], agent=AGENT,
                             value={"town": "Brno"}).run_id
    client = as_user(world["b"])
    assert client.get(f"/runs/{run_id}").status_code == 404
    assert client.get(f"/runs/{run_id}/steps").status_code == 404
    assert client.post(f"/runs/{run_id}/cancel").status_code == 404
    assert client.get("/runs").json()["data"] == []
    row = _run(svc, run_id)
    assert row["status"] == "queued" and row["cancel_requested_at"] is None


def test_cancel_marks_the_run_and_the_executor_stops_it_between_steps(svc: psycopg.Connection, world: dict[str, Any],
                                                                      as_user: Callable[[uuid.UUID], TestClient]) -> None:
    client = as_user(world["a"])
    run_id = client.post("/runs", json=BODY).json()["id"]
    seen: list[Any] = []

    def cancel_during_the_first_call(call: int) -> None:
        if call == 1:
            answered = client.post(f"/runs/{run_id}/cancel")
            seen.append((answered.status_code, answered.json()))

    model = _Model(reply((OWN_CONTEXT, {})), ANSWER, during=cancel_during_the_first_call)
    assert _work_on(model) == executor.Worked("cancelled", run_id)
    assert seen == [(200, {"id": run_id, "status": "running"})], "a running run is marked and stops at its next step"
    assert len(model.calls) == 1, "the second model call was never made"
    run = client.get(f"/runs/{run_id}").json()
    assert run["status"] == "cancelled" and run["cancel_requested_at"] and run["finished_at"]
    assert client.post(f"/runs/{run_id}/cancel").json() == {"id": run_id, "status": "cancelled"}


def test_cancelling_a_queued_run_ends_it_now(svc: psycopg.Connection, world: dict[str, Any],
                                             as_user: Callable[[uuid.UUID], TestClient]) -> None:
    client = as_user(world["a"])
    run_id = client.post("/runs", json=BODY).json()["id"]
    assert client.post(f"/runs/{run_id}/cancel").json() == {"id": run_id, "status": "cancelled"}
    assert _work_on(_Model(ANSWER)).state == "idle"


def test_a_repeated_key_returns_the_same_run_with_200(svc: psycopg.Connection, world: dict[str, Any],
                                                      as_user: Callable[[uuid.UUID], TestClient]) -> None:
    client = as_user(world["a"])
    body = {**BODY, "idempotency_key": "form-1"}
    first = client.post("/runs", json=body)
    again = client.post("/runs", json=body)
    assert (first.status_code, again.status_code) == (201, 200)
    assert again.json() == {"id": first.json()["id"], "status": "queued", "created": False}


@pytest.mark.parametrize("body, needle", [
    ({"agent": "no_such_agent", "input": {}}, "no_such_agent"),
    ({"agent": "../secrets", "input": {}}, "secrets"),
    ({"agent": AGENT, "input": {"city": "Brno"}}, "town"),
])
def test_an_agent_or_input_that_could_not_run_is_422_in_words(svc: psycopg.Connection, world: dict[str, Any],
                                                              as_user: Callable[[uuid.UUID], TestClient],
                                                              body: dict[str, Any], needle: str) -> None:
    answered = as_user(world["a"]).post("/runs", json=body)
    assert answered.status_code == 422 and needle in answered.json()["detail"]
    assert svc.execute("SELECT count(*) FROM agent_runs").fetchone()[0] == 0


@pytest.mark.parametrize("extra", [{"account_id": "x"}, {"principal_user_id": "x"}, {"provider": "anthropic"}])
def test_the_body_cannot_name_an_identity_or_a_provider(svc: psycopg.Connection, world: dict[str, Any],
                                                        as_user: Callable[[uuid.UUID], TestClient],
                                                        extra: dict[str, str]) -> None:
    assert as_user(world["a"]).post("/runs", json={**BODY, **extra}).status_code == 422
    assert svc.execute("SELECT count(*) FROM agent_runs").fetchone()[0] == 0


def test_the_in_flight_cap_is_a_429_in_words(svc: psycopg.Connection, world: dict[str, Any],
                                             as_user: Callable[[uuid.UUID], TestClient]) -> None:
    _set(svc, "agent_account_in_flight_cap", 1)
    client = as_user(world["a"])
    assert client.post("/runs", json=BODY).status_code == 201
    refused = client.post("/runs", json=BODY)
    assert refused.status_code == 429 and "1 runs" in refused.json()["detail"]


def test_a_two_membership_user_starts_on_the_oldest_and_reads_both(svc: psycopg.Connection, world: dict[str, Any],
                                                                   as_user: Callable[[uuid.UUID], TestClient]) -> None:
    """The choice the route makes for a second membership: the app's one rule (`require_account_id`), no picker."""
    svc.execute("INSERT INTO account_members (account_id, user_id, role, created_at) "
                "VALUES (%s, %s, 'member', now() + interval '1 day')", (world["b_acc"], world["a"]))
    on_b = executor.submit(svc, principal_user_id=world["b"], account_id=world["b_acc"], agent=AGENT,
                           value={"town": "Brno"}).run_id
    client = as_user(world["a"])
    started = client.post("/runs", json=BODY).json()["id"]
    assert _run(svc, started)["account_id"] == world["a_acc"]
    assert {r["id"] for r in client.get("/runs").json()["data"]} == {started, on_b}
    assert client.post(f"/runs/{on_b}/cancel").json()["status"] == "cancelled", "a member may stop any run they can read"


def test_the_list_says_when_it_is_cut_short(svc: psycopg.Connection, world: dict[str, Any],
                                            as_user: Callable[[uuid.UUID], TestClient],
                                            monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(agent_runs, "LIST_LIMIT", 2)
    client = as_user(world["a"])
    ids = [client.post("/runs", json=BODY).json()["id"] for _ in range(3)]
    listed = client.get("/runs").json()
    assert [r["id"] for r in listed["data"]] == ids[:0:-1] and listed["truncated"] is True


def test_options_offers_the_form_only_to_who_can_use_it(svc: psycopg.Connection, world: dict[str, Any],
                                                        as_user: Callable[[uuid.UUID], TestClient]) -> None:
    client = as_user(world["a"])
    options = client.get("/runs/options").json()
    assert options["start"] == {"allowed": True, "reason": None} and options["queue"] == "open"
    [agent] = options["agents"]
    assert agent["name"] == AGENT and agent["description"]
    assert agent["input_schema"]["properties"]["town"]["type"] == "string"

    _set(svc, "agent_run_allowlist", [])
    closed = client.get("/runs/options").json()
    assert closed["start"]["allowed"] is False and "not open" in closed["start"]["reason"]
    assert closed["agents"] == [] and closed["queue"] is None, "a refused account reads no definition, no platform state"


@pytest.mark.parametrize("setting, value, state", [
    ("agent_executor_enabled", False, "dark"),
    ("agent_executor_enabled", None, "dark"),
    ("agent_daily_ceiling_usd", None, "parked"),
    ("agent_account_running_cap", None, "parked"),
    ("agent_daily_ceiling_usd", 0, "parked"),
])
def test_options_says_truthfully_whether_a_queued_run_can_move(svc: psycopg.Connection, world: dict[str, Any],
                                                               as_user: Callable[[uuid.UUID], TestClient],
                                                               setting: str, value: Any, state: str) -> None:
    _set(svc, setting, value)
    assert as_user(world["a"]).get("/runs/options").json()["queue"] == state
    assert _work_on(_Model()).state == state, "the page's word is the executor's own"


def test_another_accounts_cancel_never_opens_the_service_role(svc: psycopg.Connection, world: dict[str, Any],
                                                              as_user: Callable[[uuid.UUID], TestClient],
                                                              monkeypatch: pytest.MonkeyPatch) -> None:
    """The gate comes first: a foreign id is refused on the tenant connection alone (tenancy.md, Second instance)."""
    run_id = executor.submit(svc, principal_user_id=world["a"], account_id=world["a_acc"], agent=AGENT,
                             value={"town": "Brno"}).run_id
    client = as_user(world["b"])

    def must_not_open() -> Any:
        raise AssertionError("the service role opened before the gate")

    monkeypatch.setattr(deps, "open_background_conn", must_not_open)
    assert client.post(f"/runs/{run_id}/cancel").status_code == 404


def test_a_folder_that_does_not_load_is_not_offered_and_the_others_still_are(world: dict[str, Any],
                                                                             as_user: Callable[[uuid.UUID], TestClient]) -> None:
    broken = world["folder"].parent / "broken_v1"
    broken.mkdir()
    (broken / "SKILL.md").write_text("no frontmatter", encoding="utf-8")
    assert [a["name"] for a in as_user(world["a"]).get("/runs/options").json()["agents"]] == [AGENT]


def test_an_empty_skills_folder_offers_no_agent_and_the_form_can_say_so(world: dict[str, Any], tmp_path_factory: Any,
                                                                        monkeypatch: pytest.MonkeyPatch,
                                                                        as_user: Callable[[uuid.UUID], TestClient]) -> None:
    monkeypatch.setattr(executor, "SKILLS_DIR", tmp_path_factory.mktemp("no_agents"))
    options = as_user(world["a"]).get("/runs/options").json()
    assert options["start"]["allowed"] is True and options["agents"] == []


def test_a_run_reads_as_running_while_the_worker_has_it(svc: psycopg.Connection, world: dict[str, Any],
                                                        as_user: Callable[[uuid.UUID], TestClient]) -> None:
    client = as_user(world["a"])
    run_id = client.post("/runs", json=BODY).json()["id"]
    live: list[str] = []
    model = _Model(reply((OWN_CONTEXT, {})), ANSWER,
                   during=lambda call: live.append(client.get(f"/runs/{run_id}").json()["status"]))
    assert _work_on(model, threading.Event()).state == "succeeded"
    assert live == ["running", "running"] and client.get("/runs").json()["data"][0]["status"] == "succeeded"
