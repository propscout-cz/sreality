"""The publishing check's statements, executed: Browse's stages on the canonical ad, stock, photos and the queue.

The hermetic tests answer each statement with canned rows, the replay re-implements it in Python and PREPARE checks
only names and types, so nothing else runs the rules the SQL encodes. Each test seeds ads, properties, locations,
Browse rows, photos or queue rows relative to its transaction's now(), runs the module's statement with the parameters
the check sends, then the cells it feeds. The rules pinned:

- visible: an active ad first written 30 min-24 h ago (72 h for the wider window) is in Browse, or held at the first
  stage that stops it: no property, property not active, its property's canonical ad's location not served (no
  listing_location row, or one with neither a point nor a foreign determination), waiting for the next rebuild (a
  property born, or a canonical ad resolved again, after the newest browse_list rebuild started: a fact, never a
  blocker), else not yet in Browse. A property held at its status or location is one browse_projection does not serve.
- The canonical ad is the rollup's rank 1 of property_canonical_listings (migration 588), never the ad being counted.
- stock: an active property counts once per portal it holds an active ad on, and once for the fleet.
- photos: of ads first written 1-24 h ago, a taken-down ad's photos are out, not stored is every NULL storage_path,
  narrow is a stored width under 600 px (no width is unmeasured), and the top reason drops the URL.
- ingest: the oldest NEW row not given up, claimed or not, whose ad this portal does not hold; changes: the oldest
  CHANGED or FAILURE row; a presence check counts in neither.

Runs in CI's migrations lane (`TEST_DATABASE_URL`, `DB_RAILS_REQUIRED=1`); skipped locally.
"""

from __future__ import annotations

import datetime as _dt
import os
import uuid
from collections.abc import Iterable, Iterator
from typing import Any

import pytest

from location_data.claims_common import SOURCES
from scraper.db import (QUEUE_PRIORITY_CHANGED, QUEUE_PRIORITY_FAILURE, QUEUE_PRIORITY_NEW,
                        QUEUE_PRIORITY_VERIFY)
from scripts.checks import publishing as pub
from scripts.checks.common import iso, now as db_now
from scripts.recompute_property_stats import _RECOMPUTE_ONE_SQL
from scripts.verify_pipeline import THRESHOLDS

_DB_URL = os.environ.get("TEST_DATABASE_URL")
_REQUIRED = os.environ.get("DB_RAILS_REQUIRED") == "1"
_H = _dt.timedelta(hours=1)
_MIN = _dt.timedelta(minutes=1)
PORTALS = sorted(SOURCES)

pytestmark = pytest.mark.skipif(
    not _DB_URL and not _REQUIRED,
    reason="TEST_DATABASE_URL not set — this rail runs in CI's migrations lane",
)

# listing_location rows as (a point?, granularity, country_status). Served: a point, or a foreign determination.
_POINT = (True, "building", "cz")
_ABROAD = (False, "country", "foreign")
_NO_ANSWER = (False, "unknown", "undetermined")


@pytest.fixture()
def conn() -> Iterator[Any]:
    """One connection in one transaction, always rolled back, on a replay that holds no ad, property or queue row."""
    if not _DB_URL:
        pytest.fail("DB_RAILS_REQUIRED=1 but TEST_DATABASE_URL is not set — the migrations lane is "
                    "misconfigured and this rail would otherwise have skipped green.")
    import psycopg

    connection = psycopg.connect(_DB_URL)
    try:
        held = connection.execute("select (select count(*) from listings), (select count(*) from properties), "
                                  "(select count(*) from listing_detail_queue)").fetchone()
        if any(held):
            pytest.fail(f"the replay holds {held[0]} ads, {held[1]} properties and {held[2]} queue rows: these "
                        "tests count their own seeds, so they run on CI's empty replay")
        yield connection
    finally:
        connection.rollback()
        connection.close()


def _ad(conn: Any, source: str, *, seen: _dt.timedelta = 2 * _H, active: bool = True,
        property_id: int | None = None, native: str | None = None) -> int:
    """One ad of `source`, first written `seen` ago."""
    return conn.execute(
        "insert into listings (source, source_id_native, raw_json, category_main, category_type, price_czk, "
        "area_m2, is_active, first_seen_at, property_id) values (%s, %s, '{}'::jsonb, 'byt', 'prodej', 5000000, "
        "70, %s, now() - make_interval(secs => %s), %s) returning id",
        (source, native or f"w4-{uuid.uuid4().hex}", active, seen.total_seconds(), property_id)).fetchone()[0]


def _property(conn: Any, *, status: str = "active", active: bool = True,
              born: _dt.timedelta = 3 * _H) -> int:
    return conn.execute("insert into properties (status, is_active, created_at) "
                        "values (%s, %s, now() - make_interval(secs => %s)) returning id",
                        (status, active, born.total_seconds())).fetchone()[0]


def _located(conn: Any, listing_id: int, shape: tuple[bool, str, str], *,
             resolved: _dt.timedelta = 3 * _H) -> None:
    """The resolver's answer for one ad, written (or written again) `resolved` ago."""
    point, granularity, country = shape
    conn.execute(
        "insert into listing_location (listing_id, geom, match_confidence, granularity, uncertainty_radius_m, "
        "country_status, resolver_version, claim_set_hash, registry_version, resolved_at) values (%s, "
        "case when %s::boolean then ST_SetSRID(ST_MakePoint(14.42, 50.08), 4326) end, 'exact', "
        "%s::location_granularity, 5, %s::country_status, 'test', '\\x00'::bytea, 'test', "
        "now() - make_interval(secs => %s))",
        (listing_id, point, granularity, country, resolved.total_seconds()))


def _held(conn: Any, source: str, *, status: str = "active", active: bool = True,
          born: _dt.timedelta = 3 * _H, shape: tuple[bool, str, str] | None = _POINT,
          resolved: _dt.timedelta = 3 * _H) -> int:
    """A property whose one ad, first written 2 h ago, is its canonical ad; `shape` None leaves it unresolved."""
    pid = _property(conn, status=status, active=active, born=born)
    ad = _ad(conn, source, active=active, property_id=pid)
    conn.execute("update properties set repr_listing_ref_id = %s where id = %s", (ad, pid))
    if shape:
        _located(conn, ad, shape, resolved=resolved)
    return pid


def _rebuilt(conn: Any, ago: _dt.timedelta) -> None:
    """The newest successful browse_list rebuild started `ago`."""
    stamp = conn.execute("update derived_artifacts set last_succeeded_at = now() - make_interval(secs => %s) "
                         "where name = 'browse_list'", (ago.total_seconds(),))
    assert stamp.rowcount == 1


def _listed(conn: Any, pid: int) -> None:
    """The property's Browse row, as the rebuild writes it."""
    assert conn.execute("insert into browse_list select * from browse_projection where property_id = %s",
                        (pid,)).rowcount == 1


def _projected(conn: Any, pids: Iterable[int]) -> set[int]:
    """Those of `pids` that browse_projection serves."""
    rows = conn.execute("select property_id from browse_projection where property_id = any(%s)", (list(pids),))
    return {row[0] for row in rows.fetchall()}


def _visible(conn: Any) -> dict[tuple[str, bool, str], int]:
    """_VISIBLE_SQL's rows, with the parameters _visible_cells sends."""
    params = {"sources": PORTALS, "window_h": pub._WINDOW_H, "wide_h": pub._WIDE_WINDOW_H,
              "grace_min": pub._VISIBLE_GRACE_MIN}
    return {(source, recent, stage): ads
            for source, recent, stage, ads in conn.execute(pub._VISIBLE_SQL, params).fetchall()}


def _photo(conn: Any, listing_id: int, sequence: int, *, path: str | None = None, width: int | None = None,
           reason: str | None = None, error: str | None = None) -> None:
    """One image row of an ad; no `path` is a photo not stored."""
    conn.execute("insert into images (listing_id, sreality_url, sequence, storage_path, stored_width, "
                 "unavailable_reason, last_error) values (%s, %s, %s, %s, %s, %s, %s)",
                 (listing_id, _url(listing_id, sequence), sequence, path, width, reason, error))


def _url(listing_id: int, sequence: int) -> str:
    return f"https://cdn.test/{listing_id}/{sequence}.jpg"


def _queued(conn: Any, source: str, native: str, priority: int, waited: _dt.timedelta, *,
            claimed: _dt.timedelta | None = None, given_up: bool = False) -> None:
    """One detail-queue row enqueued `waited` ago, claimed `claimed` ago (None: never claimed)."""
    conn.execute("insert into listing_detail_queue (source, native_id, priority, enqueued_at, claimed_at, given_up) "
                 "values (%s, %s, %s, now() - make_interval(secs => %s), now() - make_interval(secs => %s), %s)",
                 (source, native, priority, waited.total_seconds(),
                  None if claimed is None else claimed.total_seconds(), given_up))


def _by_subject(cells: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {cell["subject"]: cell for cell in cells}


def test_each_new_ad_is_held_at_the_first_stage_that_stops_it_and_waiting_is_never_a_blocker(conn: Any) -> None:
    """Status before location, location before the rebuild's lag; a property born, or whose canonical ad was resolved
    again, after the newest rebuild started is waiting for the next one, which the cell keeps out of its blocked
    share."""
    _rebuilt(conn, 20 * _MIN)
    _ad(conn, "remax")
    merged = _held(conn, "remax", status="merged_away", shape=None)   # held by its status, not its location
    unresolved = _held(conn, "remax", shape=None, born=10 * _MIN)     # by its location, not the rebuild's lag
    unanswered = _held(conn, "remax", shape=_NO_ANSWER)
    newborn = _held(conn, "remax", born=10 * _MIN)
    re_resolved = _held(conn, "remax", resolved=10 * _MIN)
    abroad = _held(conn, "remax", shape=_ABROAD)                      # no point, but an answer
    held_out = _held(conn, "remax")                                   # servable before the rebuild, not in it
    listed = _held(conn, "remax")
    _listed(conn, listed)
    _ad(conn, "remax", seen=30 * _H)                                  # the 72 h window only
    for seen in (20 * _MIN, 80 * _H):                                 # inside the grace; past 72 h
        _ad(conn, "remax", seen=seen)
    _ad(conn, "remax", active=False)

    assert _visible(conn) == {
        ("remax", True, "visible"): 1, ("remax", True, "no_property"): 1,
        ("remax", True, "property_not_active"): 1, ("remax", True, "location_not_served"): 2,
        ("remax", True, "waiting_for_rebuild"): 2, ("remax", True, "not_in_browse_list"): 2,
        ("remax", False, "no_property"): 1,
    }
    assert _projected(conn, (merged, unresolved, unanswered, newborn, re_resolved, abroad, held_out, listed)) == {
        newborn, re_resolved, abroad, held_out, listed}

    def facts(min_n: int) -> dict[str, Any]:
        cells = pub._visible_cells(conn, {**THRESHOLDS, "publishing_visible_min_n": min_n}, PORTALS)
        return _by_subject(cells)["remax/visible"]["facts"]

    day, wide = facts(9), facts(10)
    assert (day["window_h"], day["ads"], day["blocked"], day["stages"]["waiting_for_rebuild"]) == (24, 9, 6, 2)
    assert (wide["window_h"], wide["ads"], wide["blocked"]) == (72, 10, 7)


def test_an_ad_is_judged_on_its_propertys_canonical_ad_never_on_its_own_location(conn: Any) -> None:
    """The rollup stores rank 1 of property_canonical_listings (an ad with a point first, then the earliest first
    written) as repr_listing_ref_id, and both the served predicate and the resolved-again read take that ad."""
    _rebuilt(conn, 20 * _MIN)
    located, abroad, re_resolved = _property(conn), _property(conn), _property(conn)
    _ad(conn, "idnes", property_id=located)                                       # no location of its own
    canonical = {located: _ad(conn, "bazos", seen=50 * _H, property_id=located)}
    _located(conn, canonical[located], _POINT)
    _located(conn, _ad(conn, "idnes", property_id=abroad), _ABROAD)              # served on its own
    canonical[abroad] = _ad(conn, "bazos", seen=10 * _H, property_id=abroad)     # first written earlier, no answer
    _located(conn, _ad(conn, "idnes", property_id=re_resolved), _POINT, resolved=10 * _MIN)
    canonical[re_resolved] = _ad(conn, "bazos", seen=50 * _H, property_id=re_resolved)
    _located(conn, canonical[re_resolved], _POINT)
    for pid in canonical:
        conn.execute(_RECOMPUTE_ONE_SQL, {"pid": pid})
    stored = conn.execute("select id, repr_listing_ref_id from properties where id = any(%s)", (list(canonical),))
    assert dict(stored.fetchall()) == canonical

    assert _visible(conn) == {
        ("idnes", True, "not_in_browse_list"): 2, ("idnes", True, "location_not_served"): 1,
        ("bazos", False, "not_in_browse_list"): 2, ("bazos", True, "location_not_served"): 1,
    }
    assert _projected(conn, canonical) == {located, re_resolved}


def test_stock_counts_an_active_property_once_per_portal_it_holds_an_active_ad_on(conn: Any) -> None:
    """And once for the fleet; a merged-away property and one with no active ad are not stock, and an inactive ad
    holds nothing."""
    _rebuilt(conn, 20 * _MIN)
    listed = _held(conn, "remax")
    _ad(conn, "bazos", property_id=listed)
    _listed(conn, listed)
    _held(conn, "remax", shape=None)                                      # location not served
    _ad(conn, "bazos", active=False, property_id=_held(conn, "remax"))   # not yet in Browse, a remax holding only
    _held(conn, "bazos", born=10 * _MIN)                                 # waiting for the next rebuild
    _held(conn, "remax", status="merged_away")
    _held(conn, "remax", active=False)

    rows = {source: tuple(counts) for source, *counts in conn.execute(pub._STOCK_SQL).fetchall()}
    assert rows == {"remax": (3, 1, 1, 0), "bazos": (2, 0, 0, 1), None: (4, 1, 1, 1)}

    cells = _by_subject(pub._stock_cells(conn, THRESHOLDS, PORTALS))
    assert {subject: (cells[subject]["facts"]["properties"], cells[subject]["facts"]["missing"],
                      cells[subject]["facts"]["stages"])
            for subject in ("remax/stock", "bazos/stock", "fleet/stock")} == {
        "remax/stock": (3, 2, {"location_not_served": 1, "not_in_browse_list": 1, "waiting_for_rebuild": 0}),
        "bazos/stock": (2, 0, {"location_not_served": 0, "not_in_browse_list": 0, "waiting_for_rebuild": 1}),
        "fleet/stock": (4, 2, {"location_not_served": 1, "not_in_browse_list": 1, "waiting_for_rebuild": 1}),
    }
    assert cells["idnes/stock"]["status"] == "unknown"


def test_photos_count_new_ads_photos_but_a_taken_down_ads_and_name_the_top_reason_without_its_url(conn: Any) -> None:
    """Not stored is every NULL storage_path whatever its reason; narrow is a stored width under 600 px; the three
    taken-down photos would otherwise be the top reason."""
    stored = _ad(conn, "bezrealitky")
    for sequence, width in enumerate((1200, 600, 599, None)):
        _photo(conn, stored, sequence, path=f"w4/{stored}/{sequence}.jpg", width=width)
    _photo(conn, stored, 4)                                               # pending, no attempt recorded
    failing = _ad(conn, "bezrealitky", seen=20 * _H)
    for sequence in (0, 1):
        _photo(conn, failing, sequence,
               error=f"403 Client Error: Forbidden for url: {_url(failing, sequence)}")
    _photo(conn, failing, 2, reason="source_unavailable",
           error=f"404 Client Error: Not Found for url: {_url(failing, 2)}")
    for sequence in (3, 4, 5):
        _photo(conn, failing, sequence, reason="listing_taken_down",
               error=f"410 Client Error: Gone for url: {_url(failing, sequence)}")
    _ad(conn, "bezrealitky", seen=5 * _H)                                  # no photo rows
    for seen in (30 * _MIN, 30 * _H):                                      # inside the 1 h grace; past 24 h
        _photo(conn, _ad(conn, "bezrealitky", seen=seen), 0)

    params = {"sources": PORTALS, "window_h": pub._WINDOW_H, "grace_h": pub._PHOTOS_GRACE_H,
              "min_px": int(THRESHOLDS["publishing_photos_min_px"])}
    rows = {row[0]: row[1:] for row in conn.execute(pub._PHOTOS_SQL, params).fetchall()}
    assert rows == {"bezrealitky": (3, 1, 8, 4, 3, 1, "403 Client Error: Forbidden", 2)}

    expected = {"ads": 3, "ads_without_photos": 1, "photos": 8, "not_stored": 4, "stored_with_width": 3,
                "narrow": 1, "top_reason": "403 Client Error: Forbidden", "top_reason_photos": 2}
    facts = _by_subject(pub._photo_cells(conn, THRESHOLDS, PORTALS))["bezrealitky/photos"]["facts"]
    assert {key: facts[key] for key in expected} == expected


def test_ingest_ages_the_oldest_ad_this_portal_never_wrote_and_changes_the_oldest_re_fetch(conn: Any) -> None:
    """Claimed or not (a dead drain's claim still ages), never given up; a NEW row for an ad the portal holds is a
    photo re-fetch, while another portal's ad of the same native id holds nothing here; presence checks count in
    neither."""
    _ad(conn, "idnes", native="w4-held")
    _ad(conn, "bazos", native="w4-elsewhere")
    for native, priority, waited, claimed, given_up in (
            ("w4-elsewhere", QUEUE_PRIORITY_NEW, 5 * _H, None, False),
            ("w4-claimed", QUEUE_PRIORITY_NEW, 7 * _H, 1 * _H, False),
            ("w4-given-up", QUEUE_PRIORITY_NEW, 30 * _H, None, True),
            ("w4-held", QUEUE_PRIORITY_NEW, 20 * _H, None, False),
            ("w4-repriced", QUEUE_PRIORITY_CHANGED, 2 * _H, None, False),
            ("w4-retried", QUEUE_PRIORITY_FAILURE, 4 * _H, None, False),
            ("w4-retried-out", QUEUE_PRIORITY_FAILURE, 40 * _H, None, True),
            ("w4-presence", QUEUE_PRIORITY_VERIFY, 50 * _H, None, False)):
        _queued(conn, "idnes", native, priority, waited, claimed=claimed, given_up=given_up)
    now = db_now(conn)

    params = {"new": QUEUE_PRIORITY_NEW,
              "priorities": [QUEUE_PRIORITY_NEW, QUEUE_PRIORITY_CHANGED, QUEUE_PRIORITY_FAILURE]}
    rows = {(source, arm): (waiting, claimed, oldest)
            for source, arm, waiting, claimed, oldest in conn.execute(pub._QUEUE_SQL, params).fetchall()}
    assert rows == {("idnes", "ingest"): (2, 1, now - 7 * _H), ("idnes", "known"): (1, 0, now - 20 * _H),
                    ("idnes", "changes"): (2, 0, now - 4 * _H)}

    cells = _by_subject(pub._queue_cells(conn, THRESHOLDS, now, PORTALS))
    assert (cells["idnes/ingest"]["value"], cells["idnes/ingest"]["facts"]) == (7.0, {
        "waiting": 2, "claimed": 1, "oldest_enqueued_at": iso(now - 7 * _H), "known_ads_at_new_priority": 1})
    assert (cells["idnes/changes"]["value"], cells["idnes/changes"]["facts"]) == (4.0, {
        "waiting": 2, "claimed": 0, "oldest_enqueued_at": iso(now - 4 * _H)})
