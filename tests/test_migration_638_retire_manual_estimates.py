"""Migration 638's prompt half must land on the replayed schema, where a miss is only a WARNING.

The CI replay builds rental_estimator_full_v1's prompt from migration 076 and nothing rewrites it
before 638, so the replayed outcome is fixed: 638's verbatim bullet must occur in 076's prompt
exactly once, and removing it must leave no mention of the retired tool.
"""

from __future__ import annotations

import re
from pathlib import Path

_MIGRATIONS = Path(__file__).resolve().parent.parent / "migrations"
_TOOL = "get_manual_rental_estimates"


def _quoted(path: Path, tag: str) -> str:
    match = re.search(rf"\${tag}\$(.*?)\${tag}\$", path.read_text(), re.DOTALL)
    assert match, f"no ${tag}$ body in {path.name}"
    return match.group(1)


def test_638_removes_the_only_mention_of_the_tool_from_the_replayed_prompt() -> None:
    """RED by: changing one character of 638's bullet, or 076's."""
    prompt = _quoted(_MIGRATIONS / "076_rental_estimator_v3_condition_filters.sql", "PROMPT")
    bullet = _quoted(_MIGRATIONS / "638_retire_manual_estimates_tool.sql", "b")
    assert prompt.count(bullet) == 1
    assert _TOOL not in prompt.replace(bullet, "")
