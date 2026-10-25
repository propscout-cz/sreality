"""ONE PIN W4's rail: a property stands at its LOCATION ad, never at its canonical ad (GLOSSARY "Location ad").

Arm 1 reads SQL (every view and function body of the replayed schema, every statement tests/sql_corpus finds) for a
`listing_location` row keyed on the canonical ad; arm 2 registers every call of the served rule with its grain; arm 3
lists the runtime modules that name both `repr_listing_ref_id` and `listing_location`, because SQL assembled inside a
function reaches arm 1 nowhere; arm 4 pins the R7 subject sites to `SUBJECT_LOCATION_JOIN` and every cohort to
`COHORT_LOCATION_FROM`, the shapes an ad-keyed join would otherwise pass (docs/design/one-pin/PROGRAM.md "W4", rulings
R7 and R8); arm 5 registers every read of the publication rule (toolkit/publication.py, rule 16, migrations 668/669),
the served rule's twin. The schema half of arm 1 runs in CI's migrations lane (`TEST_DATABASE_URL`); the rest runs
everywhere.
"""

from __future__ import annotations

import ast
import inspect
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from location_data.claims_common import served_location_predicate
from tests import sql_corpus
from tests._live_property import REQUIRED_DB, cur  # noqa: F401 -- `cur` is the fixture
from toolkit import publication

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "migrations"
CANONICAL = "repr_listing_ref_id"
LOCATION = "location_listing_ref_id"

FIX = ("A property stands at its LOCATION ad: key `listing_location` on `<property>.location_listing_ref_id` (from "
       "one ad: location_data.claims_common's R7 subject fragment; a spatial cohort: its R8 cohort shape). The "
       "canonical ad keeps price, facts, photos and link only. An ad-grain surface keys on the ad itself "
       "(`ll.listing_id = l.id`) with no property in between. A view or function is fixed in a NEW migration (rule 1).")

# The modules that name both `repr_listing_ref_id` and `listing_location`, each for a reason that is not where a
# property stands. A new one fails until it is reviewed and listed here; one that stops naming both must leave.
CENSUS: dict[str, str] = {
    "api/curation.py": "a collection card's portal is its canonical ad's (listings_public `source`); "
                       "`listing_location` only in a comment (the label comes from properties_public)",
    "api/notifications.py": "price alerts follow the canonical ad's price lineage (listing_price_steps, repr_since); "
                            "`listing_location` only in a comment (the feed reads properties_public)",
    "autodedup/ui_sql.py": "identity: which ad is canonical, shown beside each ad's own place in the review pages",
    "location_data/claims_common.py": "the one definition: the R8 cohort shape joins the location ad's property to "
                                      "its canonical ad, the comparable (price, area, snapshot_id: rule 8)",
    "scraper/db.py": "identity: a singleton is born with its one ad as canonical AND location ad; the refetch lane's "
                     "`listing_location` join is ad-grain (`ll.listing_id = l.id`, rule 25's town-less ads)",
}

# Every call of the served rule (rule 25) and every read of its constant, by (module, scope, the listing-id key it is
# rendered on): its grain and why. A property-grain site keys the rule on the property's location ad; an ad-grain
# site on the ad's own row.
SERVED_RULE_SITES: dict[tuple[str, str, str], tuple[str, str]] = {
    ("location_data/claims_common.py", "SERVED_LOCATION_PREDICATE", "l.id"):
        ("ad", "the rule on `listings l`, the text the migrations carry verbatim"),
    ("location_data/location_steps.py", "step_flags_sql", "{listings}.id"):
        ("ad", "the location waterfall's `located` step, one ad at a time"),
    ("toolkit/dedup_candidates_sql.py", "_BASE_CTE", "l.listing_id"):
        ("ad", "dedup pairs ads; `l` is the ad's own listing_location row"),
    ("api/notifications.py", "_build_match_clauses", "l.location_listing_ref_id"):
        ("property", "the watchdog matcher over properties_public, served as browse_projection serves"),
    ("scripts/checks/publishing.py", "_STAGE_TAIL", "p.location_listing_ref_id"):
        ("property", "publishing's not-in-Browse stages, judged as browse_projection judges"),
}

# Bodies of the replayed schema allowed to key a place on the canonical ad: none (the two dead MF functions left in 631).
SCHEMA_ALLOWED: dict[str, str] = {}

# --------------------------------------------------------------------------- the reader of one statement

_NOISE = re.compile(r"--[^\n]*|/\*.*?\*/|'(?:[^']|'')*'", re.S)
_TOKEN = re.compile(r"[a-z_][a-z0-9_$]*(?:\.[a-z_][a-z0-9_$]*)*|\d+(?:\.\d+)?|::|<>|!=|<=|>=|=>|:=|\|\||\S")
_BOUNDARY = frozenset((
    "and or not where join left right inner outer full cross natural lateral on using then when else end group order "
    "limit offset union except intersect from select as is in between like ilike returning having window set values "
    "into for with distinct").split())
_STOPS = frozenset(("=", "<", ">", "<>", "!=", "<=", ">=", ",", ";"))
# An equality is a BINDING (a join or a filter) when it opens an ON, a WHERE or a conjunct; one in a select list or a
# CASE only reads whether the ad is canonical.
_BINDING = frozenset(("on", "where", "and"))


@dataclass(frozen=True)
class Key:
    """One `<listing_location alias>.listing_id = <key>` in a statement."""
    key: str
    at: int
    canonical: bool


def _clean(sql: str) -> str:
    """Lower-cased, comments and string literals blanked: offsets survive, prose never reads as SQL."""
    return _NOISE.sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), sql).lower()


def _aliases(text: str, table: str) -> set[str]:
    names = {table}
    for m in re.finditer(rf"\b(?:public\.)?{table}\b(?!\.)\s+(?:as\s+)?([a-z_][a-z0-9_]*)", text):
        if m.group(1) not in _BOUNDARY:
            names.add(m.group(1))
    return names


def _side(tokens: list[tuple[str, int]], i: int, step: int) -> tuple[int, int]:
    """The token span [lo, hi) on one side of tokens[i], up to the first boundary at its own depth."""
    depth, j = 0, i + step
    opening, closing = ("(", ")") if step > 0 else (")", "(")
    while 0 <= j < len(tokens):
        t = tokens[j][0]
        if t == closing:
            if not depth:
                break
            depth -= 1
        elif t == opening:
            depth += 1
        elif not depth and (t in _STOPS or t in _BOUNDARY):
            break
        j += step
    return (i + 1, j) if step > 0 else (j + 1, i)


def _opener(tokens: list[tuple[str, int]], lo: int) -> str:
    """The first token before a left side that is not an opening bracket."""
    j = lo - 1
    while j >= 0 and tokens[j][0] == "(":
        j -= 1
    return tokens[j][0] if j >= 0 else ""


def _names_canonical(words: list[str]) -> bool:
    return any(w == CANONICAL or w.endswith("." + CANONICAL) for w in words)


def location_keys(statement: str) -> list[Key]:
    """Every key a `listing_location` row is looked up by in one cleaned statement, and whether it is the canonical ad
    (named, or an alias the statement binds to it with `x.id = ….repr_listing_ref_id`)."""
    places = _aliases(statement, "listing_location")
    tokens = [(m.group(0), m.start()) for m in _TOKEN.finditer(statement)]
    sides = []
    for i, (t, _) in enumerate(tokens):
        if t == "=":
            left, right = _side(tokens, i, -1), _side(tokens, i, 1)
            sides.append((i, left, [w for w, _ in tokens[slice(*left)] if w not in "()"],
                          [w for w, _ in tokens[slice(*right)] if w not in "()"], right))
    bound = {a[0][:-3] for _, left, lw, rw, _ in sides if _opener(tokens, left[0]) in _BINDING
             for a, b in ((lw, rw), (rw, lw)) if len(a) == 1 and a[0].endswith(".id") and _names_canonical(b)}
    keys = []
    for i, left, lw, rw, right in sides:
        for a, b, span in ((lw, rw, right), (rw, lw, left)):
            if b and len(a) == 1 and a[0].endswith(".listing_id") and a[0][:-11].removeprefix("public.") in places:
                lo, hi = tokens[span[0]][1], tokens[span[1] - 1][1] + len(tokens[span[1] - 1][0])
                indirect = len(b) == 1 and b[0].endswith(".id") and b[0][:-3] in bound
                keys.append(Key(" ".join(statement[lo:hi].split()), tokens[i][1],
                                _names_canonical(b) or indirect))
    return keys


def offences(sql: str) -> list[Key]:
    """The canonical keys of every statement that names both the canonical pointer and the place store."""
    text = _clean(sql)
    if CANONICAL not in text or not re.search(r"\blisting_location\b|served_location_predicate", text):
        return []
    found, offset = [], 0
    for statement in text.split(";"):
        if CANONICAL in statement and re.search(r"\blisting_location\b", statement):
            found += [Key(k.key, offset + k.at, True) for k in location_keys(statement) if k.canonical]
        offset += len(statement) + 1
    return found


# --------------------------------------------------------------------------- where a finding lives


def _def_line(path: str, name: str) -> int | None:
    for n, line in enumerate((ROOT / path).read_text(encoding="utf-8").splitlines(), 1):
        if re.match(rf"\s*{re.escape(name)}\s*[:=]", line):
            return n
    return None


def _corpus_where(item: sql_corpus.SqlItem, at: int) -> str:
    """`path:line` of the offending line, found in the module's source; the statement's own line otherwise."""
    path = item.origin.split(":")[0]
    line = item.sql.split("\n")[item.sql[:at].count("\n")].strip()
    m = re.search(r":(\d+)$", item.origin)
    start = int(m.group(1)) if m else (_def_line(path, item.name or "") or 1)
    source = (ROOT / path).read_text(encoding="utf-8").splitlines()
    if len(line) >= 12:
        for n in [*range(start, len(source) + 1), *range(1, start)]:
            if line in source[n - 1]:
                return f"{path}:{n}"
    return f"{path}:{start}"


def _migration_where(name: str, key: str) -> str:
    """`migrations/NNN_*.sql:line` of the newest CREATE of a schema object, at the line naming its key if found."""
    bare = name.split("(")[0].split(".")[-1]
    create = re.compile(rf"create\s+(?:or\s+replace\s+)?(?:materialized\s+)?(?:view|function)\s+"
                        rf"(?:if\s+not\s+exists\s+)?(?:public\.)?{re.escape(bare)}\b", re.I)
    word = CANONICAL if CANONICAL in key or not key else key.split(".")[-1].strip("() ")
    for path in sorted(MIGRATIONS.glob("[0-9]*.sql"), reverse=True):
        text = path.read_text(encoding="utf-8")
        hits = list(create.finditer(text))
        if hits:
            start = text.count("\n", 0, hits[-1].start()) + 1
            for n, line in enumerate(text.splitlines()[start - 1:], start):
                if re.search(r"listing_id\s*=|=\s*\(*[\w.]*listing_id\b", line.lower()) and word in line.lower():
                    return f"{path.relative_to(ROOT)}:{n}"
            return f"{path.relative_to(ROOT)}:{start}"
    return name


# --------------------------------------------------------------------------- arm 1: statements and bodies

_FLAGGED = [
    "select 1 from properties p left join listing_location ll on ll.listing_id = p.repr_listing_ref_id",
    " SELECT p.id FROM (properties p LEFT JOIN listing_location ll ON ((ll.listing_id = p.repr_listing_ref_id)))",
    "select p.id from properties p join public.listing_location ll on p.repr_listing_ref_id::bigint = ll.listing_id",
    f"select p.id from properties p where not {served_location_predicate('p.repr_listing_ref_id')}",
    "select (select ll.resolved_at from listing_location ll where ll.listing_id = p.repr_listing_ref_id) > now() "
    "from properties p",
    "select 1 from listings l left join properties p on p.id = l.property_id "
    "left join listing_location ll on ll.listing_id = coalesce(p.repr_listing_ref_id, l.id)",
    "select 1 from properties p join listings l on l.id = p.repr_listing_ref_id "
    "join listing_location ll on ll.listing_id = l.id",
    "select l.id from listings l join listing_location ll on ll.listing_id = l.id where exists (select 1 from "
    "properties canon_p where canon_p.id = l.property_id and canon_p.repr_listing_ref_id = l.id)",
]
_PASSING = [
    "select 1 from listings l join listing_location ll on ll.listing_id = l.id",
    "select l.id, p.repr_listing_ref_id = l.id as is_canonical from listings l join properties p "
    "on p.id = l.property_id join listing_location ll on ll.listing_id = l.id",
    "select p.repr_listing_ref_id as listing_id from properties p "
    "left join listing_location ll on ll.listing_id = p.location_listing_ref_id",
    f"select 1 from properties p where {served_location_predicate('p.location_listing_ref_id')} "
    "and p.repr_listing_ref_id is not null",
    "select 1 from listings l left join properties lp on lp.id = l.property_id and lp.status = 'active' "
    "left join listing_location ll on ll.listing_id = coalesce(lp.location_listing_ref_id, l.id) "
    "join properties p on p.repr_listing_ref_id = l.id",
    "select l.id from listing_location ll join properties lp on lp.location_listing_ref_id = ll.listing_id "
    "and lp.status = 'active' join listings l on l.id = lp.repr_listing_ref_id and l.property_id = lp.id",
    "select 1 from properties p join listings l on l.id = p.repr_listing_ref_id -- listing_location ll.listing_id = l.id",
]


@pytest.mark.parametrize("sql", _FLAGGED)
def test_the_reader_flags_each_way_of_placing_a_property_at_its_canonical_ad(sql: str) -> None:
    assert offences(sql), sql


@pytest.mark.parametrize("sql", _PASSING)
def test_the_reader_passes_an_ads_own_place_and_the_location_ad(sql: str) -> None:
    assert offences(sql) == [], sql


def test_the_two_definers_name_no_place_and_pass() -> None:
    """They set both pointers; neither reads `listing_location`, so neither is a reader."""
    from scraper.db import NEW_SINGLETONS_SQL
    from scripts.recompute_property_stats import _RECOMPUTE_BATCH_SQL

    for sql in (NEW_SINGLETONS_SQL, _RECOMPUTE_BATCH_SQL):
        assert CANONICAL in sql and LOCATION in sql
        assert "listing_location" not in _clean(sql) and offences(sql) == []


def _corpus() -> list[sql_corpus.SqlItem]:
    corpus = sql_corpus.discover(include_inline=True, resolve_imports=True)
    assert len(corpus) > 500, f"only {len(corpus)} SQL statements discovered: the corpus scan is broken"
    return corpus


def test_the_corpus_reaches_the_f_strings_this_arm_exists_for() -> None:
    """publishing's statements live in f-strings; without `resolve_imports` the arm would read past them."""
    corpus = {item.origin: item.sql for item in _corpus()}
    for name in ("_VISIBLE_SQL", "_STOCK_SQL"):
        sql = corpus.get(f"scripts/checks/publishing.py::{name}")
        assert sql is not None, f"scripts/checks/publishing.py {name} is missing from the corpus (an import failed?)"
        keys = [k.key for statement in _clean(sql).split(";") for k in location_keys(statement)]
        assert keys and all(k.endswith(LOCATION) for k in keys), (name, keys)


def test_no_statement_places_a_property_at_its_canonical_ad() -> None:
    bad = [f"{_corpus_where(item, k.at)}: `listing_location` keyed on `{k.key}`"
           for item in _corpus() for k in offences(item.sql)]
    assert not bad, "\n".join([*bad, FIX])


_BODIES_SQL = """
select c.oid::regclass::text, pg_get_viewdef(c.oid)
  from pg_class c join pg_namespace n on n.oid = c.relnamespace
 where c.relkind in ('v', 'm') and n.nspname not in ('pg_catalog', 'information_schema')
   and not exists (select 1 from pg_depend d
                    where d.classid = 'pg_class'::regclass and d.objid = c.oid and d.deptype = 'e')
union all
select p.oid::regprocedure::text,
       case when p.prosqlbody is not null then pg_get_functiondef(p.oid) else p.prosrc end
  from pg_proc p
  join pg_namespace n on n.oid = p.pronamespace
  join pg_language lg on lg.oid = p.prolang
 where n.nspname not in ('pg_catalog', 'information_schema') and lg.lanname in ('sql', 'plpgsql')
   and not exists (select 1 from pg_depend d
                    where d.classid = 'pg_proc'::regclass and d.objid = p.oid and d.deptype = 'e')
"""

# The three bodies W4 re-keyed (migration 636): the arm must read each, and each must key its place on the location ad.
_REKEYED = ("browse_projection", "properties_public", "recompute_city_proximity(boolean)")


@REQUIRED_DB
def test_no_view_or_function_body_places_a_property_at_its_canonical_ad(cur: Any) -> None:  # noqa: F811
    cur.execute(_BODIES_SQL)
    bodies = {name: body or "" for name, body in cur.fetchall()}
    for name in _REKEYED:
        found = [b for n, b in bodies.items() if n.removeprefix("public.") == name]
        assert len(found) == 1, f"the replayed schema holds {len(found)} bodies named {name}"
        body = found[0]
        keys = [k.key for statement in _clean(body).split(";") for k in location_keys(statement)]
        assert keys and all(k.endswith(LOCATION) for k in keys), f"{name} keys its place on {keys}"
    bad = [f"{_migration_where(name, k.key)} ({name}): `listing_location` keyed on `{k.key}`"
           for name, body in sorted(bodies.items()) if name not in SCHEMA_ALLOWED for k in offences(body)]
    assert not bad, "\n".join([*bad, FIX])
    assert not set(SCHEMA_ALLOWED) - set(bodies), "an allow-listed body no longer exists: remove it"


# --------------------------------------------------------------------------- arm 2: the served rule's call sites

_DEFAULT_KEY: str = inspect.signature(served_location_predicate).parameters["listing_id"].default


@dataclass(frozen=True)
class Site:
    path: str
    line: int
    scope: str
    key: str


def _key(call: ast.Call) -> str:
    """The listing-id expression a call renders the rule on; a non-literal part as `{expr}`."""
    node = call.args[0] if call.args else next((k.value for k in call.keywords if k.arg == "listing_id"), None)
    if node is None:
        return _DEFAULT_KEY
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(v.value if isinstance(v, ast.Constant) else "{" + ast.unparse(v.value) + "}"
                       for v in node.values)
    return "{" + ast.unparse(node) + "}"


class _Sites(ast.NodeVisitor):
    """Every `served_location_predicate(` call and `SERVED_LOCATION_PREDICATE` read, with its enclosing scope (the
    function, or the module-level name it is assigned to)."""

    def __init__(self, path: str) -> None:
        self.path, self.scope, self.sites = path, [], []

    def _within(self, name: str, node: ast.AST) -> None:
        self.scope.append(name)
        self.generic_visit(node)
        self.scope.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) -> None:
        self._within(node.name, node)

    visit_AsyncFunctionDef = visit_ClassDef = visit_FunctionDef

    def visit_Assign(self, node: ast.Assign) -> None:
        names = [t.id for t in node.targets if isinstance(t, ast.Name)]
        if not self.scope and names:
            self._within(names[0], node)
        else:
            self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if not self.scope and isinstance(node.target, ast.Name):
            self._within(node.target.id, node)
        else:
            self.generic_visit(node)

    def _add(self, node: ast.AST, key: str) -> None:
        self.sites.append(Site(self.path, node.lineno, ".".join(self.scope) or "<module>", key))  # type: ignore[attr-defined]

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        if (func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)) == "served_location_predicate":
            self._add(node, _key(node))
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if node.id == "SERVED_LOCATION_PREDICATE" and isinstance(node.ctx, ast.Load):
            self._add(node, _DEFAULT_KEY)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr == "SERVED_LOCATION_PREDICATE" and isinstance(node.ctx, ast.Load):
            self._add(node, _DEFAULT_KEY)
        self.generic_visit(node)


def _served_rule_sites() -> list[Site]:
    sites: list[Site] = []
    for path in sql_corpus._source_files():
        rel = path.relative_to(ROOT).as_posix()
        visitor = _Sites(rel)
        visitor.visit(ast.parse(path.read_text(encoding="utf-8"), rel))
        sites += visitor.sites
    return sites


def test_every_call_of_the_served_rule_is_registered_with_its_grain() -> None:
    """A new call site fails until it says whether it serves an ad or a property."""
    sites = _served_rule_sites()
    found = Counter((s.path, s.scope, s.key) for s in sites)
    new = [f"{s.path}:{s.line}: served_location_predicate keyed on `{s.key}` in {s.scope} is not registered. Add "
           f"({s.path!r}, {s.scope!r}, {s.key!r}): (grain, why) to SERVED_RULE_SITES; a 'property' grain keys the "
           f"rule on the property's location ad (`p.{LOCATION}`, `l.{LOCATION}` on properties_public), an 'ad' grain "
           f"on the ad's own row." for s in sites if (s.path, s.scope, s.key) not in SERVED_RULE_SITES]
    gone = [f"{path} {scope}: served_location_predicate keyed on `{key}` is registered but no longer called: "
            f"remove it from SERVED_RULE_SITES" for path, scope, key in SERVED_RULE_SITES
            if (path, scope, key) not in found]
    assert not new + gone, "\n".join(new + gone)


def test_a_property_grain_call_keys_the_rule_on_the_location_ad() -> None:
    """The watchdog's `l.listing_id` on properties_public was the canonical ad by another name."""
    lines = {(s.path, s.scope, s.key): s.line for s in _served_rule_sites()}
    bad = []
    for (path, scope, key), (grain, _) in SERVED_RULE_SITES.items():
        where = f"{path}:{lines.get((path, scope, key), '?')} ({scope})"
        assert grain in ("ad", "property"), f"{where}: grain {grain!r} is neither 'ad' nor 'property'"
        if CANONICAL in key or (grain == "property" and not key.endswith(LOCATION)):
            bad.append(f"{where}: a {grain}-grain served rule keyed on `{key}`; a property is served by its "
                       f"location ad: pass `<property>.{LOCATION}`")
        if grain == "ad" and "_listing_ref_id" in key:
            bad.append(f"{where}: an ad-grain served rule keyed on a property pointer `{key}`: key it on the ad's "
                       f"own id, or register it as 'property'")
    assert not bad, "\n".join(bad)


# --------------------------------------------------------------------------- arm 3: the module census


def _both(text: str) -> list[int]:
    """The lines naming either word, when the module names both."""
    words = (rf"\b{CANONICAL}\b", r"\blisting_location\b")
    if not all(re.search(w, text) for w in words):
        return []
    return [n for n, line in enumerate(text.splitlines(), 1) if any(re.search(w, line) for w in words)]


def test_the_modules_naming_both_ads_are_reviewed() -> None:
    named = {}
    for path in sql_corpus._source_files():
        lines = _both(path.read_text(encoding="utf-8"))
        if lines:
            named[path.relative_to(ROOT).as_posix()] = lines
    new = [f"{path}:{lines[0]} (lines {', '.join(map(str, lines[:10]))}): names both {CANONICAL} and "
           f"listing_location. If it places a property, key the place on {LOCATION} ({FIX}) If the canonical ad is "
           f"read for another reason (price, identity, link), add {path!r} to CENSUS with that reason."
           for path, lines in sorted(named.items()) if path not in CENSUS]
    gone = [f"{path}: no longer names both {CANONICAL} and listing_location: remove it from CENSUS"
            for path in sorted(set(CENSUS) - set(named))]
    assert not new + gone, "\n".join(new + gone)


# --------------------------------------------------------------------------- arm 4: the R7 subjects and R8 cohorts

# The scopes that place one ad's subject (ruling R7): each takes its place through the shared subject fragment. An
# ad-keyed `LEFT JOIN listing_location ll ON ll.listing_id = l.id` passes arm 1, so this list is what pins them.
R7_SUBJECT_SITES: dict[tuple[str, str], str] = {
    ("api/estimation_runs.py", "_MATCH_BY_ID_SQL"): "an estimate's subject by listing id",
    ("api/estimation_runs.py", "_LIST_FROM"): "the /estimations list's place label (location only; facts diverge)",
    ("api/estimation_runs.py", "_SUBJECT_MF_FACTS_SQL"): "the subject's MF reference-rent facts",
    ("toolkit/velocity.py", "_fetch_listing_for_velocity"): "the velocity subject",
}
_COHORT_CALL = "_shared_filter_where"


class _Refs(_Sites):
    """Every read of one of `names`, with its enclosing scope."""

    def __init__(self, path: str, names: frozenset[str]) -> None:
        super().__init__(path)
        self.names = names

    def visit_Call(self, node: ast.Call) -> None:
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if node.id in self.names and isinstance(node.ctx, ast.Load):
            self._add(node, node.id)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr in self.names and isinstance(node.ctx, ast.Load):
            self._add(node, node.attr)
        self.generic_visit(node)


def _references(*names: str) -> list[Site]:
    sites: list[Site] = []
    for path in sql_corpus._source_files():
        rel = path.relative_to(ROOT).as_posix()
        visitor = _Refs(rel, frozenset(names))
        visitor.visit(ast.parse(path.read_text(encoding="utf-8"), rel))
        sites += visitor.sites
    return sites


def test_every_r7_subject_site_takes_its_place_from_the_shared_subject_fragment() -> None:
    found = {(s.path, s.scope) for s in _references("SUBJECT_LOCATION_JOIN")}
    missing = [f"{path} {scope} ({why}): does not reference claims_common.SUBJECT_LOCATION_JOIN. Place the subject "
               f"with it (ruling R7), or remove the entry if the site is gone" for (path, scope), why
               in R7_SUBJECT_SITES.items() if (path, scope) not in found]
    assert not missing, "\n".join(missing)


def test_every_cohort_counts_a_property_once_through_the_shared_cohort_shape() -> None:
    """Decision 13's EXISTS is gone (636): a cohort counts a property once only when its FROM is the R8 shape."""
    refs = _references(_COHORT_CALL, "COHORT_LOCATION_FROM")
    shaped = {(s.path, s.scope) for s in refs if s.key == "COHORT_LOCATION_FROM"}
    bad = [f"{s.path}:{s.line} ({s.scope}): calls {_COHORT_CALL} without claims_common.COHORT_LOCATION_FROM. A cohort's "
           f"FROM is the R8 shape (place -> the live property it locates -> its canonical ad); `FROM listings l JOIN "
           f"listing_location ll ON ll.listing_id = l.id` counts every ad of a property" for s in refs
           if s.key == _COHORT_CALL and (s.path, s.scope) not in shaped]
    assert any(s.key == _COHORT_CALL for s in refs), f"no call of {_COHORT_CALL} found: the scan is broken"
    assert not bad, "\n".join(bad)


# --------------------------------------------------------------------------- arm 5: the publication rule's readers

# Every Python read of toolkit/publication.py, by (module, scope, what it renders): its grain and why. The rule is a
# property's; browse_projection carries its text verbatim (tests/test_668_publication_gate.py), these render it.
PUBLISHED_RULE_SITES: dict[tuple[str, str, str], tuple[str, str]] = {
    ("api/notifications.py", "_build_match_clauses", 'published_property_predicate("l.property_id")'):
        ("property", "both watchdog producers over properties_public, published as browse_projection publishes"),
    ("api/notifications.py", "match_once", 'visible_since_sql("pp")'):
        ("property", "the 'new' window: a watchdog tells what became visible after it started"),
    ("scripts/recompute_property_stats.py", "_SETTLE_SQL", "CAP_SQL"):
        ("property", "the stamper reads only properties the cap has not yet published"),
    ("scripts/checks/publishing.py", "_STAGE_TAIL", 'visible_since_sql("p")'):
        ("property", "publishing's 'unpublished' stage, judged at the newest rebuild's start"),
    ("toolkit/property_identity.py", "_CARRY_SETTLED_SQL", "CAP_SQL"):
        ("property", "a merge passes a stamp to the survivor only when that publishes it before its cap"),
}
_PUBLICATION_FILE = "toolkit/publication.py"
_PUBLICATION_NAMES = {n: v for n, v in vars(publication).items() if not n.startswith("_")
                      and (isinstance(v, str) or getattr(v, "__module__", None) == publication.__name__)}


def _literal(node: ast.expr) -> str:
    return f'"{node.value}"' if isinstance(node, ast.Constant) and isinstance(node.value, str) else (
        "{" + ast.unparse(node) + "}")


def _rendered(name: str, call: ast.Call) -> str:
    """A call by its arguments, the first one defaulted as the function defaults it (the served census's `_key`)."""
    first = next(iter(inspect.signature(_PUBLICATION_NAMES[name]).parameters.values()))
    node = call.args[0] if call.args else next((k.value for k in call.keywords if k.arg == first.name), None)
    parts = [_literal(node) if node is not None else f'"{first.default}"', *map(_literal, call.args[1:])]
    parts += [f"{k.arg}={_literal(k.value)}" for k in call.keywords if k.arg != first.name]
    return f"{name}({', '.join(parts)})"


class _PublicationReads(_Sites):
    """Every read of a toolkit/publication.py name: a call keyed by what it renders, a constant by its name."""

    def __init__(self, path: str) -> None:
        super().__init__(path)
        self.imports = False

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        self.imports |= node.module == "toolkit.publication" or (
            node.module == "toolkit" and any(a.name == "publication" for a in node.names))

    def visit_Import(self, node: ast.Import) -> None:
        self.imports |= any(a.name == "toolkit.publication" for a in node.names)

    def visit_Call(self, node: ast.Call) -> None:
        name = node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", None)
        if name in _PUBLICATION_NAMES and callable(_PUBLICATION_NAMES[name]):
            self._add(node, _rendered(name, node))
            for arg in [*node.args, *(k.value for k in node.keywords)]:
                self.visit(arg)
        else:
            self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if node.id in _PUBLICATION_NAMES and isinstance(node.ctx, ast.Load):
            self._add(node, node.id)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr in _PUBLICATION_NAMES and isinstance(node.ctx, ast.Load):
            self._add(node, node.attr)
        else:
            self.generic_visit(node)


def test_every_read_of_the_publication_rule_is_registered() -> None:
    """A new reader fails until it says what it renders and why; a registered one that stops reading must leave."""
    sites: list[Site] = []
    importers: set[str] = set()
    for path in sql_corpus._source_files():
        rel = path.relative_to(ROOT).as_posix()
        if rel == _PUBLICATION_FILE:
            continue
        visitor = _PublicationReads(rel)
        visitor.visit(ast.parse(path.read_text(encoding="utf-8"), rel))
        sites += visitor.sites
        if visitor.imports:
            importers.add(rel)
    found = Counter((s.path, s.scope, s.key) for s in sites)
    new = [f"{s.path}:{s.line}: {s.key} in {s.scope} is not registered. Add ({s.path!r}, {s.scope!r}, {s.key!r}): "
           f"(grain, why) to PUBLISHED_RULE_SITES" for s in sites if (s.path, s.scope, s.key) not in PUBLISHED_RULE_SITES]
    missing = [f"{path} {scope}: {key} is registered but not read there: render the rule there through "
               f"toolkit.publication, or remove the entry" for path, scope, key in PUBLISHED_RULE_SITES
               if (path, scope, key) not in found]
    silent = [f"{path}: imports toolkit.publication and reads nothing of it" for path in sorted(importers)
              if path not in {s.path for s in sites}]
    assert not new + missing + silent, "\n".join(new + missing + silent)


def _sql_strings(tree: ast.AST) -> list[tuple[int, str]]:
    """Every string literal but a docstring: the SQL a module runs, never its prose."""
    docs = {id(n.body[0].value) for n in ast.walk(tree)
            if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            and n.body and isinstance(n.body[0], ast.Expr) and isinstance(n.body[0].value, ast.Constant)}
    return [(n.lineno, n.value) for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docs]


def test_no_module_spells_the_publication_rule_by_hand() -> None:
    """One rule and one cap, spelled in toolkit/publication.py only: a copy in SQL would drift from browse_projection."""
    rule = re.compile(r"coalesce\(\s*[\w.]*settled_at", re.I)
    bad = []
    for path in sql_corpus._source_files():
        rel = path.relative_to(ROOT).as_posix()
        if rel == _PUBLICATION_FILE:
            continue
        for line, text in _sql_strings(ast.parse(path.read_text(encoding="utf-8"), rel)):
            if rule.search(text) or ("first_seen_at" in text and publication.CAP_SQL in text):
                bad.append(f"{rel}:{line}: the publication rule or its cap spelled by hand")
    assert not bad, "\n".join([*bad, "render it with toolkit.publication (visible_since_sql, CAP_SQL, ...)"])
