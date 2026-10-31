"""Shared fixtures for the location-data loaders. No network, no database."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "location_data"

# The row's keys, finest first (W5): the grain every published row must carry.
_KEYS = (("address_point", "ruian_adm_kod"), ("street", "ulice_kod"),
         ("cast_obce_or_quarter", "cast_obce_kod"), ("obec", "obec_kod"), ("okres", "okres_kod"),
         ("kraj", "kraj_kod"))


@pytest.fixture(autouse=True)
def _grain_is_the_finest_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """W5's property rail, over EVERY resolution any test here makes: the published grain is the
    row's finest key (`grade.grain_of`, the one writer), so no second writer can come back."""
    from location_data.resolver import core

    resolve = core.resolve

    def checked(*args, **kwargs):
        row = resolve(*args, **kwargs)
        named = row.country_status == "foreign" and row.country_code
        finest = next((g for g, k in _KEYS if getattr(row, k) is not None),
                      "country" if named else "unknown")
        assert row.granularity == finest, (row.listing_id, row.granularity, finest)
        return row

    monkeypatch.setattr(core, "resolve", checked)


@pytest.fixture
def ob_adr_zip(tmp_path: Path) -> Path:
    """The OB_ADR product shape: a zip of per-obec CSVs, CP1250."""
    path = tmp_path / "20260731_OB_ADR_csv.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("20260731_OB_554782_ADR.csv", (FIXTURES / "ob_adr_sample.csv").read_bytes())
    return path


@pytest.fixture
def strukt_zip(tmp_path: Path) -> Path:
    """The strukt_ADR product shape: 7 CSVs under strukturovane-CSV/."""
    path = tmp_path / "20260731_strukt_ADR.csv.zip"
    with zipfile.ZipFile(path, "w") as zf:
        for member in sorted((FIXTURES / "strukturovane-CSV").glob("*.csv")):
            zf.writestr(f"strukturovane-CSV/{member.name}", member.read_bytes())
    return path
