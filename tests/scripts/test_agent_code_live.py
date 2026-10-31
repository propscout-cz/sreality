"""The live code-turn script, through the stand-in: one real turn's path without a model, its file on disk."""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import Any

import pytest

from api import dependencies
from api.agents import terminator
from api.providers import HostedFile, ModelPrice
from scraper import db
from scripts import agent_code_live
from tests.api._agent_fixtures import ScriptedCodeProvider, code_reply
from tests.api._fakes import _FakeConn

SUBMIT = terminator.NAME
MADE = HostedFile(name=agent_code_live.WORKBOOK, media_type="application/x-zip", data=b"PK a fake workbook")
OPUS = ModelPrice(4.0, 20.0, 0.2, 5.0)


def _world(monkeypatch: pytest.MonkeyPatch, *replies: Any, files: dict[str, Any] | None = None) -> ScriptedCodeProvider:
    provider = ScriptedCodeProvider(list(replies), files=files or {}, prices={agent_code_live.MODEL: OPUS})
    monkeypatch.setattr(dependencies, "get_providers", lambda: {"anthropic": provider})
    monkeypatch.setattr(db, "connect", lambda: contextlib.nullcontext(_FakeConn()))
    return provider


def test_it_uploads_one_input_saves_the_workbook_and_clears_the_provider(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    answer = (SUBMIT, {"markdown": "Total 59400.", "files": [agent_code_live.WORKBOOK]})
    provider = _world(monkeypatch, code_reply(stop="pause_turn"), code_reply(answer, files=("f",)), files={"f": MADE})
    assert agent_code_live.main(["--out", str(tmp_path / "out")]) == 0
    assert (tmp_path / "out" / agent_code_live.WORKBOOK).read_bytes() == MADE.data
    assert provider.uploaded[0][0] == "rents.csv" and b"Praha 7,31000" in provider.uploaded[0][2]
    assert provider.deleted == ["file_up_1", "f"]
    out = capsys.readouterr()
    assert "submit_result after 2 model calls" in out.err and f"should read {agent_code_live.EXPECTED_TOTAL}" in out.out


def test_it_fails_when_no_file_came_back(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _world(monkeypatch, code_reply((SUBMIT, {"markdown": "No file."})))
    assert agent_code_live.main(["--out", str(tmp_path)]) == 1


def test_a_cap_too_small_for_a_code_call_spends_nothing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    provider = _world(monkeypatch)
    assert agent_code_live.main(["--max-usd", "0.5", "--out", str(tmp_path)]) == 1
    assert provider.calls == []


def test_the_default_cap_buys_a_call_on_the_default_model(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    provider = _world(monkeypatch, code_reply((SUBMIT, {"markdown": "No file."})))
    agent_code_live.main(["--out", str(tmp_path)])
    assert provider.calls and provider.calls[0]["max_tokens"] >= 4_096


@pytest.mark.parametrize("cap", ["0", "-1", "5.01"])
def test_the_cap_has_bounds(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, cap: str) -> None:
    provider = _world(monkeypatch)
    assert agent_code_live.main(["--max-usd", cap, "--out", str(tmp_path)]) == 2
    assert provider.calls == []
