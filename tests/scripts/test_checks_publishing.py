"""Hermetic tests for scripts.checks.publishing: the portal arms, dirty and the photo tags."""

# No DB: the wave-3 fake connection answers each statement by the SQL constant it carries, through
# the per-check clock. Every statement ran read-only against production on 2026-10-19 (queue 2 ms,
# visible 183 ms, stock 2.25 s warm / 2.6 s cold, photos 251 ms, dirty 0.1 ms) and PREPAREs
# against CI's replayed schema through the shared SQL corpus.

from __future__ import annotations

import ast
import datetime as _dt
import inspect
import re
from typing import Any

import pytest

from location_data.claims_common import SOURCES, served_location_predicate
from scraper.db import QUEUE_PRIORITY_CHANGED, QUEUE_PRIORITY_FAILURE, QUEUE_PRIORITY_NEW
from scripts.checks import publishing as pub
from scripts.checks.common import wilson_lower
from tests.scripts.test_checks_platform import _Conn

_UTC = _dt.timezone.utc
_NOW = _dt.datetime(2026, 10, 19, 6, 0, tzinfo=_UTC)
PORTALS = sorted(SOURCES)
_ARMS = ("ingest", "changes", "visible", "stock", "photos")

# The publishing keys exactly as the wave-4 contract spells them (§ 2.12).
T: dict[str, Any] = {
    "publishing_queue_warn_h": 3, "publishing_queue_fail_h": 6,
    "publishing_visible_warn": 0.02, "publishing_visible_fail": 0.10,
    "publishing_visible_min_n": 50, "publishing_stock_warn": 0.001,
    "publishing_stock_fail": 0.01, "publishing_stock_min_n": 200,
    "publishing_photos_unstored_warn": 0.02, "publishing_photos_unstored_fail": 0.10,
    "publishing_photos_min_n": 200, "publishing_photos_narrow_warn": 0.10,
    "publishing_photos_narrow_fail": 0.50, "publishing_photos_min_px": 600,
    "publishing_dirty_warn_h": 4, "publishing_dirty_fail_h": 8,
    "publishing_tag_late_min": 45, "publishing_tag_late_warn": 0.05,
    "publishing_tag_late_fail": 0.10, "publishing_tag_min_n": 500,
}


def _ago(**delta: float) -> _dt.datetime:
    return _NOW - _dt.timedelta(**delta)


def _photo(source: str, *, ads: int = 500, bare: int = 0, photos: int = 7_000,
           not_stored: int = 0, measured: int | None = None, narrow: int = 70,
           reason: str | None = None, reason_photos: int | None = None) -> tuple[Any, ...]:
    """A _PHOTOS_SQL row: one portal's photos of ads first written 1-24 h ago."""
    return (source, ads, bare, photos, not_stored,
            photos - not_stored if measured is None else measured, narrow, reason, reason_photos)


def _tags(*, images: int = 1_000, late: int = 20, waiting: int = 12, unvectored: int = 0,
          registry_at: _dt.datetime | None = _NOW - _dt.timedelta(days=3),
          paused_until: _dt.datetime | None = None, paused_by: str | None = None,
          pause_note: str | None = None, p50: float | None = 8.04, p90: float | None = 13.1,
          p99: float | None = 31.0,
          oldest: _dt.datetime | None = _NOW - _dt.timedelta(minutes=52)) -> list[tuple[Any, ...]]:
    """_TAGS_SQL's one row: the clip row's join and pause, then its photos stored 45 min-24 h."""
    return [(registry_at, paused_until, paused_by, pause_note, images, late, waiting, unvectored,
             p50, p90, p99, oldest)]


def _healthy() -> dict[str, Any]:
    """Every arm's rows for a fleet where everything reaches Browse."""
    return {
        pub._QUEUE_SQL: [],
        pub._VISIBLE_SQL: [(p, True, "visible", 500) for p in PORTALS],
        pub._STOCK_SQL: [*((p, 50_000, 0, 0, 0, 0) for p in PORTALS), (None, 362_000, 0, 0, 0, 0)],
        pub._PHOTOS_SQL: [_photo(p) for p in PORTALS],
        pub._DIRTY_SQL: [(0, None)],
        pub._TAGS_SQL: _tags(),
    }


def _check(**over: Any) -> tuple[dict[str, Any], _Conn]:
    """check_publishing over a healthy fleet, with the statements a test names replaced."""
    answers = _healthy()
    for name, rows in over.items():
        answers[getattr(pub, name)] = rows
    conn = _Conn(answers, now=_NOW)
    return pub.check_publishing(conn, T), conn


def _cells(result: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {c["subject"]: c for c in result["details"]["cells"]}


def _params(conn: _Conn, sql: str) -> Any:
    return next(params for sent, params, _ in conn.sent if sent == sql)


# --- shape -------------------------------------------------------------------------------------


def test_publishing_returns_one_cell_per_portal_and_arm_and_the_three_fleet_cells() -> None:
    out, _ = _check()
    assert out["check_key"] == "publishing" and out["status"] == "ok" and out["value"] == 0
    cells = _cells(out)
    assert set(cells) == {f"{p}/{arm}" for p in PORTALS for arm in _ARMS} | {
        "fleet/stock", "fleet/dirty", "fleet/photo_tags"}
    for subject, c in cells.items():
        assert set(c) == {"subject", "portal", "status", "value", "unit", "message", "facts"}
        portal, _, _arm = subject.partition("/")
        assert c["portal"] == (None if portal == "fleet" else portal)
        assert c["status"] == "ok", c


def test_every_publishing_statement_runs_on_the_checks_own_clock() -> None:
    _, conn = _check()
    assert conn.sent and all(armed for _, _, armed in conn.sent)


def test_browse_list_is_read_by_one_statement_per_arm_and_only_visible_and_stock_read_it() -> None:
    """browse_list's rebuild swaps the table behind its readers, so each read is one statement."""
    _, conn = _check()
    readers = [sql for sql, _, _ in conn.sent if "browse_list b" in sql]
    assert readers == [pub._VISIBLE_SQL, pub._STOCK_SQL]


# --- ingest and changes ----------------------------------------------------------------------


@pytest.mark.parametrize("hours, status", [(2.9, "ok"), (3.1, "warn"), (6.1, "fail")])
def test_ingest_watches_past_3_h_and_fails_past_6_h(hours: float, status: str) -> None:
    out, _ = _check(_QUEUE_SQL=[(PORTALS[0], "ingest", 1_240, 12, _ago(hours=hours))])
    c = _cells(out)[f"{PORTALS[0]}/ingest"]
    assert c["status"] == status and c["value"] == round(hours, 1) and c["unit"] == "h"
    assert c["facts"]["waiting"] == 1_240 and c["facts"]["claimed"] == 12
    assert "1,240 waiting, 12 claimed" in c["message"]


def test_a_starved_portal_reads_problem_with_its_limit_in_the_message() -> None:
    out, _ = _check(_QUEUE_SQL=[(PORTALS[0], "ingest", 15_064, 0, _ago(hours=216))])
    c = _cells(out)[f"{PORTALS[0]}/ingest"]
    assert c["status"] == "fail" and out["status"] == "fail" and out["value"] == 1
    assert c["message"].startswith("the oldest new ad has waited 9.0 d for its first fetch "
                                   "(Problem past 6 h)")


def test_ingest_counts_claimed_rows_and_never_a_known_ad_at_new_priority() -> None:
    """A dead drain's claim still ages; a photo re-fetch of a held ad is not a missing ad."""
    flat = " ".join(pub._QUEUE_SQL.split())
    assert "claimed_at is null" not in flat and "not d.given_up" in flat
    out, _ = _check(_QUEUE_SQL=[(PORTALS[0], "known", 5_000, 0, _ago(hours=30))])
    c = _cells(out)[f"{PORTALS[0]}/ingest"]
    assert c["status"] == "ok" and c["value"] == 0.0
    assert c["facts"]["known_ads_at_new_priority"] == 5_000


def test_the_queue_read_takes_its_priorities_from_scraper_db() -> None:
    _, conn = _check()
    assert _params(conn, pub._QUEUE_SQL) == {
        "new": QUEUE_PRIORITY_NEW,
        "priorities": [QUEUE_PRIORITY_NEW, QUEUE_PRIORITY_CHANGED, QUEUE_PRIORITY_FAILURE]}


def test_changes_judge_the_oldest_repriced_ad_on_the_same_limits() -> None:
    out, _ = _check(_QUEUE_SQL=[(PORTALS[1], "changes", 40, 0, _ago(hours=6.5)),
                                (PORTALS[2], "changes", 3, 1, _ago(minutes=20))])
    cells = _cells(out)
    assert cells[f"{PORTALS[1]}/changes"]["status"] == "fail"
    assert "for its re-fetch" in cells[f"{PORTALS[1]}/changes"]["message"]
    assert cells[f"{PORTALS[2]}/changes"]["status"] == "ok"
    assert cells[f"{PORTALS[1]}/ingest"]["status"] == "ok"


# --- visible -------------------------------------------------------------------------------------


@pytest.mark.parametrize("blocked, status", [(5, "ok"), (40, "warn"), (80, "fail")])
def test_visible_judges_the_lower_bound_of_new_ads_kept_out_of_browse(blocked: int,
                                                                      status: str) -> None:
    rows = [(PORTALS[0], True, "visible", 500 - blocked),
            (PORTALS[0], True, "no_property", blocked)]
    c = _cells(_check(_VISIBLE_SQL=rows)[0])[f"{PORTALS[0]}/visible"]
    assert c["status"] == status and c["facts"]["window_h"] == 24 and c["facts"]["ads"] == 500
    assert c["value"] == round(100 * blocked / 500, 2) and c["unit"] == "%"


def test_visible_names_each_blocking_stage() -> None:
    rows = [(PORTALS[0], True, "visible", 400), (PORTALS[0], True, "no_property", 60),
            (PORTALS[0], True, "location_not_served", 30),
            (PORTALS[0], True, "not_in_browse_list", 10)]
    c = _cells(_check(_VISIBLE_SQL=rows)[0])[f"{PORTALS[0]}/visible"]
    assert c["status"] == "fail" and c["facts"]["blocked"] == 100
    assert c["message"].startswith("100 of 500 active ads first written 30 min-24 h ago are not "
                                   "in Browse (20.00%; lower bound")
    assert c["message"].endswith("60 no property, 30 location not served, 10 not yet in Browse")


def test_visible_widens_to_72_h_under_50_ads_and_reads_not_measured_below() -> None:
    wide = [(PORTALS[0], True, "visible", 30), (PORTALS[0], False, "visible", 25),
            (PORTALS[0], False, "property_not_active", 1)]
    c = _cells(_check(_VISIBLE_SQL=wide)[0])[f"{PORTALS[0]}/visible"]
    assert c["status"] == "ok" and c["facts"]["window_h"] == 72 and c["facts"]["ads"] == 56
    assert "first written 30 min-72 h ago" in c["message"]
    thin = [(PORTALS[0], True, "visible", 20), (PORTALS[0], False, "visible", 20)]
    c = _cells(_check(_VISIBLE_SQL=thin)[0])[f"{PORTALS[0]}/visible"]
    assert c["status"] == "unknown" and c["message"].startswith("Not measured: 40 active ads")


def test_an_ad_waiting_for_the_next_rebuild_is_a_fact_not_a_blocker() -> None:
    """The jobs check judges a stalled rebuild; here it would ring the same outage twice."""
    rows = [(PORTALS[0], True, "visible", 300), (PORTALS[0], True, "waiting_for_rebuild", 200)]
    c = _cells(_check(_VISIBLE_SQL=rows)[0])[f"{PORTALS[0]}/visible"]
    assert c["status"] == "ok" and c["facts"]["blocked"] == 0
    assert c["facts"]["stages"]["waiting_for_rebuild"] == 200
    assert "200 waiting for Browse's next rebuild" in c["message"]


def test_a_property_not_yet_published_is_expected_never_a_blocker() -> None:
    """The publication rule holds a fresh property back up to 6 h by design (migrations 668/669):
    tallied and named, like the rebuild's lag, never in the blocked share."""
    rows = [(PORTALS[0], True, "visible", 200), (PORTALS[0], True, "unpublished", 280),
            (PORTALS[0], True, "waiting_for_rebuild", 15), (PORTALS[0], True, "no_property", 5)]
    c = _cells(_check(_VISIBLE_SQL=rows)[0])[f"{PORTALS[0]}/visible"]
    assert c["status"] == "ok" and c["facts"]["blocked"] == 5
    assert c["facts"]["stages"]["unpublished"] == 280
    assert c["message"].endswith("5 no property; 280 not yet published; "
                                 "15 waiting for Browse's next rebuild")


def test_unpublished_is_judged_after_location_before_the_rebuilds_lag_on_its_start() -> None:
    """Off the newest rebuild's start (r.at), never now(): a property the cap or the stamp
    released after that start is not in it yet either."""
    from toolkit.publication import visible_since_sql

    tail = " ".join(pub._STAGE_TAIL.split())
    arms = [tail.index(a) for a in ("then 'location_not_served'",
                                    f"when {visible_since_sql('p')} > r.at then 'unpublished'",
                                    "then 'waiting_for_rebuild'")]
    assert arms == sorted(arms)
    for sql in (pub._VISIBLE_SQL, pub._STOCK_SQL):
        assert "now()" not in sql.split("case", 1)[1].split("end as stage", 1)[0]


def test_visible_reads_new_active_ads_per_portal_through_the_first_seen_index() -> None:
    _, conn = _check()
    assert _params(conn, pub._VISIBLE_SQL) == {"sources": PORTALS, "window_h": 24, "wide_h": 72,
                                               "grace_min": 30}
    flat = " ".join(pub._VISIBLE_SQL.split())
    assert "a.source = any(%(sources)s::text[])" in flat and "and a.is_active" in flat


def test_location_is_judged_on_the_location_ad_with_the_imported_served_predicate() -> None:
    location = served_location_predicate("p.location_listing_ref_id")
    for sql in (pub._VISIBLE_SQL, pub._STOCK_SQL):
        assert location in sql
        assert served_location_predicate("a.id") not in sql
        assert "repr_listing_ref_id" not in sql


# --- stock -------------------------------------------------------------------------------------


def _stock(n: int, location: int = 0, browse: int = 0, waiting: int = 0, unpublished: int = 0,
           *, fleet: tuple[int, ...] = (362_000, 0, 0, 0, 0)) -> list[tuple[Any, ...]]:
    return [(PORTALS[0], n, location, browse, waiting, unpublished), (None, *fleet)]


@pytest.mark.parametrize("missing, status", [(40, "ok"), (60, "warn"), (1_000, "fail")])
def test_stock_watches_past_a_tenth_of_a_percent_and_fails_on_the_lower_bound(missing: int,
                                                                              status: str) -> None:
    c = _cells(_check(_STOCK_SQL=_stock(50_000, location=missing))[0])[f"{PORTALS[0]}/stock"]
    assert c["status"] == status and c["facts"]["missing"] == missing
    assert c["value"] == round(100 * missing / 50_000, 3) and c["unit"] == "%"
    lower = wilson_lower(missing, 50_000)
    assert c["facts"]["lower_bound"] == round(lower, 5)
    assert f"({missing / 50_000:.2%}; lower bound {lower:.2%})" in c["message"]


def test_stock_fails_only_with_200_properties_behind_the_share() -> None:
    c = _cells(_check(_STOCK_SQL=_stock(150, browse=30))[0])[f"{PORTALS[0]}/stock"]
    assert c["status"] == "warn", "20 % of 150 properties is Watch: too few for a Problem"


def test_the_fleet_stock_cell_counts_every_active_property_once() -> None:
    out, _ = _check(_STOCK_SQL=_stock(50_000, fleet=(362_000, 30_000, 0, 0, 0)))
    c = _cells(out)["fleet/stock"]
    assert c["status"] == "fail" and c["portal"] is None
    assert c["facts"]["properties"] == 362_000 and c["facts"]["missing"] == 30_000
    assert c["message"].startswith("30,000 of 362,000 active properties are missing from Browse")


def test_stock_names_its_stages_and_never_counts_the_rebuilds_lag_or_the_hold() -> None:
    c = _cells(_check(_STOCK_SQL=_stock(50_000, location=17, waiting=900, unpublished=1_700))[0])[
        f"{PORTALS[0]}/stock"]
    assert c["status"] == "ok" and c["facts"]["missing"] == 17
    assert c["facts"]["stages"] == {"location_not_served": 17, "not_in_browse_list": 0,
                                    "waiting_for_rebuild": 900, "unpublished": 1_700}
    assert c["message"].endswith("17 location not served; 1,700 not yet published; "
                                 "900 waiting for Browse's next rebuild")


def test_a_portal_holding_no_active_property_is_not_measured() -> None:
    out, _ = _check(_STOCK_SQL=[(None, 362_000, 0, 0, 0, 0)])
    c = _cells(out)[f"{PORTALS[0]}/stock"]
    assert c["status"] == "unknown"
    assert c["message"].startswith("Not measured: no active properties")


# --- photos ------------------------------------------------------------------------------------


@pytest.mark.parametrize("not_stored, status", [(70, "ok"), (210, "warn"), (3_640, "fail")])
def test_photos_judge_the_lower_bound_of_new_ads_photos_not_stored(not_stored: int,
                                                                   status: str) -> None:
    row = _photo(PORTALS[0], not_stored=not_stored, reason="429 Client Error: Too Many Requests",
                 reason_photos=not_stored)
    c = _cells(_check(_PHOTOS_SQL=[row])[0])[f"{PORTALS[0]}/photos"]
    assert c["status"] == status and c["value"] == round(100 * not_stored / 7_000, 2)
    lower = wilson_lower(not_stored, 7_000)
    assert c["facts"]["lower_bound"] == round(lower, 4)
    assert f"({not_stored / 7_000:.2%}; lower bound {lower:.2%})" in c["message"]
    if status != "ok":
        assert c["message"].endswith(f"top reason: 429 Client Error: Too Many Requests "
                                     f"({not_stored:,} photos)")


@pytest.mark.parametrize("narrow, status", [(90, "ok"), (150, "warn"), (600, "fail")])
def test_narrow_photos_watch_past_10_and_fail_past_50_percent(narrow: int, status: str) -> None:
    row = _photo(PORTALS[0], photos=1_000, measured=1_000, narrow=narrow)
    c = _cells(_check(_PHOTOS_SQL=[row])[0])[f"{PORTALS[0]}/photos"]
    assert c["status"] == status and c["facts"]["narrow_share"] == narrow / 1_000


def test_photos_under_200_are_not_measured_and_narrow_needs_200_with_a_width() -> None:
    few = _cells(_check(_PHOTOS_SQL=[_photo(PORTALS[0], ads=12, photos=150)])[0])
    assert few[f"{PORTALS[0]}/photos"]["status"] == "unknown"
    assert few[f"{PORTALS[0]}/photos"]["message"].startswith("Not measured: 150 photos of 12 ads")
    unmeasured = _photo(PORTALS[0], photos=1_000, measured=100, narrow=100)
    c = _cells(_check(_PHOTOS_SQL=[unmeasured])[0])[f"{PORTALS[0]}/photos"]
    assert c["status"] == "ok" and c["facts"]["narrow_share"].startswith("Not measured")


def test_not_stored_is_every_unstored_photo_but_a_taken_down_ads() -> None:
    """A CDN that refuses our transform lands as source_unavailable: still not stored."""
    flat = " ".join(pub._PHOTOS_SQL.split())
    assert "i.unavailable_reason is distinct from 'listing_taken_down'" in flat
    assert "count(p.image_id) filter (where p.storage_path is null) as not_stored" in flat
    assert "stored_width < %(min_px)s::int" in flat
    _, conn = _check()
    assert _params(conn, pub._PHOTOS_SQL) == {"sources": PORTALS, "window_h": 24, "grace_h": 1,
                                              "min_px": 600}


def test_the_photo_facts_carry_the_top_reason_and_the_ads_without_photos() -> None:
    row = _photo(PORTALS[0], ads=701, bare=72, photos=6_729, not_stored=3,
                 reason="source_unavailable: 404 Client Error: Not Found", reason_photos=3)
    c = _cells(_check(_PHOTOS_SQL=[row])[0])[f"{PORTALS[0]}/photos"]
    assert c["status"] == "ok"
    assert c["facts"]["top_reason"] == "source_unavailable: 404 Client Error: Not Found"
    assert c["facts"]["ads_without_photos"] == 72
    assert "72 of 701 ads have no photo rows" in c["message"]


# --- the fleet's dirty queue -------------------------------------------------------------------


@pytest.mark.parametrize("hours, status", [(3.9, "ok"), (4.1, "warn"), (8.1, "fail")])
def test_the_dirty_queue_watches_past_4_h_and_fails_past_8_h(hours: float, status: str) -> None:
    c = _cells(_check(_DIRTY_SQL=[(184_670, _ago(hours=hours))])[0])["fleet/dirty"]
    assert c["status"] == status and c["value"] == round(hours, 1) and c["portal"] is None
    assert c["facts"]["dirty"] == 184_670


def test_an_empty_dirty_queue_reads_ok() -> None:
    c = _cells(_check()[0])["fleet/dirty"]
    assert c["status"] == "ok" and c["value"] == 0.0 and c["facts"]["dirty"] == 0


# --- the fleet's photo tags --------------------------------------------------------------------


def _tag_cell(**over: Any) -> dict[str, Any]:
    return _cells(_check(_TAGS_SQL=_tags(**over))[0])["fleet/photo_tags"]


@pytest.mark.parametrize("late, status", [(20, "ok"), (70, "warn"), (150, "fail")])
def test_photo_tags_judge_the_lower_bound_of_photos_tagged_past_45_min(late: int,
                                                                       status: str) -> None:
    """Watch above 5 %, Problem above 10 % (the p90 past the line), each on the Wilson bound."""
    c = _tag_cell(late=late)
    lower = wilson_lower(late, 1_000)
    assert c["status"] == status and c["portal"] is None
    assert c["value"] == round(100 * late / 1_000, 2) and c["unit"] == "%"
    assert c["facts"]["lower_bound"] == round(lower, 4)
    said = (f"{late:,} of 1,000 photos stored over 45 min and under 24.0 h ago were tagged over "
            f"45 min after it, or not yet ({late / 1_000:.2%}; lower bound {lower:.2%})")
    assert said in c["message"]


def test_photo_tags_are_judged_on_the_lower_bound_never_the_raw_share() -> None:
    """33 of 600 late is a raw 5.5 %, past the Watch line; its lower bound (3.9 %) is not."""
    c = _tag_cell(images=600, late=33)
    assert c["status"] == "ok" and c["value"] == 5.5
    assert c["facts"]["lower_bound"] == round(wilson_lower(33, 600), 4) < 0.05


def test_under_500_photos_the_tag_line_is_not_measured() -> None:
    c = _tag_cell(images=400, late=400)
    assert c["status"] == "unknown"
    assert c["message"].startswith("Not measured: 400 photos stored over 45 min and under 24.0 h "
                                   "ago, fewer than 500")


def test_the_tag_line_is_not_measured_while_no_live_runner_row_names_clip() -> None:
    c = _tag_cell(registry_at=None, images=0, late=0, waiting=0, p50=None, p90=None, p99=None,
                  oldest=None)
    assert c["status"] == "unknown"
    assert c["message"] == "Not measured: clip is not on the job runner"


def test_the_cohort_starts_when_clip_joined_the_runner_never_in_githubs_era() -> None:
    c = _tag_cell(registry_at=_NOW - _dt.timedelta(hours=3))
    since = "photos stored over 45 min ago since clip joined the job runner 3.0 h ago"
    assert since in c["message"]
    assert c["facts"]["window_from"] == (_NOW - _dt.timedelta(hours=3)).isoformat()
    assert _tag_cell()["facts"]["window_from"] == (_NOW - _dt.timedelta(hours=24)).isoformat()


def test_a_paused_clip_job_reads_watch_at_most_and_names_who_paused_it_and_why() -> None:
    """A deliberate pause (the AC2 hold, a rollback brake) opens no incident, sends no e-mail."""
    until = _NOW + _dt.timedelta(hours=20)
    pause = {"paused_until": until, "paused_by": "operator", "pause_note": "AC2 pending"}
    c = _tag_cell(late=150, waiting=140, **pause)
    assert c["status"] == "warn"
    assert c["message"].startswith(f"paused by operator until {until.isoformat()}: AC2 pending; "
                                   "140 photos waiting; 150 of 1,000")
    assert (c["facts"]["paused_until"], c["facts"]["paused_by"], c["facts"]["pause_note"]) == (
        until.isoformat(), "operator", "AC2 pending")
    calm = _tag_cell(late=20, **pause)
    assert calm["status"] == "ok" and calm["message"].startswith("paused by operator until")
    lapsed = _tag_cell(late=150, **{**pause, "paused_until": _NOW - _dt.timedelta(minutes=1)})
    assert lapsed["status"] == "fail" and not lapsed["message"].startswith("paused")


def test_the_tag_facts_carry_the_percentiles_the_waiting_and_the_stamped_without_a_vector() -> None:
    oldest = _NOW - _dt.timedelta(hours=3, minutes=6)
    c = _tag_cell(late=70, waiting=48, unvectored=3, p50=9.04, p90=51.96, p99=184.0, oldest=oldest)
    keys = ("photos", "late", "p50_min", "p90_min", "p99_min", "waiting", "stamped_no_vector",
            "oldest_waiting_stored_at")
    assert {k: c["facts"][k] for k in keys} == {
        "photos": 1_000, "late": 70, "p50_min": 9.0, "p90_min": 52.0, "p99_min": 184.0,
        "waiting": 48, "stamped_no_vector": 3, "oldest_waiting_stored_at": oldest.isoformat()}
    assert c["message"].endswith("p50 9.0 min, p90 52.0 min, p99 184.0 min; 48 waiting, the "
                                 "oldest stored 3.1 h ago; 3 stamped with no vector")


def test_a_photo_is_tagged_only_once_stamped_and_vectored_by_the_taxonomys_model() -> None:
    """A stamp with no vector (a terminal mark, an undecodable photo) is never a tagged photo."""
    from scraper import jobs
    from scraper.clip_tagger import load_taxonomy

    flat = " ".join(pub._TAGS_SQL.split())
    assert "left join image_clip_embeddings e on e.image_id = i.id and e.model = %(model)s" in flat
    assert "case when e.image_id is not null then i.clip_tagged_at end" in flat
    assert "greatest(now() - make_interval(hours => %(window_h)s::int), j.registry_at)" in flat
    assert "registry_at is not null and retired_at is null" in flat
    _, conn = _check()
    params = _params(conn, pub._TAGS_SQL)
    assert params["model"] == load_taxonomy()["model"]
    assert params["runner"] == jobs.RUNNER
    assert params["job"] in {j.name for j in jobs.runner_jobs()}
    assert (params["window_h"], params["late_min"]) == (24, 45)


# --- rails -------------------------------------------------------------------------------------


def test_the_dirty_watch_clears_a_full_length_sweep() -> None:
    """The daily sweep holds the maintenance lease, and dirt ages 1:1 with it, up to its budget."""
    from scripts import recompute_property_stats as rps
    from scripts.verify_pipeline import THRESHOLDS

    assert THRESHOLDS["publishing_dirty_warn_h"] * 3600 > rps._MAX_BUDGET_SECONDS


def test_the_module_never_imports_the_harness_or_the_worker() -> None:
    tree = ast.parse(inspect.getsource(pub))
    imported = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    imported |= {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert not imported & {"scripts.verify_pipeline", "scraper.realtime_worker",
                           "toolkit.system_alerts"}


def test_the_check_reads_exactly_the_thresholds_the_contract_names() -> None:
    read = set(re.findall(r"""thresholds\[["'](\w+)["']\]""", inspect.getsource(pub)))
    assert read == set(T)


def test_the_harness_declares_every_threshold_this_check_reads_with_the_contracts_value() -> None:
    from scripts.verify_pipeline import THRESHOLDS

    assert {key: THRESHOLDS.get(key) for key in T} == T
