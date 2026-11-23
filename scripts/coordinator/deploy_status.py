"""Print every Railway commit status of a commit. Exit 0 all success, 1 a non-success or none posted, 2 no answer."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Iterable
from typing import Any

from scripts.coordinator._gh import REPO, gh_json, run_main

SKIP_TEXT = "No deployment needed"


def flag(status: dict[str, Any]) -> str:
    """The status's warning: a success reading SKIP_TEXT is a watch skip, not a deploy."""
    if status.get("state") != "success":
        return "NOT SUCCESS"
    return "SKIPPED, NOT DEPLOYED" if SKIP_TEXT in (status.get("description") or "") else ""


def lines(statuses: Iterable[dict[str, Any]]) -> tuple[list[str], int]:
    """A line per status, by context, and the exit code."""
    ordered = sorted(statuses, key=lambda status: str(status.get("context")))
    if not ordered:
        return ["no status posted yet"], 1
    out = [" | ".join(part for part in (str(s.get("context")), str(s.get("state")), s.get("description") or "", flag(s))
                      if part)
           for s in ordered]
    return out, int(any(s.get("state") != "success" for s in ordered))


def main(argv: list[str] | None = None) -> int:
    """Read the commit's combined status once; `scripts/rollout_wait.sh` is the waiter."""
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("sha", help="the commit, short or full")
    args = ap.parse_args(argv)

    def body() -> int:
        status = gh_json(f"repos/{REPO}/commits/{args.sha}/status?per_page=100")
        out, code = lines(status["statuses"])
        print("\n".join(out))
        return code

    return run_main(body)


if __name__ == "__main__":
    sys.exit(main())
