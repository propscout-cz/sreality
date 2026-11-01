"""The /waitlist routes, hermetic: every answer is a 303 to an allowed page, every refusal names its reason and opens
no connection, a bot's honeypot is never stored, a known email looks like a new one, only a new entry gets a
confirmation and nothing about it reaches the visitor, an unsubscribe changes nothing until the POST, and the landing
page shows exactly the consent wording the API records (api/waitlist.py CONSENT_TEXTS). The SQL itself runs on the real
schema in tests/test_waitlist_live.py."""

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
from api import resend_client, waitlist, waitlist_email  # noqa: E402
from api.http_json import HttpJsonError  # noqa: E402
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


def _resend(monkeypatch: pytest.MonkeyPatch, *, fail: bool = False) -> list[dict[str, Any]]:
    sent: list[dict[str, Any]] = []

    def fake_send(**kwargs: Any) -> str:
        if fail:
            raise HttpJsonError("api.resend.com", "status", 429)
        sent.append(kwargs)
        return "id-1"

    monkeypatch.setenv("RESEND_API_KEY", "re_test")
    monkeypatch.setattr(resend_client, "send", fake_send)
    return sent


def _confirming(monkeypatch: pytest.MonkeyPatch, claimed: list[tuple[Any, ...]]) -> ScriptConn:
    conn = ScriptConn([(lambda q: q.startswith("INSERT INTO waitlist_signups"), [(TOKEN,)]),
                       (lambda q: "SET confirmation_sent_at = now()" in q, claimed),
                       (lambda q: "SET confirmation_sent_at = NULL" in q, [])])
    _install(monkeypatch, conn)
    return conn


def test_a_new_entry_gets_the_confirmation_with_its_unsubscribe_link(client: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    sent = _resend(monkeypatch)
    _confirming(monkeypatch, [("investor@example.cz",)])
    url, query, _ = _where(client.post("/waitlist", data=_form()))
    assert url == "https://propscout.cz/dekujeme" and query["t"] == [str(TOKEN)]
    assert len(sent) == 1
    mail = sent[0]
    link = f"https://api.propscout.cz/waitlist/unsubscribe?t={TOKEN}"
    assert mail["to"] == "investor@example.cz" and mail["sender"] == "PropScout <tym@novinky.propscout.cz>"
    assert mail["reply_to"] == "support@propscout.cz"
    assert link in mail["html"] and link in mail["text"]
    assert mail["headers"] == {"List-Unsubscribe": f"<{link}>", "List-Unsubscribe-Post": "List-Unsubscribe=One-Click"}
    assert "599 Kč měsíčně včetně DPH" in mail["html"] and "599 Kč měsíčně včetně DPH" in mail["text"]


def test_a_known_email_gets_no_second_confirmation(client: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    sent = _resend(monkeypatch)
    _install(monkeypatch, ScriptConn([(lambda q: q.startswith("INSERT INTO waitlist_signups"), [])]))
    client.post("/waitlist", data=_form())
    assert sent == []


def test_without_a_key_no_claim_and_no_send(client: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _confirming(monkeypatch, [("investor@example.cz",)])
    _, query, _ = _where(client.post("/waitlist", data=_form()))
    assert query["t"] == [str(TOKEN)]
    assert not any("confirmation_sent_at" in q for q, _ in conn.executed)


def test_over_the_cap_the_entry_stands_and_no_email_goes(client: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    sent = _resend(monkeypatch)
    _confirming(monkeypatch, [])
    url, _, _ = _where(client.post("/waitlist", data=_form()))
    assert url == "https://propscout.cz/dekujeme" and sent == []


def test_a_failed_send_leaves_the_count_and_never_the_visitor(client: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    _resend(monkeypatch, fail=True)
    conn = _confirming(monkeypatch, [("investor@example.cz",)])
    url, _, _ = _where(client.post("/waitlist", data=_form()))
    assert url == "https://propscout.cz/dekujeme"
    assert any("SET confirmation_sent_at = NULL" in q for q, _ in conn.executed)


def test_a_broken_database_after_the_signup_never_reaches_the_visitor(client: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    _resend(monkeypatch)
    _confirming(monkeypatch, [("investor@example.cz",)])
    monkeypatch.setattr(waitlist, "claim_confirmation", lambda *_: 1 / 0)
    url, _, _ = _where(client.post("/waitlist", data=_form()))
    assert url == "https://propscout.cz/dekujeme"


def test_opening_the_unsubscribe_link_only_asks(client: Any, fresh: Any) -> None:
    opener, _ = fresh
    res = client.get(f"/waitlist/unsubscribe?t={TOKEN}")
    assert res.status_code == 200 and f'action="?t={TOKEN}"' in res.text and 'method="post"' in res.text
    assert opener.opened == 0


def test_the_one_click_post_withdraws_and_a_repeat_reads_the_same(client: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    conn = ScriptConn([(lambda q: "SET unsubscribed_at = now()" in q, [])])
    _install(monkeypatch, conn)
    monkeypatch.setattr(routes, "_UNSUBSCRIBE_LIMIT", waitlist.RateLimit(per_client=30, total=600, window_s=600))
    first = client.post(f"/waitlist/unsubscribe?t={TOKEN}", data={"List-Unsubscribe": "One-Click"})
    second = client.post(f"/waitlist/unsubscribe?t={TOKEN}", data={"List-Unsubscribe": "One-Click"})
    assert first.status_code == second.status_code == 200
    assert "Jste odhlášeni" in first.text and first.text == second.text
    assert sum("SET unsubscribed_at = now()" in q for q, _ in conn.executed) == 2


@pytest.mark.parametrize("method", ["get", "post"])
def test_a_malformed_unsubscribe_token_opens_nothing(client: Any, fresh: Any, method: str) -> None:
    opener, _ = fresh
    res = getattr(client, method)("/waitlist/unsubscribe?t=not-a-token")
    assert res.status_code == 400 and opener.opened == 0


def test_the_email_names_every_promise_the_thank_you_page_makes() -> None:
    text = waitlist_email.confirmation_text(TOKEN)
    assert "v pořadí, v jakém se lidé přihlásili" in text and "přidat na plochu telefonu" in text
    thanks = (LANDING.parent / "dekujeme.html").read_text(encoding="utf-8")
    assert "Kromě potvrzení vám do té doby nic dalšího nepřijde." in thanks


def test_no_page_or_email_still_says_the_old_price_word() -> None:
    for page in ("index.html", "dekujeme.html", "soukromi.html"):
        assert "zakladatel" not in (LANDING.parent / page).read_text(encoding="utf-8").lower()
    assert "zakladatel" not in waitlist_email.confirmation_html(TOKEN).lower()


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


def _signup_row(i: int) -> tuple[Any, ...]:
    return (i, f"a{i}@example.cz", "2026-10-31T17:53:57+00:00", waitlist.CONSENT_VERSION, "flip",
            {"utm_source": "facebook"}, "rent", "Brno", None, None, None)


def test_the_signups_list_is_admin_only(client: Any) -> None:
    api_main.app.dependency_overrides.pop(deps.require_admin, None)
    assert client.get("/admin/waitlist").status_code == 401


def test_an_admin_reads_every_signup_newest_first(client: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    conn = ScriptConn([(lambda q: q.startswith("SELECT id, email"), [_signup_row(2), _signup_row(1)])])
    api_main.app.dependency_overrides[deps.get_db_conn] = lambda: conn
    api_main.app.dependency_overrides[deps.require_admin] = lambda: {"is_admin": True}
    try:
        body = client.get("/admin/waitlist").json()
    finally:
        api_main.app.dependency_overrides.clear()
    assert body["truncated"] is False
    assert [r["email"] for r in body["data"]] == ["a2@example.cz", "a1@example.cz"]
    assert set(body["data"][0]) == set(waitlist.SIGNUP_COLUMNS)
    sql, params = conn.executed[0]
    assert "ORDER BY created_at DESC, id DESC" in sql and params == (waitlist.LIST_MAX + 1,)


def test_the_signups_list_says_when_it_hit_its_cap(client: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(waitlist, "LIST_MAX", 2)
    conn = ScriptConn([(lambda q: q.startswith("SELECT id, email"), [_signup_row(3), _signup_row(2), _signup_row(1)])])
    api_main.app.dependency_overrides[deps.get_db_conn] = lambda: conn
    api_main.app.dependency_overrides[deps.require_admin] = lambda: {"is_admin": True}
    try:
        body = client.get("/admin/waitlist").json()
    finally:
        api_main.app.dependency_overrides.clear()
    assert body["truncated"] is True and len(body["data"]) == 2
