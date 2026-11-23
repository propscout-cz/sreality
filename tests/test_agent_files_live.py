"""A run's files through the real executor, routes and row-level security on the replayed schema, with an in-memory store
standing in for the bucket (#2073). The real bucket's round trip is a gate ticket: this file cannot reach Storage."""

from __future__ import annotations

import hashlib
import threading
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import psycopg
import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from api.agents import agent_files, executor  # noqa: E402
from api.providers import Completion, HostedFile  # noqa: E402
from tests._live_property import REQUIRED_DB, signups  # noqa: E402
from tests.api._agent_fixtures import InMemoryFileStore, ScriptedCodeProvider, code_reply  # noqa: E402
from tests import test_agent_run_routes_live as routes  # noqa: E402
from tests import test_agent_runs_live as ledger  # noqa: E402
from tests.test_agent_runs_live import CHEAP, OPUS, SUBMIT, _connect, _run, _submit  # noqa: E402

pytestmark = REQUIRED_DB

svc = ledger.svc
people = ledger.people
world = ledger.world
as_user = routes.as_user

CSV = HostedFile(name="report.csv", media_type="text/csv", data=b"a,b\n1,2\n")
ANSWERED = (SUBMIT, {"markdown": "Here.", "files": ["report.csv"]})


@pytest.fixture()
def bucket(monkeypatch: pytest.MonkeyPatch) -> InMemoryFileStore:
    """The store every path asks for, and a sweep that is due."""
    store = InMemoryFileStore()
    monkeypatch.setattr(agent_files, "store", lambda: store)
    monkeypatch.setattr(agent_files, "_next_sweep", 0.0)
    return store


@pytest.fixture()
def objects(svc: psycopg.Connection) -> Iterator[Callable[..., None]]:
    """Storage's own record of an object, which only Storage writes in production; the sweep reads it."""
    def add(name: str, *, hours_old: float = 2) -> None:
        svc.execute("INSERT INTO storage.objects (bucket_id, name, created_at) "
                    "VALUES (%s, %s, now() - make_interval(hours => %s))", (agent_files.BUCKET, name, hours_old))
    yield add
    svc.execute("DELETE FROM storage.objects")


def _code_model(*files: HostedFile) -> ScriptedCodeProvider:
    names = [f"file_out_{i}" for i in range(len(files))]
    answer = (SUBMIT, {"markdown": "Here.", "files": [file.name for file in files]})
    replies: list[Completion] = [code_reply(files=tuple(names), stop="pause_turn"), code_reply(answer)]
    return ScriptedCodeProvider(replies, files=dict(zip(names, files)), prices={OPUS: CHEAP})


def _work(model: ScriptedCodeProvider) -> executor.Worked:
    return executor.work_once(providers={"anthropic": model}, stop=threading.Event(), connect=_connect)


def _artifacts(svc: psycopg.Connection, run_id: int) -> list[dict[str, Any]]:
    with svc.cursor(row_factory=psycopg.rows.dict_row) as cur:
        cur.execute("SELECT * FROM agent_artifacts WHERE run_id = %s ORDER BY id", (run_id,))
        return cur.fetchall()


def _finished_run(svc: psycopg.Connection, world: dict[str, Any], monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
                  *files: HostedFile) -> int:
    ledger._code_world(world, monkeypatch, tmp_path)
    run_id = _submit(svc, world, "a")
    assert _work(_code_model(*(files or (CSV,)))) == executor.Worked("succeeded", run_id)
    return run_id


def test_a_produced_file_is_uploaded_and_a_row_links_it_to_the_run_and_the_account(
        svc: psycopg.Connection, world: dict[str, Any], bucket: InMemoryFileStore, tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch) -> None:
    run_id = _finished_run(svc, world, monkeypatch, tmp_path)
    sha = hashlib.sha256(CSV.data).hexdigest()
    [row] = _artifacts(svc, run_id)
    assert (row["account_id"], row["run_id"]) == (world["a_acc"], run_id)
    assert (row["name"], row["kind"], row["size_bytes"], row["sha256"]) == ("report.csv", "text/csv", len(CSV.data), sha)
    assert row["storage_key"] == f"{world['a_acc']}/{run_id}/1/{sha}"
    assert bucket.objects[row["storage_key"]] == CSV.data
    assert (row["step_attempt"], row["step_n"]) == (1, 2), "the diary's own line for this file"


def test_an_upload_that_fails_marks_the_file_and_the_paid_answer_still_succeeds(
        svc: psycopg.Connection, world: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = InMemoryFileStore(refuse={hashlib.sha256(CSV.data).hexdigest()})
    monkeypatch.setattr(agent_files, "store", lambda: store)
    run_id = _finished_run(svc, world, monkeypatch, tmp_path)
    row = _run(svc, run_id)
    assert row["status"] == "succeeded" and row["result_markdown"] == "Here." and row["error"] is None
    [artifact] = _artifacts(svc, run_id)
    assert artifact["storage_key"] is None and artifact["size_bytes"] == len(CSV.data)


def test_a_service_with_no_store_marks_the_file_and_the_run_still_succeeds(
        svc: psycopg.Connection, world: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(agent_files, "store", lambda: None)
    run_id = _finished_run(svc, world, monkeypatch, tmp_path)
    assert _run(svc, run_id)["status"] == "succeeded"
    assert [a["storage_key"] for a in _artifacts(svc, run_id)] == [None]


def test_a_run_shows_only_the_attempt_that_finished_and_counts_each_file_once(
        svc: psycopg.Connection, world: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The first attempt uploads, then the reaper takes its run before it can finish: its verdict and its rows never land."""
    ledger._code_world(world, monkeypatch, tmp_path)
    run_id = _submit(svc, world, "a")

    class _LosesItsLease(InMemoryFileStore):
        def put(self, key: str, data: bytes) -> None:
            super().put(key, data)
            with _connect() as other:
                other.execute("UPDATE agent_runs SET status = 'queued', attempts = attempts + 1, lease_until = NULL "
                              "WHERE id = %s", (run_id,))

    store = _LosesItsLease()
    monkeypatch.setattr(agent_files, "store", lambda: store)
    assert _work(_code_model(CSV)) == executor.Worked("lost", run_id)
    assert _artifacts(svc, run_id) == []
    first = set(store.objects)
    assert len(first) == 1

    store2 = InMemoryFileStore()
    store2.objects.update(store.objects)
    monkeypatch.setattr(agent_files, "store", lambda: store2)
    assert _work(_code_model(CSV)) == executor.Worked("succeeded", run_id)
    [row] = _artifacts(svc, run_id)
    assert row["step_attempt"] == 2 and row["storage_key"] not in first and "/2/" in row["storage_key"]


def test_one_row_per_name_per_run_holds_in_the_schema(svc: psycopg.Connection, world: dict[str, Any], bucket: InMemoryFileStore,
                                                      tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    run_id = _finished_run(svc, world, monkeypatch, tmp_path)
    with pytest.raises(psycopg.errors.UniqueViolation):
        svc.execute("INSERT INTO agent_artifacts (account_id, run_id, kind, name, size_bytes, sha256, storage_key) "
                    "VALUES (%s, %s, 'text/csv', 'report.csv', 1, 'x', 'y')", (world["a_acc"], run_id))


def test_a_run_with_no_answer_uploads_nothing(svc: psycopg.Connection, world: dict[str, Any], bucket: InMemoryFileStore,
                                              tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ledger._code_world(world, monkeypatch, tmp_path)
    run_id = _submit(svc, world, "a")
    model = ScriptedCodeProvider([code_reply(files=("f",), stop="end_turn")], files={"f": CSV}, prices={OPUS: CHEAP})
    assert _work(model).state == "failed"
    assert bucket.objects == {} and _artifacts(svc, run_id) == []


# --- reads --------------------------------------------------------------------------------------------------------


def test_the_owner_lists_and_downloads_a_file(svc: psycopg.Connection, world: dict[str, Any], bucket: InMemoryFileStore,
                                              as_user: Callable[[uuid.UUID], TestClient], tmp_path: Path,
                                              monkeypatch: pytest.MonkeyPatch) -> None:
    run_id = _finished_run(svc, world, monkeypatch, tmp_path)
    client = as_user(world["a"])
    [listed] = client.get(f"/runs/{run_id}").json()["files"]
    assert listed["name"] == "report.csv" and listed["size_bytes"] == len(CSV.data) and listed["stored"] is True
    assert "storage_key" not in listed
    got = client.get(f"/runs/{run_id}/files/{listed['id']}")
    assert got.status_code == 200 and got.content == CSV.data
    assert got.headers["content-type"].startswith("text/csv")
    assert got.headers["content-disposition"].startswith('attachment; filename="report.csv"')
    assert got.headers["x-content-type-options"] == "nosniff"


@pytest.mark.parametrize("media_type", ["text/html", "image/svg+xml", "application/xhtml+xml", "text/javascript"])
def test_a_produced_active_type_reaches_the_browser_as_bare_bytes(
        media_type: str, svc: psycopg.Connection, world: dict[str, Any], bucket: InMemoryFileStore,
        as_user: Callable[[uuid.UUID], TestClient], tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    hostile = HostedFile(name="page.html", media_type=media_type, data=b"<script>alert(1)</script>")
    run_id = _finished_run(svc, world, monkeypatch, tmp_path, hostile)
    client = as_user(world["a"])
    [listed] = client.get(f"/runs/{run_id}").json()["files"]
    got = client.get(f"/runs/{run_id}/files/{listed['id']}")
    assert got.status_code == 200 and got.headers["content-type"] == "application/octet-stream"
    assert got.headers["content-disposition"].startswith("attachment;")
    assert got.headers["x-content-type-options"] == "nosniff"


def test_another_account_reads_nothing_and_cannot_tell_a_file_exists(
        svc: psycopg.Connection, world: dict[str, Any], bucket: InMemoryFileStore,
        as_user: Callable[[uuid.UUID], TestClient], tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    run_id = _finished_run(svc, world, monkeypatch, tmp_path)
    [row] = _artifacts(svc, run_id)
    stranger = as_user(world["b"])
    real = stranger.get(f"/runs/{run_id}/files/{row['id']}")
    guessed = stranger.get(f"/runs/{run_id}/files/{row['id'] + 1000}")
    assert real.status_code == guessed.status_code == 404
    assert real.json() == guessed.json(), "the same answer for a foreign file and for none at all"
    assert CSV.data not in real.content
    assert stranger.get(f"/runs/{run_id}").status_code == 404


def test_the_route_names_no_object_so_a_guessed_path_is_not_an_input(
        svc: psycopg.Connection, world: dict[str, Any], bucket: InMemoryFileStore,
        as_user: Callable[[uuid.UUID], TestClient], tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    run_id = _finished_run(svc, world, monkeypatch, tmp_path)
    [row] = _artifacts(svc, run_id)
    stranger = as_user(world["b"])
    for path in (row["storage_key"], f"../{row['storage_key']}", f"{row['id']}/../{row['id']}", "%2e%2e%2f1"):
        assert stranger.get(f"/runs/{run_id}/files/{path}").status_code in (404, 422), path


def test_a_file_is_read_only_through_its_own_run(svc: psycopg.Connection, world: dict[str, Any], bucket: InMemoryFileStore,
                                                 as_user: Callable[[uuid.UUID], TestClient], tmp_path: Path,
                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    run_id = _finished_run(svc, world, monkeypatch, tmp_path)
    other = _submit(svc, world, "b")
    [row] = _artifacts(svc, run_id)
    assert as_user(world["a"]).get(f"/runs/{other}/files/{row['id']}").status_code == 404


def test_an_act_as_header_is_refused_even_with_an_edit_share(svc: psycopg.Connection, world: dict[str, Any], bucket: InMemoryFileStore,
                                                             as_user: Callable[[uuid.UUID], TestClient], tmp_path: Path,
                                                             monkeypatch: pytest.MonkeyPatch) -> None:
    run_id = _finished_run(svc, world, monkeypatch, tmp_path)
    [row] = _artifacts(svc, run_id)
    svc.execute("INSERT INTO pipeline_shares (account_id, recipient_account_id, mode) VALUES (%s, %s, 'edit')",
                (world["a_acc"], world["b_acc"]))
    try:
        got = as_user(world["b"]).get(f"/runs/{run_id}/files/{row['id']}", headers={"X-Act-As": str(world["a_acc"])})
        assert got.status_code == 403
    finally:
        svc.execute("DELETE FROM pipeline_shares WHERE account_id = %s", (world["a_acc"],))


def test_a_browser_role_holds_nothing_at_the_bucket(svc: psycopg.Connection, world: dict[str, Any]) -> None:
    """Private, with no policy: no browser role reads an object at Storage, whoever's it is. The real Storage's answer to
    a guessed path is the gate ticket's check."""
    public, size, types = svc.execute("SELECT public, file_size_limit, allowed_mime_types FROM storage.buckets WHERE id = %s",
                                      (agent_files.BUCKET,)).fetchone()
    assert public is False and size == 20 * 1024 * 1024 and types == ["application/octet-stream"]
    assert svc.execute("SELECT count(*) FROM pg_policies WHERE schemaname = 'storage'").fetchone()[0] == 0


def test_a_file_whose_bytes_were_not_kept_says_so(svc: psycopg.Connection, world: dict[str, Any],
                                                  as_user: Callable[[uuid.UUID], TestClient], tmp_path: Path,
                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    store = InMemoryFileStore(refuse={hashlib.sha256(CSV.data).hexdigest()})
    monkeypatch.setattr(agent_files, "store", lambda: store)
    run_id = _finished_run(svc, world, monkeypatch, tmp_path)
    client = as_user(world["a"])
    [listed] = client.get(f"/runs/{run_id}").json()["files"]
    assert listed["stored"] is False
    got = client.get(f"/runs/{run_id}/files/{listed['id']}")
    assert got.status_code == 404 and "not stored" in got.json()["detail"]


def test_an_object_gone_from_the_bucket_is_a_404_and_a_refusing_store_a_503(
        svc: psycopg.Connection, world: dict[str, Any], bucket: InMemoryFileStore,
        as_user: Callable[[uuid.UUID], TestClient], tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    run_id = _finished_run(svc, world, monkeypatch, tmp_path)
    [row] = _artifacts(svc, run_id)
    client = as_user(world["a"])
    path = f"/runs/{run_id}/files/{row['id']}"

    class _Down(InMemoryFileStore):
        def get(self, key: str) -> bytes | None:
            raise agent_files.StoreError("storage answered 500 to a read")

    monkeypatch.setattr(agent_files, "store", _Down)
    assert client.get(path).status_code == 503
    monkeypatch.setattr(agent_files, "store", lambda: None)
    assert client.get(path).status_code == 503
    monkeypatch.setattr(agent_files, "store", InMemoryFileStore)
    assert client.get(path).status_code == 404


# --- the bytes no row wants ----------------------------------------------------------------------------------------


def test_the_sweep_removes_an_old_object_with_no_row_and_nothing_else(
        svc: psycopg.Connection, world: dict[str, Any], bucket: InMemoryFileStore, objects: Callable[..., None], tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch) -> None:
    run_id = _finished_run(svc, world, monkeypatch, tmp_path)
    [row] = _artifacts(svc, run_id)
    objects(row["storage_key"])
    objects("lost/attempt/old")
    objects("still/uploading", hours_old=0)
    monkeypatch.setattr(agent_files, "_next_sweep", 0.0)
    agent_files.sweep(svc)
    assert bucket.deleted == ["lost/attempt/old"]


def test_an_accounts_deletion_cascades_the_rows_and_the_sweep_takes_the_bytes(
        svc: psycopg.Connection, bucket: InMemoryFileStore, objects: Callable[..., None],
        monkeypatch: pytest.MonkeyPatch) -> None:
    with signups(svc, "files", "d") as made:
        run_id = svc.execute(
            "INSERT INTO agent_runs (account_id, principal_user_id, agent, definition_sha, tool_set_sha, commit_sha, "
            "provider, model, input) VALUES (%s, %s, 'x', 'x', 'x', 'x', 'x', 'x', '{}') RETURNING id",
            (made["d_acc"], made["d"])).fetchone()[0]
        key = f"{made['d_acc']}/{run_id}/1/abc"
        svc.execute("INSERT INTO agent_artifacts (account_id, run_id, kind, name, size_bytes, sha256, storage_key) "
                    "VALUES (%s, %s, 'text/csv', 'a.csv', 1, 'abc', %s)", (made["d_acc"], run_id, key))
        bucket.objects[key] = b"personal"
        objects(key)
        agent_files.sweep(svc)
        assert key in bucket.objects, "its row still wants it"
    monkeypatch.setattr(agent_files, "_next_sweep", 0.0)
    agent_files.sweep(svc)
    assert key not in bucket.objects and bucket.deleted == [key]


def test_the_sweep_runs_at_most_hourly_and_a_refusal_costs_nothing(
        svc: psycopg.Connection, objects: Callable[..., None], monkeypatch: pytest.MonkeyPatch) -> None:
    class _Refuses(InMemoryFileStore):
        def delete(self, keys: Any) -> None:
            raise agent_files.StoreError("storage answered 500 to a delete")

    store = _Refuses()
    monkeypatch.setattr(agent_files, "store", lambda: store)
    monkeypatch.setattr(agent_files, "_next_sweep", 0.0)
    objects("lost/one")
    agent_files.sweep(svc)
    assert store.deleted == []
    assert agent_files._next_sweep > 0

    clean = InMemoryFileStore()
    monkeypatch.setattr(agent_files, "store", lambda: clean)
    agent_files.sweep(svc)
    assert clean.deleted == [], "not due yet"


def test_work_once_sweeps_with_the_brake_off_and_claims_nothing(svc: psycopg.Connection, world: dict[str, Any], bucket: InMemoryFileStore,
                                                                objects: Callable[..., None]) -> None:
    """A deleted account's bytes must not wait for the executor to be switched on."""
    ledger._set(svc, executor.BRAKE, False)
    objects("gone/account/file")
    run_id = _submit(svc, world, "a")
    assert executor.work_once(providers={}, stop=threading.Event(), connect=_connect) == executor.Worked("dark")
    assert bucket.deleted == ["gone/account/file"]
    assert _run(svc, run_id)["status"] == "queued"
