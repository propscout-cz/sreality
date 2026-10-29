"""Steering docs name a session by its role, never by its rotating name.

A session's name (`sreality-<2 hex>`) changes on every resume, so a doc that says "ask sreality-e9" sends the next
session to a name nobody answers to. The role is the merge coordinator (`docs/agents/issue-tracker.md` § Merging).
Blind spot: a bare two-letter name ("e9") passes, since it collides with ruff's E9 and with hex.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SESSION_NAME = re.compile(r"\bsreality-[0-9a-f]{2}\b")


def _steering_docs() -> list[Path]:
    return [REPO / "CLAUDE.md", REPO / "docs/architecture.md", *sorted((REPO / "docs/agents").rglob("*.md")),
            *sorted((REPO / ".claude/skills").rglob("*.md"))]


def test_steering_docs_name_no_session() -> None:
    hits = [
        f"{path.relative_to(REPO)}:{n}: {m.group()}"
        for path in _steering_docs()
        for n, line in enumerate(path.read_text().splitlines(), 1)
        for m in SESSION_NAME.finditer(line)
    ]
    assert not hits, "name the role (e.g. 'the merge coordinator'), not a session:\n" + "\n".join(hits)


def test_pattern_catches_a_session_name() -> None:
    assert SESSION_NAME.search("take sreality-e9's brake first")
    assert not SESSION_NAME.search("the sreality-dedup lane, ruff E9")
