"""Hermetic tests for scripts/csu_population.py — the ČSÚ DataStat JSON-stat population parser, by
municipality code (the RÚIAN obec code). No network, no DB; reads a trimmed JSON-stat fixture
committed alongside.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import csu_population

FIXTURE = (
    Path(__file__).resolve().parents[1] / "fixtures" / "csu_population_sample.json"
)
LATEST = {554782: (1407084, 2026), 582786: (400000, 2026), 581429: (800, 2026), 591429: (1200, 2026)}


def _doc() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_every_municipality_by_code_at_the_latest_year_and_no_kraj_aggregate() -> None:
    # Two "Březina" in Jihomoravský kraj stay two towns: a code is never a name.
    assert csu_population.parse_population_by_code(_doc()) == LATEST


def test_load_from_file() -> None:
    assert csu_population.load_population_by_code(FIXTURE) == LATEST


def test_missing_child_map_raises() -> None:
    doc = _doc()
    doc["dimension"]["UZ25"]["category"].pop("child")
    with pytest.raises(ValueError):
        csu_population.parse_population_by_code(doc)


def test_sparse_value_object_supported() -> None:
    # JSON-stat allows a sparse {index: value} object instead of a dense list.
    doc = _doc()
    doc["value"] = {str(i): v for i, v in enumerate(doc["value"])}
    assert csu_population.parse_population_by_code(doc) == LATEST
