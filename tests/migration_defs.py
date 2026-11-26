"""Which migration currently DEFINES a Postgres function.

The two Browse aggregate RPCs are re-stated WHOLE by every migration that touches them
(504 -> 537 -> 547, three programs in two months), so a source-text rail pinned to a file
name stops guarding live behaviour the moment the next one lands — silently, because the
old file still says what it always said. Every such rail resolves the file the same way,
from here.
"""

from __future__ import annotations

import re
from pathlib import Path

MIGRATIONS = Path(__file__).resolve().parent.parent / "migrations"


def latest_definition(func: str) -> Path:
    """The highest-numbered migration whose text (re)defines `func`."""
    pat = re.compile(rf"create or replace function (?:public\.)?{func}\s*\(", re.IGNORECASE)
    hits = [p for p in MIGRATIONS.glob("*.sql") if pat.search(p.read_text(encoding="utf-8"))]
    assert hits, f"no migration defines {func}"
    return max(hits, key=lambda p: int(p.name.split("_", 1)[0]))


def _params(text: str, fn: str) -> list[tuple[str, str]]:
    """`fn`'s (name, type) parameters as `text` defines them."""
    at = re.search(rf"create or replace function public\.{fn}\(", text, re.I).end()
    depth, i = 1, at
    while depth:
        depth += {"(": 1, ")": -1}.get(text[i], 0)
        i += 1
    args = re.sub(r"--[^\n]*", "", text[at: i - 1])
    out = []
    for part in re.split(r",\s*(?=[a-z][a-z0-9_]* )", " ".join(args.split())):
        name, rest = part.split(" ", 1)
        out.append((name, re.split(r"\s+default\s+", rest, flags=re.I)[0].lower()))
    return out
