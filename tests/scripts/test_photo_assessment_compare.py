"""The two-model comparison, through stand-in models and photos: it pays for no real call, stops at its limit, writes
nothing to the cache, and reports agreement per facet in words the operator can read."""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import Any

import pytest

from api import dependencies
from api.providers import ModelPrice
from scraper import db, image_storage
from scripts import photo_assessment_compare as compare
from tests.api._agent_fixtures import reply
from tests.api._fakes import _ScriptedProvider
from tests.api._script_conn import ScriptConn
from toolkit import photo_assessment as pa

OPUS, SONNET = compare.DEFAULT_MODELS
PRICES = {OPUS: ModelPrice(4.0, 20.0, 0.2, 5.0), SONNET: ModelPrice(2.0, 10.0, 0.2, 2.5)}
FACETS = pa.load_facets().keys


def _said(score: int | None = 3, **others: int | None) -> dict[str, Any]:
    return {"facets": {k: {"score": others.get(k, score), "evidence": f"{k}: viděno"} for k in FACETS}}


class _R2:
    def download_bytes(self, key: str) -> bytes:
        return b"bytes"


class _ByModel(_ScriptedProvider):
    """Answers each model from its own script, as the two real models would."""

    def __init__(self, answers: dict[str, list[Any]]) -> None:
        super().__init__("anthropic", [], prices=PRICES)
        self.answers = answers

    def complete(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return self.answers[kwargs["model"]].pop(0)


def _world(monkeypatch: pytest.MonkeyPatch, answers: dict[str, list[Any]], ids: list[int]) -> tuple[_ByModel, ScriptConn]:
    provider = _ByModel(answers)
    conn = ScriptConn([(lambda sql: "md5(p.id::text)" in sql, [(i,) for i in ids]),
                       (lambda sql: sql.startswith("INSERT INTO llm_calls"), [(1,)]),
                       (lambda sql: "llm_cost_today_usd" in sql, [(0,)])])
    monkeypatch.setattr(dependencies, "get_providers", lambda: {"anthropic": provider})
    monkeypatch.setattr(db, "connect", lambda: contextlib.nullcontext(conn))
    monkeypatch.setattr(image_storage, "is_configured", lambda: True)
    monkeypatch.setattr(image_storage.R2Client, "from_env", staticmethod(lambda: _R2()))
    monkeypatch.setattr(pa, "photo_set", lambda c, pid: pa.PhotoSet(
        property_id=pid, listing_id=pid, stored=1, pending=0, plans=(),
        photos=(pa.Photo(pid, f"img/{pid}.jpg", None, 800, 600),)))
    return provider, conn


def _reply(payload: dict[str, Any], model: str) -> Any:
    return reply((pa.TOOL_NAME, payload), usage=(10_000, 1_000), model=model)


def test_it_judges_each_property_on_both_models_and_writes_a_report_with_no_cache_row(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    far = dict.fromkeys(FACETS[:1], 1)
    answers = {OPUS: [_reply(_said(5, **far), OPUS), _reply(_said(3), OPUS)],
               SONNET: [_reply(_said(5, **dict.fromkeys(FACETS[:1], 5)), SONNET), _reply(_said(4), SONNET)]}
    provider, conn = _world(monkeypatch, answers, [11, 12])
    out = tmp_path / "comparison.md"
    assert compare.main(["--out", str(out)]) == 0
    assert [c["model"] for c in provider.calls] == [OPUS, SONNET, OPUS, SONNET]
    assert not [sql for sql, _ in conn.executed if "photo_set_assessments" in sql]
    text = out.read_text(encoding="utf-8")
    assert f"# Photo assessment: {OPUS} against {SONNET}" in text
    for facet in FACETS:
        assert f"| {facet} |" in text
    assert f"Property 11, {FACETS[0]}" in text and f"{FACETS[0]}: viděno" in text
    assert "What it cost" in text and "2 properties were tried and 2 were judged by both models" in text
    assert compare._SAMPLE_SQL.count("settled_at") == 1, "the sample is what Browse may serve"


def test_the_limit_stops_it_before_the_money_and_says_so(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    answers = {OPUS: [_reply(_said(), OPUS)], SONNET: [_reply(_said(), SONNET)]}
    provider, _ = _world(monkeypatch, answers, [11, 12, 13])
    out = tmp_path / "comparison.md"
    # One property's two worst cases fit; a second's, on top of what the first really cost, do not.
    limit = sum(pa.worst_case_usd(pa.photo_set(None, 11), pa.load_facets(), PRICES[m]) for m in compare.DEFAULT_MODELS)
    assert compare.main(["--out", str(out), "--max-usd", f"{limit + 0.01:.4f}"]) == 0
    assert len(provider.calls) == 2
    assert "stopped early" in out.read_text(encoding="utf-8")


def test_a_model_that_fails_one_property_leaves_it_out_of_the_agreement_and_names_it(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    answers = {OPUS: [_reply({"facets": {}}, OPUS), _reply(_said(), OPUS)], SONNET: [_reply(_said(), SONNET), _reply(_said(), SONNET)]}
    _world(monkeypatch, answers, [11, 12])
    out = tmp_path / "comparison.md"
    assert compare.main(["--out", str(out)]) == 0
    text = out.read_text(encoding="utf-8")
    assert "Property 11 on claude-opus-5-5" in text and "1 were judged by both models" in text


def test_named_properties_replace_the_sample(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    answers = {OPUS: [_reply(_said(), OPUS)], SONNET: [_reply(_said(), SONNET)]}
    provider, conn = _world(monkeypatch, answers, [99])
    assert compare.main(["--out", str(tmp_path / "r.md"), "--property-id", "7"]) == 0
    assert len(provider.calls) == 2 and not [sql for sql, _ in conn.executed if "md5" in sql]


@pytest.mark.parametrize("gaps, says", [
    ({}, "They agree"),
    ({"view": 1}, "They do not agree enough"),
])
def test_the_verdict_reads_as_a_sentence_and_names_the_cheaper_model(gaps: dict[str, int], says: str) -> None:
    keys = ("view", "kitchen")
    samples = [compare.Sample(i, judged={
        OPUS: {k: {"score": 5, "evidence": "a"} for k in keys},
        SONNET: {k: {"score": gaps.get(k, 5), "evidence": "b"} for k in keys},
    }) for i in range(10)]
    text = compare.report(samples, facets=keys, models=(OPUS, SONNET), version="v", costs={OPUS: 1.0, SONNET: 0.5},
                          cheaper=SONNET)
    assert says in text
    assert (SONNET + ". Read") in text if says == "They agree" else "stays as it is" in text


def test_a_facet_one_model_could_not_see_counts_as_a_disagreement() -> None:
    samples = [compare.Sample(1, judged={OPUS: {"view": {"score": None, "evidence": "a"}},
                                               SONNET: {"view": {"score": 3, "evidence": "b"}}})]
    row = compare.facet_agreement(samples, ("view",), (OPUS, SONNET))[0]
    assert row.one_sided == 1 and row.agreement == 0
    both_blind = [compare.Sample(1, judged={OPUS: {"view": {"score": None, "evidence": "a"}},
                                                  SONNET: {"view": {"score": None, "evidence": "b"}}})]
    assert compare.facet_agreement(both_blind, ("view",), (OPUS, SONNET))[0].agreement == 1


def test_a_report_it_cannot_write_spends_nothing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    provider, _ = _world(monkeypatch, {OPUS: [], SONNET: []}, [11])
    blocked = tmp_path / "a-file"
    blocked.write_text("")
    assert compare.main(["--out", str(blocked / "report.md")]) == 2
    assert provider.calls == []


def test_a_crash_keeps_what_was_paid_for(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    answers = {OPUS: [_reply(_said(), OPUS)], SONNET: [_reply(_said(), SONNET)]}
    _world(monkeypatch, answers, [11, 12])
    out = tmp_path / "comparison.md"
    with pytest.raises(IndexError):  # the third property's call finds its script empty
        compare.main(["--out", str(out)])
    assert "1 were judged by both models" in out.read_text(encoding="utf-8")


def test_nothing_judged_by_both_says_it_answers_nothing() -> None:
    assert "says nothing" in compare.report([], facets=("view",), models=(OPUS, SONNET), version="v", costs={}, cheaper=SONNET)
