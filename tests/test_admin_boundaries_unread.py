"""Location W8-3 (#2362): nothing reads `admin_boundaries` any more; the RÚIAN mirror is the one polygon store (ruling
Q12b). The table, its loader and that loader's workflow go in W8-4, which empties `_W8_4`. Offline over the code, and
live over the replayed catalog in CI's migrations job.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from tests._live_property import REQUIRED_DB

ROOT = Path(__file__).resolve().parents[1]
CODE = ("api", "toolkit", "scraper", "scripts", "location_data", "tests", "frontend/src", "chrome-extension/src",
        ".github/workflows")
SUFFIXES = {".py", ".ts", ".tsx", ".yml", ".yaml", ".json", ".sql", ".sh"}
# The table's own loader, its test and its workflow: deleted with the table in W8-4.
_W8_4 = {"scripts/ingest_boundaries.py", "tests/test_ingest_boundaries.py", ".github/workflows/ingest_boundaries.yml"}
LEGACY = re.compile(r"admin_boundaries")


def test_no_code_names_the_legacy_boundary_table() -> None:
    found = sorted(
        f"{rel}:{n}" for top in CODE for path in (ROOT / top).rglob("*")
        if path.suffix in SUFFIXES and "node_modules" not in path.parts
        and (rel := path.relative_to(ROOT).as_posix()) not in _W8_4 | {"tests/test_admin_boundaries_unread.py"}
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1) if LEGACY.search(line))
    assert not found, "read the RÚIAN mirror (ruian_unit_render, ruian_place_names_public, obec_population):\n" + \
        "\n".join(found)


@REQUIRED_DB
def test_no_view_or_function_reads_the_legacy_boundary_table(cur: Any) -> None:
    cur.execute("SELECT DISTINCT c.oid::regclass::text FROM pg_depend d JOIN pg_rewrite r ON r.oid = d.objid "
                "JOIN pg_class c ON c.oid = r.ev_class WHERE d.refobjid IN ('public.admin_boundaries'::regclass, "
                "'public.admin_boundaries_public'::regclass) AND c.relname NOT IN ('admin_boundaries', "
                "'admin_boundaries_public')")
    assert cur.fetchall() == []
    cur.execute("SELECT p.oid::regprocedure::text FROM pg_proc p WHERE p.pronamespace = 'public'::regnamespace "
                "AND p.prosrc ~ 'admin_boundaries'")
    assert cur.fetchall() == []
