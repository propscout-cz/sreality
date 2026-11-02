"""What the merge coordinator reads before a green light: one PR's state, sensitive files and migration clashes."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Iterable
from typing import Any

from scripts.check_migration_pr_collisions import API, _paged, find_collisions, migration_number, open_pr_migrations
from scripts.coordinator._gh import REPO, gh_get, gh_json, main_migrations, run_main, tool

# One small local list: scripts/check_engine_light.py (branch fix/engine-light-check) replaces it once merged.
SENSITIVE_PREFIXES = ("autodedup/", ".railway/")
SENSITIVE_FILES = frozenset({"scraper/realtime_worker.py", "scraper/job_lease.py"})
NUMBERED_MIGRATION = re.compile(r"migrations/\d+_[^/]*\.sql")
FAILED = {"FAILURE", "ERROR", "TIMED_OUT", "CANCELLED", "ACTION_REQUIRED", "STARTUP_FAILURE"}
PASSED = {"SUCCESS", "NEUTRAL", "SKIPPED"}


def is_sensitive(path: str) -> bool:
    """Whether a path needs the coordinator's light."""
    return (path.startswith(SENSITIVE_PREFIXES) or path in SENSITIVE_FILES
            or NUMBERED_MIGRATION.fullmatch(path) is not None)


def check_summary(rollup: Iterable[dict[str, Any]]) -> dict[str, list[str]]:
    """Check names by outcome; a check run reads `conclusion` once done, a status context `state`."""
    summary: dict[str, list[str]] = {"passed": [], "failed": [], "pending": []}
    for check in rollup:
        outcome = (check.get("conclusion") or check.get("state") or "").upper()
        name = str(check.get("name") or check.get("context"))
        key = "failed" if outcome in FAILED else "passed" if outcome in PASSED else "pending"
        summary[key].append(name)
    return summary


def clashes(own: list[str], on_main: list[str], open_prs: dict[int, list[str]]) -> list[str]:
    """One line per other file holding one of this PR's numbers, on main or in another open PR."""
    lines = [f"{mine} clashes with {other} on main" for mine, _, other in find_collisions(own, {0: on_main})]
    lines += [f"{mine} clashes with {other} in #{pr}" for mine, pr, other in find_collisions(own, open_prs)]
    return lines


def report(number: int) -> None:
    """Read the PR and print its lines."""
    pr = gh_json(f"repos/{REPO}/pulls/{number}")
    view = json.loads(tool("gh", "pr", "view", str(number), "--repo", REPO, "--json", "statusCheckRollup,mergeStateStatus"))
    entries = _paged(f"{API}/repos/{REPO}/pulls/{number}/files", None, gh_get)
    files = [entry["filename"] for entry in entries]
    files += [entry["previous_filename"] for entry in entries if entry.get("previous_filename")]
    summary = check_summary(view["statusCheckRollup"])
    labels = [label["name"] for label in pr["labels"]]

    print(f"#{number} {pr['title']}")
    print(f"head SHA:  {pr['head']['sha']}")
    print(f"mergeable: {pr['mergeable']} ({view['mergeStateStatus']})")
    print(f"draft:     {pr['draft']}")
    print(f"light:     {'engine-light' in labels}")
    print(f"checks:    {len(summary['passed'])} passed, {len(summary['failed'])} failed, {len(summary['pending'])} pending")
    for key in ("failed", "pending"):
        for name in summary[key]:
            print(f"  {key}: {name}")
    sensitive = sorted({path for path in files if is_sensitive(path)})
    print(f"sensitive: {len(sensitive)} file(s)")
    for path in sensitive:
        print(f"  {path}")
    own = sorted(entry["filename"] for entry in entries
                 if migration_number(entry["filename"]) and entry.get("status") != "removed")
    print(f"migrations: {', '.join(own) or 'none'}")
    if own:
        lines = clashes(own, main_migrations(), open_pr_migrations(REPO, None, number, get=gh_get))
        print(f"clashes:   {len(lines)}")
        for line in lines:
            print(f"  {line}")


def main(argv: list[str] | None = None) -> int:
    """Exit 0 once printed, whatever the PR's state; 2 when GitHub gave no answer."""
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("pr", type=int, help="the PR number")
    args = ap.parse_args(argv)

    def body() -> int:
        report(args.pr)
        return 0

    return run_main(body)


if __name__ == "__main__":
    sys.exit(main())
