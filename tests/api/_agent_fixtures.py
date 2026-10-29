"""Probe tools, a probe agent folder and scripted replies for the agent loader, loop and runner tests."""

from __future__ import annotations

import itertools
from pathlib import Path
from types import MappingProxyType
from typing import Any

import pytest
import yaml
from pydantic import Field

from api.capabilities import registry
from api.capabilities.entry import Effect, Entry, Tenancy
from api.filter_args import Args
from api.providers import Completion, ToolCall, Usage

READS = ("probe_a", "probe_b", "probe_c")
WRITE = "probe_note"
_ids = itertools.count(1)


class _ReadArgs(Args):
    q: int = Field(0, description="A number.")


class _NoteArgs(Args):
    property_id: int = Field(description="The property the note is on.")
    text: str = Field(min_length=1, description="The note.")


def _unreachable(ctx: Any, args: Any) -> dict[str, Any]:
    raise AssertionError("the loop reaches tools only through call_tool")


def install_probes(monkeypatch: pytest.MonkeyPatch) -> None:
    entries = {name: Entry(name=name, description=f"Probe {name}.", args=_ReadArgs, handler=_unreachable,
                           tenancy=Tenancy.MARKET, effect=Effect.READ) for name in READS}
    entries[WRITE] = Entry(name=WRITE, description="Add a note to a property.", args=_NoteArgs,
                           handler=_unreachable, tenancy=Tenancy.TENANT, effect=Effect.WRITE)
    monkeypatch.setattr(registry, "ENTRIES", MappingProxyType(entries))


def frontmatter(agent: str, **overrides: Any) -> dict[str, Any]:
    meta: dict[str, Any] = {
        "name": agent,
        "description": "A probe agent.",
        "preferred_model": {"anthropic": "claude-sonnet-4-5"},
        "allowed_tools": list(READS),
        "limits": {"max_iterations": 6, "max_cost_usd": 1.0, "wall_clock_timeout_s": 60},
        "input": {"type": "object", "additionalProperties": False, "required": ["town"],
                  "properties": {"town": {"type": "string", "description": "The town to look in."}}},
    }
    meta.update(overrides)
    return {k: v for k, v in meta.items() if v is not None}


def write_folder(root: Path, name: str = "probe_scout_v1", *, meta: dict[str, Any] | None = None,
                 body: str = "Find properties and cite them.", references: dict[str, str] | None = None) -> Path:
    folder = root / name
    folder.mkdir(parents=True)
    front = yaml.safe_dump(meta if meta is not None else frontmatter(name), sort_keys=False, allow_unicode=True)
    (folder / "SKILL.md").write_text(f"---\n{front}---\n\n{body}\n", encoding="utf-8")
    for path, text in (references or {}).items():
        (folder / "references").mkdir(exist_ok=True)
        (folder / "references" / path).write_text(text, encoding="utf-8")
    return folder


def reply(*calls: tuple[str, dict[str, Any]], text: str = "", stop: str | None = None,
          usage: tuple[int, int] = (100, 50), model: str = "claude-sonnet-4-5") -> Completion:
    return Completion(
        text_blocks=[text] if text else [],
        tool_calls=[ToolCall(id=f"call_{next(_ids)}", name=name, input=args) for name, args in calls],
        stop_reason=stop or ("tool_use" if calls else "end_turn"),
        usage=Usage(input_tokens=usage[0], output_tokens=usage[1]),
        model=model,
        raw=None,
    )
