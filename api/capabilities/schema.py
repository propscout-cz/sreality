"""A tool's input schema in the JSON Schema subset every provider accepts."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

_NULL = {"type": "null"}


def portable(model: type[BaseModel]) -> dict[str, Any]:
    """`model`'s JSON Schema with `$ref` inlined, `X | None` as X plus `nullable`, and no titles or null defaults."""
    # Gemini's client refuses $ref/$defs and type arrays; `nullable` is the null every provider reads, and null is
    # not omission (an unset category takes the subject's own, null means every category).
    raw = model.model_json_schema()
    return _clean(raw, raw.pop("$defs", {}))


def _clean(node: Any, defs: dict[str, Any]) -> Any:
    if isinstance(node, list):
        return [_clean(n, defs) for n in node]
    if not isinstance(node, dict):
        return node
    if "$ref" in node:
        inlined = {**defs[node["$ref"].rsplit("/", 1)[-1]], **{k: v for k, v in node.items() if k != "$ref"}}
        return _clean(inlined, defs)
    branches = [b for b in node.get("anyOf", ()) if b != _NULL]
    if len(branches) == 1:
        rest = {k: v for k, v in node.items() if k != "anyOf"}
        return _clean({**branches[0], **rest, "nullable": True}, defs)
    out: dict[str, Any] = {}
    for key, value in node.items():
        if key == "title" or (key == "default" and value is None):
            continue
        if key == "properties":
            out[key] = {name: _clean(sub, defs) for name, sub in value.items()}
        else:
            out[key] = _clean(value, defs)
    return out
