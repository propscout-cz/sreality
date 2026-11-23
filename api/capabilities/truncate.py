"""Fit a tool result into the size a model reads: whole rows from the head, every metadata key kept."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from api.capabilities.errors import ToolError, ToolErrorCode

RESULT_BUDGET = 20_000


def encode(result: Any) -> str:
    """The exact string a model is sent for a result."""
    return json.dumps(result, default=str, ensure_ascii=False, separators=(",", ":"))


def _lists(result: dict[str, Any]) -> dict[str, list[Any]]:
    data = result.get("data")
    if isinstance(data, list):
        return {"data": data}
    if isinstance(data, dict):
        return {f"data.{k}": v for k, v in data.items() if isinstance(v, list)}
    return {}


def _with_heads(result: dict[str, Any], heads: dict[str, int]) -> dict[str, Any]:
    lists = _lists(result)
    data = result["data"]
    if isinstance(data, list):
        data = data[: heads["data"]] if "data" in heads else data
    elif isinstance(data, dict):
        data = {k: v[: heads[f"data.{k}"]] if f"data.{k}" in heads else v for k, v in data.items()}
    truncated = {path: {"kept": n, "total": len(lists[path])} for path, n in heads.items() if n < len(lists[path])}
    metadata = {**(result.get("metadata") or {}), "truncated": truncated} if truncated else result.get("metadata")
    return {**result, "data": data, "metadata": metadata}


def _fits(result: dict[str, Any]) -> bool:
    return len(encode(result)) <= RESULT_BUDGET


def heads(lists: dict[str, list[Any]], fits: Callable[[dict[str, int]], bool]) -> dict[str, int] | None:
    """The longest whole-row heads of `lists` that `fits` takes, smaller lists kept whole first; None when nothing
    fits even with every list empty."""
    out = {path: 0 for path in lists}
    if not fits(out):
        return None
    for path in sorted(lists, key=lambda p: len(encode(lists[p]))):
        lo, hi = 0, len(lists[path])
        while lo < hi:
            mid = (lo + hi + 1) // 2
            lo, hi = (mid, hi) if fits({**out, path: mid}) else (lo, mid - 1)
        out[path] = lo
    return out


def fit(result: dict[str, Any]) -> dict[str, Any]:
    """`result` itself when it fits, else its lists under `data` cut to their longest fitting whole-row heads."""
    # Each cut is recorded in metadata.truncated and result_count stays the true total. A row is never split, so every
    # per-m² figure keeps its basis (rule 23).
    if _fits(result):
        return result
    kept = heads(_lists(result), lambda h: _fits(_with_heads(result, h)))
    if kept is None:
        raise ToolError(ToolErrorCode.RESULT_TOO_LARGE, "the result is too large even without its rows; narrow the request")
    return _with_heads(result, kept)
