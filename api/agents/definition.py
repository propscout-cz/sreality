"""An agent's definition: one folder in skills/, loaded from the image and checked before anything runs."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from api import dependencies
from api.capabilities import registry
from api.providers import CompletionProvider

SKILLS_DIR = Path(__file__).resolve().parents[2] / "skills"

_NAME = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_KEYS = frozenset({"name", "description", "preferred_model", "allowed_tools", "limits", "input", "output"})
_OPTIONAL = frozenset({"output"})
_FENCE = "---\n"


class DefinitionError(ValueError):
    """A definition that cannot run, in words its author can act on."""


class InputError(ValueError):
    """A run input its agent's input schema refuses."""


@dataclass(frozen=True)
class Limits:
    max_iterations: int
    max_cost_usd: float
    wall_clock_timeout_s: float


@dataclass(frozen=True)
class Definition:
    """The agent a folder declares. `instructions` is the body with its references; `sha` covers every file."""

    name: str
    description: str
    instructions: str
    tools: tuple[str, ...]
    models: Mapping[str, str]
    limits: Limits
    input_schema: Mapping[str, Any]
    output_schema: Mapping[str, Any] | None
    sha: str

    def check_input(self, value: object) -> None:
        errors = sorted(Draft202012Validator(self.input_schema).iter_errors(value), key=lambda e: list(e.path))
        if errors:
            raise InputError("; ".join(f"{'.'.join(map(str, e.path)) or 'input'}: {e.message}" for e in errors))


# --- the checks, shared with the estimator's skills table until M2 deletes it ---------------------------------------

def text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DefinitionError(f"{label} must be a non-empty string")
    return value


def tool_names(value: Any, known: Collection[str]) -> list[str]:
    if not isinstance(value, list) or not value:
        raise DefinitionError("allowed_tools must be a non-empty list of tool names")
    out: list[str] = []
    for entry in value:
        if not isinstance(entry, str):
            raise DefinitionError("allowed_tools entries must be strings")
        if entry not in known:
            raise DefinitionError(f"unknown tool {entry!r}; registered tools: {sorted(known)}")
        if entry in out:
            raise DefinitionError(f"tool {entry!r} is listed twice")
        out.append(entry)
    return out


def models(value: Any, providers: Mapping[str, CompletionProvider]) -> dict[str, str]:
    """A non-empty {provider: model} map; every model priced, or a run on it could not be capped by cost."""
    if not isinstance(value, dict) or not value:
        raise DefinitionError("preferred_model must be a {provider: model} map")
    out: dict[str, str] = {}
    for provider, model in value.items():
        if not isinstance(provider, str) or not isinstance(model, str):
            raise DefinitionError("preferred_model entries must be string -> string")
        if provider not in providers:
            raise DefinitionError(f"unknown provider {provider!r}; registered providers: {sorted(providers)}")
        if providers[provider].price_for(model) is None:
            raise DefinitionError(f"{provider} has no price for {model!r}, so its cost could not be capped")
        out[provider] = model
    return out


def limits(value: Any) -> Limits:
    if not isinstance(value, dict):
        raise DefinitionError("limits must be an object")
    max_iter = value.get("max_iterations")
    if not isinstance(max_iter, int) or isinstance(max_iter, bool) or not (1 <= max_iter <= 50):
        raise DefinitionError("limits.max_iterations must be an int in [1, 50]")
    max_cost = value.get("max_cost_usd")
    if not isinstance(max_cost, (int, float)) or isinstance(max_cost, bool) or not (0 < max_cost <= 20):
        raise DefinitionError("limits.max_cost_usd must be a number in (0, 20]")
    wall = value.get("wall_clock_timeout_s")
    if not isinstance(wall, (int, float)) or isinstance(wall, bool) or not (1 <= wall <= 600):
        raise DefinitionError("limits.wall_clock_timeout_s must be a number in [1, 600]")
    return Limits(max_iterations=max_iter, max_cost_usd=float(max_cost), wall_clock_timeout_s=float(wall))


def _schema(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise DefinitionError(f"{label} must be a JSON Schema object")
    try:
        Draft202012Validator.check_schema(value)
    except SchemaError as exc:
        raise DefinitionError(f"{label} is not a valid JSON Schema: {exc.message}") from None
    if value.get("type") != "object" or value.get("additionalProperties") is not False:
        raise DefinitionError(f"{label} must be an object that refuses unknown keys "
                              "(type: object, additionalProperties: false)")
    return value


# --- loading ---------------------------------------------------------------------------------------------------------

def _split(raw: str) -> tuple[Any, str]:
    if not raw.startswith(_FENCE):
        raise DefinitionError("SKILL.md must open with a --- frontmatter block")
    front, sep, body = raw[len(_FENCE):].partition("\n" + _FENCE)
    if not sep:
        raise DefinitionError("the frontmatter block is not closed by a --- line")
    try:
        return yaml.safe_load(front), body.strip()
    except yaml.YAMLError as exc:
        raise DefinitionError(f"the frontmatter is not valid YAML: {exc}") from None


def _parse(folder: Path) -> Definition:
    files = sorted(p for p in folder.rglob("*") if p.is_file())
    paths = [p.relative_to(folder).as_posix() for p in files]
    stray = [p for p in paths if p != "SKILL.md" and not p.startswith("references/")]
    if stray:
        raise DefinitionError(f"an agent folder holds SKILL.md and references/ only, not {stray}")
    if "SKILL.md" not in paths:
        raise DefinitionError("no SKILL.md")
    contents = {path: file.read_bytes() for path, file in zip(paths, files)}
    try:
        texts = {path: data.decode("utf-8") for path, data in contents.items()}
    except UnicodeDecodeError:
        raise DefinitionError("every file in an agent folder must be UTF-8 text") from None

    meta, body = _split(texts.pop("SKILL.md"))
    if not isinstance(meta, dict):
        raise DefinitionError("the frontmatter must be a mapping")
    if unknown := sorted(set(meta) - _KEYS):
        raise DefinitionError(f"unknown frontmatter keys {unknown}; an agent declares {sorted(_KEYS)}")
    if missing := sorted(_KEYS - _OPTIONAL - set(meta)):
        raise DefinitionError(f"missing frontmatter keys {missing}")
    name = text(meta["name"], "name")
    if name != folder.name or not _NAME.match(name):
        raise DefinitionError(f"name {name!r} must equal the folder name and match {_NAME.pattern}")

    sha = hashlib.sha256()
    for path, data in contents.items():
        sha.update(f"{json.dumps(path)}:{len(data)}:".encode() + data)
    references = "".join(f"\n\n## {path}\n\n{content.strip()}" for path, content in texts.items() if content.strip())
    return Definition(
        name=name,
        description=text(meta["description"], "description"),
        instructions=text(body, "the skill body") + references,
        tools=tuple(tool_names(meta["allowed_tools"], registry.ENTRIES)),
        models=models(meta["preferred_model"], dependencies.get_providers()),
        limits=limits(meta["limits"]),
        input_schema=_schema(meta["input"], "input"),
        output_schema=_schema(meta["output"], "output") if "output" in meta else None,
        sha=sha.hexdigest(),
    )


def load(folder: Path) -> Definition:
    """The agent `folder` declares, or a DefinitionError that names the folder and what is wrong with it."""
    try:
        return _parse(folder)
    except DefinitionError as exc:
        raise DefinitionError(f"{folder.name}: {exc}") from None


def load_all(root: Path = SKILLS_DIR) -> dict[str, Definition]:
    """Every agent under `root`: each folder must load; a file beside the folders (the README) is not an agent."""
    return {folder.name: load(folder) for folder in sorted(p for p in root.iterdir() if p.is_dir())}
