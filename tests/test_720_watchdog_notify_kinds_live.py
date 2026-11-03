"""Migration 720, executed: a watchdog keeps the kinds it tells. The CHECK refuses what the API refuses, a
kinds edit restarts the watchdog, and the producers tell a watchdog exactly the kinds it is set to: Delisted
then Relisted, a price rise, a new source, each on a published property its spec matches, and nothing to a
watchdog left on the default. Runs in CI's migrations job (`TEST_DATABASE_URL`); every test rolls back (a
producer's per-statement transaction is a savepoint inside the test's)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import psycopg
import pytest

from tests._live_property import REQUIRED_DB, _account, _advert, _property, _recompute, _snapshot

pytestmark = REQUIRED_DB

_AGED = "2026-01-01 00:00:00+00"
_SPEC = '{"category_type": "prodej"}'


def _watchdog(cur: Any, acc: Any, kinds: list[str] | None) -> Any:
    """A live watchdog over every byt for sale, started long ago (an UPDATE of active_since alone fires nothing)."""
    cols, vals = ("account_id, name, filter_spec", "%s, 'w', %s::jsonb")
    params: list[Any] = [acc, _SPEC]
    if kinds is not None:
        cols, vals = cols + ", notify_kinds", vals + ", %s"
        params.append(kinds)
    cur.execute(f"INSERT INTO notification_subscriptions ({cols}) VALUES ({vals}) RETURNING id", params)  # noqa: S608
    sid = cur.fetchone()[0]
    cur.execute("UPDATE notification_subscriptions SET active_since = %s WHERE id = %s", (_AGED, sid))
    return sid


def _served(cur: Any, pid: int, lid: int) -> None:
    """The ad has a resolved place (the consumer rule, rule 25), then the rollup names it canonical and location ad."""
    cur.execute("INSERT INTO listing_location (listing_id, geom, match_confidence, granularity, "
                "  uncertainty_radius_m, country_status, resolver_version, claim_set_hash, registry_version) "
                "VALUES (%s, ST_SetSRID(ST_MakePoint(14.42, 50.08), 4326), 'exact', 'building', 5, 'cz', 'test', "
                "  '\\x00'::bytea, 'test')", (lid,))
    _recompute(cur, pid)


def _told(cur: Any, pid: int) -> list[tuple[Any, str, str | None]]:
    cur.execute("SELECT subscription_id, change_kind, event_ref FROM notification_dispatches "
                "WHERE property_id = %s ORDER BY dispatched_at, change_kind", (pid,))
    return cur.fetchall()


def test_the_check_refuses_what_the_api_refuses(cur: Any) -> None:
    sid = _watchdog(cur, _account(cur), None)
    cur.execute("SELECT notify_kinds FROM notification_subscriptions WHERE id = %s", (sid,))
    assert cur.fetchone()[0] == ["new", "price_drop"], "the default is what every watchdog told before 720"
    for bad in ([], ["sold"], ["reactivated"], ["new", "reactivated"]):
        cur.execute("SAVEPOINT kinds")
        with pytest.raises(psycopg.errors.CheckViolation):
            cur.execute("UPDATE notification_subscriptions SET notify_kinds = %s WHERE id = %s", (bad, sid))
        cur.execute("ROLLBACK TO SAVEPOINT kinds")
    cur.execute("UPDATE notification_subscriptions SET notify_kinds = %s WHERE id = %s",
                (["inactive", "reactivated"], sid))


def test_a_kinds_edit_restarts_the_watchdog_and_the_same_set_does_not(cur: Any) -> None:
    sid = _watchdog(cur, _account(cur), None)
    for kinds, restarts in ((["new", "price_drop"], False), (["new", "price_drop", "inactive"], True)):
        cur.execute("UPDATE notification_subscriptions SET active_since = %s WHERE id = %s", (_AGED, sid))
        cur.execute("UPDATE notification_subscriptions SET notify_kinds = %s WHERE id = %s", (kinds, sid))
        cur.execute("SELECT active_since = now() FROM notification_subscriptions WHERE id = %s", (sid,))
        assert cur.fetchone()[0] is restarts, kinds


def test_a_watchdog_set_to_delisted_and_relisted_is_told_both_and_a_default_one_neither(cur: Any) -> None:
    from api import notifications as nf

    acc = _account(cur)
    telling, silent = _watchdog(cur, acc, ["inactive", "reactivated"]), _watchdog(cur, acc, None)
    gone_at = datetime.now(timezone.utc) - timedelta(days=2)
    pid = _property(cur)
    lid = _advert(cur, pid, source="bezrealitky", active=False, inactive_at=gone_at)
    _served(cur, pid, lid)

    stats = nf.match_lifecycle_once(cur.connection)
    assert stats["subscriptions_evaluated"] == 1, "a watchdog telling no lifecycle kind is not read"
    assert _told(cur, pid) == [(telling, "inactive", str(int(gone_at.timestamp())))]

    # Back on the portal after the Delisted alert: Relisted, keyed on that alert's epoch.
    cur.execute("SELECT dispatched_at FROM notification_dispatches WHERE property_id = %s", (pid,))
    told_at = cur.fetchone()[0]
    cur.execute("UPDATE listings SET is_active = true, inactive_at = NULL, "
                "last_seen_at = clock_timestamp() + interval '1 minute' WHERE id = %s", (lid,))
    _recompute(cur, pid)
    nf.match_lifecycle_once(cur.connection)
    assert _told(cur, pid)[1:] == [(telling, "reactivated", str(int(told_at.timestamp())))]
    assert silent not in {row[0] for row in _told(cur, pid)}


def test_a_watchdog_set_to_price_rises_is_told_a_rise_and_not_a_drop(cur: Any) -> None:
    from api import notifications as nf

    acc = _account(cur)
    rises = _watchdog(cur, acc, ["price_rise"])
    pid = _property(cur)
    lid = _advert(cur, pid, source="bezrealitky", price=5_200_000)
    _served(cur, pid, lid)
    cur.execute("UPDATE properties SET repr_since = '-infinity' WHERE id = %s", (pid,))
    _snapshot(cur, lid, 5_000_000, hours_ago=30)
    up = _snapshot(cur, lid, 5_200_000, hours_ago=2)
    _snapshot(cur, lid, 5_100_000, hours_ago=1)

    nf.match_changes_once(cur.connection)
    assert _told(cur, pid) == [(rises, "price_rise", str(up))]
    cur.execute("SELECT trigger_price_czk, prev_price_czk FROM notification_dispatches WHERE property_id = %s",
                (pid,))
    assert cur.fetchone() == (5_200_000, 5_000_000)


def test_a_watchdog_set_to_new_sources_is_told_the_portal_that_joined(cur: Any) -> None:
    from api import notifications as nf

    acc = _account(cur)
    sources = _watchdog(cur, acc, ["new_source"])
    now = datetime.now(timezone.utc)
    pid = _property(cur)
    first = _advert(cur, pid, source="sreality", first_seen_at=now - timedelta(days=20))
    joined = _advert(cur, pid, source="bezrealitky", first_seen_at=now - timedelta(days=1))
    _advert(cur, pid, source="bezrealitky", first_seen_at=now - timedelta(hours=3))  # the portal's second ad
    _served(cur, pid, first)

    nf.match_lifecycle_once(cur.connection)
    assert _told(cur, pid) == [(sources, "new_source", str(joined))]
