"""Offline contract tests for the operator claim producer — the address picker (W3b, ruling Q5).

No DB. A pick is written on EVERY ad of the property as the four picker types, through the lane's
own one write (`claims_store.write_claims`): the picked level's register names, a tombstone for
the rest, licence `operator`, which the resolver's read puts ahead of the portal's. Then the
location ad alone is resolved and read back (`held`).
"""

from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path
from typing import Any

import pytest

from location_data import contracts
from location_data import operator_corrections as oc

_ADS = [(11, "sreality", "s11", False), (12, "bezrealitky", "b12", True)]


class _Conn:
    """Answers the property lock, the ads read and the register read by statement."""

    def __init__(self, ads: list[tuple] = _ADS, register: tuple | None = (None, None, None)) -> None:
        self.ads, self.register = ads, register
        self.executed: list[tuple[str, Any]] = []
        self._next: Any = None

    def cursor(self, **_: Any) -> "_Conn":
        return self

    def transaction(self) -> Any:
        return nullcontext()

    def __enter__(self) -> "_Conn":
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def execute(self, sql: str, params: object = None) -> None:
        self.executed.append((" ".join(sql.split()), params))
        if sql == oc._LOCK_SQL:
            self._next = (1,) if self.ads else None
        else:
            self._next = self.ads if sql in (oc._ADS_SQL, oc._LISTINGS_SQL) else self.register

    def fetchall(self) -> Any:
        return self._next

    def fetchone(self) -> Any:
        return self._next


_ROW = {"ruian_adm_kod": 24252301, "ulice_kod": 9002, "obec_kod": 500002, "label": "Lipová 12, Betov"}


@pytest.fixture()
def resolved(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """`resolve_now` and `read_projection` recorded; the row read back is `_ROW`."""
    ads: list[int] = []
    monkeypatch.setattr(oc, "resolve_now", lambda conn, listing_id: ads.append(listing_id) or True)
    monkeypatch.setattr(oc, "read_projection", lambda conn, listing_id: dict(_ROW))
    return ads


@pytest.fixture()
def written(monkeypatch: pytest.MonkeyPatch, resolved: list[int]) -> list[tuple[list[Any], str]]:
    calls: list[tuple[list[Any], str]] = []

    def fake_write(cur: Any, claims: list[Any], reason: str = "claim_insert") -> tuple[int, int]:
        calls.append((list(claims), reason))
        return len([c for c in claims if not c.retract]), 2

    monkeypatch.setattr(oc, "write_claims", fake_write)
    return calls


def _by_ad(claims: list[Any]) -> dict[int, dict[str, str | None]]:
    out: dict[int, dict[str, str | None]] = {}
    for c in claims:
        assert (c.licence_class, c.surface, c.extraction_method) == (
            "operator", "operator_input", "operator_manual")
        assert (c.contract_entry_id, c.claim_confidence, c.blur_evidence) == (None, "exact", "none")
        assert c.retract == (c.value_text is None)
        out.setdefault(c.listing_id, {})[c.claim_type] = c.value_text
    return out


def test_an_address_pick_states_the_point_on_every_ad_and_retracts_the_rest(written) -> None:
    conn = _Conn()
    result = oc.submit_pick(conn, property_id=5, pick=("adresni_misto", 24252301), by="u-0")

    ((claims, reason),) = written
    assert reason == "operator_edit" and len(claims) == len(_ADS) * 4
    point = {"address_point_id": "24252301", "street_name": None, "obec_name": None,
             "okres_name": None}
    assert _by_ad(claims) == {11: point, 12: point}
    assert {c.source for c in claims if c.listing_id == 12} == {"bezrealitky"}
    (batch,) = [p for s, p in conn.executed if "location_claim_batches" in s]
    assert batch == (oc.EXTRACTOR_VERSION, 2, "property 5: adresni_misto 24252301 by u-0")
    assert result["ads"] == [{"listing_id": 11, "source": "sreality", "location_ad": False},
                             {"listing_id": 12, "source": "bezrealitky", "location_ad": True}]
    assert (result["pick"], result["written"]) == ({"level": "adresni_misto", "code": 24252301}, 2)
    lock, ads_read = (s for s, _p in conn.executed[:2])
    assert lock.endswith("FOR SHARE") and ads_read == " ".join(oc._ADS_SQL.split())


def test_the_location_ad_alone_is_resolved_and_read_back(written, resolved) -> None:
    result = oc.submit_pick(_Conn(), property_id=5, pick=("adresni_misto", 24252301), by="u-0")
    assert resolved == [12]
    assert (result["resolved"], result["held"], result["location"]["label"]) == (
        True, True, "Lipová 12, Betov")
    street = oc.submit_pick(_Conn(register=("Lipová", "Betov", None)), property_id=5,
                            pick=("ulice", 1), by=None)
    assert street["held"] is False                       # L1: the row binds another street
    cleared = oc.submit_pick(_Conn(), property_id=5, pick=None, by=None)
    assert cleared["held"] is None
    orphan = [(11, "sreality", "s11", False)]           # no location ad: the first ad resolves
    oc.submit_pick(_Conn(ads=orphan), property_id=5, pick=None, by=None)
    assert resolved[-1] == 11


def test_a_street_pick_writes_the_registers_names_and_drops_the_point(written) -> None:
    oc.submit_pick(_Conn(register=("Týnská ulička", "Praha", None)), property_id=5,
                   pick=("ulice", 472786), by="u-0")
    ((claims, _),) = written
    assert _by_ad(claims)[11] == {"address_point_id": None, "street_name": "Týnská ulička",
                                  "obec_name": "Praha", "okres_name": None}


def test_an_obec_pick_names_its_okres(written) -> None:
    oc.submit_pick(_Conn(register=(None, "Krásný Les", "Liberec")), property_id=5,
                   pick=("obec", 564192), by=None)
    ((claims, _),) = written
    assert _by_ad(claims)[12] == {"address_point_id": None, "street_name": None,
                                  "obec_name": "Krásný Les", "okres_name": "Liberec"}


def test_a_clear_is_four_tombstones_per_ad(written) -> None:
    conn = _Conn()
    result = oc.submit_pick(conn, property_id=5, pick=None, by="u-0")
    ((claims, _),) = written
    assert len(claims) == 8 and all(c.retract for c in claims)
    assert not [s for s, _p in conn.executed if "ruian_" in s]       # no register read
    assert [p[2] for s, p in conn.executed if "location_claim_batches" in s] == [
        "property 5: cleared by u-0"]
    assert result["pick"] is None


def test_a_code_the_register_does_not_hold_and_a_property_without_ads_are_refused(written) -> None:
    with pytest.raises(oc.CorrectionError):
        oc.submit_pick(_Conn(register=None), property_id=5, pick=("ulice", 1), by=None)
    with pytest.raises(oc.CorrectionError):
        oc.submit_pick(_Conn(), property_id=5, pick=("cast_obce", 1), by=None)
    with pytest.raises(oc.UnknownPropertyError):
        oc.submit_pick(_Conn(ads=[]), property_id=5, pick=None, by=None)
    assert written == []


def test_the_detach_clear_retracts_the_four_types_and_writes_a_batch_only_on_change(written) -> None:
    conn = _Conn(ads=[(11, "sreality", "s11")])
    oc.clear(conn, [11])
    ((claims, reason),) = written
    assert reason == "operator_edit" and [c.claim_type for c in claims] == list(oc.PICK_TYPES)
    assert all(c.retract for c in claims)
    assert not [s for s, _p in conn.executed if "location_claim_batches" in s]   # 0 written
    assert oc.clear(_Conn(), []) == 0 and len(written) == 1


def test_the_picker_types_are_contract_types_and_no_pin() -> None:
    """No new claim type: four of the twelve. `coordinate` stays out (limit L2)."""
    assert set(oc.PICK_TYPES) <= contracts.CLAIM_TYPES
    assert "coordinate" not in oc.PICK_TYPES
    assert set(oc.PICK_COLUMN) == {"adresni_misto", "ulice", "obec"}


def test_the_correction_route_never_imports_the_intake_lane() -> None:
    """The one write lives in `claims_store`, so the API's correction route pays for one small
    module, not the 2,000-line intake lane and its page readers."""
    import ast

    tree = ast.parse(Path(oc.__file__).read_text(encoding="utf-8"))
    imported = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    assert "location_data.claims_store" in imported
    assert "location_data.claims_intake" not in imported
