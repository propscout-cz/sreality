"""Curation sync census (docs/design/curation-sync.md § 6 W3): the curation clock's rails, offline.

The clock (migration 696) is one counter per (account, family), advanced only by bump_curation_clock() on twelve
deferred constraint row triggers. Three places name its tables and families and must agree: the migrations' triggers
and family CHECK, tests/_tenant_registry.py (CLOCKED_TABLES / NOT_CLOCKED) and the SPA's table→read map
(frontend/src/lib/curationSync.ts SYNC_TABLES / CURATION_FAMILIES). Each rail is a function returning its problems,
asserted empty on the repository and shown to bite on a mutated copy of its input:

  (a) the trigger set the migrations leave (after drops of a trigger, the function or a table, and renames of a table
      or a trigger) == CLOCKED_TABLES == SYNC_TABLES' table→family, each trigger still `<table>_curation_clock`
  (b) TENANT_TABLES ⊆ CLOCKED_TABLES ∪ NOT_CLOCKED, disjoint, every reason non-empty
  (c) the newest family CHECK == CURATION_FAMILIES (TS) == the families of CLOCKED_TABLES == the seed's families
  (d) every clock trigger is `<table>_curation_clock`, a constraint row trigger after insert/update/delete,
      deferrable initially deferred
  (e) nothing disables a clock trigger or sets it to fire only on replicas (named, `all` or `user`), sets
      session_replication_role, or redefines bump_curation_clock() after the clock's migration, without
      `-- ci-allow-clock-disable: <reason>`
  (f) no clock trigger on listings, properties or listing_snapshots
  (g) no x-sync-origin header or origin GUC in api/, and nobody outside the clock migration names its dedupe GUC
  (h) no DML on curation_clock in api/ toolkit/ scraper/ scripts/ autodedup/ or in any migration after the clock's,
      which writes it exactly twice (the bump and the seed)
  (i) the read policy's arms map onto 641's split: board arm = _BOARD_POLICIES ∩ clocked, edit arm =
      (_ACTING_POLICIES − board) ∩ clocked, `shares` only in the own arm; the audience never acts
  (j) THRESHOLDS["CURATION_CLOCK_TRIGGERS"] == len(CLOCKED_TABLES) == the clock migration's $post$ count
"""

from __future__ import annotations

import functools
import re
from pathlib import Path

from scripts.verify_pipeline import THRESHOLDS
from tests._tenant_registry import BROWSER_WRITE_COLUMNS, CLOCKED_TABLES, NOT_CLOCKED, TENANT_TABLES
from tests.test_migration_rls_grants import (
    _ACTING_POLICIES,
    _BOARD_POLICIES,
    _final_policy_expressions,
    _migration_number,
    _statements,
    _strip_comments,
)

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "migrations"
SYNC_TS = ROOT / "frontend" / "src" / "lib" / "curationSync.ts"
CODE_DIRS = ("api", "toolkit", "scraper", "scripts", "autodedup")
NEVER_CLOCKED = ("listings", "properties", "listing_snapshots")
# A line of its own: a header that merely quotes the annotation (696's does) sanctions nothing.
DISABLE_ALLOW = re.compile(r"^\s*--\s*ci-allow-clock-disable:\s*\S", re.M)

Migrations = list[tuple[str, str]]


def _norm(stmt: str) -> str:
    return re.sub(r"\s+", " ", stmt.lower()).strip()


@functools.cache
def _read_migrations() -> tuple[tuple[str, str], ...]:
    paths = sorted((p for p in MIGRATIONS.glob("*.sql") if _migration_number(p) is not None),
                   key=lambda p: (_migration_number(p), p.name))
    return tuple((p.name, p.read_text(encoding="utf-8")) for p in paths)


def _migrations() -> Migrations:
    return list(_read_migrations())


def _number(name: str) -> int:
    return int(name.split("_", 1)[0])


def _creating_clock(migrations: Migrations) -> list[tuple[str, str]]:
    return [(n, t) for n, t in migrations if "curation_clock" in t
            and re.search(r"\bcreate table (?:if not exists )?(?:public\.)?curation_clock\b",
                          _norm(_strip_comments(t)))]


def _clock_migration(migrations: Migrations) -> tuple[str, str]:
    """The migration that creates the clock (696, or wherever the merge coordinator numbers it)."""
    made = _creating_clock(migrations)
    assert len(made) == 1, f"exactly one migration creates curation_clock: {[n for n, _ in made]}"
    return made[0]


def _code_files() -> list[Path]:
    return [p for d in CODE_DIRS for p in sorted((ROOT / d).rglob("*"))
            if p.suffix in (".py", ".sql") and p.is_file()]


@functools.cache
def _code() -> tuple[tuple[str, str], ...]:
    return tuple(_texts(_code_files()))


# --- (a) and (d) and (f): the clock triggers ---------------------------------------------------------------------

_CREATE_TRIGGER = re.compile(r"^create (?:or replace )?(?:constraint )?trigger (\w+) .*? on (?:only )?"
                             r"(?:public\.)?(\w+) ")
_DROP_TRIGGER = re.compile(r"^drop trigger (?:if exists )?(\w+) on (?:only )?(?:public\.)?(\w+)")
_DROP_BUMP = re.compile(r"^drop (?:function|routine) .*\bbump_curation_clock\b")
_DROP_TABLE = re.compile(r"^drop table (?:if exists )?(.*?)(?: cascade| restrict)?$")
_RENAME_TABLE = re.compile(r"^alter table (?:if exists )?(?:only )?(?:public\.)?(\w+) rename to (\w+)$")
_RENAME_TRIGGER = re.compile(r"^alter trigger (\w+) on (?:public\.)?(\w+) rename to (\w+)$")
_BUMP_CALL = re.compile(r"execute (?:function|procedure) (?:public\.)?bump_curation_clock\s*\(\s*'(\w+)'\s*\)")


def clock_trigger_statements(migrations: Migrations) -> list[tuple[str, str, str, str, str | None]]:
    """(file, trigger, table, statement, family) per CREATE TRIGGER that is a clock trigger by name or function."""
    out = []
    for name, text in migrations:
        for stmt in _statements(text):
            low = _norm(stmt)
            m = _CREATE_TRIGGER.match(low)
            if m and (m.group(1).endswith("_curation_clock") or "bump_curation_clock" in low):
                call = _BUMP_CALL.search(low)
                out.append((name, m.group(1), m.group(2), low, call.group(1) if call else None))
    return out


def final_clock_state(migrations: Migrations) -> dict[tuple[str, str], str | None]:
    """(table, trigger) → family of every clock trigger the migrations leave in place, replayed in numeric order: a
    trigger drop, a drop of the bump function (only CASCADE succeeds while a trigger uses it) or of the table removes
    it; a table or trigger rename moves it."""
    state: dict[tuple[str, str], str | None] = {}
    for name, text in migrations:
        for stmt in _statements(text):
            low = _norm(stmt)
            m = _CREATE_TRIGGER.match(low)
            if m and (m.group(1).endswith("_curation_clock") or "bump_curation_clock" in low):
                call = _BUMP_CALL.search(low)
                state[(m.group(2), m.group(1))] = call.group(1) if call else None
            elif m := _DROP_TRIGGER.match(low):
                state.pop((m.group(2), m.group(1)), None)
            elif _DROP_BUMP.match(low):
                state.clear()
            elif m := _DROP_TABLE.match(low):
                gone = {t.strip().removeprefix("public.") for t in m.group(1).split(",")}
                state = {k: v for k, v in state.items() if k[0] not in gone}
            elif m := _RENAME_TABLE.match(low):
                old, new = m.groups()
                state = {(new if t == old else t, trig): v for (t, trig), v in state.items()}
            elif m := _RENAME_TRIGGER.match(low):
                old, table, new = m.groups()
                state = {(t, new if (t, trig) == (table, old) else trig): v for (t, trig), v in state.items()}
    return state


def final_clock_triggers(migrations: Migrations) -> dict[str, str | None]:
    """table → family of every clock trigger the migrations leave in place."""
    state = final_clock_state(migrations)
    tables = [table for table, _trigger in state]
    assert len(tables) == len(set(tables)), f"a table carries two clock triggers: {sorted(state)}"
    return {table: family for (table, _trigger), family in state.items()}


def _ts_block(text: str, start: str, end: str) -> str:
    assert start in text, f"{SYNC_TS.name} lost `{start}`"
    return text.split(start, 1)[1].split(end, 1)[0]


def ts_sync_tables(text: str) -> dict[str, str]:
    """SYNC_TABLES' table → family, with a guard that every top-level key was read."""
    body = _ts_block(text, "export const SYNC_TABLES = {", "\n} as const")
    keys = re.findall(r"^  (\w+):", body, re.M)
    families = dict(re.findall(r"^  (\w+): \{\s*family: '(\w+)'", body, re.M))
    assert keys and set(keys) == set(families), f"SYNC_TABLES parse went blind: keys {keys}, read {families}"
    return families


def ts_curation_families(text: str) -> list[str]:
    return re.findall(r"'(\w+)'", _ts_block(text, "export const CURATION_FAMILIES = [", "]"))


def rail_a(migrations: Migrations, ts_text: str, clocked: dict[str, str]) -> list[str]:
    problems = []
    triggers, sync = final_clock_triggers(migrations), ts_sync_tables(ts_text)
    if triggers != clocked:
        problems.append(f"migrations' clock triggers {sorted(triggers.items())} != CLOCKED_TABLES "
                        f"{sorted(clocked.items())}")
    if sync != clocked:
        problems.append(f"SYNC_TABLES {sorted(sync.items())} != CLOCKED_TABLES {sorted(clocked.items())}")
    problems += [f"the clock trigger on {table} is named {trigger}, not {table}_curation_clock"
                 for table, trigger in final_clock_state(migrations) if trigger != f"{table}_curation_clock"]
    return problems


def rail_d(migrations: Migrations) -> list[str]:
    problems = []
    for name, trigger, table, low, family in clock_trigger_statements(migrations):
        want = (f"create constraint trigger {table}_curation_clock after insert or update or delete on public.{table} "
                f"deferrable initially deferred for each row execute function public.bump_curation_clock('{family}')")
        if family is None or low != want:
            problems.append(f"{name}: {trigger} is not the deferred constraint row trigger shape: {low}")
    return problems


def rail_f(migrations: Migrations, clocked: dict[str, str]) -> list[str]:
    on_market = sorted({f"{name}: {table}" for name, _t, table, _low, _f in clock_trigger_statements(migrations)
                        if table in NEVER_CLOCKED} | {f"CLOCKED_TABLES: {t}" for t in clocked if t in NEVER_CLOCKED})
    return [f"a clock on market data (ad status and price are out, § 3.7): {x}" for x in on_market]


# --- (b) the registry covers every tenant table -------------------------------------------------------------------

def rail_b(tenant: tuple[str, ...], clocked: dict[str, str], not_clocked: dict[str, str]) -> list[str]:
    problems = [f"{t} is neither clocked nor named in NOT_CLOCKED" for t in tenant
                if t not in clocked and t not in not_clocked]
    problems += [f"{t} is both clocked and not clocked" for t in sorted(set(clocked) & set(not_clocked))]
    problems += [f"NOT_CLOCKED[{t}] needs its reason" for t, why in not_clocked.items() if not why.strip()]
    return problems


# --- (c) one family list ---------------------------------------------------------------------------------------

_FAMILY_CHECK = re.compile(r"curation_clock_family_check check \( ?family in \(([^)]*)\) ?\)")


def family_check(migrations: Migrations) -> list[str] | None:
    """The newest curation_clock_family_check's families, None when no migration declares it."""
    newest = None
    for _name, text in migrations:
        if "curation_clock_family_check" not in text:
            continue
        for m in _FAMILY_CHECK.finditer(_norm(_strip_comments(text))):
            newest = re.findall(r"'(\w+)'", m.group(1))
    return newest


def seed_families(clock_text: str) -> list[str]:
    m = re.search(r"insert into (?:public\.)?curation_clock \(account_id, family\) select .*?unnest\(array\[([^]]*)\]",
                  _norm(_strip_comments(clock_text)))
    return re.findall(r"'(\w+)'", m.group(1)) if m else []


def rail_c(migrations: Migrations, ts_text: str, clocked: dict[str, str]) -> list[str]:
    check = family_check(migrations)
    seed = seed_families(_clock_migration(migrations)[1])
    ts = ts_curation_families(ts_text)
    sets = {"the family CHECK": set(check or ()), "CURATION_FAMILIES (TS)": set(ts),
            "CLOCKED_TABLES' families": set(clocked.values()), "the seed": set(seed)}
    problems = [] if check else ["no migration declares curation_clock_family_check"]
    if len({frozenset(v) for v in sets.values()}) != 1:
        problems.append("the families disagree: " + "; ".join(f"{k} {sorted(v)}" for k, v in sets.items()))
    if len(ts) != len(set(ts)):
        problems.append(f"CURATION_FAMILIES repeats a family: {ts}")
    return problems


# --- (e) nothing silences the clock ------------------------------------------------------------------------------

# DISABLE, or ENABLE REPLICA (fires only under session_replication_role = replica, so never on our writes); the action
# may follow others in one ALTER TABLE, bounded by the statement or the string literal that holds it.
_DISABLE = re.compile(r"alter table (?:if exists )?(?:only )?(?:public\.)?(\w+) (?:[^;'\"]*?, )?"
                      r"(disable|enable replica) trigger (\w+)")
_DEFINE_BUMP = re.compile(r"\bcreate (?:or replace )?function (?:public\.)?bump_curation_clock\s*\(")


def disable_offenders(name: str, text: str, clocked: dict[str, str], *, after_clock: bool = False) -> list[str]:
    """`after_clock`: a migration numbered after the clock's, where redefining the one writer is a silencing too."""
    if DISABLE_ALLOW.search(text):
        return []
    body = _norm(_strip_comments(text) if name.endswith(".sql") else text)
    out = [f"{name}: {'disables' if how == 'disable' else 'sets replica-only'} {trigger} on {table}"
           for table, how, trigger in _DISABLE.findall(body)
           if trigger.endswith("_curation_clock") or (trigger in ("all", "user") and table in clocked)]
    if "session_replication_role" in body:
        out.append(f"{name}: sets session_replication_role, which silences every clock trigger")
    if after_clock and _DEFINE_BUMP.search(body):
        out.append(f"{name}: redefines bump_curation_clock(); a body without the bump silences every clock trigger "
                   "(and one with it is a write rail h refuses)")
    return out


def rail_e(migrations: Migrations, code: list[tuple[str, str]], clocked: dict[str, str]) -> list[str]:
    made = _creating_clock(migrations)
    clock = _number(made[0][0]) if made else None
    return [o for name, text in migrations
            for o in disable_offenders(name, text, clocked, after_clock=clock is not None and _number(name) > clock)
            ] + [o for name, text in code for o in disable_offenders(name, text, clocked)]


# --- (g) no origin marker; the dedupe GUC is the trigger's own --------------------------------------------------

_ORIGIN = re.compile(r"x-sync-origin|set_config\(\s*'[^']*origin|\bset\s+(?:local\s+|session\s+)?[\w.]*origin\b",
                     re.IGNORECASE)
_CLOCK_GUC = re.compile(r"curation_clock\.bumped")


def rail_g(api: list[tuple[str, str]], code: list[tuple[str, str]], migrations: Migrations) -> list[str]:
    clock_name = _clock_migration(migrations)[0]
    problems = [f"{name}: an origin marker ({m.group(0)!r}); self-echo is kept (Decision 15, R13)"
                for name, text in api for m in _ORIGIN.finditer(text)]
    problems += [f"{name}: names the clock's dedupe GUC, which would suppress bumps" for name, text
                 in [*code, *migrations] if name != clock_name and _CLOCK_GUC.search(text)
                 and _CLOCK_GUC.search(_strip_comments(text) if name.endswith(".sql") else text)]
    return problems


# --- (h) only the trigger writes the clock -----------------------------------------------------------------------

_DML = re.compile(r"\b(?:insert\s+into|update|delete\s+from|truncate(?:\s+table)?|merge\s+into|copy)\s+(?:only\s+)?"
                  r"(?:public\.)?curation_clock\b(?!_)", re.IGNORECASE)


def rail_h(migrations: Migrations, code: list[tuple[str, str]]) -> list[str]:
    clock_name, clock_text = _clock_migration(migrations)
    problems = [f"{name}: writes curation_clock ({m.group(0)!r})" for name, text in code
                for m in _DML.finditer(text)]
    problems += [f"{name}: writes curation_clock after {clock_name} ({m.group(0)!r}); only the trigger may"
                 for name, text in migrations if _number(name) > _number(clock_name)
                 for m in _DML.finditer(_strip_comments(text))]
    own = _DML.findall(_strip_comments(clock_text))
    if len(own) != 2:
        problems.append(f"{clock_name} writes curation_clock {len(own)} times; the bump and the seed are the two")
    return problems


# --- (i) the audience restates 641's split, and never acts ---------------------------------------------------------

def _top_level_or(expr: str) -> list[str]:
    arms, depth, start, i = [], 0, 0, 0
    while i < len(expr):
        ch = expr[i]
        depth += {"(": 1, ")": -1}.get(ch, 0)
        if depth == 0 and expr.startswith(" or ", i):
            arms.append(expr[start:i].strip())
            start = i = i + 4
            continue
        i += 1
    arms.append(expr[start:].strip())
    return arms


def policy_arms(using: str) -> dict[str, frozenset[str] | None]:
    """audience filter (mine / edit / any) → the families its arm allows (None = every family); ValueError when an
    arm bypasses the audience or two arms share one."""
    arms: dict[str, frozenset[str] | None] = {}
    for arm in _top_level_or(_norm(using)):
        if "curation_clock_audience()" not in arm or "account_ids" in arm:
            raise ValueError(f"an arm that does not go through curation_clock_audience() alone: {arm}")
        who = "mine" if "where a.mine" in arm else "edit" if "where a.edit" in arm else "any"
        fam = re.search(r"family (?:= '(\w+)'|in \(([^)]*)\))", arm)
        families = None if fam is None else frozenset(re.findall(r"'?(\w+)'?", fam.group(1) or fam.group(2)))
        if who in arms:
            raise ValueError(f"two arms for the {who} audience")
        arms[who] = families
    return arms


def _audience_body(clock_text: str) -> str:
    m = re.search(r"create (?:or replace )?function (?:public\.)?curation_clock_audience\(\).*?\$\$(.*?)\$\$",
                  _norm(_strip_comments(clock_text)))
    assert m, "the clock migration lost curation_clock_audience()"
    return m.group(1)


def rail_i(policies: dict[str, tuple[str, str | None, str | None]], clock_text: str,
           clocked: dict[str, str]) -> list[str]:
    on_clock = {n: v for n, v in policies.items() if v[0] == "curation_clock"}
    if set(on_clock) != {"curation_clock_audience_read"} or on_clock["curation_clock_audience_read"][2]:
        return [f"the clock carries exactly one read policy and no WITH CHECK: {sorted(on_clock)}"]
    try:
        arms = policy_arms(on_clock["curation_clock_audience_read"][1] or "")
    except ValueError as exc:
        return [str(exc)]

    def tables(fams: frozenset[str] | None) -> set[str]:
        return {t for t, f in clocked.items() if fams is None or f in fams}

    board = {_ACTING_POLICIES[p] for p in _BOARD_POLICIES} & set(clocked)
    edit = (set(_ACTING_POLICIES.values()) - {_ACTING_POLICIES[p] for p in _BOARD_POLICIES}) & set(clocked)
    problems = []
    if set(arms) != {"mine", "any", "edit"} or arms["mine"] is not None:
        problems.append(f"the arms are own (every family), any share (board), edit share: {arms}")
    elif tables(arms["any"]) != board:
        problems.append(f"a read share follows {sorted(tables(arms['any']))}; 641's board is {sorted(board)}")
    elif tables(arms["edit"]) != edit:
        problems.append(f"an edit share follows {sorted(tables(arms['edit']))} beyond the board; 641's edit "
                        f"share acts on {sorted(edit)}")
    if any("shares" in (fams or ()) for who, fams in arms.items() if who != "mine"):
        problems.append("a share recipient follows the owner's shares family")
    body = _audience_body(clock_text)
    if "acting_account_ids" in body or "current_account_ids()" not in body:
        problems.append("curation_clock_audience() must read current_account_ids(), never acting (follow, never act)")
    if "bool_or(s.mode = 'edit')" not in body:
        problems.append("curation_clock_audience()'s edit flag is no longer `bool_or(s.mode = 'edit')`")
    return problems


# --- (j) one number of triggers ----------------------------------------------------------------------------------

def rail_j(thresholds: dict[str, object], clock_text: str, clocked: dict[str, str]) -> list[str]:
    post = re.search(r"tgenabled = 'o'\) <> (\d+) then", _norm(clock_text))
    counts = {"THRESHOLDS": thresholds.get("CURATION_CLOCK_TRIGGERS"), "CLOCKED_TABLES": len(clocked),
              "the $post$ count": int(post.group(1)) if post else None}
    return [] if len(set(counts.values())) == 1 else [f"the trigger counts disagree: {counts}"]


# --- the rails on the repository ---------------------------------------------------------------------------------

def _texts(paths: list[Path]) -> list[tuple[str, str]]:
    return [(p.relative_to(ROOT).as_posix(), p.read_text(encoding="utf-8")) for p in paths]


def test_a_the_triggers_the_registry_and_the_spa_map_name_one_table_set() -> None:
    assert rail_a(_migrations(), SYNC_TS.read_text(encoding="utf-8"), CLOCKED_TABLES) == []


def test_b_every_tenant_table_is_clocked_or_says_why_not() -> None:
    assert rail_b(TENANT_TABLES, CLOCKED_TABLES, NOT_CLOCKED) == []
    assert "curation_clock" in TENANT_TABLES and "curation_clock" in NOT_CLOCKED
    assert BROWSER_WRITE_COLUMNS["curation_clock"] == frozenset(), "no browser write on the clock"


def test_c_one_family_list() -> None:
    assert rail_c(_migrations(), SYNC_TS.read_text(encoding="utf-8"), CLOCKED_TABLES) == []


def test_d_every_clock_trigger_is_a_deferred_constraint_row_trigger() -> None:
    migrations = _migrations()
    assert len(clock_trigger_statements(migrations)) >= len(CLOCKED_TABLES), "the trigger parse went blind"
    assert rail_d(migrations) == []


def test_e_nothing_silences_the_clock_without_the_annotation() -> None:
    assert rail_e(_migrations(), list(_code()), CLOCKED_TABLES) == []


def test_f_no_clock_on_market_data() -> None:
    assert rail_f(_migrations(), CLOCKED_TABLES) == []


def test_g_no_origin_marker_and_no_one_else_touches_the_dedupe_guc() -> None:
    api = _texts([p for p in sorted((ROOT / "api").rglob("*.py"))])
    assert rail_g(api, list(_code()), _migrations()) == []


def test_h_only_the_trigger_writes_the_clock() -> None:
    assert rail_h(_migrations(), list(_code())) == []


def test_i_the_audience_restates_rulings_2_and_never_acts() -> None:
    _name, clock_text = _clock_migration(_migrations())
    assert rail_i(_final_policy_expressions(), clock_text, CLOCKED_TABLES) == []


def test_j_one_trigger_count() -> None:
    _name, clock_text = _clock_migration(_migrations())
    assert rail_j(THRESHOLDS, clock_text, CLOCKED_TABLES) == []


def test_the_clock_read_is_a_pinned_tenant_view() -> None:
    from tests.test_tenant_isolation_live import _TENANT_VIEWS

    assert "curation_clock_public" in _TENANT_VIEWS


# --- each rail bites ---------------------------------------------------------------------------------------------

def _with(migrations: Migrations, sql: str, number: int = 999) -> Migrations:
    return [*migrations, (f"{number}_census_bite.sql", sql)]


def test_a_bites_on_a_dropped_trigger_a_ts_row_and_a_registry_row() -> None:
    migrations, ts = _migrations(), SYNC_TS.read_text(encoding="utf-8")
    for sql in ("drop trigger tags_curation_clock on public.tags;",
                "drop trigger if exists collection_properties_curation_clock on public.collection_properties;",
                "drop function public.bump_curation_clock() cascade;",
                "drop function if exists public.touch(), bump_curation_clock cascade;",
                "drop table if exists public.property_artifact_links cascade;",
                "alter table public.tags rename to labels;",
                "alter trigger tags_curation_clock on public.tags rename to tags_clock;"):
        assert rail_a(_with(migrations, sql), ts, CLOCKED_TABLES), sql
    assert rail_a(_with(migrations, "alter table public.filter_presets rename to presets;"), ts, CLOCKED_TABLES) == []
    redo = ("drop trigger if exists tags_curation_clock on public.tags;\n"
            "create constraint trigger tags_curation_clock after insert or update or delete on public.tags "
            "deferrable initially deferred for each row execute function public.bump_curation_clock('tags');")
    assert rail_a(_with(migrations, redo), ts, CLOCKED_TABLES) == [], "a drop the same file re-creates loses nothing"
    assert rail_a(migrations, ts.replace("  tags: { family: 'tags'", "  tags: { family: 'notes'"), CLOCKED_TABLES)
    assert rail_a(migrations, ts, {**CLOCKED_TABLES, "filter_presets": "board"})


def test_b_bites_on_an_unnamed_tenant_table_and_an_empty_reason() -> None:
    assert rail_b((*TENANT_TABLES, "new_curation"), CLOCKED_TABLES, NOT_CLOCKED)
    assert rail_b(TENANT_TABLES, CLOCKED_TABLES, {**NOT_CLOCKED, "entitlements": " "})
    assert rail_b(TENANT_TABLES, CLOCKED_TABLES, {**NOT_CLOCKED, "tags": "both"})


def test_c_bites_on_a_new_family_anywhere() -> None:
    migrations, ts = _migrations(), SYNC_TS.read_text(encoding="utf-8")
    widened = ("alter table public.curation_clock add constraint curation_clock_family_check check (family in "
               "('board', 'dismissals', 'collections', 'tags', 'notes', 'links', 'shares', 'presets'));")
    assert rail_c(_with(migrations, widened), ts, CLOCKED_TABLES)
    assert rail_c(migrations, ts.replace("'links', 'shares']", "'links', 'shares', 'presets']"), CLOCKED_TABLES)
    assert rail_c(migrations, ts, {**CLOCKED_TABLES, "filter_presets": "presets"})


def test_d_bites_on_an_immediate_a_statement_level_a_partial_or_a_misnamed_trigger() -> None:
    head = "create constraint trigger tags_curation_clock after insert or update or delete on public.tags "
    call = "execute function public.bump_curation_clock('tags');"
    for sql in (
        f"create trigger tags_curation_clock after insert or update or delete on public.tags for each row {call}",
        f"{head}deferrable initially immediate for each row {call}",
        f"{head}deferrable initially deferred for each statement {call}",
        f"create constraint trigger tags_curation_clock after insert or delete on public.tags "
        f"deferrable initially deferred for each row {call}",
        f"create constraint trigger tags_clock after insert or update or delete on public.tags "
        f"deferrable initially deferred for each row {call}",
        f"{head}deferrable initially deferred for each row execute function public.stamp_delegated_edit();",
    ):
        assert rail_d([("999_census_bite.sql", sql)]), sql


def test_e_bites_on_a_named_or_bulk_disable_and_on_replica_mode() -> None:
    for sql in ("alter table public.tags disable trigger tags_curation_clock;",
                "alter table tags disable trigger all;",
                "alter table only public.collections disable trigger user;",
                "set session_replication_role = replica;",
                "select set_config('session_replication_role', 'replica', true);"):
        assert rail_e([("999_census_bite.sql", sql)], [], CLOCKED_TABLES), sql
    allowed = "-- ci-allow-clock-disable: the writer kill switch (#0)\nalter table tags disable trigger all;"
    assert rail_e([("999_census_bite.sql", allowed)], [], CLOCKED_TABLES) == []
    quoted = "-- never without a `-- ci-allow-clock-disable: <reason>` line\nalter table tags disable trigger all;"
    assert rail_e([("999_census_bite.sql", quoted)], [], CLOCKED_TABLES), "a quoted annotation sanctions nothing"
    assert rail_e([("999_census_bite.sql", "alter table filter_presets disable trigger all;")], [],
                  CLOCKED_TABLES) == []
    assert rail_e([], [("scripts/x.py", 'cur.execute("SET session_replication_role = replica")')], CLOCKED_TABLES)


def test_e_bites_on_replica_only_firing_a_buried_action_and_a_later_redefinition_of_the_bump() -> None:
    for sql in ("alter table public.tags enable replica trigger tags_curation_clock;",
                "alter table property_notes enable replica trigger all;",
                "alter table public.tags add column x int, disable trigger tags_curation_clock;",
                "alter table public.tags alter column name set not null, enable replica trigger user;"):
        assert rail_e([("999_census_bite.sql", sql)], [], CLOCKED_TABLES), sql
    assert rail_e([], [("scripts/x.py", 'cur.execute("ALTER TABLE tags ENABLE REPLICA TRIGGER ALL")')], CLOCKED_TABLES)
    for sql in ("alter table public.tags enable always trigger tags_curation_clock;",
                "alter table public.tags enable trigger tags_curation_clock;",
                "alter table public.filter_presets add column x int, enable replica trigger all;"):
        assert rail_e([("999_census_bite.sql", sql)], [], CLOCKED_TABLES) == [], sql
    silent = ("create or replace function public.bump_curation_clock() returns trigger language plpgsql "
              "security definer set search_path = '' as $$ begin return null; end $$;")
    migrations = _migrations()
    assert rail_e(_with(migrations, silent), [], CLOCKED_TABLES)
    assert rail_e([("999_census_bite.sql", silent)], [], CLOCKED_TABLES) == [], "no clock migration, no later one"
    clock_name, clock_text = _clock_migration(migrations)
    assert "drop trigger if exists" in clock_text
    assert "create or replace function public.bump_curation_clock" in clock_text
    assert rail_e([(clock_name, clock_text)], [], CLOCKED_TABLES) == [], "the clock's own drops and its own writer"
    assert rail_e(_with(migrations, clock_text), [], CLOCKED_TABLES), "the same text after the clock redefines it"


def test_f_bites_on_a_clock_over_listings() -> None:
    sql = ("create constraint trigger listings_curation_clock after insert or update or delete on public.listings "
           "deferrable initially deferred for each row execute function public.bump_curation_clock('board');")
    assert rail_f([("999_census_bite.sql", sql)], CLOCKED_TABLES)
    assert rail_f([], {**CLOCKED_TABLES, "properties": "board"})


def test_g_bites_on_an_origin_header_an_origin_guc_and_the_dedupe_guc() -> None:
    migrations = _migrations()
    for text in ('headers={"X-Sync-Origin": tab}', "SELECT set_config('app.sync_origin', %s, true)",
                 "SET LOCAL curation.origin = 'api'"):
        assert rail_g([("api/x.py", text)], [], migrations), text
    assert rail_g([], [("toolkit/x.py", "set_config('curation_clock.bumped', 'board:x;', true)")], migrations)
    assert rail_g([("api/x.py", 'allow_origins=["https://app.propscout.cz"]')], [], migrations) == []


def test_h_bites_on_a_write_from_code_and_from_a_later_migration() -> None:
    migrations = _migrations()
    for text in ("UPDATE curation_clock SET counter = 1", "delete from public.curation_clock where true",
                 "INSERT INTO curation_clock (account_id, family) VALUES (%s, 'board')", "truncate curation_clock"):
        assert rail_h(migrations, [("api/x.py", text)]), text
    assert rail_h(_with(migrations, "update public.curation_clock set counter = 0;"), [])
    assert rail_h(migrations, [("api/x.py", "select * from curation_clock_public")]) == []


def test_i_bites_when_a_read_share_follows_more_than_the_board_or_anyone_follows_shares() -> None:
    policies, clock_text = _final_policy_expressions(), _clock_migration(_migrations())[1]
    read, using, _check = policies["curation_clock_audience_read"]

    def _swap(old: str, new: str) -> dict[str, tuple[str, str | None, str | None]]:
        assert old in (using or ""), old
        return {**policies, "curation_clock_audience_read": (read, (using or "").replace(old, new), None)}

    assert rail_i(_swap("family = 'board'", "family in ('board', 'notes')"), clock_text, CLOCKED_TABLES)
    assert rail_i(_swap("'notes', 'links')", "'notes', 'links', 'shares')"), clock_text, CLOCKED_TABLES)
    assert rail_i(_swap("'tags', 'notes'", "'tags'"), clock_text, CLOCKED_TABLES)
    assert rail_i(_swap("where a.edit", "where a.mine"), clock_text, CLOCKED_TABLES)
    assert rail_i(policies, clock_text.replace("public.current_account_ids()", "public.acting_account_ids(true)"),
                  CLOCKED_TABLES)
    assert rail_i(policies, clock_text.replace("bool_or(s.mode = 'edit')", "true"), CLOCKED_TABLES)


def test_j_bites_on_a_threshold_that_drifts() -> None:
    clock_text = _clock_migration(_migrations())[1]
    assert rail_j({**THRESHOLDS, "CURATION_CLOCK_TRIGGERS": 11}, clock_text, CLOCKED_TABLES)
    assert rail_j(THRESHOLDS, clock_text.replace(") <> 12 then", ") <> 13 then"), CLOCKED_TABLES)
