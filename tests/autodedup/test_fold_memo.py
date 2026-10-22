"""R1: each distinct text is folded once a pass, to the string it folded to before.

Two folds are memoised: `normalize.fold` (behind `fact_text`, `normalize_text`, the numeric facts,
the disposition, the attribute token) and `text_facts.fold` (behind the per-pair readers and
`indistinguishable`). They are kept apart because they differ: `text_facts._ACCENTS` strips
U+034F and keeps U+1DC0-1DFF, the combining class does the opposite. Each memo is a cost change
only: every reading built on it is the same on a cold memo, a warm one, an LRU of one text and the
unmemoised formula. Each is an LRU bounded at FOLD_MEMO_CAP, counted as "folds" by the lane, and
emptied when its pass ends (E949).
"""

from __future__ import annotations

import inspect
import json
import re
import unicodedata
from itertools import combinations
from pathlib import Path
from typing import Any, Callable, Iterator

import pytest

from autodedup import incremental_lane, indistinguishable, normalize, text_facts
from autodedup.dataset import Listing
from autodedup.fingerprint import build_fingerprint
from autodedup.incremental_lane import run_incremental
from autodedup.indistinguishable import (
    CLUSTER, _development_pair, distinguishing_facts, prose_obec_conflict,
)
from autodedup.normalize import (
    attr_token, disposition_norm, fact_text, fact_text_folded, fold, normalize_text,
    numeric_facts,
)
from autodedup.settings import Settings
from tests.autodedup import lane_world
from tests.autodedup.test_rt_memory import _ARGUMENTS

HERE = Path(__file__).resolve().parent
_UNMEMOISED_DEACCENT = normalize._deaccent
_PRE_R1_ACCENTS = re.compile("[̀-ͯ]")

TEXTS = (
    "Prodej bytu 3+kk, 75 m², Praha 3 – Žižkov, cena 5 500 000 Kč",
    "Pronájem RD 5+1 v obci Řevnice, přízemí a 2. NP, č. bytu 12, sklep 4 m2",
    "ÚŽASNÝ BYT S TERASOU — Čeněk Šťastný, ulice Ďáblická 1234/5, jednotka č. 7",
    "Cena 12 500 Kč/měs. + energie,  3. patro",
    "Garsoniéra 28m2 ve 4. podlaží, Ústí nad Labem, ev. č. 657349",
    "Velmi dobrý",
)
# Where the two folds part: U+034F (class 0, inside _ACCENTS), U+1DC0 (combining, outside it).
APART = "Byt͏ 2+kk ᷀Žižkov 𝟐 m²"

# Every form built on `normalize.fold`, beside the fold itself.
FORMS: tuple[Callable[[str], Any], ...] = (
    fact_text, normalize_text, numeric_facts, disposition_norm, attr_token)
# The readers `text_facts.fold` serves that no body cache holds, and the uncached `fact_text` ones.
UNCACHED_READERS: tuple[Callable[[str], Any], ...] = (
    text_facts.unit_designators, text_facts.reference_codes, text_facts.orientations,
    text_facts.states_from_price, text_facts.states_charge_range, text_facts.states_top_storey,
    text_facts.area_ranges, text_facts.built_connection, text_facts.plan_headline_area)


def _before(s: str | None) -> str:
    """`normalize.fold` as it was before R1."""
    return _UNMEMOISED_DEACCENT(s).lower() if s else ""


def _before_text_facts(text: str) -> str:
    """`text_facts.fold` as it was before R1."""
    return _PRE_R1_ACCENTS.sub("", unicodedata.normalize("NFKD", text)).lower()


def _forget() -> None:
    normalize._folds.clear()
    text_facts._folds.clear()


@pytest.fixture(autouse=True)
def _empty_memo() -> Iterator[None]:
    _forget()
    yield
    _forget()


def _counting(calls: list[str], fold_now: Callable[[str], str]) -> Callable[[str], str]:
    def counted(value: str) -> str:
        calls.append(value)
        return fold_now(value)

    return counted


def test_a_known_czech_text_folds_to_the_same_string() -> None:
    assert fold(TEXTS[0]) == "prodej bytu 3+kk, 75 m2, praha 3 – zizkov, cena 5 500 000 kc"
    assert fold(TEXTS[0]) == "prodej bytu 3+kk, 75 m2, praha 3 – zizkov, cena 5 500 000 kc"


@pytest.mark.parametrize("text", TEXTS)
def test_every_form_reads_the_same_cold_warm_and_unmemoised(
        text: str, monkeypatch: pytest.MonkeyPatch) -> None:
    with monkeypatch.context() as unmemoised:
        unmemoised.setattr(normalize, "fold", _before)
        expected = [_before(text), *(form(text) for form in FORMS)]
    assert normalize._folds == {}, "the reference never touched the memo"
    assert expected[1] == fact_text_folded(_before(text))

    cold = []
    for form in (fold, *FORMS):
        _forget()
        cold.append(form(text))
    warm = [form(text) for form in (fold, *FORMS)]

    assert cold == expected and warm == expected


@pytest.mark.parametrize("text", (*TEXTS, APART, ""))
def test_the_two_folds_each_read_as_before_and_stay_apart(text: str) -> None:
    for _ in range(2):
        assert normalize.fold(text) == _before(text)
        assert text_facts.fold(text) == _before_text_facts(text)
    if text == APART:
        assert normalize.fold(text) != text_facts.fold(text), "one shared memo would be wrong"


def test_none_and_empty_fold_to_empty_and_never_enter_the_memo() -> None:
    assert fold(None) == "" and fold("") == ""
    assert fact_text(None) == "" and normalize_text("") == ""
    assert numeric_facts("") == set() and disposition_norm(None) is None
    assert attr_token(None) == "" and attr_token("") == ""
    assert normalize._folds == {}


def test_each_distinct_text_is_deaccented_once(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(normalize, "_deaccent", _counting(calls, _UNMEMOISED_DEACCENT))
    for _ in range(3):
        for text in TEXTS:
            fold(text)
            fact_text(text)
            normalize_text(text)

    assert calls == list(TEXTS)
    assert len(normalize._folds) == len(TEXTS)
    assert fold(TEXTS[1]) is fold(TEXTS[1]), "a second fold is the stored string"


def test_each_body_is_folded_once_by_the_per_pair_readers(
        monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(text_facts, "_fold_now", _counting(calls, text_facts._fold_now))
    for _ in range(3):
        for text in TEXTS:
            for reader in UNCACHED_READERS[:5]:
                reader(text)

    assert set(TEXTS) <= set(calls)
    assert len(calls) == len(set(calls)), "no text is folded twice"


def test_a_development_pair_folds_each_body_once(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(indistinguishable, "fold", _counting(calls, text_facts.fold))
    a = Listing(id=1, block="", description="Byt 3+kk, dokončení v roce 2027")
    b = Listing(id=2, block="", description="Byt 2+kk u parku, sklep a balkon")

    assert _development_pair(a, b) is False
    assert calls == [a.description, b.description], "not once per PROJECT_TERMS term"


def test_a_text_that_folds_to_nothing_is_still_folded_once(
        monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(normalize, "_deaccent", _counting(calls, _UNMEMOISED_DEACCENT))
    combining_only = "́̌"

    assert fold(combining_only) == "" and fold(combining_only) == ""
    assert calls == [combining_only]


def test_the_memo_evicts_the_least_recently_folded_text(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(normalize, "FOLD_MEMO_CAP", 2)
    first, second, third = TEXTS[:3]
    for memo, folding in ((normalize._folds, normalize.fold),
                          (text_facts._folds, text_facts.fold)):
        folding(first)
        folding(second)
        folding(first)
        folding(third)
        assert list(memo) == [first, third], "the hit kept `first`, `second` went"
    for text in TEXTS:
        assert fold(text) == _before(text) and len(normalize._folds) <= 2
        assert text_facts.fold(text) == _before_text_facts(text) and len(text_facts._folds) <= 2


def test_the_lane_counts_both_folds_and_its_pass_end_empties_them() -> None:
    for text in TEXTS:
        normalize.fold(text)
        text_facts.fold(text)
    assert incremental_lane.memo_entries()["folds"] == 2 * len(TEXTS)

    text_facts.clear_body_caches()
    assert len(normalize._folds) == len(text_facts._folds) == len(TEXTS), (
        "the body readers' clear leaves the folds to the lane")

    incremental_lane.forget_bodies()
    assert normalize._folds == {} and text_facts._folds == {}
    assert incremental_lane.memo_entries()["folds"] == 0
    assert fold(TEXTS[0]) == _before(TEXTS[0]), "a fold after the clear is the same string"


def test_a_lane_pass_folds_into_the_memo_and_leaves_it_empty(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    world = lane_world.world()
    lane_world.seed_lane(world, tmp_path)
    incremental_lane.forget_bodies()
    held_at_clear: list[int] = []
    forget, scoring = incremental_lane.forget_bodies, incremental_lane.run_pass_bounded

    def spy() -> None:
        held_at_clear.append(incremental_lane.memo_entries()["folds"])
        forget()

    def reads_bodies(*args: Any, **kwargs: Any) -> Any:
        for text in TEXTS:
            fact_text(text)
            text_facts.reference_codes(text)
        return scoring(*args, **kwargs)

    monkeypatch.setattr(incremental_lane, "forget_bodies", spy)
    monkeypatch.setattr(incremental_lane, "run_pass_bounded", reads_bodies)

    out = run_incremental(lambda: world)

    assert out["aborted"] == "" and out.get("skipped") is None
    assert held_at_clear and held_at_clear[0] >= 2 * len(TEXTS)
    assert out["memo_entries"]["folds"] == held_at_clear[0]
    assert normalize._folds == {} and text_facts._folds == {}


# ------------------------------------------------------------------ parity over real bodies


class _Forgetful(dict):
    """A memo that keeps nothing: every fold is computed afresh."""

    def __setitem__(self, key: str, value: str) -> None:
        pass


def _corpus() -> list[Listing]:
    rows = json.loads(
        (HERE / "data_s15_trial_examples.json").read_text(encoding="utf-8"))["listings"]
    listings = [Listing.from_json(row) for row in rows.values()]
    extra = (lane_world.DESCRIPTION * 4, *TEXTS, APART)
    return listings + [Listing(id=-n, block="", description=text)
                       for n, text in enumerate(extra, start=1)]


def _clear_readings() -> None:
    """Every memo but the folds, so each reading is recomputed from the folds."""
    text_facts.clear_body_caches()
    text_facts._house_number_patterns.cache_clear()
    indistinguishable._SHINGLE_MEMO.clear()


def _readings(listings: list[Listing], settings: Settings) -> list[Any]:
    out: list[Any] = []
    for listing in listings:
        body = listing.description or ""
        for reader in text_facts.BODY_READERS:
            _, *rest = inspect.signature(reader).parameters.values()
            out.append(reader(body, *(_ARGUMENTS[param.annotation] for param in rest)))
        out.extend(reader(body) for reader in UNCACHED_READERS)
        out.append(build_fingerprint(listing, (), settings))
    for a, b in combinations(listings, 2):
        out.append((prose_obec_conflict(a, b), _development_pair(a, b),
                    distinguishing_facts(a, b, None, settings, CLUSTER)))
    return out


@pytest.mark.parametrize("row", ("w31", "w32w"))
def test_every_reading_is_the_same_unmemoised_cold_warm_and_at_a_cap_of_one(
        row: str, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings.from_json(HERE.parents[1] / "autodedup/settings" / f"{row}.json")
    listings = _corpus()

    incremental_lane.forget_bodies()
    with monkeypatch.context() as unmemoised:
        unmemoised.setattr(normalize, "_folds", _Forgetful())
        unmemoised.setattr(text_facts, "_folds", _Forgetful())
        unmemoised.setattr(normalize, "_fold_now", _before)
        unmemoised.setattr(text_facts, "_fold_now", _before_text_facts)
        expected = _readings(listings, settings)
    assert normalize._folds == {} and text_facts._folds == {}

    runs: dict[str, list[Any]] = {}
    for cap in (normalize.FOLD_MEMO_CAP, 1):
        monkeypatch.setattr(normalize, "FOLD_MEMO_CAP", cap)
        incremental_lane.forget_bodies()
        runs[f"cold {cap}"] = _readings(listings, settings)
        assert 0 < len(normalize._folds) <= cap and 0 < len(text_facts._folds) <= cap
        _clear_readings()
        runs[f"warm {cap}"] = _readings(listings, settings)

    assert {name: run == expected for name, run in runs.items()} == dict.fromkeys(runs, True)
