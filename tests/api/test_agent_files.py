"""A run's files: what a browser is served, how an object is named, and what a store's failure costs (#2073). The live
path through the executor and the routes, with real row-level security, is tests/test_agent_files_live.py."""

from __future__ import annotations

import hashlib
import re
import uuid
from pathlib import Path
from typing import Any

import pytest
import requests

from api.agents import agent_files, hosted_code
from api.providers import HostedFile
from tests.api._agent_fixtures import InMemoryFileStore

ACCOUNT = uuid.UUID("11111111-1111-1111-1111-111111111111")
OCTET = "application/octet-stream"


@pytest.mark.parametrize("given", [
    "text/html", "TEXT/HTML", "text/html; charset=utf-8", "application/xhtml+xml", "image/svg+xml", "text/xml",
    "application/xml", "text/javascript", "application/javascript", "application/x-shockwave-flash", "multipart/x-mixed-replace",
    "application/json", "", "nonsense", "text/csv\r\nX-Evil: 1",
])
def test_an_active_or_unknown_type_is_served_as_bare_bytes(given: str) -> None:
    response = agent_files.file_response(b"<script>alert(1)</script>", given, "a.bin")
    assert response.headers["content-type"] == OCTET
    assert response.headers["content-disposition"].startswith("attachment;")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cache-control"] == "no-store", "the same URL for every login on this browser"
    assert "x-evil" not in response.headers


@pytest.mark.parametrize("given, served", [
    ("text/csv", "text/csv"), ("TEXT/CSV", "text/csv"), ("text/plain", "text/plain"), ("application/pdf", "application/pdf"),
    ("image/png", "image/png"), ("image/jpeg", "image/jpeg"),
    ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
     "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
])
def test_an_inert_type_keeps_its_own_and_still_downloads(given: str, served: str) -> None:
    response = agent_files.file_response(b"x", given, "a.bin")
    assert response.headers["content-type"].split(";")[0] == served
    assert response.headers["content-disposition"].startswith("attachment;")
    assert response.headers["x-content-type-options"] == "nosniff"


@pytest.mark.parametrize("name", ['../../etc/passwd', 'a"; filename="evil.html', "a\r\nSet-Cookie: x=1", "a\\b.csv", "<img src=x>.csv"])
def test_a_stored_name_cannot_reach_a_header_as_written(name: str) -> None:
    """The row holds a name `HostedFile` already sanitised; serving does not rely on that."""
    header = agent_files.file_response(b"x", "text/csv", name).headers["content-disposition"]
    fallback = re.fullmatch(r'attachment; filename="([^"]*)"; filename\*=UTF-8\'\'(\S+)', header)
    assert fallback, header
    assert not set(fallback.group(1)) & set('/\\"\r\n<>;')


def test_a_czech_name_survives_the_download() -> None:
    header = agent_files.file_response(b"x", "text/csv", "přehled bytů.csv").headers["content-disposition"]
    assert "filename*=UTF-8''p%C5%99ehled%20byt%C5%AF.csv" in header


def _file(name: str = "report.csv", data: bytes = b"a,b\n", media_type: str = "text/csv") -> HostedFile:
    return HostedFile(name=name, media_type=media_type, data=data)


def _store(store: Any, *files: HostedFile) -> list[agent_files.StoredFile]:
    return agent_files.store_files(files, store=store, account_id=ACCOUNT, run_id=7, attempt=2)


@pytest.mark.parametrize("name", ["../../other-account/x.csv", "/etc/passwd", "a/b/c.csv", "..", "%2e%2e%2fx"])
def test_a_model_written_name_never_reaches_the_object_key(name: str) -> None:
    store = InMemoryFileStore()
    [stored] = _store(store, _file(name=name))
    sha = hashlib.sha256(b"a,b\n").hexdigest()
    assert stored.storage_key == f"{ACCOUNT}/7/2/{sha}"
    assert set(store.objects) == {stored.storage_key}


def test_a_stored_file_keeps_its_facts_and_its_bytes_go_up_typed_as_nothing() -> None:
    store = InMemoryFileStore()
    [stored] = _store(store, _file(media_type="text/html"))
    assert (stored.name, stored.media_type, stored.size_bytes) == ("report.csv", "text/html", 4)
    assert stored.sha256 == hashlib.sha256(b"a,b\n").hexdigest()
    assert store.objects[stored.storage_key] == b"a,b\n"


def test_one_files_failure_is_marked_on_that_file_and_the_others_are_kept() -> None:
    store = InMemoryFileStore(refuse={hashlib.sha256(b"bad").hexdigest()})
    bad, good = _store(store, _file("bad.csv", b"bad"), _file("good.csv", b"good"))
    assert bad.storage_key is None and bad.size_bytes == 3 and bad.sha256 == hashlib.sha256(b"bad").hexdigest()
    assert good.storage_key is not None and store.objects[good.storage_key] == b"good"


def test_a_failed_upload_is_tried_again_once() -> None:
    store = InMemoryFileStore(fail_first=1)
    [stored] = _store(store, _file())
    assert stored.storage_key is not None and store.puts == 2


def test_a_bug_in_the_store_costs_the_file_never_the_answer() -> None:
    class Broken:
        def put(self, key: str, data: bytes) -> None:
            raise RuntimeError("a bug, not a refusal")

    [stored] = _store(Broken(), _file())
    assert stored.storage_key is None


def test_no_store_marks_every_file_as_not_stored() -> None:
    [stored] = _store(None, _file())
    assert stored.storage_key is None and stored.size_bytes == 4


def test_files_past_the_time_budget_are_marked_without_trying(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = iter([0.0, 1.0, agent_files.STORE_BUDGET_S + 1])
    monkeypatch.setattr(agent_files, "_monotonic", lambda: next(clock))
    store = InMemoryFileStore()
    first, second = _store(store, _file("a.csv", b"a"), _file("b.csv", b"b"))
    assert first.storage_key is not None and second.storage_key is None and store.puts == 1


def test_the_same_bytes_under_two_names_share_one_object() -> None:
    store = InMemoryFileStore()
    one, two = _store(store, _file("a.csv"), _file("b.csv"))
    assert one.storage_key == two.storage_key and len(store.objects) == 1 and (one.name, two.name) == ("a.csv", "b.csv")


class _Wire:
    """`requests` as the real store calls it."""

    def __init__(self, status: int = 200, body: bytes = b"", raises: Exception | None = None) -> None:
        self.status, self.body, self.raises = status, body, raises
        self.calls: list[dict[str, Any]] = []

    def request(self, method: str, url: str, **kwargs: Any) -> Any:
        self.calls.append({"method": method, "url": url, **kwargs})
        if self.raises:
            raise self.raises
        response = requests.Response()
        response.status_code = self.status
        response._content = self.body
        return response


@pytest.fixture()
def wire(monkeypatch: pytest.MonkeyPatch) -> _Wire:
    stub = _Wire()
    monkeypatch.setattr(agent_files.requests, "request", stub.request)
    return stub


def _real() -> agent_files.SupabaseStore:
    return agent_files.SupabaseStore("https://ref.supabase.co/", "sb_secret_x")


def test_the_real_store_uploads_to_the_private_bucket_idempotently_typed_as_nothing(wire: _Wire) -> None:
    _real().put("acc/7/2/abc", b"bytes")
    [call] = wire.calls
    assert (call["method"], call["url"]) == ("POST", "https://ref.supabase.co/storage/v1/object/agent-files/acc/7/2/abc")
    assert call["headers"]["apikey"] == "sb_secret_x" and call["headers"]["x-upsert"] == "true"
    assert call["headers"]["Content-Type"] == OCTET and call["data"] == b"bytes" and call["timeout"]


def test_the_real_store_reads_bytes_and_says_when_an_object_is_gone(wire: _Wire) -> None:
    wire.body = b"bytes"
    assert _real().get("acc/7/2/abc") == b"bytes"
    wire.status, wire.body = 404, b'{"error":"not_found"}'
    assert _real().get("acc/7/2/abc") is None
    # Storage has answered a missing object with HTTP 400 and the 404 in its body.
    wire.status, wire.body = 400, b'{"statusCode":"404","error":"not_found","message":"Object not found"}'
    assert _real().get("acc/7/2/abc") is None


def test_the_real_store_deletes_by_prefix_list(wire: _Wire) -> None:
    _real().delete(["a/1", "b/2"])
    [call] = wire.calls
    assert (call["method"], call["url"]) == ("DELETE", "https://ref.supabase.co/storage/v1/object/agent-files")
    assert call["json"] == {"prefixes": ["a/1", "b/2"]}


@pytest.mark.parametrize("status, body", [(500, b"boom"), (401, b"no"), (400, b'{"statusCode":"413","error":"too big"}')])
def test_a_refusal_is_a_store_error_that_never_carries_the_key(wire: _Wire, status: int, body: bytes) -> None:
    wire.status, wire.body = status, body
    for call in (lambda s: s.put("k", b"x"), lambda s: s.get("k"), lambda s: s.delete(["k"])):
        with pytest.raises(agent_files.StoreError) as caught:
            call(_real())
        assert "sb_secret_x" not in str(caught.value)


def test_a_broken_connection_is_a_store_error_that_never_carries_the_key(wire: _Wire) -> None:
    wire.raises = requests.ConnectionError("HTTPSConnectionPool apikey=sb_secret_x")
    with pytest.raises(agent_files.StoreError) as caught:
        _real().put("k", b"x")
    assert "sb_secret_x" not in str(caught.value)


@pytest.mark.parametrize("env", [{}, {"SUPABASE_URL": "https://x"}, {"SUPABASE_SERVICE_ROLE_KEY": "k"}])
def test_no_store_without_both_variables(monkeypatch: pytest.MonkeyPatch, env: dict[str, str]) -> None:
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    assert agent_files.store() is None


def test_the_bucket_is_private_and_its_limits_are_the_code_s() -> None:
    sql = (Path(__file__).parents[2] / "migrations" / "714_agent_files_bucket.sql").read_text()
    row = re.search(r"values \('([^']+)', '[^']+', false, (\d+), array\['([^']+)'\]\)", sql)
    assert row, "the bucket's row, public = false"
    assert row.group(1) == agent_files.BUCKET
    assert int(row.group(2)) == hosted_code.MAX_FILE_BYTES == 20 * 1024 * 1024
    assert row.group(3) == agent_files.STORED_TYPE == OCTET
