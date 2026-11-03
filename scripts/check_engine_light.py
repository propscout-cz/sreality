"""Fail an engine-sensitive PR that has no merge coordinator's light, the label `engine-light`."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from collections.abc import Callable, Iterable
from typing import Any

LIGHT_LABEL = "engine-light"
SENSITIVE_PREFIXES = ("autodedup/", ".railway/")
SENSITIVE_FILES = frozenset({"scraper/realtime_worker.py", "scraper/job_lease.py"})
NUMBERED_MIGRATION = re.compile(r"migrations/\d+_[^/]*\.sql")
QUEUE_REF = re.compile(r"gh-readonly-queue/.+/pr-(\d+)-[0-9a-f]+")
PAGE_SIZE = 100

Api = Callable[..., Any]


class ApiError(RuntimeError):
    """GitHub's API did not answer: no verdict, so the check fails and a re-run can pass."""


def is_sensitive(path: str) -> bool:
    """Whether a repo path needs the light; `migrations/reverts/` never replays, so it does not."""
    return (path.startswith(SENSITIVE_PREFIXES) or path in SENSITIVE_FILES
            or NUMBERED_MIGRATION.fullmatch(path) is not None)


def verdict(files: Iterable[str], labels: Iterable[str]) -> tuple[bool, list[str]]:
    """(passes, the sensitive files): a sensitive PR passes only with the light."""
    sensitive = sorted({path for path in files if is_sensitive(path)})
    return (not sensitive or LIGHT_LABEL in set(labels)), sensitive


def queue_pr_number(head_ref: str) -> int | None:
    """The PR of a merge group's `refs/heads/gh-readonly-queue/<base>/pr-<n>-<sha>`, else None."""
    match = QUEUE_REF.search(head_ref)
    return int(match.group(1)) if match else None


def pr_files(get_page: Callable[[int], list[dict[str, Any]]], expected: int) -> list[str]:
    """Every path a PR touches, a rename's old path too: moving a file out of a sensitive tree touches it."""
    entries: list[dict[str, Any]] = []
    page = 1
    while True:
        batch = get_page(page)
        entries.extend(batch)
        if len(batch) < PAGE_SIZE:
            break
        page += 1
    # The listing stops at 3,000 files; a judgement over part of a PR is no judgement.
    if len(entries) < expected:
        raise ApiError(f"GitHub listed {len(entries)} of the PR's {expected} files")
    paths = [entry["filename"] for entry in entries]
    paths += [entry["previous_filename"] for entry in entries if entry.get("previous_filename")]
    return paths


def gh_api(path: str, method: str = "GET") -> Any:
    """One `gh api` call, decoded."""
    done = subprocess.run(["gh", "api", "--method", method, path], capture_output=True, text=True)
    if done.returncode != 0:
        raise ApiError(f"gh api --method {method} {path}: {done.stderr.strip()}")
    return json.loads(done.stdout) if done.stdout.strip() else None


def pr_number(event_name: str, event: dict[str, Any]) -> int:
    """The PR this run judges, from a pull_request event or the merge group's head ref."""
    if event_name == "pull_request":
        return int(event["pull_request"]["number"])
    if event_name == "merge_group":
        head_ref = event["merge_group"]["head_ref"]
        number = queue_pr_number(head_ref)
        if number is None:
            raise ApiError(f"no PR number in the merge group's head_ref {head_ref!r}")
        return number
    raise ApiError(f"unexpected event {event_name!r}")


def judge(event_name: str, event: dict[str, Any], repo: str, api: Api = gh_api) -> int:
    """Read the PR now, not from the event: a re-run replays the old payload but must see today's labels."""
    number = pr_number(event_name, event)
    pr = api(f"repos/{repo}/pulls/{number}")
    labels = [label["name"] for label in pr["labels"]]
    # A re-run removes it too: it cannot tell a light given after this push from one its first attempt failed to remove.
    if event_name == "pull_request" and event.get("action") == "synchronize" and LIGHT_LABEL in labels:
        api(f"repos/{repo}/issues/{number}/labels/{LIGHT_LABEL}", method="DELETE")
        labels.remove(LIGHT_LABEL)
        print(f"new push: the {LIGHT_LABEL} label is removed, the light was for the commit reviewed before it")
    files = pr_files(lambda page: api(f"repos/{repo}/pulls/{number}/files?per_page={PAGE_SIZE}&page={page}"),
                     int(pr["changed_files"]))
    passes, sensitive = verdict(files, labels)
    if passes:
        print(f"engine light #{number}: " + (f"lit, {len(sensitive)} sensitive file(s)" if sensitive else "not needed"))
        return 0
    print(f"::error::engine-sensitive with no light: ask the merge coordinator for it with PR #{number}, "
          f"head SHA {pr['head']['sha']} and what it touches: {', '.join(sensitive)}")
    return 1


def main() -> int:
    """Judge the GitHub Actions event this run belongs to."""
    try:
        with open(os.environ["GITHUB_EVENT_PATH"], encoding="utf-8") as handle:
            event = json.load(handle)
        return judge(os.environ["GITHUB_EVENT_NAME"], event, os.environ["GITHUB_REPOSITORY"])
    except (ApiError, KeyError, ValueError, TypeError, OSError) as exc:
        print(f"::error::engine light: no verdict, re-run this check: {exc!r}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
