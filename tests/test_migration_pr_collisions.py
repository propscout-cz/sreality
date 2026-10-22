"""The cross-PR migration-number comparison, offline."""

from __future__ import annotations

from scripts.check_migration_pr_collisions import find_collisions, migration_number


def test_migration_number_reads_only_top_level_sql() -> None:
    assert migration_number("migrations/634_stats_status_lens.sql") == "634"
    assert migration_number("migrations/reverts/634_x.sql") is None
    assert migration_number("migrations/README.md") is None
    assert migration_number("toolkit/634_x.sql") is None


def test_same_number_in_another_open_pr_collides() -> None:
    hits = find_collisions(
        ["migrations/635_mine.sql"],
        {1900: ["migrations/635_theirs.sql", "migrations/636_other.sql"], 1901: ["migrations/640_x.sql"]},
    )
    assert hits == [("migrations/635_mine.sql", 1900, "migrations/635_theirs.sql")]


def test_distinct_numbers_and_the_same_file_do_not_collide() -> None:
    assert find_collisions(["migrations/635_mine.sql"], {1900: ["migrations/636_theirs.sql"]}) == []
    assert find_collisions(["migrations/635_mine.sql"], {1900: ["migrations/635_mine.sql"]}) == []


def test_every_colliding_pr_is_named() -> None:
    hits = find_collisions(
        ["migrations/635_a.sql", "README.md"],
        {7: ["migrations/635_b.sql"], 3: ["migrations/635_c.sql"]},
    )
    assert [pr for _, pr, _ in hits] == [3, 7]
