"""The broker book (migration 691) on the replayed schema: one spell per (property, broker identity), kept true by the
property maintenance pass on every path that changes it, and quiet when nothing did. Every seed rolls back with the
test (the shared `cur` fixture); the pass is the real one (`properties_changed`, the dirty drain, the identity writers,
the attribution link statement)."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta
from typing import Any

from scripts.recompute_property_stats import (
    _BROKER_BOOK_BATCH_SQL,
    _BROKER_BOOK_SCOPED_SQL,
    _drain_dirty,
    properties_changed,
)
from tests._live_property import OP, REQUIRED_DB, _advert, _property
from toolkit.broker_sources import BROKER_SOURCES
from toolkit.property_identity import detach_listings, merge_property_set

pytestmark = REQUIRED_DB

_SEL = "l.id = ANY(%(ids)s)"


def _now(cur: Any) -> datetime:
    cur.execute("SELECT now()")
    return cur.fetchone()[0]


def _identity(cur: Any, native: str | None = None) -> int:
    cur.execute("INSERT INTO broker_identities (source, source_broker_id_native) VALUES ('sreality', %s) RETURNING id",
                (native or f"lp-{uuid.uuid4()}",))
    return int(cur.fetchone()[0])


def _ad(cur: Any, pid: int | None, identity: int | None, first: datetime, last: datetime, *,
        active: bool = True, **facts: Any) -> int:
    return _advert(cur, pid, source="sreality", active=active, broker_identity_id=identity,
                   first_seen_at=first, last_seen_at=last, **facts)


def _book(cur: Any, pid: int) -> dict[int, tuple[datetime, datetime | None, bool]]:
    cur.execute("SELECT broker_identity_id, started_at, ended_at, tom_observed FROM property_broker_spells "
                "WHERE property_id = %s", (pid,))
    return {int(i): (s, e, o) for i, s, e, o in cur.fetchall()}


def _ctids(cur: Any, pid: int) -> list[str]:
    cur.execute("SELECT ctid::text FROM property_broker_spells WHERE property_id = %s ORDER BY broker_identity_id",
                (pid,))
    return [r[0] for r in cur.fetchall()]


def _observed(cur: Any, lid: int) -> bool:
    cur.execute("SELECT measure_tom_observed(source, category_main, category_type, first_seen_at) "
                "FROM listings WHERE id = %s", (lid,))
    return cur.fetchone()[0]


def _dirty(cur: Any) -> set[int]:
    cur.execute("SELECT property_id FROM dirty_properties")
    return {int(r[0]) for r in cur.fetchall()}


def test_one_ad_is_one_spell_open_while_it_runs_and_closed_at_its_last_sighting(cur: Any) -> None:
    now = _now(cur)
    pid, who = _property(cur), _identity(cur)
    lid = _ad(cur, pid, who, now - timedelta(days=30), now - timedelta(hours=1))
    properties_changed(cur.connection, [pid])
    assert _book(cur, pid) == {who: (now - timedelta(days=30), None, _observed(cur, lid))}

    cur.execute("UPDATE listings SET is_active = false, last_seen_at = %s WHERE id = %s",
                (now - timedelta(days=3), lid))
    properties_changed(cur.connection, [pid])
    assert _book(cur, pid)[who][:2] == (now - timedelta(days=30), now - timedelta(days=3))


def test_one_identitys_ads_are_one_spell_from_the_first_start_to_the_last_end(cur: Any) -> None:
    """Start = the earliest first sighting; end = NULL while ANY of its ads runs, then the latest last sighting."""
    now = _now(cur)
    pid, who = _property(cur), _identity(cur)
    _ad(cur, pid, who, now - timedelta(days=40), now - timedelta(days=20), active=False)
    later = _ad(cur, pid, who, now - timedelta(days=10), now - timedelta(hours=1))
    properties_changed(cur.connection, [pid])
    assert _book(cur, pid)[who][:2] == (now - timedelta(days=40), None)

    cur.execute("UPDATE listings SET is_active = false, last_seen_at = %s WHERE id = %s",
                (now - timedelta(days=2), later))
    properties_changed(cur.connection, [pid])
    assert _book(cur, pid)[who][:2] == (now - timedelta(days=40), now - timedelta(days=2))


def test_two_identities_on_one_property_are_two_spells(cur: Any) -> None:
    now = _now(cur)
    pid, one, two = _property(cur), _identity(cur), _identity(cur)
    _ad(cur, pid, one, now - timedelta(days=50), now - timedelta(days=45), active=False)
    _ad(cur, pid, two, now - timedelta(days=44), now - timedelta(hours=1))
    _ad(cur, pid, None, now - timedelta(days=60), now - timedelta(hours=1))  # unattributed: no spell
    properties_changed(cur.connection, [pid])
    book = _book(cur, pid)
    assert set(book) == {one, two}
    assert book[one][:2] == (now - timedelta(days=50), now - timedelta(days=45))
    assert book[two][:2] == (now - timedelta(days=44), None)


def test_a_revival_reopens_the_spell(cur: Any) -> None:
    now = _now(cur)
    pid, who = _property(cur), _identity(cur)
    lid = _ad(cur, pid, who, now - timedelta(days=30), now - timedelta(days=5), active=False)
    properties_changed(cur.connection, [pid])
    assert _book(cur, pid)[who][1] == now - timedelta(days=5)

    cur.execute("UPDATE listings SET is_active = true, last_seen_at = now() WHERE id = %s", (lid,))
    properties_changed(cur.connection, [pid])
    assert _book(cur, pid)[who][:2] == (now - timedelta(days=30), None)


def test_a_merge_and_its_detach_move_the_spell_with_the_ad(cur: Any) -> None:
    """The identity writers run the pass over both sides: the retired property keeps no spell, the survivor holds both;
    the detach gives the advert, and its spell, back."""
    now = _now(cur)
    s, r = _property(cur), _property(cur)
    a, b = _identity(cur), _identity(cur)
    _ad(cur, s, a, now - timedelta(days=9), now - timedelta(hours=1))
    moved = _ad(cur, r, b, now - timedelta(days=9), now - timedelta(hours=1))
    properties_changed(cur.connection, [s, r])
    assert (set(_book(cur, s)), set(_book(cur, r))) == ({a}, {b})

    out = merge_property_set(cur.connection, [s, r], source="operator", reason="manual_subset",
                             decided_by=OP)["data"]
    assert (out["survivor_id"], out["retired_ids"]) == (s, [r])
    assert (set(_book(cur, s)), _book(cur, r)) == ({a, b}, {})

    (back,) = detach_listings(cur.connection, [moved], decided_by=OP)["data"]["adverts"]
    assert (back["outcome"], back["restored_property_id"]) == ("detached", r)
    assert (set(_book(cur, s)), set(_book(cur, r))) == ({a}, {b})


def test_a_re_attribution_dirties_the_property_and_the_drain_moves_the_spell(cur: Any) -> None:
    """The attribution link (toolkit/broker_sources.py) enqueues the advert's property in its own statement; the dirty
    drain's pass then replaces the old identity's spell with the new one's. An advert with no property yet enqueues
    nothing (its birth stores its spells), and a link that changes nothing enqueues nothing."""
    (link,) = [s for s in next(c for c in BROKER_SOURCES if c.source == "sreality").statements()
               if s.lstrip().startswith("WITH linked AS")]
    now = _now(cur)
    pid, old = _property(cur), _identity(cur)
    native = f"lp-{uuid.uuid4()}"
    new = _identity(cur, native)
    raw = json.dumps({"user": {"user_id": native}})
    lid = _ad(cur, pid, old, now - timedelta(days=9), now - timedelta(hours=1))
    orphan = _ad(cur, None, None, now - timedelta(days=2), now - timedelta(hours=1))
    cur.execute("UPDATE listings SET raw_json = %s::jsonb WHERE id = ANY(%s)", (raw, [lid, orphan]))
    properties_changed(cur.connection, [pid])
    assert set(_book(cur, pid)) == {old}
    cur.execute("DELETE FROM dirty_properties")

    cur.execute(link.format(sel=_SEL), {"ids": [lid, orphan]})
    cur.execute("SELECT broker_identity_id FROM listings WHERE id = ANY(%s)", ([lid, orphan],))
    assert {r[0] for r in cur.fetchall()} == {new}
    assert _dirty(cur) == {pid}

    assert _drain_dirty(cur.connection, 2000, now) == 1
    assert set(_book(cur, pid)) == {new} and _dirty(cur) == set()

    cur.execute(link.format(sel=_SEL), {"ids": [lid, orphan]})
    assert cur.rowcount == 0 and _dirty(cur) == set()


def test_a_re_attribution_committed_after_the_slice_read_survives_the_dequeue(cur: Any, monkeypatch: Any) -> None:
    """The race the dequeue's compare-and-set closes: the property is already dirty, the slice claims it and recomputes
    from the state before the link, then the link's transaction (begun before the cutoff, so its now() stamp is at or
    before it) commits. Its re-stamp must survive the dequeue and bring the new identity's spell in on the next claim;
    `marked_at <= cutoff` would have dropped it, leaving the old spell until the daily sweep."""
    import scripts.recompute_property_stats as rps

    (link,) = [s for s in next(c for c in BROKER_SOURCES if c.source == "sreality").statements()
               if s.lstrip().startswith("WITH linked AS")]
    now = _now(cur)
    pid, old = _property(cur), _identity(cur)
    native = f"lp-{uuid.uuid4()}"
    new = _identity(cur, native)
    lid = _ad(cur, pid, old, now - timedelta(days=9), now - timedelta(hours=1))
    cur.execute("UPDATE listings SET raw_json = %s::jsonb WHERE id = %s",
                (json.dumps({"user": {"user_id": native}}), lid))
    properties_changed(cur.connection, [pid])
    cur.execute("DELETE FROM dirty_properties")
    cur.execute("INSERT INTO dirty_properties (property_id, marked_at) VALUES (%s, %s)",
                (pid, now - timedelta(minutes=1)))

    seen: list[set[int]] = []

    def slice_then_link(conn: Any, ids: list[int]) -> None:
        properties_changed(conn, ids)
        seen.append(set(_book(cur, pid)))
        if len(seen) == 1:
            cur.execute(link.format(sel=_SEL), {"ids": [lid]})

    monkeypatch.setattr(rps, "properties_changed", slice_then_link)
    assert _drain_dirty(cur.connection, 2000, now) == 2
    assert seen == [{old}, {new}]
    assert set(_book(cur, pid)) == {new} and _dirty(cur) == set()


def test_a_quiescent_pass_writes_nothing(cur: Any) -> None:
    """Once the book holds the view's rows, the scoped and the range statements rewrite none (no new tuple), and the
    pass enqueues no dirty property."""
    now = _now(cur)
    pid, one, two = _property(cur), _identity(cur), _identity(cur)
    _ad(cur, pid, one, now - timedelta(days=30), now - timedelta(days=4), active=False)
    _ad(cur, pid, two, now - timedelta(days=3), now - timedelta(hours=1))
    properties_changed(cur.connection, [pid])
    before = _ctids(cur, pid)
    assert len(before) == 2
    cur.execute("DELETE FROM dirty_properties")

    properties_changed(cur.connection, [pid])
    cur.execute(_BROKER_BOOK_SCOPED_SQL, {"ids": [pid]})
    assert cur.rowcount == 0
    cur.execute(_BROKER_BOOK_BATCH_SQL, {"lo": pid, "hi": pid + 1})
    assert cur.rowcount == 0
    assert _ctids(cur, pid) == before and _dirty(cur) == set()


def test_the_range_form_deletes_a_spell_the_view_no_longer_holds(cur: Any) -> None:
    """The daily sweep's form: a property whose only ad lost its attribution keeps no spell."""
    now = _now(cur)
    pid, who = _property(cur), _identity(cur)
    lid = _ad(cur, pid, who, now - timedelta(days=9), now - timedelta(hours=1))
    properties_changed(cur.connection, [pid])
    assert set(_book(cur, pid)) == {who}
    cur.execute("UPDATE listings SET broker_identity_id = NULL WHERE id = %s", (lid,))
    cur.execute(_BROKER_BOOK_BATCH_SQL, {"lo": pid, "hi": pid + 1})
    assert _book(cur, pid) == {}


def test_tom_observed_follows_the_identitys_first_ad_in_the_projection_order(cur: Any) -> None:
    """browse_projection's order (migration 669): an ad with a category first, then first_seen_at, then id. Seeded on a
    fresh (portal, category) pair: its first day is a sweep by definition (not observed), a lone later sighting is
    ordinary (observed). A category-less ad sighted earlier still is skipped, though it reads observed on its own."""
    source, main, kind = "remax", "ostatni", "drazba"
    cur.execute("SELECT count(*) FROM listings WHERE source = %s AND category_main = %s AND category_type = %s",
                (source, main, kind))
    assert cur.fetchone()[0] == 0, "the seeded pair must be empty for its first day to be known"
    pid, who = _property(cur), _identity(cur)

    def ad(day: str, category: bool) -> int:
        return _advert(cur, pid, source=source, broker_identity_id=who,
                       first_seen_at=datetime.fromisoformat(f"{day}T12:00:00+00:00"),
                       category_main=main if category else None, category_type=kind if category else None)

    uncategorised, sweep, ordinary = ad("2026-03-01", False), ad("2026-03-02", True), ad("2026-03-03", True)
    cur.execute("REFRESH MATERIALIZED VIEW public.ingest_bulk_days")
    assert (_observed(cur, uncategorised), _observed(cur, sweep), _observed(cur, ordinary)) == (True, False, True)

    properties_changed(cur.connection, [pid])
    assert _book(cur, pid)[who] == (datetime.fromisoformat("2026-03-01T12:00:00+00:00"), None, False)


def test_broker_book_folds_a_brokers_identities_into_one_row_per_property(cur: Any) -> None:
    """broker_book (migration 691): one broker's identities on two portals are one row whose sources are those portals,
    active_sources the ones with an ad still running; the earliest spell starts it; an unresolved identity has no row."""
    now = _now(cur)
    pid = _property(cur)
    cur.execute("INSERT INTO brokers (display_name) VALUES ('lp-broker') RETURNING id")
    broker = int(cur.fetchone()[0])
    cur.execute("INSERT INTO broker_identities (source, source_broker_id_native, broker_id) VALUES "
                "('sreality', %s, %s), ('idnes', %s, %s) RETURNING id",
                (f"lp-{uuid.uuid4()}", broker, f"lp-{uuid.uuid4()}", broker))
    on_sreality, on_idnes = (int(r[0]) for r in cur.fetchall())
    unresolved = _identity(cur)
    _ad(cur, pid, on_sreality, now - timedelta(days=20), now - timedelta(days=12), active=False)
    _advert(cur, pid, source="idnes", broker_identity_id=on_idnes, first_seen_at=now - timedelta(days=15),
            last_seen_at=now - timedelta(hours=1))
    _ad(cur, pid, unresolved, now - timedelta(days=30), now - timedelta(hours=1))
    properties_changed(cur.connection, [pid])

    cur.execute("SELECT broker_id, is_active, all_sources, active_sources, started_at, ended_at FROM broker_book "
                "WHERE property_id = %s", (pid,))
    assert cur.fetchall() == [(broker, True, ["idnes", "sreality"], ["idnes"], now - timedelta(days=20), None)]
