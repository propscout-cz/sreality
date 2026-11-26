"""Say whether the realtime worker runs code at or after main's newest commit touching its image's inputs.

Pass the commit the worker reports, or `main` to fall back on GitHub commit statuses.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
MAIN = "origin/main"
IAC_FILE = ".railway/railway.ts"
WORKER_CONTEXT = "realtime-worker - sreality"
SKIP_TEXT = "No deployment needed"
STATUS_WALK_COMMITS = 100
HEARTBEAT_SQL = "select details->>'version' from worker_heartbeats where worker = 'realtime-worker'"

Run = Callable[[list[str]], tuple[int, str, str]]


class CheckError(RuntimeError):
    """The check could not read what it judges: no verdict."""


def _run(args: list[str]) -> tuple[int, str, str]:
    done = subprocess.run(args, capture_output=True, text=True, cwd=ROOT)
    return done.returncode, done.stdout, done.stderr


def _out(args: list[str], run: Run) -> str:
    code, out, err = run(args)
    if code != 0:
        raise CheckError(f"{' '.join(args[:3])}: {err.strip()}")
    return out.strip()


def backend_watch(iac: str) -> list[str]:
    """The IaC file's own BACKEND_WATCH literal."""
    match = re.search(r"^const BACKEND_WATCH = (\[.*?\]);", iac, re.M)
    if match is None:
        raise CheckError(f"no BACKEND_WATCH in {IAC_FILE}")
    return json.loads(match.group(1))


def pathspecs(watch: Iterable[str]) -> list[str]:
    """Railway's root-anchored patterns as git pathspecs, for the same files."""
    return [f":(glob){pattern.lstrip('/')}" for pattern in watch]


def newest_image_commit(run: Run) -> str:
    """main's newest commit touching the image inputs; first-parent, because a merge to main is what deploys."""
    iac = _out(["git", "show", f"{MAIN}:{IAC_FILE}"], run)
    log = ["git", "log", "-1", "--first-parent", "--format=%H", MAIN, "--", *pathspecs(backend_watch(iac))]
    sha = _out(log, run)
    if not sha:
        raise CheckError("no commit on main touches the image inputs")
    return sha


def classify(statuses: Iterable[dict[str, Any]]) -> str:
    """One commit's worker status: deployed, skipped (success, nothing deployed), absent, or its failed/pending state."""
    for status in statuses:
        if status.get("context") == WORKER_CONTEXT:
            if status.get("state") != "success":
                return str(status.get("state"))
            return "skipped" if SKIP_TEXT in (status.get("description") or "") else "deployed"
    return "absent"


def deployed_from_statuses(run: Run) -> tuple[str | None, list[str]]:
    """The newest main commit whose worker status is a real deploy, and a note per commit above it."""
    commits = _out(["git", "log", "--first-parent", f"-n{STATUS_WALK_COMMITS}", "--format=%H", MAIN], run).split()
    notes: list[str] = []
    for sha in commits:
        status = json.loads(_out(["gh", "api", f"repos/{{owner}}/{{repo}}/commits/{sha}/status"], run))
        kind = classify(status.get("statuses") or [])
        if kind == "deployed":
            return sha, notes
        notes.append(f"{sha[:9]} {kind}")
    return None, notes


def judge(ref: str, run: Run = _run) -> int:
    """0 at or after the newest image commit, 1 behind it, 2 (CheckError, from main) no verdict."""
    needed = newest_image_commit(run)
    print(f"newest image input on main: {needed}")
    if ref == "main":
        deployed, notes = deployed_from_statuses(run)
        print(f"deployed commit from commit statuses, a fallback: a status never proves a deploy ({WORKER_CONTEXT})")
        for note in notes:
            print(f"  not deployed there: {note}")
        if deployed is None:
            raise CheckError(f"no real deploy in main's newest {STATUS_WALK_COMMITS} commits")
    else:
        deployed = _out(["git", "rev-parse", "--verify", f"{ref}^{{commit}}"], run)
    print(f"worker's deployed commit: {deployed}")
    code, _, err = run(["git", "merge-base", "--is-ancestor", needed, deployed])
    if code > 1:
        raise CheckError(f"git merge-base: {err.strip()}")
    print("AT OR AFTER: the worker runs every image input on main" if code == 0
          else "BEHIND: main has image inputs the worker does not run")
    return code


def main(argv: list[str] | None = None) -> int:
    """Fetch main, then judge."""
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog=f"The worker's own report (a production read): {HEARTBEAT_SQL}\n"
                                        "Exit: 0 at or after, 1 behind, 2 no verdict.")
    ap.add_argument("ref", metavar="sha-or-main", help="the commit the worker reports, or main to read commit statuses")
    args = ap.parse_args(argv)
    try:
        _out(["git", "fetch", "-q", "origin", "main"], _run)
        return judge(args.ref)
    except (CheckError, ValueError) as exc:
        print(f"NO VERDICT: {exc}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
