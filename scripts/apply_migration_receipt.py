"""Plan / receipt for `apply_migration.yml`: what one migration file declares and drops,
and — after the apply — whether every declared object exists in the LIVE catalog and every dropped one is gone.

Reuses the parser and the probe of the `migration_drift` check (both in
scripts/migration_objects.py), so "applied" here means exactly what the hourly
alarm accepts.
The `supabase_migrations.schema_migrations` ledger is deliberately not read:
it is not the oracle (see scripts/migration_objects.py).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from scripts.migration_objects import (
    MIGRATION_OBJECT_PROBE_SQL,
    SAFE_IDENT,
    MigrationObject,
    _strip_noise,
    parse_dropped,
    parse_objects,
)


def statement_count(sql: str) -> int:
    return sum(1 for part in _strip_noise(sql).split(";") if part.strip())


def declared(path: Path) -> list[MigrationObject]:
    return parse_objects(path.read_text(encoding="utf-8"))


def probe(conn: Any, objects: list[MigrationObject]) -> list[tuple[str, str, bool]]:
    safe = [o for o in objects if SAFE_IDENT.match(o.ident)]
    if not safe:
        return []
    rows = conn.execute(
        MIGRATION_OBJECT_PROBE_SQL,
        {"kinds": [o.kind for o in safe], "idents": [o.ident for o in safe]},
    ).fetchall()
    return [(kind, ident, bool(present)) for kind, ident, present in rows]


def dropped_by(path: Path) -> list[MigrationObject]:
    return parse_dropped(path.read_text(encoding="utf-8"))


def plan(path: Path) -> int:
    objects, dropped = declared(path), dropped_by(path)
    print(f"{path.name}: {statement_count(path.read_text(encoding='utf-8'))} statement(s), "
          f"{len(objects) + len(dropped)} probeable object(s)")
    for o in objects:
        print(f"  declares {o}")
    for o in dropped:
        print(f"  drops {o}")
    if not objects and not dropped:
        print("  (nothing probeable — the receipt can only confirm psql exit 0)")
    return 0


def verdict(rows: list[tuple[str, str, bool]], dropped: list[MigrationObject]) -> tuple[int, list[str]]:
    """The exit code and the lines to print, from the probe's rows alone: `dropped` says which rows expect absence."""
    drops = {(o.kind, o.ident) for o in dropped}
    lines, wrong = [], []
    for kind, ident, present in rows:
        is_drop = (kind, ident) in drops
        lines.append(f"  {'PRESENT' if present else 'ABSENT '} {kind}:{ident}{' (dropped)' if is_drop else ''}")
        if present == is_drop:
            wrong.append(f"{kind}:{ident}")
    if wrong:
        return 1, [*lines, f"::error::{len(wrong)} object(s) wrong in the live catalog "
                           f"(a declared one missing, or a dropped one still there): {', '.join(wrong)}"]
    if not rows:
        return 0, ["::notice::UNVERIFIABLE: nothing probeable in this file, only psql's exit 0 confirms the apply"]
    gone = sum(1 for kind, ident, _ in rows if (kind, ident) in drops)
    return 0, [*lines, f"::notice::receipt OK: {len(rows) - gone} present, {gone} dropped-absent"]


def verify(path: Path) -> int:
    from scraper import db

    dropped = dropped_by(path)
    with db.connect() as conn:
        rows = probe(conn, [*declared(path), *dropped])
    code, lines = verdict(rows, dropped)
    print("\n".join(lines))
    return code


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--plan", metavar="FILE")
    mode.add_argument("--verify", metavar="FILE")
    args = ap.parse_args(argv)
    return plan(Path(args.plan)) if args.plan else verify(Path(args.verify))


if __name__ == "__main__":
    sys.exit(main())
