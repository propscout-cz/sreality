"""Tests for the MF rent-map parser + the reference-rent wrapper (toolkit.rent_map) + the
ingest's write sequence (api.rent_map.insert_revision).

Parser tests run against a committed fixture XLSX (hermetic, no network). The
measure itself is SQL (`mf_reference()`, migration 565) and is tested live in
tests/test_mf_reference.py; here only the wrapper's hand-off, on a fake connection.
The ingest runs live in tests/test_map_overlays_live.py; here its order and its
failure policy, on a fake connection.
"""

from __future__ import annotations

import contextlib
import logging
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from api import rent_map as api_rent_map
from api.rent_map import find_latest_xlsx_url
from scraper import db as scraper_db
from scripts import ingest_map_overlays
from toolkit.rent_map import (
    MF_ENGINE,
    ParsedRentMap,
    RentAdjustment,
    RentValue,
    compute_reference_rent,
    parse_rent_map_xlsx,
    source_date_from_filename,
)

FIXTURE = (
    Path(__file__).parent
    / "fixtures" / "rent_map" / "2026-05-15_Cenova-mapa.xlsx"
)


@pytest.fixture(scope="module")
def parsed():
    return parse_rent_map_xlsx(FIXTURE.read_bytes(), source_date=date(2026, 5, 15))


def test_parses_all_territories(parsed):
    assert len({v.ruian_code for v in parsed.values}) == 7630
    assert len(parsed.values) == 7630 * 4
    assert parsed.source_date == date(2026, 5, 15)


def test_level_split_matches_admin_boundaries(parsed):
    levels = {v.ruian_code: v.level for v in parsed.values}
    assert sum(1 for lv in levels.values() if lv == "ku") == 1582
    assert sum(1 for lv in levels.values() if lv == "obec") == 6048


def test_adjustment_tables(parsed):
    old = [a for a in parsed.adjustments if not a.is_novostavba]
    nov = [a for a in parsed.adjustments if a.is_novostavba]
    assert len(old) == 20  # 4 VK × 5 attributes
    assert len(nov) == 24  # 4 VK × 6 attributes (incl. other_material)
    adj = {(a.vk, a.is_novostavba, a.attribute): a.czk_per_m2
           for a in parsed.adjustments}
    assert adj[(3, False, "elevator")] == 47
    assert adj[(3, False, "balcony")] == 4
    assert adj[(3, False, "garage")] == 37
    assert adj[(1, True, "other_material")] == 26
    # other_material only exists for novostavba
    assert (1, False, "other_material") not in adj


def test_litomerice_worked_example(parsed):
    """The MF sheet's own worked example: older 3+1, 68 m², with
    výtah + balkon + garáž in Litoměřice → 291 Kč/m² → 19 788 Kč."""
    vk3 = [v for v in parsed.values
           if v.ku_name == "Litoměřice" and v.vk == 3]
    assert vk3 and vk3[0].ref_rent_per_m2 == 203
    adj = {(a.vk, a.is_novostavba, a.attribute): a.czk_per_m2
           for a in parsed.adjustments}
    per_m2 = (203 + adj[(3, False, "elevator")]
              + adj[(3, False, "balcony")] + adj[(3, False, "garage")])
    assert per_m2 == 291
    assert round(per_m2 * 68) == 19788


def test_source_date_from_filename():
    assert source_date_from_filename("2026-05-15_Cenova-mapa.xlsx") == date(2026, 5, 15)
    assert source_date_from_filename("no-date.xlsx") is None


def test_find_latest_xlsx_url_picks_newest_date():
    html = """
      <a href="/assets/attachments/2026-05-15_Cenova-mapa.xlsx">current</a>
      <a href="/assets/attachments/2026-02-15_Cenova-mapa.xlsx">hist</a>
      <a href="/assets/attachments/2025-11-15_Cenova-mapa.xlsx">hist</a>
    """
    assert find_latest_xlsx_url(html) == (
        "https://mf.gov.cz/assets/attachments/2026-05-15_Cenova-mapa.xlsx"
    )


def test_find_latest_xlsx_url_none_when_absent():
    assert find_latest_xlsx_url("<a href='/assets/x.pdf'>no</a>") is None


# --- compute_reference_rent: a thin hand-off to mf_reference() ---------------

_FACTS = {
    "category_main": "byt", "category_type": "prodej", "disposition": "3+1",
    "area_m2": 75.0, "price_czk": 5_100_000, "condition": "dobry",
    "has_balcony": True, "terrace": False, "furnished": "ne", "garage": None,
    "has_lift": True, "building_type": "cihla", "obec_kod": 586846,
    "katastr_kod": None, "country_status": "cz",
}

# mf_reference()'s declared parameter order (migration 565). The wrapper binds by name,
# so this pins that every name lands in its own positional slot of the call.
_SIGNATURE_ORDER = (
    "category_main", "category_type", "disposition", "area_m2", "price_czk", "condition",
    "has_balcony", "terrace", "furnished", "garage", "has_lift", "building_type",
    "obec_kod", "katastr_kod", "country_status",
)


class _FakeCursor:
    def __init__(self, conn):
        self._conn = conn

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        if self._conn.error is not None:
            raise self._conn.error
        self._conn.calls.append((sql, params))

    def fetchone(self):
        return self._conn.row


class _FakeConn:
    def __init__(self, row=None, error=None):
        self.row, self.error, self.calls = row, error, []

    def cursor(self):
        return _FakeCursor(self)


def test_the_wrapper_hands_every_fact_to_mf_reference_in_signature_order():
    detail = {"status": "ok", "monthly_rent_czk": 19125, "vk": 3}
    conn = _FakeConn(row=(detail,))
    out = compute_reference_rent(conn, **_FACTS)
    ((sql, params),) = conn.calls
    call = sql[sql.index("mf_reference("):]
    slots = [call.index(f"%({name})s") for name in _SIGNATURE_ORDER]
    assert slots == sorted(slots)
    assert params == _FACTS
    assert out == {**detail, "engine": MF_ENGINE}


def test_katastr_and_country_default_to_unknown():
    conn = _FakeConn(row=({"status": "location_unknown", "note": "x"},))
    facts = {k: v for k, v in _FACTS.items() if k not in ("katastr_kod", "country_status")}
    compute_reference_rent(conn, **facts)
    ((_sql, params),) = conn.calls
    assert params["katastr_kod"] is None and params["country_status"] is None


@pytest.mark.parametrize("row", [None, (None,)])
def test_a_non_flat_reads_none(row):
    assert compute_reference_rent(_FakeConn(row=row), **_FACTS) is None


def test_a_database_error_reads_none():
    assert compute_reference_rent(_FakeConn(error=RuntimeError("boom")), **_FACTS) is None


# --- insert_revision: the write, the map overlay, the cells ------------------------


class _IngestCursor:
    def __init__(self, conn):
        self._conn = conn
        self._row = None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        head = " ".join(sql.split()[:3])
        self._conn.log.append(head)
        self._row = self._conn.existing if head.startswith("select") else (self._conn.revision,)

    def fetchone(self):
        return self._row

    @contextlib.contextmanager
    def copy(self, sql):
        table = sql.split()[1]
        self._conn.log.append(f"copy {table}")
        yield SimpleNamespace(write_row=self._conn.copied.setdefault(table, []).append)


class _IngestConn:
    def __init__(self, *, existing=None, revision=7):
        self.existing, self.revision = existing, revision
        self.log, self.copied = [], {}

    def cursor(self):
        return _IngestCursor(self)

    @contextlib.contextmanager
    def transaction(self):
        self.log.append("BEGIN")
        try:
            yield
        except BaseException:
            self.log.append("ROLLBACK")
            raise
        self.log.append("COMMIT")


_PARSED = ParsedRentMap(
    values=[RentValue(990_001, "obec", "Kraj Test", None, "Obecov", 2, 250, 290, 3)],
    adjustments=[RentAdjustment(2, False, "balcony", 5)],
    source_date=date(2026, 8, 15),
)


def _insert(conn):
    return api_rent_map.insert_revision(
        conn, _PARSED, source_filename="2026-08-15_Cenova-mapa.xlsx", file_sha256="ab" * 32,
        source_date=date(2026, 8, 15), uploaded_by="test",
    )


@pytest.fixture()
def seams(monkeypatch):
    """Log the overlay render and every matview refresh into the fake connection's log."""
    def install(conn, render=lambda: 99):
        def fake_render(c, key):
            assert c is conn
            conn.log.append(f"render {key}")
            return render()

        monkeypatch.setattr(api_rent_map, "render_sql_section", fake_render)
        monkeypatch.setattr(scraper_db, "refresh_matview",
                            lambda c, name, **kw: conn.log.append(f"refresh {name}") or 1)
    return install


def test_a_new_revision_re_renders_the_map_overlay_inside_its_transaction(seams):
    """The overlay replaces the rent_map_choropleth refresh: rendered from the rows just
    copied, in the ingest's own transaction, and nothing refreshes the dropped matview."""
    conn = _IngestConn()
    seams(conn)
    assert _insert(conn) == 7
    assert conn.log == [
        "BEGIN", "select source_revision from", "insert into rent_map_revisions",
        "copy rent_map_values", "copy rent_map_adjustments",
        "render rent_map", "refresh rent_map_cells", "COMMIT",
    ]
    assert conn.copied["rent_map_values"] == [(7, 990_001, "obec", "Kraj Test", None, "Obecov",
                                               2, 250, 290, 3)]


def test_a_refused_overlay_keeps_the_revision_and_says_so(seams, caplog):
    """A guard refusal writes nothing and must not block the rent data mf_reference() reads."""
    conn = _IngestConn()

    def refuse():
        raise ingest_map_overlays.Refused("layer rent_map: 6000 features against 7630 before")

    seams(conn, render=refuse)
    with caplog.at_level(logging.ERROR, logger="rent_map"):
        assert _insert(conn) == 7
    assert conn.log[-3:] == ["render rent_map", "refresh rent_map_cells", "COMMIT"]
    assert "NOT re-rendered" in caplog.text and "6000 features against 7630" in caplog.text


def test_any_other_overlay_failure_rolls_the_ingest_back(seams):
    conn = _IngestConn()

    def crash():
        raise RuntimeError("render crashed")

    seams(conn, render=crash)
    with pytest.raises(RuntimeError, match="render crashed"):
        _insert(conn)
    assert conn.log[-1] == "ROLLBACK" and "refresh rent_map_cells" not in conn.log


def test_an_unchanged_file_re_renders_nothing(seams):
    conn = _IngestConn(existing=(3,))
    seams(conn)
    assert _insert(conn) is None
    assert conn.log == ["BEGIN", "select source_revision from", "COMMIT"]


def test_the_api_image_ships_the_overlay_ingest_this_module_imports():
    """api/rent_map.py (imported at module scope by api/routes/admin.py) imports
    scripts.ingest_map_overlays, so the API image must COPY scripts/: a missing tree builds
    green and crash-loops the whole API at boot."""
    dockerfile = Path(__file__).resolve().parents[1] / "Dockerfile"
    assert "COPY scripts/ ./scripts/" in dockerfile.read_text(encoding="utf-8")
    assert api_rent_map.render_sql_section is ingest_map_overlays.render_sql_section
