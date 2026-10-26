"""Read the Railway IaC file's literals and per-project definitions without a Node runtime (#2060)."""

import json
import re
from pathlib import Path
from typing import Any

RAILWAY_IAC = Path(__file__).resolve().parents[1] / ".railway" / "railway.ts"


def iac_text() -> str:
    """The whole IaC file."""
    return RAILWAY_IAC.read_text(encoding="utf-8")


def iac_constant(name: str) -> Any:
    """One top-level `const NAME = <JSON literal>;` of the Railway IaC file."""
    match = re.search(rf"^const {name} = (.+?);", iac_text(), re.M)
    assert match, name
    return json.loads(match.group(1))


def iac_entry(name: str) -> str:
    """The text of one project's definition (`const worker = ...` up to the next top-level const)."""
    return iac_text().split(f"\nconst {name} = ", 1)[1].split("\nconst ", 1)[0]
