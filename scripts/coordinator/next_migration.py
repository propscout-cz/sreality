"""Print the next free migration number: above origin/main's highest and every open PR's. Exit 2: no answer."""

from __future__ import annotations

import argparse
import sys

from scripts.check_migration_pr_collisions import open_pr_migrations
from scripts.coordinator._gh import REPO, gh_get, highest_number, main_migrations, run_main


def next_number() -> int:
    """One above the highest number on main or in any open PR."""
    open_prs = open_pr_migrations(REPO, None, 0, get=gh_get)
    return max(highest_number(main_migrations()), *(highest_number(paths) for paths in open_prs.values()), 0) + 1


def main(argv: list[str] | None = None) -> int:
    """Only the number goes to stdout, so a caller can use it as is."""
    argparse.ArgumentParser(description=__doc__).parse_args(argv)

    def body() -> int:
        print(f"{next_number():03d}")
        return 0

    return run_main(body)


if __name__ == "__main__":
    sys.exit(main())
