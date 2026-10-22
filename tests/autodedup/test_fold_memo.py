"""R1: `normalize.fold` folds each distinct text once a pass, to the string it folded before.

The memo is a cost change only: every form built on the fold (`fact_text`, `normalize_text`, the
numeric facts, the disposition, the attribute token) reads the same on a cold memo, a warm one and
the unmemoised formula; the memo is bounded, and the lane's end of a pass empties it with the
body readers (E949).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Iterator

import pytest

from autodedup import incremental_lane, normalize, text_facts
from autodedup.incremental_lane import run_incremental
from autodedup.normalize import (
    attr_token, disposition_norm, fact_text, fact_text_folded, fold, forget_folds,
    normalize_text, numeric_facts,
)
from tests.autodedup import lane_world

_UNMEMOISED_DEACCENT = normalize._deaccent

TEXTS = (
    "Prodej bytu 3+kk, 75 m², Praha 3 – Žižkov, cena 5 500 000 Kč",
    "Pronájem RD 5+1 v obci Řevnice, přízemí a 2. NP, č. bytu 12, sklep 4 m2",
    "ÚŽASNÝ BYT S TERASOU — Čeněk Šťastný, ulice Ďáblická 1234/5, jednotka č. 7",
    "Cena\u00a012\u00a0500\u00a0Kč/měs.\u00a0+ energie,\u00a0\u00a03.\u00a0patro",
    "Garsoniéra 28m2 ve 4. podlaží, Ústí nad Labem, ev. č. 657349",
    "Velmi dobrý",
)


# Every form built on the fold, beside the fold itself.
FORMS: tuple[Callable[[str], Any], ...] = (
    fact_text, normalize_text, numeric_facts, disposition_norm, attr_token)


def _before(s: str | None) -> str:
    """`fold` as it was before R1."""
    return _UNMEMOISED_DEACCENT(s).lower() if s else ""


@pytest.fixture(autouse=True)
def _empty_memo() -> Iterator[None]:
    forget_folds()
    yield
    forget_folds()


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
        forget_folds()
        cold.append(form(text))
    warm = [form(text) for form in (fold, *FORMS)]

    assert cold == expected and warm == expected


def test_none_and_empty_fold_to_empty_and_never_enter_the_memo() -> None:
    assert fold(None) == "" and fold("") == ""
    assert fact_text(None) == "" and normalize_text("") == ""
    assert numeric_facts("") == set() and disposition_norm(None) is None
    assert attr_token(None) == "" and attr_token("") == ""
    assert normalize._folds == {}


def test_each_distinct_text_is_deaccented_once(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def counting(value: str) -> str:
        calls.append(value)
        return _UNMEMOISED_DEACCENT(value)

    monkeypatch.setattr(normalize, "_deaccent", counting)
    for _ in range(3):
        for text in TEXTS:
            fold(text)
            fact_text(text)
            normalize_text(text)

    assert calls == list(TEXTS)
    assert len(normalize._folds) == len(TEXTS)
    assert fold(TEXTS[1]) is fold(TEXTS[1]), "a second fold is the stored string"


def test_a_text_that_folds_to_nothing_is_still_folded_once(
        monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def counting(value: str) -> str:
        calls.append(value)
        return _UNMEMOISED_DEACCENT(value)

    monkeypatch.setattr(normalize, "_deaccent", counting)
    combining_only = "\u0301\u030c"

    assert fold(combining_only) == "" and fold(combining_only) == ""
    assert calls == [combining_only]


def test_the_memo_is_bounded_and_starts_over_at_its_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(normalize, "FOLD_MEMO_CAP", 2)
    for text in TEXTS:
        assert fold(text) == _before(text)
        assert len(normalize._folds) <= 2
    for text in TEXTS:
        assert fold(text) == _before(text)


def test_forget_folds_and_the_body_readers_clear_empty_it() -> None:
    for text in TEXTS:
        fold(text)
    assert len(normalize._folds) == len(TEXTS)
    forget_folds()
    assert normalize._folds == {}

    fold(TEXTS[0])
    text_facts.clear_body_caches()
    assert normalize._folds == {}

    fold(TEXTS[0])
    incremental_lane.forget_bodies()
    assert normalize._folds == {}
    assert fold(TEXTS[0]) == _before(TEXTS[0]), "a fold after the clear is the same string"


def test_a_lane_pass_folds_into_the_memo_and_leaves_it_empty(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    world = lane_world.world()
    lane_world.seed_lane(world, tmp_path)
    incremental_lane.forget_bodies()
    held_at_clear: list[int] = []
    forget, scoring = incremental_lane.forget_bodies, incremental_lane.run_pass_bounded

    def spy() -> None:
        held_at_clear.append(len(normalize._folds))
        forget()

    def reads_bodies(*args: Any, **kwargs: Any) -> Any:
        for text in TEXTS:
            fact_text(text)
        return scoring(*args, **kwargs)

    monkeypatch.setattr(incremental_lane, "forget_bodies", spy)
    monkeypatch.setattr(incremental_lane, "run_pass_bounded", reads_bodies)

    out = run_incremental(lambda: world)

    assert out["aborted"] == "" and out.get("skipped") is None
    assert held_at_clear and held_at_clear[0] >= len(TEXTS)
    assert normalize._folds == {}
