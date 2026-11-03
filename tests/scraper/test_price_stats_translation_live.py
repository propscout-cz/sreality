"""Location W8-2 (#2362), executed: the price-stats fetch reads its towns and keys from `ruian_translation`, each run
confirms or adds keys from active sreality ads (a stored row stands, an ad never moves it), and observations, no-data
markers and metrics are written and read by RÚIAN town code (migrations 722 and 723). Runs in CI's migrations job
(`TEST_DATABASE_URL`); every test rolls back.
"""

from __future__ import annotations

import datetime as dt
import logging
import uuid
from typing import Any

import pytest

from scraper import price_stats_db as db
from scraper import price_stats_main as pm
from tests._live_property import REQUIRED_DB, _advert, _property
from toolkit import price_stats as analysis

pytestmark = REQUIRED_DB

OLD = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
ALFA, BETA, GAMA, DELTA, EPS = 900_001, 900_002, 900_003, 900_004, 900_005


def _towns(cur: Any, **names: int) -> None:
    """Live mirror obce, one registry version."""
    cur.execute("INSERT INTO registry_versions (label, kind, source_date, artifact_urls, proj_version, proj_pipeline) "
                "VALUES (%s, 'baseline', '2026-09-01', '{}', 'test', 'test') RETURNING id", (uuid.uuid4().hex,))
    version = cur.fetchone()[0]
    for name, code in names.items():
        cur.execute("INSERT INTO ruian_admin_units (level, code, name, name_norm, path, valid_from, first_version_id, "
                    "last_version_id) VALUES ('obec', %s, %s, lower(%s), %s::ltree, '2020-01-01', %s, %s)",
                    (code, name, name, f"obec_{code}", version, version))


def _key(cur: Any, key: str, town: int, matched_by: str, confirmed: dt.datetime = OLD,
         source: str = "sreality_municipality") -> None:
    cur.execute("INSERT INTO ruian_translation VALUES (%s, %s, 'obec', %s, %s, %s)",
                (source, key, town, matched_by, confirmed))


def _row(cur: Any, key: str) -> tuple[Any, ...] | None:
    cur.execute("SELECT ruian_code, matched_by, last_confirmed_at FROM ruian_translation "
                "WHERE source = 'sreality_municipality' AND source_key = %s", (key,))
    return cur.fetchone()


def _ad(cur: Any, key: str, town: int, *, source: str = "sreality", active: bool = True) -> None:
    """An ad carrying sreality's municipality id in its payload, bound to `town`."""
    listing = _advert(cur, _property(cur), source=source, active=active)
    cur.execute("UPDATE listings SET raw_json = jsonb_build_object('locality', jsonb_build_object("
                "'municipality_id', %s::text)) WHERE id = %s", (key, listing))
    cur.execute(
        "INSERT INTO listing_location (listing_id, geom, match_confidence, granularity, uncertainty_radius_m, "
        "  country_status, resolver_version, claim_set_hash, registry_version, country_code, obec_kod) "
        "VALUES (%s, ST_SetSRID(ST_MakePoint(14.4, 50.1), 4326), 'exact', 'address_point', 10, 'cz', 'test', "
        "  '\\x00'::bytea, 'test', 'CZ', %s)", (listing, town))


def _dataset(cur: Any) -> int:
    cur.execute("INSERT INTO price_stat_datasets (slug, name) VALUES (%s, 'test') RETURNING id",
                (f"t-{uuid.uuid4().hex[:8]}",))
    return int(cur.fetchone()[0])


def test_a_town_is_fetched_under_one_key_and_an_ad_matched_key_only_when_it_is_the_towns_only_row(cur: Any) -> None:
    _towns(cur, Alfa=ALFA, Beta=BETA, Gama=GAMA)
    _key(cur, "501", ALFA, "boundary_spatial_join")
    _key(cur, "9001", ALFA, "ad_bound_obec")          # a second key, matched from ads: a record, never fetched
    _key(cur, "9002", BETA, "ad_bound_obec")          # the town's only row: fetched
    _key(cur, "700", GAMA, "suggest_pip")
    _key(cur, "80", GAMA, "suggest_pip")              # two stored keys: the smaller number, not the smaller text
    _key(cur, "503", DELTA, "suggest_pip")            # no mirror unit: still fetched
    _key(cur, "x1", EPS, "suggest_pip", source="other_source")
    did = _dataset(cur)
    towns = [ALFA, BETA, GAMA, DELTA, EPS]

    rows = db.localities_ordered(cur.connection, did, towns)
    assert {(r["obec_id"], r["source_key"]) for r in rows} == {
        (ALFA, "501"), (BETA, "9002"), (GAMA, "80"), (DELTA, "503")}
    assert {r["obec_id"]: r["name"] for r in rows}[ALFA] == "Alfa", "the name is the mirror's"
    assert {(ALFA, "501"), (BETA, "9002")} <= {(r["obec_id"], r["source_key"])
                                              for r in db.localities_ordered(cur.connection, did, None)}

    db.upsert_observations(cur.connection, dataset_id=did, obec_id=ALFA, category_type_cb=1, run_id=None,
                           months=[{"year": 2026, "month": 5, "price": 90_000}])
    db.record_no_data(cur.connection, did, BETA)
    rows = db.localities_ordered(cur.connection, did, towns)
    assert [r["obec_id"] for r in rows][-1] == ALFA, "a fetched town goes to the back of the queue"
    assert BETA not in {r["obec_id"] for r in rows}, "a fresh no-data marker skips its town"
    assert db.coverage_remaining(cur.connection, did, towns) == 2, "Gama and Delta were never fetched"


def test_ads_confirm_or_add_keys_and_never_move_a_stored_row(cur: Any, caplog: pytest.LogCaptureFixture) -> None:
    _towns(cur, Alfa=ALFA, Beta=BETA)
    _key(cur, "501", ALFA, "boundary_spatial_join")
    _key(cur, "600", GAMA, "suggest_pip")
    for _ in range(2):
        _ad(cur, "501", ALFA)                       # agrees: confirms 501
    _ad(cur, "600", ALFA)                           # a different town: 600 stays Gama, unconfirmed
    _ad(cur, "7001", BETA)                          # a new id, one town: added
    _ad(cur, "7005", ALFA)                          # a new id for a town that has a key: added, never fetched
    _ad(cur, "7002", ALFA)                          # a new id, two towns: not added
    _ad(cur, "7002", BETA)
    _ad(cur, "7003", BETA, active=False)            # inactive: not read
    _ad(cur, "7004", BETA, source="idnes")          # another portal: not read
    _ad(cur, "Praha", BETA)                         # no usable id: not read

    with caplog.at_level(logging.INFO, logger=pm.LOG.name):
        pm.confirm_translation(cur.connection)

    cur.execute("SELECT now()")
    now = cur.fetchone()[0]
    assert _row(cur, "501") == (ALFA, "boundary_spatial_join", now)
    assert _row(cur, "600") == (GAMA, "suggest_pip", OLD), "an ad never moves or confirms a row it disagrees with"
    assert _row(cur, "7001") == (BETA, "ad_bound_obec", now)
    assert _row(cur, "7005") == (ALFA, "ad_bound_obec", now)
    assert _row(cur, "7002") is None, "an id whose ads name two towns waits until they name one"
    for key in ("7003", "7004"):
        assert _row(cur, key) is None
    lines = [(r.levelname, r.getMessage()) for r in caplog.records]
    assert ("INFO", "TRANSLATION confirmed=1 added=2") in lines
    assert ("INFO", f"TRANSLATION added key=7001 obec={BETA}") in lines
    assert ("INFO", f"TRANSLATION added key=7005 obec={ALFA}") in lines
    assert {line for line in lines if line[0] == "WARNING"} == {
        ("WARNING", f"TRANSLATION disagree key=600 stored={GAMA} ad={ALFA} ads=1"),
        ("WARNING", f"TRANSLATION disagree key=7002 stored=None ad={ALFA} ads=1"),
        ("WARNING", f"TRANSLATION disagree key=7002 stored=None ad={BETA} ads=1"),
    }
    did = _dataset(cur)
    assert {(r["obec_id"], r["source_key"]) for r in db.localities_ordered(cur.connection, did, [ALFA, BETA])} == {
        (ALFA, "501"), (BETA, "7001")}


def test_rows_are_written_and_read_by_town_code_and_named_by_the_mirror(cur: Any) -> None:
    _towns(cur, Alfa=ALFA, Beta=BETA)
    did = _dataset(cur)
    months = [{"year": 2020 + y, "month": 1, "price": 50_000 + 1_000 * y, "active_count": 30} for y in range(6)]
    for category, scale in ((1, 1), (2, 0.005)):
        db.upsert_observations(cur.connection, dataset_id=did, obec_id=ALFA, category_type_cb=category, run_id=None,
                               months=[{**m, "price": int(m["price"] * scale)} for m in months])
    db.upsert_observations(cur.connection, dataset_id=did, obec_id=ALFA, category_type_cb=1, run_id=None,
                           months=[{**months[-1], "price": 60_000}])
    db.record_no_data(cur.connection, did, BETA)
    db.record_no_data(cur.connection, did, ALFA)
    db.clear_no_data(cur.connection, did, ALFA)
    assert db.recompute_metrics(cur.connection, did) == 1

    cur.execute("SELECT count(*), max(price) FILTER (WHERE category_type_cb = 1), min(locality_name), min(obec_id) "
                "FROM price_stat_observations_public WHERE dataset_id = %s", (did,))
    assert cur.fetchone() == (12, 60_000, "Alfa", ALFA)
    cur.execute("SELECT obec_id, locality_name, sale_latest_price FROM price_stat_city_metrics_public "
                "WHERE dataset_id = %s", (did,))
    assert cur.fetchall() == [(ALFA, "Alfa", 60_000)]
    cur.execute("SELECT obec_id, locality_name FROM price_stat_no_data_public WHERE dataset_id = %s", (did,))
    assert cur.fetchall() == [(BETA, "Beta")]

    series = analysis.dataset_city_series(cur.connection, did, ALFA)
    assert (len(series["data"]["sale"]), len(series["data"]["rent"])) == (6, 6)
    assert analysis.dataset_summary(cur.connection, did)["data"]["cities"] == 1
