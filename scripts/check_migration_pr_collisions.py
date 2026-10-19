"""Fail a PR whose new migration number is also carried by another open PR."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request

API = "https://api.github.com"
MIGRATION_RE = re.compile(r"^migrations/(\d{3,})_[^/]+\.sql$")


def migration_number(path: str) -> str | None:
    """The NNN prefix of a migrations/NNN_*.sql path, else None."""
    m = MIGRATION_RE.match(path)
    return m.group(1) if m else None


def find_collisions(
    own: list[str], others: dict[int, list[str]]
) -> list[tuple[str, int, str]]:
    """(own file, other PR, other file) for each other PR file sharing an own NNN; the same path is the same migration."""
    own_by_number: dict[str, list[str]] = {}
    for path in own:
        n = migration_number(path)
        if n is not None:
            own_by_number.setdefault(n, []).append(path)
    hits: list[tuple[str, int, str]] = []
    for pr in sorted(others):
        for other in others[pr]:
            n = migration_number(other)
            for mine in own_by_number.get(n or "", []):
                if mine != other:
                    hits.append((mine, pr, other))
    return hits


def own_migrations(base: str) -> list[str]:
    """Migration files this branch adds or changes against base (deletions excluded)."""
    # Three-dot needs the merge base; a shallow PR checkout may lack it, and there the
    # tree diff against the merge commit's first parent is the same set.
    err = ""
    for spec in ([f"{base}...HEAD"], [base, "HEAD"]):
        r = subprocess.run(
            ["git", "diff", "--name-only", "--diff-filter=d", *spec, "--", "migrations/"],
            capture_output=True, text=True,
        )
        if r.returncode == 0:
            return [p for p in r.stdout.split() if migration_number(p)]
        err = r.stderr.strip()
    raise RuntimeError(f"git diff against {base} failed: {err}")


def _get(url: str, token: str | None) -> list[dict]:
    """One GET against the GitHub REST API, decoded."""
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json"})
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def _paged(url: str, token: str | None) -> list[dict]:
    """Every page of a per_page=100 listing."""
    out: list[dict] = []
    page = 1
    while True:
        batch = _get(f"{url}{'&' if '?' in url else '?'}per_page=100&page={page}", token)
        out.extend(batch)
        if len(batch) < 100:
            return out
        page += 1


def open_pr_migrations(repo: str, token: str | None, skip: int) -> dict[int, list[str]]:
    """Migration files carried by every other open PR, keyed by PR number."""
    result: dict[int, list[str]] = {}
    for pr in _paged(f"{API}/repos/{repo}/pulls?state=open", token):
        n = int(pr["number"])
        if n == skip:
            continue
        files = _paged(f"{API}/repos/{repo}/pulls/{n}/files", token)
        paths = [f["filename"] for f in files if f.get("status") != "removed"]
        mig = [p for p in paths if migration_number(p)]
        if mig:
            result[n] = mig
    return result


def main(argv: list[str] | None = None) -> int:
    """Compare this PR's migration numbers with every other open PR's."""
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base", required=True, help="ref this PR's diff is taken against")
    ap.add_argument("--pr", type=int, default=int(os.environ.get("PR_NUMBER") or 0))
    ap.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", "waiff/sreality"))
    args = ap.parse_args(argv)

    own = own_migrations(args.base)
    if not own:
        print("migration PR collisions: this PR adds no migration")
        return 0
    try:
        others = open_pr_migrations(args.repo, os.environ.get("GITHUB_TOKEN"), args.pr)
    except (urllib.error.URLError, OSError, ValueError, KeyError) as exc:
        print(f"::warning::migration PR collision check skipped, GitHub API unavailable: {exc}")
        return 0
    hits = find_collisions(own, others)
    for mine, pr, other in hits:
        print(f"::error file={mine}::migration number of {mine} is also taken by open PR #{pr} ({other}); renumber one of them")
    if hits:
        return 1
    print(f"migration PR collisions: none ({len(own)} own file(s) vs {len(others)} other open PR(s) with migrations)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
