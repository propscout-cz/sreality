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
from api.providers import (
    Completion,
    HostedCode,
    HostedFile,
    Message,
    OpaqueBlock,
    ProviderError,
    ToolCall,
    ToolSchema,
    Usage,
)
from tests.api._fakes import _ScriptedProvider

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


class ScriptedCodeProvider(_ScriptedProvider):
    """The stand-in for Claude's hosted code: scripted replies, and a fake file for each id a test gives it."""

    def __init__(self, completions: list[Completion], *, files: dict[str, HostedFile | Exception] | None = None,
                 prices: dict[str, Any] | None = None, name: str = "anthropic") -> None:
        super().__init__(name, completions, prices=prices)
        self.files = dict(files or {})
        self.hosted: list[HostedCode | None] = []
        self.uploaded: list[tuple[str, str, bytes, int]] = []
        self.deleted: list[str] = []
        self.refuse_delete: Exception | None = None

    def complete(self, *, system: str, messages: list[Message], tools: list[ToolSchema], model: str,
                 max_tokens: int = 4096, tool_choice: str | None = None, hosted_code: HostedCode | None = None) -> Completion:
        self.hosted.append(hosted_code)
        return super().complete(system=system, messages=messages, tools=tools, model=model, max_tokens=max_tokens,
                                tool_choice=tool_choice)

    def upload_file(self, *, name: str, media_type: str, data: bytes, expires_in_s: int) -> str:
        self.uploaded.append((name, media_type, data, expires_in_s))
        return f"file_up_{len(self.uploaded)}"

    def input_block(self, file_id: str) -> OpaqueBlock:
        return OpaqueBlock(provider=self.name, data={"type": "container_upload", "file_id": file_id})

    def download_file(self, file_id: str, *, max_bytes: int) -> HostedFile:
        found = self.files[file_id]
        if isinstance(found, Exception):
            raise found
        if len(found.data) > max_bytes:
            raise ProviderError(f"{file_id} is over {max_bytes} bytes")
        return found

    def delete_file(self, file_id: str) -> None:
        self.deleted.append(file_id)
        if self.refuse_delete:
            raise self.refuse_delete

    def container_cost_usd(self, seconds: float) -> float:
        return 0.0


def code_reply(*calls: tuple[str, dict[str, Any]], files: tuple[str, ...] = (), container: str | None = "cntr_1",
               stop: str | None = None, usage: tuple[int, int] = (100, 50), hosted_usd: float = 0.0,
               text: str = "") -> Completion:
    """A reply from a code-enabled model: the calls it ended on, the files its code left, the container it ran in."""
    return Completion(
        text_blocks=[text] if text else [],
        tool_calls=[ToolCall(id=f"call_{next(_ids)}", name=name, input=args) for name, args in calls],
        stop_reason=stop or ("tool_use" if calls else "end_turn"),
        usage=Usage(input_tokens=usage[0], output_tokens=usage[1], code_execution_requests=1 if container else 0,
                    hosted_code_usd=hosted_usd),
        model="claude-opus-5-5",
        hosted_calls=(ToolCall(id="srvtoolu_1", name="bash_code_execution", input={"command": "python x.py"}),) if container else (),
        container=container,
        file_ids=files,
    )
