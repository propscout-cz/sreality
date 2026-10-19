"""Warn when a doc line a PR adds names a code symbol that exists nowhere in the code trees."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys

DOC_PATHS = ["docs/design/*.md", "docs/architecture.md", "roadmap/*.md", "GLOSSARY.md"]
CODE_TREES = ["frontend/src", "toolkit", "api", "scraper", "scripts", "migrations", "tests"]
CODE_GLOBS = ["*.py", "*.ts", "*.tsx", "*.sql", "*.yml"]

BACKTICKED = re.compile(r"`([^`\n]+)`")
CALL_SUFFIX = re.compile(r"\([^()]*\)$")
CAMEL = re.compile(r"[a-z][A-Za-z0-9]*[A-Z][A-Za-z0-9]*")
SNAKE = re.compile(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)+")
SKIP_CHARS = set("/.=?-:<") | {" ", "\t"}


def symbols_in(line: str) -> list[str]:
    """Backticked tokens in a line that look like camelCase or snake_case code symbols."""
    found: list[str] = []
    for raw in BACKTICKED.findall(line):
        name = CALL_SUFFIX.sub("", raw.strip())
        if not name or any(c in SKIP_CHARS for c in name):
            continue
        if (CAMEL.fullmatch(name) or SNAKE.fullmatch(name)) and name not in found:
            found.append(name)
    return found


def added_lines(base: str) -> list[tuple[str, int, str]]:
    """(doc path, new line number, text) for each line the diff base...HEAD adds to a doc."""
    out = subprocess.run(
        ["git", "diff", "-U0", "--no-color", f"{base}...HEAD", "--", *DOC_PATHS],
        capture_output=True, text=True, check=True,
    ).stdout
    rows: list[tuple[str, int, str]] = []
    path, lineno = "", 0
    for line in out.splitlines():
        if line.startswith("+++ "):
            path = line[6:] if line.startswith("+++ b/") else ""
        elif line.startswith("@@"):
            m = re.search(r"\+(\d+)", line)
            lineno = int(m.group(1)) if m else 0
        elif line.startswith("+") and path:
            rows.append((path, lineno, line[1:]))
            lineno += 1
    return rows


def exists_in_code(token: str) -> bool:
    """A whole-word occurrence of token anywhere in the code trees."""
    includes = [f"--include={g}" for g in CODE_GLOBS]
    r = subprocess.run(["grep", "-rqw", *includes, "--", token, *CODE_TREES], capture_output=True)
    return r.returncode == 0


def main(argv: list[str] | None = None) -> int:
    """Print one warning annotation per missing symbol; never fail."""
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base", required=True, help="ref the PR's diff is taken against")
    args = ap.parse_args(argv)
    try:
        rows = added_lines(args.base)
    except subprocess.CalledProcessError as exc:
        print(f"::warning::docs symbol check skipped: {exc.stderr.strip()}")
        return 0
    known: dict[str, bool] = {}
    misses = 0
    for path, lineno, text in rows:
        for token in symbols_in(text):
            if token not in known:
                known[token] = exists_in_code(token)
            if not known[token]:
                misses += 1
                print(f"::warning file={path},line={lineno}::`{token}` is named here but exists nowhere in the code trees")
    print(f"docs symbol check: {len(known)} symbol(s) on {len(rows)} added doc line(s), {misses} miss(es)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
