"""What an ad says against what the register states: one verdict per fact both state on the same basis."""

from __future__ import annotations

from typing import Any

import pytest

from api.register_comparison import compare


def _ad(**over: Any) -> dict[str, Any]:
    return {"has_lift": None, "floor": None, "total_floors": None, "building_type": None, **over}


def _building(lift: int | None = None, floors: int | None = None, construction: int | None = None) -> dict[str, Any]:
    coded = lambda code: None if code is None else {"code": code, "label": "x", "unknown": False}  # noqa: E731
    return {"lift": coded(lift), "floors": floors, "construction": coded(construction)}


@pytest.mark.parametrize(("ad", "register", "differs"), [
    (True, 1, False), (False, 2, False), (True, 2, True), (False, 1, True),
    (None, 1, None), (True, 9, None), (True, None, None),
])
def test_lift(ad, register, differs):
    verdict = compare(_ad(has_lift=ad), _building(lift=register))["lift"]
    assert verdict == (None if differs is None else {"differs": differs, "ad": ad})


@pytest.mark.parametrize(("total", "floor", "register", "differs"), [
    (6, None, 6, False),
    (5, None, 6, False),   # the register counts a basement the ad leaves out
    (2, None, 9, False),   # however far below: no tolerance is invented
    (7, None, 6, True),    # more storeys than the register counts with its basements
    (None, 5, 6, False),   # ground = 0, so floor 5 is the 6th storey: the top one
    (None, 6, 6, True),    # the 7th storey of a building of 6
    (6, 6, 6, True),
    (None, -1, 1, False),  # a basement flat is above nothing
    (None, None, 6, None), (7, 3, None, None),
])
def test_floors_differ_in_one_direction_only(total, floor, register, differs):
    verdict = compare(_ad(total_floors=total, floor=floor), _building(floors=register))["floors"]
    assert verdict == (None if differs is None else {"differs": differs, "ad": {"total_floors": total, "floor": floor}})


@pytest.mark.parametrize(("ad", "register", "differs"), [
    ("cihla", 10, False), ("cihla", 1, False), ("kamen", 2, False), ("panel", 4, False), ("panel", 41, False),
    ("drevo", 61, False), ("roubena", 61, False), ("roubena", 10, True), ("cihla", 4, True), ("panel", 10, True), ("drevo", 1, True),
    # No counterpart on the other side: no verdict.
    ("skelet", 4, None), ("smisena", 10, None), ("montovana", 41, None), ("ocelova", 4, None), ("nizkoenergeticka", 10, None), ("jina", 1, None),
    ("cihla", 11, None), ("cihla", 7, None), ("panel", 42, None), ("cihla", 9, None), ("cihla", None, None),
    (None, 4, None),
])
def test_construction_compares_only_classes_both_sides_name(ad, register, differs):
    verdict = compare(_ad(building_type=ad), _building(construction=register))["construction"]
    assert verdict == (None if differs is None else {"differs": differs, "ad": ad})


def test_every_fact_is_answered_even_when_nothing_compares():
    assert compare(_ad(), _building()) == {"lift": None, "floors": None, "construction": None}
