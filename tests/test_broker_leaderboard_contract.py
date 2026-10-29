"""Offline contracts W4 must not break (migrations 435, 469, 609).

Load-bearing things about the broker leaderboard function, invisible in the normal test suite, and
each would fail only in production. Nothing calls it since the broker board (api/broker_board.py); these rails
hold its body until the post-deploy PR drops it.

  * Every parameter carries a DEFAULT, and the four chip parameters lead in Browse's names and order.
  * `CREATE OR REPLACE` preserves a function's ACL; `DROP` + `CREATE` resets it to the
    default, `EXECUTE TO PUBLIC`. Since this function returns `primary_email` and
    `primary_phone`, that default silently re-exposes broker PII to `anon` — undoing
    migration 299. The existing rule-4 gate in test_migration_rls_grants.py scans for
    literal `grant` statements and cannot see a default-ACL restoration.
    BUT a DROP is sometimes structurally unavoidable: Postgres cannot widen a function's
    parameter COUNT via CREATE OR REPLACE (verified live while building migration 448 — it
    silently created a second, overloaded function instead of replacing the first, which
    briefly left THAT overload at the anon/authenticated-executable default until caught and
    revoked by hand). Migrations 410, 448 and 469 all DROP the old signature and immediately
    CREATE + fully REVOKE the new one in the SAME file — that pairing is exactly as safe as
    CREATE OR REPLACE, so the gate below checks for the pairing, not for the DROP's absence.
  * The active-broker set must be resolved ONCE, via `AS MATERIALIZED`. Written as an
    inlinable `IN (select ... from brokers)`, the planner probes `brokers` once per
    candidate row on every geo shape — 3,942 loops and 11,826 blocks on a single region
    chip — reproducing the exact nested loop this wave exists to delete. Nothing else in
    CI can see that: the rows returned are identical either way.

All offline text assertions — they run in the normal `pytest -q` lane. The plan-shape half
lives in `tests/test_broker_leaderboard_plan_shape.py`.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

# Reused, not re-derived: both are dollar-quote aware, so a `$function$` body stays ONE
# statement. A naive regex has already matched prose inside a migration's own header
# comment in this build.
from tests.test_migration_rls_grants import _statements, _strip_comments

_ROOT = Path(__file__).resolve().parent.parent
_MIGRATIONS = _ROOT / "migrations"

def _latest_migration_defining(fn: str) -> Path:
    """The HIGHEST-numbered migration that CREATE OR REPLACEs `fn` — i.e. the live
    definition. This file used to pin migration 469 by name while migration 508 had
    already superseded the body: the offline contract was validating a dead file, and
    nothing checked the definition production actually runs (found in the 2026-09-30
    Brokers-outage investigation). Resolving dynamically keeps the contract on the
    live body without an edit here on every redefinition."""
    pattern = re.compile(
        rf"create\s+or\s+replace\s+function\s+(?:public\.)?{fn}\s*\(", re.IGNORECASE
    )
    hits = [
        p for p in _MIGRATIONS.glob("*.sql")
        if pattern.search(_strip_comments(p.read_text()))
    ]
    assert hits, f"no migration defines {fn}"
    return max(hits, key=lambda p: int(re.match(r"(\d+)", p.name).group(1)))


_FN_MIGRATION = _latest_migration_defining("broker_leaderboard")


def _sql(path: Path) -> str:
    return _strip_comments(path.read_text())


def test_every_function_parameter_carries_a_default():
    """Every one of the 13 parameters is defaulted.

    Anchored on the CREATE (not the file's leading DROP, which names the OLD 8-param
    signature it is retiring) — `body.index("broker_leaderboard(")` alone would find that
    DROP first and silently check the wrong parameter list.

    RED by: un-defaulting any parameter in 609.
    """
    body = _sql(_FN_MIGRATION)
    create_at = body.index("create or replace function public.broker_leaderboard(")
    start = create_at + len("create or replace function public.broker_leaderboard(")
    params = body[start : body.index(")\nreturns", start)]
    lines = [p.strip() for p in params.split(",\n") if p.strip()]
    assert len(lines) == 13, f"expected 13 parameters, found {len(lines)}: {lines}"
    missing = [p for p in lines if "default" not in p.lower()]
    assert not missing, (
        f"every broker_leaderboard parameter must carry a DEFAULT: {missing}"
    )
    # Browse's chip parameters, Browse's names and order (605).
    assert [p.split()[0] for p in lines[:4]] == [
        "districts_filter", "districts_excluded_filter", "districts_levels", "districts_ids",
    ]


# --- the grant posture -----------------------------------------------------


def _revoked_roles(statements: list[str]) -> set[str]:
    """Roles with EXECUTE revoked on broker_leaderboard in this statement list.

    Accepts either the combined `revoke all on function ... from a, b, c` form (migration
    410) or three separate `revoke execute ... from <role>` statements (435 on) — no role
    name is a substring of another, so a plain containment check is unambiguous either way.
    """
    roles: set[str] = set()
    for s in statements:
        if "broker_leaderboard" not in s:
            continue
        if not (s.startswith("revoke execute") or s.startswith("revoke all on function")):
            continue
        tail = s.rsplit(" from ", 1)[-1]
        roles |= {role for role in ("public", "anon", "authenticated") if role in tail}
    return roles


# Migration 299 (Amendment A6) is where broker_leaderboard's PII-locked-down posture
# BEGINS — it is the first migration to revoke the function from anon at all. Before it
# (migration 190), the function was deliberately anon-executable by design (pre-review,
# pre-D1/D2), so a DROP+GRANT-to-anon there is correct history, not a violation of a rule
# that did not exist yet. Enforce the pairing only from the rule's own origin forward.
_ACL_LOCKED_DOWN_FROM = 299


def test_every_leaderboard_drop_is_immediately_recreated_and_fully_revoked():
    """A DROP is sometimes the only correct tool (widening the parameter COUNT — see the
    module docstring), but it must never leave the ACL at its PUBLIC-executable default
    even momentarily: the same file must also CREATE the replacement and REVOKE EXECUTE
    from all three roles. CREATE OR REPLACE alone (no DROP) trivially satisfies this too,
    since it never touches the ACL.

    RED by: a DROP with no matching same-file CREATE + full 3-role REVOKE (migrations 410
    and 469 both must stay green here).
    """
    offenders: list[str] = []
    for path in sorted(_MIGRATIONS.glob("*.sql")):
        match = re.match(r"^(\d+)_", path.name)
        if not match or int(match.group(1)) < _ACL_LOCKED_DOWN_FROM:
            continue
        statements = [" ".join(s.split()).lower() for s in _statements(path.read_text())]
        drops = [s for s in statements
                if s.startswith("drop function") and "broker_leaderboard" in s]
        if not drops:
            continue
        has_create = any(
            "broker_leaderboard" in s
            and ("create function" in s or "create or replace function" in s)
            for s in statements
        )
        revoked = _revoked_roles(statements)
        if not has_create or revoked != {"public", "anon", "authenticated"}:
            offenders.append(
                f"{path.name}: drop found, create={has_create}, revoked={sorted(revoked)}"
            )
    assert not offenders, (
        "a migration drops broker_leaderboard without an immediate, same-file create + "
        "full 3-role revoke — this resets the ACL to EXECUTE TO PUBLIC, re-exposing "
        "primary_email/primary_phone to anon:\n" + "\n".join(offenders)
    )


def test_the_function_migration_replaces_and_reasserts_the_revokes():
    """RED by: dropping the three REVOKE statements from 469, or losing CREATE OR REPLACE
    for the new signature (a plain CREATE would also work for a from-empty CI replay, but
    would fail "already exists" replayed against a database where this exact signature was
    already created live — see the migration's own header)."""
    statements = [" ".join(s.split()).lower() for s in _statements(_FN_MIGRATION.read_text())]
    assert any(s.startswith("create or replace function") and "broker_leaderboard" in s
               for s in statements), (
        "469 must use CREATE OR REPLACE FUNCTION for the new signature"
    )
    revoked = _revoked_roles(statements)
    assert revoked == {"public", "anon", "authenticated"}, (
        f"469 must re-assert EXECUTE revokes for all three roles, got {sorted(revoked)}"
    )


# --- the two branches -------------------------------------------------------
#
# 448 added a live branch (reads `listings` directly) beside the unfiltered fast path
# (unchanged from 435, reads broker_region_type_stats); 469 added the SECOND filter that
# routes to it (subtype), which is what turned "price is special" into the general rule:
# a filter the matview cannot express routes to the live branch. Both are ONE `language
# sql` function — a `language plpgsql` if/else was tried and reverted (see the migration's
# own header): PL/pgSQL is never inlined, so it made the function an opaque "Function
# Scan" to EXPLAIN and broke every assertion in
# tests/test_broker_leaderboard_plan_shape.py. The two branches are instead two
# independently-gated CTE chains (`fast_...` / `live_...`) UNIONed in a `combined` CTE —
# confirmed live that Postgres prunes whichever branch's gate is false, even under a real
# bound parameter (PREPARE/EXECUTE), so this keeps both the inlining (hence EXPLAIN
# visibility) AND the "only one branch's cost is ever paid" property.
#
# `active_brokers` itself is SHARED (one CTE, defined before either branch) rather than
# duplicated per branch — also measured, not assumed (see the migration header): a
# MATERIALIZED CTE is evaluated once per reference regardless of which branch that
# reference sits in, so two identically-defined copies scanned `brokers` twice on every
# single call. It is therefore OUTSIDE both `_branches()` slices below by construction —
# tested once, directly, not per-branch.
#
# Every OTHER W4 structural guarantee must hold in EACH branch independently, not just
# twice in the function as a whole — so each test slices the `fast_...`/`live_...` CTE
# ranges apart and asserts on both, rather than just doubling a magic occurrence count.


def test_active_brokers_is_shared_and_materialized_exactly_once():
    """RED by: reintroducing a per-branch copy (`fast_active_brokers`/
    `live_active_brokers`) — measured live to double the unfiltered call's cost (4,607 of
    9,056 total cost units) for no correctness benefit, since `active_brokers` reads
    neither `broker_region_type_stats` nor `listings` and so is not itself subject to
    the branch-pruning either shape relies on."""
    fn = _fn_body().lower()
    assert fn.count("active_brokers as materialized") == 1, (
        "expected exactly one (shared) `active_brokers as materialized` CTE"
    )
    assert fn.count("join active_brokers ab on ab.id") == 2, (
        "expected the shared active_brokers CTE joined into both fast_agg and live_agg"
    )


def _branches(fn: str) -> tuple[str, str]:
    """Split the function body into (fast_path, live_path) at the `fast_raw` /
    `live_priced` / `combined` CTE boundaries — the first branch-SPECIFIC CTE in each
    chain (the shared `active_brokers` CTE sits before both and is tested separately
    above). Brittle by construction — a real parser would be overkill for one function
    with one pair of CTE chains — but if this split ever breaks it should break LOUDLY
    (an IndexError-shaped assertion), not silently match 0 characters and pass every
    test below vacuously."""
    fast_at = fn.lower().index("fast_raw as (")
    live_at = fn.lower().index("live_priced as (", fast_at)
    combined_at = fn.lower().index("\n  combined as (", live_at)
    return fn[fast_at:live_at], fn[live_at:combined_at]


def _fn_body() -> str:
    body = _sql(_FN_MIGRATION)
    return body[body.index("as $function$") : body.index("$function$;")]


def test_both_branches_split_cleanly():
    """Guards the other tests in this section: if `_branches` ever matches the wrong
    thing (e.g. a rewrite renames a CTE), the fast/live slices must still be non-trivial
    and disjoint, or every test built on them is silently vacuous instead of failing."""
    fn = _fn_body()
    fast, live = _branches(fn)
    assert len(fast) > 200 and len(live) > 200, (
        f"branch split produced suspiciously short text: fast={len(fast)} live={len(live)}"
    )
    assert fast != live


def _combined_cte(fn: str) -> str:
    """The shared hydration+union step: `combined as ( ... )` up to the outer `select *
    from combined order by ...` that closes the function. Both branches' `_top` CTEs
    feed this ONE place — unlike the ranking logic, hydration is not duplicated per
    branch, so it has no `_branches()` split of its own."""
    start = fn.lower().index("\n  combined as (")
    end = fn.index(")\n  -- Explicit final ORDER BY", start)
    return fn[start : end + 1]


def test_each_branch_ranking_cte_has_a_single_ascending_tiebreaker():
    """Once the LIMIT truncates a branch's ranking CTE, an unstable sort decides
    MEMBERSHIP, not just display position. Measured: seven brokers tie at the default
    byt/prodej limit-100 boundary.

    RED by: deleting `, a.broker_id` from either branch's `_top` CTE, or flipping it to
    `desc`.
    """
    fast, live = _branches(_fn_body())
    for name, branch in (("fast", fast), ("live", live)):
        orders = re.findall(
            r"order by\s+case p_metric.*?end desc,\s*(\w+)\.broker_id(\s+desc)?",
            branch, re.IGNORECASE | re.DOTALL,
        )
        assert len(orders) == 1, (
            f"{name} branch: expected exactly 1 metric ORDER BY in the ranking CTE, "
            f"found {len(orders)}"
        )
        assert (orders[0][1] or "").strip() == "", (
            f"{name} branch: the ranking tiebreaker must be ASCENDING, got {orders[0]}"
        )


def test_the_final_combined_order_by_carries_the_same_tiebreaker():
    """The two branches' `_top` CTEs are already correctly ranked-and-limited, but the
    function still sorts the (one non-empty) UNIONed result explicitly — belt and
    braces, not redundant, per this migration's own header. That final ORDER BY sits
    OUTSIDE both branches (it has to: `combined` is where the two become one query), so
    it is asserted separately from the per-branch tiebreaker above, on the bare
    (unqualified) `broker_id` output column rather than a table-qualified one.

    RED by: deleting `, broker_id` from the outermost ORDER BY, or flipping it to `desc`.
    """
    fn = _fn_body()
    outer = fn[fn.lower().index("\n  select * from combined") :]
    orders = re.findall(
        r"order by\s+case p_metric.*?end desc,\s*broker_id(\s+desc)?",
        outer, re.IGNORECASE | re.DOTALL,
    )
    assert len(orders) == 1, (
        f"expected exactly 1 final ORDER BY after the union, found {len(orders)}"
    )
    assert (orders[0] or "").strip() == "", (
        f"the final tiebreaker must be ASCENDING, got {orders[0]!r}"
    )


def test_each_branch_ranking_cte_has_exactly_one_limit():
    """The whole point of W4 (and why 469 preserves it in the new branch too): each
    branch truncates to p_limit rows BEFORE `combined` ever joins `brokers`/`firms` — a
    CTE can only be referenced after it is fully defined, so Postgres's own grammar
    already forces the ranking-then-hydrate ORDER here; this pins the ranking half of
    that guarantee (that there IS a single, correctly-clamped LIMIT to reference).

    RED by: deleting the LIMIT from either branch's `_top` CTE, or duplicating it.
    """
    fast, live = _branches(_fn_body())
    for name, branch in (("fast", fast), ("live", live)):
        assert branch.lower().count("limit greatest(1, least(p_limit, 2000))") == 1, (
            f"{name} branch: exactly one LIMIT clause expected, migration 414's clamp "
            "character for character"
        )


def test_combined_hydrates_from_the_ranked_top_cte_not_the_raw_aggregate():
    """The other half of the ranking-then-hydrate guarantee: `combined`'s two arms must
    each join `brokers` against that branch's `_top` CTE (already ranked + limited), not
    directly against `_agg` (the full, unranked candidate set) — joining `_agg` would
    hydrate every candidate broker before truncating, exactly the pre-W4 shape whose
    cost migration 435's header measures (87-99.2% of hydrated rows thrown away by the
    LIMIT).

    RED by: rewriting either arm of `combined` to read `from fast_agg t` / `from
    live_agg t` instead of `from fast_top t` / `from live_top t`.
    """
    combined = _combined_cte(_fn_body()).lower()
    assert "from fast_top t" in combined and "join brokers b on b.id = t.broker_id" in combined, (
        "combined's fast arm must hydrate from fast_top, not fast_agg"
    )
    assert "from live_top t" in combined, (
        "combined's live arm must hydrate from live_top, not live_agg"
    )
    assert "from fast_agg" not in combined and "from live_agg" not in combined, (
        "combined must never reference an _agg CTE directly — that would hydrate the "
        "whole candidate set instead of just the top p_limit rows"
    )
    assert combined.count("join brokers b on b.id = t.broker_id") == 2, (
        "expected exactly 2 hydration joins to brokers (one per branch arm)"
    )


# The two gate expressions, spelled once here and asserted verbatim below. They must be
# exact logical complements over the SAME parameters: if a call could satisfy both, its
# rows are double-counted; if it could satisfy neither, the leaderboard comes back empty.
# Adding a live-path filter means updating both spellings AND this pair — which is the
# point of pinning the literal text rather than a loose substring. 609 added the chips:
# the count book has cells for an INCLUDE at kraj / okres / obec only, one level per name.
_FAST_GATE = (
    "p_min_price_czk is null and p_subtypes is null\n"
    "      and coalesce(districts_levels, '{}') <@ '{kraj,okres,obec}'\n"
    "      and coalesce(districts_excluded_filter, '{}') <@ '{false}'\n"
    "      and cardinality(districts_levels) is not distinct from cardinality(districts_filter)"
)
_LIVE_GATE = (
    "p_min_price_czk is not null or p_subtypes is not null\n"
    "      or not coalesce(districts_levels, '{}') <@ '{kraj,okres,obec}'\n"
    "      or not coalesce(districts_excluded_filter, '{}') <@ '{false}'\n"
    "      or cardinality(districts_levels) is distinct from cardinality(districts_filter)"
)


def _negated(atom: str) -> str:
    if atom.endswith(" is null"):
        return atom.removesuffix(" is null") + " is not null"
    if " is not distinct from " in atom:
        return atom.replace(" is not distinct from ", " is distinct from ")
    return f"not {atom}"


def test_the_precondition_names_the_live_body_and_this_files_own():
    """A re-run must pass its own md5 gate, so the gate must name THIS body's md5 exactly
    as Postgres stores prosrc (the text between the `$function$` quotes) — any edit inside
    the body (a renumbered comment included) changes it. 605's rail, for 609.

    RED by: editing the function body without recomputing the precondition's hash."""
    sql = _FN_MIGRATION.read_text()
    pre = sql[sql.index("do $pre$"): sql.index("$pre$;")]
    assert pre.index("into populated") < pre.index("return;") < pre.index("pg_proc")
    at = sql.index("create or replace function public.broker_leaderboard(")
    body = sql[at: sql.index("$function$;", at)].split("$function$")[1]
    assert f"'{hashlib.md5(body.encode()).hexdigest()}'" in pre, "re-run would refuse"
    assert "'aabd5828e6bed3be596ae07a74c11216'" in pre, "580's body as applied live"
    code = _sql(_FN_MIGRATION)
    assert code.lstrip().startswith("set lock_timeout = '5s';")
    assert code.rstrip().endswith("reset lock_timeout;")


def test_the_two_gates_are_de_morgan_complements_and_both_branches_carry_one():
    """The live gate is the fast gate negated atom by atom. Every atom is two-valued — an
    IS NULL test, a containment over a COALESCEd array, an IS DISTINCT FROM — so De Morgan
    holds in SQL's three-valued logic too: exactly one branch answers every call.

    RED by: adding a chip condition to one gate only (a street chip would then reach the
    count book, which has no street cells, and answer with its town's or the country's
    brokers), or gating `live_priced` on less than the full live gate.
    """
    fast = re.split(r"\s+and\s+", _FAST_GATE)
    live = re.split(r"\s+or\s+", _LIVE_GATE)
    assert [_negated(a) for a in fast] == live
    _, live_branch = _branches(_fn_body())
    assert f"where ({_LIVE_GATE})" in live_branch.lower()


def test_combined_arms_are_gated_by_complementary_live_filter_conditions():
    """The two arms of `combined` must be MUTUALLY EXCLUSIVE and jointly exhaustive, or a
    call could double-count (both arms contribute) or under-fill (both suppressed)
    instead of cleanly selecting one branch's already-complete answer.

    RED by: gating both arms the same way, gating neither, or adding a live-path filter
    to one gate and forgetting the other (the failure mode 469 introduced by adding
    subtype — a subtype-only call would otherwise have matched the FAST arm too).
    """
    combined = _combined_cte(_fn_body()).lower()
    assert combined.count(f"where {_FAST_GATE}") == 1, (
        f"combined's fast arm must be gated exactly `where {_FAST_GATE}`"
    )
    assert combined.count(f"where {_LIVE_GATE}") == 1, (
        f"combined's live arm must be gated exactly `where {_LIVE_GATE}`"
    )


def test_the_fast_branch_is_one_exact_cell_arm_carrying_the_full_gate():
    """W4 collapsed the four per-level UNION ALL arms into ONE arm over exact cells:
    the geo shapes are disjuncts of a single WHERE, the category filters resolve to a
    cell (`coalesce(p_category_x, '*')` — the grouping-set rollup, computed exactly,
    never summed), and nested chips are suppressed via admin_boundaries. A second
    matview read appearing here means someone reintroduced a summing arm.

    RED by: adding another `from broker_region_type_stats` read, dropping the subtype
    half of the gate (a subtype-only call would fall through to the matview, which
    cannot express subtype, and answer with silently UNFILTERED counts), or replacing
    an exact cell match with a `p_x is null or ...` range that sums cells again.
    """
    fast, _ = _branches(_fn_body())
    low = fast.lower()
    arms = low.count("from broker_region_type_stats s")
    gates = low.count(f"where {_FAST_GATE}")
    assert arms == 1, f"expected ONE exact-cell matview arm (W4), found {arms}"
    assert gates == arms, (
        f"{arms} matview arm but {gates} full fast gate(s) `{_FAST_GATE}`"
    )
    assert "s.category_main = coalesce(p_category_main, '*')" in low, (
        "the category_main filter must resolve to an exact cell ('*' rollup when unset)"
    )
    assert "s.category_type = coalesce(p_category_type, '*')" in low, (
        "the category_type filter must resolve to an exact cell ('*' rollup when unset)"
    )
    assert "s.geo_level = 'cz'" in low, (
        "the no-geo shape must read the national 'cz' cells, never sum regions"
    )
    assert low.count("not exists") == 2, (
        "the okres and obec disjuncts must each suppress chips nested under a selected "
        "parent (count-once, operator ruling 2026-09-30)"
    )
    # "No chips" is a PARAMETER test, so the planner folds it and a chip call never reads
    # the national cells. Derived from `g` it would be an InitPlan the planner cannot fold:
    # measured live 2026-10-17, a kraj + obec call then read the cz band too (+15,099 rows,
    # 4,558 -> 11,799 heap blocks) to filter it all away.
    assert "(coalesce(cardinality(districts_filter), 0) = 0\n         and s.geo_level = 'cz')" in low


def test_the_live_branch_predicates_are_each_inert_when_unset():
    """Both live-path filters must be written `p_x is null or <match>`, so the branch can
    run for EITHER filter alone. 448 wrote the price predicate without that escape —
    correct while price was the only thing that could trigger the live branch, and a
    silently EMPTY leaderboard the moment 469 let a subtype-only call in
    (`l.price_czk >= NULL` is NULL, so every row failed).

    RED by: dropping either `p_x is null or` escape.
    """
    _, live = _branches(_fn_body())
    lowered = " ".join(live.lower().split())
    assert "p_min_price_czk is null or l.price_czk >= p_min_price_czk" in lowered, (
        "the price predicate lost its `p_min_price_czk is null or` escape — a "
        "subtype-only call would match no rows"
    )
    assert "p_subtypes is null or l.subtype = any(p_subtypes)" in lowered, (
        "the subtype predicate lost its `p_subtypes is null or` escape — a price-only "
        "call would match no rows"
    )


def test_the_activity_filter_is_joined_before_the_limit_in_both_branches():
    """The blocking correctness rule: the doctrine moves invariants, not predicates.

    Leaving the active-broker join above the LIMIT lets a merged_away broker holding
    stats/listings rows consume a top-N slot and then be discarded — an under-filled
    page. LIVE today: 5 such brokers hold matview rows, and 717 merged-away brokers
    carry a metric at or above the default cut — the same hazard applies whether the
    candidate rows come from the matview (fast branch) or from `listings` directly
    (live branch). (`status = 'active'` itself is resolved once, in the SHARED
    `active_brokers` CTE, before either branch — see
    test_active_brokers_is_shared_and_materialized_exactly_once; a CTE can only be
    referenced after it is fully defined, so that half of the ordering is enforced by
    Postgres's own grammar and does not need its own test here.)

    RED by: moving either branch's join to `active_brokers` out of `agg`/`priced` into
    the final SELECT's WHERE.
    """
    fast, live = _branches(_fn_body())
    fast_l, live_l = fast.lower(), live.lower()

    join_at = fast_l.index("join active_brokers ab on ab.id = r.broker_id")
    limit_at = fast_l.index("limit greatest(1, least(p_limit, 2000))")
    assert join_at < limit_at, (
        "fast branch: the active-broker join must sit BEFORE the LIMIT — otherwise the "
        "page silently under-fills when a merged_away broker holds stats rows"
    )

    join_at = live_l.index("join active_brokers ab on ab.id = bi.broker_id")
    limit_at = live_l.index("limit greatest(1, least(p_limit, 2000))")
    assert join_at < limit_at, (
        "live branch: the active-broker join must sit BEFORE the LIMIT — otherwise a "
        "price-filtered page can silently under-fill the same way"
    )
