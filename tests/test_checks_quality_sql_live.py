"""The parsing alarm's and the quality gauges' statements, executed: what each one counts, read off seeded rows.

The hermetic tests answer each statement with canned rows, the replays model the arms in Python and PREPARE checks only
names and types, so this file is where the SQL that scripts/checks/quality.py sends runs. Each test seeds ads,
snapshots, locations, readings, contracts, registry versions or stored results at seconds before its transaction's
now(), under made-up portals where a statement takes its portals as data and under the nine where a whole check reads
them (CI's replay holds no ad), then sends the module's statement with the parameters the check sends, or runs the
whole check, in a transaction that always rolls back. The rules pinned:
- payload: each portal's predicate, bound to it by name, counts exactly the active ads first written in the last 48 h
  that its Python reader cannot read (every stored shape, the traps, payloads that are no object); an older ad, an
  inactive one and one at the window's own edge are out;
- measures: an input gap is an ad with no price or no positive area (a NULL area too), never a price the basis floor
  rejects, read off the view through the measure function; the cell judges the Wilson lower bound of the new ads' gap
  against the whole stock's, so a regression is Watch or Problem, a standing gap cancels, and under 50 new ads or with
  no basis there is no verdict;
- median: the stock's per-m² median against this check's own median cell 6-14 d old (both edges strict), the newest per
  cell whatever its status, from a cells array only and never from another key's rows;
- churn: an ad's second and later snapshots per portal and 24-h window back from now over 29 windows (an inactive ad's
  too) beside each portal's active ads, and a storm judged against the portal's own 28-day median day;
- field matrix: the active ads of enabled scraper portals only: fill, booleans and the spellings off the canon;
- data quality: the floor, divergence and estate-sum shares and the stock's measure gap come off the view, land skipped;
  the ship-time red (realitymix komerční pronájem, 472 of 4,140, LB 10.5 %) is Problem; links are counts per portal;
- location: active ads first written more than NEW_AD_H hours ago, exactly where publishing's visible day ends; no row,
  or a Czech or undetermined row with no town, is Problem and a foreign one never; the KÚ count reads rows with a town
  only and spares only an obec holding a KÚ the boundary load found degenerate at the current version, at this resolver
  version only; the registry cell reads the current version's age and a newer one left staged, an older staged one
  being history;
- description lane: the scope is the lane's own (an active contract's llm_text entry, or an open gate); arrivals 1-24 h
  old read at any version of the current SCHEMA_VERSION, the given-up apart, p95 minutes to the first reading; the stock
  is the lane's one predicate beside every reading of the last 24 h;
- trends: a gauge cell carries its series on from the newest measured result, a subject it lacks from an older one.
The statements run in CI's migrations lane (`TEST_DATABASE_URL`, `DB_RAILS_REQUIRED=1`) and skip locally; the rail
that every statement the module defines is sent here runs in every lane.
"""

from __future__ import annotations

import datetime as _dt
import itertools
import json
import os
import uuid
from collections.abc import Iterator
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from location_data.claims_common import PAYLOAD_SHAPE_READERS, PAYLOAD_SHAPE_REMEDIES, SOURCES
from location_data.resolver.version import RESOLVER_VERSION
from scraper import field_census
from scripts.checks import publishing as pub
from scripts.checks import quality as q
from scripts.checks.clock import fetchall
from scripts.checks.common import NEW_AD_H, iso, wilson_lower
from scripts.verify_pipeline import THRESHOLDS
from tests.location_data import claim_intake_fixtures as fx
from toolkit import description_extraction as text_lane
from toolkit.measures import unit_label

_DB_URL = os.environ.get("TEST_DATABASE_URL")
_REQUIRED = os.environ.get("DB_RAILS_REQUIRED") == "1"
_H = 3600.0
_D = 86400.0
_UTC = _dt.timezone.utc
_PRAGUE = ZoneInfo("Europe/Prague")
# sreality rows carry a positive sreality_id (the sign CHECK, migration 311), every other portal NULL; ten thousand
# ids per seed call, so no two calls collide.
_SREALITY_IDS = itertools.count(9_600_000_000, 10_000)
_KEYS = (q._PARSING, q._DATA_QUALITY, q._LOCATION, q._TEXT)
_MODEL = "w6-live-model"
# Made-up RÚIAN codes no ad carries: two towns and a KÚ of each.
_TOWN, _DEGENERATE_TOWN, _KU, _DEGENERATE_KU = 990_401, 990_402, 990_501, 990_502

live = pytest.mark.skipif(not _DB_URL and not _REQUIRED,
                          reason="TEST_DATABASE_URL not set — this rail runs in CI's migrations lane")

# Each statement quality.py defines and the test here that sends it with the check's parameters. The module also sends
# common's clock and trend reads, field_census.FIELD_MATRIX_SQL and the description lane's MODEL_SQL,
# LLM_TEXT_SOURCES_SQL, EXTRACTION_LATENCY_SQL and ELIGIBLE_LAG_SQL: each is sent below too.
_SENT = {
    "PLAUSIBILITY_SQL": "test_a_new_ads_measure_gap_is_judged_against_the_whole_stocks_through_the_measure_function",
    "_MEDIAN_BASELINE_SQL": "test_the_median_baseline_is_this_checks_own_median_cells_six_to_fourteen_days_old",
    "_PAYLOAD_SQL": "test_each_payload_predicate_is_bound_to_its_portal_and_counts_what_its_reader_cannot_read",
    "_CHURN_SQL": "test_churn_counts_second_and_later_snapshots_per_portal_and_day_beside_its_active_ads",
    "_URL_SQL": "test_data_quality_reads_the_ship_time_red_off_the_view_and_skips_land",
    "_DEGENERATE_KU_OBCE_SQL": "test_location_counts_active_ads_past_the_edge_with_no_town_and_spares_a_degenerate_ku",
    "_LOCATION_TOWN_COVERAGE_SQL": "test_the_location_gauge_starts_exactly_where_publishings_visible_day_ends",
    "_REGISTRY_LIVENESS_SQL": "test_the_registry_cell_reads_the_current_versions_age_and_a_newer_one_left_staged",
}


class _Seed:
    """One test's rows, at seconds before its transaction's now(), under names of its own."""

    def __init__(self, conn: Any) -> None:
        self.conn = conn
        self.now = conn.execute("select now()").fetchone()[0].astimezone(_UTC)
        self.tag = uuid.uuid4().hex[:10]

    def portal(self, name: str) -> str:
        """A made-up portal: a statement that takes its portals as data counts it alone."""
        return f"w6-{name}-{self.tag}"

    def label(self, name: str) -> str:
        return f"w6:{self.tag}:{name}"

    def ago(self, *, days: float = 0.0, hours: float = 0.0) -> _dt.datetime:
        return self.now - _dt.timedelta(days=days, hours=hours)

    def ads(self, source: str, n: int = 1, *, seen_h: float = 720.0, active: bool = True, main: str | None = "byt",
            deal: str | None = "prodej", price: int | None = 5_000_000, area: float | None = 70.0,
            usable: float | None = None, estate: float | None = None, url: str | None = "https://portal.test/ad",
            raw: Any = None, description: str | None = None) -> list[int]:
        """`n` alike ads of `source`, first written `seen_h` hours ago (30 d by default: the standing stock)."""
        rows = self.conn.execute(
            "insert into listings (source, source_id_native, sreality_id, raw_json, category_main, category_type, "
            "  price_czk, area_m2, usable_area, estate_area, source_url, description, is_active, first_seen_at) "
            "select %(source)s::text, %(native)s::text || '-' || g, "
            "       case when %(source)s::text = 'sreality' then %(base)s::bigint + g end, %(raw)s::jsonb, "
            "       %(main)s::text, %(deal)s::text, %(price)s::int, %(area)s::numeric, %(usable)s::numeric, "
            "       %(estate)s::numeric, %(url)s::text, %(description)s::text, %(active)s::boolean, "
            "       now() - make_interval(secs => %(secs)s::float8) "
            "  from generate_series(1, %(n)s::int) as g returning id",
            {"source": source, "native": f"w6-{uuid.uuid4().hex}", "base": next(_SREALITY_IDS),
             "raw": json.dumps({} if raw is None else raw), "main": main, "deal": deal, "price": price,
             "area": area, "usable": usable, "estate": estate, "url": url, "description": description,
             "active": active, "secs": seen_h * _H, "n": n}).fetchall()
        return [row[0] for row in rows]

    def payload_ads(self, source: str, payloads: list[Any], *, seen_h: float, active: bool = True) -> None:
        """One ad of a made-up portal per payload, first written `seen_h` hours ago (None is JSON null, a value)."""
        self.conn.execute(
            "insert into listings (source, source_id_native, raw_json, is_active, first_seen_at) "
            "select %(source)s::text, %(native)s::text || '-' || p.ord, p.raw, %(active)s::boolean, "
            "       now() - make_interval(secs => %(secs)s::float8) "
            "  from jsonb_array_elements(%(payloads)s::jsonb) with ordinality as p(raw, ord)",
            {"source": source, "native": f"w6-{uuid.uuid4().hex}", "active": active, "secs": seen_h * _H,
             "payloads": json.dumps(payloads)})

    def snapshots(self, listing_id: int, ages_s: list[float]) -> None:
        """Snapshots of one ad, each written the given seconds before now."""
        self.conn.execute(
            "insert into listing_snapshots (listing_id, scraped_at, content_hash, raw_json) "
            "select %(id)s::bigint, now() - make_interval(secs => t.age), 'w6-' || t.ord, '{}'::jsonb "
            "  from unnest(%(ages)s::float8[]) with ordinality as t(age, ord)",
            {"id": listing_id, "ages": [float(age) for age in ages_s]})

    def snapshot_days(self, listing_id: int, days: int) -> None:
        """One snapshot exactly `days` calendar days before now, as the churn window counts."""
        self.conn.execute("insert into listing_snapshots (listing_id, scraped_at, content_hash, raw_json) "
                          "values (%s, now() - make_interval(days => %s::int), 'w6-edge', '{}'::jsonb)",
                          (listing_id, days))

    def located(self, listing_id: int, *, country: str = "cz", granularity: str = "obec", obec: int | None = None,
                katastr: int | None = None, point: bool = True, resolver: str = RESOLVER_VERSION) -> None:
        """The resolver's answer for one ad."""
        self.conn.execute(
            "insert into listing_location (listing_id, geom, obec_kod, katastr_kod, match_confidence, granularity, "
            "  uncertainty_radius_m, country_status, resolver_version, claim_set_hash, registry_version) values "
            "(%s, case when %s::boolean then ST_SetSRID(ST_MakePoint(14.42, 50.08), 4326) end, %s::bigint, "
            "  %s::bigint, 'exact', %s::location_granularity, 5, %s::country_status, %s, '\\x00'::bytea, 'test')",
            (listing_id, point, obec, katastr, granularity, country, resolver))

    def result(self, key: str, *, days: float = 0.0, exact_days: int | None = None, status: str = "ok",
               cells: list[dict[str, Any]] | None = None, details: dict[str, Any] | None = None) -> None:
        """A stored result of `key`, run `days` before now, or exactly `exact_days` calendar days as windows count."""
        at = ("now() - make_interval(days => %(ago)s::int)" if exact_days is not None
              else "now() - make_interval(secs => %(ago)s::float8)")
        self.conn.execute(
            f"insert into pipeline_check_results (run_at, check_key, status, details) "
            f"values ({at}, %(key)s, %(status)s, %(details)s::jsonb)",
            {"ago": exact_days if exact_days is not None else days * _D, "key": key, "status": status,
             "details": json.dumps({"cells": cells or []} if details is None else details)})

    def no_registry(self) -> None:
        """No current RÚIAN version, whatever the replay holds (migration 381's index allows one)."""
        self.conn.execute("update registry_versions set is_current = false where is_current")

    def version(self, name: str, *, age_d: int, current: bool = False, loaded_h: float = 0.0) -> int:
        """A RÚIAN registry version dated `age_d` days before today, loaded `loaded_h` hours ago."""
        return int(self.conn.execute(
            "insert into registry_versions (label, kind, source_date, artifact_urls, proj_version, proj_pipeline, "
            "  loaded_at, is_current) values (%s, 'baseline', current_date - %s::int, '{}'::jsonb, 'w6', 'w6', "
            "  now() - make_interval(secs => %s::float8), %s) returning id",
            (self.label(name), age_d, loaded_h * _H, current)).fetchone()[0])

    def unit(self, level: str, code: int, version_id: int, *, parent: int | None = None) -> int:
        """A registry unit still valid (no valid_to)."""
        return int(self.conn.execute(
            "insert into ruian_admin_units (level, code, name, name_norm, parent_id, path, display_path, valid_from, "
            "  first_version_id, last_version_id) values (%s::ruian_level, %s, %s, %s, %s, %s::ltree, %s, "
            "  current_date - 30, %s, %s) returning id",
            (level, code, f"w6 {code}", f"w6 {code}", parent, f"w6x{code}", f"w6 {code}", version_id, version_id),
        ).fetchone()[0])

    def discrepancy(self, version_id: int, ku: int, kind: str) -> None:
        self.conn.execute("insert into registry_load_discrepancies (registry_version_id, entity_kind, entity_code, "
                          "discrepancy) values (%s, 'katastralni_uzemi', %s, %s)", (version_id, ku, kind))

    def contract(self, source: str, *, method: str = "llm_text", active: bool = True) -> None:
        """A portal contract with one location entry read by `method`."""
        contract_id = self.conn.execute(
            "insert into portal_contracts (source, version, contract_sha256, git_ref, is_active) "
            "values (%s, 1, '\\x00'::bytea, 'w6', %s) returning id", (source, active)).fetchone()[0]
        self.conn.execute(
            "insert into portal_contract_entries (contract_id, entry_id, surface, page_kind, locator, claim_type, "
            "  extraction_method) values (%s, 'w6.txt.town', 'description', 'detail', '{}'::jsonb, 'obec_name', "
            "  %s::location_extraction_method)", (contract_id, method))

    def model(self, model: str | None) -> None:
        """The description lane's one switch, app_settings.enrichment_model (JSON null names no model)."""
        self.conn.execute("insert into app_settings (key, value) values (%s, %s::jsonb) "
                          "on conflict (key) do update set value = excluded.value",
                          (text_lane.MODEL_SETTING, json.dumps(model)))

    def reading(self, listing_id: int, version: str, *, after_min: float | None = None, hours_ago: float = 1.0,
                error: bool = False, attempts: int | None = None, stale: bool = False) -> None:
        """A reading of an ad's text keyed as the lane keys it (its text's hash, or a stale one), written `after_min`
        minutes after the ad was first written or `hours_ago` before now; a failed one carries its error."""
        extracted: dict[str, Any] = {"error": "w6: the provider refused"} if error else {"has_lift": {"value": True}}
        if attempts is not None:
            extracted["attempts"] = attempts
        self.conn.execute(
            "insert into listing_description_enrichments (listing_id, text_hash, extractor_version, extracted, "
            "  filled, model, created_at) "
            f"select l.id, case when %(stale)s::boolean then 'w6-stale' else {text_lane._HASH_EXPR} end, "
            "       %(version)s::text, %(extracted)s::jsonb, '{}'::jsonb, 'w6', "
            "       case when %(after)s::float8 is null then now() - make_interval(secs => %(ago)s::float8) "
            "            else l.first_seen_at + make_interval(secs => %(after)s::float8) end "
            "  from listings l where l.id = %(id)s",
            {"stale": stale, "version": version, "extracted": json.dumps(extracted),
             "after": None if after_min is None else after_min * 60.0, "ago": hours_ago * _H, "id": listing_id})


@pytest.fixture()
def conn() -> Iterator[Any]:
    """One connection in one transaction, always rolled back, on a replay that holds no ad, no description reading and
    no stored result of these four checks: the whole checks read the whole stock and their own history."""
    if not _DB_URL:
        pytest.fail("DB_RAILS_REQUIRED=1 but TEST_DATABASE_URL is not set — the migrations lane is "
                    "misconfigured and this rail would otherwise have skipped green.")
    import psycopg

    connection = psycopg.connect(_DB_URL)
    try:
        held = connection.execute(  # opens the transaction every seed rolls back with
            "select (select count(*) from listings), (select count(*) from listing_description_enrichments), "
            "(select count(*) from pipeline_check_results where check_key = any(%s::text[]))",
            (list(_KEYS),)).fetchone()
        if any(held):
            pytest.fail(f"the replay holds {held[0]} ads, {held[1]} description readings and {held[2]} results of "
                        f"{', '.join(_KEYS)}: these tests count their own seeds, so they run on CI's empty replay")
        yield connection
    finally:
        connection.rollback()
        connection.close()


@pytest.fixture()
def seed(conn: Any) -> _Seed:
    return _Seed(conn)


def _by_subject(cells: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Cells by subject, each subject emitted once."""
    by = {c["subject"]: c for c in cells}
    assert len(by) == len(cells), "a subject was emitted twice"
    return by


def _whole(out: dict[str, Any], key: str) -> dict[str, dict[str, Any]]:
    """A whole check's cells by subject: a measured result always carries them (wave 6)."""
    assert out["check_key"] == key and out["details"]["cells"], out["message"]
    return _by_subject(out["details"]["cells"])


def _view(conn: Any, *sources: str) -> list[dict[str, Any]]:
    """The measure view's rows of `sources`, read as the checks read it; loud if the admin gate shut this login out."""
    assert conn.execute("select is_platform_admin()").fetchone()[0], (
        "the migrations lane's login must pass the view's admin gate (a bypassrls login, no role switch: migration "
        "330), or every arm on the view reads no row and this rail passes vacuously")
    rows, why = q._read_plausibility(conn)
    assert why is None, why
    return [row for row in rows if row["source"] in sources]


def _picked(cell: dict[str, Any], *keys: str) -> dict[str, Any]:
    return {key: cell["facts"][key] for key in keys}


def test_every_statement_the_module_defines_is_sent_by_a_test_here() -> None:
    """A statement added to quality.py fails this rail until a test here executes it on the replayed schema."""
    assert sorted(name for name in vars(q) if name.endswith("_SQL")) == sorted(_SENT)
    assert set(_SENT.values()) <= {name for name in globals() if name.startswith("test_")}


# --- parsing: payload shape -------------------------------------------------------------------------------------------

# A key sent null (content, so readable), keys one level off, a list or a string naming the keys, no object at all.
_TRAPS: list[Any] = [{"locality": None}, {"locality": {"city": None}}, {"locality": {"gps_lat": 50.08}},
                     {"gps_lat": 50.08}, {"ruianId": None}, {"gps": {"ruianId": "22698884"}},
                     {"municipality": "Kolín", "point": None}, {}, ["ruianId", "municipality", "point", "locality"],
                     "ruianId", 1, None]


def _payloads() -> list[Any]:
    """Every stored payload shape of the claim-intake fixtures, then the traps."""
    stored = [value for name, value in sorted(vars(fx).items())
              if name.isupper() and not name.startswith("_") and isinstance(value, dict)]
    return stored + _TRAPS


@live
def test_each_payload_predicate_is_bound_to_its_portal_and_counts_what_its_reader_cannot_read(seed: _Seed) -> None:
    """_PAYLOAD_SQL with a made-up portal in each predicate's place, bound as the check binds them (shape_i is the i-th
    predicate's portal): each counts its active ads first written in the 48 h and calls unreadable exactly what its
    Python reader cannot read. The three counts differ, so a predicate bound to the wrong portal cannot pass."""
    payloads = _payloads()
    made_up = {portal: seed.portal(f"payload-{portal}") for portal in q._PAYLOAD_PORTALS}
    for source in made_up.values():
        seed.payload_ads(source, payloads, seen_h=2)
        seed.payload_ads(source, [{}], seen_h=2, active=False)
        seed.payload_ads(source, [{}], seen_h=q._PAYLOAD_WINDOW_H)       # the window's own edge: out (strict)
        seed.payload_ads(source, [{}], seen_h=72)
    expected = {source: (len(payloads), sum(not PAYLOAD_SHAPE_READERS[portal](raw) for raw in payloads))
                for portal, source in made_up.items()}
    assert len({bad for _, bad in expected.values()}) == len(made_up)
    assert all(0 < bad < ads for ads, bad in expected.values())

    params = {"portals": list(made_up.values()), "window_h": q._PAYLOAD_WINDOW_H,
              **{f"shape_{i}": made_up[portal] for i, portal in enumerate(q._PAYLOAD_PORTALS)}}
    rows = seed.conn.execute(q._PAYLOAD_SQL, params).fetchall()

    assert {source: (ads, bad) for source, ads, bad in rows} == expected


@live
def test_the_parsing_alarm_sends_every_statement_and_emits_every_subject(seed: _Seed) -> None:
    """The whole check on the replayed schema: a fill cell per blessed field (a vocabulary cell where it has a canon),
    the census, a payload cell per payload portal, a churn cell per portal, measures and a median per view row with a
    basis, and no trend (an alarm). 8 of 40 new bezrealitky ads lost `ruianId`: Problem with location_data's remedy."""
    without_key = {key: value for key, value in fx.BEZREALITKY.items() if key != "ruianId"}
    seed.ads("bezrealitky", 30, seen_h=2, raw=fx.BEZREALITKY)
    seed.ads("bezrealitky", 2, seen_h=2, raw={**fx.BEZREALITKY, "ruianId": None})    # sent null: content, readable
    seed.ads("bezrealitky", 8, seen_h=2, raw=without_key)
    seed.ads("bezrealitky", 5, seen_h=60, raw=without_key)                            # before the 48-h window
    seed.ads("sreality", 30, seen_h=2, raw=fx.SREALITY_POST_CUTOVER)
    seed.ads("mmreality", 3, seen_h=2, raw=fx.MMREALITY_ACCURATE)

    out = q.check_parsing(seed.conn, THRESHOLDS)
    cells = _whole(out, q._PARSING)

    assert out["status"] == "fail" and "measures" not in out["details"].get("not_measured", {})
    blessed = [key.split("/", 1) for key in field_census.load_baseline()["cells"]]
    assert {f"{p}/{field_census.FILL}/{f}" for p, f in blessed} <= set(cells)
    assert {f"{p}/{field_census.VOCABULARY}/{f}" for p, f in blessed if field_census.canonical_values(f)} <= set(cells)
    assert {f"{p}/churn" for p in SOURCES} | {f"{p}/payload" for p in q._PAYLOAD_PORTALS} | {"census"} <= set(cells)
    lost = cells["bezrealitky/payload"]
    assert (lost["status"], lost["value"], lost["facts"]) == (
        "fail", 20.0, {"ads": 40, "unreadable": 8, "window_h": q._PAYLOAD_WINDOW_H})
    assert PAYLOAD_SHAPE_REMEDIES["bezrealitky"] in lost["message"]
    assert (cells["sreality/payload"]["status"], cells["sreality/payload"]["facts"]["unreadable"]) == ("ok", 0)
    assert cells["mmreality/payload"]["status"] == "unknown"
    assert (cells["bezrealitky/churn"]["status"], cells["idnes/churn"]["status"]) == ("ok", "unknown")
    measures, median = cells["bezrealitky/measures/byt-prodej"], cells["bezrealitky/median/byt-prodej"]
    assert (measures["status"], measures["facts"]["ads_7d"], measures["facts"]["ads"]) == ("unknown", 45, 45)
    assert median["status"] == "unknown" and "0.0 d of its own history" in median["message"]
    assert [c["subject"] for c in cells.values() if "trend" in c["facts"]] == []


# --- parsing: measures of new ads and the per-m² median ---------------------------------------------------------------


@live
def test_a_new_ads_measure_gap_is_judged_against_the_whole_stocks_through_the_measure_function(seed: _Seed) -> None:
    """The view's gap is (all − floor-eligible) / all: no price, a NULL or zero area; a sale price under the basis
    floor has its inputs, so it is a floor NULL and no gap. The cell's excess is LB(the new ads' gap) − the stock's
    gap: Watch past 10 pp, Problem past 25 pp; a standing gap cancels; under 50 new ads it is Not measured, and a
    category with no basis has no cell."""
    watch, fail, standing, few, no_basis = (seed.portal(name) for name in (
        "watch", "fail", "standing", "few", "no-basis"))
    seed.ads(watch, 180)
    seed.ads(watch, 10, price=None)
    seed.ads(watch, 5, area=None)                  # NULL area: a gap three-valued logic must not drop (migration 534)
    seed.ads(watch, 5, area=0.0)
    seed.ads(watch, 10, price=50_000)              # under the sale floor: a floor NULL, never an input gap
    seed.ads(watch, 30, seen_h=48)
    seed.ads(watch, 30, seen_h=48, price=None)
    seed.ads(fail, 200)
    seed.ads(fail, 60, seen_h=48, price=None)
    seed.ads(standing, 100)
    seed.ads(standing, 100, price=None)
    seed.ads(standing, 30, seen_h=48)
    seed.ads(standing, 30, seen_h=48, price=None)
    seed.ads(few, 49, seen_h=48, price=None)
    seed.ads(no_basis, 10, deal=None)

    rows = _view(seed.conn, watch, fail, standing, few, no_basis)
    row = next(r for r in rows if r["source"] == watch)
    assert (row["price_per_m2_basis"], row["n_active"], row["n_active_7d"], row["n_floor_eligible"]) == (
        "sale_capital_czk_m2", 270, 60, 220)
    assert row["measure_input_gap_share"] == pytest.approx(50 / 270)
    assert row["measure_input_gap_share_7d"] == pytest.approx(0.5)
    assert row["floor_null_share"] == pytest.approx(10 / 220)
    assert [(r["price_per_m2_basis"], r["measure_input_gap_share"]) for r in rows if r["source"] == no_basis] == [
        (None, None)]

    cells = _by_subject(q._measures_cells(rows, THRESHOLDS))

    assert set(cells) == {f"{s}{q._MEASURES_ARM}byt-prodej" for s in (watch, fail, standing, few)}
    regression = cells[f"{watch}{q._MEASURES_ARM}byt-prodej"]
    assert regression["status"] == "warn"
    assert regression["value"] == pytest.approx(round((wilson_lower(30, 60) - 50 / 270) * 100, 1))
    assert _picked(regression, "missing_7d", "ads_7d", "ads", "gap_7d", "gap", "lower_bound") == {
        "missing_7d": 30, "ads_7d": 60, "ads": 270, "gap_7d": 0.5, "gap": round(50 / 270, 4),
        "lower_bound": round(wilson_lower(30, 60), 4)}
    assert cells[f"{fail}{q._MEASURES_ARM}byt-prodej"]["status"] == "fail"
    level = cells[f"{standing}{q._MEASURES_ARM}byt-prodej"]
    assert level["status"] == "ok" and level["value"] < 0 and "pp below" in level["message"]
    assert cells[f"{few}{q._MEASURES_ARM}byt-prodej"]["message"] == (
        "Not measured: 49 ads first seen in 7 d, under the 50 this arm needs")


@live
def test_the_median_baseline_is_this_checks_own_median_cells_six_to_fourteen_days_old(seed: _Seed) -> None:
    """_MEDIAN_BASELINE_SQL: the check's first result dates its history; per median cell, the newest reading strictly
    inside 6-14 d, whatever the result's status, a median that is no number skipped; cells stored as an object (the
    retired check's shape), another arm's cell and another key's rows are never read."""
    assert q._median_baseline(seed.conn) == (None, {})
    p = seed.portal("baseline")
    sale, rent, house, land, office, edge = (f"{p}{q._MEDIAN_ARM}{grain}" for grain in (
        "byt-prodej", "dum-pronajem", "dum-prodej", "pozemek-prodej", "komercni-prodej", "byt-pronajem"))

    def median(subject: str, value: Any, *, basis: str = "sale_capital_czk_m2", n: Any = 300) -> dict[str, Any]:
        return {"subject": subject, "status": "ok", "facts": {"median": value, "price_per_m2_basis": basis, "n": n}}

    young, old = q._MEDIAN_WINDOW_D
    seed.result(q._PARSING, days=20)
    seed.result(q._PARSING, days=13, status="warn", cells=[
        median(sale, 1000.0), {"subject": f"{p}{q._MEASURES_ARM}byt-prodej", "facts": {"median": 5}}])
    seed.result(q._PARSING, days=8, status="fail", cells=[
        median(sale, 1100.0, n=310), median(house, None), median(land, "big")])
    seed.result(q._PARSING, days=9, status="unknown", cells=[
        median(rent, 200.5, basis="rent_monthly_czk_m2", n="12")])
    seed.result(q._PARSING, days=5, cells=[median(sale, 9999.0)])
    seed.result(q._PARSING, days=15, cells=[median(office, 7.0)])
    seed.result(q._PARSING, days=7, details={"cells": {sale: {"median": 1.0, "n": 300}}})
    seed.result("ppm2_median_shift", days=7, cells=[median(office, 2.0)])
    seed.result(q._PARSING, exact_days=young, cells=[median(edge, 3.0)])
    seed.result(q._PARSING, exact_days=old, cells=[median(edge, 4.0)])

    first, then = q._median_baseline(seed.conn)

    assert first == seed.ago(days=20)
    assert then == {sale: (seed.ago(days=8), 1100.0, "sale_capital_czk_m2", 310),
                    rent: (seed.ago(days=9), 200.5, "rent_monthly_czk_m2", 0)}


@live
def test_the_median_shift_compares_the_stocks_median_with_its_own_reading_a_week_before(seed: _Seed) -> None:
    """_median_cells over the view and the stored baseline: 210 flats at 100,000 Kč/m² against the 40,000 this check
    read 7 d ago is 2.5x, Watch, in the basis's unit; a cell with no reading 6-14 d ago is Not measured."""
    shifted = seed.portal("shift")
    seed.ads(shifted, 210, price=7_000_000, area=70.0)
    seed.ads(shifted, 210, main="dum", price=10_000_000, area=100.0)
    subject = f"{shifted}{q._MEDIAN_ARM}byt-prodej"
    seed.result(q._PARSING, days=10)
    seed.result(q._PARSING, days=7, cells=[{"subject": subject, "status": "ok", "facts": {
        "median": 40_000.0, "price_per_m2_basis": "sale_capital_czk_m2", "n": 250}}])

    cells = _by_subject(q._median_cells(seed.conn, _view(seed.conn, shifted), THRESHOLDS, seed.now))

    shift = cells[subject]
    assert (shift["status"], shift["value"], shift["unit"]) == ("warn", 2.5, "×")
    assert shift["facts"] == {"median": 100_000.0, "price_per_m2_basis": "sale_capital_czk_m2", "n": 210,
                              "ads": 210, "median_then": 40_000.0, "n_then": 250,
                              "baseline_at": iso(seed.ago(days=7)), "ratio": 2.5}
    unit = unit_label("sale_capital_czk_m2")
    assert f"median 100,000 {unit} against 40,000 {unit} 7.0 d ago" in shift["message"]
    assert cells[f"{shifted}{q._MEDIAN_ARM}dum-prodej"]["message"] == (
        "Not measured: no reading of this median 6-14 d ago")


# --- parsing: churn and the field matrix ------------------------------------------------------------------------------


@live
def test_churn_counts_second_and_later_snapshots_per_portal_and_day_beside_its_active_ads(seed: _Seed) -> None:
    """_CHURN_SQL: whole 24-h windows back from now, 29 of them (the 29th day's own instant out); an ad's first snapshot
    never counts and an inactive ad's later ones do; a portal with active ads and no snapshot keeps its row, one with
    no active ad has none."""
    churned, quiet, gone = seed.portal("churned"), seed.portal("quiet"), seed.portal("gone")
    first, single, edged = (seed.ads(churned)[0] for _ in range(3))
    delisted = seed.ads(churned, active=False)[0]
    seed.snapshots(first, [40 * _D, 25 * _H, 2 * _H])
    seed.snapshots(single, [3 * _H])
    seed.snapshots(delisted, [10 * _D, 5 * _D + _H])
    seed.snapshots(edged, [35 * _D, 28 * _D + _H])
    seed.snapshot_days(edged, q._CHURN_DAYS + 1)
    seed.ads(quiet)
    seed.snapshots(seed.ads(gone, active=False)[0], [3 * _D, _D + _H])

    rows = seed.conn.execute(q._CHURN_SQL, {"days": q._CHURN_DAYS}).fetchall()

    assert {row for row in rows if row[0] in (churned, quiet, gone)} == {
        (churned, 3, 0, 1), (churned, 3, 1, 1), (churned, 3, 5, 1), (churned, 3, 28, 1), (quiet, 1, None, None)}


@live
def test_a_churn_storm_is_judged_against_the_portals_own_28_day_median_day(seed: _Seed) -> None:
    """idnes re-snapshots 650 times in 24 h against a median day of 100: 6.5x, Problem past 6x. remax's 120 against a
    median of 1 is 120x but under the 500 a storm needs: OK. A portal with no active ad is Not measured."""
    for portal, per_day, today in (("idnes", 100, 650), ("remax", 1, 120)):
        ages = [40 * _D]
        ages += [day * _D + _H + i for day in range(1, q._CHURN_DAYS + 1) for i in range(per_day)]
        ages += [60.0 + i for i in range(today)]
        seed.snapshots(seed.ads(portal)[0], ages)

    cells = _by_subject(q._churn_cells(seed.conn, THRESHOLDS))

    assert set(cells) == {f"{p}/churn" for p in SOURCES}
    storm = cells["idnes/churn"]
    assert (storm["status"], storm["value"]) == ("fail", 6.5)
    assert storm["facts"] == {"resnaps_24h": 650, "ads": 1, "ratio": 650.0, "median_resnaps_28d": 100.0,
                              "median_ratio_28d": 100.0, "multiple": 6.5}
    calm = cells["remax/churn"]
    assert (calm["status"], calm["facts"]["multiple"]) == ("ok", 120.0)
    assert "under the 500 a storm needs" in calm["message"]
    assert cells["bazos/churn"]["status"] == "unknown"


@live
def test_the_field_matrix_reads_the_active_ads_of_enabled_scraper_portals_only(seed: _Seed) -> None:
    """FIELD_MATRIX_SQL on the clock, as the parsing check sends it: a portal is measured only while it is an enabled
    scraper in `portals`; fill counts every active ad, a boolean field splits true and false, a canon field names its
    spellings off the canon; an inactive ad counts nowhere."""
    measured, disabled, parser, unlisted = (seed.portal(name) for name in ("scraper", "disabled", "parser", "none"))
    for source, kind, enabled in ((measured, "scraper", True), (disabled, "scraper", False), (parser, "parser", True)):
        seed.conn.execute("insert into portals (source, label, kind, is_enabled) values (%s, %s, %s, %s)",
                          (source, source, kind, enabled))
    for price, lift, disposition, active in ((5_000_000, True, "2+kk", True), (4_000_000, True, "2+kk", True),
                                             (3_000_000, False, "garsoniera", True), (None, None, None, True),
                                             (1, True, "x", False)):
        seed.conn.execute("insert into listings (source, source_id_native, raw_json, price_czk, has_lift, "
                          "disposition, is_active) values (%s, %s, '{}'::jsonb, %s, %s, %s, %s)",
                          (measured, f"w6-{uuid.uuid4().hex}", price, lift, disposition, active))
    for source in (disabled, parser, unlisted):
        seed.ads(source)

    cells = field_census.reduce_matrix(fetchall(seed.conn, field_census.FIELD_MATRIX_SQL),
                                       generated_at=seed.now)["cells"]

    assert {key.split("/", 1)[0] for key in cells} & {disabled, parser, unlisted} == set()
    assert {key.split("/", 1)[1] for key in cells if key.startswith(f"{measured}/")} == set(
        field_census.ATTRIBUTE_FIELDS)
    assert cells[f"{measured}/price_czk"] == {"n": 4, "filled": 3, "fill": 0.75}
    assert cells[f"{measured}/has_lift"] == {"n": 4, "filled": 3, "fill": 0.75, "true": 2, "false": 1}
    assert cells[f"{measured}/disposition"] == {"n": 4, "filled": 3, "fill": 0.75, "distinct": 2,
                                                "off_canon": 0.3333, "off_canon_values": ["garsoniera"],
                                                "values": {"2+kk": 2}}


# --- data_quality ----------------------------------------------------------------------------------------------------


@live
def test_data_quality_reads_the_ship_time_red_off_the_view_and_skips_land(seed: _Seed) -> None:
    """The whole gauge: realitymix komerční pronájem with 472 of 4,140 rents under the floor (the 10-19 figure, LB
    10.5 %) is Problem; bazos's flats with areas 50 % apart are Problem and its land, whose headline is the plot, is
    skipped; mmreality houses carrying area + usable as their estate area are Problem on the estate sum alone; remax's
    60 ads with no link are Watch (an inactive one out, the 7-d ones apart); sreality land with no area is a 100 %
    measure gap, Watch and never Problem; every portal gets its per-portal cells and every cell its trend."""
    seed.ads("realitymix", 3_668, main="komercni", deal="pronajem", price=30_000, area=100.0)
    seed.ads("realitymix", 472, main="komercni", deal="pronajem", price=500, area=100.0)
    seed.ads("bazos", 120, area=100.0, usable=50.0)
    seed.ads("bazos", 150, main="pozemek", area=1_000.0, usable=10.0)
    seed.ads("mmreality", 110, main="dum", area=100.0, usable=95.0, estate=195.0)
    seed.ads("remax", 40)
    seed.ads("remax", 50, url=None)
    seed.ads("remax", 10, url=None, seen_h=48)
    seed.ads("remax", 5, url=None, active=False)
    seed.ads("sreality", 210, main="pozemek", price=2_000_000, area=None)

    out = q.check_data_quality(seed.conn, THRESHOLDS)
    cells = _whole(out, q._DATA_QUALITY)

    assert out["status"] == "fail" and "not_measured" not in out["details"]
    floor = cells["floor/realitymix/komercni-pronajem"]
    assert (floor["status"], floor["value"]) == ("fail", 11.4)
    assert _picked(floor, "priced", "priced_7d", "worst", "share", "lower_bound") == {
        "priced": 4_140, "priced_7d": 0, "worst": "stock", "share": round(472 / 4_140, 4),
        "lower_bound": round(wilson_lower(472, 4_140), 4)}
    assert floor["facts"]["lower_bound"] > THRESHOLDS["data_quality_floor_fail"]
    flats = cells["divergence/bazos"]
    assert (flats["status"], flats["facts"]["offenders"], flats["facts"]["measured"]) == ("fail", ["byt-prodej"], 1)
    assert (cells["divergence/mmreality"]["status"], cells["estate_sum/mmreality"]["status"]) == ("ok", "fail")
    links = cells["url/remax"]
    assert (links["status"], links["value"]) == ("warn", 60)
    assert _picked(links, "active", "no_url", "no_url_7d") == {"active": 100, "no_url": 60, "no_url_7d": 10}
    assert (cells["url/bazos"]["status"], cells["url/bazos"]["value"]) == ("ok", 0)
    assert cells["url/idnes"]["status"] == "unknown"
    gap = cells["measure_gap/sreality/pozemek-prodej"]
    assert (gap["status"], gap["value"], gap["facts"]["active"]) == ("warn", 100.0, 210)
    assert cells["floor/sreality/pozemek-prodej"]["status"] == "unknown"
    assert {f"{family}/{p}" for family in ("divergence", "estate_sum", "url") for p in SOURCES} <= set(cells)
    assert all({"trend", "worse_7d"} <= set(c["facts"]) for c in cells.values())


# --- location_town_coverage ------------------------------------------------------------------------------------------


@live
def test_location_counts_active_ads_past_the_edge_with_no_town_and_spares_a_degenerate_ku(seed: _Seed) -> None:
    """The whole gauge, a cell per portal and the registry's: an active ad first written more than NEW_AD_H hours ago
    with no row, or a Czech or undetermined row with no town, is Problem (a foreign one never; inactive and new ads are
    not read); hidden is a fact. A Czech address-grain row with no KÚ is a Problem out of the value, spared only in an
    obec holding a KÚ the boundary load found degenerate at the CURRENT version, and only at this resolver version. A
    Czech address-grain row with no town counts once, as no town, whatever the degenerate set holds (empty, NULL NOT IN
    it would be true)."""
    seed.no_registry()
    current, old = seed.version("current", age_d=19, current=True), seed.version("old", age_d=60)
    seed.unit("katastralni_uzemi", _KU, current, parent=seed.unit("obec", _TOWN, current))
    seed.unit("katastralni_uzemi", _DEGENERATE_KU, current, parent=seed.unit("obec", _DEGENERATE_TOWN, current))
    seed.discrepancy(current, _DEGENERATE_KU, "degenerate_boundary_geometry")
    seed.discrepancy(current, _KU, "boundary_load_failed")                 # never an excuse
    seed.discrepancy(old, _KU, "degenerate_boundary_geometry")             # another version's: no excuse
    no_row, czech, undetermined, abroad, towned = (seed.ads("ceskereality", seen_h=48)[0] for _ in range(5))
    seed.located(czech, granularity="kraj", point=False)
    seed.located(undetermined, country="undetermined", granularity="unknown", point=False)
    seed.located(abroad, country="foreign", granularity="country", point=False)
    seed.located(towned, obec=_TOWN)
    seed.ads("ceskereality", seen_h=48, active=False)
    seed.ads("ceskereality", seen_h=2)
    no_ku, spared, older, with_ku, street = (seed.ads("realitymix", seen_h=48)[0] for _ in range(5))
    seed.located(no_ku, granularity="building", obec=_TOWN)
    seed.located(spared, granularity="building", obec=_DEGENERATE_TOWN)
    seed.located(older, granularity="building", obec=_TOWN, resolver="w6-older")
    seed.located(with_ku, granularity="address_point", obec=_TOWN, katastr=_KU)
    seed.located(street, granularity="street", obec=_TOWN)
    townless = seed.ads("bezrealitky", seen_h=48)[0]
    seed.located(townless, granularity="building")                       # Czech, address grain, no town, no KÚ

    out = q.check_location_town_coverage(seed.conn, THRESHOLDS)
    cells = _whole(out, q._LOCATION)

    assert list(cells) == [f"{p}/town" for p in sorted(SOURCES)] + ["registry"] and out["status"] == "fail"
    counts = ("ads", "no_row", "cz_no_town", "town", "hidden", "address_no_ku", "town_share")
    missing = cells["ceskereality/town"]
    assert (missing["status"], missing["value"]) == ("fail", 3)
    assert _picked(missing, *counts) == {"ads": 5, "no_row": 1, "cz_no_town": 2, "town": 1, "hidden": 3,
                                         "address_no_ku": 0, "town_share": 0.2}
    assert f"3 of 5 active ads first written more than {NEW_AD_H} h ago have no town" in missing["message"]
    no_cadastre = cells["realitymix/town"]
    assert (no_cadastre["status"], no_cadastre["value"]) == ("fail", 0)
    assert _picked(no_cadastre, *counts) == {"ads": 5, "no_row": 0, "cz_no_town": 0, "town": 5, "hidden": 0,
                                             "address_no_ku": 1, "town_share": 1.0}
    assert "have no cadastral area (KÚ)" in no_cadastre["message"]
    no_town = cells["bezrealitky/town"]
    assert (no_town["status"], no_town["value"]) == ("fail", 1)
    assert _picked(no_town, *counts) == {"ads": 1, "no_row": 0, "cz_no_town": 1, "town": 0, "hidden": 0,
                                         "address_no_ku": 0, "town_share": 0.0}
    assert "cadastral area" not in no_town["message"]
    assert cells["idnes/town"]["status"] == "unknown"
    registry = cells["registry"]
    assert (registry["status"], registry["value"], registry["facts"]["current"]) == ("ok", 19, seed.label("current"))

    # The degenerate set emptied: the spared obec loses its excuse, and the town-less row still counts once.
    seed.conn.execute("update registry_load_discrepancies set discrepancy = 'boundary_load_failed' "
                      "where registry_version_id = %s and entity_code = %s", (current, _DEGENERATE_KU))
    read = {row[0]: tuple(row[1:]) for row in seed.conn.execute(q._LOCATION_TOWN_COVERAGE_SQL, {
        "resolver_version": RESOLVER_VERSION, "new_ad_h": NEW_AD_H}).fetchall()}
    assert read["bezrealitky"] == (1, 0, 1, 0, 0, 0)          # ads, no row, no town, town, hidden, no KÚ
    assert read["realitymix"][-1] == 2                         # no_ku and spared


@live
def test_the_location_gauge_starts_exactly_where_publishings_visible_day_ends(seed: _Seed) -> None:
    """One number, NEW_AD_H: publishing's visible day holds an active ad first written at most NEW_AD_H hours ago (past
    its 30-min grace) and _LOCATION_TOWN_COVERAGE_SQL every one first written before that, so the edge itself is
    publishing's and every ad past the grace sits in exactly one of the two."""
    edge = seed.portal("edge")
    for hours in (0.25, NEW_AD_H - 1 / 60, NEW_AD_H, NEW_AD_H + 1 / 60, 73.0):
        seed.ads(edge, seen_h=hours)

    visible = {(recent, stage): ads for _, recent, stage, ads in seed.conn.execute(pub._VISIBLE_SQL, {
        "sources": [edge], "window_h": pub._WINDOW_H, "wide_h": pub._WIDE_WINDOW_H,
        "grace_min": pub._VISIBLE_GRACE_MIN}).fetchall()}
    gauge = {row[0]: row[1] for row in seed.conn.execute(q._LOCATION_TOWN_COVERAGE_SQL, {
        "resolver_version": RESOLVER_VERSION, "new_ad_h": NEW_AD_H}).fetchall()}

    assert pub._WINDOW_H == NEW_AD_H
    assert visible == {(True, "no_property"): 2, (False, "no_property"): 1}    # the day; the 72-h widening
    assert gauge[edge] == 2


@live
def test_the_registry_cell_reads_the_current_versions_age_and_a_newer_one_left_staged(seed: _Seed) -> None:
    """_REGISTRY_LIVENESS_SQL: no current version is Problem; the current one's age in whole days against
    location_registry_max_age_days; the first loaded of the versions newer than it and not current against
    location_registry_max_staged_hours; an older version left staged is history."""
    seed.no_registry()
    missing = q._registry_cell(seed.conn, THRESHOLDS)
    assert (missing["status"], missing["facts"]) == ("fail", {"current": None})

    current = seed.version("current", age_d=19, current=True, loaded_h=19 * 24)
    seed.version("superseded", age_d=50, loaded_h=100)
    fresh = q._registry_cell(seed.conn, THRESHOLDS)
    assert (fresh["status"], fresh["value"], fresh["facts"]) == ("ok", 19, {
        "current": seed.label("current"), "age_days": 19, "staged": None, "staged_hours": None})

    seed.conn.execute("update registry_versions set source_date = current_date - 41 where id = %s", (current,))
    aged = q._registry_cell(seed.conn, THRESHOLDS)
    assert aged["status"] == "fail" and "is 41 d old (Problem past 40 d)" in aged["message"]

    seed.conn.execute("update registry_versions set source_date = current_date - 19 where id = %s", (current,))
    seed.version("stalled", age_d=1, loaded_h=30)
    seed.version("later", age_d=0, loaded_h=2)
    stalled = q._registry_cell(seed.conn, THRESHOLDS)
    assert (stalled["status"], stalled["facts"]["staged"], stalled["facts"]["staged_hours"]) == (
        "fail", seed.label("stalled"), 30.0)
    assert "has sat staged 30 h without becoming current (Problem past 24 h)" in stalled["message"]


# --- text_extraction -------------------------------------------------------------------------------------------------


@live
def test_the_description_lane_is_read_through_its_own_scope_flow_and_stock(seed: _Seed) -> None:
    """The scope is LLM_TEXT_SOURCES_SQL (an active contract's llm_text entry) plus the open gates, in every statement.
    The flow: in-scope active ads with a text first written 1-24 h ago, read at any version of the current
    SCHEMA_VERSION, given up after GIVE_UP_AFTER failures at this version, p95 minutes to the first reading. The stock:
    the lane's one predicate (an active ad not read at this version and text, an inactive one once per SCHEMA_VERSION)
    beside every reading of the last 24 h. With no model the lane reads off: OK, every subject still emitted."""
    seed.model(_MODEL)
    version = text_lane.extractor_version(_MODEL)
    same_schema, old_schema = f"{text_lane.SCHEMA_VERSION}:w6prompt:{_MODEL}", f"0:w6prompt:{_MODEL}"
    scoped, retired, regex, unscoped = (seed.portal(name) for name in ("text", "retired", "regex", "unscoped"))
    seed.contract(scoped)
    seed.contract(retired, active=False)
    seed.contract(regex, method="regex_text")
    in_scope = [row[0] for row in seed.conn.execute(text_lane.LLM_TEXT_SOURCES_SQL).fetchall()]
    assert scoped in in_scope and retired not in in_scope and regex not in in_scope

    def ad(hours: float, *, source: str = scoped, active: bool = True,
           description: str | None = "Byt s výtahem a balkonem") -> int:
        return seed.ads(source, seen_h=hours, active=active, raw={"title": "Byt 2+kk"}, description=description)[0]

    read, read_elsewhere, read_long_ago, given_up, retrying, retried = (ad(h) for h in (2, 3, 4, 5, 6, 7))
    ad(0.5)                                                  # inside the lane's 1-h grace: waits, not yet a flow
    ad(25)                                                   # past the day: waits, out of the flow
    ad(2, description=None)
    ad(2, description="")
    ad(2, active=False)                                      # delisted, never read: the archive's re-read
    edited = ad(72)
    delisted_read, delisted_long_ago = ad(72, active=False), ad(72, active=False)
    for source in (retired, regex, unscoped):
        ad(2, source=source)
    seed.reading(read, version, after_min=10)
    seed.reading(read_elsewhere, same_schema, after_min=30)
    seed.reading(read_long_ago, old_schema, hours_ago=3)
    seed.reading(given_up, version, error=True, attempts=text_lane.GIVE_UP_AFTER)
    seed.reading(retrying, version, error=True, attempts=2)
    seed.reading(retried, version, error=True)
    seed.reading(edited, version, hours_ago=50, stale=True)
    seed.reading(delisted_read, same_schema, hours_ago=60)
    seed.reading(delisted_long_ago, old_schema, hours_ago=60)

    grace_h, window_h = q._FLOW_WINDOW_H
    flows = {row[0]: row[1:] for row in seed.conn.execute(text_lane.EXTRACTION_LATENCY_SQL, {
        "version": version, "window_h": window_h, "grace_h": grace_h}).fetchall()}
    assert list(flows) == [scoped] and flows[scoped][:3] == (6, 2, 1)
    assert flows[scoped][3] == pytest.approx(29.0)            # the p95 of 10 and 30 minutes
    assert seed.conn.execute(text_lane.ELIGIBLE_LAG_SQL, {"version": version}).fetchone() == (9, 7, 3)

    # Six arrivals sit under text_extraction_flow_min_n (30): Not measured on the harness's own lines, its counts kept as
    # facts. The verdict is read with the minimum lowered to the seeds.
    gated = _whole(q.check_text_extraction(seed.conn, THRESHOLDS), q._TEXT)[f"flow/{scoped}"]
    assert THRESHOLDS["text_extraction_flow_min_n"] > 6 and (gated["status"], gated["value"]) == ("unknown", None)
    assert _picked(gated, "arrived", "read", "unread", "given_up") == {"arrived": 6, "read": 2, "unread": 4,
                                                                       "given_up": 1}
    cells = _whole(q.check_text_extraction(seed.conn, {**THRESHOLDS, "text_extraction_flow_min_n": 6}), q._TEXT)

    assert {f"flow/{scoped}", "flow/bazos"} <= set(cells) and list(cells)[-1] == "stock"
    flow = cells[f"flow/{scoped}"]
    assert (flow["status"], flow["value"]) == ("fail", round(100 * 4 / 6, 2))
    assert _picked(flow, "arrived", "read", "unread", "given_up", "p95_minutes", "lower_bound") == {
        "arrived": 6, "read": 2, "unread": 4, "given_up": 1, "p95_minutes": 29.0,
        "lower_bound": round(wilson_lower(4, 6), 4)}
    assert cells["flow/bazos"]["status"] == "unknown"
    stock = cells["stock"]
    assert (stock["status"], stock["value"]) == ("ok", 3.0)
    assert _picked(stock, "model", "extractor_version", "waiting", "waiting_active", "waiting_inactive",
                   "readings_24h", "eta_d") == {
        "model": _MODEL, "extractor_version": version, "waiting": 9, "waiting_active": 7, "waiting_inactive": 2,
        "readings_24h": 3, "eta_d": 3.0}

    seed.conn.execute("update listing_description_enrichments set created_at = now() - interval '30 hours' "
                      "where listing_id = any(%s::bigint[])", ([read, read_elsewhere, read_long_ago],))
    stalled = seed.conn.execute(text_lane.ELIGIBLE_LAG_SQL, {"version": version}).fetchone()
    assert stalled == (9, 7, 0) and q._stock_cell(stalled, _MODEL, version, THRESHOLDS)["status"] == "fail"

    seed.model(None)
    off = _whole(q.check_text_extraction(seed.conn, THRESHOLDS), q._TEXT)
    assert {f"flow/{scoped}", "flow/bazos", "stock"} <= set(off)
    assert {(c["status"], c["facts"]["state"]) for c in off.values()} == {("ok", "off")}


# --- gauge trends ----------------------------------------------------------------------------------------------------


@live
def test_a_gauge_cell_carries_its_series_on_from_its_newest_measured_result(seed: _Seed) -> None:
    """common.trend through the whole location gauge: ceskereality's series comes from the newest MEASURED result (the
    unknown run after it resets nothing) and gains today's Problem, worse than its point a week back; bazos, absent
    from that result, keeps the series an older measured one holds; the registry, in none, starts with today's."""
    seed.no_registry()
    seed.version("current", age_d=19, current=True)
    day = seed.now.astimezone(_PRAGUE).date()

    def point(days_back: int, value: int, status: str) -> list[Any]:
        return [(day - _dt.timedelta(days=days_back)).isoformat(), value, status]

    seed.result(q._LOCATION, days=3, cells=[
        {"subject": "bazos/town", "status": "ok", "facts": {"trend": [point(3, 0, "ok")]}}])
    seed.result(q._LOCATION, days=1, cells=[
        {"subject": "ceskereality/town", "status": "ok", "facts": {"trend": [point(7, 0, "ok"), point(1, 0, "ok")]}}])
    seed.result(q._LOCATION, days=0.5, status="unknown", cells=[
        {"subject": "ceskereality/town", "status": "unknown", "facts": {"trend": []}}])
    seed.ads("ceskereality", seen_h=48)

    cells = _whole(q.check_location_town_coverage(seed.conn, THRESHOLDS), q._LOCATION)

    def series(subject: str) -> tuple[list[Any], bool | None]:
        return cells[subject]["facts"]["trend"], cells[subject]["facts"]["worse_7d"]

    assert series("ceskereality/town") == ([point(7, 0, "ok"), point(1, 0, "ok"), point(0, 1, "fail")], True)
    assert series("bazos/town") == ([point(3, 0, "ok")], None)
    assert series("registry") == ([point(0, 19, "ok")], None)
