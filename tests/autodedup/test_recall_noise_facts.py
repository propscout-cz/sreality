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


# --- E955: a price move between two postings never on sale together, one area stated -------
# 678944 (Praha-Vysočany, Čtvrť Emila Kolbena): one 1+kk re-let 19,990 -> 18,990 -> 17,990 on
# idnes, sreality and bezrealitky, ONE text — inside project vocabulary, so E282 cannot read it.
KOLBEN = ("Nabízíme vám k podnájmu velmi pěkný byt o velikosti 1+kk a o rozloze 40 m². Byt se "
          "nachází ve 2. podlaží novostavby v projektu Čtvrť Emila Kolbena, v dobře dostupné "
          "lokalitě Praha 9 - Vysočany, ulice Pechmanových. Moderní kuchyňská linka je vybavena "
          "spotřebiči, jako jsou indukční varná deska, elektrická trouba, myčka a digestoř.")
# 17701 (Jablonec, Liberecká): one 3 000 m² object, re-listed at a lower price under a new text.
LIBERECKA_OLD = ("Nabízíme podnikatelský objekt ležící vedle ulice Liberecká v Jablonci nad Nisou, "
                 "který je využitelný k provozování specializovaného obchodu. V objektu je celkem "
                 "3000 m2 podlahových ploch, skladová patra jsou propojena dvěma průmyslovými "
                 "výtahy (3.000 kg a 2.000 kg).")
LIBERECKA_NEW = ("Prodáme plně funkční průmyslový areál ve velmi pěkném stavu. Dnes lehká "
                 "kovovýroba pro bižuterní a elektrotechnický průmysl. Lokalita: Jablonec nad "
                 "Nisou, ul. Liberecká. Celková užitná plocha: 3.000 m2. Vertikální doprava: 2 "
                 "nákladní výtahy (nosnost 2.000 kg a 3.000 kg).")


def kolben(listing_id: int, source: str, price: float, first: int, last: int,
           **fields: object) -> Listing:
    unit: dict[str, object] = {"category_type": "pronajem", "disposition": "1+kk",
                               "area_m2": 40.0, "floor": 1}
    return advert(listing_id, source, KOLBEN, first=first, last=last, price=price,
                  **{**unit, **fields})


def liberecka(listing_id: int, source: str, body: str, price: float, first: int, last: int,
              **fields: object) -> Listing:
    return advert(listing_id, source, body, first=first, last=last, price=price,
                  **{"category_main": "komercni", "area_m2": 3000.0, **fields})


def test_E955_a_relet_inside_project_vocabulary_is_one_path_in_the_gate_and_the_cluster() -> None:
    a = kolben(17890713, "idnes", 19_990.0, 0, 21)
    b = kolben(18830705, "sreality", 18_990.0, 21, 49)
    for mode in (GATE, CLUSTER):
        assert "price" not in names(a, b, mode)
    # Promotion merges on the ABSENCE of a fact and keeps E185's identity (E136).
    assert "price" in names(a, b, PROMOTE)


def test_E955_a_relist_under_a_new_text_is_one_path_in_the_cluster() -> None:
    a = liberecka(45562, "sreality", LIBERECKA_OLD, 27_750_000.0, 0, 3)
    b = liberecka(244241, "idnes", LIBERECKA_NEW, 25_700_000.0, 3, 130)
    assert "price" not in names(a, b, CLUSTER)
    assert "price" in names(a, b, PROMOTE)


def test_E955_two_prices_on_sale_together_stay_two() -> None:
    a = kolben(1, "idnes", 19_990.0, 0, 40)
    b = kolben(2, "sreality", 18_990.0, 10, 49)
    assert "price" in names(a, b, CLUSTER)


def test_E955_a_side_that_states_no_area_still_needs_its_identity() -> None:
    # E192 / Pouchovská: abstention is not agreement, so a missing area keeps the old bar.
    a = liberecka(1, "sreality", LIBERECKA_OLD, 27_750_000.0, 0, 3)
    b = liberecka(2, "idnes", LIBERECKA_NEW, 25_700_000.0, 3, 130, area_m2=None)
    assert "price" in names(a, b, CLUSTER)


def test_E955_two_areas_that_differ_are_still_two_moments_of_nothing() -> None:
    a = kolben(1, "idnes", 19_990.0, 0, 21)
    b = kolben(2, "sreality", 18_990.0, 21, 49, area_m2=44.0)
    assert "price" in names(a, b, CLUSTER)


def test_E955_two_storeys_that_differ_keep_the_price() -> None:
    a = kolben(1, "idnes", 19_990.0, 0, 21, floor=1)
    b = kolben(2, "sreality", 18_990.0, 21, 49, floor=3)
    assert {"floor", "price"} <= set(names(a, b, CLUSTER))
