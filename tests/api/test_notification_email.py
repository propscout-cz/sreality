"""The notification email, hermetic (docs/design/email/PROGRAM.md PR 4): its Czech words (the subject's plurals, one
push-worded line per event, no sender on a share, no per-m² figure, escaped portal labels, the 50-line cap), the shared
left-aligned frame, one pass's claim → read → send → record on a scripted connection (sent, empty, failed by its HTTP
status alone, no address in the log), and the routes: the account's own setting (never acted for) and the off link,
which only asks on GET. The SQL itself runs on the real schema in tests/test_notification_email_live.py."""

from __future__ import annotations

import contextlib
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

pytest.importorskip("fastapi")
TestClient = pytest.importorskip("fastapi.testclient").TestClient

from api import dependencies as deps  # noqa: E402
from api import main as api_main  # noqa: E402
from api import notification_email as ne  # noqa: E402
from api import tenant_pool, waitlist, waitlist_email  # noqa: E402
from api.http_json import HttpJsonError  # noqa: E402
from api.property_sheet import NBSP  # noqa: E402
from api.routes import notification_email as routes  # noqa: E402
from tests.api._script_conn import ScriptConn  # noqa: E402

TOKEN = uuid.UUID("55555555-5555-4555-8555-555555555555")
ACCOUNT = uuid.UUID("22222222-2222-2222-2222-222222222222")
OTHER = uuid.UUID("44444444-4444-4444-4444-444444444444")
ADDRESS = "investor@example.cz"
UNTIL = datetime(2026, 11, 3, 6, 0, tzinfo=timezone.utc)
SINCE = UNTIL - timedelta(hours=24)
# 07:30 in Prague (CET, UTC+1): the day is open.
MORNING = datetime(2026, 11, 3, 6, 30, tzinfo=timezone.utc)
PRICE = f"5{NBSP}150{NBSP}000{NBSP}Kč"


def _row(i: int, kind: str = "new", **over: Any) -> dict[str, Any]:
    row = {"id": f"00000000-0000-4000-8000-{i:012d}", "change_kind": kind, "trigger_price_czk": 5_150_000,
           "prev_price_czk": None, "display_label": "Praha 5", "category_main": "byt", "subtype": None,
           "disposition": "2+kk", "price_unit": None}
    row.update(over)
    return row


# --- the words --------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(("counts", "subject"), [
    ({"new": 1}, "1 nový inzerát pro vaše hledání"),
    ({"new": 3, "price_drop": 1}, "3 nové inzeráty a 1 zlevnění pro vaše hledání"),
    ({"new": 5}, "5 nových inzerátů pro vaše hledání"),
    ({"new": 21}, "21 nových inzerátů pro vaše hledání"),
    ({"price_drop": 2}, "2 zlevnění pro vaše hledání"),
    ({"new": 2, "price_drop": 2, "inactive": 1, "shared_collection": 1},
     "2 nové inzeráty, 2 zlevnění a 2 další změny pro vaše hledání"),
    ({"inactive": 1}, "1 další změna pro vaše hledání"),
    ({"price_rise": 4, "reactivated": 1}, "5 dalších změn pro vaše hledání"),
])
def test_the_subject_counts_in_czech(counts: dict[str, int], subject: str) -> None:
    assert ne.subject(counts) == subject


def test_a_daily_email_says_each_event_the_way_push_does_and_links_it() -> None:
    rows = [
        _row(1),
        _row(2, "price_drop", display_label="Brno – Žabovřesky", trigger_price_czk=4_290_000,
             prev_price_czk=4_490_000),
        _row(3, "shared_collection", display_label=None, category_main=None, disposition=None,
             trigger_price_czk=None),
    ]
    email = ne.compose("daily", rows, {"new": 1, "price_drop": 1, "shared_collection": 1}, TOKEN)
    assert email.subject == "1 nový inzerát, 1 zlevnění a 1 další změna pro vaše hledání"
    off = f"https://api.propscout.cz/notifications/email/unsubscribe?t={TOKEN}"
    for part in (email.html, email.text):
        assert "Dobrý den," in part
        assert "tohle se za posledních 24 hodin změnilo u nabídek, které hlídáte:" in part
        assert "https://app.propscout.cz/n/00000000-0000-4000-8000-000000000001" in part
        assert "https://app.propscout.cz/notifications" in part and "Otevřít v PropScoutu" in part
        assert "Tento přehled chodí jednou denně, protože máte zapnutá e-mailová upozornění." in part
        assert "Změnit to můžete v aplikaci pod svým profilem." in part
        assert off in part and "Vypnout" in part
        assert "PropScout.cz · Provozovatel: Limen Ventures s.r.o., IČO 14053179" in part
        assert "m²" not in part and "/m2" not in part, "no per-m² figure (rule 23)"
        assert "a dalších" not in part
    assert f"Nový inzerát · Praha 5: Byt 2+kk · {PRICE}" in email.text
    assert f"Zlevněno · Brno – Žabovřesky: 4{NBSP}490{NBSP}000{NBSP}Kč → 4{NBSP}290{NBSP}000{NBSP}Kč" in email.text
    assert "Sdíleno s vámi: Sbírka" in email.text, "a share names no sender (F17)"
    assert f"<strong>Nový inzerát · Praha 5:</strong> Byt 2+kk · {PRICE}" in email.html
    preview = email.html.split("</div>", 1)[0]
    assert "display:none" in preview and f"Nový inzerát · Praha 5: Byt 2+kk · {PRICE}" in preview


def test_an_instant_email_says_why_it_came_now() -> None:
    email = ne.compose("instant", [_row(1)], {"new": 1}, TOKEN)
    for part in (email.html, email.text):
        assert "tohle se právě změnilo u nabídek, které hlídáte:" in part
        assert "Tato upozornění chodí nejvýš jednou za 15 minut, protože jste si je tak nastavili." in part
        assert "jednou denně" not in part


def test_a_portal_label_is_escaped() -> None:
    email = ne.compose("daily", [_row(1, display_label='<img src=x onerror="a()">')], {"new": 1}, TOKEN)
    assert "<img" not in email.html and "&lt;img src=x onerror=&quot;a()&quot;&gt;" in email.html


def test_past_fifty_lines_the_rest_wait_in_the_app() -> None:
    rows = [_row(i) for i in range(ne.MAX_LINES)]
    email = ne.compose("daily", rows, {"new": ne.MAX_LINES + 3}, TOKEN)
    assert email.text.count("https://app.propscout.cz/n/") == ne.MAX_LINES
    assert "a dalších 3 v aplikaci" in email.html and "a dalších 3 v aplikaci" in email.text


def test_every_email_sits_left_aligned_in_one_frame() -> None:
    for page in (ne.compose("daily", [_row(1)], {"new": 1}, TOKEN).html, waitlist_email.confirmation_html(TOKEN)):
        assert "max-width:520px;margin:0;padding:24px" in page and "margin:0 auto" not in page
        assert page.endswith("PropScout.cz · Provozovatel: Limen Ventures s.r.o., IČO 14053179</p></div>")


# --- one pass ---------------------------------------------------------------------------------------------------


def _is(sql: str) -> Any:
    text = " ".join(sql.split())
    return lambda q: q == text


def _script(mode: str = "daily", counts: list[tuple[str, int]] | None = None,
            rows: list[dict[str, Any]] | None = None) -> ScriptConn:
    counts = [("new", 1)] if counts is None else counts
    rows = [_row(1)] if rows is None else rows
    claim_sql = ne.DAILY_CLAIM_SQL if mode == "daily" else ne.INSTANT_CLAIM_SQL
    return ScriptConn([
        (_is(ne.ENSURE_SETTINGS_SQL), []),
        (_is(ne.CANDIDATES_SQL), [(ACCOUNT, mode, ADDRESS)]),
        (_is(ne.LOCK_SQL), [(mode, TOKEN)]),
        (_is(ne.SINCE_SQL), [(SINCE,)]),
        (_is(claim_sql), [(7, 1, UNTIL)]),
        (_is(ne.COUNTS_SQL), counts),
        (_is(ne.EVENTS_SQL), [tuple(r.values()) for r in rows]),
    ])


class _Conn(ScriptConn):
    closed = False

    def close(self) -> None:
        self.closed = True


def _pass(conn: ScriptConn, send: Any, **kw: Any) -> dict[str, Any]:
    kw.setdefault("now", MORNING)
    return ne.deliver_once(lambda: conn, stop=lambda: False, send=send, sleep=lambda s: None, **kw)


def _params(conn: ScriptConn, sql: str) -> list[Any]:
    text = " ".join(sql.split())
    return [p for q, p in conn.executed if q == text]


def test_a_pass_claims_reads_sends_and_records_the_email(caplog: pytest.LogCaptureFixture) -> None:
    conn = _script(counts=[("new", 2), ("price_drop", 1)], rows=[_row(1), _row(2)])
    sent: list[dict[str, Any]] = []
    with caplog.at_level(logging.INFO, logger="api.notification_email"):
        out = _pass(conn, lambda **mail: sent.append(mail) or "re_1")
    assert out["raised"] == {} and out["attempted"] == ["settings", "select", "send"]
    assert {k: out[k] for k in ("daily_open", "candidates", "claimed", "sent", "empty", "failed")} == {
        "daily_open": True, "candidates": 1, "claimed": 1, "sent": 1, "empty": 0, "failed": 0}
    [mail] = sent
    off = f"https://api.propscout.cz/notifications/email/unsubscribe?t={TOKEN}"
    assert mail["to"] == ADDRESS and mail["sender"] == "PropScout <tym@mail.propscout.cz>"
    assert mail["reply_to"] == "support@propscout.cz"
    assert mail["subject"] == "2 nové inzeráty a 1 zlevnění pro vaše hledání"
    assert mail["headers"] == {"List-Unsubscribe": f"<{off}>", "List-Unsubscribe-Post": "List-Unsubscribe=One-Click"}
    assert _params(conn, ne.CANDIDATES_SQL)[0] == {"daily_open": True, "day": MORNING.date(), "max_attempts": 3,
                                                   "limit": ne.MAX_ACCOUNTS}
    assert _params(conn, ne.DAILY_CLAIM_SQL)[0]["day"] == MORNING.date()
    window = _params(conn, ne.EVENTS_SQL)[0]
    assert window == {"account": ACCOUNT, "since": SINCE, "until": UNTIL, "limit": ne.MAX_LINES}
    assert _params(conn, ne.SENT_SQL) == [{"id": 7, "attempts": 1, "message_id": "re_1", "events": 3}]
    assert conn.tx == ["commit"], "the claim commits before the send"
    assert ADDRESS not in caplog.text and str(TOKEN) not in caplog.text and "EMAIL candidates=1 sent=1" in caplog.text


def test_nothing_to_say_is_recorded_empty_and_sends_nothing() -> None:
    conn = _script(counts=[])
    out = _pass(conn, lambda **mail: pytest.fail("an empty day sends nothing"))
    assert out["empty"] == 1 and out["sent"] == 0 and out["raised"] == {}
    assert _params(conn, ne.EMPTY_SQL) == [{"id": 7, "attempts": 1}]
    assert _params(conn, ne.EVENTS_SQL) == []


def test_a_failed_send_is_its_status_alone(caplog: pytest.LogCaptureFixture) -> None:
    conn = _script()

    def refused(**mail: Any) -> str:
        raise HttpJsonError("api.resend.com", "status", 429, {"message": f"rate limited for {ADDRESS}"})

    with caplog.at_level(logging.DEBUG):
        out = _pass(conn, refused)
    assert out["failed"] == 1 and out["raised"] == {"send": "1 of 1 accounts failed: status_429"}
    assert _params(conn, ne.FAILED_SQL) == [{"id": 7, "attempts": 1, "reason": "status_429", "events": 1}]
    assert ADDRESS not in caplog.text


def test_a_broken_read_after_the_claim_marks_it_failed_and_names_the_class() -> None:
    conn = _script()
    conn.script.insert(0, (_is(ne.COUNTS_SQL), RuntimeError(f"row for {ADDRESS}")))
    out = _pass(conn, lambda **mail: pytest.fail("nothing was read"))
    assert out["raised"] == {"send": "1 of 1 accounts failed: RuntimeError"} and out["claimed"] == 0
    assert _params(conn, ne.FAILED_SQL) == [{"id": 7, "attempts": 1, "reason": "RuntimeError", "events": 0}]


def test_a_claim_the_account_no_longer_wants_sends_nothing() -> None:
    conn = _script()
    conn.script.insert(0, (_is(ne.LOCK_SQL), [("off", TOKEN)]))
    out = _pass(conn, lambda **mail: pytest.fail("switched off since the select"))
    assert out["claimed"] == 0 and out["candidates"] == 1
    assert _params(conn, ne.DAILY_CLAIM_SQL) == []


def test_an_instant_claim_is_the_instant_statement() -> None:
    conn = _script("instant")
    out = _pass(conn, lambda **mail: "re_2")
    assert out["sent"] == 1 and len(_params(conn, ne.INSTANT_CLAIM_SQL)) == 1
    assert _params(conn, ne.DAILY_CLAIM_SQL) == []


def test_before_seven_in_prague_the_day_is_not_open() -> None:
    conn = _script()
    conn.script.insert(0, (_is(ne.CANDIDATES_SQL), []))
    out = _pass(conn, lambda **mail: "x", now=datetime(2026, 11, 3, 5, 59, tzinfo=timezone.utc))
    assert out["daily_open"] is False
    assert _params(conn, ne.CANDIDATES_SQL)[0]["daily_open"] is False
    summer = ne.prague_now(datetime(2026, 7, 1, 5, 0, tzinfo=timezone.utc))
    assert (summer.hour, summer.date().isoformat()) == (7, "2026-07-01"), "CEST is UTC+2"
    assert ne.prague_now(datetime(2026, 11, 2, 23, 30, tzinfo=timezone.utc)).date().isoformat() == "2026-11-03"


def test_the_stop_signal_ends_the_pass_before_the_next_account_and_the_connection_closes() -> None:
    conn = _Conn(_script().script)
    out = ne.deliver_once(lambda: conn, stop=lambda: True, send=lambda **m: pytest.fail("stopped"), now=MORNING)
    assert out["stopped"] is True and out["claimed"] == 0 and conn.closed


def test_a_step_that_raises_is_its_own_cell() -> None:
    conn = _script()
    conn.script.insert(0, (_is(ne.ENSURE_SETTINGS_SQL), RuntimeError("x")))
    conn.script.insert(0, (_is(ne.CANDIDATES_SQL), RuntimeError("y")))
    out = _pass(conn, lambda **mail: "x")
    assert out["raised"] == {"settings": "RuntimeError", "select": "RuntimeError"} and out["candidates"] == 0


# --- the routes -------------------------------------------------------------------------------------------------


class _Opener:
    def __init__(self, conn: ScriptConn) -> None:
        self.conn, self.opened = conn, 0

    def __call__(self) -> Any:
        self.opened += 1
        return contextlib.nullcontext(self.conn)


@pytest.fixture()
def service(monkeypatch: pytest.MonkeyPatch) -> _Opener:
    opener = _Opener(ScriptConn([
        (_is(ne.READ_MODE_SQL), [("instant",)]),
        (_is(ne.SET_MODE_SQL), [("off",)]),
        (_is(ne.UNSUBSCRIBE_SQL), [(1,)]),
    ]))
    monkeypatch.setattr(deps, "open_background_conn", opener)
    monkeypatch.setattr(routes, "_UNSUBSCRIBE_LIMIT", waitlist.RateLimit(per_client=30, total=600, window_s=600))
    return opener


@pytest.fixture()
def client(service: _Opener, monkeypatch: pytest.MonkeyPatch) -> Any:
    api_main.app.dependency_overrides[tenant_pool.tenant_conn] = lambda: ScriptConn([])
    api_main.app.dependency_overrides[deps.verify_jwt] = lambda: {"sub": "11111111-1111-1111-1111-111111111111"}
    monkeypatch.setattr(tenant_pool, "resolve_account_id", lambda c, claims: ACCOUNT)
    yield TestClient(api_main.app)
    api_main.app.dependency_overrides.clear()


def test_the_setting_is_the_callers_own(client: Any, service: _Opener) -> None:
    assert client.get("/account/notification-email").json() == {"mode": "instant"}
    assert _params(service.conn, ne.READ_MODE_SQL) == [{"account": ACCOUNT}]
    assert client.put("/account/notification-email", json={"mode": "off"}).json() == {"mode": "off"}
    assert _params(service.conn, ne.SET_MODE_SQL) == [{"account": ACCOUNT, "mode": "off"}]


def test_no_row_reads_as_the_daily_default(client: Any, service: _Opener) -> None:
    service.conn.script.insert(0, (_is(ne.READ_MODE_SQL), []))
    assert client.get("/account/notification-email").json() == {"mode": "daily"}


def test_an_unknown_mode_is_refused_before_any_connection(client: Any, service: _Opener) -> None:
    for body in ({"mode": "weekly"}, {"mode": None}, {}):
        assert client.put("/account/notification-email", json=body).status_code == 422
    assert service.opened == 0


def test_the_setting_never_acts_for_another_account(client: Any, service: _Opener) -> None:
    acting = {"X-Act-As": str(OTHER)}
    assert client.get("/account/notification-email", headers=acting).status_code == 403
    assert client.put("/account/notification-email", json={"mode": "off"}, headers=acting).status_code == 403
    assert service.opened == 0


def test_opening_the_off_link_only_asks(client: Any, service: _Opener) -> None:
    res = client.get(f"/notifications/email/unsubscribe?t={TOKEN}")
    assert res.status_code == 200 and f'action="?t={TOKEN}"' in res.text and 'method="post"' in res.text
    assert service.opened == 0


def test_the_one_click_post_switches_off_and_a_repeat_reads_the_same(client: Any, service: _Opener) -> None:
    first = client.post(f"/notifications/email/unsubscribe?t={TOKEN}", data={"List-Unsubscribe": "One-Click"})
    service.conn.script.insert(0, (_is(ne.UNSUBSCRIBE_SQL), []))
    again = client.post(f"/notifications/email/unsubscribe?t={TOKEN}", data={"List-Unsubscribe": "One-Click"})
    assert first.status_code == again.status_code == 200 and first.text == again.text
    assert _params(service.conn, ne.UNSUBSCRIBE_SQL) == [{"token": TOKEN}, {"token": TOKEN}]


@pytest.mark.parametrize("method", ["get", "post"])
def test_a_malformed_token_opens_nothing(client: Any, service: _Opener, method: str) -> None:
    res = getattr(client, method)("/notifications/email/unsubscribe?t=not-a-token")
    assert res.status_code == 400 and service.opened == 0


def test_the_off_link_is_rate_limited(client: Any, service: _Opener, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(routes, "_UNSUBSCRIBE_LIMIT", waitlist.RateLimit(per_client=1, total=600, window_s=600))
    assert client.post(f"/notifications/email/unsubscribe?t={TOKEN}").status_code == 200
    assert client.post(f"/notifications/email/unsubscribe?t={TOKEN}").status_code == 429
    assert service.opened == 1
