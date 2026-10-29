"""The registry demo (#2062): `list` shows every tool with no database; `call` runs one and prints its envelope."""

from __future__ import annotations

import contextlib
import json
from typing import Any

from api.capabilities import registry
from scripts import capabilities_demo


def test_list_prints_every_tool_with_its_tenancy_and_effect(capsys: Any) -> None:
    assert capabilities_demo.main(["list"]) == 0
    out = capsys.readouterr().out
    for entry in registry.ENTRIES.values():
        assert f"{entry.name}  [{entry.tenancy}, {entry.effect}]" in out


def test_call_prints_the_envelope_or_the_error(monkeypatch: Any, capsys: Any) -> None:
    monkeypatch.setattr("scraper.db.connect", lambda: contextlib.nullcontext(object()))
    monkeypatch.setattr(registry, "call", lambda caller, name, args: {"data": [args], "metadata": {"tool": name}})
    assert capabilities_demo.main(["call", "find_comparables_relaxed", '{"property_id": 1}']) == 0
    assert json.loads(capsys.readouterr().out) == {"data": [{"property_id": 1}],
                                                   "metadata": {"tool": "find_comparables_relaxed"}}
    monkeypatch.undo()
    monkeypatch.setattr("scraper.db.connect", lambda: contextlib.nullcontext(object()))
    assert capabilities_demo.main(["call", "get_my_property_context"]) == 1
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "forbidden"
