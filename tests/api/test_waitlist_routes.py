"""The two /waitlist routes, hermetic: every answer is a 303 to an allowed page, every refusal names its reason and
opens no connection, a bot's honeypot is never stored, a known email looks like a new one, and the landing page shows
exactly the consent wording the API records (api/waitlist.py CONSENT_TEXTS). The SQL itself runs on the real schema
in tests/test_waitlist_live.py."""

from __future__ import annotations

import contextlib
import html
import re
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest

pytest.importorskip("fastapi")
TestClient = pytest.importorskip("fastapi.testclient").TestClient

from api import dependencies as deps  # noqa: E402
from api import main as api_main  # noqa: E402
from api import waitlist  # noqa: E402
from api.routes import waitlist as routes  # noqa: E402
from tests.api._script_conn import ScriptConn  # noqa: E402

TOKEN = uuid.UUID("33333333-3333-3333-3333-333333333333")
LANDING = Path(__file__).resolve().parents[2] / "frontend" / "landing" / "index.html"


class _Opener:
    def __init__(self, conn: ScriptConn) -> None:
        self.conn, self.opened = conn, 0

    def __call__(self) -> Any:
        self.opened += 1
        return contextlib.nullcontext(self.conn)


def _install(monkeypatch: pytest.MonkeyPatch, conn: ScriptConn) -> _Opener:
    opener = _Opener(conn)
    monkeypatch.setattr(deps, "open_background_conn", opener)
    monkeypatch.setattr(routes, "_SIGNUP_LIMIT", waitlist.RateLimit(per_client=5, total=300, window_s=600))
    monkeypatch.setattr(routes, "_ANSWER_LIMIT", waitlist.RateLimit(per_client=10, total=600, window_s=600))
    monkeypatch.delenv("LANDING_ORIGIN", raising=False)
    monkeypatch.setenv("CORS_ALLOW_ORIGINS", "https://app.propscout.cz,http://localhost:5173")
    return opener


@pytest.fixture()
def fresh(monkeypatch: pytest.MonkeyPatch) -> tuple[_Opener, ScriptConn]:
    conn = ScriptConn([(lambda q: q.startswith("INSERT INTO waitlist_signups"), [(TOKEN,)]),
                       (lambda q: q.startswith("UPDATE waitlist_signups"), [(1,)])])
    return _install(monkeypatch, conn), conn


@pytest.fixture()
def client() -> Any:
    return TestClient(api_main.app, follow_redirects=False)


def _form(**over: str) -> dict[str, str]:
    body = {"email": "  Investor@Example.CZ ", "consent": waitlist.CONSENT_VERSION, "v": "flip", "next": "/dekujeme",
            "web": "", "utm_source": "facebook", "utm_campaign": "launch-1", "fbclid": "IwAR0abc"}
    body.update(over)
    return body


def _where(res: Any) -> tuple[str, dict[str, list[str]], str]:
    assert res.status_code == 303
    parts = urlsplit(res.headers["location"])
    return f"{parts.scheme}://{parts.netloc}{parts.path}", parse_qs(parts.query), parts.fragment


def test_signup_stores_the_normalized_email_and_lands_on_thanks_with_its_token(client: Any, fresh: Any) -> None:
    opener, conn = fresh
    url, query, _ = _where(client.post("/waitlist", data=_form()))
    assert url == "https://propscout.cz/dekujeme"
    assert query == {"t": [str(TOKEN)]}
    assert opener.opened == 1
    sql, params = conn.executed[0]
    assert sql.startswith("INSERT INTO waitlist_signups")
    assert params[:3] == ("investor@example.cz", waitlist.CONSENT_VERSION, "flip")
    assert params[3].obj == {"utm_source": "facebook", "utm_campaign": "launch-1"}  # fbclid is never kept


def test_a_known_email_lands_on_the_same_page_without_a_token(client: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, ScriptConn([(lambda q: q.startswith("INSERT INTO waitlist_signups"), [])]))
    url, query, _ = _where(client.post("/waitlist", data=_form()))
    assert (url, query) == ("https://propscout.cz/dekujeme", {})


@pytest.mark.parametrize(("over", "reason"), [
    ({"email": "not-an-address"}, "email"),
    ({"email": ""}, "email"),
    ({"email": "a@b"}, "email"),
    ({"consent": ""}, "obnovit"),
    ({"consent": "1999-01-01"}, "obnovit"),
])
def test_a_refusal_goes_back_to_the_form_with_its_reason_and_opens_nothing(
        client: Any, fresh: Any, over: dict[str, str], reason: str) -> None:
    opener, _ = fresh
    url, query, fragment = _where(client.post("/waitlist", data=_form(**over)))
    assert (url, query, fragment) == ("https://propscout.cz/", {"chyba": [reason], "v": ["flip"]}, "prihlaska")
    assert opener.opened == 0


def test_the_honeypot_looks_like_success_and_stores_nothing(client: Any, fresh: Any) -> None:
    opener, _ = fresh
    url, query, _ = _where(client.post("/waitlist", data=_form(web="https://spam.example")))
    assert (url, query) == ("https://propscout.cz/dekujeme", {})
    assert opener.opened == 0


def test_the_sixth_signup_from_one_client_in_ten_minutes_is_refused(client: Any, fresh: Any) -> None:
    opener, _ = fresh
    for i in range(5):
        assert "t" in _where(client.post("/waitlist", data=_form(email=f"a{i}@example.cz")))[1]
    url, query, _ = _where(client.post("/waitlist", data=_form(email="a6@example.cz")))
    assert query["chyba"] == ["limit"]
    assert opener.opened == 5


def test_a_database_failure_goes_back_to_the_form(client: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, ScriptConn([(lambda q: True, RuntimeError("db down"))]))
    _, query, _ = _where(client.post("/waitlist", data=_form()))
    assert query["chyba"] == ["server"]


def test_the_redirect_follows_an_allowed_origin_and_an_allowed_page_only(client: Any, fresh: Any) -> None:
    preview = client.post("/waitlist", data=_form(next="/landing/dekujeme.html"),
                          headers={"Origin": "https://app.propscout.cz"})
    assert _where(preview)[0] == "https://app.propscout.cz/landing/dekujeme.html"
    stranger = client.post("/waitlist", data=_form(next="//evil.example/x"), headers={"Origin": "https://evil.example"})
    assert _where(stranger)[0] == "https://propscout.cz/dekujeme"


def test_landing_origin_env_moves_the_default(client: Any, fresh: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANDING_ORIGIN", "https://www.example.cz/")
    assert _where(client.post("/waitlist", data=_form()))[0] == "https://www.example.cz/dekujeme"


def test_an_answer_is_stored_once_with_only_known_values(client: Any, fresh: Any) -> None:
    opener, conn = fresh
    res = client.post("/waitlist/answer", data={"t": str(TOKEN), "investor_type": "flip", "note": "  hlídám Brno  ",
                                                "next": "/dekujeme"})
    assert _where(res)[:2] == ("https://propscout.cz/dekujeme", {"hotovo": ["1"]})
    sql, params = conn.executed[0]
    assert sql.startswith("UPDATE waitlist_signups") and "answered_at IS NULL" in sql
    assert params == ("flip", "hlídám Brno", TOKEN)
    client.post("/waitlist/answer", data={"t": str(TOKEN), "investor_type": "landlord", "note": "x"})
    assert conn.executed[1][1] == (None, "x", TOKEN)
    assert opener.opened == 2


def test_an_answer_without_a_valid_token_or_content_opens_nothing(client: Any, fresh: Any) -> None:
    opener, _ = fresh
    for data in ({"t": "nope", "investor_type": "flip"}, {"t": str(TOKEN)}, {"t": str(TOKEN), "note": "   "}):
        assert _where(client.post("/waitlist/answer", data=data))[1] == {"hotovo": ["1"]}
    assert opener.opened == 0


def test_rate_limit_window_slides() -> None:
    limit = waitlist.RateLimit(per_client=2, total=3, window_s=10)
    assert limit.allow("a", now=0) and limit.allow("a", now=1)
    assert not limit.allow("a", now=2)
    assert limit.allow("b", now=2)
    assert not limit.allow("c", now=3)  # the total is spent
    assert limit.allow("a", now=11)  # the first two left the window


def _visible_text(fragment: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", fragment)).split())


def test_the_landing_page_shows_the_recorded_consent_wording_under_every_form() -> None:
    page = LANDING.read_text(encoding="utf-8")
    forms = re.findall(r"<form\b[^>]*>.*?</form>", page, flags=re.S)
    assert len(forms) >= 2, "the hero form and the closing form"
    expected = " ".join(waitlist.CONSENT_TEXTS[waitlist.CONSENT_VERSION].split())
    for form in forms:
        assert f'name="consent" value="{waitlist.CONSENT_VERSION}"' in form
        assert expected in _visible_text(form)
        assert 'name="web"' in form, "the honeypot"
