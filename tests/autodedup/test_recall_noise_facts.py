"""The recall-noise wave (2026-10-14): one fact written two ways is ONE fact.

Each reading is built from the live split proposal that named it (property ids in the test
names' comments); the bodies are the adverts' own sentences, cut to the clause that matters.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from autodedup.dataset import Listing
from autodedup.indistinguishable import CLUSTER, GATE, PROMOTE, distinguishing_facts
from autodedup.settings import Settings
from autodedup.text_facts import printed_storey_counts

W31 = Settings.from_json(Path(__file__).resolve().parents[2] / "autodedup/settings/w31.json")


def stamp(day: int) -> str:
    return (datetime(2026, 6, 1, tzinfo=timezone.utc) + timedelta(days=day)).isoformat()


def advert(listing_id: int, source: str, body: str, *, first: int = 0, last: int = 120,
           price: float | None = 3_450_000.0, **fields: object) -> Listing:
    raw: dict[str, object] = {
        "id": listing_id, "block": "b", "source": source, "category_main": "byt",
        "category_type": "prodej", "description": body, "price": price,
        "first_seen_at": stamp(first), "last_seen_at": stamp(last),
        "price_history": [[stamp(first), price]] if price else [],
        "location": {"obec_kod": 563510, "granularity": "obec", "granularity_rank": 20},
    }
    raw.update(fields)
    return Listing.from_json(raw)


def names(a: Listing, b: Listing, mode: str = CLUSTER, cfg: Settings = W31) -> list[str]:
    return [fact.name for fact in distinguishing_facts(a, b, None, cfg, mode)]


# --- E954: the storey count a body prints stands in for its own `total_floors` box ---------
# 343386 (Jablonec): sreality and mmreality file 5 storeys, idnes 3, every body says three.
TREMI = ("Byt 2+1 o výměře 41 m² se nachází v cihlovém, zatepleném domě celkem třemi "
         "nadzemními podlažími. Byt na 1. nadzemním podlaží má samostatný vchod a sklep.")
# 761804 (Jablonec, Kolmá): bezrealitky files 4 storeys, realitymix 3, one body.
KOLMA = ("Nabízím k prodeji krásný družstevní byt 2+1 o celkové ploše 59 m², který se nachází "
         "v cihlové budově ve velmi dobrém stavu. Byt se nachází ve 2. podlaží ze 3 a jeho "
         "dispozice je promyšlená s dostatkem světla.")
SILENT = "Byt 2+1 o výměře 41 m² v klidné části města, po rekonstrukci, se sklepem."


def test_E954_the_reader_names_the_building_never_the_unit() -> None:
    assert printed_storey_counts(TREMI) == {3}
    assert printed_storey_counts(KOLMA) == {3}
    assert printed_storey_counts("v 1. nadzemním podlaží z celkových 17") == {17}
    assert printed_storey_counts("třípodlažní dům se zahradou") == {3}
    assert printed_storey_counts("v přízemí ze šesti") == {6}
    # A maisonette's own two storeys, windows on three sides and a floor area are not counts.
    assert printed_storey_counts("dvoupodlažní byt 4+kk s terasou") == frozenset()
    assert printed_storey_counts("ve 3. patře, okna ze 3 stran") == frozenset()
    assert printed_storey_counts("hrubá podlažní plocha 300 m2") == frozenset()
    # An ordinal written without its dot places the flat; it is no count of the building.
    assert printed_storey_counts("byt ve 2 nadzemním podlaží") == frozenset()
    assert printed_storey_counts("panelový dům o 7 nadzemních podlažích") == {7}
    assert printed_storey_counts(SILENT) == frozenset()


def test_E954_every_body_saying_three_storeys_outweighs_a_box_saying_five() -> None:
    a = advert(358616, "sreality", TREMI, floor=0, total_floors=5, area_m2=41.0,
               disposition="2+1")
    b = advert(360026, "idnes", TREMI, floor=0, total_floors=3, area_m2=41.0,
               disposition="2+1")
    for mode in (PROMOTE, GATE, CLUSTER):
        assert "total_floors" not in names(a, b, mode)


def test_E954_one_side_printing_the_count_is_read_against_the_others_box() -> None:
    a = advert(18721451, "bezrealitky", KOLMA, floor=1, total_floors=4, area_m2=59.0,
               disposition="2+1")
    b = advert(18766660, "realitymix", SILENT, floor=1, total_floors=3, area_m2=59.0,
               disposition="2+1")
    assert "total_floors" not in names(a, b)


def test_E954_a_printed_count_that_contradicts_the_other_box_leaves_the_fact() -> None:
    a = advert(1, "idnes", KOLMA, total_floors=3, area_m2=59.0, disposition="2+1")
    b = advert(2, "sreality", SILENT, total_floors=5, area_m2=59.0, disposition="2+1")
    assert "total_floors" in names(a, b)


def test_E954_two_bodies_never_make_a_fact_the_boxes_do_not() -> None:
    a = advert(1, "idnes", KOLMA, total_floors=4, area_m2=59.0, disposition="2+1")
    b = advert(2, "sreality", TREMI.replace("třemi", "pěti"), total_floors=4, area_m2=59.0,
               disposition="2+1")
    assert printed_storey_counts(b.description) == {5}
    assert "total_floors" not in names(a, b)


def test_E954_settings_are_unchanged() -> None:
    raw = json.loads((Path(__file__).resolve().parents[2] / "autodedup/settings/w31.json")
                     .read_text(encoding="utf-8"))
    assert raw["d43_total_floors_agreeing_unit"] is False  # E302 stays refused (D88)
