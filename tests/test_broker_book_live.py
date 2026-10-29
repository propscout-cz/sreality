"""The broker book (migration 692) on the replayed schema: one row per (property, canonical broker), kept true by the
property maintenance pass on every path that changes it (an ad's own write, a property merge or detach, a
re-attribution, every re-point of an identity's broker) and quiet when nothing did. Every seed rolls back with the test
(the shared `cur` fixture); the pass and the writers are the real ones."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta
from typing import Any

import pytest

from api.broker_review import merge_brokers, unmerge_group
from scripts.recompute_property_stats import (
    _BROKER_BOOK_BATCH_SQL,
    _BROKER_BOOK_SCOPED_SQL,
    _drain_dirty,
    properties_changed,
)
from scripts.resolve_brokers import _apply_merges, _attach_singletons
from tests._live_property import OP, REQUIRED_DB, _advert, _property
from toolkit.broker_sources import BROKER_SOURCES
from toolkit.property_identity import detach_listings, merge_property_set

pytestmark = REQUIRED_DB

_SEL = "l.id = ANY(%(ids)s)"
_D = timedelta(days=1)


def _now(cur: Any) -> datetime:
    cur.execute("SELECT now()")
    return cur.fetchone()[0]


def _broker(cur: Any) -> int:
    cur.execute("INSERT INTO brokers (display_name) VALUES (%s) RETURNING id", (f"lp-{uuid.uuid4().hex[:8]}",))
    return int(cur.fetchone()[0])


def _identity(cur: Any, broker: int | None, source: str = "sreality", native: str | None = None) -> int:
    cur.execute("INSERT INTO broker_identities (source, source_broker_id_native, broker_id) VALUES (%s, %s, %s) "
                "RETURNING id", (source, native or f"lp-{uuid.uuid4()}", broker))
    return int(cur.fetchone()[0])


def _ad(cur: Any, pid: int | None, identity: int | None, first: datetime, last: datetime, *,
        active: bool = True, source: str = "sreality", **facts: Any) -> int:
    return _advert(cur, pid, source=source, active=active, broker_identity_id=identity,
                   first_seen_at=first, last_seen_at=last, **facts)


def _book(cur: Any, pid: int) -> dict[int, tuple[Any, ...]]:
    """broker -> (is_active, all_sources, active_sources, started_at, ended_at, tom_observed)."""
    cur.execute("SELECT broker_id, is_active, all_sources, active_sources, started_at, ended_at, tom_observed "
                "FROM broker_book WHERE property_id = %s", (pid,))
    return {int(b): tuple(rest) for b, *rest in cur.fetchall()}


def _observed(cur: Any, lid: int) -> bool:
    cur.execute("SELECT measure_tom_observed(source, category_main, category_type, first_seen_at) "
                "FROM listings WHERE id = %s", (lid,))
    return cur.fetchone()[0]


def _dirty(cur: Any) -> set[int]:
    cur.execute("SELECT property_id FROM dirty_properties")
    return {int(r[0]) for r in cur.fetchall()}


def _clean(cur: Any) -> None:
    cur.execute("DELETE FROM dirty_properties")


def _link(source: str = "sreality") -> str:
    (link,) = [s for s in next(c for c in BROKER_SOURCES if c.source == source).statements()
               if s.lstrip().startswith("WITH linked AS")]
    return link


# (broker, source, first seen and last seen in days ago, active); broker "?" = an identity with no broker yet, None =
# an unattributed ad. The row: (is_active, all_sources, active_sources, started days ago, ended days ago).
@pytest.mark.parametrize("ads, rows", [
    ([("a", "sreality", 30, 0, True)], {"a": (True, ["sreality"], ["sreality"], 30, None)}),
    ([("a", "sreality", 30, 3, False)], {"a": (False, ["sreality"], [], 30, 3)}),
    ([("a", "sreality", 40, 20, False), ("a", "sreality", 10, 0, True)],
     {"a": (True, ["sreality"], ["sreality"], 40, None)}),
    ([("a", "sreality", 40, 20, False), ("a", "sreality", 10, 2, False)], {"a": (False, ["sreality"], [], 40, 2)}),
    ([("a", "sreality", 20, 12, False), ("a", "idnes", 15, 0, True)],
     {"a": (True, ["idnes", "sreality"], ["idnes"], 20, None)}),
    ([("a", "sreality", 50, 45, False), ("b", "sreality", 44, 0, True), (None, "sreality", 60, 0, True),
      ("?", "sreality", 60, 0, True)],
     {"a": (False, ["sreality"], [], 50, 45), "b": (True, ["sreality"], ["sreality"], 44, None)}),
], ids=["one ad runs", "one ad ended", "one broker's ads, one runs", "one broker's ads, all ended",
        "one broker on two portals", "two brokers, an unattributed ad, an unresolved identity"])
def test_the_book_folds_a_brokers_ads_on_a_property_into_one_row(cur: Any, ads: list, rows: dict) -> None:
    """Start = the earliest first sighting; end = NULL while ANY of the broker's ads runs, then the latest last
    sighting; the sources are the portals of the broker's identities, one per portal (MS19: one ad)."""
    now = _now(cur)
    pid = _property(cur)
    brokers = {label: _broker(cur) for label in {b for b, *_ in ads if b not in (None, "?")}}
    identities: dict[tuple[str, str], int] = {}
    for label, source, first, last, active in ads:
        who = None if label is None else identities.setdefault(
            (label, source), _identity(cur, brokers.get(label), source))
        _ad(cur, pid, who, now - first * _D, now - last * _D, active=active, source=source)
    properties_changed(cur.connection, [pid])
    assert {b: r[:5] for b, r in _book(cur, pid).items()} == {
        brokers[label]: (*r[:3], now - r[3] * _D, None if r[4] is None else now - r[4] * _D)
        for label, r in rows.items()}


def test_a_close_and_a_revival_rewrite_the_row(cur: Any) -> None:
    now = _now(cur)
    pid, broker = _property(cur), _broker(cur)
    lid = _ad(cur, pid, _identity(cur, broker), now - 30 * _D, now - _D)
    properties_changed(cur.connection, [pid])
    assert _book(cur, pid) == {broker: (True, ["sreality"], ["sreality"], now - 30 * _D, None, _observed(cur, lid))}
    for active, last, row in ((False, now - 3 * _D, (False, ["sreality"], [], now - 30 * _D, now - 3 * _D)),
                              (True, now, (True, ["sreality"], ["sreality"], now - 30 * _D, None))):
        cur.execute("UPDATE listings SET is_active = %s, last_seen_at = %s WHERE id = %s", (active, last, lid))
        properties_changed(cur.connection, [pid])
        assert _book(cur, pid)[broker][:5] == row


def test_a_merge_and_its_detach_move_the_row_with_the_ad(cur: Any) -> None:
    """The property identity writers run the pass over both sides."""
    now = _now(cur)
    s, r = _property(cur), _property(cur)
    a, b = _broker(cur), _broker(cur)
    _ad(cur, s, _identity(cur, a), now - 9 * _D, now - _D)
    moved = _ad(cur, r, _identity(cur, b), now - 9 * _D, now - _D)
    properties_changed(cur.connection, [s, r])
    assert (set(_book(cur, s)), set(_book(cur, r))) == ({a}, {b})

    out = merge_property_set(cur.connection, [s, r], source="operator", reason="manual_subset",
                             decided_by=OP)["data"]
    assert (out["survivor_id"], out["retired_ids"]) == (s, [r])
    assert (set(_book(cur, s)), _book(cur, r)) == ({a, b}, {})

    (back,) = detach_listings(cur.connection, [moved], decided_by=OP)["data"]["adverts"]
    assert (back["outcome"], back["restored_property_id"]) == ("detached", r)
    assert (set(_book(cur, s)), set(_book(cur, r))) == ({a}, {b})


def _re_attributable(cur: Any, orphan: bool = False) -> tuple[int, list[int], int, int]:
    """A property whose ad names a new broker's identity in its payload but is still linked to the old one's (and, with
    `orphan`, an ad with no property yet naming it too); the book holds the old broker and nothing is dirty."""
    now = _now(cur)
    pid, old_broker, new_broker = _property(cur), _broker(cur), _broker(cur)
    native = f"lp-{uuid.uuid4()}"
    _identity(cur, new_broker, native=native)
    ads = [_ad(cur, pid, _identity(cur, old_broker), now - 9 * _D, now - _D)]
    ads += [_ad(cur, None, None, now - 2 * _D, now)] if orphan else []
    cur.execute("UPDATE listings SET raw_json = %s::jsonb WHERE id = ANY(%s)",
                (json.dumps({"user": {"user_id": native}}), ads))
    properties_changed(cur.connection, [pid])
    assert set(_book(cur, pid)) == {old_broker}
    _clean(cur)
    return pid, ads, old_broker, new_broker


def test_a_re_attribution_dirties_the_property_and_the_drain_moves_the_row(cur: Any) -> None:
    """The link enqueues the advert's property in its own statement; an advert with no property yet enqueues nothing
    (its birth stores its row), and a link that changes nothing enqueues nothing."""
    pid, ads, _, new_broker = _re_attributable(cur, orphan=True)
    cur.execute(_link().format(sel=_SEL), {"ids": ads})
    cur.execute("SELECT bi.broker_id FROM listings l LEFT JOIN broker_identities bi ON bi.id = l.broker_identity_id "
                "WHERE l.id = ANY(%s)", (ads,))
    assert cur.fetchall() == [(new_broker,)] * 2 and _dirty(cur) == {pid}
    assert _drain_dirty(cur.connection, 2000, _now(cur)) == 1
    assert set(_book(cur, pid)) == {new_broker} and _dirty(cur) == set()
    cur.execute(_link().format(sel=_SEL), {"ids": ads})
    assert cur.rowcount == 0 and _dirty(cur) == set()


def test_a_re_attribution_committed_after_the_slice_read_survives_the_dequeue(cur: Any, monkeypatch: Any) -> None:
    """The race the dequeue's compare-and-set closes: the slice recomputes from the state before the link, then the
    link's transaction (its now() stamp at or before the cutoff) commits; its re-stamp must survive the dequeue and bring
    the new broker's row in on the next claim (`marked_at <= cutoff` would leave the old row until the daily sweep)."""
    import scripts.recompute_property_stats as rps

    pid, (lid,), old_broker, new_broker = _re_attributable(cur)
    now = _now(cur)
    cur.execute("INSERT INTO dirty_properties (property_id, marked_at) VALUES (%s, %s)", (pid, now - timedelta(minutes=1)))
    seen: list[set[int]] = []

    def slice_then_link(conn: Any, ids: list[int]) -> None:
        properties_changed(conn, ids)
        seen.append(set(_book(cur, pid)))
        if len(seen) == 1:
            cur.execute(_link().format(sel=_SEL), {"ids": [lid]})

    monkeypatch.setattr(rps, "properties_changed", slice_then_link)
    assert _drain_dirty(cur.connection, 2000, now) == 2
    assert seen == [{old_broker}, {new_broker}] and _dirty(cur) == set()


def _two_brokers(cur: Any) -> dict[str, Any]:
    """Broker `lo` alone on `p`; broker `hi` alone on `q` with two identities (a live idnes ad, an ended sreality one); a
    third broker alone on `other`. The book is current and nothing is dirty."""
    now = _now(cur)
    p, q, other = _property(cur), _property(cur), _property(cur)
    lo, hi = sorted((_broker(cur), _broker(cur)))
    lo_id, hi_id = _identity(cur, lo), _identity(cur, hi, "idnes")
    _ad(cur, p, lo_id, now - 20 * _D, now - _D)
    _ad(cur, q, hi_id, now - 10 * _D, now - _D, source="idnes")
    _ad(cur, q, _identity(cur, hi), now - 12 * _D, now - 11 * _D, active=False)
    _ad(cur, other, _identity(cur, _broker(cur)), now - 5 * _D, now - _D)
    properties_changed(cur.connection, [p, q, other])
    _clean(cur)
    return {"p": p, "q": q, "other": other, "lo": lo, "hi": hi, "group": [lo_id, hi_id], "now": now,
            "other_row": _book(cur, other)}


@pytest.mark.parametrize("merge", [
    lambda cur, s: merge_brokers(cur.connection, [s["lo"], s["hi"]], created_by=OP)["merge_group_id"],
    lambda cur, s: _apply_merges(cur.connection, [s["group"]]) and None,
], ids=["the operator's, and its unmerge", "the resolver's"])
def test_a_broker_merge_dirties_the_moved_properties_and_the_drain_moves_them_to_the_survivor(
        cur: Any, merge: Any) -> None:
    """A merge re-points the loser's identities to the survivor (the lowest id) in a transaction that dirties their
    properties; a property of neither broker is untouched. An unmerge re-points them back, dirtied alike."""
    s = _two_brokers(cur)
    group = merge(cur, s)
    assert _dirty(cur) == {s["q"]}
    assert _drain_dirty(cur.connection, 2000, _now(cur)) == 1
    assert (set(_book(cur, s["p"])), _book(cur, s["q"])[s["lo"]][:5]) == (
        {s["lo"]}, (True, ["idnes", "sreality"], ["idnes"], s["now"] - 12 * _D, None))
    assert set(_book(cur, s["q"])) == {s["lo"]} and _book(cur, s["other"]) == s["other_row"]
    if group:
        assert unmerge_group(cur.connection, group, undone_by=OP) is not None
        assert _dirty(cur) == {s["q"]}
        assert _drain_dirty(cur.connection, 2000, _now(cur)) == 1
        assert set(_book(cur, s["q"])) == {s["hi"]}
    assert _dirty(cur) == set()


def test_a_singleton_attach_makes_an_unattributed_property_attributed(cur: Any) -> None:
    """It dirties the property of the identity's ad (not an advert with no property yet), and the drain writes the
    row."""
    now = _now(cur)
    pid = _property(cur)
    who = _identity(cur, None)
    _ad(cur, pid, who, now - 4 * _D, now - _D)
    _ad(cur, None, who, now - _D, now - _D)
    properties_changed(cur.connection, [pid])
    assert _book(cur, pid) == {}
    _clean(cur)

    assert _attach_singletons(cur.connection) >= 1
    cur.execute("SELECT broker_id FROM broker_identities WHERE id = %s", (who,))
    broker = cur.fetchone()[0]
    assert broker is not None and _dirty(cur) == {pid}
    assert _drain_dirty(cur.connection, 2000, _now(cur)) == 1
    assert {b: r[3:5] for b, r in _book(cur, pid).items()} == {broker: (now - 4 * _D, None)}
    assert _attach_singletons(cur.connection) == 0 and _dirty(cur) == set()


def test_a_quiescent_pass_writes_nothing_and_the_range_form_deletes_a_gone_row(cur: Any) -> None:
    """Once the book holds the view's rows, the scoped and the range statements rewrite none (no new tuple) and the
    pass enqueues nothing; the daily sweep's range form deletes a row the view no longer holds."""
    now = _now(cur)
    pid, one, two = _property(cur), _broker(cur), _broker(cur)
    lone = _ad(cur, pid, _identity(cur, one), now - 30 * _D, now - 4 * _D, active=False)
    _ad(cur, pid, _identity(cur, two), now - 3 * _D, now - _D)
    _ad(cur, pid, _identity(cur, two, "idnes"), now - 2 * _D, now - _D, source="idnes")
    properties_changed(cur.connection, [pid])
    ctids = "SELECT ctid::text FROM broker_book WHERE property_id = %s ORDER BY broker_id"
    before = cur.execute(ctids, (pid,)).fetchall()
    assert len(before) == 2
    _clean(cur)

    properties_changed(cur.connection, [pid])
    for sql, params in ((_BROKER_BOOK_SCOPED_SQL, {"ids": [pid]}), (_BROKER_BOOK_BATCH_SQL, {"lo": pid, "hi": pid + 1})):
        cur.execute(sql, params)
        assert cur.rowcount == 0
    assert cur.execute(ctids, (pid,)).fetchall() == before and _dirty(cur) == set()

    cur.execute("UPDATE listings SET broker_identity_id = NULL WHERE id = %s", (lone,))
    cur.execute(_BROKER_BOOK_BATCH_SQL, {"lo": pid, "hi": pid + 1})
    assert set(_book(cur, pid)) == {two}


def test_tom_observed_follows_the_brokers_first_ad_in_the_projection_order(cur: Any) -> None:
    """browse_projection's order (migration 669) over all the broker's identities: an ad with a category first, then
    first_seen_at, then id. Seeded on a fresh (portal, category) pair: its first day is a sweep by definition (not
    observed), a lone later sighting is ordinary (observed). A category-less ad sighted earlier still is skipped,
    though it reads observed on its own; it and the ordinary ad are on the broker's other identity."""
    source, main, kind = "remax", "ostatni", "drazba"
    cur.execute("SELECT count(*) FROM listings WHERE source = %s AND category_main = %s AND category_type = %s",
                (source, main, kind))
    assert cur.fetchone()[0] == 0, "the seeded pair must be empty for its first day to be known"
    pid, broker = _property(cur), _broker(cur)
    first, other = _identity(cur, broker, source), _identity(cur, broker, source)

    def ad(who: int, day: str, category: bool) -> int:
        return _advert(cur, pid, source=source, broker_identity_id=who,
                       first_seen_at=datetime.fromisoformat(f"{day}T12:00:00+00:00"),
                       category_main=main if category else None, category_type=kind if category else None)

    uncategorised = ad(other, "2026-03-01", False)
    sweep = ad(first, "2026-03-02", True)
    ordinary = ad(other, "2026-03-03", True)
    cur.execute("REFRESH MATERIALIZED VIEW public.ingest_bulk_days")
    assert (_observed(cur, uncategorised), _observed(cur, sweep), _observed(cur, ordinary)) == (True, False, True)

    properties_changed(cur.connection, [pid])
    assert _book(cur, pid)[broker][3:] == (datetime.fromisoformat("2026-03-01T12:00:00+00:00"), None, False)
