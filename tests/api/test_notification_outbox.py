"""Hermetic tests for the delivery outbox (Sprint N PR 3).

No DB / network: a fake conn scripts the NEW + RETRY queries and the recipient
lookup; a fake ChannelClient records send/retry calls. Covers the composer, the
configured-channel + recipient gating, the new-pair send path, and the retry pass.
"""

from __future__ import annotations

from typing import Any

import pytest

from api import notification_outbox as ob
from api.transports.base import RenderedMessage


# --- compose --------------------------------------------------------------


def test_compose_new_subject_and_deep_link(monkeypatch: Any) -> None:
    monkeypatch.setenv("SPA_BASE_URL", "https://app.example/")
    msg = ob.compose_message({
        "change_kind": "new", "sreality_id": 123,
        "display_label": "Praha 2", "disposition": "2+kk",
        "price_czk": 6_900_000, "price_unit": None,
    })
    assert isinstance(msg, RenderedMessage)
    assert msg.subject == "Nový inzerát: 2+kk Praha 2"
    assert msg.deep_link == "https://app.example/listing/123"  # trailing slash trimmed
    assert "6 900 000 Kč" in msg.body_text


def test_compose_deep_link_prefers_natural_key(monkeypatch: Any) -> None:
    # A non-sreality listing: the deep link must use the canonical natural key,
    # never the negative synthetic id (which a sent email would freeze forever).
    monkeypatch.setenv("SPA_BASE_URL", "https://app.example")
    msg = ob.compose_message({
        "change_kind": "new", "sreality_id": -284913,
        "source": "bazos", "source_id_native": "218865547",
        "display_label": "Praha 2", "disposition": "2+kk",
        "price_czk": 6_900_000, "price_unit": None,
    })
    assert msg.deep_link == "https://app.example/listing/bazos/218865547"
    assert "-284913" not in msg.deep_link


def test_compose_deep_link_falls_back_to_legacy_without_natural_key(monkeypatch: Any) -> None:
    # A listing missing the natural key (pre-migration-314 straggler) still gets a
    # working link via the permanent legacy /listing/{id} resolver.
    monkeypatch.setenv("SPA_BASE_URL", "https://app.example")
    msg = ob.compose_message({
        "change_kind": "new", "sreality_id": 123,
        "source": "sreality", "source_id_native": None,
        "display_label": "Praha 2", "disposition": "2+kk",
        "price_czk": 6_900_000, "price_unit": None,
    })
    assert msg.deep_link == "https://app.example/listing/123"


_ONSET = ("Jobs: Problem — gh/detail_drain: 3 production reds since 08:12 "
          "(OperationalError: server closed the connection unexpectedly)")


def _system(message: str | None, dedupe_key: str | None = "sys:jobs:onset:2026-10-18T08:20:00Z") -> RenderedMessage:
    """A system alert as either drain pass hands it to the composer."""
    return ob.compose_message({
        "source_kind": "system_health", "message": message, "dedupe_key": dedupe_key,
        "change_kind": "system_alert", "sreality_id": None,
    })


def test_compose_system_health_subject_is_the_notice_head(monkeypatch: Any) -> None:
    monkeypatch.setenv("SPA_BASE_URL", "https://app.example/")
    msg = _system(_ONSET)
    assert msg.subject == "Jobs: Problem"
    assert msg.body_text == _ONSET
    assert msg.deep_link == "https://app.example/health#jobs"


def test_compose_system_health_subject_without_a_head_is_the_first_120_characters() -> None:
    recovery = "✓ Jobs: gh/detail_drain recovered after 5 h"
    assert _system(recovery).subject == recovery
    long = "Capacity: " + "x" * 200
    assert _system(long).subject == long[:120]
    assert _system(long).body_text == long


def test_compose_system_health_subject_never_spans_two_lines() -> None:
    """The retired engine wrote multi-line notices (53 stored on 2026-10-18); a header holds one line."""
    legacy = ("Ops incident #7: checkviolation|listings_area_basis_check\n"
              "2 failures since 2026-09-02 10:58 UTC\n---\nnew row — violates check constraint")
    msg = _system(legacy, "sys:ops_incident:7:onset")
    assert msg.subject == "Ops incident #7: checkviolation|listings_area_basis_check"
    assert msg.body_text == legacy


def test_compose_system_health_empty_message_reads_system_alert() -> None:
    for message in (None, "", "\nno first line"):
        assert _system(message).subject == "System alert"
    assert _system(None).body_text == "System alert"


@pytest.mark.parametrize("dedupe_key", [None, "", "notif:dab-1:email", "sys:Bad-Key:onset:x", "sys:jobs"])
def test_compose_system_health_links_bare_health_without_a_check_key(monkeypatch: Any, dedupe_key: str | None) -> None:
    monkeypatch.setenv("SPA_BASE_URL", "https://app.example")
    assert _system(_ONSET, dedupe_key).deep_link == "https://app.example/health"


def test_compose_system_health_has_no_link_without_an_spa_base(monkeypatch: Any) -> None:
    """A relative link leads nowhere from an inbox, so an unset SPA_BASE_URL sends none."""
    monkeypatch.delenv("SPA_BASE_URL", raising=False)
    assert _system(_ONSET).deep_link == ""


def test_compose_price_drop_shows_prev_to_new(monkeypatch: Any) -> None:
    monkeypatch.setenv("SPA_BASE_URL", "https://app.example")
    msg = ob.compose_message({
        "change_kind": "price_drop", "sreality_id": 9, "display_label": "Brno",
        "disposition": "3+1", "price_czk": 4_500_000, "price_unit": None,
        "prev_price_czk": 5_000_000, "trigger_price_czk": 4_500_000,
    })
    assert msg.subject.startswith("Zlevněno")
    assert "5 000 000 Kč → 4 500 000 Kč" in msg.body_text


# --- fakes for drain_once -------------------------------------------------


class _Cur:
    def __init__(self, conn: "_Conn") -> None:
        self._conn = conn
        self._rows: list[tuple[Any, ...]] = []

    def execute(self, sql: str, params: Any = None) -> None:
        norm = " ".join(sql.split())
        self._conn.executed.append(norm)
        if "FROM app_settings" in norm:
            self._rows = [(self._conn.recipient,)] if self._conn.recipient else [(None,)]
        elif "CROSS JOIN LATERAL" in norm:
            self._rows = self._conn.new_rows
        elif "FROM channel_sends cs JOIN" in norm:
            self._rows = self._conn.retry_rows
        else:
            self._rows = []

    def fetchone(self) -> tuple[Any, ...] | None:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[tuple[Any, ...]]:
        return list(self._rows)

    def __enter__(self) -> "_Cur":
        return self

    def __exit__(self, *_: Any) -> None:
        return None


class _Conn:
    def __init__(self, *, recipient: str | None, new_rows: list, retry_rows: list) -> None:
        self.recipient = recipient
        self.new_rows = new_rows
        self.retry_rows = retry_rows
        self.executed: list[str] = []

    def cursor(self) -> _Cur:
        return _Cur(self)


class _FakeClient:
    def __init__(self, configured: set[str]) -> None:
        self._configured = configured
        self.sends: list[dict[str, Any]] = []
        self.retries: list[dict[str, Any]] = []

    def configured_channels(self) -> set[str]:
        return self._configured

    def send(self, **kw: Any) -> dict[str, Any]:
        self.sends.append(kw)
        return {"status": "sent", "id": len(self.sends)}

    def retry(self, **kw: Any) -> dict[str, Any]:
        self.retries.append(kw)
        return {"status": "sent", "id": kw["send_id"]}


def _new_row(ch: str = "email") -> tuple:
    # (dispatch_id, source_kind, change_kind, sreality_id, subscription_id,
    #  collection_id, trigger_price, prev_price, display_label, disposition,
    #  price_czk, price_unit, category_main, source, source_id_native, message,
    #  dedupe_key, ch)
    return ("dab-1", "watchdog", "new", 123, "sub-1", None,
            None, None, "Praha 2", "2+kk", 6_900_000, None, "byt",
            "sreality", "123", None, "wd:sub-1:new:123", ch)


def test_drain_noop_without_configured_channels() -> None:
    client = _FakeClient(configured=set())
    conn = _Conn(recipient="op@example.cz", new_rows=[_new_row()], retry_rows=[])
    stats = ob.drain_once(conn, client)  # type: ignore[arg-type]
    assert stats == {"sent": 0, "failed": 0, "skipped": 0, "retried": 0}
    assert client.sends == []


def test_drain_sends_new_pair_with_correct_routing() -> None:
    client = _FakeClient(configured={"email"})
    conn = _Conn(recipient="op@example.cz", new_rows=[_new_row()], retry_rows=[])
    stats = ob.drain_once(conn, client)  # type: ignore[arg-type]
    assert stats["sent"] == 1
    assert len(client.sends) == 1
    call = client.sends[0]
    assert call["channel"] == "email"
    assert call["recipient"] == "op@example.cz"
    assert call["consumer"] == "watchdog"
    assert call["dedupe_key"] == "notif:dab-1:email"
    assert call["notification_id"] == "dab-1"
    assert call["source_id"] == "sub-1"


def test_drain_skips_when_recipient_unset() -> None:
    client = _FakeClient(configured={"email"})
    conn = _Conn(recipient=None, new_rows=[_new_row()], retry_rows=[])
    stats = ob.drain_once(conn, client)  # type: ignore[arg-type]
    assert stats["sent"] == 0
    assert stats["skipped"] == 1
    assert client.sends == []


def test_drain_collection_monitor_routes_collection_id_as_source() -> None:
    client = _FakeClient(configured={"email"})
    row = ("dab-2", "collection_monitor", "price_drop", 55, None, 7,
           4_000_000, 4_500_000, "Plzeň", "1+kk", 4_000_000, None, "byt",
           "sreality", "55", None, "cm:7:price_drop:991", "email")
    conn = _Conn(recipient="op@example.cz", new_rows=[row], retry_rows=[])
    ob.drain_once(conn, client)  # type: ignore[arg-type]
    call = client.sends[0]
    assert call["consumer"] == "collection_monitor"
    assert call["source_id"] == "7"  # collection_id stringified


def test_drain_retries_failed_due_rows() -> None:
    client = _FakeClient(configured={"email"})
    # (send_id, channel, recipient, consumer, source_kind, change_kind, sreality_id,
    #  trigger_price, prev_price, display_label, disposition, price_czk, price_unit,
    #  category_main, source, source_id_native, message, dedupe_key)
    retry_row = (42, "email", "op@example.cz", "watchdog", "watchdog", "new", 5,
                 None, None, "Ostrava", "2+1", 3_000_000, None, "byt",
                 "sreality", "5", None, "wd:sub-1:new:5")
    conn = _Conn(recipient="op@example.cz", new_rows=[], retry_rows=[retry_row])
    stats = ob.drain_once(conn, client)  # type: ignore[arg-type]
    assert stats["retried"] == 1
    assert client.retries[0]["send_id"] == 42
    assert client.retries[0]["channel"] == "email"


# --- W3.3: ops alerts get the same five attempts as a price drop -----------


def test_retry_pass_has_no_consumer_allowlist() -> None:
    """It used to read `cs.consumer IN ('watchdog','collection_monitor')`, so a
    system_health send got ONE delivery attempt where a price drop gets five — the
    entire delta between "ops alerting exists" and "ops alerting is reliable"."""
    client = _FakeClient(configured={"email"})
    conn = _Conn(recipient="op@example.cz", new_rows=[], retry_rows=[])
    ob.drain_once(conn, client)  # type: ignore[arg-type]
    retry_sql = next(s for s in conn.executed if "FROM channel_sends cs JOIN" in s)
    assert "cs.consumer IN" not in retry_sql
    # The JOIN is what scopes the pass; `outreach` has notification_id IS NULL and so
    # can never appear here anyway.
    assert "JOIN notification_dispatches d ON d.id = cs.notification_id" in retry_sql


def test_drain_retries_a_system_health_send(monkeypatch: Any) -> None:
    monkeypatch.setenv("SPA_BASE_URL", "https://app.example")
    retry_row = (77, "email", "op@example.cz", "system_health", "system_health",
                 "system_alert", None, None, None, None, None, None, None, None,
                 None, None, "Ops incident #7: checkviolation|listings_area_basis_check",
                 "sys:ops_incident:7:onset")
    client = _FakeClient(configured={"email"})
    conn = _Conn(recipient="op@example.cz", new_rows=[], retry_rows=[retry_row])
    stats = ob.drain_once(conn, client)  # type: ignore[arg-type]
    assert stats["retried"] == 1
    call = client.retries[0]
    assert call["send_id"] == 77
    # The composer's system_health branch must survive the retry path too: the message
    # is rendered verbatim, not squeezed into a listing template.
    assert call["message"].subject == "Ops incident #7: checkviolation|listings_area_basis_check"
    assert "checkviolation" in call["message"].body_text
    assert call["message"].deep_link == "https://app.example/health#ops_incident"


def test_both_passes_hand_the_dispatch_key_to_the_composer(monkeypatch: Any) -> None:
    """The link names the check from the DISPATCH's key; the send keeps its own `notif:` key."""
    monkeypatch.setenv("SPA_BASE_URL", "https://app.example")
    new_row = ("dab-3", "system_health", "system_alert", None, None, None, None, None,
               None, None, None, None, None, None, None, _ONSET,
               "sys:jobs:onset:2026-10-18T08:20:00Z", "email")
    retry_row = (78, "email", "op@example.cz", "system_health", "system_health",
                 "system_alert", None, None, None, None, None, None, None, None,
                 None, None, "Capacity: still Problem for 6 h — cron/browse-list-rebuild",
                 "sys:capacity:reminder:6h:2026-10-18T02:00:00Z:41x1")
    client = _FakeClient(configured={"email"})
    conn = _Conn(recipient="op@example.cz", new_rows=[new_row], retry_rows=[retry_row])
    ob.drain_once(conn, client)  # type: ignore[arg-type]
    for sql in (next(s for s in conn.executed if "CROSS JOIN LATERAL" in s),
                next(s for s in conn.executed if "FROM channel_sends cs JOIN" in s)):
        assert "d.dedupe_key" in sql
    sent, retried = client.sends[0], client.retries[0]
    assert sent["dedupe_key"] == "notif:dab-3:email"
    assert (sent["message"].subject, sent["message"].deep_link) == ("Jobs: Problem", "https://app.example/health#jobs")
    assert (retried["message"].subject, retried["message"].deep_link) == (
        "Capacity: still Problem for 6 h", "https://app.example/health#capacity")


def test_system_health_is_a_declared_consumer() -> None:
    """drain_once passes `consumer=source_kind` straight through; migration 274 widened
    the DB CHECK for system_health but the Literal was never updated with it."""
    from typing import get_args

    from api.channel_client import Consumer

    assert "system_health" in get_args(Consumer)


def test_neither_pass_delivers_a_dismissed_property() -> None:
    """Migration 536. The drain runs service-role, so the dismissal it honours is
    the DISPATCH's own account's, named explicitly — never "anyone's"."""
    client = _FakeClient(configured={"email"})
    conn = _Conn(recipient="op@example.cz", new_rows=[], retry_rows=[])
    ob.drain_once(conn, client)  # type: ignore[arg-type]
    passes = [s for s in conn.executed if "FROM notification_dispatches d" in s
              or "FROM channel_sends cs JOIN" in s]
    assert len(passes) == 2
    for sql in passes:
        assert (
            "NOT EXISTS (SELECT 1 FROM property_dismissals pd "
            "WHERE pd.property_id = d.property_id AND pd.account_id = d.account_id "
            "AND pd.lifted_at IS NULL)"
        ) in sql
