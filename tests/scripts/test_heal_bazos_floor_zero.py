from __future__ import annotations

from typing import Any

import pytest

from scripts import heal_bazos_floor_zero as heal

PARDUBICE = ("Nabízíme byt 3+kk, který se nachází v šestém podlaží bytového domu. "
             "K bytu náleží balkon a sklep v přízemí domu.")


@pytest.mark.parametrize("text", [
    PARDUBICE,
    "Byt ve 2. patře, kočárkárna v přízemí",
    "Prodej bytu 2+1, čtvrté patro, sklep v přízemí",
    "byt v 6. NP, sklep v přízemí",
    "Patro: 3",
])
def test_the_measurement_reads_an_above_ground_storey(text: str) -> None:
    assert heal.names_above_ground_storey(text)


@pytest.mark.parametrize("text", [
    None,
    "byt v přízemí, sklep",
    "byt v 1. NP",
    "v prvním podlaží",
    "ve zvýšeném přízemí cihlového domu",
    "hezký byt 3+1 po rekonstrukci",
    # A layout or an area glued to the digit is no storey.
    "Prodám byt 2+1. Patro: přízemí.",
    "Byt 45 m2. Podlaží: přízemí",
])
def test_ground_or_silent_texts_are_left_alone(text: str | None) -> None:
    assert not heal.names_above_ground_storey(text)


def test_decide_writes_the_fixed_parsers_reading() -> None:
    assert heal.decide("Prodej bytu 3+kk", PARDUBICE) == (True, 5)
    assert heal.decide(None, "Byt ve 2. patře, kočárkárna v přízemí") == (True, 2)


def test_decide_nulls_what_the_parser_cannot_read() -> None:
    # 'v 6 podlaží' (no dot) names a storey the parser does not convert: NULL, not 0.
    assert heal.decide(None, "byt na 6 podlaží, sklep v přízemí") == (True, None)


def test_decide_nulls_a_flat_on_ground_that_names_another_storey() -> None:
    assert heal.decide(None, "byt v přízemí, ve druhém patře bydlí majitel") == (True, None)


def test_decide_skips_a_true_ground_floor() -> None:
    assert heal.decide("Byt v přízemí", "Prodám byt v přízemí, sklep") == (False, None)


def test_plan_keeps_only_selected_rows() -> None:
    rows = [
        (1, "bazos", True, "t", PARDUBICE),
        (2, "bazos", False, "t", "byt v přízemí"),
        (3, "web", False, None, "byt na 6 podlaží, sklep v přízemí"),
    ]
    assert [(c.id, c.source, c.is_active, c.new_floor) for c in heal.plan(rows)] == [
        (1, "bazos", True, 5), (3, "web", False, None)]


def test_the_write_is_compare_and_set_and_enqueues_dirty_properties() -> None:
    sql = " ".join(heal._HEAL_SQL.split())
    assert "l.floor = 0" in sql
    assert "INSERT INTO dirty_properties" in sql
    assert "listing_snapshots" not in sql and "last_seen_at" not in sql


def test_apply_without_a_backup_is_refused(monkeypatch: Any) -> None:
    monkeypatch.setenv("SUPABASE_DB_URL", "postgres://unused")
    assert heal.main(["--apply"]) == 2
