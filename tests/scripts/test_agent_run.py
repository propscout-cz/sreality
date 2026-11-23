"""The local runner: one agent folder, as one user, read-only; its cassette replays to the same outcome."""

from __future__ import annotations

import contextlib
import json
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any

import pytest

from api import dependencies, tenant_pool
from api.agents import cassette, terminator
from api.agents.definition import load
from api.capabilities import registry
from api.capabilities.errors import ToolError, ToolErrorCode
from scraper import db
from scripts import agent_run
from api.providers import HostedFile, ModelPrice
from tests.api._agent_fixtures import ScriptedCodeProvider, code_reply, frontmatter, install_probes, reply, write_folder
from tests.api._agent_replay import replay
from tests.api._fakes import _FakeConn, _ScriptedProvider

USER = uuid.UUID("00000000-0000-4000-8000-000000000007")
ACCOUNT = uuid.UUID("00000000-0000-4000-8000-000000000008")


@pytest.fixture()
def world(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Probe tools, a scripted model and a fake database; `calls` keeps every registry call the runner made."""
    install_probes(monkeypatch)
    state: dict[str, Any] = {"calls": [], "conn": _FakeConn(), "account": ACCOUNT}
    state["provider"] = _ScriptedProvider("anthropic", [
        reply(("probe_a", {"q": 1}), text="Looking."),
        reply(("probe_b", {}), ("probe_c", {"q": 3})),
        reply((terminator.NAME, {"markdown": "# Shortlist\n\n1. Property 7", "cited_property_ids": [7]})),
    ])
    results = {"probe_a": {"data": [], "metadata": {"result_count": 0}},
               "probe_b": {"data": [{"property_id": 7, "price_czk": 5_400_000}]},
               "probe_c": ToolError(ToolErrorCode.BUSY, "retry")}

    def call(caller: Any, name: str, args: Any) -> dict[str, Any]:
        state["calls"].append((caller, name, args))
        if isinstance(results[name], ToolError):
            raise results[name]
        return results[name]

    monkeypatch.setattr(dependencies, "get_providers", lambda: {"anthropic": state["provider"]})
    monkeypatch.setattr(db, "connect", lambda: contextlib.nullcontext(state["conn"]))
    monkeypatch.setattr(registry, "call", call)
    monkeypatch.setattr(tenant_pool, "resolve_account_id", lambda conn, claims: state["account"])
    return state


def test_a_recorded_run_replays_to_the_same_outcome(
        world: dict[str, Any], tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    folder = write_folder(tmp_path)
    recording = tmp_path / "run.json"
    code = agent_run.main([str(folder), "--user", str(USER), "--input", '{"town": "Brno"}', "--record", str(recording)])

    assert code == 0
    out = capsys.readouterr()
    assert out.out.startswith("# Shortlist\n\n1. Property 7\n")
    assert json.loads(out.out.split("1. Property 7\n", 1)[1])["cited_property_ids"] == [7]
    assert "submit_result after 3 model calls" in out.err
    caller = world["calls"][0][0]
    assert caller.claims == {"sub": str(USER), "role": "authenticated", "run_account_id": str(ACCOUNT)}
    assert caller.market is world["conn"]
    assert [row["params"][0] for row in world["conn"].llm_calls_rows] == ["agent_run"] * 3

    document = cassette.load(recording)
    assert (document["agent"], document["provider"], document["input"]) == ("probe_scout_v1", "anthropic",
                                                                            {"town": "Brno"})
    assert document["definition_sha"] == load(folder).sha
    replayed = replay(document, load(folder))
    assert cassette.plain(asdict(replayed)) == document["outcome"]
    assert document["outcome"]["stop_reason"] == "submit_result"


def test_a_code_agent_takes_input_files_and_writes_its_answers_files(
        world: dict[str, Any], tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    made = HostedFile(name="model.csv", media_type="text/csv", data=b"a\n1\n")
    world["provider"] = ScriptedCodeProvider([code_reply((terminator.NAME, {"markdown": "Done.", "files": ["model.csv"]}), files=("f",))],
                                             files={"f": made}, prices={"claude-sonnet-4-5": ModelPrice(1.0, 1.0)})
    folder = write_folder(tmp_path, meta=frontmatter("probe_scout_v1", code=True))
    source = tmp_path / "rents.csv"
    source.write_bytes(b"rent\n9\n")
    code = agent_run.main([str(folder), "--user", str(USER), "--input", '{"town": "Brno"}', "--file", str(source),
                           "--out", str(tmp_path / "out")])
    assert code == 0
    assert (tmp_path / "out" / "model.csv").read_bytes() == b"a\n1\n"
    assert world["provider"].uploaded[0][:3] == ("rents.csv", "text/csv", b"rent\n9\n")
    printed = capsys.readouterr().out
    assert json.loads(printed.split("Done.\n", 1)[1])["files"] == [{"name": "model.csv", "media_type": "text/csv", "bytes": 4}]


def test_an_out_folder_it_cannot_make_spends_nothing(
        world: dict[str, Any], tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    blocker = tmp_path / "a-file"
    blocker.write_text("x")
    code = agent_run.main([str(write_folder(tmp_path)), "--user", str(USER), "--input", '{"town": "Brno"}',
                           "--out", str(blocker / "out")])
    assert code == 2
    assert world["provider"].calls == []


def test_an_input_file_for_an_agent_without_code_spends_nothing(
        world: dict[str, Any], tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    source = tmp_path / "rents.csv"
    source.write_bytes(b"rent\n9\n")
    code = agent_run.main([str(write_folder(tmp_path)), "--user", str(USER), "--input", '{"town": "Brno"}', "--file", str(source)])
    assert code == 2
    assert "does not allow code" in capsys.readouterr().err
    assert world["provider"].calls == []


def test_a_missing_input_file_spends_nothing(
        world: dict[str, Any], tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    folder = write_folder(tmp_path, meta=frontmatter("probe_scout_v1", code=True))
    code = agent_run.main([str(folder), "--user", str(USER), "--input", '{"town": "Brno"}',
                           "--file", str(tmp_path / "absent.csv")])
    assert code == 2
    assert "absent.csv" in capsys.readouterr().err
    assert world["provider"].calls == []


def test_a_replay_that_leaves_the_recording_fails_loudly(world: dict[str, Any], tmp_path: Path) -> None:
    folder = write_folder(tmp_path)
    recording = tmp_path / "run.json"
    agent_run.main([str(folder), "--user", str(USER), "--input", '{"town": "Brno"}', "--record", str(recording)])
    document = cassette.load(recording)
    document["steps"][0]["output"]["tool_calls"][0]["input"] = {"q": 2}
    with pytest.raises(AssertionError, match="left the recording"):
        replay(document, load(folder))


def test_a_user_with_no_account_spends_nothing(world: dict[str, Any], tmp_path: Path,
                                               capsys: pytest.CaptureFixture[str]) -> None:
    world["account"] = None
    assert agent_run.main([str(write_folder(tmp_path)), "--user", str(USER), "--input", '{"town": "Brno"}']) == 2
    assert "no account" in capsys.readouterr().err
    assert world["provider"].calls == []


def test_a_bad_input_spends_nothing(world: dict[str, Any], tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = agent_run.main([str(write_folder(tmp_path)), "--user", str(USER), "--input", '{"town": 5}'])
    assert code == 2
    assert "town: 5 is not of type 'string'" in capsys.readouterr().err
    assert world["provider"].calls == [] and world["calls"] == []


def test_a_run_that_raises_keeps_its_paid_steps(world: dict[str, Any], tmp_path: Path) -> None:
    folder = write_folder(tmp_path)
    world["provider"]._queue[1:] = []  # the second model call finds no reply: the provider raises
    recording = tmp_path / "run.json"
    with pytest.raises(RuntimeError, match="exhausted"):
        agent_run.main([str(folder), "--user", str(USER), "--input", '{"town": "Brno"}', "--record", str(recording)])
    document = cassette.load(recording)
    assert [s["kind"] for s in document["steps"]] == ["model", "tool"] and document["outcome"] is None
    with pytest.raises(ValueError, match="incomplete cassette"):
        replay(document, load(folder))


def test_a_provider_the_agent_does_not_name_spends_nothing(
        world: dict[str, Any], tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = agent_run.main([str(write_folder(tmp_path)), "--user", str(USER), "--input", '{"town": "Brno"}',
                           "--provider", "gemini"])
    assert code == 2 and "not gemini" in capsys.readouterr().err and world["provider"].calls == []
