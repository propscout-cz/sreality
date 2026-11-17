"""Migration 752 is 636's properties_public with the two storey columns re-sourced, nothing else."""

from __future__ import annotations

from pathlib import Path

_MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"


def _view(name: str) -> str:
    text = (_MIGRATIONS / name).read_text(encoding="utf-8")
    start = text.index("properties_public as", text.index("create or replace view"))
    while "from properties p" not in text[start:text.index(";", start)]:
        start = text.index("properties_public as", start + 1)
    return " ".join(text[start:text.index(";", start)].split())


def test_the_view_is_636s_with_the_storey_read_off_the_property() -> None:
    old = _view("636_property_location_ad_readers.sql")
    new = _view("752_properties_public_own_storey.sql")
    assert "l.floor, l.total_floors," in old
    assert new == old.replace("l.floor, l.total_floors,", "p.floor, p.total_floors,")
