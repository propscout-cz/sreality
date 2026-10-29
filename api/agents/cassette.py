"""A run's journal saved as a file (a cassette): the model replies and tool results a replay serves back in order."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import asdict
from pathlib import Path
from typing import Any

from api.agents.definition import Definition
from api.agents.loop import Outcome, Step
from api.capabilities.truncate import encode
from api.providers import ModelPrice

VERSION = 1


def plain(value: Any) -> Any:
    """`value` as JSON reads it back, so a recorded outcome and a replayed one compare equal."""
    return json.loads(encode(value))


def dump(path: Path, *, definition: Definition, provider: str, model: str, price: ModelPrice,
         value: Mapping[str, Any], steps: Iterable[Step], outcome: Outcome | None) -> None:
    """Write the cassette: the definition it ran, the model and its price, the input, every step, and the outcome
    (None for a run that raised: an incomplete cassette, kept for reading, never replayed)."""
    document = {
        "version": VERSION,
        "agent": definition.name,
        "definition_sha": definition.sha,
        "provider": provider,
        "model": model,
        "price": asdict(price),
        "input": value,
        "steps": [asdict(step) for step in steps],
        "outcome": asdict(outcome) if outcome is not None else None,
    }
    path.write_text(json.dumps(plain(document), ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def load(path: Path) -> dict[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if document.get("version") != VERSION:
        raise ValueError(f"{path}: cassette version {document.get('version')!r}; this code reads {VERSION}")
    return document
