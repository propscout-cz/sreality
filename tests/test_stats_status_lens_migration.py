"""The migration that makes the Stats pivot judge a row's status by the cohort's own rule. Offline, text only.

The statement is its predecessor's with exactly three edits -- `filtered` carries the two source arrays,
`keyed` derives is_active through portal_status_matches(..., portal_filter, 'active') and keeps the
property's flag beside it, and `sample` sets a turnover row aside when the two differ -- the apply is gated
on the predecessor's body or this file's own, and the same-signature replace leaves the ACL alone. The
predecessor is the highest-numbered definition below this file, whatever it is called (stacked branches
get renumbered).
"""

from __future__ import annotations

import hashlib
import re

from tests.migration_defs import MIGRATIONS, latest_definition
from toolkit import stats_registry as sr

FN = "browse_stats_properties"
_FILES = sorted(MIGRATIONS.glob("*_stats_status_lens.sql"))
assert len(_FILES) == 1, _FILES
THIS = _FILES[0]
NUMBER = int(THIS.name.split("_", 1)[0])
SQL = THIS.read_text(encoding="utf-8")
_DEFINES = re.compile(rf"create or replace function (?:public\.)?{FN}\s*\(", re.IGNORECASE)
LENS = ("public.portal_status_matches(l.is_active, l.all_sources, l.active_sources, portal_filter, "
        "'active', null, false) as is_active")


def _number(path) -> int:
    return int(path.name.split("_", 1)[0])


PREV = max((p for p in MIGRATIONS.glob("*.sql")
            if _number(p) < NUMBER and _DEFINES.search(p.read_text(encoding="utf-8"))), key=_number)
PREV_SQL = PREV.read_text(encoding="utf-8")


def _statement(text: str) -> str:
    at = text.index(f"create or replace function public.{FN}(")
    return text[at: text.index("$function$;", at) + len("$function$;")]


def _md5(statement: str) -> str:
    return hashlib.md5(statement.split("$function$")[1].encode()).hexdigest()


def _expected() -> str:
    """The predecessor's statement with the three edits and nothing else."""
    s = _statement(PREV_SQL)
    s = s.replace("l.cast_obce_id, l.ulice_id\n    from browse_list l\n",
                  "l.cast_obce_id, l.ulice_id, l.all_sources, l.active_sources\n    from browse_list l\n")
    at = s.index("  keyed as (\n    select l.is_active, l.category_main,")
    end = s.index("\n", at + len("  keyed as (\n")) + 1
    s = s[:at] + "  keyed as (\n" + "".join(f"    -- {ln}\n" for ln in (
        "The row's status under the cohort's OWN rule (597): with a portal filter, active ON those",
        "portals; without one, the property's. The `status` fact and the turnover gate read this, so",
        "they judge the sample the way the filter selected it, never a property's other portals.",
    )) + f"    select {LENS},\n      l.is_active as property_active,\n      l.category_main, l.category_type, l.price_per_m2_basis,\n" + s[end:]
    guard = "and (c1 is null or kc is not null)\n  ),"
    assert s.count(guard) == 1
    s = s.replace(guard, "and (c1 is null or kc is not null)\n" + "".join(f"      -- {ln}\n" for ln in (
        "A turnover row must be closed under the lens AND at property grain (ruling 3): a property",
        "delisted on the chosen portal but live elsewhere has no measured run there -- its tom_days",
        "still counts -- so it is set aside and counted as excluded, never pooled.",
    )) + f"      and (v_measure not in ({sr.single_status_measures_sql()}) or is_active = property_active)\n  ),")
    return s


def test_it_is_the_effective_definition_and_the_only_one_in_the_file() -> None:
    assert latest_definition(FN).name == "694_true_circle_map_area.sql"  # 640 dropped the denylist, 644 swapped the condition conjunct, 648 added energy rating, 657 took the lens back, 662 one status parameter, 678 the small-sample summary
    assert SQL.count(f"create or replace function public.{FN}(") == 1


def test_the_three_edits_are_the_only_lines_that_moved() -> None:
    new = _statement(SQL)
    assert new == _expected()
    body = new.split("$function$")[1]
    # the status fact and the turnover gate read the lens column, as before
    assert "bool_and(is_active) as all_active, bool_or(is_active) as any_active" in body
    assert "(select bool_or(is_active) and not bool_and(is_active) from sample)" in body


def test_the_apply_is_gated_on_the_predecessors_body_or_this_files_own() -> None:
    gate = re.search(r"not in \('([0-9a-f]{32})', '([0-9a-f]{32})'\)", SQL)
    assert gate and gate.groups() == (_md5(_statement(PREV_SQL)), _md5(_statement(SQL)))
    assert f"raise notice '{NUMBER}: replay container" in SQL
    assert f"{NUMBER} refused: {FN} is not {_number(PREV)}''s body" in SQL


def test_the_replace_keeps_the_signature_and_proves_the_lens() -> None:
    sig = re.compile(rf"create or replace function public\.{FN}\((.*?)\)\nreturns jsonb", re.S)
    assert sig.search(SQL).group(1) == sig.search(PREV_SQL).group(1)
    assert "language plpgsql\nstable\nset plan_cache_mode to 'force_custom_plan'" in _statement(SQL)
    code = re.sub(r"--[^\n]*", "", SQL)
    assert "drop function" not in code and "revoke" not in code
    assert "grant" not in code.replace("has_function_privilege", "")
    post = SQL[SQL.index("do $post$"): SQL.index("$post$;", SQL.index("do $post$"))]
    assert "''active'', null, false) as is_active' in src)" in post
    assert "position('or is_active = property_active)' in src)" in post
    assert "has_function_privilege('authenticated'" in post
