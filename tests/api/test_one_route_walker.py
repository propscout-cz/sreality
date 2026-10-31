"""Every route walk in the tests goes through `tests/api/_routes.py` (#2206).

The walker had grown six copies, three of which read only the top level and would miss an included router's routes.
"""

from __future__ import annotations

import re
from pathlib import Path

_TESTS = Path(__file__).resolve().parents[1]
_HOME = _TESTS / "api" / "_routes.py"
# Reading the app's route table, a router's wrapped routes, or a route's dependency tree.
_WALK = re.compile(r"\bapp\.routes\b|\boriginal_router\b|\.dependant\b")


def test_only_the_shared_module_walks_the_route_table() -> None:
    offenders = [
        f"{path.relative_to(_TESTS.parent)}:{n}"
        for path in sorted(_TESTS.rglob("*.py"))
        if path not in (_HOME, Path(__file__).resolve())
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if _WALK.search(line)
    ]
    assert not offenders, f"walk routes through tests/api/_routes.py: {offenders}"
