"""THE TENANT-CALLER CENSUS: who opens a tenant transaction, and who builds a caller with claims. Read off the syntax
tree of every non-test Python package, with no database; each arm compares what it finds with its pinned set.

Direct callers of `tenant_pool.tenant_transaction`, attribute and bare-name calls alike (the pool's own `tenant_conn`
calls it unqualified), keyed `<file>::<enclosing function>` on the account-scope census's walk. A claims site is a
`Caller(...)` handed claims, or a dict naming both `sub` and `role`: every run's claims come out of the one builder,
so there is one claims shape. This file is the list; docs point here rather than restate it.

BLIND SPOTS. A call in a class body (no function encloses it), a call through `getattr`, or the function passed around
as a value and called elsewhere is not seen; an import alias is. A claims dict built key by key, or from another mapping,
is not seen.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator, Mapping
from pathlib import Path

import pytest

from tests.api.test_account_scope_census import _NESTED, _qualnames

ROOT = Path(__file__).resolve().parents[2]
TARGET = "tenant_transaction"

# A floor per tree: a census that found the tree empty, or lost it to a move, would pass every assertion.
TREES: Mapping[str, int] = {"api": 90, "toolkit": 55, "scraper": 80, "scripts": 80}

CALLERS = frozenset({
    "api/tenant_pool.py::tenant_conn",
    "api/main.py::post_estimation_feedback",
    "api/capabilities/registry.py::_connection",
})
CLAIMS_SITES = frozenset({"api/agents/claims.py::caller_for"})

_NAMED_EXCEPTIONS = ("`.claude/skills/database/references/tenancy.md`, its 'Named exception' paragraphs: a new caller "
                     "argues its case there, then joins the pinned set here")


def _modules(root: Path, trees: Mapping[str, int]) -> Iterator[tuple[str, ast.Module]]:
    for tree in trees:
        for path in sorted((root / tree).rglob("*.py")):
            yield path.relative_to(root).as_posix(), ast.parse(path.read_text(encoding="utf-8"))


def _scopes(module: ast.Module) -> list[tuple[str, ast.AST]]:
    return [("<module>", module), *_qualnames(module)]


def _own_nodes(scope: ast.AST) -> Iterator[ast.AST]:
    """Every node of `scope`'s own body; a nested def is its own census row."""
    stack = list(ast.iter_child_nodes(scope))
    while stack:
        node = stack.pop()
        if isinstance(node, _NESTED):
            continue
        yield node
        stack.extend(ast.iter_child_nodes(node))


def _called_name(call: ast.Call) -> str | None:
    func = call.func
    return func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else None


def direct_callers(root: Path, trees: Mapping[str, int] = TREES) -> set[str]:
    found: set[str] = set()
    for rel, module in _modules(root, trees):
        names = {TARGET} | {alias.asname for node in ast.walk(module) if isinstance(node, ast.ImportFrom)
                            for alias in node.names if alias.name == TARGET and alias.asname}
        for qual, scope in _scopes(module):
            if any(isinstance(n, ast.Call) and _called_name(n) in names for n in _own_nodes(scope)):
                found.add(f"{rel}::{qual}")
    return found


def _is_claims_site(node: ast.AST) -> bool:
    if isinstance(node, ast.Dict):
        return {"sub", "role"} <= {k.value for k in node.keys if isinstance(k, ast.Constant)}
    if not isinstance(node, ast.Call):
        return False
    name = _called_name(node)
    if name == "Caller":
        return len(node.args) > 1 or any(k.arg == "claims" for k in node.keywords)
    return name == "dict" and {"sub", "role"} <= {k.arg for k in node.keywords}


def claims_sites(root: Path, trees: Mapping[str, int] = TREES) -> set[str]:
    return {f"{rel}::{qual}" for rel, module in _modules(root, trees) for qual, scope in _scopes(module)
            if any(_is_claims_site(n) for n in _own_nodes(scope))}


def caller_problems(root: Path, trees: Mapping[str, int] = TREES, pinned: frozenset[str] = CALLERS) -> list[str]:
    found = direct_callers(root, trees)
    problems = [f"{key} opens a tenant transaction directly. See {_NAMED_EXCEPTIONS}." for key in sorted(found - pinned)]
    problems += [f"{key} is pinned but no longer calls {TARGET} (moved or renamed): update the pinned set and "
                 f"{_NAMED_EXCEPTIONS}." for key in sorted(pinned - found)]
    return problems


def claims_problems(root: Path, trees: Mapping[str, int] = TREES, pinned: frozenset[str] = CLAIMS_SITES) -> list[str]:
    found = claims_sites(root, trees)
    problems = [f"{key} builds claims by hand; a run's claims come only from api.agents.claims.caller_for."
                for key in sorted(found - pinned)]
    problems += [f"{key} is pinned but builds no claims (moved or renamed): update the pinned set."
                 for key in sorted(pinned - found)]
    return problems


def floor_problems(root: Path, trees: Mapping[str, int] = TREES) -> list[str]:
    counts = {tree: sum(1 for _ in (root / tree).rglob("*.py")) for tree in trees}
    return [f"{tree} holds {counts[tree]} Python files, under its floor of {floor}: the census lost it"
            for tree, floor in trees.items() if counts[tree] < floor]


# --- the repository, against the pinned sets ------------------------------------------------------------------------

def test_every_direct_tenant_transaction_caller_is_pinned() -> None:
    assert caller_problems(ROOT) == []


def test_every_caller_with_claims_comes_out_of_the_one_builder() -> None:
    assert claims_problems(ROOT) == []


def test_non_vacuity_every_tree_holds_at_least_its_floor_and_every_pin_is_found() -> None:
    assert floor_problems(ROOT) == []
    assert CALLERS <= direct_callers(ROOT)
    assert CLAIMS_SITES <= claims_sites(ROOT)


def test_the_message_names_paragraphs_that_exist() -> None:
    tenancy = (ROOT / ".claude/skills/database/references/tenancy.md").read_text(encoding="utf-8")
    assert "**Named exception" in tenancy


# --- the rails bite, each on a planted tree -----------------------------------------------------------------------------

_ONE_TREE = {"api": 1}


@pytest.fixture()
def planted(tmp_path: Path) -> Path:
    (tmp_path / "api").mkdir()
    return tmp_path


def _plant(root: Path, source: str, name: str = "mod.py") -> None:
    (root / "api" / name).write_text(source, encoding="utf-8")


def test_a_planted_caller_fails_and_the_message_names_the_named_exception_paragraphs(planted: Path) -> None:
    _plant(planted, "from api import tenant_pool\n\ndef sneaks(claims):\n"
                    "    with tenant_pool.tenant_transaction(claims, act_as=None, method='GET') as conn:\n"
                    "        return conn\n")
    problems = caller_problems(planted, _ONE_TREE, frozenset())
    assert len(problems) == 1
    assert "api/mod.py::sneaks" in problems[0]
    assert "Named exception" in problems[0]


@pytest.mark.parametrize("source", [
    "from api.tenant_pool import tenant_transaction\n\ndef bare(c):\n    return tenant_transaction(c)\n",
    "from api.tenant_pool import tenant_transaction as tt\n\ndef bare(c):\n    return tt(c)\n",
])
def test_a_bare_name_call_is_found_and_so_is_an_alias(planted: Path, source: str) -> None:
    _plant(planted, source)
    assert direct_callers(planted, _ONE_TREE) == {"api/mod.py::bare"}


def test_the_tenant_pools_own_unqualified_call_is_found() -> None:
    assert "api/tenant_pool.py::tenant_conn" in direct_callers(ROOT)


def test_a_call_is_keyed_by_its_nearest_function_and_a_module_level_call_by_the_module(planted: Path) -> None:
    _plant(planted, "def outer(c):\n    def inner():\n        return tenant_transaction(c)\n    return inner\n"
                    "class K:\n    def method(self, c):\n        return tenant_transaction(c)\n"
                    "tenant_transaction({})\n")
    assert direct_callers(planted, _ONE_TREE) == {"api/mod.py::outer::inner", "api/mod.py::K::method",
                                                  "api/mod.py::<module>"}


def test_a_moved_or_renamed_pinned_caller_fails_as_stale(planted: Path) -> None:
    _plant(planted, "def renamed(c):\n    return tenant_transaction(c)\n")
    problems = caller_problems(planted, _ONE_TREE, frozenset({"api/mod.py::original"}))
    assert any("api/mod.py::original is pinned but no longer calls" in p for p in problems)
    assert any("api/mod.py::renamed opens a tenant transaction" in p for p in problems)


def test_a_tree_under_its_floor_fails(planted: Path) -> None:
    _plant(planted, "x = 1\n")
    assert floor_problems(planted, {"api": 1}) == []
    assert "under its floor" in floor_problems(planted, {"api": 2})[0]


@pytest.mark.parametrize("source", [
    "def by_hand(conn, user):\n    return Caller(market=conn, claims={'sub': user, 'role': 'authenticated'})\n",
    "def by_hand(conn, built):\n    return Caller(conn, built)\n",
    "def by_hand(conn, built):\n    return Caller(market=conn, claims=built)\n",
    "def by_hand(user):\n    return {'sub': user, 'role': 'authenticated'}\n",
    "def by_hand(user):\n    return dict(sub=user, role='authenticated')\n",
])
def test_a_hand_built_claims_dict_outside_the_builder_fails(planted: Path, source: str) -> None:
    _plant(planted, source)
    assert claims_sites(planted, _ONE_TREE) == {"api/mod.py::by_hand"}
    assert "builds claims by hand" in claims_problems(planted, _ONE_TREE, frozenset())[0]


def test_a_market_only_caller_and_a_lookalike_dict_are_not_claims_sites(planted: Path) -> None:
    _plant(planted, "def market(conn):\n    return Caller(market=conn)\n\n"
                    "def jwt_for_push(a, b):\n    return {'aud': a, 'sub': b}\n")
    assert claims_sites(planted, _ONE_TREE) == set()
