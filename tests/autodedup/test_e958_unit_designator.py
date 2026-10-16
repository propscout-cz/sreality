"""E958: the developer's letter-dot unit code (`byt 3+kk B.3.7`) is a stated fact.

Pískovna Beroun posts its flats as `byt 3+kk B.3.7` / `D.3.7`; the code reader needed a digit
right after the letter, so no reader saw either and live group 341452 joined flat B.3.7 with
flat D.3.7 (same 72,7 m2 3+kk, same storey, 10,947,000 against 10,968,000).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from autodedup.dataset import Listing, Location
from autodedup.indistinguishable import MODES, distinguishing_facts
from autodedup.settings import Settings
from autodedup.text_facts import printed_unit_codes

SETTINGS_DIR = Path(__file__).resolve().parents[2] / "autodedup" / "settings"

PISKOVNA = ("Nabízíme k prodeji byt 3+kk {code} o výměře 72,7 m2 s balkonem 20,07 m2 v nově "
            "vznikající rezidenční čtvrti Pískovna Beroun. Projekt propojuje klidnější prostředí "
            "na okraji města, blízkost přírody a komfort každodenního života.")


def w31() -> Settings:
    return Settings.from_json(SETTINGS_DIR / "w31.json")


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Nabízíme k prodeji byt 3+kk B.3.7 o výměře 72,7 m² s balkonem", {"B3.7"}),
        ("Nabízíme k prodeji byt 3+kk D.3.7 o výměře 72,7 m2", {"D3.7"}),
        ("prodeji byt 4+kk, jednotku A2.104 o podlahové ploše 90,31 m2", {"A2.104"}),
        ("prostorný rodinný dům 5+kk B1.2 o výměře 177,70 m2", {"B1.2"}),
        # One house, idnes and sreality: one code written two ways is one code.
        ("prostorný rodinný dům 5+kk A.7.2 o výměře 177,7 m2", {"A7.2"}),
        ("rodinný dům 5+kk a7.2 o výměře 177,70 m2", {"A7.2"}),
        # Read whole, and its head is no longer read again as unit `B`.
        ("novostavbu bytové jednotky č. B.6.04, která je situována", {"B6.04"}),
    ],
)
def test_letter_dot_code_is_read(text: str, expected: set[str]) -> None:
    assert set(printed_unit_codes(text, wide=True)) == expected


@pytest.mark.parametrize(
    "text,expected",
    [
        # `č.` folds to `c.` and stays the marker: the reading main gave.
        ("moderní byt 1+kk C.2.3 o výměře 34,7 m2", {"2.3"}),
        ("Nabízíme mezonetový byt 3+kk č. 2.07", {"2.07"}),
        # A building letter with ONE number is a single segment, read only as its letter.
        ("nový byt č. E.302 o dispozici 4+kk", {"E"}),
    ],
)
def test_the_marker_and_the_single_segment_are_unchanged(text: str, expected: set[str]) -> None:
    assert set(printed_unit_codes(text, wide=True)) == expected


@pytest.mark.parametrize(
    "text",
    [
        "prodej bytové jednotky č. 298/4, nacházející se",
        "prodej bytové jednotky č.298/4, nacházející se",
        "dům č.p.123/4 v obci",
        "byt k nastěhování od 1.8.2026",
        "byt v 2.NP s výhledem",
        "byt 3+kk o výměře 72.7 m2",
        "byt 3+kk, cena 10.947.000 Kč",
        "byt 3+kk B.3 o výměře 72 m2",
        "byt 3+kk B. 3.7 o výměře",
        "byt 3+kk B.3.7.1.2 o výměře",
        "Ev. č. B.3.7, byt 3+kk",
    ],
)
def test_no_number_that_is_not_a_unit_code_is_read(text: str) -> None:
    assert printed_unit_codes(text) == frozenset()


def ad(listing_id: int, code: str, price: float, *, active: bool = True,
       history: tuple[tuple[str, float], ...] = ()) -> Listing:
    return Listing(
        id=listing_id, block="town531057", source="ceskereality", description=PISKOVNA.format(code=code),
        price=price, area_m2=73.0, disposition="3+kk", floor=2, category_main="byt",
        category_type="prodej", location=Location(obec_kod=531057, obec_name="Beroun"),
        first_seen_at="2026-07-07T00:00:00", last_seen_at="2026-10-14T00:00:00",
        inactive_at=None if active else "2026-07-22T00:00:00", is_active=active,
        price_history=list(history or ((("2026-07-07T00:00:00", price),))),
    )


def unit_code_in(a: Listing, b: Listing, mode: str) -> bool:
    return "unit_code" in {fact.name for fact in distinguishing_facts(a, b, None, w31(), mode)}


B_ADS = [ad(386473, "B.3.7", 10_947_000.0, active=False), ad(472041, "B.3.7", 10_947_000.0, active=False),
         ad(478306, "B.3.7", 10_947_000.0, active=False)]
D_ADS = [ad(506537, "D.3.7", 10_968_000.0), ad(506976, "D.3.7", 10_968_000.0, active=False),
         ad(507264, "D.3.7", 10_968_000.0), ad(12370264, "D.3.7", 10_968_000.0)]
# The sreality advert printed B.3.7 at 10,947,000 on 06-22 and was rewritten to D.3.7 at
# 10,968,000 on 07-07: its slot was reused for the next flat, and its text is that flat.
REUSED = ad(341452, "D.3.7", 10_968_000.0, history=(("2026-06-22T09:33:09", 10_947_000.0),
                                                     ("2026-07-07T16:30:21", 10_968_000.0)))


@pytest.mark.parametrize("mode", MODES)
def test_piskovna_b37_and_d37_are_two_flats(mode: str) -> None:
    for b in B_ADS:
        for d in [*D_ADS, REUSED]:
            assert unit_code_in(b, d, mode), (b.id, d.id)


@pytest.mark.parametrize("mode", MODES)
def test_one_flat_on_every_portal_is_not_parted(mode: str) -> None:
    for group in (B_ADS, [*D_ADS, REUSED]):
        for i, a in enumerate(group):
            for b in group[i + 1:]:
                assert not unit_code_in(a, b, mode), (a.id, b.id)
