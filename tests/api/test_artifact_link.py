"""The per-property "Artifact link" (migration 599): one http(s) URL per (account, property).

Hermetic like tests/api/test_curation.py -- the route layer runs for real (so request
validation is exercised), the persistence functions are replaced or fed a scripted cursor.
The CHECK constraint and the composite key are the database's half (a live test owns those).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest

fastapi = pytest.importorskip("fastapi")
TestClient = pytest.importorskip("fastapi.testclient").TestClient

from api import curation
from api import dependencies as deps
from api import main as api_main
from api import schemas as s
from api import tenant_pool

_SUB = "11111111-1111-1111-1111-111111111111"
_ACCT = "22222222-2222-2222-2222-222222222222"
_URL = "https://claude.ai/artifact/abc123"


@pytest.fixture()
def client(monkeypatch):
    api_main.app.dependency_overrides[deps.get_db_conn] = lambda: object()
    api_main.app.dependency_overrides[tenant_pool.tenant_conn] = lambda: object()
    api_main.app.dependency_overrides[deps.verify_jwt] = lambda: {"sub": _SUB}
    monkeypatch.setattr(tenant_pool, "resolve_account_id", lambda conn, claims: _ACCT)
    yield TestClient(api_main.app)
    api_main.app.dependency_overrides.clear()


@pytest.fixture()
def saved(monkeypatch):
    """Capture what the route hands the persistence function."""
    calls: list[tuple[int, str, str]] = []

    def fake_set(conn, property_id, body, account_id):
        calls.append((property_id, body.url, account_id))
        return {"property_id": property_id, "url": body.url,
                "created_at": "2026-10-14T00:00:00+00:00", "updated_at": "2026-10-14T00:00:00+00:00"}

    monkeypatch.setattr(curation, "set_artifact_link", fake_set)
    return calls


def test_put_stores_the_url_for_the_resolved_account(client, saved):
    res = client.put("/properties/42/artifact-link", json={"url": _URL})
    assert res.status_code == 200
    assert res.json()["url"] == _URL
    assert saved == [(42, _URL, _ACCT)]


def test_put_trims_surrounding_whitespace_and_accepts_any_scheme_case(client, saved):
    res = client.put("/properties/42/artifact-link", json={"url": "  HTTP://example.cz/a?b=1#c \n"})
    assert res.status_code == 200
    assert saved[0][1] == "HTTP://example.cz/a?b=1#c"


@pytest.mark.parametrize("url", [
    "",
    "   ",
    "javascript:alert(1)",
    "data:text/html,<script>alert(1)</script>",
    "ftp://example.cz/file",
    "//example.cz/path",
    "example.cz/path",
    "https://",
    "https://exa mple.cz/a b",
    "https://example.cz/" + "x" * 2000,
])
def test_put_refuses_what_is_not_a_clickable_http_url(client, saved, url):
    res = client.put("/properties/42/artifact-link", json={"url": url})
    assert res.status_code == 422
    assert saved == []


def test_put_without_a_url_is_422(client, saved):
    assert client.put("/properties/42/artifact-link", json={}).status_code == 422


# --- the persistence functions, against a scripted cursor -------------------------------------


class _Tx:
    def __enter__(self) -> "_Tx":
        return self

    def __exit__(self, *exc: Any) -> None:
        return None


class _Cur:
    def __init__(self, conn: "_Conn") -> None:
        self._conn, self._rows, self.rowcount = conn, [], 0

    def __enter__(self) -> "_Cur":
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def execute(self, sql: str, params: Any = None) -> None:
        text = " ".join(sql.split())
        self._conn.executed.append((text, params))
        for matches, rows in self._conn.script:
            if matches(text):
                self._rows, self.rowcount = list(rows), len(rows)
                return
        self._rows, self.rowcount = [], 0

    def fetchone(self) -> Any:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[tuple]:
        return list(self._rows)


class _Conn:
    """Answers each statement from the first (matcher, rows) that matches its text."""

    def __init__(self, script: list[tuple[Any, list[tuple]]]) -> None:
        self.script = script
        self.executed: list[tuple[str, Any]] = []

    def transaction(self) -> _Tx:
        return _Tx()

    def cursor(self) -> _Cur:
        return _Cur(self)


_ROW = (42, _URL, datetime(2026, 10, 14, 8, 0), datetime(2026, 10, 14, 9, 0))


def test_get_returns_the_link_or_an_explicit_none():
    conn = _Conn([(lambda q: "RECURSIVE chain" in q, [(42, 42)]),
                  (lambda q: "FROM property_artifact_links" in q, [_ROW])])
    assert curation.get_artifact_link(conn, 42) == {"data": {
        "property_id": 42, "url": _URL,
        "created_at": "2026-10-14T08:00:00", "updated_at": "2026-10-14T09:00:00"}}
    empty = _Conn([(lambda q: "RECURSIVE chain" in q, [(42, 42)])])
    assert curation.get_artifact_link(empty, 42) == {"data": None}


def test_get_follows_a_merged_away_id_to_its_survivor():
    conn = _Conn([(lambda q: "RECURSIVE chain" in q, [(99, 42)]),
                  (lambda q: "FROM property_artifact_links" in q, [_ROW])])
    curation.get_artifact_link(conn, 99)
    read = [p for q, p in conn.executed if "FROM property_artifact_links" in q]
    assert read == [(42,)]


def test_set_is_an_upsert_on_the_account_property_key_that_names_its_owner():
    conn = _Conn([(lambda q: "RECURSIVE chain" in q, [(42, 42)]),
                  (lambda q: "INSERT INTO property_artifact_links" in q, [_ROW])])
    out = curation.set_artifact_link(conn, 42, s.SetArtifactLinkIn(url=_URL), account_id=_ACCT)
    assert out["url"] == _URL
    sql, params = next((q, p) for q, p in conn.executed if "INSERT INTO property_artifact_links" in q)
    assert "ON CONFLICT (account_id, property_id) DO UPDATE" in sql
    assert params == (_ACCT, 42, _URL)


def test_set_on_a_merged_away_id_writes_the_survivor():
    conn = _Conn([(lambda q: "RECURSIVE chain" in q, [(99, 42)]),
                  (lambda q: "INSERT INTO property_artifact_links" in q, [_ROW])])
    curation.set_artifact_link(conn, 99, s.SetArtifactLinkIn(url=_URL), account_id=_ACCT)
    params = next(p for q, p in conn.executed if "INSERT INTO property_artifact_links" in q)
    assert params[1] == 42


def test_set_on_an_unknown_property_is_404():
    conn = _Conn([(lambda q: "RECURSIVE chain" in q, [])])
    with pytest.raises(fastapi.HTTPException) as err:
        curation.set_artifact_link(conn, 7, s.SetArtifactLinkIn(url=_URL), account_id=_ACCT)
    assert err.value.status_code == 404


def test_set_without_an_account_is_a_type_error():
    conn = _Conn([])
    with pytest.raises(TypeError, match="account_id"):
        curation.set_artifact_link(conn, 42, s.SetArtifactLinkIn(url=_URL))  # type: ignore[call-arg]


def test_delete_reports_whether_a_row_went_and_is_idempotent():
    resolve = (lambda q: "RECURSIVE chain" in q, [(42, 42)])
    gone = _Conn([resolve, (lambda q: "DELETE FROM property_artifact_links" in q, [(1,)])])
    assert curation.delete_artifact_link(gone, 42) == {"deleted": True}
    none = _Conn([resolve])
    assert curation.delete_artifact_link(none, 42) == {"deleted": False}
