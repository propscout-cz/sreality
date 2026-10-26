"""Every test module that needs a database is named by a migrations.yml step that hands it one.

test.yml runs pytest with no database, so a module that reads TEST_DATABASE_URL only ever skips
there; until a schema-and-sql step that sets TEST_DATABASE_URL names it, it has never run.
A module needs a database when it holds the "TEST_DATABASE_URL" literal or imports a tests/ helper
that does (tests._live_property, tests._tenant_scope, or any helper importing one of them); a
docstring that merely mentions the name does not count.
"""

from __future__ import annotations

import ast
from pathlib import Path

import yaml

SELF = Path(__file__).resolve()
ROOT = SELF.parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "migrations.yml"
_URL = "TEST_DATABASE_URL"


def _module(path: Path) -> str:
    return ".".join(path.relative_to(ROOT).with_suffix("").parts)


def _reads_url(tree: ast.Module) -> bool:
    return any(isinstance(n, ast.Constant) and n.value == _URL for n in ast.walk(tree))


def _imports(path: Path, tree: ast.Module) -> set[str]:
    """Every dotted name an import statement could bind, `from a import b` giving both a and a.b."""
    names: set[str] = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            names.update(a.name for a in n.names)
        elif isinstance(n, ast.ImportFrom):
            base = n.module or ""
            if n.level:
                package = _module(path).split(".")[: -n.level]
                base = ".".join([*package, base] if base else package)
            names.add(base)
            names.update(f"{base}.{a.name}" for a in n.names)
    return names


def _needs_database() -> set[str]:
    trees = {p: ast.parse(p.read_text(encoding="utf-8")) for p in (ROOT / "tests").rglob("*.py")}
    imports = {p: _imports(p, t) for p, t in trees.items()}
    helpers = {_module(p) for p, t in trees.items() if not p.name.startswith("test_") and _reads_url(t)}
    grown = True
    while grown:
        more = {
            _module(p) for p in trees
            if not p.name.startswith("test_") and imports[p] & helpers
        } - helpers
        helpers |= more
        grown = bool(more)
    return {
        p.relative_to(ROOT).as_posix()
        for p, t in trees.items()
        if p.name.startswith("test_") and p != SELF and (_reads_url(t) or imports[p] & helpers)
    }


def _laned() -> set[str]:
    steps = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]["schema-and-sql"]["steps"]
    return {
        token
        for step in steps
        if _URL in (step.get("env") or {}) and "pytest" in step.get("run", "")
        for token in step["run"].split()
        if token.endswith(".py")
    }


def test_every_database_module_is_named_by_a_migrations_step() -> None:
    missing = sorted(_needs_database() - _laned())
    assert not missing, (
        f"these modules need a database but no schema-and-sql step that sets {_URL} names them, "
        f"so they only ever skip: {missing}. Add a step to .github/workflows/migrations.yml beside "
        f"its neighbours (env {_URL} + DB_RAILS_REQUIRED: \"1\", `pytest <module> -q`)."
    )


def test_the_census_sees_both_kinds_of_database_module() -> None:
    needed = _needs_database()
    assert "tests/test_sql_schema_prepare.py" in needed, "the literal arm went blind"
    assert "tests/test_property_carriers_live.py" in needed, "the helper-import arm went blind"
