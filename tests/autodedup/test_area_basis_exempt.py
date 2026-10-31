"""The pair veto's area clause yields to a realitymix side whose area is the total floor area.

realitymix prints "celková podlahová plocha" (`listings.area_basis = 'floor'`), which may hold
the cellar, balcony or terrace a partner's figure leaves out: 12 of 45 such properties sit more
than 8 % off their partner, against 1 of 69 where realitymix states the užitná figure
(research experiments/portal_columns.md section 3.2). Capped at 15 %, the exemption releases
13 operator-SAME pairs (9 properties) and no DIFFERENT pair (V2). The area stays a scoring
feature; only the veto yields. The lane vetoes a neighbour on its stored `rt_fp` columns
(`GuardRow`), so the two facts travel there too (migration 677).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from autodedup import census, incremental_sql as S, settings as settings_module
from autodedup.dataset import Listing, Location
from autodedup.fingerprint import Fingerprint, build_fingerprint
from autodedup.guards import area_basis_exempt, pair_veto
from autodedup.incremental import GuardRow, guard_row
from autodedup.settings import Settings

ROOT = Path(__file__).resolve().parents[2]
SETTINGS = ROOT / "autodedup/settings"
MIGRATION = ROOT / "migrations/677_rt_fp_area_basis_source.sql"
W31 = Settings.from_json(SETTINGS / "w31.json")
W34 = Settings.from_json(SETTINGS / "w34.json")
EXEMPT = Settings.from_dict({**W31.to_dict(), "area_veto_basis_floor_exempt": {"realitymix": 0.15}})


def fp(listing_id: int, settings: Settings, *, source: str | None = None,
       basis: str | None = None, **kwargs: Any) -> Fingerprint:
    attrs = {"area_basis": basis} if basis is not None else {}
    listing = Listing(id=listing_id, block="b", location=Location(), category_type="prodej",
                      source=source, attrs=attrs, **kwargs)
    return build_fingerprint(listing, [], settings)


def pair(settings: Settings, area: float, *, source: str = "realitymix", basis: str = "floor",
         cat_a: str = "byt", cat_b: str = "byt") -> str | None:
    a = fp(1, settings, source=source, basis=basis, category_main=cat_a, area_m2=area)
    b = fp(2, settings, source="sreality", basis="usable", category_main=cat_b, area_m2=100.0)
    return pair_veto(a, b, settings)


def test_the_fingerprint_carries_the_listing_s_basis_and_portal() -> None:
    side = fp(1, W31, source="realitymix", basis="floor", area_m2=60.0)
    assert (side.source, side.area_basis) == ("realitymix", "floor")
    assert guard_row(side).source == "realitymix" and guard_row(side).area_basis == "floor"
    assert fp(2, W31, area_m2=60.0).area_basis is None


@pytest.mark.parametrize("area", [100.0, 92.0, 91.9, 88.0, 84.0, 70.0])
@pytest.mark.parametrize("basis", ["floor", "usable"])
def test_an_empty_dial_is_the_w31_veto(area: float, basis: str) -> None:
    named = Settings.from_dict({**W31.to_dict(), "area_veto_basis_floor_exempt": {}})
    assert W31.area_veto_basis_floor_exempt == {}
    assert pair(named, area, basis=basis) == pair(W31, area, basis=basis)
    assert pair(W31, area, basis=basis) == ("area" if area < 92.0 else None)


def test_a_realitymix_floor_side_passes_at_twelve_percent_and_is_vetoed_at_sixteen() -> None:
    assert pair(W31, 88.0) == "area"
    assert pair(EXEMPT, 88.0) is None
    assert pair(EXEMPT, 85.0) is None, "15 % is the cap, inclusive"
    assert pair(EXEMPT, 84.0) == "area"


def test_either_side_may_be_the_realitymix_one() -> None:
    a = fp(1, EXEMPT, source="sreality", basis="usable", category_main="byt", area_m2=100.0)
    b = fp(2, EXEMPT, source="realitymix", basis="floor", category_main="byt", area_m2=88.0)
    assert pair_veto(a, b, EXEMPT) is None and pair_veto(b, a, EXEMPT) is None


@pytest.mark.parametrize("source", ["sreality", "idnes", "bazos", "ceskereality"])
def test_another_portal_s_floor_side_at_twelve_percent_is_vetoed(source: str) -> None:
    assert pair(EXEMPT, 88.0, source=source) == "area"


@pytest.mark.parametrize("basis", ["usable", "total"])
def test_a_realitymix_side_of_another_basis_at_twelve_percent_is_vetoed(basis: str) -> None:
    assert pair(EXEMPT, 88.0, basis=basis) == "area"


def test_no_basis_and_no_area_is_no_exemption() -> None:
    a = fp(1, EXEMPT, source="realitymix", category_main="byt", area_m2=88.0)
    b = fp(2, EXEMPT, source="sreality", category_main="byt", area_m2=100.0)
    assert pair_veto(a, b, EXEMPT) == "area"
    assert pair_veto(fp(3, EXEMPT, source="realitymix", basis="floor"), b, EXEMPT) is None


def test_the_area_stays_a_feature_only_the_veto_yields() -> None:
    a = fp(1, EXEMPT, source="realitymix", basis="floor", area_m2=88.0)
    b = fp(2, EXEMPT, source="sreality", basis="usable", area_m2=100.0)
    assert area_basis_exempt(a, b, 0.12, EXEMPT)
    assert not area_basis_exempt(a, b, 0.16, EXEMPT)
    assert not area_basis_exempt(a, b, 0.12, W31)


def test_w34_s_exemption_needs_no_gallery_where_c4b_does() -> None:
    # C4b lets a byt x byt gap past 8 % only on one gallery; the exemption asks for none.
    mix = fp(1, W34, source="realitymix", basis="floor", category_main="byt", area_m2=88.0)
    usable = fp(3, W34, source="realitymix", basis="usable", category_main="byt", area_m2=88.0)
    partner = fp(2, W34, source="sreality", basis="usable", category_main="byt", area_m2=100.0)
    assert pair_veto(mix, partner, W34, lambda: False) is None
    assert pair_veto(usable, partner, W34, lambda: False) == "area"
    assert pair_veto(usable, partner, W34, lambda: True) is None
    for cat_a, cat_b in (("byt", "komercni"), ("komercni", "byt"), ("komercni", "komercni")):
        assert pair(W34, 88.0, cat_a=cat_a, cat_b=cat_b) is None
        assert pair(W34, 88.0, basis="usable", cat_a=cat_a, cat_b=cat_b) == "area"
        assert pair(W34, 84.0, cat_a=cat_a, cat_b=cat_b) == "area"


def test_the_rt_lane_s_served_rows_read_the_same_rule() -> None:
    mix = GuardRow(1, "komercni", "prodej", 88.0, None, None, "realitymix", "floor")
    partner = GuardRow(2, "komercni", "prodej", 100.0, None, None, "sreality", "usable")
    assert pair_veto(mix, partner, W31) == "area"
    assert pair_veto(mix, partner, EXEMPT) is None
    # A row written before migration 677 carries neither column: the veto stands.
    legacy = GuardRow(3, "komercni", "prodej", 88.0, None, None)
    assert pair_veto(legacy, partner, EXEMPT) == "area"


@pytest.mark.parametrize("dial", [{"reality": 0.15}, {"web": 0.15}, {"realitymix": 0.08},
                                  {"realitymix": 0.05}, {"realitymix": 1.0}])
def test_the_dial_is_validated(dial: dict[str, float]) -> None:
    raw = json.loads((SETTINGS / "w31.json").read_text(encoding="utf-8"))
    with pytest.raises(ValueError, match="area_veto_basis_floor_exempt"):
        Settings.from_dict({**raw, "area_veto_basis_floor_exempt": dial})


def test_the_dial_s_vocabulary_is_the_census_s() -> None:
    assert settings_module.SOURCES == census.SOURCES


def _migration_code() -> str:
    body = "\n".join(line.split("--")[0]
                     for line in MIGRATION.read_text(encoding="utf-8").lower().splitlines())
    return re.sub(r"\s+", " ", body)


def test_migration_677_adds_three_nullable_columns_without_a_rewrite() -> None:
    code = _migration_code()
    assert re.findall(r"alter table (\S+)", code) == ["autodedup.rt_fp"]
    assert "add column if not exists source text," in code
    assert "add column if not exists area_basis text," in code
    assert "add column if not exists block_key text;" in code
    assert "default" not in code and "not null" not in code
    for verb in ("drop ", "delete from", "truncate", "update ", "create "):
        assert verb not in code, verb
    assert "set lock_timeout = '5s';" in code and "reset lock_timeout;" in code


def test_the_lane_writes_reads_and_checks_the_columns_it_adds() -> None:
    assert "source           = excluded.source" in S.RT_FP_UPSERT_SQL
    assert "area_basis       = excluded.area_basis" in S.RT_FP_UPSERT_SQL
    assert "%(source)s::text, %(area_basis)s::text" in S.RT_FP_UPSERT_SQL
    assert S.RT_FP_READ_SQL.split("from autodedup.rt_fp")[0].rstrip().endswith(
        "f.dd, f.source, f.area_basis, f.block_key")
    assert "attname = 'area_basis'" in S.RT_STORE_PRESENT_SQL
    assert "attname = 'block_key'" in S.RT_STORE_PRESENT_SQL
