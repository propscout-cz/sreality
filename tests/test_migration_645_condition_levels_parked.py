"""Migration 645's prompt edit must land on the replayed schema, where a miss is only a WARNING.

The CI replay builds rental_estimator_full_v1's prompt from migration 076 (638 touches another bullet), so the
replayed outcome is fixed: 645's two 076-era blocks must each occur in 076's prompt exactly once, and replacing them
must leave no mention of the parked condition levels.
"""

from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_MIGRATION = _ROOT / "migrations" / "645_estimator_prompt_condition_levels_parked.sql"
_LEVEL_WORDS = ("condition_level", "_level_min", "derived score")


def _quoted(path: Path, tag: str) -> str:
    match = re.search(rf"\${tag}\$(.*?)\${tag}\$", path.read_text(encoding="utf-8"), re.DOTALL)
    assert match, f"no ${tag}$ body in {path.name}"
    return match.group(1)


def _edited(prompt: str) -> str:
    for old, new in (("b0", "b1"), ("s0", "s1"), ("v0", "v1")):
        prompt = prompt.replace(_quoted(_MIGRATION, old), _quoted(_MIGRATION, new))
    return prompt


def test_645_removes_every_level_mention_from_the_replayed_prompt() -> None:
    """RED by: changing one character of 645's old blocks, or 076's."""
    prompt = _quoted(_ROOT / "migrations" / "076_rental_estimator_v3_condition_filters.sql", "PROMPT")
    assert prompt.count(_quoted(_MIGRATION, "b0")) == 1
    assert prompt.count(_quoted(_MIGRATION, "s0")) == 1
    edited = _edited(prompt).lower()
    assert not [w for w in _LEVEL_WORDS if w in edited]
    assert "condition_match" in edited


def test_645_is_idempotent_and_the_new_text_names_no_level() -> None:
    for tag in ("b1", "s1", "v1"):
        new = _quoted(_MIGRATION, tag)
        assert not [w for w in _LEVEL_WORDS if w in new.lower()], tag
        for old in ("b0", "s0", "v0"):
            assert _quoted(_MIGRATION, old) not in new
