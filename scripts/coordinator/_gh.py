"""The coordinator scripts' one door to `gh` and `git`; `gh` holds its own login, so no secret passes through here."""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from scripts.check_migration_pr_collisions import API, migration_number

ROOT = Path(__file__).resolve().parents[2]
REPO = os.environ.get("GITHUB_REPOSITORY", "propscout-cz/sreality")


class ToolError(RuntimeError):
    """A `gh` or `git` call failed: the script has no answer."""


def tool(*args: str) -> str:
    """Run one command in this checkout, return its stdout."""
    done = subprocess.run(list(args), capture_output=True, text=True, cwd=ROOT)
    if done.returncode != 0:
        raise ToolError(f"{' '.join(args)}: {done.stderr.strip()}")
    return done.stdout


def gh_json(path: str) -> Any:
    """One `gh api` GET, decoded."""
    return json.loads(tool("gh", "api", path))


def gh_get(url: str, _token: str | None = None) -> Any:
    """`check_migration_pr_collisions`'s GET, through `gh`'s login: the token argument stays unused."""
    return gh_json(url.removeprefix(f"{API}/"))


def main_migrations() -> list[str]:
    """The numbered migration files on a freshly fetched origin/main."""
    tool("git", "fetch", "-q", "origin", "main")
    names = tool("git", "ls-tree", "--name-only", "origin/main", "migrations/").split()
    return [name for name in names if migration_number(name)]


def run_main(body: Callable[[], int]) -> int:
    """A script's body; a failed read prints why and exits 2, so it never reads as an answer."""
    try:
        return body()
    except (ToolError, ValueError, KeyError, TypeError) as exc:
        print(f"NO ANSWER: {exc}")
        return 2


def highest_number(paths: Sequence[str]) -> int:
    """The highest migration number among these paths, 0 for none."""
    return max((int(number) for number in map(migration_number, paths) if number), default=0)
